"""PDF 结构操作：分割、旋转、去密码、页面重排。

这些是**真正无损**的操作——直接改 PDF 对象图，不重新渲染内容流。所以 PDF→PDF
带页面选择是 LOSSLESS 而不是 LOSSY，和「转成图片再拼回 PDF」有本质区别
（后者会把文字变成像素）。

用 pypdf 而不是 pymupdf 做这些：pypdf 是纯 Python 且 BSD 授权，
而 pymupdf 是 AGPL。能用宽松授权解决的就别引入传染性授权。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..ir.document import DocumentRef
from ..policy import Policy, Strategy
from ..registry import register
from ..types import Capability, ConversionError, LossTier, MediaType, Resource

PDF = "application/pdf"

_OPTS = frozenset({"pages", "password", "rotate", "split", "owner_password", "user_password"})


@register
class PdfStructure:
    """PDF → PDF 的结构操作。

    注册成 writer 而非 DirectConverter，因为它接的是文档域枢纽产出的
    DocumentRef——已经探测过的信息可以复用，不必重新打开文件判断页数。
    """

    name = "pypdf"
    domain = "document"
    reads = ()
    writes = (Capability(PDF, LossTier.LOSSLESS, 2.0, _OPTS),)

    def available(self) -> tuple[bool, str]:
        try:
            import pypdf  # noqa: F401

            return True, ""
        except ImportError:
            return False, "缺少 pypdf，装了才能做 PDF 结构操作：uv pip install pypdf"

    def write(
        self, ir: DocumentRef, target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        import pypdf

        pol: Policy = options.get("_policy") or Policy()
        tmp = Path(options.get("_tmp") or ".")
        src = ir.source.materialize(tmp)

        reader = pypdf.PdfReader(str(src))
        if reader.is_encrypted:
            pw = options.get("password") or ""
            if not reader.decrypt(pw):
                raise ConversionError(
                    "PDF 已加密" + ("，密码不正确" if pw else "，需要 --password")
                )

        from .document import parse_page_spec

        indices = parse_page_spec(options.get("pages"), len(reader.pages))
        rotate = int(options.get("rotate") or 0)
        if rotate % 90:
            raise ConversionError(f"旋转角度必须是 90 的倍数：{rotate}")

        # split 模式：一进多出，每页一个文件
        if options.get("split"):
            return self._split(reader, indices, rotate, target, tmp, pol, options)

        writer = pypdf.PdfWriter()
        for i in indices:
            page = reader.pages[i]
            if rotate:
                page.rotate(rotate)
            writer.add_page(page)

        if len(indices) < len(reader.pages):
            kept = ", ".join(str(i + 1) for i in indices[:10])
            pol.note(
                "页面选择",
                Strategy.DROP,
                f"仅保留第 {kept}{' 等' if len(indices) > 10 else ''} 页"
                f"（共 {len(reader.pages)} 页）",
            )

        self._apply_encryption(writer, options, pol)

        # 元数据：桥产物（DocumentRef.title 来自 DocTree）与源 PDF 的
        # 标题都应保留。搜索和文献管理工具靠这个字段识别文档。
        meta = {"/Producer": "convcore"}
        src_meta = reader.metadata or {}
        for key in ("/Title", "/Author", "/Subject", "/Creator"):
            val = src_meta.get(key)
            if val:
                meta[key] = val
        if getattr(ir, "title", None):
            meta["/Title"] = ir.title
        if getattr(ir, "author", None):
            meta["/Author"] = ir.author
        writer.add_metadata(meta)

        dst = tmp / "out.pdf"
        with dst.open("wb") as fh:
            writer.write(fh)
        return [Resource(media_type=target, path=dst)]

    def _split(
        self,
        reader,
        indices: list[int],
        rotate: int,
        target: MediaType,
        tmp: Path,
        pol: Policy,
        options: dict[str, Any],
    ) -> list[Resource]:
        import pypdf

        pol.note(
            "拆分",
            Strategy.EMULATE,
            f"每页输出为独立 PDF，共 {len(indices)} 个文件",
        )

        out: list[Resource] = []
        for n, i in enumerate(indices):
            writer = pypdf.PdfWriter()
            page = reader.pages[i]
            if rotate:
                page.rotate(rotate)
            writer.add_page(page)
            self._apply_encryption(writer, options, pol, quiet=True)

            dst = tmp / f"page-{i + 1:04d}.pdf"
            with dst.open("wb") as fh:
                writer.write(fh)
            out.append(Resource(media_type=target, path=dst, name=f"p{i + 1:04d}"))
        return out

    def _apply_encryption(
        self, writer, options: dict[str, Any], pol: Policy, quiet: bool = False
    ) -> None:
        """加密输出。

        只用 AES-256。pypdf 也支持 RC4，但那个算法已被破解——静默降级到弱加密
        比直接报错危险得多，用户会以为文件是安全的。宁可要求装依赖。
        """
        user = options.get("user_password")
        owner = options.get("owner_password")
        if not (user or owner):
            return

        try:
            writer.encrypt(
                user_password=user or "",
                owner_password=owner or user or "",
                algorithm="AES-256",
            )
        except Exception as exc:
            if "cryptography" in str(exc):
                raise ConversionError(
                    "PDF 加密需要 cryptography 包：uv pip install cryptography\n"
                    "（不自动降级到 RC4——该算法已被破解，会给出虚假的安全感）"
                ) from exc
            raise

        if not quiet:
            pol.note("加密", Strategy.EMULATE, "已用 AES-256 加密输出")


def merge_pdfs(
    sources: list[Path], dst: Path, password: str | None = None
) -> int:
    """合并多个 PDF。返回总页数。

    独立函数而非插件方法：合并是多进一出，而转换管线的语义是「同类型输入」。
    CLI 用专门的 pdf merge 子命令暴露它，语义更清楚。
    """
    import pypdf

    writer = pypdf.PdfWriter()
    total = 0
    for p in sources:
        reader = pypdf.PdfReader(str(p))
        if reader.is_encrypted and not reader.decrypt(password or ""):
            raise ConversionError(f"{p.name} 已加密且密码不正确")
        for page in reader.pages:
            writer.add_page(page)
            total += 1

    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as fh:
        writer.write(fh)
    return total
