"""Offline regressions for frozen scope and the final publication boundary."""
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from aiforstudy.contracts.enums import Goal, PreparePolicy, SelectorKind
from aiforstudy.core.digest import digest_file
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.errors import ContractError
from aiforstudy.pipeline import engine as engine_module
from aiforstudy.pipeline.engine import build_engine, execute, run_request
from aiforstudy.pipeline.preparation import source_fingerprint
from aiforstudy.pipeline.services import Providers
from test_m2_engine import make_workspace, request_for


@pytest.mark.parametrize('catalog_change', ['shrink', 'missing'])
def test_resume_rejects_prepared_scope_change_before_overwriting_frozen_plan(
        tmp_path, catalog_change):
    ws, book = make_workspace(tmp_path)
    request = request_for(book.book_id, (), kind=SelectorKind.ALL, goal=Goal.PREPARE)
    _, store, initial = run_request(request, ws)
    prepared = store.work / 'prepared-parse'
    shutil.copytree(book.parse, prepared)
    write_json_atomic(store.work / 'preparation-context.json', {
        'parse': str(prepared), 'source_digest': source_fingerprint(initial.services.context)})
    if catalog_change == 'missing':
        (prepared / 'chapters.json').unlink()
    else:
        catalog = json.loads((prepared / 'chapters.json').read_text())
        catalog['lesson_units'].pop()
        write_json_atomic(prepared / 'chapters.json', catalog)
    before = {p: p.read_bytes() for p in (store.plan_path, store.request_path, store.state_path)}
    resumed = build_engine(request, ws)
    with pytest.raises(ContractError, match='冻结范围'):
        execute(resumed, resume_run=store.run_id)
    assert {p: p.read_bytes() for p in before} == before
    assert not (store.root / 'budget-history.jsonl').exists()


def test_resume_can_resolve_scope_that_was_never_frozen(tmp_path):
    from aiforstudy.pipeline.state import RunStore, book_runs_root
    ws, book = make_workspace(tmp_path)
    catalog = json.loads((book.parse / 'chapters.json').read_text())
    (book.parse / 'chapters.json').unlink()
    request = request_for(book.book_id, (), kind=SelectorKind.ALL, goal=Goal.PREPARE)
    engine = build_engine(request, ws)
    assert engine.plan.resolved_scope is None
    store = RunStore.create(book_runs_root(ws.root, book.book_id), request, engine.plan, control=engine.services.control)
    prepared = store.work / 'prepared-parse'
    shutil.copytree(book.parse, prepared)
    write_json_atomic(prepared / 'chapters.json', catalog)
    write_json_atomic(store.work / 'preparation-context.json', {
        'parse': str(prepared), 'source_digest': source_fingerprint(engine.services.context)})
    resumed = build_engine(request, ws)
    result, _ = execute(resumed, resume_run=store.run_id)
    assert result.status == 'succeeded'
    expected = [row['unit_id'] for row in catalog['lesson_units']]
    assert json.loads(store.plan_path.read_text())['resolved_scope'] == expected


def _accepted_candidate(tmp_path, monkeypatch):
    """Reach actual graph-6 delivery checks before isolating the publication boundary."""
    from support.runner_v5 import run_book
    result,store,engine,_=run_book(tmp_path,until='delivery')
    assert result.status=='succeeded',result.recovery_hint
    assert store.node_states()['delivery'].quality_state.value=='accepted'
    result=replace(result,release_ready=True,truncated_at=None)
    store.set_status('succeeded',exit_code=0,scope_accepted=True,release_ready=True)
    monkeypatch.setattr(engine_module,'_execute_locked',lambda *a,**k:(result,store))
    return engine,store



def _assert_stopped_publication(result, store, engine, status, code):
    assert (result.status, result.exit_code) == (status, code)
    assert result.release_ready is False
    assert result.publication == {}
    state = store.load_state()
    assert (state['status'], state['exit_code']) == (status, code)
    assert state['release_ready'] is False
    assert state['failure_stage'] == 'publish'
    assert state['stop_reason']
    final = json.loads((store.root / 'final.json').read_text())
    assert final['status'] == status and final['release_ready'] is False
    assert final == {**result.as_dict(), 'scope': list(engine.plan.resolved_scope),
                     'scope_kind': engine.request.selectors.kind.value}
    assert not list(engine.services.context.releases_root.rglob('current.json'))


@pytest.mark.parametrize('stop', ['control', 'cancel'])
def test_final_publish_honors_stop_before_copy(tmp_path, monkeypatch, stop):
    engine, store = _accepted_candidate(tmp_path, monkeypatch)
    if stop == 'control':
        engine.services.control.stop('测试最终发布执行控制停止')
    else:
        engine.services.cancel.cancel('测试最终发布取消')
    result, _ = execute(engine)
    _assert_stopped_publication(result, store, engine, 'cancelled', 130)
    assert not list(engine.services.context.releases_root.rglob('manifest.json'))


