"""生成测试 PDF。

用程序合成而非提交二进制：仓库干净、可复现、想改特性就改代码。
覆盖三类真实场景，每类都对应一种已知的失败模式：
  1. 普通文档（标题+段落+表格）— 基准情况
  2. 两栏版面 — 段落顺序最容易错乱的情况
  3. 无文本层（纯图像）— 扫描件，必须被 preflight 拦住
"""

from __future__ import annotations

from pathlib import Path


def _table(page, x0: float, y0: float, rows: list[list[str]], pymupdf) -> float:
    """画一个带边框的表格，返回底部 y 坐标。"""
    col_w = 130.0
    row_h = 24.0
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            rect = pymupdf.Rect(
                x0 + c * col_w, y0 + r * row_h,
                x0 + (c + 1) * col_w, y0 + (r + 1) * row_h,
            )
            page.draw_rect(rect, color=(0.4, 0.4, 0.4), width=0.7)
            page.insert_textbox(
                rect + (4, 5, -4, -2),
                cell,
                fontsize=9.5,
                fontname="helv" if r else "hebo",
            )
    return y0 + len(rows) * row_h


def make_report(path: Path) -> Path:
    """带标题、段落、表格的常规文档 + 两栏页。"""
    import pymupdf

    doc = pymupdf.open()

    # ── 第 1 页：标题 + 段落 + 表格 ──
    p = doc.new_page(width=595, height=842)
    p.insert_text((60, 80), "Quarterly Report", fontsize=22, fontname="hebo")
    p.insert_text((60, 104), "Prepared for internal review", fontsize=10.5,
                  fontname="helv", color=(0.35, 0.35, 0.35))

    body = (
        "This document exercises layout reconstruction. The converter must infer "
        "paragraph boundaries from glyph coordinates, because a PDF stores only "
        "where each character is painted, never that a run of text forms a paragraph. "
        "Heading levels are likewise guessed from font size and weight."
    )
    p.insert_textbox(pymupdf.Rect(60, 130, 535, 210), body, fontsize=11,
                     fontname="helv", align=pymupdf.TEXT_ALIGN_JUSTIFY)

    p.insert_text((60, 240), "Revenue by region", fontsize=14, fontname="hebo")
    bottom = _table(p, 60, 256, [
        ["Region", "Q1", "Q2", "Q3"],
        ["North", "1,204", "1,388", "1,502"],
        ["South", "980", "1,032", "1,180"],
        ["East", "1,455", "1,390", "1,610"],
        ["West", "760", "845", "902"],
    ], pymupdf)

    p.insert_textbox(
        pymupdf.Rect(60, bottom + 20, 535, bottom + 90),
        "Table cell merging and border styles are the least reliable part of the "
        "reconstruction. Where the source uses ruling lines the detector usually "
        "succeeds; where it relies on whitespace alignment it often does not.",
        fontsize=10.5, fontname="helv",
    )

    # ── 第 2 页：两栏 ──
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((60, 80), "Two column section", fontsize=16, fontname="hebo")
    col = (
        "Multi column layouts are where reading order breaks down. The engine sees "
        "text blocks with coordinates and must decide whether the block to the right "
        "continues the sentence or starts a parallel column. Getting this wrong "
        "interleaves the two columns into nonsense. "
    ) * 2
    p2.insert_textbox(pymupdf.Rect(60, 105, 290, 500), col, fontsize=10,
                      fontname="helv", align=pymupdf.TEXT_ALIGN_JUSTIFY)
    p2.insert_textbox(pymupdf.Rect(305, 105, 535, 500), col, fontsize=10,
                      fontname="helv", align=pymupdf.TEXT_ALIGN_JUSTIFY)

    doc.save(str(path))
    doc.close()
    return path


def make_scanned(path: Path, pages: int = 2) -> Path:
    """无文本层的 PDF：模拟扫描件。preflight 必须拦住它。"""
    import io

    import pymupdf
    from PIL import Image, ImageDraw

    doc = pymupdf.open()
    for i in range(pages):
        im = Image.new("RGB", (1240, 1754), (250, 249, 245))
        d = ImageDraw.Draw(im)
        # 画一些横线模拟扫描的文本行——有图像但没有文本层
        for y in range(220, 1500, 46):
            d.line([(150, y), (1080 - (y % 190), y)], fill=(60, 60, 68), width=5)
        d.rectangle([150, 90, 620, 150], fill=(40, 40, 48))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82)

        page = doc.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(0, 0, 595, 842), stream=buf.getvalue())
    doc.save(str(path))
    doc.close()
    return path


def make_plain(path: Path, pages: int = 3) -> Path:
    """简单多页文档，用于测分割合并旋转。"""
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        p = doc.new_page(width=595, height=842)
        p.insert_text((70, 100), f"Page {i + 1}", fontsize=28, fontname="hebo")
        p.insert_textbox(
            pymupdf.Rect(70, 140, 525, 260),
            f"Body text for page {i + 1}. Structural operations such as split, "
            f"merge and rotate work on the object graph directly, so they are "
            f"lossless — the content stream is never re-rendered.",
            fontsize=11, fontname="helv",
        )
    doc.save(str(path))
    doc.close()
    return path


if __name__ == "__main__":
    import sys

    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out.mkdir(parents=True, exist_ok=True)
    print(make_report(out / "report.pdf"))
    print(make_scanned(out / "scanned.pdf"))
    print(make_plain(out / "plain.pdf"))
