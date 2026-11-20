"""Stage ownership and real static repair work under frozen original standards."""
from copy import deepcopy
from types import SimpleNamespace
import json
from aiforstudy.core.digest import digest_of
from aiforstudy.pipeline import media_stages,content_stages
from aiforstudy.pipeline.diagnostics import technical_ticket
from aiforstudy.blocks.media.execution import execute_static
from test_teaching_policy_v2 import teaching_case
from test_semantic_static_media import case,vector,geom_spec


def setup(monkeypatch):
    content,source=teaching_case()
    ctx=SimpleNamespace(node=SimpleNamespace(scope_key=content['unit_id'],stage='figures'))
    monkeypatch.setattr(content_stages,'_frozen_context',lambda ctx:source)
    monkeypatch.setattr(media_stages,'_persisted_tickets',lambda *a,**k:{})
    return ctx,content


def test_animation_validation_failure_is_actionable_without_freeform_problem(monkeypatch):
    ctx,c=setup(monkeypatch)
    row={'request_id':'R','kind':'animation','state':'needs_review','visual_spec':{'schema_version':'2.0'},
         'validation':{'issues':[{'code':'MEDIA_LABEL_READABILITY','message':'390下标签只有10px'}]}}
    tickets,directives,routes=media_stages._tickets(ctx,c,[row])
    assert tickets[0]['kind']=='visual_layout'
    assert directives[0].owner=='visual_planner' and routes[0]['target_artifact_kind']=='visual_spec'
    old=deepcopy(tickets[0]);monkeypatch.setattr(media_stages,'_persisted_tickets',lambda *a,**k:{old['ticket_id']:old})
    row['validation']['issues'][0]['message']='390下标签12px仍过小'
    again=media_stages._tickets(ctx,c,[row])[0][0]
    assert again['ticket_id']==old['ticket_id'] and again['criteria']==old['criteria']


def test_missing_media_review_evidence_reruns_only_reviewer(monkeypatch):
    ctx,c=setup(monkeypatch)
    row={'request_id':'R','kind':'geometry','state':'needs_review','spec_hash':'sha256:abc',
         'validation':{'issues':[{'code':'MEDIA_REVIEW_EVIDENCE','message':'缺手机截图证据'}]}}
    tickets,directives,routes=media_stages._tickets(ctx,c,[row])
    assert directives[0].kind=='evidence_format' and directives[0].owner=='reviewer'
    assert routes[0]['target_artifact_kind']=='media_evidence' and tickets[0]['allowed_paths']==['/review']


def test_audit_evidence_route_keeps_actual_origin_but_dispatches_figures(monkeypatch):
    from aiforstudy.pipeline.repair import RepairRouteV1
    from aiforstudy.pipeline.runner import Runner
    from aiforstudy.contracts.stage import StageResult
    ctx,c=setup(monkeypatch);ctx.node.stage='audit'
    row={'request_id':'R','kind':'geometry','state':'needs_review','spec_hash':'sha256:abc',
        'validation':{'issues':[{'code':'MEDIA_REVIEW_EVIDENCE','message':'缺当前真实帧'}]}}
    tickets,directives,routes=media_stages._tickets(ctx,c,[row])
    route=RepairRouteV1.from_dict(routes[0])
    assert route.origin_stage=='audit' and route.target_stage=='figures'
    runner=object.__new__(Runner);runner._repair_routes={}
    runner._register_repair_routes(ctx.node,StageResult.ok('audit',repair_requests=directives,repair_routes=routes))
    assert set(runner._repair_routes)=={tickets[0]['ticket_id']}


def test_actual_static_default_layout_is_repairable_without_changing_math(tmp_path):
    c,a,r=case(objects=[vector('a',[2,1]),vector('b',[1,2]),vector('c',[3,3])]);r['spec']=geom_spec()
    first=execute_static(r,content=c,approval=a,out_dir=tmp_path)
    assert first['state']=='rendered',first['reason']
    resource=next(row for row in first['resources'] if row['kind']=='spec')
    spec=json.loads((tmp_path/resource['relative_path']).read_text())
    assert spec['layout']['font_size']==24
    ticket=technical_ticket(unit_id='U',artifact_id='media:'+r['request_id'],artifact_hash=first['spec_hash'],
        problem='font needs more margin',source=c['body_markdown'],owner='visual_planner',kind='visual_layout',
        allowed_paths=('/spec/layout/font_size',),rule='MEDIA_LABEL_READABILITY',
        action='increase to32',expected='readable at390').as_dict()
    class Client:
        def chat_json(self,messages,**kw):
            data=json.loads(messages[1]['content'])
            return {'data':{'patches':[{'schema_version':'MediaPatchV2','request_id':r['request_id'],
                'ticket_id':ticket['ticket_id'],'base_spec_hash':data['base_spec_hash'],
                'operations':[{'op':'replace','path':'/layout/font_size','value':32}]}]}}
    second=execute_static(r,content=c,approval=a,out_dir=tmp_path,client=Client(),repair_context=[ticket],previous=first)
    assert second['state']=='rendered',second['reason']
    assert second['static_product']['layout']['font_size']==32
    assert second['static_product']['data']==first['static_product']['data']
    assert second['static_product']['object_bindings']==first['static_product']['object_bindings']
    assert second['spec_hash']!=first['spec_hash']


def test_static_truth_discovery_can_repair_approved_model_with_distinct_claim_ids(tmp_path,monkeypatch):
    ctx,_=setup(monkeypatch)
    c,a,r=case(objects=[vector('a',[2,1]),vector('b',[1,2]),vector('c',[4,3])]);r['spec']=geom_spec()
    assert r['claim_ids'] != [m['claim_id'] for m in c['math_models'][0]['claims']]
    row=execute_static(r,content=c,approval=a,out_dir=tmp_path)
    row['request']=r
    assert row['failure_owner']=='content'
    ticket=media_stages._tickets(ctx,c,[row])[0][0]
    assert ticket['target']['rule']=='POST_MEDIA_CONTENT'
    assert '/math_models/0' in ticket['allowed_paths']
    assert '/teaching_plan/claims/0/conditions' in ticket['allowed_paths']
