"""Media planning authorization and goal coverage regression boundary."""
from copy import deepcopy
import json
import pytest
from aiforstudy.core.digest import digest_of
from aiforstudy.errors import ContractError, CapabilityError
from aiforstudy.blocks.media.media_requirements import candidates, resolve_bound_placement, validate_requirements
from aiforstudy.blocks.media.media_planning import plan_media_requirements, planning_prompt
from test_content_approval_v2 import approved_inputs, issue
from support.content_case import author_media, technical_plan


def approved():
    inputs=approved_inputs()
    return inputs[0], issue(inputs).as_dict()


def none_decision():
    return {'target_id':'G','goal_id':'G','requirement':'none','reason':'零是否属于约定集合可以直接代入文字定义判断。',
            'benefit':'读者可以直接检查 n=0 满足非负条件。','cost':'图示会增加不必要的坐标约定。',
            'text_alternative':'正文已经解释零满足非负条件，并直接给出边界例。',
            'request_ids':[],'handled_by':'media'}


class Planner:
    def __init__(self, raw): self.raw=raw; self.calls=0
    def chat_json(self, messages, *, tag):
        self.calls+=1
        return {'data':technical_plan(deepcopy(self.raw))}


def test_negative_keyword_never_creates_visual_obligation_and_goal_is_candidate():
    c,_=approved(); c['body_markdown']='不讨论三维曲面；只判断整数约定。'
    rows=candidates(c)
    assert [r['target_id'] for r in rows]==['G']
    assert rows[0]['required'] is False


def test_goal_evidence_in_steps_and_conclusion_is_not_dropped_from_candidates():
    c,_=approved()
    c['examples']=[{'id':'E','kind':'derived_example','statement':'比较两个空间平面','steps':[{'id':'S','markdown':'观察交线的方向'}],'conclusion':'两平面沿该直线相交'}]
    c['teaching_evidence']=[{'goal_id':'G','purpose':'derivation','reader_pointer':'/examples/0/steps/0/markdown','claim_refs':['K']}, {'goal_id':'G','purpose':'example','reader_pointer':'/examples/0/conclusion','claim_refs':['K']}]
    row=candidates(c)[0]
    assert '观察交线的方向' in row['text'] and '两平面沿该直线相交' in row['text']
    assert {x['pointer'] for x in row['reader_evidence']}=={'/examples/0/steps/0/markdown','/examples/0/conclusion'}


def test_valid_none_is_goal_specific_and_produces_v2_without_fallback():
    c,a=approved(); client=Planner({'decisions':[none_decision()],'requests':[]})
    plan=plan_media_requirements(c,a,capabilities={'kinds':[]},client=client,author_requirements=author_media(c,client.raw))
    assert plan['schema_version']=='MediaRequirementsV2'
    assert plan['decisions'][0]['target_id']=='G' and plan['requests']==[]
    assert client.calls==0
    with pytest.raises(ContractError,match='作者|author'): plan_media_requirements(c,a,capabilities={'kinds':[]})


@pytest.mark.parametrize('damage',['missing','stale','v1','revision'])
def test_bad_approval_prevents_model_call(damage):
    c,a=approved(); client=Planner({'decisions':[none_decision()],'requests':[]})
    if damage=='missing': a={}
    if damage=='stale': c['summary']='新的内容'
    if damage=='v1': a['schema_version']='ContentApprovalV1'
    if damage=='revision': a['content_revision']+=1
    with pytest.raises(ContractError): plan_media_requirements(c,a,capabilities={'kinds':[]},client=client,author_requirements=author_media(c,client.raw))
    assert client.calls==0


def test_all_goal_decisions_need_benefit_cost_and_real_text_alternative():
    c,a=approved(); d=none_decision(); d.pop('benefit')
    with pytest.raises(ContractError): plan_media_requirements(c,a,capabilities={'kinds':[]},client=Planner({'decisions':[d],'requests':[]}),author_requirements=author_media(c,{'decisions':[d],'requests':[]}))


