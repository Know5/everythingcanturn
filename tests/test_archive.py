"""归档与字体测试。

归档的重点是安全：路径穿越和解压炸弹。这两类是「必须失败」的路径，
比正常流程更需要覆盖。
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from convcore import ConversionError, LossTier, convert_file


@pytest.fixture
def src_zip(tmp_path) -> Path:
    p = tmp_path / "src.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("readme.txt", "hello archive\n" * 20)
        z.writestr("data/nums.csv", "a,b\n1,2\n3,4\n")
        z.writestr("data/deep/note.md", "# 标题\n\n内容。\n")
        z.writestr("empty/", b"")
    return p


def _zip_names(p: Path) -> set[str]:
    with zipfile.ZipFile(p) as z:
        return {n for n in z.namelist() if not n.endswith("/")}


def _tar_names(p: Path, mode: str = "r:*") -> set[str]:
    with tarfile.open(p, mode) as t:
        return {m.name for m in t.getmembers() if m.isfile()}


# ── 安全：这些必须失败 ──


def test_path_traversal_entries_rejected(tmp_path):
    """归档里的 ../../ 和绝对路径是 zip slip 载荷，必须拒绝。"""
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../../escape.txt", "pwned")
        z.writestr("C:/Windows/evil.txt", "pwned")
        z.writestr("/etc/passwd", "pwned")
        z.writestr("ok.txt", "legit")

    res = convert_file(evil, "tar", out_dir=tmp_path, overwrite=True)
    assert any("可疑路径" in w or "上跳" in w for w in res.warnings)

    names = _tar_names(res.outputs[0].path)
    assert names == {"ok.txt"}, f"恶意条目没被拒绝：{names}"
    assert not any(".." in n for n in names)


def test_safe_path_helper():
    from convcore.plugins.archive import _safe_path

    assert _safe_path("a/b.txt") == "a/b.txt"
    assert _safe_path("./a.txt") == "a.txt"
    assert _safe_path("a\\b.txt") == "a/b.txt"  # Windows 分隔符归一化

    for bad in ("../x", "a/../../x", "/abs", "C:/win", "..", ".", ""):
        assert _safe_path(bad) is None, f"{bad!r} 应被拒绝"


def test_decompression_bomb_ratio_blocked(tmp_path):
    """压缩比异常高的归档必须中止。42.zip 那类攻击。"""
    bomb = tmp_path / "bomb.zip"
    # 100MB 的零字节压缩后只有几十 KB，压缩比远超阈值
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("big.bin", b"\0" * (100 * 1024 * 1024))

    with pytest.raises(ConversionError, match="压缩比|解压"):
        convert_file(bomb, "tar", out_dir=tmp_path, overwrite=True)


def test_too_many_entries_blocked(tmp_path):
    from convcore.plugins.archive import MAX_ENTRIES, _Budget

    b = _Budget(1000)
    with pytest.raises(ConversionError, match="条目数"):
        for _ in range(MAX_ENTRIES + 2):
            b.add(1)


def test_symlinks_skipped(tmp_path):
    """符号链接转到 ZIP 无法表达，且是安全隐患。"""
    src = tmp_path / "link.tar"
    with tarfile.open(src, "w") as t:
        info = tarfile.TarInfo("real.txt")
        data = b"content"
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))

        link = tarfile.TarInfo("evil_link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        t.addfile(link)

    res = convert_file(src, "zip", out_dir=tmp_path, overwrite=True)
    names = _zip_names(res.outputs[0].path)
    assert names == {"real.txt"}, f"符号链接没被跳过：{names}"


# ── 正常互转 ──


@pytest.mark.parametrize("target", ["tar", "gz", "bz2", "xz", "7z"])
def test_zip_to_other_formats(src_zip, tmp_path, target):
    res = convert_file(src_zip, target, out_dir=tmp_path / target, overwrite=True)
    assert res.loss is LossTier.LOSSLESS
    assert res.outputs[0].path.stat().st_size > 0


def test_content_preserved_zip_to_tar(src_zip, tmp_path):
    res = convert_file(src_zip, "tar", out_dir=tmp_path, overwrite=True)

    with tarfile.open(res.outputs[0].path) as t:
        names = {m.name for m in t.getmembers() if m.isfile()}
        assert names == {"readme.txt", "data/nums.csv", "data/deep/note.md"}
        # 目录结构必须保住
        fh = t.extractfile("data/deep/note.md")
        assert fh and "标题" in fh.read().decode()


def test_roundtrip_zip_7z_zip(src_zip, tmp_path):
    pytest.importorskip("py7zr")

    seven = convert_file(
        src_zip, "7z", out_dir=tmp_path / "a", overwrite=True
    ).outputs[0].path
    back = convert_file(
        seven, "zip", out_dir=tmp_path / "b", overwrite=True
    ).outputs[0].path

    assert _zip_names(back) == _zip_names(src_zip)

    with zipfile.ZipFile(back) as z:
        assert z.read("data/nums.csv").decode() == "a,b\n1,2\n3,4\n"


def test_tar_gz_recognized(src_zip, tmp_path):
    """tar.gz 要能读回来，不能被当成裸 gzip。"""
    gz = convert_file(
        src_zip, "gz", out_dir=tmp_path / "a", overwrite=True
    ).outputs[0].path
    back = convert_file(
        gz, "zip", out_dir=tmp_path / "b", overwrite=True
    ).outputs[0].path
    assert _zip_names(back) == _zip_names(src_zip)


def test_encrypted_zip_needs_password(tmp_path):
    """加密 ZIP 必须明确要密码，不能报笼统的错。"""
    import subprocess
    import sys

    # zipfile 不支持写加密，用 py7zr 造一个加密 7z 代替
    pytest.importorskip("py7zr")
    import py7zr

    src = tmp_path / "enc.7z"
    with py7zr.SevenZipFile(src, "w", password="s3cret") as z:
        z.writestr(b"secret content", "hidden.txt")

    with pytest.raises(ConversionError, match="密码|加密"):
        convert_file(src, "zip", out_dir=tmp_path, overwrite=True)

    res = convert_file(
        src, "zip", out_dir=tmp_path, overwrite=True,
        overrides={"password": "s3cret"},
    )
    assert _zip_names(res.outputs[0].path) == {"hidden.txt"}


def test_empty_archive_rejected(tmp_path):
    src = tmp_path / "empty.zip"
    with zipfile.ZipFile(src, "w"):
        pass
    with pytest.raises(ConversionError, match="空"):
        convert_file(src, "tar", out_dir=tmp_path, overwrite=True)


def test_corrupt_archive_reports_clearly(tmp_path):
    """损坏归档的错误必须说清楚是什么损坏了。"""
    src = tmp_path / "bad.zip"
    # ZIP 是 PK 开头，但后面必须跟合法结构。写真实 local header 但内容损坏
    src.write_bytes(
        b"PK\x03\x04"  # local file header signature
        b"\x14\x00"  # version needed
        b"\x00\x00"  # flags
        b"\x08\x00"  # compression: deflate
        + b"\x00" * 200  # 其余全错
    )
    with pytest.raises(ConversionError, match="损坏|ZIP|归档"):
        convert_file(src, "tar", out_dir=tmp_path, overwrite=True)


# ── 字体 ──

_SYS_FONT = Path("C:/Windows/Fonts/arial.ttf")
_has_font = _SYS_FONT.is_file()

font_test = pytest.mark.skipif(not _has_font, reason="需要系统字体 arial.ttf")


@pytest.fixture
def ttf(tmp_path) -> Path:
    import shutil

    dst = tmp_path / "test.ttf"
    shutil.copy(_SYS_FONT, dst)
    return dst


@font_test
def test_ttf_to_woff_is_lossless(ttf, tmp_path):
    """WOFF 只是 TTF 加压缩壳，字形数据不变，所以无损。"""
    res = convert_file(ttf, "woff", out_dir=tmp_path, overwrite=True)
    assert res.loss is LossTier.LOSSLESS
    # 压缩应当有效果
    assert res.outputs[0].path.stat().st_size < ttf.stat().st_size


@font_test
def test_font_roundtrip_byte_identical_size(ttf, tmp_path):
    """TTF→WOFF→TTF 应回到原始大小——证明只是换壳。"""
    w = convert_file(
        ttf, "woff", out_dir=tmp_path / "a", overwrite=True
    ).outputs[0].path
    back = convert_file(
        w, "ttf", out_dir=tmp_path / "b", overwrite=True
    ).outputs[0].path

    from fontTools.ttLib import TTFont

    a, b = TTFont(str(ttf)), TTFont(str(back))
    assert a.getGlyphOrder() == b.getGlyphOrder(), "字形集变了"
    assert len(a["cmap"].getBestCmap()) == len(b["cmap"].getBestCmap())


@font_test
def test_woff2_compresses_more_than_woff(ttf, tmp_path):
    pytest.importorskip("brotli", reason="WOFF2 需要 brotli")

    w1 = convert_file(
        ttf, "woff", out_dir=tmp_path / "a", overwrite=True
    ).outputs[0].path
    w2 = convert_file(
        ttf, "woff2", out_dir=tmp_path / "b", overwrite=True
    ).outputs[0].path
    assert w2.stat().st_size < w1.stat().st_size


@font_test
def test_subset_shrinks_font_dramatically(ttf, tmp_path):
    """中文字体十几 MB，网页只用几十个字时裁剪能省 99%。"""
    res = convert_file(
        ttf, "woff", out_dir=tmp_path, overwrite=True,
        overrides={"subset_text": "Hello World 你好"},
    )
    assert any("裁剪" in w for w in res.warnings)

    full = convert_file(
        ttf, "woff", out_dir=tmp_path / "full", overwrite=True
    ).outputs[0].path
    assert res.outputs[0].path.stat().st_size < full.stat().st_size * 0.3


@font_test
def test_font_info_reports_outline_type(ttf, tmp_path):
    from convcore.plugins.font import FontCodec
    from convcore.types import Resource

    codec = FontCodec()
    ir = codec.parse([Resource(media_type="font/ttf", path=ttf)], {})
    assert not ir.is_cff, "Arial 是 TrueType 轮廓"
    assert ir.glyph_count > 100
    assert "TrueType" in ir.describe()


def test_corrupt_font_reports_clearly(tmp_path):
    src = tmp_path / "bad.ttf"
    src.write_bytes(b"\x00\x01\x00\x00" + b"\xff" * 300)
    with pytest.raises(ConversionError, match="字体"):
        convert_file(src, "woff", out_dir=tmp_path, overwrite=True)
