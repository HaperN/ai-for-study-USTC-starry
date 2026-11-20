"""Run creation and configured transport boundaries, entirely offline."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from aiforstudy.errors import Cancelled, ContractError
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy
from aiforstudy.pipeline.services import CancelToken, Providers
from aiforstudy.providers.gateway import CallRequest, Gateway
from aiforstudy.providers.transport import CancelAwareCall, RealTransport


def test_transport_import_is_safe_in_a_fresh_process():
    result = subprocess.run([sys.executable, '-B', '-c',
        'from aiforstudy.providers.transport import RealTransport; print(RealTransport.__name__)'],
        capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'RealTransport'


def test_failed_initial_journal_does_not_publish_a_queued_run(tmp_path, monkeypatch):
    from preparation_book import _mini_book, _request
    from aiforstudy.pipeline.engine import build_engine
    from aiforstudy.pipeline.state import RunStore
    ws = _mini_book(tmp_path)
    engine = build_engine(_request('mini-book'), ws, providers=Providers(), until='mineru')
    def disk_failure(): raise OSError('offline disk failure')
    monkeypatch.setattr(engine.services.control, '_persist', disk_failure)
    with pytest.raises(OSError, match='disk failure'):
        RunStore.create(ws.root / 'runs', engine.request, engine.plan, control=engine.services.control)
    assert not list((ws.root / 'runs').glob('*/state.json'))


def test_repeated_cancel_does_not_extend_the_existing_grace_window():
    from aiforstudy.pipeline.execution_control import remaining_cancel_grace
    token = CancelToken(); token.cancel('first')
    original = token.cancel_requested_at
    token.cancel('second')
    assert token.cancel_requested_at == original and token.reason == 'first'
    from types import SimpleNamespace
    old = SimpleNamespace(cancel_requested_at=time.monotonic() - 2)
    assert remaining_cancel_grace(.15, old) == 0


def test_store_publishes_queued_only_after_initial_execution_journal(tmp_path, monkeypatch):
    from preparation_book import _mini_book, _request
    from aiforstudy.pipeline.engine import build_engine
    from aiforstudy.pipeline.state import RunStore
    ws = _mini_book(tmp_path)
    engine = build_engine(_request('mini-book'), ws, providers=Providers(), until='mineru')
    save = RunStore.save_state
    observed = []
    def inspect(store, state):
        journal = json.loads((store.root / 'execution.json').read_text())
        assert journal['run_id'] == store.run_id
        assert journal['attempts'] == []
        assert journal['policy_digest'] == engine.services.control.policy.digest()
        observed.append(state['status'])
        return save(store, state)
    monkeypatch.setattr(RunStore, 'save_state', inspect)
    store = RunStore.create(ws.root / 'runs', engine.request, engine.plan,
                            control=engine.services.control)
    assert observed == ['queued']
    restored = ExecutionControl(engine.services.control.policy)
    restored.bind(store.root / 'execution.json', run_id=store.run_id, require_existing=True)
    assert restored.snapshot()['attempts'] == []


def test_import_candidate_queued_run_can_resume_without_recreating_identity(tmp_path, monkeypatch):
    from preparation_book import _mini_book, _tree
    from test_v4_candidate_import import migration_input
    from aiforstudy import cli
    from aiforstudy.contracts.request import RunRequest
    from aiforstudy.pipeline.engine import resume_request
    ws = _mini_book(tmp_path)
    source_before = _tree(ws.root / 'book')
    old, fields = migration_input()
    source = tmp_path / 'old.json'; source.write_text(json.dumps(old))
    field_path = tmp_path / 'fields.json'; field_path.write_text(json.dumps(fields))
    monkeypatch.setattr(cli, 'open_workspace', lambda _: ws)
    monkeypatch.setattr('aiforstudy.pipeline.engine.configured_providers', lambda *a: Providers())
    reports = []
    monkeypatch.setattr(cli, '_emit', lambda report, *_: reports.append(report))
    args = cli.build_parser().parse_args(['import-candidate', '--book', 'mini-book',
        '--unit', 'CH01-S01', '--source', str(source), '--v4-fields', str(field_path)])
    assert cli.cmd_import_candidate(args) == 0
    run_id = reports[0]['run_id']
    root = ws.root / 'data/books/mini-book/runs' / run_id
    request_bytes = (root / 'request.json').read_bytes()
    request = RunRequest.from_dict(json.loads(request_bytes))
    result, store, _ = resume_request(request, run_id, ws, providers=Providers(), until='mineru')
    assert store.run_id == run_id
    assert store.node_states()['mineru'].finished_ok(), result.as_dict()
    assert store.request_path.read_bytes() == request_bytes
    assert _tree(ws.root / 'book') == source_before
    assert source.read_text() == json.dumps(old)


def test_ui_queued_before_worker_start_resumes_after_restart(tmp_path, monkeypatch):
    from preparation_book import _mini_book
    from aiforstudy.ui.server import ControlServer
    ws = _mini_book(tmp_path)
    queued = ControlServer(ws)
    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, 'start', lambda _: None)
        reply = queued.api_start({'book_id': 'mini-book', 'units': ['CH01-S01'], 'profile': 'smoke'})
    root = ws.root / 'data/books/mini-book/runs' / reply['run_id']
    assert json.loads((root / 'state.json').read_text())['status'] == 'queued'
    restarted = ControlServer(ws)
    restarted.api_resume(reply['run_id'], {})
    task = restarted.state.tasks[reply['run_id']]
    task.thread.join(timeout=10)
    assert not task.thread.is_alive()
    state = json.loads((root / 'state.json').read_text())
    assert state['nodes'], state
    assert '缺持久执行记录' not in str(state)
    assert json.loads((root / 'execution.json').read_text())['run_id'] == reply['run_id']


def test_deleted_existing_journal_is_not_reconstructed_on_resume(tmp_path):
    from preparation_book import _mini_book, _request
    from aiforstudy.pipeline.engine import build_engine, execute, resume_request
    ws = _mini_book(tmp_path)
    req = _request('mini-book')
    _, store = execute(build_engine(req, ws, providers=Providers(), until='mineru'))
    (store.root / 'execution.json').unlink()
    with pytest.raises(ContractError, match='缺持久执行记录'):
        resume_request(req, store.run_id, ws, providers=Providers(), until='mineru')
    assert not (store.root / 'execution.json').exists()


def test_gateway_policy_reaches_real_worker_connect_and_cancel_limits(tmp_path, monkeypatch):
    captured = []
    class Worker:
        returncode = 0
        stdin = None
        stdout = None
        def __init__(self, *a, **kw): pass
        def communicate(self, input=None, timeout=None):
            captured.append(json.loads(input))
            return b'{"text":"{}","usage":{}}', None
        def poll(self): return self.returncode
    monkeypatch.setattr(subprocess, 'Popen', Worker)
    from aiforstudy.providers import transport as transport_module
    monkeypatch.setattr(transport_module, 'communicate_bounded',
        lambda process, payload, **kw: process.communicate(input=payload))
    ctl = ExecutionControl(ExecutionPolicy(connect_timeout_seconds=.125,
        cancel_grace_seconds=.25, api_deadline_seconds=2))
    ctl.bind(tmp_path / 'execution.json', run_id='r')
    transport = RealTransport(model='offline', api_key='offline-placeholder', base_url='http://127.0.0.1')
    gateway = Gateway(transport=transport, roles={'lesson': {'model':'offline', 'effort':'off'}}, control=ctl)
    assert gateway.call(CallRequest(role='lesson', messages=[])).ok
    assert captured[0]['connect_timeout'] == .125
    assert 1.5 < captured[0]['read_timeout'] <= 2
    assert transport.cancel_grace_seconds == .25
    # New controller/policy is not rebound to an existing journal; direct worker
    # boundary still caps a connect timeout by the remaining single-call deadline.
    transport(model='offline', effort='off', messages=[], images=None, max_tokens=1, timeout=.05)
    assert captured[1]['connect_timeout'] <= .05


@pytest.mark.skipif(os.name != 'posix', reason='local SIGTERM workers')
def test_cancel_aware_worker_gets_configured_grace_longer_than_one_second(tmp_path):
    # A process-local event would not reach the parent, so use an actual readiness file.
    started = tmp_path / 'started'; finished = tmp_path / 'finished'
    token = CancelToken()
    def worker(**kw):
        def stop(*_):
            time.sleep(1.15)
            finished.write_text('graceful')
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, stop)
        started.write_text('ready')
        time.sleep(10)
    def cancel_when_ready():
        end = time.monotonic() + 3
        while not started.exists() and time.monotonic() < end: time.sleep(.01)
        token.cancel('offline cancellation')
    trigger = threading.Thread(target=cancel_when_ready); trigger.start()
    call = CancelAwareCall(worker, cancel=token, grace_seconds=1.8)
    try:
        with pytest.raises(Cancelled): call(timeout=5)
        assert finished.read_text() == 'graceful'
        assert call.wait_closed(.1)
    finally: trigger.join(timeout=4)


@pytest.mark.skipif(os.name != 'posix', reason='local SIGTERM workers')
def test_services_cancel_grace_does_not_wait_for_longer_drain(tmp_path):
    from preparation_book import _mini_book, _request
    from aiforstudy.pipeline.engine import build_engine
    services = build_engine(_request('mini-book'), _mini_book(tmp_path), providers=Providers()).services
    services.control.policy.cancel_grace_seconds = .15
    services.control.policy.drain_seconds = 2
    ready = tmp_path / 'ready'
    script = "import signal,pathlib,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path(sys.argv[1]).touch(); time.sleep(10)"
    errors = []
    def run():
        try: services.run_subprocess([sys.executable, '-c', script, str(ready)], timeout=5)
        except BaseException as exc: errors.append(exc)
    worker = threading.Thread(target=run); worker.start()
    end = time.monotonic() + 3
    while not ready.exists() and time.monotonic() < end: time.sleep(.01)
    assert ready.exists()
    started = time.monotonic(); services.cancel.cancel('offline cancellation')
    worker.join(timeout=4)
    assert not worker.is_alive()
    assert time.monotonic() - started < 1
    assert len(errors) == 1 and isinstance(errors[0], Cancelled)


@pytest.mark.skipif(os.name != 'posix', reason='local SIGTERM workers')
def test_real_transport_cancel_grace_is_not_hardcoded_to_one_second(tmp_path, monkeypatch):
    ready = tmp_path / 'http-ready'
    script = "import signal,pathlib,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path(sys.argv[1]).touch(); time.sleep(10)"
    real_popen = subprocess.Popen
    workers = []
    def spawn(*args, **kw):
        process = real_popen([sys.executable, '-c', script, str(ready)], **kw)
        workers.append(process)
        return process
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    token = CancelToken()
    transport = RealTransport(model='offline', api_key='offline-placeholder', base_url='http://127.0.0.1',
                               cancel=token, cancel_grace_seconds=.15)
    errors = []
    def run():
        try: transport(model='offline', effort='off', messages=[], images=None, max_tokens=1, timeout=5)
        except BaseException as exc: errors.append(exc)
    worker = threading.Thread(target=run); worker.start()
    end = time.monotonic() + 3
    while not ready.exists() and time.monotonic() < end: time.sleep(.01)
    assert ready.exists()
    started = time.monotonic(); token.cancel('offline cancellation')
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert time.monotonic() - started < .8
    assert len(errors) == 1 and isinstance(errors[0], Cancelled)
    assert workers[0].poll() is not None and transport.last_remote_unconfirmed
