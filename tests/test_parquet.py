"""Parquet 写侧测试：能力声明、往返、类型处理。"""

from __future__ import annotations

from pathlib import Path

import pytest

from convcore.pipeline import convert_file

pytest.importorskip("pyarrow", reason="Parquet 需要 pyarrow")
pytest.importorskip("openpyxl", reason="data-table 插件需要 openpyxl")

from convcore.registry import default_registry  # noqa: E402

PARQUET = "application/vnd.apache.parquet"


def test_parquet_declared_in_reads_and_writes():
    """能力声明与实现同步：读+写都在。"""
    reg = default_registry()
    codec = next(p for p in reg._plugins if getattr(p, "name", "") == "data-table")
    assert PARQUET in {c.media_type for c in codec.reads}, "Parquet 读声明缺失"
    assert PARQUET in {c.media_type for c in codec.writes}, "Parquet 写声明缺失"


def test_csv_to_parquet_roundtrip(tmp_path):
    """CSV → Parquet → CSV：数据不丢、数值精度不丢。"""
    src = tmp_path / "t.csv"
    src.write_text(
        "name,age,score\nAlice,30,92.5\nBob,25,88.0\nCarol,35,95.5\n",
        encoding="utf-8",
    )
    pq_file = convert_file(src, "parquet", out_dir=tmp_path, overwrite=True).outputs[0]
    assert pq_file.path.read_bytes()[:4] == b"PAR1", "不是合法 Parquet 文件"

    back = convert_file(pq_file.path, "csv", out_dir=tmp_path, overwrite=True)
    text = back.outputs[0].path.read_text(encoding="utf-8-sig")
    for probe in ("Alice", "Bob", "Carol", "92.5", "88.0", "95.5"):
        assert probe in text, f"{probe} 在往返中丢失"


def test_mixed_type_column_becomes_string(tmp_path):
    """同列混合 int/str：pyarrow 归一为 string，数字不丢。"""
    import pyarrow.parquet as pq

    src = tmp_path / "mix.csv"
    src.write_text("col\n1\nabc\n3\n", encoding="utf-8")
    out = convert_file(src, "parquet", out_dir=tmp_path, overwrite=True).outputs[0]
    t = pq.read_table(out.path)
    assert str(t.schema.field("col").type) == "string"
    assert t.column("col").to_pylist() == ["1", "abc", "3"]


def test_parquet_unavailable_without_pyarrow_reports_hint(tmp_path):
    """缺 pyarrow 时给可执行的安装提示，不是笼统失败。"""
    import builtins
    import sys

    src = tmp_path / "t.csv"
    src.write_text("a\n1\n", encoding="utf-8")

    real_import = builtins.__import__

    def no_pyarrow(name, *a, **kw):
        if name.split(".")[0] == "pyarrow":
            raise ImportError("no pyarrow")
        return real_import(name, *a, **kw)

    from convcore.types import ConversionError

    builtins.__import__ = no_pyarrow
    try:
        # 从注册表绕过缓存影响：直接调插件的写方法
        reg = default_registry()
        codec = next(p for p in reg._plugins if getattr(p, "name", "") == "data-table")
        from convcore.ir.data import Sheet, Table

        ir = Table(sheets=[Sheet(columns=["a"], rows=[[1]])])
        with pytest.raises(ConversionError, match="pyarrow"):
            codec._write_parquet(
                ir.primary,
                __import__("convcore.policy", fromlist=["Policy"]).Policy(),
            )
    finally:
        builtins.__import__ = real_import
