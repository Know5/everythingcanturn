"""格式图与路径搜索。

图的结构：
- 格式节点 Fmt("image/png")：一个具体的、可落盘的文件格式。
- 枢纽节点 Hub("raster")：某个领域的内存 IR。

边：
- READ:  Fmt → Hub    解码（插件的 parse）
- WRITE: Hub → Fmt    编码（插件的 write）
- BRIDGE:Hub → Hub    跨域桥接（栅格化、描摹、排版…），必须显式声明

于是 HEIC→ICO 自动等于 READ(heic→raster) + WRITE(raster→ico)，不需要有人写过
这一对。加一个新格式插件，所有经由它的路径同时出现——这正是「做得全」的来源。

权重是四元组，字典序比较：
    (最差损失等级, 有损步数, 保真度损失, 总时间代价)

为什么首项取路径上的**最大**损失而不是求和：损失等级是序数不是基数，
LOSSY + LOSSY ≠ STRUCTURAL。用户真正在意的是「最差的一环有多差」。
次项数有损步数，用来区分「一次有损」和「两次有损」——同样都是 LOSSY 等级，
但转码两次画质更糟。四项都沿路径单调不减，所以 Dijkstra 成立。

除了代价，搜索还带内容约束：某些桥的产物天生缺文字或音轨，之后不能再走
需要它们的边（见 CONTENT / BRIDGE_LACKS）。
"""

from __future__ import annotations

import heapq
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .formats import label_of, normalize
from .registry import Registry
from .types import Domain, LossTier, MediaType, NoPathError

logger = logging.getLogger(__name__)


class EdgeKind(Enum):
    """图中的边类型。"""
    READ = "read"
    WRITE = "write"
    BRIDGE = "bridge"


# 节点：("fmt", "image/png") 或 ("hub", "raster")
Node = tuple[str, str]


def Fmt(media_type: MediaType) -> Node:
    """构建格式节点。"""
    return ("fmt", normalize(media_type))


def Hub(domain: Domain) -> Node:
    """构建领域枢纽节点。"""
    return ("hub", domain)


@dataclass(frozen=True, slots=True)
class Edge:
    kind: EdgeKind
    src: Node
    dst: Node
    loss: LossTier
    time_cost: float
    plugin: Any = None
    """执行这条边的插件。BRIDGE 边可能没有插件（由 pipeline 内置处理）。"""
    fidelity: float = 1.0
    """同等级内的相对保真度，1.0 最好。见 Capability.fidelity。"""
    requires: frozenset[str] = frozenset()
    """走这条边要求内容里有什么。见 CONTENT。"""
    lacks: frozenset[str] = frozenset()
    """这条边的产物缺什么。只有桥会设置。"""
    opaque: bool = False
    """产物只能作为终点，不能再继续转换。"""

    def describe(self) -> str:
        if self.kind is EdgeKind.READ:
            return f"{label_of(self.src[1])} → [{self.dst[1]}]"
        if self.kind is EdgeKind.WRITE:
            return f"[{self.src[1]}] → {label_of(self.dst[1])}"
        if self.kind is EdgeKind.BRIDGE:
            return f"[{self.src[1]}] ⇒ [{self.dst[1]}]"
        return f"{label_of(self.src[1])} → {label_of(self.dst[1])}"


@dataclass(slots=True)
class Path:
    """一条转换路径及其代价。"""

    edges: list[Edge]

    @property
    def loss(self) -> LossTier:
        """路径的实际损失等级 = 最差的一环。"""
        return max((e.loss for e in self.edges), default=LossTier.COPY)

    @property
    def lossy_steps(self) -> int:
        return sum(1 for e in self.edges if e.loss >= LossTier.LOSSY)

    @property
    def fidelity_penalty(self) -> float:
        """保真度损失累计。越小越好，用于同损失等级内的排序。"""
        return sum(1.0 - e.fidelity for e in self.edges)

    @property
    def time_cost(self) -> float:
        return sum(e.time_cost for e in self.edges)

    def blocked_under(self, lacks: frozenset[str]) -> bool:
        """源文件本身缺这些内容时，这条路径是否走不通。

        图搜索只知道格式不知道内容；「这个 WAV 没有封面」「这份 PDF 是扫描件」
        要打开文件才知道，由 Converter.refine_targets 探测后在这里判定。
        """
        for e in self.edges:
            nxt = _step(lacks, e)
            if nxt is None:
                return True
            lacks = nxt
        return False

    @property
    def materializations(self) -> int:
        """中间落盘的格式数。Fmt→Hub→Fmt 是 0，DOC→DOCX→MD 是 1。

        经枢纽的两跳其实是一次转换；只有路径中间出现 Fmt 节点才算多绕了一层。
        """
        return sum(1 for e in self.edges[:-1] if e.dst[0] == "fmt")

    def describe(self) -> str:
        if not self.edges:
            return "（无需转换）"
        parts = [label_of(self.edges[0].src[1])]
        for e in self.edges:
            parts.append(f"[{e.dst[1]}]" if e.dst[0] == "hub" else label_of(e.dst[1]))
        return " → ".join(parts)


