"""Whole-lecture reconciliation contracts; provider calls remain offline here."""
import copy
from types import SimpleNamespace

import pytest


def case():
    lesson = {'sections': [{'id': 'old-1', 'title': 'First', 'start': 0, 'end': 20,
        'markdown': 'Old uncertain term [asr-1]', 'evidence_ids': ['asr-1']}], 'formulas': [], 'uncertainties': []}
    frames = [{'id': 'frame-1', 'start': 30, 'end': 40, 'text': 'later clear text', 'visual_text': 'clear term', 'uncertainties': []}]
    transcript = {'segments': [{'id': 'asr-1', 'start': 0, 'end': 20, 'text': 'uncertain automatic text'}]}
    rewritten = {'sections': [{'id': 'new-1', 'title': 'Full explanation', 'start': 0, 'end': 40,
        'markdown': 'Clear term [frame-1]', 'evidence_ids': ['frame-1', 'asr-1']}], 'formulas': [], 'uncertainties': [],
        'covered_section_ids': ['old-1'], 'corrections': [{'kind': 'replace', 'before': 'Old uncertain term', 'after': 'Clear term',
        'source_ids': ['frame-1'], 'reason': 'Later visual evidence resolves an automatic recognition error', 'start': 0, 'end': 40, 'section_id': 'new-1'}]}
    return lesson, frames, transcript, rewritten


def test_numeric_equivalence_keeps_sign_and_original_sources():
    from aiforstudy.core.video_lecture_reconciliation import numeric_equivalences
    rows = [{'id': 'a', 'layer': 'visual', 'text': r'0.75\times10^{-6}'},
            {'id': 'b', 'layer': 'asr', 'text': '7.5e-7'},
            {'id': 'c', 'layer': 'visual', 'text': r'-7.5\times10^{-7}'}]
    groups = numeric_equivalences(rows)
    assert len(groups) == 1
    assert groups[0]['normalized'] == '7.5E-7'
    assert {x['source_id'] for x in groups[0]['occurrences']} == {'a', 'b'}
    assert groups[0]['occurrences'][0]['text'] == r'0.75\times10^{-6}'


def test_reconciliation_uses_all_windows_without_mutating_raw(tmp_path):
    from aiforstudy.core.video_lecture_reconciliation import reconcile_lecture
    lesson, frames, transcript, rewritten = case()
    originals = copy.deepcopy((lesson, frames, transcript)); captured = []
    def call(role, tag, system, data, validate=None, **kwargs):
        captured.append((role, tag, system, data)); validate(rewritten); return rewritten
    session = SimpleNamespace(call=call, directory=tmp_path, identities=lambda: {'lesson': {'model': 'fixture'}})
    result = reconcile_lecture(session, lesson, frames, transcript, 40, 'revision-a')
    assert (lesson, frames, transcript) == originals
    assert captured[0][3]['frames'][0]['start'] == 30
    assert captured[0][3]['asr'][0]['id'] == 'asr-1'
    assert result['corrections'][0]['before'] == 'Old uncertain term'
    assert result['reconciliation']['semantic_completeness_verified'] is False
    assert (tmp_path / 'revision-a.reconciliation.json').is_file()


@pytest.mark.parametrize('mutation', ['unknown_id', 'wrong_before', 'wrong_after', 'missing_section', 'uncertain_formula', 'time_escape'])
def test_reconciliation_rejects_unbound_edits(mutation):
    from aiforstudy.core.video_lecture_reconciliation import validate_reconciliation
    lesson, frames, transcript, result = case()
    if mutation == 'unknown_id': result['corrections'][0]['source_ids'] = ['imaginary']
    if mutation == 'wrong_before': result['corrections'][0]['before'] = 'not in original'
    if mutation == 'wrong_after': result['corrections'][0]['after'] = 'not in rewritten lesson'
    if mutation == 'missing_section': result['covered_section_ids'] = []
    if mutation == 'uncertain_formula': result['formulas'] = [{'latex': 'x=7', 'source_ids': ['frame-1'], 'uncertainty': 'unclear'}]
    if mutation == 'time_escape': result['corrections'][0]['end'] = 100
    with pytest.raises(ValueError):
        validate_reconciliation(result, lesson, {'frame-1', 'asr-1'}, 40)


