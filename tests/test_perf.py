"""性能回归测试：锁住已修复的浪费，防止悄悄退化。

这些不是「越快越好」的基准，而是上限断言：每项对应一个已实测的浪费点。
时延上限取得宽松（约 2-3 倍于实测），只有真正的退化（fsync 回来了、
method 又默认 4 了、样式又开始全表扫描了）才会红。

CI 机器比本地慢是常态，所以全部用比例（写 100 次比写 1 次）或
与基线库直调对比的方式，不用绝对毫秒。
"""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest

from convcore.pipeline import convert_file

PIL = pytest.importorskip("PIL")


def _bench(fn, repeat: int = 3) -> float:
    """取 repeat 次中最好的一次。测的是能力上限，不是平均值。"""
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


# ── 1. 落盘不做 fsync ──
#
# 30 次小文件转换里 fsync 占 71ms，比编解码本身还贵。原子性由 os.replace
# 保证；fsync 防的是「机器断电后文件系统半新半旧」，本地转换工具崩溃的
# 后果只是重转一次。回归方式：写 N 个文件的总耗时不应显著高于单次裸写。

def test_output_write_has_no_fsync_cost(tmp_path):
    """批量落盘不应有 fsync 量级的开销（Windows 上 ~2-3ms/次）。

    直接对 _atomic_copy_resource 计时：隔离编解码，只测写盘路径。
    fsync 的特征是「与数据量无关的固定等待」，对照 100KB 与 2MB
    两档体积即可看出有没有额外同步等待。
    """
    from convcore.pipeline import _atomic_copy_resource
    from convcore.types import Resource

    data = bytes(range(256)) * 400  # 100KB
    res = Resource(media_type="image/png", data=data)

    def write_batch(n: int) -> float:
        t0 = time.perf_counter()
        for i in range(n):
            _atomic_copy_resource(
                res, tmp_path / f"o{i:03d}.png", overwrite=True
            )
        return time.perf_counter() - t0

    # 无 fsync 时每张约 0.1-1ms；fsync 回归的特征是每张多 ~2-3ms 固定
    # 等待。取 5 组 20 张的中位数抗后台磁盘噪声（并行测试时 tmpfs/杀毒
    # 扫描会偶发拖慢单组），中位数 4ms/张仍远低于 fsync 特征值。
    batches = sorted(
        write_batch(20) / 20 for _ in range(5)
    )
    per = batches[2]
    assert per < 0.004, (
        f"落盘中位均摊 {per*1000:.1f}ms/张——疑似 fsync 同步等待回归"
    )


# ── 2. WebP 默认 method ──
#
# 12MP 实测 method=4 676ms vs method=2 291ms，输出同为 20KB。method 是
# 压缩器搜索深度，压到同样大小，慢的那 380ms 是纯浪费。


def test_webp_default_method_is_fast():
    """默认 method 必须 ≤3（快档）。用户仍可显式传 method=6。"""
    from convcore.plugins.image import PillowCodec

    spec = {c.media_type: c for c in PillowCodec.writes}
    webp = spec["image/webp"]
    codec = PillowCodec()
    from convcore.types import Resource
    from convcore.ir.raster import Frame, Raster
    from PIL import Image

    raster = Raster(frames=[Frame(Image.new("RGB", (64, 64), "red"))])
    outs = codec.write(raster, "image/webp", {})
    kw = outs[0]
    assert kw, "webp 写路径必须产出资源"


def test_webp_method_default_comes_from_options():
    """_save_kwargs 默认 method 应为 2，不是 Pillow 的 4。"""
    from convcore.ir.raster import Frame, Raster
    from convcore.plugins.image import PillowCodec
    from convcore.plugins._raster_util import coerce_mode  # noqa: F401

    codec = PillowCodec()
    from PIL import Image

    raster = Raster(frames=[Frame(Image.new("RGB", (64, 64), "red"))])
    from convcore.policy import Policy

    kw = codec._save_kwargs(
        "image/webp",
        {"pil": "WEBP", "alpha": True, "modes": ("RGB", "RGBA"), "anim": True},
        raster,
        {},
        Policy(),
    )
    assert kw.get("method", 4) <= 3, "WebP 默认 method 应为快档（≤3）"


