"""音视频转换测试。

需要 ffmpeg，没装就整体跳过。样本用 ffmpeg 自己的 lavfi 合成，不提交二进制。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from convcore import LossTier, convert_file
from convcore.engines.probe import ENGINES, companion, probe_one

_spec = next(s for s in ENGINES if s.key == "ffmpeg")
_info = probe_one(_spec)

pytestmark = pytest.mark.skipif(
    not _info.available,
    reason="需要 ffmpeg：convcore engines install ffmpeg",
)


def _ff() -> Path:
    assert _info.path is not None
    return _info.path


def _make(args: list[str], out: Path) -> Path:
    subprocess.run(
        [str(_ff()), "-hide_banner", "-loglevel", "error", *args, str(out), "-y"],
        check=True,
        capture_output=True,
        timeout=180,
    )
    return out


def _codecs(path: Path) -> dict[str, str]:
    """返回 {kind: codec}，用于验证是否真的走了 copy。"""
    probe = companion("ffmpeg", "ffprobe")
    res = subprocess.run(
        [
            str(probe), "-v", "error",
            "-show_entries", "stream=codec_type,codec_name",
            "-of", "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    out: dict[str, str] = {}
    for line in res.stdout.strip().splitlines():
        parts = [p for p in line.split(",") if p]
        if len(parts) >= 2:
            name, kind = parts[0], parts[1]
            out.setdefault(kind, name)
    return out


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("media")
    out: dict[str, Path] = {}

    # H.264 + AAC 的 MKV：转 MP4 应当纯 remux
    out["mkv_h264"] = _make(
        [
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
        ],
        d / "clip.mkv",
    )
    # VP9 的 WebM：转 MP4 必须转码（MP4 不该装 VP9）
    out["webm_vp9"] = _make(
        [
            "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=15:duration=1",
            "-c:v", "libvpx-vp9", "-b:v", "200k", "-an",
        ],
        d / "clip.webm",
    )
    out["wav"] = _make(
        ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "pcm_s16le"],
        d / "tone.wav",
    )
    out["flac"] = _make(
        ["-f", "lavfi", "-i", "sine=frequency=660:duration=2", "-c:a", "flac"],
        d / "tone.flac",
    )
    return out


# ── remux 判定：这个项目最想做对的事 ──


def test_mkv_to_mp4_is_pure_remux(media, tmp_path):
    """H.264+AAC 的 MKV 转 MP4 只该改封装。

    重编码会白吃一次世代损失还慢几十倍，是同类工具最常见的错误。
    """
    res = convert_file(media["mkv_h264"], "mp4", out_dir=tmp_path, overwrite=True)

    assert res.loss is LossTier.COPY, f"应为 COPY，实为 {res.loss.name}"
    # 静态声明是 LOSSY（最坏情况），实测优于它
    assert res.planned_loss is LossTier.LOSSY
    assert any("改封装" in w or "未重新编码" in w for w in res.warnings)

    # 编码必须一模一样才算真 remux
    before, after = _codecs(media["mkv_h264"]), _codecs(res.outputs[0].path)
    assert after.get("video") == before.get("video") == "h264"
    assert after.get("audio") == before.get("audio") == "aac"


def test_vp9_webm_to_mp4_must_transcode(media, tmp_path):
    """MP4 装 VP9 兼容性很差，必须转码而不是硬塞。"""
    res = convert_file(media["webm_vp9"], "mp4", out_dir=tmp_path, overwrite=True)

    assert res.loss is LossTier.LOSSY
    assert _codecs(media["webm_vp9"]).get("video") == "vp9"
    assert _codecs(res.outputs[0].path).get("video") == "h264"


def test_mp4_roundtrip_back_to_mkv_still_copies(media, tmp_path):
    """MKV→MP4→MKV 全程 remux，编码应当始终不变。"""
    mp4 = convert_file(
        media["mkv_h264"], "mp4", out_dir=tmp_path / "a", overwrite=True
    ).outputs[0].path
    back = convert_file(mp4, "mkv", out_dir=tmp_path / "b", overwrite=True)

    assert back.loss is LossTier.COPY
    assert _codecs(back.outputs[0].path).get("video") == "h264"


# ── 音频 ──


def test_wav_to_flac_is_lossless_not_lossy(media, tmp_path):
    """FLAC 是无损压缩。报成「有损」会让用户误以为音质降了。"""
    res = convert_file(media["wav"], "flac", out_dir=tmp_path, overwrite=True)

    assert res.loss is LossTier.LOSSLESS, f"应为 LOSSLESS，实为 {res.loss.name}"
    assert res.outputs[0].path.stat().st_size < media["wav"].stat().st_size
    # 无损转换不该有告警——告警滥用会让用户忽略真正的警告
    assert not res.warnings, f"无损转换不该告警：{res.warnings}"


def test_flac_to_wav_is_lossless(media, tmp_path):
    res = convert_file(media["flac"], "wav", out_dir=tmp_path, overwrite=True)
    assert res.loss is LossTier.LOSSLESS
    assert _codecs(res.outputs[0].path).get("audio", "").startswith("pcm")


def test_wav_to_mp3_is_lossy(media, tmp_path):
    res = convert_file(media["wav"], "mp3", out_dir=tmp_path, overwrite=True)
    assert res.loss is LossTier.LOSSY
    assert _codecs(res.outputs[0].path).get("audio") == "mp3"


def test_extract_audio_drops_video_with_warning(media, tmp_path):
    """视频抽音轨：视频流必须被丢弃，且要告知用户。"""
    res = convert_file(media["mkv_h264"], "mp3", out_dir=tmp_path, overwrite=True)

    codecs = _codecs(res.outputs[0].path)
    assert "video" not in codecs
    assert codecs.get("audio") == "mp3"
    assert any("video" in w for w in res.warnings)


def test_dropping_a_stream_is_never_reported_as_copy(media, tmp_path):
    """MKV(h264+aac) → AAC：音频流是直接拷贝的，但视频被丢了。

    此时报「免重编码」会让用户以为什么都没丢。丢流就必须是 STRUCTURAL。
    """
    res = convert_file(media["mkv_h264"], "aac", out_dir=tmp_path, overwrite=True)

    assert res.loss is LossTier.STRUCTURAL, f"丢了视频流却报 {res.loss.name}"
    assert "video" not in _codecs(res.outputs[0].path)
    assert any("video" in w for w in res.warnings)


def test_webm_audio_target_rejects_gracefully(media, tmp_path):
    """无音轨的视频转纯音频格式应当明确报错，而不是产出空文件。"""
    from convcore import ConversionError

    with pytest.raises(ConversionError, match="无法容纳"):
        convert_file(media["webm_vp9"], "mp3", out_dir=tmp_path, overwrite=True)


# ── 参数 ──


def test_scale_applies_to_video(media, tmp_path):
    res = convert_file(
        media["mkv_h264"], "mp4", out_dir=tmp_path, overwrite=True,
        overrides={"width": 160},
    )
    probe = companion("ffmpeg", "ffprobe")
    out = subprocess.run(
        [
            str(probe), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0",
            str(res.outputs[0].path),
        ],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout.strip()
    assert out.startswith("160,"), f"缩放没生效：{out}"


def test_force_transcode_overrides_remux(media, tmp_path):
    """显式要求转码时不该走 copy 短路。"""
    res = convert_file(
        media["mkv_h264"], "mp4", out_dir=tmp_path, overwrite=True,
        overrides={"force_transcode": True},
    )
    assert res.loss is LossTier.LOSSY


def test_progress_is_monotonic(media, tmp_path):
    """长任务的进度必须单调不减，否则进度条会跳回去。"""
    seen: list[float] = []
    convert_file(
        media["mkv_h264"], "webm", out_dir=tmp_path, overwrite=True,
        progress=lambda f, s: seen.append(f),
        overrides={"quality": 30},
    )
    assert seen, "没有收到任何进度回调"
    assert seen == sorted(seen), f"进度回退了：{seen[:20]}"
    assert seen[-1] == pytest.approx(1.0)


# ── 探测 ──


def test_refine_targets_reports_true_tier_per_file(media):
    """UI 必须在转换前显示真实损失，而不是静态最坏情况。

    静态声明说 MKV→MP4 是 LOSSY（因为可能要转码），但这个具体文件能 remux。
    转换前告诉用户「免重编码」和转完才发现，是完全不同的体验。
    """
    from convcore.pipeline import Converter

    conv = Converter()
    src = media["mkv_h264"]
    src_mt = conv.detect(src)
    targets = conv.targets_for(src_mt)
    actual = conv.refine_targets(src, src_mt, targets)

    # 容器互转保留全部流 → COPY
    assert actual["video/mp4"] is LossTier.COPY
    assert actual["video/quicktime"] is LossTier.COPY
    # 静态声明仍是最坏情况
    assert targets["video/mp4"].loss is LossTier.LOSSY
    # VP9-only 的 WebM 装不了 h264 → 必须转码
    assert actual["video/webm"] is LossTier.LOSSY
    # 抽音轨丢视频 → STRUCTURAL
    assert actual["audio/aac"] is LossTier.STRUCTURAL


def test_refine_targets_on_audio_only_source(media):
    from convcore.pipeline import Converter

    conv = Converter()
    src = media["wav"]
    src_mt = conv.detect(src)
    actual = conv.refine_targets(src, src_mt, conv.targets_for(src_mt))

    # PCM→FLAC 无损，且没有流被丢
    assert actual["audio/flac"] is LossTier.LOSSLESS
    assert actual["audio/mpeg"] is LossTier.LOSSY


def test_refine_targets_drops_what_this_file_cannot_do(media):
    """纯音频截不出图、无声视频抽不出音轨：这些目标不该出现在列表里。"""
    from convcore.pipeline import Converter

    conv = Converter()
    wav = media["wav"]
    actual = conv.refine_targets(wav, "audio/wav", conv.targets_for("audio/wav"))
    assert "audio/flac" in actual
    assert "image/png" not in actual and "application/pdf" not in actual

    silent = media["webm_vp9"]  # 生成时 -an，没有音轨
    actual = conv.refine_targets(silent, "video/webm", conv.targets_for("video/webm"))
    assert "video/mp4" in actual and "image/png" in actual
    assert "audio/mpeg" not in actual and "audio/wav" not in actual


def test_audio_without_cover_to_image_says_why(media, tmp_path):
    """列表里已经不给，但 API/CLI 仍可能硬转。要说清原因，不能甩一个 ffmpeg 退出码。"""
    from convcore.types import ConversionError

    with pytest.raises(ConversionError, match="没有画面也没有封面"):
        convert_file(media["wav"], "png", out_dir=tmp_path)


@pytest.mark.parametrize(("size", "target"), [((65, 64), "mp4"), ((64, 65), "wmv")])
def test_odd_sized_image_to_video(size, target, tmp_path):
    """libx264 / wmv2 要求偶数宽高。PDF 页渲染（1240×1755）这类奇数尺寸以前直接报错。"""
    from PIL import Image

    src = tmp_path / "odd.png"
    Image.new("RGB", size, (200, 30, 40)).save(src)
    res = convert_file(src, target, out_dir=tmp_path)
    assert res.outputs[0].path.stat().st_size > 0


def test_transparent_image_to_webm(tmp_path):
    """带透明的源不指定 pix_fmt 时，ffmpeg 给 VP9 选 gbrap，libvpx 拒绝打开编码器。"""
    from PIL import Image

    src = tmp_path / "alpha.png"
    Image.new("RGBA", (64, 64), (200, 30, 40, 128)).save(src)
    res = convert_file(src, "webm", out_dir=tmp_path)
    assert res.outputs[0].path.stat().st_size > 0


def test_refine_targets_ignores_non_media(samples):
    """图片文件不该触发 ffprobe，静态声明直接返回。"""
    from convcore.pipeline import Converter

    conv = Converter()
    src_mt = "image/png"
    targets = conv.targets_for(src_mt)
    actual = conv.refine_targets(samples["png"], src_mt, targets)
    assert actual == {mt: p.loss for mt, p in targets.items()}


def test_analyze_reports_streams(media):
    from convcore.engines import ffmpeg
    from convcore.types import Resource

    plan = ffmpeg.analyze(
        Resource(media_type="video/x-matroska", path=media["mkv_h264"]),
        media["mkv_h264"].parent,
    )
    assert plan.is_video
    assert plan.primary_video is not None
    assert plan.primary_video.codec == "h264"
    assert plan.primary_audio is not None
    assert plan.primary_audio.codec == "aac"
    assert plan.duration and 1.5 < plan.duration < 3.0


def test_analyze_rejects_non_media(tmp_path):
    from convcore.engines import ffmpeg
    from convcore.types import ConversionError, Resource

    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"definitely not a video" * 50)
    with pytest.raises(ConversionError):
        ffmpeg.analyze(Resource(media_type="video/mp4", path=junk), tmp_path)
