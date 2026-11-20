"""Source image repairs use registered alternatives and keep original obligations.

Only semantic model transport is bounded here. Provenance, request validation,
browser screenshots, evidence validation, routing, and ticket persistence are real.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from aiforstudy.core.digest import digest_of
from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.pipeline import media_stages as M
from aiforstudy.blocks.media.media_planning import plan_media_requirements
from test_media_decisions_v2 import math_request_case, Planner
from test_media_plan_stage import ctx
from support.content_case import teaching_case, author_media, technical_plan, reviewed_plan, IntentReviewer


class Reviewer:
    def __init__(self, fail='host.media.readability'):
        self.fail=fail; self.seen=[]; self.bad_version=None

    def chat_json(self,messages,*,tag,images):
        assert images
        for image in images:
            with Image.open(image) as frame: frame.verify()
        data=json.loads(messages[-1]['content']); self.seen.append(data)
        return {'data':{'verdicts':[{'criterion_id':item['criterion_id'],
            'version':'999' if item['criterion_id']==self.bad_version else item['version'],
            'status':'fail' if item['criterion_id']==self.fail else 'pass',
            'why':'bounded transport: verify original standard against current real display images',
            'evidence_ids':list(data['evidence_catalog'])} for item in data['review_items']]}}


def failed_source(tmp_path,monkeypatch):
    from aiforstudy.pipeline import content_stages
    context=ctx(tmp_path,'figures')
    for identity,color in [('IMG1','blue'),('IMG2','green')]:
        image=Image.new('RGB',(300,180),'white')
        ImageDraw.Draw(image).rectangle((40,30,250,140),fill=color)
        image.save(context.context.parse/(identity+'.png'))
    write_json_atomic(context.context.parse/'images.json',{'images':[
        {'id':identity,'file':identity+'.png','caption':'source zero'} for identity in ('IMG1','IMG2')]})
    content,approval,raw,caps=math_request_case('source_image')
    caps['source_assets']=M._source_assets(context)
    request=raw['requests'][0]
    request.update(source_image_id='IMG1',math_model_refs=[],object_refs=[],claim_ids=[])
    request['acceptance_criteria'][0].update(kind='source',
        evidence_required=['source_provenance','rendered_frame'])
    request['source_refs'].append({'kind':'asset',**{
        k:caps['source_assets']['source_images'][0][k] for k in ('asset_id','hash')}})
    raw['decisions'][0]['requirement']='required'
    request['constraints']={'conditions':['retain approved zero convention']}
    plan=reviewed_plan(content,plan_media_requirements(content,approval,capabilities=caps,
        client=Planner(raw),author_requirements=author_media(content,raw)))
    assert plan['requests'][0]['constraints']['conditions']==['retain approved zero convention']
    write_json_atomic(M._path(context,'media_plan'),{'payload':plan})
    reviewer=Reviewer()
    monkeypatch.setattr(M,'_load_content',lambda c:(content,approval))
    monkeypatch.setattr(M,'_load_author_media',lambda *args,**kwargs:author_media(content,raw))
    monkeypatch.setattr(M,'_capabilities',lambda c:caps)
    monkeypatch.setattr(M,'_client',lambda c,role,bucket:reviewer)
    monkeypatch.setattr(content_stages,'_frozen_context',lambda c:teaching_case()[1])
    result=M.figures_stage(context)
    assert result.quality_state.value=='needs_review',result.error
    manifest=read_json(M._path(context,'figures'))['payload']
    assert manifest['requests'][0]['resources']
    assert len(manifest['tickets'])==1
    return context,content,approval,raw,caps,plan,manifest,result,reviewer


def plan_repair(context,manifest,raw,monkeypatch,*,ticket_source='figures',technical_response=None):
    ticket=manifest['tickets'][0]
    if ticket_source=='audit':
        write_json_atomic(M._path(context,'audit'),{'payload':{'tickets':[ticket]}})
        saved=deepcopy(manifest);saved['tickets']=[]
        write_json_atomic(M._path(context,'figures'),{'payload':saved})
    context.node.stage='media_plan'
    context.repair_batch=SimpleNamespace(jobs=[SimpleNamespace(
        ticket_ids=[ticket['ticket_id']],artifact_id=ticket['target']['artifact_id'],
        kind=ticket['kind'],owner=ticket['owner'])])
    intent_reviewer=IntentReviewer()
    class Capture(Planner):
        def chat_json(self,messages,**kwargs):
            if kwargs.get('tag','').startswith('author-media-intent:'):
                return intent_reviewer.chat_json(messages,**kwargs)
            self.messages=deepcopy(messages)
            if technical_response is not None:
                self.calls+=1
                return {'data':deepcopy(technical_response)}
            return super().chat_json(messages,**kwargs)
    planner=Capture(raw)
    monkeypatch.setattr(M,'_client',lambda *a,**k:planner)
    return planner,M.media_plan_stage(context)


def replacement(raw,caps):
    data=deepcopy(raw);request=data['requests'][0]
    asset=caps['source_assets']['source_images'][1]
    request['source_image_id']=asset['asset_id']
    request['source_refs'][-1]={'kind':'asset','asset_id':asset['asset_id'],'hash':asset['hash']}
    return data


def test_real_source_visual_failure_routes_to_requirements_but_evidence_stays_review(tmp_path,monkeypatch):
    context,content,_,_,_,_,manifest,result,_=failed_source(tmp_path,monkeypatch)
    assert result.repair_routes[0]['target_artifact_kind']=='media_requirements'
    ticket=manifest['tickets'][0]
    assert ticket['previous_attempt']['source_image_replan'] is True
    assert ticket['allowed_paths']==['/requests']
    row=deepcopy(manifest['requests'][0]);row['repair_tickets']=[]
    row['validation']={'issues':[{'code':'MEDIA_REVIEW_EVIDENCE','message':'missing actual mobile frame citation'}]}
    tickets,_,routes=M._tickets(context,content,[row])
    assert routes[0]['target_artifact_kind']=='media_evidence'
    assert tickets[0]['kind']=='evidence_format' and tickets[0]['allowed_paths']==['/review']


@pytest.mark.parametrize('ticket_source',['figures','audit'])
def test_source_replan_reads_selected_persisted_original_then_revalidates_pixels(tmp_path,monkeypatch,ticket_source):
    context,content,approval,raw,caps,plan,manifest,_,reviewer=failed_source(tmp_path,monkeypatch)
    changed=replacement(raw,caps)
    planner,result=plan_repair(context,manifest,changed,monkeypatch,ticket_source=ticket_source)
    assert result.quality_state.value=='accepted',result.error
    repair=json.loads(planner.messages[-1]['content'])['repair_context']
    assert repair['tickets']==manifest['tickets']
    assert repair['previous_requirements']==plan
    current=read_json(M._path(context,'media_plan'))['payload']
    assert current['requests'][0]['request_id']==plan['requests'][0]['request_id']
    assert current['requests'][0]['acceptance_criteria']==plan['requests'][0]['acceptance_criteria']
    assert current['requests'][0]['constraints']['conditions']==['retain approved zero convention']
    context.node.stage='figures';context.repair_batch=None
    reviewer.fail=None;monkeypatch.setattr(M,'_client',lambda *a,**k:reviewer)
    accepted=M.figures_stage(context)
    assert accepted.quality_state.value=='accepted',accepted.error
    after=read_json(M._path(context,'figures'))['payload']
    row=after['requests'][0]
    assert row['source_provenance']['asset_id']=='IMG2'
    assert row['resources']!=manifest['requests'][0]['resources']
    assert row['repair_tickets']==manifest['tickets']
    assert any(i.get('ticket_id')==manifest['tickets'][0]['ticket_id'] for i in reviewer.seen[-1]['review_items'])
    assert not after['tickets'] and not accepted.repair_routes


@pytest.mark.parametrize('damage',['required','constraints','source','criteria','identity'])
def test_source_replan_cannot_weaken_original_requirements(tmp_path,monkeypatch,damage):
    context,_,_,raw,caps,plan,manifest,_,_=failed_source(tmp_path,monkeypatch)
    changed=replacement(raw,caps)
    response=technical_plan(changed)
    if damage=='required': response['decisions']=[{**changed['decisions'][0],'requirement':'recommended'}]
    if damage=='constraints': response['requests'][0]['constraints']={}
    if damage=='source': response['requests'][0]['source_refs'][0]['hash']='forged'
    if damage=='criteria': response['requests'][0]['acceptance_criteria']=[{'statement':'anything visible'}]
    if damage=='identity': response['requests'][0]['request_key']='new-key'
    _,result=plan_repair(context,manifest,changed,monkeypatch,technical_response=response)
    assert result.quality_state.value=='needs_review',result.error
    saved=read_json(M._path(context,'media_plan'))['payload']
    assert saved['repair_basis']==plan


def test_unknown_selected_replan_ticket_rejected_before_transport(tmp_path,monkeypatch):
    context,_,_,raw,caps,_,manifest,_,_=failed_source(tmp_path,monkeypatch)
    missing=deepcopy(manifest);missing['tickets'][0]['ticket_id']='invented'
    planner,result=plan_repair(context,missing,replacement(raw,caps),monkeypatch)
    assert result.execution_state.value=='blocked' and planner.calls==0


def test_stale_resource_ticket_rejected_before_planner(tmp_path,monkeypatch):
    context,_,_,raw,caps,_,manifest,_,_=failed_source(tmp_path,monkeypatch)
    manifest['tickets'][0]['target']['artifact_hash']='sha256:obsolete'
    write_json_atomic(M._path(context,'figures'),{'payload':manifest})
    planner,result=plan_repair(context,manifest,replacement(raw,caps),monkeypatch)
    assert result.execution_state.value=='blocked' and planner.calls==0


def test_invalid_replan_cannot_establish_a_weaker_next_round_baseline(tmp_path,monkeypatch):
    context,_,_,raw,caps,plan,manifest,_,_=failed_source(tmp_path,monkeypatch)
    changed=replacement(raw,caps)
    response=technical_plan(changed);response['decisions']=[{**changed['decisions'][0],'requirement':'recommended'}]
    _,first=plan_repair(context,manifest,changed,monkeypatch,technical_response=response)
    assert first.quality_state.value=='needs_review'
    bad=read_json(M._path(context,'media_plan'))['payload']
    assert bad['candidate']['decisions'][0]['requirement']=='required', 'host must not import attempted downgrade'
    _,second=plan_repair(context,bad,changed,monkeypatch,technical_response=response)
    assert second.quality_state.value=='needs_review'
    assert read_json(M._path(context,'media_plan'))['payload']['repair_basis']==plan
    _,third=plan_repair(context,read_json(M._path(context,'media_plan'))['payload'],replacement(raw,caps),monkeypatch)
    assert third.quality_state.value=='accepted',third.error


def test_new_source_initial_standards_pass_cannot_close_failing_original_ticket(tmp_path,monkeypatch):
    context,_,_,raw,caps,_,manifest,_,reviewer=failed_source(tmp_path,monkeypatch)
    _,planned=plan_repair(context,manifest,replacement(raw,caps),monkeypatch)
    assert planned.quality_state.value=='accepted'
    original=manifest['tickets'][0]
    reviewer.fail='ticket:'+original['ticket_id']+':'+original['criteria'][0]['criterion_id']
    context.node.stage='figures';context.repair_batch=None
    monkeypatch.setattr(M,'_client',lambda *a,**k:reviewer)
    result=M.figures_stage(context)
    assert result.quality_state.value=='needs_review'
    saved=read_json(M._path(context,'figures'))['payload']
    assert saved['tickets'][0]['ticket_id']==original['ticket_id']
    assert saved['tickets'][0]['criteria']==original['criteria']
    assert result.repair_routes[0]['target_artifact_kind']=='media_requirements'


def test_switching_to_executable_backend_keeps_standard_with_executable_scope(tmp_path,monkeypatch):
    context,content,_,_,_,_,manifest,_,_=failed_source(tmp_path,monkeypatch)
    original=manifest['tickets'][0]
    row=deepcopy(manifest['requests'][0]);row.update(kind='geometry',spec_hash='sha256:current-geometry',repair_tickets=[original])
    row['validation']={'verdicts':[{'criterion_id':'ticket:'+original['ticket_id']+':'+original['criteria'][0]['criterion_id'],
        'version':'1','status':'fail','why':'same original layout defect'}]}
    tickets,_,routes=M._tickets(context,content,[row])
    assert len(tickets)==1 and tickets[0]['ticket_id']==original['ticket_id']
    assert tickets[0]['criteria']==original['criteria']
    assert tickets[0]['target']['artifact_hash']=='sha256:current-geometry'
    assert tickets[0]['allowed_paths']==['/spec/layout/font_size','/spec/layout/label_offsets']
    assert not tickets[0]['previous_attempt'].get('source_image_replan')
    assert routes[0]['target_artifact_kind']=='media_resource'


def test_malformed_pass_cannot_erase_original_ticket_before_evidence_supplement(tmp_path,monkeypatch):
    context,_,_,raw,caps,_,manifest,_,reviewer=failed_source(tmp_path,monkeypatch)
    _,planned=plan_repair(context,manifest,replacement(raw,caps),monkeypatch)
    assert planned.quality_state.value=='accepted'
    original=manifest['tickets'][0]
    reviewer.fail=None
    reviewer.bad_version='ticket:'+original['ticket_id']+':'+original['criteria'][0]['criterion_id']
    context.node.stage='figures';context.repair_batch=None
    monkeypatch.setattr(M,'_client',lambda *a,**k:reviewer)
    result=M.figures_stage(context)
    assert result.quality_state.value=='needs_review'
    saved=read_json(M._path(context,'figures'))['payload']
    assert any(t['ticket_id']==original['ticket_id'] for t in saved['tickets'])
    assert result.repair_requests and all(d.kind=='evidence_format' for d in result.repair_requests)
    assert {route['ticket_id'] for route in result.repair_routes}=={
        tid for directive in result.repair_requests for tid in directive.ticket_ids}
    evidence=next(t for t in saved['tickets'] if t['kind']=='evidence_format')
    context.repair_batch=SimpleNamespace(jobs=[SimpleNamespace(ticket_ids=[evidence['ticket_id']],
        artifact_id=evidence['target']['artifact_id'],kind='evidence_format',owner='reviewer')])
    reviewer.bad_version=None
    from aiforstudy.blocks.media import execution
    monkeypatch.setattr(execution,'_source_product',lambda *a,**k:(_ for _ in ()).throw(AssertionError('evidence repair regenerated image')))
    accepted=M.figures_stage(context)
    assert accepted.quality_state.value=='accepted',accepted.error
    assert any(item.get('ticket_id')==original['ticket_id'] for item in reviewer.seen[-1]['review_items'])
    assert not read_json(M._path(context,'figures'))['payload']['tickets']


def test_evidence_supplement_can_discover_a_new_substantive_source_failure(tmp_path,monkeypatch):
    context,content,_,_,_,_,manifest,_,_=failed_source(tmp_path,monkeypatch)
    # The real current review fails readability, after an earlier missing-review
    # round. There is no previous substantive ticket to hide misclassification.
    manifest['tickets']=[]
    write_json_atomic(M._path(context,'figures'),{'payload':manifest})
    row=manifest['requests'][0];row['evidence_only']=True
    tickets,_,routes=M._tickets(context,content,[row])
    assert tickets[0]['kind']=='visual_layout'
    assert routes[0]['target_artifact_kind']=='media_requirements'


def test_audit_missing_v2_validation_routes_only_evidence_with_actual_origin(tmp_path,monkeypatch):
    context,_,_,_,_,_,manifest,_,_=failed_source(tmp_path,monkeypatch)
    manifest['tickets']=[]
    row=manifest['requests'][0];row.update(state='fulfilled',reason='');row.pop('validation')
    write_json_atomic(M._path(context,'figures'),{'payload':manifest})
    context.node.stage='audit'
    result=M.audit_stage(context)
    assert result.quality_state.value=='needs_review'
    assert result.repair_requests and all(d.kind=='evidence_format' for d in result.repair_requests)
    assert all(route['origin_stage']=='audit' and route['target_artifact_kind']=='media_evidence' for route in result.repair_routes)
