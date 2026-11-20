"""Approved content, mathematical claims, geometry and worker bytes must agree."""
from copy import deepcopy
import dataclasses
from pathlib import Path
import pytest
from aiforstudy.errors import ContractError
from aiforstudy.blocks.visual.compiler import compile_scene
from aiforstudy.blocks.visual.spec import parse_visual_spec
from aiforstudy.blocks.visual.manim_adapter import compile_plan, load_trusted_scene
from test_semantic_static_media import context_case, const
from test_visual_spec_v2 import visual_spec

ROOT=Path(__file__).resolve().parents[1]


def animation_case():
    expr={'op':'div','left':{'op':'pow','base':{'op':'var','name':'x'},'exponent':2},'right':const(2)}
    ctx,*_=context_case(kind='animation',objects=[{'math_object_id':'f','kind':'scalar','expr':expr}],symbols=[{'name':'x','domain':'real','assumptions':[]}])
    v=visual_spec();v.update(request_id=ctx.request['request_id'],unit_id='U',goal_ids=['G'],claim_ids=['K'],math_model_refs=ctx.request['math_model_refs'])
    v['objects'][0].update(model_id='M')
    v['steps'][0].update(goal_id='G',claim_ids=['K'],caption_claim_ids=['K'],math_claim_refs=[{'model_id':'M','claim_id':'MC'}])
    return ctx,v


def test_v2_compiler_requires_explicit_approved_context():
    ctx,value=animation_case()
    with pytest.raises(ContractError,match='ApprovedMathContext'):compile_scene(parse_visual_spec(value),'draft')


def test_approved_ast_compiles_real_mathtex_and_shared_verifier_evidence():
    ctx,value=animation_case()
    scene=compile_scene(parse_visual_spec(value),'draft',math_context=ctx)
    payload=load_trusted_scene(scene.source,expected_source_hash=scene.source_hash)
    assert r'\frac{' in payload['objects']['formula']['tex']
    assert '^{2}' in payload['objects']['formula']['tex']
    assert payload['math_binding']['models'][0]['digest']==ctx.model_by_id('M').digest
    assert payload['math_binding']['reports'][0]['status']=='pass'
    assert payload['steps'][0]['goal_id']=='G'
    plan=compile_plan(scene,value,worker_dir=ROOT/'tools/manim_worker',math_context=ctx)
    assert plan['timeline'][0]['goal_id']=='G'
    assert {'before','after'} <= {x['phase'] for x in plan['timeline'][0]['evidence_moments']}


def test_digest_mismatch_and_unrelated_math_claim_cannot_compile():
    ctx,value=animation_case();value['math_model_refs'][0]['digest']='sha256:'+'b'*64
    with pytest.raises(ContractError,match='digest|摘要'):compile_scene(value,'draft',math_context=ctx)
    ctx,value=animation_case()
    ctx2,*_=context_case(kind='animation',objects=[{'math_object_id':'other','kind':'scalar','expr':const(1)},{'math_object_id':'f','kind':'scalar','expr':const(2)}])
    value['math_model_refs']=ctx2.request['math_model_refs']
    with pytest.raises(ContractError,match='unrelated|无关|完整.*对象'):compile_scene(value,'draft',math_context=ctx2)


def test_forged_compiled_tex_does_not_cross_worker_boundary():
    from aiforstudy.blocks.visual.compiler import _render_source,RenderProfile
    from aiforstudy.core.digest import digest_text
    ctx,value=animation_case();scene=compile_scene(value,'draft',math_context=ctx)
    payload=load_trusted_scene(scene.source);payload['objects']['formula']['tex']=r'\input{/etc/passwd}'
    source=_render_source(RenderProfile.DRAFT,payload)
    forged=dataclasses.replace(scene,source=source,source_hash=digest_text(source))
    with pytest.raises(ContractError,match='canonical|重新编译|可信'):compile_plan(forged,value,worker_dir=ROOT/'tools/manim_worker',math_context=ctx)