@pytest.mark.parametrize('stop', ['control', 'cancel'])
def test_final_publish_honors_stop_after_copy_before_pointer(tmp_path, monkeypatch, stop):
    from aiforstudy.delivery import publish
    engine, store = _accepted_candidate(tmp_path, monkeypatch)
    original = publish.build_release
    def copy_then_stop(*args, **kwargs):
        release = original(*args, **kwargs)
        if stop == 'control':
            engine.services.control.stop('测试复制完成后执行控制停止')
        else:
            engine.services.cancel.cancel('测试复制完成后取消')
        return release
    monkeypatch.setattr(publish, 'build_release', copy_then_stop)
    result, _ = execute(engine)
    _assert_stopped_publication(result, store, engine, 'cancelled', 130)
    assert list(engine.services.context.releases_root.rglob('manifest.json'))


def test_publish_failure_clears_persisted_ready_flag(tmp_path, monkeypatch):
    engine, store = _accepted_candidate(tmp_path, monkeypatch)
    (store.work / 'legacy/render/CH01/index.html').write_text('changed after acceptance')
    result, _ = execute(engine)
    assert (result.status, result.exit_code, result.release_ready) == ('failed', 2, False)
    state = store.load_state()
    assert (state['status'], state['exit_code'], state['release_ready']) == ('failed', 2, False)
    assert state['failure_stage'] == 'publish'
    assert json.loads((store.root / 'final.json').read_text()) == {
        **result.as_dict(), 'scope': list(engine.plan.resolved_scope),
        'scope_kind': engine.request.selectors.kind.value}


def test_engine_records_real_publication_in_result_state_and_final(tmp_path, monkeypatch):
    engine, store = _accepted_candidate(tmp_path, monkeypatch)
    result, _ = execute(engine)
    publication = result.as_dict()['publication']
    assert publication['formally_published'] is True and publication['switched'] is True
    assert Path(publication['pointer']).is_file()
    assert (Path(publication['release_dir']) / 'CH01/index.html').is_file()
    assert store.load_state()['publication'] == publication
    assert json.loads((store.root / 'final.json').read_text()) == {
        **result.as_dict(), 'scope': list(engine.plan.resolved_scope),
        'scope_kind': engine.request.selectors.kind.value}


@pytest.mark.parametrize('policy', [PreparePolicy.ENSURE, PreparePolicy.REFRESH])
def test_registered_pdf_content_change_blocks_without_fetch_or_rebinding(tmp_path, policy):
    from support.preparation_book import _mini_book, _request
    ws = _mini_book(tmp_path)
    pdf = ws.root / 'book/source.pdf'
    pdf.write_bytes(b'original PDF bytes')
    original_digest = digest_file(pdf)
    registry = json.loads((ws.root / 'config/books.json').read_text())
    registry['books']['mini-book']['sources'] = [
        {'kind': 'pdf', 'path': 'book/source.pdf', 'sha256': original_digest}]
    write_json_atomic(ws.root / 'config/books.json', registry)
    parse = ws.root / 'book/parse'
    ingest = json.loads((parse / 'ingest.json').read_text())
    ingest['source_sha256'] = original_digest
    write_json_atomic(parse / 'ingest.json', ingest)
    pdf.write_bytes(b'replaced PDF bytes at the same path')
    calls = []
    def fetch(*a, **kw):
        calls.append((a, kw))
        raise AssertionError('changed registered PDF must be rejected before any provider call')
    engine = build_engine(replace(_request('mini-book'), prepare_policy=policy), ws,
                          until='mineru', providers=Providers(mineru=fetch))
    result, store = execute(engine)
    state = store.node_states()['mineru']
    assert state.execution_state.value == 'blocked'
    assert '注册表' in state.error['reason'] and '指纹' in state.error['reason']
    assert result.release_ready is False and calls == []
    assert json.loads((parse / 'ingest.json').read_text())['source_sha256'] == original_digest


