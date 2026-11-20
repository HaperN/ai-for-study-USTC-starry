"""Optional citations stay current; semantic decisions cover frozen criteria."""
from copy import deepcopy

import pytest

from aiforstudy.errors import ContractError


def item(**changes):
    row = {
        "criterion_id": "CR-source-T1", "version": 1,
        "rule_id": "CONTENT-MATH", "method": "reviewer", "required": True,
        "subject_id": "T1", "expected": "Preserve x != 0 before cancellation",
        "repair_hint": "State x != 0 before dividing by x",
        "required_evidence_kinds": ["reader", "source"],
        "content_hash": "sha256:current", "policy_digest": "sha256:policy",
    }
    row.update(changes)
    return row


def evidence():
    return {
        "reader:step": {"kind": "reader", "content_hash": "sha256:current",
                        "text": "For x != 0, x/x = 1.", "subject_ids": ["T1"],
                        "json_pointer": "/examples/0/steps/0/markdown"},
        "source:T1": {"kind": "source", "content_hash": "sha256:current",
                      "text": "For a nonzero x, x/x equals 1.", "subject_ids": ["T1"]},
    }


def verdict(**changes):
    row = {"criterion_id": "CR-source-T1", "version": 1, "status": "pass",
           "reason": "Both explanation and source retain the nonzero condition.",
           "evidence_refs": [
               {"evidence_id": "reader:step", "quote": "For x != 0, x/x = 1."},
               {"evidence_id": "source:T1", "quote": "For a nonzero x"},
           ]}
    row.update(changes)
    return row


def validate(items, response, catalog):
    from aiforstudy.blocks.content.review import validate_verdicts
    return validate_verdicts(items, response, catalog)


def test_current_specific_evidence_closes_frozen_criterion():
    result = validate([item()], {"verdicts": [verdict()]}, evidence())
    assert result["decision"] == "accepted"
    assert result["verdicts"][0]["status"] == "pass"


@pytest.mark.parametrize("response", [
    {"accepted": True, "reason": "Everything checked."},
    {"verdicts": []},
    {"verdicts": [verdict(), verdict()]},
    {"verdicts": [verdict(criterion_id="made-up")]},
    {"verdicts": [verdict(version=2)]},
    {"verdicts": [verdict(evidence_refs=[{"evidence_id": "reader:missing", "quote": "x/x = 1"}])]},
    {"verdicts": [verdict(evidence_refs=[{"evidence_id": "reader:step", "quote": []}])]},
])
def test_missing_fabricated_or_incomplete_evidence_cannot_approve(response):
    with pytest.raises(ContractError):
        validate([item()], response, evidence())


@pytest.mark.parametrize("change", [
    {"content_hash": "sha256:old"},
])
def test_real_but_stale_reader_quote_cannot_approve(change):
    catalog = evidence()
    catalog["reader:step"].update(change)
    with pytest.raises(ContractError):
        validate([item()], {"verdicts": [verdict()]}, catalog)


def test_reviewer_cannot_make_required_check_not_applicable():
    with pytest.raises(ContractError):
        validate([item()], {"verdicts": [verdict(status="not_applicable")]}, evidence())


@pytest.mark.parametrize("status", ["fail", "unknown", "not_run"])
def test_unresolved_required_check_never_approves(status):
    result = validate([item()], {"verdicts": [verdict(status=status)]}, evidence())
    assert result["decision"] == "needs_review"
    assert result["blocking_issues"][0]["criterion_id"] == "CR-source-T1"


def test_editorial_warning_does_not_become_blocking_rule():
    result = validate([item(required=False)], {"verdicts": [verdict(status="fail")]}, evidence())
    assert result["decision"] == "accepted"
    assert result["warnings"][0]["status"] == "fail"


def test_absent_content_is_grounded_by_source_and_host_absence_evidence():
    catalog = evidence()
    catalog["absence:T1"] = {
        "kind": "absence", "content_hash": "sha256:current", "subject_ids": ["T1"],
        "parent_pointer": "/teaching_plan/claims", "source_id": "T1",
    }
    row = verdict(status="fail", evidence_refs=[
        {"evidence_id": "source:T1", "quote": "For a nonzero x"},
        {"evidence_id": "absence:T1"},
    ])
    result = validate([item(required_evidence_kinds=["source", "absence"])],
                      {"verdicts": [row]}, catalog)
    assert result["blocking_issues"][0]["evidence_refs"][1]["evidence_id"] == "absence:T1"


def test_input_receipts_are_not_mutated_to_fill_missing_coverage():
    response = {"verdicts": [verdict()]}
    before = deepcopy(response)
    validate([item()], response, evidence())
    assert response == before


