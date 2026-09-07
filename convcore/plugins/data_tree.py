"""树形数据：JSON / YAML / TOML / XML 互转。

这四种格式的数据模型近乎同构（嵌套映射与列表），所以互转基本无损。差异集中在
少数点上，每处都要显式处理而不是静默丢弃：

  TOML  不支持 null，顶层必须是表（不能是数组）
  XML   没有原生数组和数字类型，一切都是字符串和元素
  YAML  支持锚点、多文档、注释——转出去都会丢
  JSON  最保守的公共子集
"""

from __future__ import annotations

from typing import Any

from ..ir.data import DataTree
from ..policy import Policy, Strategy
from ..registry import register
from ..types import Capability, ConversionError, LossTier, MediaType, Resource

JSON = "application/json"
YAML = "application/yaml"
TOML = "application/toml"
XML = "application/xml"

_OPTS = frozenset({"indent", "sort_keys", "root_tag", "ensure_ascii"})


def _decode(res: Resource) -> str:
    """解码文本。中文环境 GBK 常见，必须嗅探。"""
    data = res.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    for enc in ("utf-8", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


# ── XML 与树的映射 ──
#
# XML 没有数组和标量类型，映射必须定规则。这里的约定：
#   <root><a>1</a><b>x</b></root>        → {"a": 1, "b": "x"}
#   <root><item>1</item><item>2</item>/> → {"item": [1, 2]}
#   属性存进 "@name"，文本存进 "#text"
# 这套约定和业界常用的 xmltodict 一致，便于用户预期。


def _xml_to_obj(el) -> Any:
    from collections import defaultdict

    children = [c for c in el if isinstance(c.tag, str)]
    result: dict[str, Any] = {}

    for k, v in el.attrib.items():
        result[f"@{k}"] = _scalar(v)

    if children:
        groups: dict[str, list[Any]] = defaultdict(list)
        for c in children:
            groups[c.tag].append(_xml_to_obj(c))
        for tag, vals in groups.items():
            result[tag] = vals[0] if len(vals) == 1 else vals
        if el.text and el.text.strip():
            result["#text"] = _scalar(el.text)
        return result

    text = (el.text or "").strip()
    if result:
        if text:
            result["#text"] = _scalar(text)
        return result
    return _scalar(text) if text else None


def _scalar(s: str) -> Any:
    """XML 里一切都是字符串，转出来时尽量还原类型。

    和表格的类型推断同一套保守原则：前导零和超长数字串保持字符串。
    """
    t = s.strip()
    if t.lower() in ("true", "false"):
        return t.lower() == "true"
    if t.lower() in ("null", "none", "nil"):
        return None
    if len(t) > 1 and t[0] == "0" and t[1] not in ".eE":
        return t
    try:
        return int(t)
    except ValueError:
        pass
    try:
        f = float(t)
        if len(t.lstrip("-+").replace(".", "")) > 15:
            return t
        return f
    except ValueError:
        return t


def _obj_to_xml(parent, key: str, value: Any) -> None:
    from lxml import etree

    if isinstance(value, dict):
        el = etree.SubElement(parent, _safe_tag(key))
        for k, v in value.items():
            if k.startswith("@"):
                el.set(k[1:], _text(v))
            elif k == "#text":
                el.text = _text(v)
            else:
                _obj_to_xml(el, k, v)
    elif isinstance(value, list):
        # 数组表达为重复标签，这是 XML 里唯一的惯例做法
        for item in value:
            _obj_to_xml(parent, key, item)
    else:
        el = etree.SubElement(parent, _safe_tag(key))
        el.text = _text(value)


def _safe_tag(name: str) -> str:
    """把任意键名变成合法的 XML 标签名。

    XML 标签不能以数字开头、不能含空格和多数符号。JSON 的键没这些限制，
    所以必须转换——直接用会生成非法 XML，而且报错信息很难懂。
    """
    import re

    t = re.sub(r"[^\w.\-]", "_", str(name), flags=re.UNICODE)
    if not t or t[0].isdigit() or t[0] in ".-":
        t = "_" + t
    return t


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


@register
class TreeCodec:
    name = "data-tree"
    domain = "tree"

    reads = (
        Capability(JSON, LossTier.LOSSLESS, 1.0),
        Capability(YAML, LossTier.LOSSLESS, 1.5),
        Capability(TOML, LossTier.LOSSLESS, 1.0),
        # XML → 树需要按约定映射属性和文本，不是纯粹的同构
        Capability(XML, LossTier.STRUCTURAL, 1.5, fidelity=0.85),
    )

    writes = (
        Capability(JSON, LossTier.LOSSLESS, 1.0, _OPTS),
        Capability(YAML, LossTier.LOSSLESS, 1.5, _OPTS),
        # TOML 不支持 null 也不支持顶层数组
        Capability(TOML, LossTier.STRUCTURAL, 1.0, _OPTS, fidelity=0.8),
        # XML 丢类型信息（一切变字符串）
        Capability(XML, LossTier.STRUCTURAL, 1.5, _OPTS, fidelity=0.75),
    )

    def available(self) -> tuple[bool, str]:
        try:
            import yaml  # noqa: F401
            from lxml import etree  # noqa: F401

            return True, ""
        except ImportError as exc:
            return False, f"缺少 {exc.name}：uv pip install pyyaml lxml"

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> DataTree:
        import json

        res = resources[0]
        mt = res.media_type
        pol: Policy = options.get("_policy") or Policy()

        if mt == JSON:
            try:
                root = json.loads(_decode(res))
            except json.JSONDecodeError as exc:
                raise ConversionError(
                    f"JSON 解析失败（第 {exc.lineno} 行第 {exc.colno} 列）：{exc.msg}"
                ) from exc

        elif mt == YAML:
            import yaml

            text = _decode(res)
            try:
                docs = list(yaml.safe_load_all(text))
            except yaml.YAMLError as exc:
                raise ConversionError(f"YAML 解析失败：{exc}") from exc
            if len(docs) > 1:
                pol.note(
                    "多文档",
                    Strategy.EMULATE,
                    f"YAML 含 {len(docs)} 个文档，已合并为数组（目标格式多不支持多文档）",
                )
                root = docs
            else:
                root = docs[0] if docs else None
            if "&" in text and "*" in text:
                pol.note("锚点与引用", Strategy.DROP, "YAML 锚点已展开为实际值")

        elif mt == TOML:
            import tomllib

            try:
                root = tomllib.loads(_decode(res))
            except tomllib.TOMLDecodeError as exc:
                raise ConversionError(f"TOML 解析失败：{exc}") from exc

        elif mt == XML:
            from lxml import etree

            try:
                el = etree.fromstring(res.read_bytes())
            except etree.XMLSyntaxError as exc:
                raise ConversionError(f"XML 解析失败：{exc}") from exc
            root = {el.tag: _xml_to_obj(el)}
            pol.note(
                "XML 结构",
                Strategy.APPROXIMATE,
                "属性映射为 @name 键、文本映射为 #text 键；"
                "重复标签合并为数组；注释和命名空间前缀已丢弃",
            )
        else:
            raise ConversionError(f"tree 插件不支持读 {mt}")

        return DataTree(root=root, source_type=mt)

    def write(
        self, ir: DataTree, target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        pol: Policy = options.get("_policy") or Policy()
        indent = int(options.get("indent", 2))

        if target == JSON:
            import json

            text = json.dumps(
                ir.root,
                ensure_ascii=bool(options.get("ensure_ascii", False)),
                indent=indent if indent > 0 else None,
                sort_keys=bool(options.get("sort_keys", False)),
                default=str,
            )

        elif target == YAML:
            import yaml

            text = yaml.safe_dump(
                ir.root,
                allow_unicode=True,
                indent=indent or 2,
                sort_keys=bool(options.get("sort_keys", False)),
                default_flow_style=False,
            )

        elif target == TOML:
            text = self._to_toml(ir, pol, options)

        elif target == XML:
            text = self._to_xml(ir, pol, options)

        else:
            raise ConversionError(f"tree 插件不支持写 {target}")

        return [Resource(media_type=target, data=text.encode("utf-8"))]

    def _to_toml(self, ir: DataTree, pol: Policy, options: dict[str, Any]) -> str:
        import tomli_w

        root = ir.root

        # TOML 顶层必须是表。数组要包一层，且必须告知用户键名是我们加的
        if not isinstance(root, dict):
            key = str(options.get("root_tag") or "items")
            pol.note(
                "顶层结构",
                Strategy.EMULATE,
                f"TOML 顶层必须是表，已把{'数组' if isinstance(root, list) else '标量'}"
                f"包进 [{key}] 键下",
            )
            root = {key: root}

        if ir.has_null:
            pol.note(
                "空值",
                Strategy.DROP,
                "TOML 规范不支持 null，相关键已省略（不是写成空字符串）",
            )
            root = _strip_null(root)

        try:
            return tomli_w.dumps(root)
        except (TypeError, ValueError) as exc:
            raise ConversionError(
                f"数据结构无法表达为 TOML：{exc}。"
                "TOML 要求同层键类型一致，异构数组可能无法转换。"
            ) from exc

    def _to_xml(self, ir: DataTree, pol: Policy, options: dict[str, Any]) -> str:
        from lxml import etree

        root = ir.root
        pol.note(
            "类型信息",
            Strategy.DROP,
            "XML 元素内容均为文本，数字和布尔类型标记会丢失（转回时靠推断还原）",
        )

        # 单键字典且值是字典时，用那个键当根标签——这样 XML→树→XML 能往返
        if isinstance(root, dict) and len(root) == 1:
            only_key = next(iter(root))
            if isinstance(root[only_key], (dict, list)):
                el = etree.Element(_safe_tag(only_key))
                inner = root[only_key]
                if isinstance(inner, dict):
                    for k, v in inner.items():
                        if k.startswith("@"):
                            el.set(k[1:], _text(v))
                        elif k == "#text":
                            el.text = _text(v)
                        else:
                            _obj_to_xml(el, k, v)
                else:
                    for item in inner:
                        _obj_to_xml(el, "item", item)
                return _serialize(el)

        tag = _safe_tag(str(options.get("root_tag") or "root"))
        el = etree.Element(tag)
        if isinstance(root, dict):
            for k, v in root.items():
                _obj_to_xml(el, k, v)
        elif isinstance(root, list):
            for item in root:
                _obj_to_xml(el, "item", item)
        else:
            el.text = _text(root)
        return _serialize(el)


def _serialize(el) -> str:
    from lxml import etree

    etree.indent(el, space="  ")
    return etree.tostring(
        el, encoding="unicode", pretty_print=True, xml_declaration=False
    )


def _strip_null(obj: Any) -> Any:
    """递归去掉 None 值。TOML 表达不了 null。"""
    if isinstance(obj, dict):
        return {k: _strip_null(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_null(x) for x in obj if x is not None]
    return obj


def tree_to_table(tree: DataTree, opts: dict[str, Any], pol: Policy):
    """树形 → 表格桥接。

    只有「对象数组」这一种形状能有意义地变成表格。其余形状明确报错并给出可行
    建议——静默压平嵌套 JSON 会产出一堆 `a.b.c` 列名，用户拿到后往往还得手工
    清理，不如一开始就说清楚。
    """
    import json

    from ..ir.data import Sheet, Table

    root = tree.root

    # 单个对象 → 一行表。这是合理的引申，不是猜测
    if isinstance(root, dict):
        # 值全是对象数组时，每个键变一张表（多 sheet）
        if root and all(
            isinstance(v, list) and v and all(isinstance(x, dict) for x in v)
            for v in root.values()
        ):
            sheets = [
                _sheet_from_records(name, records, pol)
                for name, records in root.items()
            ]
            return Table(sheets=sheets, source_type=tree.source_type)
        records = [root]
    elif isinstance(root, list):
        if not root:
            raise ConversionError("数据是空数组，没有可写入表格的内容")
        if not all(isinstance(x, dict) for x in root):
            kinds = sorted({type(x).__name__ for x in root})
            raise ConversionError(
                f"数组元素不都是对象（含 {', '.join(kinds)}），无法确定表格的列。\n"
                "表格需要「对象数组」形状，例如 "
                '[{"名称": "甲", "数量": 1}, {"名称": "乙", "数量": 2}]。'
            )
        records = root
    else:
        raise ConversionError(
            f"顶层是{type(root).__name__}标量，无法转成表格。"
            "表格需要「对象数组」形状。"
        )

    return Table(
        sheets=[_sheet_from_records("Sheet1", records, pol)],
        source_type=tree.source_type,
    )


def _sheet_from_records(name: str, records: list[dict], pol: Policy):
    import json

    from ..ir.data import Sheet

    # 列取并集并保持首次出现顺序——不同记录可能有不同字段
    columns: list[str] = []
    for r in records:
        for k in r:
            if k not in columns:
                columns.append(k)

    nested = 0
    rows: list[list[Any]] = []
    for r in records:
        row: list[Any] = []
        for c in columns:
            v = r.get(c)
            if isinstance(v, (dict, list)):
                nested += 1
                # 序列化而非压平：压平会产出 a.b.c 列名且列数不可控
                row.append(json.dumps(v, ensure_ascii=False))
            else:
                row.append(v)
        rows.append(row)

    if nested:
        pol.note(
            "嵌套字段",
            Strategy.EMULATE,
            f"{nested} 个嵌套值已序列化为 JSON 字符串（表格无法表达嵌套结构）",
        )

    missing = sum(1 for r in records for c in columns if c not in r)
    if missing:
        pol.note(
            "字段缺失",
            Strategy.DROP,
            f"{missing} 个单元格因源记录缺少该字段而留空",
        )

    return Sheet(name=name[:31] or "Sheet1", columns=columns, rows=rows)


def table_to_tree(table, opts: dict[str, Any], pol: Policy) -> DataTree:
    """表格 → 树形桥接。每行变成一个对象。

    这个方向总是可行且无损。反方向（树→表格）只在树恰好是扁平对象数组时可行，
    所以图里没有那条边——需要时由用户显式请求。
    """
    if len(table.sheets) == 1:
        root: Any = table.primary.records()
    else:
        # 多表变成 {表名: [记录...]}，保住表名信息
        root = {s.name: s.records() for s in table.sheets}
        pol.note(
            "多表",
            Strategy.EMULATE,
            f"{len(table.sheets)} 张表已转为以表名为键的对象",
        )
    return DataTree(root=root, source_type=table.source_type)
