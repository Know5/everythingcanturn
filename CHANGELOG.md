# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **JPEG XL（可选）**：`pip install -e ".[jxl]"` 后通过 Pillow 插件读写 `.jxl`；
  未装时 JXL 不作为目标出现。支持 lossless 预设与 effort 参数
- **音视频拼接**：`convcore convert a.mp4 b.mp4 -t mp4 --concat -o joined.mp4`，
  或调用 `convcore.plugins.media.concat_media`。同编码走 ffmpeg concat demuxer
  的 `-c copy`，不重编码；编码/流布局不一致时自动回退到 H.264/AAC 中间文件。

### Changed
- **桌面端界面重写**：React 18 + TypeScript + Ant Design 5 + Vite，源码在 `ui/`，
  产物落到 `convcore/webui/static/gui/`，由 `convcore.spec` 原有的 `datas` 自动收集。
  旧内联界面（`desktop.html/css/js`）与 `CONVCORE_LEGACY_UI` 回退开关已删除；
  构建产物缺失时直接报错并给出 `npm ci && npm run build` 指引。设计 token 取 Radix Colors 暗色阶梯，
  列表行自绘而非直接拼 AntD 默认组件；行为对齐旧版（拖放、按域分组的目标选择、
  损失档位、逐文件进度、批量统一目标、预设、输出目录、引擎安装、格式矩阵）。
  详见 `docs/superpowers/specs/2026-10-29-desktop-ui-react-antd.md`
- **桌面 UI 产品化重构**：平铺列表 + 底栏改为“队列卡片 + 右侧任务面板”。
  空状态重做为居中拖放区；目标选择器改成可搜索的双列格式列表；任务面板集中
  统计、输出目录、质量预设与主操作；引擎/帮助弹层统一为同一套信息层级。
  同时补充窄窗口适配与暗色分层 token
- 文件卡片支持图片/PDF 缩略图与拖拽排序；空状态显示最近完成记录
  （localStorage，可打开/定位/重新加入）；新增 `Ctrl+O` 添加文件、
  `Ctrl+Enter` 开始转换与卡片进场动效
- 应用图标统一使用 `convcore/assets/convcore.ico`（窗口、exe、favicon）；
  图标源文件是 512px PNG，打包时生成多尺寸 ICO
- 改为无边框窗口：移除原生标题栏，应用内顶栏成为唯一品牌行（图标 + convcore
  + 本地文件转换 + 窗口控制）；加载后补回 `WS_THICKFRAME`，保留原生边缘缩放
- `convcore gui` 新增 `--ui-dev URL`，可指向 Vite 开发服务器做前端热更新
- 桌面端与浏览器端的状态机合并到 `convcore/uiservice.py`：`inspect`、逐文件任务、
  进度/取消、环境探测只维护一份；两个前端只保留传输适配（js_api / HTTP）
- 图搜索删掉未使用的 DIRECT 直连边；所有插件统一走 `Codec` 枢纽，
  remux 仍由媒体域 `MediaPlan` 在写出时判定
- **PDF→Word 版面增强**：新增 `pdf_table_mode`（auto/lattice/stream/none），
  默认 auto 在已有带框表格时关闭 stream 检测，避免公式/线条被误判成表格；
  实测 15 页论文的表格数从 26 降到 2，生成时间从 106s 降到 96s。
  新增 `pdf_heading_styles`，按字号/字重把推断标题映射为 Word Heading 1–3，
  保留直接格式并让导航窗格/目录可用。CLI 对应 `--pdf-table-mode` 与
  `--no-pdf-heading-styles`
- 新增 `benchmarks/` 能力基准：公开 arXiv 论文、IRS W-9、CC0 扫描件样本，下载脚本与
  `pdf_word_bench.py` 记录文本召回、标题/表格/图片/文本框数量与耗时；
  样本和产物只写入项目目录（`benchmarks/samples`、`benchmarks/work`，均不入库）
- **扫描件 OCR**：可选 Tesseract（`[ocr]` extra + 本机引擎），扫描 PDF/图片可转
  Markdown / Word / 纯文本；图搜索在内容约束下会重搜 OCR 路径，而不是只删掉
  `requires text` 的 pdf2docx 路径。CLI 支持 `--ocr-lang` / `--ocr-dpi`，
  `doctor` 会报告 Tesseract 状态
