"""DOCX 与 EPUB，接标记语言域。

DOCX 用 python-docx（MIT）。它已经是 pdf2docx 的依赖，不新增负担。

EPUB 自己实现而不用 ebooklib：ebooklib 是 AGPL，传染性比 GPL 更强（连网络服务
都算分发）。而 EPUB 本质就是 ZIP 里装 XHTML 加一份 OPF 清单，标准库的 zipfile
足够，也让目录结构完全可控。
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

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
    Cell,
    DocTree,
    Span,
)
from ..policy import Policy, Strategy
from ..registry import register
from ..types import Capability, ConversionError, LossTier, MediaType, Resource

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
EPUB = "application/epub+zip"

# DOCX 样式名 → 标题层级。python-docx 只给样式名，层级要自己映。
_HEADING_STYLES = {f"Heading {i}": i for i in range(1, 7)}
_HEADING_STYLES.update({f"标题 {i}": i for i in range(1, 7)})

_CODE_STYLE = "No Spacing"
"""代码块用的段落样式。默认模板里有，能去掉段间距让代码行紧挨。"""


@register
class DocxCodec:
    """DOCX 读写。经标记语言域，所以 DOCX↔MD↔HTML 三方互通。"""

    name = "python-docx"
    domain = "markup"
    reads = (Capability(DOCX, LossTier.STRUCTURAL, 3.0),)
    """读入是 STRUCTURAL：DOCX 的分页、页眉页脚、批注、修订映射不进 DocTree。"""
    # 要求有文字：原始 SVG 块塞不进 Word（见 _emit），SVG→Word 只会得到空文档。
    # 图片写成 [图片 label] 占位、RAW 丢弃——是结构性简化，不是无损
    writes = (
        Capability(DOCX, LossTier.STRUCTURAL, 4.0, frozenset({"title"}),
                   requires=frozenset({"text"}), fidelity=0.85),
    )

    def available(self) -> tuple[bool, str]:
        try:
            import docx  # noqa: F401

            return True, ""
        except ImportError:
            return False, "缺少 python-docx：uv pip install python-docx"

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> DocTree:
        import io

        import docx

        pol: Policy = options.get("_policy") or Policy()
        d = docx.Document(io.BytesIO(resources[0].read_bytes()))

        # style_id → 名称一次建好。para.style 每次访问都在 python-docx
        # 内部做 styles.default_for 全表扫描：2000 段实测 543ms，
        # 映射表 5ms，差 100 倍。
        names: dict[str, str] = {}
        for s in d.styles:
            try:
                names[s.style_id] = s.name
            except (AttributeError, ValueError):
                pass  # 个别 latent 样式没有可读的 style_id

        raw: list[Block] = []
        # 按文档顺序遍历段落和表格。python-docx 的 paragraphs 和 tables 是
        # 分开的列表，直接拼会丢失原始顺序，得走底层 body 元素。
        for child in d.element.body.iterchildren():
            tag = child.tag.split("}")[-1]
            if tag == "p":
                para = docx.text.paragraph.Paragraph(child, d)
                if (b := self._para(para, names)) is not None:
                    raw.append(b)
            elif tag == "tbl":
                tbl = docx.table.Table(child, d)
                raw.append(self._table(tbl))

        blocks = _coalesce(raw)

        if d.inline_shapes:
            pol.note(
                "图片",
                Strategy.DROP,
                f"{len(d.inline_shapes)} 张内嵌图片未提取（转换只保留文字结构）",
            )

        props = d.core_properties
        tree = DocTree(
            blocks=blocks,
            title=props.title or None,
            author=props.author or None,
            source_type=DOCX,
        )
        if tree.title is None:
            for b in blocks:
                if b.kind == HEADING and b.level == 1:
                    tree.title = b.text
                    break
        return tree

    def _para(self, para, names: dict[str, str]) -> Block | None:
        text = para.text.strip()
        style = self._style_name(para, names)

        if not text:
            return None

        if (level := _HEADING_STYLES.get(style)) is not None:
            return Block(HEADING, spans=[Span(text)], level=level, style=style)

        if style in ("Title", "标题"):
            return Block(HEADING, spans=[Span(text)], level=1, style=style)

        if style.startswith(("List", "列表")):
            ordered = "Number" in style or "编号" in style
            return Block(
                LIST,
                ordered=ordered,
                children=[Block(LIST_ITEM, spans=self._spans(para))],
                style=style,
            )

        if style in ("Quote", "Intense Quote", "引用"):
            return Block(QUOTE, children=[Block(PARAGRAPH, spans=self._spans(para))])

        # 整段都是等宽字体 → 代码。这是我们写出时的约定，读回来要还原
        runs = [r for r in para.runs if r.text.strip()]
        if runs and all((r.font.name or "") in ("Consolas", "Courier New") for r in runs):
            return Block(CODE, spans=[Span(para.text)])

        # 一整行连字符 → 分隔线
        if len(text) >= 8 and set(text) <= {"-", "─", "—", "_"}:
            return Block(RULE)

        return Block(PARAGRAPH, spans=self._spans(para), style=style or None)

    @staticmethod
    def _style_name(para, names: dict[str, str]) -> str:
        """段落样式名，查映射表。无显式样式的段落按 Normal 处理。

        para.style 每次访问都触发 python-docx 的 styles.default_for
        全表扫描；2000 段的文档实测逐段 543ms vs 查表 5ms。
        """
        pPr = para._p.pPr
        if pPr is None or pPr.pStyle is None:
            return names.get("Normal", "")
        sid = pPr.pStyle.val
        return names.get(sid, names.get("Normal", ""))

    def _spans(self, para) -> list[Span]:
        """从 run 提取行内格式。

        必须走底层 XML 而不是 para.runs：**runs 属性不包含 w:hyperlink 里的 run**，
        用它会让链接文字和 URL 一起消失。这是 python-docx 的一个陷阱。
        """
        from docx.oxml.ns import qn
        from docx.text.run import Run

        out: list[Span] = []
        rels = para.part.rels

        for child in para._p.iterchildren():
            tag = child.tag
            if tag == qn("w:r"):
                self._push(out, Run(child, para), href=None)
            elif tag == qn("w:hyperlink"):
                # 关系 ID 指向真实 URL
                rid = child.get(qn("r:id"))
                url = None
                if rid and rid in rels:
                    url = rels[rid].target_ref
                for r in child.findall(qn("w:r")):
                    self._push(out, Run(r, para), href=url)

        return out or ([Span(para.text)] if para.text else [])

    def _push(self, out: list[Span], run, href: str | None) -> None:
        if not run.text:
            return
        # Consolas 等宽字体是我们写 code span 用的标记，读回来还原成 code
        font_name = run.font.name or ""
        s = Span(
            run.text,
            bold=bool(run.bold),
            italic=bool(run.italic),
            strike=bool(run.font.strike),
            code=font_name in ("Consolas", "Courier New"),
            href=href,
        )
        if out and _same(out[-1], s) and out[-1].href == s.href and out[-1].code == s.code:
            out[-1].text += s.text
        else:
            out.append(s)

    def _table(self, tbl) -> Block:
        rows: list[list[Cell]] = []
        for ri, row in enumerate(tbl.rows):
            cells: list[Cell] = []
            for c in row.cells:
                text = c.text.strip()
                cells.append(
                    Cell(
                        blocks=[Block(PARAGRAPH, spans=[Span(text)])] if text else [],
                        header=ri == 0,
                    )
                )
            rows.append(cells)
        return Block(TABLE, rows=rows)

    # ── 写 DOCX ──

    def write(
        self, ir: DocTree, target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        import io

        import docx
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt

        pol: Policy = options.get("_policy") or Policy()
        d = docx.Document()

        if title := (options.get("title") or ir.title):
            d.core_properties.title = str(title)
        if ir.author:
            d.core_properties.author = ir.author

        for b in ir.blocks:
            self._emit(d, b, pol)

        if ir.image_count:
            pol.note(
                "图片",
                Strategy.DROP,
                f"{ir.image_count} 张图片仅保留占位说明（外链图片未下载）",
            )

        buf = io.BytesIO()
        d.save(buf)
        return [Resource(media_type=target, data=buf.getvalue())]

    def _emit(self, d, b: Block, pol: Policy, depth: int = 0) -> None:
        from docx.shared import Pt

        if b.kind == HEADING:
            d.add_heading(b.text, level=max(1, min(9, b.level or 1)))

        elif b.kind == PARAGRAPH:
            p = d.add_paragraph()
            self._runs(p, b.spans)

        elif b.kind == CODE:
            # Word 没有代码块概念，用等宽字体 + 无格式段落模拟。
            # 语言标记存进段落样式名之外的地方都会丢，所以写成首行注释保留。
            lines = "".join(s.text for s in b.spans).splitlines() or [""]
            if b.lang:
                lines = [f"# lang: {b.lang}", *lines]
            for line in lines:
                p = d.add_paragraph(style=_CODE_STYLE if _has_style(d, _CODE_STYLE) else None)
                run = p.add_run(line)
                run.font.name = "Consolas"
                run.font.size = Pt(9.5)
                p.paragraph_format.space_after = Pt(0)

        elif b.kind == QUOTE:
            for kid in b.children:
                p = d.add_paragraph(style="Quote" if _has_style(d, "Quote") else None)
                self._runs(p, kid.spans or [Span(kid.text)])

        elif b.kind == LIST:
            style = "List Number" if b.ordered else "List Bullet"
            for item in b.children:
                p = d.add_paragraph(style=style if _has_style(d, style) else None)
                self._runs(p, item.spans or [Span(item.text)])
                for kid in item.children:
                    if kid.kind != PARAGRAPH or not kid.text:
                        self._emit(d, kid, pol, depth + 1)

        elif b.kind == TABLE:
            self._write_table(d, b)

        elif b.kind == IMAGE:
            # 路径也写进占位符：DOCX 存不了外链图片，但丢掉路径就彻底找不回来了
            label = b.alt or "图片"
            if b.src:
                label = f"{label}：{b.src}"
            p = d.add_paragraph()
            run = p.add_run(f"[图片 {label}]")
            run.italic = True

        elif b.kind == RULE:
            # ASCII 而非 ─：读回来时能识别成分隔线，且在 GBK 环境不乱码
            d.add_paragraph("-" * 40)

        elif b.kind == "raw":
            return  # 原始 HTML 塞进 Word 没意义

        else:
            if b.text:
                p = d.add_paragraph()
                self._runs(p, b.spans or [Span(b.text)])

    def _runs(self, para, spans: list[Span]) -> None:
        from docx.shared import Pt

        for s in spans:
            if not s.text:
                continue
            if s.href:
                _add_hyperlink(para, s.text, s.href, s)
                continue
            run = para.add_run(s.text)
            run.bold = s.bold
            run.italic = s.italic
            run.font.strike = s.strike
            if s.code:
                run.font.name = "Consolas"
                run.font.size = Pt(9.5)

    def _write_table(self, d, b: Block) -> None:
        if not b.rows:
            return
        width = max(len(r) for r in b.rows)
        table = d.add_table(rows=0, cols=width)
        table.style = "Table Grid" if _has_style(d, "Table Grid", "table") else None

        for row in b.rows:
            cells = table.add_row().cells
            for i, c in enumerate(row[:width]):
                cells[i].text = c.text
                if c.header:
                    for p in cells[i].paragraphs:
                        for r in p.runs:
                            r.bold = True


def _coalesce(blocks: list[Block]) -> list[Block]:
    """合并相邻的同类块。

    DOCX 里每个列表项是独立段落，直接映射会得到一堆单项列表——转回 Markdown 时
    每项之间多出空行，且有序列表的编号全变成 1。代码块同理：多个等宽段落
    本来是一个代码块。
    """
    out: list[Block] = []

    for b in blocks:
        prev = out[-1] if out else None

        # 连续同类型列表合并成一个
        if (
            b.kind == LIST and prev is not None and prev.kind == LIST
            and prev.ordered == b.ordered
        ):
            prev.children.extend(b.children)
            continue

        # 连续代码段合并成一个代码块
        if b.kind == CODE and prev is not None and prev.kind == CODE:
            prev.spans.append(Span("\n" + "".join(s.text for s in b.spans)))
            continue

        out.append(b)

    # 提取代码块的语言标记（写出时存成首行注释）
    for b in out:
        if b.kind != CODE:
            continue
        text = "".join(s.text for s in b.spans)
        lines = text.splitlines()
        if lines and lines[0].startswith("# lang: "):
            b.lang = lines[0][len("# lang: "):].strip() or None
            b.spans = [Span("\n".join(lines[1:]))]

    return out


def _add_hyperlink(para, text: str, url: str, span: Span) -> None:
    """写真正的 DOCX 超链接。

    python-docx 没有超链接 API，必须手写 w:hyperlink 元素加关系。之前只加了
    下划线和蓝色当视觉提示——那样 URL 根本没进文件，往返时链接地址直接消失。
    视觉像链接但点不开、且数据已丢，比不做更糟。
    """
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml.ns import qn
    from docx.oxml.shared import OxmlElement

    r_id = para.part.relate_to(url, RT.HYPERLINK, is_external=True)

    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)

    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")

    # Hyperlink 样式在默认模板里不一定存在，所以直接写颜色和下划线
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    props.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.append(underline)
    if span.bold:
        props.append(OxmlElement("w:b"))
    if span.italic:
        props.append(OxmlElement("w:i"))

    run.append(props)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    para._p.append(link)


def _same(a: Span, b: Span) -> bool:
    return a.bold == b.bold and a.italic == b.italic and a.strike == b.strike


def _has_style(d, name: str, kind: str = "paragraph") -> bool:
    """检查样式是否存在。缺样式时 add_paragraph 会抛 KeyError。"""
    try:
        d.styles[name]
        return True
    except KeyError:
        return False


# ── EPUB ──


@register
class EpubCodec:
    """EPUB 读写。自己实现而非用 ebooklib（AGPL）。

    EPUB 3 的最小结构：
      mimetype                    必须是第一个条目且不压缩
      META-INF/container.xml      指向 OPF
      OEBPS/content.opf           清单 + spine（阅读顺序）
      OEBPS/nav.xhtml             目录（EPUB 3）
      OEBPS/ch*.xhtml             章节内容
    """

    name = "epub"
    domain = "markup"
    reads = (Capability(EPUB, LossTier.STRUCTURAL, 3.0),)
    # 图片目前只写外链不嵌资源，与 DOCX 写侧同理：结构性简化
    writes = (
        Capability(EPUB, LossTier.STRUCTURAL, 3.0,
                   frozenset({"title", "author", "lang"}), fidelity=0.85),
    )

    def available(self) -> tuple[bool, str]:
        try:
            from lxml import html  # noqa: F401

            return True, ""
        except ImportError:
            return False, "缺少 lxml：uv pip install lxml"

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> DocTree:
        import io
        import re

        from lxml import etree

        from . import markup_read as rd

        pol: Policy = options.get("_policy") or Policy()
        data = resources[0].read_bytes()

        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ConversionError(f"EPUB 不是有效的 ZIP：{exc}") from exc

        with zf:
            opf_path = self._find_opf(zf)
            opf = etree.fromstring(zf.read(opf_path))
            ns = {
                "opf": "http://www.idpf.org/2007/opf",
                "dc": "http://purl.org/dc/elements/1.1/",
            }

            title = self._first_text(opf, ".//dc:title", ns)
            author = self._first_text(opf, ".//dc:creator", ns)
            lang = self._first_text(opf, ".//dc:language", ns) or "zh-CN"

            # 按 spine 顺序读章节。manifest 的顺序不代表阅读顺序。
            base = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""
            id_to_href = {
                it.get("id"): it.get("href")
                for it in opf.findall(".//opf:manifest/opf:item", ns)
            }

            blocks: list[Block] = []
            chapters = 0
            for ref in opf.findall(".//opf:spine/opf:itemref", ns):
                href = id_to_href.get(ref.get("idref"))
                if not href:
                    continue
                full = f"{base}/{href}" if base else href
                full = re.sub(r"[^/]+/\.\./", "", full)  # 规范化 ../
                try:
                    chunk = zf.read(full)
                except KeyError:
                    continue
                sub = rd.parse_html(chunk)
                if sub.blocks:
                    blocks.extend(sub.blocks)
                    chapters += 1

        pol.note(
            "电子书结构",
            Strategy.DROP,
            f"{chapters} 章已合并为单一文档流，章节边界、封面和元数据未保留",
        )

        return DocTree(
            blocks=blocks, title=title, author=author, lang=lang, source_type=EPUB
        )

    def _find_opf(self, zf: zipfile.ZipFile) -> str:
        from lxml import etree

        try:
            container = etree.fromstring(zf.read("META-INF/container.xml"))
        except KeyError as exc:
            raise ConversionError("EPUB 缺少 META-INF/container.xml") from exc
        root = container.find(
            ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
        )
        if root is None or not root.get("full-path"):
            raise ConversionError("EPUB 的 container.xml 没指向 OPF")
        return root.get("full-path")

    def _first_text(self, el, xpath: str, ns: dict) -> str | None:
        found = el.find(xpath, ns)
        return (found.text or "").strip() or None if found is not None else None

    # ── 写 EPUB ──

    def write(
        self, ir: DocTree, target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        import io
        import uuid

        from . import markup_write as wr

        pol: Policy = options.get("_policy") or Policy()
        title = str(options.get("title") or ir.title or "未命名文档")
        author = str(options.get("author") or ir.author or "")
        lang = str(options.get("lang") or ir.lang or "zh-CN")

        chapters = self._split_chapters(ir)
        if not chapters:
            raise ConversionError("没有可写入的内容")

        book_id = f"urn:uuid:{uuid.uuid4()}"
        buf = io.BytesIO()

        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # mimetype 必须是第一个条目且**不压缩**，这是 EPUB 规范的硬要求，
            # 违反会导致部分阅读器直接拒绝打开
            zf.writestr(
                zipfile.ZipInfo("mimetype"), "application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )
            zf.writestr("META-INF/container.xml", _CONTAINER_XML)

            spine_items: list[tuple[str, str, str]] = []
            for i, (heading, blocks) in enumerate(chapters, 1):
                name = f"ch{i:03d}.xhtml"
                sub = DocTree(blocks=blocks, title=heading, lang=lang)
                body = wr.write_html(sub, pol, fragment=True)
                zf.writestr(
                    f"OEBPS/{name}",
                    _CHAPTER_XHTML.format(
                        lang=escape(lang), title=escape(heading or title), body=body
                    ),
                )
                spine_items.append((f"ch{i:03d}", name, heading or f"第 {i} 节"))

            zf.writestr("OEBPS/style.css", _EPUB_CSS)
            zf.writestr(
                "OEBPS/content.opf",
                self._opf(book_id, title, author, lang, spine_items),
            )
            zf.writestr("OEBPS/nav.xhtml", self._nav(title, lang, spine_items))

        pol.note(
            "章节划分",
            Strategy.APPROXIMATE,
            f"按一级标题切分为 {len(chapters)} 章"
            + ("（源文档无标题，全部内容作单章）" if len(chapters) == 1 and not chapters[0][0] else ""),
        )

        return [Resource(media_type=target, data=buf.getvalue())]

    def _split_chapters(self, ir: DocTree) -> list[tuple[str | None, list[Block]]]:
        """按一级标题切章。EPUB 需要章节划分，而 DocTree 是平的流。

        没有一级标题时退化成单章——这是有意的：强行按段落数切会得到毫无意义的
        章节边界，不如老实放一章。
        """
        chapters: list[tuple[str | None, list[Block]]] = []
        current: list[Block] = []
        heading: str | None = None

        top = min(
            (b.level for b in ir.blocks if b.kind == HEADING and b.level),
            default=1,
        )

        for b in ir.blocks:
            if b.kind == HEADING and b.level == top:
                if current:
                    chapters.append((heading, current))
                heading = b.text
                current = [b]
            else:
                current.append(b)

        if current:
            chapters.append((heading, current))
        return chapters

    def _opf(
        self, book_id: str, title: str, author: str, lang: str,
        items: list[tuple[str, str, str]],
    ) -> str:
        manifest = "\n".join(
            f'    <item id="{i}" href="{h}" media-type="application/xhtml+xml"/>'
            for i, h, _ in items
        )
        spine = "\n".join(f'    <itemref idref="{i}"/>' for i, _, _ in items)
        creator = (
            f"\n    <dc:creator>{escape(author)}</dc:creator>" if author else ""
        )
        import datetime

        now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{book_id}</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:language>{escape(lang)}</dc:language>{creator}
    <meta property="dcterms:modified">{now}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
{manifest}
  </manifest>
  <spine>
{spine}
  </spine>
</package>
"""

    def _nav(self, title: str, lang: str, items: list[tuple[str, str, str]]) -> str:
        lis = "\n".join(
            f'      <li><a href="{h}">{escape(label)}</a></li>'
            for _, h, label in items
        )
        return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="{escape(lang)}">
<head><meta charset="utf-8"/><title>{escape(title)}</title></head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>目录</h1>
    <ol>
{lis}
    </ol>
  </nav>
</body>
</html>
"""


_CONTAINER_XML = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_CHAPTER_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="{lang}">
<head><meta charset="utf-8"/><title>{title}</title>
<link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
{body}
</body>
</html>
"""

_EPUB_CSS = """body { line-height: 1.7; margin: 1em; }
h1, h2, h3 { line-height: 1.3; }
code { font-family: monospace; background: #f0f1f3; padding: .1em .3em; }
pre { background: #f6f8fa; padding: .8em; overflow-x: auto; }
blockquote { margin: 1em 0; padding-left: 1em; border-left: 3px solid #ccc; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #ccc; padding: .4em; }
img { max-width: 100%; }
"""