# 跨域桥接：只有语义明确的方向才允许。这张表是「什么算相关文件」的最终答案。
# 注意方向性：矢量→位图是栅格化（确定性的），位图→矢量是描摹（轮廓是猜的）。
# 描摹自动搜出来，但标 INFERRED，且执行期启发式拒绝不像图标的输入（照片）。
# (源域, 目标域, 损失等级, 时间代价, 保真度)
BRIDGES: tuple[tuple[Domain, Domain, LossTier, float, float], ...] = (
    ("vector", "raster", LossTier.STRUCTURAL, 2.0, 1.0),
    # 位图 → 矢量：vtracer 描摹。轮廓是猜的，标 INFERRED。fidelity 0.9 是有意
    # 的高值：图标的 PNG→HTML 改走「描摹再嵌 SVG」，而不是 raster→document→markup
    # 那条对图标几乎抽不到字的路径。照片在描摹入口被启发式拦住（--force 可绕过）。
    ("raster", "vector", LossTier.INFERRED, 8.0, 0.9),
    ("document", "raster", LossTier.STRUCTURAL, 8.0, 1.0),
    # PDF → 语义文档树：抽文本并按字号推断标题层级。PDF 不含「这是标题」的语义，
    # 所以是 INFERRED。fidelity 0.85：完整保留文本内容和标题层级，只丢失表格和图片。
    # 对于通用文档提取（标题、段落、大纲）优于 document→table 绕道。
    ("document", "markup", LossTier.INFERRED, 6.0, 0.85),
    # 表格 → 树形：每行变成一个对象，总是可行且无损。
    ("table", "tree", LossTier.LOSSLESS, 1.5, 1.0),
    # 树形 → 表格：只在树是「对象数组」时才有意义。形状不对时桥接会明确报错，
    # 而不是静默压平成 a.b.c 列名。嵌套值序列化为 JSON 字符串，所以是 STRUCTURAL。
    ("tree", "table", LossTier.STRUCTURAL, 2.0, 0.7),
    # 表格 → 标记语言：渲染成 Markdown/HTML 表格
    ("table", "markup", LossTier.LOSSLESS, 2.0, 1.0),
    # 表格 → 文档：直接渲染成 PDF 表格页面。数据完整保留，样式是默认样式，
    # 标 LOSSLESS。相比 table→markup→document，这条路省去中间标记语言的物化。
    ("table", "document", LossTier.LOSSLESS, 2.5, 1.0),
    # 树形 → 标记语言：JSON/YAML 渲染成可读文档（对象数组变表格，嵌套变列表）。
    # 结构被映射为文档元素（表格/列表），无信息丢失，标 LOSSLESS。
    ("tree", "markup", LossTier.LOSSLESS, 2.0, 1.0),
    # 标记语言 → PDF：DocTree 排版成页。排页是新增信息（分页点不存在
    # 于流式文档），样式映射有损，标 STRUCTURAL 而不是 LOSSLESS。
    ("markup", "document", LossTier.STRUCTURAL, 3.0, 0.9),
    # 图片 → PDF：每图一页嵌成 PDF 对象，像素原样保留（不加再压缩）。
    # 页面尺寸从图片推断，所以结构上无新增猜测，标 LOSSLESS。
    ("raster", "document", LossTier.LOSSLESS, 2.0, 1.0),
    # 视频/音频 → 位图：提取封面帧（第一帧）。对视频文件有意义（截图），
    # 音频只有带封面图时才有（BRIDGE_REQUIRES 要求 picture，纯音频由
    # refine_targets 探测后剔除）。标 LOSSLESS 是因为帧本身不丢信息，
    # 只是从流中取了一个切片。
    ("media", "raster", LossTier.LOSSLESS, 1.5, 1.0),
    # 位图 → 视频：图片序列/多帧动图合成视频。帧像素无损保留（只是重编码），
    # 标 LOSSLESS。如果目标是有损编码器（H.264/VP9），损失由 WRITE 边承担。
    ("raster", "media", LossTier.LOSSLESS, 2.5, 1.0),
    # PDF → 表格：提取文档中的表格。pdfplumber 只能抽取明确的表格结构，
    # 对于文本排版成列的"伪表格"无能为力，标 INFERRED（内容完整但结构推断）。
    # fidelity 0.8：多页多表逐页提取，但跨表关系与合并单元格仍可能识别不准。
    ("document", "table", LossTier.INFERRED, 5.0, 0.8),
    # 矢量 → 标记语言：SVG 直接嵌入 HTML，保留可缩放性。零损失，比栅格化更优。
    ("vector", "markup", LossTier.LOSSLESS, 1.0, 1.0),
)


