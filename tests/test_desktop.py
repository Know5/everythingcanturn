"""桌面客户端 API 层测试。

这一层之前零覆盖，而它恰好出过一个刺眼的 bug：Api 上的公开 window 属性让
pywebview 的反射一路钻进 WebView2 的 COM 对象，刷出几百行 E_NOINTERFACE。
所以第一组测试直接钉住"公开属性面"这个契约——它不是内部细节，是 pywebview
会当成 JS 方法桩来源的东西。

不启动真实窗口：window 用桩替掉，evaluate_js 收到的 JS 反解成 payload，
这样进度推送和最终状态都能断言，也不依赖 GUI 环境。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import pytest

from convcore.desktop.app import Api, _ui_source
from convcore.uiservice import PREFERRED


class StubWindow:
    """替掉 pywebview 的窗口。

    只需要 evaluate_js —— 那是 Api 唯一的出口。顺便记下调用，
    用来断言推送内容。
    """

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.drops: list[list[str]] = []
        self.raw: list[str] = []
        self.minimized = False
        self.maximized = False
        self.destroyed = False

    def minimize(self) -> None:
        self.minimized = True

    def maximize(self) -> None:
        self.maximized = True

    def restore(self) -> None:
        self.maximized = False

    def destroy(self) -> None:
        self.destroyed = True

    def evaluate_js(self, code: str) -> None:
        self.raw.append(code)
        if m := re.search(r"window\.onJob && window\.onJob\((.*)\)$", code, re.S):
            self.jobs.append(json.loads(m.group(1)))
        elif m := re.search(r"window\.onDrop && window\.onDrop\((.*)\)$", code, re.S):
            self.drops.append(json.loads(m.group(1)))


@pytest.fixture
def api() -> Api:
    a = Api()
    a._window = StubWindow()
    return a


def _window(api: Api) -> StubWindow:
    return api._window


def _wait(api: Api, jid: str, timeout: float = 120.0) -> dict[str, Any]:
    """等到任务进入终态，返回最后一次推送。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for job in reversed(_window(api).jobs):
            if job["id"] == jid and job["state"] in ("done", "failed", "cancelled"):
                return job
        time.sleep(0.05)
    pytest.fail(f"任务 {jid} 超时未完成")


# ── 公开属性面（回归护栏）──


def test_no_public_attributes_beyond_methods(api: Api) -> None:
    """Api 实例不能有公开的非方法属性。

    pywebview 会递归遍历 js_api 的公开属性生成 JS 方法桩。任何公开的对象
    引用（尤其是 window）都会被一路钻进去，从非 UI 线程碰 WebView2 的 COM
    属性，刷出大段 E_NOINTERFACE。私有属性会被 startswith('_') 跳过。
    """
    leaked = [
        name
        for name in dir(api)
        if not name.startswith("_") and not callable(getattr(api, name))
    ]
    assert leaked == [], f"公开的非方法属性会被 pywebview 反射钻进去：{leaked}"


def test_window_reference_is_private(api: Api) -> None:
    """窗口引用必须叫 _window。改回公开名字就会复现那个 bug。"""
    assert not hasattr(api, "window")
    assert hasattr(api, "_window")


def test_public_methods_are_the_intended_surface(api: Api) -> None:
    """公开方法就是 JS 侧能调到的集合，变动应当是有意识的。"""
    surface = {n for n in dir(api) if not n.startswith("_") and callable(getattr(api, n))}
    expected = {
        "env",
        "formats_data",
        "pick_files",
        "pick_out_dir",
        "clear_out_dir",
        "inspect",
        "convert",
        "concat",
        "cancel",
        "cancel_all",
        "notify_drop",
        "reveal",
        "open_file",
        "open_url",
        "install_engine",
        "window_minimize",
        "window_toggle_maximize",
        "window_close",
    }
    missing = expected - surface
    assert not missing, f"缺少前端依赖的方法：{missing}"


# ── env ──


def test_env_reports_capabilities(api: Api) -> None:
    env = api.env()
    assert env["readable"] > 0
    assert env["writable"] > 0
    assert env["plugins"], "至少应加载到插件"
    assert env["outDir"] is None

    # 域分组按数量降序，前端直接照序渲染
    counts = [d["count"] for d in env["domains"]]
    assert counts == sorted(counts, reverse=True)
    for d in env["domains"]:
        assert d["label"] and d["key"]

    assert {p["name"] for p in env["presets"]}, "预设不应为空"
    for e in env["engines"]:
        assert isinstance(e["ok"], bool)


def test_out_dir_round_trip(api: Api, tmp_path) -> None:
    api._service.out_dir = tmp_path
    assert api.env()["outDir"] == str(tmp_path)
    api.clear_out_dir()
    assert api.env()["outDir"] is None


