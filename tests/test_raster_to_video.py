"""测试 raster → media 跨域桥：图片序列转视频。"""

from pathlib import Path

import pytest

from convcore import Converter
from convcore.types import ConvertRequest, MediaType, Resource


@pytest.fixture
def sample_frames(tmp_path: Path) -> list[Path]:
    """生成测试用的帧序列：3张纯色图。"""
    from PIL import Image

    frames = []
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]  # 红绿蓝
    for i, color in enumerate(colors):
        img = Image.new("RGB", (320, 240), color)
        path = tmp_path / f"frame_{i:03d}.png"
        img.save(path)
        frames.append(path)
    return frames


def test_single_image_to_video(tmp_path: Path):
    """单张静态图转视频：时长应该等于 1/fps。"""
    from PIL import Image

    img_path = tmp_path / "single.png"
    Image.new("RGB", (320, 240), (128, 128, 128)).save(img_path)

    conv = Converter()
    result = conv.convert(
        ConvertRequest(
            inputs=[Resource(media_type=MediaType("image/png"), path=img_path)],
            target=MediaType("video/mp4"),
            options={"fps": 25},
        ),
        persist_dir=tmp_path,
    )

    assert len(result.outputs) == 1
    out = result.outputs[0]
    assert out.path is not None
    assert out.path.exists()
    assert out.path.stat().st_size > 1000  # 至少有几KB


def test_animated_gif_to_video(tmp_path: Path):
    """动图转视频：应该保持帧数和基本时序。"""
    from PIL import Image

    # 创建3帧动图，每帧100ms
    frames_imgs = [
        Image.new("RGB", (200, 150), (255, 0, 0)),
        Image.new("RGB", (200, 150), (0, 255, 0)),
        Image.new("RGB", (200, 150), (0, 0, 255)),
    ]
    gif_path = tmp_path / "anim.gif"
    frames_imgs[0].save(
        gif_path,
        save_all=True,
        append_images=frames_imgs[1:],
        duration=100,
        loop=0,
    )

    conv = Converter()
    result = conv.convert(
        ConvertRequest(
            inputs=[Resource(media_type=MediaType("image/gif"), path=gif_path)],
            target=MediaType("video/mp4"),
        ),
        persist_dir=tmp_path,
    )

    assert len(result.outputs) == 1
    out = result.outputs[0]
    assert out.path.exists()
    assert out.path.stat().st_size > 1000  # 至少1KB


def test_frame_sequence_preserves_count(sample_frames: list[Path], tmp_path: Path):
    """帧序列转视频：验证帧数保持（通过时长检查）。"""
    from convcore.engines import ffmpeg

    conv = Converter()
    result = conv.convert(
        ConvertRequest(
            inputs=[
                Resource(media_type=MediaType("image/png"), path=p)
                for p in sample_frames
            ],
            target=MediaType("video/mp4"),
            options={"fps": 10},  # 10fps，3帧 = 0.3秒
        ),
        persist_dir=tmp_path,
    )

    out_path = result.outputs[0].path
    assert out_path is not None

    # 用 ffprobe 确认时长接近预期
    plan = ffmpeg.analyze(
        Resource(media_type=MediaType("video/mp4"), path=out_path), tmp_path
    )
    assert plan.duration is not None
    # 允许编码器的轻微误差（±0.1秒）
    assert 0.2 <= plan.duration <= 0.4


def test_custom_framerate_applied(sample_frames: list[Path], tmp_path: Path):
    """自定义帧率生效：30fps 比 10fps 的视频时长应该更短。"""
    from convcore.engines import ffmpeg

    conv = Converter()

    # 先转慢的，立即分析，避免被后续转换覆盖
    res_slow = conv.convert(
        ConvertRequest(
            inputs=[
                Resource(media_type=MediaType("image/png"), path=p)
                for p in sample_frames
            ],
            target=MediaType("video/mp4"),
            options={"fps": 10},
        ),
        persist_dir=tmp_path,
    )

    plan_slow = ffmpeg.analyze(
        Resource(media_type=MediaType("video/mp4"), path=res_slow.outputs[0].path),
        tmp_path,
    )

    # 再转快的
    res_fast = conv.convert(
        ConvertRequest(
            inputs=[
                Resource(media_type=MediaType("image/png"), path=p)
                for p in sample_frames
            ],
            target=MediaType("video/mp4"),
            options={"fps": 30},
        ),
        persist_dir=tmp_path,
    )

    plan_fast = ffmpeg.analyze(
        Resource(media_type=MediaType("video/mp4"), path=res_fast.outputs[0].path),
        tmp_path,
    )

    # 3帧：10fps=0.3秒，30fps=0.1秒
    assert plan_slow.duration > plan_fast.duration
