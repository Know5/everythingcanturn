"""引擎下载清单：钉死版本和校验和。

为什么要钉版而不是永远拉最新：
- 引擎升级会改变输出（编码器默认参数变动），黄金样本测试会莫名失败
- 新版本可能引入回归，而转换工具的失败往往很隐蔽（画质悄悄变差）
- 校验和只有在版本固定时才有意义

关于校验和的诚实说明：
`sha256` 字段是**仓库内钉死的值**，由维护者手工核对后写入。这是真正的信任锚。
`sha256_url` 是上游提供的旁文件，只能证明「下载没损坏」——它和归档来自同一台
主机，主机被攻破时两者会同时被换掉。所以：

- 已钉值 → 用钉值校验，最强保证
- 未钉值（用户要求新版本）→ 用旁文件校验完整性，打印实测哈希，
  要求 --accept-hash 显式确认后才安装

绝不静默安装未经确认的二进制。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Package:
    """一个可下载的引擎包。"""

    engine: str
    version: str
    url: str
    archive: str
    """归档格式：zip / 7z / raw（单文件直接下载）。"""
    sha256: str | None
    """仓库钉死的校验和。None 表示未钉，安装时需用户确认实测哈希。"""
    sha256_url: str | None = None
    """上游旁文件 URL。只用于完整性校验，不构成信任锚。"""
    version_url: str | None = None
    """上游版本号文件，用于 `engines check` 报告有新版本。"""
    members: tuple[str, ...] = ()
    """要从归档里取出的可执行文件名。空表示取全部 bin 目录内容。"""
    strip_prefix: bool = True
    """归档内是否有单层顶级目录需要剥掉（ffmpeg 的 zip 就有）。"""
    size_hint: int = 0
    license_note: str = ""
    """授权提示。GPL 构建打进分发物会传染整个产品，必须让用户知道。"""


# Windows x64 的包定义。加平台时按 (engine, platform) 索引扩展。
#
# FFmpeg 选 essentials 而非 full：
#   - essentials 已含 x264/x265/libvpx/libaom/lame/opus/vorbis，覆盖 Phase 3 全部需求
#   - essentials 有 zip 版，标准库能解；full 只有 7z，要额外依赖 py7zr
#   - 体积 104MB vs full 更大
# 想要 full（硬件编码器、更多滤镜）可以 --variant full，届时需要 py7zr。
#
# 版本钉法：gyan.dev 只把「当前 release」放在滚动文件名（release-essentials.zip）
# 下——内容随发版变化，哈希钉不住；上一个 release 保留在 packages/ 的固定文件名，
# URL 永久。git master 构建只有 7z（要 py7zr），不走。所以钉上一个 release 的 zip，
# version_url 指向上游版本号——engines check 仍会提示有更新可用。
PACKAGES: dict[str, list[Package]] = {
    "ffmpeg": [
        Package(
            engine="ffmpeg",
            version="8.1.2",
            url="https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip",
            archive="zip",
            sha256="db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec",
            sha256_url="https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip.sha256",
            version_url="https://www.gyan.dev/ffmpeg/builds/release-version",
            members=("ffmpeg.exe", "ffprobe.exe"),
            size_hint=104 * 1024 * 1024,
            license_note=(
                "GPL 构建（含 x264/x265）。个人自用无约束；若将来要闭源分发，"
                "需换 LGPL 构建并处理 H.264/HEVC 专利许可。"
            ),
        ),
    ],
}

# 无法可靠自动安装的引擎：需要系统级安装器、或体积过大不适合我们代管。
# doctor 会给出手工安装指引而不是假装能自动搞定。
MANUAL_ONLY: dict[str, str] = {
    "soffice": (
        "LibreOffice 是系统级安装器（约 350MB），不适合我们代管。"
        "从 https://www.libreoffice.org/download/ 装好后 convcore 会自动探测到。"
        "只在需要 Office 文档高保真转换时才装。"
    ),
}


def find_package(engine: str, version: str | None = None) -> Package | None:
    pkgs = PACKAGES.get(engine, [])
    if not pkgs:
        return None
    if version is None:
        return pkgs[0]
    return next((p for p in pkgs if p.version == version), None)


def installable() -> list[str]:
    return sorted(PACKAGES)
