"""自定义插件示例：演示如何添加新格式支持。"""

from pathlib import Path
from convcore.registry import Codec
from convcore.types import Capability, LossTier, Resource


# 示例 1: 简单的文本格式插件
class MarkdownCodecExample:
    """Markdown 插件示例（实际项目中已有更完整的实现）。"""

    name = "markdown-example"
    domain = "markup"

    reads = (
        Capability("text/markdown", LossTier.LOSSLESS, time_cost=0.5),
    )

    writes = (
        Capability("text/markdown", LossTier.LOSSLESS, time_cost=0.5),
    )

    @staticmethod
    def available() -> tuple[bool, str]:
        """检查插件依赖是否满足。"""
        # Markdown 不需要外部依赖
        return True, ""

    def parse(self, resources: list[Resource], options: dict):
        """读取 Markdown 文件，解析为 DocTree IR。"""
        from convcore.ir.markup import DocTree, Block, PARAGRAPH

        path = resources[0].path
        text = path.read_text(encoding="utf-8")

        # 简化示例：只按段落分割
        blocks = []
        for para in text.split("\n\n"):
            if para.strip():
                blocks.append(Block(kind=PARAGRAPH, text=para.strip()))

        return DocTree(blocks=blocks)

    def write(self, ir, target: str, options: dict) -> list[Resource]:
        """将 DocTree IR 写入 Markdown 文件。"""
        from convcore.ir.markup import DocTree

        tmp = options.get("_tmp")
        out = tmp / "output.md"

        lines = []
        for block in ir.blocks:
            # 简化示例：每个块一段
            lines.append(block.text)
            lines.append("")

        out.write_text("\n".join(lines), encoding="utf-8")

        return [Resource(media_type=target, path=out)]


# 示例 2: 需要外部依赖的插件
class CustomImageCodecExample:
    """自定义图片格式插件示例。"""

    name = "custom-image"
    domain = "raster"

    reads = (
        Capability("image/x-custom", LossTier.LOSSLESS, time_cost=2.0),
    )

    writes = (
        Capability("image/x-custom", LossTier.LOSSLESS, time_cost=2.0),
    )

    @staticmethod
    def available() -> tuple[bool, str]:
        """检查是否安装了假想的 customimage 库。"""
        try:
            import customimage  # 假想的外部库
            return True, ""
        except ImportError:
            return False, "缺少 customimage 库，运行 pip install customimage"

    def parse(self, resources: list[Resource], options: dict):
        """读取自定义格式图片。"""
        import customimage
        from convcore.ir.raster import Raster, Frame

        path = resources[0].path

        # 假设库返回 PIL Image
        img = customimage.load(path)

        return Raster(
            frames=[Frame(image=img, duration=0)],
            metadata={"format": "custom"},
        )

    def write(self, ir, target: str, options: dict) -> list[Resource]:
        """写入自定义格式图片。"""
        import customimage
        from convcore.ir.raster import Raster

        tmp = options.get("_tmp")
        out = tmp / "output.custom"

        # 取首帧
        frame = ir.frames[0]

        # 保存
        customimage.save(out, frame.image, quality=options.get("quality", 95))

        return [Resource(media_type=target, path=out)]


# 示例 3: 直接转换器（绕过枢纽）
class DirectConverterExample:
    """直接转换器示例：PNG → JPEG 优化路径。"""

    name = "png-jpeg-direct"

    # 直接转换不需要 domain
    direct = (
        (
            "image/png",  # 源格式
            "image/jpeg",  # 目标格式
            Capability("image/jpeg", LossTier.LOSSY, time_cost=0.8, fidelity=0.95),
        ),
    )

    @staticmethod
    def available() -> tuple[bool, str]:
        """检查 Pillow 是否可用。"""
        try:
            import PIL
            return True, ""
        except ImportError:
            return False, "缺少 Pillow"

    def convert(self, resources: list[Resource], target: str, options: dict) -> list[Resource]:
        """直接转换 PNG → JPEG，跳过 Raster IR。"""
        from PIL import Image

        src = resources[0].path
        tmp = options.get("_tmp")
        out = tmp / "output.jpeg"

        img = Image.open(src)
        if img.mode == "RGBA":
            # JPEG 不支持透明，合成白底
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img.save(out, "JPEG", quality=options.get("quality", 85))

        return [Resource(media_type=target, path=out)]


