"""T04：唯一返修协调器的行为测试。

计划 §4.2 把返修收成**一个**入口，并且把三件事写成了硬约束：

1. **每个对象默认最多两轮内容返修**，冻结旧六轮策略仍可恢复；同对象跨阶段、跨 resume 共享；不同对象独立；
2. **证据格式修复不消耗内容批次**，但每个判词最多一次，且**重复内容继承次数**；
3. 同单元的内容修改**串行**，同一 artifact 只派给**唯一** owner，绝不两个 job 抢同一版本。

改造前这三条都不成立：`transitional.audit_stage` 有自己的两轮循环、`audit` 内部还会
自己调 `figures_stage()` 重新出图、图后端再带一个收费 retry loop —— 三个轮次池互不知情
（调研 §1.2 最后一行）。所以这里测的不是"函数返回值对不对"，而是**轮次账目只有一份**。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from aiforstudy.blocks.audit.contracts import (  # noqa: E402
    CriterionVerdict,
    EvidenceSpan,
    PatchOutcome,
    Rejection,
    RepairDirective,
)
from aiforstudy.errors import ContractError  # noqa: E402
from aiforstudy.pipeline.execution_control import ExecutionPolicy  # noqa: E402
from aiforstudy.pipeline.repair import (  # noqa: E402
    INVALIDATION_BY_KIND,
    RepairBatch,
    RepairCoordinator,
    RepairJob,
    StopDecision,
    UnitRepairState,
)

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def _directive(*, kind="text_content", owner="lesson", tickets=("t1",),
               artifact="lesson:CH04-S01", unit="CH04-S01", hash_=HASH_A,
               reason="返修") -> RepairDirective:
    return RepairDirective(unit_id=unit, ticket_ids=tuple(tickets), owner=owner,
                           reason=reason, kind=kind,
                           artifact_hashes={artifact: hash_})


def _coordinator(**kw) -> RepairCoordinator:
    policy = kw.pop("policy", None) or ExecutionPolicy()
    return RepairCoordinator(policy, **kw)


# --------------------------------------------------------------------------- #
# 两批内容返修，跨阶段共享
# --------------------------------------------------------------------------- #
def test_round_limit_follows_the_object_across_stages_without_blocking_others():
    coordinator = _coordinator()
    state = UnitRepairState(unit_id="CH04-S01")

    # 第 1 批：正文审核发现问题
    first = coordinator.plan([_directive(kind="text_content", owner="lesson")], state)
    assert isinstance(first, RepairBatch)
    assert first.consumes_content_round and first.round == 1
    state.content_rounds_used += 1
    state.content_rounds_by_artifact['lesson:CH04-S01'] = 1

    # 另一张图有独立轮次，正文的返修不应剥夺它的机会；一直走到上限。
    for expected_round in range(1, coordinator.max_content_rounds + 1):
        batch = coordinator.plan([_directive(kind="visual_math", owner="visual_planner",
                                            artifact="figure:IMG-1", hash_=HASH_B,
                                            tickets=("t%d" % expected_round,))], state)
        assert isinstance(batch, RepairBatch) and batch.round == expected_round
        state.content_rounds_by_artifact['figure:IMG-1'] = expected_round

    # 同一张图从数学到布局审核仍共用同一份轮次，不能按阶段重置：到上限即停。
    stopped = coordinator.plan([_directive(kind='visual_math', owner='visual_planner',
        artifact='figure:IMG-1', hash_=HASH_B, tickets=('tN',))], state)
    assert isinstance(stopped, StopDecision)
    assert stopped.stopped_by == 'content_rounds_exhausted'
    assert str(coordinator.max_content_rounds) in stopped.reason and 'tN' in stopped.unresolved
    assert stopped.next_action
    independent = coordinator.plan([_directive(kind='visual_layout', owner='visual_renderer',
        artifact='figure:IMG-2', tickets=('t5',))], state)
    assert isinstance(independent, RepairBatch) and independent.round == 1
def test_content_rounds_survive_resume_through_state_roundtrip():
    coordinator = _coordinator()
    state = UnitRepairState(unit_id="CH04-S01", content_rounds_used=6,
                           content_rounds_by_artifact={'lesson:CH04-S01': 6})
    restored = UnitRepairState.from_dict(json.loads(json.dumps(state.as_dict())))
    assert restored.content_rounds_used == 6
    assert restored.content_rounds_by_artifact == {'lesson:CH04-S01': 6}
    decision = coordinator.plan([_directive()], restored)
    assert isinstance(decision, StopDecision) and decision.stopped_by == "content_rounds_exhausted"


def test_policy_limit_comes_from_the_frozen_policy_not_a_magic_number():
    policy = ExecutionPolicy(content_repair_rounds_per_object=6)
    coordinator = _coordinator(policy=policy)
    state = UnitRepairState(unit_id="U", content_rounds_used=6,
                           content_rounds_by_artifact={'lesson:U': 6})
    decision = coordinator.plan([_directive(unit="U", artifact='lesson:U')], state)
    assert isinstance(decision, StopDecision)
    assert coordinator.max_content_rounds == policy.content_repair_rounds_per_object == 6
    assert coordinator.policy_digest == policy.digest()


# --------------------------------------------------------------------------- #
# 证据修复：不消耗内容批次、不重置预算、重复内容继承次数
# --------------------------------------------------------------------------- #
def test_evidence_repair_does_not_consume_content_rounds_nor_reset_budget():
    coordinator = _coordinator()
    state = UnitRepairState(unit_id="CH04-S01", content_rounds_used=2,
                           content_rounds_by_artifact={'lesson:CH04-S01': 2})
    batch = coordinator.plan([_directive(kind="evidence_format", owner="reviewer",
                                        tickets=("v1",))], state)
    assert isinstance(batch, RepairBatch), "内容批次用尽不该挡住证据修复"
    assert batch.consumes_content_round is False and batch.round == 0
    assert state.content_rounds_used == 2, "证据修复**不许**改内容轮次"
    assert batch.policy_digest == coordinator.policy_digest

    verdict = CriterionVerdict(
        ticket_id="t1", criterion_id="C-1", criteria_version=1, artifact_hash=HASH_A,
        status="fail", why="引用定位不到", spans=())
    first = coordinator.accept_evidence(verdict, state)
    assert first["attempts_used"] == 1 and first["remaining"] == 0
    assert state.content_rounds_used == 2
    # 每个判词只有一次：同一内容再来就被拒
    decision = coordinator.plan([_directive(kind="evidence_format", owner="reviewer",
                                            tickets=(verdict.verdict_id,))], state)
    assert isinstance(decision, RepairBatch) or isinstance(decision, StopDecision)
    state.evidence_attempts_by_verdict[verdict.verdict_id] = 1
    decision = coordinator.plan([_directive(kind="evidence_format", owner="reviewer",
                                            tickets=(verdict.verdict_id,))], state)
    assert isinstance(decision, StopDecision)
    assert decision.stopped_by == "evidence_attempts_exhausted"


def test_repeat_evidence_content_inherits_attempts_through_verdict_identity():
    """换 `verdict_id` 不能重获一次证据修复机会（§4.7）。"""
    coordinator = _coordinator()
    state = UnitRepairState(unit_id="U")
    base = dict(ticket_id="t1", criterion_id="C-1", criteria_version=1,
                artifact_hash=HASH_A, status="fail", why="引用定位不到")
    first = CriterionVerdict(**base)
    renamed = CriterionVerdict(**base)
    assert first.verdict_id == renamed.verdict_id, (
        "判词身份由内容派生：重复内容必须得到同一个 ID")
    coordinator.accept_evidence(first, state)
    decision = coordinator.plan([_directive(kind="evidence_format", owner="reviewer",
                                            tickets=(renamed.verdict_id,), unit="U")], state)
    assert isinstance(decision, StopDecision) and decision.stopped_by == "evidence_attempts_exhausted"


def test_evidence_attempt_limit_is_one_by_default():
    coordinator = _coordinator()
    assert coordinator.max_evidence_per_verdict == 1


# --------------------------------------------------------------------------- #
# 串行与合并
# --------------------------------------------------------------------------- #
def test_same_unit_cannot_start_a_second_batch_while_one_is_open():
    coordinator = _coordinator()
    state = UnitRepairState(unit_id="CH04-S01", active_batch_id="batch-1")
    decision = coordinator.plan([_directive()], state)
    assert isinstance(decision, StopDecision)
    assert decision.stopped_by == "batch_in_flight"
    assert "accept_result" in decision.next_action


def test_same_artifact_merges_into_one_job_with_one_owner():
    """同一 artifact 的多个问题合并给**唯一** owner，不产生两个抢同一版本的 job。"""
    coordinator = _coordinator()
    state = UnitRepairState(unit_id="CH04-S01")
    batch = coordinator.plan([
        _directive(tickets=("t1", "t2"), owner="lesson", artifact="lesson:CH04-S01"),
        _directive(tickets=("t3",), owner="lesson", artifact="lesson:CH04-S01",
                   reason="同一个 artifact 的另一处问题"),
    ], state)
    assert isinstance(batch, RepairBatch)
    assert len(batch.jobs) == 1
    assert batch.jobs[0].ticket_ids == ("t1", "t2", "t3")
    assert batch.jobs[0].owner == "lesson"


def test_batch_refuses_two_jobs_on_the_same_artifact():
    job_a = RepairJob(job_id="a", owner="lesson", artifact_id="lesson:X",
                      ticket_ids=("t1",), unit_id="X")
    job_b = RepairJob(job_id="b", owner="visual_planner", artifact_id="lesson:X",
                      ticket_ids=("t2",), unit_id="X")
    with pytest.raises(ContractError):
        RepairBatch(batch_id="b", unit_id="X", ticket_ids=("t1", "t2"),
                    jobs=(job_a, job_b), base_hashes={}, policy_digest="d")


def test_different_owners_on_different_artifacts_get_separate_jobs():
    coordinator = _coordinator()
    state = UnitRepairState(unit_id="CH04-S01")
    batch = coordinator.plan([
        _directive(tickets=("t1",), owner="lesson", artifact="lesson:CH04-S01"),
        _directive(tickets=("t2",), owner="visual_planner", artifact="figure:IMG-1",
                   kind="visual_math", hash_=HASH_B),
    ], state)
    assert isinstance(batch, RepairBatch)
    assert {job.owner for job in batch.jobs} == {"lesson", "visual_planner"}
    assert len(batch.jobs) == 2


def test_removed_source_conflict_route_is_rejected():
    with pytest.raises(ContractError):
        _directive(kind="source_conflict", owner="source_review", tickets=("s1",))


# --------------------------------------------------------------------------- #
# 早停：没有进展就不要再花额度
# --------------------------------------------------------------------------- #
def test_repeat_candidate_stops_early_instead_of_spending_more():
    coordinator = _coordinator()
    state = UnitRepairState(unit_id="CH04-S01")
    directives = [_directive(tickets=("t1",))]
    first = coordinator.plan(directives, state)
    assert isinstance(first, RepairBatch)

    state.last_candidate_signature = coordinator.signature(directives, state.artifact_heads)
    again = coordinator.plan(directives, state)
    assert isinstance(again, StopDecision)
    assert again.stopped_by == "no_progress"
    assert "继续其他对象" in again.next_action and "人工" not in again.next_action


def test_signature_ignores_batch_id_and_time_but_not_content():
    coordinator = _coordinator()
    same = [_directive(tickets=("t1",))]
    assert coordinator.signature(same, {}) == coordinator.signature(same, {})
    changed = [_directive(tickets=("t2",), reason="新的积分上下限问题")]
    assert coordinator.signature(changed, {}) != coordinator.signature(same, {})
    moved = coordinator.signature(same, {"lesson:CH04-S01": HASH_B})
    assert moved == coordinator.signature(same, {"lesson:CH04-S01": HASH_A})


def test_no_directives_is_not_a_repair():
    coordinator = _coordinator()
    decision = coordinator.plan([], UnitRepairState(unit_id="U"))
    assert isinstance(decision, StopDecision)
    assert decision.stopped_by == "no_directives"


# --------------------------------------------------------------------------- #
# 结算与失效
# --------------------------------------------------------------------------- #
def test_rejected_patch_does_not_consume_a_content_round():
    """补丁被拒（例如 hash 过期）不是一次内容修改，不该吃掉一批轮次。"""
    coordinator = _coordinator()
    job = RepairJob(job_id="j", owner="lesson", artifact_id="lesson:X", ticket_ids=("t1",),
                    unit_id="X", kind="text_content")
    outcome = PatchOutcome(applied=False, reason="base_hash 过期",
                           rejected=(Rejection(code="stale-hash", message="hash 不匹配",
                                               next_step="按当前 revision 重新生成补丁"),))
    effect = coordinator.accept_result(job, outcome)
    assert effect["content_round_consumed"] is False
    assert effect["invalidated"] == []
    assert effect["rejected"][0]["code"] == "stale-hash"


def test_applied_content_patch_consumes_a_round_and_lists_invalidations():
    coordinator = _coordinator()
    job = RepairJob(job_id="j", owner="lesson", artifact_id="lesson:X", ticket_ids=("t1",),
                    unit_id="X", kind="text_content")
    effect = coordinator.accept_result(job, PatchOutcome(applied=True),
                                       revision_hash=HASH_B)
    assert effect["content_round_consumed"] is True
    assert set(effect["invalidated"]) == set(INVALIDATION_BY_KIND["text_content"])
    assert "lesson" in effect["invalidated"] and "delivery" in effect["invalidated"]


def test_layout_only_patch_keeps_source_and_math_evidence():
    """§6.2 失效表：只动布局／镜头时，来源与数学规格不该重算。"""
    coordinator = _coordinator()
    job = RepairJob(job_id="j", owner="visual_renderer", artifact_id="figure:IMG-1",
                    ticket_ids=("t1",), unit_id="X", kind="visual_layout")
    effect = coordinator.accept_result(job, PatchOutcome(applied=True))
    assert "lesson" not in effect["invalidated"]
    assert "text_audit" not in effect["invalidated"]
    assert "figures" in effect["invalidated"]


def test_accept_result_refuses_to_fake_content_outcomes_for_evidence():
    coordinator = _coordinator()
    job = RepairJob(job_id="j", owner="reviewer", artifact_id="audit:U", ticket_ids=("v1",),
                    unit_id="U", kind="evidence_format")
    with pytest.raises(ContractError):
        coordinator.accept_result(job, {"applied": True})       # 不是 PatchOutcome


def test_directive_must_declare_its_kind():
    """类别决定算不算消耗内容批次，**不许靠猜**。"""
    coordinator = _coordinator()
    bare = RepairDirective(unit_id="U", ticket_ids=("t1",), owner="lesson",
                           reason="返修", artifact_hashes={"lesson:U": HASH_A})
    with pytest.raises(ContractError) as excinfo:
        coordinator.plan([bare], UnitRepairState(unit_id="U"))
    assert "kind" in str(excinfo.value)


def test_directive_rejects_unknown_kind_and_mixed_units():
    with pytest.raises(ContractError):
        RepairDirective(unit_id="U", ticket_ids=("t1",), owner="lesson", reason="r",
                        kind="乱写的类别")
    coordinator = _coordinator()
    with pytest.raises(ContractError):
        coordinator.plan([_directive(unit="A"), _directive(unit="B")],
                         UnitRepairState(unit_id="A"))


def test_batch_and_state_are_json_serializable():
    coordinator = _coordinator()
    state = UnitRepairState(unit_id="CH04-S01")
    batch = coordinator.plan([_directive()], state)
    payload = json.loads(json.dumps({"batch": batch.as_dict(),
                                     "state": state.as_dict()}))
    assert payload["batch"]["jobs"][0]["owner"] == "lesson"
    assert payload["state"]["content_rounds_used"] == 0
    restored = RepairBatch.from_dict(payload["batch"])
    assert restored == batch, "JSON 往返必须得到相等的批次（含 tuple 化的字段）"
    assert UnitRepairState.from_dict(payload["state"]) == state
    decision = StopDecision.from_dict(
        StopDecision(reason="到顶", unresolved=("t1",), next_action="保留未决，继续其他对象").as_dict())
    assert decision.unresolved == ("t1",)


def test_same_artifact_mixed_evidence_precedes_content_without_collision():
    c = _coordinator()
    state = UnitRepairState(unit_id='CH04-S01')
    content = _directive(tickets=('ready',))
    evidence = _directive(owner='reviewer', kind='evidence_format', tickets=('incomplete',))
    batch = c.plan([content, evidence], state)
    assert isinstance(batch, RepairBatch)
    assert len(batch.jobs) == 1 and batch.jobs[0].kind == 'evidence_format'
    assert not batch.consumes_content_round
    # Carry both diagnoses to the reviewer while dispatching only one writer.
    assert set(batch.ticket_ids) == {'ready', 'incomplete'}


def test_exhausted_evidence_never_dispatched_with_other_artifact_content():
    c = _coordinator()
    state = UnitRepairState(unit_id='CH04-S01', evidence_attempts_by_verdict={'used':1})
    for restored in (state, UnitRepairState.from_dict(state.as_dict())):
        batch = c.plan([_directive(tickets=('ready',)),
            _directive(owner='reviewer', kind='evidence_format', tickets=('used',), artifact='visual:v')], restored)
        assert isinstance(batch, RepairBatch)
        assert all('used' not in j.ticket_ids for j in batch.jobs)
        assert 'used' in batch.ticket_ids  # unresolved evidence remains inspectable
