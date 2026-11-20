from types import SimpleNamespace
from pathlib import Path
import json
import pytest
from aiforstudy.core.digest import digest_of
from aiforstudy.errors import ContractError
from support.content_case import approved_content as fixture, Planner, none_decision, no_media_requirements, author_media


def ctx(tmp_path,stage):
    from support.content_case import teaching_case
    parse=tmp_path/'parse';parse.mkdir(exist_ok=True)
    config=tmp_path/'config';config.mkdir(exist_ok=True)
    (config/'capabilities.json').write_text(json.dumps({'browser_acceptance':{
        'playwright_module':str(Path(__file__).resolve().parents[1]/'node_modules/playwright')}}))
    (parse/'source.md').write_text(teaching_case()[1]['source_text'])
    (parse/'chapters.json').write_text(json.dumps({'lesson_units':[{'unit_id':'U','title':'记号约定','original_text':'source.md'}]}))
    return SimpleNamespace(node=SimpleNamespace(stage=stage,scope_key='U',node_id=stage+':U'),context=SimpleNamespace(book_id='B',revision='source',parse=parse),workspace=SimpleNamespace(root=tmp_path),services=SimpleNamespace(store=SimpleNamespace(work=tmp_path,run_id='test'),cancel=SimpleNamespace(checkpoint=lambda:None),control=SimpleNamespace(check=lambda:None)),repair_batch=None)


