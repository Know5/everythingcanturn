# 已知问题与后续建议

> 记录截至 2026-10-30 的状态。用于交接给下一个会话或贡献者，避免重复排查。
> 安全的优先级分析见 [SECURITY_REVIEW.md](SECURITY_REVIEW.md)。
> 本文件和 README 描述的能力都应能在代码里找到对应；对不上时以代码为准，并回来修文档。

## 一、测试覆盖率短板

本机全依赖环境最近一次全量统计：约 **80%**（399 passed / 4 skipped）。
低于 85% 的主要模块：

| 模块 | 覆盖率 | 原因 |
|------|--------|------|
| `cli.py` | 67% | 命令分发的集成测试已补；`engines install/check` 的网络分支仍未覆盖 |
| `uiservice.py` | 78% | 共享 UI 状态机；缩略图只测了位图分支，取消/引擎安装的边角分支仍少 |
| `webui/server.py` | 66% | `tests/test_webui.py` 起真实服务测安全边界和转换主路径；未覆盖原生选择框等系统集成 |
| `desktop/app.py` | 44% | pywebview 原生窗口，需 GUI 环境；纯逻辑已移入 `uiservice.py` |
| `engines/install.py` | 50% | 大量分支依赖真实网络下载（HTTPS/SHA256/解压），本地无法触发 |
| `plugins/document_word.py` | 80% | pdf2docx 集成 + 标题/表格策略；失败分支与复杂版面组合仍未覆盖 |
| `plugins/office_soffice.py` | 52% | 依赖外部 LibreOffice（soffice），当前环境未装 |
| `plugins/image_extra.py` | 68% | 大量失败分支依赖可选包（pillow-heif/rawpy 等）未安装 |
| `plugins/archive.py` | 59% | 各归档格式的失败分支与外部后端 |
| `plugins/vector.py` | 75% | 依赖 resvg / vtracer 与排版边界 |
| `ir/data.py` / `ir/document.py` | 72% / 71% | 类型推断与版面分析的边角分支 |
| `engines/codecs.py` | 79% | 容器/编码兼容表的兜底分支 |

> 这些低覆盖绝大多数是**外部依赖/界面**触发的分支，属于"装了才有、不装就没有"的场景。
> 但 `webui/server.py` 在 0% 时藏过一个真实的 P1（不校验 Host，见 SECURITY_REVIEW），
> 所以「低覆盖 ≠ 没缺陷」。CLI 补测同样应写真实调用的集成测试而非 mock。

## 二、功能缺口（按影响排序）

1. ~~**位图 → 矢量（raster → vector）桥接未实现**~~ **已实现（2026-10-08）**：
   `vtracer` 描摹，图标/线稿可 PNG→SVG（INFERRED；照片默认拒绝，`--force` 强制）。
   遗留边界：视频/PDF 源经「先渲染再描摹」到达矢量目标的路径无法在 refine 阶段
   廉价预检，运行时可能被描摹入口的启发式拒绝——这是设计接受的行为，与
   「只有段落的 HTML → CSV 抽不到表格」同类的运行时内容依赖。

2. ~~**JPEG XL（image/jxl）未支持**~~ **已支持（2026-10-30，可选插件）**：
   `pip install -e ".[jxl]"` 安装 pillow-jxl-plugin 后，JXL 可读可写，图搜索自动接通；
   未装时不作为目标列出。

3. ~~**音视频多文件合并（concat）未支持**~~ **已支持（2026-10-30）**：
   CLI `--concat` 与 `plugins.media.concat_media`。同编码走 ffmpeg concat demuxer
   的 `-c copy`（无损），编码/流布局不一致时回退到 H.264/AAC 中间文件再转换。
   遗留：GUI 尚未暴露 concat；混合分辨率/帧率会按回退重编码处理。

4. ~~**扫描件 OCR 未集成**~~ **已集成（2026-10-30，可选 Tesseract）**：
   扫描 PDF / 图片可 OCR → Markdown / Word / 纯文本。需要 `pip install -e ".[ocr]"`
   + 本机 Tesseract；中文需 `chi_sim` 语言包。输出固定 INFERRED，段落按空行切分，
   错字/表格/标题层级需复核。

5. **PSD 只读、RAW 只读**
   `PsdCodec`/`RawCodec` 的 `writes = ()`，只能作为源转出，不能作为目标。

6. **图片 → Word 不可用**
   以前能「转」，实际是 pdf2docx 把图片原样塞进 .docx，与它自己拒绝扫描件的预检矛盾，
   2026-09-29 起不再列出。要支持应写一个 raster → DOCX 的直接写出（python-docx 插图），
   而不是借道 PDF 版面还原。

7. **经 PDF 抽表格，结果取决于内容**
   HTML、DOCX 等 → CSV / Excel 仍会列出：有没有表格要抽了才知道，事先探测等于做一遍转换。
   抽不到时报「PDF 中未找到可提取的表格」（实测只有标题和段落的 HTML 就是这样）；
   纯文本则常被 pdfplumber 当成单列表格抽出来。

8. **图片在文档链路里基本打通**
   PDF→Markdown 保留内嵌图（data URI）；DOCX 解析/写出都处理内嵌图；EPUB 写出把
   图片打包进 `OEBPS/images` 并登记 manifest。仍缺：表格单元格/页眉页脚里的图片，
   以及 EPUB 解析时把章节图片映射回 data URI。排版侧（DocTree→PDF）已支持嵌入。

## 三、可选依赖/引擎未装（装了即恢复）

| 依赖 | 影响 |
|------|------|
| LibreOffice（soffice） | DOC/PPT/XLS/PPTX/ODT/ODS 读写不可用 |
| pillow-heif | HEIC 读写不可用（iPhone 照片） |
| psd-tools | PSD 读取不可用 |
| rawpy | 相机 RAW 读取不可用 |
| vtracer | 位图 → SVG 描摹不可用（PNG/JPG→SVG 不再出现在目标列表） |
| Tesseract | 扫描 PDF / 图片 OCR 不可用（扫描件不列 Word/Markdown） |

外部引擎状态（本机实测）：**FFmpeg 8.1.2 ✓、UnRAR ✓、Tesseract ✓、soffice ✗**；以 `convcore doctor`
的输出为准，版本号钉在 `convcore/engines/manifest.py`。
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
