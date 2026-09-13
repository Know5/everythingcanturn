"""tree → markup 跨域桥：将 JSON/YAML 数据树渲染为可读的 HTML。

用例：API 响应、配置文件可视化、数据审查页面。

渲染策略：
  - 对象数组 → HTML 表格（最常见的数据形状）
  - 嵌套对象 → 递归渲染的定义列表
  - 简单数组 → 有序/无序列表
  - 标量 → 直接显示
"""

from __future__ import annotations

from typing import Any

from ..ir.data import DataTree
from ..ir.markup import (
    HEADING,
    LIST,
    LIST_ITEM,
    PARAGRAPH,
    TABLE,
    Block,
    Cell,
    DocTree,
    Span,
)
from ..policy import Policy


def tree_to_markup(tree: DataTree, opts: dict[str, Any], pol: Policy) -> DocTree:
    """将数据树转换为结构化文档。

    如果是对象数组（最常见的 API 响应形状），渲染成表格；
    否则渲染成嵌套的定义列表。
    """
    blocks: list[Block] = []

    # 添加可选标题
    title = opts.get("title")
    if title:
        blocks.append(Block(kind=HEADING, level=1, spans=[Span(title)]))

    # 根据数据形状选择渲染方式
    if tree.is_record_list:
        blocks.extend(_render_records_as_table(tree.root))
    else:
        blocks.extend(_render_value(tree.root, depth=0))

    return DocTree(blocks=blocks)


def _render_records_as_table(records: list[dict[str, Any]]) -> list[Block]:
    """将对象数组渲染为 HTML 表格。"""
    if not records:
        return [Block(kind=PARAGRAPH, spans=[Span("(空数据)")])]

    # 收集所有出现过的键作为列
    columns = []
    seen = set()
    for rec in records:
        for k in rec.keys():
            if k not in seen:
                columns.append(k)
                seen.add(k)

    if not columns:
        return [Block(kind=PARAGRAPH, spans=[Span("(无字段)")])]

    # 构造表头
    header = [Cell(blocks=[Block(kind=PARAGRAPH, spans=[Span(str(col))])], header=True)
              for col in columns]

    # 构造数据行
    rows = []
    for rec in records:
        row = []
        for col in columns:
            val = rec.get(col)
            text = _format_cell_value(val)
            row.append(Cell(blocks=[Block(kind=PARAGRAPH, spans=[Span(text)])]))
        rows.append(row)

    return [Block(kind=TABLE, rows=[header] + rows)]


def _format_cell_value(val: Any) -> str:
    """格式化单元格值。嵌套结构压缩为单行 JSON。"""
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        import json
        # 嵌套值显示为紧凑 JSON
        return json.dumps(val, ensure_ascii=False, separators=(",", ":"))
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val)


def _render_value(val: Any, depth: int = 0, key: str | None = None) -> list[Block]:
    """递归渲染任意值。

    - 对象 → 定义列表（嵌套块）
    - 数组 → 无序列表
    - 标量 → 段落
    """
    blocks: list[Block] = []

    if val is None:
        blocks.append(Block(kind=PARAGRAPH, spans=[Span("(null)")]))

    elif isinstance(val, dict):
        if not val:
            blocks.append(Block(kind=PARAGRAPH, spans=[Span("(空对象)")]))
        else:
            # 对象渲染为定义列表
            items = []
            for k, v in val.items():
                # 键作为粗体标签
                item_blocks = [Block(kind=PARAGRAPH, spans=[Span(f"{k}: ", bold=True)])]
                # 值嵌套渲染
                item_blocks.extend(_render_value(v, depth + 1, key=k))
                items.append(Block(kind=LIST_ITEM, children=item_blocks))
            # 包在列表块中
            blocks.append(Block(kind=LIST, children=items, ordered=False))

    elif isinstance(val, list):
        if not val:
            blocks.append(Block(kind=PARAGRAPH, spans=[Span("(空数组)")]))
        else:
            items = []
            for item in val:
                item_blocks = _render_value(item, depth + 1)
                items.append(Block(kind=LIST_ITEM, children=item_blocks))
            blocks.append(Block(kind=LIST, children=items, ordered=False))

    else:
        # 标量：字符串、数字、布尔
        text = _format_scalar(val)
        blocks.append(Block(kind=PARAGRAPH, spans=[Span(text)]))

    return blocks


def _format_scalar(val: Any) -> str:
    """格式化标量值为字符串。"""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, str):
        return val
    return str(val)
