"""标记语言领域的信息模型：语义文档树。

为什么单独一个域而不和 PDF 合并：**两者的信息模型根本不同。**

  PDF/DocumentRef  面向页面，内容固定在坐标上，有页码概念
  Markup/DocTree   面向流式，内容可重排，没有页的概念

Markdown 里不存在「第 3 页」，PDF 里不存在「这是二级标题」。强行合并会得到一个
半数字段永远为空的模型。所以分成两个域，用桥接边连接（PDF → 文本抽取 → DocTree）。

IR 取所有格式表达力的**并集**：
  - Markdown 有代码块和引用 → 进模型
  - HTML 有嵌套和任意属性   → 用 attrs 承载
  - DOCX 有样式名和对齐     → 进模型
  - EPUB 有章节划分         → 用 Block(kind="section") 表达

代价是每个 writer 都得处理自己表达不了的部分（TXT 没有粗体），但那正是
降级策略该管的事，而不是靠削弱 IR 来回避。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Span:
    """行内内容。

    为什么不把行内标记当成不透明字符串塞进 text：那样 Markdown→DOCX 时
    `**粗体**` 会变成字面的星号。行内格式必须结构化才能跨格式保真。
    """

    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    strike: bool = False
    href: str | None = None
    """非空表示这是链接。"""

    @property
    def is_plain(self) -> bool:
        return not (self.bold or self.italic or self.code or self.strike or self.href)


@dataclass(slots=True)
class Cell:
    """表格单元格。内含 blocks 而非纯文本，因为单元格里可以有列表和多段。"""

    blocks: list[Block] = field(default_factory=list)
    colspan: int = 1
    rowspan: int = 1
    header: bool = False

    @property
    def text(self) -> str:
        return " ".join(b.text for b in self.blocks).strip()


# 块级类型。用字符串而非枚举，因为格式特有的类型（如 EPUB 的 section）
# 应当能被扩展而不必改 IR 定义。
HEADING = "heading"
PARAGRAPH = "paragraph"
LIST = "list"
LIST_ITEM = "list_item"
CODE = "code"
QUOTE = "quote"
TABLE = "table"
IMAGE = "image"
RULE = "rule"
SECTION = "section"
RAW = "raw"
"""映射不上任何类型的原始内容。这是「不透明扩展槽」——HTML 里的 <video>
之类东西原样带过去，A→IR→A 往返才不劣化。"""


@dataclass(slots=True)
class Block:
    """块级元素。"""

    kind: str
    spans: list[Span] = field(default_factory=list)
    children: list[Block] = field(default_factory=list)
    """列表项、引用、章节的子内容。"""
    level: int = 0
    """标题层级 1-6；列表嵌套深度。"""
    ordered: bool = False
    lang: str | None = None
    """代码块语言。"""
    src: str | None = None
    """图片路径或 URL。"""
    alt: str | None = None
    rows: list[list[Cell]] = field(default_factory=list)
    style: str | None = None
    """源格式的样式名（DOCX 的 "Heading 1"），尽量透传。"""
    attrs: dict[str, Any] = field(default_factory=dict)
    """HTML 属性等映射不上通用字段的东西。"""

    @property
    def text(self) -> str:
        own = "".join(s.text for s in self.spans)
        if self.children:
            kid = "\n".join(c.text for c in self.children)
            return f"{own}\n{kid}".strip() if own else kid
        return own

    def plain_spans(self) -> list[Span]:
        """把 spans 压成单个无格式 Span。TXT writer 用。"""
        return [Span(self.text)] if self.text else []


@dataclass(slots=True)
class DocTree:
    """标记语言域 IR：语义文档树。"""

    blocks: list[Block] = field(default_factory=list)
    title: str | None = None
    author: str | None = None
    lang: str = "zh-CN"
    source_type: str | None = None
    assets: dict[str, bytes] = field(default_factory=dict)
    """内嵌资源：图片等。键是 IR 内部引用名，writer 负责落盘或内嵌。"""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def headings(self) -> list[Block]:
        """所有标题，用于生成目录。EPUB 和 DOCX 都需要。"""
        out: list[Block] = []

        def walk(blocks: list[Block]) -> None:
            for b in blocks:
                if b.kind == HEADING:
                    out.append(b)
                if b.children:
                    walk(b.children)

        walk(self.blocks)
        return out

    @property
    def table_count(self) -> int:
        return sum(1 for b in self._walk() if b.kind == TABLE)

    @property
    def image_count(self) -> int:
        return sum(1 for b in self._walk() if b.kind == IMAGE)

    def _walk(self):
        stack = list(self.blocks)
        while stack:
            b = stack.pop()
            yield b
            stack.extend(b.children)
            for row in b.rows:
                for cell in row:
                    stack.extend(cell.blocks)

    @property
    def word_count(self) -> int:
        import re

        text = "\n".join(b.text for b in self.blocks)
        # 中英混排：汉字按字计，西文按词计
        cjk = len(re.findall(r"[一-鿿]", text))
        words = len(re.findall(r"[A-Za-z0-9]+", text))
        return cjk + words

    def describe(self) -> str:
        bits = [f"{len(self.blocks)} 块"]
        if h := self.headings:
            bits.append(f"{len(h)} 标题")
        if self.table_count:
            bits.append(f"{self.table_count} 表格")
        if self.image_count:
            bits.append(f"{self.image_count} 图")
        bits.append(f"约 {self.word_count} 字")
        return " · ".join(bits)