def test_math_bytes_change_invalidates_old_compiled_scene():
    ctx,value=animation_case();scene=compile_scene(value,'draft',math_context=ctx)
    changed,*_=context_case(kind='animation',objects=[{'math_object_id':'f','kind':'scalar','expr':const(3)}])
    value['math_model_refs']=changed.request['math_model_refs']
    with pytest.raises(ContractError):compile_plan(scene,value,worker_dir=ROOT/'tools/manim_worker',math_context=changed)


def test_planner_example_is_valid_and_matches_actual_capabilities():
    from aiforstudy.blocks.visual.planner_contract import visual_planner_contract
    contract=visual_planner_contract();example=contract['example']
    assert example['schema_version']=='2.0'
    assert 'screen' not in contract['spaces']
    assert 'render_profile' not in contract['root_fields']
    assert parse_visual_spec(example).steps


def test_renderer_accepts_approved_context_and_host_profiles():
    import inspect
    from aiforstudy.blocks.figures.animation import manim_renderer
    from aiforstudy.blocks.visual.manim_adapter import render_scene
    assert 'math_context' in inspect.signature(manim_renderer).parameters
    assert 'math_context' in inspect.signature(render_scene).parameters


def test_reveal_before_after_and_observe_sampling_keep_purpose():
    from aiforstudy.blocks.visual.worker import load_asset
    ctx,value=animation_case();scene=compile_scene(value,'draft',math_context=ctx)
    plan=compile_plan(scene,value,worker_dir=ROOT/'tools/manim_worker',math_context=ctx)
    builder=load_asset(ROOT/'tools/manim_worker','scene_builder')
    assert hasattr(builder,'evidence_moments'), 'worker must sample the actual action timeline, not arbitrary step offsets'
    moments=builder.evidence_moments(plan['timeline'][0],frame_rate=15)
    assert any(m['phase']=='before' and m['at']<0.35 for m in moments)
    assert any(m['phase']=='after' and m['at']>0.85 for m in moments)
    assert all(m['goal_id']=='G' and m['object_ids']==['formula'] for m in moments)


def test_vtt_cue_identifier_is_the_exact_step_id(tmp_path):
    from aiforstudy.blocks.visual.manim_adapter import write_vtt
    ctx,value=animation_case();scene=compile_scene(value,'draft',math_context=ctx)
    plan=compile_plan(scene,value,worker_dir=ROOT/'tools/manim_worker',math_context=ctx)
    path=tmp_path/'captions.vtt';write_vtt(path,plan)
    assert '\ns1\n00:00:00.000 --> 00:00:08.000\n' in path.read_text()


def test_frame_selection_keeps_both_sides_of_each_targeted_change():
    from aiforstudy.blocks.visual.frame_check import select_frames
    _,value=animation_case()
    resources=[{'kind':'keyframe','relative_path':phase+'.png','sha256':'sha256:'+str(i)*64,
                'step_id':'s1','timestamp':t,'phase':phase,'action':'reveal','object_id':'formula'}
               for i,(phase,t) in enumerate([('before',.28),('after',.92),('observe',4.)],1)]
    selected,_=select_frames(value,{'resources':resources},max_images=2)
    assert {x['phase'] for x in selected}=={'before','after'}
    with pytest.raises(ContractError):select_frames(value,{'resources':resources},max_images=1)
    with pytest.raises(ContractError):select_frames(value,{'resources':resources[1:]},max_images=2)


def test_curve_uses_shared_domain_segments_and_never_connects_across_zero():
    expr={'op':'div','left':const(1),'right':{'op':'var','name':'x'}}
    ctx,*_=context_case(kind='animation',objects=[{'math_object_id':'f','kind':'scalar','expr':expr}],
        symbols=[{'name':'x','domain':'real','assumptions':['nonzero']}])
    _,value=animation_case();value['math_model_refs']=ctx.request['math_model_refs']
    value['objects'][0].update(kind='curve',x_range=[-1,1],samples=128)
    scene=compile_scene(value,'draft',math_context=ctx)
    segments=scene.samples['objects']['formula']['segments']
    assert len(segments)==2
    assert all(p[0]<0 for p in segments[0]) and all(p[0]>0 for p in segments[1])


