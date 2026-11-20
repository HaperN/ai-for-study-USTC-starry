"""**持久化离线守卫**：只要在 `tests/` 下跑，外网就是关着的。

## 为什么是"持久化"而不是"每个测试自己记得装"

改造前的现状是：若干集成测试各自写了一段 `monkeypatch.setattr(socket, "socket", tripwire)`
（见 `tests/integration/test_m10_lesson_audit_shells.py` 等）。那种写法**只保护记得装它的那个
文件**。而 `aiforstudy.providers.transport.RealTransport` 带着 `.secrets/deepseek.env` 里的
真钥匙、只要给个正数额度就会真的发请求——于是"某个新测试忘了装 tripwire"这一个疏忽，
在报告里长得和"全部通过"一模一样，代价是真钱。

本模块把那件事变成**会话级默认**：`tests/conftest.py` 在导入时装上它，
文档里的测试命令再叠一层 `-p offline_guard`（重复装是幂等的）。

## 拦什么、放什么

| | 行为 |
|---|---|
| 公网 TCP（域名或字面 IP，IPv4/IPv6） | 拒绝，抛 `OfflineNetworkBlocked` |
| 非本机 DNS 解析 | 拒绝（防 DNS 泄露，也避免"解析失败"被误读成"守卫生效"） |
| DeepSeek 账号应用或 `dsh_bridge.cjs` 子进程 | 拒绝启动，保留普通测试子进程 |
| 回环地址、`localhost`、AF_UNIX、`socketpair` | **放行**（本机测试服务器与 multiprocessing 都靠它） |
| 替身传输、`RefusingTransport` | 不受影响（整套离线验收靠替身跑） |

`OfflineNetworkBlocked` 故意**继承 `RuntimeError` 而不是 `OSError`**：`urllib` 会把 `OSError`
包成 `URLError`，域代码再把 `URLError` 记成"网络超时"；那样"被守卫拦住"与"真的连不上"
就再也分不清了。不继承 `OSError` 能让它穿过 `except OSError:` 直接浮上来。

## 已知边界（不许写成"已经隔离"）

* 本守卫在**本进程内**拦 socket／urllib，并在启动前拒绝已知的 DeepSeek 账号桥。
  其余子进程的网络仍由 T07 的 bubblewrap 隔离。
* 显式放开的口子只有一个环境变量 `AIFORSTUDY_TESTS_ALLOW_NETWORK=1`，用于极少数
  必须联网的环境步骤（如 T11 的 wheel 安装）。它一旦被设置，`is_installed()` 返回 False，
  `tests/test_offline_guard.py` 会**明确失败**——不允许"静默开着网跑绿"。
"""
from __future__ import annotations

import ipaddress
import os
import socket
import sys
import traceback
import urllib.parse
import urllib.request
from typing import Any

#: 显式放开的口子。**不要**在测试或生产代码里设置它。
ALLOW_NETWORK_ENV = "AIFORSTUDY_TESTS_ALLOW_NETWORK"

#: 允许解析／连接的主机名白名单。只放本机名字。
_LOCAL_NAMES = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
    "127.0.0.1", "::1", "0.0.0.0", "::",
})


class OfflineNetworkBlocked(RuntimeError):
    """测试会话里尝试真实外网。**不是** OSError 子类，见模块文档。"""

    def __init__(self, target: str, via: str) -> None:
        super().__init__(
            f"离线守卫拦截了对 {target} 的真实外网访问（入口：{via}）。"
            f"测试默认禁止外网：真实模型调用请用替身／回放，"
            f"本机测试服务器请用 127.0.0.1。"
            f"若这确实是必须联网的环境步骤，请在**单独记录**的命令里显式设置 "
            f"{ALLOW_NETWORK_ENV}=1。")
        self.target = target
        self.via = via


_state: dict[str, Any] = {
    "installed": False,
    "originals": {},
    "events": [],
    "disarmed": None,
}


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #
def _is_local_host(host: Any) -> bool:
    """`host` 是不是本机（回环／未指定／本机主机名）。"""
    if host is None:
        return True
    if isinstance(host, (bytes, bytearray)):
        try:
            host = bytes(host).decode("ascii", "replace")
        except Exception:  # pragma: no cover - 极端编码
            return False
    if not isinstance(host, str):
        return False
    candidate = host.strip().strip("[]")
    if not candidate:
        return True
    if candidate.lower() in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        pass
    # 本机主机名（http.server 的 server_bind 会查它）也算本机
    try:
        return candidate == socket.gethostname()
    except Exception:  # pragma: no cover
        return False


def _target_of(address: Any) -> tuple[str, bool]:
    """把各种 `connect` 地址形式归一成 `(显示名, 是否本机)`。"""
    if isinstance(address, (str, bytes, bytearray)):
        # AF_UNIX 路径：不是外网
        return (repr(address), True)
    if isinstance(address, (tuple, list)) and address:
        host = address[0]
        port = address[1] if len(address) > 1 else ""
        return (f"{host}:{port}", _is_local_host(host))
    return (repr(address), False)


def _record(target: str, via: str) -> None:
    stack = [f"{f.name}:{f.lineno}" for f in traceback.extract_stack()[-6:-1]]
    _state["events"].append({"target": target, "via": via, "stack": stack})


def _block(target: str, via: str) -> None:
    _record(target, via)
    raise OfflineNetworkBlocked(target, via)


