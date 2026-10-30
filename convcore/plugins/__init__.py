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
    "ocr",
)


def load_all(*, reload: bool = False) -> None:
    """导入全部插件模块。缺可选依赖的插件自己在 available() 里报告，不影响其他插件。

    reload=True 时对已缓存的模块强制重执行（@register 再次跑）。
    reset_registry 后的重新加载依赖这个：importlib.import_module 对
    已缓存模块是 no-op，不重跑装饰器，注册表会静默保持空。
    """
    import importlib
    import sys

    with quiet_output():
        for name in _MODULES:
            full = f"{__package__}.{name}"
            try:
                if reload and full in sys.modules:
                    importlib.reload(sys.modules[full])
                else:
                    importlib.import_module(f".{name}", __package__)
            except ImportError:
                # 整个模块级别的导入失败（如 Pillow 未装）不该让引擎起不来。
                # doctor 会报告缺什么。
                continue
