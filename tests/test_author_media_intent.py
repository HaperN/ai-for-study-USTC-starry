"""Author no-media judgements need current, bounded independent review."""
from copy import deepcopy
import importlib
import json

import pytest

from aiforstudy.errors import ContractError,Cancelled,BudgetExceeded
from aiforstudy.core.digest import digest_of
from aiforstudy.blocks.media.author_requirements import (
    bind_author_requirements, validate_author_artifact, repair_author_requirements,
)
from support.content_case import approved_content, author_media, none_decision


def module():
    assert importlib.util.find_spec('aiforstudy.blocks.media.intent_review'), 'missing required author-intent review'
    return importlib.import_module('aiforstudy.blocks.media.intent_review')


class Reviewer:
    def __init__(self, status='pass', malformed=False):
        self.status=status; self.malformed=malformed; self.calls=[]

    def chat_json(self, messages, **kwargs):
        payload=json.loads(messages[1]['content']); self.calls.append(payload)
        if self.malformed: return {'data':{'untrusted':'not verdicts'}}
        return {'data':{'verdicts':[dict(goal_id=g['goal_id'],status=self.status,
            reason='当前目标需要可观察的图示，文字替代不足。' if self.status=='fail' else '仅核对当前意图；未审成图。',
            reader_quote=g['explicit_reader_evidence'][0]['text'][:10], decision_quote=g['decision']['reason'][:10],
            **({'failure_kind':'media_decision','reader_pointer':g['explicit_reader_evidence'][0]['pointer']} if self.status=='fail' else {}))
            for g in payload['goals']]}}


def test_none_goal_semantics_change_invalidates_original_author_judgement():
    c,_=approved_content(); original=author_media(c)
    c['teaching_plan']['goals'][0]['observable_task']='在图中指出零与负数的位置。'
    changed=bind_author_requirements(original['requirements'],c,
        author_response_hash=original['author_response_hash'],previous=original,allow_stale=True)
    assert changed['status']=='stale'
    assert any(e['code']=='AUTHOR_MEDIA_DECISION_STALE' and e['goal_ids']==['G'] for e in changed['errors'])


def test_old_artifact_without_goal_binding_is_not_current():
    c,_=approved_content(); record=author_media(c); record.pop('goal_bindings',None)
    with pytest.raises(ContractError,match='目标|goal'):
        validate_author_artifact(record,c)


def test_explicit_none_review_sees_current_reason_and_replays_without_call(tmp_path):
    m=module(); c,_=approved_content(); record=author_media(c); client=Reviewer()
    receipt=m.review_author_intent(c,record,{'kinds':[]},client,journal=tmp_path/'review.json')
    assert receipt['review_outcome']['status']=='verified'
    assert receipt['verdicts'][0]['goal_id']=='G'
    assert client.calls[0]['goals'][0]['decision']['reason']==none_decision()['reason']
    assert client.calls[0]['goals'][0]['reader_text'] and client.calls[0]['goals'][0]['claims']
    again=Reviewer('fail')
    assert m.review_author_intent(c,record,{'kinds':[]},again,journal=tmp_path/'review.json')==receipt
    assert again.calls==[]
    m.validate_intent_review(receipt,c,record,{'kinds':[]})
    changed=deepcopy(record); changed['requirements']['decisions'][0]['reason']='改了媒体意图'
    with pytest.raises(ContractError):m.validate_intent_review(receipt,c,changed,{'kinds':[]})


def test_protocol_failure_is_finite_persisted_unverified_not_author_failure(tmp_path):
    """R3-1：内层纠正轮取消 —— 协议坏答复一次即判，反馈写进 journal。"""
    m=module(); c,_=approved_content(); record=author_media(c); client=Reviewer(malformed=True)
    journal=tmp_path/'review.json'
    receipt=m.review_author_intent(c,record,{},client,journal=journal)
    assert receipt['review_outcome']['status']=='unverified' and receipt['verdicts']==[]
    assert len(client.calls)==1
    feedback=json.loads(journal.read_text())['merged_feedback']
    assert feedback['allowed_paths']==['/verdicts']
    assert feedback['errors'][0]['path']=='/verdicts'
    assert feedback['errors'][0]['passing_criterion']
    again=Reviewer()
    assert m.review_author_intent(c,record,{},again,journal=journal)==receipt
    assert again.calls==[]


