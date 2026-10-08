"""FFmpeg 封装：探测、参数构造、进度解析。

进度解析用 `-progress pipe:1`，它输出机器可读的 key=value 行。不要去正则匹配
stderr 的人类可读输出——那个格式在版本间会变，而且和日志混在一起。

百分比需要总时长，来自 ffprobe。有些容器不报时长（流式 TS、损坏的文件），
这时进度是不确定态，UI 该显示转圈而不是假的百分比。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from ..ir.media import MediaPlan, Stream
from ..types import ConversionError, MediaType, Resource
from . import base
from .codecs import QUALITY_STYLE, RemuxDecision, StreamAction
from .probe import companion, require


def ffprobe_json(path: Path) -> dict[str, Any]:
    """取容器和流的完整信息。"""
    exe = companion("ffmpeg", "ffprobe")
    res = base.run(
        [
            exe, "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            path,
        ],
        timeout=120,
        engine_name="ffprobe",
    )
    try:
        return json.loads(res.stdout or b"{}")
    except json.JSONDecodeError as exc:
        raise ConversionError(f"ffprobe 输出无法解析：{exc}") from exc


def _f(val: Any) -> float | None:
    try:
        f = float(val)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _i(val: Any) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _fps(raw: str | None) -> float | None:
    """帧率是 "30000/1001" 这种分数形式。"""
    if not raw or "/" not in raw:
        return _f(raw)
    num, _, den = raw.partition("/")
    n, d = _f(num), _f(den)
    return n / d if n and d else None


def analyze(res: Resource, tmp: Path) -> MediaPlan:
    """探测出 MediaPlan。这是音视频域的 parse()。"""
    path = res.materialize(tmp)
    data = ffprobe_json(path)

    fmt = data.get("format") or {}
    streams: list[Stream] = []

    for s in data.get("streams") or []:
        disp = s.get("disposition") or {}
        tags = s.get("tags") or {}
        streams.append(
            Stream(
                index=_i(s.get("index")) or 0,
                kind=s.get("codec_type") or "data",
                codec=s.get("codec_name") or "unknown",
                profile=s.get("profile"),
                width=_i(s.get("width")),
                height=_i(s.get("height")),
                fps=_fps(s.get("r_frame_rate")),
                sample_rate=_i(s.get("sample_rate")),
                channels=_i(s.get("channels")),
                bit_rate=_i(s.get("bit_rate")),
                language=tags.get("language"),
                title=tags.get("title"),
                pix_fmt=s.get("pix_fmt"),
                duration=_f(s.get("duration")),
                is_default=bool(disp.get("default")),
                is_attached_pic=bool(disp.get("attached_pic")),
            )
        )

    if not streams:
        raise ConversionError(
            f"{path.name} 里没有可识别的音视频流（文件可能损坏或不是媒体文件）"
        )

    duration = _f(fmt.get("duration"))
    if duration is None:
        # 容器不报时长时退而求其次：取最长的流
        cand = [s.duration for s in streams if s.duration]
        duration = max(cand) if cand else None

    return MediaPlan(
        source=res,
        streams=streams,
        container=fmt.get("format_name"),
        duration=duration,
        bit_rate=_i(fmt.get("bit_rate")),
        size=_i(fmt.get("size")),
        tags=dict(fmt.get("tags") or {}),
    )


def _quality_args(
    encoder: str, kind: str, opts: dict[str, Any], *, channels: int | None = None
) -> list[str]:
    """把统一的 quality（1-100，越大越好）翻译成各编码器的参数。

    这层映射是必要的：CRF 是 0-51 越小越好，qscale 是 1-31 越小越好，
    码率是绝对值。让用户记住每个编码器的刻度是不合理的。
    """
    style = QUALITY_STYLE.get(encoder, "none")
    stream_flag = "v" if kind == "video" else "a"

    # 用户直接给码率时优先，最精确
    if kind == "video" and (vb := opts.get("video_bitrate")):
        return [f"-b:{stream_flag}", str(vb)]
    if kind == "audio" and (ab := opts.get("audio_bitrate")):
        return [f"-b:{stream_flag}", str(ab)]

    q = opts.get("quality")
    if q is None:
        # 各编码器的合理默认（高质量线；用户显式 quality 时按下方映射覆盖）
        if style == "crf":
            # x265 的 CRF 刻度比 x264 高约 4-5 档（28≈23），默认分开给
            return ["-crf", "22" if encoder == "libx265" else "20"]
        if style == "crf-vp9":
            return ["-crf", "31", "-b:v", "0"]
        if style == "crf-av1":
            return ["-crf", "30", "-b:v", "0"]
        if style == "bitrate":
            if kind == "audio":
                # 5.1 用 192k 会明显浑浊：默认按声道数给
                return [f"-b:{stream_flag}", "448k" if (channels or 2) > 2 else "192k"]
            return [f"-b:{stream_flag}", "2M"]
        if style == "qscale":
            # mpeg4（AVI）不指定就是 ffmpeg 默认约 200kbps，远低于高质量线
            return [f"-q:{stream_flag}", "3"]
        if style == "qscale-audio":
            return [f"-q:{stream_flag}", "2"]
        if style == "qscale-vorbis":
            return [f"-q:{stream_flag}", "6"]
        return []

    q = max(1, min(100, int(q)))

    if style == "crf":
        # quality 100 → crf 0（无损），quality 1 → crf 51
        return ["-crf", str(round(51 - (q / 100) * 51))]
    if style == "crf-vp9":
        # VP9 的实用 CRF 区间是 15-45
        return ["-crf", str(round(45 - (q / 100) * 30)), "-b:v", "0"]
    if style == "qscale":
        return [f"-q:{stream_flag}", str(max(1, round(31 - (q / 100) * 30)))]
    if style == "qscale-audio":
        # LAME/Vorbis 的 V 值：0 最好，9 最差
        return [f"-q:{stream_flag}", str(round(9 - (q / 100) * 9))]
    if style == "crf-av1":
        # AV1 需 -b:v 0 才是恒定质量（否则 CRF 会被码率上限钳制）
        return ["-crf", str(round(51 - (q / 100) * 51)), "-b:v", "0"]
    if style == "qscale-vorbis":
        # vorbis 的 -q:a 是 -1..10 越大越好（与 LAME 相反），quality 100 → 10
        return [f"-q:{stream_flag}", str(round(-1 + (q / 100) * 11))]
    if style == "bitrate":
        if kind == "audio":
            # 64k~320k 线性映射
            return [f"-b:{stream_flag}", f"{round(64 + (q / 100) * 256)}k"]
        return [f"-b:{stream_flag}", f"{round(0.5 + (q / 100) * 7.5)}M"]
    return []


_EVEN_SIZE_ENCODERS = frozenset({"libx264", "libx265", "wmv2"})
"""宽高必须是偶数的视频编码器（实测 65×63 输入直接报错；VP9、mpeg4、flv 不受影响）。"""


def _scale_filter(opts: dict[str, Any]) -> str | None:
    """构造 scale 滤镜。-2 让 ffmpeg 自动取偶数，因为 H.264 要求偶数尺寸。"""
    w, h = opts.get("width"), opts.get("height")
    max_edge = opts.get("max_edge")

    if w and h:
        return f"scale={int(w)}:{int(h)}"
    if w:
        return f"scale={int(w)}:-2"
    if h:
        return f"scale=-2:{int(h)}"
    if max_edge:
        m = int(max_edge)
        # 只缩不放：长边超过阈值才缩
        return (
            f"scale='if(gt(iw,ih),min({m},iw),-2)':'if(gt(iw,ih),-2,min({m},ih))'"
        )
    return None


def build_command(
    exe: Path,
    src: Path,
    dst: Path,
    plan: MediaPlan,
    decision: RemuxDecision,
    container: str,
    opts: dict[str, Any],
) -> list[str | Path]:
    """构造 ffmpeg 命令行。

    参数顺序有讲究：全局选项 → 输入 → 流映射 → 编码参数 → 输出。
    ffmpeg 对顺序敏感，把输出选项放到 -i 之前会静默失效。
    """
    cmd: list[str | Path] = [exe, "-hide_banner", "-nostdin", "-y"]

    # 处理图片序列输入（raster→media 桥）
    if plan.extra.get("input_framerate"):
        cmd += ["-framerate", str(plan.extra["input_framerate"])]
    if plan.extra.get("input_pattern_type") == "sequence":
        # image2 解复用器默认就是序列模式，不需要额外参数
        pass

    cmd += ["-i", src]

    scale = _scale_filter(opts) if plan.is_video else None

    # 显式映射每条保留的流。不写 -map 时 ffmpeg 只会选「最好的一条」，
    # 多音轨多字幕的文件会被静默丢掉轨道。
    for a in decision.actions:
        if a.action != "drop":
            cmd += ["-map", f"0:{a.stream_index}"]

    # 元数据与章节默认透传（标题、语言、章节表）；keep_metadata=False 时清空。
    # 不加这两行时 ffmpeg 只带部分全局元数据，章节整段丢失。
    keep_meta = "0" if opts.get("keep_metadata", True) else "-1"
    cmd += ["-map_metadata", keep_meta, "-map_chapters", keep_meta]

    # 按流类型给编码参数。用 -c:v / -c:a 而不是逐流指定，因为同类流通常同处理。
    video_actions = [a for a in decision.actions if a.kind == "video" and a.action != "drop"]
    audio_actions = [a for a in decision.actions if a.kind == "audio" and a.action != "drop"]
    sub_actions = [a for a in decision.actions if a.kind == "subtitle" and a.action != "drop"]

    if video_actions:
        if (
            all(a.action == "copy" for a in video_actions)
            and not scale
            and not opts.get("fps")
        ):
            cmd += ["-c:v", "copy"]
        else:
            enc = next(
                (a.encoder for a in video_actions if a.encoder), "libx264"
            )
            cmd += ["-c:v", enc]
            cmd += _quality_args(enc, "video", opts)
            filters = [scale] if scale else []
            if enc in _EVEN_SIZE_ENCODERS:
                # 4:2:0 色度按 2×2 采样，这几个编码器要求宽高是偶数。scale 的 -2
                # 只管缩放的情况；PDF 页渲染（1240×1755）、裁过的截图这类奇数尺寸
                # 源会直接报错。裁掉最后一行/列像素，偶数尺寸时是空操作。
                filters.append("crop=trunc(iw/2)*2:trunc(ih/2)*2")
            if fps := opts.get("fps"):
                # fps 用滤镜而不是 -r：与 scale 串联时时间基转换更稳
                filters.append(f"fps={int(fps)}")
            if filters:
                cmd += ["-vf", ",".join(filters)]
            if enc in ("libx264", "libx265"):
                cmd += ["-preset", str(opts.get("preset_speed", "medium"))]
                # yuv420p 保证广泛兼容；不加的话 10bit 源会产出很多播放器不认的文件
                cmd += ["-pix_fmt", str(opts.get("pix_fmt", "yuv420p"))]
            if enc == "libvpx-vp9":
                cmd += ["-row-mt", "1"]
                # 不指定时 ffmpeg 给带透明的源（SVG 渲染图、透明 PNG）选 gbrap，
                # libvpx 拒绝打开编码器，SVG→WebM 直接失败
                cmd += ["-pix_fmt", str(opts.get("pix_fmt", "yuv420p"))]
            if enc == "libaom-av1":
                cmd += ["-cpu-used", str(opts.get("cpu_used", 4)), "-row-mt", "1"]
            if enc == "libsvtav1":
                cmd += ["-preset", str(opts.get("svt_preset", 6))]

    if audio_actions:
        if all(a.action == "copy" for a in audio_actions):
            cmd += ["-c:a", "copy"]
        else:
            enc = next((a.encoder for a in audio_actions if a.encoder), "aac")
            cmd += ["-c:a", enc]
            primary_audio = plan.primary_audio
            cmd += _quality_args(
                enc, "audio", opts,
                channels=primary_audio.channels if primary_audio else None,
            )
            if ar := opts.get("sample_rate"):
                cmd += ["-ar", str(ar)]
            if ac := opts.get("channels"):
                cmd += ["-ac", str(ac)]

    if sub_actions:
        if all(a.action == "copy" for a in sub_actions):
            cmd += ["-c:s", "copy"]
        else:
            # 字幕编码随容器：mp4/mov 只认 mov_text，WebM 只认 webvtt
            if container in ("video/mp4", "video/quicktime"):
                cmd += ["-c:s", "mov_text"]
            elif container == "video/webm":
                cmd += ["-c:s", "webvtt"]
            else:
                cmd += ["-c:s", "srt"]

    # MP4 的 moov atom 放到文件头，网页播放才能边下边播
    if dst.suffix.lower() in (".mp4", ".m4v", ".m4a", ".mov"):
        cmd += ["-movflags", "+faststart"]

    if trim_start := opts.get("start"):
        cmd += ["-ss", str(trim_start)]
    if trim_dur := opts.get("duration"):
        cmd += ["-t", str(trim_dur)]

    # 进度输出到 stdout，日志留在 stderr，两者不混
    cmd += ["-progress", "pipe:1", "-nostats"]
    cmd.append(dst)
    return cmd


def parse_progress(lines: Iterator[str], total: float | None) -> Iterator[tuple[float, str]]:
    """把 -progress 的 key=value 流翻译成 (比例, 描述)。

    输出形如：
        out_time_us=1234567
        speed=2.5x
        progress=continue
    每个块以 progress= 结尾。时长未知时比例返回 -1，UI 应显示不确定态。
    """
    cur: dict[str, str] = {}
    for line in lines:
        key, _, val = line.partition("=")
        if not _:
            continue
        cur[key.strip()] = val.strip()

        if key.strip() != "progress":
            continue

        us = cur.get("out_time_us") or cur.get("out_time_ms")
        seconds: float | None = None
        if us and us.lstrip("-").isdigit():
            div = 1e6 if "out_time_us" in cur else 1e3
            seconds = int(us) / div

        speed = cur.get("speed", "").strip()
        desc = f"转码中 {speed}" if speed and speed != "N/A" else "转码中"

        if total and seconds is not None:
            # ffmpeg 偶尔会报出略超总时长的值，夹紧避免进度条超过 100%
            yield min(seconds / total, 1.0), desc
        else:
            yield -1.0, desc

        if cur.get("progress") == "end":
            yield 1.0, "完成"
        cur = {}