def test_frame_metadata_survives_strict_animation_resource_roundtrip():
    from aiforstudy.blocks.visual.result import AnimationResource
    value={'kind':'keyframe','relative_path':'keyframes/s1-0.png','sha256':'sha256:'+'a'*64,
           'bytes':10,'mime':'image/png','timestamp':.2,'step_id':'s1','phase':'before','action':'reveal',
           'object_id':'formula','goal_id':'G','claim_ids':['K'],'object_ids':['formula'],'observation':'看指数'}
    assert AnimationResource.from_dict(value).as_dict()==value
    with pytest.raises(ContractError):AnimationResource.from_dict({**value,'phase':'imaginary'})


def test_compiler_respects_pipeline_cancel_token():
    from aiforstudy.pipeline.services import CancelToken
    from aiforstudy.errors import Cancelled
    ctx,value=animation_case();cancel=CancelToken();cancel.cancel('test cancelled')
    with pytest.raises(Cancelled):compile_scene(value,'draft',math_context=ctx,cancel=cancel)


def test_authoritative_json_schema_accepts_example_and_rejects_model_profile():
    from aiforstudy.blocks.visual.planner_contract import visual_planner_contract
    import jsonschema
    contract=visual_planner_contract()
    assert 'schema' in contract
    jsonschema.Draft202012Validator.check_schema(contract['schema'])
    jsonschema.validate(contract['example'],contract['schema'])
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate({**contract['example'],'render_profile':'draft'},contract['schema'])


def derivative_animation_case():
    from aiforstudy.blocks.math import parse_math_model
    from aiforstudy.core.digest import digest_of
    from aiforstudy.blocks.media.media_requirements import semantic_dependencies
    ctx,value=animation_case()
    model=ctx.model_by_id('M').as_dict()
    model['objects'].append({'math_object_id':'df','kind':'scalar','expr':{'op':'var','name':'x'}})
    _,c,a,r=context_case(kind='animation',objects=model['objects'],symbols=model['symbols'])
    c['math_models'][0]['claims']=[{'claim_id':'MC','kind':'derivative','math_object_id':'f','variable':'x','order':1,'expected_object_id':'df'}]
    model=parse_math_model(c['math_models'][0]);r['math_model_refs']=[{'model_id':'M','digest':model.digest}]
    a['content_hash']=digest_of(c,check=False)
    semantic=semantic_dependencies(r,c);by_pointer={row['pointer']:row for row in r['dependency_paths']}
    by_pointer.update({row['pointer']:row for row in semantic});r['dependency_paths']=list(by_pointer.values())
    ctx=type(ctx).from_approved_content(c,a,r)
    value['math_model_refs']=r['math_model_refs'];value['objects'].append({'object_id':'derivative','kind':'formula','model_id':'M','math_object_id':'df'})
    value['steps'][0].update(object_ids=['formula','derivative'],observation='观察对平方项求导后，指数降低一次且分母二被系数抵消。',expected_change='平方的一半变为一次函数。',caption='对平方项的一半求导：指数变为一，系数二与分母二抵消。')
    value['steps'][0]['actions'].append({'action':'transform','object_id':'formula','target_object_id':'derivative'})
    value['steps'][0]['checkpoints']=[{'at':4,'note':'核对求导后剩下一次项'}]
    return ctx,value


def test_derivative_transform_shows_both_mathematical_objects_and_observe():
    ctx,value=derivative_animation_case()
    observe=deepcopy(value['steps'][0]);observe.update(step_id='s2',kind='observe',start=8,end=10,actions=[],checkpoints=[],object_ids=['derivative'])
    # Reading the already demonstrated derivative is a purposeful observe step.
    value['steps'].append(observe)
    scene=compile_scene(value,'draft',math_context=ctx)
    plan=compile_plan(scene,value,worker_dir=ROOT/'tools/manim_worker',math_context=ctx)
    assert plan['math_binding']['reports'][0]['checks'][0]['rule_id']=='math.derivative.v1'
    assert any(p.get('target_object_id')=='derivative' for p in plan['timeline'][0]['parts'])