def test_re_review_has_independent_tag_and_never_approves(tmp_path):
    from aiforstudy.core.video_lecture_reconciliation import re_review_lecture
    lesson, frames, transcript, rewritten = case(); called = []
    def call(role, tag, system, data, validate=None, **kwargs):
        answer = {'decision': 'no_issues_found', 'issues': [], 'limitations': ['Text evidence only']}
        called.append((role, tag, data)); validate(answer); return answer
    session = SimpleNamespace(call=call, directory=tmp_path)
    review = re_review_lecture(session, rewritten, frames, transcript, 40, 'revision-a')
    assert called[0][0] == 'text_judge'
    assert called[0][1] == 'revision-a-global-review'
    assert review['automatic_approval'] is False
    assert review['reviewed_lesson_digest']


@pytest.mark.parametrize('fails', [False, True])
def test_revision_publishes_new_pointer_preserving_original_results(tmp_path, monkeypatch, fails):
    import io
    import json
    import time
    from aiforstudy.core.video_analysis import VideoAnalysisService, _sha
    import aiforstudy.core.video_analysis_models as models
    import aiforstudy.core.video_lecture_reconciliation as reconciliation
    service = VideoAnalysisService(tmp_path)
    monkeypatch.setattr(service, '_launch', lambda job_id: None)
    source = service.register_upload(io.BytesIO(b'abc'), 3, 'source.mp4')
    job = service.submit({'source_id': source['id']}); job_id = job['job_id']; directory = service.jobs / job_id
    lesson, frames, transcript, revised = case()
    baseline = {'job_id': job_id, 'title': 'Test', 'duration': 40, 'source': {'path': 'source.mp4'},
        **lesson, 'frames': frames, 'transcript': transcript, 'provenance': {'run_identity': 'original', 'dependency_digest': 'old-code'},
        'review': {'decision': 'issues_found', 'issues': []}}
    (directory / 'result.json').write_text(json.dumps(baseline)); (directory / 'lesson.md').write_text('Original markdown')
    (directory / 'export.done.json').write_text(json.dumps({'files': {'result.json': _sha(directory/'result.json'), 'lesson.md': _sha(directory/'lesson.md')}}))
    (directory / 'lesson.json').write_text(json.dumps(lesson)); (directory / 'asr.json').write_text(json.dumps(transcript)); (directory / 'visual.json').write_text(json.dumps(frames))
    service._register_artifacts(job_id, ['result.json', 'lesson.md']); service._update(job_id, state='completed')
    original_bytes = (directory / 'result.json').read_bytes()
    monkeypatch.setattr(models, 'VideoModelSession', lambda *args, **kwargs: SimpleNamespace(directory=kwargs['directory']))
    def rewrite(*args, **kwargs):
        if fails:
            raise ValueError('unresolved source binding')
        return {**revised, 'reconciliation': {'producer': 'new'}}
    monkeypatch.setattr(reconciliation, 'reconcile_lecture', rewrite)
    monkeypatch.setattr(reconciliation, 're_review_lecture', lambda *args, **kwargs: {'state': 'completed', 'decision': 'no_issues_found', 'issues': [], 'automatic_approval': False})
    service.reconcile(job_id)
    for _ in range(100):
        row = service.get(job_id)
        if row['state'] not in {'queued', 'running', 'cancelling'}:
            break
        time.sleep(.02)
    if fails:
        assert row['state'] == 'failed'
        assert row['revision']['state'] == 'failed'
        assert 'active_result' not in row
        assert row['result']['sections'] == baseline['sections']
        assert (directory / 'result.json').read_bytes() == original_bytes
        thread = service._threads.get(job_id)
        if thread:
            thread.join(timeout=2)
        failed_revision = row['revision']['id']; fails = False
        service.reconcile(job_id, retry_revision_id=failed_revision, review_feedback={'check': 'source-based feedback'})
        for _ in range(100):
            row = service.get(job_id)
            if row['state'] not in {'queued', 'running', 'cancelling'}:
                break
            time.sleep(.02)
        receipt = json.loads((directory / 'revisions' / failed_revision / 'revision.json').read_text())
        assert len(receipt['producer_attempts']) == 2
        assert len(receipt['attempt_history']) == 1
        assert receipt['source_quality_feedback']
        assert row['revision']['id'] == failed_revision
    assert row['state'] == 'completed', row.get('error')
    assert row['active_result'].startswith('revisions/')
    assert row['result']['sections'][0]['id'] == 'new-1'
    assert row['result']['exports']['original_json'] == 'result.json'
    assert row['result']['provenance']['recognition_producer']['run_identity'] == 'original'
    assert (directory / 'result.json').read_bytes() == original_bytes
    assert (directory / 'lesson.md').read_text() == 'Original markdown'
    assert service.artifact(job_id, row['result']['exports']['markdown']).is_file()


