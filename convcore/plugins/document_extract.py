"""document → table 跨域桥：PDF 表格提取。"""

from typing import Any

from ..ir.data import Sheet, Table
from ..ir.document import DocumentRef
from ..types import ConversionError
from ._scratch import workdir


def document_to_table(
    ir: DocumentRef, opts: dict[str, Any], pol: Any
) -> Table:
    """从 PDF 提取表格，返回 Table IR。

    使用 pdfplumber 逐页提取可识别的表格，每张表一个 sheet。
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise ConversionError(
            "PDF 表格提取需要 pdfplumber：pip install pdfplumber"
        ) from e

    source = ir.source
    if source.path is None:
        tmp = workdir(opts)
        source.materialize(tmp)
        assert source.path is not None

    sheets: list[Sheet] = []
    with pdfplumber.open(source.path) as pdf:
        # pdfplumber 默认依赖线框检测表格，但很多 PDF（如 PyMuPDF/reportlab 生成的）
        # 只有文本没有线，需要指定 text-based strategy
        table_settings = {
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "min_words_vertical": 1,  # 降低检测阈值
            "min_words_horizontal": 1,
        }

        for page_no, page in enumerate(pdf.pages, 1):
            for idx, raw_table in enumerate(
                page.extract_tables(table_settings=table_settings), 1
            ):
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

                headers = [
                    str(cell or f"列{i + 1}").strip()
                    for i, cell in enumerate(non_empty_rows[0])
                ]
                rows = [
                    [str(cell or "").strip() for cell in row]
                    for row in non_empty_rows[1:]
                ]
                sheets.append(
                    Sheet(columns=headers, rows=rows, name=f"p{page_no}-t{idx}")
                )

    if not sheets:
        raise ConversionError(f"PDF 中未找到可提取的表格：{source.path.name}")
    if len(sheets) > 1:
        from ..policy import Strategy

        pol.note("多表", Strategy.EMULATE, f"提取到 {len(sheets)} 张表，各占一个 sheet")
    return Table(sheets=sheets)
