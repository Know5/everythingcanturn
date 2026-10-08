"""跨域桥的 PDF 构建侧：DocTree → PDF、Raster → PDF、Table → PDF。

设计取舍：排 PDF 用 pymupdf 的 insert_textbox/insert_image 而不是
HTML→打印的那条路。理由：
- 零外部依赖：不需要浏览器或 soffice，冷启动即用
- 我们已有完整的 DocTree 语义（标题层级/列表/表格/代码块），
  直接映射排版参数比先渲染 HTML 再让浏览器猜语义更可控
- 量级合适：pymupdf 的排版原语是 O(文本长度)，没有 DOM 开销

排版是「新增信息」：流式文档没有分页点，分页位置是我们决定的，
所以 markup→document 桥标 STRUCTURAL。图片→PDF 不做再压缩，
像素原样嵌入，标 LOSSLESS。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..ir.document import DocumentRef, PageInfo
from ..ir.markup import (
    CODE,
    HEADING,
    IMAGE,
    LIST,
    LIST_ITEM,
    PARAGRAPH,
    QUOTE,
    RULE,
    TABLE,
    Block,
    DocTree,
)
from ..policy import Policy, Strategy
from ..types import ConversionError, MediaType, Resource
from ._scratch import workdir

PDF = "application/pdf"

# A4 纵向，单位 pt（pymupdf 原生单位）
PAGE_W, PAGE_H = 595.0, 842.0
MARGIN = 62.0
# 每级元素的排版参数：(字号 pt, 段前距, 段后距)
# 字号阶梯接近 Word 默认（11pt 正文，标题逐级放大）。
_BODY = (11.0, 4.0, 8.0)
_STYLES = {
    # h4-h6 不再回退正文：压平会让 4/5/6 级标题视觉上不可分辨
    "h": {
        1: (22.0, 18.0, 10.0), 2: (16.0, 14.0, 8.0), 3: (13.5, 12.0, 6.0),
        4: (12.0, 10.0, 5.0), 5: (11.5, 9.0, 5.0), 6: (11.0, 8.0, 4.0),
    },
    "code": (9.5, 6.0, 6.0),
    "quote": (11.0, 6.0, 6.0),
}

# 全文本统一 china-ss（内置 CJK，5 万字形，Latin/中文/混排全覆盖）。
# 原因：helv 家族只有 654 个 Latin 字形，中文全是 '?'。china-ss 没有
# 粗体变体——标题强调靠字号阶梯，代码等宽性丢失记 policy 告警。
_FONT = "china-ss"


class _Pager:
    """分页状态机：往当前页排，放不下就开新页。

    坐标系：cursor 是屏幕系偏移——从页顶往下数，MARGIN 起，
    增长方向即排版前进方向。pymupdf 的 insert_textbox rect 也是
    屏幕系（y0=文本区视觉顶），两者一致；注意 blocks 等查询 API
    返回的是页坐标系（y 向上），别混用。

    排版规则简单可靠优先：不搞避头尾、不搞跨页表格——那是排版引擎的
    事。我们要的是「文本一段不丢、页数合理」，不是 InDesign。
    """

    def __init__(self, doc, pol: Policy):
        self.doc = doc
        self.pol = pol
        self.page = None
        self.cursor = 0.0
        self._new_page()

    def _new_page(self) -> None:
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        self.cursor = MARGIN

    def room(self) -> float:
        """当前页剩余竖向空间（屏幕系：页底边界 - cursor）。"""
        return (PAGE_H - MARGIN) - self.cursor

    def ensure_room(self, needed: float) -> None:
        """需要 needed pt 空间，不够就翻页。"""
        if self.room() < needed:
            self._new_page()

    def advance(self, delta: float) -> None:
        """cursor 下移，分页交给调用方的 ensure_room 处理。"""
        self.cursor += delta

    def text_box(
        self,
        text: str,
        font_size: float,
        leading: float,
        indent: float = 0.0,
        align=0,
        font: str = _FONT,
    ) -> float:
        """排一段文字，自动跨页，返回实际占用高度（含行距）。
        align: 0=左对齐，1=居中，2=右对齐。

        pymupdf.insert_textbox 返回值：正数=剩余空间，负数=溢出量（点）。
        但它不告诉我们哪些字符放下了，所以长文本必须手动切块。
        """
        import pymupdf

        x0 = MARGIN + indent
        x1 = PAGE_W - MARGIN
        line_h = font_size * leading

        remaining = text
        total_used = 0.0

        while remaining:
            # 当前页剩余空间
            avail = self.room()
            if avail < line_h:
                self._new_page()
                avail = self.room()

            y0 = self.cursor
            y1 = y0 + avail
            rect = pymupdf.Rect(x0, y0, x1, y1)

            # 先试全量
            ret = self.page.insert_textbox(
                rect,
                remaining,
                fontsize=font_size,
                fontname=font,
                align=align,
            )

            if ret >= 0:
                # 全部放下：ret 是剩余空间
                used_h = avail - ret
                self.advance(used_h)
                total_used += used_h
                break

            # 有溢出：二分查找能放下的最大长度
            # 估算每行字符数（中文占 2 倍宽）
            line_w = x1 - x0
            chars_per_line = int(line_w / (font_size * 0.5))  # 保守估计
            max_lines = int(avail / line_h)
            est_chars = max(1, chars_per_line * max_lines)

            # 二分查找
            lo, hi = 1, min(len(remaining), est_chars * 2)
            best_len = 0

            while lo <= hi:
                mid = (lo + hi) // 2
                chunk = remaining[:mid]

                # 清除上次试探的内容（重新绘制页面的这个区域）
                # 简化：直接重试，PyMuPDF 会覆盖
                test_ret = self.page.insert_textbox(
                    rect,
                    chunk,
                    fontsize=font_size,
                    fontname=font,
                    align=align,
                )

                if test_ret >= 0:
                    # 放得下：尝试更多
                    best_len = mid
                    lo = mid + 1
                else:
                    # 放不下：减少
                    hi = mid - 1

            if best_len == 0:
                # 一个字符都放不下：强制翻页
                self._new_page()
                continue

            # 用找到的最佳长度重新渲染
            chunk = remaining[:best_len]
            self.page.insert_textbox(
                rect,
                chunk,
                fontsize=font_size,
                fontname=font,
                align=align,
            )

            # 推进状态
            remaining = remaining[best_len:]
            self.advance(avail)  # 用完整页
            total_used += avail

        return total_used


def doctree_to_document(tree: DocTree, opts: dict[str, Any], pol: Policy) -> DocumentRef:
    """markup ⇒ document 桥的实现：DocTree 排成 PDF。"""
    from ..types import ConversionError

    if not tree.blocks:
        raise ConversionError("空文档：无内容可转换为 PDF")

    import pymupdf

    tmp = workdir(opts)
    dst = tmp / "built.pdf"

    doc = pymupdf.open()
    pager = _Pager(doc, pol)

    styles_lost = False

    def render(blocks: list[Block]) -> None:
        nonlocal styles_lost
        for b in blocks:
            if b.kind != TABLE and any(s.bold or s.italic for s in b.spans):
                styles_lost = True
            if b.kind == PARAGRAPH:
                sz, before, after = _BODY
                pager.advance(before)
                h = pager.text_box(b.text, sz, 1.4)
                pager.advance(h + after)
            elif b.kind == HEADING:
                lvl = b.level or 1
                sz, before, after = _STYLES["h"].get(lvl, _BODY)
                pager.advance(before)
                h = pager.text_box(b.text, sz, 1.3)
                pager.advance(h + after)
            elif b.kind == CODE:
                sz, before, after = _STYLES["code"]
                pager.advance(before)
                # 纯 ASCII 代码用内置 Courier；含 CJK 时退回 china-ss
                # （Courier 只有 Latin 字形，汉字会变 '?'）
                font = "cour" if b.text.isascii() else _FONT
                h = pager.text_box(b.text, sz, 1.3, font=font)
                pager.advance(h + after)
                if font != "cour":
                    pol.note(
                        "等宽字体",
                        Strategy.DROP,
                        "代码块含非 ASCII 字符，用普通字形排",
                    )
            elif b.kind == QUOTE:
                sz, before, after = _STYLES["quote"]
                pager.advance(before)
                h = pager.text_box(b.text, sz, 1.5, indent=20.0)
                pager.advance(h + after)
            elif b.kind == LIST:
                pager.advance(6.0)
                if b.children:
                    render(b.children)
                pager.advance(6.0)
            elif b.kind == LIST_ITEM:
                sz, _, _ = _BODY
                marker = "• "
                h = pager.text_box(marker + b.text, sz, 1.4, indent=10.0)
                pager.advance(h + 4.0)
            elif b.kind == TABLE:
                _render_table(pager, b, pol)
            elif b.kind == IMAGE:
                _render_image(pager, b, pol)
            elif b.kind == RULE:
                import pymupdf

                pager.advance(8.0)
                pager.ensure_room(1.0)
                y = pager.cursor
                pager.page.draw_line(
                    (MARGIN, y), (PAGE_W - MARGIN, y), width=0.5, color=(0.7, 0.7, 0.7)
                )
                pager.advance(8.0)

    render(tree.blocks)

    if styles_lost:
        pol.note(
            "行内样式",
            Strategy.DROP,
            "china-ss 无粗体/斜体字形，行内强调按正体排；标题层级靠字号阶梯区分",
        )

    try:
        doc.save(dst, deflate=True)
    finally:
        doc.close()

    return build_document_ref(dst, tree.title)


def _text_height(text: str, width: float, font_size: float) -> float:
    """估算 text 在 width pt 宽、font_size 下换行后的排版高度。

    用 pymupdf 的字体度量而不是「每行几个字」的粗略估算——否则中文会被
    当成半角，行高估低一半，多行单元格还是会被裁。
    """
    import pymupdf

    usable = max(10.0, width - 8.0)  # 单元格内边距
    lines = 0
    for para in text.splitlines() or [""]:
        if not para:
            lines += 1
            continue
        w = pymupdf.get_text_length(para, fontname=_FONT, fontsize=font_size)
        lines += max(1, math.ceil(w / usable))
    return max(1, lines) * font_size * 1.3 + 6.0


def _render_image(pager: _Pager, block: Block, pol: Policy) -> None:
    """图片块：本地文件 / data URI 嵌入；拿不到就留文本占位 + DROP 告警。

    外链不下载（本项目只经 engines install 走网络）。任何失败路径都在文本层
    留一条可检索的占位，而不是让图片静默消失。
    """
    import base64
    import io

    import pymupdf
    from PIL import Image

    data: bytes | None = None
    src = block.src or ""
    if src.startswith("data:"):
        try:
            data = base64.b64decode(src.split(",", 1)[1])
        except Exception:
            data = None
    elif src:
        try:
            p = Path(src)
            if p.is_file():
                data = p.read_bytes()
        except OSError:
            data = None

    label = block.alt or "未命名"
    src_hint = f"（{Path(src).name[:60]}）" if src else ""
    if data is None:
        pol.note("图片", Strategy.DROP, f"图片未嵌入（{label}{src_hint}），已留文本占位")
        pager.advance(4.0)
        h = pager.text_box(f"[图片：{label}{src_hint}]", 10.0, 1.3)
        pager.advance(h + 4.0)
        return

    try:
        with Image.open(io.BytesIO(data)) as im:
            iw, ih = im.size
    except Exception:
        pol.note("图片", Strategy.DROP, f"无法解码图片（{label}{src_hint}），已留文本占位")
        pager.advance(4.0)
        h = pager.text_box(f"[图片：{label}{src_hint}]", 10.0, 1.3)
        pager.advance(h + 4.0)
        return

    content_w = PAGE_W - 2 * MARGIN
    scale = min(1.0, content_w / max(1, iw))  # 只缩不放
    w, h = iw * scale, ih * scale
    max_h = PAGE_H - 2 * MARGIN
    if h > max_h:
        k = max_h / h
        w, h = w * k, max_h
    pager.ensure_room(h + 8.0)
    rect = pymupdf.Rect(MARGIN, pager.cursor, MARGIN + w, pager.cursor + h)
    pager.page.insert_image(rect, stream=data)
    pager.advance(h + 8.0)


def _render_table(pager: _Pager, table_block: Block, pol: Policy) -> None:
    """表格排版：逐行排、行高随内容、支持 colspan。

    行高取各单元格换行后的最大高度——固定行高会把长单元格直接裁掉。
    跨页粒度是行（每行先 ensure_room），表头不重复。rowspan 不支持：
    IR 里有字段但排版不做跨行合并，出现时记 DROP 告警而不是错位。
    """
    import pymupdf

    rows = table_block.rows
    if not rows:
        return

    ncols = max((len(r) for r in rows), default=1) or 1
    col_w = (PAGE_W - 2 * MARGIN) / ncols
    pager.advance(5.0)

    if any((cell.rowspan or 1) > 1 for row in rows for cell in row if cell):
        pol.note(
            "跨行合并",
            Strategy.DROP,
            "表格含 rowspan，按未合并单元格排（不做跨行合并）",
        )

    for i, row in enumerate(rows):
        if not row:
            continue
        fs = 11.5 if (row[0].header or i == 0) else 10.0
        cells: list[tuple[str, int, float]] = []  # (text, colspan, height)
        j = 0
        for cell in row:
            span = max(1, min(cell.colspan or 1, ncols - j))
            text = " ".join(b.text for b in cell.blocks) if cell.blocks else ""
            cells.append((text, span, _text_height(text, col_w * span, fs)))
            j += span
        row_h = max((h for _, _, h in cells), default=16.0)
        pager.ensure_room(row_h + 2.0)

        y0 = pager.cursor
        x = MARGIN
        for text, span, _ in cells:
            rect = pymupdf.Rect(x, y0, x + col_w * span, y0 + row_h)
            pager.page.insert_textbox(rect, text, fontsize=fs, fontname=_FONT, align=0)
            pager.page.draw_rect(rect, width=0.3, color=(0.6, 0.6, 0.6))
            x += col_w * span
        pager.advance(row_h)

    pager.advance(5.0)


def build_document_ref(path: Path, title: str | None) -> DocumentRef:
    """从 PDF 文件构建 DocumentRef IR。

    每页的文字数和图片数必须如实填：下游靠 PageInfo.is_scanned 判断有没有文本层。
    漏填时图片嵌成的 PDF 被当成「0 字 0 图」的普通页，抽文字静默产出空文件，
    pdf2docx 的扫描件预检也拦不住。
    """
    import pymupdf

    from ..types import Resource

    doc = pymupdf.open(path)
    try:
        pages = [
            PageInfo(
                index=i,
                width=page.rect.width,
                height=page.rect.height,
                char_count=len((page.get_text("text") or "").strip()),
                image_count=len(page.get_images(full=False)),
            )
            for i, page in enumerate(doc)
        ]
        return DocumentRef(
            source=Resource(media_type=MediaType("application/pdf"), path=path),
            pages=pages,
            title=title,
        )
    finally:
        doc.close()


def _walk(blocks: list[Block]):
    for b in blocks:
        yield b
        if b.children:
            yield from _walk(b.children)


def raster_to_document(ir, opts: dict[str, Any], pol: Policy) -> DocumentRef:
    """raster ⇒ document 桥的实现：每图一页，像素原样嵌入。"""
    import io

    import pymupdf

    tmp = workdir(opts)
    dst = tmp / "images.pdf"

    doc = pymupdf.open()
    try:
        has_alpha = False
        for frame in ir.frames:
            im = frame.image
            if im.mode in ("RGBA", "LA", "PA") or (
                im.mode == "P" and "transparency" in im.info
            ):
                has_alpha = True
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.mode else "RGB")
            # 页面尺寸：96dpi 像素 → 72dpi pt 的直接映射，一图一页。
            w, h = im.size
            page = doc.new_page(width=w * 72.0 / 96.0, height=h * 72.0 / 96.0)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            page.insert_image(page.rect, stream=buf.getvalue())

        if has_alpha:
            pol.note(
                "透明通道",
                Strategy.DROP,
                "PDF 页面不透明，图片的 alpha 通道已合成到白色背景",
            )

        doc.save(dst, deflate=True)
    finally:
        doc.close()

    ref = build_document_ref(dst, None)
    if len(ir.frames) > 1:
        pol.note(
            "多帧",
            Strategy.EMULATE,
            f"{len(ir.frames)} 帧按每帧一页排列（动图时间轴不保留）",
        )
    return ref


def table_to_document(ir, opts: dict[str, Any], pol: Policy) -> DocumentRef:
    """table ⇒ document 桥的实现：Table IR 直接排成 PDF 表格。

    多 sheet 全部导出（每张前面带 sheet 名），行高随内容换行——旧实现只排
    首表且固定行高，长单元格被裁剪。
    """
    import pymupdf

    if not any(s.rows for s in ir.sheets):
        raise ConversionError("空表格无法生成 PDF")

    tmp = workdir(opts)
    dst = tmp / "table.pdf"

    doc = pymupdf.open()
    pager = _Pager(doc, pol)

    for si, sheet in enumerate(ir.sheets):
        if not sheet.rows:
            continue
        if len(ir.sheets) > 1:
            pager.advance(6.0)
            h = pager.text_box(sheet.name, 12.0, 1.3)
            pager.advance(h + 2.0)
        _draw_data_table(pager, sheet.columns, sheet.rows)

    try:
        doc.save(dst, deflate=True)
    finally:
        doc.close()

    if len(ir.sheets) > 1:
        pol.note("多表", Strategy.EMULATE, f"{len(ir.sheets)} 张表按 sheet 顺序全部导出")

    return build_document_ref(dst, None)


def _draw_data_table(pager: _Pager, columns: list[str], rows: list[list[Any]]) -> None:
    """数据域表格排版：表头 + 数据行，行高随内容。"""
    ncols = max([len(columns)] + [len(r) for r in rows]) or 1
    col_w = (PAGE_W - 2 * MARGIN) / ncols
    pager.advance(5.0)

    header = [str(c) for c in columns]
    rows_out: list[tuple[list[str], float, float]] = []
    for text_row, fs in ((header, 11.0),) + tuple(
        ([str(x) for x in r], 9.5) for r in rows
    ):
        h = max((_text_height(t, col_w, fs) for t in text_row), default=14.0)
        rows_out.append((text_row, fs, h))

    for text_row, fs, row_h in rows_out:
        pager.ensure_room(row_h + 2.0)
        _draw_row_cells(pager, text_row, col_w, fs, row_h)
    pager.advance(5.0)


def _draw_row_cells(
    pager: _Pager, cells: list[str], col_w: float, font_size: float, row_h: float
) -> None:
    import pymupdf

    y0 = pager.cursor
    for j, text in enumerate(cells):
        x0 = MARGIN + j * col_w
        rect = pymupdf.Rect(x0, y0, x0 + col_w, y0 + row_h)
        pager.page.insert_textbox(
            rect, text, fontsize=font_size, fontname=_FONT, align=0
        )
        pager.page.draw_rect(rect, width=0.3, color=(0.5, 0.5, 0.5))
    pager.advance(row_h)
