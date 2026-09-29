"""自定义插件示例：给引擎加一个新格式，并真的跑通转换。

演示用的 CCDEMO 是虚构的位图格式：8 字节魔数 + 宽高（各 4 字节大端）+ 原始 RGB 像素。
插件只声明「能把 CCDEMO 解码成 raster IR、能把 raster IR 编码成 CCDEMO」，
经由它的多跳路径（CCDEMO→JPEG、PNG→CCDEMO、CCDEMO→PDF…）由图搜索自动得出。

这里把插件注册进一个私有 Registry，不污染全局注册表。正式加进项目要做三步：

1. 在 convcore/plugins/ 下建模块，类上加 @register（convcore.registry.register）
2. 把模块名加进 convcore/plugins/__init__.py 的 _MODULES，否则不会被导入
3. 在 convcore/formats.py 的 _TABLE 登记 MIME、域、扩展名和显示名；
   有魔数的格式再在 convcore/sniff.py 里加识别。不登记的话认不出 .ccdemo
   源文件、CLI 解析不了 `-t ccdemo`，产物也只能叫 .bin（下面的输出就能看到）

运行：python examples/custom_plugin.py
"""

from __future__ import annotations

import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from convcore import Converter, ConversionError, ConvertRequest, Resource
from convcore.ir.raster import Frame, Raster
from convcore.policy import Policy, Strategy
from convcore.registry import Registry, default_registry
from convcore.types import caps

# 路径描述里有 ⇒ 这类 GBK 编不出的符号，见 basic_conversion.py 的同名说明
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

DEMO_MIME = "image/x-ccdemo"
MAGIC = b"CCDEMO01"


class DemoCodec:
    """CCDEMO 编解码器。结构满足 convcore.registry.Codec 协议。"""

    name = "ccdemo"
    domain = "raster"
    reads = caps(DEMO_MIME)  # 默认 LOSSLESS、time_cost=1.0
    # 写出也声明 LOSSLESS：对 RGB 源成立。带透明的源会丢 alpha，这类内容相关的
    # 降级不改静态声明，而是在 write 里记进 policy，用户从 warnings 里看到
    writes = caps(DEMO_MIME)

    @staticmethod
    def available() -> tuple[bool, str]:
        # 依赖不就绪时返回 (False, 原因)：插件不进图，doctor 会显示原因
        return True, ""

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> Raster:
        data = resources[0].read_bytes()
        if not data.startswith(MAGIC) or len(data) < 16:
            raise ConversionError("不是 CCDEMO 文件：魔数不对")
        w, h = struct.unpack(">II", data[8:16])
        pixels = data[16:]
        if len(pixels) != w * h * 3:
            raise ConversionError(f"CCDEMO 像素数据长度不符：期望 {w * h * 3}，实际 {len(pixels)}")
        img = Image.frombytes("RGB", (w, h), pixels)
        return Raster(frames=[Frame(img)], source_type=DEMO_MIME)

    def write(self, ir: Raster, target: str, options: dict[str, Any]) -> list[Resource]:
        # _policy 由执行器注入。表达不了的特性要记下来，用户会在 warnings 里看到
        pol: Policy = options.get("_policy") or Policy()
        if len(ir.frames) > 1:
            pol.note("动画", Strategy.DROP, f"CCDEMO 只存单帧，丢弃其余 {len(ir.frames) - 1} 帧")
        img = ir.primary.image
        if img.mode != "RGB":
            if ir.has_alpha:
                pol.note("透明通道", Strategy.DROP, "CCDEMO 不支持透明，已丢弃 alpha")
            img = img.convert("RGB")
        header = MAGIC + struct.pack(">II", *img.size)
        return [Resource(media_type=target, data=header + img.tobytes())]


def build_converter() -> Converter:
    """内置插件 + DemoCodec 组成的私有注册表。"""
    reg = Registry()
    for plugin in default_registry().plugins:
        reg.add(plugin)
    reg.add(DemoCodec())
    return Converter(reg)


def main() -> None:
    conv = build_converter()

    targets = conv.targets_for(DEMO_MIME)
    print(f"CCDEMO 可转成 {len(targets)} 种格式，例如：")
    for mt in ("image/jpeg", "image/webp", "application/pdf"):
        if mt in targets:
            print(f"  {mt:18} {targets[mt].loss.label:8} {targets[mt].describe()}")

    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        src = work / "logo.png"
        Image.new("RGBA", (48, 32), (30, 120, 220, 255)).save(src)

        # PNG → CCDEMO（带 alpha，会记一条降级告警）
        res = conv.convert(
            ConvertRequest(
                inputs=[Resource(media_type="image/png", path=src, name=src.name)],
                target=DEMO_MIME,
            )
        )
        demo = res.outputs[0]
        print(f"\nPNG → CCDEMO：{len(demo.read_bytes())} 字节，产物名 {demo.name}")
        print(f"  路径 {res.path_desc}，告警 {res.warnings}")

        # CCDEMO → JPEG：图搜索自动走 CCDEMO → [raster] → JPEG
        res2 = conv.convert(
            ConvertRequest(inputs=[demo], target="image/jpeg"),
            persist_dir=work,
        )
        jpg = res2.outputs[0]
        with Image.open(jpg.path) as im:
            print(f"CCDEMO → JPEG：{im.format} {im.size}，损失 {res2.loss.label}")


if __name__ == "__main__":
    main()
