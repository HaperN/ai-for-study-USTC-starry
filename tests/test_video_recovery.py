"""Exercise durable publication and child transport with synthetic local sources."""
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from aiforstudy.core import video_analysis_models as models
from aiforstudy.core.video_analysis import VideoAnalysisService, _digest, _read, _sha
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy
from aiforstudy.providers.gateway import Gateway
from test_video_forward_progress import session_at
from test_video_lecture_reconciliation import case


class OwnerExit(BaseException):
    pass


@pytest.mark.parametrize('damage', [None, 'request', 'response', 'producer', 'ambiguous'])
def test_completed_child_response_survives_owner_exit(tmp_path, monkeypatch, damage):
    calls = []
    class Provider:
        cache_identity = {'model': 'offline'}
        def __call__(self, **kwargs):
            calls.append(kwargs)
            return '{"value":42}', {}
    provider = Provider()
    monkeypatch.setattr(models, 'configured_providers', lambda *a: SimpleNamespace(llm=provider))
    def child(args, log, token, timeout):
        with patch.object(sys, 'argv', args[2:]):
            models._transport_main()
        raise OwnerExit()
    def session():
        s = session_at(tmp_path, lambda req: pytest.fail('unexpected fallback'))
        s.service.workspace_root = tmp_path
        s.service._process = child
        transport = models.VideoModelTransport(provider, s.service, s.directory, s.token, 'lesson')
        control = ExecutionControl(ExecutionPolicy(attempt_limits={'transport': 2 if damage == 'ambiguous' else 1}))
        control.bind(s.directory / 'execution.json', run_id='fixture')
        s.gateway = Gateway(transport=transport, roles=s.config.roles, control=control, cancel=s.token,
                            run_id='fixture', book_id='fixture')
        return s
    def call():
        return session().call('lesson', 'unit', 'offline', {'context': 123})
    with pytest.raises(OwnerExit):
        call()
    if damage in ('request', 'response'):
        path, = (tmp_path / 'attempt/transport').glob('*.' + damage + '.json')
        data = _read(path)
        key = 'messages' if damage == 'request' else 'text'
        data[key] = 'changed'
        path.write_text(json.dumps(data))
    if damage == 'ambiguous':
        directory = tmp_path / 'attempt'
        ledger = _read(directory / 'execution.json')
        prior, = ledger['attempts']
        second = 'c' * 32
        ledger['attempts'].append({**prior, 'attempt_id': second, 'attempt': 2})
        (directory / 'execution.json').write_text(json.dumps(ledger))
        source, = (directory / 'transport').glob('*.request.json')
        other = source.with_name(second + '.request.json')
        other.write_text(json.dumps({**_read(source), 'attempt_id': second}))
        reply, = (directory / 'transport').glob('*.response.json')
        duplicate = {**_read(reply), 'request_sha256': _sha(other)}
        duplicate.pop('response_sha256')
        reply.with_name(second + '.response.json').write_text(json.dumps({**duplicate, 'response_sha256': _digest(duplicate)}))
    if damage == 'producer':
        provider.cache_identity = {'model': 'changed'}
    if damage:
        with pytest.raises((ValueError, RuntimeError), match='identity|integrity|producer|ambiguous'):
            call()
    else:
        assert call() == {'value': 42}
    assert len(calls) == 1


