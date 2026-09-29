# everythingcanturn

**任意相关文件格式互转** — 一个本地运行、53 种可双向互转格式的转换引擎。

> 关于格式数量的诚实说明：这里说「53 种可双向互转」，是**实际验证过读和写都通**的
> 数量。另有 10 种格式只能读不能写（相机 RAW、PSD、SVG、RAR——它们作为源能转出，
> 但作为目标不承诺）；6 种 Office/ODF 格式（DOC/XLS/PPT/PPTX/ODT/ODS）的读写
> 依赖可选的 LibreOffice 引擎，未装时不可用，装了自动恢复。转换器不会展示「能识别
> 但转不了」的目标——列出来的都是真实可达的。

## 原理速读

文件不是信息本身，只是信息在某种约定下的投影。转换的本质是**还原成信息模型，再换约定重新投影**。

```
PNG → [像素矩阵] → JPEG
DOCX → [文档树] → Markdown
MP4 → [视频帧流+音频流] → WebM
```

关键设计：

- **中心枢纽架构**（hub-and-spoke）：N 种格式不写 N² 个转换器，只需 N 个 reader + N 个 writer。新增格式成本是 O(1)。
- **格式图搜索**：多跳路径自动计算，`DOC →(LibreOffice)→ DOCX →[文档树]→ Markdown` 不需要手写。
- **显式损失标记**：每条边带权重（信息损失档位），路径规划时优先选保真度最高的。

支持的信息域：

| 域 | 中间表示 | 典型格式 |
|---|---|---|
| **光栅图** | 像素矩阵 + 色彩空间 | PNG, JPEG, WebP, HEIC, AVIF, BMP, TIFF, PSD |
| **矢量图** | 路径+填充+变换 | SVG |
| **音视频** | 解码后的帧流 | MP4, MKV, WebM, MOV, AVI, MP3, FLAC, WAV |
| **文档** | 带样式的段落树 | PDF, DOCX, Markdown, HTML, EPUB, TXT |
| **数据** | 树/表结构 | JSON, YAML, TOML, XML, CSV, XLSX, TSV |
| **归档** | 文件列表+压缩流 | ZIP, 7z, TAR, RAR, GZ, BZ2, XZ |
| **字体** | 字形轮廓+度量 | TTF, OTF, WOFF, WOFF2 |

## 安装

Python 3.11+ 环境（已测 3.13）：

```bash
git clone <repo>
cd everythingcanturn
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
```

基础安装包含常见光栅图像和格式探测能力，其余格式按需安装插件依赖：

```bash
# 常用的图片、文档、数据、归档和字体支持
pip install -e ".[image,document,data,archive,font]"

# 一次安装全部 Python 插件依赖（会包含 OpenCV，体积较大）
pip install -e ".[all]"
```

可用 extras：`image`、`raw`、`vector`、`document`、`pdf2docx`、`data`、
`archive`、`font`、`desktop`。这些 extras 只安装 Python 包；FFmpeg、LibreOffice、
resvg 可执行文件和 RAR 的 UnRAR 后端仍需按下方说明单独安装。

也可以用 PyInstaller 打成 Windows 可执行包（onedir，约 254MB，含所有 Python 依赖），见下文「打包」：

```
dist/convcore/
  convcore.exe      # GUI
  convcore-cli.exe  # 命令行
```

**外部引擎**（可选，按需安装）：