def test_explicit_visual_requirement_cannot_be_withdrawn_due_to_backend():
    c,a=approved(); c['teaching_plan']['goals'][0]['visual_requirement']={'required':True,'reason':'读者必须比较平面与交线的空间位置。'}
    a['content_hash']=digest_of(c,check=False)
    assert candidates(c)[0]['required'] is True
    with pytest.raises(ContractError,match='已批准 required'): plan_media_requirements(c,a,capabilities={'kinds':[]},client=Planner({'decisions':[none_decision()],'requests':[]}),author_requirements=author_media(c))


def placement(content,pointer='/body_markdown',owner='U',span=None):
    from aiforstudy.blocks.audit.evidence import resolve_json_pointer
    _, text=resolve_json_pointer(content,pointer); span=span or [0,len(text)]
    return dict(target_pointer=pointer,owner_id=owner,anchor_text=text[span[0]:span[1]],span=span,field_hash=digest_of(text),position='after')


def test_exact_placement_rejects_array_reorder_stale_hash_and_missing_occurrence():
    c,_=approved(); c['examples']=[{'id':'E1','conclusion':'观察交线。观察交线。'},{'id':'E2','conclusion':'观察交线。观察交线。'}]
    p=placement(c,'/examples/0/conclusion','E1',[5,10])
    assert resolve_bound_placement(c,p)['span']==[5,10]
    c['examples'].reverse()
    with pytest.raises(ContractError): resolve_bound_placement(c,p)
    c['examples'].reverse(); p['field_hash']='old'
    with pytest.raises(ContractError): resolve_bound_placement(c,p)
    p=placement(c,'/examples/0/conclusion','E1',[5,10]); p.pop('span')
    with pytest.raises(ContractError): resolve_bound_placement(c,p)


def test_planning_prompt_limits_placement_to_reader_fields_including_new_steps():
    c,a=approved(); payload=json.loads(planning_prompt(c,a,{'kinds':[]},author_media(c))[-1]['content'])
    pointers={r['pointer'] for r in payload['visible_fields']}
    assert '/body_markdown' in pointers and '/summary' in pointers
    assert '/teaching_plan/claims/0/conclusion' not in pointers


def math_request_case(kind='geometry'):
    """A frozen upstream approval envelope; issuance itself is tested separately."""
    from aiforstudy.blocks.math.contracts import parse_math_model
    c,a=approved()
    # 注意：这里的常量对象**只用于媒体链路夹具**，不经过正文生成的常量占位判据
    # （那条判据只看被 claim 引用的正文数学模型）。保持常量形式可让符号表为空，
    # 避免给所有下游媒体用例强加符号声明。
    model=parse_math_model({'schema_version':'1.0','model_id':'M','symbols':[],
        'objects':[{'math_object_id':'X','kind':'scalar','expr':{'op':'const','value':0}}],
        'conditions':[], 'claims':[{'claim_id':'K','kind':'identity','math_object_id':'X','lhs_object_id':'X','rhs_object_id':'X'}], 'source_refs':['SO']})
    c['math_models']=[model.as_dict()]; c['teaching_plan']['claims'][0]['math_model_refs']=['M']; a['content_hash']=digest_of(c,check=False)
    p=placement(c)
    request={'request_key':'zero-position','kind':kind,'serves':['G'],'goal_refs':['G'],'claim_ids':['K'],
        'math_model_refs':[{'model_id':'M','digest':model.digest}], 'object_refs':[{'model_id':'M','object_id':'X'}],
        'learning_goal':'识别零这个边界值','observe':'数轴原点对应零','misconception':'误认为非负不含零',
        'source_refs':[{'kind':'approved_content','pointer':'/body_markdown','hash':p['field_hash']}],
        'constraints':{},'placement':p,'dependency_paths':[{'pointer':'/body_markdown','hash':p['field_hash']}],
        'acceptance_criteria':[{'criterion_id':'zero-visible','version':'1','kind':'mathematical','statement':'原点标签精确表示批准对象 X 的数值零','evidence_required':['object_bindings','rendered_frame']}],
        'caption':'零是非负边界','alt_text':'原点标签为零'}
    d=none_decision(); d.update(requirement='recommended',request_ids=['zero-position'])
    return c,a,{'decisions':[d],'requests':[request]},{'kinds':[kind]}


