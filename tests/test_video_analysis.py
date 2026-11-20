"""Offline contracts for secure, durable real video analysis orchestration."""
import io
import json
import sys
import time
from pathlib import Path

import pytest


def service(tmp_path, monkeypatch):
    from aiforstudy.core.video_analysis import VideoAnalysisService
    instance = VideoAnalysisService(tmp_path)
    monkeypatch.setattr(instance, '_launch', lambda job_id: None)
    return instance


def test_upload_is_atomic_and_rejects_truncation(tmp_path, monkeypatch):
    s = service(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='length|完整|长度'):
        s.register_upload(io.BytesIO(b'ab'), 5, '../unsafe.mp4')
    assert not list(s.uploads.glob('*.json'))
    assert not list(s.uploads.glob('*.part'))
    row = s.register_upload(io.BytesIO(b'abcdef'), 6, '../unsafe.mp4')
    assert row['filename'] == 'unsafe.mp4'
    assert row['bytes'] == 6
    assert len(row['sha256']) == 64
    assert str(tmp_path) not in json.dumps(row)


def test_upload_limits_apply_before_read(tmp_path, monkeypatch):
    s = service(tmp_path, monkeypatch)
    class NoRead:
        def read(self, n):
            raise AssertionError('must not read over-limit upload')
    with pytest.raises(ValueError):
        s.register_upload(NoRead(), s.max_upload_bytes + 1, 'large.mp4')


