"""容器与编码的兼容表，以及 remux 判定。

这张表是整个音视频域最有价值的部分。现有工具最常犯的错就是 MKV→MP4 去重新
编码——两者容器不同但编码可以完全一样，直接改封装即可，重编码白吃一次世代
损失还慢几十倍。

判定逻辑：目标容器能装下源文件的所有编码 → `-c copy`，否则只转不兼容的那些流
（视频能拷就拷、只转音频，反之亦然）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..types import MediaType

# 容器 → 支持的编码集合。
# 有意保守：列进来的都是广泛兼容的组合。技术上 MP4 能装 VP9，但很多播放器
# 不认，所以不列——转换工具的产物应当是「能播的」而不是「规范允许的」。
CONTAINER_CODECS: dict[MediaType, dict[str, frozenset[str]]] = {
    "video/mp4": {
        "video": frozenset({"h264", "hevc", "av1", "mpeg4", "mjpeg"}),
        "audio": frozenset({"aac", "mp3", "alac", "ac3", "eac3", "opus"}),
        "subtitle": frozenset({"mov_text"}),
    },
    "video/x-matroska": {
        # MKV 几乎什么都能装，这是它存在的意义
        "video": frozenset(
            {"h264", "hevc", "av1", "vp8", "vp9", "mpeg4", "mpeg2video", "prores", "theora", "mjpeg"}
        ),
        "audio": frozenset(
            {"aac", "mp3", "flac", "opus", "vorbis", "ac3", "eac3", "dts", "pcm_s16le", "alac", "truehd"}
        ),
        "subtitle": frozenset({"subrip", "ass", "ssa", "webvtt", "hdmv_pgs_subtitle", "dvd_subtitle"}),
    },
    "video/quicktime": {
        "video": frozenset({"h264", "hevc", "prores", "mjpeg", "mpeg4", "dnxhd"}),
        "audio": frozenset({"aac", "alac", "pcm_s16le", "pcm_s24le", "mp3"}),
        "subtitle": frozenset({"mov_text"}),
    },
    "video/webm": {
        # WebM 规范严格限定：只允许这几种
        "video": frozenset({"vp8", "vp9", "av1"}),
        "audio": frozenset({"vorbis", "opus"}),
        "subtitle": frozenset({"webvtt"}),
    },
    "video/x-msvideo": {
        "video": frozenset({"mpeg4", "mjpeg", "h264", "msmpeg4v3"}),
        "audio": frozenset({"mp3", "pcm_s16le", "ac3"}),
        "subtitle": frozenset(),
    },
    "video/x-flv": {
        "video": frozenset({"h264", "flv1"}),
        "audio": frozenset({"aac", "mp3"}),
        "subtitle": frozenset(),
    },
    "video/mp2t": {
        "video": frozenset({"h264", "hevc", "mpeg2video"}),
        "audio": frozenset({"aac", "mp3", "ac3", "eac3"}),
        "subtitle": frozenset(),
    },
    "video/x-ms-wmv": {
        # ASF 容器（.wmv/.asf）。wmv 是微软私有编码，容器本身也能装 mpeg4。
        "video": frozenset({"wmv1", "wmv2", "wmv3", "mpeg4", "h264"}),
        "audio": frozenset({"wmav2", "wmapro", "mp3", "aac"}),
        "subtitle": frozenset(),
    },
    # ── 纯音频容器 ──
    "audio/mpeg": {"video": frozenset(), "audio": frozenset({"mp3"}), "subtitle": frozenset()},
    "audio/flac": {"video": frozenset(), "audio": frozenset({"flac"}), "subtitle": frozenset()},
    "audio/wav": {
        "video": frozenset(),
        "audio": frozenset({"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le"}),
        "subtitle": frozenset(),
    },
    "audio/aac": {
        "video": frozenset(),
        "audio": frozenset({"aac", "alac"}),
        "subtitle": frozenset(),
    },
    "audio/ogg": {
        "video": frozenset(),
        "audio": frozenset({"vorbis", "opus", "flac"}),
        "subtitle": frozenset(),
    },
    "audio/opus": {"video": frozenset(), "audio": frozenset({"opus"}), "subtitle": frozenset()},
    "audio/x-aiff": {
        "video": frozenset(),
        "audio": frozenset({"pcm_s16be", "pcm_s24be"}),
        "subtitle": frozenset(),
    },
    "audio/x-ms-wma": {
        # WMA 纯音频（.wma）。asf 容器只装音频流，wma 系列编码。
        "video": frozenset(),
        "audio": frozenset({"wmav1", "wmav2", "wmapro"}),
        "subtitle": frozenset(),
    },
}

# 目标容器的默认编码器。ffmpeg 的内置默认有时不理想（比如 mkv 默认可能选到
# 不那么通用的组合），显式指定更可控。
DEFAULT_ENCODERS: dict[MediaType, dict[str, str]] = {
    "video/mp4": {"video": "libx264", "audio": "aac"},
    "video/x-matroska": {"video": "libx264", "audio": "aac"},
    "video/quicktime": {"video": "libx264", "audio": "aac"},
    "video/webm": {"video": "libvpx-vp9", "audio": "libopus"},
    "video/x-msvideo": {"video": "mpeg4", "audio": "libmp3lame"},
    "video/x-flv": {"video": "libx264", "audio": "aac"},
    "video/mp2t": {"video": "libx264", "audio": "aac"},
    # WMV 用微软的 wmv2 编码器（不是 libx264——那是 H.264 不是 WMV，FFmpeg 会报错）
    "video/x-ms-wmv": {"video": "wmv2", "audio": "wmav2"},
    "audio/mpeg": {"audio": "libmp3lame"},
    "audio/flac": {"audio": "flac"},
    "audio/wav": {"audio": "pcm_s16le"},
    "audio/aac": {"audio": "aac"},
    "audio/ogg": {"audio": "libvorbis"},
    "audio/opus": {"audio": "libopus"},
    "audio/x-aiff": {"audio": "pcm_s16be"},
    "audio/x-ms-wma": {"audio": "wmav2"},
}

# 编码器 → 质量参数的表达方式。CRF 和 VBR 质量刻度完全不同，不能混用。
QUALITY_STYLE: dict[str, str] = {
    "libx264": "crf",
    "libx265": "crf",
    "libvpx-vp9": "crf-vp9",
    "libaom-av1": "crf",
    "libsvtav1": "crf",
    "mpeg4": "qscale",
    "libmp3lame": "qscale-audio",
    "aac": "bitrate",
    "libopus": "bitrate",
    "libvorbis": "qscale-audio",
    "flac": "none",
    "pcm_s16le": "none",
    "pcm_s16be": "none",
    "prores": "none",
}


# 数学上无损的编码器。转到这些编码器时不该报「有损压缩」——
# WAV→FLAC 是无损压缩，报成有损会误导用户以为音质降了。
LOSSLESS_ENCODERS: frozenset[str] = frozenset(
    {
        "flac",
        "alac",
        "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le",
        "pcm_s16be", "pcm_s24be",
        "ffv1",
        "libx264rgb",
    }
)


@dataclass(slots=True)
class StreamAction:
    """对一条流的处理决定。"""

    stream_index: int
    kind: str
    action: str
    """copy / transcode / drop"""
    encoder: str | None = None
    reason: str = ""


@dataclass(slots=True)
class RemuxDecision:
    """整个文件的处理决定。"""

    actions: list[StreamAction]
    can_copy_all: bool
    """全部流都能直接拷贝 → 纯 remux，零损失。"""

    @property
    def copies_video(self) -> bool:
        return any(a.kind == "video" and a.action == "copy" for a in self.actions)

    @property
    def transcodes_any(self) -> bool:
        return any(a.action == "transcode" for a in self.actions)

    @property
    def dropped(self) -> list[StreamAction]:
        return [a for a in self.actions if a.action == "drop"]

    @property
    def all_transcodes_lossless(self) -> bool:
        """保留的流全部无损：拷贝的，或用无损编码器重编码的。

        WAV→FLAC 属于这类。抽音轨（视频被丢）也可能满足，此时上层会因为
        存在 dropped 而降级为 STRUCTURAL。
        """
        kept = [a for a in self.actions if a.action != "drop"]
        if not kept:
            return False
        return all(
            a.action == "copy" or (a.encoder or "") in LOSSLESS_ENCODERS
            for a in kept
        )

    def summary(self) -> str:
        if self.can_copy_all:
            return "全部流直接改封装，不重编码"
        parts = []
        for a in self.actions:
            if a.action == "transcode":
                parts.append(f"{a.kind}→{a.encoder}")
            elif a.action == "drop":
                parts.append(f"丢弃{a.kind}")
        return "，".join(parts) if parts else "改封装"


def supported(container: MediaType, kind: str) -> frozenset[str]:
    return CONTAINER_CODECS.get(container, {}).get(kind, frozenset())


def decide(plan, target: MediaType, *, force_transcode: bool = False) -> RemuxDecision:
    """决定每条流是拷贝、转码还是丢弃。

    这是 remux 短路的核心。判定顺序：
    1. 目标容器根本不支持这类流（如 WebM 不支持 PGS 字幕）→ 丢弃
    2. 编码在目标容器的白名单里 → 拷贝
    3. 否则 → 转码成目标容器的默认编码器
    """
    actions: list[StreamAction] = []
    caps = CONTAINER_CODECS.get(target)
    if caps is None:
        raise ValueError(f"没有 {target} 的容器兼容定义")

    encoders = DEFAULT_ENCODERS.get(target, {})
    audio_only = not caps["video"]

    for s in plan.streams:
        # 纯音频目标：视频轨（含封面）一律丢弃
        if audio_only and s.kind in ("video", "subtitle", "data", "attachment"):
            actions.append(
                StreamAction(
                    s.index, s.kind, "drop",
                    reason=f"目标是纯音频格式，不能容纳{s.kind}流",
                )
            )
            continue

        # 封面图单独处理：能拷就拷，不能就丢，绝不当视频轨转码
        if s.is_attached_pic:
            allowed = caps.get("video", frozenset())
            if s.codec in allowed and not force_transcode:
                actions.append(StreamAction(s.index, "video", "copy", reason="封面图"))
            else:
                actions.append(
                    StreamAction(s.index, "video", "drop", reason="目标容器不支持该封面格式")
                )
            continue

        allowed = caps.get(s.kind, frozenset())
        if not allowed:
            actions.append(
                StreamAction(
                    s.index, s.kind, "drop",
                    reason=f"{target} 不支持 {s.kind} 流",
                )
            )
            continue

        if s.codec in allowed and not force_transcode:
            actions.append(
                StreamAction(s.index, s.kind, "copy", reason=f"{s.codec} 兼容目标容器")
            )
            continue

        enc = encoders.get(s.kind)
        if enc is None:
            actions.append(
                StreamAction(
                    s.index, s.kind, "drop",
                    reason=f"{s.codec} 不兼容且无默认编码器",
                )
            )
            continue

        from ..formats import label_of

        lossless = enc in LOSSLESS_ENCODERS
        verb = "无损重编码为" if lossless else "重编码为"
        actions.append(
            StreamAction(
                s.index, s.kind, "transcode", encoder=enc,
                reason=f"{label_of(target)} 不支持 {s.codec}，已{verb} {enc}",
            )
        )

    kept = [a for a in actions if a.action != "drop"]
    dropped = [a for a in actions if a.action == "drop"]
    # 有流被丢弃就不算纯 remux——抽音轨虽然音频是拷贝的，但视频没了，
    # 报「免重编码」会让用户以为什么都没丢。
    can_copy_all = bool(kept) and not dropped and all(a.action == "copy" for a in kept)
    return RemuxDecision(actions, can_copy_all)
