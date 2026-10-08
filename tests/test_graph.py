"""格式图搜索的行为测试。

用假插件而不是真插件：图搜索的正确性和具体格式无关，注入受控的假拓扑才能
精确验证权重比较和约束生效。
"""

from __future__ import annotations

import pytest

from convcore.graph import EdgeKind, FormatGraph, Path
from convcore.registry import Registry
from convcore.types import Capability, LossTier, NoPathError, caps


class FakeCodec:
    """能力可配置的假插件。"""

    def __init__(self, name, domain, reads, writes):
        self.name, self.domain = name, domain
        self.reads, self.writes = reads, writes

    def parse(self, resources, options):
        return object()

    def write(self, ir, target, options):
        return []


def make_graph(*plugins, gate_bridges: bool = False) -> FormatGraph:
    """假插件图默认不门控——单测不该绑真实依赖（resvg / vtracer 装没装）。"""
    reg = Registry()
    for p in plugins:
        reg.add(p)
    return FormatGraph(reg, gate_bridges=gate_bridges)


def test_hub_gives_unlisted_pair_for_free():
    """核心断言：没人写过 bmp→tiff，但经枢纽自动可达。

    这是「加一个插件、所有路径自动出现」这一设计主张的直接验证。
    """
    g = make_graph(
        FakeCodec("a", "raster", caps("image/bmp"), caps("image/tiff")),
    )
    path = g.find_path("image/bmp", "image/tiff")
    assert [e.kind for e in path.edges] == [EdgeKind.READ, EdgeKind.WRITE]
    assert path.materializations == 0
    assert path.loss is LossTier.LOSSLESS


def test_prefers_lossless_over_faster_lossy():
    """保真优先于速度：慢的无损路径应该胜过快的有损路径。"""
    g = make_graph(
        FakeCodec(
            "slow_lossless", "raster",
            caps("image/bmp"),
            caps("image/tiff", time_cost=50.0),
        ),
        FakeCodec(
            "fast_lossy", "raster",
            caps("image/bmp"),
            caps("image/tiff", loss=LossTier.LOSSY, time_cost=0.1),
        ),
    )
    path = g.find_path("image/bmp", "image/tiff")
    assert path.loss is LossTier.LOSSLESS
    assert path.time_cost > 10


def test_direct_edge_beats_hub_when_lossless():
    """remux 语义：COPY 级直连必须打败经枢纽的重编码。

    这是现有工具最常犯的错——MKV→MP4 只改封装却去重新编码。
    """
    g = make_graph(
        FakeDirect(
            "remux", "media",
            (
                (
                    "video/x-matroska",
                    "video/mp4",
                    Capability("video/mp4", LossTier.COPY, 1.0),
                ),
            ),
        ),
        FakeCodec(
            "transcode", "media",
            caps("video/x-matroska"),
            caps("video/mp4", loss=LossTier.LOSSY, time_cost=100.0),
        ),
    )
    path = g.find_path("video/x-matroska", "video/mp4")
    assert [e.kind for e in path.edges] == [EdgeKind.DIRECT]
    assert path.loss is LossTier.COPY


class FakeDirect:
    """假直连转换器。模拟 LibreOffice 这类黑盒引擎：只能文件进文件出，
    拿不到中间 IR，所以多跳时必须落盘。"""

    def __init__(self, name, domain, pairs):
        self.name, self.domain = name, domain
        self.direct = pairs

    def convert(self, resources, target, options):
        return []


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_multi_hop_through_intermediate_format():
    """DOC→DOCX→MD：soffice 是黑盒直连，pandoc 才走枢纽，所以中间必须落一次盘。"""
    g = make_graph(
        FakeDirect(
            "soffice", "document",
            (("application/msword", DOCX, Capability(DOCX, LossTier.LOSSLESS, 100.0)),),
        ),
        FakeCodec(
            "pandoc", "document",
            caps(DOCX),
            caps("text/markdown", loss=LossTier.STRUCTURAL),
        ),
    )
    path = g.find_path("application/msword", "text/markdown")
    assert path.materializations == 1, f"路径：{path.describe()}"
    assert path.loss is LossTier.STRUCTURAL
    assert [e.kind for e in path.edges] == [
        EdgeKind.DIRECT, EdgeKind.READ, EdgeKind.WRITE
    ]


