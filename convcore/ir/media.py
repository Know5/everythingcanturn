"""音视频领域的信息模型。

和位图的关键差别：**这个 IR 是转码计划，不是解码后的数据。**

在 Python 里物化视频帧不现实——一部 1080p 电影解码成原始帧是几百 GB。所以重域
的枢纽建在「计划层」：探测流信息、决定参数，然后让 ffmpeg 一次性完成解码到编码
的全过程。这是分域 IR 而非全局 IR 的最好例证：位图域可以真的把像素读进内存，
音视频域必须只传递元信息。

直接后果是 MediaPlan 必须持有源 Resource 的引用（ffmpeg 要读原始文件），
而 Raster 不需要。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..types import Resource


@dataclass(slots=True)
class Stream:
    """容器里的一条流。"""

    index: int
    kind: str
    """video / audio / subtitle / data / attachment"""
    codec: str
    """ffprobe 报的 codec_name，如 h264、aac、vp9。"""
    profile: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bit_rate: int | None = None
    language: str | None = None
    title: str | None = None
    pix_fmt: str | None = None
    duration: float | None = None
    is_default: bool = False
    is_attached_pic: bool = False
    """封面图会被 ffprobe 报成 video 流。当成视频轨处理会导致抽帧抽出封面。"""

    @property
    def resolution(self) -> str:
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return "—"

    def describe(self) -> str:
        bits = [self.codec]
        if self.kind == "video" and self.width:
            bits.append(self.resolution)
            if self.fps:
                bits.append(f"{self.fps:.3g}fps")
        elif self.kind == "audio":
            if self.channels:
                bits.append(f"{self.channels}ch")
            if self.sample_rate:
                bits.append(f"{self.sample_rate // 1000}kHz")
        if self.language:
            bits.append(self.language)
        return " · ".join(bits)


@dataclass(slots=True)
class MediaPlan:
    """音视频域 IR：源文件引用 + 探测出的流信息。

    writer 拿到它之后决定是 remux 还是转码，然后调 ffmpeg 一次完成。
    """

    source: Resource
    streams: list[Stream]
    container: str | None = None
    """ffprobe 报的 format_name，可能是逗号分隔的多个候选。"""
    duration: float | None = None
    """总时长（秒）。某些容器不报，此时进度只能是不确定态。"""
    bit_rate: int | None = None
    size: int | None = None
    tags: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def video(self) -> list[Stream]:
        """真正的视频轨，排除封面图。

        封面图（mp3 的专辑封面、mp4 的缩略图）会被 ffprobe 报成 video 流，
        不排除的话「这是视频还是音频」的判断会错。
        """
        return [s for s in self.streams if s.kind == "video" and not s.is_attached_pic]

    @property
    def audio(self) -> list[Stream]:
        return [s for s in self.streams if s.kind == "audio"]

    @property
    def subtitles(self) -> list[Stream]:
        return [s for s in self.streams if s.kind == "subtitle"]

    @property
    def cover_art(self) -> list[Stream]:
        return [s for s in self.streams if s.is_attached_pic]

    @property
    def is_video(self) -> bool:
        return bool(self.video)

    @property
    def is_audio_only(self) -> bool:
        return not self.video and bool(self.audio)

    @property
    def primary_video(self) -> Stream | None:
        return next(iter(self.video), None)

    @property
    def primary_audio(self) -> Stream | None:
        for s in self.audio:
            if s.is_default:
                return s
        return next(iter(self.audio), None)

    def describe(self) -> str:
        parts = []
        if v := self.primary_video:
            parts.append(f"视频 {v.describe()}")
        if a := self.primary_audio:
            parts.append(f"音频 {a.describe()}")
        if n := len(self.subtitles):
            parts.append(f"字幕 {n} 轨")
        if self.duration:
            parts.append(f"{self.duration:.1f}s")
        return " | ".join(parts) or "无可识别的流"
