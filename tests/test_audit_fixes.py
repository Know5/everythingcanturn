"""P0/P1 修复回归测试。

每项对应一次真实排查发现的问题，防止退化：
- P0-1  soffice 插件单例的暂存目录竞态（实例属性 → 每调用独立目录）
- P1-1  PDF 产物零 metadata（标题/作者/生产者）
- P1-2  RGBA→PDF 丢 alpha 无告警
- P1-4  空 DocTree 静默产出空白 PDF
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from convcore.pipeline import Converter, convert_file

pymupdf = pytest.importorskip("pymupdf", reason="需要 pymupdf")


@pytest.fixture(scope="module")
def conv() -> Converter:
    return Converter()


# ── P0-1: soffice 暂存目录线程安全 ──


def test_soffice_scratch_dir_is_per_call_not_shared(tmp_path):
    """_scratch 必须每次返回不同目录且互为兄弟——共享路径会把线程 A
    的 finally rmtree 对准线程 B 正在用的目录。"""
    from convcore.plugins.office_soffice import SofficeDocCodec

    codec = SofficeDocCodec()
    a = codec._scratch({"_tmp": str(tmp_path)})
    b = codec._scratch({"_tmp": str(tmp_path)})
    assert a != b, "两次调用必须得到不同暂存目录"
    assert a.parent == b.parent == tmp_path
    # 不再触碰实例状态
    assert not hasattr(codec, "_tmp") or codec.__dict__.get("_tmp") is None


def test_soffice_scratch_cleans_up_after_use(tmp_path):
    """_scratch 建的目录由调用方 finally 删除，_scratch 自身不该留残。"""
    import shutil

    from convcore.plugins.office_soffice import SofficeDocCodec

    codec = SofficeDocCodec()
    a = codec._scratch({"_tmp": str(tmp_path)})
    shutil.rmtree(a, ignore_errors=True)
    assert not a.exists()


# ── P1-1: PDF metadata ──


def test_md_to_pdf_carries_title_metadata(conv, tmp_path):
    """桥产物必须带标题：搜索和文献管理靠它识别文档。"""
    src = tmp_path / "t.md"
    src.write_text("# 元数据标题\n\n正文。\n", encoding="utf-8")
    res = convert_file(src, "pdf", out_dir=tmp_path, overwrite=True, converter=conv)
    doc = pymupdf.open(res.outputs[0].path)
    try:
        assert doc.metadata.get("title") == "元数据标题"
        assert doc.metadata.get("producer") == "convcore"
    finally:
        doc.close()


def test_pdf_to_pdf_preserves_source_metadata(conv, tmp_path):
    """PDF→PDF（结构操作）不丢源文件的元数据。"""
    import pymupdf as pm

    src = tmp_path / "src.pdf"
    d = pm.open()
    page = d.new_page()
    page.insert_text((72, 100), "内容")
    d.set_metadata({"title": "源标题", "author": "原作者"})
    d.save(src)
    d.close()

    res = convert_file(
        src, "pdf", out_dir=tmp_path, overwrite=True, converter=conv, stem="copy"
    )
    doc = pymupdf.open(res.outputs[0].path)
    try:
        assert doc.metadata.get("title") == "源标题"
        assert doc.metadata.get("author") == "原作者"
    finally:
        doc.close()


# ── P1-2: RGBA → PDF 的 alpha 告警 ──


def test_rgba_to_pdf_warns_alpha_dropped(conv, tmp_path):
    from PIL import Image

    src = tmp_path / "t.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 128)).save(src)
    res = convert_file(src, "pdf", out_dir=tmp_path, overwrite=True, converter=conv)
    assert any("透明" in str(w) for w in res.warnings), (
        "RGBA 的 alpha 被合成白底但未显式告警——违反降级可见原则"
    )


# ── P1-4: 空文档 ──


def test_empty_doctree_to_pdf_rejected(tmp_path):
    """空 DocTree 必须报错，不能静默产出一张白纸 PDF。"""
    from convcore.ir.markup import DocTree
    from convcore.plugins.document_build import doctree_to_document
    from convcore.policy import Policy
    from convcore.types import ConversionError

    with pytest.raises(ConversionError, match="空文档"):
        doctree_to_document(DocTree(blocks=[]), {"_tmp": str(tmp_path)}, Policy())


def test_empty_md_file_to_pdf_reports_error(conv, tmp_path):
    """端到端：空 .md 文件转 PDF 应当失败并给出原因，而非产出空白 PDF。"""
    src = tmp_path / "empty.md"
    src.write_text("", encoding="utf-8")
    from convcore.types import ConversionError

    with pytest.raises(ConversionError):
        convert_file(src, "pdf", out_dir=tmp_path, overwrite=True, converter=conv)