# ── 桥的依赖门控 ──
#
# 有些桥的可用性由外部依赖决定（栅格化要渲染器、描摹要 vtracer），而这些依赖
# 不是 Codec，不经过注册表的 available 判定。探测函数在函数体内懒加载插件，
# 避免 graph 在插件加载前被 import 时形成循环。


def _has_svg_renderer() -> bool:
    from .plugins.vector import has_svg_renderer

    return has_svg_renderer()


def _has_vtracer() -> bool:
    from .plugins.vector import has_vtracer

    return has_vtracer()


BRIDGE_GATES: dict[tuple[Domain, Domain], Callable[[], bool]] = {
    ("vector", "raster"): _has_svg_renderer,
    ("raster", "vector"): _has_vtracer,
}

BRIDGE_GATE_HINTS: dict[tuple[Domain, Domain], str] = {
    ("vector", "raster"): "缺 SVG 渲染器：pip install resvg-py",
    ("raster", "vector"): "缺描摹引擎 vtracer：pip install vtracer",
}
"""门控失败时给用户看的原因，doctor 用它解释桥为什么不可用。"""


def bridge_enabled(src: Domain, dst: Domain) -> bool:
    """桥上挂的依赖是否就绪。没有门控的桥恒为真。"""
    gate = BRIDGE_GATES.get((src, dst))
    return True if gate is None else gate()

# ── 内容约束 ──
#
# 有些桥的产物天生缺某种内容：raster ⇒ document 嵌出来的 PDF 页只有像素、没有
# 文本层；raster ⇒ media 合成的视频没有音轨；vector ⇒ markup 得到的文档树里只有
# 一段原始 SVG，没有文字。而下游有些边恰好需要这些内容：抽文字、抽表格、写 TXT、
# 写 MP3。每一跳单独看都合法，连起来却注定失败或产出空文件——
# PNG → [raster] → [media] → MP3、SVG → [vector] → [markup] → 纯文本 都是这样。
# 所以不能靠删桥解决，得在搜索里按路径排除。
#
# 缺的内容沿路径只增不减：没有 OCR、也没有配音，缺了就补不回来。
# 具体文件缺什么（纯音频没画面、扫描件没文本层、照片不可描摹）由
# Converter.refine_targets 探测。
CONTENT = ("text", "audio", "picture", "trace")
"""内容标记。text=文字/文本层，audio=音轨，picture=画面（视频轨或封面图），
trace=可描摹（图标/线稿；照片缺它，描摹桥在入口会拒绝）。"""

BRIDGE_LACKS: dict[tuple[Domain, Domain], frozenset[str]] = {
    ("raster", "document"): frozenset({"text"}),
    ("raster", "media"): frozenset({"audio"}),
    ("vector", "markup"): frozenset({"text"}),
}
"""桥的产物缺什么。"""

BRIDGE_REQUIRES: dict[tuple[Domain, Domain], frozenset[str]] = {
    ("document", "markup"): frozenset({"text"}),
    ("document", "table"): frozenset({"text"}),
    ("markup", "document"): frozenset({"text"}),  # 只有一段 SVG 的文档树排不出内容
    ("media", "raster"): frozenset({"picture"}),
    ("raster", "vector"): frozenset({"trace"}),  # 照片描出来只会是色块海报
}
"""桥要求输入有什么。READ / WRITE 边的要求来自 Capability.requires。"""


