"""数据格式的信息模型：树形与表格两个域。

为什么分两个域而不是一个「数据域」：

  树形（JSON/YAML/TOML/XML）  嵌套的映射与列表，键有序但无类型约束
  表格（CSV/XLSX/Parquet）    扁平的列，每列一个类型，行数可能极大

JSON 里不存在「第 3 列的类型是 int64」，CSV 里不存在嵌套。强行合并会让两边
都别扭：树形数据被迫扁平化，表格数据被迫套进嵌套结构。

桥接是**有方向的**，这一点比域的划分更重要：
  表格 → 树形   总是可行（每行变成一个对象），无损
  树形 → 表格   只在树恰好是「对象数组且字段扁平」时可行，否则要压平且丢结构

所以图里只有 table → tree 的桥接边。反向转换需要用户显式请求，并明确告知
会发生什么——静默压平嵌套 JSON 会产出一堆 `a.b.c` 列名，用户往往不想要。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DataTree:
    """树形数据 IR。

    直接持有 Python 的 dict/list/标量，不自造节点类型——JSON、YAML、TOML 的
    数据模型本来就和 Python 内置类型近乎同构，包一层只会增加转换成本。

    差异集中在少数点上，用 meta 记录以便 writer 决策：
      TOML 不支持 null，也不支持顶层数组
      XML 没有原生数组概念，重复标签才是数组
      YAML 支持锚点和多文档，JSON 不支持
    """

    root: Any = None
    source_type: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_record_list(self) -> bool:
        """是否为「对象数组」——能无损转成表格的唯一形状。"""
        if not isinstance(self.root, list) or not self.root:
            return False
        return all(isinstance(x, dict) for x in self.root)

    @property
    def has_nested(self) -> bool:
        """含嵌套结构。转表格时必须压平或丢弃。"""

        def deep(v: Any, level: int = 0) -> bool:
            if level > 1 and isinstance(v, (dict, list)):
                return True
            if isinstance(v, dict):
                return any(deep(x, level + 1) for x in v.values())
            if isinstance(v, list):
                return any(deep(x, level + 1) for x in v)
            return False

        return deep(self.root)

    @property
    def has_null(self) -> bool:
        """含 null。TOML 表达不了，writer 要告警。"""

        def scan(v: Any) -> bool:
            if v is None:
                return True
            if isinstance(v, dict):
                return any(scan(x) for x in v.values())
            if isinstance(v, list):
                return any(scan(x) for x in v)
            return False

        return scan(self.root)

    def describe(self) -> str:
        r = self.root
        if isinstance(r, dict):
            base = f"对象（{len(r)} 个键）"
        elif isinstance(r, list):
            base = f"数组（{len(r)} 项）"
        else:
            base = f"标量（{type(r).__name__}）"
        bits = [base]
        if self.is_record_list:
            bits.append("对象数组，可转表格")
        elif self.has_nested:
            bits.append("含嵌套")
        return " · ".join(bits)


@dataclass(slots=True)
class Sheet:
    """一张表。

    多表存在的理由是 XLSX 有多个 sheet，而 CSV 只有一张——writer 遇到多表
    要么一进多出，要么让用户选，不能静默只留第一张。
    """

    name: str = "Sheet1"
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    """每列的推断类型：int / float / bool / date / str。空则未推断。"""

    formulas: dict[tuple[int, int], str] = field(default_factory=dict)
    """(数据行下标, 列下标) → Excel 公式文本；仅 XLSX→XLSX 保留。

    行下标不含表头；没有缓存的公式单元格也能在写回 Excel 时不丢失。
    """

    @property
    def shape(self) -> tuple[int, int]:
        return len(self.rows), len(self.columns)

    def records(self) -> list[dict[str, Any]]:
        """转成对象列表。table → tree 桥接用。"""
        return [dict(zip(self.columns, row, strict=False)) for row in self.rows]


@dataclass(slots=True)
class Table:
    """表格数据 IR。"""

    sheets: list[Sheet] = field(default_factory=list)
    source_type: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.sheets:
            self.sheets = [Sheet()]

    @property
    def primary(self) -> Sheet:
        return self.sheets[0]

    @property
    def total_rows(self) -> int:
        return sum(len(s.rows) for s in self.sheets)

    def describe(self) -> str:
        if len(self.sheets) == 1:
            r, c = self.primary.shape
            return f"{r} 行 × {c} 列"
        parts = [f"{s.name}({len(s.rows)}×{len(s.columns)})" for s in self.sheets[:4]]
        more = f" 等 {len(self.sheets)} 张表" if len(self.sheets) > 4 else ""
        return "、".join(parts) + more


def infer_type(values: list[Any]) -> str:
    """从一列的值推断类型。

    只在明确时才判定，宁可留 str 也不猜错——把 "007" 判成 int 会丢前导零，
    把电话号码判成 float 会变成科学计数法。这两个是数据转换最经典的坑。
    """
    seen: set[str] = set()
    for v in values:
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            seen.add("bool")
        elif isinstance(v, int):
            seen.add("int")
        elif isinstance(v, float):
            seen.add("float")
        elif isinstance(v, str):
            seen.add(_str_type(v))
        else:
            seen.add("str")
        if len(seen) > 1:
            break

    if not seen:
        return "str"
    if len(seen) == 1:
        return seen.pop()
    # int 和 float 混排按 float 处理，其余混排退回 str
    return "float" if seen <= {"int", "float"} else "str"


def _str_type(s: str) -> str:
    t = s.strip()
    if not t:
        return "str"
    if t.lower() in ("true", "false"):
        return "bool"
    # 前导零必须留成字符串：邮编、编号、电话都会被数字化毁掉
    if len(t) > 1 and t[0] == "0" and t[1] not in ".eE":
        return "str"
    try:
        int(t)
        return "int"
    except ValueError:
        pass
    try:
        float(t)
        # 超过 15 位有效数字的整数串（如身份证、长订单号）转 float 会丢精度
        if len(t.lstrip("-+").replace(".", "")) > 15:
            return "str"
        return "float"
    except ValueError:
        return "str"


def coerce(value: Any, kind: str) -> Any:
    """按推断出的类型转换单个值。转换失败时保持原样，不抛异常。"""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return value
    t = value.strip()
    try:
        if kind == "int":
            return int(t)
        if kind == "float":
            return float(t)
        if kind == "bool":
            return t.lower() == "true"
    except ValueError:
        return value
    return value
