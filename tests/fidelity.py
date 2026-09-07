"""保真度度量。

自己实现 PSNR/SSIM 而不用 scikit-image：只为两个指标拖进 scipy 依赖不值当，
而且这两个算法本身很短。SSIM 用均匀窗口而非高斯窗口，差异对回归测试无影响。
"""

from __future__ import annotations

import numpy as np
from PIL import Image


def _as_gray(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("L"), dtype=np.float64)


def _as_rgb(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("RGB"), dtype=np.float64)


def psnr(a: Image.Image, b: Image.Image) -> float:
    """峰值信噪比，dB。完全相同返回 inf。

    经验阈值：>40dB 肉眼难辨，30-40dB 良好，<25dB 明显劣化。
    """
    if a.size != b.size:
        raise ValueError(f"尺寸不同无法比较：{a.size} vs {b.size}")
    x, y = _as_rgb(a), _as_rgb(b)
    mse = float(np.mean((x - y) ** 2))
    if mse == 0.0:
        return float("inf")
    return 10.0 * float(np.log10(255.0**2 / mse))


def _uniform_filter(img: np.ndarray, size: int) -> np.ndarray:
    """滑动窗口均值。用积分图做到 O(n) 而不依赖 scipy。"""
    pad = size // 2
    padded = np.pad(img, pad, mode="reflect")
    # 二维前缀和
    cum = padded.cumsum(axis=0).cumsum(axis=1)
    cum = np.pad(cum, ((1, 0), (1, 0)), mode="constant")
    h, w = img.shape
    total = (
        cum[size:size + h, size:size + w]
        - cum[0:h, size:size + w]
        - cum[size:size + h, 0:w]
        + cum[0:h, 0:w]
    )
    return total / (size * size)


def ssim(a: Image.Image, b: Image.Image, *, win: int = 7) -> float:
    """结构相似度，范围 [-1, 1]，1 表示完全相同。

    比 PSNR 更贴近人的感知：PSNR 对整体亮度偏移很敏感但人眼不敏感，
    SSIM 关注局部结构。判断「转换有没有毁掉图像」用 SSIM 更可靠。
    """
    if a.size != b.size:
        raise ValueError(f"尺寸不同无法比较：{a.size} vs {b.size}")
    x, y = _as_gray(a), _as_gray(b)

    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_x, mu_y = _uniform_filter(x, win), _uniform_filter(y, win)
    xx, yy, xy = _uniform_filter(x * x, win), _uniform_filter(y * y, win), _uniform_filter(x * y, win)

    var_x = np.maximum(xx - mu_x**2, 0)
    var_y = np.maximum(yy - mu_y**2, 0)
    cov = xy - mu_x * mu_y

    num = (2 * mu_x * mu_y + c1) * (2 * cov + c2)
    den = (mu_x**2 + mu_y**2 + c1) * (var_x + var_y + c2)
    return float(np.mean(num / den))


def pixels_identical(a: Image.Image, b: Image.Image) -> bool:
    """逐像素完全相同。用于验证无损路径。"""
    if a.size != b.size:
        return False
    mode = "RGBA" if (a.mode in ("RGBA", "LA", "P") or b.mode in ("RGBA", "LA", "P")) else "RGB"
    return np.array_equal(
        np.asarray(a.convert(mode)), np.asarray(b.convert(mode))
    )


# ── 供测试矩阵使用的校验器工厂 ──


def ssim_above(threshold: float):
    def check(src: Image.Image, out: Image.Image) -> None:
        score = ssim(src, out)
        assert score >= threshold, f"SSIM {score:.4f} < 要求的 {threshold}"

    check.__name__ = f"ssim>={threshold}"
    return check


def psnr_above(threshold: float):
    def check(src: Image.Image, out: Image.Image) -> None:
        score = psnr(src, out)
        assert score >= threshold, f"PSNR {score:.1f}dB < 要求的 {threshold}dB"

    check.__name__ = f"psnr>={threshold}"
    return check


def exact():
    def check(src: Image.Image, out: Image.Image) -> None:
        assert pixels_identical(src, out), "像素不完全相同（本应无损）"

    check.__name__ = "exact"
    return check


def size_equals():
    def check(src: Image.Image, out: Image.Image) -> None:
        assert src.size == out.size, f"尺寸变化：{src.size} → {out.size}"

    check.__name__ = "size_equals"
    return check
