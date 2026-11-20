"""A read-only plan names prospective run outputs, never imported or cached inputs."""
import shutil
from dataclasses import replace

from aiforstudy.contracts.enums import Goal, SelectorKind
from aiforstudy.contracts.scope import ScopePlan
from aiforstudy.core.registry import load_registry
from aiforstudy.pipeline.planner import plan
from support.preparation_book import _mini_book, _request, _tree, _write_json


def _snapshot(root):
    return _tree(root), sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_dir())


def test_cold_plan_names_run_preparation_and_cache_pointer_without_writing(tmp_path):
    ws = _mini_book(tmp_path)
    parse = ws.root / 'book/parse'
    (parse / 'chapters.json').unlink()
    request = replace(_request('mini-book'), goal=Goal.PREPARE)
    before = _snapshot(ws.root)
    result = plan(request, load_registry(ws))
    book = ws.root / 'data/books/mini-book'
    assert result.resolved_scope is None and result.requires_preparation
    assert str(book / 'runs/{new_run}/work/prepared-parse') in result.write_targets
    assert str(book / 'prepared/current.json') in result.write_targets
    assert str(parse) not in result.write_targets
    assert all('计账' not in task.note and '费用' not in task.note for task in result.preparation)
    assert any('{new_run}' in note and '模板' in note for note in result.notes)
    assert _snapshot(ws.root) == before


def test_resolved_plan_names_new_run_products_and_preserves_scope_schema(tmp_path):
    ws = _mini_book(tmp_path)
    registry = load_registry(ws)
    context = registry.resolve('mini-book')
    request = _request('mini-book', kind=SelectorKind.UNITS, values=('CH01-S01',))
    before = _snapshot(ws.root)
    result = plan(request, registry)
    run = ws.root / 'data/books/mini-book/runs/{new_run}'
    assert result.resolved_scope == ('CH01-S01',) and not result.requires_preparation
    assert {str(run / suffix) for suffix in ('work/products', 'work/media', 'work/legacy/render')} <= set(result.write_targets)
    assert str(context.lessons) not in result.write_targets
    assert str(context.audit) not in result.write_targets
    payload = result.as_dict()
    assert payload['schema_version'] == '1.0'
    assert isinstance(payload['write_targets'], list) and all(isinstance(t, str) for t in payload['write_targets'])
    assert ScopePlan.from_dict(payload).as_dict() == payload
    assert _snapshot(ws.root) == before


def test_reused_prepared_input_is_read_only_and_outputs_still_name_a_new_run(tmp_path):
    from aiforstudy.pipeline.preparation import source_fingerprint, _tree_hashes
    from aiforstudy.pipeline.graph import GRAPH_VERSION
    ws = _mini_book(tmp_path)
    registry = load_registry(ws)
    original = registry.resolve('mini-book')
    prior = ws.root / 'data/books/mini-book/runs/run-prior/work/prepared-parse'
    shutil.copytree(original.parse, prior)
    _write_json(ws.root / 'data/books/mini-book/prepared/current.json', {
        'parse': str(prior), 'source_digest': source_fingerprint(original),
        'files': _tree_hashes(prior), 'run_id': 'run-prior', 'graph_version': GRAPH_VERSION})
    before = _snapshot(ws.root)
    result = plan(_request('mini-book'), registry)
    assert result.read_dependencies[0] == str(prior)
    assert result.resolved_scope == ('CH01', 'CH01-S01')
    assert all('run-prior' not in target for target in result.write_targets)
    assert str(ws.root / 'data/books/mini-book/runs/{new_run}/work/products') in result.write_targets
    assert _snapshot(ws.root) == before


def test_model_config_compatibility_limit_matches_execution_default(tmp_path):
    from aiforstudy.pipeline.execution_control import load_policy
    from aiforstudy.providers.llm import load_config
    ws = _mini_book(tmp_path)
    assert load_config(ws).retry_limit == load_policy(ws, 'release').attempt_limits['transport']
