"""Exact host-owned reverse indexes remove no mathematical obligations."""
from copy import deepcopy
import importlib
import json

import pytest

from aiforstudy.blocks.content.content_contracts import parse_content
from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.generation import generate_content
from aiforstudy.core.digest import digest_of
from aiforstudy.errors import ContractError
from test_generation_corrections import SequenceClient,math_case


def module():
    assert importlib.util.find_spec('aiforstudy.blocks.content.math_backlinks'),'host backlink derivation missing'
    return importlib.import_module('aiforstudy.blocks.content.math_backlinks')


def omitted_case():
    source,_,raw=math_case()
    raw['content']['teaching_plan']['claims'][0].pop('math_model_refs')
    return source,raw


def test_omitted_backlinks_derive_once_preserve_raw_and_persist_replay(tmp_path):
    source,raw=omitted_case();before=deepcopy(raw);reports=[];saved=[]
    client=SequenceClient(raw);journal=tmp_path/'journal.json'
    result=generate_content(source,load_content_policy(),client,saved.append,
        correction_journal=journal,derivation_sink=reports.append)
    assert result['content']['teaching_plan']['claims'][0]['math_model_refs']==['M']
    assert raw==before and saved==[before] and len(client.calls)==1
    assert json.loads(journal.read_text())['attempts'][0]['response']==before
    report=reports[0]
    assert report['schema_version']=='MathBacklinksDerivationV1' and report['version']==1
    assert report['raw_response_hash']==digest_of({'response':before})
    assert report['accepted_content_hash']==digest_of(result['content'])
    assert report['changes']==[{'path':'/teaching_plan/claims/0/math_model_refs',
        'present_before':False,'before':None,'after':['M'],'added':['M']}]
    replay=SequenceClient();replayed=[]
    assert generate_content(source,load_content_policy(),replay,
        correction_journal=journal,derivation_sink=replayed.append)==result
    assert replay.calls==[] and replayed==reports
    # Historical parsing alone never changes its missing/empty reverse index.
    assert parse_content(result['content']).as_dict()==result['content']
    historical=deepcopy(result['content']);historical['teaching_plan']['claims'][0]['math_model_refs']=[]
    assert parse_content(historical).as_dict()['teaching_plan']['claims'][0]['math_model_refs']==[]


def test_partial_backlinks_keep_author_order_and_append_all_matching_models():
    _,raw=omitted_case();content=raw['content'];model=content['math_models'][0]
    content['math_models'] += [{**deepcopy(model),'model_id':mid} for mid in ('M2','M3')]
    content['teaching_plan']['claims'][0]['math_model_refs']=['M3']
    current,report=module().derive_math_backlinks(content)
    assert current['teaching_plan']['claims'][0]['math_model_refs']==['M3','M','M2']
    assert report['changes'][0]['added']==['M','M2']
    again,second=module().derive_math_backlinks(current)
    assert again==current and second['changes']==[]


def test_multiple_relations_for_same_teaching_claim_add_one_model_backlink():
    _,raw=omitted_case();content=raw['content'];relation=content['math_models'][0]['claims'][0]
    content['math_models'][0]['claims'].append({**deepcopy(relation),'reason':'该教学主张的第二个独立关系'})
    result,report=module().derive_math_backlinks(content)
    assert len(result['math_models'][0]['claims'])==2
    assert result['teaching_plan']['claims'][0]['math_model_refs']==['M']
    assert report['changes'][0]['added']==['M']


@pytest.mark.parametrize('damage',['unknown_claim','duplicate_teaching','duplicate_model','duplicate_relation',
                                   'unknown_explicit','unrelated_explicit','duplicate_explicit'])
def test_ambiguous_or_explicit_wrong_edges_are_not_dropped_or_guessed(damage):
    source,raw=omitted_case();content=raw['content'];claim=content['teaching_plan']['claims'][0]
    model=content['math_models'][0]
    if damage=='unknown_claim':model['claims'][0]['claim_id']='UNKNOWN'
    if damage=='duplicate_teaching':content['teaching_plan']['claims'].append(deepcopy(claim))
    if damage=='duplicate_model':content['math_models'].append(deepcopy(model))
    if damage=='duplicate_relation':model['claims'].append(deepcopy(model['claims'][0]))
    if damage=='unknown_explicit':claim['math_model_refs']=['UNKNOWN']
    if damage=='unrelated_explicit':
        content['teaching_plan']['claims'].append({**deepcopy(claim),'id':'OTHER'})
        model['claims'][0]['claim_id']='OTHER';claim['math_model_refs']=['M']
    if damage=='duplicate_explicit':claim['math_model_refs']=['M','M']
    before=deepcopy(raw)
    if damage=='duplicate_relation':
        # R3-1：内层纠正轮取消 —— 重复 relation 也是**一次即判**（反馈写进 journal）。
        client=SequenceClient(raw)
        with pytest.raises(ContractError):generate_content(source,load_content_policy(),client)
        assert raw==before and len(client.calls)==1
        return
    client=SequenceClient(raw)
    with pytest.raises(ContractError):generate_content(source,load_content_policy(),client)
    assert raw==before and len(client.calls)==1


