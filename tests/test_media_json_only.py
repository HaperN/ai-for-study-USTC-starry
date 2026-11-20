"""The reversible media freeze still produces files, without inventing approval."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiforstudy.core.review_mode import review_mode
from aiforstudy.errors import ContractError
from test_media_code_contracts import approved_case, Client
from test_media_code_execution import mock_runtime


def test_code_json_only_keeps_extra_metadata_and_defers_source_errors_to_worker():
    from aiforstudy.blocks.media.code_contracts import validate_media_code
    _, _, request, program = approved_case()
    program.update(request_id='unbound', object_bindings=[], timestamp=1, code='def broken(:')
    with review_mode('json_only'):
        assert validate_media_code(program, request) == program
    with review_mode('full'), pytest.raises(ContractError):
        validate_media_code(program, request)


def test_code_json_only_still_rejects_non_json_numbers():
    from aiforstudy.blocks.media.code_contracts import validate_media_code
    _, _, request, program = approved_case()
    program['extra'] = float('nan')
    with review_mode('json_only'), pytest.raises(ContractError, match='JSON'):
        validate_media_code(program, request)


def test_generation_uses_raw_context_without_approval_or_placement_review():
    from aiforstudy.blocks.media.code_generation import generate_media_code
    content, _, request, program = approved_case()
    request.update(placement={'target_pointer':'/unbound'}, math_model_refs=[], object_refs=[])
    program['object_bindings'] = []
    with review_mode('json_only'):
        assert generate_media_code(request, content=content, approval={}, client=Client(program)) == program
    with review_mode('full'), pytest.raises(ContractError):
        generate_media_code(request, content=content, approval={}, client=Client(program))


@pytest.mark.parametrize('kind', ['drawing', 'animation'])
def test_execution_keeps_rendered_resources_without_any_review_receipt(tmp_path, monkeypatch, kind):
    from aiforstudy.blocks.media.code_execution import execute_code
    content, _, request, program = approved_case(kind)
    calls = mock_runtime(monkeypatch, program)
    request['dependency_paths'][0]['hash'] = 'stale'
    with review_mode('json_only'):
        row = execute_code(request, content=content, approval={}, out_dir=tmp_path,
                           client=object(), reviewer=None)
    assert row['state'] == 'fulfilled'
    assert row['resources'] and all((Path(row['source_dir']) / r['relative_path']).is_file() for r in row['resources'])
    assert row['review_outcome']['status'] == 'unverified'
    assert 'validation' not in row and 'review' not in row
    assert calls['review'] == 0 and calls['render'] == ['final']
    assert row['code_product']['input_binding_status'] == 'not_run'
    assert row['code_product']['mathematical_status'] == 'not_run'


def test_execution_preserves_worker_failure_as_execution_failure(tmp_path, monkeypatch):
    from aiforstudy.blocks.media import code_runtime
    from aiforstudy.blocks.media.code_execution import execute_code
    content, approval, request, program = approved_case()
    calls = mock_runtime(monkeypatch, program)
    monkeypatch.setattr(code_runtime, 'render_generated_code', lambda *a, **k: {
        'status':'failed', 'reason':'NameError: missing_variable', 'resources':[]})
    with review_mode('json_only'):
        row = execute_code(request, content=content, approval=approval, out_dir=tmp_path,
                           client=object(), reviewer=None)
    assert row['state'] == 'failed' and 'NameError' in row['reason']
    assert 'validation' not in row and calls['review'] == 0


def test_review_and_manifest_freeze_return_skipped_without_pass():
    from aiforstudy.blocks.media.validation import validate_media, validate_media_report, validate_verdicts, actionable_media_failures
    from aiforstudy.blocks.media.media_manifest import validate_manifest, approve_media
    content, approval, request, _ = approved_case()
    with review_mode('json_only'):
        report = validate_media(request, {}, client=object())
        assert report['ok'] is None and report['decision'] == 'unverified'
        assert report['review_status'] == 'not_run' and report['verdicts'] == []
        assert validate_media_report(request, {}, None) is None
        assert validate_verdicts([], {'unstructured':True}, {})['ok'] is None
        assert actionable_media_failures({'verdicts':[{'status':'fail'}]}, items=[]) == []
        manifest_report = validate_manifest({}, {})
        assert manifest_report['ok'] is None and manifest_report['review_status'] == 'not_run'
        receipt, report = approve_media({}, {}, approval)
        assert receipt is None and report['ok'] is None
    with review_mode('full'), pytest.raises(ContractError):
        validate_media_report(request, {}, None)


def test_requirement_and_math_binding_checks_are_reversible():
    from aiforstudy.blocks.media.math_context import ApprovedMathContext
    from aiforstudy.blocks.media.media_requirements import validate_requirements, validate_criteria, require_content_binding
    content, approval, request, _ = approved_case()
    request['criteria_digest'] = 'stale'
    with review_mode('json_only'):
        require_content_binding(content, {})
        validate_criteria(request)
        validate_requirements({}, content, {}, capabilities={})
        context = ApprovedMathContext.from_content_binding(content, {}, request)
        assert context.request == request
    with review_mode('full'), pytest.raises(ContractError):
        ApprovedMathContext.from_content_binding(content, approval, request)


def test_candidate_projection_does_not_require_goals_or_valid_evidence_bindings():
    from aiforstudy.blocks.media.media_requirements import candidates
    content, _, _, _=approved_case()
    content['teaching_plan']['goals']=[]
    with review_mode('json_only'):
        assert candidates(content)==[]
        content['teaching_plan']['goals']=[{'id':'G','observable_task':'观察向量'}]
        content['teaching_evidence']=[{'goal_id':'G','reader_pointer':'/not-a-field'}]
        rows=candidates(content)
        assert [row['goal_id'] for row in rows]==['G']
        assert rows[0]['text']=='观察向量'
    with review_mode('full'), pytest.raises(ContractError):
        candidates(content)


def test_export_freeze_does_not_turn_browser_not_run_into_failure(tmp_path):
    from aiforstudy.blocks.media.composition import reader_projection_status, validate_reader_export
    with review_mode('json_only'):
        review, missing = reader_projection_status({'missing':[]}, {'status':'not_run'})
        assert review['status'] == 'unverified' and missing is False
        assert validate_reader_export({}, {}, exported=tmp_path, browser_report={}) is None


def test_blank_actual_picture_is_no_longer_a_quality_failure(tmp_path):
    from PIL import Image
    from aiforstudy.blocks.media.code_runtime import _image
    path = tmp_path / 'blank.png'
    Image.new('RGB', (8, 8), 'white').save(path)
    with review_mode('json_only'):
        assert _image(path)['width'] == 8
    with review_mode('full'), pytest.raises(ValueError):
        _image(path)
    path.write_bytes(b'not a PNG')
    with review_mode('json_only'), pytest.raises(OSError):
        _image(path)


def test_worker_records_actual_steps_without_declared_coverage_gate():
    path = Path(__file__).parents[1] / 'tools/manim_worker/aifs_media_runtime.py'
    spec = importlib.util.spec_from_file_location('test_frozen_worker_runtime', path)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    scene = SimpleNamespace(renderer=SimpleNamespace(time=1.0))
    runtime._configure({'review_mode':'json_only'}, [{'step_id':'declared'}])
    with runtime.media_step(scene, 'different'):
        scene.renderer.time = 2.0
    scene.renderer.time = 3.0
    assert runtime._finish(scene) == [{'step_id':'different', 'start':1.0, 'end':2.0}]
    runtime._configure({}, [{'step_id':'declared'}])
    with pytest.raises(ValueError):
        with runtime.media_step(scene, 'different'):
            pass


def test_animation_json_only_publishes_video_without_review_frame_or_step_coverage(tmp_path, monkeypatch):
    from aiforstudy.blocks.media import code_runtime
    from PIL import Image
    staging, output = tmp_path/'worker', tmp_path/'published'
    staging.mkdir(); output.mkdir()
    (staging/'video.mp4').write_bytes(b'worker video')
    monkeypatch.setattr(code_runtime,'_video',lambda *a, **k: ({},2.0,60))
    def frame(video, destination, at, ffmpeg):
        Image.new('RGB',(16,16),'white').save(destination)
        return {'width':16,'height':16,'variation':0}
    monkeypatch.setattr(code_runtime,'_frame',frame)
    technical={}
    with review_mode('json_only'):
        resources, steps=code_runtime._animation_resources({'steps':[{'step_id':'missing'}]},
            staging,output,{'steps':[]},SimpleNamespace(ffmpeg='unused'),{'frame_rate':30},technical)
    assert (output/'video.mp4').read_bytes()==b'worker video'
    assert {r['kind'] for r in resources}>={'mp4','poster','captions','transcript'}
    assert steps==[] and technical.get('frame_checks',{}).get('status')!='pass'


def test_frozen_media_hash_does_not_reject_legal_metadata_fields():
    from aiforstudy.blocks.media.evidence_digest import manifest_digest
    with review_mode('json_only'):
        assert manifest_digest({'requests':[{'kind':'animation','timestamp':123}]}).startswith('sha256:')
    with review_mode('full'), pytest.raises(ValueError):
        manifest_digest({'requests':[{'kind':'animation','timestamp':123}]})


def test_planner_freeze_does_not_require_exact_previous_criteria_or_policy(monkeypatch):
    from aiforstudy.blocks.media.media_planning import plan_media_requirements
    from aiforstudy.blocks.media import author_requirements
    content, _, request, _ = approved_case()
    record={'requirements':{'decisions':[], 'requests':[request]}}
    monkeypatch.setattr(author_requirements,'validate_author_artifact',lambda *a, **k:record)
    monkeypatch.setattr(author_requirements,'project_author_requests',lambda *a, **k:(record['requirements'],[deepcopy(request)]))
    old=deepcopy(request); old['acceptance_criteria']=['different']
    with review_mode('json_only'):
        plan=plan_media_requirements(content,{},author_requirements=record,
            capabilities={'code_generation':'GeneratedMediaCodeV1'},
            repair_context={'previous_requirements':{'requests':[old]}})
    assert len(plan['requests'])==1 and plan['requests'][0]['request_id']==request['request_id']
    assert plan['review_outcome']['status']=='unverified'


def test_frozen_executor_does_not_need_optional_schema_metadata(tmp_path,monkeypatch):
    from aiforstudy.blocks.media.code_execution import execute_code
    from aiforstudy.blocks.media import code_generation
    content, _, request, program=approved_case()
    mock_runtime(monkeypatch,program)
    monkeypatch.setattr(code_generation,'generate_media_code',lambda *a,**k: {'code':program['code']})
    from aiforstudy.blocks.media import code_runtime
    original=code_runtime.render_generated_code
    monkeypatch.setattr(code_runtime,'render_generated_code',lambda candidate,**kw: original({**program,**candidate},**kw))
    request['dependency_paths']=['irrelevant non-binding metadata']
    with review_mode('json_only'):
        row=execute_code(request,content=content,approval={},out_dir=tmp_path,client=object(),reviewer=None)
    assert row['state']=='fulfilled' and row['resources']
    assert row['candidate_spec']=={'code':program['code']}


def test_real_frozen_worker_produces_blank_picture_and_keeps_isolation(tmp_path):
    from aiforstudy.blocks.media.code_runtime import render_generated_code
    source='from matplotlib.figure import Figure\ndef build_figure(context):\n    return Figure()\n'
    with review_mode('json_only'):
        result=render_generated_code({'code':source},context={'request':{'kind':'drawing'}},
            out_dir=tmp_path/'out',workspace_root=Path(__file__).parents[1],profile='final')
    assert result['status']=='rendered',result
    assert result['technical_report']['isolation']['safe'] is True
    assert {r['kind'] for r in result['resources']}=={'svg','image'}
    assert result['technical_report']['mathematical_status']=='not_run'


def test_real_frozen_animation_does_not_require_media_step_declarations(tmp_path):
    from aiforstudy.blocks.media.code_runtime import render_generated_code
    source='from manim import *\nclass GeneratedScene(Scene):\n    def construct(self):\n        self.add(Dot())\n        self.wait(.2)\n'
    with review_mode('json_only'):
        result=render_generated_code({'code':source},context={'request':{'kind':'animation'}},
            out_dir=tmp_path/'out',workspace_root=Path(__file__).parents[1],profile='draft')
    assert result['status']=='rendered',result
    assert result['steps']==[]
    assert {r['kind'] for r in result['resources']}>={'mp4','poster','captions','transcript'}
    assert result['technical_report']['frame_checks']['status']=='not_run'


def test_frozen_runtime_still_refuses_missing_os_isolation(tmp_path, monkeypatch):
    from test_media_code_runtime import test_missing_isolation_never_executes_generated_import
    with review_mode('json_only'):
        test_missing_isolation_never_executes_generated_import(tmp_path,monkeypatch)


@pytest.mark.parametrize('kind',['drawing','animation'])
def test_frozen_figures_stage_keeps_real_resources_without_judge(tmp_path,monkeypatch,kind):
    from test_media_code_execution import configure_stage
    content, approval, request, program=approved_case(kind)
    calls=mock_runtime(monkeypatch,program)
    stages, context=configure_stage(tmp_path,monkeypatch,content,approval,request,{})
    def client(ctx, role, *args, **kwargs):
        assert role!='judge'
        return object()
    monkeypatch.setattr(stages,'_client',client)
    with review_mode('json_only'):
        result=stages.figures_stage(context)
    assert result.execution_state.value=='succeeded',result.error
    assert result.quality_state.value=='not_checked'
    assert not result.repair_requests
    row=json.loads(stages._path(context,'figures').read_text())['payload']['requests'][0]
    assert row['resources'] and row['review_outcome']['status']=='unverified'
    assert 'validation' not in row and calls['review']==0
