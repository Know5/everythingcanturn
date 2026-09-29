# Contributing to everythingcanturn

感谢你对这个项目的关注！这份指南会帮助你参与到项目开发中来。

## 行为准则

本项目遵循开源社区的基本准则：

- 尊重所有贡献者和用户
- 接受建设性的批评
- 关注对项目最有利的事情
- 对社区成员表现出同理心

## 如何贡献

### 报告 Bug

在提交 Bug 报告前，请先搜索现有 issue 确认问题未被报告过。提交时请包含：

- **清晰的标题**：简要描述问题
- **复现步骤**：详细说明如何触发问题
- **期望行为**：说明你预期应该发生什么
- **实际行为**：说明实际发生了什么
- **环境信息**：操作系统、Python 版本、依赖版本
- **示例文件**：如果可能，提供能复现问题的最小样本文件

示例：

```
标题: PNG 转 JPEG 时 EXIF 方向信息丢失

环境:
- OS: Windows 11
- Python: 3.13.7
- convcore: 0.1.0

复现步骤:
1. 准备一张带 EXIF Orientation=6 的 PNG 图片
2. 运行 convcore convert photo.png -t jpeg
3. 查看输出的 JPEG

期望: 输出 JPEG 保持正确方向
实际: 输出图片旋转了 90 度
```

### 提出新功能

功能请求同样需要先搜索现有 issue。提交时请说明：

- **使用场景**：什么情况下需要这个功能
- **期望接口**：你希望如何使用这个功能
- **替代方案**：目前有哪些临时解决方法
- **优先级**：对你的工作流有多重要

### 贡献代码

#### 开发环境搭建

```bash
# 克隆仓库
git clone <repo-url>
cd everythingcanturn

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装开发依赖
pip install -e ".[all,dev]"

# 运行测试确认环境正常
pytest tests/
```

#### 分支策略

- `main` 分支是稳定分支，只接受经过测试的合并
- 新功能从 `main` 创建 feature 分支：`git checkout -b feature/my-feature`
- Bug 修复从 `main` 创建 fix 分支：`git checkout -b fix/issue-123`

#### 代码风格

项目遵循以下约定：

- **格式化**：代码使用 4 空格缩进，行宽建议 88 字符（Black 默认）
- **类型提示**：公共 API 必须有类型提示，内部实现推荐使用
- **文档字符串**：公共类和函数需要 docstring，说明参数和返回值
- **命名**：
  - 变量和函数：`snake_case`
  - 类：`PascalCase`
  - 常量：`UPPER_SNAKE_CASE`
  - 私有成员：`_leading_underscore`

示例：

```python
def convert_image(
    source: Path, target: MediaType, *, quality: int = 85
) -> Path:
    """转换图片格式。

    Args:
        source: 源文件路径
        target: 目标 MIME 类型
        quality: 质量参数 0-100，仅对有损格式有效

    Returns:
        输出文件路径

    Raises:
        ConversionError: 转换失败时抛出
    """
    ...
```

#### 添加新格式支持

新格式插件的标准流程：

1. **确认格式归属**：确定格式属于哪个域（raster/media/document...）
2. **创建插件文件**：在 `convcore/plugins/` 下创建 `.py` 文件，插件类加 `@register`
3. **登记模块**：把模块名加进 `convcore/plugins/__init__.py` 的 `_MODULES`（不会自动扫描目录）
4. **登记格式**：在 `convcore/formats.py` 的 `_TABLE` 加 MIME、域、扩展名、显示名；
   有魔数的格式在 `convcore/sniff.py` 加识别。否则认不出源文件、CLI 解析不了目标名
5. **声明能力**：`reads` / `writes` 用 `caps(...)` 声明（`convcore.types`）。写出格式对内容
   有要求时加 `requires`，如音频格式要音轨 `caps("audio/mpeg", requires={"audio"})`、纯文本要
   文字 `requires={"text"}`，否则图片合成的视频转 MP3 这类注定失败的路径会出现在列表里。
   新增跨域桥时，同时在 `graph.py` 的 `BRIDGE_LACKS` / `BRIDGE_REQUIRES` 声明产物缺什么、输入要什么
6. **可选依赖**：如果需要外部库，在 `pyproject.toml` 添加到对应 extra，并实现 `available()`
7. **编写测试**：在 `tests/` 下添加往返测试和失败路径测试
8. **更新文档**：在 README 的格式列表中补充新格式

插件模板（协议定义见 `convcore/registry.py` 的 `Codec`；可运行的完整例子见
`examples/custom_plugin.py`）：

