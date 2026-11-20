"""Real Runner resumes, with counted offline media boundaries; not a policy table."""
from pathlib import Path
import json
import pytest
from support.runtime_tree import copy_runtime


def test_runner_reuses_media_bytes_for_decoration_and_review_changes(tmp_path,monkeypatch):
    from test_m2_engine import make_workspace,request_for
    from test_media_code_contracts import approved_case
    from test_media_code_execution import mock_runtime,configure_stage
    from aiforstudy.pipeline.engine import build_engine,execute,resume_request
    from aiforstudy.pipeline.stubs import smoke_registry
    from aiforstudy.pipeline.runner import Runner
    from aiforstudy.blocks.media import dependencies as D
    from aiforstudy.core.digest import digest_file
    from aiforstudy.core.jsonio import write_json_atomic
    from aiforstudy.pipeline import stage_io
    from aiforstudy.checks import offline
    from aiforstudy.providers import capabilities
    from aiforstudy.blocks.visual import worker

    workspace,book=make_workspace(tmp_path/'ws');request=request_for(book.book_id,units=('CH01-S01',))
    content,approval,media_request,program=approved_case();calls=mock_runtime(monkeypatch,program)
    original=D.file_identity;actual_package=Path(D.__file__).resolve().parents[2];actual_adapters=stage_io._repo_root()
    package=tmp_path/'package';adapters=tmp_path/'adapters'
    copy_runtime(package, adapters)
    assets=['blocks/media/prompts/code-common.md','blocks/media/prompts/code-static.md','blocks/media/prompts/review-common.md',
            'blocks/media/prompts/review-static.md','blocks/render/display_policy.py','blocks/media/geometry.py',
            'blocks/interaction/spec.py']
    for name in assets:
        p=package/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((actual_package/name).read_bytes())
    for name in ('styles.css','media-surface.css'):
        p=adapters/'blocks/render/assets'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((actual_adapters/'blocks/render/assets'/name).read_bytes())
    p=adapters/'blocks/model/model_generate.py';p.parent.mkdir(parents=True,exist_ok=True)
    p.write_bytes((actual_adapters/'blocks/model/model_generate.py').read_bytes())
    def identity(path):
        path=Path(path) if path else None
        if path and path.is_relative_to(actual_package):
            shadow=package/path.relative_to(actual_package)
            if shadow.is_file():return original(shadow)
        return original(path)
    monkeypatch.setattr(D,'file_identity',identity)
    monkeypatch.setattr(Runner,'_runtime_package_root',staticmethod(lambda:package))
    monkeypatch.setattr(stage_io,'_repo_root',lambda:adapters)
    monkeypatch.setattr(offline,'_browser_executable',lambda _:None)
    monkeypatch.setattr(D.shutil,'which',lambda _:None)
    monkeypatch.setattr(worker,'resolve_worker_dir',lambda **k:actual_adapters/'tools/manim_worker')
    monkeypatch.setattr(capabilities,'resolve_manim_interpreter',lambda _:None)
    (tmp_path/'unused').mkdir()
    deps={};stages,_=configure_stage(tmp_path/'unused',monkeypatch,content,approval,media_request,deps)
    monkeypatch.setattr(stages,'resource_dependencies',D.resource_dependencies)
    registry=smoke_registry();registry.register('figures',stages.figures_stage)
    original_plan=registry.get('media_plan')
    def media_plan(ctx):
        write_json_atomic(ctx.services.store.work/'products/media_plan'/f'{ctx.node.scope_key}.json',{'payload':{'requests':[media_request]}})
        return original_plan(ctx)
    registry.register('media_plan',media_plan)
    first,store=execute(build_engine(request,workspace,stage_registry=registry))
    assert first.status=='succeeded',first.as_dict()
    assert calls=={'generate':1,'render':['final'],'review':1}
    figure_path=store.work/'products/figures/CH01-S01.json'
    first_row=json.loads(figure_path.read_text())['payload']['requests'][0]
    first_resources=first_row['resources']
    history=[]
    for relative,root,expected in [
        ('blocks/render/assets/styles.css',adapters,(1,1)),
        ('blocks/render/display_policy.py',package,(1,2)),
        ('blocks/media/prompts/review-static.md',package,(1,3)),
        ('blocks/media/prompts/code-static.md',package,(2,4)),
        ('blocks/media/geometry.py',package,(2,4)),
        ('blocks/interaction/spec.py',package,(2,4)),
        ('blocks/model/model_generate.py',adapters,(2,4)),
    ]:
        path=root/relative;path.write_text(path.read_text()+'\n# changed by offline dependency test\n')
        result,store,_=resume_request(request,store.run_id,workspace,stage_registry=registry)
        assert result.status=='succeeded',result.as_dict()
        assert (calls['generate'],calls['review'])==expected,relative
        row=json.loads(figure_path.read_text())['payload']['requests'][0]
        assert row['resources']==first_resources
        history.append({'changed':relative,'generation_calls':calls['generate'],'review_calls':calls['review'],'artifact_hash':digest_file(figure_path)})
    assert calls['render']==['final','final']
    print('R6_RUNNER_EVIDENCE='+json.dumps(history,ensure_ascii=False))


