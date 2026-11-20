"""Preparation preserves source material without a book-wide question engine."""
import json
import shutil

import pytest

from aiforstudy.blocks.parse.catalog import build_catalog
from aiforstudy.core.registry import load_registry
from aiforstudy.pipeline import preparation
from aiforstudy.pipeline.engine import build_engine, execute, resume_request, run_request
from aiforstudy.pipeline.resource_binding import LegacyRunError
from aiforstudy.pipeline.services import Providers
from support.book_factory import _mini_book, _request
from test_m2_engine import make_workspace, request_for, snapshot_tree


def test_prepare_graph_dispatches_structure_directly_after_toc(tmp_path):
    workspace, book = make_workspace(tmp_path)
    engine = build_engine(request_for(book.book_id), workspace)
    assert engine.graph.order()[:6] == ('mineru', 'toc', 'structure', 'export', 'body', 'concepts')
    assert engine.graph.by_id()['structure'].requires == ('toc',)
    assert 'exercises' not in engine.graph.by_id()


@pytest.mark.parametrize('legacy', [None, '{invalid retired index', '{"coverage":{"state":"incomplete"}}'])
def test_catalog_ignores_missing_corrupt_and_incomplete_retired_exercise_index(tmp_path, legacy):
    workspace = _mini_book(tmp_path)
    root = workspace.root/'book/parse'
    path = root/'exercises.json'
    path.unlink(missing_ok=True)
    if legacy is not None:
        path.write_text(legacy)
    catalog, details = build_catalog(root, book_id='mini-book', revision='parse-v2')
    assert catalog.verify()['problems'] == []
    assert details['reconciliation']['consistent'] is True
    assert 'exercises' not in details


def test_missing_source_material_contract_invalidates_existing_preparation(tmp_path):
    workspace = _mini_book(tmp_path)
    context = load_registry(workspace).resolve('mini-book')
    (context.parse/'source_materials.json').unlink(missing_ok=True)
    freshness = preparation.preparation_freshness(context)
    assert freshness['invalidates_catalog'] is True
    assert {'structure', 'export', 'body'} <= freshness['required'].keys()
    assert 'exercises' not in freshness['required']


def test_old_prepared_pointer_cannot_bypass_new_source_contract(tmp_path):
    workspace = _mini_book(tmp_path)
    context = load_registry(workspace).resolve('mini-book')
    cached = workspace.root/'data/books/mini-book/runs/old/work/prepared-parse'
    shutil.copytree(context.parse, cached)
    (cached/'document.selected.md').write_text('old filtered content')
    pointer = workspace.root/'data/books/mini-book/prepared/current.json'
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({'parse':str(cached),
        'source_digest':preparation.source_fingerprint(context),
        'files':preparation._tree_hashes(cached), 'graph_version':'6.0'}))
    assert preparation.prepared_context(context).parse == context.parse


def test_graph6_resume_is_refused_before_mutating_old_run(tmp_path):
    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id)
    _result, store = execute(build_engine(request, workspace))
    state = store.load_state()
    state['graph_version'] = '6.0'
    store.save_state(state)
    before = snapshot_tree(store.root)
    with pytest.raises(LegacyRunError):
        resume_request(request, store.run_id, workspace)
    assert snapshot_tree(store.root) == before


def test_production_prepare_to_body_never_calls_question_models(tmp_path):
    workspace = _mini_book(tmp_path)
    (workspace.root/'book/parse/exercises.json').write_text('{corrupt retired index')
    calls = []
    def forbidden_transport(**request):
        calls.append(request)
        raise AssertionError('Fresh MinerU and TOC need no exercise model')
    result, store, _ = run_request(_request(), workspace,
        providers=Providers(llm=forbidden_transport), until='body')
    assert result.status == 'succeeded', result.as_dict()
    assert calls == []
    assert 'exercises' not in store.node_states()
    assert store.node_states()['body'].quality_state.value == 'accepted'