def test_materialization_limit_blocks_long_paths():
    """物化上限防止搜出荒唐的长链。全用直连边构造，强制每跳都落盘。"""
    def d(name, src, dst):
        return FakeDirect(
            name, "document", ((src, dst, Capability(dst, LossTier.LOSSLESS, 1.0)),)
        )

    g = make_graph(
        d("a", "application/msword", "application/rtf"),
        d("b", "application/rtf", "text/html"),
        d("c", "text/html", "text/markdown"),
    )
    # 需要 2 次中间物化（rtf、html），限 1 应当搜不到
    with pytest.raises(NoPathError):
        g.find_path("application/msword", "text/markdown", max_materializations=1)
    assert g.find_path(
        "application/msword", "text/markdown", max_materializations=2
    ).materializations == 2


def test_lossy_step_count_breaks_tier_ties():
    """同为 LOSSY 等级时，转码次数少的胜出——两次有损比一次更糟。"""
    g = make_graph(
        FakeCodec("direct", "raster", caps("image/png"),
                  caps("image/gif", loss=LossTier.LOSSY, time_cost=9.0)),
        FakeCodec("hop1", "raster", caps("image/png"),
                  caps("image/jpeg", loss=LossTier.LOSSY, time_cost=0.1)),
        FakeCodec("hop2", "raster", caps("image/jpeg"),
                  caps("image/gif", loss=LossTier.LOSSY, time_cost=0.1)),
    )
    path = g.find_path("image/png", "image/gif")
    assert path.lossy_steps == 1
    assert path.materializations == 0


def test_keeps_pareto_prefix_when_later_loss_can_reverse_order():
    """后续高损失边会合并当前等级，不能只保留字典序最优前缀。

    到 common 时，LOSSY→LOSSY 的路线等级更低，但已经有两次有损；
    STRUCTURAL→LOSSLESS 的路线等级更高，却只有一次有损。两条路线随后
    都经过 INFERRED 边，最终等级相同，此时后者才是更优路径。
    """
    def d(name, src, dst, loss):
        return FakeDirect(
            name,
            "test",
            ((src, dst, Capability(dst, loss, 1.0)),),
        )

    source = "application/x-source"
    lossy = "application/x-lossy"
    structural = "application/x-structural"
    common = "application/x-common"
    target = "application/x-target"
    g = make_graph(
        d("lossy-1", source, lossy, LossTier.LOSSY),
        d("lossy-2", lossy, common, LossTier.LOSSY),
        d("structural", source, structural, LossTier.STRUCTURAL),
        d("lossless", structural, common, LossTier.LOSSLESS),
        d("inferred", common, target, LossTier.INFERRED),
    )

    path = g.find_path(source, target, max_materializations=2)
    assert path.loss is LossTier.INFERRED
    assert path.lossy_steps == 2
    assert [e.plugin.name for e in path.edges] == [
        "structural", "lossless", "inferred"
    ]


def test_no_path_raises_with_readable_message():
    g = make_graph(FakeCodec("a", "raster", caps("image/png"), caps("image/bmp")))
    with pytest.raises(NoPathError) as ei:
        g.find_path("image/png", "audio/mpeg")
    assert "找不到" in str(ei.value)


def test_same_format_is_noop():
    g = make_graph(FakeCodec("a", "raster", caps("image/png"), caps("image/png")))
    assert g.find_path("image/png", "image/png").edges == []


