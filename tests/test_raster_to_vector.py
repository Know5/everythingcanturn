"""raster → vector 描摹桥的集成测试（需要 vtracer，缺则整文件跳过）。

语义是「能出合法 SVG、图标能出路径、照片默认拒绝」，不对比曲线好不好看。
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from convcore import Converter
from convcore.pipeline import convert_file
from convcore.types import ConversionError, LossTier

pytest.importorskip("pymupdf", reason="conftest 的样本与文档桥需要")
vtracer = pytest.importorskip("vtracer", reason="需要 vtracer 才测描摹")


def _logo(path: Path, size: tuple[int, int] = (96, 96)) -> Path:
    """纯色圆 Logo：少色、抗锯齿，典型可描摹输入。"""
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    d.ellipse((10, 10, size[0] - 10, size[1] - 10), fill="#e03b4a")
    im.save(path)
    return path


@pytest.fixture(scope="module")
def conv() -> Converter:
    return Converter()


def test_logo_png_to_svg(tmp_path):
    """少色 Logo → SVG：合法产物、含路径元素、档位 INFERRED。"""
    src = _logo(tmp_path / "logo.png")
    res = convert_file(src, "svg", out_dir=tmp_path, overwrite=True)
    out = res.outputs[0].path
    assert out is not None and out.suffix == ".svg"
    text = out.read_text(encoding="utf-8")
    assert "<svg" in text
    assert any(tag in text for tag in ("<path", "<circle", "<ellipse", "<rect"))
    assert res.loss is LossTier.INFERRED


def test_svg_listed_in_targets(tmp_path, conv):
    """装了 vtracer，PNG 的目标列表里应该有 SVG。"""
    src = _logo(tmp_path / "logo2.png")
    listed = conv.refine_targets(src, "image/png", conv.targets_for("image/png"))
    assert "image/svg+xml" in listed


def test_refine_drops_svg_for_photo(tmp_path, conv):
    """照片在 refine 阶段就被剔除 SVG 目标——列出来的都必须真能转。"""
    rnd = random.Random(7)
    im = Image.new("RGB", (256, 256))
    im.putdata(
        [
            (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
            for _ in range(256 * 256)
        ]
    )
    src = tmp_path / "photo-refine.png"
    im.save(src)
    listed = conv.refine_targets(src, "image/png", conv.targets_for("image/png"))
    assert "image/svg+xml" not in listed


def test_photo_rejected_without_force(tmp_path):
    """随机噪声不是图标：默认拒绝，错误信息指向 --force。"""
    rnd = random.Random(7)
    im = Image.new("RGB", (256, 256))
    im.putdata(
        [
            (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
            for _ in range(256 * 256)
        ]
    )
    src = tmp_path / "photo.png"
    im.save(src)
    with pytest.raises(ConversionError, match="不像可描摹的图标"):
        convert_file(src, "svg", out_dir=tmp_path, overwrite=True)


def test_photo_with_force_succeeds_with_warning(tmp_path):
    """--force（API 里是 overrides={'force': True}）可强制描摹，但必须留下硬警告。"""
    rnd = random.Random(7)
    im = Image.new("RGB", (256, 256))
    im.putdata(
        [
            (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
            for _ in range(256 * 256)
        ]
    )
    src = tmp_path / "photo2.png"
    im.save(src)
    res = convert_file(
        src, "svg", out_dir=tmp_path, overwrite=True, overrides={"force": True}
    )
    assert res.outputs[0].path is not None
    assert any("强制" in str(w) for w in res.warnings), "强制描摹必须显式告警"


def test_transparent_png_traces(tmp_path):
    """色块 + 透明底：描出的 SVG 合法，不是空壳。"""
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((8, 8, 56, 56), fill=(59, 109, 255, 255))
    src = tmp_path / "shapes.png"
    im.save(src)
    res = convert_file(src, "svg", out_dir=tmp_path, overwrite=True)
    text = res.outputs[0].path.read_text(encoding="utf-8")
    assert "<svg" in text
    assert any(tag in text for tag in ("<path", "<rect"))


def test_fully_transparent_rejected(tmp_path):
    """全透明没有任何轮廓可描，必须拒绝而不是写一个空 <svg>。"""
    im = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    src = tmp_path / "empty.png"
    im.save(src)
    with pytest.raises(ConversionError, match="全透明"):
        convert_file(src, "svg", out_dir=tmp_path, overwrite=True)


def test_animated_gif_warns_first_frame_only(tmp_path):
    """动图只描第一帧，必须显式告警。"""
    frames = [Image.new("RGB", (32, 32), c) for c in ("red", "white", "blue")]
    src = tmp_path / "anim.gif"
    frames[0].save(
        src, save_all=True, append_images=frames[1:], duration=200, loop=0
    )
    res = convert_file(src, "svg", out_dir=tmp_path, overwrite=True)
    assert res.outputs[0].path is not None
    assert any("帧" in str(w) for w in res.warnings)


def test_gate_hides_target_without_vtracer(monkeypatch, tmp_path):
    """门控探测为假后，PNG 的目标列表里没有 SVG——列出来的都必须真能转。"""
    import convcore.plugins.vector as vector_mod

    monkeypatch.setattr(vector_mod, "has_vtracer", lambda: False)
    fresh = Converter()
    assert "image/svg+xml" not in fresh.targets_for("image/png")


def test_formats_domain_reach_respects_gate(monkeypatch, capsys):
    """formats 的域间「可转去」也要过门控：没 vtracer 就不宣称 raster→vector。"""
    import convcore.cli as cli
    import convcore.plugins.vector as vector_mod

    monkeypatch.setattr(vector_mod, "has_vtracer", lambda: False)
    assert cli.main(["formats"]) == 0
    out = capsys.readouterr().out
    raster_line = next(line for line in out.splitlines() if line.startswith("raster"))
    assert "vector" not in raster_line
