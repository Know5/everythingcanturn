"""测试 tree → markup 跨域桥。"""

from pathlib import Path

import pytest

from convcore import convert_file


def test_json_array_to_html(tmp_path: Path) -> None:
    """对象数组渲染为 HTML 表格。"""
    src = tmp_path / "data.json"
    src.write_text('[{"name":"Alice","age":30},{"name":"Bob","age":25}]', encoding="utf-8")

    out = tmp_path / "out.html"
    convert_file(src, "html", out_path=out)

    html = out.read_text(encoding="utf-8")
    assert "<table>" in html
    assert "Alice" in html
    assert "30" in html
    assert "Bob" in html


def test_yaml_object_to_markdown(tmp_path: Path) -> None:
    """嵌套对象渲染为 Markdown 定义列表。"""
    src = tmp_path / "config.yaml"
    src.write_text(
        """
database:
  host: localhost
  port: 5432
cache:
  enabled: true
""",
        encoding="utf-8",
    )

    out = tmp_path / "out.md"
    convert_file(src, "markdown", out_path=out)

    md = out.read_text(encoding="utf-8")
    # 对象键应该出现
    assert "database" in md
    assert "host" in md
    assert "localhost" in md
    assert "cache" in md
    assert "true" in md


def test_json_simple_array_to_html(tmp_path: Path) -> None:
    """简单数组渲染为列表。"""
    src = tmp_path / "tags.json"
    src.write_text('["python", "rust", "typescript"]', encoding="utf-8")

    out = tmp_path / "out.html"
    convert_file(src, "html", out_path=out)

    html = out.read_text(encoding="utf-8")
    assert "<ul>" in html or "<li>" in html
    assert "python" in html
    assert "rust" in html


def test_json_with_title_option(tmp_path: Path) -> None:
    """title 选项生成标题。"""
    src = tmp_path / "data.json"
    src.write_text('[{"id":1,"value":"test"}]', encoding="utf-8")

    out = tmp_path / "out.html"
    convert_file(src, "html", out_path=out, overrides={"title": "Test Data"})

    html = out.read_text(encoding="utf-8")
    assert "Test Data" in html
    assert "<h1>" in html


def test_empty_json_to_html(tmp_path: Path) -> None:
    """空数据不崩溃。"""
    src = tmp_path / "empty.json"
    src.write_text("[]", encoding="utf-8")

    out = tmp_path / "out.html"
    convert_file(src, "html", out_path=out)

    html = out.read_text(encoding="utf-8")
    assert "空数据" in html or len(html) > 10  # 至少生成了完整文档
