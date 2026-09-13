"""引擎下载与安装。

安全要点（按重要性排序）：
1. 只走 HTTPS，拒绝 http:// 和其他协议
2. SHA256 校验：优先用仓库钉死的值，未钉时要求用户确认实测哈希
3. 解压防路径穿越：归档里的 ../../ 或绝对路径会写到目录外
4. 原子安装：先解到临时目录，校验通过才移到最终位置，避免半成品被当成可用
5. 下载有大小上限，防止被喂一个无限流

绝不静默联网。所有下载都由显式命令触发。
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..types import ConversionError
from .manifest import MANUAL_ONLY, Package, find_package
from .probe import engines_dir

USER_AGENT = "convcore/0.1 (+local file converter)"
MAX_DOWNLOAD = 512 * 1024 * 1024
CHUNK = 256 * 1024

ProgressFn = Callable[[int, int], None]
"""(已下载字节, 总字节)。总字节为 0 表示服务器未给 Content-Length。"""


class InstallError(ConversionError):
    pass


@dataclass(slots=True)
class InstallResult:
    engine: str
    version: str
    path: Path
    sha256: str
    pinned: bool
    """校验和是否来自仓库钉值。False 表示只做了完整性校验。"""


def _open(url: str, timeout: float = 60.0, headers: dict[str, str] | None = None):
    """打开 HTTPS URL。协议白名单是第一道防线。"""
    if not url.lower().startswith("https://"):
        raise InstallError(f"只允许 HTTPS 下载源，拒绝：{url}")
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 已校验协议
    except urllib.error.HTTPError as exc:
        raise InstallError(f"下载失败 HTTP {exc.code}：{url}") from exc
    except urllib.error.URLError as exc:
        raise InstallError(f"无法连接 {url}：{exc.reason}") from exc


def fetch_text(url: str, limit: int = 4096) -> str:
    """取小段文本（.ver / .sha256 旁文件）。"""
    with _open(url) as resp:
        return resp.read(limit).decode("utf-8", "replace").strip()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _download_once(
    url: str,
    dest: Path,
    *,
    resume_from: int = 0,
    progress: ProgressFn | None = None,
) -> tuple[int, int]:
    """下载一趟，返回 (当前文件总字节, 服务器声明的完整大小)。

    resume_from > 0 时用 Range 续传。服务器不支持 Range 会返回 200 而非 206，
    此时必须从头写，否则会把完整内容追加到已有片段后面，产出一个更大的垃圾文件。
    """
    headers = {"Range": f"bytes={resume_from}-"} if resume_from else None
    with _open(url, headers=headers) as resp:
        status = getattr(resp, "status", 200) or 200
        length = int(resp.headers.get("Content-Length") or 0)

        if resume_from and status == 206:
            mode, have = "ab", resume_from
            full = resume_from + length
        else:
            # 200 表示服务器无视了 Range，只能从头来
            mode, have = "wb", 0
            full = length

        if full > MAX_DOWNLOAD:
            raise InstallError(
                f"文件过大（{full / 1e6:.0f}MB），超过 {MAX_DOWNLOAD / 1e6:.0f}MB 上限"
            )

        with dest.open(mode) as fh:
            while chunk := resp.read(CHUNK):
                have += len(chunk)
                if have > MAX_DOWNLOAD:
                    raise InstallError("下载超过大小上限，已中止")
                fh.write(chunk)
                if progress:
                    progress(have, full)

    return have, full


def download(
    url: str,
    dest: Path,
    *,
    progress: ProgressFn | None = None,
    retries: int = 4,
) -> str:
    """下载到 dest 并返回 SHA256。

    大文件（ffmpeg 是 111MB）在不稳定网络上断流是常态，所以必须重试 + 续传。
    哈希在下载完成后整体计算，因为续传时无法沿用中断前的流式哈希状态。
    """
    last_err: Exception | None = None
    have = 0

    for attempt in range(retries):
        try:
            have, full = _download_once(
                url, dest, resume_from=have, progress=progress
            )
            if not full or have == full:
                return file_sha256(dest)
            last_err = InstallError(
                f"下载不完整：收到 {have} 字节，声明 {full} 字节"
            )
        except InstallError as exc:
            # 大小上限和协议错误不该重试
            if "上限" in str(exc) or "只允许 HTTPS" in str(exc):
                raise
            last_err = exc
        except OSError as exc:
            last_err = exc

        have = dest.stat().st_size if dest.exists() else 0
        if attempt < retries - 1 and progress:
            progress(have, 0)  # 通知调用方进入重试

    raise InstallError(
        f"下载失败，已重试 {retries} 次：{last_err}\n"
        "网络不稳定时可以重新运行命令，已下载的部分会续传。"
    )


def _safe_extract_zip(archive: Path, dest: Path, members: tuple[str, ...]) -> list[Path]:
    """从 zip 里取出指定可执行文件。

    只按文件名匹配、且把结果平铺到 dest，天然免疫路径穿越——我们从不使用
    归档里的目录结构。归档里的 ../../evil.exe 只会被当成名为 evil.exe 的候选，
    匹配不上 members 就直接忽略。
    """
    wanted = {m.lower() for m in members}
    extracted: list[Path] = []

    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if wanted and name.lower() not in wanted:
                continue
            # 显式再挡一道：文件名本身不该含分隔符或上跳
            if name in ("", ".", "..") or "/" in name or "\\" in name:
                continue
            target = dest / name
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
            extracted.append(target)

    missing = wanted - {p.name.lower() for p in extracted}
    if missing:
        raise InstallError(f"归档里找不到：{', '.join(sorted(missing))}")
    return extracted


def _extract_7z(archive: Path, dest: Path, members: tuple[str, ...]) -> list[Path]:
    try:
        import py7zr
    except ImportError as exc:
        raise InstallError(
            "解压 7z 需要 py7zr：uv pip install py7zr。"
            "或改用 zip 版本（essentials 构建）。"
        ) from exc

    wanted = {m.lower() for m in members}
    extracted: list[Path] = []
    with py7zr.SevenZipFile(archive) as zf:
        names = [n for n in zf.getnames() if Path(n).name.lower() in wanted]
        if not names:
            raise InstallError(f"归档里找不到：{', '.join(sorted(wanted))}")
        staging = dest / "_7z"
        zf.extract(path=staging, targets=names)
        for n in names:
            found = staging / n
            if found.is_file():
                target = dest / found.name
                shutil.move(str(found), str(target))
                extracted.append(target)
        shutil.rmtree(staging, ignore_errors=True)
    return extracted


def verify_checksum(pkg: Package, actual: str, *, accept_unpinned: bool) -> bool:
    """校验下载物。返回「是否用了仓库钉值」。

    钉值不匹配一律失败——要么下载损坏，要么上游包被替换，两种都不该继续。
    未钉值时用旁文件做完整性校验，但必须由用户显式接受实测哈希。
    """
    if pkg.sha256:
        if actual.lower() != pkg.sha256.lower():
            raise InstallError(
                "SHA256 与仓库钉死的值不符，已中止安装。\n"
                f"  期望：{pkg.sha256}\n"
                f"  实测：{actual}\n"
                "可能是下载损坏，也可能是上游包被替换。不要绕过这个检查。"
            )
        return True

    # 未钉值：尽力用上游旁文件验证完整性
    if pkg.sha256_url:
        upstream = fetch_text(pkg.sha256_url).split()[0]
        if upstream.lower() != actual.lower():
            raise InstallError(
                "SHA256 与上游旁文件不符，下载可能损坏。\n"
                f"  旁文件：{upstream}\n"
                f"  实测：  {actual}"
            )

    if not accept_unpinned:
        raise InstallError(
            f"{pkg.engine} {pkg.version} 未在仓库中钉死校验和。\n"
            f"实测 SHA256：{actual}\n"
            "核对无误后用 --accept-hash 确认安装，并考虑把该值写入 manifest.py。"
        )
    return False


def install(
    engine: str,
    *,
    version: str | None = None,
    progress: ProgressFn | None = None,
    accept_unpinned: bool = False,
    force: bool = False,
) -> InstallResult:
    """下载并安装一个引擎到私有目录。

    原子性：全程在临时目录操作，校验和解压都成功后才移到最终位置。
    中途失败不会留下半个引擎——半成品被 probe 探测到会导致更难诊断的失败。
    """
    if engine in MANUAL_ONLY:
        raise InstallError(f"{engine} 不支持自动安装。\n{MANUAL_ONLY[engine]}")

    pkg = find_package(engine, version)
    if pkg is None:
        from .manifest import installable

        raise InstallError(
            f"没有 {engine}"
            + (f" {version}" if version else "")
            + f" 的下载定义。可自动安装：{', '.join(installable())}"
        )

    final_dir = engines_dir() / engine
    if final_dir.exists() and not force:
        existing = sorted(p.name for p in final_dir.glob("*.exe"))
        raise InstallError(
            f"{engine} 已安装在 {final_dir}（{', '.join(existing) or '空目录'}）。"
            "用 --force 覆盖。"
        )

    # 归档下到持久缓存而不是临时目录：断了之后重跑命令才能续传。
    cache = engines_dir() / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"{engine}-{pkg.version}.{pkg.archive}"

    with tempfile.TemporaryDirectory(prefix="convcore-dl-") as td:
        tmp = Path(td)

        # 缓存里已有完整且校验通过的归档就直接用，不重复下载 111MB
        actual = ""
        if archive.is_file() and pkg.sha256:
            if file_sha256(archive) == pkg.sha256.lower():
                actual = pkg.sha256.lower()

        if not actual:
            actual = download(pkg.url, archive, progress=progress)
        pinned = verify_checksum(pkg, actual, accept_unpinned=accept_unpinned)

        staging = tmp / "staging"
        staging.mkdir()
        if pkg.archive == "zip":
            _safe_extract_zip(archive, staging, pkg.members)
        elif pkg.archive == "7z":
            _extract_7z(archive, staging, pkg.members)
        elif pkg.archive == "raw":
            shutil.copy2(archive, staging / pkg.members[0])
        else:
            raise InstallError(f"不支持的归档格式：{pkg.archive}")

        # 校验解压结果真的可执行，而不是拿到一个同名的垃圾文件
        primary = staging / pkg.members[0]
        if not primary.is_file() or primary.stat().st_size < 1024:
            raise InstallError(f"解压结果异常：{primary.name} 不存在或过小")

        # 原子替换
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        if final_dir.exists():
            backup = final_dir.with_suffix(".old")
            shutil.rmtree(backup, ignore_errors=True)
            final_dir.rename(backup)
            try:
                shutil.move(str(staging), str(final_dir))
            except OSError:
                backup.rename(final_dir)  # 回滚
                raise
            shutil.rmtree(backup, ignore_errors=True)
        else:
            shutil.move(str(staging), str(final_dir))

        # 记录版本和来源，便于诊断「装的到底是哪个」
        (final_dir / "INSTALLED.txt").write_text(
            f"engine={engine}\nversion={pkg.version}\nurl={pkg.url}\n"
            f"sha256={actual}\npinned={pinned}\n",
            encoding="utf-8",
        )

    # 装成功了就不用留着 111MB 的归档
    archive.unlink(missing_ok=True)
    return InstallResult(engine, pkg.version, final_dir / pkg.members[0], actual, pinned)


def remove(engine: str) -> bool:
    target = engines_dir() / engine
    if not target.exists():
        return False
    shutil.rmtree(target)
    return True


def check_upstream(engine: str) -> tuple[str, str] | None:
    """返回 (已钉版本, 上游最新版本)。用于提示有新版可用。"""
    pkg = find_package(engine)
    if pkg is None or not pkg.version_url:
        return None
    try:
        latest = fetch_text(pkg.version_url).splitlines()[0].strip()
    except InstallError:
        return None
    return pkg.version, latest