def test_candidate_snapshot_tracks_author_assets_separately_from_repair(tmp_path,monkeypatch):
    from test_m2_engine import make_workspace,request_for
    from aiforstudy.pipeline.engine import build_engine
    from aiforstudy.pipeline.state import RunStore,book_runs_root
    from aiforstudy.pipeline.stubs import smoke_registry
    from aiforstudy.pipeline.runner import Runner
    workspace,book=make_workspace(tmp_path/'ws');request=request_for(book.book_id,units=('CH01-S01',))
    engine=build_engine(request,workspace,stage_registry=smoke_registry())
    store=RunStore.create(book_runs_root(workspace.root,book.book_id),request,engine.plan, control=engine.services.control)
    runner=Runner(engine.services,engine.graph,store);runner._input_snapshot=runner._snapshot_inputs()
    runner._request_obj,runner._plan_obj=engine.request,engine.plan
    package=tmp_path/'package';copy_runtime(package)
    prompts=package/'blocks/content/prompts';prompts.mkdir(parents=True,exist_ok=True)
    for name in ('common.md','author.md','repair.md'):(prompts/name).write_text(name)
    monkeypatch.setattr(runner,'_runtime_package_root',lambda:package)
    node=engine.graph.by_id()['lesson:CH01-S01'];before=runner._stage_snapshot(node,candidate_generation=True)
    (prompts/'repair.md').write_text('repair changed')
    assert runner._stage_snapshot(node,candidate_generation=True)==before
    # Reviewer-only protocol changes must invalidate review/approval while
    # preserving the identity of the original author request for revalidation.
    review_node=engine.graph.by_id()['text_audit:CH01-S01']
    for name in ('review.py','review_aliases.py','review_partial.py'):
        path=package/'blocks/content'/name
        original=path.read_bytes() if path.exists() else b''
        review_before=runner._stage_snapshot(review_node)
        path.write_bytes(original+b'\n# reviewer protocol changed\n')
        assert runner._stage_snapshot(node,candidate_generation=True)==before,name
        assert runner._stage_snapshot(review_node)!=review_before,name
        if original: path.write_bytes(original)
        else: path.unlink()
    for name in ('common.md','author.md'):
        p=prompts/name;p.write_text('changed')
        assert runner._stage_snapshot(node,candidate_generation=True)!=before,name
        p.write_text(name)


@pytest.mark.parametrize('damage',['changed','deleted','half-written'])
def test_real_runner_invalidates_damaged_output_instead_of_trusting_existence(tmp_path,damage):
    from test_m2_engine import make_workspace,request_for
    from aiforstudy.pipeline.engine import build_engine,execute,resume_request
    from aiforstudy.pipeline.stubs import smoke_registry
    workspace,book=make_workspace(tmp_path/'ws');request=request_for(book.book_id,units=('CH01-S01',))
    registry=smoke_registry();first,store=execute(build_engine(request,workspace,stage_registry=registry))
    assert first.status=='succeeded'
    original=store.node_states()['lesson:CH01-S01'];path=Path(original.outputs[0]['path']);before=path.read_bytes()
    if damage=='deleted':path.unlink()
    else:path.write_text('{"changed":true}' if damage=='changed' else '{"half":')
    second,store,_=resume_request(request,store.run_id,workspace,stage_registry=registry)
    assert second.status=='succeeded'
    assert store.node_states()['lesson:CH01-S01'].attempt==original.attempt+1
    assert path.read_bytes()==before
    assert store.node_states()['render:CH01'].execution_state.value=='succeeded'


def test_real_runner_reuses_unchanged_generation_outputs_without_dispatch(tmp_path):
    from test_m2_engine import make_workspace,request_for
    from aiforstudy.pipeline.engine import build_engine,execute,resume_request
    from aiforstudy.pipeline.stubs import smoke_registry
    workspace,book=make_workspace(tmp_path/'ws');request=request_for(book.book_id,units=('CH01-S01',))
    registry=smoke_registry();first,store=execute(build_engine(request,workspace,stage_registry=registry,until="interaction"));assert first.status=='succeeded'
    before={key:(row.attempt,row.outputs) for key,row in store.node_states().items()}
    second,store,_=resume_request(request,store.run_id,workspace,stage_registry=registry,until="interaction")
    assert second.status=='succeeded'
    assert 'usage' not in second.as_dict()
    assert before=={key:(row.attempt,row.outputs) for key,row in store.node_states().items()}
    assert all(row.execution_state.value=='cached' for row in store.node_states().values())


@pytest.mark.parametrize('name',[
    'blocks/render/layout.py','blocks/render/layout_contracts.py','blocks/render/reader_layout_probe.cjs',
    'blocks/render/display_policy.py','pipeline/media_review_recovery.py','core/prompt_assets.py',
])
def test_declared_runtime_consumers_track_new_reader_and_prompt_components(tmp_path,monkeypatch,name):
    from aiforstudy.pipeline.runner import Runner
    root=tmp_path/'package';copy_runtime(root)
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('before')
    runner=object.__new__(Runner);monkeypatch.setattr(runner,'_runtime_package_root',lambda:root)
    stage='figures' if 'recovery' in name else 'render'
    before=runner._stage_runtime_identity(stage);path.write_text('after')
    assert runner._stage_runtime_identity(stage)!=before
