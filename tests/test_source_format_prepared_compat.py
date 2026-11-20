"""Graph 8 reuses intact graph 7 preparation without upgrading historical runs."""
import shutil

import pytest

from aiforstudy.contracts.enums import Profile
from aiforstudy.core.digest import digest_file
from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.core.registry import load_registry
from aiforstudy.core.source_materials import is_current
from aiforstudy.pipeline import preparation
from aiforstudy.pipeline.engine import resume_request, run_request
from aiforstudy.pipeline.graph import GRAPH_VERSION
from aiforstudy.pipeline.resource_binding import LegacyRunError
from support.book_factory import _mini_book, _request


def prepared_book(tmp_path, version='7.0'):
    workspace = _mini_book(tmp_path)
    context = load_registry(workspace).resolve('mini-book')
    prepared = workspace.root/'data/books/mini-book/runs/prior/work/prepared-parse'
    shutil.copytree(context.parse, prepared)
    pointer = workspace.root/'data/books/mini-book/prepared/current.json'
    write_json_atomic(pointer, {
        'graph_version': version, 'parse': str(prepared),
        'source_digest': preparation.source_fingerprint(context),
        'files': preparation._tree_hashes(prepared),
    })
    return workspace, context, prepared, pointer


def tree_identity(root):
    return {str(path.relative_to(root)): (path.stat().st_mtime_ns,
            digest_file(path) if path.is_file() else 'directory')
            for path in root.rglob('*')}


@pytest.mark.parametrize('version', ['7.0', '8.0'])
def test_graph8_reuses_intact_prepared_sources_read_only(tmp_path, version):
    assert GRAPH_VERSION == '8.0'
    workspace, context, prepared, _ = prepared_book(tmp_path, version)
    assert is_current(prepared)
    before = tree_identity(workspace.root)
    assert preparation.prepared_context(context).parse == prepared
    assert tree_identity(workspace.root) == before


@pytest.mark.parametrize('change', [
    'original_document', 'prepared_lesson', 'prepared_concepts',
    'extra_prepared_file', 'missing_prepared_file',
])
def test_graph7_prepared_compatibility_still_requires_all_file_hashes(tmp_path, change):
    workspace, context, prepared, _ = prepared_book(tmp_path)
    if change == 'original_document':
        path = context.parse/'document.md'
    elif change == 'prepared_lesson':
        path = prepared/'original_text/CH01-S01.md'
    elif change == 'prepared_concepts':
        path = prepared/'concepts.json'
    elif change == 'extra_prepared_file':
        path = prepared/'new-evidence.txt'
    else:
        (prepared/'body.json').unlink()
        path = None
    if path is not None:
        path.write_text('changed source bytes', encoding='utf-8')
    before = tree_identity(workspace.root)
    assert preparation.prepared_context(context).parse == context.parse
    assert tree_identity(workspace.root) == before


def test_graph7_prepared_cache_rechecks_source_contract_even_with_matching_file_list(tmp_path):
    workspace, context, prepared, pointer = prepared_book(tmp_path)
    material_path = prepared/'source_materials.json'
    payload = read_json(material_path)
    payload['materials'].append({'id': 'invented-material', 'raw_markdown': 'invented source'})
    write_json_atomic(material_path, payload)
    saved = read_json(pointer)
    saved['files'] = preparation._tree_hashes(prepared)
    write_json_atomic(pointer, saved)
    assert not is_current(prepared)
    before = tree_identity(workspace.root)
    assert preparation.prepared_context(context).parse == context.parse
    assert tree_identity(workspace.root) == before


@pytest.mark.parametrize('version', ['6.0', None])
def test_prepared_compatibility_does_not_accept_earlier_or_unversioned_data(tmp_path, version):
    _, context, _, _ = prepared_book(tmp_path, version)
    assert preparation.prepared_context(context).parse == context.parse


@pytest.mark.parametrize('legacy_declaration', ['state', 'plan', 'both'])
def test_graph7_run_resume_is_rejected_before_any_workspace_write(tmp_path, legacy_declaration):
    workspace = _mini_book(tmp_path)
    request = _request(profile=Profile.SMOKE)
    result, store, _ = run_request(request, workspace)
    assert result.status == 'succeeded', result.as_dict()
    assert store.load_state()['graph_version'] == '8.0'
    targets = [store.state_path] if legacy_declaration == 'state' else [store.plan_path]
    if legacy_declaration == 'both':
        targets = [store.state_path, store.plan_path]
    for path in targets:
        saved = read_json(path)
        saved['graph_version'] = '7.0'
        write_json_atomic(path, saved)
    before = tree_identity(workspace.root)
    with pytest.raises(LegacyRunError) as error:
        resume_request(request, store.run_id, workspace)
    assert error.value.decision.code == 'graph_version_changed_requires_new_run'
    assert tree_identity(workspace.root) == before
