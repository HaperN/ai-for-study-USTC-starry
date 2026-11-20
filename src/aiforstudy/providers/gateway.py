"""Model gateway: durable bounded attempts, cache, cancellation; no billing state."""
from __future__ import annotations

import json
import time
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Protocol

from ..core.digest import digest_file, digest_of
from ..core.json_syntax import loads_json_syntax
from ..core.review_mode import reviews_frozen
KIND_CACHE, KIND_CALL, KIND_RETRY = "cache", "call", "retry"
from ..errors import (AifsError, BudgetExceeded, Cancelled, CapabilityError,
                      ConfigError, ContractError)
from ..pipeline.execution_control import (
    CallAttempt, ExecutionControl, ExecutionScope, AttemptLimitExceeded, RECOVERABLE_RESPONSE_STATES, TERMINAL_RESPONSE_STATES,
)
from ..pipeline.services import CancelToken
from ..pipeline.network_gate import (NETWORK_RETRY, NETWORK_WAIT, RATE_LIMIT_BACKOFF_SECONDS,
                                     REQUEST_FAILURE_LIMIT, REQUEST_RETRY_DELAY_SECONDS)
from .response import response_metadata
from .review_failures import classify_review_failure
from .response_evidence import preserve_response, previous_length_response

#: 同一逻辑请求最多 2 次，跨 resume 累计（包括媒体证据审核）。
MAX_ATTEMPTS = 2
MAX_RETRIES = MAX_ATTEMPTS - 1
NETWORK_RETRY_DELAY_SECONDS = 5.0

#: 可重试的错误类别（内容返修**不在此列**，它走 audit 的轮次政策）
RETRYABLE = ("timeout", "network", "http-5xx", "http-429", "bad-json", "empty")

#: 交付物协议无法解析时，把**原始文本**交回调用方的哨兵字段。
#:
#: 背景（历史回归案例）：模型漏一个逗号会让 `json.loads` 在网关里失败，
#: 于是 `bad-json` 按传输错误**重发同一份请求**（输入一个字没变），模型只会再错一次，
#: 两次用尽就把整节判死。而 D16-1 要的是"输出不合格 → 可纠正错误 + 轮次"。
#: 所以内容生成/返修这类"交付物必须是完整 JSON"的调用方选择 `protocol_errors=True`：
#: 坏 JSON 只花**一次**尝试，原文原样交回，由上层把它当可纠正错误喂回模型。
#: 默认 `False`，其余调用方（媒体证据审核等）行为与从前完全一致。
UNPARSED_PROTOCOL = '__unparsed_protocol_text__'

#: 修改计划 A2：可见内容为空（不是截断）时交回调用方的哨兵；只对 `protocol_errors=True` 的调用方。
#: 它不是网络失败，也不是语法错误：调用方带提示重发原请求，占协议额度。
EMPTY_PROTOCOL = '__empty_protocol_response__'


class Transport(Protocol):
    """返回兼容二元组；真实传输的 TransportResponse 另带本次响应元数据。"""

    def __call__(self, *, model: str, effort: str, messages: list[dict],
                 images: list[str] | None, max_tokens: int,
                 timeout: float | None) -> tuple[str, dict]: ...


def _role_value(role, key, default=None):
    return role.get(key, default) if isinstance(role, dict) else getattr(role, key, default)


def _capacity_policy(role, requested):
    initial = requested or int(_role_value(role, 'max_tokens', 0) or 0)
    larger = _role_value(role, 'length_retry_max_tokens', 0)
    if (type(initial) is not int or initial < 0 or type(larger) is not int or larger < 0
            or (larger and (initial <= 0 or larger <= initial))):
        raise ConfigError('length_retry_max_tokens 必须为大于初始 max_tokens 的显式整数容量上界')
    return {'version':1, 'max_tokens':initial, 'length_retry_max_tokens':larger}


