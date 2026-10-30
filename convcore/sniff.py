"""文件类型嗅探：先看魔数，扩展名只作回退。

为什么不信扩展名：用户会把 .webp 存成 .jpg，微信下载的图片经常没有扩展名，
截图工具的命名也不可靠。按扩展名选插件会导致解码器和真实内容不匹配。

为什么用 puremagic 而不是 python-magic：后者要在 Windows 上另外装 libmagic
的 DLL，这跟「零门槛」的目标冲突。puremagic 是纯 Python 实现。
"""

from __future__ import annotations

from pathlib import Path

from .formats import from_extension, normalize
from .types import MediaType

# 需要手工判定的格式。puremagic 对这些的支持不稳定或给不出我们要的粒度。
# 检查顺序有意义：ISOBMFF 的 ftyp brand 必须在通用容器判定之前。
_ZIP_MAGIC = b"PK\x03\x04"


def _sniff_jxl(head: bytes) -> MediaType | None:
    """JPEG XL 容器签名或裸 codestream 起始码。"""
    if head.startswith(b"\x00\x00\x00\x0cJXL \r\n\x87\n"):
        return "image/jxl"
    if head.startswith(b"\xff\x0a"):
        return "image/jxl"
    return None


def _sniff_isobmff(head: bytes) -> MediaType | None:
    """ISO 基础媒体格式家族靠 ftyp brand 区分：MP4 / MOV / HEIC / AVIF 同一套容器。

    这是最容易出错的一类——HEIC 和 MP4 的文件头几乎一样，只有 brand 不同。
    """
    if len(head) < 12 or head[4:8] != b"ftyp":
        return None
    brand = head[8:12]
    if brand in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"hevm"):
        return "image/heic"
    if brand in (b"mif1", b"msf1"):
        return "image/heic"  # 通用 HEIF，按 HEIC 处理
    if brand in (b"avif", b"avis"):
        return "image/avif"
    if brand == b"qt  ":
        return "video/quicktime"
    if brand.startswith(b"M4A"):
        return "audio/aac"
    if brand in (b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"dash"):
        return "video/mp4"
    return None


def _sniff_zip_container(head: bytes, data: bytes | None) -> MediaType | None:
    """OOXML、ODF、EPUB 外面都是 ZIP，得看内部条目才能区分。

    快路径：ZIP 规范允许 mimetype 作为第一个条目且不压缩（ODF/EPUB 就这么做），
    这时直接在文件头附近就能读到。慢路径才真的开 zipfile。
    """
    if not head.startswith(_ZIP_MAGIC):
        return None
    if b"mimetypeapplication/epub+zip" in head[:60]:
        return "application/epub+zip"
    if b"mimetypeapplication/vnd.oasis.opendocument." in head[:100]:
        tail = head[30:200]
        if b"opendocument.text" in tail:
            return "application/vnd.oasis.opendocument.text"
        if b"opendocument.spreadsheet" in tail:
            return "application/vnd.oasis.opendocument.spreadsheet"
    if data is None:
        return None
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        # 有 PK 签名但打不开中央目录 = 损坏的 ZIP。
        # 返回 None 会让 puremagic 按 PK 签名猜成 DOCX（两者魔数完全一致），
        # 于是用户看到的是「找不到 Word→TAR 的路径」而非「ZIP 损坏」。
        return "application/zip"
    if "word/document.xml" in names:
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if "xl/workbook.xml" in names:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if "ppt/presentation.xml" in names:
        return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    return "application/zip"


def _sniff_text(head: bytes) -> MediaType | None:
    """文本格式没有魔数，只能看内容特征。放在最后作为兜底。"""
    try:
        text = head.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    stripped = text.lstrip()
    if not stripped:
        return "text/plain"
    if stripped.startswith("<?xml"):
        # SVG 是 XML 的子集，得往后看根元素
        return "image/svg+xml" if "<svg" in text[:2048] else "application/xml"
    if stripped.startswith("<svg"):
        return "image/svg+xml"
    if stripped[:1] in "{[":
        return "application/json"
    if stripped.startswith("<!DOCTYPE html") or stripped.startswith("<html"):
        return "text/html"
    if stripped.startswith("%PDF"):
        return "application/pdf"
    return None


def _refine(media_type: MediaType, head: bytes) -> MediaType:
    """把过于笼统的判定收窄。

    puremagic 只按魔数判断，所以 SVG 会被认成 application/xml——魔数层面它确实
    就是 XML。要区分必须看根元素。同理 XML 家族里还有 XHTML 等。
    """
    if media_type == "application/xml":
        refined = _sniff_text(head)
        if refined and refined != "application/xml":
            return refined
    return media_type


def sniff_bytes(data: bytes, filename: str | None = None) -> MediaType | None:
    """从字节内容判定类型。filename 仅在魔数无结论时用于回退。"""
    head = data[:4096]

    # 手工判定优先：这些格式 puremagic 给不出足够粒度
    for probe in (_sniff_jxl(head), _sniff_isobmff(head), _sniff_zip_container(head, data)):
        if probe:
            return probe

    try:
        import puremagic

        from .formats import domain_of

        for match in puremagic.magic_string(data)[:5]:
            mt = normalize(match.mime_type or "")
            if mt and domain_of(mt):
                return _refine(mt, head)
            # puremagic 有时只给扩展名不给 MIME
            if match.extension:
                guessed = from_extension(match.extension)
                if guessed:
                    return _refine(guessed, head)
    except Exception:
        # 嗅探失败不该让整个转换崩掉，继续走回退路径
        pass

    if (text_type := _sniff_text(head)) is not None:
        return text_type

    if filename:
        return from_extension(Path(filename).suffix)
    return None


def sniff(path: Path) -> MediaType | None:
    """嗅探磁盘文件。只读文件头，不为了判类型把大文件整个读进内存。"""
    with path.open("rb") as fh:
        head = fh.read(64 * 1024)

    # ZIP 容器判定可能需要完整文件（要开 zipfile 读条目表）
    data: bytes | None = head
    if head.startswith(_ZIP_MAGIC) and path.stat().st_size > len(head):
        data = path.read_bytes()

    return sniff_bytes(data or head, filename=path.name)


def sniff_resource(res) -> MediaType | None:
    """对 Resource 嗅探，内存和磁盘两种来源都支持。"""
    if res.data is not None:
        return sniff_bytes(res.data, filename=res.name)
    return sniff(res.path)
