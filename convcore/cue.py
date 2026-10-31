"""CUE 分轨：把整轨音频按 CUE 的时间索引切成多个文件。

只做本地文件操作，不涉及任何在线服务。CUE 是纯文本索引，音频仍由 ffmpeg
解码；输出走正常转换管线，因此目标格式、质量和元数据策略与单文件转换一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CueTrack:
    number: int
    title: str
    performer: str | None
    start: float


@dataclass(slots=True)
class CueSheet:
    audio: Path | None
    title: str | None = None
    performer: str | None = None
    tracks: list[CueTrack] = field(default_factory=list)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _parse_time(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"无效的 CUE 时间：{value}")
    minutes, seconds, frames = (int(p) for p in parts)
    return minutes * 60 + seconds + frames / 75.0


def parse_cue(path: Path) -> CueSheet:
    """解析 CUE；FILE 只取第一个，TRACK 取 INDEX 01。"""
    text = _decode(path.read_bytes())
    sheet = CueSheet(audio=None)
    current: dict[str, Any] | None = None
    tracks: list[CueTrack] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("REM"):
            continue
        key, _, value = line.partition(" ")
        key = key.upper()
        value = value.strip()
        if key == "FILE":
            file_part = re.match(r'^(?:"([^"]+)"|(\S+))', value)
            if file_part and sheet.audio is None:
                name = file_part.group(1) or file_part.group(2) or ""
                sheet.audio = (path.parent / name).resolve()
        elif key == "TRACK":
            if current is not None:
                tracks.append(_finish_track(current))
            number = int(value.split()[0])
            current = {"number": number, "title": "", "performer": None, "start": None}
        elif key == "TITLE":
            title = _unquote(value)
            if current is None:
                sheet.title = title
            else:
                current["title"] = title
        elif key == "PERFORMER":
            performer = _unquote(value)
            if current is None:
                sheet.performer = performer
            else:
                current["performer"] = performer
        elif key == "INDEX" and current is not None:
            parts = value.split()
            if len(parts) == 2 and parts[0] == "01":
                current["start"] = _parse_time(parts[1])

    if current is not None:
        tracks.append(_finish_track(current))
    sheet.tracks = tracks
    return sheet


def _finish_track(data: dict[str, Any]) -> CueTrack:
    start = data.get("start")
    if start is None:
        raise ValueError(f"TRACK {data['number']:02d} 缺少 INDEX 01")
    return CueTrack(
        number=int(data["number"]),
        title=str(data.get("title") or f"Track {int(data['number']):02d}"),
        performer=data.get("performer"),
        start=float(start),
    )


def _infer_audio(cue_path: Path) -> Path | None:
    for suffix in (".flac", ".wav", ".ape", ".mp3", ".m4a", ".ogg", ".opus", ".wv"):
        candidate = cue_path.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def split_cue(
    cue_path: Path,
    target: str,
    *,
    audio: Path | None = None,
    out_dir: Path | None = None,
    preset: str | None = None,
    overwrite: bool = False,
    progress=None,
) -> list[Path]:
    """按 CUE 分轨并转换，返回产物路径列表。"""
    from .formats import domain_of, primary_extension, resolve_target
    from .pipeline import convert_file
    from .types import ConversionError

    cue_path = cue_path.expanduser()
    if not cue_path.is_file():
        raise ConversionError(f"CUE 文件不存在：{cue_path}")
    try:
        sheet = parse_cue(cue_path)
    except ValueError as exc:
        raise ConversionError(f"CUE 解析失败：{exc}") from exc
    if not sheet.tracks:
        raise ConversionError("CUE 里没有 TRACK 01")

    source = (audio or sheet.audio or _infer_audio(cue_path))
    if source is None or not source.is_file():
        raise ConversionError(
            "找不到 CUE 对应的音频文件；用 --audio 指定，或把音频放在 CUE 同目录同名"
        )
    target_mt = resolve_target(target)
    if target_mt is None:
        raise ConversionError(f"未知目标格式：{target}")
    if domain_of(target_mt) != "media":
        raise ConversionError("CUE 分轨目标必须是音视频格式")

    dest_dir = (out_dir or cue_path.parent).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = primary_extension(target_mt) or ".bin"
    outputs: list[Path] = []
    for i, track in enumerate(sheet.tracks):
        duration = None
        if i + 1 < len(sheet.tracks):
            duration = sheet.tracks[i + 1].start - track.start
            if duration <= 0:
                raise ConversionError(f"第 {track.number:02d} 轨时间索引不递增")
        safe = re.sub(r'[\\/:*?"<>|]+', "_", track.title).strip() or "track"
        dest = dest_dir / f"{track.number:02d} - {safe}{ext}"
        if progress:
            progress(i / len(sheet.tracks), f"分轨 {track.number:02d}/{len(sheet.tracks)}")
        tags = {
            "title": track.title,
            "artist": track.performer or sheet.performer or "",
            "album": sheet.title or "",
            "track": f"{track.number}/{len(sheet.tracks)}",
        }
        result = convert_file(
            source,
            target_mt,
            out_path=dest,
            preset=preset,
            overwrite=overwrite,
            progress=None,
            overrides={
                "start": track.start,
                **({"duration": duration} if duration is not None else {}),
                "metadata": {k: v for k, v in tags.items() if v},
            },
        )
        outputs.append(result.outputs[0].path)
    if progress:
        progress(1.0, "分轨完成")
    return outputs
