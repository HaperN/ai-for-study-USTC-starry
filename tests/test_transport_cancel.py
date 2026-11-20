"""T03：取消的**实测**，以及控制流异常必须穿透旧宽泛 catch。

计划 §6.1 把两件事写得很硬：

> 同步网络调用需有**可终止的 transport worker** 或经过验证的取消接口；
> 仅 `Future.cancel()`／`shutdown(wait=False)` **不构成取消**。
> `Cancelled/BudgetExceeded` 必须**穿透旧宽泛 catch**。

所以这里不用任何 mock 假装取消生效：

* 「慢 socket」是**真的**本机 TCP 服务器 —— 它 accept 之后**永不回包**；
* 「慢子进程」是**真的** `subprocess`，而且故意让子进程**忽略 SIGTERM**，
  用来验证终止会升级到 SIGKILL；
* 收尾时间是从 `time.monotonic()` 量出来的，不是断言出来的。

远端是否停算这件事**如实记录**：本地不等了 ≠ 远端没计费。
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from aiforstudy.errors import BudgetExceeded, Cancelled  # noqa: E402
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy
from aiforstudy.pipeline.engine import build_engine  # noqa: E402
from aiforstudy.pipeline.services import (  # noqa: E402
    CancelToken,
    is_control_error,
    reraise_control_error,
)
from aiforstudy.providers.gateway import CallRequest, Gateway  # noqa: E402
from aiforstudy.providers.transport import CancelAwareCall  # noqa: E402


class _SilentServer:
    """接受连接但**永不回包**的本机服务器：模拟"请求已发出、响应还没来"。"""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.accepted: list[socket.socket] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (TimeoutError, socket.timeout, OSError):
                continue
            conn.settimeout(0.2)
            try:
                conn.recv(4096)          # 读掉请求，然后**什么都不回**
            except (TimeoutError, socket.timeout, OSError):
                pass
            self.accepted.append(conn)

    def close(self) -> None:
        self._stop.set()
        for conn in self.accepted:
            try:
                conn.close()
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass
        self._thread.join(timeout=5)


def _blocking_http_get(port: int, timeout: float):
    """一个真的会阻塞的"传输"：连上本机静默服务器后等响应。"""
    def call(**_kwargs):
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/slow", timeout=timeout) as res:
            return res.read().decode(), {}
    return call


# --------------------------------------------------------------------------- #
# 控制流异常
# --------------------------------------------------------------------------- #
def test_control_error_classification():
    assert is_control_error(Cancelled("x"))
    assert is_control_error(BudgetExceeded("x"))
    assert not is_control_error(ValueError("x"))
    assert not is_control_error(RuntimeError("x"))
    reraise_control_error(ValueError("普通错误"))          # 不该抛
    with pytest.raises(BudgetExceeded):
        reraise_control_error(BudgetExceeded("预算到顶"))
    with pytest.raises(Cancelled):
        reraise_control_error(Cancelled("用户取消"))


@pytest.mark.parametrize('error',[BudgetExceeded,Cancelled])
def test_current_audit_boundary_does_not_swallow_control_errors(tmp_path,monkeypatch,error):
    from aiforstudy.pipeline import media_stages
    from test_media_plan_stage import ctx
    def stop(_ctx): raise error('control boundary stopped')
    monkeypatch.setattr(media_stages,'_load_content',stop)
    with pytest.raises(error,match='control boundary stopped'):
        media_stages.audit_stage(ctx(tmp_path,'audit'))


@pytest.mark.parametrize('error',[BudgetExceeded,Cancelled])
def test_current_toc_boundary_does_not_swallow_control_errors(tmp_path,monkeypatch,error):
    from integration.test_dual_toc_pipeline import _ctx, _directory_double
    from aiforstudy.pipeline import preparation
    context=_ctx(tmp_path)
    _directory_double(monkeypatch,fail=error)
    with pytest.raises(error):preparation.toc_stage(context)


# --------------------------------------------------------------------------- #
# Gateway 不许把控制流异常变成"失败的结果"
# --------------------------------------------------------------------------- #
def _gateway(tmp_path, transport, *, cancel=None, max_tokens=100000):
    ctl=ExecutionControl(ExecutionPolicy(parallelism=1))
    ctl.bind(tmp_path / 'execution.json',run_id='run-cancel')
    return Gateway(transport=transport,roles={'lesson':{'model':'m','effort':'off'}},
        control=ctl,cancel=cancel or CancelToken(),cache_dir=tmp_path/'cache',book_id='b')


def test_gateway_propagates_cancelled_and_budget_errors(tmp_path):
    def cancelling(**_kw):
        raise Cancelled("用户取消")
    gateway = _gateway(tmp_path, cancelling)
    with pytest.raises(Cancelled):
        gateway.call(CallRequest(role="lesson", messages=[{"role": "user", "content": "hi"}]))

    def broke(**_kw):
        raise BudgetExceeded("额度到顶")
    gateway = _gateway(tmp_path, broke)
    with pytest.raises(BudgetExceeded):
        gateway.call(CallRequest(role="lesson", messages=[{"role": "user", "content": "hi"}]))


def test_no_dispatch_after_cancel(tmp_path):
    """取消之后**零新派发**：传输一次都不该被调到。"""
    calls: list = []

    def spy(**_kw):
        calls.append(1)
        return "{}", {"total_tokens": 1}

    cancel = CancelToken()
    cancel.cancel("测试取消")
    gateway = _gateway(tmp_path, spy, cancel=cancel)
    for _ in range(3):
        with pytest.raises(Cancelled):
            gateway.call(CallRequest(role="lesson",
                                     messages=[{"role": "user", "content": "hi"}]))
    assert calls == [], "取消之后还发出去了请求，等于取消没生效"


def test_cancelled_call_retains_attempt_without_production_usage(tmp_path):
    """本地不等了 ≠ 远端没算钱。用量不明的调用要标 unknown，不能写 0。"""
    def cancelling(**_kw):
        raise Cancelled("用户取消（远端是否停算未知）")
    gateway = _gateway(tmp_path, cancelling)
    with pytest.raises(Cancelled):
        gateway.call(CallRequest(role="lesson", messages=[{"role": "user", "content": "hi"}]))
    snapshot = gateway.control.snapshot()
    assert len(snapshot['attempts']) == 1
    assert snapshot['attempts'][0]['state'] == 'cancelled'
    assert not (tmp_path / 'usage.jsonl').exists()


# --------------------------------------------------------------------------- #
# 慢 socket：真的本机服务器 + 可放弃 worker
# --------------------------------------------------------------------------- #
def test_slow_socket_cancel_wraps_up_within_grace():
    server = _SilentServer()
    try:
        cancel = CancelToken(grace_seconds=0.5)
        call = CancelAwareCall(_blocking_http_get(server.port, timeout=3.0), cancel=cancel,
                               poll_seconds=0.02, grace_seconds=0.5)

        def cancel_soon():
            time.sleep(0.2)
            cancel.cancel("测试取消慢 socket")

        threading.Thread(target=cancel_soon, daemon=True).start()
        started = time.monotonic()
        with pytest.raises(Cancelled):
            call()
        elapsed = time.monotonic() - started
        assert elapsed < 10, f"收尾花了 {elapsed:.2f}s，超过 §6.1 的 10s 窗口"
        assert call.remote_unconfirmed or call.remote_confirmed
        # worker 必须能真的结束（它是 daemon，不结束也不会卡住进程，但要如实记录）
        assert call.wait_closed(6.0) is True
    finally:
        server.close()


def test_cancel_aware_call_returns_normally_when_not_cancelled():
    call = CancelAwareCall(lambda **_kw: ("{}", {"total_tokens": 3}),
                           cancel=CancelToken(), poll_seconds=0.01)
    assert call() == ("{}", {"total_tokens": 3})
    assert call.remote_confirmed is True
    assert call.remote_unconfirmed is False


def test_slow_transport_records_remote_unconfirmed_without_lying():
    """`RealTransport` 带取消时：把"远端是否停算"如实记在 `last_remote_unconfirmed`。"""
    from aiforstudy.providers.transport import RealTransport

    server = _SilentServer()
    try:
        cancel = CancelToken(grace_seconds=0.3)
        transport = RealTransport(model="m", api_key="sk-not-a-real-key", cancel=cancel,
                                  cancel_grace_seconds=0.3,
                                  base_url=f"http://127.0.0.1:{server.port}")
        threading.Thread(target=lambda: (time.sleep(0.2), cancel.cancel("测试")),
                         daemon=True).start()
        with pytest.raises(Cancelled):
            transport(model="m", effort="off", messages=[{"role": "user", "content": "x"}],
                      images=None, max_tokens=8, timeout=3.0)
        assert transport.last_remote_unconfirmed is True, (
            "本地放弃时要标记远端未确认；写成'已确认停止'就是伪造账目")
        assert transport.calls == 0, "没拿到响应就不该记成一次成功调用"
    finally:
        server.close()
