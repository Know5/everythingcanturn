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
        # Markdown 表达不了合并单元格和任意 HTML 属性
        Capability(MD, LossTier.LOSSLESS, 1.0, _OPTS),
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


def pdf_to_doctree(ref, opts: dict[str, Any], pol: Policy) -> DocTree:
    """PDF → DocTree 桥接：抽取文本并推断结构。

    这是推断而非翻译——PDF 没有「这是标题」的语义，只有「这段字号更大」。
    用字号作为标题层级的信号是最可靠的启发式，但对样式不规范的 PDF 会失效。
    """
    from ..ir.markup import HEADING, PARAGRAPH, Block, Span
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
                page_dict = doc[i].get_text("dict") or {}
                for blk in page_dict.get("blocks", []):
                    if blk.get("type") != 0:
                        continue  # 图像块
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
                        continue

                    # 字号显著大于正文，或加粗且较短 → 判为标题
                    ratio = max_size / body_size if body_size else 1.0
                    if ratio >= 1.5:
                        blocks.append(Block(HEADING, spans=[Span(text)], level=1))
                    elif ratio >= 1.18:
                        blocks.append(Block(HEADING, spans=[Span(text)], level=2))
                    elif bold and len(text) < 60:
                        blocks.append(Block(HEADING, spans=[Span(text)], level=3))
                    else:
                        blocks.append(Block(PARAGRAPH, spans=[Span(text)]))

        pol.note(
            "结构推断",
            Strategy.APPROXIMATE,
            "标题层级按字号相对正文的比例推断，PDF 本身不含此语义；"
            "表格和图片未保留",
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