@pytest.mark.parametrize('kind',['plot','geometry','diagram','animation'])
def test_five_type_interface_preserves_math_objects_and_frozen_criteria(kind):
    c,a,raw,caps=math_request_case(kind)
    plan=plan_media_requirements(c,a,capabilities=caps,client=Planner(raw),author_requirements=author_media(c,raw))
    r=plan['requests'][0]
    assert r['math_model_refs']==raw['requests'][0]['math_model_refs']
    assert r['object_refs']==[{'model_id':'M','object_id':'X'}]
    assert plan['decisions'][0]['request_ids']==[r['request_id']]
    r['acceptance_criteria'][0]['statement']='允许标签不是零'
    with pytest.raises(ContractError,match='acceptance_criteria|摘要'): validate_requirements(plan,c,a,capabilities=caps)


@pytest.mark.parametrize('damage',['model_digest','unknown_object','unknown_claim','unknown_goal','missing_model','unsupported_backend','svg','source','criteria_string','fulfilled','duplicate_decision'])
def test_request_invariants_cannot_be_forged(damage):
    c,a,raw,caps=math_request_case()
    plan=plan_media_requirements(c,a,capabilities=caps,client=Planner(raw),author_requirements=author_media(c,raw))
    r=plan['requests'][0]
    if damage=='model_digest': r['math_model_refs'][0]['digest']='old'
    if damage=='unknown_object': r['object_refs'][0]['object_id']='other'
    if damage=='unknown_claim': r['claim_ids']=['other']
    if damage=='unknown_goal': r['goal_refs']=['other']
    if damage=='missing_model': r['math_model_refs']=[]
    if damage=='unsupported_backend': caps['kinds']=[]
    if damage=='svg': r['kind']='svg'; caps['kinds']=['svg']
    if damage in ('unsupported_backend','svg'):
        plan['capability_digest']=digest_of(caps)
        message='技术编译改变作者媒体类型' if damage=='svg' else '媒体后端不支持 kind='
        with pytest.raises(ContractError,match=message):
            validate_requirements(plan,c,a,capabilities=caps)
        return
    if damage=='source': r['source_refs'][0]['hash']='old'
    if damage=='criteria_string': r['acceptance_criteria']=['looks good']
    if damage=='fulfilled': plan['decisions'][0]['status']='fulfilled'
    if damage=='duplicate_decision': plan['decisions']*=2
    with pytest.raises(ContractError): validate_requirements(plan,c,a,capabilities=caps)


def test_source_image_requires_real_registered_asset():
    c,a,raw,caps=math_request_case('source_image'); r=raw['requests'][0]
    r.update(source_image_id='IMG1',math_model_refs=[],object_refs=[],claim_ids=[])
    r['acceptance_criteria'][0]['evidence_required']=['source_provenance','rendered_frame']
    caps['source_assets']={'source_images':[{'asset_id':'IMG1','hash':'sha256:asset'}]}
    r['source_refs'].append({'kind':'asset','asset_id':'IMG1','hash':'sha256:asset'})
    plan=plan_media_requirements(c,a,capabilities=caps,client=Planner(raw),author_requirements=author_media(c,raw))
    assert plan['requests'][0]['kind']=='source_image'
    raw['requests'][0]['source_refs'][-1]['hash']='forged'
    with pytest.raises(ContractError): plan_media_requirements(c,a,capabilities=caps,client=Planner(raw),author_requirements=author_media(c,raw))


