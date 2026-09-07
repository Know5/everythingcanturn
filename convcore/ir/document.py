"""文档领域的信息模型。

和位图、音视频都不同的第三种形态：

- Raster 是真的解码数据（像素在内存里）
- MediaPlan 是转码计划（视频帧不可能物化）
- DocumentRef 介于两者之间：结构信息读进内存，页面内容按需渲染

PDF 的对象图极其复杂（xref 表、间接对象、内容流），为转换而完整解析它得不偿失。
所以这里只携带「转换决策需要的信息」：页数、每页尺寸、有没有文本层、有没有表格、
是否加密。真正的内容操作交给 pypdf（结构）和 pymupdf（渲染）。

关键判断是 has_text_layer：
  有文本层 → PDF 是数字生成的，可以提取文字，转 Word 是版面推断
  无文本层 → PDF 是扫描件，只有图像，转 Word 必须先 OCR
这两条路的保真度和失败模式完全不同，混在一起处理必然出错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..types import Resource


@dataclass(slots=True)
class PageInfo:
    """一页的结构摘要。"""

    index: int
    width: float
    """点（1/72 英寸）为单位。A4 是 595×842。"""
    height: float
    rotation: int = 0
    char_count: int = 0
    """文本层字符数。0 说明这页是纯图像（扫描件或纯图排版）。"""
    image_count: int = 0
    table_count: int = 0
    has_vector: bool = False

    @property
    def is_landscape(self) -> bool:
        # 旋转 90/270 时实际显示方向要翻过来
        w, h = (self.height, self.width) if self.rotation in (90, 270) else (self.width, self.height)
        return w > h

    @property
    def is_scanned(self) -> bool:
        """这页大概是扫描的：几乎没有文本但有图像。

        阈值取 8 个字符——扫描件常带页码水印之类的少量文本层，
        完全按 0 判断会漏掉。
        """
        return self.char_count < 8 and self.image_count > 0

    def describe(self) -> str:
        bits = [f"{self.width:.0f}×{self.height:.0f}pt"]
        if self.rotation:
            bits.append(f"旋转{self.rotation}°")
        if self.char_count:
            bits.append(f"{self.char_count}字")
        if self.image_count:
            bits.append(f"{self.image_count}图")
        if self.table_count:
            bits.append(f"{self.table_count}表")
        return " · ".join(bits)


@dataclass(slots=True)
class DocumentRef:
    """文档域 IR：源文件引用 + 结构摘要。

    带 source 引用的理由同 MediaPlan——pypdf/pymupdf 要读原始文件，
    而完整解析 PDF 对象图放进内存既慢又没必要。
    """

    source: Resource
    pages: list[PageInfo] = field(default_factory=list)
    encrypted: bool = False
    """加密且未解密。此时页数可能拿不到，转换必须先要密码。"""
    title: str | None = None
    author: str | None = None
    producer: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def has_text_layer(self) -> bool:
        """有可提取的文本。决定转 Word 走版面推断还是必须 OCR。"""
        return any(p.char_count >= 8 for p in self.pages)

    @property
    def scanned_pages(self) -> list[int]:
        return [p.index for p in self.pages if p.is_scanned]

    @property
    def is_scanned_doc(self) -> bool:
        """整份都是扫描件。转 Word 需要 OCR，否则只能得到一堆图片。"""
        if not self.pages:
            return False
        return len(self.scanned_pages) >= len(self.pages) * 0.8

    @property
    def table_count(self) -> int:
        return sum(p.table_count for p in self.pages)

    def describe(self) -> str:
        if self.encrypted:
            return "已加密（需要密码）"
        bits = [f"{self.page_count} 页"]
        if self.pages:
            first = self.pages[0]
            bits.append(f"{first.width:.0f}×{first.height:.0f}pt")
        if self.table_count:
            bits.append(f"{self.table_count} 个表格")
        if self.is_scanned_doc:
            bits.append("扫描件（无文本层）")
        elif self.scanned_pages:
            bits.append(f"{len(self.scanned_pages)} 页为扫描图")
        return " · ".join(bits)


@dataclass(slots=True)
class Confidence:
    """一个重建元素的置信度。

    学 FlyingMouse 的做法：PDF→Word 是推断，低置信度的部分应当在产物里
    标出来让人复核，而不是静默输出一个可能错的文档。这比假装转换完美更负责。
    """

    kind: str
    """paragraph / table / image / heading"""
    page: int
    score: float
    note: str = ""

    @property
    def needs_review(self) -> bool:
        return self.score < 0.85