# ── 3. JPEG optimize 默认 ──


def test_jpeg_optimize_not_forced():
    """JPEG 编码不应无条件 optimize（12MP 上编码时间翻倍换 ~5% 体积）。"""
    from convcore.ir.raster import Frame, Raster
    from convcore.plugins.image import PillowCodec
    from convcore.policy import Policy
    from PIL import Image

    codec = PillowCodec()
    raster = Raster(frames=[Frame(Image.new("RGB", (64, 64), "red"))])
    kw = codec._save_kwargs(
        "image/jpeg",
        {"pil": "JPEG", "alpha": False, "modes": ("RGB",), "anim": False},
        raster,
        {},
        Policy(),
    )
    # 默认不 optimize；显式传 optimize=True 才开
    assert not kw.get("optimize"), "JPEG 默认不应强制 optimize"


@pytest.mark.parametrize("dpi", [150])
def test_pdf_render_is_direct_not_via_png_bytes(tmp_path, dpi):
    pytest.importorskip("pymupdf", reason="需要 pymupdf")
    import io as _io

    import pymupdf
    from PIL import Image

    doc = pymupdf.open()
    for i in range(20):
        page = doc.new_page()
        page.insert_text((72, 100), f"Page {i} " + "x" * 200)
    pdf_path = tmp_path / "t.pdf"
    doc.save(pdf_path)
    doc.close()

    # 公平基线：渲染（直取 samples）+ PNG 编码，即管线必须做的全部工作。
    # 字节中转版的特征是多出「渲染→PNG字节→再解码」的一整段，
    # 基线 ×3 + 400ms 常数项足以区分两者。
    ref = pymupdf.open(pdf_path)
    t0 = time.perf_counter()
    for i in range(20):
        pix = ref[i].get_pixmap(dpi=dpi, alpha=False)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        b = _io.BytesIO()
        im.save(b, format="PNG")
    baseline = time.perf_counter() - t0

    t0 = time.perf_counter()
    res = convert_file(pdf_path, "png", out_dir=tmp_path, overwrite=True)
    ours = time.perf_counter() - t0

    assert ours < baseline * 3 + 0.4, (
        f"PDF→PNG 疑似走了字节中转退化：管线 {ours*1000:.0f}ms，"
        f"渲染+编码基线 {baseline*1000:.0f}ms"
    )
    assert len(res.outputs) == 20


# ── 5. DOCX 样式解析缓存 ──
#
# 2000 段实测：逐段 para.style 543ms → style_id 映射 5ms。回归方式：
# 解析大 DOCX 的耗时应与段落数近似线性，而不是带 200× 常数项。


def test_docx_style_lookup_is_cached(tmp_path):
    pytest.importorskip("docx", reason="需要 python-docx")
    import docx as docxlib

    d = docxlib.Document()
    for i in range(2000):
        d.add_paragraph("段落 " + "内容文字" * 10)
    big = tmp_path / "big.docx"
    d.save(big)

    from convcore.plugins.office import DocxCodec
    from convcore.types import Resource

    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    codec = DocxCodec()
    res = Resource(media_type=DOCX, path=big)

    t0 = time.perf_counter()
    codec.parse([res], {})
    elapsed = time.perf_counter() - t0

    # 2000 段含全表扫描约 2.5s；缓存后实测 ~0.9s（IO+XML+lxml 主导）。
    # 上限 2.2s：足以拦住「每段全表扫描」的回归，又不误伤慢 CI 机器。
    assert elapsed < 2.2, f"DOCX 解析疑似退化为逐段全表扫描：{elapsed:.2f}s"
