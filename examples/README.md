# Examples

这个目录包含了 `everythingcanturn` 的使用示例，涵盖从基础转换到高级定制的各种场景。

## 文件列表

### `basic_conversion.py`
基础转换示例，演示最常见的使用场景：

- 图片格式转换（PNG → JPEG）
- 文档提取（PDF → Markdown）
- 视频转码（MP4 → WebM）
- 批量转换
- 探测转换能力
- 路径规划查询
- 跨域桥接（视频 → PDF）

适合初次使用者快速上手。

### `advanced_usage.py`
高级用法示例，展示更复杂的功能：

- 自定义转换策略（优先保真度/速度）
- 进度跟踪和回调
- 格式自动探测
- 跨域转换详解（表格 → 文档）
- 文档结构提取
- 视频拆帧
- 动图转视频
- 错误处理
- 注册表检查
- 可达格式查询

适合需要精细控制转换流程的场景。

### `custom_plugin.py`
自定义插件开发指南和示例代码：

- 简单文本格式插件（Markdown）
- 需要外部依赖的插件（自定义图片格式）
- 直接转换器（绕过枢纽优化）
- 插件注册方法
- 开发最佳实践（10 条）

适合想要扩展格式支持的开发者。

## 快速开始

所有示例都可以直接运行（演示模式，实际转换代码已注释）：

```bash
# 基础示例
python examples/basic_conversion.py

# 高级示例
python examples/advanced_usage.py

# 插件开发示例
python examples/custom_plugin.py
```

要运行真实的转换，取消注释相应的转换调用并准备输入文件。

## 示例数据

示例代码中使用的测试文件（需自行准备）：

- `photo.png` / `photo.heic` - 任意图片文件
- `report.pdf` / `document.docx` - 任意文档文件
- `video.mp4` - 任意视频文件
- `data.csv` - CSV 表格文件
- `animated.gif` - 动图文件

或者使用实际项目中的测试文件（`tests/fixtures/`）。

## 更多资源

- [README.md](../README.md) - 项目总览和安装指南
- [CONTRIBUTING.md](../CONTRIBUTING.md) - 贡献指南和开发规范
- [convcore/plugins/](../convcore/plugins/) - 现有插件源码，可作为参考

## 反馈

如果示例中有不清楚的地方，或者希望看到特定场景的示例，欢迎提 issue 或 PR。
