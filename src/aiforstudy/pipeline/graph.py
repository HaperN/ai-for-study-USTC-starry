"""One execution graph shared by single units, chapters and whole books.

Prepare book sources and concepts first. The author emits content and media
intent together; content review precedes media compilation/execution. Media and
interaction results are then composed with the approved reader content.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts.scope import ScopePlan
from ..errors import ContractError

#: 阶段图版本。**旧 run 缺这个字段就视作 legacy**（§6.3：不原地升级、不混新节点）。
#: 3.0 = 新增 `text_audit` 正文前置门 + `figures` 依赖它。
GRAPH_VERSION = "8.0"

# Review outcomes belong to the actual content/media contract. Technical review
# failures can produce unverified previews; they are never promoted to accepted.
EDGE_DISPATCH_GATES: dict[tuple[str, str], str] = {}


def dispatch_quality_gate(consumer_stage: str, producer_stage: str) -> str | None:
    """消费阶段对某条上游边要求的质量态；`None` = 只要上游跑完即可。"""
    return EDGE_DISPATCH_GATES.get((producer_stage, consumer_stage))


#: 粒度：整本一次 / 每章一次 / 每个教学单元一次
GRAIN_BOOK = "book"
GRAIN_CHAPTER = "chapter"
GRAIN_UNIT = "unit"
GRAIN_SCOPE = "scope"
GRAINS = (GRAIN_BOOK, GRAIN_CHAPTER, GRAIN_UNIT, GRAIN_SCOPE)


@dataclass(frozen=True)
class StageSpec:
    """一个阶段的声明。`requires` 写的是**阶段名**，实例化时才解析成节点 id。"""
    name: str
    grain: str
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    required: bool = True
    in_prepare: bool = False          #: `aifs prepare` 走到这里为止
    #: 允许"经政策认可的 not_applicable"作为合法终态（§8：图与交互可以有
    #: "没有适用对象"的终态）。**其它阶段一律不允许用它冒充完成**（§9）。
    allow_not_applicable: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if self.grain not in GRAINS:
            raise ContractError(f"阶段 {self.name!r} 的 grain 必须是 {GRAINS}，实际 {self.grain!r}")


#: **唯一**的阶段表。加一步只改这里。
CANONICAL_STAGES: tuple[StageSpec, ...] = (
    StageSpec("mineru", GRAIN_BOOK, (), ("parse.mineru",), in_prepare=True,
              description="MinerU 获取或复用（已有结果核验源哈希后复用；失败不回退 OCR）"),
    StageSpec("toc", GRAIN_BOOK, ("mineru",), ("parse.toc",), in_prepare=True,
              description="目录抽取 → 对账 → review/apply/recover/align"),
    StageSpec("structure", GRAIN_BOOK, ("toc",), ("parse.structure",), in_prepare=True,
              description="目录权威结构与原文资料保全：normalize / export / body / catalog"),
    StageSpec("export", GRAIN_BOOK, ("structure",), ("parse.catalog",), in_prepare=True,
              description="规范化与导出（chapters.json 等）"),
    StageSpec("body", GRAIN_BOOK, ("export",), ("parse.body",), in_prepare=True,
              description="正文归属：目录声明节点 / 内容承载节点 / 空节 / 孤儿"),
    StageSpec("concepts", GRAIN_BOOK, ("body",), ("parse.concepts",), in_prepare=True,
              description="全书概念抽取 + 单写方合并 + 完整索引（冻结后才生成讲解）"),
    StageSpec("source_format", GRAIN_BOOK, ("body",), ("source.formatted",), required=False,
              description="未进入正文单元的独立资料整理格式；保留原文，失败回退，不作数学审核"),
    StageSpec("lesson", GRAIN_UNIT, ("concepts",), ("lesson.unit",),
              description="作者同次生成正文 JSON、习题原文区与图片/动画需求"),
    StageSpec("text_audit", GRAIN_UNIT, ("lesson",), ("audit.text",),
              description="内容审核与通过收据"),
    StageSpec("media_plan", GRAIN_UNIT, ("lesson", "text_audit"), ("media.requirements",),
              description="核验作者媒体需求并编译图片/动画执行规格"),
    StageSpec("figures", GRAIN_UNIT, ("lesson", "text_audit", "media_plan"), ("media.manifest",), allow_not_applicable=True,
              description="已批准需求 → 图片、过程图与动画 → 逐项结果台账"),
    StageSpec("audit", GRAIN_UNIT, ("lesson", "text_audit", "media_plan", "figures"), ("audit.unit",),
              description="媒体验收：需求、实际资源及冻结内容版本核验"),
    StageSpec("interaction", GRAIN_UNIT, ("lesson", "text_audit", "media_plan", "figures", "audit"), ("interaction.unit",),
              allow_not_applicable=True,
              description="冻结教案之后生成教学交互规格并验证"),
    StageSpec("render", GRAIN_CHAPTER, ("text_audit", "audit", "interaction", "source_format"), ("render.chapter",),
              description="按范围汇总渲染页面与交互验收"),
    StageSpec("delivery", GRAIN_SCOPE, ("render",), ("delivery.scope",),
              description="章节 HTML / 离线验收 / 发布清单"),
)

STAGE_BY_NAME: dict[str, StageSpec] = {spec.name: spec for spec in CANONICAL_STAGES}

# Preparation consumers share the graph's order and membership. Parsing stops
# at body; concepts completes the full preparation workflow.
PREPARATION_STAGES = tuple(spec.name for spec in CANONICAL_STAGES if spec.in_prepare)
PARSE_STAGES = PREPARATION_STAGES[:PREPARATION_STAGES.index('body') + 1]
MEDIA_TICKET_STAGES = ('media_plan', 'figures', 'audit')


def stage(name: str) -> StageSpec:
    if name not in STAGE_BY_NAME:
        raise ContractError(f"没有 {name!r} 这个阶段；有的是：{list(STAGE_BY_NAME)}")
    return STAGE_BY_NAME[name]


@dataclass(frozen=True)
class Node:
    """图里的一个节点。`node_id` 形如 `concepts`、`lesson:CH03-S01`、`render:CH03`。"""
    node_id: str
    stage: str
    scope_key: str
    requires: tuple[str, ...] = ()
    required: bool = True
    allow_not_applicable: bool = False

    def as_dict(self) -> dict:
        return {"node_id": self.node_id, "stage": self.stage, "scope_key": self.scope_key,
                "requires": list(self.requires), "required": self.required,
                "allow_not_applicable": self.allow_not_applicable}


@dataclass(frozen=True)
class ExecutionGraph:
    """一张可执行图。**同一份代码产出的图，作用域只体现在节点集合上。**"""
    nodes: tuple[Node, ...]
    goal: str
    profile: str
    scope_digest: str = ""
    truncated_at: str | None = None      #: `--until STAGE` 时停在哪一步
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: 阶段图版本。**缺它 = legacy run**（§6.3）：不原地升级、不在同一个旧 run 里混新节点。
    graph_version: str = GRAPH_VERSION

    def by_id(self) -> dict[str, Node]:
        return {n.node_id: n for n in self.nodes}

    def order(self) -> tuple[str, ...]:
        """拓扑序（稳定）。同层按 `CANONICAL_STAGES` 顺序、再按 scope_key 排。"""
        nodes = self.by_id()
        rank = {spec.name: i for i, spec in enumerate(CANONICAL_STAGES)}
        done: list[str] = []
        seen: set[str] = set()
        remaining = dict(nodes)
        while remaining:
            ready = [n for n in remaining.values()
                     if all(r in seen or r not in nodes for r in n.requires)]
            if not ready:
                # 环：这在固定阶段表里不该发生，发生了就是表写错了
                raise ContractError(
                    f"阶段图里有环，无法排序：{[n.node_id for n in remaining.values()]}")
            ready.sort(key=lambda n: (rank.get(n.stage, 99), n.scope_key, n.node_id))
            for node in ready:
                seen.add(node.node_id)
                done.append(node.node_id)
                remaining.pop(node.node_id)
        return tuple(done)

    def dependents(self, node_id: str) -> tuple[str, ...]:
        return tuple(n.node_id for n in self.nodes if node_id in n.requires)

    def as_dict(self) -> dict:
        return {"graph_version": self.graph_version,
                "goal": self.goal, "profile": self.profile, "scope_digest": self.scope_digest,
                "truncated_at": self.truncated_at, "notes": list(self.notes),
                "nodes": [n.as_dict() for n in self.nodes],
                "order": list(self.order())}

    def digest(self) -> str:
        from ..core.digest import digest_of
        return digest_of({"graph_version": self.graph_version,
                          "goal": self.goal, "profile": self.profile,
                          "nodes": [n.as_dict() for n in self.nodes],
                          "truncated_at": self.truncated_at})


def _units_by_chapter(plan: ScopePlan) -> dict[str, list[str]]:
    """从单元键推出它属于哪一章。**只用键的构造规则**，不猜、不读产物。

    单元键形如 `CH03`（章导言）/ `CH03-S01` / `CH03-S01-SS02` —— 第一段永远是章。
    这也是为什么 §4 要求"CH 编号只在指定解析版本中解析，不做字符串前缀匹配"：
    匹配前缀会把 `CH1` 和 `CH10` 混起来，而这里取的是**完整的一段**。
    """
    out: dict[str, list[str]] = {}
    for unit in plan.resolved_scope or ():
        chapter = unit.split("-", 1)[0]
        out.setdefault(chapter, []).append(unit)
    return out


def build_graph(plan: ScopePlan, *, until: str | None = None) -> ExecutionGraph:
    """**只读**地把计划变成执行图。不建目录、不写产物、不调用任何服务。"""
    if plan.resolved_scope is None:
        raise ContractError(
            "计划还没有解析出范围（resolved_scope=None），无法建图 —— "
            "方案 §6：run 要先在 catalog 形成后校验并固定 scope，合法才继续。")

    stages = list(CANONICAL_STAGES)
    if until is not None:
        if until not in STAGE_BY_NAME:
            raise ContractError(
                f"--until 的取值 {until!r} 不是一个阶段；有的是：{list(STAGE_BY_NAME)}")
        cut = [s.name for s in CANONICAL_STAGES].index(until)
        stages = stages[:cut + 1]
    if plan.goal.value == "prepare":
        stages = [s for s in stages if s.in_prepare]
    active = {s.name for s in stages}

    units = list(plan.resolved_scope)
    chapters: list[str] = []
    for chapter in _units_by_chapter(plan).keys():
        if chapter not in chapters:
            chapters.append(chapter)

    nodes: list[Node] = []
    notes: list[str] = []

    def scope_for(spec: StageSpec) -> list[str]:
        if spec.grain == GRAIN_BOOK:
            return ["book"]
        if spec.grain == GRAIN_SCOPE:
            return ["scope"]
        if spec.grain == GRAIN_CHAPTER:
            return chapters
        return units

    def node_id(spec: StageSpec, scope: str) -> str:
        return spec.name if spec.grain in (GRAIN_BOOK, GRAIN_SCOPE) else f"{spec.name}:{scope}"

    def resolve_requires(spec: StageSpec, scope: str) -> tuple[str, ...]:
        """把阶段名依赖解析成节点 id。

        规则（只有这一份）：

        * 依赖**整本**阶段 → 就是那个节点（书级只有一份）；
        * 单元级依赖单元级 → 同 scope 的那个节点；
        * 章级/范围级依赖单元级 → **本章/本范围内所有**对应单元节点；
        * 范围级依赖章级 → **所有**章节点。

        ⚠️ 最后一条是第一版漏掉的，而且漏得很安静（2026-10-05 实测抓到）：
        `delivery`（范围级）依赖 `render`（章级）时，代码走了
        `f"{dep_name}:{scope}"`，而范围级节点的 scope 是字符串 `"scope"`，
        于是解析出 **`render:scope`** —— 一个不存在的节点；`ready()` 里的
        `dep not in pending` 把它当成"已满足"，**结果 delivery 在整条链一开始就跑了**。
        文件照样生成、退出码照样 0，只有事件流里"delivery 出现在 mineru 之前"
        这一条能看出来。所以现在建完图会核对"每个依赖都指向真实存在的节点"，
        见 `_assert_closed`。
        """
        out: list[str] = []
        for dep_name in spec.requires:
            if dep_name not in active:
                continue
            dep = stage(dep_name)
            if dep.grain == GRAIN_BOOK:
                out.append(dep_name)
            elif dep.grain == GRAIN_SCOPE:
                out.append(dep_name)
            elif dep.grain == GRAIN_CHAPTER:
                if spec.grain == GRAIN_CHAPTER:
                    out.append(f"{dep_name}:{scope}")
                else:                       # 范围级依赖章级 → **所有**章
                    out.extend(f"{dep_name}:{ch}" for ch in chapters)
            else:                                   # 单元级
                if spec.grain == GRAIN_UNIT:
                    out.append(f"{dep_name}:{scope}")
                elif spec.grain == GRAIN_CHAPTER:
                    out.extend(f"{dep_name}:{u}" for u in _units_by_chapter(plan).get(scope, []))
                else:                               # 范围级依赖单元级 → 全部单元
                    out.extend(f"{dep_name}:{u}" for u in units)
        return tuple(dict.fromkeys(out))

    for spec in stages:
        if spec.grain == GRAIN_BOOK and spec.name == "mineru":
            pass
        for scope in scope_for(spec):
            requires = resolve_requires(spec, scope)
            nodes.append(Node(node_id=node_id(spec, scope), stage=spec.name, scope_key=scope,
                              requires=requires, required=spec.required,
                              allow_not_applicable=spec.allow_not_applicable))

    # ── 图闭合自检：**每个依赖都必须指向真实存在的节点**
    #    为什么值得一道断言：解析出的节点 id 拼错时（`render:scope`），
    #    `ready()` 会把不存在的依赖当成"已满足"，于是节点**提前执行**且不报错。
    #    这类错误不会被任何别的检查抓到 —— 产物照样有、退出码照样 0。
    known = {n.node_id for n in nodes}
    dangling = {n.node_id: [d for d in n.requires if d not in known]
                for n in nodes if any(d not in known for d in n.requires)}
    if dangling:
        raise ContractError(
            f"阶段图里有指向不存在节点的依赖（图不闭合）：{dangling}。"
            f"这通常意味着依赖解析把粒度搞错了（例如范围级节点去依赖某个具体章）。")
    # ── 可达性：每个节点都必须从某个"无依赖节点"可达（没有孤儿）
    roots = {n.node_id for n in nodes if not n.requires}
    if nodes and not roots:
        raise ContractError("阶段图里没有无依赖的起点节点 —— 表写错了")
    reachable = set(roots)
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if node.node_id in reachable:
                continue
            if all(d in reachable for d in node.requires):
                reachable.add(node.node_id)
                changed = True
    orphans = sorted(known - reachable)
    if orphans:
        raise ContractError(f"阶段图里有不可达节点（永远跑不到）：{orphans}")

    if plan.goal.value == "prepare":
        notes.append("goal=prepare：只做全书基础准备；结果标为阶段完成、未交付，"
                     "**不获得 release_ready**（方案 §4）")
    if until is not None:
        notes.append(f"--until {until}：开发用早停；结果同样**不获得 release_ready**（方案 §4）")

    graph = ExecutionGraph(nodes=tuple(nodes), goal=plan.goal.value, profile=plan.profile.value,
                           scope_digest=plan.digest(), truncated_at=until, notes=tuple(notes))
    graph.order()                    # 建图时就检查无环（坏表不要等到跑起来才发现）
    return graph


def _distinct_stage_order(graph: ExecutionGraph) -> list[str]:
    seen: list[str] = []
    for node_id in graph.order():
        name = graph.by_id()[node_id].stage
        if name not in seen:
            seen.append(name)
    return seen


def graphs_equivalent_for_scope(a: ExecutionGraph, b: ExecutionGraph) -> bool:
    """两张图在"阶段顺序"上是否一致（作用域可以不同）。

    用来证明 R02：单章 / 多章 / 整本**只改变 scope**，不改变执行顺序。

    ⚠️ 比的是**去重后的阶段顺序**，不是整个节点序列。第一版比了完整序列，于是
    "一章 vs 两章"必然不相等（`render` 出现两次而不是一次）—— 那是**作用域差异**，
    不是**顺序差异**。把两者混在一起，这条判据就永远为假，也就等于没有判据
    （本包的 `tests/contracts/test_m2_graph.py` 当场把它抓了出来）。

    去重后仍然是一条有意义的判据：谁把 `render` 挪到 `audit` 前面，序列立刻不同。
    """
    return _distinct_stage_order(a) == _distinct_stage_order(b)


def stage_relative_order(graph: ExecutionGraph) -> tuple[tuple[str, str, bool], ...]:
    """阶段两两之间的先后关系 —— 比"去重序列"更细的一层判据。

    跨作用域比较这个集合，能证明"没有哪一步因为作用域不同而换了位置"，
    而不只是"首次出现的顺序一样"。
    """
    first: dict[str, int] = {}
    for idx, node_id in enumerate(graph.order()):
        name = graph.by_id()[node_id].stage
        first.setdefault(name, idx)
    out = []
    for s1 in sorted(first):
        for s2 in sorted(first):
            if s1 != s2:
                out.append((s1, s2, first[s1] < first[s2]))
    return tuple(out)


__all__ = ["GRAIN_BOOK", "GRAIN_CHAPTER", "GRAIN_UNIT", "GRAIN_SCOPE", "GRAINS",
           "StageSpec", "CANONICAL_STAGES", "STAGE_BY_NAME", "stage", "Node",
           "PREPARATION_STAGES", "PARSE_STAGES", "MEDIA_TICKET_STAGES",
           "ExecutionGraph", "build_graph", "graphs_equivalent_for_scope",
           "stage_relative_order"]
