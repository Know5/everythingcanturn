"""测试 vector → markup 跨域桥（SVG 嵌入 HTML）。"""

from pathlib import Path

import pytest

from convcore.formats import resolve_target
from convcore.pipeline import ConvertRequest, Converter
from convcore.types import Resource


@pytest.fixture
def sample_svg(tmp_path: Path) -> Path:
    """生成一个简单的 SVG 测试文件。"""
    svg_content = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">
  <circle cx="100" cy="100" r="80" fill="#3B6DFF" />
  <text x="100" y="110" text-anchor="middle" fill="white" font-size="20">Test</text>
</svg>"""
    svg_path = tmp_path / "test.svg"
    svg_path.write_text(svg_content, encoding="utf-8")
    return svg_path


def test_svg_to_html(sample_svg: Path, tmp_path: Path):
    """SVG → HTML 应该将 SVG 直接嵌入 HTML 文档。"""
    conv = Converter()

    src_mt = conv.detect(sample_svg)
    target_mt = resolve_target("html")
    assert target_mt is not None

    result = conv.convert(
        ConvertRequest(
            inputs=[Resource(media_type=src_mt, path=sample_svg)],
            target=target_mt,
            options={},
        ),
        persist_dir=tmp_path,
    )

    assert len(result.outputs) == 1
    assert result.outputs[0].path is not None

    html_content = result.outputs[0].path.read_text(encoding="utf-8")

    # 验证 SVG 被嵌入
    assert "<svg" in html_content
    assert 'xmlns="http://www.w3.org/2000/svg"' in html_content
    assert "<circle" in html_content
    assert 'fill="#3B6DFF"' in html_content


def test_svg_to_markdown(sample_svg: Path, tmp_path: Path):
    """SVG → Markdown 应该通过 HTML 标签嵌入 SVG。"""
    conv = Converter()

    src_mt = conv.detect(sample_svg)
    target_mt = resolve_target("md")
    assert target_mt is not None

    result = conv.convert(
        ConvertRequest(
            inputs=[Resource(media_type=src_mt, path=sample_svg)],
            target=target_mt,
            options={},
        ),
        persist_dir=tmp_path,
    )

    assert len(result.outputs) == 1
    assert result.outputs[0].path is not None

    md_content = result.outputs[0].path.read_text(encoding="utf-8")

    # Markdown 也应该包含 SVG（作为内嵌 HTML）
    assert "<svg" in md_content
    assert "<circle" in md_content
