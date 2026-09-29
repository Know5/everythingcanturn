"""核心类型：资源、损失等级、能力声明。

这些类型是整个引擎的词汇表。设计要点：
- Resource 既可以是磁盘文件也可以是内存字节，转换链的中间产物常常不必落盘。
- LossTier 是可比较的整数枚举，因为图搜索要拿它当权重比大小。
- Capability 把「读/写某个格式的代价」显式声明出来，注册表和图搜索都靠它。
"""

from __future__ import annotations

__all__ = [
    "MediaType",
    "Domain",
    "LossTier",
    "Resource",
    "Capability",
    "Codec",
    "DirectConverter",
    "ConvertRequest",
    "ConvertResult",
    "ConversionError",
    "Cancelled",
    "NoPathError",
]

from dataclasses import dataclass, field, replace
from enum import IntEnum
from pathlib import Path
from typing import Any

# MIME 类型字符串，例如 "image/png"。用 MIME 而不是扩展名做内部标识，
# 因为扩展名有别名（jpg/jpeg/jpe）且不可靠。
MediaType = str

# 领域标识，例如 "raster"、"media"、"document"。同域内可经枢纽互转，
# 跨域需要显式桥接声明。
Domain = str


class LossTier(IntEnum):
    """信息损失等级。数值越小越保真，作为图搜索的首要权重。

    这个枚举是整个项目最重要的设计决策：把「转换会丢什么」变成可计算的量，
    于是路径搜索能自动偏好保真路线，UI 也能在转换前就警告用户。
    """

    COPY = 0
    """改容器不重编码（MKV→MP4 remux）、或纯字节复制。零损失且极快。"""

    LOSSLESS = 1
    """需要重新编码但数学上无损（PNG→BMP、WAV→FLAC）。"""

    LOSSY = 2
    """有损压缩（PNG→JPEG、WAV→MP3）。像素/采样会变，但语义完整。"""

    STRUCTURAL = 3
    """丢结构（DOCX→TXT 丢样式和图片、矢量→位图丢可缩放性）。"""

    INFERRED = 4
    """需要推断而非翻译（位图→矢量、TXT→排版文档）。不可逆，且结果可能不正确。"""

    @property
    def label(self) -> str:
        return {
            LossTier.COPY: "无损·免重编码",
            LossTier.LOSSLESS: "无损",
            LossTier.LOSSY: "有损压缩",
            LossTier.STRUCTURAL: "丢失结构",
            LossTier.INFERRED: "需要推断·不可逆",
        }[self]

    @property
    def is_reversible(self) -> bool:
        """能否期待 A→B→A 还原。UI 用它决定是否显示不可逆警告。"""
        return self <= LossTier.LOSSLESS


@dataclass(slots=True)
class Resource:
    """一个待转换或已产出的资源。

    path 和 data 二者至少有一个。中间产物优先留在内存里（data），只有当引擎
    必须读文件时才落盘——外部引擎如 ffmpeg 只能吃文件，纯 Python 插件不必。
    """

    media_type: MediaType
    path: Path | None = None
    data: bytes | None = None
    name: str | None = None
    """建议的输出文件名（不含目录），用于一进多出时生成 page-001.png 这类名字。"""

    def __post_init__(self) -> None:
        if self.path is None and self.data is None:
            raise ValueError("Resource 必须提供 path 或 data 之一")
        if self.name is None and self.path is not None:
            self.name = self.path.name

    @property
    def size(self) -> int:
        if self.data is not None:
            return len(self.data)
        assert self.path is not None
        return self.path.stat().st_size

    def read_bytes(self) -> bytes:
        """拿到字节内容，不管它原本在内存还是磁盘。

        对于磁盘文件会检查大小：超过 1GB 的文档/数据/图片会抛出异常，
        避免内存耗尽。音视频（media 域）不受此限，因为它们走 FFmpeg 文件流。
        """
        if self.data is not None:
            return self.data
        assert self.path is not None

        # 大文件内存保护：在所有调用路径上设防
        size = self.path.stat().st_size
        max_bytes = 1 << 30  # 1 GiB

        if size > max_bytes:
            from .formats import domain_of
            domain = domain_of(self.media_type)
            # 音视频走 FFmpeg 文件流，不物化到 Python 内存
            if domain != "media":
                from . import ConversionError
                raise ConversionError(
                    f"文件过大：{self.path.name}（{size / (1 << 30):.2f} GB），"
                    f"内存加载上限 1 GB。音视频不受此限。"
                )

        return self.path.read_bytes()

    def materialize(self, tmpdir: Path) -> Path:
        """确保内容在磁盘上并返回路径。外部引擎调用前必须走这一步。"""
        if self.path is not None:
            return self.path
        assert self.data is not None
        from .formats import primary_extension

        suffix = primary_extension(self.media_type) or ".bin"
        target = tmpdir / f"{id(self):x}{suffix}"
        target.write_bytes(self.data)
        self.path = target
        return target

    def with_type(self, media_type: MediaType) -> Resource:
        return replace(self, media_type=media_type)


