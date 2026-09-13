"""原生窗口与 JS 桥。

架构上比 Web 版简单：不需要 HTTP 服务，前端直接通过 js_api 调 Python 方法。
少一层网络边界，也少一层鉴权顾虑（没有端口暴露给别的进程）。

线程模型是这里唯一需要小心的地方：webview 的事件循环占用主线程，转换必须
跑在工作线程；而 window.evaluate_js 可以从任意线程调，用它把进度推给前端。
"""

from __future__ import annotations

import json
import threading
import traceback
from pathlib import Path
from typing import Any

from .._paths import resource
from ..formats import domain_of, gated_types, label_of, primary_extension, resolve_target
from ..pipeline import Converter, convert_file
from ..policy import PRESETS
from ..types import ConversionError, LossTier

STATIC = resource("convcore", "webui", "static")

_TIER = {
    LossTier.COPY: "copy",
    LossTier.LOSSLESS: "lossless",
    LossTier.LOSSY: "lossy",
    LossTier.STRUCTURAL: "structural",
    LossTier.INFERRED: "inferred",
}

# 各源格式最常用的目标。用于选默认值——按"最保真"排会给 PNG 默认选 BMP，
# 技术上无损但实际没人要。这张表编码真实使用偏好。
PREFERRED: dict[str, str] = {
    # 图片：手机照片和截图基本都是转通用格式
    "image/heic": "image/jpeg",
    "image/avif": "image/jpeg",
    "image/webp": "image/jpeg",
    "image/png": "image/jpeg",
    "image/jpeg": "image/webp",
    "image/tiff": "image/jpeg",
    "image/bmp": "image/png",
    "image/vnd.adobe.photoshop": "image/png",
    "image/svg+xml": "image/png",
    # RAW 显影成 JPEG
    "image/x-canon-cr2": "image/jpeg",
    "image/x-canon-cr3": "image/jpeg",
    "image/x-nikon-nef": "image/jpeg",
    "image/x-sony-arw": "image/jpeg",
    "image/x-adobe-dng": "image/jpeg",
    # 视频：MP4 是通用容器
    "video/x-matroska": "video/mp4",
    "video/quicktime": "video/mp4",
    "video/x-msvideo": "video/mp4",
    "video/x-flv": "video/mp4",
    "video/webm": "video/mp4",
    "video/x-ms-wmv": "video/mp4",
    "video/mp2t": "video/mp4",
    "video/mp4": "audio/mpeg",  # 已是 MP4 时最常见的需求是抽音轨
    # 音频
    "audio/wav": "audio/flac",
    "audio/flac": "audio/mpeg",
    "audio/aac": "audio/mpeg",
    "audio/ogg": "audio/mpeg",
    "audio/x-ms-wma": "audio/mpeg",
    "audio/x-aiff": "audio/flac",
    "audio/opus": "audio/mpeg",
    # 文档
    "application/pdf": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    "text/markdown": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        "text/markdown"
    ),
    "text/html": "text/markdown",
    "application/epub+zip": "text/markdown",
    "text/plain": "text/markdown",
    # 数据
    "application/json": "text/csv",
    "application/yaml": "application/json",
    "application/toml": "application/json",
    "application/xml": "application/json",
    "text/csv": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/tab-separated-values": "text/csv",
    "application/x-ndjson": "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "text/csv",
    # 归档：ZIP 最通用
    "application/x-7z-compressed": "application/zip",
    "application/vnd.rar": "application/zip",
    "application/x-tar": "application/zip",
    "application/gzip": "application/zip",
    "application/x-bzip2": "application/zip",
    "application/x-xz": "application/zip",
    "application/zip": "application/x-7z-compressed",
    # 字体：网页用 WOFF2
    "font/ttf": "font/woff2",
    "font/otf": "font/woff2",
    "font/woff": "font/woff2",
    "font/woff2": "font/ttf",
}

