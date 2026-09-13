"""测试 media → raster 跨域桥（视频截帧）。"""

from pathlib import Path
import pytest
from convcore.pipeline import convert_file


def test_video_to_image_via_bridge(sample_video, tmp_path):
    """视频→图片应走 media→raster 桥，提取第一帧。"""
    out = tmp_path / "frame.png"
    res = convert_file(sample_video, "png", out_path=out, overwrite=True)

    assert out.exists()
    assert out.stat().st_size > 500  # 320x240 红色帧，PNG 压缩后约 700-800 字节
    # 路径应显示桥接
    assert "media" in res.path_desc.lower() and "raster" in res.path_desc.lower()
    # 提取帧本身无损
    assert res.loss.value <= 1  # LOSSLESS or COPY


def test_video_to_jpg(sample_video, tmp_path):
    """视频→JPEG 也应能提取帧。"""
    out = tmp_path / "cover.jpg"
    res = convert_file(sample_video, "jpg", out_path=out, overwrite=True)

    assert out.exists()
    assert out.stat().st_size > 500


@pytest.fixture
def sample_video(tmp_path):
    """生成一个简单的测试视频（1秒，红色帧）。"""
    from convcore.engines.probe import require

    video = tmp_path / "test.mp4"
    exe = require("ffmpeg")

    import subprocess
    subprocess.run(
        [
            str(exe), "-y",
            "-f", "lavfi",
            "-i", "color=c=red:s=320x240:d=1",
            "-pix_fmt", "yuv420p",
            str(video),
        ],
        check=True,
        capture_output=True,
    )

    return video
