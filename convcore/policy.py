"""降级策略与预设。

「降级」指 writer 遇到目标格式表达不了的特性时怎么办。三种处理方式：
  DROP        丢弃（JPEG 不支持透明 → 合成到背景色）
  APPROXIMATE 近似（16 位深 → 8 位深，抖动处理）
  EMULATE     模拟（多帧动图 → 只留首帧，或导出为图序列）

关键是**显式**：每次降级都记进 warnings，让用户知道发生了什么。现有工具的通病
是静默降级，用户转完才发现透明背景变黑了。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .types import MediaType


class Strategy(Enum):
    """降级策略：目标格式表达不了的特性如何处理。"""
    DROP = "drop"
    APPROXIMATE = "approximate"
    EMULATE = "emulate"
    FAIL = "fail"
    """拒绝转换。用于损失大到不该静默进行的场合。"""


@dataclass(slots=True)
class Degradation:
    """一次降级的记录。

    Attributes:
        feature: 被降级的特性名称（如"透明通道"、"位深"）
        strategy: 采用的降级策略
        detail: 降级的具体说明
    """

    feature: str
    strategy: Strategy
    detail: str

    def __str__(self) -> str:
        return f"{self.feature}：{self.detail}"


@dataclass(slots=True)
class Policy:
    """转换策略。可由预设或用户选项覆盖。"""

    alpha_background: tuple[int, int, int] = (255, 255, 255)
    """目标格式不支持透明时的背板色。默认白色而非黑色——照片和文档场景白底更常见。"""

    animation: Strategy = Strategy.EMULATE
    """动图转静态格式：EMULATE 导出全部帧为多文件，DROP 只留首帧。"""

    keep_metadata: bool = True
    """是否透传 EXIF/ICC。默认保留（无损原则），隐私场景可关。"""

    keep_icc: bool = True
    dither: bool = True
    """降位深或转调色板时是否抖动。开启视觉更好，关闭文件更小。"""

    actual_loss: Any = None
    """插件回填的实测损失等级，覆盖路径的静态声明。

    存在理由：静态声明只能说最坏情况。MKV→MP4 可能是 remux（COPY）也可能要
    转码（LOSSY），取决于具体文件的编码。图搜索必须按最坏情况排序（不能承诺
    做不到的保真），但结果该报真实值。
    """

    warnings: list[Degradation] = field(default_factory=list)

    def note(self, feature: str, strategy: Strategy, detail: str) -> None:
        """记录一次降级。

        Args:
            feature: 被降级的特性名称
            strategy: 采用的降级策略
            detail: 降级的具体说明
        """
        self.warnings.append(Degradation(feature, strategy, detail))


@dataclass(frozen=True, slots=True)
class Preset:
    """面向场景的参数包。用户不该被迫理解 quality=82 意味着什么。"""

    name: str
    label: str
    description: str
    options: dict[str, Any] = field(default_factory=dict)
    """按格式覆盖的选项：{"image/jpeg": {"quality": 82}}，"*" 表示全局。"""

    def options_for(self, target: MediaType) -> dict[str, Any]:
        """按 全局("*") → 域通配（"video/*"）→ 具体格式 逐层合并。"""
        merged = dict(self.options.get("*", {}))
        if "/" in target:
            merged.update(self.options.get(f"{target.split('/', 1)[0]}/*", {}))
        merged.update(self.options.get(target, {}))
        return merged


PRESETS: dict[str, Preset] = {
    "original": Preset(
        "original", "保持原样", "最高保真，不主动压缩",
        {
            "image/jpeg": {"quality": 95, "subsampling": 0, "optimize": True},
            "image/webp": {"quality": 95, "method": 6},
            "image/avif": {"quality": 90, "speed": 4},
            "image/png": {"compress_level": 9, "optimize": True},
        },
    ),
    "web": Preset(
        "web", "网页优化", "体积和画质平衡，适合网页和社交平台",
        {
            "*": {"max_edge": 2048},
            "image/jpeg": {"quality": 82, "progressive": True, "subsampling": 2,
                           "optimize": True},
            "image/webp": {"quality": 80},
            "image/avif": {"quality": 65, "speed": 6},
            "image/png": {"optimize": True, "compress_level": 9},
            "video/*": {"max_edge": 1920, "quality": 70, "preset_speed": "veryfast"},
            "audio/*": {"quality": 70},
        },
    ),
    "compact": Preset(
        "compact", "极限压缩", "优先体积，画质可见下降",
        {
            "*": {"max_edge": 1280, "keep_metadata": False},
            "image/jpeg": {"quality": 70, "progressive": True, "subsampling": 2,
                           "optimize": True},
            "image/webp": {"quality": 65},
            "image/avif": {"quality": 50, "speed": 8},
            "video/*": {"max_edge": 1280, "quality": 50, "preset_speed": "veryfast"},
            "audio/*": {"quality": 50},
        },
    ),
    "archive": Preset(
        "archive", "归档保存", "无损优先，保留全部元数据",
        {
            "*": {"keep_metadata": True, "keep_icc": True},
            # 有损源不装无损：lossless="auto" 只在源本身无损时启用，
            # 有损源走 q98——有损转无损只会更大更慢，画质不会回来
            "image/webp": {"lossless": "auto", "quality": 98, "method": 6},
            "image/jpeg": {"quality": 98, "subsampling": 0, "optimize": True},
            "image/png": {"optimize": True, "compress_level": 9},
            "image/avif": {"quality": 92, "speed": 2},
            "video/*": {"quality": 95, "preset_speed": "slow"},
            "audio/*": {"quality": 95},
        },
    ),
}


def resolve_options(
    target: MediaType,
    preset_name: str | None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """预设 + 用户覆盖合并成最终选项。用户显式传的参数优先级最高。"""
    opts: dict[str, Any] = {}
    if preset_name:
        preset = PRESETS.get(preset_name)
        if preset is None:
            raise ValueError(
                f"未知预设 {preset_name!r}，可选：{', '.join(PRESETS)}"
            )
        opts.update(preset.options_for(target))
    if overrides:
        opts.update({k: v for k, v in overrides.items() if v is not None})
    return opts


def policy_from_options(opts: dict[str, Any]) -> Policy:
    p = Policy()
    if "keep_metadata" in opts:
        p.keep_metadata = bool(opts["keep_metadata"])
    if "keep_icc" in opts:
        p.keep_icc = bool(opts["keep_icc"])
    if "dither" in opts:
        p.dither = bool(opts["dither"])
    if "background" in opts:
        p.alpha_background = tuple(opts["background"])  # type: ignore[assignment]
    return p
