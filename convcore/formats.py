"""MIME 类型与扩展名的双向映射表。

不用标准库 mimetypes：它依赖系统注册表（Windows 上结果不可预测）、缺少
AVIF/HEIC/JXL 等新格式、也没有我们要的领域归属信息。自己维护一张表更可控。

每个条目的第一个扩展名是「首选」，用于生成输出文件名。
"""

from __future__ import annotations

__all__ = [
    "known_types",
    "gated_types",
    "normalize",
    "label_of",
    "domain_of",
    "extensions",
    "primary_extension",
    "from_extension",
    "sniff",
]

from .types import Domain, MediaType

# (MIME, 领域, 扩展名..., 显示名)
_TABLE: list[tuple[MediaType, Domain, tuple[str, ...], str]] = [
    # ── 图片：位图 ──
    ("image/png", "raster", (".png",), "PNG"),
    ("image/jpeg", "raster", (".jpg", ".jpeg", ".jpe", ".jfif"), "JPEG"),
    ("image/webp", "raster", (".webp",), "WebP"),
    ("image/gif", "raster", (".gif",), "GIF"),
    ("image/tiff", "raster", (".tiff", ".tif"), "TIFF"),
    ("image/bmp", "raster", (".bmp", ".dib"), "BMP"),
    ("image/vnd.microsoft.icon", "raster", (".ico",), "ICO"),
    ("image/avif", "raster", (".avif",), "AVIF"),
    ("image/heic", "raster", (".heic", ".heif"), "HEIC"),
    # JPEG XL 留在表外：无插件实现，列进来只会让 UI 显示不可达目标。
    # 真要支持时走 Pillow（pillow-jxl-plugin）再加回。
    ("image/vnd.adobe.photoshop", "raster", (".psd",), "Photoshop"),
    ("image/x-portable-pixmap", "raster", (".ppm", ".pgm", ".pbm", ".pnm"), "Netpbm"),
    ("image/x-tga", "raster", (".tga",), "TGA"),
    # ── 图片：RAW（只读）──
    ("image/x-canon-cr2", "raster", (".cr2",), "Canon RAW"),
    ("image/x-canon-cr3", "raster", (".cr3",), "Canon RAW 3"),
    ("image/x-nikon-nef", "raster", (".nef",), "Nikon RAW"),
    ("image/x-sony-arw", "raster", (".arw",), "Sony RAW"),
    ("image/x-adobe-dng", "raster", (".dng",), "Adobe DNG"),
    ("image/x-fuji-raf", "raster", (".raf",), "Fuji RAW"),
    ("image/x-panasonic-rw2", "raster", (".rw2",), "Panasonic RAW"),
    # ── 图片：矢量 ──
    # PostScript/EPS 留在表外：Ghostscript 是 AGPL，pymupdf 不读 PS。
    # 需要时优先找宽松授权的 PS 解析库再进表。
    ("image/svg+xml", "vector", (".svg",), "SVG"),
    # ── 视频容器 ──
    ("video/mp4", "media", (".mp4", ".m4v"), "MP4"),
    ("video/x-matroska", "media", (".mkv",), "Matroska"),
    ("video/quicktime", "media", (".mov",), "QuickTime"),
    ("video/webm", "media", (".webm",), "WebM"),
    ("video/x-msvideo", "media", (".avi",), "AVI"),
    ("video/x-flv", "media", (".flv",), "FLV"),
    ("video/mp2t", "media", (".ts", ".m2ts"), "MPEG-TS"),
    ("video/x-ms-wmv", "media", (".wmv",), "WMV"),
    # ── 音频 ──
    ("audio/mpeg", "media", (".mp3",), "MP3"),
    ("audio/aac", "media", (".aac", ".m4a"), "AAC"),
    ("audio/flac", "media", (".flac",), "FLAC"),
    ("audio/wav", "media", (".wav",), "WAV"),
    ("audio/ogg", "media", (".ogg", ".oga"), "Ogg"),
    ("audio/opus", "media", (".opus",), "Opus"),
    ("audio/x-ms-wma", "media", (".wma",), "WMA"),
    ("audio/x-aiff", "media", (".aiff", ".aif"), "AIFF"),
    # ── 文档 ──
    ("application/pdf", "document", (".pdf",), "PDF"),
    (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "document", (".docx",), "Word",
    ),
    (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "table", (".xlsx",), "Excel",
    ),
    (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "document", (".pptx",), "PowerPoint",
    ),
    ("application/msword", "document", (".doc",), "Word 97"),
    ("application/vnd.ms-excel", "table", (".xls",), "Excel 97"),
    ("application/vnd.ms-powerpoint", "document", (".ppt",), "PowerPoint 97"),
    ("application/vnd.oasis.opendocument.text", "document", (".odt",), "ODF Text"),
    ("application/vnd.oasis.opendocument.spreadsheet", "table", (".ods",), "ODF Sheet"),
    # 标记语言是独立领域：面向流式可重排，没有页的概念，
    ("text/markdown", "markup", (".md", ".markdown"), "Markdown"),
    ("text/html", "markup", (".html", ".htm"), "HTML"),
    # RTF 留在表外：无插件（striprtf 只读且丢格式），不值得为它
    # 提供一个会悄悄丢所有样式的「转换」。
    ("text/plain", "markup", (".txt",), "纯文本"),
    ("application/epub+zip", "markup", (".epub",), "EPUB"),
    # MOBI 同理：无 Python 库能写，读侧也只有半成品。真需要时
    # 经 LibreOffice 转一次再进 markup 域。
    # ── 数据：树形与表格是两个域 ──
    # 树形数据是嵌套映射与列表；表格是扁平的带类型列。JSON 里没有「列类型」，
    # CSV 里没有嵌套。合并成一个域会让两边都别扭。
    ("application/json", "tree", (".json",), "JSON"),
    ("application/yaml", "tree", (".yaml", ".yml"), "YAML"),
    ("application/toml", "tree", (".toml",), "TOML"),
    ("application/xml", "tree", (".xml",), "XML"),
    ("text/csv", "table", (".csv",), "CSV"),
    ("text/tab-separated-values", "table", (".tsv",), "TSV"),
    ("application/x-ndjson", "table", (".jsonl", ".ndjson"), "JSON Lines"),
    ("application/vnd.apache.parquet", "table", (".parquet",), "Parquet"),
    # ── 归档 ──
    ("application/zip", "archive", (".zip",), "ZIP"),
    ("application/x-7z-compressed", "archive", (".7z",), "7-Zip"),
    ("application/vnd.rar", "archive", (".rar",), "RAR"),
    ("application/x-tar", "archive", (".tar",), "TAR"),
    ("application/gzip", "archive", (".gz", ".tgz"), "gzip"),
    ("application/x-bzip2", "archive", (".bz2",), "bzip2"),
    ("application/x-xz", "archive", (".xz",), "xz"),
    ("application/zstd", "archive", (".zst",), "Zstandard"),
    # ── 字体 ──
    ("font/ttf", "font", (".ttf",), "TrueType"),
    ("font/otf", "font", (".otf",), "OpenType"),
    ("font/woff", "font", (".woff",), "WOFF"),
    ("font/woff2", "font", (".woff2",), "WOFF2"),
    # ── 3D（mesh 域）整体留在表外：零实现。加回来时一个 trimesh
    # 插件可覆盖 STL/OBJ/glTF/GLB/PLY 五种格式，UI 的 "3D" 标签
    # （cli.py / desktop / webui 已有）届时直接生效。
]

