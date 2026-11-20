"""Real subprocess/loopback tests for the input > pipe capacity deadlock."""
import base64
import hashlib
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from aiforstudy.errors import Cancelled
from aiforstudy.pipeline.services import CancelToken
from aiforstudy.providers.http_exchange import exchange
from aiforstudy.providers.transport import RealTransport
from aiforstudy.providers.gateway import Gateway, CallRequest
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy


@pytest.fixture
def endpoint():
    received = []
    reply = b'r' * (2 * 1024 * 1024)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            received.append((self.command, self.path, dict(self.headers), raw))
            if self.path == '/chat/completions':
                body = json.dumps({'choices': [{'message': {'content': json.dumps({
                    'sha256': hashlib.sha256(raw).hexdigest(), 'reply': reply.decode()})}}],
                    'usage': {'total_tokens': 7}}).encode()
            else:
                body = reply
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        do_GET = do_PUT = do_POST
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', received, reply
    server.shutdown()
    server.server_close()
    thread.join(2)


@pytest.fixture
def delayed_workers(monkeypatch):
    """Delay worker startup beyond the first polling slice, not its I/O code."""
    original = subprocess.Popen
    children = []
    def spawn(argv, **kwargs):
        target = argv[2] if argv[1] == '-m' else argv[1]
        launch = 'runpy.run_module' if argv[1] == '-m' else 'runpy.run_path'
        source = f'import runpy,time; time.sleep(.15); {launch}({target!r},run_name="__main__")'
        child = original([sys.executable, '-c', source], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    yield children
    for child in children:
        if child.poll() is None:
            child.kill()
            child.wait(2)
        assert child.poll() is not None


@pytest.mark.parametrize('kind', ['text', 'image'])
def test_model_large_input_and_output_survive_slow_worker(endpoint, delayed_workers, tmp_path, kind):
    url, received, _ = endpoint
    images = []
    content = 'x' * (2 * 1024 * 1024)
    if kind == 'image':
        path = tmp_path / 'image.png'
        path.write_bytes(b'\x89PNG\r\n\x1a\n' + b'p' * (2 * 1024 * 1024))
        images = [str(path)]
        content = 'Return JSON.'
    transport = RealTransport(model='test', api_key='placeholder', base_url=url)
    text, usage = transport(model='test', effort='high',
        messages=[{'role': 'user', 'content': content}], images=images, max_tokens=32, timeout=2)
    body = received[0][3]
    assert json.loads(text)['sha256'] == hashlib.sha256(body).hexdigest()
    assert len(json.loads(text)['reply']) == 2 * 1024 * 1024
    assert usage == {'total_tokens': 7}
    sent_content = json.loads(body)['messages'][0]['content']
    if images:
        assert base64.b64decode(sent_content[1]['image_url']['url'].split(',', 1)[1]) == path.read_bytes()
    else:
        assert sent_content == content
    assert len(received) == 1


@pytest.mark.parametrize(('method', 'body'), [('POST', b'x' * 2097152),
    ('PUT', b'%PDF-' + b'x' * 2097152), ('GET', None), ('PUT', b'')],
    ids=['post-large', 'put-large', 'get', 'put-empty'])
def test_mineru_methods_keep_full_request_and_response(endpoint, delayed_workers, method, body):
    url, received, reply = endpoint
    status, raw = exchange(url + '/payload?signature=keep', method=method, body=body, timeout=2)
    assert (status, raw) == (200, reply)
    assert received[0][0:2] == (method, '/payload?signature=keep')
    assert received[0][3] == (body or b'')
    assert 'Content-Type' not in received[0][2]


def test_every_configured_role_reaches_the_real_worker_and_returns_json(endpoint, tmp_path):
    url, received, _ = endpoint
    config = json.loads((Path(__file__).resolve().parents[2] / 'config/models.json').read_text())
    control = ExecutionControl(ExecutionPolicy(api_deadline_seconds=3))
    control.bind(tmp_path / 'execution.json', run_id='api-offline')
    transport = RealTransport(model='fallback-must-not-win', api_key='placeholder', base_url=url)
    gateway = Gateway(transport=transport, roles=config['roles'], control=control)
    for name, spec in config['roles'].items():
        result = gateway.call(CallRequest(role=name, stage='api-connectivity', tag=name,
            messages=[{'role': 'user', 'content': 'Return JSON for ' + name}], max_tokens=17))
        assert result.ok and not result.cached and result.attempts == 1
        wire = json.loads(received[-1][3])
        assert wire['model'] == spec['model']
        assert wire['reasoning_effort'] == spec['effort']
        assert wire['max_tokens'] == 17
        assert result.payload['sha256'] == hashlib.sha256(received[-1][3]).hexdigest()
    assert transport.calls == len(config['roles']) == len(received)
    assert all(row['state'] == 'response_received' for row in control.snapshot()['attempts'])


@pytest.mark.parametrize('api', ['model', 'mineru'])
@pytest.mark.parametrize('cancel_it', [False, True])
def test_incomplete_input_can_be_cancelled_or_timed_out(monkeypatch, api, cancel_it):
    original = subprocess.Popen
    children = []
    def spawn(argv, **kwargs):
        child = original([sys.executable, '-c', 'import time; time.sleep(20)'], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    cancel = CancelToken(grace_seconds=.1)
    timer = threading.Timer(.15, lambda: cancel.cancel('test'))
    started = time.monotonic()
    try:
        if cancel_it:
            timer.start()
        with pytest.raises(Cancelled if cancel_it else TimeoutError):
            if api == 'mineru':
                exchange('http://127.0.0.1:1', method='PUT', body=b'x' * 2097152,
                         timeout=.5, checkpoint=cancel.checkpoint)
            else:
                RealTransport(model='test', api_key='placeholder', base_url='http://127.0.0.1:1',
                              cancel=cancel, cancel_grace_seconds=.1)(
                    model='test', effort='off', messages=[{'role':'user','content':'x'*2097152}],
                    images=[], max_tokens=1, timeout=.5)
        assert time.monotonic() - started < 2
        assert children and all(child.poll() is not None for child in children)
    finally:
        timer.cancel()
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(2)
