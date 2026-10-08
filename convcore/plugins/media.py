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
        "force_transcode",
    }
)
_AUDIO_OPTS = frozenset(
    {"quality", "audio_bitrate", "sample_rate", "channels", "start", "duration"}
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
            ir, target, force_transcode=bool(options.get("force_transcode"))
        )

        kept = [a for a in decision.actions if a.action != "drop"]
        if not kept:
            raise ConversionError(
                f"{label_of(target)} 无法容纳源文件的任何流"
                f"（源：{ir.describe()}）"
            )

        self._record_decision(ir, decision, target, pol)

        cmd = ffmpeg.build_command(exe, src, dst, ir, decision, options)

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

