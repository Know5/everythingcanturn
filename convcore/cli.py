"""命令行入口。

子命令：
  convert  转换文件
  formats  列出支持的格式
  targets  某个文件能转成什么（UI 下拉框的数据来源）
  path     显示转换路径和损失等级，不实际转换
  doctor   环境诊断
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ._paths import frozen
from .formats import label_of, resolve_target
from .policy import PRESETS
from .types import ConversionError, LossTier

# Windows 控制台可能是 GBK，输出中文和符号会炸。提前切成 UTF-8。
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def _tier_mark(tier: LossTier) -> str:
    return {
        LossTier.COPY: "⚡",
        LossTier.LOSSLESS: "✓",
        LossTier.LOSSY: "~",
        LossTier.STRUCTURAL: "⚠",
        LossTier.INFERRED: "⚠⚠",
    }[tier]


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def cmd_convert(args: argparse.Namespace) -> int:
    from .jobs import JobQueue, JobState

    sources = [Path(p) for p in args.inputs]
    missing = [p for p in sources if not p.is_file()]
    if missing:
        print(f"文件不存在：{', '.join(str(p) for p in missing)}", file=sys.stderr)
        return 2

    overrides = {
        k: v
        for k, v in (
            ("quality", args.quality),
            ("width", args.width),
            ("height", args.height),
            ("max_edge", args.max_edge),
            ("scale", args.scale),
            ("lossless", args.lossless or None),
            ("keep_metadata", False if args.strip else None),
            ("pages", args.pages),
            ("password", args.password),
            ("dpi", args.dpi),
            ("rotate", args.rotate),
            ("split", args.split or None),
            ("review_marks", False if args.no_review_marks else None),
            ("pdf_table_mode", args.pdf_table_mode),
            ("pdf_heading_styles", False if args.no_pdf_heading_styles else None),
            ("ocr_lang", args.ocr_lang),
            ("ocr_dpi", args.ocr_dpi),
            ("force", args.force or None),
        )
        if v is not None
    }

    out_dir = Path(args.out_dir) if args.out_dir else None
    single_out = Path(args.output) if args.output else None

    if getattr(args, "concat", False):
        if len(sources) < 2:
            print("concat 至少需要两个源文件", file=sys.stderr)
            return 2
        from .plugins.media import concat_media

        try:
            res = concat_media(
                sources,
                args.target,
                out_path=single_out,
                out_dir=out_dir,
                options=overrides,
                preset=args.preset,
                overwrite=args.overwrite,
                progress=None if args.quiet else _progress_printer(),
            )
        except ConversionError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 1
        _report(sources[0], res, quiet=args.quiet)
        return 0

    # 单文件 + -o 走直接路径，能精确控制输出文件名
    if single_out is not None and len(sources) == 1:
        from .pipeline import convert_file

        try:
            res = convert_file(
                sources[0],
                args.target,
                out_path=single_out,
                preset=args.preset,
                overrides=overrides,
                overwrite=args.overwrite,
                progress=_progress_printer() if not args.quiet else None,
            )
        except ConversionError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 1
        _report(sources[0], res, quiet=args.quiet)
        return 0

    queue = JobQueue()
    for job in queue.add_files(
        sources, args.target, out_dir=out_dir, preset=args.preset,
        overwrite=args.overwrite,
    ):
        job.overrides = overrides

    queue.run()

    failed = 0
    for job in queue:
        if job.state is JobState.DONE and job.result:
            _report(job.source, job.result, quiet=args.quiet)
        elif job.state is JobState.FAILED:
            failed += 1
            print(f"✗ {job.source.name}：{job.error}", file=sys.stderr)
        elif job.state is JobState.CANCELLED:
            print(f"- {job.source.name}：已取消")

    if len(sources) > 1 and not args.quiet:
        done = sum(1 for j in queue if j.state is JobState.DONE)
        print(f"\n共 {len(sources)} 个，成功 {done}，失败 {failed}")
    return 1 if failed else 0


def _progress_printer():
    """创建进度条打印回调函数。

    Returns:
        ProgressFn: 进度回调函数
    """
    def fn(frac: float, step: str) -> None:
        bar = "█" * int(frac * 20) + "░" * (20 - int(frac * 20))
        print(f"\r  {bar} {frac * 100:3.0f}% {step[:40]:<40}", end="", flush=True)
        if frac >= 1.0:
            print("\r" + " " * 72 + "\r", end="")

    return fn


def _report(source: Path, res, *, quiet: bool) -> None:
    if quiet:
        for out in res.outputs:
            print(out.path)
        return

    mark = _tier_mark(res.loss)
    src_size = source.stat().st_size
    for out in res.outputs:
        assert out.path is not None
        delta = ""
        if src_size:
            pct = (out.path.stat().st_size - src_size) / src_size * 100
            delta = f"（{pct:+.0f}%）"
        print(
            f"{mark} {source.name} → {out.path.name}  "
            f"{_human_size(src_size)} → {_human_size(out.path.stat().st_size)}{delta}"
        )
    # 实测优于静态声明时点出来——「本来以为要重编码，实际直接改封装」是好消息
    tier = res.loss.label
    if res.planned_loss is not None and res.loss < res.planned_loss:
        tier = f"{res.loss.label}（优于预估的{res.planned_loss.label}）"
    print(f"   {res.path_desc} · {tier} · {res.elapsed:.2f}s")
    for w in res.warnings:
        print(f"   ⚠ {w}")


def cmd_formats(args: argparse.Namespace) -> int:
    """列出全部格式与域间可达关系。

    [读写] 双向 · [读 ] 只读 · [ 写] 只写。
    """
    from .formats import _TABLE, domain_of, gated_types
    from .graph import BRIDGES, bridge_enabled
    from .pipeline import Converter

    conv = Converter()
    readable, writable = conv.registry.readable(), conv.registry.writable()
    gated = set(gated_types(readable, writable))

    by_domain: dict[str, list[tuple[str, str, str, bool]]] = {}
    for mt, domain, exts, label in _TABLE:
        r = mt in readable
        w = mt in writable
        live = r or w
        if not live:
            gated.add(mt)
            if args.available:
                continue
            # 门控格式标 [需LibreOffice] 而不是裸的 [  ]——后者读作「死了」，
            # 但实际上装上引擎就能用，是完全不同的信息。
            flags = "需LibreOffice"
        else:
            flags = f"{'读' if r else ' '}{'写' if w else ' '}"
        by_domain.setdefault(domain, []).append((flags, label, exts[0], live))

    # 域间可达：BRIDGES 里两端域都活着才算数。回答「什么能转什么」的域级答案。
    live = set()
    for mt in readable | writable:
        if d := domain_of(mt):
            live.add(d)
    reach: dict[str, set[str]] = {d: {d} for d in live}
    for src_d, dst_d, _, _, _ in BRIDGES:
        if src_d in live and dst_d in live and bridge_enabled(src_d, dst_d):
            reach.setdefault(src_d, set()).add(dst_d)

    for domain in sorted(by_domain):
        items = by_domain[domain]
        live_n = sum(1 for _, _, _, live in items if live)
        to = sorted(reach.get(domain, set()) - {domain})
        head = f"\n{domain}  （{live_n}/{len(items)} 已支持）"
        if to:
            head += f"  → 可转去：{'、'.join(to)}"
        print(head)
        for flags, label, ext, live in items:
            print(f"  [{flags}] {label:<14} {ext}")

    if gated:
        print("\n需装外部引擎才能用的格式（当前环境不可转换）：")
        print("  这些格式在格式表里，但读/写都依赖可选的 LibreOffice。"
              "装上引擎后自动变为可用，未装时形同不存在。")
        for mt in sorted(gated):
            print(f"  · {label_of(mt)}（{mt[:48]}）")

    if conv.registry.unavailable:
        print("\n未启用的插件（装了依赖即可用）：")
        for name, reason in sorted(conv.registry.unavailable.items()):
            print(f"  {name}: {reason}")
    return 0


def _media_summary(src: Path, src_mt: str) -> str | None:
    """媒体文件的流摘要。非媒体或缺 ffmpeg 时返回 None。"""
    from .formats import domain_of

    if domain_of(src_mt) != "media":
        return None
    try:
        from .engines import ffmpeg
        from .types import Resource

        plan = ffmpeg.analyze(Resource(media_type=src_mt, path=src), src.parent)
        return plan.describe()
    except Exception:
        return None


def cmd_info(args: argparse.Namespace) -> int:
    """显示文件的详细信息。媒体文件列出全部流。"""
    from .pipeline import Converter

    conv = Converter()
    src = Path(args.input)
    if not src.is_file():
        print(f"文件不存在：{src}", file=sys.stderr)
        return 2

    src_mt = conv.detect(src)
    print(f"{src.name}")
    print(f"  类型  {label_of(src_mt)}（{src_mt}）")
    print(f"  大小  {_human_size(src.stat().st_size)}")

    from .formats import domain_of

    if src_mt == "application/pdf":
        from .plugins.document import analyze
        from .types import Resource

        ref = analyze(Resource(media_type=src_mt, path=src), src.parent)
        print(f"  页数  {ref.page_count}")
        if ref.title:
            print(f"  标题  {ref.title}")
        if ref.producer:
            print(f"  生成  {ref.producer}")
        print(f"  概况  {ref.describe()}")
        if not ref.has_text_layer:
            print("  ⚠ 无文本层（扫描件），转 Word 需要先 OCR")
        elif ref.scanned_pages:
            pages = ", ".join(str(i + 1) for i in ref.scanned_pages[:10])
            print(f"  ⚠ 第 {pages} 页无文本层")
        print("  页面：")
        for p in ref.pages[:12]:
            print(f"    #{p.index + 1:<4} {p.describe()}")
        if ref.page_count > 12:
            print(f"    …… 另 {ref.page_count - 12} 页")
        return 0

    if domain_of(src_mt) == "media":
        from .engines import ffmpeg
        from .types import Resource

        plan = ffmpeg.analyze(Resource(media_type=src_mt, path=src), src.parent)
        if plan.container:
            print(f"  容器  {plan.container}")
        if plan.duration:
            print(f"  时长  {plan.duration:.2f}s")
        if plan.bit_rate:
            print(f"  码率  {plan.bit_rate // 1000}kbps")
        print("  流：")
        for s in plan.streams:
            tag = "封面" if s.is_attached_pic else s.kind
            default = " (默认)" if s.is_default else ""
            print(f"    #{s.index} {tag:<9} {s.describe()}{default}")
    else:
        try:
            from PIL import Image

            with Image.open(src) as im:
                frames = getattr(im, "n_frames", 1)
                print(f"  尺寸  {im.width}×{im.height}  模式 {im.mode}")
                if frames > 1:
                    print(f"  帧数  {frames}")
                if im.info.get("icc_profile"):
                    print("  ICC   有")
                if im.info.get("exif"):
                    print("  EXIF  有")
        except Exception:
            pass
    return 0


def cmd_targets(args: argparse.Namespace) -> int:
    from .pipeline import Converter

    conv = Converter()
    src = Path(args.input)
    if not src.is_file():
        print(f"文件不存在：{src}", file=sys.stderr)
        return 2

    try:
        src_mt = conv.detect(src)
    except ConversionError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    print(f"{src.name}  识别为 {label_of(src_mt)}（{src_mt}）")
    if (summary := _media_summary(src, src_mt)) is not None:
        print(f"  {summary}")
    print()
    targets = conv.targets_for(src_mt)
    if not targets:
        print("没有可用的转换目标")
        return 0

    # 针对这个具体文件算真实损失，而不是静态最坏情况；走不通的目标已被剔除
    actual = conv.refine_targets(src, src_mt, targets)
    if not actual:
        print("没有可用的转换目标")
        return 0

    # 按损失等级排序，最保真的在前——这正是 UI 下拉框该有的顺序
    for mt, tier in sorted(
        actual.items(), key=lambda kv: (kv[1], targets[kv[0]].time_cost, kv[0])
    ):
        path = targets[mt]
        note = ""
        if tier < path.loss:
            note = "  ← 免重编码" if tier is LossTier.COPY else "  ← 实测更保真"
        print(
            f"  {_tier_mark(tier)} {label_of(mt):<14} "
            f"{tier.label:<14} {path.describe()}{note}"
        )
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    from .pipeline import Converter

    conv = Converter()
    src_mt = resolve_target(args.source) if "/" in args.source or "." not in args.source else None
    if src_mt is None:
        p = Path(args.source)
        if p.is_file():
            src_mt = conv.detect(p)
        else:
            src_mt = resolve_target(args.source)
    if src_mt is None:
        print(f"无法解析源格式：{args.source}", file=sys.stderr)
        return 2

    dst_mt = resolve_target(args.target)
    if dst_mt is None:
        print(f"无法解析目标格式：{args.target}", file=sys.stderr)
        return 2

    try:
        path = conv.plan(src_mt, dst_mt)
    except ConversionError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    print(f"{path.describe()}")
    print(f"损失等级：{_tier_mark(path.loss)} {path.loss.label}")
    print(f"可逆：{'是' if path.loss.is_reversible else '否'}")
    print(
        f"中间物化：{path.materializations} 次 · 保真度损失 {path.fidelity_penalty:.2f}"
        f" · 时间代价 {path.time_cost:.1f}"
    )
    for i, e in enumerate(path.edges, 1):
        plugin = getattr(e.plugin, "name", "内置")
        print(f"  {i}. {e.describe()}  [{plugin}] {e.loss.label}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    import platform

    from .engines import probe_all
    from .pipeline import Converter

    print(f"convcore 环境诊断\n{'─' * 56}")
    print(f"Python  {platform.python_version()}  ({sys.platform})")

    try:
        from PIL import Image, __version__ as pil_ver

        Image.init()
        print(f"Pillow  {pil_ver}")
    except ImportError:
        print("Pillow  未安装 ← 图片转换不可用，先跑 uv sync")
        return 1

    conv = Converter()
    print(
        f"\n插件  已启用 {len(conv.registry.plugins)} 个，"
        f"可读 {len(conv.registry.readable())} 格式，"
        f"可写 {len(conv.registry.writable())} 格式"
    )
    for p in sorted(conv.registry.plugins, key=lambda x: x.name):
        kinds = []
        if hasattr(p, "reads") and p.reads:
            kinds.append(f"读{len(p.reads)}")
        if hasattr(p, "writes") and p.writes:
            kinds.append(f"写{len(p.writes)}")
        print(f"  ✓ {p.name:<16} [{p.domain}] {' '.join(kinds)}")
    for name, reason in sorted(conv.registry.unavailable.items()):
        print(f"  ○ {name:<16} {reason}")

    # 分级显示：不分级的话一排红叉会让人以为全都得装，
    # 实际上图片转换零外部依赖。
    print("\n外部引擎（按需安装，不装只是少对应领域的能力）")
    infos = probe_all()
    missing_needed: list[str] = []

    for info in infos:
        spec = info.spec
        if info.available:
            ver = (info.version or "").split("Copyright")[0].strip()
            print(f"  ✓ {spec.key:<9} {ver[:56]}")
            if args.verbose:
                print(f"      来源 {info.source} · {info.path}")
            continue

        mark = "!" if spec.tier == "needed" else "○"
        size = f"（{spec.size_hint}）" if spec.size_hint else ""
        print(f"  {mark} {spec.key:<9} 未装{size} — 装了才能：{spec.enables}")
        if args.verbose:
            print(f"      {spec.install_hint}")
        if spec.tier == "needed":
            missing_needed.append(spec.key)

    # 跨域桥的依赖门禁：这些桥不是 Codec，不在插件可用性列表里，必须单独报，
    # 否则用户只看到「PNG 转不了 SVG」，不知道缺什么。
    from .graph import BRIDGE_GATE_HINTS, bridge_enabled

    print("\n跨域桥（依赖可选引擎，各自独立门控）")
    for (a, b), hint in sorted(BRIDGE_GATE_HINTS.items()):
        if bridge_enabled(a, b):
            print(f"  ✓ {a} ⇒ {b}")
        else:
            print(f"  ○ {a} ⇒ {b} 未启用 — {hint}")

    print(f"\n{'─' * 56}")

    # 从注册表推导可用领域，而不是硬编码「文档要 soffice」——文档域现在全是
    # 纯 Python，硬编码会报出错误的结论。
    from .formats import domain_of

    labels = {
        "raster": "图片", "vector": "矢量", "media": "音视频",
        "document": "PDF/Word", "markup": "Markdown/HTML/EPUB",
        "tree": "JSON/YAML/TOML/XML", "table": "CSV/Excel",
        "archive": "归档", "font": "字体", "mesh": "3D",
    }
    counts: dict[str, int] = {}
    for mt in conv.registry.writable():
        if d := domain_of(mt):
            counts[d] = counts.get(d, 0) + 1

    ready = "、".join(
        f"{labels.get(d, d)}({n})" for d, n in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    print(f"可写格式：{ready}")

    if missing_needed:
        print(
            f"\n未装 {'、'.join(missing_needed)}。图片、PDF 和标记语言不依赖任何"
            "外部引擎；Office 系高保真互转才需要 LibreOffice。"
        )
        if not args.verbose:
            print("用 -v 看安装方式。")
    return 0


def cmd_presets(args: argparse.Namespace) -> int:
    for p in PRESETS.values():
        print(f"{p.name:<10} {p.label:<10} {p.description}")
    return 0


def cmd_engines(args: argparse.Namespace) -> int:
    from .engines import engines_dir, probe_all
    from .engines.manifest import MANUAL_ONLY, PACKAGES, find_package, installable

    if args.action == "list":
        print(f"引擎目录：{engines_dir()}\n")
        for info in probe_all():
            key = info.spec.key
            if info.available:
                print(f"  ✓ {key:<10} {info.version or ''}  [{info.source}]")
            elif key in PACKAGES:
                pkg = find_package(key)
                assert pkg is not None
                print(
                    f"  ○ {key:<10} 未安装 — 可自动装 {pkg.version}"
                    f"（{pkg.size_hint / 1e6:.0f}MB）：convcore engines install {key}"
                )
            else:
                print(f"  ✗ {key:<10} 需手工安装 — {MANUAL_ONLY.get(key, info.spec.install_hint)}")
        return 0

    if args.action == "check":
        from .engines.install import check_upstream

        for key in installable():
            res = check_upstream(key)
            if res is None:
                print(f"  {key}: 无法查询上游")
                continue
            pinned, latest = res
            flag = "有新版" if pinned != latest else "已是最新"
            print(f"  {key}: 钉死 {pinned}，上游 {latest} — {flag}")
        return 0

    if args.action == "remove":
        from .engines.install import remove

        if not args.engine:
            print("需要指定引擎名", file=sys.stderr)
            return 2
        if remove(args.engine):
            print(f"已移除 {args.engine}")
            return 0
        print(f"{args.engine} 未安装在私有目录")
        return 1

    # install
    from .engines.install import InstallError, install
    from .engines.manifest import find_package

    if not args.engine:
        print(f"需要指定引擎名。可自动安装：{', '.join(installable())}", file=sys.stderr)
        return 2

    pkg = find_package(args.engine, args.engine_version)
    if pkg is None:
        print(
            f"没有 {args.engine} 的下载定义。"
            + (
                f"\n{MANUAL_ONLY[args.engine]}"
                if args.engine in MANUAL_ONLY
                else f"可自动安装：{', '.join(installable())}"
            ),
            file=sys.stderr,
        )
        return 2

    print(f"准备安装 {pkg.engine} {pkg.version}")
    print(f"  来源：{pkg.url}")
    print(f"  大小：约 {pkg.size_hint / 1e6:.0f}MB")
    print(f"  校验：{'仓库钉死的 SHA256' if pkg.sha256 else '未钉值，需你确认实测哈希'}")
    if pkg.license_note:
        print(f"  授权：{pkg.license_note}")
    print()

    last = [-1]

    def on_progress(done: int, total: int) -> None:
        if total:
            pct = int(done * 100 / total)
            if pct != last[0]:
                last[0] = pct
                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                print(
                    f"\r  {bar} {pct:3d}%  {done / 1e6:.1f}/{total / 1e6:.0f}MB",
                    end="",
                    flush=True,
                )
        else:
            print(f"\r  已下载 {done / 1e6:.1f}MB", end="", flush=True)

    try:
        res = install(
            args.engine,
            version=args.engine_version,
            progress=on_progress,
            accept_unpinned=args.accept_hash,
            force=args.force,
        )
    except InstallError as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        return 1

    print(f"\n✓ {res.engine} {res.version} 已安装到 {res.path.parent}")
    print(f"  SHA256 {res.sha256}  {'（钉值校验通过）' if res.pinned else '（未钉值，你已确认）'}")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from .desktop import run
    except ImportError as exc:
        print(
            f"缺少 pywebview（桌面窗口）：uv pip install pywebview\n（{exc}）",
            file=sys.stderr,
        )
        return 1

    run(debug=args.debug, dev_url=getattr(args, "ui_dev", None))
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .webui import serve

    serve(
        port=args.port,
        open_browser=not args.no_browser,
        out_dir=Path(args.out_dir) if args.out_dir else None,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="convcore",
        description="全格式转换引擎：能力注册表 + 格式图搜索",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="转换文件")
    c.add_argument("inputs", nargs="+", help="源文件（可多个）")
    c.add_argument("-t", "--target", required=True, help="目标格式：png / .png / image/png")
    c.add_argument("-o", "--output", help="输出文件路径（仅单文件时有效）")
    c.add_argument("-d", "--out-dir", help="输出目录")
    c.add_argument("-p", "--preset", choices=list(PRESETS), help="参数预设")
    c.add_argument("-q", "--quality", type=int, help="质量 1-100")
    c.add_argument("--width", type=int, help="目标宽度（等比）")
    c.add_argument("--height", type=int, help="目标高度（等比）")
    c.add_argument("--max-edge", type=int, help="最长边不超过此值")
    c.add_argument("--scale", type=float, help="缩放倍率，如 0.5")
    c.add_argument("--lossless", action="store_true", help="无损模式（WebP 等支持）")
    c.add_argument("--strip", action="store_true", help="去掉 EXIF 等元数据")
    # 描摹：不是图标的位图默认拒绝，--force 强制
    c.add_argument(
        "--force",
        action="store_true",
        help="允许对不像图标的位图强制描摹（raster→vector）",
    )
    # 文档域
    c.add_argument("--pages", help="页码范围，如 1-3,5（1 基，与阅读器一致）")
    c.add_argument("--password", help="加密 PDF 的密码")
    c.add_argument("--dpi", type=int, help="PDF 转图片的分辨率，默认 150")
    c.add_argument("--rotate", type=int, help="旋转角度，90 的倍数")
    c.add_argument("--split", action="store_true", help="PDF 每页拆成独立文件")
    c.add_argument(
        "--no-review-marks",
        action="store_true",
        help="PDF 转 Word 时不在文档里插入复核提示",
    )
    c.add_argument(
        "--pdf-table-mode",
        choices=["auto", "lattice", "stream", "none"],
        help="PDF 转 Word 的表格识别策略：auto 有带框表时关闭无框检测，避免公式误判",
    )
    c.add_argument(
        "--no-pdf-heading-styles",
        action="store_true",
        help="PDF 转 Word 时不把推断出的标题映射为 Word Heading 样式",
    )
    c.add_argument(
        "--ocr-lang",
        help="OCR 语言，默认 chi_sim+eng（需装对应 Tesseract 语言包）",
    )
    c.add_argument(
        "--ocr-dpi",
        type=int,
        help="扫描件 OCR 的渲染分辨率，默认 300",
    )
    c.add_argument(
        "--concat",
        action="store_true",
        help="把多个源文件按顺序拼接成一个音视频输出",
    )
    c.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的文件")
    c.add_argument("--quiet", action="store_true", help="只输出产物路径")
    c.set_defaults(fn=cmd_convert)

    f = sub.add_parser("formats", help="列出支持的格式")
    f.add_argument("-a", "--available", action="store_true", help="只显示已支持的")
    f.set_defaults(fn=cmd_formats)

    t = sub.add_parser("targets", help="某个文件能转成什么")
    t.add_argument("input")
    t.set_defaults(fn=cmd_targets)

    i = sub.add_parser("info", help="显示文件详情（媒体文件列出全部流）")
    i.add_argument("input")
    i.set_defaults(fn=cmd_info)

    p = sub.add_parser("path", help="显示转换路径与损失等级，不实际转换")
    p.add_argument("source", help="源文件或格式名")
    p.add_argument("target", help="目标格式名")
    p.set_defaults(fn=cmd_path)

    d = sub.add_parser("doctor", help="环境诊断")
    d.add_argument("-v", "--verbose", action="store_true", help="显示安装方式")
    d.set_defaults(fn=cmd_doctor)

    pr = sub.add_parser("presets", help="列出参数预设")
    pr.set_defaults(fn=cmd_presets)

    e = sub.add_parser("engines", help="管理外部引擎")
    e.add_argument(
        "action", choices=["list", "install", "remove", "check"], help="操作"
    )
    e.add_argument("engine", nargs="?", help="引擎名，如 ffmpeg")
    e.add_argument("--engine-version", help="指定版本（默认清单里的钉死版本）")
    e.add_argument(
        "--accept-hash",
        action="store_true",
        help="接受未钉死校验和的实测哈希（核对后再用）",
    )
    e.add_argument("--force", action="store_true", help="覆盖已安装的引擎")
    e.set_defaults(fn=cmd_engines)

    g = sub.add_parser("gui", help="启动桌面客户端（原生窗口）")
    g.add_argument("--debug", action="store_true", help="开启 WebView 开发者工具")
    g.add_argument(
        "--ui-dev",
        metavar="URL",
        help="指向 Vite 开发服务器（如 http://localhost:5173），改前端不用重新构建",
    )
    g.set_defaults(fn=cmd_gui)

    u = sub.add_parser("ui", help="启动浏览器版界面（无法拖放取路径）")
    u.add_argument("--port", type=int, default=0, help="端口，默认随机")
    u.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    u.add_argument("-d", "--out-dir", help="默认输出目录")
    u.set_defaults(fn=cmd_ui)

    return ap


def main(argv: list[str] | None = None) -> int:
    # 自举开关：冻结后没有 python.exe 可用，需要独立进程跑一段代码的地方
    # （tkinter 文件对话框）改用 exe 自己。必须在 argparse 之前拦下来，
    # 否则那段代码会被当成子命令。见 _paths.python_cmd。
    raw = sys.argv[1:] if argv is None else argv
    if len(raw) == 2 and raw[0] == "--run-python":
        exec(raw[1], {"__name__": "__main__"})  # noqa: S102
        return 0

    # 打包成 GUI exe 后双击不带任何参数，期望是开窗口而不是 argparse 报错退出
    # （无控制台模式下那条报错还看不见，表现为「双击没反应」）。
    if not raw and frozen():
        raw = ["gui"]

    args = build_parser().parse_args(raw if argv is None else argv)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except ConversionError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
