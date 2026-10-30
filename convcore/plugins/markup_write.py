"""DocTree → Markdown / HTML / TXT。

每个 writer 都要面对自己表达不了的东西，处理方式必须显式记进 policy：
  TXT      丢掉全部行内格式和链接（DROP），表格用空格对齐（EMULATE）
  Markdown 嵌套表格和 colspan 表达不了（DROP）
  HTML     几乎全都能表达，最接近无损
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
    DocTree,
    Span,
)
from ..policy import Policy, Strategy

# ── Markdown ──


def _md_inline(spans: list[Span]) -> str:
    out: list[str] = []
    for s in spans:
        t = s.text
        if not t:
            continue
        # 转义会被当标记的字符，否则往返时 * 会变成强调
        if not s.code:
            for ch in ("\\", "*", "_", "`", "[", "]"):
                t = t.replace(ch, "\\" + ch)
        if s.code:
            t = f"`{t}`"
        if s.bold:
            t = f"**{t}**"
        if s.italic:
            t = f"*{t}*"
        if s.strike:
            t = f"~~{t}~~"
        if s.href:
            t = f"[{t}]({s.href})"
        out.append(t)
    return "".join(out)


def _md_block(b: Block, pol: Policy, depth: int = 0) -> list[str]:
    ind = "  " * depth

    if b.kind == HEADING:
        return [f"{'#' * max(1, min(6, b.level or 1))} {_md_inline(b.spans)}"]

    if b.kind == PARAGRAPH:
        text = _md_inline(b.spans)
        return [ind + text] if text else []

    if b.kind == CODE:
        fence = "```"
        body = "".join(s.text for s in b.spans).rstrip("\n")
        return [f"{fence}{b.lang or ''}", body, fence]

    if b.kind == QUOTE:
        lines: list[str] = []
        for kid in b.children:
            for ln in _md_block(kid, pol, depth):
                lines.append(f"> {ln}" if ln else ">")
        return lines

    if b.kind == LIST:
        lines = []
        for i, item in enumerate(b.children, 1):
            marker = f"{i}." if b.ordered else "-"
            inner = _md_block(item, pol, depth + 1)
            first = inner[0].lstrip() if inner else ""
            lines.append(f"{ind}{marker} {first}")
            lines.extend(inner[1:])
        return lines

    if b.kind == LIST_ITEM:
        lines = []
        if b.spans:
            lines.append(ind + _md_inline(b.spans))
        for kid in b.children:
            lines.extend(_md_block(kid, pol, depth))
        return lines

    if b.kind == TABLE:
        return _md_table(b, pol)

    if b.kind == IMAGE:
        return [f"![{b.alt or ''}]({b.src or ''})"]

    if b.kind == RULE:
        return ["---"]

    if b.kind == RAW:
        # Markdown 允许内嵌 HTML，所以原始内容能原样保留
        return ["".join(s.text for s in b.spans)]

    text = _md_inline(b.spans)
    return [ind + text] if text else []


def _md_table(b: Block, pol: Policy) -> list[str]:
    if not b.rows:
        return []

    if any(c.colspan > 1 or c.rowspan > 1 for row in b.rows for c in row):
        pol.note(
            "表格合并单元格",
            Strategy.DROP,
            "Markdown 表格不支持单元格合并，已按普通单元格展开",
        )

    width = max(len(r) for r in b.rows)

    def cell_text(c) -> str:
        parts: list[str] = []
        for block in c.blocks:
            if block.kind == IMAGE:
                parts.append(f"![{block.alt or ''}]({block.src or ''})")
            elif block.text:
                parts.append(block.text)
        return " ".join(parts).strip()

    def fmt_row(cells) -> str:
        vals = [cell_text(c).replace("|", "\\|").replace("\n", " ") for c in cells]
        vals += [""] * (width - len(vals))
        return "| " + " | ".join(vals) + " |"

    lines = [fmt_row(b.rows[0])]
    lines.append("|" + "|".join([" --- "] * width) + "|")
    for row in b.rows[1:]:
        lines.append(fmt_row(row))
    return lines


def write_markdown(tree: DocTree, pol: Policy) -> str:
    out: list[str] = []
    for b in tree.blocks:
        chunk = _md_block(b, pol)
        if chunk:
            out.extend(chunk)
            out.append("")
    return "\n".join(out).rstrip() + "\n"


# ── HTML ──


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _html_inline(spans: list[Span]) -> str:
    out: list[str] = []
    for s in spans:
        t = _esc(s.text)
        if not t:
            continue
        if s.code:
            t = f"<code>{t}</code>"
        if s.bold:
            t = f"<strong>{t}</strong>"
        if s.italic:
            t = f"<em>{t}</em>"
        if s.strike:
            t = f"<del>{t}</del>"
        if s.href:
            t = f'<a href="{_esc(s.href)}">{t}</a>'
        out.append(t)
    return "".join(out)


def _html_block(b: Block, indent: int = 0) -> list[str]:
    pad = "  " * indent

    if b.kind == HEADING:
        lv = max(1, min(6, b.level or 1))
        return [f"{pad}<h{lv}>{_html_inline(b.spans)}</h{lv}>"]

    if b.kind == PARAGRAPH:
        inner = _html_inline(b.spans)
        return [f"{pad}<p>{inner}</p>"] if inner else []

    if b.kind == CODE:
        body = _esc("".join(s.text for s in b.spans).rstrip("\n"))
        cls = f' class="language-{_esc(b.lang)}"' if b.lang else ""
        return [f"{pad}<pre><code{cls}>{body}</code></pre>"]

    if b.kind == QUOTE:
        lines = [f"{pad}<blockquote>"]
        for kid in b.children:
            lines.extend(_html_block(kid, indent + 1))
        lines.append(f"{pad}</blockquote>")
        return lines

    if b.kind == LIST:
        tag = "ol" if b.ordered else "ul"
        lines = [f"{pad}<{tag}>"]
        for item in b.children:
            inner = _html_inline(item.spans)
            kids = [ln for kid in item.children for ln in _html_block(kid, indent + 2)]
            if kids:
                lines.append(f"{pad}  <li>{inner}")
                lines.extend(kids)
                lines.append(f"{pad}  </li>")
            else:
                lines.append(f"{pad}  <li>{inner}</li>")
        lines.append(f"{pad}</{tag}>")
        return lines

    if b.kind == TABLE:
        lines = [f"{pad}<table>"]
        for row in b.rows:
            lines.append(f"{pad}  <tr>")
            for c in row:
                tag = "th" if c.header else "td"
                attrs = ""
                if c.colspan > 1:
                    attrs += f' colspan="{c.colspan}"'
                if c.rowspan > 1:
                    attrs += f' rowspan="{c.rowspan}"'
                parts: list[str] = []
                for block in c.blocks:
                    if block.kind == PARAGRAPH:
                        parts.append(_html_inline(block.spans))
                    elif block.kind == IMAGE:
                        parts.append(
                            f'<img src="{_esc(block.src or "")}" '
                            f'alt="{_esc(block.alt or "")}">'
                        )
                    else:
                        parts.extend(
                            line.strip() for line in _html_block(block, 0)
                        )
                inner = "".join(parts)
                lines.append(
                    f"{pad}    <{tag}{attrs}>{inner or _esc(c.text)}</{tag}>"
                )
            lines.append(f"{pad}  </tr>")
        lines.append(f"{pad}</table>")
        return lines

    if b.kind == IMAGE:
        alt = _esc(b.alt or "")
        return [f'{pad}<img src="{_esc(b.src or "")}" alt="{alt}">']

    if b.kind == RULE:
        return [f"{pad}<hr>"]

    if b.kind == RAW:
        return [pad + "".join(s.text for s in b.spans)]

    inner = _html_inline(b.spans)
    return [f"{pad}<p>{inner}</p>"] if inner else []


def write_html(tree: DocTree, pol: Policy, *, fragment: bool = False) -> str:
    body: list[str] = []
    for b in tree.blocks:
        body.extend(_html_block(b, 1 if not fragment else 0))

    if fragment:
        return "\n".join(body) + "\n"

    title = _esc(tree.title or "Document")
    # 内联样式而不是外链 CSS：产物必须是单文件自包含的，
    # 否则用户拷走 HTML 会发现样式没了
    return f"""<!DOCTYPE html>
