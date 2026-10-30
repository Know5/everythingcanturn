"""OCR 能力测试：可选依赖，没有 Tesseract 时自动跳过。"""

from __future__ import annotations

from pathlib import Path

import pytest

pytesseract = pytest.importorskip("pytesseract", reason="需要 pytesseract")
from convcore.plugins.ocr import OcrCodec  # noqa: E402

if not OcrCodec().available()[0]:
    pytest.skip("需要 Tesseract 可执行文件", allow_module_level=True)


def _sample_image(tmp_path: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    path = tmp_path / "ocr.png"
    image = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 64)
    except OSError:
        font = ImageFont.load_default(size=64)
    draw.text((30, 60), "HELLO OCR 123", fill="black", font=font)
    image.save(path)
    return path


def test_image_ocr_to_docx(tmp_path):
    from convcore import convert_file

    source = _sample_image(tmp_path)
    res = convert_file(source, "docx", out_dir=tmp_path, overwrite=True)
    import docx

    text = "\n".join(p.text for p in docx.Document(res.outputs[0].path).paragraphs).upper()
    assert "HELLO" in text
    assert "123" in text
    assert any("OCR" in w for w in res.warnings)


def test_image_only_pdf_ocr_to_markdown(tmp_path):
    """扫描 PDF 走 OCR→Markdown，而不是被描摹成 SVG。"""
    import pymupdf
    from convcore import convert_file

    image = _sample_image(tmp_path)
    pdf = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=900, height=240)
    page.insert_image(pymupdf.Rect(0, 0, 900, 240), filename=str(image))
    doc.save(pdf)
    doc.close()

    res = convert_file(pdf, "md", out_dir=tmp_path, overwrite=True)
    text = res.outputs[0].path.read_text(encoding="utf-8").upper()
    assert "HELLO" in text
    assert "123" in text


def test_scanned_pdf_offers_word_when_ocr_available(tmp_path):
    from convcore.pipeline import Converter
    from tests.make_pdf import make_scanned

    source = make_scanned(tmp_path / "scan.pdf", pages=1)
    conv = Converter()
    targets = conv.targets_for("application/pdf")
    actual = conv.refine_targets(source, "application/pdf", targets)
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert docx in actual, "装了 OCR 后扫描件应重新列出 Word 目标"