@pytest.fixture
def publication(tmp_path, monkeypatch):
    s = VideoAnalysisService(tmp_path)
    monkeypatch.setattr(s, '_launch', lambda job: None)
    monkeypatch.setattr(s, '_config_snapshot', lambda: {'dependencies': {'engine': 'fixture'}})
    upload = s.register_upload(io.BytesIO(b'abc'), 3, 'fixture.mp4')
    job = s.submit({'source_id': upload['id']})['job_id']
    root = s._jobdir(job)
    lesson, frames, transcript, corrected = case()
    pending = '等待听审 [asr-1]'
    registry = {'external_sources': [{'id': 'A1', 'url': 'https://example.org/paper', 'title': '资料', 'supports': '独立注释'}],
                'pending': [pending], 'limits': ['no listening']}
    old_id = 'a' * 32
    prefix = 'revisions/' + old_id
    old = root / prefix
    (old / 'evidence').mkdir(parents=True)
    layer = {'model': 'fixture', 'result': {'segments': [{'start': 1, 'end': 2, 'text': '独立转写'}]}}
    for name, data in [('review-evidence', registry), ('asr-turbo-full', layer),
                       ('dense-frame-index', [{'path': 'frame.jpg', 'time': 1}])]:
        (old / 'evidence' / (name + '.json')).write_text(json.dumps(data))
    base = {**lesson, 'job_id': job, 'title': 'Offline', 'source': {'path': 'source.mp4'}, 'duration': 40,
            'frames': frames, 'transcript': transcript, 'editorial_revision': {'id': old_id}, 'uncertainties': [pending],
            'provenance': {'editorial_revision': {'id': old_id}, 'active_editorial_producer': {'id': old_id},
                           'followup_source_reviews': [registry]}, 'exports': {'review_evidence': prefix + '/evidence/review-evidence.json'}}
    for directory in [root, old]:
        (directory / 'result.json').write_text(json.dumps(base))
        (directory / 'lesson.md').write_text('baseline')
    files = {p.relative_to(old).as_posix(): _sha(p) for p in old.rglob('*') if p.is_file()}
    (old / 'revision.json').write_text(json.dumps({'state': 'completed', 'files': files}))
    (root / 'export.done.json').write_text(json.dumps({'files': {n: _sha(root / n) for n in ['result.json','lesson.md']}}))
    s._register_artifacts(job, ['result.json','lesson.md', *(prefix + '/' + n for n in files)])
    s._update(job, state='completed', active_result=prefix+'/result.json', active_result_sha256=files['result.json'])
    contexts = []
    def respond(req):
        contexts.append(json.loads(req.messages[1]['content']))
        if req.role == 'lesson':
            return corrected
        return {'decision':'no_issues_found','issues':[], 'limitations':[]}
    session_type = models.VideoModelSession
    def factory(service, ident, token, *, directory):
        session = session_at(root, respond, directory.relative_to(root), session_type=session_type)
        session.service, session.job_id = service, ident
        return session
    monkeypatch.setattr(models, 'VideoModelSession', factory)
    pending_threads = []
    def thread(target,args,**kwargs):
        pending_threads.append(lambda: target(*args))
        return SimpleNamespace(start=lambda: None)
    monkeypatch.setattr('aiforstudy.core.video_analysis.threading.Thread', thread)
    s.reconcile(job)
    return s, job, contexts, pending_threads.pop(), base


def test_revision_archives_verification_and_retains_sources(publication):
    s, job, contexts, execute, base = publication
    execute()
    result = s.get(job)['result']
    assert 'editorial_revision' not in result
    for key in ('active_editorial_producer', 'editorial_revision'):
        assert key not in result['provenance']
    assert base['editorial_revision']['id'] in json.dumps(result['provenance']['editorial_history'])
    assert result['exports']['review_evidence'] == base['exports']['review_evidence']
    assert base['uncertainties'][0] in result['uncertainties']
    assert len(contexts) == 2
    for ctx in contexts:
        value = json.dumps(ctx, ensure_ascii=False)
        assert all(term in value for term in ['https://example.org/paper','独立转写','dense-frame-index','no listening'])
    markdown = s.artifact(job,result['exports']['markdown']).read_text()
    assert 'https://example.org/paper' in markdown and '独立注释' in markdown
    for row in result['provenance']['review_source_artifacts']:
        assert Path(row['path']).name in markdown
        assert _sha(s.artifact(job, row['path'])) == row['sha256']


@pytest.mark.parametrize('damage', [None, 'companion', 'parent', 'receipt_id', 'receipt_state'])
def test_publish_after_owner_exit_without_model(publication, monkeypatch, damage):
    s, job, contexts, execute, base = publication
    update = s._update
    def stop(job, **values):
        if 'active_result' in values:
            raise OwnerExit()
        return update(job, **values)
    monkeypatch.setattr(s, '_update', stop)
    with pytest.raises(OwnerExit):
        execute()
    monkeypatch.setattr(s, '_update', update)
    root = s._jobdir(job)
    row = _read(root/'job.json')
    output = root/'revisions'/row['revision']['id']
    if damage == 'companion':
        (output/'lesson.md').write_text('tamper')
    if damage == 'parent':
        s._update(job, active_result='result.json',active_result_sha256=_sha(root/'result.json'))
    if damage in ('receipt_id','receipt_state'):
        receipt = _read(output/'revision.json')
        receipt.update({'id': 'b' * 32} if damage == 'receipt_id' else {'state': 'failed'})
        (output/'revision.json').write_text(json.dumps(receipt))
    if damage:
        before = _read(root/'job.json')['active_result']
        with pytest.raises(ValueError):
            s.resume(job)
        assert _read(root/'job.json')['active_result'] == before
    else:
        for _ in range(2):
            assert s.resume(job)['active_result'] == output.relative_to(root).as_posix() + '/result.json'
    assert len(contexts) == 2


