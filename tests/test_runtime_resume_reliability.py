"""Offline regressions for interrupted calls, migrated clocks, and CLI cancellation."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from aiforstudy.core.usage import UsageLedger, UsageRecord
from aiforstudy.errors import BudgetExceeded, Cancelled, ContractError
from aiforstudy.pipeline.budget import BudgetGuard, BudgetPolicy, BudgetScope
from aiforstudy.pipeline.runner import Runner, RunResult
from aiforstudy.pipeline.state import RunStore
from aiforstudy.providers.gateway import CallRequest, Gateway
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy, AttemptLimitExceeded


def policy():
    return BudgetPolicy(max_tokens=1_000_000, max_minutes=60, parallelism=1)












def make_gateway(tmp_path, guard, calls):
    guard.bind(tmp_path / "execution.json",run_id="r")
    def transport(**_):
        calls.append(1)
        return "bad json", {"total_tokens": 5}
    return Gateway(transport=transport, roles={"lesson": {"model": "offline", "max_tokens": 10}},
                   control=guard, run_id="r")


def test_transport_attempts_survive_resume_without_sharing_a_stage_bucket(tmp_path):
    calls = []
    first = ExecutionControl(ExecutionPolicy(parallelism=1))
    request = CallRequest(role="lesson", messages=[], unit_key="u", stage="lesson")
    assert not make_gateway(tmp_path, first, calls).call(request).ok
    assert len(calls) == 2
    restored = ExecutionControl(ExecutionPolicy(parallelism=1))
    gw = make_gateway(tmp_path, restored, calls)
    with pytest.raises(AttemptLimitExceeded, match="运输|传输|transport"):
        gw.call(request)
    assert len(calls) == 2
    # A different logical request in the same stage has its own transport cap.
    assert not gw.call(replace(request, messages=[{"role": "user", "content": "different"}])).ok
    assert len(calls) == 4




def test_interrupted_request_has_only_one_transport_attempt_left_on_resume(tmp_path):
    calls = []
    first = ExecutionControl(ExecutionPolicy(parallelism=1))
    request = CallRequest(role="lesson", messages=[], unit_key="u", stage="lesson")
    gw = make_gateway(tmp_path, first, calls)
    def interrupted(**_):
        calls.append(1)
        raise Cancelled("offline interrupted response")
    gw.transport = interrupted
    with pytest.raises(Cancelled):
        gw.call(request)
    fresh = ExecutionControl(ExecutionPolicy(parallelism=1))
    result = make_gateway(tmp_path, fresh, calls).call(request)
    assert not result.ok and result.attempts == 2
    assert len(calls) == 2
    assert [row["attempt"] for row in fresh.snapshot()["attempts"]] == [1, 2]


def test_concurrent_duplicate_requests_cannot_exceed_the_gateway_transport_cap(tmp_path):
    guard = ExecutionControl(ExecutionPolicy(parallelism=2))
    calls = []
    barrier = threading.Barrier(2)
    def transport(**_):
        calls.append(1)
        if len(calls) <= 2:
            barrier.wait(timeout=3)
        return "bad json", {"total_tokens": 5}
    gw = make_gateway(tmp_path, guard, calls)
    gw.transport = transport
    request = CallRequest(role="lesson", messages=[], unit_key="u", stage="lesson")
    def call():
        try:
            return gw.call(request)
        except AttemptLimitExceeded:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: call(), range(2)))
    assert len(calls) == 2
    assert len(guard.snapshot()["attempts"]) == 2




def test_scope_time_survives_monotonic_epoch_changes_and_overlap(tmp_path):
    old_now = [10000.0]
    first = BudgetGuard(policy(), clock=lambda: old_now[0])
    scope = BudgetScope("r", "u", "lesson", "figure")
    first.begin_active(scope)
    old_now[0] += 40
    first.heartbeat(scope)
    snapshot = first.snapshot()
    new_now = [10.0]
    restored = BudgetGuard(policy(), clock=lambda: new_now[0])
    restored.restore_from_snapshot("r", snapshot)
    assert restored.active_seconds(scope) == 45
    restored.begin_active(scope)
    new_now[0] += 30
    restored.end_active(scope)
    assert restored.active_seconds(scope) == 75
    # Another host can have an epoch overlapping the original range.
    third = BudgetGuard(policy(), clock=lambda: 10020.0)
    third.restore_from_snapshot("r", restored.snapshot())
    third.restore_from_snapshot("r", restored.snapshot())
    assert third.active_seconds(scope) == 75


@pytest.mark.skipif(os.name != "posix", reason="uses a real POSIX SIGINT")
def test_cli_sigint_cancels_before_thread_pool_shutdown(tmp_path):
    """The CLI must pass its signal token into the real engine and worker boundary."""
    root = Path(__file__).resolve().parents[1]
    script = r'''
import json, pathlib, sys, time
from aiforstudy import cli
from aiforstudy.pipeline import engine
from aiforstudy.pipeline.stubs import smoke_registry
from aiforstudy.pipeline.gateway_client import _gateway_client
from aiforstudy.pipeline.services import Providers
from aiforstudy.providers.transport import CancelAwareCall
from test_m2_engine import make_workspace
root = pathlib.Path(sys.argv[1])
workspace, book = make_workspace(root / "workspace")
registry = smoke_registry()
def slow(ctx):
    def block(**kwargs):
        (root / "started").write_text("ready")
        time.sleep(30)
        return '{"ok":true}', {"total_tokens": 1}
    ctx.services.providers.llm = CancelAwareCall(block, cancel=ctx.services.cancel)
    client, _ = _gateway_client(ctx, role="lesson", bucket="lesson")
    client.chat_json([])
registry.register("lesson", slow)
original = engine.run_request
def local_run(request, workspace, **kwargs):
    return original(request, workspace, stage_registry=registry, providers=Providers(), **kwargs)
engine.run_request = local_run
code = cli.main(["--workspace", str(workspace.root), "run", "--book", book.book_id,
                 "--units", "CH01-S01", "--profile", "smoke", "--until", "lesson", "--json"])
(root / "exit-code").write_text(str(code))
raise SystemExit(code)
'''
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root / "src"), str(root / "tests"),
                                                        str(root / "tests/integration")])}
    proc = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        deadline = time.monotonic() + 12
        while not (tmp_path / "started").exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(.03)
        assert (tmp_path / "started").exists(), proc.communicate(timeout=2)[0].decode()
        started = time.monotonic()
        proc.send_signal(signal.SIGINT)
        try:
            output = proc.communicate(timeout=5)[0].decode()
        except subprocess.TimeoutExpired:
            pytest.fail("SIGINT waited for a live transport instead of cancelling it")
        assert proc.returncode == 130, output
        assert time.monotonic() - started < 5
        states = list((tmp_path / "workspace").rglob("runs/*/state.json"))
        assert len(states) == 1
        import json
        state = json.loads(states[0].read_text())
        assert state["status"] == "cancelled"
        journal=json.loads((states[0].parent/"execution.json").read_text())
        assert len(journal["attempts"]) == 1
        assert journal["attempts"][0]["state"] == "cancelled"
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate(timeout=5)