def test_install_engine_takes_effect_without_restart(api: Api, monkeypatch) -> None:
    """回归：装完引擎只新建 Converter，注册表仍是启动时的判定，要重启才生效。

    用私有注册表 + 假插件模拟「启动时缺引擎、装完就有」，不碰全局注册表。
    """
    from types import SimpleNamespace

    from convcore.engines import install as install_mod
    from convcore.pipeline import Converter
    from convcore.registry import Registry

    from .test_registry import Toggle

    engine = Toggle("fake-engine", "image/x-late", ready=False)
    reg = Registry()
    reg.add(engine)
    api._service.converter = Converter(reg)
    assert "image/x-late" not in api._service.converter.registry.writable()
    assert "fake-engine" in api.env()["unavailable"]

    def fake_install(key, progress=None):
        engine.ready = True
        return SimpleNamespace(version="1.0", path="engines/fake")

    monkeypatch.setattr(install_mod, "install", fake_install)
    assert api.install_engine("fake")["ok"]

    assert "image/x-late" in api._service.converter.registry.writable()
    # 格式图也得跟着重建：新格式有了出边，inspect 才能列出它能转去哪
    assert api._service.converter.graph.edges_from(("fmt", "image/x-late")), "格式图没有随注册表重建"
    env = api.env()
    assert "fake-engine" in env["plugins"]
    assert "fake-engine" not in env["unavailable"]


# ── inspect ──


def test_inspect_rejects_missing_file(api: Api, tmp_path) -> None:
    result = api.inspect(str(tmp_path / "nope.png"))
    assert "error" in result


def test_inspect_png(api: Api, samples) -> None:
    info = api.inspect(str(samples["png"]))
    assert "error" not in info
    assert info["mime"] == "image/png"
    assert info["domain"] == "raster"
    assert info["size"] > 0
    assert "320" in info["detail"] and "240" in info["detail"]
    assert info["groups"], "PNG 应有可达目标"


def test_inspect_groups_sorted_by_fidelity(api: Api, samples) -> None:
    """组内按损失等级升序——用户第一眼看到的应是最保真的选项。"""
    order = ["copy", "lossless", "lossy", "structural", "inferred"]
    for g in api.inspect(str(samples["png"]))["groups"]:
        idx = [order.index(i["tier"]) for i in g["items"]]
        assert idx == sorted(idx), f"{g['key']} 组内未按保真度排序"


def test_inspect_puts_same_domain_first(api: Api, samples) -> None:
    """同域目标排最前——多数转换发生在域内。"""
    groups = api.inspect(str(samples["png"]))["groups"]
    assert groups[0]["key"] == "raster"


def test_inspect_items_carry_ui_fields(api: Api, samples) -> None:
    for g in api.inspect(str(samples["png"]))["groups"]:
        for item in g["items"]:
            assert item["mime"] and item["label"]
            assert item["tier"] and item["tierLabel"]
            assert isinstance(item["reversible"], bool)
            assert item["path"], "需要路径描述给用户看"


# ── 默认目标 ──


def test_default_target_follows_preference_table(api: Api, samples) -> None:
    """PNG 默认不能选 BMP。

    纯按保真度排序会选中 BMP——技术上无损，实际没人要。偏好表编码真实用法。
    """
    info = api.inspect(str(samples["png"]))
    assert info["default"] == PREFERRED["image/png"] == "image/jpeg"


def test_default_target_is_never_the_source(api: Api, samples) -> None:
    for key in ("png", "jpg", "webp", "gif"):
        info = api.inspect(str(samples[key]))
        if info.get("default"):
            assert info["default"] != info["mime"]


def test_default_target_is_reachable(api: Api, samples) -> None:
    """默认值必须在列出的目标里，否则前端选中一个不存在的项。"""
    for key in ("png", "jpg", "svg"):
        info = api.inspect(str(samples[key]))
        if not info.get("default"):
            continue
        reachable = {i["mime"] for g in info["groups"] for i in g["items"]}
        assert info["default"] in reachable


def test_preference_table_targets_are_known(api: Api) -> None:
    """偏好表里的 MIME 不能写错，否则静默退回兜底逻辑。"""
    from convcore.formats import resolve_target

    for src, dst in PREFERRED.items():
        assert resolve_target(dst) is not None, f"{src} 的偏好目标无法解析：{dst}"


# ── convert ──


def test_convert_png_to_jpeg(api: Api, samples, tmp_path) -> None:
    api._service.out_dir = tmp_path
    ids = api.convert([{"path": str(samples["png"]), "target": "image/jpeg"}], None, {})
    assert len(ids) == 1

    final = _wait(api, ids[0])
    assert final["state"] == "done", final.get("error")
    assert final["outName"].endswith(".jpg")
    assert final["outSize"] > 0
    assert final["tier"] in ("copy", "lossless", "lossy", "structural", "inferred")
    assert final["pathDesc"]


