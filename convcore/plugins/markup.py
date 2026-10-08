"""标记语言插件：注册 MD / HTML / TXT 的读写能力。

一个插件覆盖三种格式，因为它们共用同一个 DocTree IR 和同一套降级逻辑。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..ir.markup import DocTree
from ..policy import Policy
from ..registry import register
from ..types import Capability, ConversionError, LossTier, MediaType, Resource
from . import markup_read as rd
from . import markup_write as wr
from ._scratch import workdir

MD = "text/markdown"
HTML = "text/html"
TXT = "text/plain"

_OPTS = frozenset({"fragment", "title"})


def _decode(res: Resource) -> str:
    """解码文本文件。

    编码嗅探是必须的：中文环境里 GBK 文件很常见，按 UTF-8 硬解会得到乱码
    或直接抛异常。顺序上先试 UTF-8（最普遍且能自我验证），再试中文编码。
    """
    data = res.read_bytes()

    # BOM 优先，它是明确的信号
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")

    for enc in ("utf-8", "gb18030", "big5", "shift_jis", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    # latin-1 不会失败，所以走不到这里
    return data.decode("utf-8", "replace")


def _resolve_image_srcs(blocks: list, base: Path) -> None:
    """把 Markdown/HTML 里相对路径的图片解析成绝对路径。

    排版层要按路径读图片字节；不解析的话相对路径会按进程 CWD 找，找不到
    只能留占位——转出来的 PDF 里图片全丢。data URI 与外链保持原样。
    """
    from ..ir.markup import IMAGE

    for b in blocks:
        if b.kind == IMAGE and b.src:
            if not b.src.startswith(("data:", "http://", "https://")):
                p = Path(b.src)
                if not p.is_absolute():
                    b.src = str(base / b.src)
        if b.children:
            _resolve_image_srcs(b.children, base)


@register
class MarkupCodec:
    name = "markup"
    domain = "markup"

    reads = (
        Capability(MD, LossTier.LOSSLESS, 1.0),
        Capability(HTML, LossTier.LOSSLESS, 1.5),
        Capability(TXT, LossTier.LOSSLESS, 0.5),
    )

    writes = (
        # HTML 表达力最强，几乎能承载 DocTree 的全部信息
        Capability(HTML, LossTier.LOSSLESS, 1.0, _OPTS),
        # Markdown 表达不了合并单元格、任意 HTML 属性和单元格内格式——写出来是
        # 结构性简化。报 LOSSLESS 会让「HTML 还是 MD」的选路失去依据
        Capability(MD, LossTier.STRUCTURAL, 0.8, _OPTS, fidelity=0.9),
        # 纯文本丢掉全部格式——这是结构性损失，不是「有损压缩」。
        # 要求有文字：只有一段原始 SVG 的文档树（SVG→纯文本）写出来是空文件
        Capability(TXT, LossTier.STRUCTURAL, 0.5, requires=frozenset({"text"})),
    )

    def available(self) -> tuple[bool, str]:
        try:
            import mistune  # noqa: F401
            from lxml import html  # noqa: F401

            return True, ""
        except ImportError as exc:
            return False, f"缺少 {exc.name}：uv pip install mistune lxml"

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> DocTree:
        if len(resources) > 1:
            raise ConversionError("标记语言暂不支持多文件合并")

        res = resources[0]
        mt = res.media_type

        if mt == HTML:
            tree = rd.parse_html(res.read_bytes(), source_type=mt)
        elif mt == MD:
            tree = rd.parse_markdown(_decode(res), source_type=mt)
        else:
            tree = rd.parse_text(_decode(res), source_type=mt)
        if res.path is not None:
            _resolve_image_srcs(tree.blocks, res.path.parent)
        return tree

    def write(
        self, ir: DocTree, target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        pol: Policy = options.get("_policy") or Policy()

        if title := options.get("title"):
            ir.title = str(title)

        if target == HTML:
            text = wr.write_html(ir, pol, fragment=bool(options.get("fragment")))
        elif target == MD:
            text = wr.write_markdown(ir, pol)
        elif target == TXT:
            text = wr.write_text(ir, pol)
        else:
            raise ConversionError(f"markup 插件不支持写 {target}")

        return [Resource(media_type=target, data=text.encode("utf-8"))]


def _text_block(blk: dict, body_size: float) -> Block | None:
    """文本块 → HEADING/PARAGRAPH 块；无文字时返回 None。"""
    from ..ir.markup import HEADING, PARAGRAPH, Block, Span

    text_parts: list[str] = []
    max_size = 0.0
    bold = False
    for line in blk.get("lines", []):
        for span in line.get("spans", []):
            t = span.get("text") or ""
            if not t.strip():
                continue
            text_parts.append(t)
            max_size = max(max_size, span.get("size", 0))
            # flags 位 4 表示粗体
            if span.get("flags", 0) & (1 << 4):
                bold = True
    text = " ".join(" ".join(text_parts).split())
    if not text:
        return None

    # 字号显著大于正文，或加粗且较短 → 判为标题
    ratio = max_size / body_size if body_size else 1.0
    if ratio >= 1.5:
        return Block(HEADING, spans=[Span(text)], level=1)
    if ratio >= 1.18:
        return Block(HEADING, spans=[Span(text)], level=2)
    if bold and len(text) < 60:
        return Block(HEADING, spans=[Span(text)], level=3)
    return Block(PARAGRAPH, spans=[Span(text)])


def _looks_like_table(table) -> bool:
    """text 策略的误报过滤：真表格填得满、单元格短。

    实测把带线框表格的页面用 text 策略重跑，会把整页散文框成一张 6 列
    表（填充率 65%、27% 的单元格是长句片段）。真表格的填充率接近 100%，
    单元格是短词。
    """
    try:
        rows = [
            r for r in table.extract() if r and any(c and str(c).strip() for c in r)
        ]
    except Exception:
        return False
    if len(rows) < 2:
        return False
    ncols = max(len(r) for r in rows)
    if ncols < 2:
        return False
    cells = [str(c).strip() for r in rows for c in r if c and str(c).strip()]
    if not cells:
        return False
    if len(cells) < len(rows) * ncols * 0.7:
        return False
    long_cells = sum(1 for c in cells if len(c) > 24 or len(c.split()) > 3)
    return long_cells <= len(cells) * 0.2


def _page_tables(page) -> list:
    """本页检出的表格：先认线框，没有线框的表再走文本策略 + 形态过滤。

    pymupdf 默认策略只认带线的表，且没有误报；text 策略能认纯文本对齐出来
    的表（reportlab 默认不画线），但会把散文误框成表格，所以必须过滤。
    """
    from ._quiet import quiet_stderr

    try:
        with quiet_stderr():
            ruled = list(page.find_tables().tables)
    except Exception:
        ruled = []
    if ruled:
        return ruled

    try:
        with quiet_stderr():
            candidates = list(page.find_tables(strategy="text").tables)
    except TypeError:
        return []  # 老版本 pymupdf 没有 strategy 参数
    except Exception:
        return []
    return [t for t in candidates if _looks_like_table(t)]


def _table_to_block(table) -> Block | None:
    """find_tables 的表格 → 带表头标记的 TABLE 块。"""
    from ..ir.markup import PARAGRAPH, TABLE, Block, Cell, Span

    try:
        raw = table.extract()
    except Exception:
        return None
    rows = [r for r in raw if r and any(c and str(c).strip() for c in r)]
    if len(rows) < 2:
        return None

    has_header = bool(getattr(table, "header", None))
    out_rows: list[list[Cell]] = []
    for i, r in enumerate(rows):
        cells = []
        for c in r:
            text = str(c).strip() if c is not None else ""
            cells.append(
                Cell(
                    blocks=(
                        [Block(PARAGRAPH, spans=[Span(text)])] if text else []
                    ),
                    header=has_header and i == 0,
                )
            )
        out_rows.append(cells)
    return Block(TABLE, rows=out_rows)


def _inside_boxes(bbox: tuple, boxes: list[tuple]) -> bool:
    """文本块中心是否落在某个表格 bbox 内（表格文字不再以段落重复）。"""
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    return any(bx0 <= cx <= bx2 and by0 <= cy <= by1 for bx0, by0, bx2, by1 in boxes)


def pdf_to_doctree(ref, opts: dict[str, Any], pol: Policy) -> DocTree:
    """PDF → DocTree 桥接：抽取文本并推断结构。

    这是推断而非翻译——PDF 没有「这是标题」的语义，只有「这段字号更大」。
    用字号作为标题层级的信号是最可靠的启发式，但对样式不规范的 PDF 会失效。
    """
    from ..ir.markup import HEADING, PARAGRAPH, TABLE, Block, Span
    from ..policy import Strategy
    from ._quiet import quiet_stderr

    from .document import _open_doc, parse_page_spec

    tmp = workdir(opts)
    doc = _open_doc(ref.source, tmp, opts.get("password"))
    try:
        indices = parse_page_spec(opts.get("pages"), doc.page_count)

        if ref.is_scanned_doc:
            raise ConversionError(
                f"这份 PDF 没有文本层（{len(ref.scanned_pages)}/{ref.page_count} 页是图像），"
                "抽不出文字。需要先做 OCR，或转成图片（-t png）。"
            )

        # 先扫一遍收集字号分布，用中位数当正文基准
        sizes: list[float] = []
        with quiet_stderr():
            for i in indices:
                for blk in (doc[i].get_text("dict") or {}).get("blocks", []):
                    for line in blk.get("lines", []):
                        for span in line.get("spans", []):
                            if (span.get("text") or "").strip():
                                sizes.append(span.get("size", 0))

        body_size = _median(sizes) if sizes else 11.0
        blocks: list[Block] = []

        with quiet_stderr():
            for i in indices:
                page = doc[i]
                page_dict = page.get_text("dict") or {}
                items: list[tuple[tuple[float, float, float, float], Block]] = []
                for blk in page_dict.get("blocks", []):
                    if blk.get("type") != 0:
                        continue  # 图像块：无文本层，结尾统一告警
                    block = _text_block(blk, body_size)
                    if block is not None:
                        items.append(
                            (tuple(blk.get("bbox", (0.0, 0.0, 0.0, 0.0))), block)
                        )

                # 表格：find_tables 检出后转 TABLE 块；被表格 bbox 覆盖的文本块
                # 剔除，否则整表文字会再以段落重复一遍。按纵坐标排序保持阅读序。
                tables = _page_tables(page)
                for t in tables:
                    block = _table_to_block(t)
                    if block is not None:
                        items.append((tuple(t.bbox), block))
                if tables:
                    boxes = [tuple(t.bbox) for t in tables]
                    items = [
                        (bb, b) for bb, b in items
                        if b.kind == TABLE or not _inside_boxes(bb, boxes)
                    ]
                items.sort(key=lambda item: item[0][1])
                blocks.extend(b for _, b in items)

        pol.note(
            "结构推断",
            Strategy.APPROXIMATE,
            "标题层级按字号相对正文的比例推断，PDF 本身不含此语义；"
            "图片未保留（无文本层）",
        )

        tree = DocTree(blocks=blocks, source_type="application/pdf")
        if ref.title:
            tree.title = ref.title
        elif blocks and blocks[0].kind == HEADING:
            tree.title = blocks[0].text
        return tree
    finally:
        doc.close()


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