- **文档图片链路**：PDF→Markdown 保留内嵌图（data URI）；Markdown/HTML→DOCX 的
  data URI/本地图片真正嵌入 `word/media`；DOCX 解析提取内嵌图；EPUB 写出把图片
  打包进 `OEBPS/images` 并登记 manifest。表格/页眉页脚图片与 EPUB 解析图片仍待补

### Fixed
- CI 一直红：`pip install .[all,dev]` 会拉到 PyMuPDF 2.0 / Pillow 13 / ReportLab 5.1，
  其中 PyMuPDF 2.0 的 `find_tables` 让 PDF→Markdown 表格丢失，渲染基线也变慢。
  CI/release 改为用 `requirements.lock.txt` 约束依赖，并把 PyMuPDF 锁在 1.x；
  锁文件为 Python 3.11 补了 numpy/puremagic 环境标记
- 拖入文件被 WebView2 当成导航目标打开：全局 `dragover`/`drop` 的 `preventDefault`
  现在由 `main.tsx` 强制的 `bridge` 模块注册（应用级不变量，不再依赖某个组件恰好引用）
- 桌面窗口加载 file:// 界面时不能带查询串：.NET `Uri` 会把 `?` 转义成 `%3F`，
  导航到不存在的文件名（P0 实测）；防旧资源改由内容 hash 文件名承担
- README 的能力数字与代码对齐（54 种双向、9 种只读；SVG 已可写），PDF 表格提取说明
  改为跨页收集所有表格；桌面端 API 契约测试补全 `cancel_all` / `open_file` / `open_url` /
  `formats_data` 四个方法

### Removed
- 删除 P0 验收后遗留的 `ui/spike/spike.py`，以及 `test_samples/` 中未被任何测试引用的
  5 个样本（保留 LibreOffice 用例使用的 `b.docx`）
- 删除 `engines/codecs.py` 中没有调用方的 `supported()`
- 删除 `registry.DirectConverter`、`EdgeKind.DIRECT` 及 `Codec` 之外的直连插件分支
  （生产插件 0 个实现，整条分支无调用方）
- `convcore/ir/__init__.py` 不再重复导出子模块类型，调用方直接导入具体 IR 子模块

### Added
- Raster → vector tracing bridge (`vtracer`, included in the `vector` and `all` extras):
  icons, logos and line art to SVG at the `INFERRED` tier. Photos are rejected by the entry
  heuristic with a hint to `--force`; a forced trace succeeds with a hard warning.
  `refine_targets` probes raster sources with the same heuristic, so photos do not even list
  SVG as a target
- Bridge dependency gates: `raster → vector` requires `vtracer`, `vector → raster` requires
  a renderer. `convcore formats` domain reaches, the desktop help panel and a new
  `convcore doctor` section report the gate state; `tests/test_raster_to_vector.py` covers
  the gate, the heuristic and the end-to-end conversions
- PDF → Markdown keeps tables now: `find_tables` detects ruled tables first and falls back to
  text alignment with a shape filter for line-less ones (plain prose is no longer mistaken
  for a table), and text inside table boxes is not duplicated as paragraphs
- PDF structure operations clone the document when it is copied whole, preserving bookmarks,
  attachments and forms; page selection / rotation report the document-level structure they
  drop. DocTree → PDF embeds images (local files and data URIs; a searchable placeholder is
  left otherwise) and renders code in Courier when it is ASCII, and tables now use
  content-based row heights with `colspan` merging (`rowspan` warns instead of misplacing)
- `table → PDF` exports every sheet with its name, and PDF table extraction collects every
  table across pages instead of only the first one

### Fixed
- Vorbis quality was mapped with the LAME scale, so `quality: 100` produced the worst setting.
  Vorbis `-q:a` runs -1..10 with higher being better; the default is now `-q:a 6`
- EXIF orientation was applied to the pixels but the tag was passed through unchanged, so
  viewers rotated phone photos a second time. The tag is stripped after transposing, other
  EXIF fields are kept
