"""Single-call HTTP boundary. Configuration constructs no network request."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import BudgetExceeded, Cancelled, ConfigError
from ..core.process_io import communicate_bounded

#: 默认从环境变量取；**绝不落进配置文件**（方案 §5：配置只保留角色与密钥引用）
API_KEY_ENV = "DEEPSEEK_API_KEY"
#: 旧实现已经在用的密钥文件（相对工作区）。新传输**必须认同一个来源**，
#: 否则会出现"密钥就在那儿、新引擎说没有"这种最难查的分裂。
SECRETS_REL = Path(".secrets") / "deepseek.env"


def resolve_api_key(workspace_root: Path | None = None) -> tuple[str, str]:
    """按 **环境变量 → `.secrets/deepseek.env`** 取密钥，返回 `(密钥, 引用名)`。

    顺序与旧 `lib/model.py: load_api_key` **完全一致** —— 两条路必须认同一份来源。
    第一版只读环境变量，于是"密钥在 `.secrets` 里、新引擎报缺密钥"。
    返回的引用名只用于**记账与报告**（`env:DEEPSEEK_API_KEY` /
    `file:.secrets/deepseek.env#DEEPSEEK_API_KEY`），**永远不含密钥内容**。
    """
    env = os.environ.get(API_KEY_ENV, "").strip()
    if env:
        return env, f"env:{API_KEY_ENV}"
    if workspace_root is not None:
        path = Path(workspace_root) / SECRETS_REL
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                if name.strip() == API_KEY_ENV and value.strip():
                    return value.strip(), f"file:{SECRETS_REL.as_posix()}#{API_KEY_ENV}"
    return "", ""
BASE_URL_ENV = "AIFORSTUDY_MODEL_BASE_URL"
DEFAULT_BASE_URL = "https://api.deepseek.com"


def reasoning_options(effort: str) -> dict:
    """One wire contract for production and standalone compatibility clients."""
    aliases = {"none": "none", "off": "none", "no": "none", "0": "none",
               "minimal": "low", "low": "low", "medium": "high",
               "high": "high", "xhigh": "high", "max": "max", "ultra": "max"}
    normalized = aliases.get(effort.strip().lower()) if isinstance(effort, str) else None
    if normalized is None:
        raise ConfigError(f"不支持的模型 effort：{effort!r}；需要 none/low/high/max 或兼容别名")
    return {"thinking": {"type": "disabled" if normalized == "none" else "enabled"},
            "reasoning_effort": normalized}


@dataclass(frozen=True)
class ImagePayload:
    """一张**真实发出**的图片：路径、内容哈希、字节数、MIME。

    为什么单独成一个值对象（T08）："评审看到了图"必须是**可复核的数据**，而不是
    调用方的一句保证。`sha256` 对着**读到的字节**算，所以事后能把
    "送审的就是盘上那一版"与关键帧资源逐条对上。
    """
    path: str
    sha256: str
    bytes: int
    mime: str
    data_url: str = field(repr=False, default="")

    def as_dict(self) -> dict:
        return {"path": self.path, "sha256": self.sha256, "bytes": self.bytes,
                "mime": self.mime}


def image_payloads(images: list[str] | tuple[str, ...]) -> tuple[ImagePayload, ...]:
    """把图片文件读成 `ImagePayload`：**真实 bytes + sha256 + MIME**。

    缺图是**明确错误**：静默丢图会让审核看到缺图的产物却以为完整。data URL 与
    旧客户端（`lib/model.py`）同一套格式，免得两条路的请求体不一样、行为却看着相同。
    """
    import base64
    import hashlib
    import mimetypes

    out: list[ImagePayload] = []
    for raw in images:
        candidate = Path(raw)
        if not candidate.is_file():
            raise ConfigError(f"图片不存在：{candidate}")
        data = candidate.read_bytes()
        mime = mimetypes.guess_type(candidate.name)[0] or "image/png"
        encoded = base64.b64encode(data).decode("ascii")
        out.append(ImagePayload(path=str(candidate),
                                sha256="sha256:" + hashlib.sha256(data).hexdigest(),
                                bytes=len(data), mime=mime,
                                data_url=f"data:{mime};base64,{encoded}"))
    return tuple(out)


def _messages_with_images(messages: list[dict], images: list[str],
                          max_tokens: int) -> list[dict]:
    """把图片按 **data URL** 附到最后一条 `user` 消息上。

    调用方可在 user 消息放 `image_labels: list[str]`，与本次 images 一一对应。
    最近一组标签转成每张图前的独立 text part；协议纠正追加 user 消息后仍沿用
    原标签。该字段只作内部元数据，始终从 HTTP 消息移除。未提供时保留旧格式。
    """
    labelled = next((message for message in reversed(messages)
                     if message.get('role') == 'user' and 'image_labels' in message), None)
    labels = None
    if labelled is not None:
        labels = labelled['image_labels']
        if (not isinstance(labels, list) or len(labels) != len(images)
                or any(not isinstance(label, str) or not label.strip() for label in labels)):
            raise ConfigError('image_labels 必须是与 images 一一对应的非空字符串列表')
    out = [{key: value for key, value in message.items() if key != 'image_labels'}
           for message in messages]
    if not images:
        return out
    payloads = image_payloads(images)
    parts: list[dict] = []
    for index, payload in enumerate(payloads):
        if labels is not None:
            parts.append({'type': 'text', 'text': labels[index]})
        parts.append({'type': 'image_url', 'image_url': {'url': payload.data_url}})
    for message in reversed(out):
        if message.get("role") == "user":
            text = message.get("content")
            if isinstance(text, str):
                message["content"] = [{"type": "text", "text": text}, *parts]
            elif isinstance(text, list):
                message["content"] = [*text, *parts]
            else:
                message["content"] = [*parts]
            break
    else:
        raise ConfigError('图片请求必须包含 user 消息；不能静默丢弃图片')
    return out


class CancelAwareCall:
    """Run blocking I/O in a reaped process; cancellation never leaves a thread.

    The local process can be terminated, but remote billing remains unknown.
    Linux fork preserves injected synchronous transports without serializing keys
    or requests to a temporary file. Production counters are settled by the parent.
    """

    def __init__(self, fn, *, cancel=None, poll_seconds: float = 0.05,
                 grace_seconds: float = 10.0) -> None:
        self._fn = fn
        self._cancel = cancel
        self._poll = max(0.01, float(poll_seconds))
        self._grace = max(0.0, float(grace_seconds))
        self._done = threading.Event()
        self.remote_confirmed = False
        self.remote_unconfirmed = False
        self.thread = None  # Compatibility: exposes is_alive/join for old callers.

    def wait_closed(self, timeout: float) -> bool:
        if self.thread is not None:
            self.thread.join(timeout)
            return not self.thread.is_alive()
        return self._done.is_set()

    def __call__(self, **kwargs):
        import multiprocessing
        from ..pipeline.execution_control import remaining_cancel_grace
        if self._cancel is not None:
            self._cancel.checkpoint()
        if "fork" not in multiprocessing.get_all_start_methods():
            raise ConfigError("可终止同步 transport 当前需要 Linux fork worker")
        ctx = multiprocessing.get_context("fork")
        receive, send = ctx.Pipe(duplex=False)

        def run():
            receive.close()
            try:
                send.send((True, self._fn(**kwargs)))
            except BaseException as exc:
                send.send((False, exc))
            finally:
                send.close()

        self.thread = ctx.Process(target=run, name="cancel-aware-call", daemon=True)
        self.thread.start()
        send.close()
        timeout = kwargs.get("timeout", 1000)
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        try:
            while True:
                if self._cancel is not None and self._cancel.cancelled:
                    self.remote_unconfirmed = True
                    raise Cancelled(f"已取消（{self._cancel.reason}）；本地 worker 终止，远端完成状态未确认")
                if deadline is not None and time.monotonic() >= deadline:
                    self.remote_unconfirmed = True
                    raise TimeoutError("同步传输超过绝对 deadline；远端完成状态未确认")
                wait = self._poll if deadline is None else min(self._poll, max(0, deadline - time.monotonic()))
                if receive.poll(wait):
                    try:
                        ok, value = receive.recv()
                    except EOFError as exc:
                        self.remote_unconfirmed = True
                        raise ConnectionError("transport worker exited without a response") from exc
                    if not ok:
                        raise value
                    self.remote_confirmed = True
                    return value
        finally:
            receive.close()
            if self.thread.is_alive():
                self.thread.terminate()
                self.thread.join(remaining_cancel_grace(self._grace, self._cancel))
            if self.thread.is_alive():
                self.thread.kill()
            self.thread.join(timeout=1.0)
            if self.thread.is_alive():
                raise RuntimeError("transport worker failed to terminate")
            self._done.set()


@dataclass
class RealTransport:
    """单次 HTTP 传输；构造只解析配置与密钥，不连接网络。"""
    model: str
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    workspace_root: Path | None = None
    timeout: float | None = 1000.0
    calls: int = 0
    image_calls: int = 0
    #: 可选取消信号。生产 HTTP 始终放入可终止的 http_worker。
    cancel: object | None = None
    #: 取消后的本地收尾窗口，默认取 §6.1 的 10 秒
    cancel_grace_seconds: float = 10.0
    connect_timeout_seconds: float = 15.0
    last_remote_unconfirmed: bool = False
    #: 上一次请求**实际发出**的图片（path／sha256／bytes／mime）。证据，不是声明。
    last_image_payloads: tuple[ImagePayload, ...] = ()
    #: 断网时等待网络恢复（修改计划 N 节）；只由生产工厂打开。
    network_wait: bool = False
    _key_ref: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key, self._key_ref = resolve_api_key(self.workspace_root)
        self.base_url = os.environ.get(BASE_URL_ENV, self.base_url)
        if not self.api_key:
            raise ConfigError(
                f"缺 API 密钥：环境变量 {API_KEY_ENV} 未设置，"
                f"{SECRETS_REL.as_posix()} 里也没有（或工作区没给）。"
                f"方案 §5：配置只保留角色与密钥引用，密钥从环境或密钥文件读、"
                f"不写日志、不回传浏览器。")

    @property
    def cache_identity(self):
        from .local_compatible import endpoint_identity
        return {'provider':'deepseek', 'endpoint':endpoint_identity(self.base_url), 'version':1}

    def request_body(self, *, messages: list[dict], images: list[str] | None = None,
                     max_tokens: int = 1024, effort: str = "high",
                     model: str = "") -> dict:
        """构造**真实请求体**但**不发送**（能力预检与证据复核用，0 次调用、0 token）。

        它和 `_attempt` 走同一段 `_messages_with_images` —— 所以"这里能看到图片 bytes"
        与"真的会发出去图片"是同一件事，不是两套实现。图片的 sha256／bytes 同时记进
        `last_image_payloads`，供报告与测试逐条核对。
        """
        options = reasoning_options(effort)
        payloads = image_payloads(list(images or ()))
        self.last_image_payloads = payloads
        return {
            "model": model or self.model,
            "messages": _messages_with_images(messages, list(images or ()),
                                              max(0, int(max_tokens or 0))),
            "max_tokens": max(1, int(max_tokens or 1024)),
            "response_format": {"type": "json_object"},
            **options,
        }

    def __call__(self, *, model: str, effort: str, messages: list[dict],
                 images: list[str] | None, max_tokens: int,
                 timeout: float | None) -> tuple[str, dict]:
        import subprocess
        import sys
        from ..pipeline.execution_control import remaining_cancel_grace
        from urllib.parse import urlsplit
        if self.cancel is not None:
            self.cancel.checkpoint()
        url = self.base_url.rstrip("/") + "/chat/completions"
        # Emit before starting the worker: host egress policy can veto a request
        # without sending credentials or relying on child-process monkeypatches.
        sys.audit("urllib.Request", url, None, {}, "POST")
        if urlsplit(url).scheme not in ("https", "http"):
            raise ConfigError("模型 endpoint 必须为 HTTP(S)")
        body = self.request_body(model=model, effort=effort, messages=messages,
                                 images=images, max_tokens=max_tokens)
        # An explicit None removes the response deadline, including the instance
        # default cap. Connection establishment and cancellation remain bounded.
        limit = (None if timeout is None else float(timeout) if self.timeout is None
                 else min(float(timeout), self.timeout))
        if limit is not None and limit <= 0:
            raise TimeoutError("调用 deadline 已耗尽")
        connect_timeout = float(self.connect_timeout_seconds)
        if limit is not None:
            connect_timeout = min(connect_timeout, limit)
        payload = json.dumps({"url": url, "key": self.api_key, "body": body,
                              "connect_timeout": connect_timeout,
                              "read_timeout":limit}).encode()
        deadline = None if limit is None else time.monotonic() + limit
        self.last_remote_unconfirmed = False
        process = subprocess.Popen([sys.executable, "-m", "aiforstudy.providers.http_worker"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
        try:
            output, _ = communicate_bounded(process, payload, deadline=deadline,
                checkpoint=self.cancel.checkpoint if self.cancel is not None else None)
            if process.returncode:
                raise ConnectionError("模型传输 worker 异常退出；远端完成状态未确认")
            result = json.loads(output)
            if result.get("error"):
                from ..errors import ContractError
                error = {'ConfigError':ConfigError, 'TimeoutError':TimeoutError,
                         'ConnectionError':ConnectionError, 'ContractError':ContractError}.get(
                             result['error'], ConnectionError)
                details = result.get('transport_details') or {}
                detail_text = ', '.join(f'{key}={value}' for key, value in details.items())
                message = result.get("message") or "模型网络传输失败"
                failure = error(message + (f' ({detail_text})' if detail_text else ''))
                failure.transport_details = details
                raise failure
            text, usage = result["text"], result["usage"]
            self.calls += 1
            if images:
                self.image_calls += 1
            from .response import TransportResponse
            return TransportResponse(text, usage, metadata=result.get('metadata'))
        except (Cancelled, BudgetExceeded, TimeoutError, ConnectionError):
            self.last_remote_unconfirmed = True
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=remaining_cancel_grace(self.cancel_grace_seconds, self.cancel))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()

    def probe(self, timeout: float = 10.0) -> bool:
        """连接 API 主机并 GET /models：有 HTTP 回应且非 5xx 即视为可用（不消耗 token）。"""
        import http.client
        import sys
        from urllib.parse import urlsplit
        url = self.base_url.rstrip("/") + "/models"
        sys.audit("urllib.Request", url, None, {}, "GET")
        parts = urlsplit(url)
        if parts.scheme not in ("https", "http"):
            return False
        connection = (http.client.HTTPSConnection if parts.scheme == "https" else
                      http.client.HTTPConnection)(parts.hostname, parts.port, timeout=timeout)
        try:
            connection.request("GET", parts.path or "/",
                               headers={"Authorization": "Bearer " + self.api_key})
            response = connection.getresponse()
            response.read(1024 * 1024)
            return response.status < 500
        except (OSError, http.client.HTTPException):
            return False
        finally:
            connection.close()

    def report(self) -> dict:
        return {"calls":self.calls,"image_calls":self.image_calls,"key_ref":self._key_ref,
                "last_image_hashes":[p.sha256 for p in self.last_image_payloads]}


class RefusingTransport:
    """拒绝真实服务的传输替身：**缺回放就阻断**，不回退真网络（方案 §4）。"""

    def __init__(self, reason: str = "") -> None:
        self.reason = reason or ("回放库里没有这个请求；方案 §4：replay 缺缓存立即阻断，"
                                 "禁止回退真实服务")

    def __call__(self, **_kwargs):
        from ..errors import CapabilityError
        raise CapabilityError(self.reason)


__all__ = ["API_KEY_ENV", "BASE_URL_ENV", "DEFAULT_BASE_URL", "SECRETS_REL",
           "resolve_api_key", "RealTransport", "RefusingTransport",
           "CancelAwareCall", "_messages_with_images", "ImagePayload", "image_payloads"]
