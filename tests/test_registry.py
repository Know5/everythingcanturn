"""注册表：插件可用性的判定与刷新。

可用性只在注册时判定一次。装好外部引擎后不 refresh，新建多少个 Converter
都看不到新能力——桌面端「一键装 ffmpeg」装完还得重启，就是栽在这里。
"""

from __future__ import annotations

from convcore.registry import Registry
from convcore.types import caps


class Toggle:
    """可用性可以随时拨动的假插件。"""

    def __init__(self, name: str, mt: str, ready: bool) -> None:
        self.name, self.domain = name, "raster"
        self.reads = caps(mt)
        self.writes = caps(mt)
        self.ready = ready

    def available(self) -> tuple[bool, str]:
        return (True, "") if self.ready else (False, f"缺少 {self.name}")

    def parse(self, resources, options):
        return object()

    def write(self, ir, target, options):
        return []


def test_refresh_picks_up_newly_available_plugin():
    reg = Registry()
    late = Toggle("late", "image/x-late", ready=False)
    reg.add(Toggle("early", "image/x-early", ready=True))
    reg.add(late)
    assert "image/x-late" not in reg.writable()
    assert reg.unavailable == {"late": "缺少 late"}

    late.ready = True  # 相当于刚装好依赖
    reg.refresh()

    assert "image/x-late" in reg.writable()
    assert reg.unavailable == {}
    # 顺序与注册顺序一致：同代价路径的平局裁决不该因为刷新而改变
    assert [p.name for p in reg.plugins] == ["early", "late"]


def test_refresh_also_drops_plugins_that_became_unavailable():
    reg = Registry()
    p = Toggle("p", "image/x-p", ready=True)
    reg.add(p)
    p.ready = False  # 比如引擎被 convcore engines remove 删了
    reg.refresh()
    assert reg.plugins == []
    assert "p" in reg.unavailable


def test_refresh_is_idempotent():
    reg = Registry()
    reg.add(Toggle("a", "image/x-a", ready=True))
    reg.add(Toggle("b", "image/x-b", ready=False))
    reg.refresh()
    reg.refresh()
    assert [p.name for p in reg.plugins] == ["a"]
    assert list(reg.unavailable) == ["b"]
