"""Read-only MinerU provenance: use fetch evidence, never hide conflicts."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from aiforstudy.core.workspace import Workspace
from aiforstudy.errors import CapabilityError
from aiforstudy.pipeline.preparation_checks import _declared_source_hash
from aiforstudy.providers import mineru


PDF_HASH = hashlib.sha256(b'original PDF bytes').hexdigest()
OTHER_HASH = hashlib.sha256(b'another PDF').hexdigest()


def book(tmp_path, *, ingest=None, fetch=None):
    root = tmp_path / 'parse'
    (root / 'mineru').mkdir(parents=True)
    (root / 'mineru/layout.json').write_text('{"pdf_info": []}')
    if ingest is not None:
        (root / 'ingest.json').write_text(json.dumps(ingest))
    if fetch is not None:
        (root / 'mineru/fetch.json').write_text(json.dumps(fetch))
    return Workspace(tmp_path), root


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_fetch_only_identity_proves_source_without_mutating_import(tmp_path):
    workspace, root = book(tmp_path, ingest={'route': 'mineru'},
                           fetch={'_meta': {'source_sha256': PDF_HASH}})
    before = snapshot(root)
    decision = mineru.decide(workspace, root, source_hash='sha256:' + PDF_HASH, allow_api=False)
    assert decision.route == mineru.ROUTE_REUSE and not decision.requires_network
    assert decision.recorded_source_hash == PDF_HASH
    assert 'fetch.json' in ' '.join(decision.evidence)
    assert not any('缺少源哈希' in e or '无法证明' in e for e in decision.evidence)
    assert snapshot(root) == before


@pytest.mark.parametrize('current', [PDF_HASH, OTHER_HASH, ''])
def test_disagreeing_ingest_and_fetch_never_reuse_even_if_one_matches(tmp_path, current):
    workspace, root = book(tmp_path, ingest={'source_sha256': PDF_HASH},
                           fetch={'_meta': {'source_sha256': OTHER_HASH}})
    decision = mineru.decide(workspace, root, source_hash=current, allow_api=False)
    assert decision.blocked
    assert 'ingest.json' in decision.reason and 'fetch.json' in decision.reason


def test_conflicting_ingest_alias_cannot_be_hidden_by_first_field(tmp_path):
    workspace, root = book(tmp_path, ingest={'source_sha256': PDF_HASH,
                                          'pdf': {'sha256': OTHER_HASH}})
    assert mineru.decide(workspace, root, source_hash=PDF_HASH, allow_api=False).blocked


def test_matching_declarations_accept_prefix_and_keep_ingest_priority(tmp_path):
    workspace, root = book(tmp_path, ingest={'source_sha256': 'sha256:' + PDF_HASH},
                           fetch={'_meta': {'source_sha256': PDF_HASH.upper()}})
    decision = mineru.decide(workspace, root, source_hash=PDF_HASH, allow_api=False)
    assert decision.route == mineru.ROUTE_REUSE
    assert decision.recorded_source_hash == 'sha256:' + PDF_HASH


def test_fetch_only_wrong_pdf_is_blocked(tmp_path):
    workspace, root = book(tmp_path, fetch={'_meta': {'source_sha256': OTHER_HASH}})
    assert mineru.decide(workspace, root, source_hash=PDF_HASH, allow_api=False).blocked


@pytest.mark.parametrize('record', ['{incomplete', '[]', '{"_meta": []}',
                                   '{"_meta": {"source_sha256": ["hash"]}}'])
def test_present_broken_fetch_evidence_cannot_be_treated_as_missing(tmp_path, record):
    workspace, root = book(tmp_path, ingest={'source_sha256': PDF_HASH})
    (root / 'mineru/fetch.json').write_text(record)
    assert mineru.decide(workspace, root, source_hash=PDF_HASH, allow_api=False).blocked


def test_truly_unfingerprinted_legacy_import_keeps_honest_warning(tmp_path):
    workspace, root = book(tmp_path, ingest={'route': 'mineru'})
    decision = mineru.decide(workspace, root, source_hash=PDF_HASH, allow_api=False)
    assert decision.route == mineru.ROUTE_REUSE
    assert decision.recorded_source_hash == ''
    assert any('缺少源哈希' in e for e in decision.evidence)


def test_refresh_may_replace_conflicting_old_records_but_never_reuses_them(tmp_path, monkeypatch):
    workspace, root = book(tmp_path, ingest={'source_sha256': PDF_HASH},
                           fetch={'_meta': {'source_sha256': OTHER_HASH}})
    monkeypatch.setenv('MINERU_API_TOKEN', 'offline-fixture-only')
    decision = mineru.decide(workspace, root, source_hash=PDF_HASH, refresh=True)
    assert decision.route == mineru.ROUTE_API


def test_declared_source_uses_readable_pdf_bytes_after_checking_registry(tmp_path):
    pdf = tmp_path / 'book.pdf'
    pdf.write_bytes(b'original PDF bytes')
    ctx = SimpleNamespace(context=SimpleNamespace(sources=(
        SimpleNamespace(kind='pdf', sha256=PDF_HASH, path=pdf),)))
    assert _declared_source_hash(ctx) == ('sha256:' + PDF_HASH, 'local-file')


def test_frozen_registry_must_not_override_changed_pdf_bytes(tmp_path):
    pdf = tmp_path / 'book.pdf'
    pdf.write_bytes(b'original PDF bytes')
    ctx = SimpleNamespace(context=SimpleNamespace(sources=(
        SimpleNamespace(kind='pdf', sha256=OTHER_HASH, path=pdf),)))
    with pytest.raises(CapabilityError, match='注册表'):
        _declared_source_hash(ctx)
    assert pdf.read_bytes() == b'original PDF bytes'
