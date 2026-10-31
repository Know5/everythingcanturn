"""桌面端与浏览器端共用的界面服务。

两套前端（pywebview 的 js_api 和本地 HTTP）需要的是同一件事：
识别文件、列出目标、跑任务、报告进度、探测环境。传输层不同，但状态机相同。
这里放共享实现，前端文件只做传输适配，避免两份逻辑慢慢分叉。

`UiJob` 的 `to_dict()` 是 JS 侧看到的任务结构；两个前端都用它。
"""

from __future__ import annotations

import secrets
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .engines import probe_all
from .formats import domain_of, gated_types, label_of, primary_extension
from .pipeline import Converter, convert_file
from .policy import PRESETS
from .types import ConversionError, LossTier

TIER_NAME = {
    LossTier.COPY: "copy",
    LossTier.LOSSLESS: "lossless",
    LossTier.LOSSY: "lossy",
    LossTier.STRUCTURAL: "structural",
    LossTier.INFERRED: "inferred",
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
    "ocr": "OCR 文字识别",
}

# 各源格式最常用的目标。用于选默认值——按“最保真”排会给 PNG 默认选 BMP，
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


def _tier_index(tier: str) -> int:
    return list(TIER_NAME.values()).index(tier)


def _default_target(src_mt: str, groups: list[dict[str, Any]]) -> str | None:
    """挑一个合理的默认目标：偏好表优先，否则同域第一个非自身项。"""
    pref = PREFERRED.get(src_mt)
    if pref:
        for group in groups:
            for item in group["items"]:
                if item["mime"] == pref:
                    return pref
    for group in groups:
        for item in group["items"]:
            if item["mime"] != src_mt:
                return item["mime"]
    return None


def detail(path: Path, mime: str) -> str:
    """一行摘要：媒体给流信息，PDF 给页数，图片给尺寸，ZIP 给条目数。"""
    dom = domain_of(mime)
    try:
        if dom == "media":
            from .engines import ffmpeg

            plan = ffmpeg.analyze(
                _resource(mime, path), path.parent
            )
            return plan.describe()

        if mime == "application/pdf":
            from .plugins.document import analyze

            return analyze(_resource(mime, path), path.parent).describe()

        if dom in ("raster", "vector"):
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

        if dom == "archive":
            import zipfile

            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as z:
                    n = sum(1 for i in z.infolist() if not i.is_dir())
                    total = sum(i.file_size for i in z.infolist())
                    return f"{n} 个文件 · 解压后 {total / 1e6:.1f}MB"
    except Exception:
        return ""
    return ""


def _thumb_data(image) -> str:
    """把 PIL Image 缩成 160px 的 WebP data URI，供文件卡片直接展示。"""
    import base64
    import io

    image = image.copy()
    image.thumbnail((160, 160))
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=78, method=4)
    return "data:image/webp;base64," + base64.b64encode(buffer.getvalue()).decode()


def thumbnail(path: Path, mime: str) -> str | None:
    """生成卡片缩略图。只做图片和 PDF；其他格式没有廉价可靠的预览。"""
    dom = domain_of(mime)
    try:
        if dom == "raster":
            from PIL import Image

            with Image.open(path) as image:
                if getattr(image, "n_frames", 1) > 1:
                    image.seek(0)
                return _thumb_data(image)

        if mime == "application/pdf":
            import pymupdf
            from PIL import Image

            with pymupdf.open(path) as document:
                page = document[0]
                pix = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False)
                image = Image.frombytes(
                    "RGB", (pix.width, pix.height), pix.samples
                )
                return _thumb_data(image)
    except Exception:
        return None
    return None


def _resource(mime: str, path: Path):
    from .types import Resource

    return Resource(media_type=mime, path=path)


