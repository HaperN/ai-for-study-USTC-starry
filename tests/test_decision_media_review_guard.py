"""D2/D5: unavailable review and genuine unknown buy no repair attempt."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from aiforstudy.blocks.media import validation
from aiforstudy.blocks.content.review_outcome import media_outcome, outcome
from aiforstudy.core.digest import digest_file, digest_of
from aiforstudy.errors import ContractError, ConfigError
from aiforstudy.pipeline import content_stages, media_stages as M
from aiforstudy.providers.review_failures import ReviewCallFailure
from test_media_validation_v2 import static_row
from test_media_plan_stage import ctx
from support.content_case import approved_inputs


FAILURES = [(TimeoutError('media timed out'),'timeout'),
    (ConnectionError('media connection refused'),'network'),
    (ConfigError('HTTP 400 max_tokens rejected'),'configuration'),
    (ReviewCallFailure('finish_reason=length',failure_kind='truncation'),'truncation')]


class Reviewer:
    def __init__(self,failure=None,status='needs_review',why='当前尚无法核实实际画面的教学含义。'):
        self.failure,self.status=failure,status
        self.why=why
        self.calls=0

    def chat_json(self,messages,*,tag,images):
        self.calls+=1
        if self.failure: raise self.failure
        request=json.loads(messages[-1]['content'])
        return {'data':{'verdicts':[{'criterion_id':i['criterion_id'],'version':i['version'],
            'status':self.status,'why':self.why,
            'evidence_ids':[] if self.status=='needs_review' else list(request['evidence_catalog'])}
            for i in request['review_items']]}}


def validation_case(tmp_path,monkeypatch,reviewer):
    request,row=static_row(tmp_path)
    def capture(path,*,profiles,**kwargs):
        return [{'profile_id':p['profile_id'],'image_path':str(path),'image_hash':digest_file(path),
            'viewport_width':p['width'],'media_width':p['content_width'],'overflow':False,'labels':[]}
            for p in profiles]
    monkeypatch.setattr(validation,'capture_display',capture)
    report=validation.validate_media(request,row,client=reviewer)
    row.update(validation=report,state='needs_review',request=request)
    content,source,*_=approved_inputs()
    context=ctx(tmp_path,'figures')
    monkeypatch.setattr(content_stages,'_frozen_context',lambda *_:source)
    monkeypatch.setattr(M,'_persisted_tickets',lambda *_a,**_k:{})
    return context,content,row


@pytest.mark.parametrize('failure,kind',FAILURES)
def test_media_validation_call_failure_keeps_nature_without_tickets(tmp_path,monkeypatch,failure,kind):
    context,content,row=validation_case(tmp_path,monkeypatch,Reviewer(failure))
    assert row['validation']['review_failure']['kind']==kind
    assert type(failure).__name__ in row['validation']['review_failure']['reason']
    assert not row['validation']['ok']
    assert media_outcome(row)['status']=='unverified'
    tickets,directives,routes=M._tickets(context,content,[row])
    assert not tickets and not directives and not routes


def test_media_missing_reviewer_is_unverified_and_never_protocol_repair(tmp_path,monkeypatch):
    context,content,row=validation_case(tmp_path,monkeypatch,None)
    assert row['validation']['review_failure']['kind']=='internal'
    assert media_outcome(row)['status']=='unverified'
    assert M._tickets(context,content,[row])==([],(),())


def test_media_unknown_with_empty_evidence_is_valid_unknown_without_repair(tmp_path,monkeypatch):
    context,content,row=validation_case(tmp_path,monkeypatch,Reviewer())
    assert not any(i['code'].startswith('MEDIA_REVIEW_') for i in row['validation']['issues'])
    assert media_outcome(row)['status']=='unverified'
    assert M._tickets(context,content,[row])==([],(),())


@pytest.mark.parametrize('failure',[None,*[item[0] for item in FAILURES]])
def test_media_unknown_or_call_failure_does_not_redispatch_old_fail(tmp_path,monkeypatch,failure):
    context,content,row=validation_case(tmp_path,monkeypatch,Reviewer(status='fail'))
    tickets,directives,_=M._tickets(context,content,[row])
    assert tickets and directives
    old=deepcopy(tickets)
    _,_,current=validation_case(tmp_path,monkeypatch,Reviewer(failure))
    current['repair_tickets']=old
    retained,requests,routes=M._tickets(context,content,[current])
    assert [t['ticket_id'] for t in retained]==[t['ticket_id'] for t in old]
    assert all(t['criteria']==o['criteria'] for t,o in zip(retained,old))
    assert not requests and not routes


def test_media_current_fail_reuses_old_identity_and_still_dispatches(tmp_path,monkeypatch):
    context,content,row=validation_case(tmp_path,monkeypatch,Reviewer(status='fail'))
    old,_,_=M._tickets(context,content,[row])
    row['repair_tickets']=old
    current,directives,_=M._tickets(context,content,[row])
    assert [t['ticket_id'] for t in current]==[t['ticket_id'] for t in old]
    assert {tid for directive in directives for tid in directive.ticket_ids}=={t['ticket_id'] for t in old}


def test_media_current_new_judgment_survives_frozen_original_ticket_in_signature(tmp_path,monkeypatch):
    context,content,row=validation_case(tmp_path,monkeypatch,Reviewer(status='fail',why='端点遮挡。'))
    old,first,_=M._tickets(context,content,[row])
    _,_,row=validation_case(tmp_path,monkeypatch,Reviewer(status='fail',why='端点已经清楚，但批准条件遗漏。'))
    row['repair_tickets']=old
    _,second,_=M._tickets(context,content,[row])
    assert first[0].ticket_ids==second[0].ticket_ids
    assert first[0].verdict_content!=second[0].verdict_content


def test_media_protocol_same_judgment_does_not_change_signature_with_pixel_identity(tmp_path,monkeypatch):
    from test_media_code_contracts import approved_case
    from test_media_review_routing import report_for
    from aiforstudy.pipeline.repair import RepairCoordinator
    from aiforstudy.pipeline.execution_control import ExecutionPolicy
    context,content,_=validation_case(tmp_path,monkeypatch,None)
    _,_,request,program=approved_case('animation')
    first=[]
    for version in ('first','second'):
        row={'request_id':request['request_id'],'kind':'animation','state':'needs_review',
            'request':request,'candidate_spec':program,'spec_hash':digest_of({'version':version}),
            'render_profile':'draft','code_product':{'schema_version':'GeneratedMediaProductV1'},
            'resources':[{'kind':'keyframe','relative_path':'frame.png','sha256':digest_of({'pixels':version})}]}
        row['validation']=report_for(row,semantic_fail=False)
        _,directives,_=M._tickets(context,content,[row])
        assert directives and all(d.kind=='evidence_format' for d in directives)
        first.append(directives)
    assert first[0][0].ticket_ids!=first[1][0].ticket_ids
    coordinator=RepairCoordinator(ExecutionPolicy())
    assert coordinator.signature(first[0])==coordinator.signature(first[1])


def test_actual_media_protocol_error_still_gets_evidence_repair(tmp_path,monkeypatch):
    context,content,row=validation_case(tmp_path,monkeypatch,Reviewer(ContractError('invalid review response')))
    assert row['validation']['review_failure']['kind']=='protocol'
    tickets,directives,_=M._tickets(context,content,[row])
    assert tickets and directives and all(d.kind=='evidence_format' for d in directives)


@pytest.mark.parametrize('failure',[None,TimeoutError('review still unavailable')])
def test_media_unknown_retains_prior_protocol_evidence_without_spending_more_budget(tmp_path,monkeypatch,failure):
    context,content,row=validation_case(tmp_path,monkeypatch,Reviewer(ContractError('invalid review response')))
    old,_,_=M._tickets(context,content,[row])
    _,_,current=validation_case(tmp_path,monkeypatch,Reviewer(failure))
    current['repair_tickets']=old
    retained,directives,routes=M._tickets(context,content,[current])
    assert [t['ticket_id'] for t in retained]==[t['ticket_id'] for t in old]
    assert not directives and not routes


@pytest.mark.parametrize('failure_kind',[None,'timeout','network','configuration','truncation'])
def test_unverified_media_review_stops_paid_code_candidate_loop(tmp_path,monkeypatch,failure_kind):
    from aiforstudy.blocks.media import code_execution
    from test_media_review_routing import prepare,report_for
    content,approval,request,calls,_=prepare(monkeypatch,mode='protocol')
    def review(request,row,**kwargs):
        calls['review']+=1
        if failure_kind:
            return {'ok':False,'decision':'needs_review','verdicts':[],
                'review_failure':{'kind':failure_kind,'reason':'review unavailable'},
                'issues':[{'code':'MEDIA_CALL_FAILED','message':'review unavailable'}]}
        report=report_for(row,repaired=True)
        for verdict in report['verdicts']:
            verdict.update(status='needs_review',evidence_ids=[])
        report.update(validation.validate_verdicts(report['review_items'],
            {'verdicts':report['verdicts']},report['evidence_catalog']),decision='needs_review')
        return report
    monkeypatch.setattr(validation,'validate_media',review)
    row=code_execution.execute_code(request,content=content,approval=approval,out_dir=tmp_path,
        client=object(),reviewer=object())
    assert row['state']=='needs_review'
    assert calls=={'generate':1,'render':['draft'],'review':1}
    assert len(row['code_attempts'])==1
    repeated=code_execution.execute_code(request,content=content,approval=approval,out_dir=tmp_path,
        client=object(),reviewer=object(),previous=row)
    assert repeated==row and calls=={'generate':1,'render':['draft'],'review':1}


@pytest.mark.parametrize('failure,kind',FAILURES)
def test_figures_stage_caught_review_failure_does_not_fabricate_protocol_ticket(tmp_path,monkeypatch,failure,kind):
    request,old=static_row(tmp_path)
    content,source,*_=approved_inputs()
    context=ctx(tmp_path,'figures')
    plan={'requests':[request],'decisions':[],'content_hash':digest_of(content)}
    approval={'reader_document_hash':'reader'}
    monkeypatch.setattr(M,'_load_content',lambda *_:(content,approval))
    monkeypatch.setattr(M,'_load',lambda *_a,**_k:plan)
    monkeypatch.setattr(M,'validate_requirements',lambda *_a,**_k:None)
    monkeypatch.setattr(M,'_capabilities',lambda *_:{'source_assets':{}})
    monkeypatch.setattr(M,'_client',lambda *_a,**_k:object())
    monkeypatch.setattr(M,'resource_dependencies',lambda *_a,**_k:{'generation_dependency_hash':'generation','review_dependency_hash':'review'})
    monkeypatch.setattr(M,'_intact_media_resources',lambda *_:True)
    monkeypatch.setattr(M,'_persisted_tickets',lambda *_a,**_k:{})
    monkeypatch.setattr(M,'_plan_outcome',lambda *_:outcome('verified'))
    monkeypatch.setattr(content_stages,'_frozen_context',lambda *_:source)
    def fail(*_a,**_k):raise failure
    monkeypatch.setattr(M,'execute_static',fail)
    path=tmp_path/'products/figures/U.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'payload':{'requests':[old]}}))
    result=M.figures_stage(context)
    assert result.execution_state.value=='succeeded',result.error
    payload=json.loads(path.read_text())['payload']
    row=payload['requests'][0]
    assert row['validation']['review_failure']['kind']==kind
    assert row['review_outcome']['status']=='unverified'
    assert row['validation']['result_hash']==validation.result_digest(row)
    assert M._pending_protocol_review(row), 'frozen evidence recovery must retain failed-call identity'
    assert not payload['tickets'] and not result.repair_requests and not result.repair_routes
