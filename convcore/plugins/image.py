"""Pillow 位图插件：PNG / JPEG / WebP / GIF / TIFF / BMP / ICO / PPM / TGA。

一个插件覆盖多个格式而不是一格式一插件，因为它们共用同一个底层库和同一套
模式适配逻辑。真正需要独立插件的是依赖不同库的格式（HEIC、PSD、RAW）。
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from ..ir.raster import Raster
from ..policy import Policy, Strategy
from ..registry import register
from ..types import LossTier, MediaType, Resource, caps
from ._raster_util import (
    apply_resize,
    coerce_mode,
    flatten_alpha,
    handle_animation,
    load_frames,
    metadata_kwargs,
)

# 每个格式的写入约束：Pillow 保存用的名字、支持的模式、是否支持透明/动画。
# 把这些做成数据而不是 if-else，加格式时只加一行。
_WRITE_SPEC: dict[MediaType, dict[str, Any]] = {
    "image/png": {
        "pil": "PNG",
        "modes": ("RGBA", "RGB", "L", "P", "LA", "I;16"),
        "alpha": True,
        "anim": False,
    },
    "image/jpeg": {
        "pil": "JPEG",
        "modes": ("RGB", "L", "CMYK"),
        "alpha": False,
        "anim": False,
    },
    "image/webp": {
        "pil": "WEBP",
        "modes": ("RGBA", "RGB", "L"),
        "alpha": True,
        "anim": True,
    },
    "image/gif": {
        "pil": "GIF",
        "modes": ("P", "L", "RGB", "RGBA"),
        "alpha": True,
        "anim": True,
    },
    "image/tiff": {
        "pil": "TIFF",
        "modes": ("RGBA", "RGB", "L", "CMYK", "I;16", "P"),
        "alpha": True,
        "anim": True,
    },
    "image/bmp": {"pil": "BMP", "modes": ("RGB", "L", "P"), "alpha": False, "anim": False},
    "image/vnd.microsoft.icon": {
        "pil": "ICO",
        "modes": ("RGBA", "RGB"),
        "alpha": True,
        "anim": False,
    },
    "image/x-portable-pixmap": {
        "pil": "PPM",
        "modes": ("RGB", "L"),
        "alpha": False,
        "anim": False,
    },
    "image/x-tga": {"pil": "TGA", "modes": ("RGBA", "RGB", "L"), "alpha": True, "anim": False},
}

# ICO 的标准尺寸集。Windows 会按需挑选，所以要多尺寸打包而不是只存一张。
_ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

_GEOM_OPTS = frozenset({"width", "height", "max_edge", "scale"})


@register
class PillowCodec:
    name = "pillow"
    domain = "raster"

    reads = (
        *caps(
            "image/png",
            "image/gif",
            "image/tiff",
            "image/bmp",
            "image/vnd.microsoft.icon",
            "image/x-portable-pixmap",
            "image/x-tga",
        ),
        # 从有损格式读入本身不再损失，解码是精确的；损失发生在写出时。
        *caps("image/jpeg", "image/webp"),
    )

    writes = (
        *caps("image/png", options=_GEOM_OPTS | {"optimize", "compress_level"}),
        *caps(
            "image/jpeg",
            loss=LossTier.LOSSY,
            options=_GEOM_OPTS | {"quality", "progressive", "subsampling"},
        ),
        *caps(
            "image/webp",
            loss=LossTier.LOSSY,
            options=_GEOM_OPTS | {"quality", "lossless", "method",
                                  "alpha_quality", "exact"},
        ),
        # GIF 只有 256 色，从真彩色转过去必然丢色彩
        *caps("image/gif", loss=LossTier.LOSSY, options=_GEOM_OPTS | {"dither"}),
        *caps("image/tiff", options=_GEOM_OPTS | {"compression"}),
        *caps("image/bmp", options=_GEOM_OPTS),
        *caps("image/vnd.microsoft.icon", options=_GEOM_OPTS | {"sizes"}),
        *caps("image/x-portable-pixmap", options=_GEOM_OPTS),
        *caps("image/x-tga", options=_GEOM_OPTS),
    )

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> Raster:
        rasters: list[Raster] = []
        for res in resources:
            with Image.open(io.BytesIO(res.read_bytes())) as img:
                rasters.append(load_frames(img, res.media_type))

        if len(rasters) == 1:
            return rasters[0]

        # 多进一出：多张图合并成多帧（用于合成 GIF/动图 WebP/多页 TIFF）
        merged = rasters[0]
        for extra in rasters[1:]:
            merged.frames.extend(extra.frames)
        return merged

    def write(
        self, ir: Raster, target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        spec = _WRITE_SPEC[target]
        pol: Policy = options.get("_policy") or Policy()
        label = spec["pil"]

        raster = apply_resize(ir, options, pol)
        if not spec["alpha"]:
            raster = flatten_alpha(raster, pol, label)
        raster = handle_animation(raster, pol, label, spec["anim"])

        if target == "image/vnd.microsoft.icon":
            return [self._write_ico(raster, options, pol)]

        save_kw = self._save_kwargs(target, spec, raster, options, pol)

        # 动图且目标支持动画 → 单文件多帧
        if raster.is_animated and spec["anim"]:
            return [self._write_animated(raster, target, spec, save_kw, pol)]

        # 多帧但目标不支持动画 → 一进多出，每帧一个文件
        gif_frames = (
            self._gif_quantize([f.image for f in raster.frames], pol)
            if target == "image/gif"
            else None
        )
        outputs: list[Resource] = []
        for i, frame in enumerate(raster.frames):
            im = (
                gif_frames[i]
                if gif_frames is not None
                else coerce_mode(frame.image, spec["modes"], pol)
            )
            buf = io.BytesIO()
            im.save(buf, format=spec["pil"], **save_kw)
            outputs.append(
                Resource(
                    media_type=target,
                    data=buf.getvalue(),
                    name=None if len(raster.frames) == 1 else f"frame-{i + 1:03d}",
                )
            )
        return outputs

    @staticmethod
    def _gif_quantize(frames: list[Any], pol: Policy) -> list[Any]:
        """GIF 帧统一到一张自适应 256 色调色板。

        Pillow 的 convert("P") 用 WEB 216 色调色板（上世纪 8 位显示器的色阶），
        照片渐变会 posterize；逐帧独立调色板又会让动图闪烁。首帧中值切分
        出调色板、其余帧映射过去：颜色自适应且帧间稳定。
        """
        dither = Image.Dither.FLOYDSTEINBERG if pol.dither else Image.Dither.NONE
        palette = frames[0].convert("RGB").quantize(
            colors=256, method=Image.Quantize.MEDIANCUT
        )
        return [
            im
            if im.mode == "P"
            else im.convert("RGB").quantize(palette=palette, dither=dither)
            for im in frames
        ]


    def _save_kwargs(
        self,
        target: MediaType,
        spec: dict[str, Any],
        raster: Raster,
        options: dict[str, Any],
        pol: Policy,
    ) -> dict[str, Any]:
        # JPEG 不支持 ICC 之外的东西，TIFF/BMP 不吃 exif 参数，分开处理
        kw = metadata_kwargs(
            raster,
            pol,
            icc=target in ("image/png", "image/jpeg", "image/webp", "image/tiff"),
            exif=target in ("image/jpeg", "image/webp", "image/png", "image/tiff"),
        )

        if target == "image/jpeg":
            kw["quality"] = int(options.get("quality", 90))
            kw["progressive"] = bool(options.get("progressive", False))
            # optimize 是霍夫曼表二次扫描：纯体积收益（约 5-8%），编码时间约翻倍
            # （12MP 实测 16ms→31ms）。代码默认保持快档；original/archive/web
            # 预设会显式开启。
            kw["optimize"] = bool(options.get("optimize", False))
            # 4:2:0（Pillow 默认）砍掉一半色彩分辨率，高质量档下肉眼可见。
            # 默认 4:2:2；original/archive 预设用 0（4:4:4），web 可显式 2。
            kw["subsampling"] = int(options.get("subsampling", 1))

        elif target == "image/webp":
            lossless = options.get("lossless")
            if lossless == "auto":
                # 归档语义：源本身无损才无损——有损转无损只会更大更慢
                lossless = (raster.source_type or "") in (
                    "image/png", "image/bmp", "image/tiff", "image/gif",
                    "image/x-portable-pixmap", "image/x-tga",
                )
            if lossless:
                kw["lossless"] = True
            else:
                kw["quality"] = int(options.get("quality", 85))
            # method 是搜索深度。12MP 实测 method=4 676ms vs method=2 291ms，
            # 输出同为 20KB——默认快档；original/archive 预设显式 method=6。
            kw["method"] = int(options.get("method", 2))
            if "alpha_quality" in options:
                kw["alpha_quality"] = int(options["alpha_quality"])
            if options.get("exact"):
                kw["exact"] = True

        elif target == "image/png":
            # 压缩级别只换体积不换画质，但 level 9 + optimize 在 A4 页面上把编码
            # 拖慢 2.5 倍（20 页 491ms vs 200ms）。代码默认快档，预设里拉满。
            kw["optimize"] = bool(options.get("optimize", False))
            if "compress_level" in options:
                kw["compress_level"] = int(options["compress_level"])

        elif target == "image/tiff":
            kw["compression"] = options.get("compression", "tiff_deflate")

        elif target == "image/gif":
            # GIF 限 256 色，量化质量取决于是否抖动
            kw["optimize"] = True

        return kw

    def _write_animated(
        self,
        raster: Raster,
        target: MediaType,
        spec: dict[str, Any],
        save_kw: dict[str, Any],
        pol: Policy,
    ) -> Resource:
        """多帧写成单个动图文件。"""
        frames = [coerce_mode(f.image, spec["modes"], pol) for f in raster.frames]
        durations = [max(f.duration_ms, 20) for f in raster.frames]

        if target == "image/gif":
            # GIF 需要调色板化，统一走全局自适应调色板（见 _gif_quantize）
            if any(im.mode != "P" for im in frames):
                pol.note(
                    "色彩深度",
                    Strategy.APPROXIMATE,
                    "GIF 限 256 色，真彩色已量化" + ("（抖动）" if pol.dither else ""),
                )
            frames = self._gif_quantize(frames, pol)

        buf = io.BytesIO()
        extra: dict[str, Any] = {}
        if target != "image/tiff":
            # TIFF 的「帧」是页，没有延时/循环语义，传了只会被忽略
            extra = {"duration": durations, "loop": raster.loop}
        frames[0].save(
            buf,
            format=spec["pil"],
            save_all=True,
            append_images=frames[1:],
            **extra,
            **save_kw,
        )
        return Resource(media_type=target, data=buf.getvalue())

    def _write_ico(
        self, raster: Raster, options: dict[str, Any], pol: Policy
    ) -> Resource:
        """ICO 要多尺寸打包，Windows 会按显示场景挑选合适的那张。

        只存单尺寸的 ICO 在任务栏或大图标视图下会被拉伸得很糟——这是同类工具
        常见的偷懒做法。
        """
        im = raster.primary.image
        if im.mode not in ("RGBA", "RGB"):
            im = im.convert("RGBA")

        w, h = im.size
        requested = options.get("sizes")
        if requested:
            sizes = [(int(s), int(s)) for s in requested]
        else:
            # 不超过原图尺寸，避免放大产生虚假细节
            sizes = [s for s in _ICO_SIZES if s[0] <= max(w, h)] or [(16, 16)]

        if max(w, h) < 256:
            pol.note(
                "图标尺寸",
                Strategy.APPROXIMATE,
                f"源图 {w}×{h}，未生成 256×256 层（放大无意义）",
            )

        # ICO 内部要求正方形；非正方形先补透明边而不是拉伸变形
        if w != h:
            side = max(w, h)
            square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            square.paste(im.convert("RGBA"), ((side - w) // 2, (side - h) // 2))
            im = square
            pol.note(
                "宽高比",
                Strategy.APPROXIMATE,
                f"ICO 需正方形，{w}×{h} 已补透明边至 {side}×{side}",
            )

        buf = io.BytesIO()
        im.save(buf, format="ICO", sizes=sizes)
        return Resource(media_type="image/vnd.microsoft.icon", data=buf.getvalue())
