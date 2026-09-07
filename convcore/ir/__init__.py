"""分域信息模型（IR）。

每个领域一个模型，不做全局 IR。原则是取所有格式表达力的**并集**：宁可模型里
有某些格式用不到的字段，也不能让本可无损的一对在枢纽处丢信息。
"""

from .document import Confidence, DocumentRef, PageInfo
from .markup import Block, Cell, DocTree, Span
from .media import MediaPlan, Stream
from .raster import Frame, Raster

__all__ = [
    "Block",
    "Cell",
    "Confidence",
    "DocTree",
    "DocumentRef",
    "Frame",
    "MediaPlan",
    "PageInfo",
    "Raster",
    "Span",
    "Stream",
]
