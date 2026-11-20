"""Legacy CLI clients share the bounded HTTP boundary and release heartbeats."""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import model as legacy
from aiforstudy.providers import http_exchange


def response(content='{"answer": 42}'):
    return {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}}


def client(tmp_path, **kwargs):
    return legacy.DeepSeekClient(api_key='offline-placeholder', cache_dir=tmp_path,
                                 base_url='http://127.0.0.1:1', **kwargs)


def forbid_urllib(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen',
                        lambda *a, **k: pytest.fail('legacy client bypassed shared HTTP exchange'))


def test_default_call_can_finish_after_300_seconds_through_shared_exchange(tmp_path, monkeypatch):
    """The boundary, rather than a separate urllib call, receives the full deadline."""
    forbid_urllib(monkeypatch)
    captured = []
    def exchange(url, **kwargs):
        captured.append((url, kwargs))
        if kwargs['timeout'] <= 400:
            raise TimeoutError('offline simulated response arrives at 400 seconds')
        return 200, json.dumps(response()).encode()
    monkeypatch.setattr(http_exchange, 'exchange', exchange)
    model = client(tmp_path)
    result = model.chat_json([{'role': 'user', 'content': 'question'}], tag='one')
    assert result['data'] == {'answer': 42}
    assert result['usage']['total_tokens'] == 5
    assert not result['cached'] and result['fingerprint']
    assert model.calls == 1
    assert captured[0][0] == 'http://127.0.0.1:1/chat/completions'
    sent = captured[0][1]
    assert sent['method'] == 'POST'
    assert sent['headers']['Content-Type'] == 'application/json'
    assert sent['headers']['Authorization'] == 'Bearer offline-placeholder'
    assert json.loads(sent['body'])['messages'] == [{'role': 'user', 'content': 'question'}]
    again = model.chat_json([{'role': 'user', 'content': 'question'}], tag='one')
    assert again['cached'] and again['data'] == {'answer': 42}
    assert len(captured) == 1


@pytest.mark.parametrize(('effort', 'expected'), [
    ('off', 'none'), ('none', 'none'), ('minimal', 'low'), ('low', 'low'),
    ('medium', 'high'), ('high', 'high'), ('xhigh', 'high'), ('max', 'max'), ('ultra', 'max'),
])
def test_legacy_wire_uses_top_level_normalized_effort(tmp_path, monkeypatch, effort, expected):
    model = client(tmp_path, effort=effort, temperature=0.2, seed=7)
    sent = []
    def post(path, body):
        sent.append(body)
        return response()
    monkeypatch.setattr(model, '_post', post)
    assert model.chat_json([{'role': 'user', 'content': 'question'}], use_cache=False)['data'] == {'answer': 42}
    assert sent[0]['reasoning_effort'] == expected
    assert sent[0]['thinking'] == {'type': 'disabled' if expected == 'none' else 'enabled'}
    assert (sent[0]['temperature'], sent[0]['seed']) == (0.2, 7)


def test_invalid_effort_fails_before_http(tmp_path, monkeypatch):
    model = client(tmp_path, effort='unsupported')
    sent = []
    monkeypatch.setattr(model, '_post', lambda *args: sent.append(args) or response())
    with pytest.raises(legacy.ModelError, match='effort'):
        model.chat_json([{'role': 'user', 'content': 'question'}], use_cache=False)
    assert not sent


@pytest.mark.parametrize('failure', ['network', 'empty', 'json', 'none'])
def test_verbose_heartbeat_is_joined_on_success_and_every_failure(tmp_path, monkeypatch, failure):
    monkeypatch.setenv('AIFORSTUDY_VERBOSE_CALLS', '1')
    model = client(tmp_path)
    started = legacy._start_heartbeat
    heartbeats = []
    def track(tag):
        heartbeat = started(tag, every=60)
        heartbeats.append(heartbeat)
        return heartbeat
    monkeypatch.setattr(legacy, '_start_heartbeat', track)
    def post(*args):
        if failure == 'network':
            raise legacy.ModelError('offline request failed')
        return response('' if failure == 'empty' else 'not-json' if failure == 'json' else '{"answer":42}')
    monkeypatch.setattr(model, '_post', post)
    try:
        if failure == 'none':
            assert model.chat_json([{'role': 'user', 'content': 'question'}], use_cache=False)['data'] == {'answer': 42}
        else:
            with pytest.raises(legacy.ModelError):
                model.chat_json([{'role': 'user', 'content': 'question'}], use_cache=False)
        assert len(heartbeats) == 1
        assert not heartbeats[0].is_alive(), 'completed/failed call left its waiting heartbeat alive'
    finally:
        # Green implementation can always clean up; red daemon exits with pytest.
        for heartbeat in heartbeats:
            legacy._stop_heartbeat(heartbeat)


@pytest.mark.parametrize('first', [TimeoutError('offline timeout'), 429, 503])
def test_legacy_retries_shared_exchange_with_finite_attempts(tmp_path, monkeypatch, first):
    forbid_urllib(monkeypatch)
    calls = []
    sleeps = []
    def exchange(url, **kwargs):
        calls.append(kwargs['timeout'])
        if len(calls) < 3:
            if isinstance(first, Exception):
                raise first
            return first, b'{}'
        return 200, json.dumps(response()).encode()
    monkeypatch.setattr(http_exchange, 'exchange', exchange)
    monkeypatch.setattr(legacy.time, 'sleep', sleeps.append)
    assert client(tmp_path, timeout=7)._post('/chat/completions', {}) == response()
    assert calls == [7, 7, 7]
    assert sleeps == [2, 4]


@pytest.mark.parametrize('status', [302, 401])
def test_nonretryable_http_error_does_not_retry_or_log_response_body(tmp_path, monkeypatch, status):
    forbid_urllib(monkeypatch)
    calls = []
    def exchange(url, **kwargs):
        calls.append(url)
        return status, b'private-provider-response'
    monkeypatch.setattr(http_exchange, 'exchange', exchange)
    with pytest.raises(legacy.ModelError, match=f'HTTP {status}') as error:
        client(tmp_path)._post('/chat/completions', {})
    assert len(calls) == 1
    assert 'private-provider-response' not in str(error.value)


def test_legacy_large_request_reaches_real_loopback_worker_intact(tmp_path, monkeypatch):
    forbid_urllib(monkeypatch)
    seen = []
    question = '大请求正文' * 100_000
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            time.sleep(.12)
            body = self.rfile.read(int(self.headers['Content-Length']))
            seen.append((self.path, json.loads(body)))
            raw = json.dumps(response()).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        model = legacy.DeepSeekClient(api_key='offline-placeholder', cache_dir=tmp_path,
            base_url=f'http://127.0.0.1:{server.server_port}/v1', timeout=3)
        result = model.chat_json([{'role': 'user', 'content': question}], use_cache=False)
        assert result['data'] == {'answer': 42}
        assert len(seen) == 1
        assert seen[0][0] == '/v1/chat/completions'
        assert seen[0][1]['messages'][0]['content'] == question
        assert seen[0][1]['reasoning_effort'] == 'high'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
