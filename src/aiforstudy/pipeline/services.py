"""Injected boundaries: execution control, cancellation, providers and local workers."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from ..contracts.artifact import ArtifactRef
from ..contracts.enums import ExecutionState, Profile
from ..contracts.request import RunRequest
from ..contracts.scope import ScopePlan
from ..contracts.stage import StageResult
from ..core.registry import BookContext, BookRegistry
from ..core.workspace import Workspace
from ..errors import BudgetExceeded, Cancelled, CapabilityError
from ..providers.capabilities import Capabilities
from .execution_control import ExecutionControl, remaining_cancel_grace
from .graph import Node


class CancelToken:
    """取消信号。**检查点是显式的**：长循环必须自己调 `checkpoint()`。"""

    def __init__(self, *, grace_seconds: float = 10.0) -> None:
        self._event = threading.Event()
        self._reason = ""
        self._requested_at: float | None = None
        self.grace_seconds = float(grace_seconds)

    def cancel(self, reason: str = "用户取消") -> None:
        if self._event.is_set():
            return  # Repeated cancel clicks cannot restart the cleanup window.
        self._reason = reason
        self._requested_at = time.monotonic()
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def cancel_requested_at(self) -> float | None:
        return self._requested_at

    def wrap_up_remaining(self) -> float:
        """本地收尾还剩多少秒（§6.1：取消后本地请求／worker 在 10s 内结束或标远端未确认）。"""
        if self._requested_at is None:
            return self.grace_seconds
        return max(0.0, self.grace_seconds - (time.monotonic() - self._requested_at))

    def checkpoint(self) -> None:
        from ..errors import Cancelled
        if self._event.is_set():
            raise Cancelled(self._reason or "已取消")


#: **控制流异常**：它们不是"这个阶段失败了"，而是"整个 run 该停了"。
#: §6.1 原话：`Cancelled/BudgetExceeded` 必须**穿透旧宽泛 catch**。
#: 旧代码里大量 `except Exception` 会把它们翻成 `blocked`/`failed`，于是
#: 预算停止（退出码 3）和取消（130）在报告里变成"某个阶段没跑成"（2）——
#: 三种完全不同的收场被压成一个，排查时看不出是哪一头。
CONTROL_ERRORS = (BudgetExceeded, Cancelled)


def is_control_error(exc: BaseException) -> bool:
    """是不是"整个 run 该停了"这类异常。"""
    return isinstance(exc, CONTROL_ERRORS)


def reraise_control_error(exc: BaseException) -> None:
    """控制流异常原样抛出；其它异常返回给调用方继续处理。

    用法固定成每个宽泛 handler 的**第一行**：

        except Exception as exc:
            reraise_control_error(exc)
            ...      # 到这里才说明它真的是内容／环境问题
    """
    if is_control_error(exc):
        raise exc


@dataclass
class Providers:
    """外部服务的替身入口。**任何一项都允许是 None**（表示本次不该用它）。

    ⚠️ `allow_real_services=False`（replay/smoke）时，拿到真实服务就报错而不是"顺手用一下"——
    方案 §4：replay 缓存缺失**立即阻断**，禁止回退真实服务。
    """
    llm: object | None = None
    mineru: object | None = None
    normalizer: object | None = None
    auditor: object | None = None
    is_stub: bool = False
    #: 合成替身用的**确定性注入**（不是产品功能）：指定 scope 的阶段直接失败，
    #: 用来为"失败沿依赖传播、但不缩小用户指定的范围"提供真实样本（§15 A19/A31）。
    fail_scopes: frozenset[str] = frozenset()
    #: 同上：指定 scope 走"经政策认可的 not_applicable"
    not_applicable_scopes: frozenset[str] = frozenset()
    #: Optional local-test observation; never affects routing or execution limits.
    call_observer: Callable[[str, dict], None] | None = None

    def require(self, name: str):
        value = getattr(self, name, None)
        if value is None:
            raise CapabilityError(
                f"本次运行没有可用的 {name} 服务。若这是 replay/smoke 档，"
                f"说明替身没接上；**不允许回退真实服务**（方案 §4）。")
        return value


@dataclass
class StageContext:
    """交给阶段实现的一切。阶段**不自己找**书籍默认值或"最新"文件（§6）。"""
    node: Node
    request: RunRequest
    plan: ScopePlan
    workspace: Workspace
    context: BookContext
    services: "Services"
    #: 上一次该节点的输出（恢复时给阶段判断能不能增量），不是权威输入
    previous_outputs: tuple[ArtifactRef, ...] = ()
    #: §4.2.2：本次运行是**协调器排出的返修批次**的一部分时，把批次交给阶段。
    #: 阶段据此知道"为什么又跑了一遍、要关哪些工单、哪些内容不许动"。
    #: 类型是 `pipeline.repair.RepairBatch`；这里不导入它，避免 services 依赖 repair。
    repair_batch: object | None = None
    #: Host validation of the previous rejected patch; consumes the same repair rounds.
    repair_feedback: dict | None = None
    #: Runner 核验完整旧产物和生成依赖后，才可要求政策重检冻结候选。
    recheck_only: bool = False
    recheck_reason: str = ""

    @property
    def scope_key(self) -> str:
        return self.node.scope_key

    def log(self, message: str, **data) -> None:
        self.services.emit("stage.note", stage=self.node.stage, node_id=self.node.node_id,
                           scope_key=self.node.scope_key, message=message, **data)


#: 阶段实现签名
StageImpl = Callable[[StageContext], StageResult]


class StageRegistry:
    """阶段名 → 实现。默认全是"未迁入"，由调用方按需覆盖。"""

    def __init__(self, impls: dict[str, StageImpl] | None = None) -> None:
        self._impls: dict[str, StageImpl] = {}
        if impls:
            self.register_all(impls)

    def register(self, name: str, impl: StageImpl) -> None:
        self._impls[name] = impl

    def register_all(self, impls: dict[str, StageImpl]) -> None:
        for name, impl in impls.items():
            self.register(name, impl)

    def __contains__(self, name: object) -> bool:
        return str(name) in self._impls

    def names(self) -> list[str]:
        return sorted(self._impls)

    def get(self, name: str) -> StageImpl:
        if name in self._impls:
            return self._impls[name]
        return not_implemented(name)


def not_implemented(missing_stage: str) -> StageImpl:
    """未迁入阶段的**诚实**实现：明确 blocked，而不是返回 succeeded 让门看起来过了。"""
    def impl(ctx: StageContext) -> StageResult:
        return StageResult.blocked(
            missing_stage,
            f"阶段 {missing_stage!r} 的实现尚未迁入统一引擎（属 M3–M7 的工作）。"
            f"本阶段现在**不产出**任何产物：返回 succeeded 会让 release 档看起来通过。",
            outputs=())
    return impl


@dataclass
class Services:
    """注入进 runner 与阶段的一切外部边界。"""
    workspace: Workspace
    registry: BookRegistry
    context: BookContext
    capabilities: Capabilities
    control: ExecutionControl
    cancel: CancelToken
    providers: Providers = field(default_factory=Providers)
    stages: StageRegistry = field(default_factory=StageRegistry)
    clock: Callable[[], float] = time.monotonic
    wall_clock: Callable[[], float] = time.time
    store: object | None = None                 # RunStore（runner 会设）
    subprocess_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run

    # ── 事件
    def emit(self, kind: str, **kw):
        store = self.store
        if store is not None:
            return store.emit(kind, **kw)
        return None

    # ── 一致性自检：档位与真实服务
    def real_providers(self) -> list[str]:
        """注入了哪些**真实**外部服务。什么都没注入不算"有真实服务可用"。"""
        if self.providers.is_stub:
            return []
        return [name for name in ("llm", "mineru", "normalizer", "auditor")
                if getattr(self.providers, name, None) is not None]

    def assert_profile_allows_real_services(self) -> None:
        """档位禁真实服务时，**注入的**真实服务必须为 0。

        ⚠️ 判据是"注入了真实服务"，不是"`is_stub` 为假"。第一版写成后者，于是
        replay 档在**什么都没注入**时也被拒 —— 而"没注入"恰恰是 replay 该有的样子：
        它本来就只用固定响应，遇到缺缓存要 blocked（那是 replay 自己的阶段实现负责的），
        不是"试图用真实服务"。
        """
        if self.control.policy.allow_real_services:
            return
        real = self.real_providers()
        if real:
            raise CapabilityError(
                f"档位不允许真实服务，但注入了 {real}。"
                f"方案 §4：replay 只取固定响应与本地输入，**缓存缺失立即阻断，"
                f"禁止回退真实服务**；smoke 用替身外部服务。")

    # ── 子进程（单次绝对超时）
    def run_subprocess(self, argv: list[str], *, cwd: Path | None = None,
                       timeout: float | None = None, env: dict | None = None):
        from ..errors import BudgetExceeded
        self.cancel.checkpoint()
        self.control.check()
        limit = self.control.policy.api_deadline_seconds if timeout is None else float(timeout)
        if limit is not None and limit <= 0:
            raise ValueError("单次子进程 timeout 必须为正")
        if self.subprocess_runner is not subprocess.run:
            # Explicitly injected boundary remains usable by offline adapters.
            try:
                return self.subprocess_runner(argv, cwd=str(cwd or self.workspace.root),
                                              timeout=limit, env=env,
                                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            except subprocess.TimeoutExpired:
                self.control.check()
                raise
        started = time.monotonic()
        proc = subprocess.Popen(argv, cwd=str(cwd or self.workspace.root), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=(os.name == "posix"))
        try:
            while True:
                self.cancel.checkpoint()
                self.control.check()
                left = None if limit is None else limit - (time.monotonic() - started)
                if left is not None and left <= 0:
                    self.control.check()
                    raise subprocess.TimeoutExpired(argv, limit)
                try:
                    output, _ = proc.communicate(timeout=0.1 if left is None else min(0.1, left))
                except subprocess.TimeoutExpired:
                    continue
                self.cancel.checkpoint()
                self.control.check()
                return subprocess.CompletedProcess(argv, proc.returncode, output, None)
        except BaseException:
            # Kill the whole session, including grandchildren that inherited pipes.
            self._stop_process(proc)
            raise

    def _stop_process(self, proc: subprocess.Popen) -> None:
        def send(sig):
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, sig)
                elif proc.poll() is None:
                    proc.terminate() if sig == signal.SIGTERM else proc.kill()
            except ProcessLookupError:
                pass
        send(signal.SIGTERM)
        try:
            boundary = BoundaryCancellation(self.cancel, self.control)
            grace = (remaining_cancel_grace(self.control.policy.cancel_grace_seconds, boundary)
                     if boundary.cancelled else self.control.policy.drain_seconds)
            proc.communicate(timeout=max(0.0, grace))
        except subprocess.TimeoutExpired:
            pass
        finally:
            # A grandchild may have closed stdout but ignored TERM.
            send(signal.SIGKILL if os.name == "posix" else signal.SIGTERM)
            proc.communicate()

    def snapshot(self) -> dict:
        return {"workspace": str(self.workspace.root), "book_id": self.context.book_id,
                "providers_stub": self.providers.is_stub,
                "stage_impls": self.stages.names(),
                "execution": self.control.snapshot(),
                "capabilities": self.capabilities.as_dict()}


def stub_impls(result_factory: Callable[[StageContext], StageResult],
               names: tuple[str, ...] | None = None) -> dict[str, StageImpl]:
    """给 smoke/replay 用：把一批阶段都接到同一个替身实现上。"""
    from .graph import CANONICAL_STAGES
    target = names or tuple(s.name for s in CANONICAL_STAGES)
    return {name: result_factory for name in target}


__all__ = ["CancelToken", "Providers", "StageContext", "StageImpl", "StageRegistry",
           "not_implemented", "Services", "stub_impls", "CONTROL_ERRORS",
           "is_control_error", "reraise_control_error"]


class BoundaryCancellation:
    """Cancellation bridge for blocking workers."""
    def __init__(self,cancel,control):
        self.user=cancel
        self.control=control
    def checkpoint(self):
        self.user.checkpoint()
        self.control.check()
    @property
    def cancelled(self): return self.user.cancelled or bool(self.control.stop_reason)
    @property
    def reason(self): return self.user.reason if self.user.cancelled else self.control.stop_reason
    @property
    def cancel_requested_at(self):
        values = [value for value in (self.user.cancel_requested_at, self.control.stop_requested_at)
                  if value is not None]
        return min(values) if values else None