def test_convert_pushes_progress_then_terminal_state(api: Api, samples, tmp_path) -> None:
    api._service.out_dir = tmp_path
    ids = api.convert([{"path": str(samples["png"]), "target": "image/webp"}], None, {})
    _wait(api, ids[0])

    pushes = [j for j in _window(api).jobs if j["id"] == ids[0]]
    assert pushes[0]["state"] == "running"
    assert pushes[-1]["state"] == "done"
    fracs = [j["progress"] for j in pushes]
    assert fracs == sorted(fracs), "进度不应回退"
    assert all(0.0 <= f <= 1.0 for f in fracs)
    assert pushes[-1]["progress"] == 1.0


def test_convert_reports_unknown_target_as_failure(api: Api, samples, tmp_path) -> None:
    """坏目标应变成任务失败，而不是抛穿到 pywebview。"""
    api._service.out_dir = tmp_path
    ids = api.convert([{"path": str(samples["png"]), "target": "image/nonexistent"}], None, {})
    final = _wait(api, ids[0])
    assert final["state"] == "failed"
    assert final["error"]


def test_convert_batch_gets_distinct_ids(api: Api, samples, tmp_path) -> None:
    api._service.out_dir = tmp_path
    items = [
        {"path": str(samples["png"]), "target": "image/jpeg"},
        {"path": str(samples["jpg"]), "target": "image/webp"},
    ]
    ids = api.convert(items, None, {})
    assert len(set(ids)) == 2
    for jid in ids:
        assert _wait(api, jid)["state"] == "done"


def test_emit_survives_closed_window(api: Api, samples, tmp_path) -> None:
    """窗口关掉后转换仍在跑，推送必须静默失败而不是炸线程。"""
    api._window = None
    api._service.out_dir = tmp_path
    ids = api.convert([{"path": str(samples["png"]), "target": "image/jpeg"}], None, {})
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if api._service.jobs[ids[0]].state in ("done", "failed"):
            break
        time.sleep(0.05)
    assert api._service.jobs[ids[0]].state == "done"


# ── 其它 ──


def test_notify_drop_forwards_real_paths(api: Api) -> None:
    """原生拖放的真实路径要原样送到前端——这是桌面版相对 Web 版的关键能力。"""
    paths = [r"C:\some dir\a'b.png", "/tmp/x.pdf"]
    api.notify_drop(paths)
    assert _window(api).drops == [paths]


def test_emit_escapes_filenames(api: Api) -> None:
    """文件名里的引号和反斜杠不能破坏 JS 语法，所以走 JSON 序列化。"""
    job = {"id": "j1", "name": "it's \"weird\"\\path.png", "state": "running"}
    api._emit(job)
    code = _window(api).raw[-1]
    payload = json.loads(re.search(r"window\.onJob\((.*)\)$", code, re.S).group(1))
    assert payload["name"] == "it's \"weird\"\\path.png"


def test_cancel_unknown_job_is_false(api: Api) -> None:
    assert api.cancel("nope") is False


def test_reveal_missing_path_is_false(api: Api, tmp_path) -> None:
    assert api.reveal(str(tmp_path / "gone.png")) is False


# ── 桌面端加载源 ──


def test_ui_source_requires_build(monkeypatch, tmp_path) -> None:
    """没有构建产物必须报可操作的错误，不能静默加载别的界面。"""
    monkeypatch.setattr("convcore.desktop.app.STATIC", tmp_path)
    with pytest.raises(SystemExit) as exc:
        _ui_source()
    assert "npm ci && npm run build" in str(exc.value)


def test_ui_source_points_at_built_index(monkeypatch, tmp_path) -> None:
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr("convcore.desktop.app.STATIC", tmp_path)

    source = _ui_source()
    assert source["url"].startswith("file:")
    assert source["url"].endswith("/gui/index.html")


def test_ui_source_dev_url_wins(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("convcore.desktop.app.STATIC", tmp_path)
    assert _ui_source(dev_url="http://localhost:5173") == {
        "url": "http://localhost:5173"
    }


# ── 无边框窗口控制 ──


def test_window_controls_delegate(api: Api) -> None:
    win = _window(api)
    api.window_minimize()
    assert win.minimized
    api.window_toggle_maximize()
    assert win.maximized
    api.window_close()
    assert win.destroyed


def test_concat_starts_one_job(api: Api, tmp_path) -> None:
    """音视频合并只产生一个任务，输出一个文件。"""
    import subprocess

    from convcore.engines.probe import ENGINES, probe_one

    spec = next(s for s in ENGINES if s.key == "ffmpeg")
    info = probe_one(spec)
    if not info.available:
        pytest.skip("需要 ffmpeg")

    assert info.path is not None
    clips = []
    for i, color in enumerate(("red", "blue")):
        clip = tmp_path / f"{i}.mp4"
        subprocess.run(
            [
                str(info.path), "-y", "-f", "lavfi", "-i",
                f"color=c={color}:s=160x120:d=1:r=10",
                "-c:v", "libx264", "-preset", "ultrafast", str(clip),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        clips.append(str(clip))

    ids = api.concat(clips, "mp4", None, {})
    assert len(ids) == 1
    job = _wait(api, ids[0])
    assert job["state"] == "done", job
    assert job["count"] == 2
    assert job["outName"].endswith(".mp4")
