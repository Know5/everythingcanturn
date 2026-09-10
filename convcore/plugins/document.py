"""PDF 域插件：探测、渲染成图、结构操作。

分工（有意让每个库只做它最擅长的事）：
- pypdf    结构操作：分割、合并、旋转、加密。不重新渲染，所以是无损的
- pymupdf  渲染：PDF→图片、文本提取、表格检测
- pdf2docx PDF→Word 的版面还原（在 document_word.py）

pymupdf 是 AGPL-3.0。个人自用无约束，但将来若要分发闭源版本需要重新评估——
AGPL 比 GPL 更严格，连网络服务都算分发。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from ..ir.document import DocumentRef, PageInfo
from ..ir.raster import Frame, Raster
from ..policy import Policy, Strategy
from ..registry import register
from ..types import Capability, ConversionError, LossTier, MediaType, Resource, caps
from ._quiet import quiet_stderr

PDF = "application/pdf"

_PAGE_OPTS = frozenset({"pages", "password", "dpi"})


def _open_doc(res: Resource, tmp: Path, password: str | None = None):
    """打开 PDF。加密文档需要密码，失败时给出明确原因。"""
    with quiet_stderr():
        import pymupdf

    path = res.materialize(tmp)
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise ConversionError(f"无法打开 PDF：{exc}") from exc

    if doc.needs_pass:
        if not password:
            doc.close()
            raise ConversionError(
                "PDF 已加密，需要密码。用 --password 提供。"
            )
        if not doc.authenticate(password):
            doc.close()
            raise ConversionError("PDF 密码不正确")
    return doc


def parse_page_spec(spec: Any, total: int) -> list[int]:
    """解析页码范围：'1-3,5' / '2' / [1,2] → 0 基索引列表。

    用户写的是 1 基（跟 PDF 阅读器一致），内部用 0 基。这个转换必须集中做，
    散在各处必然有地方差一。
    """
    if spec is None or spec == "":
        return list(range(total))
    if isinstance(spec, (list, tuple)):
        idx = [int(p) - 1 for p in spec]
    else:
        idx = []
        for part in str(spec).split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, _, b = part.partition("-")
                start = int(a) if a.strip() else 1
                end = int(b) if b.strip() else total
                idx.extend(range(start - 1, end))
            else:
                idx.append(int(part) - 1)

    bad = [i + 1 for i in idx if i < 0 or i >= total]
    if bad:
        raise ConversionError(f"页码超出范围（共 {total} 页）：{bad}")
    # 去重但保持顺序
    seen: set[int] = set()
    return [i for i in idx if not (i in seen or seen.add(i))]


def analyze(res: Resource, tmp: Path, password: str | None = None) -> DocumentRef:
    """探测 PDF 结构。

    表格检测是这里最贵的操作（pymupdf 要分析标尺线和文字对齐），所以只在
    页数不多时做全量检测，大文档抽样——探测不该比转换本身还慢。
    """
    doc = _open_doc(res, tmp, password)
    try:
        n = doc.page_count
        detect_tables = n <= 30
        pages: list[PageInfo] = []

        for i in range(n):
            page = doc[i]
            rect = page.rect
            text = page.get_text("text") or ""
            images = page.get_images(full=False)

            tables = 0
            if detect_tables:
                try:
                    # find_tables 会往 stderr 推销 pymupdf_layout 包，压掉
                    with quiet_stderr():
                        tables = len(page.find_tables().tables)
                except Exception:
                    tables = 0  # 表格检测失败不该让整个探测挂掉

            pages.append(
                PageInfo(
                    index=i,
                    width=rect.width,
                    height=rect.height,
                    rotation=page.rotation,
                    char_count=len(text.strip()),
                    image_count=len(images),
                    table_count=tables,
                    has_vector=bool(page.get_drawings()),
                )
            )

        meta = doc.metadata or {}
        return DocumentRef(
            source=res,
            pages=pages,
            encrypted=False,
            title=meta.get("title") or None,
            author=meta.get("author") or None,
            producer=meta.get("producer") or None,
            tags={k: v for k, v in meta.items() if v},
            extra={"tables_sampled": not detect_tables},
        )
    finally:
        doc.close()


@register
class PdfCodec:
    """PDF 读入 + 渲染成位图。

    PDF→图片经桥接边（document ⇒ raster）实现，所以这里只需 parse，
    栅格化由 bridge 调 rasterize。
    """

    name = "pymupdf"
    domain = "document"
    reads = (Capability(PDF, LossTier.LOSSLESS, 3.0, _PAGE_OPTS),)
    writes = ()
    """写 PDF 由 pypdf 插件负责（结构操作无损），这里只读。"""

    def available(self) -> tuple[bool, str]:
        try:
            import pymupdf  # noqa: F401

            return True, ""
        except ImportError:
            return False, "缺少 pymupdf，装了才能读 PDF：uv pip install pymupdf"

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> DocumentRef:
        if len(resources) > 1:
            raise ConversionError(
                "多个 PDF 合并请用 pdf merge（转换管线是一进一出的语义）"
            )
        tmp = Path(options.get("_tmp") or ".")
        return analyze(resources[0], tmp, options.get("password"))


def rasterize_pdf(doc: DocumentRef, opts: dict[str, Any], pol: Policy) -> Raster:
    """PDF → 位图。document ⇒ raster 桥接的实现。

    一进多出：每页一帧，Raster 的多帧模型天然支持，writer 会输出多个文件。
    """
    import pymupdf

    tmp = Path(opts.get("_tmp") or ".")
    dpi = int(opts.get("dpi") or 150)
    if dpi < 36 or dpi > 900:
        raise ConversionError(f"dpi 超出合理范围（36-900）：{dpi}")

    fitz_doc = _open_doc(doc.source, tmp, opts.get("password"))
    try:
        indices = parse_page_spec(opts.get("pages"), fitz_doc.page_count)

        pol.note(
            "矢量内容",
            Strategy.DROP,
            f"PDF 栅格化为 {dpi}dpi 位图，文字和矢量图形不再可编辑或无损缩放",
        )

        # 多页并行 + samples 直取：pymupdf 的渲染和 Pillow 的 frombytes
        # 都释放 GIL，4 线程渲染 20 页比串行快 ~20%（84ms vs 103ms）；
        # 直取 pix.samples 跳过 PNG 编码-再解码的往返（592ms→103ms，
        # 5.7 倍）。pix.samples 由 frombytes 借用而非复制，同一 pix 上
        # 下一个 get_pixmap 会复用缓冲，所以帧内必须立 im.copy() 固化。
        from concurrent.futures import ThreadPoolExecutor

        from PIL import Image

        def render(i: int) -> Frame:
            pix = fitz_doc[i].get_pixmap(dpi=dpi, alpha=False)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            return Frame(im.copy())

        workers = min(4, len(indices))
        if workers > 1:
            with ThreadPoolExecutor(workers) as ex:
                frames = list(ex.map(render, indices))
        else:
            frames = [render(i) for i in indices]

        if not frames:
            raise ConversionError("没有可渲染的页面")

        return Raster(frames=frames, source_type=PDF)
    finally:
        fitz_doc.close()
