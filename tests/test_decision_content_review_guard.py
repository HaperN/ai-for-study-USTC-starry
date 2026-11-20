"""D0/D2/D5 regressions discovered by the independent content review."""
from copy import deepcopy
import json

import pytest

from aiforstudy.blocks.content.content_contracts import JsonRecord, content_digest
from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.errors import ContractError, ConfigError
from aiforstudy.providers.review_failures import ReviewCallFailure
from aiforstudy.pipeline import content_stages as C, stage_io
from aiforstudy.pipeline.repair import RepairCoordinator, UnitRepairState, StopDecision
from aiforstudy.pipeline.execution_control import ExecutionPolicy
from support.content_case import approved_inputs, teaching_case, authoring
from test_feedback import ticket_case
from test_generation_corrections import SequenceClient
from test_media_plan_stage import ctx


class Reviewer:
    def __init__(self, failure=None, failed_rule=None):
        self.failure, self.failed_rule = failure, failed_rule
        self.calls = 0

    def chat_json(self, messages, **kwargs):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        request = json.loads(messages[-1]['content'])
        request = request.get('frozen_context', request)
        verdicts = []
        for item in request['criteria']:
            failed = item['rule_id'] == self.failed_rule
            refs = []
            if failed:
                for kind in item['required_evidence_kinds']:
                    eid, entry = next((eid, entry) for eid, entry in request['evidence_catalog'].items()
                        if entry['kind'] == kind and item['subject_id'] in entry['subject_ids'])
                    refs.append({'evidence_id':eid, 'quote':entry['text']})
            verdicts.append({'criterion_id':item['criterion_id'], 'version':item['version'],
                'status':'fail' if failed else 'unknown', 'reason':'当前来源结论仍遗漏' if failed else '当前修订尚无法独立核实',
                'evidence_refs':refs})
        return {'data':{'verdicts':verdicts}}


def audit(tmp_path, monkeypatch, *, previous=(), reviewer=None, math=None, changed=True):
    content, source, *_ = approved_inputs()
    if changed:
        content['body_markdown'] += '\n修订说明：非负整数包括零。'
    context = ctx(tmp_path, 'text_audit')
    monkeypatch.setattr(C, 'load_content', lambda *_a, **_k:(content,{}))
    monkeypatch.setattr(C, '_frozen_context', lambda *_:source)
    monkeypatch.setattr(C, '_load_tickets', lambda *_:(list(previous),{}))
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *_a, **_k:(reviewer,'controlled'))
    if math is not None:
        monkeypatch.setattr('aiforstudy.blocks.content.math_checks.check_content_math',
                            lambda *_a, **_k:JsonRecord(math))
    result = C.text_audit_stage(context)
    assert result.execution_state.value == 'succeeded', result.error
    payload = json.loads((tmp_path/'products/text_audit/U.json').read_text())['payload']
    return result, payload


@pytest.mark.parametrize('failure', [None, TimeoutError('review timed out'),
    ConfigError('HTTP 400: max_tokens rejected'),
    ReviewCallFailure('capacity_failure: finish_reason=length',failure_kind='truncation')])
def test_new_version_unknown_or_call_failure_does_not_redispatch_old_content_fail(tmp_path, monkeypatch, failure):
    _,_,_,old = ticket_case()
    result, payload = audit(tmp_path, monkeypatch, previous=[old], reviewer=Reviewer(failure))
    assert old.target['artifact_hash'] != payload['content_hash']
    assert payload['review_outcome']['status'] == 'unverified'
    assert bool(result.repair_requests) == (failure is None)
    retained = next(t for t in payload['tickets'] if t['ticket_id']==old.ticket_id)
    assert retained['ticket_id'] == old.ticket_id
    assert retained['criteria'] == old.as_dict()['criteria']
    assert retained['source_evidence'] == old.as_dict()['source_evidence']
    assert not payload['closed_ticket_ids'] and not payload['approval']
    state = UnitRepairState('U',content_rounds_by_artifact={'lesson:U':1})
    if failure is not None:
        decision = RepairCoordinator(ExecutionPolicy()).plan(list(result.repair_requests),state)
        assert isinstance(decision, StopDecision) and decision.stopped_by == 'no_directives'
    assert state.content_rounds_by_artifact == {'lesson:U':1}


def test_current_valid_fail_still_dispatches_when_other_criteria_are_unknown(tmp_path, monkeypatch):
    _,_,_,old = ticket_case()
    result, payload = audit(tmp_path,monkeypatch,previous=[old],reviewer=Reviewer(failed_rule='SOURCE-FIDELITY'))
    assert payload['review_outcome']['status'] == 'known_error'
    # R2-2 之后机器判据也进复核，指令条数随判据数增减；
    # 性质不变：**当前有效失败**的那个工单必须被排进返修，且都归 lesson。
    assert {d.owner for d in result.repair_requests} == {'lesson'}
    assert old.ticket_id in {tid for d in result.repair_requests for tid in d.ticket_ids}
    assert not payload['approval']


@pytest.mark.parametrize('reason_code,kind', [('timeout','timeout'), ('resource_failure','internal'),
    ('cas_failure','internal'), ('worker_protocol_error','internal')])