# 如何注册插件
def register_custom_plugins():
    """将自定义插件注册到系统。"""
    from convcore.registry import Registry

    # 创建注册表
    registry = Registry()

    # 手动注册插件实例
    # registry.register_codec(MarkdownCodecExample())
    # registry.register_codec(CustomImageCodecExample())
    # registry.register_direct(DirectConverterExample())

    # 或者使用装饰器（需要在模块顶层）
    # from convcore.registry import register
    #
    # @register
    # class MyPlugin:
    #     ...

    print("自定义插件注册完成")


# 使用自定义插件
def use_custom_plugin():
    """使用自定义插件进行转换。"""
    from convcore import Converter

    # 创建转换器时会自动扫描所有已注册插件
    conv = Converter()

    # 如果插件已注册，路径图会自动包含新格式
    # result = conv.convert("file.custom", "output.png")

    print("自定义插件使用示例完成")


# 插件开发指南
"""
## 插件开发最佳实践

### 1. 命名约定
- 插件文件名：描述性的 snake_case，如 `my_format.py`
- 类名：PascalCase + Codec/Converter 后缀
- name 属性：kebab-case，如 `my-format`

### 2. 依赖管理
- 在 available() 中检查外部依赖
- 返回 (False, "缺少 xxx，安装方法...") 提供清晰指引
- 将可选依赖添加到 pyproject.toml 的对应 extra

### 3. 错误处理
- parse/write 中抛出具体的异常（ConversionError）
- 提供有用的错误信息（文件路径、失败原因）
- 不要吞掉底层库的异常，让调试信息传递上来

### 4. 损失等级标注
- COPY: 字节完全相同（同格式同参数）
- LOSSLESS: 信息完整但编码不同（PNG ↔ BMP）
- LOSSY: 有损但视觉/语义可接受（PNG → JPEG）
- STRUCTURAL: 结构简化（DOCX → TXT）
- INFERRED: 需要推断补全（TXT → DOCX）

### 5. 性能考虑
- time_cost 基于 1 核 3GHz 处理 1MB 文件的秒数
- 大文件考虑流式处理，避免一次性加载到内存
- 使用 _tmp 目录存放中间文件

### 6. 测试覆盖
- 至少一个往返测试（A → B → A）
- 边界情况：空文件、单像素图、超长文本
- 错误情况：损坏文件、不支持的子格式

### 7. 元数据处理
- 尽量保留源文件的元数据（EXIF、标题、作者）
- IR 的 metadata 字段用于传递格式特定信息
- 写入时从 IR.metadata 恢复元数据

### 8. 多帧/多页处理
- 图片：ir.frames 列表存放所有帧
- 文档：ir.pages 列表存放页面信息
- 表格：ir.sheets 列表存放多张表
- 写入时处理 primary 属性（首帧/首页/首表）

### 9. 选项传递
- options 字典包含用户指定的参数
- 标准选项名：quality, width, height, preset
- 不认识的选项直接忽略（不要报错）

### 10. 文档规范
- 模块 docstring 说明插件支持的格式和用途
- 复杂的格式特性在注释中说明
- 如果有已知限制，在 available() 返回的消息中说明
"""


if __name__ == "__main__":
    print("=== 自定义插件示例 ===\n")

    # 检查示例插件是否可用
    plugin = MarkdownCodecExample()
    avail, msg = plugin.available()
    print(f"MarkdownCodecExample: {'可用' if avail else f'不可用 - {msg}'}")

    plugin2 = CustomImageCodecExample()
    avail2, msg2 = plugin2.available()
    print(f"CustomImageCodecExample: {'可用' if avail2 else f'不可用 - {msg2}'}")

    print("\n插件示例完成！")
    print("提示：将插件类放入 convcore/plugins/ 并用 @register 装饰即可自动注册。")
