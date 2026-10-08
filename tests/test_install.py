"""引擎安装的安全路径测试。

这些测试不联网。重点是校验和拒绝、协议白名单、路径穿越防护——都是
「必须失败」的路径，比正常流程更需要测试覆盖。
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from convcore.engines.install import (
    InstallError,
    _safe_extract_zip,
    download,
    verify_checksum,
)
from convcore.engines.manifest import Package, find_package


def pkg(sha: str | None, sha_url: str | None = None) -> Package:
    return Package(
        engine="test",
        version="1.0",
        url="https://example.invalid/x.zip",
        archive="zip",
        sha256=sha,
        sha256_url=sha_url,
        members=("tool.exe",),
    )


# ── 协议白名单 ──


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid/x.zip",
        "ftp://example.invalid/x.zip",
        "file:///C:/Windows/System32/calc.exe",
        "//example.invalid/x.zip",
    ],
)
def test_rejects_non_https(url, tmp_path):
    """明文和本地协议一律拒绝。file:// 尤其危险。"""
    with pytest.raises(InstallError, match="只允许 HTTPS"):
        download(url, tmp_path / "out.bin")


# ── 校验和 ──


def test_pinned_mismatch_aborts():
    """钉值不符必须失败：要么下载损坏，要么上游被换，都不该继续。"""
    with pytest.raises(InstallError, match="与仓库钉死的值不符"):
        verify_checksum(pkg("a" * 64), "b" * 64, accept_unpinned=True)


def test_pinned_match_passes():
    assert verify_checksum(pkg("A" * 64), "a" * 64, accept_unpinned=False) is True


def test_unpinned_requires_explicit_accept():
    """未钉值时不能静默安装，必须用户确认。"""
    with pytest.raises(InstallError, match="未在仓库中钉死校验和"):
        verify_checksum(pkg(None), "c" * 64, accept_unpinned=False)


def test_unpinned_accepted_returns_false():
    assert verify_checksum(pkg(None), "c" * 64, accept_unpinned=True) is False


def test_accept_hash_cannot_bypass_pinned_value():
    """--accept-hash 只对未钉值生效，不能用来绕过钉值检查。"""
    with pytest.raises(InstallError, match="与仓库钉死的值不符"):
        verify_checksum(pkg("d" * 64), "e" * 64, accept_unpinned=True)


# ── 解压防护 ──


def _make_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def test_extracts_only_requested_members(tmp_path):
    archive = tmp_path / "a.zip"
    _make_zip(
        archive,
        {
            "ffmpeg-9.0.1/bin/tool.exe": b"X" * 2048,
            "ffmpeg-9.0.1/bin/other.exe": b"Y" * 2048,
            "ffmpeg-9.0.1/README.txt": b"docs",
        },
    )
    dest = tmp_path / "out"
    dest.mkdir()
    got = _safe_extract_zip(archive, dest, ("tool.exe",))

    assert [p.name for p in got] == ["tool.exe"]
    # 未请求的文件不该落地
    assert not (dest / "other.exe").exists()
    assert not (dest / "README.txt").exists()


def test_path_traversal_entries_cannot_escape(tmp_path):
    """归档里的 ../../ 和绝对路径不得写到目标目录之外。

    我们只按文件名匹配并平铺，所以穿越路径连不上 members，天然免疫。
    这个测试锁住该性质，防止将来有人「优化」成保留目录结构。
    """
    archive = tmp_path / "evil.zip"
    _make_zip(
        archive,
        {
            "../../../evil.exe": b"Z" * 2048,
            "C:/Windows/System32/evil2.exe": b"Z" * 2048,
            "bin/tool.exe": b"X" * 2048,
        },
    )
    dest = tmp_path / "out"
    dest.mkdir()
    got = _safe_extract_zip(archive, dest, ("tool.exe",))

    assert [p.name for p in got] == ["tool.exe"]
    # 目标目录外一个文件都不该出现
    outside = list(tmp_path.parent.glob("evil*.exe")) + list(tmp_path.glob("evil*.exe"))
    assert not outside, f"路径穿越写出了文件：{outside}"
    assert sorted(p.name for p in dest.iterdir()) == ["tool.exe"]


def test_traversal_named_member_still_flattened(tmp_path):
    """即使穿越路径的文件名正好等于请求的成员名，也只会落到 dest 内。"""
    archive = tmp_path / "evil2.zip"
    _make_zip(archive, {"../../tool.exe": b"X" * 2048})
    dest = tmp_path / "out"
    dest.mkdir()
    got = _safe_extract_zip(archive, dest, ("tool.exe",))

    assert len(got) == 1
    assert got[0].parent == dest
    assert not (tmp_path.parent / "tool.exe").exists()


def test_missing_member_reports_clearly(tmp_path):
    archive = tmp_path / "a.zip"
    _make_zip(archive, {"bin/something-else.exe": b"X" * 2048})
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(InstallError, match="找不到"):
        _safe_extract_zip(archive, dest, ("tool.exe",))