def test_no_media_stage_chain_creates_receipts_without_resource_calls(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media_stages as M
    content,approval=fixture()
    monkeypatch.setattr(M,'_load_content',lambda c:(content,approval))
    monkeypatch.setattr(M,'_load_author_media',lambda *a,**k:author_media(content))
    monkeypatch.setattr(M,'_capabilities',lambda c:{'kinds':[]})
    monkeypatch.setattr(M,'_client',lambda *a,**k:Planner())
    assert M.media_plan_stage(ctx(tmp_path,'media_plan')).quality_state.value=='accepted'
    assert M.figures_stage(ctx(tmp_path,'figures')).execution_state.value=='not_applicable'
    assert M.audit_stage(ctx(tmp_path,'audit')).quality_state.value=='accepted'
    assert M.interaction_stage(ctx(tmp_path,'interaction')).quality_state.value=='accepted'
    plan=json.loads((tmp_path/'products/media_plan/U.json').read_text())['payload']
    assert plan['requests']==[]
    assert json.loads((tmp_path/'products/audit/U.json').read_text())['payload']['approval']['decision']=='accepted'


def test_stage_invalid_approval_never_creates_plan(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media_stages as M
    def reject(c): raise ContractError('approval mismatch')
    monkeypatch.setattr(M,'_load_content',reject)
    outcome=M.media_plan_stage(ctx(tmp_path,'media_plan'))
    assert outcome.execution_state.value=='blocked'
    assert json.loads((tmp_path/'products/media_plan/U.json').read_text())['payload']['review_outcome']['status']=='missing'


def test_planner_receives_real_source_image_index(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media_stages as M
    from aiforstudy.core.jsonio import write_json_atomic
    c=ctx(tmp_path,'media_plan'); c.context.parse=tmp_path/'parse'; c.context.parse.mkdir(exist_ok=True)
    picture=c.context.parse/'figure.png'
    from PIL import Image
    Image.new('RGB',(40,40),'blue').save(picture)
    write_json_atomic(c.context.parse/'images.json',{'images':[{'id':'IMG-1','file':'figure.png','caption':'教材圆图'}]})
    assets=M._source_assets(c)
    assert assets['source_images'][0]['asset_id']=='IMG-1'
    assert assets['source_images'][0]['file_hash'].startswith('sha256:')


def test_retired_arbitrary_svg_fails_before_any_spec_paid_call(tmp_path):
    from aiforstudy.blocks.media.execution import execute_static
    class Forbidden:
        def chat_json(self,*a,**k): raise AssertionError('paid before image preflight')
    row=execute_static({'request_id':'R','kind':'svg'},content={},out_dir=tmp_path,client=Forbidden(),reviewer=None)
    assert row['state']=='failed' and 'SVG/code is retired' in row['reason']


def test_invalid_plan_is_persisted_and_routes_to_media_plan(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media_stages as M
    from test_media_decisions_v2 import math_request_case
    content,approval,raw,caps=math_request_case()
    authored=author_media(content,raw)
    monkeypatch.setattr(M,'_load_content',lambda c:(content,approval))
    monkeypatch.setattr(M,'_load_author_media',lambda *a,**k:authored)
    monkeypatch.setattr(M,'_capabilities',lambda c:caps)
    # A technical compiler omits a real author request; the author intent survives.
    client=SimpleNamespace(chat_json=lambda *a,**k:{'data':{'requests':[]}})
    monkeypatch.setattr(M,'_client',lambda *a,**k:client)
    result=M.media_plan_stage(ctx(tmp_path,'media_plan'))
    assert result.quality_state.value=='needs_review'
    assert result.repair_routes[0]['target_artifact_kind']=='media_requirements'
    payload=json.loads((tmp_path/'products/media_plan/U.json').read_text())['payload']
    assert payload['candidate']['author_media']==authored and payload['tickets']
    assert payload['candidate']['requests'][0]['request_key']=='zero-position'
    ticket=payload['tickets'][0]
    assert set(ticket['allowed_paths'])=={
        '/requests/0/'+field for field in ('kind','source_image_id','source_refs','spec','library_id')}
    assert {'/decisions','/author_media','/requests/0/request_key',
            '/requests/0/acceptance_criteria'}<=set(ticket['preserve'])
    assert '技术编译' in ticket['repair_instructions']['action']
    assert '不得修改 decisions' in ticket['repair_instructions']['action']
    assert '每个作者 request_key 恰有一个技术项' in ticket['repair_instructions']['expected_change']


def test_missing_interaction_capability_cannot_authorize_rewriting_author_decisions(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media_stages as M
    content,approval=fixture()
    decision=none_decision();decision.update(requirement='required',handled_by='interaction')
    authored=author_media(content,{'decisions':[decision],'requests':[]})
    monkeypatch.setattr(M,'_load_content',lambda c:(content,approval))
    monkeypatch.setattr(M,'_load_author_media',lambda *a,**k:authored)
    monkeypatch.setattr(M,'_capabilities',lambda c:{'kinds':[],'interaction':False})
    def forbidden(*a,**k): raise AssertionError('No technical request authorizes a compiler call')
    monkeypatch.setattr(M,'_client',forbidden)
    result=M.media_plan_stage(ctx(tmp_path,'media_plan'))
    assert result.execution_state.value=='blocked'
    assert '交互能力未声明' in str(result.error)
    assert not result.repair_requests and not result.repair_routes
    assert json.loads((tmp_path/'products/media_plan/U.json').read_text())['payload']['review_outcome']['status']=='missing'


@pytest.mark.parametrize('has_source',[False,True])
def test_image_without_math_bindings_requires_source_before_compiler_call(tmp_path,monkeypatch,has_source):
    from PIL import Image
    from aiforstudy.pipeline import media_stages as M
    from test_media_decisions_v2 import math_request_case
    content,approval=fixture()
    _,_,raw,_=math_request_case('source_image')
    request=raw['requests'][0]
    request.update(math_model_refs=[],object_refs=[],claim_ids=[])
    request['acceptance_criteria'][0].update(kind='source',evidence_required=['source_provenance','rendered_frame'])
    context=ctx(tmp_path,'media_plan')
    if has_source:
        Image.new('RGB',(40,40),'blue').save(context.context.parse/'figure.png')
        (context.context.parse/'images.json').write_text(json.dumps({'images':[{'id':'IMG','file':'figure.png'}]}))
    assets=M._source_assets(context)
    caps={'kinds':['source_image','geometry'],'source_assets':assets}
    if has_source:
        asset=assets['source_images'][0]
        request['source_image_id']=asset['asset_id']
        request['source_refs'].append({'kind':'asset','asset_id':asset['asset_id'],'hash':asset['hash']})
    authored=author_media(content,raw)
    planner=Planner(raw)
    monkeypatch.setattr(M,'_load_content',lambda c:(content,approval))
    monkeypatch.setattr(M,'_load_author_media',lambda *a,**k:authored)
    monkeypatch.setattr(M,'_capabilities',lambda c:caps)
    monkeypatch.setattr(M,'_client',lambda *a,**k:planner)
    result=M.media_plan_stage(context)
    if has_source:
        assert result.quality_state.value=='accepted',result.error
        assert planner.calls==1
        plan=json.loads((tmp_path/'products/media_plan/U.json').read_text())['payload']
        assert plan['author_media']==authored
        assert plan['requests'][0]['kind']=='source_image'
        assert plan['requests'][0]['source_image_id']=='IMG'
    else:
        assert result.execution_state.value=='blocked'
        assert planner.calls==0
        assert all(text in str(result.error) for text in ('zero-position','批准数学模型/对象','教材源图','正文/作者','none'))
        assert not result.repair_requests and not result.repair_routes
        assert json.loads((tmp_path/'products/media_plan/U.json').read_text())['payload']['review_outcome']['status']=='missing'


def test_incomplete_interaction_assignment_dispatches_own_repair(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media_stages as M
    from aiforstudy.core.jsonio import write_json_atomic
    content,approval=fixture();monkeypatch.setattr(M,'_load_content',lambda c:(content,approval))
    monkeypatch.setattr(M,'_load_author_media',lambda *a,**k:author_media(content));monkeypatch.setattr(M,'_capabilities',lambda c:{'kinds':[]})
    decision=none_decision(); decision.update(requirement='required', handled_by='interaction')
    plan=no_media_requirements(content,approval,capabilities={'kinds':[], 'interaction':True},decision=decision)
    monkeypatch.setattr(M,'_load_author_media',lambda *a,**k:plan['author_media'])
    manifest={'schema_version':'MediaManifestV1','content_hash':approval['content_hash'],'requirements_hash':digest_of(plan),'requests':[]}
    from aiforstudy.blocks.media.media_manifest import approve_media
    media,_=approve_media(plan,manifest,approval)
    write_json_atomic(tmp_path/'products/media_plan/U.json',{'payload':plan});write_json_atomic(tmp_path/'products/figures/U.json',{'payload':manifest});write_json_atomic(tmp_path/'products/audit/U.json',{'payload':{'approval':media}})
    monkeypatch.setattr(M,'validate_requirements',lambda *a,**k:{'ok':True});monkeypatch.setattr(M,'_client',lambda *a,**k:object())
    from aiforstudy.pipeline import stage_io as T
    monkeypatch.setattr(T,'_legacy',lambda name:SimpleNamespace(generate_assigned_models=lambda *a,**k:{'models':[],'results':[]}))
    context=ctx(tmp_path,'interaction');context.context.parse=tmp_path
    outcome=M.interaction_stage(context)
    assert outcome.quality_state.value=='needs_review', outcome.error
    assert outcome.repair_requests and outcome.repair_routes[0]['target_artifact_kind']=='interaction_spec'


def test_content_not_passed_blocks_media_plan_and_says_why(tmp_path,monkeypatch):
    """R1（用户选 A）：正文第一道审核**没过**（known_error / unverified）就不做媒体。

    这是与旧 D1 决策相反的一处：旧实现刻意不拦，于是给"确定有错误"的正文照样配图。
    现在只在**审核明确判了没过**时才拦 —— 没有审核结论不算没过（首跑/复用旧产物不能停摆）。
    """
    from aiforstudy.pipeline import media_stages as M
    content,approval=fixture()
    monkeypatch.setattr(M,'_load_content',lambda c:(content,approval))
    monkeypatch.setattr(M,'_load_author_media',lambda *a,**k:author_media(content))
    monkeypatch.setattr(M,'_capabilities',lambda c:{'kinds':[]})
    monkeypatch.setattr(M,'_client',lambda *a,**k:Planner())
    context=ctx(tmp_path,'media_plan')
    audit=tmp_path/'products'/'text_audit';audit.mkdir(parents=True)
    (audit/'U.json').write_text(json.dumps({'payload':{
        'review_outcome':{'status':'known_error','reasons':['缺少 approach，需要可用的教学内容。']}}},
        ensure_ascii=False))
    result=M.media_plan_stage(context)
    assert result.quality_state.value=='needs_review'
    assert any(row.get('code')=='MEDIA_BLOCKED_BY_CONTENT' for row in result.findings),result.findings
    assert not (tmp_path/'products'/'media_plan'/'U.json').exists(), '未过审的正文不得产出媒体计划'


def test_unverified_content_also_stops_media(tmp_path,monkeypatch):
    """unverified 同样算"没过"（R1 覆盖 known_error 与 unverified）。"""
    from aiforstudy.pipeline import media_stages as M
    content,approval=fixture()
    monkeypatch.setattr(M,'_load_content',lambda c:(content,approval))
    monkeypatch.setattr(M,'_load_author_media',lambda *a,**k:author_media(content))
    monkeypatch.setattr(M,'_capabilities',lambda c:{'kinds':[]})
    monkeypatch.setattr(M,'_client',lambda *a,**k:Planner())
    context=ctx(tmp_path,'media_plan')
    audit=tmp_path/'products'/'text_audit';audit.mkdir(parents=True)
    (audit/'U.json').write_text(json.dumps({'payload':{
        'review_outcome':{'status':'unverified','reasons':['证据引文不是逐字。']}}},ensure_ascii=False))
    result=M.media_plan_stage(context)
    assert any(row.get('code')=='MEDIA_BLOCKED_BY_CONTENT' for row in result.findings)
