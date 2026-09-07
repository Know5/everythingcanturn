"""标记语言解析：Markdown / HTML / TXT → DocTree。

Markdown 用 mistune 的 AST 模式而不是先转 HTML 再解析——少一次有损中转。
mistune 是 BSD 授权，纯 Python。

HTML 用 lxml 遍历。不用 markdownify 之类的库是因为它们直接输出 Markdown 字符串，
而我们要的是结构化 IR——中间落成 Markdown 会丢掉 HTML 特有的属性。
"""

from __future__ import annotations

from ..ir.markup import (
    CODE,
    HEADING,
    IMAGE,
    LIST,
    LIST_ITEM,
    PARAGRAPH,
    QUOTE,
    RAW,
    RULE,
    TABLE,
    Block,
    Cell,
    DocTree,
    Span,
)

# ── Markdown ──


def _md_spans(children: list[dict]) -> list[Span]:
    """把 mistune 的行内 AST 压成 Span 列表。

    递归展开嵌套格式（粗体里套斜体），把格式状态沿途累积到叶子文本上。
    """
    out: list[Span] = []

    def walk(nodes: list[dict], **fmt) -> None:
        for n in nodes:
            t = n.get("type")
            kids = n.get("children") or []

            if t == "text":
                if raw := n.get("raw", ""):
                    out.append(Span(raw, **fmt))
            elif t == "strong":
                walk(kids, **{**fmt, "bold": True})
            elif t == "emphasis":
                walk(kids, **{**fmt, "italic": True})
            elif t == "strikethrough":
                walk(kids, **{**fmt, "strike": True})
            elif t == "codespan":
                out.append(Span(n.get("raw", ""), code=True, **fmt))
            elif t == "link":
                href = (n.get("attrs") or {}).get("url")
                walk(kids, **{**fmt, "href": href})
            elif t == "image":
                # 行内图片：转成带 alt 的文本，块级图片由 _md_block 处理
                alt = "".join(c.get("raw", "") for c in kids)
                out.append(Span(alt or "[图片]", **fmt))
            elif t in ("linebreak", "softbreak"):
                out.append(Span(" ", **fmt))
            elif t == "inline_html":
                out.append(Span(n.get("raw", ""), **fmt))
            elif kids:
                walk(kids, **fmt)

    walk(children)
    return out


def _md_block(node: dict) -> Block | None:
    """单个块级 AST 节点 → Block。"""
    t = node.get("type")
    kids = node.get("children") or []
    attrs = node.get("attrs") or {}

    if t == "heading":
        return Block(HEADING, spans=_md_spans(kids), level=int(attrs.get("level", 1)))

    if t == "paragraph":
        # 只含一张图片的段落当成块级图片，这是 Markdown 的惯例写法
        if len(kids) == 1 and kids[0].get("type") == "image":
            img = kids[0]
            ia = img.get("attrs") or {}
            alt = "".join(c.get("raw", "") for c in (img.get("children") or []))
            return Block(IMAGE, src=ia.get("url"), alt=alt or None)
        return Block(PARAGRAPH, spans=_md_spans(kids))

    if t == "block_code":
        return Block(
            CODE,
            spans=[Span(node.get("raw", ""))],
            lang=attrs.get("info") or None,
        )

    if t == "block_quote":
        return Block(QUOTE, children=_md_blocks(kids))

    if t == "list":
        return Block(
            LIST,
            ordered=bool(attrs.get("ordered")),
            level=int(attrs.get("depth", 0)),
            children=_md_blocks(kids),
        )

    if t == "list_item":
        return Block(LIST_ITEM, children=_md_blocks(kids))

    if t == "block_text":
        # 列表项内部的文本节点，当段落处理
        return Block(PARAGRAPH, spans=_md_spans(kids))

    if t == "thematic_break":
        return Block(RULE)

    if t == "table":
        return _md_table(kids)

    if t == "block_html":
        # 映射不上的原始 HTML 原样带过去——这是不透明扩展槽的用途
        return Block(RAW, spans=[Span(node.get("raw", ""))], attrs={"format": "html"})

    if t == "blank_line":
        return None

    if kids:
        return Block(PARAGRAPH, spans=_md_spans(kids))
    return None