@pytest.mark.parametrize('policy', [PreparePolicy.ENSURE, PreparePolicy.REFRESH])
def test_controlled_mineru_provider_builds_run_copy_and_records_source_receipt(
        tmp_path, monkeypatch, policy):
    """Exercise the API branch with an injected provider, real adaptation and no network."""
    from aiforstudy.contracts.enums import Profile
    from test_connected_entry import _DirectoryTransport, _register_directory_pdf
    ws, book = make_workspace(tmp_path)
    # This provider fixture introduces another source book; no prior lessons belong to it.
    shutil.rmtree(book.lessons)
    # Its directory is also new: retaining the vector-book catalog would ask the
    # identity guard to silently replace an already selected unit with another book.
    (book.parse / 'chapters.json').unlink()
    pdf = _register_directory_pdf(tmp_path, book.book_id, with_exercise=True)
    import pymupdf
    with pymupdf.open(pdf) as original_pdf:
        assert '求复数的实部' in original_pdf[1].get_text()
    before = {p.relative_to(book.parse).as_posix(): digest_file(p)
              for p in book.parse.rglob('*') if p.is_file()}
    monkeypatch.setenv('MINERU_API_TOKEN', 'controlled-test-token-never-send')
    def no_http(*a, **kw):
        raise AssertionError('controlled MinerU provider must never use HTTP')
    monkeypatch.setattr('urllib.request.urlopen', no_http)
    def block(kind, content):
        return {'type': kind, 'bbox': [0, 0, 200, 30], 'lines': [
            {'bbox': [0, 0, 200, 30], 'spans': [{'type': 'text', 'content': content}]}]}
    rows = [block('title', '第一章 复数'), block('text', '这一章介绍复数的基本运算。'),
            block('title', '1.1 复数的定义'),
            block('text', '复数由实部和虚部构成，记为 z=x+iy，其中 i 的平方等于负一。'),
            block('title', '习题'), block('text', '1. 求复数的实部。')]
    layout = {'_version_name': '3.4.4', '_backend': 'hybrid', '_ocr_enable': True,
              'pdf_info': [
                  {'page_idx': 0, 'page_size': [595, 842], 'para_blocks': [
                      block('title', '目录'), block('text', '第一章 复数 1\n1.1 复数的定义 1')],
                   'discarded_blocks': []},
                  {'page_idx': 1, 'page_size': [595, 842], 'para_blocks': rows,
                   'preproc_blocks': rows, 'discarded_blocks': []}]}
    calls = []
    def fetch(source, out, **kwargs):
        calls.append((source, out, kwargs))
        write_json_atomic(out / 'mineru/layout.json', layout)
        return {'provider': 'controlled-test', 'pages': 2}
    request = replace(request_for(book.book_id, ('CH01-S01',), profile=Profile.RELEASE),
                      prepare_policy=policy)
    transport = _DirectoryTransport()
    engine = build_engine(request, ws, until='body',
                          providers=Providers(mineru=fetch, llm=transport, is_stub=True))
    result, store = execute(engine)
    assert not result.failed_nodes and not result.blocked_nodes, result.as_dict()
    states = store.node_states()
    assert states['mineru'].quality_state.value == 'accepted'
    assert states['body'].quality_state.value == 'accepted'
    from aiforstudy.core.source_materials import is_current
    parse = engine.services.context.parse
    materials = json.loads((parse / 'source_materials.json').read_text())
    assert materials['schema_version'] == '1.0'
    assert materials['materials'] == [], '章内习题随教学来源保留，不另建独立资料章'
    assert is_current(parse)
    assert not (parse / 'exercises.json').exists()
    assert 'exercises' not in states
    body_text = '\n'.join(p.read_text() for p in (engine.services.context.parse/'original_text').glob('*.md'))
    assert '复数由实部和虚部构成' in body_text
    assert '习题\n1. 求复数的实部。' in body_text
    assert result.status == 'succeeded' and states['toc'].quality_state.value == 'accepted'
    assert len([c for c in transport.calls if 'phase' not in c['data']]) == 2, '新 PDF 必须独立视觉生成，再比较完整两源目录'
    assert all('phase' not in c['data'] for c in transport.calls)
    assert len(calls) == 1
    source, output, kwargs = calls[0]
    assert source == pdf and output == store.work / 'prepared-parse'
    assert kwargs['refetch'] is (policy is PreparePolicy.REFRESH)
    deadline = engine.services.control.policy.api_deadline_seconds
    assert kwargs['timeout_s'] == (None if deadline is None else max(1, int(deadline)))
    assert kwargs['token'] == 'controlled-test-token-never-send'
    assert (output / 'chapters.json').is_file() and (output / 'body.json').is_file()
    receipt = json.loads((store.work / 'mineru-fetch.json').read_text())
    assert receipt['record'] == {'provider': 'controlled-test', 'pages': 2}
    assert receipt['source_hash'] == digest_file(pdf)
    attempts = json.loads((store.root/'execution.json').read_text())['attempts']
    assert len([r for r in attempts if r['scope']['stage'] == 'toc']) == 2
    assert len(attempts) == 2
    assert all(row['state'] == 'response_received' for row in attempts)
    assert not (store.root/'usage.jsonl').exists()
    assert {p.relative_to(book.parse).as_posix(): digest_file(p)
            for p in book.parse.rglob('*') if p.is_file()} == before
