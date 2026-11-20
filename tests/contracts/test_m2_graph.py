"""M2 阶段图回归：**单 / 多 / 整本同一张图**。

方案 §14 的 M2 通过条件第一条就是"单/多/整本同图"；R02 说得更直接：

> 单章、多章、整本只改变 scope，**不维护三套执行顺序**。

为什么值得一批专门的测试：三套执行顺序的现实后果不是"重复代码"，而是它们会**漂移**
（单章脚本加了一步修复、整本脚本没加），于是"单章过了、整本没过"且无从察觉。
本项目正是这样长出 `run_chapter.py` 与 `run_book.py` 两套入口的。

除此之外这里还守两个"错了也不报错"的形态：

* **依赖指向不存在的节点** —— 被当成"已满足"，节点提前执行。实测抓到过一次
  （`render:scope`），产物照样有、退出码照样 0；
* **`--until` 早停不能被当成交付**（§4：结果必须标为阶段完成、未交付）。
"""
from __future__ import annotations

import pytest

from aiforstudy.contracts.enums import Goal, Profile, SelectorKind
from aiforstudy.contracts.request import RunRequest, Selectors
from aiforstudy.contracts.scope import ScopePlan
from aiforstudy.errors import ContractError
from aiforstudy.pipeline.graph import (CANONICAL_STAGES, GRAPH_VERSION, GRAIN_BOOK,
                                       GRAIN_CHAPTER, GRAIN_SCOPE, GRAIN_UNIT, build_graph,
                                       dispatch_quality_gate, graphs_equivalent_for_scope,
                                       stage)


def make_plan(units, *, goal=Goal.DELIVER, profile=Profile.RELEASE, kind=SelectorKind.ALL):
    return ScopePlan(request_digest="sha256:" + "0" * 64, book_id="b", goal=goal,
                     profile=profile,
                     selectors=Selectors(kind=kind, values=() if kind is SelectorKind.ALL
                                         else tuple(units)),
                     resolved_scope=tuple(units), requires_preparation=False,
                     chapter_order=("CH01", "CH02"))


UNITS = ("CH01-S01", "CH01-S02", "CH02-S01", "CH02-S02")


# ────────────────────────────────────────── 同图

@pytest.mark.parametrize("scope", [
    ("CH01-S01",),
    ("CH01-S01", "CH02-S01"),
    UNITS,
])
def test_stage_order_is_the_same_for_every_scope(scope):
    one = build_graph(make_plan(("CH01-S01",), kind=SelectorKind.CHAPTERS))
    other = build_graph(make_plan(scope, kind=SelectorKind.CHAPTERS))
    assert graphs_equivalent_for_scope(one, other), (
        "单章/多章/整本的**阶段顺序**必须一致（R02：只改变 scope）")


def test_scope_only_changes_the_number_of_instances():
    small = build_graph(make_plan(("CH01-S01",), kind=SelectorKind.CHAPTERS))
    big = build_graph(make_plan(UNITS))
    small_stages = {n.stage for n in small.nodes}
    big_stages = {n.stage for n in big.nodes}
    assert small_stages <= big_stages
    assert len(big.nodes) > len(small.nodes)
    # 书级阶段各自只有一份，不随作用域翻倍
    for spec in CANONICAL_STAGES:
        if spec.grain == GRAIN_BOOK:
            assert sum(1 for n in big.nodes if n.stage == spec.name) == 1


def test_graph_is_deterministic():
    a = build_graph(make_plan(UNITS))
    b = build_graph(make_plan(UNITS))
    assert a.order() == b.order()
    assert a.digest() == b.digest()


# ────────────────────────────────────────── 依赖闭合（错了不报错的那一类）

def test_every_dependency_points_to_a_real_node():
    """**图闭合**：依赖不许指向不存在的节点。

    第一版 `delivery`（范围级）依赖 `render`（章级）时解析出了 `render:scope`，
    它在图里不存在；`ready()` 把不存在的依赖当成"已满足"，于是 **delivery 在 mineru
    之前就跑了**。产物照样生成、退出码照样 0。
    """
    graph = build_graph(make_plan(UNITS))
    known = {n.node_id for n in graph.nodes}
    for node in graph.nodes:
        for dep in node.requires:
            assert dep in known, f"{node.node_id} 依赖了不存在的 {dep}"


def test_scope_level_node_waits_for_all_chapter_nodes():
    """范围级节点必须等**所有**章级节点 —— 这是上面那个 bug 的直接回归。"""
    graph = build_graph(make_plan(UNITS))
    delivery = graph.by_id()["delivery"]
    renders = {n.node_id for n in graph.nodes if n.stage == "render"}
    assert renders and set(delivery.requires) == renders
    assert graph.order().index("delivery") > max(graph.order().index(r) for r in renders)


def test_chapter_node_waits_for_all_its_units():
    graph = build_graph(make_plan(UNITS))
    render = graph.by_id()["render:CH01"]
    assert set(render.requires) == {"source_format"} | {
        f"{producer}:{unit}"
        for producer in ("text_audit", "audit", "interaction")
        for unit in ("CH01-S01", "CH01-S02")
    }
    assert all(graph.order().index(dep) < graph.order().index(render.node_id)
               for dep in render.requires)


def test_topological_order_starts_with_the_book_stages():
    graph = build_graph(make_plan(UNITS))
    assert graph.order()[:6] == ("mineru", "toc", "structure", "export", "body", "concepts")
    assert graph.order()[-1] == "delivery"