def test_surface_does_not_ignore_approved_symbol_domain_assumptions():
    ctx,*_=context_case(kind='animation',objects=[{'math_object_id':'f','kind':'scalar','expr':{'op':'var','name':'x'}}],
        symbols=[{'name':'x','domain':'real','assumptions':['positive']}])
    _,value=animation_case();value['math_model_refs']=ctx.request['math_model_refs']
    value['objects'][0].update(kind='surface',domain={'x':[-1,1],'y':[-1,1]},resolution=[4,4])
    with pytest.raises(ContractError,match='domain|域'):compile_scene(value,'draft',math_context=ctx)


def test_visible_formulas_get_distinct_host_slots_and_transform_keeps_its_slot():
    ctx,value=derivative_animation_case()
    compare=deepcopy(value['steps'][0]);compare.update(step_id='s2',start=8,end=12,
        actions=[{'action':'reveal','object_id':'formula'}],checkpoints=[])
    value['steps'].append(compare)
    scene=compile_scene(value,'draft',math_context=ctx)
    plan=compile_plan(scene,value,worker_dir=ROOT/'tools/manim_worker',math_context=ctx)
    actions=[p for s in plan['timeline'] for p in s['parts'] if p['action'] in ('reveal','transform')]
    assert actions[0]['formula_slot']==actions[1]['formula_slot']
    assert actions[2]['formula_slot']!=actions[1]['formula_slot']


def test_failed_mixed_typography_probe_blocks_even_if_it_has_an_image_hash(monkeypatch,tmp_path):
    import aiforstudy.blocks.visual.manim_adapter as adapter
    ctx,value=animation_case();scene=compile_scene(value,'draft',math_context=ctx)
    services=adapter.default_services(workspace_root=ROOT,scratch_root=tmp_path)
    monkeypatch.setattr(adapter,'run_worker_job',lambda **kw: pytest.fail('failed typography must not render'))
    result=adapter.render_scene(scene,adapter.limits_for_profile(services.worker_dir,'draft'),services,
        spec=value,output_dir=tmp_path/'out',math_context=ctx,environment={
            'isolation':{'safe':True,'applied':{'landlock':{'backend':'bwrap'}}},
            'environment':{'math_tex':{'ok':False,'frame_hash':'sha256:'+'a'*64}}})
    assert result['status']=='blocked' and result['technical_report']['kind']=='typography'


def test_public_v2_parser_and_exact_planner_example_compile_with_approved_fixture():
    from aiforstudy.blocks.visual.spec import parse_spec
    from aiforstudy.blocks.visual.planner_contract import visual_planner_contract
    from aiforstudy.blocks.media.media_requirements import semantic_dependencies
    from aiforstudy.core.digest import digest_of
    contract=visual_planner_contract();example=contract['example'];model=contract['example_math_model']
    ctx,c,a,r=context_case(kind='animation',objects=model['objects'],symbols=model['symbols'])
    c['math_models']=[model];c['unit_id']=example['unit_id']
    c['teaching_plan']['claims'][0]['math_model_refs']=[model['model_id']]
    a.update(unit_id=c['unit_id'],content_hash=digest_of(c,check=False))
    r.update(request_id=example['request_id'],math_model_refs=example['math_model_refs'],
        object_refs=[{'model_id':model['model_id'],'object_id':'f'}])
    r['dependency_paths']=semantic_dependencies(r,c)
    context=type(ctx).from_approved_content(c,a,r)
    assert compile_scene(parse_spec(example),'draft',math_context=context).captions==tuple(s['caption'] for s in example['steps'])