@pytest.fixture
def wire(tmp_path, monkeypatch):
    """Use the production session constructor and child CLI, replacing the actual provider and process."""
    from aiforstudy.providers.llm import RoleConfig, ProviderConfig
    from aiforstudy.pipeline.services import CancelToken
    from aiforstudy.providers.response import TransportResponse
    state = SimpleNamespace(replies=[], calls=[], limit=1, exit_after_worker=False, after_worker=None)
    class Offline:
        cache_identity = {'provider': 'offline'}
        def __call__(self, **kwargs):
            state.calls.append(kwargs)
            text, metadata = state.replies.pop(0)
            return TransportResponse(text, {}, metadata=metadata)
    provider = Offline()
    monkeypatch.setattr(models, 'configured_providers', lambda *a: SimpleNamespace(llm=provider))
    roles = {'lesson': RoleConfig('lesson', 'fixture', 'low', 10, length_retry_max_tokens=20)}
    monkeypatch.setattr(models, 'load_config', lambda *a: ProviderConfig('fixture','','',None,roles))
    monkeypatch.setattr(models, 'load_policy', lambda *a: ExecutionPolicy(
        attempt_limits={'transport': state.limit}, api_deadline_seconds=60))
    def process(args, log, token, timeout):
        with patch.object(sys, 'argv', args[2:]):
            models._transport_main()
        if state.after_worker:
            state.after_worker(token)
        if state.exit_after_worker:
            state.exit_after_worker = False
            raise OwnerExit()
    service = SimpleNamespace(workspace_root=tmp_path, _process=process, _jobdir=lambda job:tmp_path,
                              _register_artifacts=lambda *a:None)
    state.session = lambda: models.VideoModelSession(service, 'fixture', CancelToken())
    state.call = lambda session: session.call('lesson', 'unit', 'fixture', {'source':'raw'})
    state.ledger = tmp_path / 'execution.json'
    return state


@pytest.mark.parametrize('raw', ['bad json', ''])
def test_protocol_outcome_replays_into_repair_at_exhausted_quota(wire, monkeypatch, raw):
    wire.replies = [(raw, {}), ('{"fixed":true}', {})]
    first = wire.session()
    finish = first.gateway.control.finish
    def exit_after_close(*args, **kwargs):
        finish(*args, **kwargs)
        raise OwnerExit()
    monkeypatch.setattr(first.gateway.control, 'finish', exit_after_close)
    with pytest.raises(OwnerExit):
        wire.call(first)
    before, = _read(wire.ledger)['attempts']
    assert before['state'] == 'protocol_failed'
    assert wire.call(wire.session()) == {'fixed':True}
    assert [len(c['messages']) for c in wire.calls] == [2,4]
    assert wire.calls[1]['messages'][2]['content'] == raw
    original, repair = _read(wire.ledger)['attempts']
    assert original == before and repair['fingerprint'] != original['fingerprint']


@pytest.mark.parametrize('expire', [False, True])
def test_truncation_evidence_survives_before_close(wire, monkeypatch, expire):
    wire.limit = 2
    wire.replies = [('truncated', {'finish_reason':'length'}), ('{"fixed":true}', {})]
    first = wire.session()
    def exit_before_close(*args, **kwargs):
        raise OwnerExit()
    monkeypatch.setattr(first.gateway.control, 'finish', exit_before_close)
    with pytest.raises(OwnerExit):
        wire.call(first)
    assert _read(wire.ledger)['attempts'][0]['state'] == 'dispatched'
    if expire:
        ledger = _read(wire.ledger)
        ledger['run_started_at'] -= 120
        wire.ledger.write_text(json.dumps(ledger))
        with pytest.raises(TimeoutError):
            wire.call(wire.session())
        assert [c['max_tokens'] for c in wire.calls] == [10]
        assert _read(wire.ledger)['attempts'][0]['state'] == 'protocol_failed'
    else:
        assert wire.call(wire.session()) == {'fixed':True}
        assert [c['max_tokens'] for c in wire.calls] == [10,20]
        assert [a['state'] for a in _read(wire.ledger)['attempts']] == ['protocol_failed','response_received']