DOMAIN_LABEL = {
    "raster": "图片",
    "vector": "矢量图",
    "media": "音视频",
    "document": "PDF / Word",
    "markup": "文档与电子书",
    "tree": "结构化数据",
    "table": "表格",
    "archive": "压缩包",
    "font": "字体",
    "mesh": "3D 模型",
}


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
        self._conv = Converter()
        self._window: Any = None
        self._out_dir: Path | None = None
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    # ── 环境 ──

    def env(self) -> dict[str, Any]:
        from ..engines import probe_all

        reg = self._conv.registry
        by_domain: dict[str, int] = {}
        for mt in reg.writable():
            if d := domain_of(mt):
                by_domain[d] = by_domain.get(d, 0) + 1

        return {
            "readable": len(reg.readable()),
            "writable": len(reg.writable()),
            "plugins": sorted(p.name for p in reg.plugins),
            "unavailable": reg.unavailable,
            "domains": [
                {"key": k, "label": DOMAIN_LABEL.get(k, k), "count": v}
                for k, v in sorted(by_domain.items(), key=lambda kv: -kv[1])
            ],
            "engines": [
                {
                    "key": i.spec.key,
                    "ok": i.available,
                    "tier": i.spec.tier,
                    "enables": i.spec.enables,
                    "size": i.spec.size_hint,
                    "hint": i.spec.install_hint,
                    "version": (i.version or "").split("Copyright")[0].strip()[:60],
                }
                for i in probe_all()
            ],
            "presets": [
                {"name": p.name, "label": p.label, "desc": p.description}
                for p in PRESETS.values()
            ],
            "outDir": str(self._out_dir) if self._out_dir else None,
        }

    def formats_data(self) -> list[dict[str, Any]]:
        """全格式能力矩阵，帮助面板用。

        每个域一组：读得进的、写得出的、以及经桥接能跨域到达的目标域。
        用户最常问的问题是「我这文件到底能转去哪」——这里给完整答案，
        而不是只给当前文件的可达列表（那是 inspect 的职责）。
        """
        from ..formats import _TABLE

        reg = self._conv.registry
        readable, writable = reg.readable(), reg.writable()

        # 每个域里可读/可写的 MIME 各自去哪：直接报目标域，不展开逐对矩阵
        # （可读+可写各域的格子没人能读）。域间的可达性由 BRIDGES 决定。
        from ..graph import BRIDGES

        # 哪些域活着（有插件且至少一边是活的）
        live_domains: set[str] = set()
        for mt in readable | writable:
            if d := domain_of(mt):
                live_domains.add(d)

        # 域 → 可达目标域（含自身：经枢纽同域互转）
        reach: dict[str, set[str]] = {}
        for d in live_domains:
            reach[d] = {d}
        for src_d, dst_d, _, _, _ in BRIDGES:
            if src_d in live_domains and dst_d in live_domains:
                reach.setdefault(src_d, set()).add(dst_d)

        groups: dict[str, list[dict[str, Any]]] = {}
        for mt, domain, exts, label in _TABLE:
            r, w = mt in readable, mt in writable
            if not r and not w:
                continue  # 既读不了也写不了的格式不列——列出来只会误导
            groups.setdefault(domain, []).append(
                {
                    "mime": mt,
                    "label": label,
                    "ext": exts[0].lstrip("."),
                    "read": r,
                    "write": w,
                    "readonly": r and not w,
                }
            )

        out: list[dict[str, Any]] = []
        for d in sorted(groups, key=lambda k: -len(groups[k])):
            items = groups[d]
            out.append(
                {
                    "key": d,
                    "label": DOMAIN_LABEL.get(d, d),
                    "count": len(items),
                    "items": items,
                    "reaches": sorted(reach.get(d, {d})),
                }
            )
        return out

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
            self._out_dir = Path(result[0])
            return str(self._out_dir)
        return None

    def clear_out_dir(self) -> None:
        self._out_dir = None

    # ── 探测 ──

    def inspect(self, raw: str) -> dict[str, Any]:
        """识别文件并列出可达目标，按域分组。"""
        path = Path(raw)
        if not path.is_file():
            return {"error": f"文件不存在：{raw}"}

        try:
            src_mt = self._conv.detect(path)
        except ConversionError as exc:
            return {"error": str(exc)}

        try:
            targets = self._conv.targets_for(src_mt)
            actual = self._conv.refine_targets(path, src_mt, targets)
        except Exception as exc:
            return {"error": f"分析可用目标失败：{exc}"}

        # 按域分组，组内按保真度排序。全部格式平铺成一个下拉框没法用。
        groups: dict[str, list[dict[str, Any]]] = {}
        order = list(_TIER.values())
        for mt, cpath in targets.items():
            tier = actual[mt]
            d = domain_of(mt) or "other"
            groups.setdefault(d, []).append(
                {
                    "mime": mt,
                    "label": label_of(mt),
                    "ext": (primary_extension(mt) or "").lstrip("."),
                    "tier": _TIER[tier],
                    "tierLabel": tier.label,
                    "better": tier < cpath.loss,
                    "reversible": tier.is_reversible,
                    "path": cpath.describe(),
                }
            )
        for items in groups.values():
            items.sort(key=lambda d: (order.index(d["tier"]), d["label"]))

        src_domain = domain_of(src_mt) or "other"
        # 同域目标排最前——用户多数时候是在同域内转换
        group_list = sorted(
            (
                {"key": k, "label": DOMAIN_LABEL.get(k, k), "items": v}
                for k, v in groups.items()
            ),
            key=lambda g: (g["key"] != src_domain, -len(g["items"])),
        )

        # 门控格式：源本身读不了/写不了（如 Word 97 无 soffice）。此时 targets
        # 是空的，但要给可执行的指引而不是干巴巴的「没有可用目标」——缺的不是
        # 转换路径，是一个可选的引擎。
        gated = gated_types(self._conv.registry.readable(), self._conv.registry.writable())
        gated_hint = None
        if src_mt in gated:
            gated_hint = (
                f"{label_of(src_mt)} 的读写依赖 LibreOffice，当前环境未装。"
                "装上后即可转换；未装时该格式不可用。"
            )

        return {
            "path": str(path),
            "name": path.name,
            "size": path.stat().st_size,
            "mime": src_mt,
            "label": label_of(src_mt),
            "domain": src_domain,
            "domainLabel": DOMAIN_LABEL.get(src_domain, src_domain),
            "detail": self._detail(path, src_mt),
            "groups": group_list,
            "gatedHint": gated_hint,
            "default": self._default_target(src_mt, group_list),
        }

    def _default_target(self, src_mt: str, groups: list[dict]) -> str | None:
        """挑一个合理的默认目标。

        不能简单取"最保真的第一个"——那会给 PNG 默认选 BMP，技术上无损但
        没人要。实际最常用的转换是有偏好的，这张表编码那些偏好。
        """
        pref = PREFERRED.get(src_mt)
        if pref:
            for g in groups:
                for item in g["items"]:
                    if item["mime"] == pref:
                        return pref

        # 没有偏好时退回同域第一个非自身目标
        for g in groups:
            for item in g["items"]:
                if item["mime"] != src_mt:
                    return item["mime"]
        return None

    def _detail(self, path: Path, mime: str) -> str:
        d = domain_of(mime)
        try:
            if d == "media":
                from ..engines import ffmpeg
                from ..types import Resource

                plan = ffmpeg.analyze(Resource(media_type=mime, path=path), path.parent)
                return plan.describe()

            if mime == "application/pdf":
                from ..plugins.document import analyze
                from ..types import Resource

                return analyze(
                    Resource(media_type=mime, path=path), path.parent
                ).describe()

            if d in ("raster", "vector"):
                from PIL import Image

                with Image.open(path) as im:
                    bits = [f"{im.width}×{im.height}", im.mode]
                    if getattr(im, "n_frames", 1) > 1:
                        bits.append(f"{im.n_frames} 帧")
                    if im.info.get("icc_profile"):
                        bits.append("ICC")
                    return " · ".join(bits)

            if d == "archive":
                import zipfile

                if zipfile.is_zipfile(path):
                    with zipfile.ZipFile(path) as z:
                        n = sum(1 for i in z.infolist() if not i.is_dir())
                        total = sum(i.file_size for i in z.infolist())
                        return f"{n} 个文件 · 解压后 {total / 1e6:.1f}MB"
        except Exception:
            return ""
        return ""

    # ── 转换 ──

    # 终态任务保留上限。前端持有每条任务的结果引用，但列表清空后
    # 这些条目就是纯内存占用——保留最近 300 条供重启后找回产物。
    _KEEP_DONE = 300

    def _prune(self) -> None:
        """清扫终态任务，保留最近 _KEEP_DONE 条。convert 前调用。"""
        done = [
            (jid, j) for jid, j in self._jobs.items()
            if j.get("state") in ("done", "failed", "cancelled")
        ]
        excess = len(done) - self._KEEP_DONE
        if excess <= 0:
            return
        for jid, _ in done[:excess]:
            del self._jobs[jid]

    def convert(self, items: list[dict], preset: str | None, opts: dict) -> list[str]:
        """启动转换。立即返回任务 ID，进度通过 evaluate_js 推给前端。"""
        import secrets

        ids: list[str] = []
        with self._lock:
            self._prune()
            out_dir = self._out_dir
        # 在入队时固定输出目录，并为同批同名产物预先消歧。
        seen: dict[tuple[str, str], int] = {}
        with self._lock:
            for it in items:
                jid = secrets.token_hex(5)
                src = Path(it["path"])
                key = (str(out_dir or src.parent), src.stem)
                n = seen.get(key, 0)
                seen[key] = n + 1
                stem = src.stem if n == 0 else f"{src.stem}-{src.suffix.lstrip('.') or n}"
                self._jobs[jid] = {
                    "id": jid,
                    "name": src.name,
                    "state": "pending",
                    "progress": 0.0,
                    "step": "",
                    "out_dir": str(out_dir) if out_dir else None,
                    "stem": stem,
                }
                self._cancel[jid] = threading.Event()
                ids.append(jid)

        for jid, it in zip(ids, items, strict=False):
            threading.Thread(
                target=self._run_one,
                args=(jid, it, preset, opts or {}),
                daemon=True,
            ).start()
        return ids

    def _run_one(self, jid: str, item: dict, preset: str | None, opts: dict) -> None:
        src = Path(item["path"])
        target = item["target"]
        cancel = self._cancel[jid]
        with self._lock:
            job_meta = dict(self._jobs[jid])

        def push(**kw: Any) -> None:
            # 读-改-写必须原子：工作线程与取消/完成路径可能并发更新同一任务
            with self._lock:
                job = self._jobs[jid]
                job.update(kw)
            self._emit(job)

        push(state="running", step="准备")

        def on_progress(frac: float, step: str) -> None:
            if cancel.is_set():
                raise ConversionError("已取消")
            push(progress=max(0.0, min(1.0, frac)), step=step)

        try:
            if resolve_target(target) is None:
                raise ConversionError(f"未知目标格式：{target}")

            overrides = {
                k: v for k, v in opts.items() if v not in (None, "", False)
            }
            overrides["_cancel"] = lambda: cancel.is_set()

            res = convert_file(
                src,
                target,
                out_dir=Path(job_meta["out_dir"]) if job_meta.get("out_dir") else src.parent,
                preset=preset or None,
                overrides=overrides,
                overwrite=False,
                progress=on_progress,
                converter=Converter(),
                stem=job_meta.get("stem"),
            )

            if cancel.is_set():
                push(state="cancelled", step="已取消")
                return

            first = res.outputs[0]
            push(
                state="done",
                progress=1.0,
                step="完成",
                outPath=str(first.path),
                outName=first.path.name if first.path else "",
                outSize=first.path.stat().st_size if first.path else 0,
                srcSize=src.stat().st_size,
                loss=res.loss.label,
                tier=_TIER[res.loss],
                better=(
                    res.planned_loss is not None and res.loss < res.planned_loss
                ),
                pathDesc=res.path_desc,
                warnings=list(res.warnings),
                elapsed=round(res.elapsed, 2),
                extra=len(res.outputs) - 1,
            )
        except ConversionError as exc:
            if cancel.is_set():
                push(state="cancelled", step="已取消")
            else:
                push(state="failed", error=str(exc))
        except Exception as exc:
            push(state="failed", error=f"{type(exc).__name__}: {exc}")
            traceback.print_exc()

    def _emit(self, job: dict) -> None:
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
        if ev := self._cancel.get(jid):
            ev.set()
            return True
        return False

    def cancel_all(self) -> int:
        """取消所有尚未结束的任务。"""
        count = 0
        with self._lock:
            for jid, job in self._jobs.items():
                if job.get("state") in ("pending", "running"):
                    if self._cancel.get(jid):
                        self._cancel[jid].set()
                        count += 1
        return count

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
        import subprocess
        import sys

        p = Path(path)
        if not p.exists():
            return False
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["explorer", "/select,", str(p)], check=False, shell=False
                )
            elif sys.platform == "darwin":
                subprocess.run(["open", "-R", str(p)], check=False, shell=False)
            else:
                subprocess.run(["xdg-open", str(p.parent)], check=False, shell=False)
            return True
        except Exception:
            return False

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

        # 装完要重建注册表和格式图，否则新能力不生效
        self._conv = Converter()
        return {"ok": True, "version": res.version, "path": str(res.path)}


