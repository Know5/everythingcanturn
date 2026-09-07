"""依赖可选包的位图格式：HEIC / AVIF / JPEG XL / PSD / RAW。

每个插件用 available() 报告依赖是否就绪。缺依赖时它不进图，但原因会记在注册表里，
让 doctor 能说清「装 pillow-heif 就能支持 HEIC」而不是笼统的「不支持」。
这个区分对用户很重要——iPhone 照片全是 HEIC。
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from ..ir.raster import Raster
from ..policy import Policy
from ..registry import register
from ..types import LossTier, MediaType, Resource, caps
from ._raster_util import (
    apply_resize,
    coerce_mode,
    flatten_alpha,
    load_frames,
    metadata_kwargs,
)

_GEOM = frozenset({"width", "height", "max_edge", "scale"})


@register
class HeifCodec:
    """HEIC/HEIF：iPhone 默认照片格式，转换需求最高频的一个。"""

    name = "pillow-heif"
    domain = "raster"
    reads = caps("image/heic", time_cost=2.0)
    writes = caps("image/heic", loss=LossTier.LOSSY, time_cost=3.0,
                  options=_GEOM | {"quality"})

    def available(self) -> tuple[bool, str]:
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
            return True, ""
        except ImportError:
            return False, "缺少 pillow-heif，装了才能读写 HEIC（iPhone 照片）"

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> Raster:
        with Image.open(io.BytesIO(resources[0].read_bytes())) as img:
            return load_frames(img, resources[0].media_type)

    def write(
        self, ir: Raster, target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        pol: Policy = options.get("_policy") or Policy()
        raster = apply_resize(ir, options, pol)
        im = coerce_mode(raster.primary.image, ("RGBA", "RGB", "L"), pol)
        buf = io.BytesIO()
        im.save(
            buf,
            format="HEIF",
            quality=int(options.get("quality", 90)),
            **metadata_kwargs(raster, pol, icc=True, exif=True),
        )
        return [Resource(media_type=target, data=buf.getvalue())]


@register
class AvifCodec:
    """AVIF：压缩率最好的常用格式，Pillow 11+ 可能内置，否则需要插件包。"""

    name = "avif"
    domain = "raster"
    reads = caps("image/avif", time_cost=2.0)
    writes = caps("image/avif", loss=LossTier.LOSSY, time_cost=6.0,
                  options=_GEOM | {"quality", "speed"})

    def available(self) -> tuple[bool, str]:
        # Pillow 11 起可能自带 AVIF 支持，取决于编译选项
        try:
            Image.init()
            if "AVIF" in Image.SAVE and "AVIF" in Image.OPEN:
                return True, ""
        except Exception:
            pass
        try:
            import pillow_avif  # noqa: F401

            return True, ""
        except ImportError:
            return False, "当前 Pillow 未编译 AVIF 支持，可装 pillow-avif-plugin"

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> Raster:
        with Image.open(io.BytesIO(resources[0].read_bytes())) as img:
            return load_frames(img, resources[0].media_type)

    def write(
        self, ir: Raster, target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        pol: Policy = options.get("_policy") or Policy()
        raster = apply_resize(ir, options, pol)
        im = coerce_mode(raster.primary.image, ("RGBA", "RGB", "L"), pol)
        buf = io.BytesIO()
        im.save(
            buf,
            format="AVIF",
            quality=int(options.get("quality", 75)),
            speed=int(options.get("speed", 6)),
            **metadata_kwargs(raster, pol, icc=True, exif=True),
        )
        return [Resource(media_type=target, data=buf.getvalue())]


@register
class PsdCodec:
    """PSD 只读：读合成后的效果图，不解析图层树。

    这是有意的取舍——图层语义映射不到 Raster IR，硬塞会污染模型。真要编辑图层
    应该用 Photoshop，转换工具只需给出合成结果。
    """

    name = "psd-tools"
    domain = "raster"
    reads = caps("image/vnd.adobe.photoshop", loss=LossTier.STRUCTURAL, time_cost=4.0)
    writes = ()

    def available(self) -> tuple[bool, str]:
        try:
            import psd_tools  # noqa: F401

            return True, ""
        except ImportError:
            return False, "缺少 psd-tools，装了才能读 PSD"

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> Raster:
        import psd_tools

        pol: Policy = options.get("_policy") or Policy()
        psd = psd_tools.PSDImage.open(io.BytesIO(resources[0].read_bytes()))
        composite = psd.composite()
        if composite is None:
            raise ValueError("PSD 无法合成预览（可能是空文档）")

        from ..policy import Strategy

        if len(psd) > 1:
            pol.note(
                "图层",
                Strategy.DROP,
                f"{len(psd)} 个图层已合成为单层（转换不保留图层结构）",
            )
        from ..ir.raster import Frame

        return Raster(
            frames=[Frame(composite)],
            source_type=resources[0].media_type,
        )


@register
class RawCodec:
    """相机 RAW 只读。RAW→可用图像本质是「显影」，参数选择影响很大。"""

    name = "rawpy"
    domain = "raster"
    reads = caps(
        "image/x-canon-cr2",
        "image/x-canon-cr3",
        "image/x-nikon-nef",
        "image/x-sony-arw",
        "image/x-adobe-dng",
        "image/x-fuji-raf",
        "image/x-panasonic-rw2",
        loss=LossTier.LOSSY,
        time_cost=8.0,
    )
    writes = ()

    def available(self) -> tuple[bool, str]:
        try:
            import rawpy  # noqa: F401

            return True, ""
        except ImportError:
            return False, "缺少 rawpy，装了才能读相机 RAW（CR2/NEF/ARW/DNG 等）"

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> Raster:
        import numpy as np
        import rawpy

        from ..ir.raster import Frame
        from ..policy import Strategy

        pol: Policy = options.get("_policy") or Policy()
        pol.note(
            "RAW 显影",
            Strategy.APPROXIMATE,
            "使用相机白平衡和默认显影参数，专业调色请用 RAW 编辑器",
        )

        with rawpy.imread(io.BytesIO(resources[0].read_bytes())) as raw:
            rgb = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=False,
                output_bps=8,
            )
        return Raster(
            frames=[Frame(Image.fromarray(np.asarray(rgb)))],
            source_type=resources[0].media_type,
        )
