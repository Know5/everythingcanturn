"""基础转换示例：每一段都真的执行。

输入样本在临时目录里现场生成，跑完自动清理，不需要准备任何文件。
只依赖基础安装（Pillow）；跨域到 PDF 那段需要 document extra，缺了会提示并跳过。

运行：python examples/basic_conversion.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from convcore import Converter, NoPathError, convert_file
from convcore.jobs import JobQueue

# 路径描述里有 ⇒ 这类 GBK 编不出的符号。输出重定向到文件/管道时 Windows 默认 GBK，
# 不切 UTF-8 会抛 UnicodeEncodeError。与 convcore/cli.py 同样处理。
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def make_samples(d: Path) -> dict[str, Path]:
    photo = Image.new("RGB", (640, 480), (40, 90, 160))
    ImageDraw.Draw(photo).ellipse([160, 80, 480, 400], fill=(240, 200, 60))
    photo.save(d / "photo.png")
    photo.save(d / "photo.bmp")

    logo = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    ImageDraw.Draw(logo).ellipse([8, 8, 120, 120], fill=(220, 50, 60, 255))
    logo.save(d / "logo.png")
    return {"photo": d / "photo.png", "bmp": d / "photo.bmp", "logo": d / "logo.png"}


def example_single(src: Path, out: Path) -> None:
    print("=== 单文件：PNG → WebP ===")
    # 目标可以写扩展名（webp / .webp）或 MIME（image/webp）
    res = convert_file(src, "webp", out_dir=out)
    o = res.outputs[0].path
    print(f"  {o.name}  {o.stat().st_size} 字节  损失：{res.loss.label}  路径：{res.path_desc}")


def example_options(src: Path, out: Path) -> None:
    print("\n=== 带参数：质量 + 限制最长边 ===")
    res = convert_file(
        src, "jpeg", out_path=out / "photo-small.jpg",
        overrides={"quality": 80, "max_edge": 320},
    )
    with Image.open(res.outputs[0].path) as im:
        print(f"  {res.outputs[0].path.name}  {im.size}")


def example_preset(src: Path, out: Path) -> None:
    print("\n=== 预设：web ===")
    res = convert_file(src, "webp", out_path=out / "photo-web.webp", preset="web")
    print(f"  {res.outputs[0].path.name}  {res.outputs[0].path.stat().st_size} 字节")


def example_batch(sources: list[Path], out: Path) -> None:
    print("\n=== 批量：JobQueue，同名产物自动消歧 ===")
    # photo.png 和 photo.bmp 都转 webp，第二个会命名为 photo-bmp.webp
    q = JobQueue()
    q.add_files(sources, "webp", out_dir=out / "batch")
    for job in q.run():
        name = job.result.outputs[0].path.name if job.result else job.error
        print(f"  {job.source.name:10} → {name}  [{job.state.value}]")


def example_overwrite(src: Path, out: Path) -> None:
    print("\n=== 覆盖保护：目标已存在时默认拒绝 ===")
    try:
        convert_file(src, "webp", out_dir=out)  # example_single 已经产出过
    except Exception as exc:
        print(f"  {type(exc).__name__}: {exc}")
    res = convert_file(src, "webp", out_dir=out, overwrite=True)
    print(f"  overwrite=True 后成功：{res.outputs[0].path.name}")


def example_cross_domain(src: Path, out: Path) -> None:
    print("\n=== 跨域：PNG → PDF（经 raster ⇒ document 桥）===")
    try:
        res = convert_file(src, "pdf", out_dir=out)
    except NoPathError as exc:
        print(f"  跳过：{exc}（需要 pip install -e \".[document]\"）")
        return
    print(f"  {res.outputs[0].path.name}  路径：{res.path_desc}")


def example_targets() -> None:
    print("\n=== 查询：PNG 能转成什么 ===")
    targets = Converter().targets_for("image/png")
    print(f"  共 {len(targets)} 种，前几种：")
    for mt, path in list(targets.items())[:5]:
        print(f"  {mt:24} {path.loss.label}")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        s = make_samples(work)
        out = work / "out"
        example_single(s["photo"], out)
        example_options(s["photo"], out)
        example_preset(s["photo"], out)
        example_batch([s["photo"], s["bmp"]], out)
        example_overwrite(s["photo"], out)
        example_cross_domain(s["logo"], out)
        example_targets()
    print("\n全部示例执行完毕。")