# ── 清单一致性 ──


def test_manifest_pins_are_well_formed():
    """钉死的校验和必须是合法的 64 位十六进制，URL 必须是 HTTPS。"""
    from convcore.engines.manifest import PACKAGES

    for engine, pkgs in PACKAGES.items():
        assert pkgs, f"{engine} 没有任何包定义"
        for p in pkgs:
            assert p.url.startswith("https://"), f"{engine} 的 URL 不是 HTTPS"
            assert p.members, f"{engine} 没声明要取哪些文件"
            if p.sha256:
                assert len(p.sha256) == 64, f"{engine} 校验和长度不对"
                int(p.sha256, 16)  # 必须是十六进制
            if p.sha256_url:
                assert p.sha256_url.startswith("https://")


def test_find_package_version_selection():
    assert find_package("ffmpeg") is not None
    # 版本号跟随 manifest 的钉版（gyan 撤旧版后钉到上一个 release）
    pinned = find_package("ffmpeg")
    assert pinned is not None
    assert find_package("ffmpeg", pinned.version) is not None
    assert find_package("ffmpeg", "0.0.0") is None
    assert find_package("nonexistent") is None


class FakeResp(io.BytesIO):
    """假 HTTP 响应。status 用于模拟 206 部分内容。"""

    def __init__(self, data: bytes, *, length: int | None = None, status: int = 200):
        super().__init__(data)
        self.headers = {"Content-Length": str(length if length is not None else len(data))}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_download_hash_matches_content(tmp_path, monkeypatch):
    payload = b"hello convcore" * 1000
    expect = hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(
        "convcore.engines.install._open",
        lambda url, timeout=60.0, headers=None: FakeResp(payload),
    )
    got = download("https://example.invalid/x.bin", tmp_path / "x.bin")
    assert got == expect
    assert (tmp_path / "x.bin").read_bytes() == payload


def test_truncated_download_retries_then_fails(tmp_path, monkeypatch):
    """声明长度与实收不符要报错，否则会拿到半个引擎。"""
    monkeypatch.setattr(
        "convcore.engines.install._open",
        lambda url, timeout=60.0, headers=None: FakeResp(b"short", length=99999),
    )
    with pytest.raises(InstallError, match="下载失败，已重试"):
        download("https://example.invalid/x.bin", tmp_path / "x.bin", retries=2)


def test_resume_continues_from_partial(tmp_path, monkeypatch):
    """断流后必须续传，而不是从头重下 111MB。"""
    payload = b"".join(bytes([i % 256]) for i in range(10000))
    calls: list[dict[str, str] | None] = []

    def fake_open(url, timeout=60.0, headers=None):
        calls.append(headers)
        if headers is None:
            # 第一趟：只给前 4000 字节就断
            return FakeResp(payload[:4000], length=len(payload))
        # 第二趟：按 Range 续传剩下的
        start = int(headers["Range"].removeprefix("bytes=").rstrip("-"))
        return FakeResp(payload[start:], status=206)

    monkeypatch.setattr("convcore.engines.install._open", fake_open)
    got = download("https://example.invalid/x.bin", tmp_path / "x.bin")

    assert got == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / "x.bin").read_bytes() == payload
    assert calls[0] is None
    assert calls[1] == {"Range": "bytes=4000-"}


def test_server_ignoring_range_restarts_cleanly(tmp_path, monkeypatch):
    """服务器无视 Range 时返回 200 + 完整内容。

    此时必须从头覆盖写。若仍以追加模式写入，会把完整内容接在已有片段后面，
    产出一个更大的垃圾文件——而且哈希对不上时用户完全不知道原因。
    """
    payload = b"Z" * 8000
    state = {"first": True}

    def fake_open(url, timeout=60.0, headers=None):
        if state["first"]:
            state["first"] = False
            return FakeResp(payload[:3000], length=len(payload))
        # 无视 Range，返回 200 和完整内容
        return FakeResp(payload, status=200)

    monkeypatch.setattr("convcore.engines.install._open", fake_open)
    got = download("https://example.invalid/x.bin", tmp_path / "x.bin")

    assert (tmp_path / "x.bin").read_bytes() == payload
    assert got == hashlib.sha256(payload).hexdigest()


def test_oversize_declared_rejected_without_retry(tmp_path, monkeypatch):
    """超大文件应立即拒绝，不该重试浪费带宽。"""
    tries = [0]

    def fake_open(url, timeout=60.0, headers=None):
        tries[0] += 1
        return FakeResp(b"x", length=10**12)

    monkeypatch.setattr("convcore.engines.install._open", fake_open)
    with pytest.raises(InstallError, match="文件过大"):
        download("https://example.invalid/x.bin", tmp_path / "x.bin")
    assert tries[0] == 1, "大小超限不该重试"
