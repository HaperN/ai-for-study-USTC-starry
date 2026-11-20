"""An interrupted fresh preparation may temporarily lack its frozen catalog."""
from copy import deepcopy
import pytest

from aiforstudy.contracts.enums import SelectorKind
from aiforstudy.contracts.stage import StageResult
from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.errors import ContractError
from aiforstudy.pipeline.execution_control import ExecutionScope
from aiforstudy.pipeline.engine import build_engine, execute, resume_request
from aiforstudy.pipeline.preparation import _mutable
from aiforstudy.pipeline.stubs import smoke_registry
from test_m2_engine import make_workspace, request_for


def interrupted_run(tmp_path, *, all_units=False):
    workspace, book = make_workspace(tmp_path)
    catalog = read_json(book.parse / 'chapters.json')
    source_before = {str(p.relative_to(book.parse)): p.read_bytes() for p in book.parse.rglob('*') if p.is_file()}
    request = request_for(book.book_id, () if all_units else ('CH01-S01',),
                          kind=SelectorKind.ALL if all_units else SelectorKind.UNITS)
    registry = smoke_registry()
    original = {name: registry.get(name) for name in ('mineru', 'toc', 'export', 'concepts', 'lesson')}
    phase = {'fail_toc': True, 'catalog': deepcopy(catalog)}
    visited = []

    def mineru(ctx):
        visited.append('mineru')
        parse = _mutable(ctx)
        # The real adapter removes derived catalog files when its contract changes.
        if not (parse / 'adapter-rebuilt').exists():
            (parse / 'chapters.json').unlink()
            (parse / 'adapter-rebuilt').write_text('rebuilt')
        return original['mineru'](ctx)

    def toc(ctx):
        visited.append('toc')
        if phase['fail_toc']:
            scope = ExecutionScope(ctx.services.store.run_id, 'book', 'toc')
            lease = ctx.services.control.acquire_call(scope, fingerprint='directory-request')
            lease.finish('transport_failed', error='offline timeout')
            return StageResult.blocked('toc', 'offline directory arbitration timeout')
        return original['toc'](ctx)

    def export(ctx):
        visited.append('export')
        write_json_atomic(ctx.context.parse / 'chapters.json', phase['catalog'])
        return original['export'](ctx)

    def concepts(ctx):
        visited.append('concepts')
        assert ctx.plan.resolved_scope is not None
        return original['concepts'](ctx)

    def lesson(ctx):
        visited.append('lesson')
        assert ctx.plan.resolved_scope is not None
        return original['lesson'](ctx)

    for name, implementation in [('mineru', mineru), ('toc', toc), ('export', export), ('concepts', concepts), ('lesson', lesson)]:
        registry.register(name, implementation)
    engine = build_engine(request, workspace, stage_registry=registry, until='lesson')
    frozen = list(engine.plan.resolved_scope)
    first, store = execute(engine)
    assert first.status != 'succeeded'
    assert read_json(store.plan_path)['resolved_scope'] == frozen
    assert not (store.work / 'prepared-parse/chapters.json').exists()
    assert 'concepts' not in visited and 'lesson' not in visited
    assert [a['state'] for a in read_json(store.root / 'execution.json')['attempts']] == ['transport_failed']
    return workspace, book, request, registry, phase, visited, frozen, first, store, source_before


def test_repeated_incomplete_preparation_resume_keeps_scope_and_persistent_attempts(tmp_path):
    workspace, book, request, registry, phase, visited, frozen, first, store, source_before = interrupted_run(tmp_path)
    original_request = store.request_path.read_bytes()
    second, store, _ = resume_request(request, store.run_id, workspace, stage_registry=registry, until='lesson')
    assert second.status != 'succeeded'
    assert read_json(store.plan_path)['resolved_scope'] is None
    assert store.load_state()['frozen_scope'] == frozen
    assert len(read_json(store.root / 'execution.json')['attempts']) == 2
    assert store.node_states()['toc'].attempt == 2
    phase['fail_toc'] = False
    third, resumed, engine = resume_request(request, store.run_id, workspace, stage_registry=registry, until='lesson')
    assert third.status == 'succeeded', third.recovery_hint
    assert resumed.run_id == store.run_id
    assert list(engine.plan.resolved_scope) == frozen == read_json(store.plan_path)['resolved_scope']
    assert store.request_path.read_bytes() == original_request
    assert len(read_json(store.root / 'execution.json')['attempts']) == 2
    assert engine.services.control.transport_attempts_used(ExecutionScope(store.run_id, 'book', 'toc'), 'directory-request') == 2
    assert store.node_states()['toc'].attempt == 3
    assert visited[-3:] == ['export', 'concepts', 'lesson']
    assert {str(p.relative_to(book.parse)): p.read_bytes() for p in book.parse.rglob('*') if p.is_file()} == source_before


def test_new_catalog_scope_must_match_original_before_any_downstream_dispatch(tmp_path):
    workspace, _, request, registry, phase, visited, frozen, _, store, _ = interrupted_run(tmp_path, all_units=True)
    phase['fail_toc'] = False
    phase['catalog']['lesson_units'].pop()
    result, store, _ = resume_request(request, store.run_id, workspace, stage_registry=registry, until='lesson')
    assert result.status == 'failed' and result.failure_stage == 'scope'
    assert '冻结范围' in result.recovery_hint
    assert 'concepts' not in visited and 'lesson' not in visited
    assert store.load_state()['frozen_scope'] == frozen
    assert read_json(store.plan_path)['resolved_scope'] is None


def test_known_scope_change_is_rejected_before_dispatch_or_run_mutation(tmp_path):
    workspace, _, request, registry, phase, visited, _, _, store, _ = interrupted_run(tmp_path, all_units=True)
    phase['catalog']['lesson_units'].pop()
    write_json_atomic(store.work / 'prepared-parse/chapters.json', phase['catalog'])
    before = {str(p.relative_to(store.root)): p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    old_visited = list(visited)
    with pytest.raises(ContractError, match='冻结范围'):
        resume_request(request, store.run_id, workspace, stage_registry=registry, until='lesson')
    assert visited == old_visited
    assert {str(p.relative_to(store.root)): p.read_bytes() for p in store.root.rglob('*') if p.is_file()} == before
