"""Author intent survives publication, technical compilation, and narrow repair."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from aiforstudy.errors import ContractError
from aiforstudy.blocks.content.generation import generate_content
from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.media.media_planning import plan_media_requirements
from support.content_case import teaching_case, approved_content, none_decision
from test_generation_corrections import SequenceClient


def envelope(content):
    return {'schema_version':'ContentAuthoringV1','content':deepcopy(content),
            'media_requirements':{'schema_version':'AuthorMediaRequirementsV1',
                                  'decisions':[none_decision()],'requests':[]}}


class Response:
    def __init__(self, value): self.value=value; self.calls=[]
    def chat_json(self, messages, **kw):
        self.calls.append(deepcopy(messages))
        return {'data':deepcopy(self.value)}


def test_author_returns_content_and_explicit_media_intent_in_one_response():
    content,source=teaching_case(); client=Response(envelope(content)); saved=[]
    result=generate_content(source,load_content_policy(),client,saved.append)
    assert result['content']['body_markdown']==content['body_markdown']
    assert result['media_requirements']['decisions'][0]['requirement']=='none'
    assert saved==[envelope(content)]
    payload=json.loads(client.calls[0][1]['content'])
    assert {'content','media_requirements'}<=set(payload['output_json_schema']['required'])


def test_historical_content_alone_is_not_new_author_output_or_implicit_none():
    content,source=teaching_case()
    with pytest.raises(ContractError,match='ContentAuthoringV1'):
        generate_content(source,load_content_policy(),Response(content))


def test_missing_author_media_stops_before_semantic_planning():
    content,approval=approved_content(); client=Response({'decisions':[none_decision()],'requests':[]})
    with pytest.raises(ContractError,match='作者'):
        plan_media_requirements(content,approval,capabilities={'kinds':[]},client=client)
    assert client.calls==[]


def test_explicit_author_none_compiles_without_a_model():
    from aiforstudy.blocks.media.author_requirements import bind_author_requirements
    content,approval=approved_content()
    intent=bind_author_requirements(envelope(content)['media_requirements'],content,author_response_hash='sha256:raw')
    plan=plan_media_requirements(content,approval,author_requirements=intent,capabilities={'kinds':[]})
    assert plan['requests']==[] and plan['decisions'][0]['requirement']=='none'
    assert plan['author_media_hash']


def test_boolean_schema_rejects_options_before_runtime_validation():
    import jsonschema
    from aiforstudy.blocks.content.content_contracts import LessonContentV4
    c,_=teaching_case()
    c['learning_checks']=[{'id':'LC','kind':'boolean','source_kind':'derived','goal_refs':['G'],'source_refs':[],
        'statement':'零符合约定吗？','input_spec':{'options':[{'id':'yes','label':'是'}]},
        'scoring':{'accepted_inputs':[True]},'hints':[],'answer':'是，零是非负整数。',
        'misconception_feedback':[],'test_cases':[]}]
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(c,LessonContentV4.model_json_schema(by_alias=True))


def test_publication_saves_independent_same_revision_author_requirements(tmp_path,monkeypatch):
    from test_content_generation_v3 import context, scripted_client
    from aiforstudy.pipeline import content_stages as C, stage_io as T
    monkeypatch.setattr(T,'_gateway_client',lambda *a,**k:(scripted_client(),'offline'))
    result=C.lesson_stage(context(tmp_path))
    assert result.execution_state.value=='succeeded',result.error
    head=json.loads((tmp_path/'work/products/content/U/current.json').read_text())
    record=json.loads(__import__('pathlib').Path(head['author_media_ref']['path']).read_text())
    assert record['content_hash']==head['content_hash']
    assert record['content_revision']==head['content_revision']
    assert record['requirements']['decisions'][0]['requirement']=='none'


def math_case():
    from test_media_decisions_v2 import math_request_case
    from support.content_case import author_media, technical_plan
    content,approval,raw,caps=math_request_case()
    return content,approval,author_media(content,raw),caps,technical_plan(raw)


@pytest.mark.parametrize('damage',['drop','add','intent','kind'])
def test_technical_compiler_cannot_drop_add_or_rewrite_author_requests(damage):
    c,a,author,caps,technical=math_case()
    if damage=='drop': technical['requests']=[]
    if damage=='add': technical['requests'].append({**technical['requests'][0],'request_key':'extra'})
    if damage=='intent': technical['requests'][0]['observe']='改变教学含义'
    if damage=='kind': technical['requests'][0]['kind']='animation'
    with pytest.raises(ContractError):
        plan_media_requirements(c,a,author_requirements=author,capabilities=caps,client=Response(technical))


def test_repeated_anchor_requires_explicit_occurrence_and_host_computes_span():
    from aiforstudy.blocks.media.author_requirements import bind_author_requirements
    c,a,author,caps,technical=math_case()
    c['body_markdown']='观察零。观察零。'
    requirements=deepcopy(author['requirements']); p=requirements['requests'][0]['placement']
    p.update(anchor_text='观察零。',occurrence=None)
    with pytest.raises(ContractError,match='重复'):
        bind_author_requirements(requirements,c,author_response_hash='sha256:raw')
    p['occurrence']=2
    result=bind_author_requirements(requirements,c,author_response_hash='sha256:raw')
    assert result['bindings']['zero-position']['placement']['span']==[4,8]


def test_failed_author_binding_publishes_neither_content_nor_head(tmp_path):
    from test_content_generation_v3 import context
    from aiforstudy.pipeline.content_stages import _publish_revision, _frozen_context
    ctx=context(tmp_path); c,_=teaching_case()
    bad=envelope(c)['media_requirements']; bad['decisions']=[]
    with pytest.raises(ContractError):
        _publish_revision(ctx,c,_frozen_context(ctx),author_media=bad,author_response_hash='raw')
    assert not (tmp_path/'work/products/content/U/revisions/000001.json').exists()
    assert not (tmp_path/'work/products/content/U/current.json').exists()


def test_changed_anchor_routes_to_original_author_and_narrow_patch_restores_it(tmp_path,monkeypatch):
    from aiforstudy.pipeline import content_stages as C, media_stages as M
    from aiforstudy.blocks.media.author_requirements import bind_author_requirements, repair_author_requirements
    from test_media_plan_stage import ctx
    c,a,author,caps,technical=math_case()
    current=deepcopy(c);current['body_markdown']='新的段落。'
    stale=bind_author_requirements(author['requirements'],current,author_response_hash=author['author_response_hash'],previous=author,allow_stale=True)
    monkeypatch.setattr(M,'_load_content',lambda _: (current,a))
    monkeypatch.setattr(M,'_capabilities',lambda _: caps)
    monkeypatch.setattr(M,'_load_author_media',lambda *args,**kw:stale)
    result=M.media_plan_stage(ctx(tmp_path,'media_plan'))
    assert result.quality_state.value=='needs_review',result.error
    assert result.repair_routes[0]['target_artifact_kind']=='author_media'
    payload=json.loads((tmp_path/'products/media_plan/U.json').read_text())['payload']
    ticket=payload['tickets'][0]
    assert ticket['allowed_paths']==['/requests/0/placement']
    from aiforstudy.core.digest import digest_of
    placement={**author['requirements']['requests'][0]['placement'],'anchor_text':'新的段落。','occurrence':1}
    patch={'schema_version':'AuthorMediaPatchV1','base_author_media_hash':digest_of(stale),
        'ticket_refs':[ticket['ticket_id']],'operations':[{'op':'replace','path':'/requests/0/placement','value':placement}]}
    repaired=repair_author_requirements(stale,patch,[ticket],current)
    assert repaired['status']=='bound'
    assert repaired['requirements']['requests'][0]['acceptance_criteria']==author['requirements']['requests'][0]['acceptance_criteria']
    patch['operations'][0]['path']='/requests/0/observe'
    with pytest.raises(ContractError,match='授权'):
        repair_author_requirements(stale,patch,[ticket],current)


def test_missing_goals_remain_content_audit_candidate_instead_of_structure_failure():
    c,s=teaching_case(); c['teaching_plan']['goals']=[]; c['teaching_evidence']=[]
    value=envelope(c); value['media_requirements']['decisions']=[]
    result=generate_content(s,load_content_policy(),Response(value))
    assert result['content']['teaching_plan']['goals']==[]


def test_content_repair_new_goal_has_author_owned_coverage_repair(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media_stages as M
    from aiforstudy.blocks.media.author_requirements import bind_author_requirements, repair_author_requirements
    from aiforstudy.core.digest import digest_of
    from test_media_plan_stage import ctx
    c,a=approved_content();old=deepcopy(c);old['teaching_plan']['goals']=[];old['teaching_evidence']=[]
    requirement={'schema_version':'AuthorMediaRequirementsV1','decisions':[],'requests':[]}
    author=bind_author_requirements(requirement,old,author_response_hash='raw')
    stale=bind_author_requirements(requirement,c,author_response_hash='raw',previous=author,allow_stale=True)
    monkeypatch.setattr(M,'_load_content',lambda _: (c,a));monkeypatch.setattr(M,'_capabilities',lambda _: {'kinds':[]})
    monkeypatch.setattr(M,'_load_author_media',lambda *args,**kw:stale)
    result=M.media_plan_stage(ctx(tmp_path,'media_plan'))
    assert result.quality_state.value=='needs_review',result.error
    ticket=json.loads((tmp_path/'products/media_plan/U.json').read_text())['payload']['tickets'][0]
    patch={'schema_version':'AuthorMediaPatchV1','base_author_media_hash':digest_of(stale),'ticket_refs':[ticket['ticket_id']],
        'operations':[{'op':'replace','path':'/decisions','value':[none_decision()]}]}
    repaired=repair_author_requirements(stale,patch,[ticket],c)
    assert repaired['status']=='bound' and repaired['requirements']['decisions'][0]['goal_id']=='G'


def test_four_author_schema_errors_get_one_explicit_bounded_format_repair(tmp_path):
    c,s=teaching_case(); valid=envelope(c)
    bad=deepcopy(valid)
    bad['content']['teaching_evidence']=[{**c['teaching_evidence'][0],'purpose':'theorem'} for _ in range(3)]
    from aiforstudy.blocks.content.prompt import learning_check_examples
    check=learning_check_examples()[0];check['goal_refs']=['G']
    check['input_spec']['options']=[{'id':'true','label':'正确'},{'id':'false','label':'错误'}]
    bad['content']['learning_checks']=[check]
    valid=deepcopy(bad)
    for item in valid['content']['teaching_evidence']: item['purpose']='derivation'
    corrected=valid['content']['learning_checks'][0]
    corrected['kind']='choice'
    corrected['scoring']['accepted_inputs']=['true']
    corrected['misconception_feedback'][0]['inputs']=['false']
    for row,value in zip(corrected['test_cases'],['true','false','not-an-option']):row['input']=value
    # R3-1：内层纠正轮取消 —— 坏答复一次即判，四条 schema 诊断写进 journal。
    journal=tmp_path/'generation.json'; saved=[]
    client=SequenceClient(bad)
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(s,load_content_policy(),client,saved.append,correction_journal=journal)
    assert len(client.calls)==1 and saved==[bad]
    feedback=json.loads(journal.read_text())['merged_feedback']
    assert len(feedback['errors'])==4
    assert feedback['original_response']==bad
    assert {tuple(e['loc']) for e in feedback['errors']}=={
        ('content','teaching_evidence',i,'purpose') for i in range(3)}|{('content','learning_checks',0)}
    # 合法答复另起一次调用即通过
    result=generate_content(s,load_content_policy(),SequenceClient(valid))
    assert result['content']['body_markdown']==c['body_markdown']
    assert result['content']['learning_checks'][0]['input_spec']['options']==check['input_spec']['options']
    assert result['content']['learning_checks'][0]['kind']=='choice'
    permanent=Response(bad); saved=[]
    with pytest.raises(ContractError): generate_content(s,load_content_policy(),permanent,saved.append)
    assert len(permanent.calls)==1 and saved==[bad]


# rename_option 会连带触发 schema（choice 的 options 必须带 id/label），归到 schema 用例；
# 这条只留“不再被身份冻结拦住”的两种形态。
@pytest.mark.parametrize('damage',['remove_options','replace_input_spec'])
def test_format_correction_option_edits_are_no_longer_identity_gated(damage):
    """用户 2026-10-11 决定删除 preserve 护栏：选项 id/结构不再按"冻结身份"拦住纠正轮。

    这条原来断言"改 id 会被 preserve 拦死"。现在**只有数学结构**仍会拦；
    选项这类格式改动不再进身份冻结，所以同样的输入应当**正常通过**。
    """
    from aiforstudy.blocks.content.prompt import learning_check_examples
    from test_generation_corrections import SequenceClient
    c,s=teaching_case();bad=envelope(c)
    check=learning_check_examples()[0];check['goal_refs']=['G']
    check['input_spec']['options']=[{'id':'true','label':'正确'},{'id':'false','label':'错误'}]
    bad['content']['learning_checks']=[check]
    after=deepcopy(bad)
    candidate=after['content']['learning_checks'][0]
    if damage=='replace_input_spec':candidate['input_spec']={'options':[],'placeholder':''}
    elif damage=='remove_options':candidate['input_spec']['options']=[]
    else:candidate['input_spec']['options'][0]['id']='renamed'
    client=SequenceClient(after)
    result=generate_content(s,load_content_policy(),client)
    assert len(client.calls)==1
    assert result['content']['learning_checks'][0]['input_spec']==candidate['input_spec']


def test_interrupted_pair_publication_can_retry_identical_unpublished_revision(tmp_path,monkeypatch):
    from test_content_generation_v3 import context
    from aiforstudy.pipeline import content_stages as C
    ctx=context(tmp_path); c,_=teaching_case(); frozen=C._frozen_context(ctx)
    real=C.write_json_atomic
    def interrupt(path,value):
        if 'author_media' in path.parts: raise OSError('interrupted before sidecar')
        return real(path,value)
    monkeypatch.setattr(C,'write_json_atomic',interrupt)
    with pytest.raises(OSError):
        C._publish_revision(ctx,c,frozen,author_media=envelope(c)['media_requirements'],author_response_hash='raw')
    monkeypatch.setattr(C,'write_json_atomic',real)
    content,head,refs=C._publish_revision(ctx,c,frozen,author_media=envelope(c)['media_requirements'],author_response_hash='raw')
    assert head['content_revision']==1 and C.load_author_media(ctx,content)['status']=='bound'


def test_anchor_failure_cannot_hide_changed_math_or_authorize_only_anchor_repair(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media_stages as M
    from aiforstudy.blocks.media.author_requirements import bind_author_requirements, repair_author_requirements
    from aiforstudy.core.digest import digest_of
    from test_media_plan_stage import ctx
    c,a,author,caps,technical=math_case(); c['body_markdown']='新的段落。'
    c['math_models'][0]['objects'][0]['expr']['value']=1
    stale=bind_author_requirements(author['requirements'],c,author_response_hash='raw',previous=author,allow_stale=True)
    assert {e['code'] for e in stale['errors']}=={'AUTHOR_MEDIA_BINDING','AUTHOR_MEDIA_SEMANTICS_CHANGED'}
    monkeypatch.setattr(M,'_load_content',lambda _: (c,a));monkeypatch.setattr(M,'_capabilities',lambda _:caps)
    monkeypatch.setattr(M,'_load_author_media',lambda *args,**kw:stale)
    result=M.media_plan_stage(ctx(tmp_path,'media_plan'))
    tickets=json.loads((tmp_path/'products/media_plan/U.json').read_text())['payload']['tickets']
    patch={'schema_version':'AuthorMediaPatchV1','base_author_media_hash':digest_of(stale),'ticket_refs':[t['ticket_id'] for t in tickets],
        'operations':[{'op':'replace','path':'/requests/0/placement','value':{
            **author['requirements']['requests'][0]['placement'],'anchor_text':'新的段落。'}}]}
    with pytest.raises(ContractError,match='语义|数学'):
        repair_author_requirements(stale,patch,tickets,c)
    anchor_ticket=next(t for t in tickets if t['previous_attempt']['binding_error']['code']=='AUTHOR_MEDIA_BINDING')
    patch['ticket_refs']=[anchor_ticket['ticket_id']]
    with pytest.raises(ContractError,match='未授权|未覆盖|语义|数学'):
        repair_author_requirements(stale,patch,[anchor_ticket],c)


@pytest.mark.parametrize('damage',['duplicate_object','unrelated_model'])
def test_author_owns_math_binding_errors_before_technical_compilation(damage):
    from aiforstudy.blocks.media.author_requirements import bind_author_requirements
    c,a,author,caps,technical=math_case();requirements=deepcopy(author['requirements'])
    request=requirements['requests'][0]
    if damage=='duplicate_object':request['object_refs']*=2
    else:request['claim_ids']=[]
    with pytest.raises(ContractError):bind_author_requirements(requirements,c,author_response_hash='raw')


def test_cross_field_schema_error_allows_related_kind_repair_with_full_identity_validation(tmp_path):
    from support.content_case import authoring
    c,s=teaching_case(); mc,a,author,caps,technical=math_case()
    c['math_models']=mc['math_models']; c['teaching_plan']['claims'][0]['math_model_refs']=['M']
    bad={'schema_version':'ContentAuthoringV1','content':c,'media_requirements':deepcopy(author['requirements'])}
    request=bad['media_requirements']['requests'][0]; request.update(kind='animation',animation=None)
    after=deepcopy(bad);after['media_requirements']['requests'][0]['kind']='image'
    # R3-1：坏答复一次即判；可修的 kind 降级由模型在**全新一份**完整答复里主张，并重过身份校验。
    journal=tmp_path/'generation.json'
    client=SequenceClient(bad)
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(s,load_content_policy(),client,correction_journal=journal)
    assert len(client.calls)==1
    feedback=json.loads(journal.read_text())['merged_feedback']
    assert feedback['errors'][0]['path']=='/media_requirements/requests/0'
    contender=SequenceClient(after)
    result=generate_content(s,load_content_policy(),contender)
    assert len(contender.calls)==1
    repaired=result['media_requirements']['requests'][0]
    assert repaired['kind']=='image'
    assert repaired['request_key']==request['request_key']
    assert repaired['acceptance_criteria']==request['acceptance_criteria']
    assert result['content']['body_markdown']==c['body_markdown']


def test_coordinator_author_media_job_runs_lesson_patch_and_publishes_bound_revision(tmp_path,monkeypatch):
    from test_content_generation_v3 import context, scripted_client
    from aiforstudy.pipeline import content_stages as C, media_stages as M, stage_io as T
    from aiforstudy.pipeline.repair import RepairCoordinator, UnitRepairState, RepairRouteV1
    from aiforstudy.core.digest import digest_of
    ctx=context(tmp_path)
    monkeypatch.setattr(T,'_gateway_client',lambda *a,**k:(scripted_client(),'offline'))
    assert C.lesson_stage(ctx).execution_state.value=='succeeded'
    content,_=C.load_content(ctx); source=C._frozen_context(ctx)
    _,_,sample,_,_=math_case();requirements=deepcopy(sample['requirements'])
    request=requirements['requests'][0]
    request.update(math_model_ids=[],object_refs=[])
    request['acceptance_criteria'][0]['evidence_required']=['rendered_frame','request']
    original,_,_=C._publish_revision(ctx,content,source,author_media=requirements,author_response_hash='raw')
    original_media=C.load_author_media(ctx,original)
    changed=deepcopy(original);changed['body_markdown']+=' 新的精确落点。'
    # The old entire-paragraph anchor has been removed, so this cannot be fixed
    # merely by recalculating an offset.
    changed['body_markdown']='新的精确落点。'+changed['body_markdown'].replace('这个约定','此约定')
    current,_,_=C._publish_revision(ctx,changed,source,author_media=original_media,previous_author_media=original_media)
    record=C.load_author_media(ctx,current,allow_stale=True)
    media_ctx=context(tmp_path,'media_plan')
    media_ctx.services.control=SimpleNamespace(check=lambda:None)
    monkeypatch.setattr(M,'_load_content',lambda _: (current,{}));monkeypatch.setattr(M,'_capabilities',lambda _: {'kinds':['source_image']})
    result=M.media_plan_stage(media_ctx)
    assert result.quality_state.value=='needs_review',result.error
    from test_repair_coordinator import _coordinator
    batch=_coordinator().plan(result.repair_requests,UnitRepairState('U'))
    assert batch.jobs[0].owner=='lesson'
    assert RepairRouteV1.from_dict(result.repair_routes[0]).target_stage=='lesson'
    # D16-3：作者媒体返修已改成**整份重写** —— 模型交回完整 AuthorMediaRequirementsV1，
    # 不再交 AuthorMediaPatchV1 的 operations。
    revised=deepcopy(record['requirements'])
    revised['requests'][0]['placement']={**request['placement'],'anchor_text':'新的精确落点。','occurrence':1}
    client=Response(revised);monkeypatch.setattr(T,'_gateway_client',lambda *a,**k:(client,'offline'))
    ctx.repair_batch=batch
    repaired=C.lesson_stage(ctx)
    assert repaired.execution_state.value=='succeeded',repaired.error
    final,_=C.load_content(ctx); bound=C.load_author_media(ctx,final)
    assert final['body_markdown']==current['body_markdown']
    assert bound['status']=='bound' and bound['repair_provenance']['ticket_refs']==list(batch.ticket_ids)
    assert bound['requirements']['requests'][0]['request_key']=='zero-position'


def test_author_binding_code_change_invalidates_lesson_runtime(tmp_path,monkeypatch):
    from aiforstudy.pipeline.runner import Runner
    from support.runtime_tree import copy_runtime
    package=tmp_path/'package';adapters=tmp_path/'adapters'
    copy_runtime(package, adapters)
    path=package/'blocks/media/author_requirements.py';path.parent.mkdir(parents=True,exist_ok=True);path.write_text('before')
    monkeypatch.setattr(Runner,'_runtime_package_root',staticmethod(lambda:package))
    monkeypatch.setattr('aiforstudy.pipeline.stage_io._repo_root',lambda:adapters)
    runner=object.__new__(Runner); before=runner._stage_runtime_identity('lesson')
    path.write_text('after')
    assert runner._stage_runtime_identity('lesson')!=before
