"""类型嗅探测试。

重点是「扩展名骗人时以内容为准」——用户会把 WebP 存成 .jpg，微信下载的图片
常常没扩展名。
"""

from __future__ import annotations

from PIL import Image

from convcore.formats import resolve_target
from convcore.sniff import sniff, sniff_bytes


def test_detects_by_content_not_extension(tmp_path):
    """扩展名撒谎时以魔数为准。按扩展名选解码器会直接失败。"""
    liar = tmp_path / "actually_png.jpg"
    Image.new("RGB", (16, 16), (200, 30, 40)).save(liar, format="PNG")
    assert sniff(liar) == "image/png"


def test_detects_without_extension(tmp_path):
    noext = tmp_path / "screenshot"
    Image.new("RGB", (16, 16)).save(noext, format="PNG")
    assert sniff(noext) == "image/png"


def test_common_image_formats(tmp_path):
    for fmt, ext, expect in [
        ("PNG", ".png", "image/png"),
        ("JPEG", ".jpg", "image/jpeg"),
        ("WEBP", ".webp", "image/webp"),
        ("GIF", ".gif", "image/gif"),
        ("BMP", ".bmp", "image/bmp"),
        ("TIFF", ".tiff", "image/tiff"),
    ]:
        p = tmp_path / f"s{ext}"
        Image.new("RGB", (16, 16), (90, 140, 200)).save(p, format=fmt)
        assert sniff(p) == expect, f"{fmt} 识别错误"


def test_svg_distinguished_from_generic_xml():
    svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>'
    xml = b'<?xml version="1.0"?><root><item/></root>'
    assert sniff_bytes(svg) == "image/svg+xml"
    assert sniff_bytes(xml) == "application/xml"


def test_isobmff_brands_distinguish_heic_from_mp4():
    """HEIC 和 MP4 文件头几乎一样，只有 ftyp brand 不同。最容易搞错的一类。"""
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32
    avif = b"\x00\x00\x00\x18ftypavif" + b"\x00" * 32
    mp4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32
    mov = b"\x00\x00\x00\x18ftypqt  " + b"\x00" * 32

    assert sniff_bytes(heic) == "image/heic"
    assert sniff_bytes(avif) == "image/avif"
    assert sniff_bytes(mp4) == "video/mp4"
    assert sniff_bytes(mov) == "video/quicktime"


def test_json_and_text():
    assert sniff_bytes(b'{"a": 1}') == "application/json"
    assert sniff_bytes(b"[1, 2, 3]") == "application/json"
    assert sniff_bytes(b"<!DOCTYPE html><html></html>") == "text/html"


def test_unknown_falls_back_to_extension():
    assert sniff_bytes(b"\x00\x01\x02\x03", filename="x.png") == "image/png"
    assert sniff_bytes(b"\x00\x01\x02\x03") is None


def test_resolve_target_accepts_three_spellings():
    assert resolve_target("png") == "image/png"
    assert resolve_target(".png") == "image/png"
    assert resolve_target("image/png") == "image/png"
    assert resolve_target("jpeg") == "image/jpeg"
    assert resolve_target("jpg") == "image/jpeg"
    assert resolve_target("nonsense") is None


def test_mime_aliases_normalized():
    from convcore.formats import normalize

    assert normalize("image/heif") == "image/heic"
    assert normalize("IMAGE/JPG") == "image/jpeg"
    assert normalize("image/png; charset=binary") == "image/png"


def test_jxl_signature_and_codestream():
    from convcore.sniff import sniff_bytes

    container = b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32
    assert sniff_bytes(container) == "image/jxl"
    assert sniff_bytes(b"\xff\x0a" + b"\x00" * 32) == "image/jxl"
