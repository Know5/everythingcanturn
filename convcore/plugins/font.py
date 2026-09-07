"""字体格式：TTF / OTF / WOFF / WOFF2 互转。

用 fontTools，它已随 pdf2docx 装了，所以这一域零新依赖。

字体转换的本质是**换容器不换字形数据**：WOFF 就是 TTF/OTF 加了一层压缩壳，
所以 TTF↔WOFF 是无损的。真正有损的只有 TTF↔OTF——两者的字形描述方式不同
（TrueType 用二次贝塞尔，OpenType/CFF 用三次），换过去必须重新拟合曲线。
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from ..policy import Policy, Strategy
from ..registry import register
from ..types import Capability, ConversionError, LossTier, MediaType, Resource

TTF = "font/ttf"
OTF = "font/otf"
WOFF = "font/woff"
WOFF2 = "font/woff2"

_OPTS = frozenset({"subset_text", "keep_hinting"})


@dataclass(slots=True)
class FontFile:
    """字体域 IR。

    持有 fontTools 的 TTFont 对象而非自建模型：字体的内部结构（字形表、
    字距调整、OpenType 特性）极其复杂，为转换而重新建模毫无收益。
    """

    font: Any
    source_type: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_cff(self) -> bool:
        """CFF 轮廓（OpenType）还是 glyf 轮廓（TrueType）。

        这个区分决定转换是否有损：同类互转换壳即可，跨类要重拟合曲线。
        """
        return "CFF " in self.font or "CFF2" in self.font

    @property
    def glyph_count(self) -> int:
        try:
            return len(self.font.getGlyphOrder())
        except Exception:
            return 0

    @property
    def family(self) -> str | None:
        try:
            for rec in self.font["name"].names:
                if rec.nameID == 1:
                    return rec.toUnicode()
        except Exception:
            pass
        return None

    def describe(self) -> str:
        bits = []
        if f := self.family:
            bits.append(f)
        bits.append("CFF/OpenType 轮廓" if self.is_cff else "TrueType 轮廓")
        bits.append(f"{self.glyph_count} 个字形")
        return " · ".join(bits)


@register
class FontCodec:
    name = "fonttools"
    domain = "font"

    reads = (
        Capability(TTF, LossTier.LOSSLESS, 2.0, _OPTS),
        Capability(OTF, LossTier.LOSSLESS, 2.0, _OPTS),
        Capability(WOFF, LossTier.LOSSLESS, 2.5, _OPTS),
        Capability(WOFF2, LossTier.LOSSLESS, 3.0, _OPTS),
    )
    writes = (
        Capability(TTF, LossTier.LOSSLESS, 2.0, _OPTS),
        Capability(OTF, LossTier.LOSSLESS, 2.0, _OPTS),
        Capability(WOFF, LossTier.LOSSLESS, 3.0, _OPTS),
        Capability(WOFF2, LossTier.LOSSLESS, 4.0, _OPTS),
    )

    def available(self) -> tuple[bool, str]:
        try:
            from fontTools.ttLib import TTFont  # noqa: F401

            return True, ""
        except ImportError:
            return False, "缺少 fonttools：uv pip install fonttools"

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> FontFile:
        from fontTools.ttLib import TTFont, TTLibError

        res = resources[0]
        data = res.read_bytes()

        try:
            font = TTFont(io.BytesIO(data), fontNumber=0, lazy=False)
        except TTLibError as exc:
            raise ConversionError(f"字体文件无法解析：{exc}") from exc
        except Exception as exc:
            # WOFF2 缺 brotli 时报的是别的异常类型
            if "brotli" in str(exc).lower():
                raise ConversionError(
                    "读 WOFF2 需要 brotli：uv pip install brotli"
                ) from exc
            raise ConversionError(f"字体文件无法解析：{exc}") from exc

        return FontFile(font=font, source_type=res.media_type)

    def write(
        self, ir: FontFile, target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        pol: Policy = options.get("_policy") or Policy()
        font = ir.font

        if subset := options.get("subset_text"):
            font = self._subset(font, str(subset), pol)

        self._warn_outline_change(ir, target, pol)

        # flavor 决定容器：None 是裸 TTF/OTF，woff/woff2 是压缩壳
        flavor = {WOFF: "woff", WOFF2: "woff2"}.get(target)
        original = font.flavor
        font.flavor = flavor

        buf = io.BytesIO()
        try:
            font.save(buf)
        except Exception as exc:
            if "brotli" in str(exc).lower():
                raise ConversionError(
                    "写 WOFF2 需要 brotli：uv pip install brotli"
                ) from exc
            raise ConversionError(f"字体写出失败：{exc}") from exc
        finally:
            font.flavor = original

        return [Resource(media_type=target, data=buf.getvalue())]

    def _warn_outline_change(self, ir: FontFile, target: MediaType, pol: Policy) -> None:
        """跨轮廓类型转换要告警。

        TTF 用二次贝塞尔，OTF/CFF 用三次。fontTools 的 save 不会自动转换轮廓
        类型——它只换容器。所以 OTF 存成 .ttf 得到的是「扩展名是 ttf 但内含
        CFF 表」的文件，多数系统能用但不是真正的 TrueType。这个必须说清楚。
        """
        wants_glyf = target in (TTF,)
        wants_cff = target in (OTF,)

        if ir.is_cff and wants_glyf:
            pol.note(
                "轮廓格式",
                Strategy.APPROXIMATE,
                "源字体是 CFF/PostScript 轮廓，输出容器为 TTF 但轮廓数据未转换为 "
                "TrueType 二次曲线（多数系统可正常使用，但严格意义上不是 TrueType）",
            )
        elif not ir.is_cff and wants_cff:
            pol.note(
                "轮廓格式",
                Strategy.APPROXIMATE,
                "源字体是 TrueType 轮廓，输出容器为 OTF 但轮廓仍是 glyf 表",
            )

    def _subset(self, font, text: str, pol: Policy):
        """按字符集裁剪字体。

        中文字体动辄十几 MB，网页只用到几十个字时裁剪能省 99%。这是字体转换
        最实用的功能，尤其对中文。
        """
        from fontTools import subset

        chars = sorted(set(text))
        before = len(font.getGlyphOrder())

        opts = subset.Options()
        opts.layout_features = ["*"]      # 保留所有 OpenType 特性
        opts.name_IDs = ["*"]
        opts.notdef_outline = True
        opts.recalc_bounds = True

        subsetter = subset.Subsetter(options=opts)
        subsetter.populate(unicodes=[ord(c) for c in chars])
        try:
            subsetter.subset(font)
        except Exception as exc:
            raise ConversionError(f"字体裁剪失败：{exc}") from exc

        after = len(font.getGlyphOrder())
        pol.note(
            "字体裁剪",
            Strategy.DROP,
            f"按 {len(chars)} 个字符裁剪，字形数 {before} → {after}"
            f"（未包含的字符将无法显示）",
        )
        return font
