"""音视频插件：容器互转、转码、抽音轨。

注册成一个 Codec：parse 用 ffprobe 出计划，write 用 ffmpeg 执行。这样枢纽模型
照样成立——ffmpeg 读写的格式两两组合自动可达，不需要 N² 张直连表。

损失等级的处理有个微妙之处：静态声明只能说「最坏情况」，因为同一对格式
（MKV→MP4）可能是 remux（COPY）也可能要转码（LOSSY），取决于具体文件的编码。
所以 write 时把实测结果回填到 policy.actual_loss，让 ConvertResult 报真实值。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..ir.media import MediaPlan
from ..policy import Policy, Strategy
from ..registry import register
from ..types import Cancelled, ConversionError, LossTier, MediaType, Resource, caps
from ..engines import codecs as codec_tbl
from ._scratch import workdir

_VIDEO_OPTS = frozenset(
    {
        "quality", "width", "height", "max_edge", "fps", "preset_speed",
        "video_bitrate", "audio_bitrate", "pix_fmt", "start", "duration",
        "force_transcode", "cpu_used", "svt_preset", "keep_metadata",
    }
)
_AUDIO_OPTS = frozenset(
    {
        "quality", "audio_bitrate", "sample_rate", "channels", "start",
        "duration", "keep_metadata",
    }
)

_VIDEO_TYPES = (
    "video/mp4",
    "video/x-matroska",
    "video/quicktime",
    "video/webm",
    "video/x-msvideo",
    "video/x-flv",
    "video/mp2t",
    # WMV 写侧：asf 容器装微软 wmv/wma 编码。读侧已有（上边 *caps 加了），
    # 这里补进写出列表让图搜索接通双向。编码器在 codecs.py 的 DEFAULT_ENCODERS。
    "video/x-ms-wmv",
)
_AUDIO_TYPES = (
    "audio/mpeg",
    "audio/aac",
    "audio/flac",
    "audio/wav",
    "audio/ogg",
    "audio/opus",
    "audio/x-aiff",
    "audio/x-ms-wma",
)


@register
class FFmpegCodec:
    name = "ffmpeg"
    domain = "media"

    # 读入只是探测，不解码，所以是无损且极快的
    reads = (
        *caps(*_VIDEO_TYPES, time_cost=2.0),
        *caps(*_AUDIO_TYPES, time_cost=2.0),
    )

    # 写出声明成 LOSSY：这是最坏情况的诚实声明。实际可能是 COPY（remux），
    # 由 write 回填真实值。图搜索用最坏情况排序是对的——不能承诺做不到的保真。
    # 音频格式要求有音轨：图片合成的视频、无声视频转 MP3 只会报「装不下任何流」
    writes = (
        *caps(*_VIDEO_TYPES, loss=LossTier.LOSSY, time_cost=60.0, options=_VIDEO_OPTS),
        *caps(*_AUDIO_TYPES, loss=LossTier.LOSSY, time_cost=15.0, options=_AUDIO_OPTS,
              requires={"audio"}),
    )

    def available(self) -> tuple[bool, str]:
        from ..engines.probe import probe_one
        from ..engines.probe import ENGINES

        spec = next(s for s in ENGINES if s.key == "ffmpeg")
        info = probe_one(spec)
        if info.available:
            return True, ""
        return False, (
            "缺少 ffmpeg，音视频转换不可用："
            "convcore engines install ffmpeg"
        )

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> MediaPlan:
        from ..engines import ffmpeg

        if len(resources) > 1:
            raise ConversionError(
                "音视频暂不支持多文件合并为一个输出（后续会加 concat 支持）"
            )
        tmp = workdir(options)
        return ffmpeg.analyze(resources[0], tmp)

    def write(
        self, ir: MediaPlan, target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        from ..engines import base, ffmpeg
        from ..engines.probe import require
        from ..formats import label_of, primary_extension

        pol: Policy = options.get("_policy") or Policy()
        tmp = workdir(options)
        exe = require("ffmpeg")

        src = ir.source.materialize(tmp)
        dst = tmp / f"out{primary_extension(target) or '.bin'}"

        decision = codec_tbl.decide(
            ir,
            target,
            force_transcode=bool(options.get("force_transcode")),
            force_video_transcode=bool(options.get("fps")),
        )

        kept = [a for a in decision.actions if a.action != "drop"]
        if not kept:
            raise ConversionError(
                f"{label_of(target)} 无法容纳源文件的任何流"
                f"（源：{ir.describe()}）"
            )

        self._record_decision(ir, decision, target, pol)

        cmd = ffmpeg.build_command(exe, src, dst, ir, decision, target, options)

        # 转码耗时与时长强相关。给足余量：慢编码器（VP9/AV1）可能比实时慢 20 倍
        timeout = max(600.0, (ir.duration or 60) * 30)
        progress_cb = options.get("_progress")
        cancel_cb = options.get("_cancel")

        completed = False
        try:
            lines = base.stream_lines(
                cmd, timeout=timeout, engine_name="ffmpeg", cancel=cancel_cb
            )
            for frac, desc in ffmpeg.parse_progress(lines, ir.duration):
                if progress_cb and frac >= 0:
                    progress_cb(frac, desc)

            # stream_lines deliberately keeps cancellation as a normal end of
            # iteration for backwards compatibility. Never let a partial ffmpeg
            # file escape as a successful resource, including the race where the
            # process exits just as the user presses Cancel.
            if cancel_cb and cancel_cb():
                raise Cancelled("已取消")

            if not dst.is_file() or dst.stat().st_size == 0:
                raise ConversionError("ffmpeg 执行完毕但没有产出有效文件")

            completed = True
            return [Resource(media_type=target, path=dst)]
        finally:
            if not completed:
                dst.unlink(missing_ok=True)

    def _record_decision(
        self,
        ir: MediaPlan,
        decision: codec_tbl.RemuxDecision,
        target: MediaType,
        pol: Policy,
    ) -> None:
        """把处理决定写进告警，并回填真实损失等级。

        remux 是这个项目最想做对的事——用户应当看到「免重编码」，而不是
        以为自己刚刚把视频重压了一遍。
        """
        from ..formats import label_of

        dropped = decision.dropped

        if decision.can_copy_all:
            pol.actual_loss = LossTier.COPY
            pol.note(
                "改封装",
                Strategy.DROP,
                f"全部流直接拷贝到 {label_of(target)}，未重新编码（无质量损失）",
            )
        elif decision.all_transcodes_lossless:
            # WAV→FLAC 这类：重编码了，但数学上无损
            pol.actual_loss = LossTier.STRUCTURAL if dropped else LossTier.LOSSLESS
        else:
            pol.actual_loss = LossTier.LOSSY

        for a in dropped:
            pol.note(f"{a.kind} 流", Strategy.DROP, a.reason)

        # 无损重编码不告警：告警是给「有东西丢了」用的，滥用会让用户忽略真正的警告
        if decision.all_transcodes_lossless:
            return

        for a in decision.actions:
            if a.action == "transcode":
                pol.note(f"{a.kind} 重编码", Strategy.APPROXIMATE, a.reason)


def extract_cover_frame(
    ir: MediaPlan, opts: dict[str, Any], pol: Policy
) -> Any:
    """跨域桥：media → raster，提取视频第一帧或音频封面图。

    返回 Raster IR（io.BytesIO 的图像数据）。对纯音频文件，如果有封面图流
    就提取它；没有封面图则报错。对视频文件，提取第一帧。
    """
    from ..engines.probe import require
    from ..engines import base
    from ..ir.raster import Raster
    import io

    # 纯音频交给 ffmpeg 只会得到一个看不懂的退出码，先说清楚
    if not ir.has_picture:
        raise ConversionError(
            "这个文件没有画面也没有封面图，截不出图片"
            "（只有视频、或带封面的音频才能转成图片/PDF）"
        )

    tmp = workdir(opts)
    exe = require("ffmpeg")
    src = ir.source.materialize(tmp)
    dst = tmp / "frame.png"

    # 使用 -vf select='eq(n,0)' 提取第一帧，对音频封面图也有效（会提取 attached_pic 流）
    cmd = [
        str(exe), "-y",
        "-i", str(src),
        "-vf", "select=eq(n\\,0)",
        "-frames:v", "1",
        "-f", "image2",
        str(dst),
    ]

    try:
        base.run(cmd, timeout=30.0, engine_name="ffmpeg")

        if not dst.is_file() or dst.stat().st_size == 0:
            raise ConversionError(
                "无法从媒体文件提取封面帧（可能是纯音频且无封面图）"
            )

        data = dst.read_bytes()
        dst.unlink(missing_ok=True)

        # 返回 Raster IR：用 Pillow 加载成 Frame
        from PIL import Image
        from ..ir.raster import Frame

        img = Image.open(io.BytesIO(data))
        return Raster(frames=[Frame(image=img)])
    except Exception as e:
        dst.unlink(missing_ok=True)
        if isinstance(e, ConversionError):
            raise
        raise ConversionError(f"提取封面帧失败：{e}")



def concat_media(
    sources: list[Path],
    target: MediaType,
    *,
    out_path: Path | None = None,
    out_dir: Path | None = None,
    options: dict[str, Any] | None = None,
    preset: str | None = None,
    overwrite: bool = False,
    progress=None,
):
    """把多个音视频文件按顺序拼成一个输出。

    先尝试 ffmpeg concat demuxer 的无损 `-c copy`；编码/流布局不一致时回退到
    H.264/AAC 中间文件，再走正常转换。这样同编码源保持零损失，混合源也能用。
    """
    import shutil
    import tempfile

    from ..engines import base
    from ..engines.probe import companion, require
    from ..formats import domain_of, label_of, primary_extension, resolve_target
    from ..pipeline import convert_file

    if len(sources) < 2:
        raise ConversionError("concat 至少需要两个源文件")
    if resolve_target(target) is None:
        raise ConversionError(f"未知目标格式：{target}")
    target_mt = resolve_target(target) or target
    if domain_of(target_mt) != "media":
        raise ConversionError(f"concat 只支持音视频目标，收到：{target_mt}")
    for source in sources:
        if not source.is_file():
            raise ConversionError(f"文件不存在：{source}")

    exe = require("ffmpeg")
    opts = dict(options or {})
    tmp = Path(tempfile.mkdtemp(prefix="convcore-concat-"))
    try:
        listing = tmp / "concat.txt"
        lines = ["ffconcat version 1.0"]
        for source in sources:
            # concat demuxer 的 Windows 路径用正斜杠，单引号需要转义
            path = source.resolve().as_posix().replace("'", "'\\''")
            lines.append(f"file '{path}'")
        listing.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ffprobe = companion("ffmpeg", "ffprobe")

        import json

        def probe_json(path: Path, entries: str) -> dict:
            out = base.run(
                [
                    str(ffprobe), "-v", "error", "-show_entries", entries,
                    "-of", "json", str(path),
                ],
                timeout=30.0,
                engine_name="ffprobe",
            ).stdout.decode("utf-8", "replace")
            return json.loads(out)

        def duration(path: Path) -> float:
            data = probe_json(path, "format=duration")
            return float(data["format"]["duration"])

        def signature(path: Path) -> tuple:
            data = probe_json(
                path,
                "stream=codec_type,codec_name,width,height,r_frame_rate,"
                "sample_rate,channels",
            )
            return tuple(sorted(
                (
                    st.get("codec_type"), st.get("codec_name"),
                    st.get("width"), st.get("height"), st.get("r_frame_rate"),
                    st.get("sample_rate"), st.get("channels"),
                )
                for st in data.get("streams", [])
            ))

        # ffmpeg concat demuxer 遇到编码不一致时可能只输出第一段而不报错。
        # 用源总时长校验，避免静默丢掉后面的片段。
        try:
            expected = sum(duration(source) for source in sources)
        except Exception:
            expected = 0.0
        merged = tmp / "merged.mkv"

        def fallback() -> None:
            merged.unlink(missing_ok=True)
            # 逐段转成统一参数，再 concat copy。直接对 demuxer 加编码参数
            # 在分辨率/帧率不同时仍可能只输出第一段。
            streams = [probe_json(src, "stream=codec_type,width,height")["streams"]
                       for src in sources]
            has_video = [any(st.get("codec_type") == "video" for st in ss) for ss in streams]
            has_audio = [any(st.get("codec_type") == "audio" for st in ss) for ss in streams]
            if any(has_video):
                if not all(has_video):
                    raise ConversionError("不能把纯音频文件和视频文件拼在一起")
                widths = [st.get("width") or 0
                          for ss in streams for st in ss if st.get("codec_type") == "video"]
                heights = [st.get("height") or 0
                           for ss in streams for st in ss if st.get("codec_type") == "video"]
                width = max(widths) + (max(widths) % 2)
                height = max(heights) + (max(heights) % 2)
                vf = (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
                )
                parts: list[Path] = []
                for i, src in enumerate(sources):
                    part = tmp / f"norm-{i:03d}.mkv"
                    cmd = [str(exe), "-y", "-i", str(src)]
                    if has_audio[i]:
                        cmd += ["-map", "0:v:0", "-map", "0:a:0"]
                    else:
                        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                                "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
                    cmd += [
                        "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "20", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                        str(part),
                    ]
                    base.run(cmd, timeout=900.0, engine_name="ffmpeg")
                    parts.append(part)
            else:
                if not all(has_audio):
                    raise ConversionError("源文件里没有可拼接的音轨")
                parts = []
                for i, src in enumerate(sources):
                    part = tmp / f"norm-{i:03d}.mka"
                    cmd = [
                        str(exe), "-y", "-i", str(src),
                        "-map", "0:a:0", "-c:a", "aac", "-b:a", "192k",
                        "-ar", "48000", "-ac", "2", str(part),
                    ]
                    base.run(cmd, timeout=900.0, engine_name="ffmpeg")
                    parts.append(part)

            sub_list = tmp / "normalized.txt"
            sub_list.write_text(
                "ffconcat version 1.0\n" + "\n".join(
                    f"file '{part.resolve().as_posix()}'" for part in parts
                ) + "\n",
                encoding="utf-8",
            )
            base.run(
                [
                    str(exe), "-y", "-f", "concat", "-safe", "0",
                    "-i", str(sub_list), "-c", "copy", str(merged),
                ],
                timeout=1800.0, engine_name="ffmpeg",
            )

        copy_cmd = [
            str(exe), "-y", "-f", "concat", "-safe", "0",
            "-i", str(listing), "-c", "copy", str(merged),
        ]
        copied = True
        try:
            # 编码/分辨率/帧率任一不同，concat demuxer 可能把后段当额外轨道
            # 或在输出容器里丢掉；这种情况直接走统一转码，不能信任 -c copy。
            signatures = {signature(source) for source in sources}
            if len(signatures) > 1:
                raise ConversionError("源文件的流布局不一致")
            base.run(copy_cmd, timeout=600.0, engine_name="ffmpeg")
            got = duration(merged)
            if expected > 0 and got < expected * 0.9 - 0.5:
                raise ConversionError(
                    f"concat 输出时长 {got:.2f}s 短于源总时长 {expected:.2f}s"
                )
        except Exception:
            copied = False
            fallback()
            got = duration(merged)
            if expected > 0 and got < expected * 0.9 - 0.5:
                raise ConversionError(
                    f"源文件无法拼接：输出时长 {got:.2f}s，"
                    f"源总时长 {expected:.2f}s"
                )

        if not merged.is_file() or merged.stat().st_size == 0:
            raise ConversionError("ffmpeg concat 没有产出有效文件")

        if progress:
            progress(0.4, "合并完成，开始转换")
        final = out_path
        if final is None:
            base_dir = out_dir or sources[0].parent
            final = base_dir / f"{sources[0].stem}-concat{primary_extension(target_mt) or '.bin'}"
        result = convert_file(
            merged,
            target_mt,
            out_path=final,
            preset=preset,
            overwrite=overwrite,
            overrides=opts,
            progress=(
                (lambda frac, step: progress(0.4 + frac * 0.6, step))
                if progress else None
            ),
        )
        result.path_desc = (
            f"{len(sources)} 个文件 → [concat] → {label_of(target_mt)}"
        )
        if copied:
            result.warnings.insert(0, f"已按顺序合并 {len(sources)} 个文件（未重编码）")
        else:
            result.loss = LossTier.LOSSY
            result.warnings.insert(
                0,
                f"已按顺序合并 {len(sources)} 个文件；源编码/流布局不一致，"
                "合并前已统一转码（有损）",
            )
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