def _md_table(children: list[dict]) -> Block:
    rows: list[list[Cell]] = []
    for section in children:
        st = section.get("type")
        is_head = st == "table_head"
        # table_head 的子节点直接是 cell，table_body 的子节点是 row
        row_nodes = [section] if is_head else (section.get("children") or [])
        for rn in row_nodes:
            cells: list[Cell] = []
            for cn in rn.get("children") or []:
                spans = _md_spans(cn.get("children") or [])
                cells.append(
                    Cell(blocks=[Block(PARAGRAPH, spans=spans)], header=is_head)
                )
            if cells:
                rows.append(cells)
    return Block(TABLE, rows=rows)


def _md_blocks(nodes: list[dict]) -> list[Block]:
    out: list[Block] = []
    for n in nodes:
        if (b := _md_block(n)) is not None:
            out.append(b)
    return out


def parse_markdown(text: str, source_type: str | None = None) -> DocTree:
    """Markdown → DocTree。"""
    import mistune

    md = mistune.create_markdown(
        renderer=None,  # None 表示输出 AST 而不是 HTML
        plugins=["strikethrough", "table", "url"],
    )
    ast = md(text)
    if not isinstance(ast, list):
        ast = []

    tree = DocTree(blocks=_md_blocks(ast), source_type=source_type)

    # 首个一级标题当文档标题——EPUB 和 DOCX 都需要文档级标题
    for b in tree.blocks:
        if b.kind == HEADING and b.level == 1:
            tree.title = b.text
            break
    return tree


def parse_text(text: str, source_type: str | None = None) -> DocTree:
    """纯文本 → DocTree。空行分段，这是唯一可靠的结构信号。"""
    blocks: list[Block] = []
    for chunk in text.replace("\r\n", "\n").split("\n\n"):
        chunk = chunk.strip()
        if chunk:
            blocks.append(Block(PARAGRAPH, spans=[Span(chunk)]))
    return DocTree(blocks=blocks, source_type=source_type)


# ── HTML ──

_INLINE_FMT = {
    "b": "bold", "strong": "bold",
    "i": "italic", "em": "italic",
    "code": "code", "tt": "code",
    "s": "strike", "del": "strike", "strike": "strike",
}

_SKIP_TAGS = frozenset({"script", "style", "head", "meta", "link", "noscript"})

_BLOCK_TAGS = frozenset(
    {
        "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
        "pre", "blockquote", "table", "hr", "img", "section", "article",
        "figure", "figcaption", "br",
    }
)


def _html_spans(el, inherited: dict | None = None) -> list[Span]:
    """收集元素的行内内容，格式沿树累积。"""
    out: list[Span] = []
    fmt = dict(inherited or {})

    if el.text and el.text.strip():
        out.append(Span(el.text, **fmt))

    for child in el:
        tag = str(child.tag).lower() if isinstance(child.tag, str) else ""
        if tag in _SKIP_TAGS:
            continue

        # 遇到块级子元素就停——它该由 _html_blocks 单独处理
        if tag in _BLOCK_TAGS and tag not in ("br", "img"):
            continue

        kid_fmt = dict(fmt)
        if key := _INLINE_FMT.get(tag):
            kid_fmt[key] = True
        if tag == "a" and (href := child.get("href")):
            kid_fmt["href"] = href
        if tag == "br":
            out.append(Span(" ", **fmt))

        out.extend(_html_spans(child, kid_fmt))

        if child.tail and child.tail.strip():
            out.append(Span(child.tail, **fmt))

    # 合并相邻的同格式 Span，避免碎成一堆
    merged: list[Span] = []
    for s in out:
        if not s.text:
            continue
        if merged and _same_fmt(merged[-1], s):
            merged[-1].text += s.text
        else:
            merged.append(s)
    for s in merged:
        s.text = " ".join(s.text.split()) if not s.code else s.text
    return [s for s in merged if s.text]


