"""T02：**原子补丁**与**关单核验**的行为测试（§5.2）。

两条主线：

1. **补丁必须原子**（旧路径允许"一条落地、一条静默拒绝"，见 `T02-red-behavior-probe.log`）：
   过期 `base_hash`、越权路径、区间重叠、结构破坏 → 全部拒绝，且 `artifact` 一个字节都不动。
2. **关单必须有独立证据**：缺 criterion、旧 `criteria_version`、生成器自报"已修好" → 都不能关闭。

计划依据：主计划 §5.2；验收 A04。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from aiforstudy.blocks.audit.contracts import (  # noqa: E402
    ArtifactRevision,
    Criterion,
    CriterionVerdict,
    EvidenceSpan,
    PatchOperation,
    PatchSet,
    RepairTicket,
    SCHEMA_VERSION,
    make_ticket_id,
    validate_ticket,
)
from aiforstudy.blocks.audit.feedback import evaluate_closure  # noqa: E402
from aiforstudy.blocks.audit.patches import apply_patchset  # noqa: E402

UNIT = "CH04-S01"
ARTIFACT_ID = f"lesson:{UNIT}"
POINTER = "/body_markdown"
BODY = ("例 2　已知 $v=2x^{2}-2y^{2}+x$，求以它为虚部的解析函数 $f(z)$。\n\n"
        "先验证 $v$ 是调和函数。[[NOTE:N-03]]\n\n"
        "接下来就用 (4.5) 把 $u$ 积出来。\n")
HASH = "sha256:" + hashlib.sha256(BODY.encode("utf-8")).hexdigest()
REVISION = ArtifactRevision(artifact_id=ARTIFACT_ID, unit_id=UNIT, revision=3, hash=HASH)
PRESERVE = "先验证 $v$ 是调和函数。"
ANCHOR = "接下来就用 (4.5)"


def ticket_data(**overrides) -> dict:
    data = {
        "schema_version": SCHEMA_VERSION,
        "ticket_id": make_ticket_id(rule="LOGIC_GAP", unit_id=UNIT,
                                    artifact_id=ARTIFACT_ID, location=f"p:{POINTER}"),
        "unit_id": UNIT,
        "kind": "text_content",
        "severity": "P0",
        "owner": "lesson",
        "status": "ready",
        "hard_acceptance": True,
        "target": {"artifact_id": ARTIFACT_ID, "rule": "LOGIC_GAP",
                   "json_pointer": POINTER, "artifact_hash": HASH},
        "problem": "例 2 只写到「验证调和」就止住，读者看不到 u 怎么积出来。",
        "learner_obstacle": "没学过线积分的读者无法核对 (4.5) 的用法。",
        "source_evidence": [
            {"artifact_id": "parse:CH04-S01", "artifact_hash": "sha256:" + "b" * 64,
             "json_pointer": "/body/§4.5", "start": 0, "end": 4, "quote": "调和函数"}],
        "repair_instructions": {
            "locate": f"{POINTER} 末段",
            "action": "在「接下来就用 (4.5) 把 u 积出来」之后补出折线路线与 C=1 的代入步骤。",
            "rationale": "教材 §4.5 给出由 v 反求 u 的线积分公式。",
            "expected_change": "该段出现折线路线、u=-4xy-y+C、f(z)=2i z²+i z+1。"},
        "allowed_paths": [POINTER],
        "preserve": [PRESERVE],
        "criteria": [{"criterion_id": "C-LOGIC-1", "version": 1, "required": True,
                      "expected": "折线路线、u=-4xy-y+C 与 C=1 全部逐字出现在该段内",
                      "method": "reviewer", "evidence_type": "text"}],
        "previous_attempt": None,
        "history": [],
        "usage_refs": [],
    }
    data.update(overrides)
    return data


def ticket() -> RepairTicket:
    return validate_ticket(ticket_data())


def artifact(**overrides) -> dict:
    out = {"artifact_id": ARTIFACT_ID, "hash": HASH, "body_markdown": BODY,
           "ticket": ticket().as_dict(), "required_placeholders": ["[[NOTE:N-03]]"]}
    out.update(overrides)
    return out


def patchset(operations: tuple[PatchOperation, ...], *, base_hash: str = HASH,
             ticket_ids: tuple[str, ...] | None = None,
             criterion_ids: tuple[str, ...] = ("C-LOGIC-1",)) -> PatchSet:
    return PatchSet(ticket_ids=ticket_ids if ticket_ids is not None else (ticket().ticket_id,),
                    base_hash=base_hash, criterion_ids=criterion_ids,
                    explanation="补出例 2 缺失的积分过程", operations=operations)


def replace(start: int, end: int, text: str, *, path: str = POINTER) -> PatchOperation:
    return PatchOperation(op="replace", path=path, start=start, end=end, text=text)


def codes(outcome) -> list[str]:
    return [item.code for item in outcome.rejected]


def assert_rejected_atomically(outcome, box: dict, code: str) -> None:
    """每个拒绝都必须：不落地、给出可执行下一步、且**没动** artifact。"""
    assert outcome.applied is False
    assert outcome.result_revision is None
    assert code in codes(outcome), (code, codes(outcome))
    rejection = next(item for item in outcome.rejected if item.code == code)
    assert rejection.message.strip() and rejection.next_step.strip()
    assert "请重试" not in rejection.next_step
    assert len(rejection.next_step) >= 10
    assert box["body_markdown"] == BODY
    assert box["hash"] == HASH
    assert outcome.operation_results, "拒绝也必须逐条给出 operation_results"


# --------------------------------------------------------------------------- #
# 原子拒绝
# --------------------------------------------------------------------------- #
def test_stale_base_hash_is_rejected_with_an_actionable_next_step():
    box = artifact()
    outcome = apply_patchset(patchset((replace(0, 3, "例 3"),),
                                      base_hash="sha256:" + "0" * 64), REVISION, box)
    assert_rejected_atomically(outcome, box, "BASE_HASH_MISMATCH")
    next_step = next(item.next_step for item in outcome.rejected
                     if item.code == "BASE_HASH_MISMATCH")
    assert str(REVISION.revision) in next_step and "重新" in next_step


def test_path_escape_and_paths_outside_the_whitelist_are_rejected():
    box = artifact()
    for path, code in (("/../etc/passwd", "PATH_ESCAPE"),
                       ("/body_markdown/../../x", "PATH_ESCAPE"),
                       ("C:\\tmp\\x", "PATH_ESCAPE"),
                       ("body_markdown", "PATH_ESCAPE")):
        outcome = apply_patchset(patchset((replace(0, 3, "例 3", path=path),)),
                                 REVISION, artifact())
        assert_rejected_atomically(outcome, box, code)

    outcome = apply_patchset(patchset((replace(0, 3, "例 3", path="/other"),)),
                             REVISION, artifact())
    assert_rejected_atomically(outcome, box, "PATH_NOT_ALLOWED")
    assert POINTER in next(item.next_step for item in outcome.rejected
                           if item.code == "PATH_NOT_ALLOWED")


def test_missing_whitelist_and_unbound_ticket_are_rejected():
    box = {"artifact_id": ARTIFACT_ID, "hash": HASH, "body_markdown": BODY}
    outcome = apply_patchset(patchset((replace(0, 3, "例 3"),)), REVISION, box)
    assert_rejected_atomically(outcome, box, "NO_WHITELIST")

    box = artifact()
    outcome = apply_patchset(patchset((replace(0, 3, "例 3"),), ticket_ids=()),
                             REVISION, box)
    assert_rejected_atomically(outcome, box, "NO_TICKET_BINDING")

    outcome = apply_patchset(patchset((replace(0, 3, "例 3"),), ticket_ids=("别的工单",)),
                             REVISION, artifact())
    assert_rejected_atomically(outcome, box, "TICKET_MISMATCH")


def test_unknown_criterion_is_rejected():
    box = artifact()
    outcome = apply_patchset(patchset((replace(0, 3, "例 3"),),
                                      criterion_ids=("C-NOT-FROZEN",)), REVISION, box)
    assert_rejected_atomically(outcome, box, "UNKNOWN_CRITERION")


def test_overlapping_operations_are_rejected_atomically():
    box = artifact()
    outcome = apply_patchset(patchset((replace(0, 8, "甲"), replace(4, 12, "乙"))),
                             REVISION, box)
    assert_rejected_atomically(outcome, box, "OVERLAPPING_OPERATIONS")
    assert codes(outcome).count("OVERLAPPING_OPERATIONS") == 2
    # 逐条结果都在：调用方不用猜哪一条被拒
    assert {item["index"] for item in outcome.operation_results} == {0, 1}


def test_overlaps_across_different_paths_are_not_flagged():
    """重叠只按**同一条路径**判：不同路径上的区间互不影响。"""
    box = artifact(body_markdown=BODY, appendix="附录：结论。")
    operation_one = PatchOperation(op="replace", path=POINTER, start=0, end=3, text="例 3")
    operation_two = PatchOperation(op="replace", path="/appendix", start=0, end=3,
                                   text="附：")
    ticket_row = ticket()
    box["ticket"] = {**ticket_row.as_dict(), "allowed_paths": [POINTER, "/appendix"],
                     "preserve": []}
    outcome = apply_patchset(patchset((operation_one, operation_two)), REVISION, box)
    assert outcome.applied is True
    assert box["appendix"].startswith("附：")


def test_structure_break_is_rejected_atomically():
    box = artifact()
    outcome = apply_patchset(patchset((replace(0, 3, "例 3", path="/body_markdown/xyz"),)),
                             REVISION, artifact())
    assert_rejected_atomically(outcome, box, "POINTER_NOT_FOUND")

    outcome = apply_patchset(patchset((replace(0, len(BODY) + 9, "x"),)), REVISION, artifact())
    assert_rejected_atomically(outcome, box, "RANGE_OUT_OF_BOUNDS")

    duplicate = PatchOperation(op="insert_after", path=POINTER, start=0,
                               text="[[NOTE:N-03]] ")
    outcome = apply_patchset(patchset((duplicate,)), REVISION, artifact())
    assert_rejected_atomically(outcome, box, "STRUCTURE_INVALID")
    assert "[[NOTE:N-03]]" in next(item.message for item in outcome.rejected
                                   if item.code == "STRUCTURE_INVALID")


def test_preserve_violation_is_rejected():
    box = artifact()
    start = BODY.index(PRESERVE)
    outcome = apply_patchset(patchset((replace(start, start + 6, "不必验证"),)),
                             REVISION, box)
    assert_rejected_atomically(outcome, box, "PRESERVE_VIOLATION")


def test_test_operation_with_a_non_unique_anchor_is_rejected():
    ambiguous_text = "同一句。别的。同一句。"
    box = artifact(body_markdown=ambiguous_text)
    box["hash"] = "sha256:" + hashlib.sha256(ambiguous_text.encode("utf-8")).hexdigest()
    revision = ArtifactRevision(artifact_id=ARTIFACT_ID, unit_id=UNIT, revision=3,
                                hash=box["hash"])
    box["ticket"] = {**ticket().as_dict(), "target": {
        **ticket().target, "artifact_hash": box["hash"]}}
    operation = PatchOperation(op="test", path=POINTER, start=0, end=4, text="同一句。")
    outcome = apply_patchset(patchset((operation,), base_hash=box["hash"]), revision, box)
    assert outcome.applied is False
    assert "AMBIGUOUS_ANCHOR" in codes(outcome)
    rejection = next(item for item in outcome.rejected if item.code == "AMBIGUOUS_ANCHOR")
    assert "多处匹配" in rejection.message and "唯一" in rejection.next_step
    assert box["body_markdown"] == ambiguous_text


def test_no_change_and_test_mismatch_are_rejected():
    box = artifact()
    outcome = apply_patchset(patchset((replace(0, 3, BODY[0:3]),)), REVISION, box)
    assert_rejected_atomically(outcome, box, "NO_CHANGE")

    operation = PatchOperation(op="test", path=POINTER, start=0, end=3, text="并不存在")
    outcome = apply_patchset(patchset((operation,)), REVISION, artifact())
    assert_rejected_atomically(outcome, box, "TEST_UNSATISFIED")


# --------------------------------------------------------------------------- #
# 原子成功
# --------------------------------------------------------------------------- #
def test_successful_batch_lands_atomically_and_bumps_the_revision():
    box = artifact()
    start = BODY.index(ANCHOR)
    end = len(BODY)
    operation = replace(start, end,
                        ANCHOR + " 折线路线：先沿 $x$ 轴到 $(x,0)$，"
                        "得 $u=-4xy-y+C$；再由 $f(0)=1$ 定出 $C=1$，"
                        "于是 $f(z)=2\\mathrm{i}z^{2}+\\mathrm{i}z+1$。\n")
    outcome = apply_patchset(patchset((operation,)), REVISION, box)

    assert outcome.applied is True
    assert outcome.rejected == ()
    assert outcome.result_revision == REVISION.revision + 1
    assert "$u=-4xy-y+C$" in box["body_markdown"]
    assert PRESERVE in box["body_markdown"]          # 没被顺手改掉
    assert box["hash"] != HASH and str(box["hash"]).startswith("sha256:")
    assert box["parent_hash"] == HASH
    assert box["revision"] == REVISION.revision + 1
    assert [item["status"] for item in outcome.operation_results] == ["applied"]
    assert json.dumps(outcome.as_dict(), ensure_ascii=False)   # 可序列化


def test_multiple_non_overlapping_replaces_apply_from_the_end():
    box = artifact()
    first = replace(BODY.index("例 2"), BODY.index("例 2") + 2, "例 3")
    last = replace(BODY.index(ANCHOR), BODY.index(ANCHOR) + 4, "随后")
    outcome = apply_patchset(patchset((first, last)), REVISION, box)
    assert outcome.applied is True
    assert box["body_markdown"].startswith("例 3")
    assert "随后用 (4.5)" in box["body_markdown"]


def test_test_only_batch_does_not_create_a_revision():
    box = artifact()
    operation = PatchOperation(op="test", path=POINTER, start=0, end=2, text=BODY[0:2])
    outcome = apply_patchset(patchset((operation,)), REVISION, box)
    assert outcome.applied is True and outcome.result_revision is None
    assert box["body_markdown"] == BODY and box["hash"] == HASH
    assert "无改动" in outcome.reason


# --------------------------------------------------------------------------- #
# 关单核验
# --------------------------------------------------------------------------- #
def verdict(criterion_id: str = "C-LOGIC-1", *, version: int = 1, status: str = "pass",
            artifact_hash: str = HASH, role: str = "judge",
            quote: str = "先验证 $v$ 是调和函数。") -> CriterionVerdict:
    start = BODY.index(quote)
    span = EvidenceSpan(artifact_id=ARTIFACT_ID, artifact_hash=HASH, json_pointer=POINTER,
                        start=start, end=start + len(quote), quote=quote)
    return CriterionVerdict(ticket_id=ticket().ticket_id, criterion_id=criterion_id,
                            criteria_version=version, artifact_hash=artifact_hash,
                            status=status, why="折线路线、u 与 C=1 都逐字出现",
                            spans=(span,), verifier={"role": role, "model": "fixture"})


def test_closure_accepts_independent_evidence_on_the_current_revision():
    decision = evaluate_closure(ticket(), REVISION, [verdict()])
    assert decision.closed is True
    assert decision.failed_criteria == () and decision.missing_evidence == ()
    assert decision.verdicts


def test_closure_rejects_a_criterion_that_is_not_frozen_in_the_ticket():
    decision = evaluate_closure(ticket(), REVISION, [verdict("C-别的标准")])
    assert decision.closed is False
    assert "C-别的标准" in decision.failed_criteria
    assert "C-LOGIC-1" in decision.missing_evidence


def test_closure_rejects_an_old_criteria_version():
    decision = evaluate_closure(ticket(), REVISION, [verdict(version=2)])
    assert decision.closed is False
    assert "C-LOGIC-1" in decision.missing_evidence
    assert "版本" in decision.reason


def test_closure_rejects_evidence_bound_to_an_old_artifact_hash():
    decision = evaluate_closure(ticket(), REVISION,
                                [verdict(artifact_hash="sha256:" + "c" * 64)])
    assert decision.closed is False
    assert "旧版本" in decision.reason


def test_closure_rejects_non_independent_evidence():
    """`method=reviewer` 的判据要 judge 的独立证据；拿别的方法的角色自证不算。"""
    decision = evaluate_closure(ticket(), REVISION, [verdict(role="math")])
    assert decision.closed is False
    assert "独立" in decision.reason


def test_closure_ignores_the_generator_self_report():
    """生成器把工单标成 closed、说"已修好"，一个判词都不给 → 不能关闭。"""
    self_reported = validate_ticket(ticket_data(status="closed"))
    decision = evaluate_closure(self_reported, REVISION, [])
    assert decision.closed is False
    assert decision.missing_evidence == ("C-LOGIC-1",)
    assert "自报" in decision.reason

    # 即使判词是 pass，只要没有真实 span/依赖哈希，也不许关（构造期就挡住了）
    with pytest.raises(Exception):
        CriterionVerdict(ticket_id=ticket().ticket_id, criterion_id="C-LOGIC-1",
                         criteria_version=1, artifact_hash=HASH, status="pass",
                         why="已修好", spans=(), verifier={"role": "judge"})


def test_closure_requires_at_least_one_required_criterion():
    data = ticket_data(criteria=[{"criterion_id": "C1", "version": 1, "required": False,
                                  "expected": "随便", "method": "reviewer"}])
    ticket_without_required = validate_ticket(data)
    decision = evaluate_closure(ticket_without_required, REVISION, [])
    assert decision.closed is False
    assert "required" in decision.reason


def test_closure_allows_supplementing_the_old_sentence_without_rewriting_it():
    """§5.2：允许"旧原句没改、但补了正确前提"真正解决问题的那种修法。

    所以关单**不许**用"新引用与旧句不同"当判据 —— 这里引文就是工单 `target.location`
    指向的那句原话，只要它带着独立证据，照样能关。
    """
    old_sentence = ANCHOR
    decision = evaluate_closure(ticket(), REVISION, [verdict(quote=old_sentence)])
    assert decision.closed is True, decision.reason
