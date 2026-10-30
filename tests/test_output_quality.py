"""输出质量修复的回归测试（审计清单驱动）。

覆盖：GIF 自适应调色板、EXIF 方向只应用一次、JPEG/PNG/WebP 默认参数、
预设的音视频覆盖、ffmpeg 质量参数映射（vorbis 刻度、mpeg4 默认、crf 默认、
声道感知码率）、build_command 的元数据 / fps / 字幕行为。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from convcore.pipeline import convert_file
from convcore.policy import PRESETS, Policy

pytest.importorskip("pymupdf", reason="文档桥需要 pymupdf")


def _gradient(size=(256, 256), shift=0) -> Image.Image:
    im = Image.new("RGB", size)
    im.putdata(
        [
            ((x * 7 + shift) % 256, (y * 11) % 256, (x + y) % 256)
            for y in range(size[1])
            for x in range(size[0])
        ]
    )
    return im


# ── GIF：自适应调色板（原来用 WEB 216 色安全调色板） ──


def test_gif_uses_adaptive_palette(tmp_path):
    """渐变真彩图转 GIF 必须用自适应调色板（>216 色），不能 posterize。"""
    src = tmp_path / "grad.png"
    _gradient().save(src)
    res = convert_file(src, "gif", out_dir=tmp_path, overwrite=True)
    with Image.open(res.outputs[0].path) as out:
        colors = out.convert("RGB").getcolors(maxcolors=1 << 16) or []
    assert len(colors) > 216, "仍是 WEB 216 色安全调色板，应用首帧中值切分"


def test_animated_gif_uses_one_adaptive_palette(tmp_path):
    """动图所有帧映射到同一张自适应调色板：颜色自适应且不闪烁。"""
    frames = [_gradient((128, 128), shift=i * 40) for i in range(3)]
    src = tmp_path / "anim.webp"
    frames[0].save(src, save_all=True, append_images=frames[1:], duration=100, loop=0)
    res = convert_file(src, "gif", out_dir=tmp_path, overwrite=True)
    with Image.open(res.outputs[0].path) as out:
        assert getattr(out, "n_frames", 1) == 3
        out.seek(0)
        colors = out.convert("RGB").getcolors(maxcolors=1 << 16) or []
    assert len(colors) > 216


# ── EXIF 方向：像素旋正后旧标记必须剥掉（否则查看器二次旋转） ──


def test_exif_orientation_applied_exactly_once(tmp_path):
    im = Image.new("RGB", (40, 20), "white")
    im.paste(Image.new("RGB", (10, 10), "red"), (0, 0))
    exif = Image.Exif()
    exif[0x0112] = 6  # 顺时针 90°
    exif[0x010F] = "TestCam"
    src = tmp_path / "rot.jpg"
    im.save(src, exif=exif.tobytes(), quality=95)

    res = convert_file(src, "png", out_dir=tmp_path, overwrite=True)
    with Image.open(res.outputs[0].path) as out:
        assert out.size == (20, 40), "像素没有按 EXIF 方向旋正"
        got = out.getexif()
        assert got.get(0x0112) is None, "旧 Orientation 标记没剥掉"
        assert got.get(0x010F) == "TestCam", "其余 EXIF 字段应保留"


# ── 编码器默认参数（单元级：_save_kwargs 的产物） ──


def _kv(target: str, options: dict | None = None) -> dict:
    from convcore.ir.raster import Frame, Raster
    from convcore.plugins.image import PillowCodec

    raster = Raster(
        frames=[Frame(Image.new("RGB", (64, 64), "red"))], source_type="image/png"
    )
    return PillowCodec()._save_kwargs(
        target, {"pil": target.split("/")[-1]}, raster, options or {}, Policy()
    )


def test_jpeg_default_is_chroma_quality_not_slow_optimize():
    """默认修的是画质相关的 4:2:0；体积换时间的 optimize 归预设。"""
    kw = _kv("image/jpeg")
    assert kw["subsampling"] == 1, "默认不应是 4:2:0"
    assert not kw["optimize"], "optimize 是体积换编码时间，代码默认不开"
    assert PRESETS["original"].options_for("image/jpeg")["optimize"] is True


def test_png_size_first_options_live_in_presets():
    """PNG 无损：级别只换体积；代码默认快档，original/archive 拉满。"""
    kw = _kv("image/png")
    assert not kw["optimize"] and "compress_level" not in kw
    assert PRESETS["original"].options_for("image/png") == {
        "compress_level": 9, "optimize": True
    }


def test_webp_default_depth_fast_and_alpha_options():
    kw = _kv("image/webp", {"alpha_quality": 90, "exact": True})
    assert kw["method"] == 2
    assert kw["alpha_quality"] == 90 and kw["exact"] is True


def test_webp_lossless_auto_follows_source():
    """archive 的 lossless="auto"：无损源才无损，有损源走高质量有损。"""
    from convcore.ir.raster import Frame, Raster
    from convcore.plugins.image import PillowCodec

    codec = PillowCodec()
    im = Image.new("RGB", (32, 32), "red")
    spec = {"pil": "WEBP"}
    lossless_src = Raster(frames=[Frame(im)], source_type="image/png")
    lossy_src = Raster(frames=[Frame(im)], source_type="image/jpeg")
    kw = codec._save_kwargs("image/webp", spec, lossless_src, {"lossless": "auto"}, Policy())
    assert kw.get("lossless") is True
    kw = codec._save_kwargs("image/webp", spec, lossy_src, {"lossless": "auto"}, Policy())
    assert "lossless" not in kw and kw["quality"] == 85


# ── 预设：音视频覆盖 + 域通配 + 归档语义 ──


def test_presets_cover_video_and_audio():
    web = PRESETS["web"].options_for("video/mp4")
    assert web["max_edge"] == 1920 and web["quality"] == 70
    assert PRESETS["web"].options_for("audio/mpeg")["quality"] == 70
    archive = PRESETS["archive"].options_for("video/mp4")
    assert archive["quality"] == 95 and archive["preset_speed"] == "slow"


def test_archive_preset_webp_is_source_aware():
    arch = PRESETS["archive"].options_for("image/webp")
    assert arch["lossless"] == "auto", "有损源不该被强制无损 WebP"
    assert PRESETS["original"].options_for("image/jpeg")["subsampling"] == 0



def test_fps_marks_video_for_transcode_in_decision():
    """fps 只强制视频轨重编码；决策与 build_command 必须一致，否则损失等级骗人。"""
    from convcore.engines import codecs

    class _Stream:
        index, kind, codec, is_attached_pic = 0, "video", "h264", False

    class _Plan:
        streams = [_Stream()]

    plain = codecs.decide(_Plan(), "video/mp4")
    assert plain.actions[0].action == "copy"
    forced = codecs.decide(_Plan(), "video/mp4", force_video_transcode=True)
    assert forced.actions[0].action == "transcode"
    assert forced.can_copy_all is False

# ── ffmpeg 质量参数映射 ──


def test_vorbis_quality_scale_not_inverted():
    from convcore.engines.ffmpeg import _quality_args

    assert _quality_args("libvorbis", "audio", {}) == ["-q:a", "6"]
    assert _quality_args("libvorbis", "audio", {"quality": 100}) == ["-q:a", "10"]
    assert _quality_args("libvorbis", "audio", {"quality": 1}) == ["-q:a", "-1"]


def test_mpeg4_has_default_quality():
    from convcore.engines.ffmpeg import _quality_args

    assert _quality_args("mpeg4", "video", {}) == ["-q:v", "3"]


def test_crf_defaults_split_by_encoder():
    from convcore.engines.ffmpeg import _quality_args

    assert _quality_args("libx264", "video", {}) == ["-crf", "20"]
    assert _quality_args("libx265", "video", {}) == ["-crf", "22"]
    assert _quality_args("libsvtav1", "video", {}) == ["-crf", "30", "-b:v", "0"]


def test_audio_bitrate_scales_with_channels():
    from convcore.engines.ffmpeg import _quality_args

    assert _quality_args("aac", "audio", {}) == ["-b:a", "192k"]
    assert _quality_args("aac", "audio", {}, channels=6) == ["-b:a", "448k"]


# ── build_command 行为 ──


class _Plan:
    is_video = True
    extra: dict = {}
    primary_audio = None


def _cmd(decision, container="video/mp4", opts=None):
    from convcore.engines.ffmpeg import build_command

    return [
        str(c)
        for c in build_command(
            Path("ffmpeg"), Path("in.mp4"), Path("out.mp4"),
            _Plan(), decision, container, opts or {},
        )
    ]


def _action(kind, action, encoder=None):
    from convcore.engines.codecs import StreamAction

    return StreamAction(0, kind, action, encoder=encoder)


def _decision():
    from convcore.engines.codecs import RemuxDecision

    return RemuxDecision([_action("video", "copy")], can_copy_all=True)


def test_build_command_passes_metadata_and_chapters():
    cmd = _cmd(_decision())
    assert cmd[cmd.index("-map_metadata") + 1] == "0"
    assert cmd[cmd.index("-map_chapters") + 1] == "0"
    stripped = _cmd(_decision(), opts={"keep_metadata": False})
    assert stripped[stripped.index("-map_metadata") + 1] == "-1"


def test_fps_forces_transcode():
    """显式 fps 时不能走流复制——-r/滤镜根本没机会生效。"""
    cmd = _cmd(_decision(), opts={"fps": 30})
    assert "copy" not in cmd
    assert "fps=30" in " ".join(cmd)


def test_subtitle_transcode_follows_container():
    from convcore.engines.codecs import RemuxDecision

    decision = RemuxDecision(
        [_action("subtitle", "transcode", encoder="srt")], can_copy_all=False
    )
    mp4 = _cmd(decision)
    assert mp4[mp4.index("-c:s") + 1] == "mov_text"
    mkv = _cmd(decision, container="video/x-matroska")
    assert mkv[mkv.index("-c:s") + 1] == "srt"


def test_incompatible_subtitles_transcode_instead_of_dropped():
    """容器不支持源字幕编码时应转码而不是静默丢弃。"""
    from convcore.engines import codecs

    class _Stream:
        index, kind, codec = 2, "subtitle", "ass"
        is_attached_pic = False

    class _Plan:
        streams = [_Stream()]

    decision = codecs.decide(_Plan(), "video/mp4")
    (action,) = decision.actions
    assert action.action == "transcode" and action.encoder == "mov_text"


# ── HEIC/AVIF：动图不再静默丢帧 ──


def test_animated_source_to_heic_exports_every_frame(tmp_path):
    pytest.importorskip("pillow_heif")
    frames = [_gradient((64, 64), shift=i * 60) for i in range(3)]
    src = tmp_path / "anim.webp"
    frames[0].save(src, save_all=True, append_images=frames[1:], duration=100, loop=0)
    res = convert_file(src, "heic", out_dir=tmp_path, overwrite=True)
    assert len(res.outputs) == 3, "动图转 HEIC 必须导出每帧，而不是静默只留首帧"
    assert any("帧" in str(w) for w in res.warnings)


def test_jpegxl_roundtrip(tmp_path):
    """装有 pillow-jxl-plugin 时,PNG→JXL→PNG 应可用且能嗅探。"""
    from convcore.plugins.image import _HAS_JXL
    from convcore.sniff import sniff

    if not _HAS_JXL:
        pytest.skip("需要 pillow-jxl-plugin")

    src = tmp_path / "gradient.png"
    _gradient((64, 48)).save(src)
    enc = convert_file(src, "jxl", out_path=tmp_path / "gradient.jxl", overwrite=True)
    assert sniff(enc.outputs[0].path) == "image/jxl"

    dec = convert_file(
        enc.outputs[0].path, "png", out_path=tmp_path / "back.png", overwrite=True
    )
    with Image.open(dec.outputs[0].path) as im:
        assert im.size == (64, 48)
