import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from aiforstudy.providers.transport import RealTransport
from aiforstudy.pipeline.services import CancelToken
from aiforstudy.errors import Cancelled


def test_api_can_receive_response_after_300_seconds_with_1000_second_limit(monkeypatch):
    """A virtual clock exercises the production cutoff without a paid API call."""
    import subprocess
    from aiforstudy.providers import transport as module
    from aiforstudy.pipeline.execution_control import ExecutionPolicy

    import sys
    from aiforstudy.core.process_io import communicate_bounded

    clock = [0.0]
    original_popen = subprocess.Popen
    def spawn(*args, **kwargs):
        return original_popen([sys.executable, '-c',
            "import sys; sys.stdin.buffer.read(); print('{\"text\":\"{}\",\"usage\":{}}')"], **kwargs)
    def after_long_wait(process, payload, *, deadline, checkpoint=None):
        clock[0] = 400.0
        return communicate_bounded(process, payload, deadline=deadline, checkpoint=checkpoint)

    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    monkeypatch.setattr(module, 'communicate_bounded', after_long_wait)
    transport = RealTransport(model='test', api_key='offline-placeholder',
                              base_url='http://127.0.0.1:1')
    result = transport(model='test', effort='off', messages=[], images=[],
                       max_tokens=1, timeout=ExecutionPolicy().api_deadline_seconds)
    assert result == ('{}', {})
    assert clock[0] == 400.0


@pytest.mark.parametrize('cancel_it', [False, True])
def test_dribbling_response_cannot_outlive_deadline_or_cancel(cancel_it):
    seen = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Length', '1000000')
            self.end_headers()
            seen.set()
            try:
                for _ in range(200):
                    self.wfile.write(b' ')
                    self.wfile.flush()
                    time.sleep(.025)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token = CancelToken()
    timer = threading.Timer(.4, lambda: token.cancel('test')) if cancel_it else None
    transport = RealTransport(model='test', api_key='offline-placeholder',
                              base_url=f'http://127.0.0.1:{server.server_port}', cancel=token)
    started = time.monotonic()
    try:
        if timer:
            timer.start()
        with pytest.raises(Cancelled if cancel_it else TimeoutError):
            transport(model='test', effort='off', messages=[{'role':'user','content':'test'}],
                      images=[], max_tokens=1, timeout=1)
        assert time.monotonic() - started < 3
        assert seen.is_set()
        assert transport.last_remote_unconfirmed
    finally:
        if timer:
            timer.cancel()
        server.shutdown()
        server.server_close()


def test_response_wait_uses_api_deadline_after_short_connect_timeout():
    import subprocess
    import sys
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            time.sleep(.3)
            body = json.dumps({'choices':[{'message':{'content':'ok'}}], 'usage':{'total_tokens':1}}).encode()
            self.send_response(200); self.end_headers()
            try: self.wfile.write(body)
            except BrokenPipeError: pass
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        payload = dict(url=f'http://127.0.0.1:{server.server_port}/', key='stub',body={},connect_timeout=.1,read_timeout=2)
        result = subprocess.run([sys.executable,'-m','aiforstudy.providers.http_worker'],
                                input=json.dumps(payload).encode(),capture_output=True,timeout=3)
        assert json.loads(result.stdout).get('text') == 'ok'
    finally:
        server.shutdown();server.server_close()
