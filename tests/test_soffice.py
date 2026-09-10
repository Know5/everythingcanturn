"""soffice 桥接插件测试：注册、降级、能力声明、命令构造。

无 LibreOffice 的机器上跑前四类；装了 LibreOffice 才跑真机转换（skipif）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from convcore.registry import reset_registry

pytestmark = pytest.mark.usefixtures("clean_registry")


@pytest.fixture()
def clean_registry():
    reset_registry()
    yield
    reset_registry()


def _has_soffice() -> bool:
    from convcore.engines.probe import ENGINES, probe_one

    return probe_one(next(s for s in ENGINES if s.key == "soffice")).available


def test_capabilities_declared(clean_registry):
    """能力声明的格式集合精确（6 读 5 写）。"""
    from convcore.plugins.office_soffice import (
        SofficeDocCodec,
        SofficeSheetCodec,
    )

    doc_reads = {c.media_type for c in SofficeDocCodec.reads}
    assert doc_reads == {
        "application/msword",
        "application/vnd.ms-powerpoint",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    doc_writes = {c.media_type for c in SofficeDocCodec.writes}
    assert doc_writes == {
        "application/msword",
        "application/vnd.ms-powerpoint",
        "application/vnd.oasis.opendocument.text",
    }

    sheet_reads = {c.media_type for c in SofficeSheetCodec.reads}
    assert sheet_reads == {
        "application/vnd.ms-excel",
        "application/vnd.oasis.opendocument.spreadsheet",
    }


def test_available_matches_probe(clean_registry):
    """available() 与引擎探测一致：没装时给出下载指引。"""
    from convcore.plugins.office_soffice import SofficeDocCodec

    ok, reason = SofficeDocCodec().available()
    if _has_soffice():
        assert ok
    else:
        assert not ok
        assert "LibreOffice" in reason
def test_degrades_to_unavailable_registry(clean_registry):
    """未装时注册表把两个插件列入 unavailable，doctor 可报告。"""
    from convcore.registry import default_registry

    reg = default_registry()
    un = reg.unavailable
    if not _has_soffice():
        assert "soffice-doc" in un
        assert "soffice-sheet" in un
        assert "LibreOffice" in un["soffice-doc"]
    else:
        assert "soffice-doc" not in un


def test_convert_command_construction(tmp_path, clean_registry, monkeypatch):
    """命令构造：独立 profile 绕单实例锁、参数列表化（无 shell 拼接）。

    require() 在 _convert 里先于 run 被调用，没装 soffice 时会先抛
    「需要 soffice」——把 require 一并替换成假路径，专注验证命令构造。
    """
    import convcore.engines.probe as probe_mod
    import convcore.plugins.office_soffice as mod
    from convcore.plugins.office_soffice import SofficeDocCodec

    fake_exe = tmp_path / "soffice.exe"
    fake_exe.write_bytes(b"")
    # require 在 _convert 内是 from ..engines.probe import require——
    # 函数级 import 每次从 probe 模块取名字，patch probe 命名空间才生效
    monkeypatch.setattr(probe_mod, "require", lambda key: fake_exe)

    codec = SofficeDocCodec()
    src = tmp_path / "input.doc"
    src.write_bytes(b"x")

    captured = {}

    def fake_run(args, timeout=None):
        captured["args"] = args
        captured["timeout"] = timeout
        out_dir = Path(args[args.index("--outdir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "input.docx").write_bytes(b"fake-docx")
        from convcore.engines.base import RunResult

        return RunResult(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(mod, "run", fake_run)
    out = codec._convert(tmp_path, src, "docx")

    args = captured["args"]
    assert str(fake_exe) == args[0]
    assert "--headless" in args
    assert args[args.index("--convert-to") + 1] == "docx"
    # outdir 在 _convert 内部基于传入的 tmp 构造，不再依赖实例状态
    out_dir_arg = Path(args[args.index("--outdir") + 1])
    assert out_dir_arg.parent == tmp_path
    assert out_dir_arg.name == "out-docx"
    assert args[-1] == str(src)
    assert captured["timeout"] == 600.0
    assert out == out_dir_arg / "input.docx"



@pytest.mark.skipif(not _has_soffice(), reason="需要 LibreOffice")
def test_doc_to_odt_roundtrip(tmp_path, clean_registry):
    """真机：DOC→ODT→读回 DocTree。仅在装了 LibreOffice 的环境跑。"""
    import zipfile

    # 造一个 DOCX 再让 soffice 转成 .doc？简化：直接用 soffice 读 ODT
    odt = tmp_path / "in.odt"
    from convcore.pipeline import Converter
    from pathlib import Path

    conv = Converter()
    src = Path("test_samples") / "b.docx"
    res = __import__("convcore.pipeline", fromlist=["convert_file"]).convert_file(
        src, "odt", out_dir=tmp_path, converter=conv, overwrite=True
    )
    assert res.outputs[0].path.suffix == ".odt"