def inspect_file(converter: Converter, raw: str) -> dict[str, Any]:
    """识别文件并列出可达目标。

    返回同时包含分组视图（`groups`，桌面端）和平铺视图（`targets`，浏览器端）。
    两边字段一致，前端不必再各自拼一套。
    """
    path = Path(raw).expanduser()
    if not path.is_file():
        raise ConversionError(f"文件不存在：{raw}")

    src_mt = converter.detect(path)
    try:
        targets = converter.targets_for(src_mt)
        actual = converter.refine_targets(path, src_mt, targets)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"分析可用目标失败：{exc}") from exc

    groups: dict[str, list[dict[str, Any]]] = {}
    for mt, tier in actual.items():
        cpath = targets[mt]
        domain = domain_of(mt) or "other"
        groups.setdefault(domain, []).append(
            {
                "mime": mt,
                "label": label_of(mt),
                "ext": (primary_extension(mt) or "").lstrip("."),
                "tier": TIER_NAME[tier],
                "tierLabel": tier.label,
                "better": tier < cpath.loss,
                "reversible": tier.is_reversible,
                "path": cpath.describe(),
                "pathDesc": cpath.describe(),
                "domain": domain,
            }
        )

    for items in groups.values():
        items.sort(key=lambda d: (_tier_index(d["tier"]), d["label"]))

    src_domain = domain_of(src_mt) or "other"
    group_list = sorted(
        (
            {"key": k, "label": DOMAIN_LABEL.get(k, k), "items": v}
            for k, v in groups.items()
        ),
        key=lambda g: (g["key"] != src_domain, -len(g["items"])),
    )
    flat = [item for group in group_list for item in group["items"]]
    flat.sort(key=lambda d: (_tier_index(d["tier"]), d["label"]))

    gated = gated_types(
        converter.registry.readable(), converter.registry.writable()
    )
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
        "detail": detail(path, src_mt),
        "thumb": thumbnail(path, src_mt),
        "groups": group_list,
        "targets": flat,
        "gatedHint": gated_hint,
        "default": _default_target(src_mt, group_list),
    }


def environment(converter: Converter, out_dir: Path | None = None) -> dict[str, Any]:
    """前端启动时需要的环境快照。两种界面使用同一形状。"""
    reg = converter.registry
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
        "outDir": str(out_dir) if out_dir else None,
    }


def formats_data(converter: Converter) -> list[dict[str, Any]]:
    """全格式能力矩阵，帮助面板用。"""
    from .formats import _TABLE
    from .graph import BRIDGES, bridge_enabled

    reg = converter.registry
    readable, writable = reg.readable(), reg.writable()

    live_domains: set[str] = set()
    for mt in readable | writable:
        if d := domain_of(mt):
            live_domains.add(d)

    reach: dict[str, set[str]] = {d: {d} for d in live_domains}
    for src_d, dst_d, _, _, _ in BRIDGES:
        if (
            src_d in live_domains
            and dst_d in live_domains
            and bridge_enabled(src_d, dst_d)
        ):
            reach.setdefault(src_d, set()).add(dst_d)

    groups: dict[str, list[dict[str, Any]]] = {}
    for mt, domain, exts, label in _TABLE:
        r, w = mt in readable, mt in writable
        if not r and not w:
            continue
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


def reveal_path(path: str) -> bool:
    """在文件管理器里定位文件。"""
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


@dataclass
class UiJob:
    """一个转换任务。字段与 JS 侧的 Job 类型一一对应。"""

    id: str
    source: Path
    target: str
    preset: str | None
    overrides: dict[str, Any]
    out_dir: Path | None
    stem: str | None
    sources: list[Path] = field(default_factory=list)
    cue_path: Path | None = None
    cue_audio: Path | None = None
    src_size: int = 0
    state: str = "pending"
    progress: float = 0.0
    step: str = ""
    out_path: str | None = None
    out_name: str = ""
    out_size: int = 0
    loss: str = ""
    tier: str = ""
    better: bool = False
    path_desc: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    elapsed: float = 0.0
    extra: int = 0
    _cancel: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.source.name,
            "count": len(self.sources) if self.sources else 1,
            "size": self.src_size,
            "srcSize": self.src_size,
            "target": self.target,
            "state": self.state,
            "progress": round(self.progress, 4),
            "step": self.step,
            "outPath": self.out_path,
            "outName": self.out_name,
            "outSize": self.out_size,
            "loss": self.loss,
            "tier": self.tier,
            "lossMark": self.tier,
            "better": self.better,
            "pathDesc": self.path_desc,
            "warnings": list(self.warnings),
            "error": self.error,
            "elapsed": round(self.elapsed, 2),
            "extra": self.extra,
        }


