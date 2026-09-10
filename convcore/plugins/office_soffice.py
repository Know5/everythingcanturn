"""LibreOffice (soffice) 桥接：Office 老格式与 ODF 的高保真互转。

覆盖 formats 表里登记但没有原生实现的格式：
  读: PPTX / DOC / PPT / XLS / ODT / ODS
  写: DOCX / PPTX / DOC / PPT / XLS / ODT / ODS

设计要点：

**为什么经 soffice 而不是各自找库。** .doc/.ppt 是二进制复合文档，解析它们的
开源库（antiword、LibOWF……）质量参差且无维护；ODF 需要一整套 zip+XML 处理。
LibreOffice 是这些格式的「参考实现」，一次安装全部解锁，输出质量可预期。

**走 markup 还是 document 域。** PPT/ODT 这类流式文档读成 DocTree（标记语言域），
与 MD/HTML/DOCX 互通——它们都是「可重排内容」；XLS 读成 Table（表格域）。
不进 document 域（页面模型），因为没有页概念。

**临时目录是硬约束。** soffice 的 --convert-to 只吃文件路径，输出名=输入名
换后缀。所有调用都把源文件拷进独立临时目录再调，避免同名冲突和源文件污染。

**soffice 单实例锁。** 同一用户配置目录只能跑一个 soffice 进程。并发转换时
用 -env:UserInstallation 给每次调用独立配置目录，否则第二个进程会静默退出。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..engines.base import DEFAULT_TIMEOUT, run
from ..policy import Policy, Strategy
from ..registry import register
from ..types import Capability, ConversionError, LossTier, MediaType, Resource

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLS = "application/vnd.ms-excel"
PPT = "application/vnd.ms-powerpoint"
DOC = "application/msword"
ODT = "application/vnd.oasis.opendocument.text"
ODS = "application/vnd.oasis.opendocument.spreadsheet"

# soffice 的 --convert-to 目标名 → 我们的目标 MIME。
# 注意 XLSX 不走这里：data_table 插件用 openpyxl 原生读写，质量更高更快。
_TO_MIME = {
    "docx": DOCX,
    "pptx": PPTX,
    "doc": DOC,
    "ppt": PPT,
    "xls": XLS,
    "odt": ODT,
    "ods": ODS,
}

# 源 MIME → (域, 转成哪类 IR)。文本类 → DocTree，表格类 → Table。
_DOC_TYPES = {DOC, PPT, ODT, PPTX, DOCX, ODT}
_SHEET_TYPES = {XLS, ODS}


def _ext_of(mt: MediaType) -> str:
    return {
        DOCX: "docx", PPTX: "pptx", DOC: "doc", PPT: "ppt",
        XLS: "xls", ODT: "odt", ODS: "ods",
    }[mt]


class _SofficeCodecBase:
    """共享实现。读写都是：源文件 → soffice 转成目标后缀 → 读回/存回。

    读入侧经 DocTree/Table 意味着一次转换是两跳：soffice 先把老格式
    转成 docx/xlsx，再用原生插件读。这比让 soffice 直接吐 HTML 质量高——
    DOCX 保留的样式信息（标题层级、列表、表格）能被 DocxCodec 精确解析。
    """

    def _scratch(self, options: dict[str, Any]) -> Path:
        """本次调用专属的暂存目录。

        线程安全关键：不能用 self._tmp 实例属性——@register 插件是全局
        单例，JobQueue 4 worker 并发时线程 B 的 _ensure_tmp 会覆写线程
        A 的路径，A 的 finally rmtree 就把 B 正在用的目录拆了。
        改为每调用一个 uuid 子目录，生命周期就在这一个函数帧里。
        """
        import uuid

        tmp = Path(options.get("_tmp") or ".") / f"soffice-{uuid.uuid4().hex[:12]}"
        tmp.mkdir(parents=True, exist_ok=True)
        return tmp

    def _convert(self, tmp: Path, src: Path, target_ext: str) -> Path:
        from ..engines.probe import require

        exe = require("soffice")
        out_dir = tmp / f"out-{target_ext}"
        out_dir.mkdir(parents=True, exist_ok=True)

        # 独立用户配置目录：绕开单实例锁，允许并发调用（见模块注释）
        profile = tmp / "lo-profile"
        args = [
            str(exe),
            "-env:UserInstallation=file:///" + str(profile).replace("\\", "/"),
            "--headless",
            "--norestore",
            "--convert-to",
            target_ext,
            "--outdir",
            str(out_dir),
            str(src),
        ]
        result = run(args, timeout=DEFAULT_TIMEOUT)
        if result.returncode != 0:
            raise ConversionError(f"LibreOffice 转换失败：{result.stderr_tail}")

        produced = out_dir / (src.stem + "." + target_ext)
        if not produced.is_file():
            raise ConversionError(
                f"LibreOffice 未产生输出（{target_ext}）。源文件可能损坏或加密"
            )
        return produced

    @staticmethod
    def _stage_source(tmp: Path, data: bytes, ext: str) -> Path:
        src = tmp / f"input.{ext}"
        src.write_bytes(data)
        return src


@register
class SofficeDocCodec(_SofficeCodecBase):
    """文字类 Office 格式 → DocTree。DOC/PPT/ODT/PPTX 读，DOC/PPT/ODT 写。"""

    name = "soffice-doc"
    domain = "markup"
    reads = (
        Capability(DOC, LossTier.STRUCTURAL, 6.0),
        Capability(PPT, LossTier.STRUCTURAL, 6.0),
        Capability(ODT, LossTier.STRUCTURAL, 4.0),
        # PPTX 也在这里：python-pptx 不在依赖里，soffice 是唯一路径
        Capability(PPTX, LossTier.STRUCTURAL, 6.0),
    )
    writes = (
        Capability(DOC, LossTier.LOSSLESS, 8.0),
        Capability(PPT, LossTier.LOSSLESS, 8.0),
        Capability(ODT, LossTier.LOSSLESS, 5.0),
    )

    def available(self) -> tuple[bool, str]:
        from ..engines.probe import ENGINES, probe_one

        info = probe_one(next(s for s in ENGINES if s.key == "soffice"))
        if info.available:
            return True, ""
        return False, (
            "缺少 LibreOffice：装了才能读 DOC/PPT/XLS/ODF 并写回。"
            "https://www.libreoffice.org/download/"
        )

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> Any:
        from ..ir.markup import DocTree

        pol: Policy = options.get("_policy") or Policy()
        tmp = self._scratch(options)
        mt = resources[0].media_type
        ext = _ext_of(mt)

        pol.note(
            "LibreOffice 转换",
            Strategy.APPROXIMATE,
            "老格式经 LibreOffice 转成 DOCX 再解析，版式细节（艺术字、"
            "切换动画、嵌入对象）不保留",
        )

        src = self._stage_source(tmp, resources[0].read_bytes(), ext)
        try:
            docx_path = self._convert(tmp, src, "docx")
            # 直接复用 DocxCodec 的解析——样式映射逻辑不重复实现
            from .office import DocxCodec

            docx_codec = DocxCodec()
            res = Resource(media_type=DOCX, path=docx_path)
            tree: DocTree = docx_codec.parse([res], options)
            tree.source_type = mt
            return tree
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def write(self, ir: Any, target: MediaType, options: dict[str, Any]) -> list[Resource]:
        from .office import DocxCodec

        pol: Policy = options.get("_policy") or Policy()
        tmp = self._scratch(options)

        # DocTree → 先写 DOCX（原生、保真最高）→ soffice 转目标格式
        docx_codec = DocxCodec()
        docx_res = docx_codec.write(ir, DOCX, options)
        src = self._stage_source(tmp, docx_res[0].read_bytes(), "docx")
        try:
            out = self._convert(tmp, src, _ext_of(target))
            dst_name = f"{ir.title or 'document'}.{_ext_of(target)}"
            data = out.read_bytes()
            if ir.blocks and target == PPT:
                pol.note(
                    "幻灯片结构",
                    Strategy.APPROXIMATE,
                    "连续文档转 PPT 会按标题切分页面，长段落会溢出版面",
                )
            return [Resource(media_type=target, data=data, name=dst_name)]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@register
class SofficeSheetCodec(_SofficeCodecBase):
    """表格类 Office 格式 → Table。XLS/ODS 读，XLS/ODS 写。"""

    name = "soffice-sheet"
    domain = "table"
    reads = (
        Capability(XLS, LossTier.STRUCTURAL, 6.0),
        Capability(ODS, LossTier.STRUCTURAL, 4.0),
    )
    writes = (
        Capability(XLS, LossTier.LOSSLESS, 8.0),
        Capability(ODS, LossTier.LOSSLESS, 5.0),
    )

    def available(self) -> tuple[bool, str]:
        return SofficeDocCodec().available()

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> Any:
        from ..ir.data import Table

        pol: Policy = options.get("_policy") or Policy()
        tmp = self._scratch(options)
        mt = resources[0].media_type

        pol.note(
            "LibreOffice 转换",
            Strategy.APPROXIMATE,
            "老格式经 LibreOffice 转成 XLSX 再解析，宏与图表不保留",
        )

        src = self._stage_source(tmp, resources[0].read_bytes(), _ext_of(mt))
        try:
            xlsx_path = self._convert(tmp, src, "xlsx")
            from .data_table import TableCodec

            res = Resource(
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                path=xlsx_path,
            )
            table: Table = TableCodec().parse([res], options)
            return table
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def write(self, ir: Any, target: MediaType, options: dict[str, Any]) -> list[Resource]:
        from .data_table import TableCodec

        tmp = self._scratch(options)
        xlsx_codec = TableCodec()
        xlsx_res = xlsx_codec.write(
            ir,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            options,
        )
        src = self._stage_source(tmp, xlsx_res[0].read_bytes(), "xlsx")
        try:
            out = self._convert(tmp, src, _ext_of(target))
            return [Resource(media_type=target, data=out.read_bytes())]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