```python
"""MyFormat 插件：读写 image/x-myformat。"""

from __future__ import annotations

from typing import Any

from ..ir.raster import Frame, Raster
from ..registry import register
from ..types import ConversionError, Resource, caps


@register
class MyFormatCodec:
    name = "myformat"
    domain = "raster"
    reads = caps("image/x-myformat")
    writes = caps("image/x-myformat")

    @staticmethod
    def available() -> tuple[bool, str]:
        """依赖不就绪时插件不进图，原因显示在 convcore doctor 里。"""
        try:
            import myformat_lib  # noqa: F401
        except ImportError:
            return False, "缺少 myformat-lib，安装后可读写 MyFormat"
        return True, ""

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> Raster:
        import myformat_lib

        try:
            img = myformat_lib.load(resources[0].read_bytes())  # 返回 PIL Image
        except myformat_lib.Error as exc:
            raise ConversionError(f"MyFormat 解码失败：{exc}") from exc
        return Raster(frames=[Frame(img)], source_type="image/x-myformat")

    def write(self, ir: Raster, target: str, options: dict[str, Any]) -> list[Resource]:
        import myformat_lib

        # 需要落盘时写进 options["_tmp"]（执行器管理的工作目录），不要写 CWD
        return [Resource(media_type=target, data=myformat_lib.dump(ir.primary.image))]
```

测试模板：

```python
# tests/test_myformat.py
from PIL import Image

from convcore import convert_file

from .fidelity import pixels_identical


def test_myformat_roundtrip(samples, tmp_path):
    """MyFormat 往返测试：PNG → MyFormat → PNG，无损路径应逐像素一致。"""
    src = samples["png"]  # conftest.py 里的会话级合成样本

    step1 = convert_file(src, "image/x-myformat", out_dir=tmp_path)
    step2 = convert_file(step1.outputs[0].path, "png", out_path=tmp_path / "back.png")

    with Image.open(src) as a, Image.open(step2.outputs[0].path) as b:
        assert pixels_identical(a, b)
```

有损路径改用 `tests/fidelity.py` 里的 `psnr` / `ssim` 设阈值；要表驱动地批量测格式对，
在 `tests/test_matrix.py` 的 `CASES` 里加行。

#### 运行测试

```bash
# 运行全部测试
pytest tests/

# 运行特定测试
pytest tests/test_matrix.py
pytest tests/test_sniff.py -k heic

# 查看覆盖率
pytest --cov=convcore --cov-report=html
```

缺可选依赖或外部引擎（ffmpeg、soffice 等）的用例会自动跳过，用 `pytest -rs` 看跳过原因。

#### 提交代码

提交信息遵循约定式提交（Conventional Commits）：

```
<type>: <subject>

<body>

<footer>
```

类型：

- `feat`: 新功能
- `fix`: Bug 修复
- `docs`: 文档更新
- `refactor`: 重构（不改变行为）
- `test`: 添加或修改测试
- `perf`: 性能优化
- `chore`: 构建/工具链变更

示例：

```
feat: 添加 AVIF 格式支持

实现 pillow-heif 插件，支持 AVIF/HEIC 读写。
- 读：解码为 PIL Image
- 写：支持质量参数和有损/无损模式
- 测试：往返测试 + 多帧动图

Closes #42
```

#### Pull Request

提交 PR 前的检查清单：

- [ ] 所有测试通过：`pytest tests/`
- [ ] 代码覆盖率未下降（新代码有对应测试）
- [ ] 提交信息清晰
- [ ] 如果是新功能，更新了 README
- [ ] 如果修复 Bug，添加了回归测试
- [ ] 如果改变了公共 API，更新了文档

PR 描述模板：

```markdown
## 变更内容

简要描述这个 PR 做了什么。

## 动机

为什么需要这个变更？解决了什么问题？

## 测试

如何验证这个变更是正确的？添加了哪些测试？

## 截图（如果适用）

对于 UI 变更，提供截图展示前后对比。

## 相关 Issue

Closes #123
```

### 文档贡献

文档改进同样欢迎！包括但不限于：

- 修正拼写或语法错误
- 补充缺失的 API 文档
- 添加使用示例
- 完善安装说明
- 翻译文档到其他语言

文档改进直接提 PR，无需先开 issue。

## 开发路线图

当前优先级（按顺序）：

1. **测试覆盖率提升**：CLI 和 WebUI 模块目前 0 覆盖，需要补充测试
2. **性能优化**：大文件转换的内存和速度优化
3. **格式补全**：添加更多格式支持（GIF、ICO、TIFF 多页等）
4. **跨域桥完善**：补充 raster→vector（位图描摹）等缺失桥接
5. **插件系统**：支持外部插件动态加载

## 问题讨论

如果你有任何疑问或想讨论某个想法，可以：

- 开一个 Discussion（如果仓库启用了）
- 提一个标记为 `question` 的 issue
- 直接在相关 issue 下评论

## 致谢

感谢所有贡献者让这个项目变得更好！每一个 PR、issue 报告、文档改进都很重要。
