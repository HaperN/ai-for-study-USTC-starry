"""修改计划 N 节（2026-10-20）：断网等待恢复、不占额度。"""
import json

import pytest

from aiforstudy.errors import Cancelled
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionScope
from aiforstudy.pipeline.network_gate import (NETWORK_RETRY, NETWORK_WAIT, NetworkGate,
                                              NetworkPaused, REQUEST_FAILURE_LIMIT)
from aiforstudy.providers.gateway import CallRequest, Gateway


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _gateway(tmp_path, wire, probes, *, pause_after=7200.0):
    clock = FakeClock()
    control = ExecutionControl()
    control.bind(tmp_path / 'execution.json', run_id='run')
    calls = []

    def probe():
        calls.append(clock.now)
        return probes.pop(0) if probes else True
    control.network_gate = NetworkGate(probe, clock=clock, sleep=clock.sleep, pause_after=pause_after)
    gateway = Gateway(transport=wire, roles={'judge': {'model': 'offline', 'max_tokens': 1000}},
                      control=control)
    return gateway, control, clock, calls


def _states(control):
    return [a['state'] for a in control.snapshot()['attempts']]


def _counted(control, request):
    scope = ExecutionScope('run', request.unit_key, request.stage, request.resource_id)
    fingerprint = request.fingerprint(model='offline', effort='high', max_tokens=1000)
    return control.transport_attempts_used(scope, fingerprint)


def _ok(**_kwargs):
    return json.dumps({'ok': True}), {}


def test_outage_waits_for_probe_and_resends_without_spending_attempts(tmp_path):
    outcomes = [ConnectionError('SSLEOFError'), ConnectionError('IncompleteRead'), None]

    def wire(**kwargs):
        exc = outcomes.pop(0)
        if exc is not None:
            raise exc
        return _ok()
    # 每次失败后立即探测都失败 → 断网；等待中探测通过 → 原请求重发。
    gateway, control, clock, probes = _gateway(tmp_path, wire, [False, True, False, True])
    request = CallRequest(role='judge', messages=[{'role': 'user', 'content': 'x'}])
    result = gateway.call(request)
    assert result.ok and result.payload == {'ok': True}
    assert _states(control) == [NETWORK_WAIT, NETWORK_WAIT, 'response_received']
    assert _counted(control, request) == 1  # 只有成功那次计入
    assert not control.network_gate.outage
    assert clock.now >= 10.0  # 真的按退避等待过


def test_long_outage_pauses_run_and_keeps_records_resumable(tmp_path):
    def wire(**kwargs):
        raise ConnectionError('connect refused')
    gateway, control, clock, _ = _gateway(tmp_path, wire, [False] * 100, pause_after=600.0)
    request = CallRequest(role='judge', messages=[])
    with pytest.raises(NetworkPaused) as caught:
        gateway.call(request)
    assert isinstance(caught.value, Cancelled)
    assert 'network_paused' in str(caught.value)
    assert control.network_gate.paused
    assert _counted(control, request) == 0
    # 恢复：新的控制器读取同一份执行记录，网络状态不算运输次数。
    resumed = ExecutionControl()
    resumed.bind(tmp_path / 'execution.json', run_id='run', require_existing=True)
    assert _counted(resumed, request) == 0
    resumed.network_gate = NetworkGate(lambda: True, clock=clock, sleep=clock.sleep)
    gateway2 = Gateway(transport=_ok, roles={'judge': {'model': 'offline', 'max_tokens': 1000}},
                       control=resumed)
    assert gateway2.call(request).ok


def test_request_specific_failures_stop_after_three_when_network_is_healthy(tmp_path):
    sent = []

    def wire(**kwargs):
        sent.append(1)
        raise TimeoutError('read timeout')
    gateway, control, _, _ = _gateway(tmp_path, wire, [True] * 10)
    request = CallRequest(role='judge', messages=[])
    result = gateway.call(request)
    assert not result.ok and result.failure_kind == 'timeout'
    assert len(sent) == REQUEST_FAILURE_LIMIT
    assert _states(control) == [NETWORK_RETRY] * REQUEST_FAILURE_LIMIT
    assert _counted(control, request) == 0
    assert not control.network_gate.outage
    # 同一请求再次进入不再自动重发，也不能借 resume 清零。
    again = gateway.call(request)
    assert not again.ok and len(sent) == REQUEST_FAILURE_LIMIT


def test_rate_limit_backs_off_without_global_outage(tmp_path):
    outcomes = ['429', None]

    def wire(**kwargs):
        if outcomes.pop(0) == '429':
            exc = ConnectionError('模型服务 HTTP 429')
            exc.transport_details = {'phase': 'receive_headers', 'http_status': 429}
            raise exc
        return _ok()
    gateway, control, clock, probes = _gateway(tmp_path, wire, [])
    result = gateway.call(CallRequest(role='judge', messages=[]))
    assert result.ok
    assert probes == []  # 限流不探测、不暂停全局
    assert control.network_gate.outages == 0
    assert clock.now >= 60.0


def test_without_probe_gateway_keeps_bounded_transport_attempts(tmp_path):
    sent = []

    def wire(**kwargs):
        sent.append(1)
        raise ConnectionError('refused')
    control = ExecutionControl()
    control.bind(tmp_path / 'execution.json', run_id='run')
    control.network_gate = NetworkGate(None, sleep=lambda s: None)
    gateway = Gateway(transport=wire, roles={'judge': {'model': 'offline', 'max_tokens': 1000}},
                      control=control)
    gateway._wait_network_retry = lambda: None
    assert not gateway.call(CallRequest(role='judge', messages=[])).ok
    assert len(sent) == 2 and _states(control) == ['transport_failed', 'transport_failed']


