"""T01：返修契约 `2.0` 的**行为**测试（工单、版本、通过标准、不可变 revision）。

这组测试针对的是本轮改造里最容易被忽略、却决定其余一切的一环：**工单本身是不是可执行的**。

旧链路的失败不是"没有工单"，而是工单里少东西：
`blocks/audit/feedback.py` 把 `evidence`（教材依据）挤成了 `required_condition`，
`transitional.py:_verdict_closed` 又只回一个 bool 把 `why` 丢掉。于是生成器收到的是
「这句话有问题，改一改」——它既不知道卡在哪、也不知道凭什么标准算改好了。
本文件把"一条能派出去的工单必须回答六件事"变成**构造期就会失败的断言**，
而不是靠 prompt 里多写一句"请认真填写"。

计划依据：主计划 §5.1、§5.2、§4.2.1、§4.7；验收 A01、A04。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from aiforstudy.blocks.audit.contracts import (  # noqa: E402
    SCHEMA_VERSION,
    ArtifactRevision,
    Criterion,
    CriterionVerdict,
    EvidenceSpan,
    PatchOperation,
    PatchSet,
    RepairDirective,
    RepairTicket,
    RevisionLedger,
    assert_criteria_unchanged,
    assert_verdict_matches_ticket,
    criteria_versions,
    legacy_finding_to_ticket,
    make_ticket_id,
    validate_ticket,
    verdict_identity,
)
from aiforstudy.errors import ContractError  # noqa: E402


# --------------------------------------------------------------------------- #
# 夹具：一条**完整**的、真的能派出去的工单
# --------------------------------------------------------------------------- #
def _full_ticket_data(**overrides) -> dict:
    data = {
        "schema_version": SCHEMA_VERSION,
        "ticket_id": make_ticket_id(rule="LOGIC_GAP", unit_id="CH04-S01",
                                    artifact_id="lesson:CH04-S01", location="p:/body_markdown/§例2"),
        "unit_id": "CH04-S01",
        "kind": "text_content",
        "severity": "P0",
        "owner": "lesson",
        "status": "ready",
        "hard_acceptance": True,
        "target": {
            "artifact_id": "lesson:CH04-S01",
            "rule": "LOGIC_GAP",
            "json_pointer": "/body_markdown/§例2",
            "artifact_hash": "sha256:" + "a" * 64,
        },
        "problem": "例 2 只写到「用 (4.5) 积出 u」就止住，读者看不到折线积分怎么取、u 怎么算出来。",
        "learner_obstacle": "没学过复变积分的读者无法核对 (4.5) 的用法，也不知道 f(0)=1 如何定出常数。",
        "source_evidence": [
            {"artifact_id": "parse:CH04-S01", "artifact_hash": "sha256:" + "b" * 64,
             "json_pointer": "/body/§4.5", "start": 120, "end": 168,
             "quote": "$$u(x,y)=\\int_{0}^{x}\\frac{\\partial v}{\\partial y}(t,0)\\,\\mathrm{d}t$$"},
        ],
        "repair_instructions": {
            "locate": "/body_markdown/§例2/末段",
            "action": "在「再用 (4.5) 积出 u」之后补出折线路线与积分过程，并写出 C=1 的代入步骤。",
            "rationale": "教材 §4.5 给出了由 v 反求 u 的公式；(4.5) 就是这条路径积分。",
            "expected_change": "该段出现折线路线、u=-4xy-y+C、f(z)=2i z²+i z+1 三处可核对结果。",
        },
        "allowed_paths": ["/body_markdown"],
        "preserve": ["不得改动 §4.5 的公式编号", "不得删除原有的 C-R 方程推导"],
        "criteria": [
            {"criterion_id": "C-LOGIC-1", "version": 1, "required": True,
             "expected": "折线路线、u 的计算、f(z) 的拼装与 C=1 全部逐字出现在该段内",
             "method": "reviewer", "evidence_type": "text",
             "dependency_paths": ["/body_markdown/§例2"]},
        ],
        "previous_attempt": None,
        "history": [],
        "usage_refs": [],
    }
    data.update(overrides)
    return data


# --------------------------------------------------------------------------- #
# A01：不完整工单不许派发
# --------------------------------------------------------------------------- #
def test_full_ticket_is_dispatchable():
    ticket = validate_ticket(_full_ticket_data())
    assert ticket.status == "ready"
    assert ticket.owner == "lesson"
    assert ticket.criteria[0].criterion_id == "C-LOGIC-1"
    assert ticket.is_blocking is True
    assert ticket.schema_version == SCHEMA_VERSION


@pytest.mark.parametrize("broken, marker", [
    ("source_evidence", "source_evidence"),
    ("repair_action", "repair_instructions.action"),
    ("criterion_expected", "expected"),
    ("learner_obstacle", "learner_obstacle"),
])
def test_ticket_requires_actionable_feedback(broken, marker):
    """六件事缺任何一件，就不许自称 `ready`（A01：不完整不派发）。

    断言的是**拒绝理由点名了缺哪一格**：理由含糊的拒绝，生成器下一轮只会再猜一次。
    `preserve` 那一档已随 preserve 护栏删除，不再是"必须回答的六件事"之一。
    """
    data = _full_ticket_data()
    if broken == "source_evidence":
        data["source_evidence"] = []
    elif broken == "repair_action":
        data["repair_instructions"]["action"] = "   "
    elif broken == "criterion_expected":
        data["criteria"][0]["expected"] = ""
    elif broken == "learner_obstacle":
        data["learner_obstacle"] = ""
    with pytest.raises(ContractError) as excinfo:
        validate_ticket(data)
    assert marker in str(excinfo.value), (
        f"拒绝理由必须点名缺的是「{marker}」，实际：{excinfo.value}")


def test_incomplete_ticket_may_say_needs_evidence_but_not_ready():
    """诊断不充分可以存在，但只能以 `needs_evidence` 存在，不能装成可以派发。"""
    data = _full_ticket_data(status="needs_evidence", source_evidence=[])
    ticket = validate_ticket(data)
    assert ticket.status == "needs_evidence"
    assert not ticket.is_dispatchable
    with pytest.raises(ContractError):
        validate_ticket({**data, "status": "ready"})


def test_criteria_must_be_per_criterion_and_not_empty():
    with pytest.raises(ContractError):
        validate_ticket(_full_ticket_data(criteria=[]))
    with pytest.raises(ContractError):
        validate_ticket(_full_ticket_data(criteria=[
            {**(_full_ticket_data()["criteria"][0]), "method": "vibes"}]))


def test_hard_acceptance_failure_cannot_be_softened_by_severity():
    """§5.1：本轮新增硬验收失败均为 blocking，**不能只靠改 severity 放行**。"""
    ticket = validate_ticket(_full_ticket_data(severity="P2", hard_acceptance=True))
    assert ticket.is_blocking, "硬验收失败即便标 P2 也必须仍然阻塞发布"
    soft = validate_ticket(_full_ticket_data(severity="P2", hard_acceptance=False))
    assert not soft.is_blocking


# --------------------------------------------------------------------------- #
# 稳定 ID：同一规则的两处问题必须拿到不同 ID，同一问题重复发现必须稳定
# --------------------------------------------------------------------------- #
def test_same_rule_two_locations_get_distinct_ids():
    first = make_ticket_id(rule="LOGIC_GAP", unit_id="CH04-S01",
                           artifact_id="lesson:CH04-S01", location="p:/body_markdown/§例2")
    second = make_ticket_id(rule="LOGIC_GAP", unit_id="CH04-S01",
                            artifact_id="lesson:CH04-S01", location="p:/body_markdown/§例3")
    assert first != second, "同一规则的两次独立发现不能共用一个 ticket_id"

    # 稳定：同样的输入两次必须得到同一个 ID（否则续跑会重复开工单／重复计轮次）
    assert first == make_ticket_id(rule="LOGIC_GAP", unit_id="CH04-S01",
                                   artifact_id="lesson:CH04-S01",
                                   location="p:/body_markdown/§例2")
    # 且不是旧链路那种 `code@unit` 拼接
    assert "@" not in first
    assert first != "LOGIC_GAP@CH04-S01"


def test_ticket_id_does_not_change_when_only_the_quote_is_rewritten():
    """§5.1：不能拿「现有原句」当唯一 ID —— 句子改了不等于换了一个问题。"""
    data = _full_ticket_data()
    before = validate_ticket(data).ticket_id
    data["source_evidence"][0]["quote"] = "完全换了一句话，但位置和规则都没变"
    data["problem"] = "换个说法描述同一个卡点"
    after = validate_ticket(data).ticket_id
    assert before == after


def test_repeat_verdict_cannot_regain_attempts_by_new_id():
    """§4.7：重复 verdict 内容不能通过换 ID 重获一次证据修复机会。"""
    base = dict(ticket_id="t-1", criterion_id="C-LOGIC-1", criteria_version=1,
                artifact_hash="sha256:" + "a" * 64, status="fail",
                why="折线路线没有出现")
    again = {**base, "verdict_id": "随便换个 ID"}
    assert verdict_identity(base) == verdict_identity(again)
    changed = {**base, "why": "u 的计算过程缺失"}
    assert verdict_identity(changed) != verdict_identity(base)


# --------------------------------------------------------------------------- #
# 通过与标准的版本不可变
# --------------------------------------------------------------------------- #
def test_criteria_version_is_immutable():
    ticket = validate_ticket(_full_ticket_data())
    frozen = criteria_versions(ticket)
    assert frozen == {"C-LOGIC-1": 1}

    # 同一 criterion_id 改版本 = 偷改通过标准，必须拒绝
    with pytest.raises(ContractError) as excinfo:
        assert_criteria_unchanged(ticket, [
            Criterion(criterion_id="C-LOGIC-1", version=2, required=True,
                      expected="放宽后的标准", method="reviewer")])
    assert "版本" in str(excinfo.value)

    # 标准内容变了但版本没变，等于暗改，同样拒绝
    with pytest.raises(ContractError):
        assert_criteria_unchanged(ticket, [
            Criterion(criterion_id="C-LOGIC-1", version=1, required=True,
                      expected="偷偷换成更容易过的话", method="reviewer")])

    # 原样重放是允许的（重验会用同一套标准）
    assert_criteria_unchanged(ticket, ticket.criteria)


def test_verdict_must_carry_the_frozen_criteria_version():
    """判词要**对得上工单**：同一条 criterion、冻结版本、同一个 artifact 版本。

    版本一致性天然需要两边都在场，所以判据是一个显式函数而不是构造期断言 ——
    把"必须先有工单才能判"这件事写进 API，比在构造函数里猜更诚实。
    """
    ticket = validate_ticket(_full_ticket_data())
    span = EvidenceSpan(artifact_id="lesson:CH04-S01", artifact_hash="sha256:" + "a" * 64,
                        json_pointer="/body_markdown", start=0, end=3, quote="例 2 ")
    good = CriterionVerdict(
        ticket_id=ticket.ticket_id, criterion_id="C-LOGIC-1", criteria_version=1,
        artifact_hash="sha256:" + "a" * 64, status="pass", why="逐字核对通过",
        spans=(span,), verifier={"role": "judge", "version": "deepseek-flash"})
    assert_verdict_matches_ticket(ticket, good)

    wrong_version = CriterionVerdict(
        ticket_id=ticket.ticket_id, criterion_id="C-LOGIC-1", criteria_version=99,
        artifact_hash="sha256:" + "a" * 64, status="pass", why="按另一个版本的标准判的",
        spans=(span,))
    with pytest.raises(ContractError) as excinfo:
        assert_verdict_matches_ticket(ticket, wrong_version)
    assert "版本" in str(excinfo.value)

    # 拿**旧 revision** 的通过去关新版本的工单，必须拒绝（A04）
    stale = CriterionVerdict(
        ticket_id=ticket.ticket_id, criterion_id="C-LOGIC-1", criteria_version=1,
        artifact_hash="sha256:" + "d" * 64, status="pass", why="我核的是上一版正文",
        spans=(span,))
    with pytest.raises(ContractError) as excinfo:
        assert_verdict_matches_ticket(ticket, stale)
    assert "旧版本" in str(excinfo.value)

    # 判 pass 却只给一句"已修好"、没有任何 span／独立哈希 → 构造期就拒绝
    with pytest.raises(ContractError):
        CriterionVerdict(ticket_id=ticket.ticket_id, criterion_id="C-LOGIC-1",
                         criteria_version=1, artifact_hash="sha256:" + "a" * 64,
                         status="pass", why="已修好")


def test_pending_criterion_cannot_be_verified():
    """旧工单导入的"待补标准"永远不能拿来核验 —— 否则空标准就等于放行。"""
    legacy = {"code": "LOGIC_GAP", "severity": "P0", "unit_key": "CH04-S01",
              "why": "卡点", "evidence": "要求", "where": "某句"}
    ticket = legacy_finding_to_ticket(legacy, artifact_id="lesson:CH04-S01",
                                      artifact_hash="sha256:" + "a" * 64)
    pending = ticket.criteria[0]
    assert pending.pending and not pending.usable
    verdict = CriterionVerdict(
        ticket_id=ticket.ticket_id, criterion_id=pending.criterion_id, criteria_version=1,
        artifact_hash="sha256:" + "a" * 64, status="pass", why="看着没问题",
        spans=(EvidenceSpan(artifact_id="lesson:CH04-S01",
                            artifact_hash="sha256:" + "a" * 64,
                            json_pointer="/body_markdown", start=0, end=2, quote="某句"),))
    with pytest.raises(ContractError) as excinfo:
        assert_verdict_matches_ticket(ticket, verdict)
    assert "待补标准" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 证据必须是真的切片
# --------------------------------------------------------------------------- #
def test_evidence_span_quote_must_equal_the_real_slice():
    span = EvidenceSpan(artifact_id="parse:CH04-S01", artifact_hash="sha256:" + "b" * 64,
                        json_pointer="/body/§4.5", start=6, end=11, quote="world")
    assert span.check_against("hello world") is True
    bad = EvidenceSpan(artifact_id="parse:CH04-S01", artifact_hash="sha256:" + "b" * 64,
                       json_pointer="/body/§4.5", start=6, end=11, quote="world")
    with pytest.raises(ContractError):
        bad.check_against("hello there")
    with pytest.raises(ContractError):
        EvidenceSpan(artifact_id="x", artifact_hash="h", json_pointer="/p",
                     start=5, end=3, quote="举")


# --------------------------------------------------------------------------- #
# artifact revision 链不可覆盖父版本
# --------------------------------------------------------------------------- #
def test_revision_chain_does_not_overwrite_parent():
    ledger = RevisionLedger(artifact_id="lesson:CH04-S01", unit_id="CH04-S01")
    first = ledger.append(hash="sha256:" + "1" * 64, context_digest="digest-1")
    assert first.revision == 1
    second = ledger.append(hash="sha256:" + "2" * 64, context_digest="digest-2")
    assert second.revision == 2
    assert second.parent_hash == first.hash
    assert ledger.head.hash == second.hash

    # 同一个 revision 想换个内容盖回去 = 改历史，必须拒绝
    with pytest.raises(ContractError) as excinfo:
        ledger.append(hash="sha256:" + "3" * 64, context_digest="digest-2", revision=2)
    assert "覆盖" in str(excinfo.value) or "不可变" in str(excinfo.value)
    # 上下文摘要变了也算改历史：revision 2 的内容指纹不能原地变
    with pytest.raises(ContractError):
        ledger.append(hash="sha256:" + "2" * 64, context_digest="后来补的摘要",
                      revision=2)

    # 原样重放同一个 revision 是幂等的（续跑不会炸）
    assert ledger.append(hash="sha256:" + "1" * 64, context_digest="digest-1",
                         revision=1).revision == 1
    # 跳号追加也不许
    with pytest.raises(ContractError):
        ledger.append(hash="sha256:" + "9" * 64, context_digest="d", revision=9)


def test_revision_ledger_roundtrips_through_json():
    ledger = RevisionLedger(artifact_id="lesson:CH04-S01", unit_id="CH04-S01")
    ledger.append(hash="sha256:" + "1" * 64, context_digest="d1")
    ledger.append(hash="sha256:" + "2" * 64, context_digest="d2")
    restored = RevisionLedger.from_dict(json.loads(json.dumps(ledger.as_dict())))
    assert [r.hash for r in restored.revisions] == [r.hash for r in ledger.revisions]
    assert restored.head.revision == 2


def test_artifact_revision_requires_hash_and_unit():
    with pytest.raises(ContractError):
        ArtifactRevision(artifact_id="x", unit_id="", revision=1, hash="h")
    with pytest.raises(ContractError):
        ArtifactRevision(artifact_id="x", unit_id="u", revision=0, hash="h")


# --------------------------------------------------------------------------- #
# 工单 roundtrip 不丢字段；directive 只带数据不带可执行内容
# --------------------------------------------------------------------------- #
def test_ticket_roundtrip_preserves_every_field():
    ticket = validate_ticket(_full_ticket_data())
    again = validate_ticket(json.loads(json.dumps(ticket.as_dict())))
    assert again.as_dict() == ticket.as_dict()
    assert again.ticket_id == ticket.ticket_id
    assert again.source_evidence == ticket.source_evidence
    assert again.repair_instructions == ticket.repair_instructions
    assert again.usage_refs == ticket.usage_refs


def test_unknown_fields_are_rejected_not_silently_dropped():
    data = _full_ticket_data()
    data["secret_sauce"] = "偷偷塞一个字段"
    with pytest.raises(ContractError) as excinfo:
        validate_ticket(data)
    assert "secret_sauce" in str(excinfo.value)


def test_patchset_only_allows_test_replace_insert_after():
    ok = PatchSet(ticket_ids=[], base_hash="sha256:" + "a" * 64, criterion_ids=["C-LOGIC-1"],
                  operations=[PatchOperation(op="replace", path="/body_markdown",
                                             start=0, end=3, text="新")],
                  explanation="把这句话补完整")
    assert ok.operations[0].op == "replace"
    for bad_op in ("write_file", "delete", "eval", "shell"):
        with pytest.raises(ContractError):
            PatchOperation(op=bad_op, path="/x", text="y")
    with pytest.raises(ContractError):
        PatchOperation(op="replace", path="/x", start=3, end=3, text="")   # 空替换等于没改


def test_repair_directive_is_data_only():
    """§4.2.1：directive 指向工单与哈希，**不夹带可执行代码**。"""
    directive = RepairDirective(unit_id="CH04-S01", ticket_ids=["t-1"], owner="lesson",
                                artifact_hashes={"lesson:CH04-S01": "sha256:" + "a" * 64},
                                reason="正文返修", kind="text_content")
    payload = json.loads(json.dumps(directive.as_dict()))
    assert set(payload) == {"unit_id", "ticket_ids", "owner", "artifact_hashes", "reason",
                            "kind", "verdict_content"}
    assert "code" not in json.dumps(payload)
    assert payload["kind"] == "text_content", (
        "返修类别必须显式携带：它决定这条工单算不算消耗内容批次，不许靠解析 reason 猜")
    with pytest.raises(ContractError):
        RepairDirective(unit_id="", ticket_ids=[], owner="lesson")
    with pytest.raises(ContractError):
        RepairDirective(unit_id="u", ticket_ids=["t"], owner="nobody")
    with pytest.raises(ContractError):
        RepairDirective(unit_id="u", ticket_ids=["t"], owner="lesson", reason="r",
                        kind="乱写的类别")


# --------------------------------------------------------------------------- #
# 旧 Finding → 候选工单：只读适配，缺信息标 needs_evidence，不编造标准
# --------------------------------------------------------------------------- #
def test_legacy_finding_adapter_marks_needs_evidence_when_evidence_was_squeezed():
    """旧链路把教材依据挤成了 `required_condition`；适配时**不许**把它当成依据。

    这一条是本轮要治的那个 bug 的**结构性**防线：如果适配器把旧 `evidence`
    （里面装的其实是「要求」）塞进 `source_evidence`，那就是把同一处错误又搬了一遍 ——
    工单会带着一份假依据变成 `ready`。所以旧 `required_condition` 只**留档**，
    `source_evidence` 保持空，工单因此停在 `needs_evidence`。
    """
    legacy = {
        "code": "LOGIC_GAP",
        "severity": "P0",
        "unit_key": "CH04-S01",
        "why": "例 2 只写到「用 (4.5) 积出 u」就止住",
        "evidence": "补出完整解答链",          # 这一格旧代码里其实装的是「要求」
        "where": "再用 (4.5) 积出 $u$，最后用 $f(0)=1$ 定常数。",
    }
    ticket = legacy_finding_to_ticket(legacy, artifact_id="lesson:CH04-S01",
                                      artifact_hash="sha256:" + "a" * 64)
    assert ticket.status == "needs_evidence"
    assert not ticket.is_dispatchable
    assert ticket.owner == "reviewer", "诊断不充分的工单先由 reviewer 补齐，不直接派给生成器"
    assert ticket.legacy_required_condition == "补出完整解答链", "旧「要求」必须留档，不能丢"
    assert not ticket.source_evidence, (
        "旧 required_condition 不许冒充 source_evidence 里的教材依据 —— "
        "那正是本轮要治的 evidence 被占用的 bug")
    assert ticket.criteria, "缺通过标准时也要显式列出待补标准，不能空着当通过"
    assert all(not c.expected.strip() for c in ticket.criteria), (
        "适配旧工单不许**编造** expected —— 那等于替 reviewer 定标准")
    assert ticket.target["anchor_kind"] == "text", "用正文句当锚点必须如实标注，不假装是结构化指针"


def test_legacy_finding_adapter_keeps_identity_stable():
    legacy = {"code": "LOGIC_GAP", "severity": "P0", "unit_key": "CH04-S01",
              "why": "卡点", "evidence": "要求", "where": "原文那一句"}
    a = legacy_finding_to_ticket(copy.deepcopy(legacy), artifact_id="lesson:CH04-S01",
                                 artifact_hash="sha256:" + "a" * 64)
    b = legacy_finding_to_ticket(copy.deepcopy(legacy), artifact_id="lesson:CH04-S01",
                                 artifact_hash="sha256:" + "a" * 64)
    assert a.ticket_id == b.ticket_id


def test_ticket_rejects_bad_enums_and_missing_identity():
    for field, value in (("kind", "随便"), ("owner", "nobody"), ("status", "done"),
                         ("severity", "P9")):
        with pytest.raises(ContractError):
            validate_ticket(_full_ticket_data(**{field: value}))
    with pytest.raises(ContractError):
        validate_ticket(_full_ticket_data(unit_id=""))
    with pytest.raises(ContractError):
        validate_ticket(_full_ticket_data(
            schema_version="1.9"))  # 旧版本号不许当新契约用
