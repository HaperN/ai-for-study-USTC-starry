"""Code production boundaries: review-only reuse, final authority and resume cap."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from aiforstudy.core.digest import digest_file, digest_of
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.errors import Cancelled, ContractError
from test_media_code_contracts import approved_case


def test_invalid_model_metadata_is_recorded_without_poisoning_host_receipts(tmp_path,monkeypatch):
    from aiforstudy.blocks.media.code_execution import execute_code
    from aiforstudy.blocks.media.code_generation import MediaCodeGenerationError
    from aiforstudy.blocks.media import code_generation, code_runtime
    from aiforstudy.blocks.media.evidence_digest import manifest_digest
    c,a,request,program=approved_case(); calls=[]
    def invalid(*args,**kwargs):
        calls.append(kwargs)
        raise MediaCodeGenerationError('unexpected model timestamp',{'timestamp':42,'status':'pass'})
    monkeypatch.setattr(code_generation,'generate_media_code',invalid)
    monkeypatch.setattr(code_runtime,'preflight_generated_code',lambda *a,**k:{'ok':True})
    row=execute_code(request,content=c,approval=a,out_dir=tmp_path,client=object(),reviewer=object())
    assert len(calls)==3 and row['state']=='failed'
    assert 'candidate_spec' not in row and 'timestamp' in row['invalid_candidate_json']
    assert calls[1]['feedback']['invalid_candidate_json']
    assert manifest_digest({'requests':[row]}).startswith('sha256:')


def test_lost_resource_uses_frozen_python_without_another_model_call(tmp_path,monkeypatch):
    from aiforstudy.blocks.media.code_execution import execute_code
    c,a,request,program=approved_case(); calls=mock_runtime(monkeypatch,program)
    kwargs=dict(content=c,approval=a,out_dir=tmp_path,client=object(),reviewer=object())
    old=execute_code(request,**kwargs)
    (Path(old['source_dir'])/old['resources'][0]['relative_path']).unlink()
    restored=execute_code(request,previous=old,**kwargs)
    assert restored['state']=='fulfilled' and calls['generate']==1
    assert restored['candidate_spec']==old['candidate_spec'] and len(calls['render'])==2


def mock_runtime(monkeypatch, program, *, final_blocked=False):
    from aiforstudy.blocks.media import code_generation, code_runtime, validation
    calls = {'generate':0, 'render':[], 'review':0}

    def generate(*args, **kwargs):
        calls['generate'] += 1
        return deepcopy(program)

    def render(candidate, *, out_dir, profile, **kwargs):
        calls['render'].append(profile)
        if final_blocked and profile == 'final':
            return {'status':'blocked','reason':'final worker unavailable','resources':[]}
        root = Path(out_dir)
        root.mkdir(parents=True, exist_ok=True)
        resources = []
        if kwargs['context']['request']['kind'] == 'animation':
            kinds = [('mp4','clip.mp4')]
        else:
            kinds = [('svg','figure.svg'),('image','figure.png')] if candidate['backend'] == 'matplotlib' else [('image','figure.png')]
        for kind, name in kinds:
            path = root/name
            path.write_bytes(b'offline resource bytes')
            resources.append({'kind':kind,'relative_path':name,'sha256':digest_file(path)})
        steps = [{**step,'start':float(i),'end':float(i+1)} for i,step in enumerate(candidate['steps'])]
        return {'status':'ok','reason':'','resources':resources,'steps':steps,
                'technical_report':{'ok':True},'source_dir':str(root),
                'compiler_hash':'sha256:compiler','environment_hash':'sha256:environment'}

    def review(request, row, **kwargs):
        calls['review'] += 1
        return {'ok':True,'decision':'accepted','issues':[],'review_problems':[],
                'result_hash':validation.result_digest(row),'review_items':[],
                'verdicts':[],'evidence_catalog':{},'math_report':{'status':'pass'}}

    def validate_report(request, row, report, **kwargs):
        if not report or not report.get('ok') or report.get('result_hash') != validation.result_digest(row):
            raise ContractError('stale offline test receipt')
        return True

    monkeypatch.setattr(code_generation,'generate_media_code',generate)
    monkeypatch.setattr(code_runtime,'preflight_generated_code',lambda *a,**k:{'ok':True})
    monkeypatch.setattr(code_runtime,'render_generated_code',render)
    monkeypatch.setattr(validation,'validate_media',review)
    monkeypatch.setattr(validation,'validate_media_report',validate_report)
    return calls


def test_missing_backend_blocks_before_spending_api_or_candidate_budget(tmp_path,monkeypatch):
    from aiforstudy.blocks.media import code_runtime
    from aiforstudy.blocks.media.code_execution import execute_code
    from aiforstudy.errors import CapabilityError
    content,approval,request,program=approved_case()
    calls=mock_runtime(monkeypatch,program)
    def missing(*args,**kwargs):
        raise CapabilityError('configured render interpreter is missing')
    monkeypatch.setattr(code_runtime,'preflight_generated_code',missing)
    kwargs=dict(content=content,approval=approval,out_dir=tmp_path,client=object(),reviewer=object())
    blocked=execute_code(request,**kwargs)
    assert blocked['state']=='blocked' and blocked['code_attempts']==[]
    assert 'interpreter is missing' in blocked['reason']
    assert calls=={'generate':0,'render':[],'review':0}
    assert json.loads((tmp_path/'preflight.json').read_text())['ok'] is False
    monkeypatch.setattr(code_runtime,'preflight_generated_code',lambda *a,**k:{'ok':True})
    resumed=execute_code(request,previous=blocked,**kwargs)
    assert resumed['state']=='fulfilled' and len(resumed['code_attempts'])==1
    assert calls=={'generate':1,'render':['final'],'review':1}


def configure_stage(tmp_path, monkeypatch, content, approval, request, dependencies):
    from test_media_plan_stage import ctx
    from aiforstudy.pipeline import media_stages as stages
    from aiforstudy.blocks.media import validation
    from aiforstudy.blocks.media.media_planning import plan_media_requirements
    from aiforstudy.contracts.artifact import ArtifactRef
    from support.content_case import author_media, none_decision, reviewed_plan
    context = ctx(tmp_path,'figures')
    capabilities = {'code_generation':'GeneratedMediaCodeV1','kinds':[request['kind']]}
    decision = {**none_decision(), 'requirement':'recommended',
                'request_ids':[request['request_key']]}
    record = author_media(content, {'decisions':[decision], 'requests':[request]})
    plan = reviewed_plan(content, plan_media_requirements(content, approval,
        author_requirements=record, capabilities=capabilities))
    assert plan['requests'] == [request], 'test fixture must preserve the exact supplied code request'
    monkeypatch.setattr(stages,'_load_content',lambda _: (content,approval))
    monkeypatch.setattr(stages,'_load',lambda _, stage: plan)
    monkeypatch.setattr(stages,'_capabilities',lambda _: capabilities)
    monkeypatch.setattr(stages,'resource_dependencies',lambda *a,**k: deepcopy(dependencies))
    monkeypatch.setattr(stages,'_client',lambda *a,**k: object())
    monkeypatch.setattr(stages,'_persisted_tickets',lambda *a,**k: {})
    monkeypatch.setattr(stages,'_tickets',lambda *a,**k: ([],(),()))
    monkeypatch.setattr(validation,'display_runtime',lambda *a,**k: None)

    def emit(ctx, stage, payload, kind, inputs):
        path = stages._path(ctx,stage)
        write_json_atomic(path,{'payload':deepcopy(payload)})
        return ArtifactRef(book_id='B',kind=kind,logical_key='unit:U',path=path)

    monkeypatch.setattr(stages,'_emit',emit)
    return stages, context


def test_review_dependency_change_reuses_code_and_bytes_without_new_generation(tmp_path,monkeypatch):
    from aiforstudy.blocks.media.code_execution import execute_code
    content, approval, request, program = approved_case()
    calls = mock_runtime(monkeypatch,program)
    dependencies = {'generation_dependency_hash':'generation-v1','review_dependency_hash':'review-v1'}
    previous = execute_code(request,content=content,approval=approval,out_dir=tmp_path,
                            client=object(),reviewer=object(),dependencies=dependencies)
    assert previous['state'] == 'fulfilled'
    dependencies['review_dependency_hash'] = 'review-v2'
    renewed = execute_code(request,content=content,approval=approval,out_dir=tmp_path,
                           client=object(),reviewer=object(),dependencies=dependencies,
                           previous=previous,evidence_only=True)
    assert renewed['state'] == 'fulfilled'
    assert renewed['resources'] == previous['resources']
    assert renewed['review_dependency_hash'] == 'review-v2'
    assert calls == {'generate':1,'render':['final'],'review':2}


@pytest.mark.parametrize('changed', ['generation_dependency_hash', 'review_dependency_hash'])
def test_newer_persisted_pending_review_blocks_dependency_upgrade_before_writes(tmp_path,monkeypatch,changed):
    from aiforstudy.blocks.media.code_execution import execute_code
    from aiforstudy.blocks.media.validation import result_digest
    from aiforstudy.errors import CapabilityError
    content,approval,request,program=approved_case()
    calls=mock_runtime(monkeypatch,program)
    dependencies={'generation_dependency_hash':'generation-v1','review_dependency_hash':'review-v1'}
    kwargs=dict(content=content,approval=approval,out_dir=tmp_path,client=object(),reviewer=object())
    stale_manifest=execute_code(request,dependencies=dependencies,**kwargs)
    actual=deepcopy(stale_manifest)
    actual.update(state='needs_review',reason='实际结果尚缺补审证据',review={'ok':False,'problems':[]})
    actual['validation']={'ok':False,'decision':'needs_review','result_hash':result_digest(actual),
        'issues':[{'code':'MEDIA_REVIEW_NOT_RUN','message':'实际结果尚缺补审证据'}],
        'verdicts':[],'review_problems':[]}
    write_json_atomic(tmp_path/'result.json',actual)
    before=(tmp_path/'result.json').read_bytes()
    dependencies[changed]='upgraded'
    with pytest.raises(CapabilityError,match='待补审'):
        execute_code(request,previous=stale_manifest,dependencies=dependencies,
            evidence_only=changed=='review_dependency_hash',**kwargs)
    assert calls=={'generate':1,'render':['final'],'review':1}
    assert (tmp_path/'result.json').read_bytes()==before


def test_new_approved_content_context_invalidates_cache_even_when_request_is_unchanged(tmp_path,monkeypatch):
    from aiforstudy.blocks.media.math_context import ApprovedMathContext
    content, approval, request, program = approved_case()
    calls = mock_runtime(monkeypatch,program)
    deps = {'generation_dependency_hash':'generation-v1','review_dependency_hash':'review-v1'}
    stages, context = configure_stage(tmp_path,monkeypatch,content,approval,request,deps)
    assert stages.figures_stage(context).quality_state.value == 'accepted'
    old = json.loads(stages._path(context,'figures').read_text())['payload']['requests'][0]
    assert stages.figures_stage(context).quality_state.value == 'accepted'
    assert calls['generate'] == 1, 'unchanged approved context should retain normal cache reuse'

    # This field is outside the request's semantic dependency slice, but it was
    # part of the complete approved_content sent to the code-generation model.
    content['summary'] = '新的批准总结：比较方向与位置时仍要保留数学条件。'
    content['revision'] += 1
    approval.update(content_hash=digest_of(content,check=False),
                    content_revision=content['revision'],
                    reader_document_hash=digest_of({'updated_summary':content['summary']}))
    stages, context = configure_stage(tmp_path,monkeypatch,content,approval,request,deps)
    assert stages.figures_stage(context).quality_state.value == 'accepted'
    current = json.loads(stages._path(context,'figures').read_text())['payload']['requests'][0]
    assert current['request_hash'] == old['request_hash']
    assert current['generation_dependency_hash'] == old['generation_dependency_hash']
    assert calls['generate'] == 2
    assert current['math_context_digest'] == ApprovedMathContext.from_approved_content(content,approval,request).digest
    assert current['math_context_digest'] != old['math_context_digest']


def test_final_render_failure_cannot_publish_accepted_draft_through_figures_stage(tmp_path,monkeypatch):
    content, approval, request, program = approved_case('animation')
    calls = mock_runtime(monkeypatch,program,final_blocked=True)
    deps = {'generation_dependency_hash':'generation-v1','review_dependency_hash':'review-v1'}
    stages, context = configure_stage(tmp_path,monkeypatch,content,approval,request,deps)
    result = stages.figures_stage(context)
    payload = json.loads(stages._path(context,'figures').read_text())['payload']
    row = payload['requests'][0]
    assert calls['render'] == ['draft','final']
    assert row['state'] != 'fulfilled', 'a passing draft receipt cannot authorize a failed final render'
    assert result.quality_state.value != 'accepted'
    assert payload['animation_payload'][content['unit_id']] == []


def test_cancelled_stage_resumes_persisted_attempt_budget_before_stage_manifest(tmp_path,monkeypatch):
    from aiforstudy.blocks.media import code_generation
    content, approval, request, program = approved_case()
    calls = mock_runtime(monkeypatch,program)
    deps = {'generation_dependency_hash':'generation-v1','review_dependency_hash':'review-v1'}
    stages, context = configure_stage(tmp_path,monkeypatch,content,approval,request,deps)

    def exhausted_generation(*args, **kwargs):
        calls['generate'] += 1
        if calls['generate'] == 3:
            raise Cancelled('cancelled after the third generation was dispatched')
        raise ContractError('unusable program')

    monkeypatch.setattr(code_generation,'generate_media_code',exhausted_generation)
    with pytest.raises(Cancelled):
        stages.figures_stage(context)
    assert calls['generate'] == 3
    assert not stages._path(context,'figures').exists()
    saved = tmp_path/'media'/str(context.node.scope_key)/request['request_id']/'result.json'
    assert len(json.loads(saved.read_text())['code_attempts']) == 3
    resumed = stages.figures_stage(context)
    assert calls['generate'] == 3, 'resume must not reset the paid candidate budget when the stage manifest was never emitted'
    assert resumed.quality_state.value != 'accepted'
    row = json.loads(stages._path(context,'figures').read_text())['payload']['requests'][0]
    assert len(row['code_attempts']) == 3