def _same_fmt(a: Span, b: Span) -> bool:
    return (
        a.bold == b.bold and a.italic == b.italic
        and a.code == b.code and a.strike == b.strike and a.href == b.href
    )


def _html_blocks(el) -> list[Block]:
    out: list[Block] = []

    for child in el:
        if not isinstance(child.tag, str):
            continue  # 注释、处理指令
        tag = child.tag.lower()
        if tag in _SKIP_TAGS:
            continue

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            out.append(Block(HEADING, spans=_html_spans(child), level=int(tag[1])))

        elif tag == "p":
            spans = _html_spans(child)
            imgs = child.findall(".//img")
            if imgs and not spans:
                for im in imgs:
                    out.append(Block(IMAGE, src=im.get("src"), alt=im.get("alt")))
            elif spans:
                out.append(Block(PARAGRAPH, spans=spans))

        elif tag == "pre":
            code_el = child.find("code")
            target = code_el if code_el is not None else child
            text = "".join(target.itertext())
            lang = None
            if cls := (target.get("class") or ""):
                for c in cls.split():
                    if c.startswith("language-"):
                        lang = c[len("language-"):]
            out.append(Block(CODE, spans=[Span(text)], lang=lang))

        elif tag == "blockquote":
            out.append(Block(QUOTE, children=_html_blocks(child)))

        elif tag in ("ul", "ol"):
            items = [
                Block(LIST_ITEM, spans=_html_spans(li), children=_html_blocks(li))
                for li in child.findall("li")
            ]
            out.append(Block(LIST, ordered=tag == "ol", children=items))

        elif tag == "table":
            out.append(_html_table(child))

        elif tag == "hr":
            out.append(Block(RULE))

        elif tag == "img":
            out.append(Block(IMAGE, src=child.get("src"), alt=child.get("alt")))

        elif tag in ("div", "section", "article", "figure", "main", "body"):
            # 容器：先看有没有块级子元素，没有就把自己当段落
            nested = _html_blocks(child)
            if nested:
                out.extend(nested)
            elif (spans := _html_spans(child)):
                out.append(Block(PARAGRAPH, spans=spans))

        elif tag in ("video", "audio", "iframe", "svg", "canvas", "form"):
            # 表达不了的东西原样留在 RAW 里，往返才不丢
            from lxml import etree

            out.append(
                Block(
                    RAW,
                    spans=[Span(etree.tostring(child, encoding="unicode")[:4000])],
                    attrs={"format": "html", "tag": tag},
                )
            )

        else:
            if (spans := _html_spans(child)):
                out.append(Block(PARAGRAPH, spans=spans))

    return out


def _html_table(el) -> Block:
    rows: list[list[Cell]] = []
    for tr in el.findall(".//tr"):
        cells: list[Cell] = []
        for td in tr:
            if not isinstance(td.tag, str):
                continue
            tag = td.tag.lower()
            if tag not in ("td", "th"):
                continue
            spans = _html_spans(td)
            cells.append(
                Cell(
                    blocks=[Block(PARAGRAPH, spans=spans)] if spans else [],
                    colspan=int(td.get("colspan") or 1),
                    rowspan=int(td.get("rowspan") or 1),
                    header=tag == "th",
                )
            )
        if cells:
            rows.append(cells)
    return Block(TABLE, rows=rows)


def parse_html(data: bytes | str, source_type: str | None = None) -> DocTree:
    """HTML → DocTree。用 lxml 的容错解析器，真实网页很少是合规 XML。"""
    from lxml import html as lhtml

    if isinstance(data, bytes):
        doc = lhtml.fromstring(data) if data.strip() else lhtml.fromstring(b"<html/>")
    else:
        doc = lhtml.fromstring(data or "<html/>")

    title = None
    if (t := doc.find(".//title")) is not None and t.text:
        title = t.text.strip()

    body = doc.find(".//body")
    root = body if body is not None else doc

    tree = DocTree(blocks=_html_blocks(root), title=title, source_type=source_type)
    if tree.title is None:
        for b in tree.blocks:
            if b.kind == HEADING and b.level == 1:
                tree.title = b.text
                break
    return tree
