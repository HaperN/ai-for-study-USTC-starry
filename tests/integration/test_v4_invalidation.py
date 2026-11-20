from types import SimpleNamespace
from pathlib import Path
from aiforstudy.pipeline.runner import Runner
from aiforstudy.pipeline.graph import build_graph
from aiforstudy.contracts.scope import ScopePlan
from aiforstudy.contracts.request import Selectors
from aiforstudy.contracts.enums import Goal,Profile
from support.runtime_tree import copy_runtime


def test_css_change_is_visible_only_to_composition_runtime(tmp_path, monkeypatch):
    from aiforstudy.pipeline import stage_io as transitional
    package = tmp_path/'runtime'
    adapters = tmp_path/'adapters'
    copy_runtime(package, adapters)
    css = adapters/'blocks/render/assets/style.css'
    css.parent.mkdir(parents=True, exist_ok=True)
    css.write_text('body { color: black }')
    monkeypatch.setattr(transitional, '_repo_root', lambda:adapters)
    r=object.__new__(Runner)
    r._runtime_package_root=lambda:package
    before={name:r._stage_runtime_identity(name) for name in ('lesson','text_audit','media_plan','figures','audit','interaction','render','delivery')}
    css.write_text('body { color: blue }')
    after={name:r._stage_runtime_identity(name) for name in before}
    assert [name for name in before if before[name]!=after[name]] == ['render','delivery']


def test_dependency_closure_reaches_chapter_and_scope_only_for_affected_unit():
    plan=ScopePlan(request_digest='sha256:'+'0'*64,book_id='b', goal=Goal.DELIVER,profile=Profile.RELEASE,selectors=Selectors('all'),resolved_scope=('CH01-S01','CH01-S02','CH02-S01'))
    r=object.__new__(Runner)
    r.graph=build_graph(plan)
    impacted=r._dependent_closure({'lesson:CH01-S01'})
    assert {'text_audit:CH01-S01','media_plan:CH01-S01','figures:CH01-S01','render:CH01','delivery'} <= impacted
    assert 'lesson:CH01-S02' not in impacted
    assert 'render:CH02' not in impacted


def test_actual_runner_css_resume_keeps_model_stages_cached(tmp_path, monkeypatch):
    from test_m2_engine import make_workspace, request_for
    from aiforstudy.pipeline.engine import build_engine, execute, resume_request
    from aiforstudy.pipeline.stubs import smoke_registry
    from aiforstudy.pipeline import stage_io as transitional
    workspace,book=make_workspace(tmp_path/'ws')
    adapters=tmp_path/'adapters'
    copy_runtime(tmp_path/'runtime', adapters)
    css=adapters/'blocks/render/assets/style.css'
    css.parent.mkdir(parents=True, exist_ok=True)
    css.write_text('body {color:black}')
    monkeypatch.setattr(transitional,'_repo_root',lambda:adapters)
    request=request_for(book.book_id,units=('CH01-S01',))
    registry=smoke_registry()
    first,store=execute(build_engine(request,workspace,stage_registry=registry))
    assert first.status=='succeeded'
    before={key:row.attempt for key,row in store.node_states().items()}
    css.write_text('body {color:blue}')
    second,store,_=resume_request(request,store.run_id,workspace,stage_registry=registry)
    assert second.status=='succeeded'
    changed={key for key,row in store.node_states().items() if row.attempt!=before[key]}
    assert changed=={'render:CH01','delivery'}
    assert 'usage' not in second.as_dict()
    assert not (store.root/'usage.jsonl').exists()


def test_stale_inflight_result_cannot_commit_node_state(tmp_path):
    from test_m2_engine import make_workspace, request_for
    from aiforstudy.pipeline.engine import build_engine
    from aiforstudy.pipeline.state import RunStore,book_runs_root
    from aiforstudy.pipeline.stubs import smoke_registry
    workspace,book=make_workspace(tmp_path)
    request=request_for(book.book_id,units=('CH01-S01',))
    registry=smoke_registry()
    engine=build_engine(request,workspace,stage_registry=registry,until='mineru')
    store=RunStore.create(book_runs_root(workspace.root,book.book_id),request,engine.plan, control=engine.services.control)
    r=Runner(engine.services,engine.graph,store)
    r._request_obj=request; r._plan_obj=engine.plan; r._input_snapshot=r._snapshot_inputs()
    original=registry.get('mineru')
    def invalidated_while_running(ctx):
        value=original(ctx)
        r._generations['mineru']=1
        return value
    registry.register('mineru',invalidated_while_running)
    result,state=r._execute(engine.graph.by_id()['mineru'],'old',generation=0)
    assert state is None
    assert 'mineru' not in store.node_states()
    assert any(event.kind=='stage.stale_discarded' for event in store.events_after())


def test_content_review_rule_change_invalidates_both_real_consumers(tmp_path, monkeypatch):
    from aiforstudy.pipeline import stage_io as transitional
    package=tmp_path/'runtime'; adapters=tmp_path/'adapters'
    copy_runtime(package, adapters)
    policy=package/'blocks/content/content_policy.py'
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text('POLICY_VERSION = 1\n')
    monkeypatch.setattr(transitional,'_repo_root',lambda:adapters)
    r=object.__new__(Runner); r._runtime_package_root=lambda:package
    before={name:r._stage_runtime_identity(name) for name in ('lesson','text_audit')}
    policy.write_text('POLICY_VERSION = 2\n')
    after={name:r._stage_runtime_identity(name) for name in before}
    assert before['lesson']!=after['lesson']
    assert before['text_audit']!=after['text_audit']