def test_raster_to_vector_bridge_is_declared():
    """位图→矢量是声明的桥：READ/BRIDGE/WRITE，档位 INFERRED。"""
    g = make_graph(
        FakeCodec("raster", "raster", caps("image/png"), caps("image/bmp")),
        FakeCodec("vector", "vector", caps("image/svg+xml"), caps("image/svg+xml")),
    )
    path = g.find_path("image/png", "image/svg+xml")
    assert [e.kind for e in path.edges] == [
        EdgeKind.READ, EdgeKind.BRIDGE, EdgeKind.WRITE
    ]
    assert path.loss is LossTier.INFERRED


def test_vector_without_write_is_not_a_target():
    """桥在，但矢量域没有写出边时 PNG→SVG 仍不可达。"""
    g = make_graph(
        FakeCodec("raster", "raster", caps("image/png"), caps("image/bmp")),
        FakeCodec("vector", "vector", caps("image/svg+xml"), ()),
    )
    with pytest.raises(NoPathError):
        g.find_path("image/png", "image/svg+xml")


def _gated_registry() -> Registry:
    reg = Registry()
    reg.add(FakeCodec("raster", "raster", caps("image/png"), caps("image/bmp")))
    reg.add(FakeCodec("vector", "vector", caps("image/svg+xml"), caps("image/svg+xml")))
    return reg


def test_bridge_gate_blocks_when_unavailable(monkeypatch):
    """门控为假时桥不进图：PNG→SVG 找不到路径，而不是运行到一半失败。"""
    import convcore.graph as graph_mod

    monkeypatch.setattr(graph_mod, "bridge_enabled", lambda a, b: False)
    g = FormatGraph(_gated_registry(), gate_bridges=True)
    with pytest.raises(NoPathError):
        g.find_path("image/png", "image/svg+xml")


def test_bridge_gate_allows_when_available(monkeypatch):
    import convcore.graph as graph_mod

    monkeypatch.setattr(graph_mod, "bridge_enabled", lambda a, b: True)
    g = FormatGraph(_gated_registry(), gate_bridges=True)
    assert g.find_path("image/png", "image/svg+xml").edges


def test_bridge_enables_vector_to_raster():
    g = make_graph(
        FakeCodec("vec", "vector", caps("image/svg+xml"), ()),
        FakeCodec("ras", "raster", caps("image/png"), caps("image/png")),
    )
    path = g.find_path("image/svg+xml", "image/png")
    assert [e.kind for e in path.edges] == [
        EdgeKind.READ, EdgeKind.BRIDGE, EdgeKind.WRITE
    ]
    assert path.loss is LossTier.STRUCTURAL


def test_identity_is_direct_two_edges_not_a_detour():
    """同格式重写（PNG→PNG 但要缩放）必须走 READ→WRITE 两跳。

    回归测试：起点被标记成已访问过时，搜索会被迫绕道其他格式
    （PNG→raster→TIFF→raster→PNG），白吃两次编解码。
    """
    g = make_graph(
        FakeCodec("a", "raster", caps("image/png", "image/tiff"),
                  caps("image/png", "image/tiff")),
    )
    path = g.find_path("image/png", "image/png", allow_identity=False)
    assert len(path.edges) == 2, f"绕远了：{path.describe()}"
    assert [e.kind for e in path.edges] == [EdgeKind.READ, EdgeKind.WRITE]
    assert path.materializations == 0


def test_reachable_from_lists_targets():
    g = make_graph(
        FakeCodec("a", "raster", caps("image/png"),
                  caps("image/bmp", "image/tiff", "image/gif")),
    )
    targets = g.reachable_from("image/png")
    assert {"image/bmp", "image/tiff", "image/gif"} <= set(targets)
    assert "image/png" not in targets


# ── 内容约束：桥的产物缺什么、边要什么 ──


def _media_graph():
    return make_graph(
        FakeCodec("ras", "raster", caps("image/png"), ()),
        FakeCodec("med", "media", caps("video/mp4"),
                  (*caps("video/mp4"), *caps("audio/mpeg", requires={"audio"}))),
    )