def _ui_html() -> str:
    """把 desktop.html 的 css/js 引用替换为内联内容后返回。

    pywebview 的 private_mode=False 让 WebView2 把 file:// 的 JS/CSS 存进
    磁盘缓存（AppData/Roaming/pywebview/EBWebView）。升级后 HTML 会重新
    读盘，但 <script src> 命中缓存时加载的还是旧 JS——v1.0 → v1.1 升级时
    真实踩到：Python 侧全是新代码，顶栏新按钮却不出现。

    内联成自包含 HTML 后没有任何子资源请求，缓存无从命中，改完必生效。
    实测（NavigateToString + about:blank 基址）：内联后 CSS 115 条规则全部
    生效、按钮绑定正常。
    """
    index = STATIC / "desktop.html"
    if not index.is_file():
        raise SystemExit(f"缺少界面文件：{index}")

    html = index.read_text(encoding="utf-8")

    css = (STATIC / "desktop.css").read_text(encoding="utf-8")
    html = html.replace(
        '<link rel="stylesheet" href="desktop.css">',
        f"<style>{css}</style>",
    )

    js = (STATIC / "desktop.js").read_text(encoding="utf-8")
    html = html.replace(
        '<script src="desktop.js"></script>',
        f"<script>{js}</script>",
    )
    return html


def run(*, debug: bool = False) -> None:
    """开原生窗口。"""
    import webview

    api = Api()

    window = webview.create_window(
        "convcore",
        html=_ui_html(),
        js_api=api,
        width=1000,
        height=760,
        min_size=(720, 560),
        # 不用系统默认背景色：加载瞬间的白闪在深色界面上很刺眼
        background_color="#0f1115",
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

    webview.start(debug=debug, private_mode=False)