def test_submit_rejects_arbitrary_source_path_and_unknown_source(tmp_path, monkeypatch):
    s = service(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        s.submit({'source_path': '/etc/passwd'})
    with pytest.raises((ValueError, KeyError)):
        s.submit({'source_id': '../../etc/passwd'})


def test_cache_is_bound_to_source_bytes_title_and_config(tmp_path, monkeypatch):
    s = service(tmp_path, monkeypatch)
    src = s.register_upload(io.BytesIO(b'abcdef'), 6, 'test.mp4')
    a = s.submit({'source_id': src['id'], 'title': 'First'})
    assert s.submit({'source_id': src['id'], 'title': 'First'})['job_id'] == a['job_id']
    assert s.submit({'source_id': src['id'], 'title': 'Second'})['job_id'] != a['job_id']
    assert s.submit({'source_id': src['id'], 'title': 'First', 'force': True})['job_id'] != a['job_id']


def test_artifact_requires_registry_and_blocks_traversal_symlink(tmp_path, monkeypatch):
    s = service(tmp_path, monkeypatch)
    source = s.register_upload(io.BytesIO(b'abc'), 3, 'test.mp4')
    job = s.submit({'source_id': source['id']})
    directory = s.jobs / job['job_id']
    (directory / 'private.txt').write_text('private')
    assert s.artifact(job['job_id'], '../private.txt') is None
    assert s.artifact(job['job_id'], 'private.txt') is None
    (directory / 'escape.txt').symlink_to(tmp_path / 'outside.txt')
    (tmp_path / 'outside.txt').write_text('outside')
    s._register_artifacts(job['job_id'], ['escape.txt'])
    assert s.artifact(job['job_id'], 'escape.txt') is None
    s._register_artifacts(job['job_id'], ['private.txt'])
    assert s.artifact(job['job_id'], 'private.txt').read_text() == 'private'


def test_reference_validation_rejects_invented_sources_and_times():
    from aiforstudy.core.video_analysis import validate_lesson
    good = {'sections': [{'id': 'sec1', 'title': 'A', 'start': 0, 'end': 10,
              'markdown': 'Evidence [a1]', 'evidence_ids': ['a1']}], 'formulas': [], 'uncertainties': []}
    assert validate_lesson(good, {'a1'}, 10) == good
    bad = json.loads(json.dumps(good)); bad['sections'][0]['evidence_ids'] = ['invented']
    with pytest.raises(ValueError, match='source|来源|evidence'):
        validate_lesson(bad, {'a1'}, 10)
    bad = json.loads(json.dumps(good)); bad['sections'][0]['end'] = 100
    with pytest.raises(ValueError):
        validate_lesson(bad, {'a1'}, 10)


def test_cancel_terminates_and_reaps_worker(tmp_path, monkeypatch):
    from aiforstudy.core.video_analysis import VideoAnalysisService
    from aiforstudy.pipeline.services import CancelToken
    from aiforstudy.errors import Cancelled
    import threading
    s = VideoAnalysisService(tmp_path)
    token = CancelToken(); result = []
    def run():
        try:
            s._process([sys.executable, '-c', 'import time; time.sleep(100)'], tmp_path / 'worker.log', token)
        except Cancelled:
            result.append('cancelled')
    thread = threading.Thread(target=run); thread.start(); time.sleep(.2); token.cancel(); thread.join(4)
    assert not thread.is_alive()
    assert result == ['cancelled']


def test_restart_marks_abandoned_jobs_interrupted(tmp_path, monkeypatch):
    s = service(tmp_path, monkeypatch)
    source = s.register_upload(io.BytesIO(b'abc'), 3, 'test.mp4')
    job = s.submit({'source_id': source['id']})
    s._update(job['job_id'], state='running', stage='asr')
    from aiforstudy.core.video_analysis import VideoAnalysisService
    restarted = VideoAnalysisService(tmp_path)
    assert restarted.get(job['job_id'])['state'] == 'interrupted'


def test_model_invented_inline_source_is_rejected():
    from aiforstudy.core.video_analysis import validate_lesson
    lesson = {'sections': [{'id': 'x', 'title': 'A', 'start': 0, 'end': 1,
        'markdown': 'Unsupported claim [frame-9999]', 'evidence_ids': ['frame-0001']}],
        'formulas': [], 'uncertainties': []}
    with pytest.raises(ValueError, match='source|来源|evidence'):
        validate_lesson(lesson, {'frame-0001'}, 1)


def test_cancel_queued_without_worker_reaches_terminal_state(tmp_path, monkeypatch):
    s = service(tmp_path, monkeypatch)
    source = s.register_upload(io.BytesIO(b'abc'), 3, 'test.mp4')
    job = s.submit({'source_id': source['id']})
    assert s.cancel(job['job_id'])['state'] == 'cancelled'


def test_model_transport_runs_in_cancellable_inherited_lock_subprocess(tmp_path):
    from aiforstudy.core.video_analysis_models import VideoModelTransport
    from aiforstudy.pipeline.services import CancelToken
    from types import SimpleNamespace
    class Underlying:
        cache_identity = {'provider': 'fixture'}
    captured = []
    service = SimpleNamespace(workspace_root=tmp_path)
    def execute(args, log, token, timeout):
        captured.append(args)
        output = Path(args[args.index('--response') + 1])
        from aiforstudy.core.video_analysis import _digest, _sha
        data = {'text': '{"ok":true}', 'usage': {'total_tokens': 11}, 'metadata': {'model': 'fixture'}}
        data['request_sha256'] = _sha(Path(args[args.index('--request') + 1]))
        output.write_text(json.dumps({**data, 'response_sha256': _digest(data)}))
    service._process = execute
    adapter = VideoModelTransport(Underlying(), service, tmp_path, CancelToken(), 'lesson')
    response = adapter(model='fixture', effort='low', messages=[], images=[], max_tokens=10, timeout=5)
    assert tuple(response) == ('{"ok":true}', {'total_tokens': 11})
    assert adapter.cache_identity == {'provider': 'fixture'}
    assert captured[0][captured[0].index('-m') + 1] == 'aiforstudy.core.video_analysis_models'


def test_changed_pipeline_bytes_invalidate_submit_and_resume(tmp_path, monkeypatch):
    import aiforstudy.core.video_analysis as module
    code = tmp_path / 'pipeline.py'; code.write_text('version = 1')
    monkeypatch.setattr(module, 'VIDEO_COMPONENTS', (str(code),), raising=False)
    s = service(tmp_path, monkeypatch)
    source = s.register_upload(io.BytesIO(b'abc'), 3, 'test.mp4')
    first = s.submit({'source_id': source['id']})
    s._update(first['job_id'], state='failed')
    code.write_text('version = 2')
    assert s.submit({'source_id': source['id']})['job_id'] != first['job_id']
    with pytest.raises(ValueError, match='配置|依赖|changed'):
        s.resume(first['job_id'])


def test_changed_asr_weight_bytes_invalidate_submit(tmp_path, monkeypatch):
    model = tmp_path / 'model'; model.mkdir(); weights = model / 'model.safetensors'; weights.write_bytes(b'version one')
    monkeypatch.setenv('AIFORSTUDY_VIDEO_ASR_MODEL', str(model))
    s = service(tmp_path, monkeypatch)
    source = s.register_upload(io.BytesIO(b'abc'), 3, 'test.mp4')
    first = s.submit({'source_id': source['id']})
    weights.write_bytes(b'version two')
    assert s.submit({'source_id': source['id']})['job_id'] != first['job_id']


def test_changed_effective_transport_invalidates_submit(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import aiforstudy.providers.factory as factory
    (tmp_path / 'config').mkdir(); (tmp_path / 'config' / 'models.json').write_text('{"roles":{"lesson":{"model":"fixture"}}}')
    endpoint = ['http://localhost:1234']
    def provider(request, workspace):
        return SimpleNamespace(llm=SimpleNamespace(cache_identity={'provider': 'fixture', 'endpoint': endpoint[0]}))
    monkeypatch.setattr(factory, 'configured_providers', provider)
    s = service(tmp_path, monkeypatch)
    source = s.register_upload(io.BytesIO(b'abc'), 3, 'test.mp4')
    first = s.submit({'source_id': source['id']})
    endpoint[0] = 'http://localhost:5678'
    assert s.submit({'source_id': source['id']})['job_id'] != first['job_id']


def test_external_worker_release_is_observed_by_get_and_list(tmp_path, monkeypatch):
    import fcntl
    from aiforstudy.core.video_analysis import VideoAnalysisService
    s = service(tmp_path, monkeypatch)
    source = s.register_upload(io.BytesIO(b'abc'), 3, 'test.mp4')
    job = s.submit({'source_id': source['id']}); s._update(job['job_id'], state='running')
    with (s.jobs / job['job_id'] / 'worker.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        observer = VideoAnalysisService(tmp_path)
        assert observer.get(job['job_id'])['state'] == 'running'
    assert observer.get(job['job_id'])['state'] == 'interrupted'
    assert observer.list_jobs()[0]['state'] == 'interrupted'


def test_invalid_model_protocol_is_preserved_and_resume_uses_new_tag(tmp_path):
    from aiforstudy.core.video_analysis_models import VideoModelSession
    from aiforstudy.providers.gateway import CallResult
    from aiforstudy.pipeline.services import CancelToken
    from types import SimpleNamespace
    session = VideoModelSession.__new__(VideoModelSession)
    session.directory = tmp_path; (tmp_path / 'exchanges').mkdir()
    session.token = CancelToken(); session.job_id = 'test'
    session.service = SimpleNamespace(_register_artifacts=lambda *args: None)
    session.config = SimpleNamespace(role=lambda role: SimpleNamespace(model='fixture'))
    tags = []
    def respond(request):
        tags.append(request.tag)
        payload = {'refs': ['bad' if len(tags) == 1 else 'good']}
        return CallResult(ok=True, payload=payload, text=json.dumps(payload))
    session.gateway = SimpleNamespace(call=respond)
    def validate(data):
        if data['refs'] != ['good']:
            raise ValueError('Invalid source ids')
    result = session.call('lesson', 'lesson-001', 'system', {}, validate=validate)
    assert result == {'refs': ['good']}
    assert len(set(tags)) == 2
    preserved = json.loads((tmp_path / 'exchanges/lesson-001.output.json').read_text())
    assert preserved['data'] == {'refs': ['bad']}
    assert preserved['validation_error']


@pytest.mark.parametrize('field,value', [('formulas', ['bad']), ('formulas', 'bad'), ('uncertainties', 'bad')])
def test_lesson_rejects_malformed_collections(field, value):
    from aiforstudy.core.video_analysis import validate_lesson
    data = {'sections': [{'id': 's', 'title': 'a', 'start': 0, 'end': 1, 'markdown': 'text', 'evidence_ids': ['a']}], 'formulas': [], 'uncertainties': []}
    data[field] = value
    with pytest.raises(ValueError):
        validate_lesson(data, {'a'}, 1)


@pytest.mark.parametrize('data', [{'frames': ['bad']}, {'frames': [{'id': 'a', 'visual_text': 'text', 'uncertainties': 'bad'}]}])
def test_visual_rejects_malformed_collections(data):
    from aiforstudy.core.video_analysis_models import validate_visual_response
    with pytest.raises(ValueError):
        validate_visual_response(data, {'a'})


@pytest.mark.parametrize('data', [{'decision': 'issues_found', 'issues': ['bad'], 'limitations': []}, {'decision': 'no_issues_found', 'issues': [], 'limitations': 'bad'}])
def test_review_rejects_malformed_collections(data):
    from aiforstudy.core.video_analysis_models import validate_review_response
    with pytest.raises(ValueError):
        validate_review_response(data, {'a'})


def test_protocol_repair_includes_candidate_errors_and_can_patch_selected_fields(tmp_path):
    from aiforstudy.core.video_analysis_models import VideoModelSession
    from aiforstudy.providers.gateway import CallResult
    from aiforstudy.pipeline.services import CancelToken
    from types import SimpleNamespace
    session = VideoModelSession.__new__(VideoModelSession)
    session.directory = tmp_path; (tmp_path / 'exchanges').mkdir()
    session.token = CancelToken(); session.job_id = 'test'
    session.service = SimpleNamespace(_register_artifacts=lambda *args: None)
    session.config = SimpleNamespace(role=lambda role: SimpleNamespace(model='fixture'))
    requests = []
    first = {'sections': [{'markdown': 'Preserve this prose'}], 'formulas': [{'latex': 'x=1', 'uncertainty': 'source note'}]}
    patch = {'formulas': [{'latex': 'x=1', 'uncertainty': '', 'note': 'source note'}]}
    def respond(request):
        requests.append(request)
        payload = first if len(requests) == 1 else patch
        return CallResult(ok=True, payload=payload, text=json.dumps(payload))
    session.gateway = SimpleNamespace(call=respond)
    def validate(data):
        if data['formulas'][0].get('uncertainty'):
            raise ValueError('formulas[0].uncertainty: use note for source explanations')
        assert data['sections'] == first['sections']
    answer = session.call('lesson', 'repair', 'system', {}, validate=validate, repair_fields=('formulas',))
    assert answer['sections'] == first['sections']
    assert answer['formulas'][0]['note'] == 'source note'
    assert len(requests) == 2
    assert any(m['role'] == 'assistant' and 'Preserve this prose' in m['content'] for m in requests[1].messages)
    assert 'formulas[0].uncertainty' in requests[1].messages[-1]['content']
    output = json.loads((tmp_path / 'exchanges/repair-repair-1.output.json').read_text())
    assert output['data'] == patch
    assert output['validated_data'] == answer
    assert json.loads((tmp_path / 'exchanges/repair.output.json').read_text())['data'] == first


@pytest.mark.parametrize('changed_file', ['result.json', 'lesson.md'])
def test_published_revision_tampering_blocks_api_and_downloads(tmp_path, monkeypatch, changed_file):
    from aiforstudy.core.video_analysis import _sha
    s = service(tmp_path, monkeypatch)
    src = s.register_upload(io.BytesIO(b'abc'), 3, 'test.mp4'); row = s.submit({'source_id': src['id']})
    job_id = row['job_id']; output = s.jobs / job_id / 'revisions' / ('b' * 32); output.mkdir(parents=True)
    (output / 'result.json').write_text('{"title":"Original"}')
    (output / 'lesson.md').write_text('Original markdown')
    receipt = {'state': 'completed', 'files': {name: _sha(output / name) for name in ('result.json', 'lesson.md')}}
    (output / 'revision.json').write_text(json.dumps(receipt))
    relative = output.relative_to(s.jobs / job_id).as_posix()
    s._register_artifacts(job_id, [relative + '/result.json', relative + '/lesson.md'])
    s._update(job_id, state='completed', active_result=relative+'/result.json', active_result_sha256=_sha(output/'result.json'))
    assert s.get(job_id)['result']['title'] == 'Original'
    (output / changed_file).write_text('{"title":"Tampered"}' if changed_file.endswith('.json') else 'Tampered markdown')
    with pytest.raises(ValueError, match='integrity|完整性'):
        s.get(job_id)
    assert s.artifact(job_id, relative + '/result.json') is None
    assert s.artifact(job_id, relative + '/lesson.md') is None


def test_successful_exchange_cannot_be_relabelled_after_model_change(tmp_path):
    from aiforstudy.core.video_analysis_models import VideoModelSession
    from aiforstudy.providers.gateway import CallResult
    from aiforstudy.pipeline.services import CancelToken
    from types import SimpleNamespace
    session = VideoModelSession.__new__(VideoModelSession)
    session.directory = tmp_path; (tmp_path/'exchanges').mkdir(); session.token = CancelToken(); session.job_id = 'fixture'
    session.service = SimpleNamespace(_register_artifacts=lambda *args: None)
    current_model = ['model-A']; calls = []
    session.config = SimpleNamespace(role=lambda role: SimpleNamespace(model=current_model[0]))
    def respond(request):
        calls.append(request)
        return CallResult(ok=True, payload={'text': 'model-A-output'}, text='{"text":"model-A-output"}')
    session.gateway = SimpleNamespace(call=respond)
    assert session.call('lesson', 'cached', 'system', {})['text'] == 'model-A-output'
    current_model[0] = 'model-B'
    with pytest.raises(ValueError, match='producer|生产身份'):
        session.call('lesson', 'cached', 'system', {})
    assert len(calls) == 1
    preserved = json.loads((tmp_path/'exchanges/cached.output.json').read_text())
    assert preserved['model'] == 'model-A'


def test_baseline_exports_are_checked_against_existing_export_receipt(tmp_path, monkeypatch):
    from aiforstudy.core.video_analysis import _sha
    s = service(tmp_path, monkeypatch)
    source = s.register_upload(io.BytesIO(b'abc'), 3, 'test.mp4')
    row = s.submit({'source_id': source['id']}); job_id = row['job_id']; directory = s.jobs / job_id
    (directory/'result.json').write_text('{"title":"Original"}')
    (directory/'lesson.md').write_text('Original lecture')
    (directory/'export.done.json').write_text(json.dumps({'files': {
        'result.json': _sha(directory/'result.json'), 'lesson.md': _sha(directory/'lesson.md')}}))
    s._register_artifacts(job_id, ['result.json', 'lesson.md']); s._update(job_id, state='completed')
    assert s.get(job_id)['result']['title'] == 'Original'
    assert s.artifact(job_id, 'lesson.md') is not None
    (directory/'lesson.md').write_text('Altered historical lecture')
    assert s.artifact(job_id, 'lesson.md') is None
    (directory/'result.json').write_text('{"title":"Altered historical result"}')
    assert s.artifact(job_id, 'result.json') is None
    with pytest.raises(ValueError, match='integrity|完整性'):
        s.get(job_id)
