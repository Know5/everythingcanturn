"""document → table 跨域桥：PDF 表格提取。"""

from pathlib import Path
from typing import Any

from ..ir.data import Table
from ..ir.document import DocumentRef
from ..types import ConversionError


def document_to_table(
    ir: DocumentRef, opts: dict[str, Any], pol: Any
) -> Table:
    """从 PDF 提取表格，返回 Table IR。

    使用 pdfplumber 提取第一个可识别的表格。
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise ConversionError(
            "PDF 表格提取需要 pdfplumber：pip install pdfplumber"
        ) from e

    source = ir.source
    if source.path is None:
        tmp = Path(opts.get("_tmp") or ".")
        source.materialize(tmp)
        assert source.path is not None

    with pdfplumber.open(source.path) as pdf:
        # 尝试从每一页提取表格，返回第一个非空表格
        # pdfplumber 默认依赖线框检测表格，但很多 PDF（如 PyMuPDF/reportlab 生成的）
        # 只有文本没有线，需要指定 text-based strategy
        table_settings = {
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "min_words_vertical": 1,  # 降低检测阈值
            "min_words_horizontal": 1,
        }

        for page in pdf.pages:
            tables = page.extract_tables(table_settings=table_settings)
            if not tables:
                continue

            for raw_table in tables:
                if not raw_table:
                    continue

                # 过滤掉全空行（pdfplumber 从文本坐标推断表格时可能产生）
                non_empty_rows = [
                    row for row in raw_table
                    if any(cell and str(cell).strip() for cell in row)
                ]

                if len(non_empty_rows) < 2:
                    # 至少需要表头 + 一行数据
                    continue

                # 验证是否有多列结构：单列的纯文本不算表格
                if len(non_empty_rows[0]) < 2:
                    continue

                headers = [str(cell or f"列{i+1}").strip() for i, cell in enumerate(non_empty_rows[0])]
                rows = []
                for row in non_empty_rows[1:]:
                    rows.append([str(cell or "").strip() for cell in row])

                from ..ir.data import Sheet
                sheet = Sheet(columns=headers, rows=rows)
                return Table(sheets=[sheet])

        # 没找到任何有效表格
        raise ConversionError(f"PDF 中未找到可提取的表格：{source.path.name}")