def test_image_made_video_has_no_audio_track():
    """raster ⇒ media 合成的视频没有音轨：PNG→MP4 可达，PNG→MP3 不列。"""
    g = _media_graph()
    assert g.find_path("image/png", "video/mp4").edges
    with pytest.raises(NoPathError):
        g.find_path("image/png", "audio/mpeg")
    # 真视频不受影响：约束跟着路径走，不是跟着格式走
    assert g.find_path("video/mp4", "audio/mpeg").edges


def _doc_graph():
    return make_graph(
        FakeCodec("ras", "raster", caps("image/png"), ()),
        FakeCodec("doc", "document", caps("application/pdf"), caps("application/pdf")),
        FakeCodec("mk", "markup", caps("text/markdown"),
                  (*caps("text/markdown"), *caps("text/plain", requires={"text"}))),
    )


def test_pdf_from_image_has_no_text_layer():
    """图片嵌成的 PDF 抽不出文字：PNG→PDF 可达，PNG→Markdown 不列。"""
    g = _doc_graph()
    assert g.find_path("image/png", "application/pdf").edges
    with pytest.raises(NoPathError):
        g.find_path("image/png", "text/markdown")
    # 普通 PDF 照常能抽文字
    assert g.find_path("application/pdf", "text/markdown").edges


def test_photo_lacks_trace_blocks_vector_targets():
    """照片缺「可描摹」：raster ⇒ vector 桥走不通，SVG 目标不列出。"""
    g = make_graph(
        FakeCodec("raster", "raster", caps("image/png"), caps("image/bmp")),
        FakeCodec("vector", "vector", caps("image/svg+xml"), caps("image/svg+xml")),
    )
    path = g.find_path("image/png", "image/svg+xml")
    assert path.blocked_under(frozenset({"trace"}))


def test_missing_content_survives_materialization():
    """缺的内容落盘再读回来也补不回来：PNG→PDF 落盘→读回→抽文字 仍然走不通。"""
    g = _doc_graph()
    [bridge] = [e for e in g.edges_from(("hub", "raster")) if e.dst == ("hub", "document")]
    [write_pdf] = [e for e in g.edges_from(("hub", "document")) if e.dst == ("fmt", "application/pdf")]
    [read_pdf] = g.edges_from(("fmt", "application/pdf"))
    [to_markup] = [e for e in g.edges_from(("hub", "document")) if e.dst == ("hub", "markup")]
    [read_png] = g.edges_from(("fmt", "image/png"))
    detour = Path([read_png, bridge, write_pdf, read_pdf, to_markup])
    assert detour.blocked_under(frozenset())
    assert not Path([read_pdf, to_markup]).blocked_under(frozenset())


def test_embedded_svg_tree_has_no_text():
    """vector ⇒ markup 的文档树只有一段 SVG：能写 Markdown/HTML，不列纯文本。"""
    g = make_graph(
        FakeCodec("vec", "vector", caps("image/svg+xml"), ()),
        FakeCodec("mk", "markup", caps("text/markdown"),
                  (*caps("text/markdown"), *caps("text/plain", requires={"text"}))),
    )
    assert g.find_path("image/svg+xml", "text/markdown").edges
    with pytest.raises(NoPathError):
        g.find_path("image/svg+xml", "text/plain")


def test_blocked_under_file_level_lacks():
    """具体文件缺的内容（无声视频没有音轨）由调用方探测后传进来判定。"""
    g = _media_graph()
    path = g.find_path("video/mp4", "audio/mpeg")
    assert path.blocked_under(frozenset({"audio"}))
    assert not path.blocked_under(frozenset({"picture"}))
    assert not g.find_path("video/mp4", "video/mp4", allow_identity=False).blocked_under(
        frozenset({"audio"})
    )