@pytest.mark.parametrize('damage',['missing','duplicate','unknown_status'])
def test_incomplete_or_forged_goal_verdict_cannot_certify_none(damage,tmp_path):
    m=module(); c,_=approved_content(); record=author_media(c)
    class Bad(Reviewer):
        def chat_json(self,*args,**kwargs):
            result=super().chat_json(*args,**kwargs); rows=result['data']['verdicts']
            if damage=='missing': rows.clear()
            if damage=='duplicate': rows.append(deepcopy(rows[0]))
            if damage=='unknown_status': rows[0]['status']='not_applicable'
            return result
    receipt=m.review_author_intent(c,record,{},Bad(),journal=tmp_path/'review.json')
    assert receipt['review_outcome']['status']=='unverified'


@pytest.mark.parametrize('status', ['pass', 'fail'])
@pytest.mark.parametrize('quotes', [{}, {'reader_quote': '', 'decision_quote': ''}])
def test_intent_reason_can_supply_evidence_without_duplicate_quote_fields(status, quotes):
    m = module(); c, _ = approved_content(); record = author_media(c)
    class Review:
        def chat_json(self, messages, **kwargs):
            goal = json.loads(messages[1]['content'])['goals'][0]
            row = dict(goal_id=goal['goal_id'], status=status,
                reason='正文解释了非负整数包含零；数轴配图可显示零与正整数的关系，当前 none 理由只说文字足够。', **quotes)
            if status == 'fail': row['failure_kind'] = 'media_decision'
            return {'data': {'verdicts': [row]}}
    receipt = m.review_author_intent(c, record, {}, Review())
    assert receipt['protocol_error'] == ''
    assert receipt['review_outcome']['status'] == ('verified' if status == 'pass' else 'known_error')


def test_reader_content_failure_still_requires_executable_location():
    m = module(); c, _ = approved_content(); record = author_media(c)
    class Review:
        def chat_json(self, messages, **kwargs):
            goal = json.loads(messages[1]['content'])['goals'][0]
            return {'data': {'verdicts': [dict(goal_id=goal['goal_id'], status='fail',
                reason='正文把非负范围误写成严格大于零，应包含零。', failure_kind='reader_content')]}}
    receipt = m.review_author_intent(c, record, {}, Review())
    assert receipt['review_outcome']['status'] == 'unverified'


@pytest.mark.parametrize('optional_quotes', [True, False])
def test_current_fidelity_failure_routes_author_none_to_real_required_request(tmp_path,monkeypatch,optional_quotes):
    from aiforstudy.pipeline import media_stages as stages
    from test_author_media_requirements import math_case
    from test_media_plan_stage import ctx
    c,approval,required,caps,_=math_case()
    required['requirements']['decisions'][0]['requirement']='required'
    c=deepcopy(c); c['teaching_plan']['goals'][0]['visual_requirement']=None
    record=author_media(c); original=deepcopy(record)
    monkeypatch.setattr(stages,'_load_content',lambda *_:(c,approval))
    monkeypatch.setattr(stages,'_load_author_media',lambda *a,**k:record)
    monkeypatch.setattr(stages,'_capabilities',lambda *_:caps)
    class IntentFailure(Reviewer):
        def chat_json(self, *args, **kwargs):
            response=super().chat_json(*args, **kwargs)
            if not optional_quotes:
                for row in response['data']['verdicts']:
                    for key in ('reader_quote','decision_quote','reader_pointer'): row.pop(key)
            return response
    monkeypatch.setattr(stages,'_client',lambda *a,**k:IntentFailure('fail'))
    result=stages.media_plan_stage(ctx(tmp_path,'media_plan'))
    assert result.repair_routes and result.repair_routes[0]['target_artifact_kind']=='author_media'
    payload=json.loads((tmp_path/'products/media_plan/U.json').read_text())['payload']
    ticket=payload['tickets'][0]
    assert ticket['target']['rule']=='AUTHOR_MEDIA_INTENT'
    assert ticket['allowed_paths']==['/decisions/0','/requests']
    assert record==original, 'review failure must not mutate the immutable author record'
    patch={'schema_version':'AuthorMediaPatchV1','base_author_media_hash':digest_of(record),
        'ticket_refs':[ticket['ticket_id']],'operations':[
            {'op':'replace','path':'/decisions/0','value':required['requirements']['decisions'][0]},
            {'op':'replace','path':'/requests','value':required['requirements']['requests']}]}
    updated=repair_author_requirements(record,patch,[ticket],c)
    assert updated['requirements']['decisions'][0]['requirement']=='required'
    assert len(updated['requirements']['requests'])==1
    assert updated['status']=='bound'
    with pytest.raises(ContractError):
        corrupt=deepcopy(ticket);corrupt['previous_attempt']['intent_review']['verdicts'][0]['status']='pass'
        repair_author_requirements(record,patch,[corrupt],c)


