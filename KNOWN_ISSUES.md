# 已知问题与后续建议

> 记录截至 2026-09-14 的收尾状态。用于交接给下一个会话或贡献者，避免重复排查。
> 安全的优先级分析见 [SECURITY_REVIEW.md](SECURITY_REVIEW.md)。

## 一、测试覆盖率短板

当前覆盖率 **71%**（6699 语句，1975 未覆盖）。除以下模块外，其余均 ≥85%。

| 模块 | 覆盖率 | 原因 |
|------|--------|------|
| `cli.py` | 0% | 命令行入口，子函数多为 `argparse` 驱动的 I/O 逻辑，难用 pytest 覆盖 |
| `webui/server.py` | 0% | HTTP 服务端，需 `ThreadingHTTPServer` 起真实服务测试 |
| `engines/install.py` | 50% | 大量分支依赖真实网络下载（HTTPS/SHA256/解压），本地无法触发 |
| `plugins/office_soffice.py` | 51% | 依赖外部 LibreOffice（soffice），当前环境未装 |
| `desktop/app.py` | 52% | pywebview 原生窗口，需 GUI 环境 |
| `plugins/image_extra.py` | 56% | 大量失败分支依赖可选包（pillow-heif/rawpy 等）未安装 |

> 这些低覆盖绝大多数是**外部依赖/界面**触发的分支，属于"装了才有、不装就没有"的场景，
> 并非逻辑缺陷。CLI/WebUI 若需补测，应写 `subprocess`/`http` 集成测试而非 mock。

## 二、功能缺口（按影响排序）

1. **位图 → 矢量（raster → vector）桥接未实现**
   桥接表 `BRIDGES` 只有 `vector → raster`（栅格化）。反向的 `raster → vector`（描摹）
   在 README 里标为 INFERRED，但当前无插件实现。图搜索不会搜出这条路径。

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

## 五、待推送

`main` 领先 `origin/main` **8 个提交**，尚未 push。

```
b128707 文档增强：为核心类型和函数添加 docstring
75206e3 chore: 将覆盖率报告加入 .gitignore
d38837b docs: 补充项目文档与示例代码
ad43077 恢复 _tmp 参数传递以修复桥接和插件的临时目录访问
502d065 修复动图转视频时用户指定帧率被忽略的问题 + 移除 _tmp 参数传递
4a3aa49 feat: 完成所有跨域桥接功能
2bc085f 扩展跨域桥：media↔raster、table↔document、vector→markup
b32b6b1 树形标记跨域桥 + GUI 损失档位显示 + 大文件预检 + 线程安全改进
```
