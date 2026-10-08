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

from pathlib import Path
from typing import Any

from ..ir.document import DocumentRef, PageInfo
from ..ir.markup import (
    CODE,
    HEADING,
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
    "h": {1: (22.0, 18.0, 10.0), 2: (16.0, 14.0, 8.0), 3: (13.5, 12.0, 6.0)},
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
                fontname=_FONT,
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
                    fontname=_FONT,
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
                fontname=_FONT,
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

    def render(blocks: list[Block]) -> None:
        for b in blocks:
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
                h = pager.text_box(b.text, sz, 1.3)
                pager.advance(h + after)
                pol.note(
                    "等宽字体",
                    Strategy.DROP,
                    f"china-ss 无等宽变体，代码块用普通字形排",
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
                _render_table(pager, b)
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

    try:
        doc.save(dst, deflate=True)
    finally:
        doc.close()

    return build_document_ref(dst, tree.title)


def _render_table(pager: _Pager, table_block: Block) -> None:
    """表格排版：逐行 insert_textbox，列宽均分。

    简化规则：
    - 列宽 = (页宽 - 2×边距) / 列数
    - 行高固定 20pt（单行）
    - 表头（第一行）加粗由字号放大 1pt 模拟
    - 不做跨页：整表放不下就翻页
    """
    if not table_block.rows:
        return

    rows = table_block.rows
    if not rows:
        return

    # 推断列数：取第一行的单元格数量
    ncols = len(rows[0]) if rows else 1

    col_w = (PAGE_W - 2 * MARGIN) / ncols
    row_h = 20.0
    table_h = len(rows) * row_h + 10.0  # 表前后留白

    pager.ensure_room(table_h)
    pager.advance(5.0)

    for i, row in enumerate(rows):
        if not row:
            continue
        y0 = pager.cursor
        for j, cell in enumerate(row):
            x0 = MARGIN + j * col_w
            x1 = x0 + col_w
            y1 = y0 + row_h
            import pymupdf
            rect = pymupdf.Rect(x0, y0, x1, y1)
            # 表头单元格或第一行字号稍大
            fs = 11.5 if (cell.header or i == 0) else 10.0
            # Cell 的文本内容在 blocks 里
            text = " ".join(b.text for b in cell.blocks) if cell.blocks else ""
            pager.page.insert_textbox(
                rect,
                text,
                fontsize=fs,
                fontname=_FONT,
                align=0,
            )
            # 画单元格边框
            pager.page.draw_rect(rect, width=0.3, color=(0.6, 0.6, 0.6))
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
    """table ⇒ document 桥的实现：Table IR 直接排成 PDF 表格。"""
    import pymupdf

    tmp = workdir(opts)
    dst = tmp / "table.pdf"

    # ir 是 Table 对象，不是 DataFrame
    sheet = ir.primary
    if not sheet.rows:
        raise ConversionError("空表格无法生成 PDF")

    doc = pymupdf.open()
    pager = _Pager(doc, pol)

    # 表头 + 数据行
    headers = sheet.columns
    ncols = len(headers)
    nrows = len(sheet.rows) + 1  # +1 for header

    col_w = (PAGE_W - 2 * MARGIN) / ncols
    row_h = 18.0

    def draw_row(y0: float, cells: list[str], is_header: bool):
        for j, text in enumerate(cells):
            x0 = MARGIN + j * col_w
            x1 = x0 + col_w
            y1 = y0 + row_h
            rect = pymupdf.Rect(x0, y0, x1, y1)
            fs = 11.0 if is_header else 9.5
            pager.page.insert_textbox(
                rect,
                str(text),
                fontsize=fs,
                fontname=_FONT,
                align=0,
            )
            pager.page.draw_rect(rect, width=0.3, color=(0.5, 0.5, 0.5))

    # 排表头
    pager.ensure_room(row_h + 10.0)
    pager.advance(5.0)
    draw_row(pager.cursor, headers, is_header=True)
    pager.advance(row_h)

    # 排数据行
    for i, row_data in enumerate(sheet.rows):
        needed = row_h + (5.0 if i == 0 else 0.0)
        pager.ensure_room(needed)
        draw_row(pager.cursor, [str(x) for x in row_data], is_header=False)
        pager.advance(row_h)

    pager.advance(5.0)

    try:
        doc.save(dst, deflate=True)
    finally:
        doc.close()

    if len(ir.sheets) > 1:
        pol.note(
            "多表",
            Strategy.EMULATE,
            f"源文件有 {len(ir.sheets)} 张表，仅导出首表 {sheet.name}",
        )

    return build_document_ref(dst, None)
