"""基础转换示例：演示最常见的文件转换场景。"""

from pathlib import Path
from convcore import Converter


def example_image_format_conversion():
    """图片格式转换：PNG → JPEG。"""
    print("=== 图片格式转换 ===")

    conv = Converter()

    # 单文件转换
    # conv.convert("photo.png", "output.jpeg")

    # 或者指定 MIME 类型
    # conv.convert("photo.png", target_mime="image/jpeg")

    # 带质量参数
    # conv.convert("photo.png", "output.jpeg", quality=95)

    print("转换完成：photo.png → output.jpeg\n")


def example_document_extraction():
    """文档提取：PDF → Markdown。"""
    print("=== PDF 转 Markdown ===")

    conv = Converter()

    # PDF 文本提取并推断标题层级
    # result = conv.convert("report.pdf", "report.md")

    # 路径会自动规划：PDF → [document] → [markup] → Markdown
    # 损失等级：INFERRED（标题层级基于字号推断）

    print("转换完成：report.pdf → report.md\n")


def example_video_conversion():
    """视频转换：MP4 → WebM。"""
    print("=== 视频格式转换 ===")

    conv = Converter()

    # 视频转码（需要 FFmpeg）
    # result = conv.convert("video.mp4", "output.webm")

    # 带预设
    # result = conv.convert("video.mp4", "output.webm", preset="web-video")

    print("转换完成：video.mp4 → output.webm\n")


def example_batch_conversion():
    """批量转换：将目录中的所有 HEIC 照片转为 PNG。"""
    print("=== 批量转换 ===")

    conv = Converter()

    # 获取所有 HEIC 文件
    # heic_files = list(Path("photos").glob("*.heic"))

    # 批量转换，带进度回调
    # results = conv.batch(
    #     heic_files,
    #     target_mime="image/png",
    #     output_dir="photos_png",
    #     on_progress=lambda p: print(f"{p.percent:.0f}% - {p.file.name}")
    # )

    # print(f"转换完成：{len(results)} 个文件")

    print("批量转换示例\n")


def example_inspect_capabilities():
    """探测能力：查看某格式能转换到哪些目标。"""
    print("=== 探测转换能力 ===")

    conv = Converter()

    # 查询从 PNG 可达的所有目标格式
    targets = conv.graph.reachable_from("image/png")

    print(f"PNG 可转换到 {len(targets)} 种格式：")
    for mime, path in sorted(targets.items())[:5]:
        print(f"  - {mime}: {path.describe()}")
        print(f"    损失: {path.loss.name}, 步数: {len(path.edges)}")

    print(f"  ... 还有 {len(targets) - 5} 种格式\n")


def example_find_path():
    """路径规划：查看两个格式间的转换路径。"""
    print("=== 路径规划 ===")

    conv = Converter()

    # 查找 HEIC → JPEG 的路径
    try:
        path = conv.graph.find_path("image/heic", "image/jpeg")
        print(f"HEIC → JPEG 路径：")
        print(f"  {path.describe()}")
        print(f"  损失: {path.loss.name}")
        print(f"  有损步数: {path.lossy_steps}")
        print(f"  中间物化: {path.materializations}")
    except Exception as e:
        print(f"找不到路径: {e}")

    print()


def example_cross_domain_bridge():
    """跨域桥接：视频截图转 PDF 文档。"""
    print("=== 跨域桥接 ===")

    conv = Converter()

    # 视频 → 位图 → 文档，路径自动规划
    # result = conv.convert("video.mp4", "screenshot.pdf")

    # 路径：MP4 → [media] → [raster] → [document] → PDF
    # 第一跳：提取首帧（LOSSLESS）
    # 第二跳：图片嵌入 PDF 页面（LOSSLESS）

    print("跨域转换：video.mp4 → screenshot.pdf\n")


def example_with_options():
    """带选项的转换：精细控制输出质量。"""
    print("=== 带选项的转换 ===")

    conv = Converter()

    # 图片转换带质量参数
    # conv.convert("photo.png", "output.jpeg", quality=95)

    # 视频转换带编码选项
    # conv.convert("input.mp4", "output.webm",
    #              video_bitrate=2000, audio_bitrate=128)

    # 文档转换带页面范围
    # conv.convert("document.pdf", "extract.txt", pages="1-5")

    print("选项示例\n")


if __name__ == "__main__":
    # 运行所有示例（注释掉实际转换调用，避免找不到文件）
    example_image_format_conversion()
    example_document_extraction()
    example_video_conversion()
    example_batch_conversion()
    example_inspect_capabilities()
    example_find_path()
    example_cross_domain_bridge()
    example_with_options()

    print("所有示例运行完成！")
    print("\n提示：取消注释实际转换代码并准备对应的输入文件后即可运行真实转换。")