def test_none_review_receipt_required_before_reader_can_treat_empty_plan_as_complete():
    m=module(); from aiforstudy.blocks.render.readiness import validate_media_bindings
    from aiforstudy.blocks.media.media_planning import plan_media_requirements
    c,approval=approved_content(); record=author_media(c); caps={'kinds':[]}
    plan=plan_media_requirements(c,approval,author_requirements=record,capabilities=caps)
    with pytest.raises(ContractError,match='意图|intent'):
        validate_media_bindings(c,plan,{})
    plan['intent_review']=m.review_author_intent(c,record,caps,Reviewer())
    validate_media_bindings(c,plan,{})


def test_goal_review_failure_cannot_delete_required_request_or_weaken_original_criteria(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media_stages as stages
    from test_author_media_requirements import math_case
    from test_media_plan_stage import ctx
    c,approval,record,caps,_=math_case()
    record['requirements']['decisions'][0]['requirement']='required'
    caps={**caps,'code_generation':'GeneratedMediaCodeV1','kinds':['drawing']}
    monkeypatch.setattr(stages,'_load_content',lambda *_:(c,approval))
    monkeypatch.setattr(stages,'_load_author_media',lambda *a,**k:record)
    monkeypatch.setattr(stages,'_capabilities',lambda *_:caps)
    monkeypatch.setattr(stages,'_client',lambda *a,**k:Reviewer('fail'))
    result=stages.media_plan_stage(ctx(tmp_path,'media_plan'))
    assert result.repair_routes
    ticket=json.loads((tmp_path/'products/media_plan/U.json').read_text())['payload']['tickets'][0]
    base={'schema_version':'AuthorMediaPatchV1','base_author_media_hash':digest_of(record),'ticket_refs':[ticket['ticket_id']]}
    old=record['requirements']; changes=[('/requests',[]),('/decisions/0',none_decision())]
    weakened=deepcopy(old['requests']); weakened[0]['acceptance_criteria'][0]['statement']='只需有图'
    changes.append(('/requests',weakened))
    for path,value in changes:
        with pytest.raises(ContractError):
            repair_author_requirements(record,{**base,'operations':[dict(op='replace',path=path,value=value)]},[ticket],c)


def test_unknown_intent_survives_empty_media_and_interaction_stages(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media_stages as stages
    from test_media_plan_stage import ctx
    c,approval=approved_content(); record=author_media(c)
    monkeypatch.setattr(stages,'_load_content',lambda *_:(c,approval))
    monkeypatch.setattr(stages,'_load_author_media',lambda *a,**k:record)
    monkeypatch.setattr(stages,'_capabilities',lambda *_:{'kinds':[]})
    reviewer=Reviewer('unknown')
    monkeypatch.setattr(stages,'_client',lambda *a,**k:reviewer)
    for stage in ('media_plan','figures','audit','interaction'):
        result=getattr(stages,stage+'_stage')(ctx(tmp_path,stage))
        assert result.execution_state.value in {'succeeded','not_applicable'},result.error
        payload=json.loads((tmp_path/f'products/{stage}/U.json').read_text())['payload']
        assert payload['review_outcome']['status']=='unverified',stage
    assert len(reviewer.calls)==1


def test_unknown_receipt_survives_formal_empty_reader(tmp_path):
    from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
    from aiforstudy.core.jsonio import write_json_atomic
    from aiforstudy.blocks.content.review_outcome import outcome
    from support.content_case import approved_inputs,issue,no_media_requirements
    c,source,policy,doc,reports,math=inputs=approved_inputs()
    run=tmp_path/'run';write_json_atomic(run/'state.json',{'book_id':c['book_id']})
    def save(stage,value):write_json_atomic(run/f'work/products/{stage}/U.json',{'payload':value})
    save('lesson',{'content':c,'content_hash':digest_of(c)})
    approval=issue(inputs).as_dict()
    save('text_audit',{'content_hash':digest_of(c),'approval':approval,'source_context':source,
        'review_document':doc.as_dict(),'check_reports':[r.as_dict() if hasattr(r,'as_dict') else r for r in reports],
        'math_report':math,'review_outcome':outcome('verified')})
    plan=no_media_requirements(c,approval)
    plan['intent_review']=module().review_author_intent(c,plan['author_media'],plan['capabilities'],Reviewer('unknown'))
    save('media_plan',plan)
    save('figures',{'content_hash':digest_of(c),'requirements_hash':digest_of(plan),'requests':[]})
    snapshot=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader',require_complete_units=True)
    assert snapshot['review_outcome']['status']=='unverified'


def test_other_goal_failure_cannot_erase_prior_known_intent_gap(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media_stages as stages
    from test_media_plan_stage import ctx
    from aiforstudy.blocks.content.review_outcome import content_identity
    from support.content_case import reference_catalog
    c,approval=approved_content()
    c['teaching_plan']['goals'].append({**deepcopy(c['teaching_plan']['goals'][0]),'id':'G2'})
    c['teaching_evidence'].append({**deepcopy(c['teaching_evidence'][0]),'goal_id':'G2'})
    approval=content_identity(c,reference_catalog(),policy_digest=approval['policy_digest'])
    record=author_media(c)
    monkeypatch.setattr(stages,'_load_content',lambda *_:(c,approval))
    monkeypatch.setattr(stages,'_load_author_media',lambda *a,**k:record)
    monkeypatch.setattr(stages,'_capabilities',lambda *_:{'kinds':[]})
    class Mixed(Reviewer):
        def chat_json(self,*args,**kwargs):
            response=super().chat_json(*args,**kwargs)
            for row in response['data']['verdicts']:
                row['status']=verdicts[row['goal_id']]
                if row['status']=='fail':row.update(failure_kind='media_decision',reader_pointer='/body_markdown')
            return response
    monkeypatch.setattr(stages,'_client',lambda *a,**k:Mixed())
    for i,verdicts in enumerate(({'G':'fail','G2':'pass'}, {'G':'unknown','G2':'fail'}, {'G':'unknown','G2':'pass'})):
        record['requirements']['decisions'][0]['reason']+='已更新理由。'
        result=stages.media_plan_stage(ctx(tmp_path,'media_plan'))
        if i<2: assert result.repair_routes,result.error
    assert result.execution_state.value=='blocked', 'the old G deficiency still has no current pass'
    payload=json.loads((tmp_path/'products/media_plan/U.json').read_text())['payload']
    assert 'G' in payload['unresolved_intent'] and payload['tickets']==[]


@pytest.mark.parametrize('exception',[KeyboardInterrupt,Cancelled,BudgetExceeded])
def test_interrupted_review_reservation_is_never_resent(tmp_path,exception):
    from aiforstudy.core.jsonio import read_json
    c,_=approved_content();record=author_media(c)
    class Interrupt:
        def chat_json(self,*a,**k):raise exception('cancel')
    path=tmp_path/'review.json'
    with pytest.raises(exception):module().review_author_intent(c,record,{},Interrupt(),journal=path)
    assert read_json(path)['attempts'][0]['status']=='reserved'
    reviewer=Reviewer()
    result=module().review_author_intent(c,record,{},reviewer,journal=path)
    assert result['review_outcome']['status']=='unverified' and reviewer.calls==[]


def test_text_reaudit_preserves_body_history_without_dispatching_unconfirmed_failure(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from aiforstudy.pipeline import content_stages as stages,stage_io
    from aiforstudy.pipeline.diagnostics import technical_ticket
    from test_media_plan_stage import ctx
    from support.content_case import approved_inputs
    c,source,policy,doc,reports,math=approved_inputs();h=digest_of(c)
    def ticket(artifact,rule):
        return technical_ticket(unit_id='U',artifact_id=artifact,artifact_hash=h,
            problem='仍需当前独立复核',source=c['body_markdown'],owner='lesson',kind='text_content',
            allowed_paths=['/body_markdown'],rule=rule,action='核对该字段',expected='当前证据与标准一致')
    body=ticket('lesson:U','POST_MEDIA_CONTENT');author=ticket('author-media:U','AUTHOR_MEDIA_INTENT')
    monkeypatch.setattr(stages,'load_content',lambda *_:(c,{}))
    monkeypatch.setattr(stages,'_frozen_context',lambda *_:source)
    monkeypatch.setattr(stages,'_load_tickets',lambda *_:([body,author],{}))
    monkeypatch.setattr(stage_io,'_gateway_client',lambda *a,**k:(object(),'offline'))
    import aiforstudy.blocks.content.review as review
    captured={}
    def reviewer(*a,**kwargs):
        captured.update(kwargs['repair_context'])
        return SimpleNamespace(as_dict=lambda:deepcopy(reports[1]))
    monkeypatch.setattr(review,'review_content',reviewer)
    result=stages.text_audit_stage(ctx(tmp_path,'text_audit'))
    assert result.execution_state.value=='succeeded',result.error
    assert [t['ticket_id'] for t in captured['previous_tickets']]==[body.ticket_id]
    assert not result.repair_routes and not result.repair_requests
    payload=json.loads((tmp_path/'products/text_audit/U.json').read_text())['payload']
    assert body.ticket_id in {t['ticket_id'] for t in payload['tickets']}
    assert author.ticket_id not in {t['ticket_id'] for t in payload['tickets']}


@pytest.mark.parametrize('early',['stale','technical'])
def test_early_output_cannot_erase_prior_intent_failure(tmp_path,monkeypatch,early):
    from aiforstudy.pipeline import media_stages as stages
    from test_media_plan_stage import ctx
    c,approval=approved_content();record=author_media(c);reviewer=Reviewer('fail')
    monkeypatch.setattr(stages,'_load_content',lambda *_:(c,approval))
    monkeypatch.setattr(stages,'_load_author_media',lambda *a,**k:record)
    monkeypatch.setattr(stages,'_capabilities',lambda *_:{'kinds':[]})
    monkeypatch.setattr(stages,'_client',lambda *a,**k:reviewer)
    assert stages.media_plan_stage(ctx(tmp_path,'media_plan')).repair_routes
    old=deepcopy(record)
    with monkeypatch.context() as extra:
        if early=='stale':
            record.update(status='stale',errors=[{'code':'AUTHOR_MEDIA_DECISION_STALE','request_key':'goal:G',
                'goal_ids':['G'],'message':'须重新核对目标'}])
        else:
            def broken(*a,**k):raise stages.MediaPlanningError('技术输入异常',{})
            extra.setattr(stages,'plan_media_requirements',broken)
        stages.media_plan_stage(ctx(tmp_path,'media_plan'))
    assert len(reviewer.calls)==1
    record=old;record['requirements']['decisions'][0]['reason']+='重新解释。'
    reviewer=Reviewer('unknown')
    result=stages.media_plan_stage(ctx(tmp_path,'media_plan'))
    assert result.execution_state.value=='blocked'
    payload=json.loads((tmp_path/'products/media_plan/U.json').read_text())['payload']
    assert 'G' in payload['unresolved_intent']
