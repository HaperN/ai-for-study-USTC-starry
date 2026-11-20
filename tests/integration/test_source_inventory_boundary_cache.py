"""Changing source exercise boundaries invalidates prior semantic inventories."""
from aiforstudy.core import source_boundaries
from aiforstudy.pipeline.content_stages import _inventory_identity
import pytest


def test_inventory_cache_tracks_source_boundary_interpretation(tmp_path, monkeypatch):
    policy = tmp_path/'source_boundaries.py'
    policy.write_text('old boundary interpretation')
    monkeypatch.setattr(source_boundaries, '__file__', str(policy))
    before = _inventory_identity()
    policy.write_text('new boundary interpretation')
    assert _inventory_identity() != before


@pytest.mark.parametrize('stage,path', [
    ('lesson', 'core/source_boundaries.py'),
    ('concepts', 'core/source_boundaries.py'),
    ('render', 'core/source_boundaries.py'),
    ('render', 'core/source_materials.py'),
    ('render', 'blocks/render/source_materials.py'),
])
def test_outer_stage_cache_tracks_source_material_implementation(tmp_path, monkeypatch, stage, path):
    from aiforstudy.pipeline.runner import Runner
    from support.runtime_tree import copy_runtime
    package = tmp_path/'package'
    copy_runtime(package)
    source = package/path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('old source interpretation')
    runner = object.__new__(Runner)
    monkeypatch.setattr(runner, '_runtime_package_root', lambda: package)
    before = runner._stage_runtime_identity(stage)
    source.write_text('changed source interpretation')
    assert runner._stage_runtime_identity(stage) != before


def test_current_graph_media_snapshot_uses_renderer_environment_probe(tmp_path, monkeypatch):
    from test_m2_engine import make_workspace, request_for
    from aiforstudy.pipeline.engine import build_engine
    from aiforstudy.pipeline.runner import Runner
    from aiforstudy.pipeline.state import RunStore, book_runs_root
    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id, units=('CH01-S01',))
    engine = build_engine(request, workspace)
    store = RunStore.create(book_runs_root(workspace.root, book.book_id), request,
        engine.plan, control=engine.services.control)
    runner = Runner(engine.services, engine.graph, store)
    runner._input_snapshot = runner._snapshot_inputs()
    runner._request_obj, runner._plan_obj = request, engine.plan
    environment = {'renderer': 'old-interpreter-and-fonts'}
    monkeypatch.setattr(runner, '_media_resource_identities', lambda node: dict(environment))
    node = engine.graph.by_id()['figures:CH01-S01']
    before = runner._stage_snapshot(node)
    assert before['media_resources'] == {'renderer': 'old-interpreter-and-fonts'}
    environment['renderer'] = 'updated-interpreter-and-fonts'
    assert runner._stage_snapshot(node) != before
