"""引擎探测：本机有哪些外部转换引擎、版本多少、缺的怎么装。

查找顺序：
1. 环境变量覆盖（CONVCORE_FFMPEG 等），便于指定特定版本
2. 私有引擎目录 %LOCALAPPDATA%\\convcore\\engines\\
3. PATH

私有目录优先于 PATH：版本要可控。系统上碰巧存在的某个老版本 ffmpeg 可能缺
我们需要的编码器，静默用它会产生难以诊断的失败。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EngineSpec:
    key: str
    exe: str
    version_args: tuple[str, ...]
    purpose: str
    install_hint: str
    tier: str = "optional"
    """needed（该领域绕不过）/ optional（有更好效果但可替代）。

    分级的意义：doctor 列一排红叉会让人以为全都得装，实际上图片转换零依赖，
    音视频只需 ffmpeg。不分级就是在制造「要装很多东西」的错觉。
    """
    enables: str = ""
    """装了之后多出什么能力。空则表示只是提升质量。"""
    size_hint: str = ""
    optional_exes: tuple[str, ...] = ()


# 有意保持短。每加一个引擎都是在给用户增加负担，必须能说清「不装会缺什么」。
#
# 砍掉的引擎及替代方案：
#   ImageMagick → Pillow 已覆盖常见格式，罕见格式(JXL)用 pillow-jxl-plugin
#   Ghostscript → pypdf 做分割/合并/旋转/加密，pymupdf 做渲染
#   Pandoc      → markdown + lxml 处理标记语言，ebooklib 处理 EPUB
# 这三个共 220MB，换成 Python 库后是 0（wheel 几 MB，随 pip 装）。
ENGINES: tuple[EngineSpec, ...] = (
    EngineSpec(
        "ffmpeg", "ffmpeg", ("-version",),
        "音视频转码、remux、抽帧",
        "convcore engines install ffmpeg",
        tier="needed",
        enables="全部音视频格式（16 种容器）",
        size_hint="196MB",
        optional_exes=("ffprobe",),
    ),
    EngineSpec(
        "soffice", "soffice", ("--version",),
        "Office 文档高保真互转",
        "装 LibreOffice：https://www.libreoffice.org/download/",
        tier="needed",
        enables="DOCX/XLSX/PPTX 及 DOC/XLS/PPT 的高保真转换",
        size_hint="350MB",
    ),
    EngineSpec(
        "unrar", "UnRAR", (),
        "RAR 解压（rarfile 的解压后端）",
        "convcore engines install unrar",
        tier="needed",
        enables="RAR 读取",
        size_hint="0.6MB",
    ),
    EngineSpec(
        "tesseract", "tesseract", ("--version",),
        "扫描件 / 图片 OCR",
        "装 Tesseract：https://github.com/UB-Mannheim/tesseract/wiki"
        "（中文再装 chi_sim 语言包）",
        tier="optional",
        enables="扫描 PDF / 图片 → 可编辑文字、Markdown、Word",
        size_hint="50MB",
    ),
)


def engines_dir() -> Path:
    """私有引擎目录。引擎下载到这里，版本由我们控制。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / "convcore" / "engines"


@dataclass(slots=True)
class EngineInfo:
    spec: EngineSpec
    path: Path | None = None
    version: str | None = None
    source: str = "缺失"
    """found 的来源：env / private / PATH。用于诊断「用的到底是哪个」。"""

    @property
    def available(self) -> bool:
        return self.path is not None


def _version_of(exe: Path, args: tuple[str, ...]) -> str | None:
    try:
        proc = subprocess.run(
            [str(exe), *args],
            capture_output=True,
            timeout=10,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    out = (proc.stdout or proc.stderr).decode("utf-8", "replace")
    first = next((ln.strip() for ln in out.splitlines() if ln.strip()), "")
    return first[:120] or None


def probe_one(spec: EngineSpec) -> EngineInfo:
    # 1. 环境变量覆盖
    env_key = f"CONVCORE_{spec.key.upper()}"
    if (override := os.environ.get(env_key)):
        p = Path(override)
        if p.is_file():
            return EngineInfo(spec, p, _version_of(p, spec.version_args), f"env:{env_key}")

    # 2. 私有目录
    exe_name = spec.exe + (".exe" if os.name == "nt" else "")
    private = engines_dir() / spec.key / exe_name
    if private.is_file():
        return EngineInfo(spec, private, _version_of(private, spec.version_args), "private")

    # 3. PATH。Ghostscript 在非 Windows 上叫 gs，这里兜一下
    for candidate in (spec.exe, "gs" if spec.key == "gs" else spec.exe):
        if (found := shutil.which(candidate)) is not None:
            p = Path(found)
            return EngineInfo(spec, p, _version_of(p, spec.version_args), "PATH")

    # Tesseract 的 Windows 安装器不一定写进当前进程的 PATH。
    if spec.key == "tesseract" and os.name == "nt":
        for root in (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
        ):
            if not root:
                continue
            p = Path(root) / "Tesseract-OCR" / "tesseract.exe"
            if p.is_file():
                return EngineInfo(
                    spec, p, _version_of(p, spec.version_args), "ProgramFiles"
                )

    return EngineInfo(spec)


def probe_all() -> list[EngineInfo]:
    return [probe_one(s) for s in ENGINES]


def companion(key: str, name: str) -> Path:
    """取与某引擎同目录的配套可执行文件（ffmpeg 旁边的 ffprobe）。

    必须同目录取而不是各自 which()：混用不同版本的 ffmpeg 和 ffprobe 会
    产生难以诊断的不一致（探测说有某流，转码却找不到）。
    """
    from ..types import ConversionError

    main = require(key)
    exe = main.with_name(name + (".exe" if os.name == "nt" else ""))
    if exe.is_file():
        return exe

    # 私有目录装的引擎一定同目录；PATH 上的可能分散，退一步 which
    if (found := shutil.which(name)) is not None:
        return Path(found)

    raise ConversionError(
        f"找到了 {main.name} 但缺少配套的 {name}，安装可能不完整。"
        f"试试 convcore engines install {key} --force"
    )


def require(key: str) -> Path:
    """取某个引擎的可执行路径，缺失时抛带安装提示的错误。

    错误消息必须包含怎么装——「ffmpeg not found」对用户毫无帮助。
    """
    from ..types import ConversionError

    spec = next((s for s in ENGINES if s.key == key), None)
    if spec is None:
        raise ConversionError(f"未知引擎：{key}")

    info = probe_one(spec)
    if not info.available:
        raise ConversionError(
            f"需要 {spec.exe} 但没找到（{spec.purpose}）。\n安装方式：{spec.install_hint}"
        )
    assert info.path is not None
    return info.path
