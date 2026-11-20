"""Explicit null means unlimited waiting, with durable attempts and cancellation."""
import json
import sys
import pytest

from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy, ExecutionScope


def test_unlimited_attempt_survives_clock_advance_and_restore(tmp_path):
    now = [0.0]
    policy = ExecutionPolicy(api_deadline_seconds=None)
    ctl = ExecutionControl(policy, clock=lambda: now[0])
    path = tmp_path / 'execution.json'
    ctl.bind(path, run_id='unlimited')
    lease = ctl.acquire_call(ExecutionScope('unlimited'), fingerprint='f')
    now[0] += 1_000_000
    assert lease.timeout_seconds() == 0
    assert ctl.run_timeout_seconds() is None
    lease.finish()
    assert json.loads(path.read_text())['policy']['api_deadline_seconds'] is None
    restored = ExecutionControl(policy)
    restored.bind(path, run_id='unlimited', require_existing=True)
    assert len(restored.snapshot()['attempts']) == 1


def test_gateway_passes_explicit_unlimited_to_transport(tmp_path):
    from aiforstudy.providers.gateway import Gateway, CallRequest
    from aiforstudy.pipeline.services import CancelToken
    ctl = ExecutionControl(ExecutionPolicy(api_deadline_seconds=None))
    ctl.bind(tmp_path / 'execution.json', run_id='unlimited')
    seen = []
    def transport(**kwargs):
        seen.append(kwargs['timeout'])
        return '{"ok":true}', {}
    gateway = Gateway(transport=transport, control=ctl, cancel=CancelToken(),
        roles={'lesson': {'model':'offline', 'effort':'high', 'max_tokens':100}},
        run_id='unlimited', book_id='book')
    assert gateway.call(CallRequest(role='lesson', messages=[{'role':'user','content':'test'}],
                                   stage='lesson', unit_key='U1')).ok
    assert len(seen) == 1 and 899 < seen[0] <= 900


def test_unlimited_subprocess_completes_and_remains_cancellable(tmp_path):
    from types import SimpleNamespace
    from aiforstudy.pipeline.services import Services, CancelToken
    from aiforstudy.errors import Cancelled
    import threading
    token = CancelToken()
    services = Services(workspace=SimpleNamespace(root=tmp_path), cancel=token,
        registry=None, context=None, capabilities=None,
        control=ExecutionControl(ExecutionPolicy(api_deadline_seconds=None)))
    result = services.run_subprocess([sys.executable, '-c', 'print("done")'])
    assert result.returncode == 0 and result.stdout.strip() == b'done'
    timer = threading.Timer(.2, lambda: token.cancel('test stop'))
    timer.start()
    try:
        with pytest.raises(Cancelled):
            services.run_subprocess([sys.executable, '-c', 'import time; time.sleep(60)'])
    finally:
        timer.join()
