"""T00：**持久化离线守卫**自身的行为测试。

为什么需要这组测试，而不是散落在各测试里写 `monkeypatch.setattr(socket, ...)`：
现有若干集成测试已经各自装了"断网 tripwire"，但那是**每个文件自己记得装**。忘装一次，
真实的 `RealTransport`（它默认带着 `.secrets/deepseek.env` 里那把真钥匙）就会在
`pytest` 里发出真请求、花真钱，而且**看起来和通过一模一样**。计划 §6.1 与 T00 要求
把这件事变成**会话级默认**：只要在 tests 下跑，外网就是关着的。

本文件断言的是**效果**，不是实现细节：

* 公网 TCP（域名与裸 IP 两条路）被拒绝，且拒绝可归因（守卫记下目标）；
* 已配置真实 key 也不能绕开 —— `RealTransport` 带着哨兵密钥发请求时，
  一分钱都不花、一次调用都不记，且哨兵密钥没有离开进程；
* DeepSeek 账号桥接在启动前被拦截，普通测试子进程仍可启动；
* mock 传输、`RefusingTransport`、本地 HTTP 测试服务器照常可用。

本守卫只检查已知的账号桥接命令；其余子进程的网络隔离由 T07 的 bubblewrap 负责。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "tests" / "support"
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

#: 一个公网字面 IP：不经 DNS 就能连，用来把"守卫拦住了"与"解析失败"区分开。
PUBLIC_IP = "1.1.1.1"


def _guard():
    """取守卫模块。守卫不在时**显式失败**，不静默跳过。"""
    import offline_guard  # noqa: PLC0415 —— 故意放在函数内，让 collection 不因缺模块而中断
    return offline_guard


def test_guard_module_is_present_and_installed():
    guard = _guard()
    assert guard.is_installed(), "离线守卫必须处于已安装状态"
    assert issubclass(guard.OfflineNetworkBlocked, Exception)
    assert not issubclass(guard.OfflineNetworkBlocked, OSError), (
        "守卫异常**不能**是 OSError 子类：urllib 会把 OSError 包成 URLError，"
        "域代码再把 URLError 记成'网络超时'，于是'被守卫拦住'与'真的连不上'就分不清了")


def test_public_tcp_connect_by_literal_ip_is_blocked():
    guard = _guard()
    sock = socket.socket()
    try:
        sock.settimeout(5)
        with pytest.raises(guard.OfflineNetworkBlocked):
            sock.connect((PUBLIC_IP, 443))
    finally:
        sock.close()


def test_public_tcp_connect_by_hostname_is_blocked():
    guard = _guard()
    with pytest.raises(guard.OfflineNetworkBlocked):
        socket.create_connection(("pypi.org", 443), timeout=5)


def test_blocked_attempts_are_recorded_for_attribution():
    guard = _guard()
    guard.clear_events()
    with pytest.raises(guard.OfflineNetworkBlocked):
        socket.create_connection(("example.com", 80), timeout=5)
    targets = {event["target"] for event in guard.blocked_events()}
    assert any("example.com" in target for target in targets), (
        "被拦下的目标必须可归因，否则报告里只能写'网络失败'")


def test_urlopen_to_public_host_is_blocked():
    guard = _guard()
    import urllib.request  # noqa: PLC0415

    with pytest.raises(guard.OfflineNetworkBlocked):
        urllib.request.urlopen("https://pypi.org/simple/", timeout=5)


@pytest.mark.parametrize("name", ["dsh_bridge.cjs", "DeepSeek Harness"])
def test_account_bridge_is_blocked_before_a_process_starts(tmp_path, name):
    guard = _guard()
    guard.clear_events()
    executable = tmp_path / name
    executable.write_text('#!/bin/sh\nprintf started > "$1"\n')
    executable.chmod(0o755)
    marker = tmp_path / "started"
    cmd = [str(executable), str(marker)]
    if name == "dsh_bridge.cjs":
        cmd.insert(0, "/bin/sh")

    # The named file is a harmless local fixture even if the guard fails.
    with pytest.raises(guard.OfflineNetworkBlocked):
        subprocess.run(cmd, check=True, timeout=5)
    assert not marker.exists()
    assert any(event["target"] == name and event["via"] == "subprocess.Popen audit"
               for event in guard.blocked_events())


def test_guard_still_allows_ordinary_fixture_subprocesses():
    result = subprocess.run([sys.executable, "-c", "print('fixture')"],
                            check=True, capture_output=True, text=True, timeout=5)
    assert result.stdout.strip() == "fixture"


def test_configured_real_key_cannot_bypass_guard(monkeypatch):
    """**核心断言**：钥匙就位，网络照样是关的，而且一分钱没花。"""
    guard = _guard()
    guard.clear_events()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-sentinel-must-never-leave-the-process")
    monkeypatch.delenv("AIFORSTUDY_TESTS_ALLOW_NETWORK", raising=False)

    from aiforstudy.providers.transport import RealTransport  # noqa: PLC0415

    transport = RealTransport(model="deepseek-flash")

    with pytest.raises(Exception) as excinfo:
        transport(
            model="deepseek-flash",
            effort="off",
            messages=[{"role": "user", "content": "哨兵请求，绝不允许出网"}],
            images=None,
            max_tokens=16,
            timeout=5,
        )

    # 允许 urllib 包装过一次；但**必须**能在事件里看到被拦下的真实目标。
    blocked = {event["target"] for event in guard.blocked_events()}
    assert any("deepseek" in target for target in blocked), (
        f"真实传输必须撞在守卫上，实际异常={excinfo.value!r}，拦截事件={blocked}")
    assert transport.calls == 0, "被守卫拦下的请求不能记为一次调用"
    assert "spent_tokens" not in transport.report(), "生产传输不提供费用统计"
    assert "sk-sentinel" not in repr(excinfo.value), "异常里不许带出密钥内容"


def test_mock_transport_still_callable():
    """守卫不能把**替身**也一并封死：整套离线验收都靠替身跑。"""
    guard = _guard()
    assert guard.is_installed()

    calls: list[dict] = []

    def fake_transport(*, model, effort, messages, images, max_tokens, timeout):
        calls.append({"model": model, "images": images, "messages": messages})
        return json.dumps({"ok": True}), {"total_tokens": 7}

    text, usage = fake_transport(
        model="deepseek-flash", effort="off", messages=[{"role": "user", "content": "hi"}],
        images=None, max_tokens=16, timeout=5)
    assert json.loads(text) == {"ok": True}
    assert usage["total_tokens"] == 7
    assert len(calls) == 1


def test_refusing_transport_still_refuses():
    """缺回放时**阻断**，不回退真网络：这是方案 §4 的既有语义，守卫不许改它。"""
    _guard()
    from aiforstudy.errors import CapabilityError  # noqa: PLC0415
    from aiforstudy.providers.transport import RefusingTransport  # noqa: PLC0415

    with pytest.raises(CapabilityError):
        RefusingTransport()(model="x", effort="off", messages=[], images=None,
                            max_tokens=1, timeout=1)


class _EchoHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 —— http.server 的固定命名
        body = b"local-ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # 不让测试输出被访问日志淹没
        return


def test_localhost_test_server_is_still_reachable():
    """本机测试服务器必须照常可用，否则守卫会把离线验收本身也一起封死。"""
    _guard()
    import urllib.request  # noqa: PLC0415

    server = ThreadingHTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/ping"
        with urllib.request.urlopen(base, timeout=5) as response:
            assert response.status == 200
            assert response.read() == b"local-ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_unix_socket_pair_is_still_usable():
    """AF_UNIX 不是外网；误封它会让 multiprocessing 之类的本地设施挂掉。"""
    _guard()
    left, right = socket.socketpair()
    try:
        left.sendall(b"ping")
        assert right.recv(4) == b"ping"
    finally:
        left.close()
        right.close()


def test_guard_rejects_non_loopback_ipv6():
    guard = _guard()
    sock = socket.socket(socket.AF_INET6)
    try:
        sock.settimeout(5)
        with pytest.raises(guard.OfflineNetworkBlocked):
            sock.connect(("2606:4700:4700::1111", 443))
    finally:
        sock.close()


def test_install_is_idempotent_and_uninstall_restores():
    guard = _guard()
    before = socket.socket.connect
    guard.install()  # 再来一次不许叠加包装
    guard.install()
    assert socket.socket.connect is before
    guard.uninstall()
    try:
        assert not guard.is_installed()
        assert socket.socket.connect is not before
    finally:
        guard.install()
    assert guard.is_installed()
    assert os.environ.get("AIFORSTUDY_TESTS_ALLOW_NETWORK") is None