<html lang="{_esc(tree.lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ max-width: 46em; margin: 2.5em auto; padding: 0 1.2em;
  font: 16px/1.7 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  color: #24292f; }}
h1, h2, h3, h4 {{ line-height: 1.3; margin-top: 1.6em; }}
code {{ background: #f0f1f3; padding: .15em .35em; border-radius: 3px;
  font-size: .9em; }}
pre {{ background: #f6f8fa; padding: 1em; overflow-x: auto; border-radius: 6px; }}
pre code {{ background: none; padding: 0; }}
blockquote {{ margin: 1em 0; padding-left: 1em; border-left: 4px solid #d0d7de;
  color: #57606a; }}
table {{ border-collapse: collapse; width: 100%; margin: 1.2em 0; }}
th, td {{ border: 1px solid #d0d7de; padding: .5em .7em; text-align: left; }}
th {{ background: #f6f8fa; }}
img {{ max-width: 100%; height: auto; }}
hr {{ border: 0; border-top: 1px solid #d0d7de; margin: 2em 0; }}
</style>
</head>
<body>
{chr(10).join(body)}
</body>
</html>
"""


# ── 纯文本 ──


def _txt_table(b: Block, pol: Policy) -> list[str]:
    """表格用空格对齐。这是 EMULATE 而非 DROP——结构没了但可读性保住了。"""
    if not b.rows:
        return []

    width = max(len(r) for r in b.rows)
    grid = [
        [c.text.replace("\n", " ") for c in row] + [""] * (width - len(row))
        for row in b.rows
    ]
    # 按显示宽度对齐：中文字符占两格，不算的话中文表格会歪
    def disp(s: str) -> int:
        return sum(2 if "ᄀ" <= ch <= "ￜ" and not ch.isascii() else 1 for ch in s)

    cols = [max(disp(row[i]) for row in grid) for i in range(width)]

    def pad(s: str, w: int) -> str:
        return s + " " * max(0, w - disp(s))

    lines = []
    for r, row in enumerate(grid):
        lines.append("  ".join(pad(v, cols[i]) for i, v in enumerate(row)).rstrip())
        if r == 0:
            lines.append("  ".join("-" * c for c in cols))
    return lines


def write_text(tree: DocTree, pol: Policy) -> str:
    has_fmt = any(
        not s.is_plain for b in tree.blocks for s in b.spans
    )
    if has_fmt:
        pol.note(
            "行内格式",
            Strategy.DROP,
            "纯文本不支持粗体、斜体和链接，已丢弃格式仅保留文字",
        )
    if tree.table_count:
        pol.note(
            "表格",
            Strategy.EMULATE,
            f"{tree.table_count} 个表格已用空格对齐模拟，不再是结构化表格",
        )
    if tree.image_count:
        pol.note(
            "图片",
            Strategy.DROP,
            f"{tree.image_count} 张图片已替换为 alt 文字占位",
        )

    out: list[str] = []

    def emit(b: Block, depth: int = 0) -> None:
        ind = "  " * depth
        if b.kind == HEADING:
            text = b.text
            out.append(text)
            # 用下划线标出层级，纯文本里这是唯一能表达标题的办法
            out.append(("=" if (b.level or 1) == 1 else "-") * max(4, len(text)))
        elif b.kind == CODE:
            out.extend(ind + ln for ln in "".join(s.text for s in b.spans).splitlines())
        elif b.kind == TABLE:
            out.extend(_txt_table(b, pol))
        elif b.kind == IMAGE:
            out.append(f"{ind}[图片：{b.alt or b.src or '无说明'}]")
        elif b.kind == RULE:
            out.append("-" * 40)
        elif b.kind == QUOTE:
            for kid in b.children:
                for ln in kid.text.splitlines():
                    out.append(f"{ind}> {ln}")
        elif b.kind == LIST:
            for i, item in enumerate(b.children, 1):
                # 用 ASCII 连字符而非 •：纯文本最可能被老编辑器或 GBK 环境打开，
                # 非 ASCII 符号在那里会变成乱码
                marker = f"{i}." if b.ordered else "-"
                out.append(f"{ind}{marker} {item.text}".rstrip())
        elif b.kind == RAW:
            return  # 原始 HTML 在纯文本里没意义
        else:
            if text := b.text:
                out.append(ind + text)
        out.append("")

    for b in tree.blocks:
        emit(b)
    return "\n".join(out).rstrip() + "\n"
