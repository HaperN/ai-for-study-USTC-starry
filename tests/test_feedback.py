"""Current stable, evidence-bound tickets replace the retired free-text feedback loop."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from aiforstudy.blocks.audit.contracts import validate_ticket
from aiforstudy.blocks.content.content_contracts import content_digest
from aiforstudy.blocks.content.content_policy import applicable_items, load_content_policy
from aiforstudy.blocks.content.reader_document import project_reader
from aiforstudy.blocks.content.review import build_evidence_catalog, validate_verdicts
from aiforstudy.blocks.content.repair import finding_ticket, reconcile_tickets
from aiforstudy.errors import ContractError
from test_teaching_policy_v2 import teaching_case


def review_case():
    c, source = teaching_case()
    policy = load_content_policy(source['learner_profile'])
    doc = project_reader(c, source['reference_catalog'])
    catalog = build_evidence_catalog(c, doc, source)
    criterion = next(row for row in applicable_items(c, source, policy) if row['rule_id'] == 'SOURCE-FIDELITY')
    refs = []
    for kind in criterion['required_evidence_kinds'] or ['reader']:
        eid, entry = next((eid, entry) for eid, entry in catalog.items()
                          if entry['kind'] == kind and criterion['subject_id'] in entry['subject_ids'])
        refs.append({'evidence_id':eid,'quote':entry['text']})
    verdict = {'criterion_id':criterion['criterion_id'],'version':criterion['version'],'status':'fail',
        'reason':'读者需要明确零属于非负整数；保留该条件。','evidence_refs':refs,
        'repair_instructions':{'action':'明确包含零'}}
    return c, source, policy, criterion, catalog, verdict


def ticket_case(**changes):
    c, source, policy, criterion, catalog, verdict = review_case()
    verdict.update(changes)
    report = validate_verdicts([criterion], {'verdicts':[verdict]}, catalog)
    ticket = finding_ticket(c, source, policy, report.blocking_issues[0], catalog)
    return c, source, policy, ticket


def test_validated_feedback_becomes_complete_scoped_ticket():
    c, source, _, ticket = ticket_case()
    assert validate_ticket(ticket).ticket_id == ticket.ticket_id
    assert ticket.status == 'ready' and not ticket.missing_answers
    assert set(ticket.allowed_paths) == {'/body_markdown',
        '/teaching_plan/claims/0/conditions', '/teaching_plan/claims/0/conclusion',
        '/teaching_plan/claims/0/symbols'}
    assert ticket.repair_instructions['action'] == '明确包含零'
    assert ticket.target['artifact_hash'] == content_digest(c)
    assert all(span.quote == source['source_text'][span.start:span.end] for span in ticket.source_evidence)


@pytest.mark.parametrize('change', ['reason', 'quote-length', 'quote-order'])
def test_same_frozen_issue_keeps_identity_across_diagnosis_wording(change):
    c, source, policy, criterion, catalog, verdict = review_case()
    first = finding_ticket(c, source, policy, validate_verdicts([criterion],{'verdicts':[verdict]},catalog).blocking_issues[0],catalog)
    if change == 'reason': verdict['reason'] = '重新表述同一个缺少边界解释的问题。'
    if change == 'quote-length':
        for ref in verdict['evidence_refs']: ref['quote'] = '非负整数'
    if change == 'quote-order': verdict['evidence_refs'].reverse()
    second = finding_ticket(c, source, policy, validate_verdicts([criterion],{'verdicts':[verdict]},catalog).blocking_issues[0],catalog)
    assert first.ticket_id == second.ticket_id


@pytest.mark.parametrize('damage', ['missing', 'foreign', 'duplicate', 'no-reason', 'wrong-quote-type', 'old-version'])
def test_invalid_feedback_cannot_spawn_ticket_authority(damage):
    _, _, _, criterion, catalog, verdict = review_case()
    rows = [verdict]
    if damage == 'missing': rows = []
    if damage == 'foreign': verdict['criterion_id'] = 'foreign'
    if damage == 'duplicate': rows = [verdict, deepcopy(verdict)]
    if damage == 'no-reason': verdict['reason'] = ''
    if damage == 'wrong-quote-type': verdict['evidence_refs'][0]['quote'] = []
    if damage == 'old-version': verdict['version'] += 1
    with pytest.raises(ContractError):
        validate_verdicts([criterion], {'verdicts':rows}, catalog)


def test_rediscovery_preserves_frozen_criteria_severity_and_history():
    c, source, policy, old = ticket_case()
    old = replace(old, history=({'event':'repair_dispatched','attempt':2},))
    c['body_markdown'] += '新的修订。'
    _, _, _, item, catalog, verdict = review_case()
    row = {**verdict, 'rule_id':item['rule_id'], 'subject_id':item['subject_id'], 'expected':'降低要求'}
    current = finding_ticket(c, source, policy, row, catalog)
    result, closed = reconcile_tickets([current], [old], [], content_hash=content_digest(c))
    assert closed == [] and len(result) == 1
    assert result[0].ticket_id == old.ticket_id and result[0].criteria == old.criteria
    assert result[0].severity == old.severity and result[0].history == old.history
    assert result[0].target['artifact_hash'] == content_digest(c)


@pytest.mark.parametrize('verdicts', [[], [{'criterion_id':'foreign','version':2,'status':'pass'}],
    [{'criterion_id':'SOURCE-FIDELITY:SO','version':1,'status':'pass'}],
    [{'criterion_id':'SOURCE-FIDELITY:SO','version':2,'status':'unknown'}],
    [{'criterion_id':'SOURCE-FIDELITY:SO','version':2,'status':'not_run'}],
    [{'criterion_id':'SOURCE-FIDELITY:SO','version':2,'status':'fail'}]])
def test_disappearing_sentence_or_noncurrent_verdict_does_not_close_ticket(verdicts):
    c, _, _, ticket = ticket_case()
    c['body_markdown'] = '完全改写后的另一句话。'
    h = content_digest(c)
    pending, closed = reconcile_tickets([], [ticket], verdicts, content_hash=h)
    assert closed == [] and [t.ticket_id for t in pending] == [ticket.ticket_id]
    assert pending[0].target['artifact_hash'] == h
    assert pending[0].criteria == ticket.criteria


def test_only_matching_current_frozen_verdict_closes_ticket():
    c, _, _, ticket = ticket_case()
    verdict = {'criterion_id':ticket.criteria[0].criterion_id,'version':ticket.criteria[0].version,'status':'pass'}
    pending, closed = reconcile_tickets([], [ticket], [verdict], content_hash=content_digest(c))
    assert pending == [] and closed == [ticket.ticket_id]


def test_rediscovered_issue_deduplicates_without_erasing_unrelated_ticket():
    c, _, _, first = ticket_case()
    second = replace(first, ticket_id='other-ticket', previous_attempt={'criterion_id':'different','deterministic':False})
    pending, closed = reconcile_tickets([first, first], [first, second], [], content_hash=content_digest(c))
    assert {t.ticket_id for t in pending} == {first.ticket_id, second.ticket_id}
    assert closed == []


def test_retired_ticket_reconcile_is_refusal_even_with_dry_run(tmp_path, capsys):
    path = Path(__file__).resolve().parents[1]/'tools/reconcile_tickets.py'
    spec = importlib.util.spec_from_file_location('retired_ticket_reconcile',path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    sentinel = tmp_path/'tickets.json'; sentinel.write_text('{"open":["T"]}',encoding='utf-8')
    before = sentinel.read_bytes()
    assert module.main(['--audit',str(tmp_path),'--dry-run']) == 2
    assert '已退役' in capsys.readouterr().err
    assert sentinel.read_bytes() == before and list(tmp_path.iterdir()) == [sentinel]