# --------------------------------------------------------------------------- #
# 包装
# --------------------------------------------------------------------------- #
def _wrap_socket_methods(originals: dict[str, Any]) -> None:
    real_connect = originals["socket.connect"]
    real_connect_ex = originals["socket.connect_ex"]

    def guarded_connect(self, address):  # type: ignore[no-untyped-def]
        target, local = _target_of(address)
        if not local:
            _block(target, "socket.socket.connect")
        return real_connect(self, address)

    def guarded_connect_ex(self, address):  # type: ignore[no-untyped-def]
        target, local = _target_of(address)
        if not local:
            _block(target, "socket.socket.connect_ex")
        return real_connect_ex(self, address)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]


def _wrap_resolvers(originals: dict[str, Any]) -> None:
    real_getaddrinfo = originals["socket.getaddrinfo"]

    def guarded_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        if not _is_local_host(host):
            _block(f"{host}:{port}", "socket.getaddrinfo")
        return real_getaddrinfo(host, port, *args, **kwargs)

    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]

    real_gethostbyname = originals["socket.gethostbyname"]

    def guarded_gethostbyname(host):  # type: ignore[no-untyped-def]
        if not _is_local_host(host):
            _block(str(host), "socket.gethostbyname")
        return real_gethostbyname(host)

    socket.gethostbyname = guarded_gethostbyname  # type: ignore[assignment]


def _wrap_urlopen(originals: dict[str, Any]) -> None:
    real_urlopen = originals["urllib.request.urlopen"]

    def guarded_urlopen(url, *args, **kwargs):  # type: ignore[no-untyped-def]
        target = url if isinstance(url, str) else getattr(url, "full_url", str(url))
        parsed = urllib.parse.urlparse(target)
        scheme = (parsed.scheme or "").lower()
        if scheme in {"http", "https", "ftp", "ftps", "ws", "wss"}:
            if not _is_local_host(parsed.hostname):
                _block(parsed.netloc or target, "urllib.request.urlopen")
        return real_urlopen(url, *args, **kwargs)

    urllib.request.urlopen = guarded_urlopen  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# 生命周期
# --------------------------------------------------------------------------- #
def _audit_request(event, args):
    if not _state["installed"]:
        return
    if event == "urllib.Request":
        host = urllib.parse.urlsplit(str(args[0])).hostname
        if not _is_local_host(host):
            _block(str(args[0]), "urllib.Request audit")
    elif event == "subprocess.Popen":
        executable, command, _cwd, _env = args
        operands = (command,) if isinstance(command, (str, bytes)) else command
        for operand in (executable, *operands):
            if isinstance(operand, (str, bytes, os.PathLike)):
                name = os.path.basename(os.fsdecode(operand))
                if name in {"DeepSeek Harness", "dsh_bridge.cjs"}:
                    _block(name, "subprocess.Popen audit")


sys.addaudithook(_audit_request)


def install() -> None:
    """装上守卫。重复调用是幂等的。"""
    if os.environ.get(ALLOW_NETWORK_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        if _state["installed"]:
            uninstall()
        _state["disarmed"] = ALLOW_NETWORK_ENV
        return
    if _state["installed"]:
        return
    _state["originals"] = {
        "socket.connect": socket.socket.connect,
        "socket.connect_ex": socket.socket.connect_ex,
        "socket.getaddrinfo": socket.getaddrinfo,
        "socket.gethostbyname": socket.gethostbyname,
        "urllib.request.urlopen": urllib.request.urlopen,
    }
    _wrap_socket_methods(_state["originals"])
    _wrap_resolvers(_state["originals"])
    _wrap_urlopen(_state["originals"])
    _state["installed"] = True
    _state["disarmed"] = None


def uninstall() -> None:
    """卸下守卫，恢复原始函数。"""
    if not _state["installed"]:
        return
    originals = _state["originals"]
    socket.socket.connect = originals["socket.connect"]  # type: ignore[method-assign]
    socket.socket.connect_ex = originals["socket.connect_ex"]  # type: ignore[method-assign]
    socket.getaddrinfo = originals["socket.getaddrinfo"]  # type: ignore[assignment]
    socket.gethostbyname = originals["socket.gethostbyname"]  # type: ignore[assignment]
    urllib.request.urlopen = originals["urllib.request.urlopen"]  # type: ignore[assignment]
    _state["installed"] = False
    _state["originals"] = {}


def is_installed() -> bool:
    """守卫此刻是否真的在拦。被环境变量放开时返回 False（**不许**静默算通过）。"""
    return bool(_state["installed"])


def disarmed_reason() -> str | None:
    return _state["disarmed"]


def blocked_events() -> list[dict[str, Any]]:
    return list(_state["events"])


def clear_events() -> None:
    _state["events"] = []


# --------------------------------------------------------------------------- #
# pytest 插件钩子（`-p offline_guard` 时生效）
# --------------------------------------------------------------------------- #
def pytest_configure(config) -> None:  # type: ignore[no-untyped-def]
    install()


def pytest_unconfigure(config) -> None:  # type: ignore[no-untyped-def]
    uninstall()


def pytest_report_header(config) -> str:  # type: ignore[no-untyped-def]
    if is_installed():
        return "offline-guard: 已启用（真实外网与账号桥默认拒绝；回环与 AF_UNIX 放行）"
    return (f"offline-guard: **未启用**（{disarmed_reason() or '未知原因'}）；"
            f"本次运行的外网不受保护")


__all__ = ["ALLOW_NETWORK_ENV", "OfflineNetworkBlocked", "install", "uninstall",
           "is_installed", "disarmed_reason", "blocked_events", "clear_events"]
