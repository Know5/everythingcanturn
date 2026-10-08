"""标记语言域测试：Markdown / HTML / TXT / DOCX / EPUB。

重点是往返保真——A→B→A 后退化说明 IR 丢了本该透传的信息，是设计缺陷而非误差。
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from convcore import LossTier, convert_file

pytest.importorskip("mistune")
pytest.importorskip("lxml")

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

SAMPLE_MD = """# 项目说明

这是**加粗**和*斜体*，还有 `行内代码` 与[链接](https://example.com)。

## 功能表

| 领域 | 格式数 | 状态 |
| --- | --- | --- |
| 图片 | 12 | 完成 |
| 音视频 | 16 | 完成 |

## 代码

```python
def convert(src, dst):
    return run(src, dst)
```

> 引用块测试。

- 无序项一
- 无序项二

1. 有序项一
2. 有序项二

---

![示意图](diagram.png)
"""


@pytest.fixture(scope="module")
def md_file(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("markup")
    p = d / "doc.md"
    p.write_text(SAMPLE_MD, encoding="utf-8")
    return p


def words(s: str) -> list[str]:
    """提取可比较的词元。先去掉 Markdown 转义反斜杠。"""
    s = re.sub(r"\\([*_`\[\]\\~])", r"\1", s)
    return re.findall(r"[\w\u4e00-\u9fff]+", s)


def recall(src_text: str, got_text: str) -> float:
    a, b = Counter(words(src_text)), Counter(words(got_text))
    total = sum(a.values())
    return 1 - sum((a - b).values()) / total if total else 1.0


# ── 解析 ──


def test_markdown_parses_all_block_types(md_file):
    from convcore.ir.markup import CODE, HEADING, IMAGE, LIST, QUOTE, RULE, TABLE
    from convcore.plugins.markup_read import parse_markdown

    tree = parse_markdown(SAMPLE_MD)
    kinds = {b.kind for b in tree.blocks}
    for want in (HEADING, TABLE, CODE, QUOTE, LIST, RULE, IMAGE):
        assert want in kinds, f"{want} 没被解析出来"

    assert tree.title == "项目说明"
    assert len(tree.headings) == 3
    assert tree.table_count == 1


def test_inline_formats_become_structured_spans():
    """行内标记必须结构化，不能当字符串塞进 text。

    否则 Markdown→DOCX 时 **粗体** 会变成字面的星号。
    """
    from convcore.plugins.markup_read import parse_markdown

    tree = parse_markdown("这是**粗**和*斜*与`码`和[链](http://x.com)。")
    spans = tree.blocks[0].spans
    assert any(s.bold and s.text == "粗" for s in spans)
    assert any(s.italic and s.text == "斜" for s in spans)
    assert any(s.code and s.text == "码" for s in spans)
    assert any(s.href == "http://x.com" for s in spans)


def test_markdown_table_has_header_flag():
    from convcore.plugins.markup_read import parse_markdown

    tree = parse_markdown("| A | B |\n| --- | --- |\n| 1 | 2 |\n")
    tbl = next(b for b in tree.blocks if b.kind == "table")
    assert len(tbl.rows) == 2
    assert all(c.header for c in tbl.rows[0])
    assert not any(c.header for c in tbl.rows[1])
    assert [c.text for c in tbl.rows[1]] == ["1", "2"]


def test_html_parses_nested_and_preserves_unknown():
    from convcore.ir.markup import RAW
    from convcore.plugins.markup_read import parse_html

    html = """<html><head><title>标题</title></head><body>
    <h1>大标题</h1>
    <p>正文<strong>粗</strong>和<a href="http://x">链</a></p>
    <table><tr><th>H</th></tr><tr><td>D</td></tr></table>
    <video src="v.mp4"></video>
    </body></html>"""
    tree = parse_html(html)
    assert tree.title == "标题"
    assert any(b.kind == "heading" and b.level == 1 for b in tree.blocks)
    assert any(b.kind == "table" for b in tree.blocks)
    # 表达不了的元素原样留在 RAW，往返才不丢
    assert any(b.kind == RAW for b in tree.blocks)


def test_text_encoding_sniffed(tmp_path):
    """中文环境里 GBK 文件很常见，按 UTF-8 硬解会乱码或抛异常。"""
    from convcore.plugins.markup import _decode
    from convcore.types import Resource

    p = tmp_path / "gbk.txt"
    p.write_bytes("中文内容测试".encode("gb18030"))
    assert "中文内容测试" in _decode(Resource(media_type="text/plain", path=p))

    p2 = tmp_path / "bom.txt"
    p2.write_bytes(b"\xef\xbb\xbf" + "带 BOM".encode())
    assert _decode(Resource(media_type="text/plain", path=p2)).startswith("带")


# ── 转换与损失等级 ──


@pytest.mark.parametrize(
    "target,expect_loss",
    [
        ("html", LossTier.LOSSLESS),
        # DOCX/EPUB 写侧：图片变占位、RAW 丢弃——结构性简化
        ("docx", LossTier.STRUCTURAL),
        ("epub", LossTier.STRUCTURAL),
        # 纯文本丢掉全部格式，是结构性损失而非「有损压缩」
        ("txt", LossTier.STRUCTURAL),
    ],
)
def test_markdown_to_targets(md_file, tmp_path, target, expect_loss):
    res = convert_file(md_file, target, out_dir=tmp_path, overwrite=True)
    assert res.loss is expect_loss, f"{target} 损失等级应为 {expect_loss.name}"

    out = res.outputs[0].path
    assert out.exists() and out.stat().st_size > 0


def test_same_format_without_options_short_circuits(md_file, tmp_path):
    """MD→MD 无选项时应短路成 COPY，不做无谓的解析和重写。"""
    res = convert_file(md_file, "md", out_dir=tmp_path, overwrite=True)
    assert res.loss is LossTier.COPY
    assert res.path_desc == "（无需转换）"


def test_html_output_is_self_contained(md_file, tmp_path):
    """产物必须单文件自包含。外链 CSS 会让用户拷走 HTML 后发现样式没了。"""
    res = convert_file(md_file, "html", out_dir=tmp_path, overwrite=True)
    html = res.outputs[0].path.read_text(encoding="utf-8")

    assert "<style>" in html, "样式应内联"
    assert 'rel="stylesheet"' not in html, "不该外链 CSS"
    assert "<!DOCTYPE html>" in html
    assert 'charset="utf-8"' in html or "charset=utf-8" in html
    # 内容都在
    assert "项目说明" in html and "<table>" in html and "<pre>" in html


def test_txt_degradation_is_explicit(md_file, tmp_path):
    """降级必须告警。静默降级是同类工具的通病。"""
    res = convert_file(md_file, "txt", out_dir=tmp_path, overwrite=True)
    joined = " ".join(res.warnings)
    assert "行内格式" in joined
    assert "表格" in joined
    assert "图片" in joined


def test_txt_table_aligns_cjk_by_display_width(md_file, tmp_path):
    """中文按显示宽度对齐。按字符数算会让中文表格歪掉。"""
    res = convert_file(md_file, "txt", out_dir=tmp_path, overwrite=True)
    text = res.outputs[0].path.read_text(encoding="utf-8")

    # 表格的两个数据行：「图片 12 完成」和「音视频 16 完成」
    rows = [
        ln for ln in text.splitlines()
        if ("图片" in ln and "12" in ln) or ("音视频" in ln and "16" in ln)
    ]
    assert len(rows) == 2, f"没找到表格数据行：{text[:300]}"

    # 必须按**显示宽度**比较而不是字符数：「图片」2 字占 4 格，「音视频」3 字占 6 格，
    # 正确对齐时前缀的显示宽度相同，但字符数不同。
    def disp_width(s: str) -> int:
        return sum(2 if not ch.isascii() else 1 for ch in s)

    prefixes = [ln.split("12")[0] if "12" in ln else ln.split("16")[0] for ln in rows]
    widths = {disp_width(p) for p in prefixes}
    assert len(widths) == 1, f"中文表格列没对齐（宽字符没按 2 格算）：{rows}"


def test_txt_uses_ascii_markers(md_file, tmp_path):
    """纯文本最可能被老编辑器或 GBK 环境打开，非 ASCII 符号会变乱码。"""
    res = convert_file(md_file, "txt", out_dir=tmp_path, overwrite=True)
    text = res.outputs[0].path.read_text(encoding="utf-8")
    assert "•" not in text, "列表标记应用 ASCII 连字符"
    # 能用 GBK 编码说明兼容老环境
    text.encode("gb18030")


# ── 往返保真 ──


def test_roundtrip_md_html_md(md_file, tmp_path):
    html = convert_file(
        md_file, "html", out_dir=tmp_path / "a", overwrite=True
    ).outputs[0].path
    back = convert_file(
        html, "md", out_dir=tmp_path / "b", overwrite=True
    ).outputs[0].path

    r = recall(SAMPLE_MD, back.read_text(encoding="utf-8"))
    assert r >= 0.97, f"MD→HTML→MD 词汇召回仅 {r:.1%}"


def test_roundtrip_preserves_inline_formats(md_file, tmp_path):
    """往返后粗体、斜体、代码、链接都该还在。

    这是 IR 把行内格式结构化的直接回报——存成字符串就做不到。
    """
    html = convert_file(
        md_file, "html", out_dir=tmp_path / "a", overwrite=True
    ).outputs[0].path
    back = convert_file(
        html, "md", out_dir=tmp_path / "b", overwrite=True
    ).outputs[0].path
    text = back.read_text(encoding="utf-8")

    assert "**加粗**" in text
    assert "`行内代码`" in text
    assert "https://example.com" in text
    assert re.search(r"(?<!\*)\*斜体\*(?!\*)", text), "斜体丢了"


def test_roundtrip_md_docx_md(md_file, tmp_path):
    docx_path = convert_file(
        md_file, "docx", out_dir=tmp_path / "a", overwrite=True
    ).outputs[0].path
    back = convert_file(
        docx_path, "md", out_dir=tmp_path / "b", overwrite=True
    ).outputs[0].path
    text = back.read_text(encoding="utf-8")

    assert text.lstrip().startswith("#"), "标题层级丢了"
    assert "**加粗**" in text, "粗体丢了"
    assert "领域" in text and "格式数" in text, "表格内容丢了"
    r = recall(SAMPLE_MD, text)
    assert r >= 0.90, f"MD→DOCX→MD 词汇召回仅 {r:.1%}"


def test_docx_preserves_heading_levels(md_file, tmp_path):
    import docx

    out = convert_file(
        md_file, "docx", out_dir=tmp_path, overwrite=True
    ).outputs[0].path
    d = docx.Document(str(out))

    styles = [p.style.name for p in d.paragraphs if p.text.strip()]
    assert any("Heading 1" in s for s in styles)
    assert any("Heading 2" in s for s in styles)
    assert d.tables, "表格没写进 DOCX"
    assert len(d.tables[0].rows) == 3


# ── EPUB ──


def test_epub_structure_is_spec_compliant(md_file, tmp_path):
    """mimetype 必须是第一个条目且不压缩，这是 EPUB 规范的硬要求。

    违反会让部分阅读器直接拒绝打开。
    """
    out = convert_file(
        md_file, "epub", out_dir=tmp_path, overwrite=True
    ).outputs[0].path

    with zipfile.ZipFile(out) as z:
        infos = z.infolist()
        assert infos[0].filename == "mimetype", "mimetype 必须是第一个条目"
        assert infos[0].compress_type == zipfile.ZIP_STORED, "mimetype 不能压缩"
        assert z.read("mimetype") == b"application/epub+zip"

        names = z.namelist()
        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names
        assert "OEBPS/nav.xhtml" in names
        assert any(n.startswith("OEBPS/ch") for n in names)

        # OPF 里的 spine 必须引用到实际存在的章节
        from lxml import etree

        opf = etree.fromstring(z.read("OEBPS/content.opf"))
        ns = {"opf": "http://www.idpf.org/2007/opf"}
        refs = opf.findall(".//opf:spine/opf:itemref", ns)
        assert refs, "spine 是空的"
        ids = {i.get("id"): i.get("href") for i in opf.findall(".//opf:manifest/opf:item", ns)}
        for r in refs:
            href = ids.get(r.get("idref"))
            assert href and f"OEBPS/{href}" in names, f"spine 指向不存在的 {href}"


def test_epub_splits_by_top_heading(tmp_path):
    src = tmp_path / "multi.md"
    src.write_text(
        "# 第一章\n\n内容一。\n\n# 第二章\n\n内容二。\n\n# 第三章\n\n内容三。\n",
        encoding="utf-8",
    )
    out = convert_file(src, "epub", out_dir=tmp_path, overwrite=True).outputs[0].path

    with zipfile.ZipFile(out) as z:
        chapters = [n for n in z.namelist() if n.startswith("OEBPS/ch")]
        assert len(chapters) == 3, f"应切成 3 章，实为 {len(chapters)}"
        nav = z.read("OEBPS/nav.xhtml").decode()
        assert "第一章" in nav and "第三章" in nav, "目录不完整"


def test_roundtrip_md_epub_md(md_file, tmp_path):
    ep = convert_file(
        md_file, "epub", out_dir=tmp_path / "a", overwrite=True
    ).outputs[0].path
    back = convert_file(
        ep, "md", out_dir=tmp_path / "b", overwrite=True
    ).outputs[0].path

    r = recall(SAMPLE_MD, back.read_text(encoding="utf-8"))
    assert r >= 0.95, f"MD→EPUB→MD 词汇召回仅 {r:.1%}"


# ── 跨域：PDF → Markdown ──


def test_pdf_to_markdown_is_inferred():
    """PDF 不含「这是标题」的语义，只有「这段字号更大」。所以是推断。"""
    pytest.importorskip("pymupdf")
    from convcore.pipeline import Converter

    conv = Converter()
    path = conv.plan("application/pdf", "text/markdown")
    assert path.loss is LossTier.INFERRED
    assert not path.loss.is_reversible


def test_pdf_to_markdown_extracts_headings(tmp_path):
    pytest.importorskip("pymupdf")
    from .make_pdf import make_report

    pdf = make_report(tmp_path / "r.pdf")
    res = convert_file(pdf, "md", out_dir=tmp_path, overwrite=True)

    text = res.outputs[0].path.read_text(encoding="utf-8")
    assert "Quarterly Report" in text
    # 字号最大的那行该被判成标题
    assert re.search(r"^#+ .*Quarterly Report", text, re.M), text[:200]
    assert any("推断" in w for w in res.warnings)
