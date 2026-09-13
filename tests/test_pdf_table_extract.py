"""测试 document → table 跨域桥（PDF 表格提取）。"""

from pathlib import Path

import pytest

from convcore.formats import resolve_target
from convcore.pipeline import ConvertRequest, Converter
from convcore.types import Resource


@pytest.fixture
def sample_pdf_with_table(tmp_path: Path) -> Path:
    """生成一个包含表格的测试 PDF。

    使用 ASCII 文本避免编码问题，用紧密布局避免空行。
    """
    try:
        import pymupdf
    except ImportError:
        pytest.skip("需要 pymupdf")

    pdf_path = tmp_path / "table_sample.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4

    # 用 ASCII 文本，行间距紧密避免 pdfplumber 检测出空行
    headers = ["Name", "Age", "City"]
    rows = [
        ["Alice", "28", "Beijing"],
        ["Bob", "35", "Shanghai"],
        ["Carol", "42", "Shenzhen"],
    ]

    col_widths = [150, 100, 150]
    row_height = 25  # 更紧密的行距
    start_x, start_y = 50, 100

    # 插入表头
    x = start_x
    for i, header in enumerate(headers):
        rect = pymupdf.Rect(x, start_y, x + col_widths[i], start_y + row_height)
        page.insert_textbox(rect, header, fontsize=11, align=pymupdf.TEXT_ALIGN_LEFT)
        x += col_widths[i]

    # 插入数据行
    y = start_y + row_height
    for row in rows:
        x = start_x
        for i, cell in enumerate(row):
            rect = pymupdf.Rect(x, y, x + col_widths[i], y + row_height)
            page.insert_textbox(rect, cell, fontsize=10, align=pymupdf.TEXT_ALIGN_LEFT)
            x += col_widths[i]
        y += row_height

    doc.save(pdf_path)
    doc.close()

    return pdf_path


def test_pdf_to_csv_via_table_domain(sample_pdf_with_table: Path, tmp_path: Path):
    """PDF → CSV 经过 document → table → CSV 路径。"""
    conv = Converter()

    src_mt = conv.detect(sample_pdf_with_table)
    target_mt = resolve_target("csv")
    assert target_mt is not None

    out_csv = tmp_path / "output.csv"

    result = conv.convert(
        ConvertRequest(
            inputs=[Resource(media_type=src_mt, path=sample_pdf_with_table)],
            target=target_mt,
            options={},
        ),
        persist_dir=tmp_path,
    )

    assert len(result.outputs) == 1
    assert result.outputs[0].path is not None

    # 写出到目标路径
    result.outputs[0].path.rename(out_csv)

    # 验证内容
    content = out_csv.read_text(encoding="utf-8")
    assert "Name" in content
    assert "Alice" in content
    assert "Beijing" in content


def test_pdf_to_xlsx_table_extraction(sample_pdf_with_table: Path, tmp_path: Path):
    """PDF → XLSX 提取表格。"""
    conv = Converter()

    src_mt = conv.detect(sample_pdf_with_table)
    target_mt = resolve_target("xlsx")
    assert target_mt is not None

    result = conv.convert(
        ConvertRequest(
            inputs=[Resource(media_type=src_mt, path=sample_pdf_with_table)],
            target=target_mt,
            options={},
        ),
        persist_dir=tmp_path,
    )

    assert len(result.outputs) == 1
    assert result.outputs[0].path is not None
    assert result.outputs[0].path.exists()


def test_pdf_without_table_raises_error(tmp_path: Path):
    """PDF 中不含表格时应该抛出 ConversionError。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate
    from reportlab.lib.styles import getSampleStyleSheet

    pdf_path = tmp_path / "no_table.pdf"
    doc = SimpleDocTemplate(str(pdf_path), pagesize=A4)
    styles = getSampleStyleSheet()

    # 只放文本段落，不放表格
    content = [
        Paragraph("这是一段纯文本", styles["Normal"]),
        Paragraph("没有任何表格结构", styles["Normal"]),
    ]
    doc.build(content)

    conv = Converter()
    src_mt = conv.detect(pdf_path)
    target_mt = resolve_target("csv")
    assert target_mt is not None

    from convcore.types import ConversionError
    with pytest.raises(ConversionError, match="未找到可提取的表格"):
        conv.convert(
            ConvertRequest(
                inputs=[Resource(media_type=src_mt, path=pdf_path)],
                target=target_mt,
                options={},
            ),
            persist_dir=tmp_path,
        )
