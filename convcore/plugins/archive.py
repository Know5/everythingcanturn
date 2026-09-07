"""归档格式：ZIP / TAR / GZ / BZ2 / XZ / 7Z 互转。

归档域的信息模型和其他域都不同：**它是文件集合而非单个文档。** 所以 IR 是
条目列表，每个条目带路径、内容和元数据。

安全是这一域的首要考虑，两类攻击都必须防：

**路径穿越（zip slip）。** 归档里的 `../../evil.exe` 或绝对路径会写到目标目录外。
我们不解压到磁盘（内容留在内存里再写进目标归档），所以天然免疫，但仍要拒绝
这类条目——它们本身就是恶意信号。

**解压炸弹。** 42.zip 那种几十 KB 解压出几 PB 的文件。必须同时限制总解压大小
和压缩比，只限其中一项都能被绕过。
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from ..policy import Policy, Strategy
from ..registry import register
from ..types import Capability, ConversionError, LossTier, MediaType, Resource

ZIP = "application/zip"
TAR = "application/x-tar"
GZ = "application/gzip"
BZ2 = "application/x-bzip2"
XZ = "application/x-xz"
SEVENZ = "application/x-7z-compressed"
RAR = "application/vnd.rar"
ZST = "application/zstd"

_OPTS = frozenset({"password", "level", "flatten"})

# 解压上限。两个都要有：只限总大小时攻击者可以用很多个小文件，
# 只限压缩比时可以用一个中等压缩比但极大的文件。
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
MAX_RATIO = 200
MAX_ENTRIES = 50_000


@dataclass(slots=True)
class ArchiveEntry:
    path: str
    """归档内的相对路径，统一用 / 分隔。"""
    data: bytes | None = None
    is_dir: bool = False
    mtime: float | None = None
    mode: int | None = None

    @property
    def size(self) -> int:
        return len(self.data) if self.data else 0


@dataclass(slots=True)
class Archive:
    """归档域 IR。"""

    entries: list[ArchiveEntry] = field(default_factory=list)
    source_type: str | None = None
    comment: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def total_size(self) -> int:
        return sum(e.size for e in self.entries)

    @property
    def file_count(self) -> int:
        return sum(1 for e in self.entries if not e.is_dir)

    def describe(self) -> str:
        n = self.file_count
        dirs = sum(1 for e in self.entries if e.is_dir)
        bits = [f"{n} 个文件"]
        if dirs:
            bits.append(f"{dirs} 个目录")
        bits.append(f"解压后 {self.total_size / 1e6:.1f}MB")
        return " · ".join(bits)


def _safe_path(name: str) -> str | None:
    """校验归档内路径。返回规范化路径，非法则返回 None。

    拒绝三类：绝对路径、上跳（..）、Windows 盘符。这些是 zip slip 的载荷特征。
    """
    if not name or name in (".", ".."):
        return None
    norm = name.replace("\\", "/").strip()

    # 绝对路径必须**拒绝**而不是 lstrip("/") 归一化成相对路径——
    # 后者会把 /etc/passwd 悄悄接受成 etc/passwd。这类宽容正是 zip slip 的入口。
    if norm.startswith("/"):
        return None
    # 盘符
    if len(norm) > 1 and norm[1] == ":":
        return None

    parts = [p for p in PurePosixPath(norm).parts if p not in (".",)]
    if any(p == ".." for p in parts):
        return None

    joined = "/".join(parts)
    if not joined:
        return None

    # Windows 保留设备名。写成这些名字的文件在 Windows 上会造成异常行为
    stem = parts[-1].split(".")[0].upper()
    if stem in ("CON", "PRN", "AUX", "NUL") or (
        len(stem) == 4 and stem[:3] in ("COM", "LPT") and stem[3].isdigit()
    ):
        return None

    return joined


class _Budget:
    """解压预算。防解压炸弹。"""

    def __init__(self, compressed_size: int) -> None:
        self.compressed = max(compressed_size, 1)
        self.total = 0
        self.count = 0

    def add(self, n: int) -> None:
        self.total += n
        self.count += 1
        if self.count > MAX_ENTRIES:
            raise ConversionError(
                f"归档条目数超过上限 {MAX_ENTRIES}，疑似解压炸弹，已中止"
            )
        if self.total > MAX_TOTAL_BYTES:
            raise ConversionError(
                f"解压总大小超过 {MAX_TOTAL_BYTES / 1e9:.0f}GB 上限，已中止"
            )
        if self.total / self.compressed > MAX_RATIO and self.total > 64 * 1024 * 1024:
            raise ConversionError(
                f"压缩比 {self.total / self.compressed:.0f}:1 异常"
                f"（超过 {MAX_RATIO}:1），疑似解压炸弹，已中止"
            )


@register
class ArchiveCodec:
    name = "archive"
    domain = "archive"

    reads = (
        Capability(ZIP, LossTier.LOSSLESS, 2.0, _OPTS),
        Capability(TAR, LossTier.LOSSLESS, 2.0, _OPTS),
        Capability(GZ, LossTier.LOSSLESS, 2.0, _OPTS),
        Capability(BZ2, LossTier.LOSSLESS, 3.0, _OPTS),
        Capability(XZ, LossTier.LOSSLESS, 3.0, _OPTS),
        Capability(SEVENZ, LossTier.LOSSLESS, 4.0, _OPTS),
        Capability(ZST, LossTier.LOSSLESS, 3.0, _OPTS),
        # RAR 需要外部 UnRAR（专有许可，不能进 wheel），缺引擎时插件仍启用
        # 但 RAR 这条能力在 available() 里动态报。
        Capability(RAR, LossTier.LOSSLESS, 4.0, _OPTS),
    )
    writes = (
        Capability(ZIP, LossTier.LOSSLESS, 2.0, _OPTS),
        Capability(TAR, LossTier.LOSSLESS, 2.0, _OPTS),
        Capability(GZ, LossTier.LOSSLESS, 2.5, _OPTS),
        Capability(BZ2, LossTier.LOSSLESS, 4.0, _OPTS),
        Capability(XZ, LossTier.LOSSLESS, 5.0, _OPTS),
        Capability(SEVENZ, LossTier.LOSSLESS, 6.0, _OPTS),
        Capability(ZST, LossTier.LOSSLESS, 3.0, _OPTS),
    )

    def available(self) -> tuple[bool, str]:
        # ZIP/TAR 是标准库；zstd 走 backports.zstd（py7zr 已带入）；
        # RAR 的 unrar 缺失时只砍 RAR 读（读时给明确指引），插件本身仍启用。
        return True, ""

    def parse(self, resources: list[Resource], options: dict[str, Any]) -> Archive:
        res = resources[0]
        mt = res.media_type
        data = res.read_bytes()
        pol: Policy = options.get("_policy") or Policy()
        budget = _Budget(len(data))

        if mt == ZIP:
            arc = self._read_zip(data, options, budget, pol)
        elif mt == SEVENZ:
            arc = self._read_7z(data, options, budget, pol)
        elif mt == RAR:
            arc = self._read_rar(data, options, budget, pol)
        elif mt == ZST:
            arc = self._read_zst(data, budget)
        elif mt in (TAR, GZ, BZ2, XZ):
            arc = self._read_tar(data, mt, budget, pol)
        else:
            raise ConversionError(f"archive 插件不支持读 {mt}")

        if not arc.entries:
            raise ConversionError("归档是空的，或所有条目都被安全检查拒绝")
        return arc

    def _read_zip(
        self, data: bytes, options: dict[str, Any], budget: _Budget, pol: Policy
    ) -> Archive:
        import datetime

        rejected = 0
        entries: list[ArchiveEntry] = []

        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ConversionError(f"ZIP 文件损坏：{exc}") from exc

        with zf:
            if pw := options.get("password"):
                zf.setpassword(str(pw).encode())

            for info in zf.infolist():
                safe = _safe_path(info.filename)
                if safe is None:
                    rejected += 1
                    continue

                if info.is_dir():
                    entries.append(ArchiveEntry(safe, is_dir=True))
                    continue

                budget.add(info.file_size)
                try:
                    content = zf.read(info)
                except RuntimeError as exc:
                    if "password" in str(exc).lower():
                        raise ConversionError(
                            "ZIP 已加密，需要 --password"
                        ) from exc
                    raise ConversionError(f"读取 {safe} 失败：{exc}") from exc

                mtime = None
                try:
                    mtime = datetime.datetime(*info.date_time).timestamp()
                except (ValueError, OverflowError):
                    pass
                entries.append(ArchiveEntry(safe, content, mtime=mtime))

            comment = zf.comment.decode("utf-8", "replace") or None

        if rejected:
            pol.note(
                "可疑路径",
                Strategy.DROP,
                f"{rejected} 个条目含绝对路径或上跳（../），已拒绝（zip slip 防护）",
            )
        return Archive(entries=entries, source_type=ZIP, comment=comment)

    def _read_tar(
        self, data: bytes, mt: MediaType, budget: _Budget, pol: Policy
    ) -> Archive:
        mode = {TAR: "r:", GZ: "r:gz", BZ2: "r:bz2", XZ: "r:xz"}[mt]
        rejected = 0
        entries: list[ArchiveEntry] = []

        try:
            tf = tarfile.open(fileobj=io.BytesIO(data), mode=mode)
        except tarfile.TarError as exc:
            # 单文件 .gz 不是 tar，退回当成裸压缩流处理
            if mt == GZ:
                return self._read_bare_gz(data, budget)
            raise ConversionError(f"归档损坏或格式不符：{exc}") from exc

        with tf:
            for member in tf.getmembers():
                safe = _safe_path(member.name)
                if safe is None:
                    rejected += 1
                    continue
                if member.isdir():
                    entries.append(ArchiveEntry(safe, is_dir=True, mtime=member.mtime))
                    continue
                if not member.isfile():
                    # 符号链接、设备节点：转到 ZIP 无法表达，且是安全隐患
                    rejected += 1
                    continue

                budget.add(member.size)
                fh = tf.extractfile(member)
                content = fh.read() if fh else b""
                entries.append(
                    ArchiveEntry(safe, content, mtime=member.mtime, mode=member.mode)
                )

        if rejected:
            pol.note(
                "跳过的条目",
                Strategy.DROP,
                f"{rejected} 个条目为可疑路径、符号链接或特殊文件，已跳过",
            )
        return Archive(entries=entries, source_type=mt)

    def _read_bare_gz(self, data: bytes, budget: _Budget) -> Archive:
        """单文件 gzip（不是 tar.gz）。"""
        import gzip

        content = gzip.decompress(data)
        budget.add(len(content))
        return Archive(
            entries=[ArchiveEntry("content", content)],
            source_type=GZ,
            meta={"bare": True},
        )

    def _read_7z(
        self, data: bytes, options: dict[str, Any], budget: _Budget, pol: Policy
    ) -> Archive:
        try:
            import py7zr
        except ImportError as exc:
            raise ConversionError(
                "读 7z 需要 py7zr：uv pip install py7zr"
            ) from exc

        import shutil
        import tempfile
        from pathlib import Path

        entries: list[ArchiveEntry] = []
        rejected = 0
        pw = options.get("password")

        # py7zr 1.1+ 去掉了 read/readall，只剩写盘的 extractall。所以必须解到
        # 临时目录再读回。解到隔离的临时目录也顺带隔离了 py7zr 自身的路径处理。
        staging = Path(tempfile.mkdtemp(prefix="convcore-7z-"))
        try:
            try:
                with py7zr.SevenZipFile(
                    io.BytesIO(data), password=str(pw) if pw else None
                ) as z:
                    if z.needs_password() and not pw:
                        raise ConversionError("7z 已加密，需要 --password")
                    names = z.getnames()
                    z.extractall(path=staging)
            except ConversionError:
                raise
            except py7zr.exceptions.PasswordRequired as exc:
                raise ConversionError("7z 已加密，需要 --password") from exc
            except py7zr.exceptions.Bad7zFile as exc:
                raise ConversionError(f"7z 文件损坏：{exc}") from exc
            except Exception as exc:
                msg = str(exc).lower()
                if "password" in msg or "encrypt" in msg:
                    raise ConversionError(
                        "7z 已加密或密码不正确" + ("" if pw else "，需要 --password")
                    ) from exc
                raise ConversionError(f"7z 解压失败：{exc}") from exc

            for name in names:
                safe = _safe_path(name)
                if safe is None:
                    rejected += 1
                    continue
                full = staging / name
                if full.is_dir():
                    entries.append(ArchiveEntry(safe, is_dir=True))
                    continue
                if not full.is_file():
                    continue
                content = full.read_bytes()
                budget.add(len(content))
                entries.append(
                    ArchiveEntry(safe, content, mtime=full.stat().st_mtime)
                )
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        if rejected:
            pol.note("可疑路径", Strategy.DROP, f"{rejected} 个条目被安全检查拒绝")
        return Archive(entries=entries, source_type=SEVENZ)

    def _unrar_path(self) -> str:
        from ..engines.probe import ENGINES, probe_one

        spec = next(s for s in ENGINES if s.key == "unrar")
        info = probe_one(spec)
        if not info.available:
            raise ConversionError("读 RAR 需要 UnRAR 引擎：convcore engines install unrar")
        return str(info.path)

    def _read_rar(
        self, data: bytes, options: dict[str, Any], budget: _Budget, pol: Policy
    ) -> Archive:
        """RAR 只读。rarfile 解析结构，解压走外部 UnRAR（专有，不能捆进 wheel）。

        rarfile 的解压必须经过真实文件（UnRAR 不吃 stdin），落临时盘。
        """
        try:
            import rarfile
        except ImportError as exc:
            raise ConversionError("读 RAR 需要 rarfile：pip install rarfile") from exc

        rarfile.UNRAR_TOOL = self._unrar_path()


        import shutil
        import tempfile
        from pathlib import Path

        staging = Path(tempfile.mkdtemp(prefix="convcore-rar-"))
        tmp_rar = staging / "input.rar"
        entries: list[ArchiveEntry] = []
        rejected = 0
        pw = options.get("password")

        try:
            tmp_rar.write_bytes(data)  # rarfile/UnRAR 都要真实文件路径
            try:
                rf = rarfile.RarFile(str(tmp_rar))
                rf.setpassword(str(pw)) if pw else None
                names = [i.filename for i in rf.infolist()]
                for name in names:
                    safe = _safe_path(name)
                    if safe is None:
                        rejected += 1
                        continue
                    inf = rf.getinfo(name)
                    if inf.is_dir():
                        entries.append(ArchiveEntry(safe, is_dir=True))
                        continue
                    content = rf.read(name)
                    budget.add(len(content))
                    mtime = None
                    if inf.mtime:
                        mtime = inf.mtime.timestamp()
                    entries.append(ArchiveEntry(safe, content, mtime=mtime))
            except rarfile.BadRarFile as exc:
                raise ConversionError(f"RAR 文件损坏：{exc}") from exc
            except rarfile.PasswordRequired as exc:
                raise ConversionError("RAR 已加密，需要 --password") from exc
            except (rarfile.RarWrongPassword,) as exc:
                raise ConversionError("RAR 密码不正确") from exc
            except rarfile.RarCannotExec as exc:
                raise ConversionError(
                    "UnRAR 引擎执行失败，重装：convcore engines install unrar"
                ) from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        if rejected:
            pol.note("可疑路径", Strategy.DROP, f"{rejected} 个条目被安全检查拒绝")
        return Archive(entries=entries, source_type=RAR)

    def _read_zst(self, data: bytes, budget: _Budget) -> Archive:
        """zst 读入。解压后先按 tar 试（tar.zst 惯例），失败退成裸流。"""
        try:
            import compression.zstd  # type: ignore[attr-defined]

            decompress = compression.zstd.decompress
        except ImportError:
            import backports.zstd as _bz

            decompress = _bz.decompress

        try:
            content = decompress(data)
        except Exception as exc:
            raise ConversionError(f"zstd 解压失败：{exc}") from exc

        budget.add(len(content))

        # tar.zst：解出来是 tar 容器，按 tar 条目展开（对齐 _read_tar 的单文件
        # .gz 退裸流逻辑，方向反过来——tar 优先，因为写出侧多文件必套 tar）
        import tarfile

        try:
            tf = tarfile.open(fileobj=io.BytesIO(content), mode="r:")
        except tarfile.TarError:
            return Archive(entries=[ArchiveEntry("data", content)], source_type=ZST, meta={"bare": True})

        entries: list[ArchiveEntry] = []
        with tf:
            for member in tf.getmembers():
                safe = _safe_path(member.name)
                if safe is None:
                    continue
                if member.isdir():
                    entries.append(ArchiveEntry(safe, is_dir=True))
                    continue
                inner = tf.extractfile(member)
                if inner is None:
                    continue
                raw = inner.read()
                budget.add(len(raw))
                entries.append(ArchiveEntry(safe, raw, mtime=member.mtime, mode=member.mode))
        return Archive(entries=entries, source_type=ZST)


    # ── 写 ──

    def write(
        self, ir: Archive, target: MediaType, options: dict[str, Any]
    ) -> list[Resource]:
        pol: Policy = options.get("_policy") or Policy()
        level = options.get("level")

        if target == ZIP:
            data = self._write_zip(ir, level, pol)
        elif target == SEVENZ:
            data = self._write_7z(ir, options, pol)
        elif target == ZST:
            data = self._write_zst(ir, level, pol)
        elif target in (TAR, GZ, BZ2, XZ):
            data = self._write_tar(ir, target, level, pol)
        else:
            raise ConversionError(f"archive 插件不支持写 {target}")

        return [Resource(media_type=target, data=data)]

    def _write_zip(self, ir: Archive, level: Any, pol: Policy) -> bytes:
        import time

        buf = io.BytesIO()
        lvl = int(level) if level is not None else 6

        with zipfile.ZipFile(
            buf, "w", zipfile.ZIP_DEFLATED, compresslevel=lvl
        ) as zf:
            for e in ir.entries:
                if e.is_dir:
                    zf.writestr(e.path.rstrip("/") + "/", b"")
                    continue
                info = zipfile.ZipInfo(e.path)
                if e.mtime:
                    try:
                        info.date_time = time.localtime(e.mtime)[:6]
                    except (ValueError, OSError):
                        pass
                info.compress_type = zipfile.ZIP_DEFLATED
                if e.mode:
                    # 高 16 位存 Unix 权限，这是 ZIP 的惯例
                    info.external_attr = (e.mode & 0xFFFF) << 16
                zf.writestr(info, e.data or b"")

            if ir.comment:
                zf.comment = ir.comment.encode("utf-8")

        return buf.getvalue()

    def _write_zst(self, ir: Archive, level: Any, pol: Policy) -> bytes:
        """裸 zst 流。多个文件时打包成内嵌 tar 再压（对齐 gzip 的处理惯例）。"""
        try:
            import compression.zstd  # type: ignore[attr-defined]

            compress = compression.zstd.compress
        except ImportError:
            import backports.zstd as _bz

            compress = _bz.compress

        files = [e for e in ir.entries if not e.is_dir]
        if ir.comment:
            pol.note("归档注释", Strategy.DROP, "zstd 裸流不支持归档级注释")

        if len(files) == 1 and "data" in (ir.meta or {}):
            # 裸流往返：单文件保内容，不套 tar
            payload = files[0].data or b""
        elif len(files) == 1:
            payload = files[0].data or b""
            pol.note(
                "裸流封装",
                Strategy.APPROXIMATE,
                f"zstd 是纯压缩流不含文件名，写出的 .zst 解压后叫 data",
            )
        else:
            # 多文件：内嵌 tar
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:") as tf:
                for e in files:
                    info = tarfile.TarInfo(e.path)
                    info.size = len(e.data or b"")
                    info.mode = e.mode or 0o644
                    if e.mtime:
                        info.mtime = int(e.mtime)
                    tf.addfile(info, io.BytesIO(e.data or b""))
            payload = buf.getvalue()
            pol.note(
                "tar.zst",
                Strategy.DROP,
                "多文件归档写成 tar.zst：解压先得 tar 再得文件",
            )

        lvl = int(level) if level is not None else 3
        return compress(payload, level=lvl)

    def _write_tar(self, ir: Archive, target: MediaType, level: Any, pol: Policy) -> bytes:
        mode = {TAR: "w:", GZ: "w:gz", BZ2: "w:bz2", XZ: "w:xz"}[target]
        buf = io.BytesIO()
        kwargs: dict[str, Any] = {}
        if level is not None and target in (GZ, BZ2):
            kwargs["compresslevel"] = int(level)

        with tarfile.open(fileobj=buf, mode=mode, **kwargs) as tf:
            for e in ir.entries:
                info = tarfile.TarInfo(e.path)
                if e.is_dir:
                    info.type = tarfile.DIRTYPE
                    info.mode = e.mode or 0o755
                    if e.mtime:
                        info.mtime = int(e.mtime)
                    tf.addfile(info)
                    continue
                content = e.data or b""
                info.size = len(content)
                info.mode = e.mode or 0o644
                if e.mtime:
                    info.mtime = int(e.mtime)
                tf.addfile(info, io.BytesIO(content))

        if ir.comment:
            pol.note("归档注释", Strategy.DROP, "TAR 格式不支持归档级注释")
        return buf.getvalue()

    def _write_7z(self, ir: Archive, options: dict[str, Any], pol: Policy) -> bytes:
        try:
            import py7zr
        except ImportError as exc:
            raise ConversionError("写 7z 需要 py7zr：uv pip install py7zr") from exc

        buf = io.BytesIO()
        pw = options.get("password")
        with py7zr.SevenZipFile(
            buf, "w", password=str(pw) if pw else None
        ) as z:
            for e in ir.entries:
                if e.is_dir:
                    continue  # py7zr 会按文件路径自动建目录
                z.writestr(e.data or b"", e.path)

        if ir.comment:
            pol.note("归档注释", Strategy.DROP, "7z 格式不支持归档级注释")
        return buf.getvalue()
