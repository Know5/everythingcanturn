"""能力注册表：插件声明自己能读写什么，注册表把声明收集成可查询的结构。

这是「把可转换性建模成数据」的落点。插件不实现 A→B 函数，而是声明
「我能把 X 解码成本域 IR」和「我能把本域 IR 编码成 Y」，两两组合由图搜索完成。
新增一个格式只需注册一个插件，所有经由它的多跳路径自动出现。

两类插件：
- Codec：经领域枢纽（parse → IR → write），绝大多数插件是这类。
- DirectConverter：绕过枢纽的引擎原生路径。存在的理由是 remux——MKV→MP4 只改
  封装，解码成 IR 再编码会白丢一代质量。也用于 LibreOffice 这种黑盒引擎。
"""

from __future__ import annotations

__all__ = [
    "Codec",
    "DirectConverter",
    "Registry",
    "register",
    "default_registry",
]

from typing import Any, Protocol, runtime_checkable

from .formats import domain_of, normalize
from .types import Capability, Domain, MediaType, Resource


@runtime_checkable
class Codec(Protocol):
    """经领域枢纽的编解码插件。"""

    name: str
    domain: Domain
    reads: tuple[Capability, ...]
    writes: tuple[Capability, ...]

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> Any:
        """解码成本域 IR。resources 是列表以支持多进一出（多图→GIF）。"""
        ...

    def write(
        self, ir: Any, target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        """从本域 IR 编码。返回列表以支持一进多出（PDF→每页一图）。"""
        ...


@runtime_checkable
class DirectConverter(Protocol):
    """绕过枢纽的直连转换器。"""

    name: str
    domain: Domain
    direct: tuple[tuple[MediaType, MediaType, Capability], ...]

    def convert(
        self, resources: list[Resource], target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        ...


Plugin = Codec | DirectConverter


class Registry:
    """插件集合，以及从中派生的能力索引。"""

    def __init__(self) -> None:
        self._all: list[Plugin] = []
        """注册过的全部插件（含不可用的），按注册顺序。refresh 靠它重新判定。"""
        self._plugins: list[Plugin] = []
        self._unavailable: dict[str, str] = {}
        """插件名 → 不可用原因。用于 doctor 报告缺什么依赖。"""

    def add(self, plugin: Plugin) -> None:
        self._all.append(plugin)
        # 插件可以声明 available() 表示依赖是否就绪（如 pillow-heif 没装）。
        # 不可用的插件不进图，但要记下原因——「不支持 HEIC」和「装个包就支持」
        # 对用户是完全不同的信息。
        reason = self._check(plugin)
        if reason is None:
            self._plugins.append(plugin)
        else:
            self._unavailable[plugin.name] = reason

    @staticmethod
    def _check(plugin: Plugin) -> str | None:
        """可用返回 None，否则返回不可用原因。"""
        check = getattr(plugin, "available", None)
        if check is None:
            return None
        ok, reason = check()
        return None if ok else reason

    def refresh(self) -> None:
        """重新判定每个插件是否可用。

        可用性只在注册时判定一次，之后新建的 Converter 都沿用这个结果。
        所以装好外部引擎（ffmpeg、LibreOffice）后必须调这里，否则要重启进程才生效。

        先算出新列表再整体替换，不原地增删：并发新建 Converter 的线程看到的
        要么是旧列表、要么是新列表，不会是填了一半的。
        """
        plugins: list[Plugin] = []
        unavailable: dict[str, str] = {}
        for p in self._all:
            reason = self._check(p)
            if reason is None:
                plugins.append(p)
            else:
                unavailable[p.name] = reason
        self._plugins, self._unavailable = plugins, unavailable

    @property
    def plugins(self) -> list[Plugin]:
        return list(self._plugins)

    @property
    def unavailable(self) -> dict[str, str]:
        return dict(self._unavailable)

    def codecs(self) -> list[Codec]:
        """经枢纽的插件。

        只有 parse 或只有 write 都是合法的：psd-tools 只读（PSD 不该由我们写），
        pdf2docx 只写（它把 PDF 转出去，不需要读 DOCX）。按「两者都有」筛会让
        单向插件的边悄悄不进图——症状是「找不到转换路径」而插件明明已启用，
        很难查。
        """
        return [p for p in self._plugins if hasattr(p, "parse") or hasattr(p, "write")]

    def direct_converters(self) -> list[DirectConverter]:
        return [p for p in self._plugins if hasattr(p, "direct")]

    def readable(self) -> set[MediaType]:
        out: set[MediaType] = set()
        for p in self.codecs():
            if hasattr(p, "parse"):
                out.update(c.media_type for c in p.reads)
        for d in self.direct_converters():
            out.update(src for src, _, _ in d.direct)
        return out

    def writable(self) -> set[MediaType]:
        out: set[MediaType] = set()
        for p in self.codecs():
            if hasattr(p, "write"):
                out.update(c.media_type for c in p.writes)
        for d in self.direct_converters():
            out.update(dst for _, dst, _ in d.direct)
        return out

    def domains(self) -> set[Domain]:
        return {p.domain for p in self._plugins}

    def options_for(self, media_type: MediaType) -> set[str]:
        """某个目标格式支持哪些选项。CLI/UI 用来校验用户传的参数。"""
        mt = normalize(media_type)
        opts: set[str] = set()
        for p in self.codecs():
            for cap in p.writes:
                if cap.media_type == mt:
                    opts |= set(cap.options)
        return opts


# 全局默认注册表。插件模块在导入时通过 @register 装饰器注册进来。
_REGISTRY = Registry()
_LOADED = False


def register(cls):
    """插件类装饰器。在类定义处直接注册一个实例。

    用装饰器而不是配置文件：插件的能力声明和实现放在一起，加格式时只碰一个文件。

    Args:
        cls: 插件类（Codec 或 DirectConverter）

    Returns:
        cls: 原类（装饰器透传）
    """
    _REGISTRY.add(cls())
    return cls


def default_registry() -> Registry:
    """拿到全局注册表，首次调用时惰性加载所有插件模块。"""
    global _LOADED
    if not _LOADED:
        _LOADED = True
        from . import plugins

        # 重加载场景：reset_registry() 清空后 _LOADED=False，但插件模块
        # 已在 sys.modules 缓存。import_module 是 no-op，@register 不会跑，
        # 注册表静默保持空——所以只要一个插件都没注册过就走 reload。
        # 看 _all 而不是 _plugins：插件全都不可用时 _plugins 也是空的，
        # 那时 reload 会把同一批插件重复注册一遍。
        plugins.load_all(reload=not _REGISTRY._all)
    return _REGISTRY


def reset_registry() -> None:
    """仅供测试使用：清空注册表以便注入假插件。"""
    global _LOADED
    _REGISTRY._all.clear()
    _REGISTRY._plugins.clear()
    _REGISTRY._unavailable.clear()
    _LOADED = False
