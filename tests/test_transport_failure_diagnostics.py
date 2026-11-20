"""Keep enough network evidence to diagnose failures without leaking request data."""
import io
import json
import ssl
from types import SimpleNamespace

import pytest

from aiforstudy.errors import Cancelled
from aiforstudy.pipeline.execution_control import ExecutionControl, AttemptLimitExceeded
from aiforstudy.pipeline.services import CancelToken
from aiforstudy.providers.gateway import CallRequest, Gateway


@pytest.mark.parametrize('phase', ['connect', 'send_request', 'receive_headers', 'read_body'])
def test_worker_records_ssl_phase_and_status_without_exception_text(monkeypatch, phase):
    from aiforstudy.providers import http_worker
    error = ssl.SSLEOFError(8, 'PRIVATE exception contains request/key/body')
    error.reason = 'UNEXPECTED_EOF_WHILE_READING'
    class Connection:
        sock = SimpleNamespace(settimeout=lambda value: None)
        status = 200
        length = None
        def __init__(self, *args, **kwargs): pass
        def fail(self, at):
            if phase == at: raise error
        def connect(self): self.fail('connect')
        def request(self, *args, **kwargs): self.fail('send_request')
        def getresponse(self):
            self.fail('receive_headers')
            return self
        def read(self, *args): self.fail('read_body')
        def close(self): pass
    output = io.StringIO()
    monkeypatch.setattr(http_worker.http.client, 'HTTPSConnection', Connection)
    monkeypatch.setattr(http_worker.sys, 'stdin', io.StringIO(json.dumps({
        'url': 'https://example.invalid/chat/completions', 'connect_timeout': 1,
        'key': 'PRIVATE-key', 'body': {'messages': ['PRIVATE-body']}})))
    monkeypatch.setattr(http_worker.sys, 'stdout', output)
    http_worker.main()
    result = json.loads(output.getvalue())
    assert result['error'] == 'ConnectionError'
    assert result['transport_details']['phase'] == phase
    assert result['transport_details']['cause_type'] == 'SSLEOFError'
    assert result['transport_details']['ssl_reason'] == 'UNEXPECTED_EOF_WHILE_READING'
    assert result['transport_details']['errno'] == 8
    assert result['transport_details'].get('http_status') == (200 if phase == 'read_body' else None)
    assert 'PRIVATE' not in output.getvalue()


def _gateway(tmp_path, wire, cancel=None):
    control = ExecutionControl()
    control.bind(tmp_path/'execution.json', run_id='network-retry')
    return Gateway(transport=wire, roles={'judge': {'model': 'offline', 'max_tokens': 8}},
                   control=control, cancel=cancel)


def _request():
    return CallRequest(role='judge', messages=[{'role': 'user', 'content': 'offline'}],
                       stage='media_review', unit_key='U')


def test_network_retry_waits_without_adding_attempts_and_can_then_succeed(tmp_path, monkeypatch):
    from aiforstudy.providers import gateway as module
    now = [0.0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0],
                         sleep=lambda seconds: now.__setitem__(0, now[0]+seconds)))
    starts = []
    def wire(**kwargs):
        starts.append(now[0])
        if len(starts) == 1: raise ConnectionError('SSLEOFError')
        return '{"ok":true}', {}
    result = _gateway(tmp_path, wire).call(_request())
    assert result.ok and result.attempts == 2
    assert len(starts) == 2 and starts[1]-starts[0] >= 5
    rows = json.loads((tmp_path/'execution.json').read_text())['attempts']
    assert [r['state'] for r in rows] == ['transport_failed', 'response_received']


def test_cancel_during_network_wait_does_not_send_another_request(tmp_path, monkeypatch):
    from aiforstudy.providers import gateway as module
    cancel = CancelToken()
    now = [0.0]
    def sleep(seconds):
        now[0] += seconds
        cancel.cancel('stop during backoff')
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0], sleep=sleep))
    sent = []
    def wire(**kwargs):
        sent.append(kwargs)
        raise ConnectionError('SSLEOFError')
    with pytest.raises(Cancelled, match='stop during backoff'):
        _gateway(tmp_path, wire, cancel).call(_request())
    assert len(sent) == 1 and now[0] <= .1


def test_failed_network_attempts_remain_exhausted_after_resume(tmp_path, monkeypatch):
    from aiforstudy.providers import gateway as module
    now = [0.0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0],
                         sleep=lambda seconds: now.__setitem__(0, now[0]+seconds)))
    sent = []
    def wire(**kwargs):
        sent.append(kwargs)
        raise ConnectionError('SSLEOFError')
    result = _gateway(tmp_path, wire).call(_request())
    assert not result.ok and result.attempts == 2 and result.failure_kind == 'network'
    assert now[0] == pytest.approx(5)
    with pytest.raises(AttemptLimitExceeded):
        _gateway(tmp_path, wire).call(_request())
    assert len(sent) == 2
