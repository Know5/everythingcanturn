"""矢量领域：SVG 读入，经桥接栅格化成位图。

方向性在这里体现得最清楚：矢量→位图是栅格化（确定性的，只是丢了可缩放性），
位图→矢量是推断（需要猜测轮廓，结果可能完全不对）。所以图里只有前者的桥接边，
后者要等专门的描摹插件并明确标成 INFERRED。

渲染后端优先级：resvg_py（官方 Python 绑定，wheel 随包分发，零外部依赖）
> resvg exe（PATH 或私有引擎目录）> cairosvg（Windows 上常缺 cairo DLL）。
resvg 官方 release 不发 Windows 二进制，exe 路径仅服务已手工装好的用户。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..policy import Policy, Strategy
from ..registry import register
from ..types import LossTier, MediaType, Resource, caps


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


@register
class SvgCodec:
    name = "svg"
    domain = "vector"
    reads = caps("image/svg+xml")
    writes = ()
    """SVG 只读。写 SVG 意味着从位图描摹，属于 INFERRED，留给后续专门插件。"""

    def available(self) -> tuple[bool, str]:
        if _has_resvg_py() or _find_resvg() or _has_cairosvg():
            return True, ""
        return False, "缺少 SVG 渲染器（resvg_py）。pip install resvg-py 即可"

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> VectorDoc:
        res = resources[0]
        return VectorDoc(data=res.read_bytes(), source_type=res.media_type)


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
    import tempfile

    tmp = Path(opts.get("_tmp") or tempfile.mkdtemp(prefix="convcore-svg-"))
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
