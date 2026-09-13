"""格式图与路径搜索。

图的结构：
- 格式节点 Fmt("image/png")：一个具体的、可落盘的文件格式。
- 枢纽节点 Hub("raster")：某个领域的内存 IR。

边：
- READ:  Fmt → Hub    解码（插件的 parse）
- WRITE: Hub → Fmt    编码（插件的 write）
- DIRECT:Fmt → Fmt    绕过枢纽的引擎原生转换（remux、LibreOffice）
- BRIDGE:Hub → Hub    跨域桥接（vector→raster 栅格化），必须显式声明

于是 HEIC→ICO 自动等于 READ(heic→raster) + WRITE(raster→ico)，不需要有人写过
这一对。加一个新格式插件，所有经由它的路径同时出现——这正是「做得全」的来源。

权重是三元组，字典序比较：
    (最差损失等级, 有损步数, 总时间代价)

为什么首项取路径上的**最大**损失而不是求和：损失等级是序数不是基数，
LOSSY + LOSSY ≠ STRUCTURAL。用户真正在意的是「最差的一环有多差」。
次项数有损步数，用来区分「一次有损」和「两次有损」——同样都是 LOSSY 等级，
但转码两次画质更糟。三项都沿路径单调不减，所以 Dijkstra 成立。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .formats import domain_of, label_of, normalize
from .registry import Capability, Codec, DirectConverter, Registry
from .types import Domain, LossTier, MediaType, NoPathError


class EdgeKind(Enum):
    READ = "read"
    WRITE = "write"
    DIRECT = "direct"
    BRIDGE = "bridge"


# 节点：("fmt", "image/png") 或 ("hub", "raster")
Node = tuple[str, str]


def Fmt(media_type: MediaType) -> Node:
    return ("fmt", normalize(media_type))


def Hub(domain: Domain) -> Node:
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

    def describe(self) -> str:
        if self.kind is EdgeKind.READ:
            return f"{label_of(self.src[1])} → [{self.dst[1]}]"
        if self.kind is EdgeKind.WRITE:
            return f"[{self.src[1]}] → {label_of(self.dst[1])}"
        if self.kind is EdgeKind.BRIDGE:
            return f"[{self.src[1]}] ⇒ [{self.dst[1]}]"
        return f"{label_of(self.src[1])} ⇒ {label_of(self.dst[1])} (直连)"


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

    @property
    def materializations(self) -> int:
        """中间落盘的格式数。Fmt→Hub→Fmt 是 0，DOC→DOCX→MD 是 1。

        这个量才是「路径有多绕」的正确度量，比数跳数准——经枢纽的两跳
        其实是一次转换。
        """
        return sum(
            1
            for e in self.edges[:-1]
            if e.dst[0] == "fmt"
        )

    def describe(self) -> str:
        if not self.edges:
            return "（无需转换）"
        parts = [label_of(self.edges[0].src[1])]
        for e in self.edges:
            parts.append(f"[{e.dst[1]}]" if e.dst[0] == "hub" else label_of(e.dst[1]))
        return " → ".join(parts)


# 跨域桥接：只有语义明确的方向才允许。这张表是「什么算相关文件」的最终答案。
# 注意方向性：矢量→位图是栅格化（合理），位图→矢量是推断（需要显式请求，不自动搜出来）。
# (源域, 目标域, 损失等级, 时间代价, 保真度)
BRIDGES: tuple[tuple[Domain, Domain, LossTier, float, float], ...] = (
    ("vector", "raster", LossTier.STRUCTURAL, 2.0, 1.0),
    ("document", "raster", LossTier.STRUCTURAL, 8.0, 1.0),
    # PDF → 语义文档树：抽文本并按字号推断标题层级。PDF 不含「这是标题」的语义，
    # 所以是 INFERRED。fidelity 低是因为表格和图片全丢——同样是 INFERRED，
    # pdf2docx 会还原表格，这条路不会。没有 fidelity 维度时图搜索会选这条
    # （它快 3 倍），产出一个没有表格的 Word。
    ("document", "markup", LossTier.INFERRED, 6.0, 0.55),
    # 表格 → 树形：每行变成一个对象，总是可行且无损。
    ("table", "tree", LossTier.LOSSLESS, 1.5, 1.0),
    # 树形 → 表格：只在树是「对象数组」时才有意义。形状不对时桥接会明确报错，
    # 而不是静默压平成 a.b.c 列名。嵌套值序列化为 JSON 字符串，所以是 STRUCTURAL。
    ("tree", "table", LossTier.STRUCTURAL, 2.0, 0.7),
    # 表格 → 标记语言：渲染成 Markdown/HTML 表格
    ("table", "markup", LossTier.LOSSLESS, 2.0, 1.0),
    # 树形 → 标记语言：JSON/YAML 渲染成可读文档（对象数组变表格，嵌套变列表）。
    # 结构被映射为文档元素（表格/列表），无信息丢失，标 LOSSLESS。
    ("tree", "markup", LossTier.LOSSLESS, 2.0, 1.0),
    # 标记语言 → PDF：DocTree 排版成页。排页是新增信息（分页点不存在
    # 于流式文档），样式映射有损，标 STRUCTURAL 而不是 LOSSLESS。
    ("markup", "document", LossTier.STRUCTURAL, 3.0, 0.9),
    # 图片 → PDF：每图一页嵌成 PDF 对象，像素原样保留（不加再压缩）。
    # 页面尺寸从图片推断，所以结构上无新增猜测，标 LOSSLESS。
    ("raster", "document", LossTier.LOSSLESS, 2.0, 1.0),
)


class FormatGraph:
    """由注册表构建的格式图。构建一次，多次查询。"""

    def __init__(self, registry: Registry) -> None:
        self._out: dict[Node, list[Edge]] = {}
        self._build(registry)

    def _add(self, edge: Edge) -> None:
        self._out.setdefault(edge.src, []).append(edge)

    def _build(self, registry: Registry) -> None:
        for codec in registry.codecs():
            hub = Hub(codec.domain)
            # 只声明能力不够，还得真有对应方法——否则执行时才 AttributeError。
            # 宁可边不进图（表现为「找不到路径」）也不要运行到一半炸。
            if hasattr(codec, "parse"):
                for cap in codec.reads:
                    self._add(
                        Edge(EdgeKind.READ, Fmt(cap.media_type), hub,
                             cap.loss, cap.time_cost, codec, cap.fidelity)
                    )
            if hasattr(codec, "write"):
                for cap in codec.writes:
                    self._add(
                        Edge(EdgeKind.WRITE, hub, Fmt(cap.media_type),
                             cap.loss, cap.time_cost, codec, cap.fidelity)
                    )

        for conv in registry.direct_converters():
            for src, dst, cap in conv.direct:
                self._add(
                    Edge(EdgeKind.DIRECT, Fmt(src), Fmt(dst),
                         cap.loss, cap.time_cost, conv, cap.fidelity)
                )

        live = registry.domains()
        for a, b, loss, cost, fid in BRIDGES:
            if a in live and b in live:
                self._add(
                    Edge(EdgeKind.BRIDGE, Hub(a), Hub(b), loss, cost, None, fid)
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
    ) -> Path:
        """搜索 src → dst 的最优路径。找不到抛 NoPathError。

        用 Dijkstra 而不是 BFS，因为边有权重（保真路线可能更长）。
        状态是 (节点, 已用中间物化数) 而非单纯节点——中间物化数是一个约束维度，
        只按节点去重会误剪掉「多物化一次但更保真」的合法路径。

        allow_identity=False 时同格式也要搜出真实的 READ→WRITE 路径。用于
        「PNG→PNG 但要缩放」这类同格式重写——直接返回空路径会让选项被静默忽略。
        """
        start, goal = Fmt(src), Fmt(dst)
        if start == goal and allow_identity:
            return Path([])

        # (代价四元组, 序号, 状态, 路径, 标签 ID)。序号用于打破平局，避免比较
        # Edge 对象；标签 ID 用于识别已经被 Pareto frontier 剪掉的堆项。
        Cost = tuple[int, int, float, float]
        counter = 0
        heap: list[tuple[Cost, int, tuple[Node, int], list[Edge], int]] = [
            ((LossTier.COPY, 0, 0.0, 0.0), counter, (start, 0), [], counter)
        ]
        # 单个状态只保留一个字典序最优前缀并不安全：路径的第一项是 max，
        # 后续遇到更高损失等级的边时，原本较低等级的前缀会与其他前缀合并，
        # 此时有损步数/保真度/时间的排序可能反转。因此每个状态保留
        # component-wise 不被支配的 Pareto frontier。
        #
        # frontier[state] 的值是 {label_id: cost}。active 用来让堆中已经被
        # 新标签支配的旧项失效；相同代价的标签由支配关系自然去重。
        frontier: dict[tuple[Node, int], dict[int, Cost]] = {}
        active: set[int] = {counter}
        # 同格式重写时不能把空路径的起点加入 frontier，否则 READ→WRITE 回到
        # 起点时会被空路径支配。起点不是终点时则照常作为零代价标签。
        if start != goal:
            frontier[(start, 0)] = {counter: (0, 0, 0.0, 0.0)}

        def dominates(a: Cost, b: Cost) -> bool:
            """是否能安全地把代价 a 延伸成不差于 b 的任意后缀。"""
            return all(x <= y for x, y in zip(a, b))

        def enqueue(
            state: tuple[Node, int], cost: Cost, path: list[Edge]
        ) -> None:
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

        while heap:
            cost, _, state, edges, label_id = heapq.heappop(heap)
            if label_id not in active:
                continue
            node, mats = state
            # edges 非空的判断处理 allow_identity=False：起点即终点时不能立即返回，
            # 得真的绕一圈枢纽出来
            if node == goal and (edges or allow_identity):
                return Path(edges)
            tier, lossy, fid_penalty, elapsed = cost
            for edge in self.edges_from(node):
                # 落到中间格式节点（不是终点）才算一次物化
                extra = 1 if (edge.dst[0] == "fmt" and edge.dst != goal) else 0
                nmats = mats + extra
                if nmats > max_materializations:
                    continue

                nstate = (edge.dst, nmats)
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
