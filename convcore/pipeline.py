"""执行器：把搜出来的路径真正跑起来。

职责：
- 按路径依次调用插件的 parse / write / convert
- 管理临时目录生命周期（外部引擎只能读文件，中间产物有时必须落盘）
- 汇总降级告警和实际损失等级
- 进度回调

基数模型是 List[Resource] → List[Resource]，不是 1:1。多进一出（多图→GIF）和
一进多出（PDF→每页一图）从第一天就在签名里，后期加 PDF 分页不用改接口。
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import policy as policy_mod
from .formats import normalize, primary_extension
from .graph import EdgeKind, FormatGraph, Path as ConvPath
from .registry import Registry, default_registry
from .sniff import sniff_resource
from .types import (
    ConversionError,
    ConvertRequest,
    ConvertResult,
    LossTier,
    MediaType,
    NoPathError,
    Resource,
)

ProgressFn = Callable[[float, str], None]
"""进度回调：(0.0~1.0, 当前步骤描述)。"""


@contextmanager
def _workspace(keep_on_error: bool = False):
    """临时工作目录。失败时可保留现场供诊断。

    Args:
        keep_on_error: 失败时是否保留临时目录

    Yields:
        Path: 临时目录路径
    """
    tmp = Path(tempfile.mkdtemp(prefix="convcore-"))
    failed = False
    try:
        yield tmp
    except BaseException:
        failed = True
        raise
    finally:
        if not (failed and keep_on_error):
            shutil.rmtree(tmp, ignore_errors=True)


class Converter:
    """转换引擎门面。构造一次，反复使用（图只建一遍）。"""

    def __init__(self, registry: Registry | None = None) -> None:
        self.registry = registry or default_registry()
        self.graph = FormatGraph(self.registry)

    # ── 查询 ──

    def detect(self, path: Path) -> MediaType:
        res = Resource(media_type="application/octet-stream", path=path)
        mt = sniff_resource(res)
        if mt is None:
            from .sniff import encrypted_audio_hint

            if hint := encrypted_audio_hint(path):
                raise ConversionError(
                    f"{path.name} 是 {hint} 加密音频，不是普通音频容器。"
                    "本项目不内置 DRM 解密；请使用官方客户端导出你有权使用的 "
                    "MP3/FLAC，或转换未加密文件。"
                )
            raise ConversionError(f"无法识别文件类型：{path.name}")
        return mt

    def targets_for(self, src: MediaType) -> dict[MediaType, ConvPath]:
        """某源格式可达的全部目标。UI 填充下拉框用。"""
        return self.graph.reachable_from(src)

    def refine_targets(
        self, source: Path, src_mt: MediaType, targets: dict[MediaType, ConvPath]
    ) -> dict[MediaType, LossTier]:
        """针对具体文件算出每个目标的真实损失等级，并剔除这个文件走不通的目标。

        静态声明只能给最坏情况，但 UI 必须在转换前告诉用户真实情况——
        「MKV→MP4 免重编码」和「MKV→MP4 要重压」对用户是完全不同的决策。
        一次探测供所有目标复用，不重复开进程。

        返回值的键是 targets 的子集：这个文件缺的内容会让一部分路径走不通——
        纯音频截不出图、无声视频抽不出音轨、扫描件抽不出文字——这些目标被去掉。
        调用方应遍历返回值，而不是 targets。
        """
        out: dict[MediaType, LossTier] = {mt: p.loss for mt, p in targets.items()}

        from .formats import domain_of

        lacks: set[str] = set()
        plan = None
        domain = domain_of(src_mt)
        if domain == "document":
            try:
                from .plugins.document import has_text_layer

                if has_text_layer(source) is False:
                    lacks.add("text")
            except Exception:
                pass  # 探测失败就按静态声明列，不该让 targets 命令挂掉
        elif domain == "media":
            try:
                from .engines import ffmpeg

                plan = ffmpeg.analyze(
                    Resource(media_type=src_mt, path=source), source.parent
                )
            except Exception:
                return out  # 同上
            if not plan.has_picture:
                lacks.add("picture")
            if not plan.audio:
                lacks.add("audio")
        elif domain == "raster":
            try:
                from .plugins.vector import traceable_raster

                if traceable_raster(source) is False:
                    lacks.add("trace")
            except Exception:
                pass  # 探测失败就按静态声明列

        if domain in ("vector", "media"):
            # OCR 只对原始文档/图片开放；不要把 SVG 渲染出的 PNG、
            # 视频封面帧这类派生图自动送进 OCR。
            for mt, cpath in targets.items():
                if any(
                    getattr(e.plugin, "name", "") == "tesseract"
                    for e in cpath.edges
                ):
                    out.pop(mt, None)

        if lacks:
            frozen = frozenset(lacks)
            for mt in list(out):
                cpath = targets[mt]
                if not cpath.blocked_under(frozen):
                    continue
                # 静态最优路径被内容约束挡住时，重新搜一条能走的路。
                # 例：扫描 PDF 的静态最优是 pdf2docx，但它要求 text；
                # OCR 写 Word 的边不要求 text，重搜后才能出现在目标里。
                if domain != "document":
                    del out[mt]
                    continue
                try:
                    alt = self.graph.find_path(src_mt, mt, lacks=frozen)
                except NoPathError:
                    del out[mt]
                else:
                    out[mt] = alt.loss

        if plan is None:
            return out

        from .engines import codecs as codec_tbl

        for mt in list(out):
            if mt not in codec_tbl.CONTAINER_CODECS:
                continue
            try:
                d = codec_tbl.decide(plan, mt)
            except ValueError:
                continue
            kept = [a for a in d.actions if a.action != "drop"]
            if not kept:
                # 目标容器装不下源里任何一条流，转换必然报「无法容纳」
                del out[mt]
                continue
            if d.can_copy_all:
                out[mt] = LossTier.COPY
            elif d.all_transcodes_lossless:
                out[mt] = LossTier.STRUCTURAL if d.dropped else LossTier.LOSSLESS
            else:
                out[mt] = LossTier.LOSSY
        return out

    def plan(
        self,
        src: MediaType,
        dst: MediaType,
        *,
        max_mats: int = 2,
        allow_identity: bool = True,
        lacks: frozenset[str] = frozenset(),
    ) -> ConvPath:
        return self.graph.find_path(
            src,
            dst,
            max_materializations=max_mats,
            allow_identity=allow_identity,
            lacks=lacks,
        )

    # ── 执行 ──

    def convert(
        self,
        req: ConvertRequest,
        *,
        progress: ProgressFn | None = None,
        keep_temp_on_error: bool = False,
        persist_dir: Path | None = None,
    ) -> ConvertResult:
        if not req.inputs:
            raise ConversionError("没有输入文件")

        target = normalize(req.target)
        src_types = {normalize(r.media_type) for r in req.inputs}
        if len(src_types) > 1:
            raise ConversionError(
                f"一次转换的输入必须同类型，收到：{', '.join(sorted(src_types))}"
            )
        src = src_types.pop()

        opts = dict(req.options)

        # 同格式且带了选项（缩放、改质量）意味着用户要重写而非复制。
        # 直接返回空路径会让选项被静默忽略——这是最难排查的一类 bug。
        identity_ok = not (src == target and self._has_transform(opts))

        lacks: frozenset[str] = frozenset()
        try:
            from .formats import domain_of

            source_path = req.inputs[0].path
            if domain_of(src) == "document" and source_path and source_path.is_file():
                from .plugins.document import has_text_layer

                if has_text_layer(source_path) is False:
                    lacks = frozenset({"text"})
        except Exception:
            pass  # 探测失败按静态路径走，不要把转换整体拦掉

        try:
            path = self.plan(
                src,
                target,
                max_mats=req.max_materializations,
                allow_identity=identity_ok,
                lacks=lacks,
            )
        except NoPathError as exc:
            if "text" in lacks:
                raise ConversionError(
                    "这份 PDF 没有文本层（扫描件）。转 Word/Markdown 需要 OCR：\n"
                    "  pip install -e \".[ocr]\" 并安装 Tesseract；\n"
                    "  也可以先转成图片（-t png）留档。"
                ) from exc
            raise
        pol = policy_mod.policy_from_options(opts)

        started = time.perf_counter()
        if persist_dir is not None:
            persist_dir.mkdir(parents=True, exist_ok=True)
        with _workspace(keep_temp_on_error) as tmp:
            outputs = self._run(path, req.inputs, opts, pol, tmp, progress)
            # 保留磁盘产物的路径，避免把大媒体文件读进 Python 内存。
            outputs = [self._detach(r, tmp, persist_dir) for r in outputs]

        self._name_outputs(req, outputs, target)

        # 插件回填的实测等级优先于路径的静态声明（remux 场景）
        actual = pol.actual_loss if pol.actual_loss is not None else path.loss

        return ConvertResult(
            outputs=outputs,
            loss=actual,
            planned_loss=path.loss,
            path_desc=path.describe(),
            elapsed=time.perf_counter() - started,
            warnings=[str(w) for w in pol.warnings],
        )

    _TRANSFORM_KEYS = frozenset(
        {
            # 图片
            "width", "height", "max_edge", "scale", "quality", "lossless",
            "optimize", "compress_level", "compression", "progressive",
            "subsampling", "method", "sizes", "keep_metadata", "keep_icc",
            # 音视频
            "force_transcode", "fps", "video_bitrate", "audio_bitrate",
            "sample_rate", "channels", "preset_speed", "pix_fmt", "start",
            # 文档：漏了这些会让 PDF→PDF 带页面选择走同格式短路，
            # 命令成功退出但文件没变
            "pages", "rotate", "split", "dpi",
            "user_password", "owner_password",
        }
    )

    def _has_transform(self, opts: dict[str, Any]) -> bool:
        """判断选项里有没有需要真实重写的变换。以下划线开头的是内部键，不算。"""
        return any(k in self._TRANSFORM_KEYS for k in opts)

    def _name_outputs(
        self, req: ConvertRequest, outputs: list[Resource], target: MediaType
    ) -> None:
        """给产物起最终文件名，基于源文件名。

        插件不知道源文件叫什么（它只拿到 IR），所以命名必须在这一层做。漏掉这步
        会让批量转换的产物全叫 output.xxx 互相覆盖——静默的数据丢失。
        """
        ext = primary_extension(target) or ".bin"
        stem = req.stem or "output"
        if req.stem is None:
            for r in req.inputs:
                if r.name:
                    stem = Path(r.name).stem
                    break

        multi = len(outputs) > 1
        for i, out in enumerate(outputs):
            # 插件给了后缀（如动图拆帧的 frame-001）就拼在源名后面保持可辨识
            if out.name and not out.name.endswith(ext):
                out.name = f"{stem}-{out.name}{ext}"
            elif multi:
                out.name = f"{stem}-{i + 1:03d}{ext}"
            else:
                out.name = f"{stem}{ext}"

    def _detach(
        self, res: Resource, tmp: Path, persist_dir: Path | None = None
    ) -> Resource:
        """让工作目录中的产物在工作目录销毁后仍可用。

        当 persist_dir 未提供时会把产物读进内存。大产物（>1GB 非 media 域）
        会在 read_bytes() 内部被拦截，这是预期行为——调用方应该提供 persist_dir。
        """
        if res.path is not None and tmp in res.path.parents:
            if persist_dir is not None:
                fd, raw_target = tempfile.mkstemp(
                    prefix="convcore-result-", suffix=res.path.suffix, dir=persist_dir
                )
                os.close(fd)
                target = Path(raw_target)
                shutil.copyfile(res.path, target)
                return Resource(media_type=res.media_type, path=target, name=res.name)
            # 无 persist_dir：读进内存。大文件会在 read_bytes() 里报错。
            return Resource(
                media_type=res.media_type,
                data=res.path.read_bytes(),
                name=res.name,
            )
        # 插件返回了内存数据，但调用方要求持久化（如测试需要验证产物文件）
        if res.data is not None and persist_dir is not None:
            from .formats import primary_extension
            ext = primary_extension(res.media_type) or ".bin"
            fd, raw_target = tempfile.mkstemp(
                prefix="convcore-result-", suffix=ext, dir=persist_dir
            )
            os.close(fd)
            target = Path(raw_target)
            target.write_bytes(res.data)
            return Resource(media_type=res.media_type, path=target, name=res.name)
        return res

    def _run(
        self,
        path: ConvPath,
        inputs: list[Resource],
        opts: dict[str, Any],
        pol: policy_mod.Policy,
        tmp: Path,
        progress: ProgressFn | None,
    ) -> list[Resource]:
        if not path.edges:
            return inputs

        total = len(path.edges)
        current: list[Resource] | Any = inputs
        ir: Any = None

        for i, edge in enumerate(path.edges):
            if progress:
                progress(i / total, edge.describe())

            # 长耗时插件（ffmpeg 转码）需要在单条边内部上报进度。把内部比例
            # 映射到这条边在整条路径上占的区间，进度条才不会卡在某一段上不动。
            inner = self._edge_progress(progress, i, total, edge) if progress else None
            plugin_opts = {**opts, "_policy": pol, "_tmp": tmp}
            if inner is not None:
                plugin_opts["_progress"] = inner
            if (cancel := opts.get("_cancel")) is not None:
                plugin_opts["_cancel"] = cancel

            try:
                if edge.kind is EdgeKind.READ:
                    ir = edge.plugin.parse(current, plugin_opts)
                    current = None

                elif edge.kind is EdgeKind.WRITE:
                    if ir is None:
                        raise ConversionError(
                            f"内部错误：{edge.describe()} 缺少 IR 输入"
                        )
                    current = edge.plugin.write(ir, edge.dst[1], plugin_opts)
                    ir = None

                else:  # BRIDGE
                    # 桥函数可能落盘（document_build 排 PDF、vector 渲染 SVG），
                    # 必须拿到带 _tmp 的 plugin_opts；传裸 opts 会丢工作目录——
                    # 产物落进兜底临时目录，_detach 搬不走，最终文件就丢了。
                    ir = self._bridge(edge.src[1], edge.dst[1], ir, plugin_opts, pol)

            except ConversionError:
                raise
            except Exception as exc:
                plugin_name = getattr(edge.plugin, "name", "内置")
                raise ConversionError(
                    f"{edge.describe()} 失败（插件 {plugin_name}）：{exc}"
                ) from exc

        if progress:
            progress(1.0, "完成")

        if not isinstance(current, list):
            raise ConversionError("内部错误：路径未以 WRITE 边结束")
        return current

    @staticmethod
    def _edge_progress(
        progress: ProgressFn, index: int, total: int, edge
    ) -> ProgressFn:
        """把插件内部的 0~1 进度映射到这条边占的区间。"""
        span = 1.0 / total
        base = index * span
        label = edge.describe()

        def fn(frac: float, step: str) -> None:
            clamped = min(max(frac, 0.0), 1.0)
            progress(base + clamped * span, step or label)

        return fn

    def _bridge(
        self,
        src_domain: str,
        dst_domain: str,
        ir: Any,
        opts: dict[str, Any],
        pol: policy_mod.Policy,
    ) -> Any:
        """跨域桥接分发。每对域一个分支，实现在各自域的插件里。"""
        if (src_domain, dst_domain) == ("vector", "raster"):
            from .plugins.vector import rasterize

            return rasterize(ir, opts, pol)
        if (src_domain, dst_domain) == ("raster", "vector"):
            from .plugins.vector import trace_raster

            return trace_raster(ir, opts, pol)
        if (src_domain, dst_domain) == ("document", "raster"):
            from .plugins.document import rasterize_pdf

            return rasterize_pdf(ir, opts, pol)
        if (src_domain, dst_domain) == ("document", "markup"):
            from .plugins.markup import pdf_to_doctree

            return pdf_to_doctree(ir, opts, pol)
        if (src_domain, dst_domain) == ("markup", "document"):
            from .plugins.document_build import doctree_to_document

            return doctree_to_document(ir, opts, pol)
        if (src_domain, dst_domain) == ("raster", "document"):
            from .plugins.document_build import raster_to_document

            return raster_to_document(ir, opts, pol)
        if (src_domain, dst_domain) == ("table", "tree"):
            from .plugins.data_tree import table_to_tree

            return table_to_tree(ir, opts, pol)
        if (src_domain, dst_domain) == ("tree", "table"):
            from .plugins.data_tree import tree_to_table

            return tree_to_table(ir, opts, pol)
        if (src_domain, dst_domain) == ("table", "markup"):
            from .plugins.data_table import table_to_doctree

            return table_to_doctree(ir, opts, pol)
        if (src_domain, dst_domain) == ("tree", "markup"):
            from .plugins.markup_tree import tree_to_markup

            return tree_to_markup(ir, opts, pol)
        if (src_domain, dst_domain) == ("media", "raster"):
            from .plugins.media import extract_cover_frame

            return extract_cover_frame(ir, opts, pol)
        if (src_domain, dst_domain) == ("raster", "media"):
            from .plugins.media_image_seq import raster_to_video

            return raster_to_video(ir, opts, pol)
        if (src_domain, dst_domain) == ("table", "document"):
            from .plugins.document_build import table_to_document

            return table_to_document(ir, opts, pol)
        if (src_domain, dst_domain) == ("document", "table"):
            from .plugins.document_extract import document_to_table

            return document_to_table(ir, opts, pol)
        if (src_domain, dst_domain) == ("vector", "markup"):
            from .plugins.vector import vector_to_markup

            return vector_to_markup(ir, opts, pol)
        raise ConversionError(
            f"跨域桥接 {src_domain} ⇒ {dst_domain} 尚未实现"
        )


def output_name(res: Resource, target: MediaType, index: int = 0, total: int = 1) -> str:
    """给产出资源起名。一进多出时加序号。"""
    if res.name:
        stem = Path(res.name).stem
    else:
        stem = "output"
    ext = primary_extension(target) or ".bin"
    if total > 1:
        return f"{stem}-{index + 1:03d}{ext}"
    return f"{stem}{ext}"


def convert_file(
    source: Path,
    target: str,
    *,
    out_dir: Path | None = None,
    out_path: Path | None = None,
    preset: str | None = None,
    overrides: dict[str, Any] | None = None,
    overwrite: bool = False,
    progress: ProgressFn | None = None,
    converter: Converter | None = None,
    stem: str | None = None,
) -> ConvertResult:
    """便捷入口：转换单个文件并落盘。CLI 和测试都走这里。

    Args:
        source: 源文件路径
        target: 目标格式（MIME 类型或扩展名）
        out_dir: 输出目录（与 out_path 二选一）
        out_path: 精确输出路径（与 out_dir 二选一）
        preset: 预设名称（如 "web", "print"）
        overrides: 覆盖预设的选项
        overwrite: 是否覆盖已存在的文件
        progress: 进度回调函数
        converter: 使用的转换器实例
        stem: 输出文件名主干

    Returns:
        ConvertResult: 转换结果，包含输出文件列表和元数据
    """
    from .formats import resolve_target

    conv = converter or Converter()
    target_mt = resolve_target(target)
    if target_mt is None:
        raise ConversionError(f"未知目标格式：{target}")

    src_mt = conv.detect(source)
    # 大文件内存保护已下移到 Resource.read_bytes()，对所有调用路径生效。
    # 这里保留快速失败：文件级入口在构造 Resource 前就拦截，错误信息更友好。
    from .formats import domain_of

    if domain_of(src_mt) != "media":
        max_input = 1 << 30  # 1 GiB
        size = source.stat().st_size
        if size > max_input:
            raise ConversionError(
                f"文件过大：{source.name}（{size / (1 << 30):.1f} GB），"
                "文档和数据类输入上限 1 GB"
            )
    opts = policy_mod.resolve_options(target_mt, preset, overrides)

    base = out_dir or (out_path.parent if out_path else source.parent)
    base.mkdir(parents=True, exist_ok=True)

    res = conv.convert(
        ConvertRequest(
            inputs=[Resource(media_type=src_mt, path=source)],
            target=target_mt,
            options=opts,
            overwrite=overwrite,
            stem=stem,
        ),
        progress=progress,
        persist_dir=base,
    )

    total = len(res.outputs)
    staged_paths = {
        out.path
        for out in res.outputs
        if out.path is not None and out.path.resolve() != source.resolve()
    }
    try:
        for i, out in enumerate(res.outputs):
            if out_path is not None and total == 1:
                dest = out_path
            else:
                dest = base / (out.name or output_name(out, target_mt, i, total))
            # 一批里出现重名会静默覆盖，宁可报错
            if any(
                o is not out and o.path is not None and o.path == dest
                for o in res.outputs
            ):
                raise ConversionError(f"产物文件名冲突：{dest.name}")
            if dest.exists() and not overwrite:
                raise ConversionError(
                    f"目标已存在：{dest}（用 --overwrite 覆盖）"
                )
            if dest.resolve() == source.resolve():
                raise ConversionError("输出会覆盖源文件，请指定不同的输出路径")
            staged = out.path
            _atomic_copy_resource(out, dest, overwrite=overwrite)
            if staged in staged_paths:
                staged_paths.discard(staged)
                staged.unlink(missing_ok=True)
            out.path, out.data, out.name = dest, None, dest.name
    except BaseException:
        for staged in staged_paths:
            staged.unlink(missing_ok=True)
        raise

    return res


def merge_files(
    sources: list[Path],
    target: str,
    *,
    out_dir: Path | None = None,
    out_path: Path | None = None,
    preset: str | None = None,
    overrides: dict[str, Any] | None = None,
    overwrite: bool = False,
    progress: ProgressFn | None = None,
    converter: Converter | None = None,
    stem: str | None = None,
) -> ConvertResult:
    """多张图片合成一张动图（GIF/WebP/TIFF）。

    与 convert_file 的区别只在输入基数；PillowCodec 已经把多图解析成帧序列，
    这里负责校验、命名和落盘。
    """
    from .formats import domain_of, resolve_target

    if len(sources) < 2:
        raise ConversionError("图片合并至少需要两个文件")
    conv = converter or Converter()
    target_mt = resolve_target(target)
    if target_mt is None:
        raise ConversionError(f"未知目标格式：{target}")
    if target_mt not in ("image/gif", "image/webp", "image/tiff"):
        raise ConversionError(
            "图片合并目标只支持 GIF / WebP / TIFF（支持动画的格式）"
        )

    src_mts = [conv.detect(source) for source in sources]
    if any(domain_of(mt) != "raster" for mt in src_mts):
        bad = next(s for s, mt in zip(sources, src_mts) if domain_of(mt) != "raster")
        raise ConversionError(f"不是位图文件：{bad.name}")

    opts = policy_mod.resolve_options(target_mt, preset, overrides)
    base = out_dir or (out_path.parent if out_path else sources[0].parent)
    base.mkdir(parents=True, exist_ok=True)

    # 多图可能格式不同；统一按第一张的读入类型进图，Pillow 仍按字节内容解码。
    src_mt = src_mts[0]
    res = conv.convert(
        ConvertRequest(
            inputs=[Resource(media_type=src_mt, path=p) for p in sources],
            target=target_mt,
            options=opts,
            overwrite=overwrite,
            stem=stem or f"{sources[0].stem}-merge",
        ),
        progress=progress,
        persist_dir=base,
    )

    total = len(res.outputs)
    staged_paths = {
        out.path
        for out in res.outputs
        if out.path is not None and out.path.resolve() not in {p.resolve() for p in sources}
    }
    try:
        for i, out in enumerate(res.outputs):
            dest = out_path if out_path is not None and total == 1 else (
                base / (out.name or output_name(out, target_mt, i, total))
            )
            if any(
                o is not out and o.path is not None and o.path == dest
                for o in res.outputs
            ):
                raise ConversionError(f"产物文件名冲突：{dest.name}")
            if dest.exists() and not overwrite:
                raise ConversionError(
                    f"目标已存在：{dest}（用 --overwrite 覆盖）"
                )
            if any(dest.resolve() == source.resolve() for source in sources):
                raise ConversionError("输出会覆盖源文件，请指定不同的输出路径")
            staged = out.path
            _atomic_copy_resource(out, dest, overwrite=overwrite)
            if staged in staged_paths:
                staged_paths.discard(staged)
                staged.unlink(missing_ok=True)
            out.path, out.data, out.name = dest, None, dest.name
    except BaseException:
        for staged in staged_paths:
            staged.unlink(missing_ok=True)
        raise
    return res


def _atomic_copy_resource(res: Resource, dest: Path, *, overwrite: bool) -> None:
    """流式写出资源并原子替换目标，避免留下半截文件。"""
    # 耐久性说明：写完只 flush 到 OS 缓存不做 fsync（见下方），掉电最多
    # 丢这个文件，不会出现半截内容——os.replace 在同一个卷上是原子的。
    fd, raw_part = tempfile.mkstemp(
        prefix=f".{dest.name}.", suffix=".part", dir=dest.parent
    )
    os.close(fd)
    part = Path(raw_part)
    try:
        if res.data is not None:
            src = io.BytesIO(res.data)
            close_src = True
        else:
            assert res.path is not None
            src = res.path.open("rb")
            close_src = True
        try:
            with part.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
                dst.flush()
                # 不做 fsync：本地转换工具崩溃的后果是重转一次，不是数据
                # 损坏；原子性已由 os.replace 保证（绝不会留下半截目标文件）。
                # 而 fsync 在 Windows 上是 ~2-3ms/次的固定同步等待，
                # 批量转换 100 张就白付 300ms。需要持久性保证的调用方
                # （如数据库导入）自行 fsync 产物目录。
        finally:
            if close_src:
                src.close()

        if not overwrite and dest.exists():
            raise ConversionError(f"目标已存在：{dest}（用 --overwrite 覆盖）")
        os.replace(part, dest)
    finally:
        try:
            part.unlink()
        except FileNotFoundError:
            pass
