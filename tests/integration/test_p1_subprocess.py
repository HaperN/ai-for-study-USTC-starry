"""Cancellation and deadline tests exercise real local process trees."""
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from aiforstudy.errors import Cancelled
from aiforstudy.pipeline.engine import build_engine
from test_m2_engine import make_workspace, request_for


@pytest.mark.skipif(os.name != "posix", reason="process-group assertion uses /proc")
@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_stop_terminates_process_group_including_grandchild(tmp_path, stop):
    workspace, book = make_workspace(tmp_path)
    engine = build_engine(request_for(book.book_id), workspace)
    services = engine.services
    services.control.policy.drain_seconds = 0.1
    pid_file = tmp_path / "grandchild.pid"
    script = """
import pathlib,subprocess,sys,time
p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
pathlib.Path(sys.argv[1]).write_text(str(p.pid))
time.sleep(30)
"""
    trigger = None
    if stop == "cancel":
        def cancel_after_spawn():
            end = time.monotonic() + 3
            while not pid_file.exists() and time.monotonic() < end:
                time.sleep(0.01)
            services.cancel.cancel("test cancellation")
        trigger = threading.Thread(target=cancel_after_spawn)
        trigger.start()
        expected = Cancelled
    else:
        services.control.policy.api_deadline_seconds = 1.5
        expected = subprocess.TimeoutExpired
    started = time.monotonic()
    try:
        with pytest.raises(expected):
            services.run_subprocess([sys.executable, "-c", script, str(pid_file)], timeout=1.5)
    finally:
        if trigger:
            trigger.join(timeout=3)
        # Keep the fixture bounded even against the deliberately broken implementation.
        if pid_file.exists():
            pid = int(pid_file.read_text())
            status = Path(f"/proc/{pid}/stat")
            end = time.monotonic() + .5
            while True:
                try:
                    alive = status.read_text().split()[2] != "Z"
                except (FileNotFoundError, ProcessLookupError):
                    alive = False
                if not alive or time.monotonic() >= end: break
                time.sleep(.01)  # A signalled grandchild may not have been scheduled yet.
            if alive:
                try: os.kill(pid, 9)
                except ProcessLookupError: pass
    # ⚠️ 时间上界按**计划 §6.1 的收尾窗口**（`cancel_grace_seconds`，默认 10s）算，
    #    不是原稿那个更紧的 3s：3s 在机器有负载时（例如同时编译 Manim）会假红 ——
    #    它测的是"宿主多快能收尾"，而负载会让**正确实现**也变慢。
    #    「没有孤儿」才是这条测试的实质判据，下面那条断言仍然严格。
    elapsed = time.monotonic() - started
    assert elapsed < services.control.policy.cancel_grace_seconds, (
        f"收尾用了 {elapsed:.2f}s，超过 §6.1 的 {services.control.policy.cancel_grace_seconds}s 窗口")
    assert not alive, "grandchild escaped the timeout/cancellation process group"


# --------------------------------------------------------------------------- #
# T03 扩：慢子进程**故意忽略 SIGTERM**，终止必须升级到 SIGKILL 且在 10s 内收尾
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(os.name != "posix", reason="进程组与 SIGTERM 语义只在 posix 下成立")
def test_slow_subprocess_that_ignores_sigterm_is_killed_within_grace(tmp_path):
    workspace, book = make_workspace(tmp_path)
    engine = build_engine(request_for(book.book_id), workspace)
    services = engine.services
    services.control.policy.cancel_grace_seconds = 0.2
    pid_file = tmp_path / "stubborn.pid"
    script = (
        "import os,pathlib,signal,sys,time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"      # 故意忽略 TERM
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    errors: list[BaseException] = []

    def run() -> None:
        try:
            services.run_subprocess([sys.executable, "-c", script, str(pid_file)], timeout=30)
        except BaseException as exc:                            # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 5
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pid_file.exists(), "子进程没起来，测的不是取消"
    pid = int(pid_file.read_text())
    started = time.monotonic()
    services.cancel.cancel("测试取消顽固子进程")
    worker.join(timeout=services.control.policy.cancel_grace_seconds + 5)
    elapsed = time.monotonic() - started
    try:
        assert not worker.is_alive(), f"宿主 {elapsed:.2f}s 内没能收尾"
        assert elapsed < 10, f"收尾花了 {elapsed:.2f}s，超过 §6.1 的 10s 窗口"
        assert any(isinstance(exc, Cancelled) for exc in errors), (
            f"取消应当以 Cancelled 浮上来，实际 {errors!r}")
        status = Path(f"/proc/{pid}/stat")
        alive = status.exists() and status.read_text().split()[2] != "Z"
        assert not alive, "忽略 SIGTERM 的子进程必须被升级为 SIGKILL，不能留成孤儿"
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