@dataclass
class CallRequest:
    """一次逻辑模型请求。**逻辑身份全在这里**（run_id 由 gateway 补）。"""
    role: str
    messages: list[dict]
    tag: str = ""
    unit_key: str = ""
    resource_id: str = ""
    stage: str = ""
    bucket: str = "other"
    scope_kind: str = "book"
    images: tuple[str, ...] = ()
    max_tokens: int = 0
    schema_version: str = "1.0"       # 进缓存指纹
    prompt_version: str = ""          # 提示词版本
    rules_version: str = ""           # 规则/模板版本
    extra_identity: dict = field(default_factory=dict)
    # 交付物协议无法解析时是否把原文交回调用方（见 UNPARSED_PROTOCOL）。
    # **刻意不进指纹**：它是失败处置策略，不改变模型收到的输入，也不该让缓存归属漂移。
    protocol_errors: bool = False
    # A syntax-only repair keeps its model/scope/capacity, but needs no images
    # or high-effort author/reviewer reasoning.
    syntax_repair: bool = False

    def fingerprint(self, *, model: str, effort: str, book_id: str = "",
                    max_tokens: int | None = None, transport_identity=None) -> str:
        """缓存指纹：**只吃逻辑身份与内容**，不含 run_id/时间/绝对路径（§11）。

        ⚠️ **`book_id` / `scope_kind` / `unit_key` / `stage` 必须进指纹**。
        这是实测抓出来的：第一版只放了 role/model/effort/messages/version，
        于是"同一段提示词"在**不同书或不同单元**上会命中同一条缓存 ——
        方案 §11 的原话就是"**跨书同名 CH 单元也不能串缓存归属**"。
        这类串号极其隐蔽：两本书都有 `CH01-S01`，提示词也可能一模一样。
        """
        image_hashes = []
        for image in self.images:
            path = Path(image)
            if not path.is_file():
                raise ContractError(f"视觉请求缺图像输入：{path}")
            try:
                image_hashes.append(digest_file(path))
            except OSError as exc:
                raise ContractError(f"无法读取视觉请求的图像输入：{path}") from exc
        return digest_of({
            "book_id": book_id, "scope_kind": self.scope_kind,
            "unit_key": self.unit_key, "stage": self.stage, "bucket": self.bucket,
            "role": self.role, "model": model, "effort": effort,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "schema_version": self.schema_version,
            "prompt_version": self.prompt_version, "rules_version": self.rules_version,
            "messages": self.messages, "images": image_hashes,
            "extra": self.extra_identity, **({"transport":transport_identity} if transport_identity is not None else {})})


@dataclass
class CallResult:
    ok: bool
    payload: Any = None
    text: str = ""
    cached: bool = False
    attempts: int = 0
    error: str = ""
    kind: str = ""
    fingerprint: str = ""
    failure_kind: str = ""
    @property
    def retried(self) -> bool:
        return self.attempts > 1

    def as_dict(self) -> dict:
        return {"ok":self.ok,"cached":self.cached,"attempts":self.attempts,
                "error":self.error,"kind":self.kind,"fingerprint":self.fingerprint,
                "failure_kind":self.failure_kind}


