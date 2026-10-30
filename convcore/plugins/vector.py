"""矢量领域：SVG 读入，经桥接栅格化成位图。

方向性在这里体现得最清楚：矢量→位图是栅格化（确定性的，只是丢了可缩放性），
位图→矢量是描摹（推断：轮廓是猜的，标 INFERRED）。描摹只面向图标/线稿——
照片在入口被启发式拦住，而不是产出一坨色块海报。

渲染后端优先级：resvg_py（官方 Python 绑定，wheel 随包分发，零外部依赖）
> resvg exe（PATH 或私有引擎目录）> cairosvg（Windows 上常缺 cairo DLL）。
resvg 官方 release 不发 Windows 二进制，exe 路径仅服务已手工装好的用户。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from ..policy import Policy, Strategy
from ..registry import register
from ..types import ConversionError, MediaType, Resource, caps
from ._scratch import workdir


@dataclass(slots=True)
class VectorDoc:
    """矢量领域 IR。

    有意保持不透明：SVG 的完整对象模型极其复杂，为了转换而解析它得不偿失。
    真正需要的操作（栅格化、改尺寸）交给成熟渲染器，IR 只携带源字节和视口信息。
    """

    data: bytes
    source_type: MediaType
    width: float | None = None
    height: float | None = None

def _has_resvg_py() -> bool:
    try:
        import resvg_py  # noqa: F401

        return True
    except Exception:
        return False


def _find_resvg() -> str | None:
    """resvg exe：PATH + 私有引擎目录都查。"""
    from ..engines.probe import engines_dir

    candidates = [shutil.which("resvg")]
    exe = engines_dir() / "resvg" / ("resvg.exe" if os.name == "nt" else "resvg")
    if exe.is_file():
        candidates.append(str(exe))
    return next((c for c in candidates if c), None)


def _has_cairosvg() -> bool:
    try:
        import cairosvg  # noqa: F401

        return True
    except Exception:
        # cairosvg 在 Windows 上常因缺 cairo DLL 而导入失败（OSError 而非 ImportError）
        return False


def has_svg_renderer() -> bool:
    """图有没有 vector → raster 桥，由这里门控。"""
    return _has_resvg_py() or _find_resvg() is not None or _has_cairosvg()


def has_vtracer() -> bool:
    """raster → vector 桥的依赖门控。"""
    try:
        import vtracer  # noqa: F401

        return True
    except Exception:
        return False


@register
class SvgCodec:
    name = "svg"
    domain = "vector"
    reads = caps("image/svg+xml")
    writes = caps("image/svg+xml")
    """读写都只是 SVG 字节进出。栅格化要渲染器、描摹要 vtracer，各自门控桥，不绑这里。"""

    def available(self) -> tuple[bool, str]:
        return True, ""

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> VectorDoc:
        res = resources[0]
        return VectorDoc(data=res.read_bytes(), source_type=res.media_type)

    def write(
        self, ir: VectorDoc, target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        if not isinstance(ir, VectorDoc):
            raise ConversionError(f"SVG 写出需要 VectorDoc，收到 {type(ir).__name__}")
        return [Resource(media_type=target, data=ir.data)]


def rasterize(doc: VectorDoc, opts: dict[str, Any], pol: Policy) -> Any:
    """矢量 → 位图桥接。pipeline._bridge 调用这里。

    尺寸决策：用户给了 width/height 就用；否则按 SVG 自身尺寸；都没有则用
    1024 作为默认。矢量没有「原始分辨率」，所以必须有个决定，且要告诉用户。
    """
    import io

    from PIL import Image

    from ..ir.raster import Frame, Raster

    width = opts.get("width")
    height = opts.get("height")
    if not width and not height:
        edge = opts.get("max_edge") or 1024
        width = int(edge)
        pol.note(
            "栅格化尺寸",
            Strategy.APPROXIMATE,
            f"未指定尺寸，按宽 {width}px 渲染（矢量无固有分辨率，"
            f"可用 --width 指定）",
        )

    pol.note(
        "可缩放性",
        Strategy.DROP,
        "矢量转位图后不可无损缩放",
    )

    png = _render_png(doc, width, height, opts)
    with Image.open(io.BytesIO(png)) as im:
        return Raster(frames=[Frame(im.convert("RGBA").copy())], source_type=doc.source_type)


def _render_png(
    doc: VectorDoc, width: int | None, height: int | None, opts: dict[str, Any]
) -> bytes:
    """调渲染器出 PNG 字节。优先级与 available() 一致。"""
    if _has_resvg_py():
        import resvg_py

        return resvg_py.svg_to_bytes(
            doc.data.decode("utf-8", "replace"),
            width=width,
            height=height,
        )
    if (resvg := _find_resvg()) is not None:
        return _render_resvg(resvg, doc, width, height, opts)
    if _has_cairosvg():
        import cairosvg

        return cairosvg.svg2png(
            bytestring=doc.data,
            output_width=width,
            output_height=height,
        )
    raise RuntimeError("没有可用的 SVG 渲染器")


def _render_resvg(
    exe: str, doc: VectorDoc, width: int | None, height: int | None, opts: dict[str, Any]
) -> bytes:
    """resvg 只吃文件路径，需要临时落盘。"""

    tmp = workdir(opts)
    src = tmp / "input.svg"
    dst = tmp / "output.png"
    src.write_bytes(doc.data)

    # 参数用列表传递，绝不拼字符串——文件名里的空格和特殊字符会出事
    cmd = [exe, str(src), str(dst)]
    if width:
        cmd += ["--width", str(int(width))]
    if height:
        cmd += ["--height", str(int(height))]

    proc = subprocess.run(
        cmd, capture_output=True, timeout=120, check=False, shell=False
    )
    if proc.returncode != 0 or not dst.exists():
        err = proc.stderr.decode("utf-8", "replace").strip() or "无错误输出"
        raise RuntimeError(f"resvg 渲染失败：{err}")
    return dst.read_bytes()


# ── raster → vector 描摹（INFERRED）──
#
# 面向图标 / Logo / 线稿。照片描出来只会是色块海报，所以不像图标的输入
# 默认拒绝（--force 绕过），而不是悄悄产出一坨没法用的色块。

TRACE_MAX_EDGE = 2048
"""描摹前的最长边上限。这是速度约束（vtracer 随像素迅速变慢），不是质量旋钮。"""
TRACE_THUMB_EDGE = 128
"""启发式判定用缩略图的边长。"""
TRACE_UNIQUE_PASS = 48
"""唯一色数不超过它即判为可描摹。抗锯齿 Logo 常见 20–80，取下限作绿灯。"""
TRACE_PALETTE_MSE_MAX = 80.0
"""16 色量化重建的均方误差上限。平坦 Logo 通常 <40，照片通常 >200。"""

_VTRACER_PARAMS: dict[str, Any] = {
    "hierarchical": "stacked",  # 重叠色块，适合 Logo
    "mode": "spline",           # 贝塞尔曲线，真矢量而不是折线
    "filter_speckle": 4,        # 去噪点，又不吃细线
    "color_precision": 6,       # 够用，避免颜色爆炸
    "layer_difference": 16,
    "corner_threshold": 60,
    "length_threshold": 4.0,
    "max_iterations": 10,
    "splice_threshold": 45,
    "path_precision": 3,        # 体积与平滑的折中
}
"""面向 Logo 的 vtracer 参数。v1 不做成 CLI 选项。"""


def _flatten_white(im: Any) -> Any:
    """透明合成到白底。只用于判定与统计，不改真正被描摹的图。"""
    from PIL import Image

    if im.mode in ("RGBA", "LA", "PA") or (
        im.mode == "P" and "transparency" in im.info
    ):
        rgba = im.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.split()[-1])
        return base
    return im.convert("RGB")


def _trace_thumb(im: Any) -> Any:
    """白底展平、最长边不超过 TRACE_THUMB_EDGE 的 RGB 缩略图。"""
    from PIL import Image

    flat = _flatten_white(im)
    if max(flat.size) > TRACE_THUMB_EDGE:
        ratio = TRACE_THUMB_EDGE / max(flat.size)
        flat = flat.resize(
            (max(1, round(flat.width * ratio)), max(1, round(flat.height * ratio))),
            Image.Resampling.LANCZOS,
        )
    return flat


def _palette_mse(thumb: Any) -> float:
    """16 色量化重建的均方误差。照片量化后失真大，Logo 小。"""
    from PIL import Image, ImageChops

    pal = thumb.quantize(colors=16, method=Image.Quantize.MEDIANCUT).convert("RGB")
    hist = ImageChops.difference(thumb, pal).histogram()
    # histogram() 是三通道拼接的（768 项）：第 v 项的差值 = v % 256，
    # 直接拿 v 平方会被高通道下标放大数倍
    return sum((v % 256) ** 2 * c for v, c in enumerate(hist)) / (
        thumb.width * thumb.height * 3
    )


def _is_binary_ink(thumb: Any) -> bool:
    """图像本身是不是二值的（按 128 阈值灰度化不丢信息）。

    vtracer 的 binary 模式只认「黑前景、白背景」：彩色前景（如红圆）会被
    阈值吃掉、产出空 SVG。所以只在图像真是二值时才用它。
    """
    from PIL import ImageChops

    gray = thumb.convert("L")
    bw = gray.point(lambda v: 255 if v > 128 else 0)
    return ImageChops.difference(gray, bw).getbbox() is None


def trace_raster(raster: Any, opts: dict[str, Any], pol: Policy) -> VectorDoc:
    """位图 → 矢量桥接：vtracer 描摹。pipeline._bridge 调用这里。

    轮廓是猜出来的，所以整条边标 INFERRED。输入必须像图标/线稿；照片描摹
    只会得到色块海报，默认拒绝，opts["force"] 可强制。
    """
    from PIL import Image

    from ..ir.raster import Frame, Raster
    from ._raster_util import apply_resize

    # 选帧：ICO 常把 16px 放最前，取面积最大的一帧；动图/多页只用第一帧并告警
    if raster.source_type == "image/x-icon" and len(raster.frames) > 1:
        frame = max(raster.frames, key=lambda f: f.size[0] * f.size[1])
    elif raster.is_animated:
        frame = raster.primary
        pol.note("动画", Strategy.EMULATE, "描摹只使用第一帧，时间轴不保留")
    elif len(raster.frames) > 1:
        frame = raster.primary
        pol.note("多页", Strategy.EMULATE, "只描摹第一页，其余页不保留")
    else:
        frame = raster.primary

    # 几何：先尊重用户的缩放参数，再兜速度上限
    single = Raster(
        frames=[Frame(frame.image, frame.duration_ms)],
        source_type=raster.source_type,
    )
    im = apply_resize(single, opts, pol).primary.image
    if max(im.size) > TRACE_MAX_EDGE:
        ratio = TRACE_MAX_EDGE / max(im.size)
        im = im.resize(
            (max(1, round(im.width * ratio)), max(1, round(im.height * ratio))),
            Image.Resampling.LANCZOS,
        )
        pol.note(
            "描摹尺寸",
            Strategy.APPROXIMATE,
            f"描摹前缩到 {TRACE_MAX_EDGE}px 最长边（速度上限；可用 --max-edge 指定更小）",
        )

    w, h = im.size
    if w == 0 or h == 0:
        raise ConversionError("图像是空的或全透明，无法描摹")
    if im.mode in ("RGBA", "LA", "PA") or (
        im.mode == "P" and "transparency" in im.info
    ):
        if im.convert("RGBA").getchannel("A").getextrema()[1] == 0:
            raise ConversionError("图像是空的或全透明，无法描摹")

    # 启发式：唯一色少（Logo）或 16 色量化误差低（抗锯齿线稿）即可描摹；
    # 照片两个条件都不满足
    thumb = _trace_thumb(im)
    unique = len(thumb.getcolors(maxcolors=1 << 16) or [])
    mse = _palette_mse(thumb)
    if unique > TRACE_UNIQUE_PASS and mse > TRACE_PALETTE_MSE_MAX:
        if not opts.get("force"):
            raise ConversionError(
                f"图像不像可描摹的图标（唯一色 {unique}、16 色量化误差 {mse:.0f}）——"
                "描摹照片只会得到色块海报。确实要试就加 --force"
            )
        pol.note(
            "强制描摹",
            Strategy.APPROXIMATE,
            "已强制描摹，结果可能是色块海报而非可用矢量",
        )

    params = dict(
        _VTRACER_PARAMS,
        colormode="binary" if _is_binary_ink(thumb) else "color",
    )

    import vtracer

    tmp = workdir(opts)
    src = tmp / "trace-input.png"
    dst = tmp / "trace-output.svg"
    im.save(src, format="PNG")
    try:
        vtracer.convert_image_to_svg_py(str(src), str(dst), **params)
    except Exception as exc:
        raise ConversionError(f"vtracer 描摹失败：{exc}") from exc
    if not dst.exists():
        raise ConversionError("vtracer 没有产出 SVG 文件")
    svg = dst.read_bytes()
    if b"<svg" not in svg:
        raise ConversionError("vtracer 产物不是 SVG（缺少 <svg 根元素）")
    shapes = (b"<path", b"<rect", b"<circle", b"<ellipse", b"<polygon")
    if not any(tag in svg for tag in shapes):
        # 空轮廓的 SVG 没有任何用途（纯白画布、binary 误判的彩色图都会走到这）
        raise ConversionError("vtracer 没有描出任何轮廓（产物是空 SVG）")

    return VectorDoc(
        data=svg, source_type=raster.source_type, width=float(w), height=float(h)
    )


def traceable_raster(path: Any) -> bool | None:
    """描摹可行性预检，refine_targets 用：图标/线稿 True，照片 False。

    与 trace_raster 的启发式同一套判定，但只看主帧缩略图，不开引擎。
    打不开的文件返回 None（不判死刑，交给执行期报错）。
    """
    from PIL import Image

    try:
        with Image.open(path) as im:
            try:
                im.draft("RGB", (TRACE_THUMB_EDGE * 2, TRACE_THUMB_EDGE * 2))
            except Exception:
                pass  # draft 只对 JPEG 有效，其它格式照常解码
            if getattr(im, "is_animated", False):
                im.seek(0)
            thumb = _trace_thumb(im)
            unique = len(thumb.getcolors(maxcolors=1 << 16) or [])
            mse = _palette_mse(thumb)
    except Exception:
        return None
    return unique <= TRACE_UNIQUE_PASS or mse <= TRACE_PALETTE_MSE_MAX


def vector_to_markup(doc: VectorDoc, opts: dict[str, Any], pol: Policy) -> Any:
    """vector → markup 桥接：将 SVG 嵌入 HTML 文档。

    SVG 本身就是 markup 格式，可以直接内嵌到 HTML 中。这是零损失的转换，
    保留了矢量的可缩放性——比栅格化成位图要好。
    """
    from ..ir.markup import RAW, Block, DocTree, Span

    # 解码 SVG 字节为文本
    svg_text = doc.data.decode("utf-8", "replace")

    # 将 SVG 作为 RAW 块嵌入 DocTree
    blocks = [
        Block(
            kind=RAW,
            spans=[Span(svg_text)],
            attrs={"format": "html", "tag": "svg"},
        )
    ]

    title = opts.get("title", "SVG 图形")

    pol.note(
        "矢量保留",
        Strategy.EMULATE,
        "SVG 直接嵌入 HTML，保留可缩放性",
    )

    return DocTree(blocks=blocks, title=title, source_type=doc.source_type)