def test_no_unreachable_nodes():
    """每个节点都必须能从某个起点走到 —— 跑不到的节点是"看起来有计划"的假象。"""
    graph = build_graph(make_plan(UNITS))
    reachable = {n.node_id for n in graph.nodes if not n.requires}
    changed = True
    while changed:
        changed = False
        for node in graph.nodes:
            if node.node_id not in reachable and all(d in reachable for d in node.requires):
                reachable.add(node.node_id)
                changed = True
    assert reachable == {n.node_id for n in graph.nodes}


# ────────────────────────────────────────── 粒度与档位

def test_grain_of_each_canonical_stage():
    grains = {s.name: s.grain for s in CANONICAL_STAGES}
    assert grains["concepts"] == GRAIN_BOOK
    assert grains["lesson"] == GRAIN_UNIT
    assert grains["render"] == GRAIN_CHAPTER
    assert grains["delivery"] == GRAIN_SCOPE
    with pytest.raises(ContractError):
        stage("不存在的阶段")


def test_prepare_goal_only_builds_the_book_stages():
    """§4：`aifs prepare` 的目标天然是全书基础准备。"""
    graph = build_graph(make_plan(UNITS, goal=Goal.PREPARE))
    assert {n.stage for n in graph.nodes} == {"mineru", "toc", "structure", "export",
                                              "body", "concepts"}
    assert graph.nodes[-1].stage == "concepts"


def test_until_truncates_and_is_marked():
    """§4：`--until STAGE` 结果必须标为阶段完成、未交付。"""
    graph = build_graph(make_plan(UNITS), until="lesson")
    assert graph.truncated_at == "lesson"
    assert max(graph.order().index(n) for n in graph.order()) < len(graph.order())
    assert {n.stage for n in graph.nodes} == {"mineru", "toc", "structure", "export", "body",
                                              "concepts", "source_format", "lesson"}
    assert any("未交付" in note or "release_ready" in note for note in graph.notes)
    with pytest.raises(ContractError, match="不是一个阶段"):
        build_graph(make_plan(UNITS), until="没有这个阶段")


def test_cold_start_plan_cannot_be_graphed():
    """§6：run 要先校验并固定 scope，合法才继续 —— 冷启动计划不能建图。"""
    cold = ScopePlan(request_digest="sha256:" + "0" * 64, book_id="b", goal=Goal.DELIVER,
                     profile=Profile.RELEASE, selectors=Selectors(kind=SelectorKind.ALL),
                     resolved_scope=None, requires_preparation=True)
    with pytest.raises(ContractError, match="resolved_scope=None"):
        build_graph(cold)


def test_not_applicable_is_declared_per_stage():
    """§8：图与交互可以有"没有适用对象"的合法终态；**其它阶段不行**。"""
    allowed = {s.name for s in CANONICAL_STAGES if s.allow_not_applicable}
    assert allowed == {"figures", "interaction"}
    for spec in CANONICAL_STAGES:
        if spec.name in allowed:
            continue
        assert not spec.allow_not_applicable, f"{spec.name} 不该允许 not_applicable 当完成"


def test_unit_scope_request_and_chapter_scope_request_give_the_same_nodes_for_one_chapter():
    """`--units CH01-S01,CH01-S02` 与 `--chapters CH01` 在图**结构**上等价。

    这证明了 R01/R02 的另一半：CLI 与界面用不同的选择器表达同一个范围时，
    解析后的阶段、顺序、依赖一致。
    """
    by_units = build_graph(make_plan(("CH01-S01", "CH01-S02"), kind=SelectorKind.UNITS))
    by_chapter = build_graph(make_plan(("CH01-S01", "CH01-S02"), kind=SelectorKind.CHAPTERS))
    assert by_units.order() == by_chapter.order()
    assert by_units.digest() == by_chapter.digest()


# ────────────────────────────────────────── T04：边级质量门策略与图版本

def test_unknown_review_is_not_blocked_by_an_accepted_only_graph_edge():
    """执行依赖保留，审核态由消费方核对，未核实内容可进入预览。"""
    for consumer, producer in (
        ("text_audit", "lesson"), ("media_plan", "text_audit"),
        ("figures", "media_plan"), ("audit", "figures"),
        ("interaction", "audit"), ("render", "text_audit"),
        ("render", "audit"), ("render", "interaction"), ("delivery", "render"),
    ):
        assert dispatch_quality_gate(consumer, producer) is None
    graph = build_graph(make_plan(("CH01-S01",)))
    assert "text_audit:CH01-S01" in graph.by_id()["media_plan:CH01-S01"].requires


def test_graph_version_is_recorded_for_new_graphs():
    """§6.3：缺 graph_version 视作 legacy；新图记录独立资料格式整理版本。"""
    graph = build_graph(make_plan(("CH01-S01",)))
    assert graph.graph_version == GRAPH_VERSION == "8.0"
    assert graph.as_dict()["graph_version"] == "8.0"


def test_prepare_and_until_do_not_publish():
    """prepare／--until 一律不能伪装正式发布（§4.1）。"""
    prepare = build_graph(make_plan(UNITS, goal=Goal.PREPARE))
    assert "text_audit" not in {n.stage for n in prepare.nodes}
    assert not prepare.as_dict().get("release_ready")
    cut = build_graph(make_plan(UNITS), until="lesson")
    assert cut.truncated_at == "lesson"
    assert "figures" not in {n.stage for n in cut.nodes}
    assert any("未交付" in note or "release_ready" in note for note in cut.notes)
