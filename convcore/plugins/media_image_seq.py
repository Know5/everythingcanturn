"""raster → media 跨域桥：图片序列转视频。

需求背景：用户有一组图片（帧序列或多帧动图），想合成视频。这是典型的跨域转换：
raster IR → media IR，然后由 FFmpegCodec 写出目标格式。

实现要点：
- 帧序列需要临时写入磁盘（ffmpeg 只能从文件读取图片序列）
- 使用 -framerate 和 -pattern_type sequence 让 ffmpeg 按顺序读取
- MediaPlan 需要携带 source Resource 引用（ffmpeg 要读原始文件）
- 这里创建一个虚拟的 Resource 指向帧序列模式串
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..ir.media import MediaPlan, Stream
from ..ir.raster import Raster
from ..policy import Policy
from ..types import ConversionError, Resource


def raster_to_video(
    ir: Raster, opts: dict[str, Any], pol: Policy
) -> MediaPlan:
    """将 Raster IR（图片序列/多帧动图）转为视频 MediaPlan。

    策略：
    1. 把每帧写成临时 PNG 文件（frame_00000.png, frame_00001.png, ...）
    2. 构造一个虚拟的 Resource，path 指向模式串 frame_%05d.png
    3. 返回 MediaPlan 并附带 ffmpeg 输入参数（-framerate, -pattern_type sequence）
    """
    if not ir.frames:
        raise ConversionError("raster→media 桥：输入为空")

    # 从选项获取帧率，默认 25fps
    # 用户显式指定 fps 时优先使用，即使动图有自己的帧延时
    fps = opts.get("fps")
    if fps is None:
        # 未指定时：动图用自身帧延时，静态序列用默认 25fps
        if ir.is_animated:
            total_ms = sum(f.duration_ms for f in ir.frames)
            if total_ms > 0:
                fps = len(ir.frames) * 1000 / total_ms
            else:
                fps = 25
        else:
            fps = 25

    # 获取临时目录（由 pipeline._run 传入）
    tmp = Path(opts.get("_tmp") or ".")

    # 把帧序列写到临时目录
    for i, frame in enumerate(ir.frames):
        frame_path = tmp / f"frame_{i:05d}.png"
        frame.image.save(frame_path, format="PNG")

    # 构造模式串路径，ffmpeg 用它识别序列
    input_pattern = tmp / "frame_%05d.png"

    # 创建虚拟 Resource 指向图片序列
    # MediaPlan 需要 source: Resource，但这里没有单一源文件，
    # 所以构造一个路径指向模式串
    source_res = Resource(
        media_type="image/png",  # 占位，实际是序列
        path=input_pattern,
        name=input_pattern.name,
    )

    # 构造虚拟的视频流信息（实际流信息由 ffmpeg 写入时决定）
    w, h = ir.primary.size
    video_stream = Stream(
        index=0,
        kind="video",
        codec="png",  # 输入编码
        width=w,
        height=h,
        fps=fps,
    )

    # 总时长 = 帧数 / 帧率
    duration = len(ir.frames) / fps

    # 把 -framerate 和 -pattern_type 编码到 extra 字段，让 FFmpegCodec.write 使用
    plan = MediaPlan(
        source=source_res,
        streams=[video_stream],
        container="image2",  # ffmpeg 的图片序列解复用器
        duration=duration,
        extra={
            "input_framerate": fps,
            "input_pattern_type": "sequence",
        },
    )

    return plan