def _step(lacks: frozenset[str], edge: Edge) -> frozenset[str] | None:
    """沿一条边前进后内容缺什么。这条边走不通（它要的正好缺）时返回 None。"""
    if edge.requires & lacks:
        return None
    return lacks | edge.lacks if edge.lacks else lacks


class FormatGraph:
    """由注册表构建的格式图。构建一次，多次查询。"""

    def __init__(self, registry: Registry, *, gate_bridges: bool = True) -> None:
        """gate_bridges=False 只看域是否活着，供不绑真实依赖的测试图使用。"""
        self._out: dict[Node, list[Edge]] = {}
        self._build(registry, gate_bridges=gate_bridges)

    def _add(self, edge: Edge) -> None:
        self._out.setdefault(edge.src, []).append(edge)

    def _build(self, registry: Registry, *, gate_bridges: bool = True) -> None:
        for codec in registry.codecs():
            hub = Hub(codec.domain)
            # 只声明能力不够，还得真有对应方法——否则执行时才 AttributeError。
            # 宁可边不进图（表现为「找不到路径」）也不要运行到一半炸。
            if hasattr(codec, "parse"):
                for cap in codec.reads:
                    self._add(
                        Edge(EdgeKind.READ, Fmt(cap.media_type), hub,
                             cap.loss, cap.time_cost, codec, cap.fidelity,
                             requires=cap.requires)
                    )
            if hasattr(codec, "write"):
                for cap in codec.writes:
                    self._add(
                        Edge(EdgeKind.WRITE, hub, Fmt(cap.media_type),
                             cap.loss, cap.time_cost, codec, cap.fidelity,
                             requires=cap.requires, opaque=cap.opaque)
                    )

        live = registry.domains()
        for a, b, loss, cost, fid in BRIDGES:
            if a in live and b in live and (not gate_bridges or bridge_enabled(a, b)):
                self._add(
                    Edge(EdgeKind.BRIDGE, Hub(a), Hub(b), loss, cost, None, fid,
                         requires=BRIDGE_REQUIRES.get((a, b), frozenset()),
                         lacks=BRIDGE_LACKS.get((a, b), frozenset()))
                )

    def edges_from(self, node: Node) -> list[Edge]:
        return self._out.get(node, [])

    def find_path(
        self,
        src: MediaType,
        dst: MediaType,
        *,
        max_materializations: int = 2,
        allow_identity: bool = True,
        lacks: frozenset[str] = frozenset(),
    ) -> Path:
        """搜索 src → dst 的最优路径。找不到抛 NoPathError。

        用 Dijkstra 而不是 BFS，因为边有权重（保真路线可能更长）。
        状态是 (节点, 已用中间物化数, 内容缺什么) 而非单纯节点——物化数和内容
        都是约束维度，只按节点去重会误剪掉合法路径：同一个 document 枢纽，
        从 Markdown 排版进来的能抽文字，从图片嵌进来的不能。

        allow_identity=False 时同格式也要搜出真实的 READ→WRITE 路径。用于
        「PNG→PNG 但要缩放」这类同格式重写——直接返回空路径会让选项被静默忽略。
        """
        start, goal = Fmt(src), Fmt(dst)
        if start == goal and allow_identity:
            return Path([])

        # (代价四元组, 序号, 状态, 路径, 标签 ID)。序号用于打破平局，避免比较
        # Edge 对象；标签 ID 用于识别已经被 Pareto frontier 剪掉的堆项。
        Cost = tuple[int, int, float, float]
        State = tuple[Node, int, frozenset[str]]  # (节点, 中间物化数, 内容缺什么)
        counter = 0
        initial_lacks = frozenset(lacks)
        heap: list[tuple[Cost, int, State, list[Edge], int]] = [
            ((LossTier.COPY, 0, 0.0, 0.0), counter, (start, 0, initial_lacks), [], counter)
        ]
        # 单个状态只保留一个字典序最优前缀并不安全：路径的第一项是 max，
        # 后续遇到更高损失等级的边时，原本较低等级的前缀会与其他前缀合并，
        # 此时有损步数/保真度/时间的排序可能反转。因此每个状态保留
        # component-wise 不被支配的 Pareto frontier。
        #
        # frontier[state] 的值是 {label_id: cost}。active 用来让堆中已经被
        # 新标签支配的旧项失效；相同代价的标签由支配关系自然去重。
        frontier: dict[State, dict[int, Cost]] = {}
        active: set[int] = {counter}
        # 同格式重写时不能把空路径的起点加入 frontier，否则 READ→WRITE 回到
        # 起点时会被空路径支配。起点不是终点时则照常作为零代价标签。
        if start != goal:
            frontier[(start, 0, initial_lacks)] = {counter: (0, 0, 0.0, 0.0)}

        def dominates(a: Cost, b: Cost) -> bool:
            """是否能安全地把代价 a 延伸成不差于 b 的任意后缀。"""
            return all(x <= y for x, y in zip(a, b))

        def enqueue(state: State, cost: Cost, path: list[Edge]) -> None:
            """加入状态的 Pareto frontier，并失效被新标签支配的旧标签。"""
            nonlocal counter
            labels = frontier.setdefault(state, {})
            if any(dominates(existing, cost) for existing in labels.values()):
                return

            for label_id, existing in list(labels.items()):
                if dominates(cost, existing):
                    del labels[label_id]
                    active.discard(label_id)

            counter += 1
            label_id = counter
            labels[label_id] = cost
            active.add(label_id)
            heapq.heappush(heap, (cost, counter, state, path, label_id))

        # 收集到达终点的候选路径用于调试日志
        candidates: list[tuple[Cost, list[Edge]]] = []

        while heap:
            cost, _, state, edges, label_id = heapq.heappop(heap)
            if label_id not in active:
                continue
            node, mats, lacks = state
            # edges 非空的判断处理 allow_identity=False：起点即终点时不能立即返回，
            # 得真的绕一圈枢纽出来
            if node == goal and (edges or allow_identity):
                candidates.append((cost, edges))
                # 只取第一个到达终点的路径（堆保证了它是最优的）
                if len(candidates) == 1:
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(
                            f"路径选择: {label_of(src)} → {label_of(dst)}\n"
                            f"  选中: {Path(edges).describe()}\n"
                            f"  成本: (max_loss={cost[0]}, lossy_steps={cost[1]}, "
                            f"fid_penalty={cost[2]:.2f}, time={cost[3]:.1f})"
                        )
                    return Path(edges)
            tier, lossy, fid_penalty, elapsed = cost
            for edge in self.edges_from(node):
                nlacks = _step(lacks, edge)
                if nlacks is None:
                    continue
                if edge.opaque and edge.dst[0] == "fmt" and edge.dst != goal:
                    continue  # OCR 文本等产物不能再当下一跳源
                # 落到中间格式节点（不是终点）才算一次物化
                extra = 1 if (edge.dst[0] == "fmt" and edge.dst != goal) else 0
                nmats = mats + extra
                if nmats > max_materializations:
                    continue

                nstate = (edge.dst, nmats, nlacks)
                # 字典序：先最小化最差损失等级，再最小化有损步数，
                # 再最小化保真度损失，最后才比时间。
                # 保真度必须排在时间前面——否则同等级里最快的路径胜出，
                # 而最快的往往是最糙的。
                ncost = (
                    max(tier, int(edge.loss)),
                    lossy + (1 if edge.loss >= LossTier.LOSSY else 0),
                    round(fid_penalty + (1.0 - edge.fidelity), 6),
                    elapsed + edge.time_cost,
                )
                enqueue(nstate, ncost, [*edges, edge])

        raise NoPathError(
            f"找不到 {label_of(src)} → {label_of(dst)} 的转换路径"
            f"（{src} → {dst}）"
        )

    def reachable_from(
        self, src: MediaType, *, max_materializations: int = 2
    ) -> dict[MediaType, Path]:
        """从某格式出发能到达的所有目标及其最优路径。

        UI 拖入文件后就调这个填充「转换为」下拉框——目标列表是查出来的，
        不是硬编码的。这是注册表设计的直接回报。
        """
        out: dict[MediaType, Path] = {}
        for node in self._out:
            if node[0] != "fmt" or node[1] == normalize(src):
                continue
            try:
                out[node[1]] = self.find_path(
                    src, node[1], max_materializations=max_materializations
                )
            except NoPathError:
                continue
        # 目标节点可能只作为终点出现（没有出边），补扫一遍
        for edges in list(self._out.values()):
            for e in edges:
                if e.dst[0] == "fmt" and e.dst[1] not in out and e.dst[1] != normalize(src):
                    try:
                        out[e.dst[1]] = self.find_path(
                            src, e.dst[1], max_materializations=max_materializations
                        )
                    except NoPathError:
                        continue
        return out
