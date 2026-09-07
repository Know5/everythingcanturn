"""位图领域的信息模型。

设计要点（都是「并集而非交集」原则的体现）：

- frames 是列表而不是单张图：GIF/WebP/APNG 是动图，TIFF/ICO 是多页/多尺寸。
  如果 IR 只存一张图，动图转动图会退化成单帧——这是同类工具最常见的缺陷。
- icc_profile 和 exif 原样保存：它们映射不到任何通用字段，但丢了就意味着
  色彩偏移和照片方向错误。这就是「不透明扩展槽」。
- 像素统一由 Pillow 的 Image 持有，不自己造像素容器：Pillow 已经处理了各种
  模式（L/RGB/RGBA/P/CMYK/I;16），重新发明只会漏掉边角情况。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL import Image


@dataclass(slots=True)
class Frame:
    """一帧图像。"""

    image: Image.Image
    duration_ms: int = 0
    """动图帧延时。静态图为 0。"""

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size

    @property
    def has_alpha(self) -> bool:
        return self.image.mode in ("RGBA", "LA", "PA") or (
            self.image.mode == "P" and "transparency" in self.image.info
        )


@dataclass(slots=True)
class Raster:
    """位图领域的 IR：一组帧加上跨帧共享的元数据。"""

    frames: list[Frame]
    icc_profile: bytes | None = None
    exif: bytes | None = None
    loop: int = 0
    """动图循环次数，0 表示无限。"""
    source_type: str | None = None
    """来源 MIME，用于判定「同格式重写」这类短路，以及生成告警文案。"""
    extra: dict[str, Any] = field(default_factory=dict)
    """不透明扩展槽：XMP、IPTC 等映射不到通用字段的东西原样带过去。"""

    def __post_init__(self) -> None:
        if not self.frames:
            raise ValueError("Raster 至少要有一帧")

    @property
    def primary(self) -> Frame:
        return self.frames[0]

    @property
    def size(self) -> tuple[int, int]:
        return self.primary.size

    @property
    def is_animated(self) -> bool:
        return len(self.frames) > 1 and any(f.duration_ms > 0 for f in self.frames)

    @property
    def has_alpha(self) -> bool:
        return any(f.has_alpha for f in self.frames)

    def map_frames(self, fn) -> Raster:
        """对每帧应用变换，保留元数据。缩放、去透明都走这里。"""
        return Raster(
            frames=[Frame(fn(f.image), f.duration_ms) for f in self.frames],
            icc_profile=self.icc_profile,
            exif=self.exif,
            loop=self.loop,
            source_type=self.source_type,
            extra=dict(self.extra),
        )
