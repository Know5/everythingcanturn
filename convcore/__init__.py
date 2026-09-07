"""convcore：全格式转换引擎。

核心思想是把「可转换性」建模成数据而不是代码：插件声明自己能读写什么格式、
代价多大，这些声明构成一张格式图，转换就是图上按信息损失最小的路径搜索。
加一个格式只需注册一个插件，所有经由它的多跳路径自动出现。

典型用法：

    from convcore import Converter, convert_file

    convert_file(Path("photo.heic"), "png", out_dir=Path("out"))

    conv = Converter()
    for mt, path in conv.targets_for("image/heic").items():
        print(mt, path.loss.label)
"""

from .pipeline import Converter, convert_file
from .policy import PRESETS, Policy
from .types import (
    Capability,
    Cancelled,
    ConversionError,
    ConvertRequest,
    ConvertResult,
    LossTier,
    NoPathError,
    Resource,
)

__version__ = "0.1.0"

__all__ = [
    "Capability",
    "Cancelled",
    "ConversionError",
    "ConvertRequest",
    "ConvertResult",
    "Converter",
    "LossTier",
    "NoPathError",
    "PRESETS",
    "Policy",
    "Resource",
    "convert_file",
]
