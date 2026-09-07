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
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .._paths import python_cmd, resource
from ..formats import domain_of, label_of, primary_extension, resolve_target
from ..pipeline import Converter, convert_file
from ..policy import PRESETS
from ..types import ConversionError, LossTier

STATIC = resource("convcore", "webui", "static")


# 单个待转任务在服务端的状态。前端靠轮询读它。
@dataclass
class UiJob:
    id: str
    source: Path
    target: str
    preset: str | None = None
    overrides: dict[str, Any] = field(default_factory=dict)
    out_dir: Path | None = None
    stem: str | None = None
    state: str = "pending"
    progress: float = 0.0
    step: str = ""
    out_path: str | None = None
    out_size: int = 0
    loss: str = ""
    loss_mark: str = ""
    path_desc: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    elapsed: float = 0.0
    _cancel: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.source.name,
            "size": self.source.stat().st_size if self.source.exists() else 0,
            "target": self.target,
            "state": self.state,
            "progress": round(self.progress, 4),
            "step": self.step,
            "outPath": self.out_path,
            "outSize": self.out_size,
            "loss": self.loss,
            "lossMark": self.loss_mark,
            "pathDesc": self.path_desc,
            "warnings": self.warnings,
            "error": self.error,
            "elapsed": round(self.elapsed, 2),
        }


_TIER_MARK = {
    LossTier.COPY: "copy",
    LossTier.LOSSLESS: "lossless",
    LossTier.LOSSY: "lossy",
    LossTier.STRUCTURAL: "structural",
    LossTier.INFERRED: "inferred",
}


