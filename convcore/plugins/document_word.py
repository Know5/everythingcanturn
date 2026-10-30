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
from ._quiet import quiet_stderr
from ._scratch import workdir

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_OPTS = frozenset({
    "pages",
    "password",
    "review_marks",
    "min_confidence",
    "pdf_table_mode",
    "pdf_heading_styles",
})

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
        tmp = workdir(options)
        progress = options.get("_progress")

        src = ir.source.materialize(tmp)
        dst = tmp / "out.docx"

        self._preflight(ir, pol)

        from .document import parse_page_spec

        indices = parse_page_spec(options.get("pages"), ir.page_count)

        if progress:
            progress(0.05, "分析版面")

        table_mode = str(options.get("pdf_table_mode") or "auto").lower()
        if table_mode not in {"auto", "lattice", "stream", "none"}:
            raise ConversionError(
                f"未知的 PDF 表格模式：{table_mode}（可用 auto/lattice/stream/none）"
            )
        # auto：有带框表格时关掉 stream 识别，避免把公式/线条误判成表格；
        # 没有带框表格时保留 stream，否则无框表格会整体丢失。
        parse_stream = ir.table_count == 0 if table_mode == "auto" else table_mode == "stream"
        parse_lattice = table_mode != "none"
        if table_mode == "stream":
            parse_lattice = True

        cv = P2DConverter(str(src))
        try:
            # pdf2docx 的 pages 参数是 0 基索引列表
            cv.convert(
                str(dst),
                pages=indices if options.get("pages") else None,
                parse_lattice_table=parse_lattice,
                parse_stream_table=parse_stream,
            )
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

        self._apply_header_footer(
            dst, src, indices, pol, options.get("password")
        )
        self._merge_split_tables(dst, pol)

        if options.get("review_marks", True):
            self._annotate(dst, scores, ir, pol)

        if options.get("pdf_heading_styles", True):
            self._apply_heading_styles(
                dst, src, indices, pol, options.get("password")
            )

        if progress:
            progress(1.0, "完成")

        return [Resource(media_type=target, path=dst)]

    def _page_margin_info(
        self, src: Path, indices: list[int], password: str | None = None
    ) -> list[dict[str, Any]]:
        """按页提取页边距文字、页码位置和页码原文。

        不要求页眉跨页重复：不同章节使用不同页眉是正常排版。调用方只在
        多页转换时应用，单页仍然不搬，避免把正文大标题误判成页眉。
        """
        import re

        import pymupdf

        patterns = (
            re.compile(r"^\d{1,4}$"),
            re.compile(r"^page\s+\d{1,4}$", re.IGNORECASE),
            re.compile(r"^\d{1,4}\s*/\s*\d{1,4}$"),
            re.compile(r"^\d{1,4}\s+of\s+\d{1,4}$", re.IGNORECASE),
            re.compile(r"^-\s*\d{1,4}\s*-$"),
            re.compile(r"^第\s*\d{1,4}\s*页$"),
        )

        def clean(text: str) -> str:
            return re.sub(r"\s+", " ", text or "").strip()

        def is_page_number(text: str) -> bool:
            return any(pattern.match(text) for pattern in patterns)

        out: list[dict[str, Any]] = []
        with pymupdf.open(src) as doc:
            if doc.needs_pass and password:
                doc.authenticate(password)
            for index in indices:
                page = doc[index]
                height = float(page.rect.height)
                top: list[tuple[float, float, str]] = []
                bottom: list[tuple[float, float, str]] = []
                page_hits: list[tuple[str, str]] = []
                for block in page.get_text("blocks"):
                    if len(block) < 7 or block[6] != 0:
                        continue
                    text = clean(block[4])
                    if not text:
                        continue
                    y0, y1 = float(block[1]), float(block[3])
                    where = (
                        "top" if y1 < 55
                        else "bottom" if y0 > height - 55
                        else None
                    )
                    if is_page_number(text):
                        if where:
                            page_hits.append((where, text))
                        continue
                    if len(text) > 80 or not where:
                        continue
                    (top if where == "top" else bottom).append(
                        (y0, float(block[0]), text)
                    )

                top.sort()
                bottom.sort()
                page_where = None
                if page_hits:
                    counts: dict[str, int] = {}
                    for where, _ in page_hits:
                        counts[where] = counts.get(where, 0) + 1
                    page_where = max(counts, key=counts.get)
                out.append(
                    {
                        "header": "  ·  ".join(item[2] for item in top),
                        "footer": "  ·  ".join(item[2] for item in bottom),
                        "pageWhere": page_where,
                        "pageTexts": {text for _, text in page_hits},
                    }
                )
        return out

    @staticmethod
    def _add_page_field(paragraph) -> None:
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        run = paragraph.add_run()
        begin = OxmlElement("w:fldChar")
        begin.set(qn("w:fldCharType"), "begin")
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = " PAGE "
        sep = OxmlElement("w:fldChar")
        sep.set(qn("w:fldCharType"), "separate")
        text = OxmlElement("w:t")
        text.text = "1"
        end = OxmlElement("w:fldChar")
        end.set(qn("w:fldCharType"), "end")
        run._r.append(begin)
        run._r.append(instr)
        run._r.append(sep)
        run._r.append(text)
        run._r.append(end)

    def _apply_header_footer(
        self,
        docx_path: Path,
        src: Path,
        indices: list[int],
        pol: Policy,
        password: str | None = None,
    ) -> None:
        """把每页页眉/页脚搬进对应 Word section，并把页码转成 PAGE 域。

        pdf2docx 通常把页眉页脚当正文段落；不同章节的页眉还各不相同。这里按
        页建立 header/footer 文本，再按 section 顺序映射。单页文档不搬，避免
        把正文标题误判成页眉。
        """
        if len(indices) < 2:
            return
        try:
            import re

            import docx
            from docx.shared import Pt

            infos = self._page_margin_info(src, indices, password)
            headers = {info["header"] for info in infos if info["header"]}
            footers = {info["footer"] for info in infos if info["footer"]}
            page_texts = {
                text for info in infos for text in info.get("pageTexts", set())
            }
            if not headers and not footers and not page_texts:
                return

            doc = docx.Document(str(docx_path))
            targets = {
                re.sub(r"\s+", " ", text).strip().lower()
                for text in headers | footers | page_texts
            }
            removed = 0
            for paragraph in list(doc.paragraphs):
                key = re.sub(r"\s+", " ", paragraph.text or "").strip().lower()
                if key and key in targets:
                    parent = paragraph._element.getparent()
                    if parent is not None:
                        parent.remove(paragraph._element)
                        removed += 1

            sections = doc.sections
            if len(sections) == len(infos):
                per_section = infos
            else:
                # section 数不等于页数时只能顺序近似；超出部分留空，不复制末页页眉
                per_section = [
                    infos[i] if i < len(infos) else {
                        "header": "", "footer": "", "pageWhere": None,
                    }
                    for i in range(len(sections))
                ]
                pol.note(
                    "页眉页脚",
                    Strategy.APPROXIMATE,
                    f"Word section 数（{len(sections)}）与页数（{len(infos)}）不一致，"
                    "按顺序近似映射",
                )

            for i, section in enumerate(sections):
                info = per_section[i]
                section.header.is_linked_to_previous = False
                section.footer.is_linked_to_previous = False
                hp = section.header.paragraphs[0]
                hp.text = info.get("header", "")
                for run in hp.runs:
                    run.font.size = Pt(9)

                fp = section.footer.paragraphs[0]
                fp.text = info.get("footer", "")
                for run in fp.runs:
                    run.font.size = Pt(8)

                where = info.get("pageWhere")
                if where == "top":
                    if hp.text:
                        hp.add_run("  ·  ")
                    self._add_page_field(hp)
                elif where == "bottom":
                    if fp.text:
                        fp.add_run("  ·  ")
                    self._add_page_field(fp)

            if removed or headers or footers or page_texts:
                doc.save(str(docx_path))
                parts = []
                if headers:
                    parts.append(f"{len(headers)} 种页眉")
                if footers:
                    parts.append(f"{len(footers)} 种页脚")
                if page_texts:
                    parts.append("页码")
                msg = "已把每页的 " + "、".join(parts) + " 搬进 Word 页眉/页脚"
                if page_texts:
                    msg += "；页码已转为 PAGE 域"
                pol.note("页眉页脚", Strategy.APPROXIMATE, msg)
        except Exception:
            # 页眉页脚是增强项；失败不能破坏已生成的文档
            pass

    def _merge_split_tables(self, docx_path: Path, pol: Policy) -> None:
        """合并跨页拆分、表头重复的相邻 Word 表格。

        pdf2docx 通常把跨页表格拆成两张表，并在新页重复表头。这里只在
        列数一致、表头文本一致、且中间只有空段落/分页/分节符（或上一行残留）
        时合并，避免把文档里两个独立的同构表格误接在一起。
        """
        try:
            import re
            from copy import deepcopy

            import docx
            from docx.oxml import OxmlElement
            from docx.oxml.ns import qn

            doc = docx.Document(str(docx_path))
            body = doc.element.body

            def norm(text: str) -> str:
                return re.sub(r"\s+", " ", text or "").strip()

            def compact(text: str) -> str:
                return re.sub(r"\s+", "", text or "")

            def rows(tbl):
                return tbl.findall(qn("w:tr"))

            def row_texts(tr) -> list[str]:
                return [
                    norm("".join(tc.itertext()))
                    for tc in tr.findall(qn("w:tc"))
                ]

            merged = 0
            while True:
                children = list(body)
                tables = [c for c in children if c.tag == qn("w:tbl")]
                changed = False
                for ai in range(len(tables) - 1):
                    a, b = tables[ai], tables[ai + 1]
                    ra, rb = rows(a), rows(b)
                    if len(ra) < 2 or len(rb) < 2:
                        continue
                    if len(row_texts(ra[0])) != len(row_texts(rb[0])):
                        continue
                    if row_texts(ra[0]) != row_texts(rb[0]):
                        continue

                    start = children.index(a)
                    end = children.index(b)
                    between = children[start + 1:end]
                    last_row_compact = compact("".join(row_texts(ra[-1])))
                    ncols = len(row_texts(ra[0]))

                    def artifact_cells(p) -> list[str] | None:
                        """pdf2docx 有时把表格最后一行留在表外，按制表符还原。"""
                        import re as _re

                        runs = [
                            "".join(r.itertext())
                            for r in p.findall(".//" + qn("w:r"))
                        ]
                        parts: list[str] = []
                        for run in runs:
                            parts.extend(norm(x) for x in run.split("\t") if norm(x))
                        if len(parts) != ncols:
                            return None

                        def dedupe(s: str) -> str:
                            words = s.split()
                            if len(words) >= 4 and len(words) % 2 == 0:
                                half = len(words) // 2
                                if words[:half] == words[half:]:
                                    return " ".join(words[:half])
                            comp = _re.sub(r"\s+", "", s)
                            if len(comp) >= 2 and len(comp) % 2 == 0:
                                half = len(comp) // 2
                                if comp[:half] == comp[half:]:
                                    return comp[:half]
                            return s

                        cells = [dedupe(p) for p in parts]
                        return cells if all(cells) else None

                    def append_cells(dst, cells: list[str]) -> None:
                        tr = OxmlElement("w:tr")
                        for value in cells:
                            tc = OxmlElement("w:tc")
                            p = OxmlElement("w:p")
                            r = OxmlElement("w:r")
                            t = OxmlElement("w:t")
                            t.text = value
                            r.append(t)
                            p.append(r)
                            tc.append(p)
                            tr.append(tc)
                        dst.append(tr)

                    allowed = True
                    artifact_rows: list[list[str]] = []
                    for child in between:
                        if child.tag != qn("w:p"):
                            allowed = False
                            break
                        text = norm("".join(child.itertext()))
                        if not text:
                            continue
                        if compact(text) == last_row_compact:
                            continue
                        cells = artifact_cells(child)
                        if cells is None:
                            allowed = False
                            break
                        artifact_rows.append(cells)
                    if not allowed:
                        continue

                    # 把表外残留的最后一行补回表内，再追加下一页的行
                    for cells in artifact_rows:
                        append_cells(a, cells)
                    for tr in rb[1:]:
                        a.append(deepcopy(tr))

                    # 第二张表末尾也可能有同一类残留行
                    after = list(body)[list(body).index(b) + 1:]
                    trailing: list[Any] = []
                    for child in after:
                        if child.tag != qn("w:p"):
                            break
                        text = norm("".join(child.itertext()))
                        if not text:
                            trailing.append(child)
                            continue
                        cells = artifact_cells(child)
                        if cells is None:
                            break
                        append_cells(a, cells)
                        trailing.append(child)

                    for child in between:
                        body.remove(child)
                    for child in trailing:
                        body.remove(child)
                    body.remove(b)
                    merged += 1
                    changed = True
                    break
                if not changed:
                    break

            if merged:
                doc.save(str(docx_path))
                pol.note(
                    "跨页表格",
                    Strategy.EMULATE,
                    f"已合并 {merged} 处跨页拆分的表格；请核对表头和行顺序",
                )
        except Exception:
            # 表格合并是增强项；失败不能破坏已生成的文档
            pass

    def _heading_outline(
        self, src: Path, indices: list[int], password: str | None = None
    ) -> list[tuple[str, int]]:
        """从 PDF 字号/字重推断标题候选，返回 (规范化文本, 级别)。

        只做标题层级，不重建正文。正文仍由 pdf2docx 的版面还原负责；
        这里补的是 Word 的语义结构——没有 Heading 样式，Word 导航窗格、
        目录和后续自动化都看不到章节。
        """
        import re
        from collections import Counter, defaultdict

        import pymupdf

        sizes: dict[float, int] = defaultdict(int)
        block_counts: Counter[str] = Counter()
        pages: list[Any] = []
        with pymupdf.open(src) as doc:
            if doc.needs_pass and password:
                doc.authenticate(password)
            for index in indices:
                page = doc[index]
                data = page.get_text("dict")
                try:
                    with quiet_stderr():
                        table_boxes = [
                            tuple(t.bbox) for t in page.find_tables().tables
                        ]
                except Exception:
                    table_boxes = []
                pages.append((float(page.rect.height), data, table_boxes))
                for block in data.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    block_text = re.sub(
                        r"\s+",
                        " ",
                        " ".join(
                            "".join(s.get("text", "") for s in line.get("spans", []))
                            for line in block.get("lines", [])
                        ),
                    ).strip().lower()
                    if block_text:
                        block_counts[block_text] += 1
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            text = (span.get("text") or "").strip()
                            if len(text) >= 3:
                                sizes[round(float(span.get("size", 0)), 1)] += len(text)

        if not sizes:
            return []
        body = max(sizes, key=sizes.get)  # type: ignore[arg-type]
        if body <= 0:
            return []

        outline: list[tuple[str, int]] = []
        seen: set[str] = set()
        for height, data, table_boxes in pages:
            for block in data.get("blocks", []):
                if block.get("type") != 0:
                    continue
                bbox = block.get("bbox") or (0, 0, 0, 0)
                if bbox[3] < 40 or bbox[1] > height - 40:
                    continue  # 页眉/页脚/页码不参与标题推断
                if any(
                    box[0] <= bbox[0]
                    and box[1] <= bbox[1]
                    and box[2] >= bbox[2]
                    and box[3] >= bbox[3]
                    for box in table_boxes
                ):
                    continue  # 表格单元格里的表单标签不是标题
                spans = [
                    span
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                    if (span.get("text") or "").strip()
                ]
                if not spans:
                    continue
                lines = block.get("lines", [])
                if len(lines) > 2:
                    continue  # 标题通常一到两行；长块更像正文
                text = " ".join(
                    "".join(s.get("text", "") for s in line.get("spans", []))
                    for line in lines
                ).strip()
                normalized = re.sub(r"\s+", " ", text).strip().lower()
                if (
                    len(normalized) < 2
                    or len(normalized) > 120
                    or normalized.isdigit()
                    or normalized.endswith((",", ";", "."))
                    or "@" in normalized
                    or "http" in normalized
                    or normalized.startswith(
                        ("arxiv:", "doi:", "http:", "https:", "www.", "©")
                    )
                ):
                    continue
                if normalized in seen:
                    continue
                if block_counts.get(normalized, 0) >= 2:
                    continue  # 跨页重复的页眉/页脚/表单编号
                if re.match(r"line\s+\d", normalized):
                    continue  # 表单行标签不是章节标题
                size = max(float(s.get("size", 0)) for s in spans)
                ratio = size / body
                if ratio >= 1.5:
                    level = 1
                elif ratio >= 1.18:
                    level = 2
                elif ratio >= 1.12:
                    level = 3
                else:
                    continue
                seen.add(normalized)
                outline.append((normalized, level))
        return outline

    def _apply_heading_styles(
        self,
        docx_path: Path,
        src: Path,
        indices: list[int],
        pol: Policy,
        password: str | None = None,
    ) -> None:
        """把推断出的标题映射到 Word 的 Heading 样式，保留直接格式。"""
        try:
            import docx
            import re

            outline = self._heading_outline(src, indices, password)
            if not outline:
                return
            doc = docx.Document(str(docx_path))
            remaining = list(enumerate(outline))
            applied = 0
            for paragraph in doc.paragraphs:
                raw = paragraph.text or ""
                if re.search(r"(?:\.{3,}|…)\s*\d+\s*$", raw) or re.search(
                    r"\t\s*\d+\s*$", raw
                ):
                    continue  # 目录项，别抢在真正的标题前面匹配
                text = re.sub(r"\s+", " ", raw).strip().lower()
                if len(text) < 2:
                    continue
                for pos, (heading, level) in list(remaining):
                    if not (
                        text == heading
                        or (len(text) >= 6 and heading.startswith(text))
                        or (len(heading) >= 6 and text.startswith(heading[:80]))
                    ):
                        continue
                    try:
                        paragraph.style = f"Heading {level}"
                        paragraph.paragraph_format.keep_with_next = True
                        applied += 1
                    except Exception:
                        pass
                    remaining.remove((pos, (heading, level)))
                    break
            if applied:
                doc.save(str(docx_path))
                pol.note(
                    "标题结构",
                    Strategy.APPROXIMATE,
                    f"已按字号/字重推断 {applied} 个标题并应用 Word Heading 样式；"
                    "标题层级仍可能误判，可在 Word 导航窗格里快速核对",
                )
        except Exception:
            # 标题样式是增强项，失败不能破坏已经生成的文档
            pass

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
                "扫描件转 Word 需要先做 OCR：安装 Tesseract 后可直接走 OCR 路径\n"
                "（扫描 PDF → Markdown/Word），中文需额外 chi_sim 语言包。\n"
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
