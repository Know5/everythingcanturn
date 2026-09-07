"""位图插件共用的工具：EXIF 方向、透明合成、缩放、模式适配。

这些是「降级策略」的具体实现。每个函数在做出让步时都往 policy 里记一条告警，
保证用户能看到发生了什么。
"""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageOps, ImageSequence

from ..ir.raster import Frame, Raster
from ..policy import Policy, Strategy


def load_frames(img: Image.Image, source_type: str | None = None) -> Raster:
    """把 Pillow 图像（可能多帧）读成 Raster。

    两个容易漏的点：
    1. EXIF 方向必须在这里应用。手机照片普遍带方向标记，不处理转出来就是躺着的。
    2. ImageSequence 迭代得 copy() 每帧，否则拿到的都是同一个共享缓冲。
    """
    icc = img.info.get("icc_profile")
    exif = img.info.get("exif")
    loop = int(img.info.get("loop", 0) or 0)

    frames: list[Frame] = []
    for page in ImageSequence.Iterator(img):
        # exif_transpose 处理方向；它返回新图或 None（无 EXIF 时旧版行为）
        fixed = ImageOps.exif_transpose(page) or page
        frames.append(
            Frame(fixed.copy(), duration_ms=int(page.info.get("duration", 0) or 0))
        )

    return Raster(
        frames=frames,
        icc_profile=icc if isinstance(icc, bytes) else None,
        exif=exif if isinstance(exif, bytes) else None,
        loop=loop,
        source_type=source_type,
    )


def flatten_alpha(raster: Raster, pol: Policy, target_label: str) -> Raster:
    """目标格式不支持透明时合成到背板色。

    默认白底而不是黑底：照片和文档场景白底几乎总是对的，而黑底会让
    透明 PNG 图标转 JPEG 后变成一团黑，是最常见的踩坑。
    """
    if not raster.has_alpha:
        return raster

    bg = pol.alpha_background
    pol.note(
        "透明通道",
        Strategy.DROP,
        f"{target_label} 不支持透明，已合成到 RGB{bg} 背景",
    )

    def compose(im: Image.Image) -> Image.Image:
        if im.mode == "P":
            im = im.convert("RGBA")
        if im.mode not in ("RGBA", "LA"):
            return im.convert("RGB")
        base = Image.new("RGB", im.size, bg)
        alpha = im.convert("RGBA").getchannel("A")
        base.paste(im.convert("RGB"), mask=alpha)
        return base

    return raster.map_frames(compose)


def apply_resize(raster: Raster, opts: dict[str, Any], pol: Policy) -> Raster:
    """按 width/height/max_edge/scale 缩放。等比例，不拉伸变形。"""
    w0, h0 = raster.size
    width = opts.get("width")
    height = opts.get("height")
    max_edge = opts.get("max_edge")
    scale = opts.get("scale")

    target: tuple[int, int] | None = None
    if width and height:
        target = (int(width), int(height))
    elif width:
        target = (int(width), max(1, round(h0 * int(width) / w0)))
    elif height:
        target = (max(1, round(w0 * int(height) / h0)), int(height))
    elif scale:
        target = (max(1, round(w0 * float(scale))), max(1, round(h0 * float(scale))))
    elif max_edge and max(w0, h0) > int(max_edge):
        ratio = int(max_edge) / max(w0, h0)
        target = (max(1, round(w0 * ratio)), max(1, round(h0 * ratio)))

    if target is None or target == (w0, h0):
        return raster

    if target[0] > w0 or target[1] > h0:
        pol.note(
            "放大",
            Strategy.APPROXIMATE,
            f"{w0}×{h0} → {target[0]}×{target[1]}，放大无法增加细节",
        )

    # LANCZOS 缩小质量最好；动图逐帧缩放
    return raster.map_frames(lambda im: im.resize(target, Image.Resampling.LANCZOS))


def handle_animation(
    raster: Raster, pol: Policy, target_label: str, supports_animation: bool
) -> Raster:
    """动图转不支持动画的格式。

    EMULATE 保留全部帧（writer 会导出成图序列多文件），DROP 只留首帧。
    默认 EMULATE——用户转动图时通常不想只得到第一帧。
    """
    if supports_animation or not raster.is_animated:
        return raster

    n = len(raster.frames)
    if pol.animation is Strategy.EMULATE:
        pol.note(
            "动画",
            Strategy.EMULATE,
            f"{target_label} 不支持动画，{n} 帧将导出为多个文件",
        )
        return raster

    pol.note("动画", Strategy.DROP, f"{target_label} 不支持动画，已丢弃后 {n - 1} 帧")
    return Raster(
        frames=[raster.frames[0]],
        icc_profile=raster.icc_profile,
        exif=raster.exif,
        source_type=raster.source_type,
        extra=dict(raster.extra),
    )


def coerce_mode(im: Image.Image, allowed: tuple[str, ...], pol: Policy) -> Image.Image:
    """把图像模式收敛到目标格式支持的集合。

    高位深（I;16）降到 8 位、CMYK 转 RGB 都会记告警——这些是真实的信息损失。
    """
    if im.mode in allowed:
        return im

    if im.mode in ("I;16", "I;16B", "I", "F"):
        pol.note("位深", Strategy.APPROXIMATE, f"{im.mode} → 8 位，动态范围压缩")
        im = im.point(lambda v: v * (1 / 256)).convert("L")
        if "L" in allowed:
            return im

    if im.mode == "CMYK" and "CMYK" not in allowed:
        pol.note("色彩空间", Strategy.APPROXIMATE, "CMYK → RGB，印刷色域会偏移")

    for candidate in ("RGBA", "RGB", "L", "P"):
        if candidate in allowed:
            return im.convert(candidate)
    return im.convert(allowed[0])


def metadata_kwargs(raster: Raster, pol: Policy, *, icc: bool, exif: bool) -> dict:
    """构造 Pillow save() 的元数据参数。

    透传而不是丢弃是默认行为：ICC 丢了颜色会偏，EXIF 丢了拍摄信息就没了。
    """
    out: dict[str, Any] = {}
    if icc and pol.keep_icc and raster.icc_profile:
        out["icc_profile"] = raster.icc_profile
    if exif and pol.keep_metadata and raster.exif:
        out["exif"] = raster.exif
    return out
