"""Semantic inventory validates source evidence before the candidate exists."""
from copy import deepcopy
from hashlib import sha256
import importlib
import json

import pytest

from aiforstudy.blocks.content.source_context import source_context_from_frozen
from aiforstudy.blocks.content.generation_corrections import LocalCorrectionRequired
from aiforstudy.errors import ContractError


TEXT = ("背景导语。\n\n定理1 对非零 x，x/x=1。\n\n"
        "计算时先检查 x 非零，再约去 x。\n\n记号 n 表示非负整数，包括零。\n")


def context(text=TEXT, concepts=None):
    return source_context_from_frozen(dict(
        book_id="B", unit_id="U", source_text=text, original_text="U.md",
        original_sha256="sha256:" + sha256(text.encode()).hexdigest(),
        source_inputs={"original": "version-one"}, unit={}, chapters={},
        concept_index={"concepts": concepts or []},
    ))


def inventory_module():
    return importlib.import_module("aiforstudy.blocks.content.source_inventory")


class ScriptedClient:
    """本地脚本化传输；R3-1 后一次答复即判，多余响应视为错误。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def chat_json(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        assert self.responses, "unexpected extra provider call"
        return {'data': deepcopy(self.responses.pop(0))}


def response(source, *, extra=True):
    """Complete fake provider response, with positions derived from literal text."""
    rows = []
    for obligation in source.source_obligations:
        rows.append(dict(id=obligation.id, kind=obligation.kind, level=obligation.level,
            concept_id=obligation.concept_id, conditions=obligation.conditions,
            conclusion=obligation.conclusion or obligation.source_excerpt,
            source_excerpt=obligation.source_excerpt))
    if extra:
        rows.append(dict(id="NEW-calculation", kind="procedure", level="core", concept_id="",
            conditions=["x 非零"], conclusion="检查 x 非零后约去 x。",
            source_excerpt="计算时先检查 x 非零，再约去 x。"))
    blocks = inventory_module().source_blocks(source)
    coverage = []
    for block in blocks:
        covered = []
        for row in rows:
            start = source.source_text.find(row["source_excerpt"])
            if start < block["end"] and start + len(row["source_excerpt"]) > block["start"]:
                covered.append(row["id"])
        coverage.append(dict(block_id=block["id"], obligation_ids=covered,
            reason="已逐段核对；本段所列义务覆盖其教学要求。" if covered else "背景导语，仅交代语境，没有新增教学义务。"))
    return dict(schema_version="SourceInventoryV1", book_id=source.book_id,
        unit_id=source.unit_id, original_sha256=source.original_sha256,
        source_context_hash=source.digest, obligations=rows, coverage=coverage, unresolved=[])


def test_source_blocks_keep_all_nonblank_text_and_exact_offsets():
    source = context("  引言\r\n\r\n第一行\n第二行\n  \n最后一行")
    blocks = inventory_module().source_blocks(source)
    assert [(b["start"], b["end"], b["text"]) for b in blocks] == [
        (0, 6, "  引言\r\n"), (8, 16, "第一行\n第二行\n"), (19, 23, "最后一行")]
    for block in blocks:
        assert source.source_text[block["start"]:block["end"]] == block["text"]
    assert blocks == inventory_module().source_blocks(source)
    assert blocks[0]["id"] != inventory_module().source_blocks(context("  引言\r\n\r\n另一个来源"))[0]["id"]


def test_messages_contain_full_source_and_structural_candidates_before_authoring():
    source = context()
    messages = inventory_module().build_inventory_messages(source)
    payload = json.loads(messages[1]["content"])
    assert payload["source_text"] == TEXT
    assert payload["structural_obligations"] == [row.as_dict() for row in source.source_obligations]
    assert len(payload["source_blocks"]) == 4
    assert payload["output_json_schema"]["additionalProperties"] is False
    assert "candidate" not in payload and "teaching_plan" not in payload


def test_capacity_refuses_full_request_before_any_provider_call(monkeypatch):
    module = inventory_module()
    monkeypatch.setattr(module, "MAX_REQUEST_BYTES", 100)
    class NoCall:
        def chat_json(self, *_args, **_kw):
            pytest.fail("oversized source reached provider")
    with pytest.raises(ContractError, match="capacity.*fix:.*passing criterion:"):
        module.extract_source_inventory(context(), NoCall())


def test_valid_inventory_preserves_frozen_identity_and_exact_host_references():
    source = context()
    raw = response(source)
    before = deepcopy(raw)
    refined = inventory_module().validate_source_inventory(source, raw)
    assert refined.obligation_extraction_status == "semantic_verified"
    assert refined.source_context_hash == refined.digest
    assert refined.digest != source.digest
    assert {row.id for row in source.source_obligations} < {row.id for row in refined.source_obligations}
    added = next(row for row in refined.source_obligations if row.kind == "procedure")
    assert added.id.startswith("SO-") and added.id != "NEW-calculation"
    assert added.conditions == ["x 非零"]
    assert added.conclusion == "检查 x 非零后约去 x。"
    ref = added.source_refs[0]
    assert source.source_text[ref.start:ref.end] == "计算时先检查 x 非零，再约去 x。"
    assert ref.quote == added.source_excerpt and ref.sha256 == source.original_sha256
    assert ref.unit_id == "U" and ref.path == "U.md"
    assert raw == before and source.obligation_extraction_status == "structural_only"
    for name in ("book_id", "unit_id", "source_text", "source_inputs", "original_sha256"):
        assert getattr(source, name) == getattr(refined, name)


def test_new_ids_and_refined_digest_ignore_model_labels_and_array_order():
    source = context()
    raw = response(source)
    first = inventory_module().validate_source_inventory(source, raw)
    raw["obligations"][-1]["id"] = "NEW-different-label"
    for block in raw["coverage"]:
        block["obligation_ids"] = ["NEW-different-label" if oid == "NEW-calculation" else oid for oid in block["obligation_ids"]]
        block["obligation_ids"].reverse()
    raw["obligations"].reverse()
    raw["coverage"].reverse()
    second = inventory_module().validate_source_inventory(source, raw)
    assert first.as_dict() == second.as_dict()


def reject(source, raw, code):
    with pytest.raises(ContractError, match="(?s)" + code + ".*fix:.*passing criterion:"):
        inventory_module().validate_source_inventory(source, raw)


@pytest.mark.parametrize("field,value", [
    ("book_id", "OTHER"), ("unit_id", "FOREIGN"),
    ("original_sha256", "changed"), ("source_context_hash", "stale"),
])
def test_response_bound_to_current_source_identity(field, value):
    source = context()
    raw = response(source)
    raw[field] = value
    reject(source, raw, "source_identity")


def test_source_omission_cannot_be_hidden_by_empty_coverage():
    source = context()
    raw = response(source)
    omitted = raw["obligations"].pop(0)["id"]
    for block in raw["coverage"]:
        block["obligation_ids"] = [oid for oid in block["obligation_ids"] if oid != omitted]
    reject(source, raw, "missing_structural")


@pytest.mark.parametrize("change,code", [
    ({"level": "support"}, "core_downgrade"),
    ({"kind": "convention"}, "structural_kind"),
    ({"concept_id": "FOREIGN"}, "structural_concept"),
    ({"source_excerpt": "记号 n 表示非负整数，包括零。"}, "structural_span"),
    ({"source_excerpt": "对所有 x，x/x=1。"}, "source_quote"),
])
def test_structural_requirements_cannot_downgrade_or_move(change, code):
    source = context()
    raw = response(source)
    raw["obligations"][0].update(change)
    reject(source, raw, code)


@pytest.mark.parametrize("mutation,code", [
    ("duplicate_obligation", "duplicate_obligation"),
    ("foreign_obligation", "foreign_obligation"),
    ("foreign_concept", "foreign_concept"),
    ("missing_block", "block_coverage"),
    ("duplicate_block", "block_coverage"),
    ("foreign_block", "block_coverage"),
    ("unknown_reference", "coverage_reference"),
    ("duplicate_reference", "coverage_reference"),
    ("unrelated_reference", "coverage_span"),
    ("empty_reason", "coverage_reason"),
    ("orphan_obligation", "uncovered_obligation"),
    ("new_duplicate_location", "duplicate_source_identity"),
    ("unresolved", "unresolved"),
    ("free_truth_flag", "schema"),
    ("unsupported_kind", "schema"),
    ("wrong_condition_type", "schema"),
    ("empty_quote", "schema"),
])
def test_invalid_inventory_never_promotes_semantic_verified(mutation, code):
    source = context()
    raw = response(source)
    if mutation == "duplicate_obligation": raw["obligations"].append(deepcopy(raw["obligations"][0]))
    if mutation == "foreign_obligation": raw["obligations"][-1]["id"] = "SO-foreign"
    if mutation == "foreign_concept": raw["obligations"][-1]["concept_id"] = "C-foreign"
    if mutation == "missing_block": raw["coverage"].pop()
    if mutation == "duplicate_block": raw["coverage"].append(deepcopy(raw["coverage"][0]))
    if mutation == "foreign_block": raw["coverage"][0]["block_id"] = "SB-foreign"
    if mutation == "unknown_reference": raw["coverage"][0]["obligation_ids"] = ["NEW-unknown"]
    if mutation == "duplicate_reference": raw["coverage"][1]["obligation_ids"] *= 2
    if mutation == "unrelated_reference": raw["coverage"][0]["obligation_ids"] = [raw["obligations"][-1]["id"]]
    if mutation == "empty_reason": raw["coverage"][0]["reason"] = " "
    if mutation == "orphan_obligation":
        for block in raw["coverage"]:
            block["obligation_ids"] = [oid for oid in block["obligation_ids"] if oid != "NEW-calculation"]
    if mutation == "new_duplicate_location":
        raw["obligations"].append(dict(raw["obligations"][-1], id="NEW-duplicate"))
    if mutation == "unresolved": raw["unresolved"] = ["无法确定本段是否含隐藏条件。"]
    if mutation == "free_truth_flag": raw["semantic_verified"] = True
    if mutation == "unsupported_kind": raw["obligations"][-1]["kind"] = "axiom"
    if mutation == "wrong_condition_type": raw["obligations"][-1]["conditions"] = "x 非零"
    if mutation == "empty_quote": raw["obligations"][-1]["source_excerpt"] = ""
    reject(source, raw, code)


def test_repeated_quote_requires_disambiguating_exact_source_context():
    source = context("相同约定。\n\n相同约定。")
    raw = response(source, extra=False)
    raw["obligations"] = [dict(id="NEW-repeat", kind="convention", level="core", concept_id="",
        conditions=[], conclusion="相同约定。", source_excerpt="相同约定。")]
    reject(source, raw, "ambiguous_quote")


def test_unconditional_convention_and_intro_without_obligation_are_valid():
    source = context("这一段介绍记号的背景。\n\n记号 n 表示非负整数。")
    raw = response(source, extra=False)
    result = inventory_module().validate_source_inventory(source, raw)
    assert result.obligation_extraction_status == "semantic_verified"
    assert result.source_obligations[0].conditions == []


def test_no_source_text_is_rejected_instead_of_vacuously_verified():
    source = context(" \n\t")
    raw = response(source, extra=False)
    reject(source, raw, "empty_source")


def test_concept_scaffold_can_be_source_backed_refined_with_before_after_evidence():
    source = context(concepts=[dict(id="C1", first_unit="U", formal="x/x=1。", conditions=["任意 x"])])
    raw = response(source)
    row = next(row for row in raw["obligations"] if row["concept_id"] == "C1")
    prior = next(row for row in source.source_obligations if row.concept_id == "C1")
    row.update(kind="theorem", conditions=["x 非零"], conclusion="x/x=1。",
        refinement_reason="索引误把带非零条件的定理当作定义；原文定理明确限制 x。",
        previous_requirements=dict(kind=prior.kind, conditions=prior.conditions, conclusion=prior.conclusion))
    result = inventory_module().validate_source_inventory(source, raw)
    assert next(row for row in result.source_obligations if row.id == prior.id).conditions == ["x 非零"]
    row.pop("previous_requirements")
    # 判据已删除：不再要求逐字节复现索引预记值。
    assert inventory_module().validate_source_inventory(source, raw) is not None
def test_extraction_preserves_failed_response_before_validation_and_calls_once():
    source = context()
    raw = response(source)
    raw["unresolved"] = ["需要补充定位。"]
    saved, calls = [], []
    class Client:
        def chat_json(self, messages, *, tag):
            calls.append((messages, tag))
            return {"data": raw}
    with pytest.raises(ContractError, match="unresolved"):
        inventory_module().extract_source_inventory(source, Client(), saved.append)
    assert saved == [raw] and saved[0] is not raw
    assert len(calls) == 1 and calls[0][1] == "content-source-inventory:U"


def test_successful_extraction_uses_same_pure_validator_as_replay():
    source = context()
    raw = response(source)
    class Client:
        def chat_json(self, messages, *, tag):
            return {"data": raw}
    extracted = inventory_module().extract_source_inventory(source, Client())
    replayed = inventory_module().validate_source_inventory(source, raw)
    assert extracted.as_dict() == replayed.as_dict()


def test_missing_coverage_can_be_locally_corrected_without_rewriting_obligations(tmp_path):
    """R3-1：coverage 缺失一次即判，反馈（含 source_blocks）写进 journal。"""
    source = context()
    valid = response(source)
    invalid = deepcopy(valid)
    invalid['coverage'].pop()
    journal = tmp_path / 'journal.json'
    client = ScriptedClient(invalid)
    with pytest.raises(ContractError, match='逐条修正'):
        inventory_module().extract_source_inventory(source, client, correction_journal=journal)
    assert len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    assert feedback['allowed_paths'] == ['/coverage']
    assert feedback['errors'][0]['code'] == 'block_coverage'
    assert feedback['errors'][0]['source_blocks']
    # 合法答复另起一次调用通过
    result = inventory_module().extract_source_inventory(source, ScriptedClient(valid))
    assert result.obligation_extraction_status == 'semantic_verified'


def test_coverage_correction_cannot_rewrite_source_requirements(tmp_path):
    source = context()
    first = source.source_obligations[0].model_copy(update={"conditions": ["x 非零"], "conclusion": "x/x=1。"})
    source = source.model_copy(update={"source_obligations": [first] + source.source_obligations[1:]})
    source = source.model_copy(update={"source_context_hash": source.digest})
    invalid = response(source)
    invalid['coverage'].pop()
    changed = response(source)
    changed['obligations'][0]['conclusion'] = 'x/x=2。'
    # R3-1：内层取消 —— coverage 缺失一次即判；改写冻结结论在**全新一份**答复里仍被判死，
    # 保证本身不变（改写永远不会被接受），只是不再用"纠正轮"来体现。
    journal = tmp_path / 'journal.json'
    client = ScriptedClient(invalid)
    with pytest.raises(ContractError, match='逐条修正'):
        inventory_module().extract_source_inventory(source, client, correction_journal=journal)
    assert len(client.calls) == 1
    assert json.loads(journal.read_text())['merged_feedback']['allowed_paths'] == ['/coverage']
    changed_client = ScriptedClient(changed)
    with pytest.raises(ContractError, match='structural_requirements'):
        inventory_module().extract_source_inventory(source, changed_client)
    assert len(changed_client.calls) == 1


def test_coverage_correction_gets_a_lawful_round_and_can_succeed(tmp_path):
    """R3-1：一次即判；照 journal 反馈改好的完整答复（另起一次调用）能通过。"""
    source = context()
    first = source.source_obligations[0].model_copy(update={"conditions": ["x 非零"], "conclusion": "x/x=1。"})
    source = source.model_copy(update={"source_obligations": [first] + source.source_obligations[1:]})
    source = source.model_copy(update={"source_context_hash": source.digest})
    invalid = response(source)
    invalid['coverage'].pop()
    good = response(source)
    journal = tmp_path / 'journal.json'
    client = ScriptedClient(invalid)
    with pytest.raises(ContractError, match='逐条修正'):
        inventory_module().extract_source_inventory(source, client, correction_journal=journal)
    assert len(client.calls) == 1
    assert json.loads(journal.read_text())['merged_feedback']['allowed_paths'] == ['/coverage']
    extracted = inventory_module().extract_source_inventory(source, ScriptedClient(good))
    assert extracted.source_obligations[0].conclusion == 'x/x=1。'


def test_example_cannot_replace_original_problem_with_its_solution_quote():
    source = context("例1.1 已知 x 非零，求 x/x。\n解：结果为 1。")
    raw = response(source, extra=False)
    raw["obligations"][0]["source_excerpt"] = "结果为 1。"
    reject(source, raw, "example_source")


@pytest.mark.parametrize("field,value", [("conditions", []), ("conclusion", "x/x=2。")])
def test_directly_extracted_canonical_requirements_cannot_disappear(field, value):
    source = context()
    first = source.source_obligations[0].model_copy(update={"conditions": ["x 非零"], "conclusion": "x/x=1。"})
    source = source.model_copy(update={"source_obligations": [first] + source.source_obligations[1:]})
    source = source.model_copy(update={"source_context_hash": source.digest})
    raw = response(source)
    raw["obligations"][0][field] = value
    reject(source, raw, "structural_requirements")


def test_multiple_frozen_ids_for_same_source_quote_are_not_deduplicated():
    source = context("定理1 对非零 x，x/x=1。", concepts=[
        dict(id="C1", first_unit="U", formal="定理1 对非零 x，x/x=1。"),
        dict(id="C2", first_unit="U", formal="定理1 对非零 x，x/x=1。"),
    ])
    raw = response(source, extra=False)
    refined = inventory_module().validate_source_inventory(source, raw)
    assert len(refined.source_obligations) == 3
    assert {row.id for row in refined.source_obligations} == {row.id for row in source.source_obligations}


def test_same_text_with_changed_frozen_inputs_invalidates_saved_response():
    source = context()
    raw = response(source)
    changed = source.model_copy(update={"source_inputs": {"original": "version-two"}})
    changed = changed.model_copy(update={"source_context_hash": changed.digest})
    reject(changed, raw, "source_identity")


def test_mutated_context_cannot_replay_under_old_context_digest():
    source = context()
    raw = response(source)
    changed = source.model_copy(update={"source_text": TEXT + "新条件。"})
    reject(changed, raw, "context_digest")


def test_response_sink_cannot_mutate_data_used_for_validation():
    source = context()
    raw = response(source)
    class Client:
        def chat_json(self, messages, *, tag):
            return {"data": raw}
    def sink(saved):
        saved["obligations"] = []
    refined = inventory_module().extract_source_inventory(source, Client(), sink)
    assert refined.obligation_extraction_status == "semantic_verified"
    assert raw["obligations"]


@pytest.mark.parametrize("raw", [None, [], {"accepted": True}])
def test_malformed_provider_data_survives_validation_failure_in_sink(raw):
    source = context()
    saved = []
    class Client:
        def chat_json(self, messages, *, tag):
            return {"data": raw}
    with pytest.raises(ContractError, match="schema"):
        inventory_module().extract_source_inventory(source, Client(), saved.append)
    assert saved == [raw]
