"""Disposable child processes prove admission, cancellation and timeout behavior."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from aiforstudy.blocks.visual import worker


@pytest.fixture
def controlled_workers(monkeypatch, tmp_path):
    # Keep the real Popen/process-group monitor. Only substitute the sandboxed
    # workload, which waits for a host release file instead of importing Manim.
    (tmp_path / "run_scene.py").write_text(
        "import json,os,sys,time\nfrom pathlib import Path\n"
        "job=json.loads(Path(sys.argv[-1]).read_text())\n"
        "scratch=Path(job['scratch'])\n"
        "(scratch/'started').write_text(str(os.getpid()))\n"
        "while not (scratch/'release').exists(): time.sleep(.01)\n"
        "Path(job['result_path']).write_text(json.dumps({'status':'ok'}))\n")
    monkeypatch.setattr(worker, "select_isolation", lambda *a, **kw:
                        worker.WorkerIsolation("landlock-seccomp", "controlled fixture", True))
    monkeypatch.setattr(worker, "load_asset", lambda *a: SimpleNamespace(
        sandbox_paths=lambda **kw: {"read_only": [], "read_write": []},
        worker_env=lambda **kw: {"PATH": os.defpath}))
    monkeypatch.setattr(worker, "MONITOR_INTERVAL_SECONDS", .01)
    monkeypatch.setattr(worker, "TERM_GRACE_SECONDS", .2)
    previous_capacity = worker._WORKER_CAPACITY
    worker.configure_worker_concurrency(3)

    def run(index, *, cancel=None, timeout=5, parent_deadline=None):
        scratch = tmp_path / str(index) / "scratch"
        return worker.run_worker_job(
            services=worker.WorkerServices(tmp_path, sys.executable, tmp_path),
            job={"mode": "code", "scratch": str(scratch),
                 "output": str(tmp_path / str(index) / "out")},
            limits=worker.RenderLimits(timeout_seconds=timeout, parent_deadline=parent_deadline),
            job_id=str(index), cancel=cancel)

    yield run
    worker.configure_worker_concurrency(previous_capacity)


def _wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return bool(predicate())


def _release(tmp_path, *indices):
    for index in indices:
        scratch = tmp_path / str(index) / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "release").touch()


def test_three_workers_run_and_fourth_waits_for_a_shared_slot(controlled_workers, tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(controlled_workers, index) for index in range(4)]
        try:
            assert _wait_until(lambda: len(list(tmp_path.glob("*/scratch/started"))) == 3)
            time.sleep(.1)
            started = list(tmp_path.glob("*/scratch/started"))
            assert len(started) == 3, "the fourth worker must stay queued"
            _release(tmp_path, started[0].parent.parent.name)
            assert _wait_until(lambda: len(list(tmp_path.glob("*/scratch/started"))) == 4)
        finally:
            _release(tmp_path, *range(4))
        outcomes = [future.result() for future in futures]
    assert all(item["status"] == "ok" for item in outcomes)
    assert max(item["concurrency_observed"] for item in outcomes) == 3
    assert len({item["worker_scratch"] for item in outcomes}) == 4


def test_cancellation_stops_three_running_workers_and_does_not_launch_fourth(controlled_workers, tmp_path):
    cancelled = threading.Event()
    class Token:
        @property
        def cancelled(self):
            return cancelled.is_set()
    token = Token()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(controlled_workers, index, cancel=token) for index in range(4)]
        try:
            assert _wait_until(lambda: len(list(tmp_path.glob("*/scratch/started"))) == 3)
        finally:
            cancelled.set()
        outcomes = [future.result(timeout=3) for future in futures]
    assert len(list(tmp_path.glob("*/scratch/started"))) == 3
    assert all(item["status"] == "cancelled" for item in outcomes)
    assert all(item.get("orphans_remaining", 0) == 0 for item in outcomes)
    assert all(not worker._group_alive(int(p.read_text())) for p in tmp_path.glob("*/scratch/started"))
    _release(tmp_path, 4)
    assert controlled_workers(4)["status"] == "ok", "cancelled jobs must release their slots"


def test_queue_time_does_not_spend_the_render_timeout(controlled_workers, tmp_path):
    worker.configure_worker_concurrency(1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(controlled_workers, 0)
        try:
            assert _wait_until(lambda: (tmp_path / "0/scratch/started").exists())
            _release(tmp_path, 1)
            second = pool.submit(controlled_workers, 1, timeout=.25)
            time.sleep(.4)
            assert not (tmp_path / "1/scratch/started").exists()
        finally:
            _release(tmp_path, 0, 1)
        assert first.result()["status"] == "ok"
        assert second.result()["status"] == "ok"


def test_queued_parent_deadline_expires_without_starting_process(controlled_workers, tmp_path):
    worker.configure_worker_concurrency(1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(controlled_workers, 0)
        try:
            assert _wait_until(lambda: (tmp_path / "0/scratch/started").exists())
            second = pool.submit(controlled_workers, 1, parent_deadline=time.monotonic() + .15)
            outcome = second.result(timeout=1)
            assert outcome["terminated_by"] == "deadline"
            assert not (tmp_path / "1/scratch/started").exists()
        finally:
            _release(tmp_path, 0, 1)
        assert first.result()["status"] == "ok"


def test_lowering_worker_limit_does_not_interrupt_active_processes(controlled_workers, tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(controlled_workers, index) for index in range(3)]
        try:
            assert _wait_until(lambda: len(list(tmp_path.glob("*/scratch/started"))) == 3)
            worker.configure_worker_concurrency(1)
            futures.append(pool.submit(controlled_workers, 3))
            _release(tmp_path, 0, 1)
            assert futures[0].result()["status"] == "ok"
            assert futures[1].result()["status"] == "ok"
            time.sleep(.1)
            assert not (tmp_path / "3/scratch/started").exists()
            assert not futures[2].done(), "changing capacity must not kill active workers"
            _release(tmp_path, 2)
            assert _wait_until(lambda: (tmp_path / "3/scratch/started").exists())
        finally:
            _release(tmp_path, *range(4))
        assert all(future.result()["status"] == "ok" for future in futures)


def test_parent_deadline_is_not_charged_twice_during_execution(monkeypatch):
    monkeypatch.setattr(worker.time, "monotonic", lambda: 104)
    limits = worker.RenderLimits(timeout_seconds=10, parent_deadline=105)
    assert worker.monitor_decision(elapsed=4, rss=0, output=0, cancelled=False, limits=limits) == ""


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_worker_capacity_is_rejected(limit):
    with pytest.raises(worker.ConfigError, match="max_concurrent_workers"):
        worker.configure_worker_concurrency(limit)
