"""多图合成动图：GIF/WebP/TIFF 的帧与调色板回归。"""

from __future__ import annotations

from PIL import Image

import pytest

from convcore import ConversionError
from convcore.pipeline import merge_files


def _images(tmp_path):
    paths = []
    for i, color in enumerate(("red", "green", "blue")):
        path = tmp_path / f"{i}.png"
        Image.new("RGB", (40, 30), color).save(path)
        paths.append(path)
    return paths


def test_merge_images_to_animated_gif(tmp_path):
    paths = _images(tmp_path)
    res = merge_files(paths, "gif", out_dir=tmp_path, overwrite=True)
    assert len(res.outputs) == 1
    with Image.open(res.outputs[0].path) as im:
        assert im.n_frames == 3
        colors = []
        for i in range(im.n_frames):
            im.seek(i)
            colors.append(im.convert("RGB").getpixel((0, 0)))
    assert len(set(colors)) == 3, f"多图调色板丢失了颜色：{colors}"


def test_merge_images_mixed_formats(tmp_path):
    paths = _images(tmp_path)
    jpg = tmp_path / "0.jpg"
    paths[0].replace(jpg)
    paths[0] = jpg
    res = merge_files(paths, "webp", out_dir=tmp_path, overwrite=True)
    with Image.open(res.outputs[0].path) as im:
        assert im.n_frames == 3


def test_merge_images_rejects_non_animation_target(tmp_path):
    paths = _images(tmp_path)
    with pytest.raises(ConversionError, match="GIF / WebP / TIFF"):
        merge_files(paths, "png", out_dir=tmp_path, overwrite=True)