- An explicit `fps` was silently ignored whenever the video stream could be copied; it now
  forces a video transcode, and `decide()` reports the matching loss tier instead of COPY
- GIF went through Pillow's `convert("P")`, which uses the WEB 216-colour palette and
  posterises gradients; frames now share one adaptive median-cut palette (both the static and
  the animated path)
- The HEIC/AVIF writers kept only the first frame of animated sources; they now export every
  frame (or the first one with a warning under the DROP policy)
- mpeg4 (AVI) had no default quality argument and fell back to ffmpeg's ~200 kbps default
- Incompatible subtitle streams were dropped instead of transcoded (mp4/mov → mov_text,
  WebM → webvtt, others → srt)
- AV1 encodes lacked `-b:v 0` (so CRF was clamped) and any speed control
- FFmpeg engine install was broken in CI: the pinned 9.0.1 build had been removed from
  gyan.dev's package directory (HTTP 404). The pin is now the previous release (8.1.2), whose
  URL is permanent, with the published SHA256 verified against the downloaded archive;
  `version_url` reports the upstream release so `engines check` still shows updates
- `puremagic>=2.0` requires Python >= 3.12, which contradicted the declared
  `requires-python = ">=3.11"` and broke dependency installation on 3.11. The floor is now
  `>=1.30` (verified against both 1.30 and 2.2 locally)

### Changed
- `SvgCodec` no longer depends on a renderer: reading and writing SVG, and SVG → HTML, work
  without resvg. Only rasterization is gated on a renderer
- Icon-like PNG sources now reach HTML / Markdown / EPUB through tracing (the SVG gets
  embedded) instead of the text-extraction route that produced empty documents; video/PDF
  sources may still be rejected at the tracing entry, which the reachability test documents
  as an accepted runtime behaviour

- Presets now cover video and audio through `video/*` / `audio/*` wildcards: `-p web`,
  `-p compact` and `-p archive` previously had no effect on media files at all
- JPEG defaults to 4:2:2 chroma subsampling instead of Pillow's 4:2:0 (4:4:4 and 4:2:0 stay
  available through presets and options); `archive` WebP is `lossless="auto"` — lossless only
  when the source itself is lossless, high-quality lossy otherwise
- Audio bitrate defaults scale with channel count (448k above stereo), x264/x265 default CRF
  is 20/22, AVIF defaults to `speed=4, quality=80`
- Media conversions pass `-map_metadata 0 -map_chapters 0` by default and clear them when
  `keep_metadata` is false

- Loss tiers corrected where the writers simplify: Markdown (merged cells, cell formatting),
  DOCX (images become placeholder text) and EPUB (images stay external references) are
  `STRUCTURAL` instead of `LOSSLESS`. Tiers feed path ranking, so overstating them misled
  route selection

## [0.2.0] - 2026-10-08

### Security
- Web UI (`convcore ui`) now rejects requests whose `Host` header is not
  `127.0.0.1:<port>` or `localhost:<port>`. Previously a DNS-rebinding page could
  read the token from `/` and call every API. See `SECURITY_REVIEW.md` (2026-09-29).

### Added
- `tests/test_webui.py`: integration tests against a real server for token, Origin and Host checks
- Content constraints in path search: bridges declare what their output lacks (`BRIDGE_LACKS`),
  edges declare what they need (`BRIDGE_REQUIRES`, `Capability.requires`), and
  `Converter.refine_targets` probes what a specific file lacks
- `Registry.refresh()` re-evaluates plugin availability
- `tests/test_reachability.py`: converts every listed target of each sample source end to end

### Changed
- Images, video frames and cover art are no longer offered as Word targets. That path wrapped
  the picture in a .docx through pdf2docx, contradicting pdf2docx's own scanned-PDF preflight;
  now that image-only PDFs are detected as scanned, it would be rejected anyway

### Fixed
- Plugins called directly (library use, debug scripts, tests) no longer write intermediates into
  the current directory. Without `_tmp` the code fell back to `.`, so `built.pdf`, `out.pdf`,
  `table.pdf` and `images.pdf` could land in the repo root; the fallback is now a process-level
  scratch directory under the OS temp, removed at exit
