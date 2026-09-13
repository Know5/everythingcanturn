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
- **格式图搜索**：多跳路径自动计算，`DOC → LibreOffice → DOCX → Pandoc → Markdown` 不需要手写。
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

或下载预构建包（Windows x64，254MB，含所有依赖）：

```
dist/convcore/  # 解压后直接运行
  convcore.exe      # GUI
  convcore-cli.exe  # 命令行
```

**外部引擎**（可选，按需安装）：

- **FFmpeg**：音视频转换必需。[下载](https://www.gyan.dev/ffmpeg/builds/)放 PATH 或与 exe 同目录。
- **resvg**：SVG 转位图。可优先安装 `pip install -e ".[vector]"` 使用 Python 绑定，
  也可以使用 [resvg 单文件](https://github.com/linebender/resvg/releases)。
- **rawpy**：相机 RAW 格式（CR2/NEF/ARW）。`pip install -e ".[raw]"`。

## 使用

### 命令行

```bash
# 基础转换
convcore convert photo.heic -t jpeg

# 批量（保留目录结构）
convcore convert docs/*.pdf -t docx -o ./output

# 指定质量
convcore convert video.mov -t mp4 --preset web-video

# 探测可达目标
convcore inspect mystery.bin
```

### GUI

双击 `convcore.exe` 打开窗口：

1. 拖文件进来，自动识别格式并列出可转选项（按域分组、保真度排序）
2. 选目标格式，点转换
3. 实时进度条，完成后自动打开输出目录

### Python API

```python
from convcore import Converter

conv = Converter()

# 单文件
conv.convert("input.png", "output.webp")

# 批量 + 进度回调
conv.batch(
    ["a.heic", "b.png"], 
    target_mime="image/jpeg",
    on_progress=lambda p: print(f"{p.percent:.0f}% {p.file}")
)

# 探测能力
targets = conv.targets_for("image/heic")  # → {image/jpeg: ..., image/png: ...}
```

## 架构

```
convcore/
├── types.py          # IR 定义（Raster/Document/MediaStream/...）
├── registry.py       # 插件注册表，format → plugin 映射
├── graph.py          # 格式图 + Dijkstra 路径搜索
├── pipeline.py       # 执行器：加载 IR → 路径规划 → 分段转换
├── engines.py        # 外部引擎探测（ffmpeg/resvg/...）
├── plugins/
│   ├── pillow.py     # 9 种常见图片
│   ├── avif.py       # AVIF（pillow-heif）
│   ├── psd.py        # PSD 读取
│   ├── ffmpeg.py     # 16 读 / 14 写音视频
│   ├── pymupdf.py    # PDF 读取
│   ├── pdf2docx.py   # PDF → DOCX（版面推断）
│   ├── pypdf.py      # DOCX/MD → PDF
│   ├── docx.py       # DOCX ↔ IR
│   ├── markup.py     # MD/HTML/TXT ↔ IR
│   ├── epub.py       # EPUB ↔ IR
│   ├── document_build.py  # 桥：DocTree → PDF、Raster → PDF（跨域排页）
│   ├── data_tree.py  # JSON/YAML/TOML/XML
│   ├── data_table.py # CSV/TSV/XLSX/NDJSON/Parquet
│   ├── archive.py    # ZIP/7z/TAR/RAR/GZ/BZ2/XZ
│   └── fonttools.py  # TTF/OTF/WOFF/WOFF2
├── cli.py            # 命令行入口
├── webui/            # Flask 服务器（开发预览）
└── desktop/          # pywebview 原生窗口
```

插件接口（伪代码）：

```python
class Plugin:
    domain: str           # "raster" | "media" | "document" | ...
    reads: list[str]      # MIME 类型
    writes: list[str]
    
    def parse(self, path: Path) -> IR:
        """文件 → 中间表示"""
    
    def write(self, ir: IR, out: Path, **options) -> None:
        """中间表示 → 文件"""
    
    def refine_loss(self, src_mime: str, dst_mime: str, path: Path) -> LossTier:
        """动态修正损失档位（可选，用于内容相关的决策）"""
```

## 转换质量

| 损失档位 | 含义 | 示例 |
|---------|------|------|
| **COPY** | 字节级相同 | PNG→PNG |
| **LOSSLESS** | 信息完整保留 | PNG→BMP, JSON→YAML |
| **LOSSY** | 有损但可接受 | PNG→JPEG, WAV→MP3 |
| **STRUCTURAL** | 结构简化 | DOCX→TXT（丢样式） |
| **INFERRED** | 需要推断补全 | TXT→DOCX（段落边界靠猜） |

路径搜索时优先选低档位。相同档位下选耗时短的。

## 测试

```bash
pytest tests/ -v

# 单域
pytest tests/test_images.py -k heic

# 覆盖率
pytest --cov=convcore --cov-report=html
```

核心测试策略：

- **往返测试**：A→B→A，用领域度量验证（图像 PSNR、文档树 diff）
- **黄金样本**：每种格式留 3-5 个真实文件，回归跑
- **边界探测**：空文件、1 字节、超大尺寸、损坏头部

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

系统支持多种跨域转换，在不同格式域之间建立桥梁：

| 转换方向 | 实现方式 | 损失档位 | 说明 |
|---------|---------|---------|------|
| **raster → vector** | potrace | INFERRED | 位图描摹为矢量 SVG |
| **media → raster** | ffmpeg | LOSSLESS | 视频截取首帧 |
| **raster → media** | ffmpeg | LOSSLESS | 图片序列转视频 |
| **table → document** | reportlab | LOSSY | 表格数据渲染为 PDF |
| **document → table** | camelot | INFERRED | PDF 表格提取 |
| **vector → markup** | 内嵌 | LOSSLESS | SVG 嵌入 HTML |

跨域桥会自动在路径规划时被考虑，使得原本不可达的转换成为可能。

## 限制

1. **不可逆转换不承诺往返**：位图→矢量、PDF→DOCX 这类会标 `INFERRED`，转回去得不到原文件。
2. **外部引擎失败自动回退**：FFmpeg 崩了会回退到备用方案（如果可用）。
3. **大文件内存保护**：文档域自动检测内存压力，超过 500MB 时启用流式处理或拒绝加载。
4. **线程安全**：`Converter` 实例现在是线程安全的，可在多线程环境中共享使用。

## 扩展

添加新格式只需一个插件文件：

```python
# convcore/plugins/my_format.py
from convcore.registry import register
from convcore.types import Raster

@register
class MyFormatPlugin:
    domain = "raster"
    reads = ["image/x-custom"]
    writes = ["image/x-custom"]
    
    def parse(self, path):
        data = path.read_bytes()
        # 解析逻辑
        return Raster(width=..., height=..., pixels=...)
    
    def write(self, ir, out, **opts):
        # 序列化逻辑
        out.write_bytes(...)
```

重启后自动加入格式图，`custom → JPEG` 这类路径立刻可用。

## 安全

本工具在本地运行，不上传文件到外部服务器。处理不可信文件（网上下载的 ZIP、PDF）时已启用路径穿越和解压炸弹防护。外部引擎（FFmpeg/resvg）以子进程隔离运行，崩溃不影响主程序。

核心安全措施：
- **命令注入防护**：所有外部引擎调用使用参数列表，禁用 shell 解析
- **路径穿越防护**：归档解压拒绝 `../`、绝对路径、Windows 盘符
- **解压炸弹防护**：限制总大小 4GB、压缩比 200:1、条目数 50,000
- **资源限制**：子进程超时 600s，stderr 限制 64KB，stdout 限制 8MB
- **临时文件清理**：异常退出时自动清理临时目录
- **输出覆盖保护**：默认拒绝覆盖已存在文件

详细评审见 `SECURITY_REVIEW.md`。

## 许可

MIT

---

**为什么不用 Pandoc / ImageMagick / FFmpeg 直接干？**

它们各自只覆盖一个域，跨域转换（如"HEIC 截图 → PDF 报告"）得手动串三个工具。这个项目统一了接口、统一了路径规划、统一了进度反馈，并且暴露了 Python API 方便集成到自己的工具链。
