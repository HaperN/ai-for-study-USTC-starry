"""Unlimited response waits retain pipe progress, cancellation and connect limits."""
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from aiforstudy.core.process_io import communicate_bounded
from aiforstudy.errors import Cancelled
from aiforstudy.pipeline.services import CancelToken
from aiforstudy.providers.http_exchange import exchange
from aiforstudy.providers.transport import CancelAwareCall, RealTransport


@pytest.fixture
def delayed_endpoint():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            time.sleep(.15)
            body = json.dumps({'choices': [{'message': {'content': '{"ok":true}'}}],
                               'usage': {'total_tokens': 1}}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        do_GET = do_POST
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    thread.join(2)


def test_unlimited_pipe_progresses_large_input_and_output():
    process = subprocess.Popen([sys.executable, '-c',
        "import sys,time; time.sleep(.1); data=sys.stdin.buffer.read(); "
        "sys.stdout.buffer.write(data); sys.stderr.write('done')"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert communicate_bounded(process, b'x' * 2097152, deadline=None) == (b'x' * 2097152, b'done')
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(2)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def test_unlimited_model_response_ignores_instance_cap_but_keeps_connect_limit(delayed_endpoint, monkeypatch):
    from aiforstudy.providers import transport as module
    observed = []
    original = module.communicate_bounded
    def capture(process, payload, **kwargs):
        observed.append((json.loads(payload), kwargs['deadline']))
        return original(process, payload, **kwargs)
    monkeypatch.setattr(module, 'communicate_bounded', capture)
    transport = RealTransport(model='test', api_key='placeholder', base_url=delayed_endpoint,
                              timeout=.01, connect_timeout_seconds=.1)
    result = transport(model='test', effort='off', messages=[{'role': 'user', 'content': 'ok'}],
                       images=[], max_tokens=8, timeout=None)
    assert result == ('{"ok":true}', {'total_tokens': 1})
    assert observed[0][0]['connect_timeout'] == .1
    assert observed[0][0]['read_timeout'] is None
    assert observed[0][1] is None


def test_unlimited_http_exchange_still_has_connect_timeout(delayed_endpoint, monkeypatch):
    from aiforstudy.core import process_io
    observed = []
    original = process_io.communicate_bounded
    def capture(process, payload, **kwargs):
        observed.append((json.loads(payload.split(b'\n', 1)[0]), kwargs['deadline']))
        return original(process, payload, **kwargs)
    monkeypatch.setattr(process_io, 'communicate_bounded', capture)
    status, body = exchange(delayed_endpoint, timeout=None)
    assert status == 200 and json.loads(body)['usage']['total_tokens'] == 1
    assert observed[0][0]['timeout'] == 15
    assert observed[0][0]['read_timeout'] is None
    assert observed[0][1] is None


def test_unlimited_sync_wrapper_does_not_restore_default_1000_seconds(monkeypatch):
    from aiforstudy.providers import transport as module
    clock = iter([0, 2000, 2001])
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: next(clock)))
    wrapper = CancelAwareCall(lambda **kwargs: (time.sleep(.05), kwargs['timeout'])[1])
    assert wrapper(timeout=None) is None
    assert wrapper.remote_confirmed and wrapper.wait_closed(1)


@pytest.mark.parametrize('api', ['model', 'exchange', 'sync-wrapper'])
def test_unlimited_wait_can_be_cancelled_and_worker_reaped(monkeypatch, api):
    original = subprocess.Popen
    children = []
    def spawn(argv, **kwargs):
        child = original([sys.executable, '-c', 'import time; time.sleep(20)'], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    class PolledCancelToken(CancelToken):
        checks = 0
        @property
        def cancelled(self):
            self.checks += 1
            if self.checks == 3:
                self.cancel('unlimited-test')
            return super().cancelled
    token = (PolledCancelToken if api == 'sync-wrapper' else CancelToken)(grace_seconds=.1)
    timer = threading.Timer(.15, lambda: token.cancel('unlimited-test'))
    started = time.monotonic()
    wrapper = None
    try:
        if api != 'sync-wrapper':
            timer.start()
        with pytest.raises(Cancelled):
            if api == 'exchange':
                exchange('http://127.0.0.1:1', method='POST', body=b'x' * 2097152,
                         timeout=None, checkpoint=token.checkpoint)
            elif api == 'model':
                RealTransport(model='test', api_key='placeholder', base_url='http://127.0.0.1:1',
                              cancel=token, cancel_grace_seconds=.1)(
                    model='test', effort='off', messages=[{'role':'user','content':'x' * 2097152}],
                    images=[], max_tokens=1, timeout=None)
            else:
                wrapper = CancelAwareCall(lambda **kwargs: time.sleep(20), cancel=token,
                                          grace_seconds=.1)
                wrapper(timeout=None)
        assert time.monotonic() - started < 2
        assert all(child.poll() is not None for child in children)
        if wrapper is not None:
            assert wrapper.wait_closed(1) and wrapper.remote_unconfirmed
    finally:
        timer.cancel()
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(2)