def test_production_adapter_preserves_v2_binding_renders_both_profiles_and_reuses_only_evidence(monkeypatch,tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from aiforstudy.pipeline import media,stage_io
    from aiforstudy.pipeline.services import CancelToken
    from aiforstudy.blocks.visual.result import AnimationResult,AnimationResource
    from aiforstudy.core.digest import digest_of,digest_file
    context,spec=derivative_animation_case();data=context._data();request=context.request
    request['spec']=spec;before=deepcopy(request);calls=[];probes=[]
    ctx=SimpleNamespace(workspace=SimpleNamespace(root=ROOT),node=SimpleNamespace(stage='figures'),
        services=SimpleNamespace(cancel=CancelToken(),control=SimpleNamespace(check=lambda:None,work_scope=lambda scope:nullcontext()),
            store=SimpleNamespace(work=tmp_path,run_id='offline-test')))
    monkeypatch.setattr(media,'_animation_runtime_identity',lambda ctx: probes.append(True) or {'environment':'test'})
    monkeypatch.setattr(stage_io,'_gateway_client',lambda *a,**kw: pytest.fail('request already has approved spec; no paid call'))
    def renderer_factory(**kw):
        assert kw['math_context'].request==request
        def render(profile,planned):
            calls.append(profile);assert planned==spec
            base=tmp_path/profile;base.mkdir(exist_ok=True);(base/'video.mp4').write_bytes(b'boundary fixture')
            result=AnimationResult(request_id=request['request_id'],status='needs_review',
                spec_hash=parse_visual_spec(spec).spec_hash,compiler_hash='sha256:'+'a'*64,environment_hash='sha256:'+'b'*64,
                math_report={'status':'pass'},technical_report={'ffmpeg_decode_ok':True},
                resources=(AnimationResource(kind='mp4',relative_path='video.mp4',sha256=digest_file(base/'video.mp4'),bytes=16,mime='video/mp4'),))
            return result,str(base)
        return render
    monkeypatch.setattr(media.animation,'manim_renderer',renderer_factory)
    pairs=[('body',SimpleNamespace(request_id=request['request_id']),request)]
    caps=SimpleNamespace(render_backends=lambda:['manim'])
    rows,payloads,tickets,_=media.fulfill(ctx,pairs,data['content'],{},caps,content_approval=data['approval'])
    assert calls==['draft','final'] and not tickets and not payloads
    row=rows[0];assert row['state']=='needs_review' and row['request_hash']==digest_of(before)
    assert request==before and row['criteria_digest']==request['criteria_digest']
    assert all(digest_of(dep['value'])==dep['hash'] for dep in row['approved_dependencies'])
    cached,*_=media.fulfill(ctx,pairs,data['content'],{'requests':rows},caps,content_approval=data['approval'])
    assert cached[0]['cache_hit'] is True and cached[0]['state']=='needs_review'
    assert calls==['draft','final'] and len(probes)==2
    # A physical resource ticket restores the frozen scene, never pays the
    # planner to rewrite a scene whose original semantic checks already passed.
    from aiforstudy.pipeline.diagnostics import technical_ticket
    ticket=technical_ticket(unit_id='U',artifact_id='media:'+request['request_id'],
        artifact_hash=digest_of(row['visual_spec']),problem='video bytes corrupt',
        source=data['content']['body_markdown'],owner='visual_renderer',kind='visual_math',
        allowed_paths=('/resources',),rule='MEDIA_RESOURCE_INVALID:RESOURCE_INVALID',
        action='restore frozen scene resources',expected='same scene and valid resources').as_dict()
    ctx.repair_batch=SimpleNamespace(jobs=[SimpleNamespace(ticket_ids=[ticket['ticket_id']])])
    (tmp_path/'final/video.mp4').write_bytes(b'corrupt')
    restored,_,tickets,_=media.fulfill(ctx,pairs,data['content'],
        {'requests':rows,'tickets':[ticket]},caps,content_approval=data['approval'])
    assert not tickets and calls==['draft','final','draft','final']
    assert restored[0]['state']=='needs_review'
    assert restored[0]['visual_spec']==row['visual_spec'] and request==before
    assert media.valid_cached(restored[0])
