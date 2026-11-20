"""T04：**协调器真的在驱动返修**（runner 接线）的行为测试。

`test_repair_coordinator.py` 验的是协调器的纯逻辑；这里验的是**接线**：
阶段交了 `repair_requests` 之后，究竟会不会真的排批次、真的按作业再跑一次目标阶段、
真的把失效的下游重新入调度，以及**两次之后会不会停**。

改造前的现实是三个互不知情的轮次池（`audit` 内部两轮 + 它自己调 `figures_stage()`
+ 末尾整篇重生成）。所以这里每一条断言都对着"**轮次账目只有一份**"这件事。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from aiforstudy.blocks.audit.contracts import RepairDirective  # noqa: E402
from aiforstudy.contracts.enums import Goal, QualityState  # noqa: E402
from aiforstudy.contracts.stage import StageResult  # noqa: E402
from aiforstudy.pipeline.engine import build_engine, execute  # noqa: E402
from aiforstudy.pipeline.stubs import smoke_registry  # noqa: E402

from test_m2_engine import make_workspace, request_for  # noqa: E402

UNIT = "CH01-S01"
HASH = "sha256:" + "a" * 64


_OWNER_STAGE = {"lesson": "lesson", "visual_planner": "figures", "visual_renderer": "figures",
                "interaction": "interaction", "reviewer": "audit", "source_review": "audit"}


def _mk(kind="text_content", owner="lesson", tickets=("t1",), reason="返修") -> dict:
    """返修请求的**规格**（不是成品）：unit 与哈希必须由阶段按自己的 scope 填。

    第一版把 `unit_id="CH01-S01"` 写死，于是 4 个单元的 run 里第 2 个单元就炸了 ——
    协调器按单元隔离批次是对的，错的是测试夹具。
    """
    return {"kind": kind, "owner": owner, "tickets": tuple(tickets), "reason": reason}


def _build(spec: dict, unit: str) -> RepairDirective:
    stage = _OWNER_STAGE.get(spec["owner"], "lesson")
    return RepairDirective(unit_id=unit, ticket_ids=spec["tickets"], owner=spec["owner"],
                           reason=spec["reason"], kind=spec["kind"],
                           artifact_hashes={f"{stage}:{unit}": HASH})


def _routes(directives, origin_stage):
    """Synthetic stages still owe one explicit v4 route per submitted ticket."""
    from aiforstudy.pipeline.repair import RepairRouteV1
    kinds = {"lesson": "content", "visual_planner": "visual_spec",
             "visual_renderer": "media_resource", "interaction": "interaction_spec",
             "reviewer": "evidence", "source_review": "evidence"}
    return tuple(RepairRouteV1(ticket_id, origin_stage, kinds[d.owner],
                              next(iter(d.artifact_hashes))).as_dict()
                 for d in directives for ticket_id in d.ticket_ids)


def _make_emitter(specs_by_call, *, always_request=False):
    """造一个会产出返修请求的 lesson 阶段。

    两条约束都是实现指纹逼出来的，不是测试的怪癖：

    * 必须是**函数**：`runner._snapshot_inputs` 用 `inspect.getsourcefile(impl)` 取实现指纹，
      callable 实例会直接 TypeError；
    * 必须经由 `_emit_synthetic` 产出 `ArtifactRef`：手搓的 ref 缺字段会让阶段直接 failed。
      内容随调用次数变化，这样 `accept_result` 才能观察到"这一批真的改了内容"。
    """
    from aiforstudy.pipeline.stubs import _emit_synthetic
    state = {"calls": []}

    def stage(ctx):
        saw_batch = getattr(ctx, "repair_batch", None)
        unit = ctx.node.scope_key
        state["calls"].append({"node": ctx.node.node_id,
                               "batch": getattr(saw_batch, "batch_id", None)})
        ref = _emit_synthetic(ctx, ctx.node.stage,
                              {"unit": unit, "stage": ctx.node.stage,
                               "revision": len(state["calls"])})
        specs = ()
        if specs_by_call:
            index = min(len(state["calls"]) - 1, len(specs_by_call) - 1)
            specs = specs_by_call[index]
        elif always_request:
            specs = (_mk(),)
        directives = tuple(_build(spec, unit) for spec in specs)
        return StageResult.ok(ctx.node.stage, outputs=(ref,),
                              quality_state=(QualityState.NEEDS_REVIEW if directives
                                             else QualityState.ACCEPTED),
                              repair_requests=directives,
                              repair_routes=_routes(directives, ctx.node.stage))

    stage.calls = state["calls"]
    return stage


def _make_no_change_emitter():
    """永远交同一张工单、内容一个字节都不改 —— 用来验"没有进展就早停"。"""
    from aiforstudy.pipeline.stubs import _emit_synthetic

    def stage(ctx):
        saw = getattr(ctx, "repair_batch", None)
        stage.calls.append({"node": ctx.node.node_id,
                            "batch": getattr(saw, "batch_id", None)})
        ref = _emit_synthetic(ctx, ctx.node.stage,
                              {"unit": ctx.node.scope_key, "stage": ctx.node.stage})
        return StageResult.ok(ctx.node.stage, outputs=(ref,),
                              quality_state=QualityState.NEEDS_REVIEW,
                              repair_requests=(_build(_mk(), ctx.node.scope_key),),
                              repair_routes=_routes((_build(_mk(), ctx.node.scope_key),), ctx.node.stage))

    stage.calls = []
    return stage


def _engine_with(emitter, tmp_path, *, until=None):
    workspace, book = make_workspace(tmp_path)
    registry = smoke_registry()
    registry.register("lesson", emitter)
    request = request_for(book.book_id, goal=Goal.DELIVER, parallel=1)
    engine = build_engine(request, workspace, stage_registry=registry, until=until)
    return engine, workspace


def _events(store, kind):
    return [event for event in store.events_after() if event.kind == kind]


def test_repair_batch_is_planned_and_the_target_stage_is_rerun(tmp_path):
    """阶段交请求 → 协调器排批次 → **真的**再跑一次目标阶段，并带上批次。"""
    emitter = _make_emitter([[_mk()], []])          # 第 1 次交请求，返修那一次不再交
    engine, _ = _engine_with(emitter, tmp_path, until="lesson")
    result, store = execute(engine)

    mine = [c for c in emitter.calls if c["node"] == f"lesson:{UNIT}"]
    assert len(mine) == 2, f"返修作业应当再跑一次 {UNIT} 的 lesson，实际 {mine}"
    assert mine[0]["batch"] is None, "初次生成不是返修批次"
    assert mine[1]["batch"], "返修那次必须带上批次（阶段才知道为什么又跑一遍）"

    planned = [e for e in _events(store, "repair.batch.planned") if e.scope_key == UNIT]
    assert len(planned) == 1, [e.kind for e in store.events_after()]
    assert planned[0].data["round"] == 1 and planned[0].data["consumes_content_round"] is True

    finished = [e for e in _events(store, "repair.job.finished") if e.scope_key == UNIT]
    assert len(finished) == 1
    assert finished[0].data["effect"]["content_round_consumed"] is True
    assert finished[0].data["content_rounds_used"] == 1

    states = store.load_state()["repair_states"]
    assert states[UNIT]["content_rounds_used"] == 1
    assert states[UNIT]["active_batch_id"] is None, "批次收尾后必须清掉 active_batch_id"


def test_invalidated_downstream_is_requeued_not_reused(tmp_path):
    """内容改动后，失效的下游必须**重新排入调度**，不许拿旧产物接着跑。"""
    emitter = _make_emitter([[_mk()], []])
    # 跑整张图（不截断），否则 figures/delivery 这些节点根本不在图里，
    # "下游被标失效"就无从观察
    engine, _ = _engine_with(emitter, tmp_path)
    _, store = execute(engine)
    invalidated = _events(store, "repair.node.invalidated")
    assert invalidated, "内容改了却没有任何下游被标失效"
    stages = {event.stage for event in invalidated}
    assert {"figures", "audit", "interaction"} <= stages, stages
    assert "lesson" not in stages, (
        "刚承接内容返修的 lesson 不该被重复排队：作业那次运行**就是**新版本，"
        "再排一次等于同一份内容白跑两遍、还多花一次模型调用")


def _drive_repairs_from_audit(tmp_path, audit_specs, *, always_request=False):
    """lesson 每次内容都变（返修才算"真的改了"）；audit 负责反复上报问题。"""
    workspace, book = make_workspace(tmp_path)
    registry = smoke_registry()
    registry.register("lesson", _make_emitter([]))
    registry.register("audit", _make_emitter(audit_specs, always_request=always_request))
    request = request_for(book.book_id, goal=Goal.DELIVER, parallel=1)
    engine = build_engine(request, workspace, stage_registry=registry)
    _, store = execute(engine)
    return store


def test_content_rounds_cap_stops_further_batches(tmp_path):
    """**同一对象默认最多两轮内容返修**：第 3 个新问题须停止自动返修并记录。

    为什么把返修请求装在 `audit` 上：内容改完之后，只有**下游重新上报**才会产生
    下一批 —— 那正是"改过一遍，审核又发现新问题"的真实路径。
    每批换一个**真问题**（不是只换工单 ID）；用满上限后还要再报一次才会触发"到顶"。
    """
    cap = 2
    reasons = ("补充定义域", "修正推导等号", "解释边界取值")
    store = _drive_repairs_from_audit(
        tmp_path, [[_mk(tickets=(f"t{i}",), reason=r)] for i, r in enumerate(reasons, start=1)])
    state = store.load_state()["repair_states"][UNIT]
    assert state["content_rounds_used"] == cap, (
        f"内容返修批次必须到 {cap} 就停，实际用了 {state['content_rounds_used']}")
    assert state["stopped_reason"], "停止原因必须持久化进单元状态"
    stopped = [e for e in _events(store, "repair.stopped") if e.scope_key == UNIT]
    assert stopped, "到顶必须留下明确的停止事件"
    assert stopped[-1].data["stopped_by"] == "content_rounds_exhausted"
    assert stopped[-1].data["next_action"], "停下来必须给出下一步"
    assert stopped[-1].data["unresolved"], "必须列出还剩哪些没解决"


def test_repeated_candidate_stops_auto_repair_before_burning_the_second_batch(tmp_path):
    """**没有进展就早停**：同一个工单在同一个 artifact 版本上被反复报回来时，
    不该再花掉第二批 —— 那只是把同一份钱花第二遍。

    这一条比"到顶才停"更值钱：它省下的正是计划 §9 说的"相同 candidate_hash ＋
    未通过标准集合反复出现"的情形。
    """
    engine, _ = _engine_with(_make_no_change_emitter(), tmp_path, until="lesson")
    _, store = execute(engine)
    state = store.load_state()["repair_states"][UNIT]
    stopped = [e for e in _events(store, "repair.stopped") if e.scope_key == UNIT]
    assert stopped, "重复候选必须早停"
    assert stopped[-1].data["stopped_by"] == "no_progress", stopped[-1].data
    assert state["content_rounds_used"] == 1, (
        "重复候选应当在用掉第一批之后就停，不该继续烧第二批")
    assert state["stopped_reason"]


def test_evidence_repair_does_not_consume_content_rounds(tmp_path):
    """证据格式修复走另一个账：内容轮次不动，也**不重置**预算。"""
    evidence = _make_emitter([[_mk(kind="evidence_format", owner="reviewer", tickets=("v1",))],
                            []])
    workspace, book = make_workspace(tmp_path)
    registry = smoke_registry()
    registry.register("text_audit", evidence)
    engine = build_engine(request_for(book.book_id, units=(UNIT,), parallel=1), workspace,
                          stage_registry=registry, until="text_audit")
    _, store = execute(engine)
    states = store.load_state()["repair_states"][UNIT]
    assert states["content_rounds_used"] == 0, "证据修复不许消耗内容批次"
    planned = [e for e in _events(store, "repair.batch.planned") if e.scope_key == UNIT]
    assert planned and planned[0].data["consumes_content_round"] is False
    assert planned[0].data["round"] == 0


def test_repair_requests_without_a_target_node_are_reported_not_silently_dropped(tmp_path):
    """找不到承接作业的节点时必须留下事件 —— 静默丢弃等于悄悄少修一个问题。"""
    emitter = _make_emitter([[_mk(owner="interaction", tickets=("t9",))], []])
    engine, _ = _engine_with(emitter, tmp_path, until="lesson")
    _, store = execute(engine)
    skipped = [e for e in _events(store, "repair.job.skipped") if e.scope_key == UNIT]
    assert skipped, "没有承接节点时要显式报告，而不是当作修好了"
    assert "interaction" in skipped[0].message


def test_repair_state_survives_resume(tmp_path):
    """轮次账必须跨 resume 保留：换个进程不许重新获得两批（§4.6）。"""
    from aiforstudy.pipeline.engine import resume_request

    emitter = _make_emitter([[_mk()], []])
    workspace, book = make_workspace(tmp_path)
    registry = smoke_registry()
    registry.register("lesson", _make_emitter([[_mk()], []]))
    request = request_for(book.book_id, goal=Goal.DELIVER, parallel=1)
    _, store = execute(build_engine(request, workspace, stage_registry=registry,
                                    until="lesson"))
    before = store.load_state()["repair_states"][UNIT]["content_rounds_used"]

    again = _make_emitter([[_mk(tickets=("t9",))], []])
    registry2 = smoke_registry()
    registry2.register("lesson", again)
    resume_request(request, store.run_id, workspace, stage_registry=registry2,
                   until="lesson")
    after = store.load_state()["repair_states"][UNIT]["content_rounds_used"]
    assert after == before, f"resume 之后内容轮次被重置了：{before} → {after}"


def test_two_visual_jobs_execute_figures_once_with_both_targets(tmp_path):
    from aiforstudy.pipeline.stubs import _emit_synthetic
    workspace, book = make_workspace(tmp_path)
    seen = []
    def figures(ctx):
        batch = ctx.repair_batch
        seen.append(tuple(j.artifact_id for j in batch.jobs) if batch else ())
        ref = _emit_synthetic(ctx, 'figures', {'revision':len(seen)})
        directives = tuple(RepairDirective(unit_id=UNIT, ticket_ids=(rid,), owner='visual_planner',
            reason='布局需修正', kind='visual_layout', artifact_hashes={f'visual:{rid}':HASH})
            for rid in ('v1','v2')) if batch is None else ()
        return StageResult.ok('figures', outputs=(ref,), quality_state=QualityState.NEEDS_REVIEW if directives else QualityState.ACCEPTED, repair_requests=directives,
                              repair_routes=_routes(directives, ctx.node.stage))
    registry = smoke_registry()
    registry.register('figures', figures)
    engine = build_engine(request_for(book.book_id, units=(UNIT,), parallel=1), workspace,
                          stage_registry=registry, until='figures')
    result, store = execute(engine)
    assert seen == [(), ('visual:v1','visual:v2')]
    assert store.load_state()['repair_states'][UNIT]['content_rounds_used'] == 1
