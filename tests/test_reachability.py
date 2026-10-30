"""「列出来的都是真实可达的」：每个源格式的每个可达目标都真的转一遍。

这是 README 的承诺。以前 PNG 列出 41 个目标，其中 CSV/JSON/Excel 一转就失败，
Markdown/纯文本 转出空文件；WAV 列出 PNG；SVG 列出 Word 却只得到空文档。
每一跳单独看都合法，连起来是假的——只有端到端地转一遍才抓得住。

音视频目标编码慢，而编码器本身由 test_media 覆盖，这里只抽 MP3 / MP4 / WAV 三个；
其余域的目标全量跑——出问题的都是跨域路径。
"""

from __future__ import annotations

import json
import re
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from convcore import Converter, convert_file
from convcore.engines.probe import ENGINES, probe_one
from convcore.formats import domain_of, label_of
from convcore.graph import EdgeKind, Hub

pytest.importorskip("pymupdf", reason="文档桥需要 pymupdf")

_FF = probe_one(next(s for s in ENGINES if s.key == "ffmpeg"))
_MEDIA_SAMPLE = {"audio/mpeg", "video/mp4", "audio/wav"}
"""音视频目标只抽这几个。"""


@pytest.fixture(scope="module")
def conv() -> Converter:
    return Converter()


