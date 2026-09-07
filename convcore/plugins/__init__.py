"""插件包。导入子模块即完成注册（靠 @register 装饰器）。

新增格式支持只需在这里加一行导入 + 写一个声明了 reads/writes 的类。
"""

from __future__ import annotations

from ._quiet import quiet_output

_MODULES = (
    "image",
    "image_extra",
    "vector",
    "media",
    "document",
    "document_pdf",
    "document_word",
    "markup",
    "office",
    "office_soffice",
    "data_tree",
    "data_table",
    "archive",
    "font",
)


def load_all() -> None:
    """导入全部插件模块。缺可选依赖的插件自己在 available() 里报告，不影响其他插件。"""
    import importlib

    with quiet_output():
        for name in _MODULES:
            try:
                importlib.import_module(f".{name}", __package__)
            except ImportError:
                # 整个模块级别的导入失败（如 Pillow 未装）不该让引擎起不来。
                # doctor 会报告缺什么。
                continue
