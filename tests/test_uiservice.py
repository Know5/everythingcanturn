"""共享 UI 服务层的契约测试。

桌面端（js_api）和浏览器端（HTTP）都依赖这份返回结构；这里钉住
「分组视图与平铺视图一致」和「环境快照字段完整」两个跨前端契约。
"""

from __future__ import annotations

from convcore.pipeline import Converter
from convcore.uiservice import PREFERRED, UiService, inspect_file


def test_inspect_returns_consistent_group_and_flat_views(samples):
    data = inspect_file(Converter(), str(samples["png"]))

    grouped = [item for group in data["groups"] for item in group["items"]]
    assert {i["mime"]: i for i in data["targets"]} == {
        i["mime"]: i for i in grouped
    }
    assert data["default"] in {item["mime"] for item in grouped}
    assert data["default"] == PREFERRED["image/png"]

    for item in grouped:
        assert item["path"] == item["pathDesc"]
        assert item["tier"] and item["tierLabel"]
        assert isinstance(item["reversible"], bool)


def test_inspect_rejects_missing_file(tmp_path):
    import pytest

    from convcore.types import ConversionError

    with pytest.raises(ConversionError, match="文件不存在"):
        inspect_file(Converter(), str(tmp_path / "gone.png"))


def test_environment_shape_is_shared_by_both_frontends():
    env = UiService().environment()

    assert env["writable"] > 0
    assert isinstance(env["domains"], list)
    assert env["domains"] == sorted(
        env["domains"], key=lambda d: -d["count"]
    )
    assert all({"key", "label", "count"} <= set(d) for d in env["domains"])
    assert all({"ok", "tier", "version"} <= set(e) for e in env["engines"])
    assert env["outDir"] is None


def test_formats_data_and_snapshot(tmp_path):
    service = UiService(out_dir=tmp_path)
    formats = service.formats_data()
    assert formats and all({"key", "items", "reaches"} <= set(g) for g in formats)
    assert service.snapshot() == []
    assert service.snapshot(["missing"]) == []


def test_inspect_includes_raster_thumbnail(samples):
    data = inspect_file(Converter(), str(samples["png"]))
    assert data["thumb"].startswith("data:image/webp;base64,")
    assert len(data["thumb"]) > 100