def test_repair_cannot_downgrade_existing_request_standards():
    c,a,raw,caps=math_request_case(); plan=plan_media_requirements(c,a,capabilities=caps,client=Planner(raw),author_requirements=author_media(c,raw))
    raw['requests'][0]['acceptance_criteria'][0]['statement']='只需有图，不检查零'
    with pytest.raises(ContractError,match='冻结'):
        plan_media_requirements(c,a,capabilities=caps,client=Planner(raw),author_requirements=author_media(c,raw),repair_context={'previous_requirements':plan})


def test_interaction_goal_decision_preserves_downstream_mapping_and_requires_capability():
    c,a=approved(); d=none_decision(); d.update(handled_by='interaction',requirement='recommended')
    raw={'decisions':[d],'requests':[]}
    with pytest.raises(ContractError): plan_media_requirements(c,a,capabilities={'kinds':[]},client=Planner(raw),author_requirements=author_media(c,raw))
    plan=plan_media_requirements(c,a,capabilities={'kinds':[],'interaction':True},client=Planner(raw),author_requirements=author_media(c,raw))
    assert plan['decisions'][0]['target_id']=='G'
    assert plan['decisions'][0]['handled_by']=='interaction'


def test_identical_text_step_reorder_cannot_reuse_old_placement():
    c,_=approved(); c['examples']=[{'id':'E','steps':[{'id':'S1','markdown':'相同句子'},{'id':'S2','markdown':'相同句子'}]}]
    p=placement(c,'/examples/0/steps/0/markdown','E'); p['step_id']='S1'
    assert resolve_bound_placement(c,p)['status']=='resolved'
    c['examples'][0]['steps'].reverse()
    with pytest.raises(ContractError,match='step_id'): resolve_bound_placement(c,p)


def test_all_claim_model_references_are_actual_dependencies():
    c,a,raw,caps=math_request_case()
    plan=plan_media_requirements(c,a,capabilities=caps,client=Planner(raw),author_requirements=author_media(c,raw))
    deps={r['pointer']:r['hash'] for r in plan['requests'][0]['dependency_paths']}
    assert '/math_models/0' in deps and '/teaching_plan/goals/0' in deps and '/teaching_plan/claims/0' in deps


def test_repair_cannot_rename_request_to_evade_frozen_standards():
    c,a,raw,caps=math_request_case(); plan=plan_media_requirements(c,a,capabilities=caps,client=Planner(raw),author_requirements=author_media(c,raw))
    raw['requests'][0]['request_key']='another-key'; raw['decisions'][0]['request_ids']=['another-key']
    raw['requests'][0]['acceptance_criteria'][0]['statement']='只要有图'
    with pytest.raises(ContractError,match='身份'):
        plan_media_requirements(c,a,capabilities=caps,client=Planner(raw),author_requirements=author_media(c,raw),repair_context={'previous_requirements':plan})


@pytest.mark.parametrize('field,value',[('goal_refs',7),('claim_ids',7),('math_model_refs',7),('math_model_refs',[{'model_id':[],'digest':'old'}])])
def test_malformed_model_output_is_a_contract_failure(field,value):
    c,a,raw,caps=math_request_case()
    plan=plan_media_requirements(c,a,capabilities=caps,client=Planner(raw),author_requirements=author_media(c,raw))
    plan['requests'][0][field]=value
    with pytest.raises(ContractError): validate_requirements(plan,c,a,capabilities=caps)


def test_unsupported_evidence_kind_is_rejected_before_execution():
    c,a,raw,caps=math_request_case('plot')
    raw['requests'][0]['acceptance_criteria'][0]['evidence_required']=['imaginary_proof']
    with pytest.raises(ContractError,match='evidence_required'):
        plan_media_requirements(c,a,capabilities=caps,client=Planner(raw),author_requirements=author_media(c,raw))