def test_gate_shares_one_prober_between_waiting_threads():
    import threading
    clock = FakeClock()
    lock = threading.Lock()
    probes = []

    def probe():
        with lock:
            probes.append(1)
            return len(probes) >= 2
    gate = NetworkGate(probe, clock=clock, sleep=lambda s: (clock.sleep(s)))
    gate.enter_outage('test')
    errors = []

    def waiter():
        try:
            gate.wait_until_healthy()
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
    threads = [threading.Thread(target=waiter) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors and not gate.outage
    assert len(probes) == 2  # 只有一个线程负责探测


def test_correction_journal_resends_attempt_paused_by_network(tmp_path):
    from aiforstudy.blocks.content.generation_corrections import run_local_generation
    journal = tmp_path / 'journal.json'

    class Client:
        def __init__(self, outcomes):
            self.outcomes = outcomes

        def chat_json(self, messages, *, tag='', **kwargs):
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return {'data': outcome}
    messages = [{'role': 'user', 'content': 'x'}]
    with pytest.raises(NetworkPaused):
        run_local_generation(messages, Client([NetworkPaused('network_paused')]), lambda v: v,
                             tag='t', correction_tag='c', correction_journal=journal)
    saved = json.loads(journal.read_text())
    assert saved['attempts'][0]['status'] == 'network_paused'
    value = run_local_generation(messages, Client([{'ok': 1}]), lambda v: v,
                                 tag='t', correction_tag='c', correction_journal=journal)
    assert value == {'ok': 1}
    saved = json.loads(journal.read_text())
    assert len(saved['attempts']) == 1 and saved['attempts'][0]['status'] == 'accepted'


def test_real_transport_probe_reports_reachable_service(tmp_path):
    import http.server
    import threading
    from aiforstudy.providers.transport import RealTransport

    class Handler(http.server.BaseHTTPRequestHandler):
        status = 200

        def do_GET(self):
            assert self.path.endswith('/models')
            self.send_response(Handler.status)
            self.end_headers()
            self.wfile.write(b'{}')

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        wire = RealTransport(model='offline', api_key='fixture',
                             base_url=f'http://127.0.0.1:{server.server_address[1]}')
        assert wire.probe(timeout=2) is True
        Handler.status = 503
        assert wire.probe(timeout=2) is False
    finally:
        server.shutdown()
    assert wire.probe(timeout=1) is False  # 服务已关：连不上


class _FlakyNetworkTransport:
    """前 `fail_calls` 次调用断网；探测结果按 `probes` 依次给出（用完后为 healthy）。"""
    network_wait = True

    def __init__(self, inner, *, fail_calls, probes, healthy=True):
        self.inner, self.fail_calls, self.probes, self.healthy = inner, fail_calls, list(probes), healthy
        self.failed = 0

    def probe(self):
        return self.probes.pop(0) if self.probes else self.healthy

    def __call__(self, **kwargs):
        if self.failed < self.fail_calls:
            self.failed += 1
            raise ConnectionError('SSLEOFError (phase=connect)')
        return self.inner(**kwargs)


@pytest.fixture
def fast_network(monkeypatch):
    import aiforstudy.pipeline.network_gate as gate_module
    import aiforstudy.providers.gateway as gateway_module
    monkeypatch.setattr(gate_module, 'PROBE_SCHEDULE_SECONDS', (0.01,))
    monkeypatch.setattr(gateway_module, 'REQUEST_RETRY_DELAY_SECONDS', 0.01)
    return gate_module


def test_run_waits_through_outage_without_transport_failures(tmp_path, fast_network):
    from support.runner_v5 import TeachingTransport, run_book
    transport = _FlakyNetworkTransport(TeachingTransport(), fail_calls=2, probes=[False, True, False, True])
    result, store, engine, _ = run_book(tmp_path, transport=transport)
    assert result.status == 'succeeded', result.recovery_hint
    states = [a['state'] for a in json.loads((store.root / 'execution.json').read_text())['attempts']]
    assert states.count(NETWORK_WAIT) == 2 and 'transport_failed' not in states
    events = [e.data['network_event'] for e in store.events_after(0) if (e.data or {}).get('network_event')]
    assert {'outage_started', 'recovered'} <= set(events)


def test_run_pauses_after_long_outage_and_resumes_with_budgets_intact(tmp_path, fast_network, monkeypatch):
    from aiforstudy.pipeline.engine import resume_request
    from aiforstudy.pipeline.services import Providers
    from support.runner_v5 import TeachingTransport, run_book
    monkeypatch.setattr(fast_network, 'PAUSE_AFTER_SECONDS', 0.05)
    down = _FlakyNetworkTransport(TeachingTransport(), fail_calls=10**6, probes=[], healthy=False)
    result, store, engine, _ = run_book(tmp_path, transport=down)
    assert result.status == 'cancelled'
    assert '网络中断暂停' in result.recovery_hint
    status = json.loads(store.state_path.read_text())
    assert status.get('stop_reason') == 'network_paused'
    attempts = json.loads((store.root / 'execution.json').read_text())['attempts']
    assert attempts and all(a['state'] == NETWORK_WAIT for a in attempts)
    monkeypatch.setattr(fast_network, 'PAUSE_AFTER_SECONDS', 7200.0)
    up = _FlakyNetworkTransport(TeachingTransport(), fail_calls=0, probes=[])
    again, _, _ = resume_request(engine.request, store.run_id, engine.services.workspace,
                                 providers=Providers(llm=up), until='text_audit')
    assert again.status == 'succeeded', again.recovery_hint