class UiService:
    """两个前端共用的状态与任务执行器。"""

    _KEEP_DONE = 300

    def __init__(
        self,
        out_dir: Path | None = None,
        converter: Converter | None = None,
    ) -> None:
        self.converter = converter or Converter()
        self.out_dir = out_dir
        self.jobs: dict[str, UiJob] = {}
        self.lock = threading.RLock()

    # ── 查询 ──

    def inspect(self, raw: str) -> dict[str, Any]:
        return inspect_file(self.converter, raw)

    def environment(self) -> dict[str, Any]:
        return environment(self.converter, self.out_dir)

    def formats_data(self) -> list[dict[str, Any]]:
        return formats_data(self.converter)

    # ── 任务 ──

    def _prune(self) -> None:
        done = [
            jid for jid, job in self.jobs.items()
            if job.state in ("done", "failed", "cancelled")
        ]
        excess = len(done) - self._KEEP_DONE
        if excess <= 0:
            return
        for jid in done[:excess]:
            del self.jobs[jid]

    def enqueue(
        self,
        items: list[dict[str, Any]],
        preset: str | None = None,
        opts: dict[str, Any] | None = None,
        on_update: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[str]:
        """创建任务并启动工作线程。立即返回任务 ID。"""
        ids: list[str] = []
        base_opts = dict(opts or {})
        with self.lock:
            self._prune()
            out_dir = self.out_dir
            seen: dict[tuple[str, str], int] = {}
            for item in items:
                jid = secrets.token_hex(6)
                source = Path(item["path"]).expanduser()
                key = (str(out_dir or source.parent), source.stem)
                n = seen.get(key, 0)
                seen[key] = n + 1
                stem = (
                    source.stem
                    if n == 0
                    else f"{source.stem}-{source.suffix.lstrip('.') or n}"
                )
                self.jobs[jid] = UiJob(
                    id=jid,
                    source=source,
                    target=item["target"],
                    preset=preset,
                    overrides={**base_opts, **(item.get("overrides") or {})},
                    out_dir=out_dir,
                    stem=stem,
                    src_size=source.stat().st_size if source.is_file() else 0,
                )
                ids.append(jid)
        for jid in ids:
            threading.Thread(
                target=self._run, args=(jid, on_update), daemon=True
            ).start()
        return ids

    def enqueue_concat(
        self,
        paths: list[str],
        target: str,
        preset: str | None = None,
        opts: dict[str, Any] | None = None,
        on_update: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[str]:
        """把多个音视频文件合成一个任务。"""
        sources = [Path(p).expanduser() for p in paths]
        if len(sources) < 2:
            raise ConversionError("合并至少需要两个文件")
        for source in sources:
            if not source.is_file():
                raise ConversionError(f"文件不存在：{source}")
        from .formats import resolve_target
        from .formats import domain_of as _domain_of

        target_mt = resolve_target(target)
        if target_mt is None:
            raise ConversionError(f"未知目标格式：{target}")
        if _domain_of(target_mt) != "media":
            raise ConversionError("合并目标必须是音视频格式")
        for source in sources:
            if _domain_of(self.converter.detect(source)) != "media":
                raise ConversionError(f"不是音视频文件：{source.name}")

        jid = secrets.token_hex(6)
        with self.lock:
            self._prune()
            self.jobs[jid] = UiJob(
                id=jid,
                source=sources[0],
                sources=sources,
                target=target_mt,
                preset=preset,
                overrides=dict(opts or {}),
                out_dir=self.out_dir,
                stem=sources[0].stem,
                src_size=sum(s.stat().st_size for s in sources),
            )
        threading.Thread(
            target=self._run, args=(jid, on_update), daemon=True
        ).start()
        return [jid]

    def enqueue_merge_images(
        self,
        paths: list[str],
        target: str,
        preset: str | None = None,
        opts: dict[str, Any] | None = None,
        on_update: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[str]:
        """多张位图合成一张动图（GIF/WebP/TIFF）。"""
        sources = [Path(p).expanduser() for p in paths]
        if len(sources) < 2:
            raise ConversionError("图片合并至少需要两个文件")
        for source in sources:
            if not source.is_file():
                raise ConversionError(f"文件不存在：{source}")
        from .formats import resolve_target
        from .formats import domain_of as _domain_of

        target_mt = resolve_target(target)
        if target_mt is None:
            raise ConversionError(f"未知目标格式：{target}")
        if target_mt not in ("image/gif", "image/webp", "image/tiff"):
            raise ConversionError("图片合并目标只支持 GIF / WebP / TIFF")
        for source in sources:
            if _domain_of(self.converter.detect(source)) != "raster":
                raise ConversionError(f"不是位图文件：{source.name}")

        jid = secrets.token_hex(6)
        with self.lock:
            self._prune()
            self.jobs[jid] = UiJob(
                id=jid,
                source=sources[0],
                sources=sources,
                target=target_mt,
                preset=preset,
                overrides=dict(opts or {}),
                out_dir=self.out_dir,
                stem=f"{sources[0].stem}-merge",
                src_size=sum(s.stat().st_size for s in sources),
            )
        threading.Thread(
            target=self._run, args=(jid, on_update), daemon=True
        ).start()
        return [jid]

    def enqueue_cue(
        self,
        cue: str,
        target: str = "mp3",
        audio: Path | None = None,
        preset: str | None = None,
        opts: dict[str, Any] | None = None,
        on_update: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[str]:
        """CUE 分轨任务；一条 CUE 产生多个输出。"""
        from .cue import parse_cue
        from .formats import domain_of, resolve_target

        cue_path = Path(cue).expanduser()
        if not cue_path.is_file():
            raise ConversionError(f"CUE 文件不存在：{cue_path}")
        sheet = parse_cue(cue_path)
        if not sheet.tracks:
            raise ConversionError("CUE 里没有 TRACK 01")
        source = audio or sheet.audio
        if source is None:
            from .cue import _infer_audio

            source = _infer_audio(cue_path)
        if source is None or not source.is_file():
            raise ConversionError(
                "找不到 CUE 对应的音频文件；请把音频放在同目录同名，或先用 CLI --audio 指定"
            )
        target_mt = resolve_target(target)
        if target_mt is None:
            raise ConversionError(f"未知目标格式：{target}")
        if domain_of(target_mt) != "media":
            raise ConversionError("CUE 分轨目标必须是音频格式")

        jid = secrets.token_hex(6)
        with self.lock:
            self._prune()
            self.jobs[jid] = UiJob(
                id=jid,
                source=cue_path,
                sources=[source],
                cue_path=cue_path,
                cue_audio=source,
                target=target_mt,
                preset=preset,
                overrides=dict(opts or {}),
                out_dir=self.out_dir,
                stem=cue_path.stem,
                src_size=sum(p.stat().st_size for p in (cue_path, source)),
            )
        threading.Thread(
            target=self._run, args=(jid, on_update), daemon=True
        ).start()
        return [jid]

    def _run(
        self,
        jid: str,
        on_update: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        job = self.jobs[jid]

        def push(**kw: Any) -> None:
            with self.lock:
                for key, value in kw.items():
                    setattr(job, key, value)
                payload = job.to_dict()
            if on_update is not None:
                try:
                    on_update(payload)
                except Exception:
                    pass  # 前端/窗口已关闭，不该影响任务

        push(state="running", step="准备")

        def on_progress(frac: float, step: str) -> None:
            if job._cancel.is_set():
                raise ConversionError("已取消")
            push(progress=max(0.0, min(1.0, frac)), step=step)

        try:
            overrides = {
                k: v for k, v in job.overrides.items() if v not in (None, "", False)
            }
            overrides["_cancel"] = lambda: job._cancel.is_set()

            if job.cue_path is not None:
                from .cue import split_cue

                paths = split_cue(
                    job.cue_path,
                    job.target,
                    audio=job.cue_audio,
                    out_dir=job.out_dir or job.cue_path.parent,
                    preset=job.preset,
                    overwrite=False,
                    progress=on_progress,
                )
                if job._cancel.is_set():
                    push(state="cancelled", step="已取消")
                    return
                first = paths[0]
                push(
                    state="done",
                    progress=1.0,
                    step="完成",
                    out_path=str(first),
                    out_name=first.name,
                    out_size=first.stat().st_size,
                    loss="有损",
                    tier="lossy",
                    path_desc=f"CUE → {len(paths)} 轨",
                    warnings=[f"已按 CUE 分成 {len(paths)} 个文件"],
                    extra=len(paths) - 1,
                )
                return

            if len(job.sources) > 1:
                from .formats import domain_of

                if domain_of(job.target) == "raster":
                    from .pipeline import merge_files

                    res = merge_files(
                        job.sources,
                        job.target,
                        out_dir=job.out_dir or job.source.parent,
                        preset=job.preset,
                        overrides=overrides,
                        overwrite=False,
                        progress=on_progress,
                    )
                else:
                    from .plugins.media import concat_media

                    res = concat_media(
                        job.sources,
                        job.target,
                        out_dir=job.out_dir or job.source.parent,
                        preset=job.preset,
                        options=overrides,
                        overwrite=False,
                        progress=on_progress,
                    )
            else:
                res = convert_file(
                    job.source,
                    job.target,
                    out_dir=job.out_dir or job.source.parent,
                    preset=job.preset,
                    overrides=overrides,
                    overwrite=False,
                    progress=on_progress,
                    converter=Converter(),
                    stem=job.stem,
                )

            if job._cancel.is_set():
                push(state="cancelled", step="已取消")
                return

            first = res.outputs[0]
            warnings = list(res.warnings)
            extra = len(res.outputs) - 1
            if extra > 0:
                warnings.insert(0, f"产出 {len(res.outputs)} 个文件")
            push(
                state="done",
                progress=1.0,
                step="完成",
                out_path=str(first.path),
                out_name=first.path.name if first.path else "",
                out_size=first.path.stat().st_size if first.path else 0,
                src_size=job.src_size,
                loss=res.loss.label,
                tier=TIER_NAME[res.loss],
                better=(
                    res.planned_loss is not None and res.loss < res.planned_loss
                ),
                path_desc=res.path_desc,
                warnings=warnings,
                elapsed=round(res.elapsed, 2),
                extra=extra,
            )
        except ConversionError as exc:
            if job._cancel.is_set():
                push(state="cancelled", step="已取消")
            else:
                push(state="failed", error=str(exc))
        except Exception as exc:
            push(state="failed", error=f"{type(exc).__name__}: {exc}")
            traceback.print_exc()

    def cancel(self, jid: str) -> bool:
        job = self.jobs.get(jid)
        if job is None:
            return False
        job._cancel.set()
        return True

    def cancel_all(self) -> int:
        count = 0
        with self.lock:
            for job in self.jobs.values():
                if job.state in ("pending", "running") and not job._cancel.is_set():
                    job._cancel.set()
                    count += 1
        return count

    def snapshot(self, ids: list[str] | None = None) -> list[dict[str, Any]]:
        with self.lock:
            jobs = (
                [self.jobs[i] for i in ids if i in self.jobs]
                if ids
                else list(self.jobs.values())
            )
            return [job.to_dict() for job in jobs]
