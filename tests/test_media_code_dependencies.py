"""Code-media cache identity excludes unused historical renderers."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiforstudy.blocks.media import dependencies as D


@pytest.fixture
def dependency_case(tmp_path, monkeypatch):
    from aiforstudy.checks import offline
    from aiforstudy.blocks.visual import worker
    from aiforstudy.providers import capabilities
    config = {'roles':{'figures':{'model':'author','effort':'none','max_tokens':32000},
                       'judge':{'model':'judge','effort':'high','max_tokens':32000}}}
    context = SimpleNamespace(workspace=SimpleNamespace(root=tmp_path, config_json=lambda name:config),
                              services=SimpleNamespace())
    worker_dir = Path(__file__).parents[1]/'tools/manim_worker'
    monkeypatch.setattr(offline, '_browser_executable', lambda root:None)
    monkeypatch.setattr(D.shutil, 'which', lambda name:None)
    monkeypatch.setattr(worker, 'resolve_worker_dir', lambda **kwargs:worker_dir)
    monkeypatch.setattr(capabilities, 'resolve_manim_interpreter', lambda root:None)
    original = D.file_identity
    changes = {}
    monkeypatch.setattr(D, 'file_identity', lambda path:changes.get(str(path), original(path)))
    package = Path(D.__file__).resolve().parents[2]
    return context, config, changes, package, worker_dir


def test_unused_legacy_edits_keep_code_cache_but_invalidate_historical_resources(dependency_case):
    context, _, changes, package, worker = dependency_case
    code = {'request_id':'code','kind':'drawing','acceptance_criteria':[]}
    legacy = {'request_id':'legacy','kind':'geometry','acceptance_criteria':[]}
    caps = {'code_generation':'GeneratedMediaCodeV1'}
    before = D.resource_dependencies(context, code, caps)
    historical = D.resource_dependencies(context, legacy, {})
    for name in ('plot.py','geometry.py','diagram.py','execution.py','static_common.py','media_patches.py'):
        changes[str(package/'blocks/media'/name)] = 'sha256:changed-'+name
    changes[str(package/'blocks/visual/compiler.py')] = 'sha256:changed-legacy-compiler'
    changes[str(worker/'render_fixture.py')] = 'sha256:changed-fixture'
    assert D.resource_dependencies(context, code, caps) == before
    assert D.resource_dependencies(context, legacy, {})['generation_dependency_hash'] != historical['generation_dependency_hash']


def test_cached_code_is_not_regenerated_for_legacy_edits_but_is_for_prompt_edits(dependency_case, tmp_path, monkeypatch):
    from test_media_code_contracts import approved_case
    from test_media_code_execution import mock_runtime, configure_stage
    dep_context, _, changes, package, _ = dependency_case
    content, approval, request, program = approved_case()
    calls = mock_runtime(monkeypatch, program)
    caps = {'code_generation':'GeneratedMediaCodeV1'}
    current = D.resource_dependencies(dep_context, request, caps)
    stages, context = configure_stage(tmp_path, monkeypatch, content, approval, request, current)
    assert stages.figures_stage(context).quality_state.value == 'accepted'
    changes[str(package/'blocks/media/geometry.py')] = 'sha256:unused-geometry-change'
    current.update(D.resource_dependencies(dep_context, request, caps))
    assert stages.figures_stage(context).quality_state.value == 'accepted'
    assert calls == {'generate':1, 'render':['final'], 'review':1}
    changes[str(package/'blocks/media/code_generation.py')] = 'sha256:current-prompt-change'
    current.update(D.resource_dependencies(dep_context, request, caps))
    assert stages.figures_stage(context).quality_state.value == 'accepted'
    assert calls == {'generate':2, 'render':['final','final'], 'review':2}


@pytest.mark.parametrize('component', [
    'blocks/media/code_contracts.py', 'blocks/media/code_generation.py',
    'blocks/media/static_example.py', 'blocks/media/animation_example.py',
    'blocks/media/code_execution.py', 'blocks/media/code_runtime.py',
    'blocks/media/math_context.py', 'blocks/visual/worker.py',
    'blocks/visual/manim_adapter.py', 'blocks/content/content_approval.py',
])
def test_actual_code_components_invalidate_generation(dependency_case, component):
    context, _, changes, package, _ = dependency_case
    request = {'request_id':'R','kind':'animation','acceptance_criteria':[]}
    caps = {'code_generation':'GeneratedMediaCodeV1'}
    before = D.resource_dependencies(context, request, caps)
    changes[str(package/component)] = 'sha256:changed-active-component'
    assert D.resource_dependencies(context, request, caps)['generation_dependency_hash'] != before['generation_dependency_hash']


def test_balanced_media_prompt_invalidates_review_without_regeneration(dependency_case):
    context, _, changes, package, _ = dependency_case
    request = {'request_id': 'R', 'kind': 'drawing', 'acceptance_criteria': []}
    caps = {'code_generation': 'GeneratedMediaCodeV1'}
    before = D.resource_dependencies(context, request, caps)
    changes[str(package/'blocks/media/prompts/review-balanced.md')] = 'sha256:balanced-font-guidance'
    after = D.resource_dependencies(context, request, caps)
    assert before['generation_dependency_hash'] == after['generation_dependency_hash']
    assert before['review_dependency_hash'] != after['review_dependency_hash']


def test_worker_model_request_and_review_identities_stay_bound(dependency_case):
    context, config, changes, package, worker = dependency_case
    request = {'request_id':'R','kind':'animation','acceptance_criteria':[],
               'dependency_paths':[{'pointer':'/body_markdown','hash':'sha256:approved-original'}]}
    caps = {'code_generation':'GeneratedMediaCodeV1'}
    baseline = D.resource_dependencies(context, request, caps)
    for name in ('code_renderer.py','aifs_media_runtime.py','policy.py','sandbox.py','scene_builder.py'):
        changes[str(worker/name)] = 'sha256:changed-worker'
        assert D.resource_dependencies(context, request, caps)['generation_dependency_hash'] != baseline['generation_dependency_hash']
        changes.clear()
    config['roles']['figures']['effort'] = 'low'
    assert D.resource_dependencies(context, request, caps)['generation_dependency_hash'] != baseline['generation_dependency_hash']
    config['roles']['figures']['effort'] = 'none'
    altered = deepcopy(request)
    altered['dependency_paths'][0]['hash'] = 'sha256:approved-changed'
    assert D.resource_dependencies(context, altered, caps)['generation_dependency_hash'] != baseline['generation_dependency_hash']
    changes[str(package/'blocks/media/validation.py')] = 'sha256:changed-review'
    reviewed = D.resource_dependencies(context, request, caps)
    assert reviewed['generation_dependency_hash'] == baseline['generation_dependency_hash']
    assert reviewed['review_dependency_hash'] != baseline['review_dependency_hash']
    changes.clear()
    config['roles']['judge']['max_tokens'] = 64000
    judged = D.resource_dependencies(context, request, caps)
    assert judged['generation_dependency_hash'] == baseline['generation_dependency_hash']
    assert judged['review_dependency_hash'] != baseline['review_dependency_hash']


def test_static_manim_font_worker_change_invalidates_drawing(dependency_case):
    context, _, changes, _, worker = dependency_case
    request = {'request_id':'R', 'kind':'drawing', 'acceptance_criteria':[]}
    caps = {'code_generation':'GeneratedMediaCodeV1'}
    before = D.resource_dependencies(context, request, caps)
    changes[str(worker/'scene_builder.py')] = 'sha256:changed-font-helper'
    assert D.resource_dependencies(context, request, caps)['generation_dependency_hash'] != before['generation_dependency_hash']


@pytest.mark.parametrize('kind,asset,generation,review',[
    ('drawing','code-common.md',True,False),('drawing','code-static.md',True,False),
    ('drawing','code-animation.md',False,False),('animation','code-animation.md',True,False),
    ('animation','code-static.md',False,False),('drawing','review-common.md',False,True),
    ('drawing','review-static.md',False,True),('drawing','review-animation.md',False,False),
    ('animation','review-animation.md',False,True),('animation','review-static.md',False,False),
])
def test_prompt_assets_invalidate_only_their_kind_and_phase(dependency_case,kind,asset,generation,review):
    context,_,changes,package,_=dependency_case
    request={'request_id':'R','kind':kind,'acceptance_criteria':[]};caps={'code_generation':'GeneratedMediaCodeV1'}
    before=D.resource_dependencies(context,request,caps)
    changes[str(package/'blocks/media/prompts'/asset)]='sha256:changed-prompt'
    after=D.resource_dependencies(context,request,caps)
    assert (after['generation_dependency_hash']!=before['generation_dependency_hash']) is generation
    assert (after['review_dependency_hash']!=before['review_dependency_hash']) is review


def test_geometry_invalidates_review_but_decoration_invalidates_neither(dependency_case):
    from aiforstudy.pipeline.stage_io import _repo_root
    context,_,changes,package,_=dependency_case
    request={'request_id':'R','kind':'drawing','acceptance_criteria':[]};caps={'code_generation':'GeneratedMediaCodeV1'}
    before=D.resource_dependencies(context,request,caps)
    changes[str(_repo_root()/'blocks/render/assets/styles.css')]='sha256:changed-decoration'
    assert D.resource_dependencies(context,request,caps)==before
    for path in (package/'blocks/render/display_policy.py',package/'blocks/media/display_probe.cjs',
                 _repo_root()/'blocks/render/assets/media-surface.css'):
        changes[str(path)]='sha256:changed-geometry';after=D.resource_dependencies(context,request,caps)
        assert after['generation_dependency_hash']==before['generation_dependency_hash']
        assert after['review_dependency_hash']!=before['review_dependency_hash']
        changes.pop(str(path))


@pytest.mark.parametrize('component', ['blocks/media/narration.py', 'blocks/media/narration_providers.py'])
def test_provider_implementation_invalidates_media_cache(dependency_case, component):
    context, _, changes, package, _ = dependency_case
    request = {'request_id':'R','kind':'animation','acceptance_criteria':[]}
    caps = {'code_generation':'GeneratedMediaCodeV1'}
    before = D.resource_dependencies(context, request, caps)
    changes[str(package/component)] = 'sha256:new-narration'
    assert D.resource_dependencies(context, request, caps)['generation_dependency_hash'] != before['generation_dependency_hash']


def test_narration_config_change_invalidates_animation_only(dependency_case):
    import json
    context, _, _, _, _ = dependency_case
    caps = {'code_generation':'GeneratedMediaCodeV1'}
    request = {'request_id':'R','kind':'animation','acceptance_criteria':[]}
    drawing = {**request, 'kind':'drawing'}
    path = Path(context.workspace.root)/'config'/'narration.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'backend':'minimax','voice':'teacher','dictionary_revision':'v1'}))
    before = D.resource_dependencies(context, request, caps)
    before_drawing = D.resource_dependencies(context, drawing, caps)
    path.write_text(json.dumps({'backend':'minimax','voice':'teacher','dictionary_revision':'v2'}))
    after = D.resource_dependencies(context, request, caps)
    assert before['generation_dependency_hash'] != after['generation_dependency_hash']
    assert before['review_dependency_hash'] == after['review_dependency_hash']
    assert D.resource_dependencies(context, drawing, caps) == before_drawing


def test_local_effective_identity_and_worker_changes_reject_outer_animation_cache(dependency_case,monkeypatch,tmp_path):
    import json,sys
    from aiforstudy.blocks.media import local_narration as L
    context,_,changes,package,_=dependency_case
    monkeypatch.setattr(L,'runtime_identity',lambda python:{'mlx-audio':'0.6.5'},raising=False)
    root=Path(context.workspace.root);manifest=root/'manifest.json'
    manifest.write_text(json.dumps({'status':'verified_complete','repo':'test','revision':'a','files':[]}))
    for key,value in {'MODEL_DIR':root,'MODEL_MANIFEST':manifest,'PYTHON':sys.executable,'CACHE_DIR':root/'cache'}.items():monkeypatch.setenv('AIFORSTUDY_TTS_'+key,str(value))
    (root/'config').mkdir();(root/'config/narration.json').write_text(json.dumps({'backend':'local-qwen'}))
    animation={'request_id':'R','kind':'animation'};drawing={**animation,'kind':'drawing'};caps={'code_generation':'GeneratedMediaCodeV1'}
    before=D.resource_dependencies(context,animation,caps);draw=D.resource_dependencies(context,drawing,caps)
    manifest.write_text(json.dumps({'status':'verified_complete','repo':'test','revision':'b','files':[]}))
    after=D.resource_dependencies(context,animation,caps)
    assert after['generation_dependency_hash']!=before['generation_dependency_hash']
    assert after['review_dependency_hash']==before['review_dependency_hash']
    assert D.resource_dependencies(context,drawing,caps)==draw
    for name in ('local_narration.py','local_speech_worker.py'):
        before=D.resource_dependencies(context,animation,caps)
        changes[str(package/'blocks/media'/name)]='changed-'+name
        assert D.resource_dependencies(context,animation,caps)['generation_dependency_hash']!=before['generation_dependency_hash']
        assert D.resource_dependencies(context,drawing,caps)==draw

    # Exercise the actual figures-stage resource reuse branch as well as hashes.
    from test_media_code_contracts import approved_case
    from test_media_code_execution import mock_runtime,configure_stage
    content,approval,request,program=approved_case('animation')
    calls=mock_runtime(monkeypatch,program)
    current=D.resource_dependencies(context,request,caps)
    (tmp_path/'stage').mkdir()
    stages,stage_context=configure_stage(tmp_path/'stage',monkeypatch,content,approval,request,current)
    assert stages.figures_stage(stage_context).quality_state.value=='accepted'
    assert stages.figures_stage(stage_context).quality_state.value=='accepted'
    assert calls['generate']==1
    second=tmp_path/'new-manifest.json'
    second.write_text(json.dumps({'status':'verified_complete','repo':'test','revision':'c','files':[]}))
    monkeypatch.setenv('AIFORSTUDY_TTS_MODEL_MANIFEST',str(second))
    current.update(D.resource_dependencies(context,request,caps))
    assert stages.figures_stage(stage_context).quality_state.value=='accepted'
    assert calls['generate']==2 and calls['render']==['draft','final','draft','final']