_BY_TYPE: dict[MediaType, tuple[Domain, tuple[str, ...], str]] = {
    mt: (domain, exts, label) for mt, domain, exts, label in _TABLE
}

_BY_EXT: dict[str, MediaType] = {
    ext: mt for mt, _, exts, _ in _TABLE for ext in exts
}

# 同一容器有多个常见 MIME 写法时的归一化。puremagic 和各家工具的叫法不统一。
_ALIASES: dict[MediaType, MediaType] = {
    "image/heif": "image/heic",
    "image/jpg": "image/jpeg",
    "image/x-ms-bmp": "image/bmp",
    "image/x-icon": "image/vnd.microsoft.icon",
    "audio/x-wav": "audio/wav",
    "audio/vnd.wave": "audio/wav",
    "audio/x-flac": "audio/flac",
    "audio/mp3": "audio/mpeg",
    "audio/mp4": "audio/aac",
    "application/x-yaml": "application/yaml",
    "text/yaml": "application/yaml",
    "application/x-parquet": "application/vnd.apache.parquet",
    "application/rar": "application/vnd.rar",
    "text/xml": "application/xml",
}


def normalize(media_type: MediaType) -> MediaType:
    """把 MIME 别名收敛到规范写法。所有入口都该先过这一层。"""
    mt = media_type.split(";", 1)[0].strip().lower()
    return _ALIASES.get(mt, mt)


def domain_of(media_type: MediaType) -> Domain | None:
    """获取 MIME 类型对应的领域。"""
    entry = _BY_TYPE.get(normalize(media_type))
    return entry[0] if entry else None


def extensions(media_type: MediaType) -> tuple[str, ...]:
    """获取 MIME 类型对应的扩展名列表。"""
    entry = _BY_TYPE.get(normalize(media_type))
    return entry[1] if entry else ()


def primary_extension(media_type: MediaType) -> str | None:
    """获取 MIME 类型的首选扩展名。"""
    exts = extensions(media_type)
    return exts[0] if exts else None


def label_of(media_type: MediaType) -> str:
    entry = _BY_TYPE.get(normalize(media_type))
    return entry[2] if entry else media_type


def from_extension(ext: str) -> MediaType | None:
    """由扩展名反查 MIME。用于解析用户在 CLI 里写的目标格式（-t png）。"""
    if not ext.startswith("."):
        ext = "." + ext
    return _BY_EXT.get(ext.lower())


def resolve_target(spec: str) -> MediaType | None:
    """把用户输入解析成 MIME。接受 "png"、".png"、"image/png" 三种写法。"""
    spec = spec.strip()
    if "/" in spec:
        mt = normalize(spec)
        return mt if mt in _BY_TYPE else None
    return from_extension(spec)


def known_types() -> list[MediaType]:
    return [mt for mt, _, _, _ in _TABLE]


def gated_types(readable: set[MediaType], writable: set[MediaType]) -> list[MediaType]:
    """表格里有声明、但当前环境既读不了也写不了的格式。

    这类格式不是「死了」：它们的读/写插件因缺少可选外部引擎（如 LibreOffice）
    在 available() 里被拦下。装上引擎后自动恢复，未装时形同不存在。
    CLI 的 formats 和 GUI 的帮助面板用这个把「需装引擎」和「不支持」区分开，
    避免给用户「这格式转不了」的错误印象。
    """
    live = readable | writable
    return [mt for mt in known_types() if mt not in live]