def test_reconciliation_reports_all_formula_errors_and_accepts_notes():
    from aiforstudy.core.video_lecture_reconciliation import reconciliation_errors, validate_reconciliation
    lesson, frames, transcript, answer = case()
    answer['formulas'] = [{'latex': 'x=1', 'source_ids': ['frame-1'], 'uncertainty': 'A provenance explanation'},
                          {'latex': 'y=2', 'source_ids': ['frame-1'], 'uncertainty': 'Unclear source'}]
    errors = reconciliation_errors(answer, lesson, {'frame-1', 'asr-1'}, 40)
    assert {e['path'] for e in errors} == {'formulas[0].uncertainty', 'formulas[1].uncertainty'}
    answer['formulas'] = [{'latex': 'x=1', 'source_ids': ['frame-1'], 'uncertainty': '', 'note': 'A provenance explanation'}]
    answer['uncertainties'] = ['Second formula remains unclear [frame-1]']
    assert validate_reconciliation(answer, lesson, {'frame-1', 'asr-1'}, 40) == answer


def test_retry_rejects_dependency_change_after_successful_exchange(tmp_path, monkeypatch):
    import io
    import json
    from aiforstudy.core.video_analysis import VideoAnalysisService, _sha, _digest
    s = VideoAnalysisService(tmp_path); monkeypatch.setattr(s, '_launch', lambda job_id: None)
    version = [1]
    monkeypatch.setattr(s, '_config_snapshot', lambda: {'dependencies': {'version': version[0]}})
    src = s.register_upload(io.BytesIO(b'abc'), 3, 'test.mp4'); job = s.submit({'source_id': src['id']}); job_id = job['job_id']
    directory = s.jobs / job_id; (directory/'result.json').write_text('{}')
    (directory/'export.done.json').write_text(json.dumps({'files': {'result.json': _sha(directory/'result.json')}}))
    s._register_artifacts(job_id, ['result.json']); s._update(job_id, state='failed')
    revision_id = 'c' * 32; output = directory/'revisions'/revision_id; (output/'exchanges').mkdir(parents=True)
    prior = {'dependency_digest': _digest({'version': 1}), 'configuration_digest': _digest({'dependencies': {'version': 1}})}
    (output/'revision.json').write_text(json.dumps({'state': 'failed', 'parent_result': 'result.json',
        'parent_result_sha256': _sha(directory/'result.json'), 'producer': prior, 'latest_producer': prior}))
    (output/'exchanges/accepted.output.json').write_text(json.dumps({'ok': True, 'data': {'text': 'previous producer'}}))
    version[0] = 2
    with pytest.raises(ValueError, match='producer|生产身份'):
        s.reconcile(job_id, retry_revision_id=revision_id)
    assert json.loads((output/'revision.json').read_text())['producer'] == prior
