"""跨域桥的 PDF 构建侧：DocTree → PDF、Raster → PDF。

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

    def need(self, height: float) -> None:
        """确保当前页还有 height 的竖向空间，不够就翻页。"""
        if self.room() < height:
            self._new_page()

    def advance(self, used: float, minimum: float) -> None:
        """光标下移 used（至少 minimum），并做空间兜底检查。"""
        self.cursor += max(used, minimum)

    def blocks(self) -> list[PageInfo]:
        return []  # PageInfo 由 build_document_ref 统一探测生成


def _layout_tree(ir: DocTree, opts: dict[str, Any], pol: Policy) -> Path:
    """把 DocTree 排成 PDF 落盘，返回路径。"""
    import pymupdf

    tmp = Path(opts.get("_tmp") or ".")
    dst = tmp / "built.pdf"

    doc = pymupdf.open()
    try:
        pager = _Pager(doc, pol)
        for b in ir.blocks:
            _layout_block(pager, b, pol)

        doc.save(dst, garbage=3, deflate=True)
    finally:
        doc.close()
    return dst


def _layout_block(pager: _Pager, b: Block, pol: Policy, depth: int = 0) -> None:
    import pymupdf

    if b.kind == HEADING:
        size, before, after = _STYLES["h"].get(max(1, min(3, b.level or 1)), _BODY)
        text = b.text
        # china-ss 行高 ≈ 字号 × 1.8（实测 1.5 放不下，返回负值被静默丢弃）
        pager.need(size * 1.8 + before + after)
        pager.cursor += before
        top = pager.cursor
        bottom = top + size * 1.8
        rect = pymupdf.Rect(MARGIN, top, PAGE_W - MARGIN, bottom)
        if pager.page.insert_textbox(rect, text, fontsize=size, fontname=_FONT) < 0:
            raise ConversionError(f"标题排版失败：{text[:30]}")
        pager.cursor = bottom + after
        return

    if b.kind == PARAGRAPH:
        _flow_text(pager, b, pol)
        return

    if b.kind == CODE:
        lines = b.text.split("\n") or [""]
        size, before, after = _STYLES["code"]
        for k, line in enumerate(lines):
            pager.need(size * 1.8)
            if k == 0:
                pager.cursor += before
            top = pager.cursor
            bottom = top + size * 1.8
            rect = pymupdf.Rect(MARGIN, top, PAGE_W - MARGIN, bottom)
            pager.page.insert_textbox(rect, line, fontsize=size, fontname=_FONT)
            pager.cursor = bottom
        pager.cursor += after
        return
    if b.kind in (LIST,):
        for i, kid in enumerate(b.children):
            marker = f"{i + 1}. " if b.ordered else "• "
            if kid.kind == LIST_ITEM:
                _flow_text(pager, kid, pol, prefix=marker, indent=depth * 18)
                for sub in kid.children:
                    _layout_block(pager, sub, pol, depth + 1)
        return

    if b.kind == LIST_ITEM:
        _flow_text(pager, b, pol)
        return

    if b.kind == QUOTE:
        for kid in b.children:
            _flow_text(pager, kid, pol, indent=20, quote=True)
        return

    if b.kind == RULE:
        pager.need(14)
        y = pager.cursor + 7
        pager.page.draw_line(
            pymupdf.Point(MARGIN, y), pymupdf.Point(PAGE_W - MARGIN, y)
        )
        pager.cursor += 14
        return

    if b.kind == TABLE:
        _layout_table(pager, b, pol)
        return

    # 未知块（IMAGE/SECTION/RAW）：保文本不丢，提示余项
    if b.text.strip():
        _flow_text(pager, b, pol)
    if b.kind not in (PARAGRAPH,):
        pol.note(
            "块类型",
            Strategy.EMULATE,
            f"{b.kind} 在 PDF 排版中按纯文本段处理",
        )


def _flow_text(
    pager: _Pager,
    b: Block,
    pol: Policy,
    prefix: str = "",
    indent: float = 0.0,
    quote: bool = False,
) -> None:
    """流式排一段：按文本框宽度自动换行，放不下翻页续排。"""
    import pymupdf

    text = (prefix + b.text).strip()
    if not text:
        return

    size, _, after = _BODY
    x0 = MARGIN + indent
    x1 = PAGE_W - MARGIN
    width = x1 - x0
    bottom_edge = PAGE_H - MARGIN

    # insert_textbox 语义：rc<0 时一个字都不写。所以长段落必须
    # 「试探→按 rc 比例截断→写入→剩余部分在下一页续排」。
    # rc 的绝对值 ≈ 溢出部分还差的高度，用它推算能放下的比例。
    while text:
        pager.need(size * 1.8)
        rect = pymupdf.Rect(x0, pager.cursor, x1, bottom_edge)
        consumed = pager.page.insert_textbox(
            rect, text, fontsize=size, fontname=_FONT, align=0,
        )
        if consumed >= 0:
            pager.advance(rect.height - consumed, size * 1.2)
            break

        # 放不下：先看本页剩余空间。不足 3 行就直接翻页，
        # 否则 frac 极小会退化成逐字符截断（慢得像死循环）。
        overflow = -consumed
        avail = rect.height
        if avail < size * 1.8 * 3 or overflow >= avail * 50:
            pager._new_page()
            continue
        frac = avail / (avail + overflow)
        n = max(1, int(len(text) * frac * 0.9))
        if n >= len(text):
            # 比例算不准（字宽差异大），保守砍半逐轮逼近
            n = max(1, len(text) // 2)
        cut = text[:n]
        rc = pager.page.insert_textbox(
            rect, cut, fontsize=size, fontname=_FONT, align=0,
        )
        if rc < 0:
            n = max(1, n // 2)
            continue
        pager.advance(rect.height - rc, size * 1.2)
        text = text[n:]
        pager._new_page()
        # 不 break：while text 会检查剩余文本，空了自然退出

    pager.cursor += after


def _layout_table(pager: _Pager, b: Block, pol: Policy) -> None:
    """表格：网格线 + 单元格文本。单元格内只取纯文本（嵌套块展平）。"""
    import pymupdf

    if not b.rows:
        return

    size = 9.5
    padding = 3.0
    col_n = max(len(r) for r in b.rows)
    col_w = (PAGE_W - 2 * MARGIN) / col_n

    # 先算每行高度（估行数，放不下翻页——表格不跨页，简单可靠）
    def cell_lines(cell) -> list[str]:
        text = cell.text if hasattr(cell, "text") else str(cell)
        per_line = max(1, int(col_w / (size * 0.55)))
        out: list[str] = []
        for raw in text.split("\n") or [""]:
            while len(raw) > per_line:
                out.append(raw[:per_line])
                raw = raw[per_line:]
            out.append(raw)
        return out or [""]

    row_heights = [
        max(len(cell_lines(c)) for c in row) * size * 1.8 + 2 * padding
        for row in b.rows
    ]

    cursor = pager.cursor
    for ri, row in enumerate(b.rows):
        h = row_heights[ri]
        if (PAGE_H - MARGIN) - cursor < h:
            pager.need(h)
            cursor = pager.cursor
        rect_top = cursor
        # 行网格线
        pager.page.draw_rect(
            pymupdf.Rect(MARGIN, rect_top, PAGE_W - MARGIN, rect_top + h),
            color=(0.75, 0.75, 0.75),
        )
        for ci in range(col_n):
            cell = row[ci] if ci < len(row) else None
            text = cell.text if cell is not None and hasattr(cell, "text") else ""
            x0 = MARGIN + ci * col_w
            rect = pymupdf.Rect(
                x0 + padding, rect_top + padding, x0 + col_w - padding, rect_top + h - padding
            )
            # 表头用字号+底纹区分（china-ss 无粗体变体）
            fname = _FONT
            pager.page.insert_textbox(rect, text, fontsize=size, fontname=fname)
        # 竖线
        for ci in range(col_n + 1):
            x = MARGIN + ci * col_w
            pager.page.draw_line(
                pymupdf.Point(x, rect_top), pymupdf.Point(x, rect_top + h),
                color=(0.75, 0.75, 0.75),
            )
        cursor += h
    pager.cursor = cursor + 8.0


def build_document_ref(path: Path, title: str | None) -> DocumentRef:
    """给排好的 PDF 文件造 DocumentRef（PDF writer 消费这个）。"""
    import pymupdf

    doc = pymupdf.open(path)
    try:
        pages = []
        for i in range(doc.page_count):
            page = doc[i]
            pages.append(
                PageInfo(
                    index=i,
                    width=page.rect.width,
                    height=page.rect.height,
                    rotation=page.rotation,
                    char_count=len((page.get_text("text") or "").strip()),
                    image_count=len(page.get_images(full=False)),
                    table_count=0,
                    has_vector=bool(page.get_drawings()),
                )
            )
        return DocumentRef(
            source=Resource(media_type=PDF, path=path),
            pages=pages,
            encrypted=False,
            title=title,
        )
    finally:
        doc.close()


def doctree_to_document(ir: DocTree, opts: dict[str, Any], pol: Policy) -> DocumentRef:
    """markup ⇒ document 桥的实现：DocTree → 排版成 PDF 的 DocumentRef。"""
    if not ir.blocks:
        # 空文档产出一张白纸 PDF 是「成功」的假象——用户拿到空文件
        # 不会怀疑转换器，只会以为内容本来就没有。明确报错。
        raise ConversionError("文档没有可排版的内容块（空文档）")
    path = _layout_tree(ir, opts, pol)
    ref = build_document_ref(path, ir.title)
    tables = any(b.kind == TABLE for b in ir.blocks)
    images = any(b.kind == "image" for b in _walk(ir.blocks))

    if tables:
        pol.note(
            "表格排版",
            Strategy.APPROXIMATE,
            "表格单元格跨页合并与精确对齐未保留，仅还原网格结构",
        )
    if images:
        pol.note(
            "图片",
            Strategy.DROP,
            "文档中的图片未嵌入 PDF（当前桥只排文本结构）",
        )
    return ref


def _walk(blocks: list[Block]):
    for b in blocks:
        yield b
        if b.children:
            yield from _walk(b.children)


def raster_to_document(ir, opts: dict[str, Any], pol: Policy) -> DocumentRef:
    """raster ⇒ document 桥的实现：每图一页，像素原样嵌入。"""
    import io

    import pymupdf

    tmp = Path(opts.get("_tmp") or ".")
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