def test_media_closure_uses_derived_backlinks_in_same_rejection_feedback(tmp_path):
    """R3-1：宿主派生的 backlink 会进入拒绝反馈的候选；一次即判，合法重交另起一次调用。"""
    from test_author_media_requirements import math_case as media_case
    from support.content_case import teaching_case
    content,_,author,_,_=media_case();model=content['math_models'][0]
    content['math_models'].append({**deepcopy(model),'model_id':'M2'})
    content['teaching_plan']['claims'][0].pop('math_model_refs')
    raw={'schema_version':'ContentAuthoringV1','content':content,'media_requirements':author['requirements']}
    corrected=deepcopy(raw);corrected['media_requirements']['requests'][0]['math_model_ids'].append('M2')
    source=teaching_case()[1]
    # This fixture's metadata comes from the same frozen source after validation.
    journal=tmp_path/'journal.json';client=SequenceClient(raw)
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source,load_content_policy(),client,correction_journal=journal)
    assert len(client.calls)==1
    feedback=json.loads(journal.read_text())['merged_feedback']
    assert feedback['allowed_paths']==['/media_requirements/requests/0/math_model_ids']
    assert feedback['errors'][0]['candidates']==[['M','M2']]
    result=generate_content(source,load_content_policy(),SequenceClient(corrected))
    assert result['content']['teaching_plan']['claims'][0]['math_model_refs']==['M','M2']
    assert 'math_model_refs' not in corrected['content']['teaching_plan']['claims'][0]


def test_derivation_does_not_turn_abstract_math_into_pass():
    from aiforstudy.blocks.content.math_checks import check_content_math
    source,raw=omitted_case()
    result=generate_content(source,load_content_policy(),SequenceClient(raw))
    assert result['content']['teaching_plan']['claims'][0]['math_model_refs']==['M']
    assert check_content_math(result['content']).status=='unknown'


def test_derivation_does_not_repair_or_hide_a_false_mathematical_relation():
    from aiforstudy.blocks.content.math_checks import check_content_math
    source,raw=omitted_case();model=raw['content']['math_models'][0]
    model['objects'].append({'math_object_id':'n-plus-one','kind':'scalar',
        'expr':{'op':'add','left':{'op':'var','name':'n'},'right':{'op':'const','value':1}}})
    model['claims']=[{'claim_id':'K','kind':'identity','math_object_id':'n-object',
        'lhs_object_id':'n-object','rhs_object_id':'n-plus-one'}]
    result=generate_content(source,load_content_policy(),SequenceClient(raw))
    assert result['content']['math_models'][0]['objects']==model['objects']
    assert check_content_math(result['content']).status=='fail'


@pytest.mark.parametrize('damage',['models_type','claims_missing','claims_type','claim_id_missing'])
def test_derivation_preserves_existing_finite_schema_correction(damage, tmp_path):
    source,good=omitted_case();bad=deepcopy(good)
    if damage=='models_type':bad['content']['math_models']='not an array'
    if damage=='claims_missing':bad['content']['teaching_plan'].pop('claims')
    if damage=='claims_type':bad['content']['teaching_plan']['claims']='not an array'
    if damage=='claim_id_missing':bad['content']['teaching_plan']['claims'][0].pop('id')
    before=deepcopy(bad);client=SequenceClient(bad);journal=tmp_path/'journal.json'
    # R3-1：坏答复一次即判，schema 诊断写进 journal；派生证据只属于**被接受**的答复。
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source,load_content_policy(),client,correction_journal=journal)
    assert len(client.calls)==1 and bad==before
    feedback=json.loads(journal.read_text())['merged_feedback']
    assert feedback['errors'][0]['code']=='schema'
    reports=[]
    result=generate_content(source,load_content_policy(),SequenceClient(good),derivation_sink=reports.append)
    assert len(reports)==1 and reports[0]['changes'][0]['added']==['M']
    assert result['content']['teaching_plan']['claims'][0]['math_model_refs']==['M']


def test_lesson_stage_saves_derivation_evidence_as_artifact(tmp_path,monkeypatch):
    from test_content_generation_v3 import context
    from aiforstudy.pipeline import content_stages as stages,stage_io
    from support.runner_v5 import TeachingTransport
    from aiforstudy.core.jsonio import read_json
    class Transport(TeachingTransport):
        def _answer(self,system,user):
            result=json.loads(super()._answer(system,user))
            if json.loads(user).get('role_task')=='author':
                _,sample=omitted_case();c=result['content']
                c['math_models']=sample['content']['math_models']
                c['math_models'][0]['source_refs']=[c['teaching_plan']['claims'][0]['source_refs'][0]['obligation_id']]
            return json.dumps(result,ensure_ascii=False)
    transport=Transport()
    class Client:
        def chat_json(self,messages,**kwargs):return {'data':json.loads(transport._answer(messages[0]['content'],messages[1]['content']))}
    monkeypatch.setattr(stage_io,'_gateway_client',lambda *a,**k:(Client(),'offline'))
    result=stages.lesson_stage(context(tmp_path))
    assert result.execution_state.value=='succeeded',result.error
    refs=[r for r in result.outputs if r.kind=='content.host_derivation']
    assert len(refs)==1
    report=read_json(refs[0].path)
    assert report['version']==1 and report['changes'][0]['added']==['M']
    raw=read_json(tmp_path/'work/products/content/U/responses/000001.json')['response']
    assert report['raw_response_hash']==digest_of({'response':raw})
    assert 'math_model_refs' not in raw['content']['teaching_plan']['claims'][0]
