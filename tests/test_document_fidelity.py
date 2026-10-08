"""文档域保真修复的回归测试（审计清单驱动）。

覆盖：Markdown 相对路径图片进 PDF、缺图占位与告警、长单元格不截断、
colspan 合并、rowspan 告警、多表提取与多 sheet 导出。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from convcore.pipeline import convert_file

pytest.importorskip("pymupdf", reason="文档桥需要 pymupdf")


def _pdf_text(path: Path) -> str:
    import pymupdf

    with pymupdf.open(path) as doc:
        return "".join(p.get_text() for p in doc)


def _squeeze(text: str) -> str:
    return "".join(text.split())


def test_markdown_image_is_embedded_in_pdf(tmp_path):
    """相对路径的图片要按源文件目录解析并真正嵌进 PDF，而不是留占位。"""
    Image.new("RGB", (120, 60), "blue").save(tmp_path / "pic.png")
    md = tmp_path / "doc.md"
    md.write_text("# 标题\n\n![蓝色的图](pic.png)\n\n尾段。\n", encoding="utf-8")

    res = convert_file(md, "pdf", out_dir=tmp_path, overwrite=True)
    import pymupdf

    with pymupdf.open(res.outputs[0].path) as doc:
        assert any(page.get_images() for page in doc), "图片没有被嵌入 PDF"
    assert not any("占位" in str(w) for w in res.warnings), "图片应该被找到并嵌入"
    assert "尾段" in _squeeze(_pdf_text(res.outputs[0].path))


def test_missing_image_leaves_searchable_placeholder(tmp_path):
    """图片找不到时留可检索的占位 + DROP 告警，不许静默消失。"""
    md = tmp_path / "doc.md"
    md.write_text("# T\n\n![缺失](nope.png)\n", encoding="utf-8")
    res = convert_file(md, "pdf", out_dir=tmp_path, overwrite=True)
    assert any("图片" in str(w) for w in res.warnings), "缺图必须有告警"
    assert "nope.png" in _squeeze(_pdf_text(res.outputs[0].path))


def test_long_table_cell_is_not_truncated(tmp_path):
    """行高必须随内容换行——固定行高会把长单元格直接裁掉。"""
    long = "很长的中文单元格内容" * 10
    md = tmp_path / "t.md"
    md.write_text(f"| A | B |\n|---|---|\n| {long} | x |\n", encoding="utf-8")
    res = convert_file(md, "pdf", out_dir=tmp_path, overwrite=True)
    text = _squeeze(_pdf_text(res.outputs[0].path))
    assert _squeeze(long)[-15:] in text, "长单元格被固定行高裁剪"


def test_colspan_table_renders_merged_cell(tmp_path):
    html = tmp_path / "t.html"
    html.write_text(
        '<table><tr><td colspan="2">合并标题</td></tr>'
        "<tr><td>a1</td><td>b1</td></tr></table>",
        encoding="utf-8",
    )
    res = convert_file(html, "pdf", out_dir=tmp_path, overwrite=True)
    text = _squeeze(_pdf_text(res.outputs[0].path))
    assert "合并标题" in text and "a1" in text and "b1" in text


def test_rowspan_table_warns_instead_of_misplacing(tmp_path):
    html = tmp_path / "t.html"
    html.write_text(
        '<table><tr><td rowspan="2">跨行</td><td>a</td></tr>'
        "<tr><td>b</td></tr></table>",
        encoding="utf-8",
    )
    res = convert_file(html, "pdf", out_dir=tmp_path, overwrite=True)
    assert any("rowspan" in str(w) for w in res.warnings)


def test_pdf_extraction_keeps_every_table(tmp_path):
    """跨页的多张表全部提取进 Table IR，而不是只留第一张。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import PageBreak, SimpleDocTemplate
    from reportlab.platypus import Table as RLTable

    from convcore.plugins.document import analyze
    from convcore.plugins.document_extract import document_to_table
    from convcore.policy import Policy
    from convcore.types import Resource

    pdf = tmp_path / "two.pdf"
    SimpleDocTemplate(str(pdf), pagesize=A4).build(
        [
            RLTable([["a", "b"], ["1", "2"]]),
            PageBreak(),
            RLTable([["c", "d"], ["3", "4"]]),
        ]
    )
    ref = analyze(Resource(media_type="application/pdf", path=pdf), tmp_path)
    table = document_to_table(ref, {}, Policy())
    assert len(table.sheets) >= 2, f"多表被并成一张：{[s.name for s in table.sheets]}"


def test_multi_sheet_table_pdf_exports_all(tmp_path):
    """table→PDF 多 sheet 全部导出（每张带 sheet 名），不是只排首表。"""
    from convcore.ir.data import Sheet, Table
    from convcore.plugins.document_build import table_to_document
    from convcore.policy import Policy

    table = Table(
        sheets=[
            Sheet(columns=["x"], rows=[["1"]], name="第一表"),
            Sheet(columns=["y"], rows=[["2"]], name="第二表"),
        ]
    )
    ref = table_to_document(table, {}, Policy())
    assert ref.source.path is not None
    text = _squeeze(_pdf_text(ref.source.path))
    assert "第一表" in text and "第二表" in text
