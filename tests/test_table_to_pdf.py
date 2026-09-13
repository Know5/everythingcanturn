"""测试 table → document 跨域桥。"""

from pathlib import Path

import pytest

from convcore.pipeline import Converter
from convcore.types import ConvertRequest, MediaType, Resource

pytest.importorskip("pandas")
pytest.importorskip("openpyxl")

import pandas as pd


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    """生成一个简单的 CSV 表格。"""
    csv = tmp_path / "data.csv"
    df = pd.DataFrame({
        "Name": ["Alice", "Bob", "Charlie"],
        "Age": [25, 30, 35],
        "City": ["北京", "上海", "深圳"],
    })
    df.to_csv(csv, index=False, encoding="utf-8")
    return csv


def test_csv_to_pdf_via_table_domain(sample_csv: Path, tmp_path: Path):
    """CSV → PDF 应通过 table 域桥接，生成包含表格的 PDF。"""
    conv = Converter()

    result = conv.convert(
        ConvertRequest(
            inputs=[Resource(media_type=MediaType("text/csv"), path=sample_csv)],
            target=MediaType("application/pdf"),
        ),
        persist_dir=tmp_path,
    )

    assert len(result.outputs) == 1
    out = result.outputs[0]
    assert out.path is not None
    assert out.path.exists()
    assert out.path.stat().st_size > 0

    # 验证 PDF 结构
    import pymupdf
    doc = pymupdf.open(out.path)
    try:
        assert doc.page_count >= 1
        # 表格应在第一页
        page = doc[0]
        text = page.get_text()
        assert "Name" in text
        assert "Alice" in text
        assert "北京" in text
    finally:
        doc.close()


def test_excel_to_pdf_via_table_domain(tmp_path: Path):
    """Excel → PDF 应通过 table 域桥接。"""
    xlsx = tmp_path / "data.xlsx"
    df = pd.DataFrame({
        "Product": ["Apple", "Banana", "Orange"],
        "Price": [3.5, 2.0, 4.5],
        "Stock": [100, 150, 80],
    })
    df.to_excel(xlsx, index=False, engine="openpyxl")

    conv = Converter()
    result = conv.convert(
        ConvertRequest(
            inputs=[Resource(media_type=MediaType("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), path=xlsx)],
            target=MediaType("application/pdf"),
        ),
        persist_dir=tmp_path,
    )

    assert len(result.outputs) == 1
    assert result.outputs[0].path.exists()

    import pymupdf
    doc = pymupdf.open(result.outputs[0].path)
    try:
        text = doc[0].get_text()
        assert "Product" in text
        assert "Apple" in text
    finally:
        doc.close()


def test_large_table_pagination(tmp_path: Path):
    """大表格应分页排列，不应超出页面边界。"""
    csv = tmp_path / "large.csv"
    # 生成 100 行数据，确保需要分页
    df = pd.DataFrame({
        "ID": range(100),
        "Name": [f"User{i}" for i in range(100)],
        "Value": [i * 1.5 for i in range(100)],
    })
    df.to_csv(csv, index=False)

    conv = Converter()
    result = conv.convert(
        ConvertRequest(
            inputs=[Resource(media_type=MediaType("text/csv"), path=csv)],
            target=MediaType("application/pdf"),
        ),
        persist_dir=tmp_path,
    )

    import pymupdf
    doc = pymupdf.open(result.outputs[0].path)
    try:
        # 应生成多页
        assert doc.page_count >= 2
        # 每页都应有内容
        for i in range(doc.page_count):
            text = doc[i].get_text()
            assert len(text.strip()) > 0
    finally:
        doc.close()


def test_empty_table_raises_error(tmp_path: Path):
    """空表格应报错。"""
    csv = tmp_path / "empty.csv"
    pd.DataFrame().to_csv(csv, index=False)

    conv = Converter()
    with pytest.raises(Exception, match="空表格"):
        conv.convert(
            ConvertRequest(
                inputs=[Resource(media_type=MediaType("text/csv"), path=csv)],
                target=MediaType("application/pdf"),
            ),
            persist_dir=tmp_path,
        )


def test_table_pdf_chinese_rendering(tmp_path: Path):
    """中文表格内容应正确渲染（不出现乱码）。"""
    csv = tmp_path / "chinese.csv"
    df = pd.DataFrame({
        "姓名": ["张三", "李四", "王五"],
        "部门": ["技术部", "销售部", "市场部"],
        "薪资": [15000, 12000, 18000],
    })
    df.to_csv(csv, index=False, encoding="utf-8")

    conv = Converter()
    result = conv.convert(
        ConvertRequest(
            inputs=[Resource(media_type=MediaType("text/csv"), path=csv)],
            target=MediaType("application/pdf"),
        ),
        persist_dir=tmp_path,
    )

    import pymupdf
    doc = pymupdf.open(result.outputs[0].path)
    try:
        text = doc[0].get_text()
        # china-ss 字体应支持中文
        assert "姓名" in text
        assert "张三" in text
        assert "技术部" in text
        # 不应出现 '?' 替代字符（乱码标志）
        assert text.count("?") < 3  # 允许个别非 CJK 字符
    finally:
        doc.close()
