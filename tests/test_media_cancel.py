"""媒体取消语义不依赖本机 ffmpeg 的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from convcore.engines import base, ffmpeg
from convcore.ir.media import MediaPlan, Stream
from convcore.plugins.media import FFmpegCodec
from convcore.policy import Policy
from convcore.types import Cancelled, Resource


def test_media_write_removes_partial_output_on_cancel(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source")
    plan = MediaPlan(
        source=Resource(media_type="video/x-matroska", path=source),
        streams=[Stream(index=0, kind="video", codec="h264")],
        duration=1.0,
    )

    monkeypatch.setattr("convcore.engines.probe.require", lambda _: Path("ffmpeg"))
    monkeypatch.setattr(
        ffmpeg,
        "build_command",
        lambda _exe, _src, dst, _ir, _decision, _container, _opts: ["fake", str(dst)],
    )

    def fake_stream_lines(cmd, **_kwargs):
        # 模拟 ffmpeg 已经创建了但尚未完成的容器文件。
        Path(cmd[-1]).write_bytes(b"partial")
        return iter(())

    monkeypatch.setattr(base, "stream_lines", fake_stream_lines)

    with pytest.raises(Cancelled, match="已取消"):
        FFmpegCodec().write(
            plan,
            "video/mp4",
            {
                "_tmp": tmp_path,
                "_policy": Policy(),
                "_cancel": lambda: True,
            },
        )

    assert not (tmp_path / "out.mp4").exists()