@pytest.fixture(scope="module")
def sources(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("reach")
    s: dict[str, Path] = {}
    im = Image.new("RGB", (320, 200), "white")
    ImageDraw.Draw(im).text((20, 80), "HELLO 123", fill="black")  # 字只是像素
    s["png"] = d / "a.png"
    im.save(s["png"])
    s["svg"] = d / "icon.svg"
    s["svg"].write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
        '<circle cx="32" cy="32" r="28" fill="#e03b4a"/></svg>',
        encoding="utf-8",
    )
    s["md"] = d / "doc.md"
    s["md"].write_text(
        "# Title\n\nSome paragraph.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n",
        encoding="utf-8",
    )
    s["json"] = d / "rows.json"
    s["json"].write_text(json.dumps([{"name": "a", "n": 1}, {"name": "b", "n": 2}]), encoding="utf-8")
    s["pdf_text"] = convert_file(s["md"], "pdf", out_path=d / "text.pdf").outputs[0].path
    s["pdf_scan"] = convert_file(s["png"], "pdf", out_path=d / "scan.pdf").outputs[0].path
    if _FF.available:
        def ff(*args: str) -> None:
            subprocess.run([str(_FF.path), "-v", "error", "-y", *args], check=True, timeout=120)

        ff("-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10:duration=1", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", str(d / "silent.mp4"))
        s["mp4_silent"] = d / "silent.mp4"
        ff("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "pcm_s16le", str(d / "tone.wav"))
        s["wav"] = d / "tone.wav"
        ff("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-i", str(s["png"]),
           "-map", "0:a", "-map", "1:v", "-c:a", "libmp3lame", "-c:v", "mjpeg",
           "-disposition:v", "attached_pic", str(d / "cover.mp3"))
        s["mp3_cover"] = d / "cover.mp3"
    return s


def _has_content(p: Path) -> bool:
    """产物里有没有东西。空文件、只有外壳的 HTML/Word/PDF 都算没有。"""
    suf = p.suffix.lower()
    if p.stat().st_size == 0:
        return False
    if suf in (".md", ".txt", ".csv", ".tsv", ".yaml", ".toml", ".xml", ".jsonl", ".json"):
        text = p.read_text(encoding="utf-8", errors="replace").strip()
        return bool(text) and text not in ("[]", "{}", "null")
    if suf == ".html":
        body = re.sub(r"(?is)<head.*?</head>", "", p.read_text(encoding="utf-8", errors="replace"))
        return bool(re.search(r"(?i)<(svg|img)\b", body) or re.sub(r"<[^>]+>", "", body).strip())
    if suf in (".docx", ".epub"):
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                if re.search(r"(?i)\.(png|jpe?g|gif|svg)$", n):
                    return True
                if n.endswith("document.xml") or (n.endswith((".xhtml", ".html")) and "nav" not in n):
                    raw = re.sub(r"(?is)<head.*?</head>", "", z.read(n).decode("utf-8", "replace"))
                    if "<svg" in raw or re.search(r">\s*[^<>\s][^<>]*<", raw):
                        return True
        return False
    if suf == ".pdf":
        import pymupdf

        with pymupdf.open(p) as doc:
            return any(pg.get_text().strip() or pg.get_images() or pg.get_drawings() for pg in doc)
    return True


@pytest.mark.parametrize(
    "key", ["png", "svg", "md", "json", "pdf_text", "pdf_scan", "mp4_silent", "wav", "mp3_cover"]
)
def test_every_listed_target_converts(key, sources, conv, tmp_path):
    if key not in sources:
        pytest.skip("需要 ffmpeg")
    src = sources[key]
    src_mt = conv.detect(src)
    targets = conv.targets_for(src_mt)
    listed = conv.refine_targets(src, src_mt, targets)
    assert listed, f"{key} 一个目标都没有"

    problems = []
    for mt in sorted(listed):
        if domain_of(mt) == "media" and mt not in _MEDIA_SAMPLE:
            continue
        path = targets.get(mt)
        if domain_of(src_mt) != "raster" and path is not None and any(
            e.kind is EdgeKind.BRIDGE
            and e.src == Hub("raster")
            and e.dst == Hub("vector")
            for e in path.edges
        ):
            # 视频/PDF 要先渲染成位图才谈得上描摹，refine 无法廉价预检「像不像
            # 图标」，描摹入口的启发式拒绝是设计接受的运行时行为（设计文档 §13）。
            # 光栅源有 refine 的描摹预检兜底，不跳过。
            continue
        try:
            # review_marks 只影响 pdf2docx：不加提示段落，才看得出正文是不是空的
            res = convert_file(src, mt, out_dir=tmp_path / "out", overwrite=True,
                               overrides={"review_marks": False})
        except Exception as exc:
            problems.append(f"{label_of(mt)}: 失败 {str(exc).splitlines()[0][:80]}")
            continue
        if not _has_content(res.outputs[0].path):
            problems.append(f"{label_of(mt)}: 产物为空")
    assert not problems, f"{key} 列出了转不了的目标：\n  " + "\n  ".join(problems)


@pytest.mark.parametrize(
    ("key", "absent"),
    [
        # 贴图标类 PNG 现在可经描摹产出 Markdown（内嵌 SVG），不再是「不可能目标」；
        # 照片 PNG 会被 refine 的描摹预检剔除（见 test_raster_to_vector.py）。
        # 装了 OCR 后 PNG/扫描 PDF 又能直接产出 Word/Markdown/纯文本，这里不再钉死。
        ("png", {"text/csv", "application/json"}),
        ("svg", {"text/plain", "text/csv",
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
        ("pdf_scan", {"text/csv"}),
        ("wav", {"image/png", "application/pdf"}),
        ("mp4_silent", {"audio/mpeg", "audio/wav"}),
    ],
)
def test_impossible_targets_not_listed(key, absent, sources, conv):
    """反向钉住：这些以前列出来、实际转不了的目标，现在不该出现。"""
    if key not in sources:
        pytest.skip("需要 ffmpeg")
    src = sources[key]
    src_mt = conv.detect(src)
    listed = conv.refine_targets(src, src_mt, conv.targets_for(src_mt))
    assert not (absent & set(listed)), f"{key} 仍列出：{sorted(absent & set(listed))}"


def test_same_formats_still_reachable_from_real_sources(sources, conv):
    """约束不能误伤：有文字的 PDF、带封面的 MP3 照常可转。"""
    pdf = sources["pdf_text"]
    listed = conv.refine_targets(pdf, "application/pdf", conv.targets_for("application/pdf"))
    assert {"text/markdown", "text/csv"} <= set(listed)
    if "mp3_cover" in sources:
        mp3 = sources["mp3_cover"]
        listed = conv.refine_targets(mp3, "audio/mpeg", conv.targets_for("audio/mpeg"))
        assert {"image/png", "audio/flac"} <= set(listed)
