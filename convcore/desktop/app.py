"""原生窗口与 JS 桥。

架构上比 Web 版简单：不需要 HTTP 服务，前端直接通过 js_api 调 Python 方法。
少一层网络边界，也少一层鉴权顾虑（没有端口暴露给别的进程）。

线程模型是这里唯一需要小心的地方：webview 的事件循环占用主线程，转换必须
跑在工作线程；而 window.evaluate_js 可以从任意线程调，用它把进度推给前端。
状态机与浏览器版共用 `convcore.uiservice`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .._paths import resource
from ..pipeline import Converter
from ..types import ConversionError
from ..uiservice import UiService, reveal_path

STATIC = resource("convcore", "webui", "static")


class Api:
    """暴露给前端的方法集合。

    pywebview 会把这个类的公开方法映射成 JS 侧的 window.pywebview.api.*，
    参数和返回值走 JSON 序列化，所以只能用基本类型。
    """

    def __init__(self) -> None:
        # 全部用下划线私有属性。pywebview 会 dir() 递归遍历 js_api 的公开属性
        # 来生成 JS 侧的方法桩（util.py:190），公开的 window 引用会让它一路钻进
        # window.native.browser.webview.*，从非 UI 线程访问 WebView2 的 COM 属性，
        # 于是刷出几十条 E_NOINTERFACE。私有属性会被 startswith('_') 跳过。
        self._service = UiService()
        self._window: Any = None

    # ── 环境 ──

    def env(self) -> dict[str, Any]:
        return self._service.environment()

    def formats_data(self) -> list[dict[str, Any]]:
        """全格式能力矩阵，帮助面板用。"""
        return self._service.formats_data()

    # ── 文件选择（原生对话框）──

    def pick_files(self) -> list[str]:
        import webview

        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True
        )
        return list(result or [])

    def pick_out_dir(self) -> str | None:
        import webview

        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if result:
            self._service.out_dir = Path(result[0])
            return str(self._service.out_dir)
        return None

    def clear_out_dir(self) -> None:
        self._service.out_dir = None

    # ── 探测 ──

    def inspect(self, raw: str) -> dict[str, Any]:
        """识别文件并列出可达目标。"""
        try:
            return self._service.inspect(raw)
        except ConversionError as exc:
            return {"error": str(exc)}

    # ── 转换 ──

    def convert(self, items: list[dict], preset: str | None, opts: dict) -> list[str]:
        """启动转换。立即返回任务 ID，进度通过 evaluate_js 推给前端。"""
        return self._service.enqueue(
            items, preset, opts, on_update=self._emit
        )

    def _emit(self, job: dict[str, Any]) -> None:
        """把任务状态推给前端。

        evaluate_js 可以从任意线程调，这是 pywebview 的线程安全出口。
        走 JSON 字符串而不是拼 JS 表达式——文件名里的引号和反斜杠会破坏语法。
        """
        if self._window is None:
            return
        try:
            payload = json.dumps(job, ensure_ascii=False)
            self._window.evaluate_js(f"window.onJob && window.onJob({payload})")
        except Exception:
            pass  # 窗口已关闭

    def cancel(self, jid: str) -> bool:
        return self._service.cancel(jid)

    def cancel_all(self) -> int:
        """取消所有尚未结束的任务。"""
        return self._service.cancel_all()

    def notify_drop(self, paths: list[str]) -> None:
        """把原生拖放拿到的真实路径推给前端。"""
        if self._window is None:
            return
        try:
            self._window.evaluate_js(
                f"window.onDrop && window.onDrop({json.dumps(paths)})"
            )
        except Exception:
            pass

    # ── 系统集成 ──

    def reveal(self, path: str) -> bool:
        """在文件管理器里定位文件。转完能直接跳过去，省一步。"""
        return reveal_path(path)

    def open_file(self, path: str) -> bool:
        import os
        import subprocess
        import sys

        p = Path(path)
        if not p.exists():
            return False
        try:
            if sys.platform == "win32":
                os.startfile(str(p))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.run(["open", str(p)], check=False, shell=False)
            else:
                subprocess.run(["xdg-open", str(p)], check=False, shell=False)
            return True
        except Exception:
            return False

    def open_url(self, url: str) -> bool:
        """打开外部链接（引擎下载页）。只允许 https。"""
        import webbrowser

        if not url.lower().startswith("https://"):
            return False
        webbrowser.open(url)
        return True

    def install_engine(self, key: str) -> dict[str, Any]:
        """下载安装引擎。同步执行，前端要显示等待状态。"""
        from ..engines.install import InstallError, install

        def on_progress(done: int, total: int) -> None:
            pct = (done / total) if total else 0
            if self._window:
                try:
                    self._window.evaluate_js(
                        f"window.onEngineProgress && window.onEngineProgress"
                        f"({json.dumps(key)}, {pct:.4f}, {done}, {total})"
                    )
                except Exception:
                    pass

        try:
            res = install(key, progress=on_progress)
        except InstallError as exc:
            return {"ok": False, "error": str(exc)}

        # 注册表只在启动时判定一次插件可用性，光新建 Converter 还是旧结果：
        # 必须先 refresh，新 Converter 建出来的格式图才带上刚装的引擎。
        # 之后每个任务新建的 Converter 也共用这个注册表，同样生效。
        registry = self._service.converter.registry
        registry.refresh()
        self._service.converter = Converter(registry)
        return {"ok": True, "version": res.version, "path": str(res.path)}


def _ui_source(*, dev_url: str | None = None) -> dict[str, Any]:
    """决定窗口加载什么。返回 create_window 的关键字参数。

    优先级：--ui-dev 开发服务器 > 构建产物（ui/ 里 npm run build）。

    构建产物用 file:// URI，**不能带查询串**：Windows 后端用 .NET Uri 承载地址，
    会把 file URI 里的 ? 转义成 %3F，导航到一个不存在的文件名（P0 实测）。
    防旧资源靠 Vite 的内容 hash 文件名；主文档升级不陈旧也已实测。
    """
    if dev_url:
        return {"url": dev_url}

    built = STATIC / "gui" / "index.html"
    if built.is_file():
        return {"url": built.as_uri()}

    raise SystemExit(
        f"缺少桌面端构建产物：{built}\n"
        "先在 ui/ 目录执行 `npm ci && npm run build`，再启动桌面端。"
    )


def run(*, debug: bool = False, dev_url: str | None = None) -> None:
    """开原生窗口。"""
    import webview

    api = Api()

    window = webview.create_window(
        "convcore",
        **(_ui_source(dev_url=dev_url)),
        js_api=api,
        width=1040,
        height=740,
        min_size=(760, 560),
        # 不用系统默认背景色：加载瞬间的白闪在深色界面上很刺眼
        background_color="#111113",
    )
    api._window = window

    # 原生拖放：这是相对浏览器版的核心优势——能拿到真实路径。
    # 浏览器的 DataTransfer 只给文件名，所以 Web 版只能让用户点选择框。
    #
    # 机制上有个关键点：pywebview 只在**注册了 Python 侧 DOM 事件处理器**时
    # 才会往 file 对象里注入 pywebviewFullPath。纯前端监听 drop 拿不到路径。
    def on_drop(event: dict) -> None:
        paths: list[str] = []
        for f in (event.get("dataTransfer") or {}).get("files") or []:
            if p := f.get("pywebviewFullPath"):
                paths.append(p)
        if paths:
            api.notify_drop(paths)

    def wire_dom() -> None:
        # DOM API 要等窗口加载完才能用——它内部走 evaluate_js
        try:
            window.dom.document.events.drop += on_drop
            window.dom.document.events.dragover += lambda e: None
        except Exception:
            # 挂不上就退化成「点击选择」，前端有对应提示
            pass

    window.events.loaded += wire_dom

    def dark_titlebar() -> None:
        """把 Win32 标题栏切成暗色。

        原生标题栏是系统默认白色，和全暗界面割裂得很难看。Windows 10 1809+
        的 DwmSetWindowAttribute(20) 能让它跟随暗色（Windows 11 上是 20，
        早期 build 用 19）。不支持就静静跳过——外观退化，不影响功能。
        """
        import ctypes
        import sys

        if sys.platform != "win32":
            return
        try:
            hwnd = int(window.native.Handle.ToInt64())  # type: ignore[attr-defined]
            on = ctypes.c_int(1)
            for attr in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
                    hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)
                ) == 0:
                    break
        except Exception:
            pass

    window.events.loaded += dark_titlebar

    webview.start(
        debug=debug,
        private_mode=False,
        icon=str(resource("convcore", "assets", "convcore.ico")),
    )