- Conversion lists no longer offer targets that cannot work. A sweep converting every listed
  target of 17 sample sources found 288 of 698 failing or producing empty files. Now 471 are
  listed and 461 work; the other 10 depend on content (HTML without a table → CSV) or are
  checker false positives. Examples of removed targets: PNG → CSV / JSON / Markdown,
  PNG → MP3, WAV → PNG, silent video → MP3, scanned PDF → Markdown, SVG → plain text / Word / CSV
- `refine_targets` now returns only the targets the file can reach; callers iterate its result.
  Code that indexes the result with every key of `targets_for(...)` must switch to iterating it
- PDFs built from images now report their images, so scanned-page detection works on them
  (PNG → HTML used to "succeed" with an empty file)
- Audio without cover art → image now fails with a clear message instead of an ffmpeg exit code
- Odd-sized frames no longer break libx264 / libx265 / wmv2. Documents and data → MP4 / MKV /
  MOV / FLV / MPEG-TS always failed before, because rendered pages are 1240×1755
- Transparent sources to WebM no longer fail (VP9 now gets `-pix_fmt yuv420p` instead of `gbrap`)
- Desktop GUI: installing an engine takes effect immediately; previously it needed a restart
- Web UI: a rejected POST (403) no longer resets the connection before the client reads the
  response. The handler now reads the request body before its checks
- Examples now perform real conversions on generated samples; previously every conversion
  call was commented out and used APIs that do not exist (`conv.convert(src, dst)`, `conv.batch`)
- README rewritten where it disagreed with the code: Python API, CLI examples, architecture,
  plugin interface, bridge table, limitations and the extension guide
- Corrected three 0.1.0 entries below that described behaviour the code never had

## [0.1.0] - 2026-09-13

### Fixed
- Restored `_tmp` parameter passing to fix bridge and plugin temporary directory access
- Fixed user-specified frame rate being ignored in animated image to video conversion

### Added
- Core conversion engine with hub-and-spoke architecture
- Format graph with Dijkstra path finding
- 9 domain support: raster, vector, media, document, markup, table, tree, archive, font
- 53 formats with verified read and write, plus 10 read-only formats
- 14 codec plugins with lazy loading
- Cross-domain bridges:
  - media ↔ raster (video frame extraction and image sequence to video)
  - table → document (table rendering to PDF)
  - document → table (PDF table extraction)
  - document → markup (PDF text extraction with heading inference)
  - vector → markup (SVG embedding in HTML)
  - table ↔ tree (bidirectional table/JSON conversion)
  - tree → markup (JSON/YAML rendering as readable documents)
  - markup → document (DocTree layout to PDF)
  - raster → document (images to PDF pages)
- GUI desktop application with drag-and-drop interface
- CLI with batch conversion and preset support
- Python API for programmatic usage
- External engine integration: FFmpeg, LibreOffice, resvg
- Comprehensive test suite (273 passing tests, 71% coverage)
- Security hardening: path traversal protection, zip bomb detection, command injection prevention
- Progress tracking and cancellation support
- Policy system for quality presets
- Concurrent batch conversion: `JobQueue`, GUI and Web UI create one `Converter` per task
  (corrected: originally listed as "thread-safe converter"; `Converter` has no internal locking)
- Parquet format support in data domain
- Dynamic loss tier refinement based on file content
- Input size cap: non-media inputs over 1 GiB are rejected
  (corrected: originally "large file memory protection"; there is no streaming or memory-pressure detection)
- ~~Automatic fallback when external engines fail~~ (corrected: never implemented; engine failures raise an error)

### Documentation
- Comprehensive README with architecture overview
- Security review documentation
- Installation guide with optional extras
- API usage examples
- Format compatibility matrix

### Infrastructure
- PyInstaller packaging for Windows
- Optional dependency groups for modular installation
- Coverage reporting with pytest-cov
- Git repository with 15+ commits tracking development progress

[Unreleased]: https://github.com/Know5/everythingcanturn/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Know5/everythingcanturn/releases/tag/v0.2.0
[0.1.0]: https://github.com/Know5/everythingcanturn/releases/tag/v0.1.0