@dataclass(frozen=True, slots=True)
class Capability:
    """插件对「我能读/写这个格式，代价是这样」的声明。

    time_cost 是相对量级而非秒数：1 表示典型的纯内存操作，10 表示要起子进程，
    100 表示 LibreOffice 冷启动这种。只用于同损失等级下的路径排序，不必精确。
    """

    media_type: MediaType
    loss: LossTier = LossTier.LOSSLESS
    time_cost: float = 1.0
    options: frozenset[str] = frozenset()
    """该格式支持的选项名，例如 quality、lossless。用于 CLI/UI 校验与提示。"""
    fidelity: float = 1.0
    """同一损失等级内部的相对保真度，1.0 最好。

    存在理由：LossTier 是粗粒度的序数，无法区分「同样是推断，但一个还原表格
    一个只抽文本」。没有这个维度时，图搜索会在同等级里挑最快的——而最快的
    往往是最糙的。PDF→Word 就踩过这个坑：抽文本的桥接路径比 pdf2docx 快 3 倍，
    但丢掉全部表格。
    """
    requires: frozenset[str] = frozenset()
    """这条能力要求内容里有什么："text"（文字）、"audio"（音轨）、"picture"（画面）。

    写 MP3 要有音轨，写 TXT 要有文字。图搜索据此排除注定失败或产出空文件的路径，
    比如 PNG 经 raster ⇒ media 得到的视频没有音轨，就不会列出 PNG→MP3。见 graph.CONTENT。
    """


def caps(
    *media_types: MediaType,
    loss: LossTier = LossTier.LOSSLESS,
    time_cost: float = 1.0,
    options: frozenset[str] | set[str] = frozenset(),
    fidelity: float = 1.0,
    requires: frozenset[str] | set[str] = frozenset(),
) -> tuple[Capability, ...]:
    """批量声明同代价的能力。插件里最常见的写法。

    >>> caps("image/png", "image/bmp")                    # 两个无损格式
    >>> caps("image/jpeg", loss=LossTier.LOSSY)           # 有损
    >>> caps("audio/mpeg", requires={"audio"})            # 源里得有音轨
    """
    opts, req = frozenset(options), frozenset(requires)
    return tuple(
        Capability(
            mt, loss=loss, time_cost=time_cost, options=opts, fidelity=fidelity,
            requires=req,
        )
        for mt in media_types
    )


@dataclass(slots=True)
class ConvertRequest:
    """一次转换请求。

    inputs 是列表因为存在多进一出（多图→PDF/GIF）；输出同理可能一进多出
    （PDF→每页一张 PNG）。基数模型从一开始就是 List→List，不是 1:1，
    后期加 PDF 分页时才不用改签名。
    """

    inputs: list[Resource]
    target: MediaType
    options: dict[str, Any] = field(default_factory=dict)
    overwrite: bool = False
    max_materializations: int = 2
    """允许的中间物化格式数上限。防止搜出 A→B→C→D→E 这种荒唐路径。"""
    stem: str | None = None
    """产物文件名主干，覆盖从源文件名推导的默认值。批量转换时用来消解重名。"""


@dataclass(slots=True)
class ConvertResult:
    outputs: list[Resource]
    loss: LossTier
    """实测损失等级。可能优于 planned_loss（如 remux 实际是 COPY）。"""
    planned_loss: LossTier | None = None
    """路径搜索时的静态声明（最坏情况）。与 loss 不同说明走了更优的实际路径。"""
    path_desc: str = ""
    """人类可读的路径描述，如 "image/heic → [raster] → image/png"。"""
    elapsed: float = 0.0
    warnings: list[str] = field(default_factory=list)
    """降级策略产生的告警，例如「目标格式不支持透明，已合成白底」。"""


class ConversionError(Exception):
    """转换失败。消息应当包含引擎的真实错误，不要笼统说「转换失败」。"""


class Cancelled(ConversionError):
    """用户主动取消转换。"""

    """用户主动取消了转换。"""


class NoPathError(ConversionError):
    """格式图上找不到从源到目标的路径。"""