- **FFmpeg**：音视频转换必需。`convcore engines install ffmpeg` 一键下载（版本和 SHA256 钉在
  `convcore/engines/manifest.py`），或自行[下载](https://www.gyan.dev/ffmpeg/builds/)放 PATH / 与 exe 同目录。
- **resvg**：SVG 转位图。可优先安装 `pip install -e ".[vector]"` 使用 Python 绑定，
  也可以使用 [resvg 单文件](https://github.com/linebender/resvg/releases)。
- **rawpy**：相机 RAW 格式（CR2/NEF/ARW）。`pip install -e ".[raw]"`。

## 使用

### 命令行

```bash
# 基础转换
convcore convert photo.heic -t jpeg

# 批量：产物平铺到 -d 目录，同名产物自动消歧（photo.webp / photo-bmp.webp）
# 通配符由 shell 展开：bash/zsh 可以；PowerShell 和 cmd 不展开，需逐个列出文件
convcore convert docs/*.pdf -t docx -d ./output

# 单文件指定输出路径
convcore convert photo.png -t webp -o out/photo.webp

# 参数预设（original / web / compact / archive）或单项参数
convcore convert video.mov -t mp4 -p web
convcore convert photo.png -t jpeg -q 85 --max-edge 1920

# 探测可达目标、查看路径与损失等级
convcore targets mystery.bin
convcore path photo.heic pdf

# 环境诊断：缺哪些依赖、哪些引擎没装
convcore doctor
```

### GUI

双击 `convcore.exe` 打开窗口：

1. 拖文件进来，自动识别格式并列出可转选项（按域分组、保真度排序）
2. 选目标格式，点转换
3. 实时进度条，完成后可一键定位产物或直接打开

### Python API

```python
from pathlib import Path

from convcore import Converter, convert_file
from convcore.jobs import JobQueue

# 单文件：目标可写扩展名或 MIME，产物落到 out_dir
res = convert_file(Path("input.png"), "webp", out_dir=Path("out"))
print(res.outputs[0].path, res.loss.label, res.warnings)

# 批量 + 进度回调：并发执行，同名产物自动消歧
q = JobQueue(listener=lambda job: print(f"{job.progress:4.0%} {job.source.name} {job.state.value}"))
q.add_files([Path("a.heic"), Path("b.png")], "jpeg", out_dir=Path("out"))
q.run()

# 探测能力：目标 MIME → 转换路径（含损失等级）
targets = Converter().targets_for("image/heic")
for mt, path in targets.items():
    print(mt, path.loss.label, path.describe())
```

`convert_file` 的常用参数：`out_path`（精确输出路径）、`preset`、`overrides`
（如 `{"quality": 85, "max_edge": 1920}`）、`overwrite`、`progress`。
底层接口是 `Converter.convert(ConvertRequest(...))`，处理内存中的 `Resource`。

## 架构

```
convcore/
├── types.py          # 核心类型：Resource、LossTier、Capability、异常
├── formats.py        # MIME ↔ 扩展名 ↔ 域 映射表
├── sniff.py          # 魔数嗅探，扩展名只作回退
├── registry.py       # 插件注册表（@register）
├── graph.py          # 格式图 + 跨域桥表 + 字典序代价的最短路搜索
├── pipeline.py       # 执行器：Converter / convert_file
├── policy.py         # 降级策略与参数预设
├── jobs.py           # 批量任务队列：并发、进度、取消
├── ir/               # 各域中间表示：raster / document / markup / media / data
├── engines/          # 外部引擎：探测、子进程调用、ffmpeg 封装、下载安装
├── plugins/
│   ├── image.py          # Pillow：PNG/JPEG/WebP/GIF/TIFF/BMP/ICO/PPM/TGA
│   ├── image_extra.py    # 可选包：HEIC/AVIF、PSD（只读）、RAW（只读）
│   ├── vector.py         # SVG 读入；桥：栅格化、嵌入 HTML
│   ├── media.py          # 音视频：容器互转、转码、抽音轨；桥：截封面帧
│   ├── media_image_seq.py  # 桥：图片序列 / 动图 → 视频
│   ├── document*.py      # PDF：渲染、分页操作、PDF→Word、排版建 PDF、表格提取
│   ├── markup*.py        # MD/HTML/TXT ↔ 文档树；桥：PDF 抽文本、数据树渲染
│   ├── office.py         # DOCX、EPUB ↔ 文档树
│   ├── office_soffice.py # LibreOffice：DOC/XLS/PPT/PPTX/ODT/ODS
│   ├── data_tree.py      # JSON/YAML/TOML/XML
│   ├── data_table.py     # CSV/TSV/NDJSON/XLSX/Parquet
│   ├── archive.py        # ZIP/7z/TAR/GZ/BZ2/XZ，RAR 只读
│   └── font.py           # TTF/OTF/WOFF/WOFF2
├── cli.py            # 命令行入口
├── webui/            # 标准库 http.server 本地服务（convcore ui）+ 前端静态资源
└── desktop/          # pywebview 原生窗口（convcore gui），经 js_api 调 Python
```

插件接口（`convcore/registry.py` 里的 `Codec` 协议）：

```python
class Codec(Protocol):
    name: str
    domain: Domain                    # "raster" | "media" | "document" | ...
    reads: tuple[Capability, ...]     # 用 caps("image/png", loss=..., time_cost=...) 声明
    writes: tuple[Capability, ...]

    def parse(self, resources: list[Resource], options: dict) -> IR:
        """资源 → 本域中间表示。列表是为了支持多进一出。"""

    def write(self, ir: IR, target: MediaType, options: dict) -> list[Resource]:
        """中间表示 → 资源。返回列表是为了支持一进多出。"""

    def available(self) -> tuple[bool, str]:
        """可选：依赖是否就绪。不就绪的插件不进图，原因显示在 doctor 里。"""
```

`options` 里除了用户参数，还有执行器注入的 `_tmp`（工作目录）、`_policy`（记录降级告警）、
`_progress`、`_cancel`。同一文件还定义了绕过枢纽的 `DirectConverter` 协议，图搜索支持它，
但目前没有插件用：remux 由媒体域的 `MediaPlan` 在写出时判定，LibreOffice 也是按 Codec 接入的。

## 转换质量

| 损失档位 | 含义 | 示例 |
|---------|------|------|
| **COPY** | 字节级相同 | PNG→PNG |
| **LOSSLESS** | 信息完整保留 | PNG→BMP, JSON→YAML |
| **LOSSY** | 有损但可接受 | PNG→JPEG, WAV→MP3 |
| **STRUCTURAL** | 结构简化 | DOCX→TXT（丢样式） |
| **INFERRED** | 需要推断补全 | PDF→DOCX、PDF→Markdown（段落和标题靠版面推断） |

路径搜索按字典序比较：先比路径上最差的损失档位，再比有损步数，再比保真度损失，
最后才比耗时。保真度排在耗时前面，是为了避免同档位里「最快但最糙」的路径胜出。

静态声明只代表最坏情况。媒体文件会在转换前探测流信息：能免重编码（remux）的目标
显示为实际档位，比如 H.264 编码的 MKV→MP4 显示为 COPY 而不是 LOSSY。

## 测试

```bash
pytest tests/ -v

# 单个文件 / 按名字筛选
pytest tests/test_matrix.py
pytest tests/test_sniff.py -k heic

# 覆盖率
pytest --cov=convcore --cov-report=html
```

依赖可选包或外部引擎（ffmpeg、soffice、pillow-heif 等）的用例在缺依赖时自动跳过。

核心测试策略：

- **样本程序合成**：`tests/conftest.py` 等在测试时生成图片、动图、SVG、PDF 等样本，
  基本不依赖入库的二进制（`test_samples/` 里的少量文件只有 LibreOffice 用例在用）；
  带奇怪 EXIF、非标准编码的真实文件放在手动验收里
- **格式矩阵**：`test_matrix.py` 批量跑格式对，图像用 PSNR/SSIM 验证（`tests/fidelity.py`）
- **往返测试**：A→B→A 验证内容保留，如 JSON→YAML→JSON、MD→HTML→MD、ZIP→7z→ZIP
- **失败路径**：空输入、损坏文件、解压炸弹、截断下载、子进程超时与输出上限
- **Web UI 安全边界**：`test_webui.py` 起真实服务，验证 token、Origin、Host 校验

## 打包

```bash
# Windows exe（onedir 模式，254MB）
pyinstaller convcore.spec

# 测试
dist/convcore/convcore-cli.exe doctor
dist/convcore/convcore.exe  # 启动 GUI
```

体积构成：

- Python runtime + stdlib: 35MB
- OpenCV (pdf2docx 依赖): 85MB
- Pillow + 图像库: 25MB
- PyMuPDF: 18MB
- 其他依赖 + 静态资源: 91MB

## 跨域桥接

桥表在 `convcore/graph.py` 的 `BRIDGES`，分发在 `pipeline.Converter._bridge`：

| 转换方向 | 实现 | 损失档位 | 说明 |
|---------|------|---------|------|
| **vector → raster** | resvg-py / resvg / cairosvg | STRUCTURAL | SVG 栅格化 |
| **vector → markup** | 内置 | LOSSLESS | SVG 嵌入 HTML |
| **document → raster** | PyMuPDF | STRUCTURAL | PDF 渲染成图 |
| **document → markup** | PyMuPDF | INFERRED | PDF 抽文本，按字号推断标题 |
| **document → table** | pdfplumber | INFERRED | PDF 表格提取（取第一个表格） |
| **markup → document** | PyMuPDF | STRUCTURAL | 文档树排版成 PDF |
| **raster → document** | PyMuPDF | LOSSLESS | 每图一页嵌入 PDF |
| **table → document** | PyMuPDF | LOSSLESS | 表格渲染为 PDF |
| **table → markup** | 内置 | LOSSLESS | 渲染成 Markdown / HTML 表格 |
| **table → tree** | 内置 | LOSSLESS | 每行一个对象 |
| **tree → table** | 内置 | STRUCTURAL | 仅限对象数组，嵌套值序列化为 JSON 字符串 |
| **tree → markup** | 内置 | LOSSLESS | JSON/YAML 渲染成可读文档 |
| **media → raster** | ffmpeg | LOSSLESS | 截取封面帧 |
| **raster → media** | ffmpeg | LOSSLESS | 图片序列 / 动图合成视频 |

两端的域都有可用插件时，桥才进图。表里的档位是桥本身的损失，整条路径取各边最差的一档，
比如 PDF→JPEG：桥是 STRUCTURAL，JPEG 写出是 LOSSY，整条路径记 STRUCTURAL。

**位图 → 矢量（描摹）尚未实现**：PNG→SVG 目前没有路径，设计在 `feature/raster-to-vector` 分支。

## 限制

1. **不可逆转换不承诺往返**：PDF→DOCX、PDF→Markdown 这类标 `INFERRED`，转回去得不到原文件。
2. **外部引擎失败不回退**：FFmpeg、LibreOffice 等子进程失败时直接报错并附上引擎输出，
   不会换编码器或换引擎重试。SVG 渲染器按优先级选第一个可用的（resvg-py > resvg > cairosvg），
   选中的失败了也不会换下一个。
3. **输入大小上限**：非音视频输入上限 1 GiB，超过直接拒绝，没有流式处理。文档类会把
   整棵文档树读进内存，大文件的内存占用可能是文件大小的数倍。音视频交给 ffmpeg 流式处理，不受此限。
4. **并发**：`Converter` 内部没有加锁，没有验证过多线程共享一个实例。`JobQueue`、GUI、
   Web UI 都是每个任务新建一个 `Converter()`（约 0.1ms），自己写并发代码时照这个做。
5. **扫描件没有 OCR**：没有文本层的 PDF 转 Word / Markdown 会被拒绝，并提示先做 OCR 或转成图片。

## 扩展

加一个格式要做三步：

1. 在 `convcore/plugins/` 下写插件，类上加 `@register`：

   ```python
   # convcore/plugins/my_format.py
   from PIL import Image

   from ..ir.raster import Frame, Raster
   from ..registry import register
   from ..types import Resource, caps

   @register
   class MyFormatCodec:
       name = "my-format"
       domain = "raster"
       reads = caps("image/x-myformat")
       writes = caps("image/x-myformat")

       def parse(self, resources, options):
           img = decode(resources[0].read_bytes())      # 你的解码逻辑，返回 PIL Image
           return Raster(frames=[Frame(img)], source_type="image/x-myformat")

       def write(self, ir, target, options):
           data = encode(ir.primary.image)              # 你的编码逻辑
           return [Resource(media_type=target, data=data)]
   ```

2. 把模块名加进 `convcore/plugins/__init__.py` 的 `_MODULES`。插件包不会自动扫描目录，
   不登记就不会被导入。
3. 在 `convcore/formats.py` 的 `_TABLE` 登记 MIME、域、扩展名和显示名。不登记的话
   认不出 `.myformat` 源文件（魔数没结论时靠扩展名回退）、CLI 解析不了 `-t myformat`、
   产物也会叫 `.bin`。有魔数的格式再在 `convcore/sniff.py` 里加识别。

之后经由 raster 枢纽的路径（MyFormat → JPEG、PNG → MyFormat、MyFormat → PDF…）自动可用。
完整可运行的例子见 `examples/custom_plugin.py`。

## 安全

本工具在本地运行，不上传文件到外部服务器。处理不可信文件（网上下载的 ZIP、PDF）时已启用路径穿越和解压炸弹防护。外部引擎（FFmpeg/resvg）以子进程隔离运行，崩溃不影响主程序。

核心安全措施：
- **命令注入防护**：所有外部引擎调用使用参数列表，禁用 shell 解析
- **路径穿越防护**：归档解压拒绝 `../`、绝对路径、Windows 盘符
- **解压炸弹防护**：限制总大小 4GB、压缩比 200:1、条目数 50,000
- **资源限制**：子进程超时 600s，stderr 限制 64KB，stdout 限制 8MB
- **临时文件清理**：异常退出时自动清理临时目录
- **输出覆盖保护**：默认拒绝覆盖已存在文件
- **本地 Web UI 防护**（`convcore ui`）：只监听 127.0.0.1，接口要带随机 token，
  校验 Origin，并且只接受 `127.0.0.1` / `localhost` 作为 Host（防 DNS rebinding）

详细评审见 `SECURITY_REVIEW.md`。

## 许可

MIT

---

**为什么不用 Pandoc / ImageMagick / FFmpeg 直接干？**

它们各自只覆盖一个域，跨域转换（如"HEIC 截图 → PDF 报告"）得手动串三个工具。这个项目统一了接口、统一了路径规划、统一了进度反馈，并且暴露了 Python API 方便集成到自己的工具链。