class UiState:
    """服务端状态。引擎和任务表都在这里，跨请求共享。"""

    def __init__(self, out_dir: Path | None = None) -> None:
        self.converter = Converter()
        self.jobs: dict[str, UiJob] = {}
        self.out_dir = out_dir
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(24)

    # ── 查询 ──

    def inspect(self, raw: str) -> dict[str, Any]:
        """识别文件并列出可达目标（含逐文件的真实损失等级）。"""
        path = Path(raw).expanduser()
        if not path.is_file():
            raise ConversionError(f"文件不存在：{raw}")

        src_mt = self.converter.detect(path)
        targets = self.converter.targets_for(src_mt)
        actual = self.converter.refine_targets(path, src_mt, targets)

        items = []
        for mt, cpath in targets.items():
            tier = actual[mt]
            items.append(
                {
                    "mime": mt,
                    "label": label_of(mt),
                    "ext": (primary_extension(mt) or "").lstrip("."),
                    "tier": _TIER_MARK[tier],
                    "tierLabel": tier.label,
                    "better": tier < cpath.loss,
                    "reversible": tier.is_reversible,
                    "pathDesc": cpath.describe(),
                    "domain": domain_of(mt) or "",
                }
            )
        # 最保真的排前面——这正是下拉框该有的顺序
        items.sort(key=lambda d: (list(_TIER_MARK.values()).index(d["tier"]), d["label"]))

        return {
            "path": str(path),
            "name": path.name,
            "size": path.stat().st_size,
            "mime": src_mt,
            "label": label_of(src_mt),
            "domain": domain_of(src_mt) or "",
            "detail": self._detail(path, src_mt),
            "targets": items,
        }

    def _detail(self, path: Path, mime: str) -> str:
        """一行摘要：媒体给流信息，图片给尺寸。"""
        dom = domain_of(mime)
        if dom == "media":
            try:
                from ..engines import ffmpeg
                from ..types import Resource

                plan = ffmpeg.analyze(Resource(media_type=mime, path=path), path.parent)
                return plan.describe()
            except Exception:
                return ""
        if dom in ("raster", "vector"):
            try:
                from PIL import Image

                with Image.open(path) as im:
                    bits = [f"{im.width}×{im.height}", im.mode]
                    if getattr(im, "n_frames", 1) > 1:
                        bits.append(f"{im.n_frames} 帧")
                    if im.info.get("icc_profile"):
                        bits.append("ICC")
                    if im.info.get("exif"):
                        bits.append("EXIF")
                    return " · ".join(bits)
            except Exception:
                return ""
        return ""

    # ── 转换 ──

    def enqueue(self, items: list[dict[str, Any]], preset: str | None) -> list[str]:
        ids: list[str] = []
        with self.lock:
            out_dir = self.out_dir
        seen: dict[tuple[str, str], int] = {}
        with self.lock:
            for it in items:
                jid = secrets.token_hex(6)
                source = Path(it["path"])
                key = (str(out_dir or source.parent), source.stem)
                n = seen.get(key, 0)
                seen[key] = n + 1
                stem = source.stem if n == 0 else f"{source.stem}-{source.suffix.lstrip('.') or n}"
                self.jobs[jid] = UiJob(
                    id=jid,
                    source=source,
                    target=it["target"],
                    preset=preset,
                    overrides=it.get("overrides") or {},
                    out_dir=out_dir,
                    stem=stem,
                )
                ids.append(jid)
        for jid in ids:
            threading.Thread(target=self._run, args=(jid,), daemon=True).start()
        return ids

    def _run(self, jid: str) -> None:
        job = self.jobs[jid]
        job.state = "running"

        def on_progress(frac: float, step: str) -> None:
            job.progress, job.step = frac, step

        try:
            out_dir = job.out_dir or job.source.parent
            res = convert_file(
                job.source,
                job.target,
                out_dir=out_dir,
                preset=job.preset,
                overrides={**job.overrides, "_cancel": lambda: job._cancel.is_set()},
                overwrite=False,
                progress=on_progress,
                converter=Converter(),
                stem=job.stem,
            )
            if job._cancel.is_set():
                job.state = "cancelled"
                return

            first = res.outputs[0]
            job.out_path = str(first.path)
            job.out_size = first.path.stat().st_size if first.path else 0
            job.loss = res.loss.label
            job.loss_mark = _TIER_MARK[res.loss]
            job.path_desc = res.path_desc
            job.warnings = list(res.warnings)
            job.elapsed = res.elapsed
            job.progress = 1.0
            job.state = "done"
            if len(res.outputs) > 1:
                job.warnings.insert(0, f"产出 {len(res.outputs)} 个文件")
        except Exception as exc:
            if job._cancel.is_set():
                job.state = "cancelled"
            else:
                job.state = "failed"
                job.error = str(exc)

    def cancel(self, jid: str) -> bool:
        job = self.jobs.get(jid)
        if job is None:
            return False
        job._cancel.set()
        return True

    def snapshot(self, ids: list[str] | None = None) -> list[dict[str, Any]]:
        with self.lock:
            jobs = [self.jobs[i] for i in ids if i in self.jobs] if ids else list(self.jobs.values())
        return [j.to_dict() for j in jobs]

    def environment(self) -> dict[str, Any]:
        from ..engines import probe_all

        reg = self.converter.registry
        engines = []
        for info in probe_all():
            engines.append(
                {
                    "key": info.spec.key,
                    "available": info.available,
                    "tier": info.spec.tier,
                    "enables": info.spec.enables,
                    "size": info.spec.size_hint,
                    "hint": info.spec.install_hint,
                }
            )
        domains: dict[str, int] = {}
        for mt in reg.writable():
            d = domain_of(mt) or "other"
            domains[d] = domains.get(d, 0) + 1
        return {
            "readable": len(reg.readable()),
            "writable": len(reg.writable()),
            "plugins": sorted(p.name for p in reg.plugins),
            "unavailable": reg.unavailable,
            "engines": engines,
            "domains": domains,
            "presets": [
                {"name": p.name, "label": p.label, "desc": p.description}
                for p in PRESETS.values()
            ],
        }


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


def reveal(path: str) -> bool:
    """在文件管理器里定位文件。转换完能直接跳过去，省一步。"""
    import subprocess
    import sys

    p = Path(path)
    if not p.exists():
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(["explorer", "/select,", str(p)], check=False, shell=False)
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", str(p)], check=False, shell=False)
        else:
            subprocess.run(["xdg-open", str(p.parent)], check=False, shell=False)
        return True
    except Exception:
        return False


def make_handler(state: UiState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "convcore-ui"

        def log_message(self, *args) -> None:  # 静音，不往控制台刷请求日志
            pass

        # ── 安全校验 ──

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
            if not (self._authorized() and self._origin_ok()):
                self._err("未授权", 403)
                return

            route = urlparse(self.path).path
            body = self._body()

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
                elif route == "/api/progress":
                    self._json({"jobs": state.snapshot(body.get("ids"))})
                elif route == "/api/cancel":
                    self._json({"ok": state.cancel(body.get("id", ""))})
                elif route == "/api/reveal":
                    self._json({"ok": reveal(body.get("path", ""))})
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
