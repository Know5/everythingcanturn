# 已知问题与后续建议

> 记录截至 2026-09-29 的状态。用于交接给下一个会话或贡献者，避免重复排查。
> 安全的优先级分析见 [SECURITY_REVIEW.md](SECURITY_REVIEW.md)。
> 本文件和 README 描述的能力都应能在代码里找到对应；对不上时以代码为准，并回来修文档。

## 一、测试覆盖率短板

当前覆盖率 **73%**（6706 语句，1797 未覆盖）。除以下模块外，其余均 ≥85%。

| 模块 | 覆盖率 | 原因 |
|------|--------|------|
| `cli.py` | 0% | 命令行入口。`main(argv)` 可直接调用，CI 冒烟测试走的也是 CLI，只是还没写 pytest 用例 |
| `webui/server.py` | 68% | `tests/test_webui.py` 起真实服务测安全边界和转换主路径；未覆盖的是原生选择框、reveal 等系统集成 |
| `engines/install.py` | 50% | 大量分支依赖真实网络下载（HTTPS/SHA256/解压），本地无法触发 |
| `plugins/office_soffice.py` | 51% | 依赖外部 LibreOffice（soffice），当前环境未装 |
| `desktop/app.py` | 52% | pywebview 原生窗口，需 GUI 环境 |
| `plugins/image_extra.py` | 54% | 大量失败分支依赖可选包（pillow-heif/rawpy 等）未安装 |

> 这些低覆盖绝大多数是**外部依赖/界面**触发的分支，属于"装了才有、不装就没有"的场景。
> 但 `webui/server.py` 在 0% 时藏过一个真实的 P1（不校验 Host，见 SECURITY_REVIEW），
> 所以「低覆盖 ≠ 没缺陷」。CLI 补测同样应写真实调用的集成测试而非 mock。

## 二、功能缺口（按影响排序）

1. **位图 → 矢量（raster → vector）桥接未实现**
   桥接表 `BRIDGES` 只有 `vector → raster`（栅格化）。反向的 `raster → vector`（描摹）
   当前无实现，图搜索不会搜出这条路径，README 已如实标注。设计已批准，在
   `feature/raster-to-vector` 分支：`docs/superpowers/specs/2026-09-20-raster-to-vector-design.md`。

2. **JPEG XL（image/jxl）未支持**
   `formats.py` 明确留言"JPXL 留在表外：无插件实现"。要支持需引入
   `pillow-jxl-plugin` 并加回格式表。

3. **音视频多文件合并（concat）未支持**
   `media.py:parse` 里 `len(resources) > 1` 直接抛错，注释写明"后续会加 concat 支持"。

4. **扫描件 OCR 未集成**
   `document_word.py` 的 `_preflight` 会拦截无文本层的 PDF，提示需要 OCR。
   当前未集成 Tesseract 中文包（约 50MB）。

5. **PSD 只读、RAW 只读**
   `PsdCodec`/`RawCodec` 的 `writes = ()`，只能作为源转出，不能作为目标。

## 三、可选依赖/引擎未装（装了即恢复）

| 依赖 | 影响 |
|------|------|
| LibreOffice（soffice） | DOC/PPT/XLS/PPTX/ODT/ODS 读写不可用 |
| pillow-heif | HEIC 读写不可用（iPhone 照片） |
| psd-tools | PSD 读取不可用 |
| rawpy | 相机 RAW 读取不可用 |

外部引擎状态：**FFmpeg 9.0.1 ✓、UnRAR ✓、soffice ✗**。
未装依赖的插件不会破坏引擎启动——`available()` 返回原因并跳过，`doctor` 会列出。

## 四、代码质量问题（非阻塞）

- **`webui/server.py` 的 `make_handler` 有 151 行**（闭包内含路由分支），是本项目
  最长的函数。可拆成独立 handler 类，但不影响运行。
- **`find_path`（graph.py）112 行**，内含 Pareto frontier 的复杂去重逻辑，
  是图搜索的核心，修改需谨慎，已有专门测试（`test_graph.py`）。
- 极少数 `pass` 语句（共 22 处）均为"尽力而为"的防御分支（如标注失败不该中断转换），
  无裸 `except:`。

## 五、文档曾与代码脱节（2026-09-29 已修正）

提交 `4a3aa49`「feat: 完成所有跨域桥接功能」只改了 README，却把「限制」一节从正确描述
改成了代码没有的行为（引擎失败自动回退、500MB 流式处理、`Converter` 线程安全），
桥接表写了不存在的 raster→vector（potrace）以及没用到的 reportlab、camelot。
`d38837b` 的 examples 声称「已验证可运行」，实际所有转换调用都被注释，且用的 API 不存在。

已按代码实况修正 README、CHANGELOG、examples。之后改文档时，能力描述要能指到具体代码或测试。
