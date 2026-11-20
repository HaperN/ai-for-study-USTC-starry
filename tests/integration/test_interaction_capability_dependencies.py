"""Capability promises are generation inputs, not only reader dependencies."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.runtime_tree import copy_runtime


CONSUMERS = ('lesson', 'media_plan', 'figures', 'audit', 'interaction', 'render')


@pytest.mark.parametrize('stage', CONSUMERS)
@pytest.mark.parametrize('component', ('blocks/interaction/spec.py', 'blocks/model/model_generate.py'))
def test_capability_producer_changes_invalidate_every_consumer(tmp_path, monkeypatch, stage, component):
    from aiforstudy.pipeline.runner import Runner
    from aiforstudy.pipeline import stage_io
    root = tmp_path / 'runtime'
    copy_runtime(root, root)
    path = root / component
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('before')
    runner = object.__new__(Runner)
    monkeypatch.setattr(runner, '_runtime_package_root', lambda: root)
    monkeypatch.setattr(stage_io, '_repo_root', lambda: root)
    before = runner._stage_runtime_identity(stage)
    path.write_text('after')
    assert runner._stage_runtime_identity(stage) != before


def make_runner(tmp_path):
    from test_m2_engine import make_workspace, request_for
    from aiforstudy.pipeline.engine import build_engine
    from aiforstudy.pipeline.state import RunStore, book_runs_root
    from aiforstudy.pipeline.stubs import smoke_registry
    from aiforstudy.pipeline.runner import Runner
    workspace, book = make_workspace(tmp_path / 'ws')
    request = request_for(book.book_id, units=('CH01-S01',))
    engine = build_engine(request, workspace, stage_registry=smoke_registry())
    store = RunStore.create(book_runs_root(workspace.root, book.book_id), request, engine.plan, control=engine.services.control)
    runner = Runner(engine.services, engine.graph, store)
    runner._request_obj, runner._plan_obj = engine.request, engine.plan
    runner._input_snapshot = runner._snapshot_inputs()
    return runner, engine


def test_actual_availability_is_shared_with_candidate_identity(tmp_path, monkeypatch):
    from aiforstudy.pipeline import gateway_client
    from aiforstudy.checks import offline
    runner, engine = make_runner(tmp_path)
    available = {'transport': None, 'browser': None}
    monkeypatch.setattr(gateway_client, '_transport_for', lambda ctx: (available['transport'], 'test'))
    monkeypatch.setattr(offline, '_browser_executable', lambda root: available['browser'])
    node = engine.graph.by_id()['lesson:CH01-S01']
    before = runner._stage_snapshot(node, candidate_generation=True)
    available['transport'] = object()
    transport = runner._stage_snapshot(node, candidate_generation=True)
    assert transport != before
    available['browser'] = Path('/local/chrome')
    reader = runner._stage_snapshot(node, candidate_generation=True)
    assert reader != transport
    from aiforstudy.pipeline.interaction_runtime import interaction_runtime_inputs
    ctx = SimpleNamespace(services=engine.services, workspace=engine.services.workspace,
                          request=engine.request)
    assert reader['interaction_runtime'] == interaction_runtime_inputs(ctx)
    assert reader['interaction_runtime'] == {'transport_available': True, 'reader_available': True}


@pytest.mark.parametrize('component', ('blocks/interaction/spec.py', 'blocks/model/model_generate.py'))
def test_candidate_generation_includes_capability_producers(tmp_path, monkeypatch, component):
    from aiforstudy.pipeline import stage_io
    runner, engine = make_runner(tmp_path)
    root = tmp_path / 'runtime'
    copy_runtime(root, root)
    path = root / component
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('before')
    monkeypatch.setattr(runner, '_runtime_package_root', lambda: root)
    monkeypatch.setattr(stage_io, '_repo_root', lambda: root)
    node = engine.graph.by_id()['lesson:CH01-S01']
    before = runner._stage_snapshot(node, candidate_generation=True)
    path.write_text('after')
    assert runner._stage_snapshot(node, candidate_generation=True) != before