@pytest.mark.parametrize('damage', ['missing', 'stripped', 'scope', 'fingerprint'])
def test_artifacts_prevent_accounting_reset(wire, damage):
    from aiforstudy.errors import ContractError
    wire.exit_after_worker = True
    wire.replies = [('{"ok":true}', {})]
    with pytest.raises(OwnerExit):
        wire.call(wire.session())
    ledger = _read(wire.ledger)
    if damage == 'missing':
        wire.ledger.unlink()
    else:
        if damage == 'stripped':
            ledger['attempts'] = []
        elif damage == 'scope':
            ledger['attempts'][0]['scope']['unit_id'] = 'other'
        else:
            ledger['attempts'][0]['fingerprint'] = 'other'
        wire.ledger.write_text(json.dumps(ledger))
    with pytest.raises((ValueError,ContractError)):
        wire.session()
    assert len(wire.calls) == 1
    if damage == 'missing':
        assert not wire.ledger.exists()


@pytest.mark.parametrize('cancel', [None, 'token', 'control'])
def test_completed_response_survives_run_deadline_but_not_cancel(wire, cancel):
    from aiforstudy.errors import Cancelled
    wire.exit_after_worker = True
    wire.replies = [('{"ok":true}', {})]
    with pytest.raises(OwnerExit):
        wire.call(wire.session())
    ledger = _read(wire.ledger)
    ledger['run_started_at'] -= 120
    wire.ledger.write_text(json.dumps(ledger))
    second = wire.session()
    if cancel:
        if cancel == 'token':
            second.token.cancel('test')
        else:
            second.gateway.control.stop('test')
        with pytest.raises(Cancelled):
            wire.call(second)
    else:
        assert wire.call(second) == {'ok': True}
    assert len(wire.calls) == 1


@pytest.mark.parametrize('state', ['network_wait', 'network_retry', 'blocked', 'failed'])
def test_child_response_rejects_inconsistent_ledger_state(wire, state):
    wire.exit_after_worker = True
    wire.replies = [('{"saved":true}', {}), ('{"duplicate":true}', {})]
    with pytest.raises(OwnerExit):
        wire.call(wire.session())
    ledger = _read(wire.ledger)
    ledger['attempts'][0]['state'] = state
    wire.ledger.write_text(json.dumps(ledger))
    with pytest.raises(ValueError, match='state'):
        wire.call(wire.session())
    assert len(wire.calls) == 1
    assert _read(wire.ledger) == ledger


@pytest.mark.parametrize('limit', [1, 2])
@pytest.mark.parametrize('stop', ['timeout', 'cancel'])
def test_stopped_worker_after_response_only_dispatches_once(wire, limit, stop, monkeypatch):
    from aiforstudy.errors import Cancelled
    wire.limit = limit
    wire.replies = [('{"saved":true}', {}), ('{"duplicate":true}', {})]
    first = wire.session()
    monkeypatch.setattr(first.gateway, '_wait_network_retry', lambda: None)
    def stop_polling(token):
        if stop == 'cancel':
            token.cancel('cancelled during final worker poll')
            token.checkpoint()
        raise TimeoutError('worker exit wait timed out')
    wire.after_worker = stop_polling
    with pytest.raises(Cancelled if stop == 'cancel' else TimeoutError):
        wire.call(first)
    assert len(wire.calls) == 1
    before, = _read(wire.ledger)['attempts']
    assert before['state'] == ('cancelled' if stop == 'cancel' else 'transport_failed')
    if stop == 'cancel':
        with pytest.raises(Cancelled):
            wire.call(first)
    wire.after_worker = None
    resumed = wire.session()
    assert wire.call(resumed) == {'saved': True}
    assert wire.call(resumed) == {'saved': True}
    after, = _read(wire.ledger)['attempts']
    assert after['attempt_id'] == before['attempt_id'] and after['attempt'] == 1
    assert after['state'] == 'response_received'
    assert len(wire.calls) == 1
