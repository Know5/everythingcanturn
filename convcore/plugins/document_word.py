"""PDF → Word 的版面还原。

**这是本项目唯一的 INFERRED 等级转换，必须诚实对待。**

PDF 存的是「字形画在什么坐标」，Word 存的是「段落、样式、表格结构」。前者没有
后者的语义——段落边界、标题层级、表格单元格都得从坐标关系里**推断**出来。
所以这不是翻译而是重建，结果可能不正确。

pdf2docx 的做法：按文本块位置聚合段落、按标尺线和空白检测表格、提取图片并定位、
映射字体。做不到的是复杂多栏、跨页表格合并、扫描件（那需要 OCR）。

学 FlyingMouse 的两点（它这块做得对）：
1. 低置信度的重建内容标出来让人复核，而不是静默输出一个可能错的文档
2. 扫描件必须提前拦住——没有文本层的 PDF 转出来只会是一堆图片，
   不如直接告诉用户需要 OCR
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..ir.document import Confidence, DocumentRef
from ..policy import Policy, Strategy
from ..registry import register
from ..types import Capability, ConversionError, LossTier, MediaType, Resource

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_OPTS = frozenset({"pages", "password", "review_marks", "min_confidence"})

# 置信度阈值。低于此值的重建元素在产物里标黄。
# 取 0.85 和 FlyingMouse 一致——这个值的含义是「大概对但值得看一眼」。
REVIEW_THRESHOLD = 0.85


def _score_page(page) -> Confidence:
    """给一页的重建质量打分。

    pdf2docx 不吐置信度，所以这里按页面特征估算——不是模型输出的真实概率，
    而是「已知哪些情况容易出错」的启发式。命名上要诚实：这是估计不是测量。

    扣分依据（都是实践中已知的失败模式）：
      多栏版面     → 段落顺序容易错乱
      表格多       → 单元格合并和边框还原不稳
      矢量图形多   → 可能是图表，会被当成杂散线条
      字符密度极高 → 可能是紧排版，行间距推断易错
    """
    score = 1.0
    notes: list[str] = []

    area = (page.width * page.height) / 1000.0
    density = page.char_count / area if area else 0

    if page.table_count >= 3:
        score -= 0.20
        notes.append(f"{page.table_count} 个表格")
    elif page.table_count:
        score -= 0.08

    if page.has_vector and page.image_count == 0:
        score -= 0.12
        notes.append("含矢量图形")

    if density > 4.0:
        score -= 0.15
        notes.append("排版密集")

    if page.is_landscape:
        score -= 0.05

    if page.rotation:
        score -= 0.10
        notes.append(f"页面旋转 {page.rotation}°")

    return Confidence(
        kind="page",
        page=page.index,
        score=max(0.3, score),
        note="、".join(notes),
    )


@register
class PdfToWord:
    """PDF → DOCX。经文档域枢纽，输入是 PdfCodec 产出的 DocumentRef。"""

    name = "pdf2docx"
    domain = "document"
    reads = ()
    """只写不读：读 PDF 由 pymupdf 插件负责。"""
    writes = (
        # INFERRED 而非 STRUCTURAL：这不是「丢了些东西」，是「猜出来的可能是错的」。
        # 等级选错会让 UI 显示成可逆转换，误导用户。
        # requires text：版面还原靠文本层。图片嵌成的 PDF、扫描件不列出 Word，
        # 与 _preflight 拒绝扫描件一致。
        Capability(DOCX, LossTier.INFERRED, 40.0, _OPTS, requires=frozenset({"text"})),
    )

    def available(self) -> tuple[bool, str]:
        try:
            import pdf2docx  # noqa: F401

            return True, ""
        except ImportError:
            return False, (
                "缺少 pdf2docx，装了才能 PDF 转 Word："
                "uv pip install pdf2docx"
            )

    def write(
        self, ir: DocumentRef, target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        from pdf2docx import Converter as P2DConverter

        pol: Policy = options.get("_policy") or Policy()
        tmp = Path(options.get("_tmp") or ".")
        progress = options.get("_progress")

        src = ir.source.materialize(tmp)
        dst = tmp / "out.docx"

        self._preflight(ir, pol)

        from .document import parse_page_spec

        indices = parse_page_spec(options.get("pages"), ir.page_count)

        if progress:
            progress(0.05, "分析版面")

        cv = P2DConverter(str(src))
        try:
            # pdf2docx 的 pages 参数是 0 基索引列表
            cv.convert(str(dst), pages=indices if options.get("pages") else None)
        except Exception as exc:
            raise ConversionError(
                f"版面还原失败：{exc}。"
                "复杂多栏或异常字体的 PDF 可能超出 pdf2docx 的能力，"
                "可试 --pages 分段转换定位问题页。"
            ) from exc
        finally:
            cv.close()

        if not dst.is_file() or dst.stat().st_size == 0:
            raise ConversionError("pdf2docx 执行完毕但没有产出有效文件")

        if progress:
            progress(0.85, "标注复核区域")

        scores = [_score_page(ir.pages[i]) for i in indices]
        self._report(scores, indices, pol, options)

        if options.get("review_marks", True):
            self._annotate(dst, scores, ir, pol)

        if progress:
            progress(1.0, "完成")

        return [Resource(media_type=target, path=dst)]

    def _preflight(self, ir: DocumentRef, pol: Policy) -> None:
        """转换前拦住注定失败的情况。

        扫描件是最常见的坑：用户以为转出来能编辑，实际只会得到一堆图片。
        提前说清楚比转完让他失望好。
        """
        if ir.encrypted:
            raise ConversionError("PDF 已加密，请先用 --password 解密")

        if ir.is_scanned_doc:
            raise ConversionError(
                f"这份 PDF 没有文本层（{len(ir.scanned_pages)}/{ir.page_count} 页是扫描图像），"
                "版面还原只能产出图片而非可编辑文字。\n"
                "扫描件转 Word 需要先做 OCR，本项目暂未集成（需要 Tesseract 中文包约 50MB）。\n"
                "现在可以做的：转成图片（-t png）留档，或用 --pages 只转有文本层的页。"
            )

        if ir.scanned_pages:
            pol.note(
                "扫描页",
                Strategy.DROP,
                f"第 {', '.join(str(i + 1) for i in ir.scanned_pages[:8])} 页无文本层，"
                f"将只保留图像",
            )

    def _report(
        self,
        scores: list[Confidence],
        indices: list[int],
        pol: Policy,
        options: dict[str, Any],
    ) -> None:
        """把置信度汇总成告警。"""
        threshold = float(options.get("min_confidence") or REVIEW_THRESHOLD)

        pol.note(
            "版面还原",
            Strategy.APPROXIMATE,
            "段落边界、标题层级和表格结构是从坐标关系推断的，不是 PDF 里的语义。"
            "转回 PDF 不会得到原文件",
        )

        low = [c for c in scores if c.score < threshold]
        if not low:
            return

        detail = "、".join(
            f"第{c.page + 1}页({c.score:.0%}"
            + (f"，{c.note}" if c.note else "")
            + ")"
            for c in low[:6]
        )
        more = f" 等 {len(low)} 页" if len(low) > 6 else ""
        pol.note(
            "建议复核",
            Strategy.APPROXIMATE,
            f"{detail}{more}——这些页面版面复杂，还原结果已在文档中标黄",
        )

    def _annotate(
        self, docx_path: Path, scores: list[Confidence], ir: DocumentRef, pol: Policy
    ) -> None:
        """在 DOCX 开头插入复核提示。

        为什么写进文档而不只是 CLI 告警：Word 文件会被转发、归档、打印，
        脱离转换工具的上下文。把「这是机器推断的结果」写进文件本身，
        下一个打开它的人才知道不能当原件用。
        """
        try:
            import docx
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.shared import Pt, RGBColor
        except ImportError:
            return  # python-docx 是 pdf2docx 的依赖，理论上一定在

        low = [c for c in scores if c.score < REVIEW_THRESHOLD]

        try:
            doc = docx.Document(str(docx_path))
        except Exception:
            return  # 标注失败不该让转换整体失败——文档本身是好的

        # 插到最前面。python-docx 没有 insert_paragraph_at(0)，
        # 用第一个段落的 insert_paragraph_before 实现
        anchor = doc.paragraphs[0] if doc.paragraphs else None

        lines: list[tuple[str, bool]] = [
            ("本文档由 PDF 自动转换生成，版面为程序推断结果，可能与原件有差异。", True),
        ]
        if low:
            pages = "、".join(f"第{c.page + 1}页" for c in low[:10])
            lines.append(
                (f"以下页面版面较复杂，建议对照原件复核：{pages}"
                 + (f" 等 {len(low)} 页" if len(low) > 10 else ""), False)
            )
        if ir.table_count:
            lines.append(
                (f"原件含 {ir.table_count} 个表格，单元格合并和边框可能需要手工调整。", False)
            )

        if anchor is None:
            return

        # 正序插入。insert_paragraph_before 每次都插在同一个锚点之前，所以后插的
        # 会排在先插的**后面**——用 reversed() 反而会把顺序颠倒。
        for text, bold in lines:
            p = anchor.insert_paragraph_before(text)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            for run in p.runs:
                run.font.size = Pt(9)
                run.font.bold = bold
                run.font.color.rgb = RGBColor(0x99, 0x6B, 0x00)

        try:
            doc.save(str(docx_path))
        except Exception:
            pass
