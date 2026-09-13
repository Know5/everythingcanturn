"""高级用法示例：自定义插件、策略控制、进度跟踪。"""

from pathlib import Path
from convcore import Converter
from convcore.policy import Policy, Strategy
from convcore.types import LossTier


def example_custom_policy():
    """自定义转换策略：使用预设和自定义选项。"""
    print("=== 自定义策略 ===")

    from convcore.policy import PRESETS

    # 查看可用预设
    print("可用预设:")
    for name, preset in PRESETS.items():
        print(f"  - {name}: {preset.label}")

    # 使用预设转换
    conv = Converter()
    # result = conv.convert("photo.heic", "output.jpeg", preset="web")

    # 自定义 Policy 对象
    policy = Policy()
    policy.keep_metadata = True
    policy.alpha_background = (255, 255, 255)  # 白色背景
    policy.animation = Strategy.EMULATE  # 导出所有帧

    # 带自定义 policy 的转换
    # result = conv.convert("photo.png", "output.jpeg",
    #                       keep_metadata=True, background=(255,255,255))

    print("策略示例完成\n")


def example_progress_tracking():
    """进度跟踪：实时监控批量转换。"""
    print("=== 进度跟踪 ===")

    from convcore.jobs import JobQueue

    conv = Converter()

    # 模拟批量任务
    files = [f"file{i}.png" for i in range(10)]

    def progress_callback(status):
        """进度回调：打印当前状态。"""
        print(f"[{status.percent:5.1f}%] {status.current}/{status.total} - {status.file}")
        if status.error:
            print(f"  错误: {status.error}")

    # 实际使用时取消注释
    # results = conv.batch(
    #     files,
    #     target_mime="image/jpeg",
    #     on_progress=progress_callback
    # )

    print("进度跟踪示例完成\n")


def example_format_detection():
    """格式探测：自动识别未知文件。"""
    print("=== 格式探测 ===")

    from convcore.sniff import sniff

    # 探测文件真实格式（不依赖扩展名）
    # mime = sniff(Path("mystery_file.bin"))
    # print(f"探测到格式: {mime}")

    conv = Converter()

    # 自动探测源格式
    # result = conv.convert("unknown_file.dat", "output.pdf")
    # 转换器会先调用 sniff 识别格式

    print("格式探测示例完成\n")


def example_cross_domain_table_to_pdf():
    """跨域示例：CSV 表格转 PDF 文档。"""
    print("=== 表格转文档 ===")

    conv = Converter()

    # CSV → [table] → [document] → PDF
    # 路径自动规划，表格渲染为 PDF 页面
    # result = conv.convert("data.csv", "report.pdf")

    # 查看路径
    path = conv.graph.find_path("text/csv", "application/pdf")
    print("CSV → PDF 路径：")
    print(f"  {path.describe()}")
    print(f"  损失: {path.loss.name}")
    print(f"  步数: {len(path.edges)}")

    print()


def example_document_to_markdown():
    """文档提取：DOCX/PDF 提取为 Markdown。"""
    print("=== 文档提取为 Markdown ===")

    conv = Converter()

    # DOCX → Markdown（保留标题层级和列表结构）
    # result = conv.convert("document.docx", "output.md")

    # PDF → Markdown（基于字号推断标题层级）
    # result = conv.convert("report.pdf", "output.md")

    # 查看 PDF → Markdown 路径
    path = conv.graph.find_path("application/pdf", "text/markdown")
    print("PDF -> Markdown 路径：")
    for edge in path.edges:
        # 使用 ASCII 安全的描述
        src = edge.src[1] if edge.src[0] == "hub" else edge.src[1]
        dst = edge.dst[1] if edge.dst[0] == "hub" else edge.dst[1]
        print(f"  {src} -> {dst} (损失: {edge.loss.name})")

    print()


def example_video_to_images():
    """视频拆帧：提取视频的每一帧为图片序列。"""
    print("=== 视频拆帧 ===")

    from convcore.pipeline import Converter

    conv = Converter()

    # 提取首帧
    # conv.convert("video.mp4", "frame.png")

    # 批量提取多帧需要直接使用 FFmpeg 插件
    # from convcore.plugins.media import extract_frames
    # frames = extract_frames(Path("video.mp4"), fps=1)  # 每秒 1 帧

    print("视频拆帧示例完成\n")


def example_animated_gif_to_video():
    """动图转视频：GIF/WebP 动画转 MP4。"""
    print("=== 动图转视频 ===")

    conv = Converter()

    # 多帧图片 → 视频
    # result = conv.convert("animated.gif", "output.mp4")

    # 路径：GIF → [raster] → [media] → MP4
    # 每帧转为视频帧流，时间轴从 GIF 帧延迟推断

    print("动图转视频示例完成\n")


def example_error_handling():
    """错误处理：捕获转换失败。"""
    print("=== 错误处理 ===")

    from convcore.types import NoPathError, ConversionError

    conv = Converter()

    # 捕获找不到路径的错误
    try:
        path = conv.graph.find_path("application/x-unknown", "image/png")
    except NoPathError as e:
        print(f"找不到路径: {e}")

    # 捕获转换失败
    try:
        # result = conv.convert("corrupted.pdf", "output.png")
        pass
    except ConversionError as e:
        print(f"转换失败: {e}")

    print()


def example_inspect_registry():
    """检查注册表：查看已加载的插件。"""
    print("=== 检查注册表 ===")

    conv = Converter()

    # 列出所有已加载的插件
    codecs = list(conv.registry.codecs())
    print(f"已加载 {len(codecs)} 个编解码器：")
    for codec in codecs[:5]:
        print(f"  - {codec.name}: {codec.domain} 域")
        print(f"    读: {len(codec.reads)} 种格式, 写: {len(codec.writes)} 种格式")

    print(f"  ... 还有 {len(codecs) - 5} 个")

    # 列出不可用的插件
    unavail = conv.registry._unavailable
    if unavail:
        print(f"\n不可用插件 ({len(unavail)}):")
        for name, reason in list(unavail.items())[:3]:
            print(f"  - {name}: {reason}")
    else:
        print("\n所有插件已加载")

    print()


def example_reachable_formats():
    """可达格式：从某格式出发能到达的所有目标。"""
    print("=== 可达格式查询 ===")

    conv = Converter()

    # 查询 PDF 可转换到的所有格式
    targets = conv.graph.reachable_from("application/pdf")

    # 按损失等级分组
    by_loss = {}
    for mime, path in targets.items():
        by_loss.setdefault(path.loss.name, []).append(mime)

    print("从 PDF 可达的格式 (按损失分组):")
    for loss, mimes in sorted(by_loss.items()):
        print(f"  {loss}: {len(mimes)} 种格式")
        for mime in sorted(mimes)[:3]:
            print(f"    - {mime}")
        if len(mimes) > 3:
            print(f"    ... 还有 {len(mimes) - 3} 种")

    print()


if __name__ == "__main__":
    # 运行所有高级示例
    example_custom_policy()
    example_progress_tracking()
    example_format_detection()
    example_cross_domain_table_to_pdf()
    example_document_to_markdown()
    example_video_to_images()
    example_animated_gif_to_video()
    example_error_handling()
    example_inspect_registry()
    example_reachable_formats()

    print("所有高级示例运行完成！")
