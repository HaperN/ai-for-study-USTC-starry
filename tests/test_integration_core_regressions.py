"""Offline regressions for preparation, release rollback and media boundaries."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from aiforstudy.blocks.media.evidence_digest import validation_digest
from aiforstudy.core.digest import DigestError, digest_file
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.core.review_mode import review_mode
from aiforstudy.delivery.publish import (
    ChapterRef, _switch_current, build_release, read_current, rollback, switch_current,
)
from aiforstudy.errors import BudgetExceeded, Cancelled, ContractError
from aiforstudy.pipeline.engine import run_request
from aiforstudy.pipeline.lease import BookBusyError, BookLease
from aiforstudy.pipeline.services import Providers
from blocks.figures.backends import circuitikz
from support.preparation_book import _mini_book, _request, _tree


def _review_toc(tmp_path, error=None):
    workspace = _mini_book(tmp_path)
    parse = workspace.root / 'book/parse'
    (parse / 'toc.review.json').unlink()
    before = _tree(parse)

    def transport(**kwargs):
        if error is not None:
            raise error
        return '{"decisions":[]}', {}

    result, store, _ = run_request(
        _request('mini-book'), workspace, until='toc',
        providers=Providers(llm=transport),
    )
    assert _tree(parse) == before
    return result, store


def test_fresh_toc_review_saves_only_an_unapplied_suggestion(tmp_path):
    _, store = _review_toc(tmp_path)
    state = store.node_states()['toc']
    assert state.execution_state.value == 'succeeded', state.error
    assert state.quality_state.value == 'needs_review'
    payload = json.loads((store.work / 'products/toc/toc.json').read_text())['payload']
    draft = store.work / 'toc.review.suggested.json'
    assert payload['review_generated']['state'] == 'suggested'
    assert payload['review_generated']['path'] == str(draft)
    assert json.loads(draft.read_text())['decisions'] == []
    assert payload['review']['applied'] == []
    assert payload['blocked_for_release'] is True


def test_toc_review_transport_error_is_recorded_without_losing_the_toc(tmp_path):
    _, store = _review_toc(tmp_path, RuntimeError('offline synthetic provider error'))
    state = store.node_states()['toc']
    assert state.execution_state.value == 'succeeded', state.error
    assert state.quality_state.value == 'needs_review'
    payload = json.loads((store.work / 'products/toc/toc.json').read_text())['payload']
    assert payload['review_generated']['state'] == 'blocked'
    assert 'offline synthetic provider error' in payload['review_generated']['why']
    assert payload['toc_nodes']
    assert not (store.work / 'toc.review.suggested.json').exists()


@pytest.mark.parametrize(('error_type', 'expected_status'), [
    (Cancelled, 'cancelled'),
    (BudgetExceeded, 'failed'),
])
def test_toc_review_preserves_run_control_exceptions(tmp_path, error_type, expected_status):
    result, store = _review_toc(tmp_path, error_type('offline synthetic stop'))
    assert result.status == expected_status
    error = store.node_states()['toc'].error
    assert error['reason'] == 'offline synthetic stop'
    if error_type is BudgetExceeded:
        assert error['type'] == 'BudgetExceeded'
    assert not (store.work / 'toc.review.suggested.json').exists()


def _publish(releases, release_id, chapters, *, scope_kind='chapters', revision='',
             activate=True, manifest_updates=None):
    files = {
        f'{chapter}/index.html': f'<html>{release_id} {chapter}</html>'.encode()
        for chapter in chapters
    }
    files[f'{chapters[0]}/assets/style.css'] = b'body { color: black; }'
    members = {path: 'sha256:' + hashlib.sha256(data).hexdigest() for path, data in files.items()}
    manifest = {
        'book_id': 'mini-book', 'release_id': release_id, 'scope_kind': scope_kind,
        'scope': list(chapters), 'chapters': list(chapters), 'members': members,
        'offline_acceptance': {'passed': True, 'blocked': False, 'checks': [
            {'name': 'browser-execution', 'status': 'accepted'},
        ]},
        **(manifest_updates or {}),
    }
    if revision:
        manifest['parse_revision'] = revision
    directory, hashes = build_release(releases, release_id, manifest=manifest, files=files)
    if activate:
        _switch_current(
            releases, release_id, book_id='mini-book',
            scope_kind='book' if scope_kind == 'all' else 'selection',
            chapters=tuple(ChapterRef(chapter, release_id, members[f'{chapter}/index.html'], revision)
                           for chapter in chapters),
            verified_hashes=hashes,
        )
    return directory, hashes


@pytest.mark.parametrize('scope_kind', ['all', 'chapters'])
def test_rollback_recovers_target_chapter_refs_after_newer_publications(tmp_path, scope_kind):
    _publish(tmp_path, 'r1', ('CH01', 'CH02'), scope_kind=scope_kind, revision='r1-parse')
    target = read_current(tmp_path)
    _publish(tmp_path, 'r2', ('CH01', 'CH02'), revision='r2-parse')
    _publish(tmp_path, 'r3', ('CH03',), revision='r3-parse')
    latest = read_current(tmp_path)

    result = rollback(tmp_path, 'r1')
    current = read_current(tmp_path)
    assert result.switched
    assert result.previous_release == 'r3'
    assert current['release_id'] == 'r1'
    assert current['scope_kind'] == target['scope_kind']
    expected = target['chapters']
    if scope_kind == 'chapters':
        expected = [*expected, latest['chapters'][2]]
    assert current['chapters'] == expected
    assert result.replaced_chapters == ('CH01', 'CH02')
    assert result.untouched_chapters == (('CH03',) if scope_kind == 'chapters' else ())


@pytest.mark.parametrize('damage', [
    'changed', 'deleted', 'symlink', 'foreign-book', 'undeclared', 'scope-outside', 'traversal',
    'escalate-scope', 'failed-acceptance', 'contradicted-acceptance',
])
def test_rollback_refuses_an_invalid_target_without_writing_current(tmp_path, damage):
    _publish(tmp_path, 'r1', ('CH01',))
    _publish(tmp_path, 'r2', ('CH01',))
    current_file = tmp_path / 'current.json'
    current_before = current_file.read_bytes()
    page = tmp_path / 'r1/CH01/index.html'
    if damage == 'changed':
        (page.parent / 'assets/style.css').write_bytes(b'body { color: red; }')
    elif damage == 'deleted':
        page.unlink()
    elif damage == 'symlink':
        outside = tmp_path / 'outside.html'
        outside.write_bytes(page.read_bytes())
        page.unlink()
        page.symlink_to(outside)
    elif damage == 'undeclared':
        (page.parent / 'unverified.html').write_bytes(b'<html>undeclared page</html>')
    else:
        manifest_path = tmp_path / 'r1/manifest.json'
        manifest = json.loads(manifest_path.read_text())
        if damage == 'foreign-book':
            manifest['book_id'] = 'another-book'
        elif damage == 'scope-outside':
            manifest['scope'].append('CH02-S01')
        elif damage == 'escalate-scope':
            manifest['scope_kind'] = 'all'
        elif damage == 'failed-acceptance':
            manifest['offline_acceptance']['passed'] = False
        elif damage == 'contradicted-acceptance':
            manifest['offline_acceptance']['checks'].append({'name': 'images', 'status': 'failed'})
        else:
            manifest['members']['../outside.html'] = manifest['members']['CH01/index.html']
        manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ContractError):
        rollback(tmp_path, 'r1')
    assert current_file.read_bytes() == current_before


@pytest.mark.parametrize('legacy_damage', [None, 'no-journal', 'changed-scope', 'changed-delivery'])
def test_rollback_legacy_manifest_recovers_the_original_revision_from_run_evidence(tmp_path, legacy_damage):
    releases = tmp_path / 'releases'
    run = tmp_path / 'runs/r1'
    delivery = run / 'work/products/delivery/delivery.json'
    write_json_atomic(delivery, {'_meta': {
        'book_id': 'mini-book', 'run_id': 'r1', 'revision': 'original-parse',
        'stage': 'delivery', 'file_type': 'delivery.scope'}, 'payload': {}})
    directory, hashes = _publish(
        releases, 'r1', ('CH01',), activate=False,
        manifest_updates={'delivery_artifact_digest': digest_file(delivery), 'namespace': 'chapters'},
    )
    original_ref = ChapterRef('CH01', 'r1', hashes['CH01/index.html'], 'original-parse').as_dict()
    current = releases / 'current.json'
    write_json_atomic(current, {
        'book_id': 'mini-book', 'release_id': 'r1', 'scope_kind': 'selection', 'chapters': [original_ref],
    })
    if legacy_damage != 'no-journal':
        write_json_atomic(run / 'state.json', {
            'run_id': 'r1', 'book_id': 'mini-book',
            'publication': {'release_id': 'r1', 'switched': True, 'formally_published': True,
                            'release_dir': str(directory), 'manifest': str(directory / 'manifest.json'),
                            'scope': ['CH01'], 'replaced_chapters': ['CH01'], 'namespace': 'chapters',
                            'pointer': str(current), 'member_hashes': hashes},
        })
    if legacy_damage == 'changed-scope':
        manifest = json.loads((directory / 'manifest.json').read_text())
        manifest['scope_kind'] = 'all'
        write_json_atomic(directory / 'manifest.json', manifest)
    if legacy_damage == 'changed-delivery':
        payload = json.loads(delivery.read_text())
        payload['_meta']['revision'] = 'newer-parse'
        write_json_atomic(delivery, payload)
    _publish(releases, 'r2', ('CH01', 'CH02'), revision='newer-parse')
    before = current.read_bytes()

    if legacy_damage:
        with pytest.raises(ContractError):
            rollback(releases, 'r1')
        assert current.read_bytes() == before
    else:
        rollback(releases, 'r1')
        assert read_current(releases)['chapters'][0] == original_ref
        assert read_current(releases)['chapters'][1]['release_id'] == 'r2'


def test_original_receipts_survive_full_and_partial_publications_and_rollback(tmp_path):
    _, old_hashes = _publish(tmp_path, 'r1', ('CH01',), revision='parse1')
    original = read_current(tmp_path)['chapters'][0]
    _publish(tmp_path, 'r2', ('CH01', 'CH02'), scope_kind='all', revision='parse2')
    _publish(tmp_path, 'r3', ('CH02',), revision='parse3')
    current = tmp_path / 'current.json'
    before = current.read_bytes()

    with pytest.raises(ContractError):
        switch_current(tmp_path, 'r1', book_id='mini-book', scope_kind='book', chapters=(
            ChapterRef('CH01', 'r1', old_hashes['CH01/index.html'], 'unrecorded-revision'),
        ))
    assert current.read_bytes() == before
    rollback(tmp_path, 'r1')
    state = read_current(tmp_path)
    assert state['chapters'][0] == original
    assert state['chapters'][1]['release_id'] == 'r3'
    rollback(tmp_path, 'r2')
    assert {row['release_id'] for row in read_current(tmp_path)['chapters']} == {'r2'}
    rollback(tmp_path, 'r3')
    assert [row['release_id'] for row in read_current(tmp_path)['chapters']] == ['r2', 'r3']


def test_low_level_legacy_activation_does_not_attest_caller_supplied_revision(tmp_path):
    _, hashes = _publish(tmp_path, 'legacy', ('CH01',), activate=False)
    switch_current(tmp_path, 'legacy', book_id='mini-book', scope_kind='selection', chapters=(
        ChapterRef('CH01', 'legacy', hashes['CH01/index.html'], 'unverified-parse'),
    ))
    assert read_current(tmp_path)['release_id'] == 'legacy'
    _publish(tmp_path, 'newer', ('CH01',), revision='verified-newer-parse')
    current = tmp_path / 'current.json'
    before = current.read_bytes()
    with pytest.raises(ContractError):
        rollback(tmp_path, 'legacy')
    assert current.read_bytes() == before
    assert 'legacy' not in read_current(tmp_path).get('publication_receipts', {})


def test_rollback_rejects_contradictory_acceptance_even_with_original_manifest_hash(tmp_path):
    _publish(tmp_path, 'r1', ('CH01',), revision='parse1', manifest_updates={
        'offline_acceptance': {'passed': True, 'blocked': False, 'checks': [
            {'name': 'browser-execution', 'status': 'accepted'},
            {'name': 'images', 'status': 'failed'},
        ]},
    })
    _publish(tmp_path, 'r2', ('CH01',), revision='parse2')
    current = tmp_path / 'current.json'
    before = current.read_bytes()
    with pytest.raises(ContractError, match='离线验收'):
        rollback(tmp_path, 'r1')
    assert current.read_bytes() == before


@pytest.mark.parametrize('first', ['rollback', 'switch'])
def test_release_pointer_serializes_both_writers(tmp_path, monkeypatch, first):
    from aiforstudy.delivery import publish

    _publish(tmp_path, 'r1', ('CH01',), revision='parse1')
    _publish(tmp_path, 'r2', ('CH01',), revision='parse2')
    _, hashes = _publish(tmp_path, 'r4', ('CH02',), revision='parse4')
    _publish(tmp_path, 'r3', ('CH02',), revision='parse3')
    ready, proceed = Event(), Event()

    def pause_first_writer(path, payload):
        if payload.get('release_id') == 'r1':
            ready.set()
            assert proceed.wait(timeout=5)
        return write_json_atomic(path, payload)

    monkeypatch.setattr(publish, 'write_json_atomic', pause_first_writer)
    chapter1 = read_current(tmp_path)['chapters'][0]
    old_ref = ChapterRef('CH01', 'r1', digest_file(tmp_path / 'r1/CH01/index.html'), 'parse1')
    new_ref = ChapterRef('CH02', 'r4', hashes['CH02/index.html'], 'parse4')

    def write_first():
        if first == 'rollback':
            return rollback(tmp_path, 'r1')
        return switch_current(tmp_path, 'r1', book_id='mini-book', scope_kind='selection',
                              chapters=(old_ref,))

    def write_second():
        if first == 'switch':
            return rollback(tmp_path, 'r4')
        return switch_current(tmp_path, 'r4', book_id='mini-book', scope_kind='selection',
                              chapters=(new_ref,))

    with BookLease(tmp_path), ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(write_first)
        try:
            assert ready.wait(timeout=5)
            with pytest.raises(BookBusyError):
                write_second()
            assert read_current(tmp_path)['chapters'][0] == chapter1
        finally:
            proceed.set()
        assert pending.result(timeout=5).switched
        assert write_second().switched
    assert read_current(tmp_path)['chapters'] == [old_ref.as_dict(), new_ref.as_dict()]


def test_validation_digest_binds_the_ffprobe_stream_duration():
    stream = {
        'codec_type': 'video', 'codec_name': 'h264', 'width': 1280, 'height': 720,
        'pix_fmt': 'yuv420p', 'r_frame_rate': '30/1', 'duration': '2.000000',
    }
    report = {'technical_checks': [{
        'name': 'animation.video', 'status': 'accepted',
        'evidence': {'duration': 2.0, 'video': stream},
    }]}
    with review_mode('full'):
        first = validation_digest(report)
        stream['duration'] = '1.999000'
        assert validation_digest(report) != first
        stream['duration'] = '-1.000000'
        with pytest.raises(DigestError):
            validation_digest(report)
        stream['duration'] = '2.000000'
        stream['metadata'] = {'duration': 2}
        with pytest.raises(DigestError):
            validation_digest(report)


@pytest.mark.parametrize(('compiler_exit', 'converter_exit'), [(0, 0), (1, 0), (0, 1)])
def test_circuitikz_handles_non_utf8_compiler_diagnostics(tmp_path, monkeypatch, compiler_exit, converter_exit):
    # Drive actual subprocess capture and decoding with offline tool substitutes.
    compiler = tmp_path / 'pdflatex'
    compiler_diagnostics = (
        'printf "compiler detail: \\377\\n"\nprintf "stderr detail: \\376\\n" >&2\n'
        if not converter_exit else ''
    )
    compiler.write_text('#!/bin/sh\n' + compiler_diagnostics
                        + f'touch "${{5%.tex}}.pdf"\nexit {compiler_exit}\n')
    converter = tmp_path / 'pdftocairo'
    converter.write_text(
        '#!/bin/sh\nprintf \'<svg width="60pt" height="70pt"><path d="M0 0"/></svg>\' > "$3"\n'
        f'printf "converter detail: \\377\\n" >&2\nexit {converter_exit}\n'
    )
    tools = {'pdflatex': compiler, 'pdftocairo': converter}
    for tool in tools.values():
        tool.chmod(0o755)
    monkeypatch.setattr(circuitikz, '_tool', lambda name: str(tools[name]))
    spec = {'name': 'sample', 'tikz': r'\draw (0,0) -- (1,0);'}

    if compiler_exit == converter_exit == 0:
        produced = circuitikz.draw(spec, tmp_path, show_png=False)
        assert Path(produced['svg']).is_file()
        geometry = json.loads(Path(produced['geometry']).read_text())
        assert geometry['paths'] == 1
    elif compiler_exit:
        with pytest.raises(RuntimeError, match='pdflatex') as raised:
            circuitikz.draw(spec, tmp_path, show_png=False)
        assert 'compiler detail: �' in str(raised.value)
        assert 'stderr detail: �' in str(raised.value)
        assert not (tmp_path / 'sample.svg').exists()
    else:
        with pytest.raises(RuntimeError, match='pdftocairo') as raised:
            circuitikz.draw(spec, tmp_path, show_png=False)
        assert 'converter detail: �' in str(raised.value)
        assert not (tmp_path / 'sample.geometry.json').exists()