class Gateway:
    """唯一入口。传输、缓存、持久执行控制与取消均可注入。"""

    def __init__(self, *, transport: Transport, roles: dict,
                 control: ExecutionControl, cancel: CancelToken | None = None,
                 cache_dir: Path | None = None, run_id: str = "", book_id: str = "",
                 clock: Callable[[], float] = time.time,
                 allow_real_services: bool = True,
                 call_observer: Callable[[str, dict], None] | None = None) -> None:
        self.transport = transport
        self.roles = dict(roles)
        self.control = control
        self.cancel = cancel or CancelToken()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.run_id = run_id or control.run_id
        self.book_id = book_id
        self.clock = clock
        self.allow_real_services = allow_real_services
        self.call_observer = call_observer
        # One owner for actual worker limits, including direct Gateway callers.
        # Injected offline transports keep their established call signature.
        from .transport import RealTransport
        if isinstance(transport, RealTransport):
            from ..pipeline.services import BoundaryCancellation
            transport.cancel = BoundaryCancellation(self.cancel, control)
            transport.cancel_grace_seconds = control.policy.cancel_grace_seconds
            transport.connect_timeout_seconds = control.policy.connect_timeout_seconds
        # Plan N: only transports that opt in (the production factory) get a probe.
        gate = getattr(control, 'network_gate', None)
        if (getattr(transport, 'network_wait', False) and callable(getattr(transport, 'probe', None))
                and gate is not None and gate.probe is None):
            gate.probe = transport.probe

    def _observe(self, event: str, metadata: dict, **fields) -> None:
        """Optional test telemetry cannot change calls, retry policy or responses.

        No prompts, response text, image paths, headers or exception messages cross
        this hook. Production has no observer and creates no observation files.
        """
        if self.call_observer is None:
            return
        try:
            self.call_observer(event, deepcopy({**metadata, **fields}))
        except Exception:
            # The optional observer owns its own diagnostics. A failed log must
            # never discard a paid response or provoke an extra model request.
            pass

    # ── 缓存
    def _cache_path(self, fingerprint: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"{fingerprint}.json"

    def _cache_read(self, fingerprint: str) -> dict | None:
        path = self._cache_path(fingerprint)
        if path is None or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None                    # 坏缓存当没有（**并且不删**，留给人工看）
        if not isinstance(payload,dict) or payload.get("fingerprint") != fingerprint:
            return None
        return payload

    def _cache_write(self, fingerprint: str, text: str, *, metadata: dict,
                     capacity_policy: dict, max_tokens: int) -> None:
        path = self._cache_path(fingerprint)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        import uuid
        tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        tmp.write_text(json.dumps({"fingerprint": fingerprint, "text": text,
                                   "response_metadata":metadata, "capacity_policy":capacity_policy,
                                   "max_tokens":max_tokens,
                                   "at": self.clock()},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)                  # 原子：半写的缓存会被当成没有

    def _response_failure(self, scope, lease, *, text, metadata, max_tokens, problem, recovery, usage=None):
        try:
            path = preserve_response(self.control, scope, lease, text=text, metadata=metadata,
                                     max_tokens=max_tokens, problem=problem, usage=usage)
        except Exception as exc:
            error = f'模型失败响应无法留证（{type(exc).__name__}）；禁止自动重发'
            lease.finish('failed',error=error)
            if recovery is not None: recovery.finished('blocked',error=error)
            raise ContractError(error) from exc
        error = problem + '; response_record=' + str(path)
        lease.finish('protocol_failed',error=error)
        if recovery is not None: recovery.finished('transport_failed',error=error)
        return error

    def _wait_network_retry(self):
        """Give a transient disconnect time to recover within the same quota."""
        until = time.monotonic() + NETWORK_RETRY_DELAY_SECONDS
        while True:
            self.cancel.checkpoint()
            self.control.check()
            remaining = until - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(.1, remaining))

    def _network_checkpoint(self):
        self.cancel.checkpoint()
        self.control.check()

    # ── 主流程
    def call(self, request: CallRequest) -> CallResult:
        if request.syntax_repair:
            # Media clients may attach their original images again. Strip them
            # before fingerprinting as well as transport; syntax is text-only.
            request = replace(request, images=())
        role = self.roles.get(request.role)
        if role is None:
            raise ContractError(f"没有 {request.role!r} 这个模型角色")
        transport = (self.transport.for_role(request.role) if callable(getattr(self.transport, 'for_role', None)) else self.transport)
        if hasattr(transport, 'cancel'):
            from ..pipeline.services import BoundaryCancellation
            transport.cancel = BoundaryCancellation(self.cancel, self.control)
            transport.cancel_grace_seconds = self.control.policy.cancel_grace_seconds
            transport.connect_timeout_seconds = self.control.policy.connect_timeout_seconds
        model = str(_role_value(role, 'model', '') or '')
        effort = 'low' if request.syntax_repair else str(_role_value(role, 'effort', 'high') or 'high')
        capacity_policy = _capacity_policy(role, request.max_tokens)
        max_tokens = capacity_policy['max_tokens']
        retry_max_tokens = capacity_policy['length_retry_max_tokens']
        fingerprint = request.fingerprint(model=model, effort=effort,
                                          book_id=self.book_id, max_tokens=max_tokens,
                                          transport_identity=getattr(transport, 'cache_identity', None))
        observation = {'run_id':self.run_id, 'book_id':self.book_id,
            'stage':request.stage, 'unit_id':request.unit_key, 'resource_id':request.resource_id,
            'role':request.role, 'model':model, 'effort':effort, 'max_tokens':max_tokens,
            'fingerprint':fingerprint, 'scope_kind':request.scope_kind}

        self.cancel.checkpoint()
        recover_attempt = getattr(transport, 'recover_attempt', None)
        try:
            self.control.check()
        except TimeoutError:
            if not callable(recover_attempt):
                raise
            # Only a child response bound to a previous dispatch may cross this deadline.
            cached = None
        else:
            cached = self._cache_read(fingerprint)
        if cached is not None and (cached.get('capacity_policy') != capacity_policy
                or response_metadata(cached.get('response_metadata')).get('finish_reason') == 'length'):
            cached = None
        if cached is not None:
            try:
                payload = (loads_json_syntax(cached["text"]) if reviews_frozen()
                           else json.loads(cached["text"]))
                if not reviews_frozen() and not isinstance(payload, dict):
                    raise ValueError("object required")
            except (ValueError, KeyError, TypeError):
                cached = None
            else:
                self._observe('cache_hit', observation, attempt=0, attempt_id=None,
                              wall_seconds=0.0, usage=None)
                return CallResult(ok=True,payload=payload,text=cached["text"],cached=True,
                                  attempts=0,kind=KIND_CACHE,fingerprint=fingerprint)

        scope = ExecutionScope(self.run_id,request.unit_key,request.stage,request.resource_id)
        used = self.control.transport_attempts_used(scope,fingerprint)
        limit = self.control.policy.attempt_limits['transport']
        if used >= limit and not callable(recover_attempt):
            raise AttemptLimitExceeded(f'逻辑请求运输尝试已达上限 {limit}；恢复不重置')
        transport_args = dict(model=model, effort=effort, messages=request.messages, images=list(request.images) or None)
        recovered = None
        if callable(recover_attempt):
            attempts = [a for a in self.control.snapshot()['attempts'] if a['fingerprint'] == fingerprint]
            if any(a['scope'] != scope.as_dict() for a in attempts):
                raise ContractError('Saved video fingerprint has mismatched execution scope')
            for attempt_record in attempts:
                state = attempt_record['state']
                if state == 'protocol_failed' and attempt_record != attempts[-1]:
                    continue  # A recorded length retry can legitimately follow this outcome.
                attempt_id = attempt_record['attempt_id']
                if state not in RECOVERABLE_RESPONSE_STATES:
                    if transport.has_saved_response(attempt_id):
                        raise ContractError('Saved transport response inconsistent with ledger state')
                    continue
                prior_truncation = previous_length_response(self.control, scope, fingerprint,
                                                           before_attempt=attempt_record['attempt'])
                original_capacity = retry_max_tokens if prior_truncation else max_tokens
                response = recover_attempt(attempt_id, fingerprint, scope=scope.as_dict(),
                                           **transport_args, max_tokens=original_capacity)
                if response is None:
                    if state in TERMINAL_RESPONSE_STATES:
                        raise ContractError('Completed video attempt missing its saved transport response')
                    continue
                if attempt_record != attempts[-1]:
                    raise ValueError('Saved transport response has ambiguous attempt identity')
                lease = CallAttempt(self.control, attempt_id, fingerprint, attempt_record['attempt'],
                                    self.control.clock(), None, recovered=True)
                recovered = (lease, response)
                max_tokens = original_capacity
        if recovered is None:
            if callable(recover_attempt):
                self.control.check()
            previous_length = previous_length_response(self.control, scope, fingerprint)
            if previous_length is not None:
                prior_max, prior_path = previous_length
                if retry_max_tokens <= prior_max:
                    return CallResult(ok=False,attempts=used,
                        error='capacity_failure: finish_reason=length; 未配置更大容量，禁止按原额度重发; response_record='+str(prior_path),
                        kind=KIND_CALL,fingerprint=fingerprint,failure_kind="truncation")
                max_tokens = retry_max_tokens
        if used >= limit and recovered is None:
            raise AttemptLimitExceeded(f'逻辑请求运输尝试已达上限 {limit}；恢复不重置')
        recovery = getattr(self,'media_review_recovery',None)
        if recovery is not None: recovery.begin(fingerprint,used,batch_size=limit)
        last_error = ''
        failure_kind = 'internal'
        # Plan N: with a probe, network failures wait for connectivity and spend
        # no transport attempt; a request failing while the probe is healthy
        # stops after REQUEST_FAILURE_LIMIT. Without a probe the old bound holds.
        gate = getattr(self.control, 'network_gate', None)
        waiting = gate is not None and gate.probe is not None
        if waiting and self.control.request_network_failures(scope,fingerprint) >= REQUEST_FAILURE_LIMIT:
            return CallResult(ok=False,attempts=used,kind=KIND_CALL,fingerprint=fingerprint,
                error=f'网络正常时同一请求已连续失败 {REQUEST_FAILURE_LIMIT} 次；恢复不重置，不再自动重发',
                failure_kind='network')
        while recovered is not None or self.control.transport_attempts_used(scope,fingerprint) < limit:
            self.cancel.checkpoint()
            lease = recovered[0] if recovered else self.control.acquire_call(scope,fingerprint=fingerprint,cancel=self.cancel)
            attempt = lease.transport_attempt
            call_observation = {**observation, 'max_tokens':max_tokens,
                                'attempt':attempt, 'attempt_id':lease.lease_id}
            self._observe('recovered' if recovered else 'started', call_observation)
            observed_start = time.monotonic()
            try:
                if recovery is not None: recovery.started(lease)
                if recovered:
                    response = recovered[1]
                    recovered = None
                elif callable(getattr(transport, 'dispatch_attempt', None)):
                    response = transport.dispatch_attempt(lease.lease_id, fingerprint, scope=scope.as_dict(), **transport_args,
                                                          max_tokens=max_tokens, timeout=lease.timeout_seconds())
                else:
                    response = transport(**transport_args, max_tokens=max_tokens, timeout=lease.timeout_seconds())
                text, _usage = response
                metadata = response_metadata(getattr(response, 'metadata', None))
            except (TimeoutError,ConnectionError,OSError) as exc:
                self._observe('failed', call_observation, error_type=type(exc).__name__,
                              wall_seconds=time.monotonic()-observed_start, usage=None,
                              transport_details=getattr(exc, 'transport_details', {}))
                last_error = f'{type(exc).__name__}: {exc}'
                failure_kind = classify_review_failure(exc)
                if callable(recover_attempt) and transport.has_saved_response(lease.lease_id):
                    # The child is reaped. Keep this dispatch charged and leave its
                    # response to an explicit resume, even if the network is down.
                    lease.finish('transport_failed', error=last_error)
                    raise
                if waiting:
                    details = getattr(exc, 'transport_details', None) or {}
                    rate_limited = details.get('http_status') == 429
                    if not rate_limited and not gate.probe_now():
                        lease.finish(NETWORK_WAIT,error=last_error)
                        if recovery is not None: recovery.finished(NETWORK_WAIT,error=last_error)
                        gate.enter_outage(last_error)
                        gate.wait_until_healthy(self._network_checkpoint)
                        continue
                    lease.finish(NETWORK_RETRY,error=last_error)
                    if recovery is not None: recovery.finished(NETWORK_WAIT,error=last_error)
                    failures = self.control.request_network_failures(scope,fingerprint)
                    if failures >= REQUEST_FAILURE_LIMIT:
                        return CallResult(ok=False,attempts=attempt,kind=KIND_CALL,fingerprint=fingerprint,
                            error=f'{last_error}；网络正常时同一请求连续失败 {failures} 次',
                            failure_kind=failure_kind)
                    delay = (RATE_LIMIT_BACKOFF_SECONDS[min(failures, len(RATE_LIMIT_BACKOFF_SECONDS)) - 1]
                             if rate_limited else REQUEST_RETRY_DELAY_SECONDS)
                    gate.wait_request_retry(delay, self._network_checkpoint)
                    continue
                lease.finish('transport_failed',error=last_error)
                if recovery is not None: recovery.finished('transport_failed',error=last_error)
                if attempt < limit:
                    self._wait_network_retry()
                continue
            except AifsError as exc:
                self._observe('failed', call_observation, error_type=type(exc).__name__,
                              wall_seconds=time.monotonic()-observed_start, usage=None)
                lease.finish('cancelled' if isinstance(exc,Cancelled) else 'blocked',error=str(exc))
                if recovery is not None: recovery.finished('transport_failed' if isinstance(exc,Cancelled) else 'blocked',error=str(exc))
                raise
            except Exception as exc:
                self._observe('failed', call_observation, error_type=type(exc).__name__,
                              wall_seconds=time.monotonic()-observed_start, usage=None)
                lease.finish('failed',error=f'{type(exc).__name__}: {exc}')
                if recovery is not None: recovery.finished('blocked',error=str(exc))
                return CallResult(ok=False,attempts=attempt,error=f"{type(exc).__name__}: {exc}",
                    kind=KIND_CALL,fingerprint=fingerprint,failure_kind=classify_review_failure(exc))
            except BaseException as exc:
                self._observe('failed', call_observation, error_type=type(exc).__name__,
                              wall_seconds=time.monotonic()-observed_start, usage=None)
                lease.finish('interrupted')
                if recovery is not None: recovery.finished('transport_failed',error='process interrupted')
                raise
            self._observe('response', call_observation,
                          wall_seconds=time.monotonic()-observed_start, usage=_usage)
            if metadata.get('finish_reason') == 'length':
                last_error = self._response_failure(scope,lease,text=text,metadata=metadata,
                    max_tokens=max_tokens,problem='capacity_failure: finish_reason=length; 模型输出达到容量上限',
                    recovery=recovery)
                if retry_max_tokens > max_tokens and attempt < limit:
                    max_tokens = retry_max_tokens
                    continue
                return CallResult(ok=False,attempts=attempt,error=last_error,
                                  kind=KIND_CALL,fingerprint=fingerprint,failure_kind="truncation")
            if not str(text or '').strip():
                # 修改计划 A2：只有推理、没有可见输出。留证（含 usage），归为 empty_response；
                # 不送语法纠正，也不叫网络失败。
                last_error = self._response_failure(scope,lease,text=text,metadata=metadata,
                    max_tokens=max_tokens,recovery=recovery,usage=_usage,
                    problem='empty_response: 可见内容为空（finish_reason=%s）' % (metadata.get('finish_reason') or '未给出'))
                failure_kind = 'empty_response'
                if request.protocol_errors:
                    return CallResult(ok=False,payload=None,text='',attempts=attempt,error=last_error,
                                      kind=KIND_CALL,fingerprint=fingerprint,failure_kind='empty_response')
                continue
            # Provider usage is deliberately ignored by execution. Protocol state is the only
            # input to retry; malformed billing metadata cannot discard an answer.
            try:
                payload = loads_json_syntax(text) if reviews_frozen() else json.loads(text)
                if not reviews_frozen() and not isinstance(payload,dict):
                    raise ValueError('响应必须为 JSON 对象')
            except (ValueError,TypeError) as exc:
                last_error = self._response_failure(scope,lease,text=text,metadata=metadata,
                    max_tokens=max_tokens,problem=f'bad-json: {exc}',recovery=recovery)
                failure_kind = 'protocol'
                if request.protocol_errors:
                    # 交付物协议坏掉时重发同一份提示词不会产生新信息；把原文交回调用方，
                    # 由它作为「可纠正错误 + 轮次」喂回模型（D16-1）。
                    # 只花这一次尝试：纠正轮换新指纹，仍各自拥有完整额度。
                    return CallResult(ok=False,payload=None,text=text,attempts=attempt,
                                      error=last_error,kind=KIND_CALL,fingerprint=fingerprint,
                                      failure_kind='unparsed')
                continue
            lease.finish('response_received')
            if recovery is not None: recovery.finished('response_received',payload=payload)
            self._cache_write(fingerprint,text,metadata=metadata,
                              capacity_policy=capacity_policy,max_tokens=max_tokens)
            return CallResult(ok=True,payload=payload,text=text,attempts=attempt,
                              kind=KIND_CALL,fingerprint=fingerprint)
        return CallResult(ok=False,attempts=limit,error=last_error,kind=KIND_CALL,
                          fingerprint=fingerprint,failure_kind=failure_kind)


@dataclass
class MineruLedger:
    """MinerU 的**独立账**（§11：服务操作/页数，不混作聊天 token）。"""

    pages: int = 0
    chunks: int = 0
    retries: int = 0
    operations: int = 0
    unknown_chunks: int = 0

    def record_chunk(self, *, pages: int, retries: int = 0, ok: bool = True) -> None:
        self.chunks += 1
        self.operations += 1
        self.retries += max(0, retries)
        if ok:
            self.pages += max(0, pages)
        else:
            self.unknown_chunks += 1

    def as_dict(self) -> dict:
        return {"pages": self.pages, "chunks": self.chunks, "retries": self.retries,
                "operations": self.operations, "unknown_chunks": self.unknown_chunks,
                "note": "MinerU 按**页数/服务操作**计账，不换算成聊天 token（方案 §11）"}


def replay_transport(responses: dict[str, tuple[str, dict]],
                     calls: list | None = None) -> Transport:
    """回放替身：只认固定响应，**缺了就抛**（不回落真实服务）。"""
    def _transport(*, model: str, effort: str, messages: list[dict],
                   images: list[str] | None, max_tokens: int,
                   timeout: float) -> tuple[str, dict]:
        key = digest_of({"model": model, "effort": effort, "messages": messages,
                         "images": list(images or []), "max_tokens": max_tokens})
        if calls is not None:
            calls.append(key)
        if key not in responses:
            raise CapabilityError(
                f"回放库里没有这个请求（{key[:16]}…）。方案 §4：replay 缺缓存**立即阻断**，"
                f"禁止回退真实服务。")
        return responses[key]
    return _transport


__all__ = ["MAX_RETRIES", "MAX_ATTEMPTS", "RETRYABLE", "Transport", "CallRequest",
           "CallResult", "Gateway", "MineruLedger", "replay_transport"]