def test_cas_execution_failure_keeps_specific_nature_without_protocol_ticket(tmp_path,monkeypatch,reason_code,kind):
    reviewer = Reviewer()
    math = {'status':'unknown','checks':[{'status':'unknown','claim_id':'K','model_id':'M',
        'relation_id':'R','reason_code':reason_code,'reason':'CAS execution did not complete'}]}
    result,payload = audit(tmp_path,monkeypatch,reviewer=reviewer,math=math)
    assert payload['review_outcome']['status'] == 'unverified'
    assert payload['review_failure']['kind'] == kind
    assert payload['review_failure']['reason_code'] == reason_code
    assert payload['math_report']['checks'][0]['reason_code'] == reason_code
    assert not result.repair_requests and not result.repair_routes
    assert not payload['tickets'] and not payload['approval']
    assert reviewer.calls == 0, 'unexecuted mathematics cannot be approved by reviewer fallback'


def test_missing_transport_is_capability_failure_without_protocol_ticket(tmp_path,monkeypatch):
    result,payload = audit(tmp_path,monkeypatch,reviewer=None)
    assert payload['review_outcome']['status'] == 'unverified'
    assert payload['review_failure']['kind'] == 'internal'
    assert 'missing content review transport' in payload['review_failure']['reason']
    assert not result.repair_requests and not payload['tickets'] and not payload['approval']






def test_same_version_unknown_keeps_confirmed_history_and_can_repair(tmp_path,monkeypatch):
    _,_,_,old = ticket_case()
    from dataclasses import replace
    current,*_ = approved_inputs()
    old = replace(old,target={**old.target,'artifact_hash':content_digest(current)})
    result,payload = audit(tmp_path,monkeypatch,previous=[old],reviewer=Reviewer(),changed=False)
    assert old.target['artifact_hash'] == payload['content_hash']
    assert payload['review_outcome']['status'] == 'known_error'
    assert old.ticket_id in {t['ticket_id'] for t in payload['tickets']}
    assert result.repair_requests and result.repair_routes
    assert not payload['approval'] and not payload['closed_ticket_ids']


def test_unknown_does_not_redispatch_historical_protocol_ticket(tmp_path,monkeypatch):
    from aiforstudy.pipeline.diagnostics import technical_ticket
    from dataclasses import replace
    content,source,*_ = approved_inputs()
    ticket = technical_ticket(unit_id='U',artifact_id='lesson:U',artifact_hash=content_digest(content),
        problem='previous response missed criterion',source=source['source_text'],owner='reviewer',
        kind='evidence_format',allowed_paths=('/review/verdicts',),action='complete reviewer response',
        expected='every criterion receives a current valid verdict',rule='CONTENT-REVIEW-PROTOCOL')
    ticket = replace(ticket,previous_attempt={'protocol':True})
    result,payload = audit(tmp_path,monkeypatch,previous=[ticket],reviewer=Reviewer())
    assert payload['review_outcome']['status'] == 'unverified'
    assert ticket.ticket_id in {t['ticket_id'] for t in payload['tickets']}
    assert ticket.ticket_id not in {tid for d in result.repair_requests for tid in d.ticket_ids}
    assert not payload['closed_ticket_ids']


@pytest.mark.parametrize('failed_rule', [None, 'POST_MEDIA_CONTENT'])
def test_external_media_content_ticket_requires_current_valid_fail_to_dispatch(tmp_path,monkeypatch,failed_rule):
    from test_content_repair_closure import external_ticket
    old = external_ticket()
    result,payload = audit(tmp_path,monkeypatch,previous=[old],reviewer=Reviewer(failed_rule=failed_rule))
    assert payload['review_outcome']['status'] == ('known_error' if failed_rule else 'unverified')
    dispatched={tid for d in result.repair_requests for tid in d.ticket_ids}
    assert (old.ticket_id in dispatched) == bool(failed_rule)
    retained=next(t for t in payload['tickets'] if t['ticket_id']==old.ticket_id)
    assert retained['criteria'] == old.as_dict()['criteria']
    assert not payload['approval'] and not payload['closed_ticket_ids']


def test_author_correction_can_add_criterion_without_rewriting_existing_identity(tmp_path):
    from test_media_decisions_v2 import math_request_case
    from aiforstudy.blocks.content.generation import generate_content
    content,_,raw,_ = math_request_case()
    bad = authoring(content,raw)
    bad['content']['body_markdown'] = 123
    good = deepcopy(bad)
    good['content']['body_markdown'] = content['body_markdown']
    criteria = good['media_requirements']['requests'][0]['acceptance_criteria']
    additional = {**deepcopy(criteria[0]), 'criterion_id':'additional-criterion'}
    criteria.append(additional)
    # R3-1：坏正文一次即判，反馈写进 journal；合法答复另起一次调用通过。
    journal = tmp_path/'generation.json'
    client = SequenceClient(bad)
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(teaching_case()[1],load_content_policy(),client,correction_journal=journal)
    assert len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    assert feedback['allowed_paths'] == ['/content/body_markdown']
    result = generate_content(teaching_case()[1],load_content_policy(),SequenceClient(good))
    actual = result['media_requirements']['requests'][0]['acceptance_criteria']
    assert actual[0]['criterion_id'] == 'zero-visible'
    assert actual[-1]['criterion_id'] == 'additional-criterion'


def test_repeated_unknown_on_revised_version_does_not_relabel_history_as_current_fail(tmp_path,monkeypatch):
    from aiforstudy.blocks.audit.contracts import validate_ticket
    _,_,_,old = ticket_case()
    _,first = audit(tmp_path,monkeypatch,previous=[old],reviewer=Reviewer())
    retained = validate_ticket(next(t for t in first['tickets'] if t['ticket_id']==old.ticket_id))
    result,second = audit(tmp_path,monkeypatch,previous=[retained],reviewer=Reviewer())
    assert second['content_hash'] == first['content_hash']
    assert second['review_outcome']['status'] == 'unverified'
    assert result.repair_requests and not second['closed_ticket_ids']