def source_bound_inputs():
    c = {"unit_id": "U", "teaching_plan": {
        "claims": [{"id": "K", "source_refs": [{"obligation_id": "SO1"}]}],
        "goals": [{"id": "G", "claim_refs": ["K"]}]},
        "teaching_evidence": [{"goal_id": "G", "claim_refs": ["K"],
                               "reader_pointer": "/body_markdown"}],
        "body_markdown": "For x != 0, x/x = 1."}
    doc = {"blocks": [{"block_id": "B", "json_pointer": "/body_markdown",
                       "visible_text": c["body_markdown"], "raw_text": c["body_markdown"]}]}
    ctx = {"source_text": "For a nonzero x, x/x equals 1.",
           "source_obligations": [{"id": "SO1", "source_excerpt": "For a nonzero x"}]}
    return c, doc, ctx


def test_evidence_catalog_preserves_independent_source_obligation_when_author_omits_it():
    from aiforstudy.blocks.content.review import build_evidence_catalog
    c, doc, ctx = source_bound_inputs()
    catalog = build_evidence_catalog(c, doc, ctx)
    assert {"K", "SO1", "G", "unit:U"} <= set(catalog["reader:B"]["subject_ids"])
    assert "absence:SO1" not in catalog
    c["teaching_plan"]["claims"] = []
    c["teaching_evidence"] = []
    catalog = build_evidence_catalog(c, doc, ctx)
    assert catalog["source:SO1"]["text"] == "For a nonzero x"
    assert catalog["absence:SO1"]["parent_pointer"] == "/teaching_plan/claims"
    assert "SO1" not in catalog["reader:B"]["subject_ids"]


def test_fabricated_source_inventory_is_not_accepted_as_review_evidence():
    from aiforstudy.blocks.content.review import build_evidence_catalog
    c, doc, ctx = source_bound_inputs()
    ctx["source_obligations"][0]["source_excerpt"] = "For every x including zero"
    with pytest.raises(ContractError):
        build_evidence_catalog(c, doc, ctx)


def test_derived_claim_source_evidence_resolves_frozen_dependency_chain():
    from aiforstudy.blocks.content.review import build_evidence_catalog
    c, doc, ctx = source_bound_inputs()
    c["teaching_plan"]["claims"].append({"id": "K2", "source_refs": [{"kind": "content", "claim_id": "K"}]})
    c["teaching_evidence"][0]["claim_refs"] = ["K", "K2"]
    catalog = build_evidence_catalog(c, doc, ctx)
    assert "K2" in catalog["source:SO1"]["subject_ids"]
    assert "K2" in catalog["reader:B"]["subject_ids"]


def test_cyclic_claim_dependency_cannot_manufacture_source_evidence():
    from aiforstudy.blocks.content.review import build_evidence_catalog
    c, doc, ctx = source_bound_inputs()
    c["teaching_plan"]["claims"][0]["source_refs"] = [{"kind": "content", "claim_id": "K"}]
    with pytest.raises(ContractError):
        build_evidence_catalog(c, doc, ctx)


def test_real_review_adapter_sends_frozen_criteria_and_checks_returned_quotes():
    import json
    from aiforstudy.blocks.content.review import review_content
    from aiforstudy.blocks.content.content_contracts import parse_content
    from aiforstudy.blocks.content.content_policy import load_content_policy, applicable_items
    from aiforstudy.blocks.content.reader_document import project_reader
    from test_teaching_policy_v2 import teaching_case
    c, ctx = teaching_case()
    c = parse_content(c).as_dict()
    doc = project_reader(c, ctx["reference_catalog"])
    policy = load_content_policy(ctx["learner_profile"])
    items = applicable_items(c, ctx, policy)

    class Client:
        def __init__(self): self.requests = []; self.instructions = []
        def chat_json(self, messages, **kw):
            self.instructions.append(messages[0]['content'])
            payload = json.loads(messages[1]["content"])
            request = payload["frozen_context"]
            self.requests.append(payload)
            rows = []
            for criterion in request["criteria"]:
                refs = []
                for kind in criterion["required_evidence_kinds"]:
                    eid, entry = next((key, val) for key, val in request["evidence_catalog"].items()
                        if val["kind"] == kind and criterion["subject_id"] in val["subject_ids"])
                    refs.append({"evidence_id": eid, "quote": entry["text"]})
                rows.append({"criterion_id": criterion["criterion_id"], "version": criterion["version"],
                             "status": "pass", "reason": "条件及零的边界案例在对应文字中完整。", "evidence_refs": refs})
            return {"data": {"verdicts": rows}}

    client = Client()
    result = review_content(c, doc, ctx, items, None, client)
    assert result["decision"] == "accepted"
    assert len(result["verdicts"]) == len(items)
    assert client.requests[0]["policy_digest"] == policy.digest
    payload = client.requests[0]
    assert payload['content_json_schema']['properties']['_meta']
    assert 'output_json_schema' not in payload  # reviewer does not rewrite author content
    assert '"verdicts"' in client.instructions[0]
    assert 'evidence_refs' in client.instructions[0]
    assert payload['frozen_context']['criteria']
    assert payload['frozen_context']['evidence_catalog']
