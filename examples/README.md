# Examples

每个示例都会真实执行转换。输入样本在临时目录里现场生成，跑完自动清理，
不需要准备任何文件，也不会在当前目录留下产物。

```bash
python examples/basic_conversion.py
python examples/advanced_usage.py
python examples/custom_plugin.py
```

只依赖基础安装（`pip install -e .`）。需要可选依赖的段落（如 PNG → PDF 要 `document` extra）
在缺依赖时会打印「跳过」并继续。

## `basic_conversion.py`

- 单文件转换：`convert_file(src, "webp", out_dir=...)`
- 带参数：质量、限制最长边、指定输出路径
- 参数预设：`preset="web"`
- 批量：`JobQueue`，同名产物自动消歧（`photo.webp` / `photo-bmp.webp`）
- 覆盖保护：目标已存在时默认报错，`overwrite=True` 才覆盖
- 跨域：PNG → PDF（经 `raster ⇒ document` 桥）
- 查询可达目标：`Converter().targets_for(...)`

## `advanced_usage.py`

- 降级告警：透明 PNG → JPEG，自定义合成底色，读 `res.warnings`
- 列出参数预设
- 路径解释：CSV → PDF 的每条边及其损失等级
- 按魔数嗅探，不信扩展名
- 错误处理：`NoPathError`（找不到路径）与 `ConversionError`（无法识别、转换失败）
- 取消任务：`job.cancel()`
- 环境诊断：哪些插件因缺依赖没有启用

## `custom_plugin.py`

实现一个虚构的位图格式 CCDEMO，注册进私有 `Registry`，然后真的跑
PNG → CCDEMO → JPEG。文件开头的文档字符串写了正式接入项目的三步：
加 `@register`、登记 `_MODULES`、在 `formats.py` 登记 MIME 和扩展名。

更多参考：[README.md](../README.md) 的「扩展」一节，以及 [convcore/plugins/](../convcore/plugins/) 下的现有插件。
