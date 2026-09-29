"""进阶示例：降级告警、预设、路径解释、嗅探、错误处理、取消、环境诊断。

和 basic_conversion.py 一样，样本在临时目录里现场生成，每一段都真的执行。

运行：python examples/advanced_usage.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from convcore import ConversionError, Converter, NoPathError, convert_file
from convcore.jobs import JobQueue
from convcore.policy import PRESETS
from convcore.sniff import sniff

# 路径描述里有 ⇒ 这类 GBK 编不出的符号，见 basic_conversion.py 的同名说明
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def example_degradation_warnings(d: Path) -> None:
    print("=== 降级告警：透明 PNG → JPEG ===")
    src = d / "logo.png"
    logo = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(logo).ellipse([4, 4, 60, 60], fill=(220, 50, 60, 255))
    logo.save(src)
    # JPEG 表达不了透明：按 background 选项合成底色（默认白色），并在 warnings 里说明
    res = convert_file(src, "jpeg", out_dir=d / "out", overrides={"background": (0, 0, 0)})
    for w in res.warnings:
        print(f"  ! {w}")
    with Image.open(res.outputs[0].path) as im:
        print(f"  左上角像素（原本透明）：{im.getpixel((0, 0))}")


def example_presets() -> None:
    print("\n=== 参数预设 ===")
    for name, p in PRESETS.items():
        print(f"  {name:9} {p.label}：{p.description}")


def example_explain_path() -> None:
    print("\n=== 路径解释：CSV → PDF 的每一步 ===")
    try:
        path = Converter().plan("text/csv", "application/pdf")
    except NoPathError as exc:
        print(f"  跳过：{exc}")
        return
    print(f"  {path.describe()}  整体：{path.loss.label}")
    for e in path.edges:
        print(f"    {e.kind.value:7} {e.describe()}  {e.loss.label}")


def example_sniff(d: Path) -> None:
    print("\n=== 嗅探：按魔数识别，不信扩展名 ===")
    fake = d / "really-a-png.dat"
    Image.new("RGB", (8, 8)).save(fake, format="PNG")
    print(f"  {fake.name} → {sniff(fake)}")


def example_errors(d: Path) -> None:
    print("\n=== 错误处理 ===")
    # NoPathError、Cancelled 都是 ConversionError 的子类，只关心成败时捕获基类即可
    try:
        Converter().plan("image/png", "image/svg+xml")  # 位图 → 矢量描摹尚未实现
    except NoPathError as exc:
        print(f"  NoPathError: {exc}")

    junk = d / "junk.bin"
    junk.write_bytes(b"\x00\x01garbage" * 8)
    try:
        convert_file(junk, "png", out_dir=d / "out")
    except ConversionError as exc:
        print(f"  ConversionError: {exc}")


def example_cancel(d: Path) -> None:
    print("\n=== 取消：开跑前取消其中一个任务 ===")
    sources = []
    for i in range(3):
        p = d / f"frame{i}.png"
        Image.new("RGB", (64, 64), (i * 80, 60, 120)).save(p)
        sources.append(p)
    q = JobQueue(workers=1)
    jobs = q.add_files(sources, "webp", out_dir=d / "out")
    # 运行中的任务也能取消：job.cancel() 后，下一次进度回调或引擎轮询时中断
    jobs[-1].cancel()
    q.run()
    for job in q:
        print(f"  {job.source.name}: {job.state.value}")


def example_doctor() -> None:
    print("\n=== 环境诊断：哪些插件因缺依赖没启用 ===")
    reg = Converter().registry
    print(f"  已启用 {len(reg.plugins)} 个插件，可读 {len(reg.readable())} 种、可写 {len(reg.writable())} 种格式")
    for name, reason in reg.unavailable.items():
        print(f"  - {name}: {reason}")
    if not reg.unavailable:
        print("  所有插件都已启用")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        example_degradation_warnings(work)
        example_presets()
        example_explain_path()
        example_sniff(work)
        example_errors(work)
        example_cancel(work)
        example_doctor()
    print("\n全部示例执行完毕。")
