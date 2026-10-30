"""本地 HTTP 服务，给 Web UI 提供引擎能力。

设计要点：
- 引擎实例全局复用一份（格式图建一次），不是每请求重建
- 转换在后台线程跑，前端轮询进度；不用 WebSocket 是为了少一层复杂度，
  轮询对本地服务完全够用
- 文件选择走原生对话框（tkinter），拿到的是真实路径，不必上传字节。
  这是本地工具相对网页工具的天然优势——大视频不用复制一遍。
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .._paths import python_cmd, resource
from ..formats import resolve_target
from ..types import ConversionError
from ..uiservice import UiService, reveal_path

STATIC = resource("convcore", "webui", "static")


class UiState:
    """服务端状态。引擎和任务表都在共享的 UiService 里，跨请求复用。"""

    def __init__(self, out_dir: Path | None = None) -> None:
        self.service = UiService(out_dir=out_dir)
        self.token = secrets.token_urlsafe(24)

    @property
    def out_dir(self) -> Path | None:
        return self.service.out_dir

    @out_dir.setter
    def out_dir(self, value: Path | None) -> None:
        self.service.out_dir = value

    def inspect(self, raw: str) -> dict[str, Any]:
        """识别文件并列出可达目标（含逐文件的真实损失等级）。"""
        return self.service.inspect(raw)

    def enqueue(self, items: list[dict[str, Any]], preset: str | None) -> list[str]:
        return self.service.enqueue(items, preset)

    def concat(
        self, paths: list[str], target: str, preset: str | None
    ) -> list[str]:
        return self.service.enqueue_concat(paths, target, preset)

    def merge_images(
        self, paths: list[str], target: str, preset: str | None
    ) -> list[str]:
        return self.service.enqueue_merge_images(paths, target, preset)

    def cancel(self, jid: str) -> bool:
        return self.service.cancel(jid)

    def snapshot(self, ids: list[str] | None = None) -> list[dict[str, Any]]:
        return self.service.snapshot(ids)

    def environment(self) -> dict[str, Any]:
        return self.service.environment()


def pick_files() -> list[str]:
    """弹原生文件选择框。

    本地工具相对网页工具的天然优势：拿到真实路径，几百 MB 的视频不用先上传一遍。
    tkinter 必须在主线程用，所以这里在独立进程里跑，避免和 HTTP 线程打架。
    """
    import subprocess

    code = (
        "import tkinter as tk, json\n"
        "from tkinter import filedialog\n"
        "r = tk.Tk(); r.withdraw()\n"
        "f = filedialog.askopenfilenames(title='选择要转换的文件')\n"
        "print(json.dumps(list(f)))\n"
    )
    try:
        proc = subprocess.run(
            python_cmd(code),
            capture_output=True,
            timeout=300,
            check=False,
        )
        out = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        return json.loads(out[-1]) if out else []
    except Exception:
        return []


def pick_directory() -> str | None:
    import subprocess

    code = (
        "import tkinter as tk, json\n"
        "from tkinter import filedialog\n"
        "r = tk.Tk(); r.withdraw()\n"
        "d = filedialog.askdirectory(title='选择输出目录')\n"
        "print(json.dumps(d))\n"
    )
    try:
        proc = subprocess.run(
            python_cmd(code), capture_output=True, timeout=300, check=False
        )
        out = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        val = json.loads(out[-1]) if out else ""
        return val or None
    except Exception:
        return None


def make_handler(state: UiState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "convcore-ui"

        def log_message(self, *args) -> None:  # 静音，不往控制台刷请求日志
            pass

        # ── 安全校验 ──

        def _host_ok(self) -> bool:
            """Host 必须是本机回环地址加本服务端口。

            防 DNS rebinding：攻击者把自己的域名解析到 127.0.0.1 后，浏览器认为
            页面与本服务同源，Origin 和 Host 都是攻击者域名、彼此一致，
            _origin_ok 拦不住；首页又会吐出 token。只认回环主机名才能切断这条链。
            """
            port = self.server.server_address[1]
            host = (self.headers.get("Host") or "").strip().lower()
            return host in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _authorized(self) -> bool:
            """token 校验 + Origin 校验。

            本地服务同样需要防护：别的网页可以让浏览器向 127.0.0.1 发请求，
            没有 token 就等于把文件系统操作暴露给任意网页。
            """
            if self.headers.get("X-Token") == state.token:
                return True
            # 首屏 HTML 通过 URL 里的 token 拿到，之后走 header
            qs = urlparse(self.path).query
            return f"token={state.token}" in qs

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return True  # 同源请求可能不带 Origin
            host = self.headers.get("Host", "")
            return origin in (f"http://{host}", f"https://{host}")

        # ── 响应helper ──

        def _json(self, obj: Any, code: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _err(self, msg: str, code: int = 400) -> None:
            self._json({"error": msg}, code)

        def _body(self) -> dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 4 * 1024 * 1024:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}

        # ── 路由 ──

        def do_GET(self) -> None:
            # 必须先于首页：首页里注入了 token
            if not self._host_ok():
                self._err("非法 Host", 403)
                return

            route = urlparse(self.path).path

            if route in ("/", "/index.html"):
                self._serve_static("index.html")
                return
            if route.startswith("/static/"):
                self._serve_static(route[len("/static/"):])
                return

            if not self._authorized():
                self._err("未授权", 403)
                return

            if route == "/api/env":
                self._json(state.environment())
            elif route == "/api/jobs":
                self._json({"jobs": state.snapshot()})
            elif route == "/api/pick":
                self._json({"paths": pick_files()})
            elif route == "/api/pick-dir":
                self._json({"path": pick_directory()})
            else:
                self._err("未知接口", 404)

        def do_POST(self) -> None:
            # 先把请求体读完再做校验：拒绝时若请求体还留在接收缓冲区，关连接会发 RST，
            # 客户端（Windows 上尤其明显）读 403 响应时直接报连接被中止，拿不到错误信息。
            # 上限 4MB 由 _body 保证，服务只监听回环地址。
            body = self._body()
            if not (self._host_ok() and self._authorized() and self._origin_ok()):
                self._err("未授权", 403)
                return

            route = urlparse(self.path).path

            try:
                if route == "/api/inspect":
                    self._json(state.inspect(body.get("path", "")))
                elif route == "/api/convert":
                    items = body.get("items") or []
                    if not items:
                        self._err("没有待转换项")
                        return
                    for it in items:
                        if not isinstance(it, dict) or not it.get("path"):
                            self._err("转换项缺少文件路径")
                            return
                        source = Path(str(it["path"])).expanduser()
                        if not source.is_file():
                            self._err(f"文件不存在：{source.name}")
                            return
                        if resolve_target(it.get("target", "")) is None:
                            self._err(f"未知目标格式：{it.get('target')}")
                            return
                    ids = state.enqueue(items, body.get("preset") or None)
                    self._json({"ids": ids})
                elif route == "/api/merge-images":
                    paths = body.get("paths") or []
                    target = body.get("target", "")
                    if len(paths) < 2:
                        self._err("图片合并至少需要两个文件")
                        return
                    if resolve_target(target) is None:
                        self._err(f"未知目标格式：{target}")
                        return
                    for raw in paths:
                        if not Path(str(raw)).expanduser().is_file():
                            self._err(f"文件不存在：{raw}")
                            return
                    ids = state.merge_images(
                        [str(p) for p in paths], target, body.get("preset") or None
                    )
                    self._json({"ids": ids})
                elif route == "/api/concat":
                    paths = body.get("paths") or []
                    target = body.get("target", "")
                    if len(paths) < 2:
                        self._err("合并至少需要两个文件")
                        return
                    if resolve_target(target) is None:
                        self._err(f"未知目标格式：{target}")
                        return
                    for raw in paths:
                        if not Path(str(raw)).expanduser().is_file():
                            self._err(f"文件不存在：{raw}")
                            return
                    ids = state.concat(
                        [str(p) for p in paths], target, body.get("preset") or None
                    )
                    self._json({"ids": ids})
                elif route == "/api/progress":
                    self._json({"jobs": state.snapshot(body.get("ids"))})
                elif route == "/api/cancel":
                    self._json({"ok": state.cancel(body.get("id", ""))})
                elif route == "/api/reveal":
                    self._json({"ok": reveal_path(body.get("path", ""))})
                elif route == "/api/out-dir":
                    d = body.get("path")
                    state.out_dir = Path(d) if d else None
                    self._json({"path": str(state.out_dir) if state.out_dir else None})
                else:
                    self._err("未知接口", 404)
            except ConversionError as exc:
                self._err(str(exc))
            except Exception as exc:  # 兜底，别让服务因单个请求崩掉
                self._err(f"内部错误：{exc}", 500)

        def _serve_static(self, rel: str) -> None:
            # 只从 static 目录取文件，且解析后必须仍在目录内——防路径穿越
            target = (STATIC / rel).resolve()
            try:
                target.relative_to(STATIC.resolve())
            except ValueError:
                self._err("非法路径", 403)
                return
            if not target.is_file():
                self._err("找不到文件", 404)
                return

            data = target.read_bytes()
            if target.name == "index.html":
                # token 注入首屏，前端后续请求带在 header 里
                data = data.replace(b"__TOKEN__", state.token.encode())

            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def serve(
    *,
    port: int = 0,
    open_browser: bool = True,
    out_dir: Path | None = None,
) -> None:
    """起本地服务并打开浏览器。port=0 表示让系统分配随机端口。"""
    import webbrowser

    state = UiState(out_dir=out_dir)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    actual = httpd.server_address[1]
    url = f"http://127.0.0.1:{actual}/?token={state.token}"

    # flush 是必需的：管道里 stdout 默认全缓冲，不刷的话 URL 要等缓冲区满才出来，
    # 用户看不到该访问哪个地址
    print(f"convcore UI  {url}", flush=True)
    print("按 Ctrl+C 停止", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
