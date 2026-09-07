"""测试样本生成。

用程序合成而不是提交二进制文件：仓库干净、样本可复现、想改特性（加透明通道、
改位深）就改代码。真实文件的验收放在手动步骤里，因为它们才有奇怪的 EXIF 和
非标准编码。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw


def _gradient_photo(size: tuple[int, int] = (320, 240)) -> Image.Image:
    """模拟照片：连续色调渐变 + 几何形状。

    渐变很重要——纯色块无法暴露色彩量化和抖动问题，而 GIF/调色板转换恰恰
    在渐变上最容易露馅。
    """
    w, h = size
    im = Image.new("RGB", size)
    px = im.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (
                int(255 * x / w),
                int(255 * y / h),
                int(255 * ((x + y) % w) / w),
            )
    d = ImageDraw.Draw(im)
    d.ellipse([w // 4, h // 4, w * 3 // 4, h * 3 // 4], outline=(255, 255, 255), width=3)
    d.rectangle([10, 10, 60, 40], fill=(0, 0, 0))
    return im


def _alpha_logo(size: tuple[int, int] = (128, 128)) -> Image.Image:
    """带透明通道的图标，用于验证 alpha 降级策略。"""
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([8, 8, size[0] - 8, size[1] - 8], fill=(220, 50, 60, 255))
    d.rectangle([size[0] // 2 - 6, 24, size[0] // 2 + 6, size[1] - 24], fill=(255, 255, 255, 255))
    return im


def _animation(frames: int = 4, size: tuple[int, int] = (64, 64)) -> list[Image.Image]:
    """多帧动图，用于验证帧序列不被吃掉。"""
    out = []
    for i in range(frames):
        im = Image.new("RGB", size, (20, 20, 30))
        d = ImageDraw.Draw(im)
        x = 8 + i * (size[0] - 24) // max(frames - 1, 1)
        d.ellipse([x, 24, x + 16, 40], fill=(240, 200, 60))
        out.append(im)
    return out


@pytest.fixture(scope="session")
def samples(tmp_path_factory) -> dict[str, Path]:
    """一次生成全套样本，整个会话共用。"""
    d = tmp_path_factory.mktemp("samples")
    out: dict[str, Path] = {}

    photo = _gradient_photo()
    photo.save(d / "photo.png")
    out["png"] = d / "photo.png"

    photo.save(d / "photo.jpg", quality=95)
    out["jpg"] = d / "photo.jpg"

    photo.save(d / "photo.webp", quality=95)
    out["webp"] = d / "photo.webp"

    photo.save(d / "photo.bmp")
    out["bmp"] = d / "photo.bmp"

    photo.save(d / "photo.tiff")
    out["tiff"] = d / "photo.tiff"

    logo = _alpha_logo()
    logo.save(d / "logo.png")
    out["alpha_png"] = d / "logo.png"

    frames = _animation()
    frames[0].save(
        d / "anim.gif",
        save_all=True,
        append_images=frames[1:],
        duration=120,
        loop=0,
    )
    out["gif"] = d / "anim.gif"

    # 竖长图，用于验证 ICO 补边和 EXIF 方向处理
    _gradient_photo((100, 200)).save(d / "tall.png")
    out["tall_png"] = d / "tall.png"

    (d / "icon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
        '<circle cx="32" cy="32" r="28" fill="#e03b4a"/></svg>',
        encoding="utf-8",
    )
    out["svg"] = d / "icon.svg"

    # 有 HEIC 支持时补一个，iPhone 照片是最高频的真实场景
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        photo.save(d / "photo.heic", quality=95)
        out["heic"] = d / "photo.heic"
    except Exception:
        pass

    return out


@pytest.fixture(scope="session")
def converter():
    from convcore.pipeline import Converter

    return Converter()
