"""Legacy explicit budget test tool. Production uses execution_control and does not import this module.

预算护栏：**达到上限就停止派发**，并给正在跑的进程一个收尾窗口。

方案 §11 的要求逐条落到这里：

> 预算配置必须包含正的 token/time 上限，服务请求受总并发限制；首期默认任务并行 2、
> 上限 8，**不使用无界线程池**。wall-clock watchdog 持续运行，**最后一批没有 pending 时也生效**。
> 达到上限停止派发，通知正在跑的进程；给最多 30 秒收尾记账窗口，随后结束整个进程组。
> 已发出的远端请求可能仍计费，报告展示在途/未知额度，**不能承诺完全消除服务端在途费用**。

三个容易做错、这里刻意做对的地方：

1. **"最后一批也生效"**：只在派发前检查预算是不够的 —— 最后一节跑 40 分钟也能把
   120 分钟的预算吃穿。所以 `check()` 在派发前与收尾时都要调，并且有独立的
   `deadline_remaining()` 供 runner 给子进程设超时。
2. **未知用量不填零**：`record_unknown()` 只累计"未知额度"，报告里单列。
   填 0 会让报告看起来"还有余额"，而实际钱已经花了。
3. **预约（reserve）与实报（record）分开**：请求已发出但响应未回时，那部分额度必须
   **先占住**；否则并行 8 个请求会同时看到"还有余额"然后一起超。

## T03 补上的那一层：`BudgetScope` + `CallLease`

只做 run 级护栏是不够的（调研 §1.2）：一个单元可以在审核里反复重试，把整段额度吃光，
而报告上只看到"跑了很久"。所以 §6.1 又加了三层**累计**上限（unit 32／stage 16／
resource 8）与单次调用的绝对 deadline，并把它做成 `acquire_call()` 唯一入口：

* **attempt 计数**在**取租约时**就涨（初次、审核、证据修复、运输重试全都算），
  结算**不退还** —— 重试不是"重新开始"（§4.7）。
* **活跃时间**按各 scope **活跃区间的并集**算；父层不把并行子任务时长相加（§6.1）。
  暂停期间不算执行时间，但暂停之前的活跃时间不许丢。
* **恢复只恢复一次**：`restore_from_snapshot()` 对同一 run 幂等，崩溃时在途的租约
  **按未知费用保守保留**，并按"最后心跳 + 租约上限"结算崩溃区间 —— 不凭重启归零。
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..core.digest import digest_of
from ..errors import BudgetExceeded, ConfigError
from ..core.workspace import Workspace

DRAIN_SECONDS_DEFAULT = 30

#: 返修护栏的版本化配置文件（§6.1：**受版本控制**，不是散落的魔数）。
REPAIR_POLICY_NAME = "repair_policy.json"
REPAIR_POLICY_VERSION = 1

#: §6.1 的历史默认护栏。正式运行以 `config/execution.json` 为准；旧 `repair_policy.json`/`budget.json`
#: 已于 2026-10-16 移到 `tests/fixtures/legacy_budget/`，仅供本地测试显式读取。
DEFAULT_ATTEMPT_LIMITS = {"unit": 32, "stage": 16, "resource": 8, "transport": 2}
DEFAULT_ACTIVE_SECONDS = {"unit": 1800.0, "stage": 1200.0, "resource": 600.0}
DEFAULT_RENDER_LIMITS = {"draft_seconds": 120.0, "final_seconds": 300.0,
                         "cpu_cores": 2, "memory_bytes": 2 * 1024 ** 3,
                         "output_bytes": 128 * 1024 ** 2,
                         "max_concurrent_workers": 1}


def load_repair_policy(path: str | Path) -> dict:
    """读并校验 `config/repair_policy.json`。**缺文件／坏 JSON／版本不对都要明确报错**。

    允许回退到内置默认值会掩盖"策略文件没装上"这件事，而它正好决定轮次与额度上限。
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"缺返修／预算策略文件：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError(f"策略文件 {path} 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"策略文件 {path} 的顶层必须是 JSON 对象")
    version = data.get("policy_version")
    if version != REPAIR_POLICY_VERSION:
        raise ConfigError(
            f"策略文件 {path} 的 policy_version 必须是 {REPAIR_POLICY_VERSION}，"
            f"实际 {version!r} —— 旧策略不许当新策略用（§6.1：resume 保持原 policy digest）")
    for key in ("attempt_limits", "active_seconds", "api_deadline_seconds",
                "cancel_grace_seconds", "heartbeat_seconds", "render",
                "content_repair_batches_per_unit", "evidence_repairs_per_verdict"):
        if key not in data:
            raise ConfigError(f"策略文件 {path} 缺字段 {key!r}")
    return data


@dataclass
class BudgetPolicy:
    max_tokens: int
    max_minutes: float
    parallelism: int
    drain_seconds: float = DRAIN_SECONDS_DEFAULT
    allow_real_services: bool = True
    release_ready_possible: bool = True
    # ── T03 新增（全部带默认值，位置参数调用点不受影响）
    policy_version: int = REPAIR_POLICY_VERSION
    attempt_limits: dict = field(default_factory=lambda: dict(DEFAULT_ATTEMPT_LIMITS))
    active_seconds: dict = field(default_factory=lambda: dict(DEFAULT_ACTIVE_SECONDS))
    api_deadline_seconds: float = 300.0
    connect_timeout_seconds: float = 15.0
    cancel_grace_seconds: float = 10.0
    heartbeat_seconds: float = 5.0
    content_repair_batches_per_unit: int = 2
    evidence_repairs_per_verdict: int = 1
    render: dict = field(default_factory=lambda: dict(DEFAULT_RENDER_LIMITS))
    #: 这份护栏**从哪来**：`config/repair_policy.json` 的路径，或 `"builtin-defaults"`。
    #:
    #: ⚠️ 它**有意不进 `as_dict()`／`digest()`**：摘要是给"额度有没有被改"用的，
    #: 而来源含**机器相关的绝对路径** —— 把它算进摘要，换台机器 resume 就会被误判成
    #: "额度变了"而拒绝。来源是**溯源信息**，不是护栏本身。
    #:
    #: 它原来是**动态属性**（`policy.policy_source = ...`，带 `# type: ignore`），
    #: 于是 `engine.py` 重新构造 `BudgetPolicy` 时**静默丢掉了它** ——
    #: 运行记录里因此永远看不到"用了哪份策略"。
    policy_source: str = ""

    def __post_init__(self) -> None:
        if self.max_minutes <= 0:
            raise ConfigError(f"max_minutes 必须为正，实际 {self.max_minutes}")
        if self.max_tokens < 0:
            raise ConfigError(f"max_tokens 不能为负，实际 {self.max_tokens}")
        if not 1 <= self.parallelism <= 8:
            raise ConfigError(f"parallelism 必须在 1..8，实际 {self.parallelism}")
        self.attempt_limits = {**DEFAULT_ATTEMPT_LIMITS, **dict(self.attempt_limits)}
        self.active_seconds = {**DEFAULT_ACTIVE_SECONDS,
                               **{k: float(v) for k, v in dict(self.active_seconds).items()}}
        self.render = {**DEFAULT_RENDER_LIMITS, **dict(self.render)}
        for name, value in self.attempt_limits.items():
            if int(value) < 1:
                raise ConfigError(f"attempt_limits[{name!r}] 必须 ≥ 1，实际 {value!r}")
        for name, value in self.active_seconds.items():
            if float(value) <= 0:
                raise ConfigError(f"active_seconds[{name!r}] 必须为正，实际 {value!r}")

    @property
    def policy_digest(self) -> str:
        return self.digest()

    def digest(self) -> str:
        """护栏摘要。**冻结进 run**：resume 必须认它，改了额度就换 digest（§6.1）。"""
        return digest_of(self.as_dict())

    def as_dict(self) -> dict:
        return {"max_tokens": self.max_tokens, "max_minutes": self.max_minutes,
                "parallelism": self.parallelism, "drain_seconds": self.drain_seconds,
                "allow_real_services": self.allow_real_services,
                "release_ready_possible": self.release_ready_possible,
                "policy_version": self.policy_version,
                "attempt_limits": dict(sorted(self.attempt_limits.items())),
                "active_seconds": dict(sorted(self.active_seconds.items())),
                "api_deadline_seconds": self.api_deadline_seconds,
                "connect_timeout_seconds": self.connect_timeout_seconds,
                "cancel_grace_seconds": self.cancel_grace_seconds,
                "heartbeat_seconds": self.heartbeat_seconds,
                "content_repair_batches_per_unit": self.content_repair_batches_per_unit,
                "evidence_repairs_per_verdict": self.evidence_repairs_per_verdict,
                "render": dict(sorted(self.render.items()))}


def _apply_repair_policy(policy: BudgetPolicy, data: dict, source: str) -> BudgetPolicy:
    policy.policy_version = int(data.get("policy_version", policy.policy_version))
    policy.attempt_limits = {**policy.attempt_limits,
                             **{k: int(v) for k, v in (data.get("attempt_limits") or {}).items()}}
    policy.active_seconds = {**policy.active_seconds,
                             **{k: float(v) for k, v in (data.get("active_seconds") or {}).items()}}
    policy.api_deadline_seconds = float(data.get("api_deadline_seconds",
                                                policy.api_deadline_seconds))
    policy.connect_timeout_seconds = float(data.get("connect_timeout_seconds",
                                                    policy.connect_timeout_seconds))
    policy.cancel_grace_seconds = float(data.get("cancel_grace_seconds",
                                                 policy.cancel_grace_seconds))
    policy.heartbeat_seconds = float(data.get("heartbeat_seconds", policy.heartbeat_seconds))
    policy.content_repair_batches_per_unit = int(
        data.get("content_repair_batches_per_unit", policy.content_repair_batches_per_unit))
    policy.evidence_repairs_per_verdict = int(
        data.get("evidence_repairs_per_verdict", policy.evidence_repairs_per_verdict))
    policy.render = {**policy.render, **dict(data.get("render") or {})}
    policy.__post_init__()
    policy.policy_source = source
    return policy


def load_policy(workspace: Workspace, profile: str, goal: str = "deliver") -> BudgetPolicy:
    cfg = workspace.config_json("budget.json")
    profiles = cfg.get("profiles") or {}
    if profile not in profiles:
        raise ConfigError(f"config/budget.json 里没有档位 {profile!r}；有的是：{sorted(profiles)}")
    spec = profiles[profile]
    goals = cfg.get("goals") or {}
    goal_spec = goals.get(goal) or {}
    parallelism = int(spec.get("parallelism", (cfg.get("parallelism") or {}).get("default", 2)))
    policy = BudgetPolicy(
        max_tokens=int(goal_spec.get("max_tokens", spec.get("max_tokens", 0))),
        max_minutes=float(goal_spec.get("max_minutes", spec.get("max_minutes", 0))),
        parallelism=parallelism,
        drain_seconds=float(cfg.get("drain_seconds", DRAIN_SECONDS_DEFAULT)),
        allow_real_services=bool(spec.get("allow_real_services", True)),
        release_ready_possible=bool(spec.get("release_ready_possible", True)),
    )
    repair_path = Path(getattr(workspace, "config", Path("config"))) / REPAIR_POLICY_NAME
    if repair_path.is_file():
        return _apply_repair_policy(policy, load_repair_policy(repair_path),
                                    str(repair_path))
    # 缺策略文件的 workspace（临时夹具、空工作区）用内置默认值，但**不假装文件在**
    policy.policy_source = "builtin-defaults"
    return policy


@dataclass
class Usage:
    """一路账。`unknown` 单列 —— 它表示"花了但数不出来"，不是 0。"""
    tokens: int = 0
    unknown_events: int = 0
    cached_hits: int = 0
    calls: int = 0  # compatibility: API settlements + stage releases + cache hits
    retries: int = 0
    by_bucket: dict[str, int] = field(default_factory=dict)
    api_calls: int = 0  # settled transport attempts, including unknown/retried requests
    stage_releases: int = 0

    def add(self, tokens: int, bucket: str = "other") -> None:
        self.tokens += max(0, int(tokens))
        self.by_bucket[bucket] = self.by_bucket.get(bucket, 0) + max(0, int(tokens))

    def as_dict(self) -> dict:
        return {"tokens": self.tokens, "unknown_events": self.unknown_events,
                "cached_hits": self.cached_hits, "calls": self.calls, "retries": self.retries,
                "api_calls": self.api_calls, "stage_releases": self.stage_releases,
                "by_bucket": dict(sorted(self.by_bucket.items()))}


# --------------------------------------------------------------------------- #
# scope
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BudgetScope:
    """一次调用的归属：`run → unit → stage → resource`。

    四层都参与限额判定，但**子级上限不是额外额度**：它只能更紧（§6.1）。
    """
    run_id: str
    unit_id: str = ""
    stage: str = ""
    resource_id: str = ""

    def __post_init__(self) -> None:
        if not str(self.run_id or "").strip():
            raise ConfigError("BudgetScope.run_id 必填 —— 没有 run 归属就不可能有账")

    def key(self) -> str:
        return "|".join(part for part in (self.run_id, self.unit_id, self.stage,
                                          self.resource_id) if part)

    def with_unit(self, unit_id: str) -> "BudgetScope":
        return BudgetScope(run_id=self.run_id, unit_id=unit_id, stage=self.stage,
                           resource_id=self.resource_id)

    def parents(self) -> tuple[str, ...]:
        """从**最近**的父级到根，不含自己。"""
        chain: list[str] = []
        for scope in (BudgetScope(self.run_id, self.unit_id, self.stage),
                      BudgetScope(self.run_id, self.unit_id),
                      BudgetScope(self.run_id)):
            key = scope.key()
            if key != self.key() and key not in chain:
                chain.append(key)
        return tuple(chain)

    def as_dict(self) -> dict:
        return {"run_id": self.run_id, "unit_id": self.unit_id, "stage": self.stage,
                "resource_id": self.resource_id, "key": self.key()}


# --------------------------------------------------------------------------- #
# 租约
# --------------------------------------------------------------------------- #
@dataclass
class CallLease:
    """一次 Gateway 尝试的**预约**。结算幂等，未知用量保守保留。"""
    guard: "BudgetGuard"
    scope: BudgetScope
    estimate_tokens: int
    lease_id: str
    attempt_index: int
    started_at: float
    deadline_seconds: float
    fingerprint: str = ""
    transport_attempt: int = 0
    outcome_hint: str = ""
    _settlement: dict | None = field(default=None, repr=False)

    @property
    def settled(self) -> bool:
        return self._settlement is not None

    def timeout_seconds(self) -> float:
        """本次调用允许的**绝对**窗口：取"单次 API 上限"与"父层剩余"的更小者。

        刻意**每次现算**：调用开始后父层剩余时间会变少，若只算一次，长调用会把
        父层 deadline 拖穿（§6.1：不能靠不断收到少量数据无限延长）。
        """
        parent = self.guard.deadline_remaining()
        return max(0.0, min(float(self.deadline_seconds) - (self.guard._clock() - self.started_at),
                            float(parent), self.guard.scope_time_remaining(self.scope)))

    def connect_timeout_seconds(self) -> float:
        return max(0.0, min(float(self.guard.policy.connect_timeout_seconds),
                            self.timeout_seconds()))

    def finish(self, actual_tokens: int | None, outcome: str = "ok") -> dict:
        """结算。**重复调用返回同一结果**，不会把额度记两遍。"""
        if self._settlement is not None:
            return self._settlement
        settlement = self.guard._settle(self, actual_tokens, outcome)
        self._settlement = settlement
        return settlement

    def as_dict(self) -> dict:
        return {"lease_id": self.lease_id, "scope": self.scope.as_dict(),
                "estimate_tokens": self.estimate_tokens, "attempt_index": self.attempt_index,
                "started_at": self.started_at, "deadline_seconds": self.deadline_seconds,
                "settled": self.settled, "fingerprint": self.fingerprint,
                "transport_attempt": self.transport_attempt}


class BudgetGuard:
    """线程安全的预算护栏。runner 在派发前与收尾时都调它。"""

    def __init__(self, policy: BudgetPolicy, *, clock=time.monotonic,
                 started_at: float | None = None) -> None:
        self.policy = policy
        self._clock = clock
        self._started = clock() if started_at is None else started_at
        self._guard_id = uuid.uuid4().hex
        self._historical_run_seconds = 0.0
        self._run_pause_started = None
        self._run_paused_seconds = 0.0
        self._lock = threading.RLock()
        self.usage = Usage()
        self._reserved = 0
        self._unknown_reserved = 0
        self._stopped = False
        self._stop_reason = ""
        self._historical_tokens = 0
        self._historical_unknown_events = 0
        self._restored_runs: set[str] = set()
        self._reported_usage: dict = {}
        #: scope 级累计 attempt：unit / stage / resource / transport
        self._attempts: dict[str, dict[str, int]] = {"unit": {}, "stage": {}, "resource": {},
                                                     "transport": {}}
        #: scope → 活跃区间（并集口径）
        self._active: dict[str, list[list[float]]] = {}
        self._historical_active: dict[str, float] = {}
        self._active_open: dict[str, float] = {}
        self._paused = False
        self._pause_reason = ""
        self._pause_reopen: list[str] = []
        self._in_flight: dict[str, CallLease] = {}
        self._lease_seq = 0
        self._heartbeats: dict[str, float] = {}
        self._active_scopes: dict[str, BudgetScope] = {}
        self._scope_committed: dict[str, int] = {}
        self._lease_active_counts: dict[str, int] = {}
        self._lease_active_owned: set[str] = set()
        self._persist = None

    def set_persistence(self, callback) -> None:
        self._persist = callback

    def persist(self) -> None:
        if self._persist:
            self._persist(self.snapshot())

    def _scope_chain(self, scope):
        chain = [BudgetScope(scope.run_id)]
        if scope.unit_id:
            chain.append(BudgetScope(scope.run_id, scope.unit_id))
        if scope.stage:
            chain.append(BudgetScope(scope.run_id, scope.unit_id, scope.stage))
        if scope.resource_id:
            chain.append(scope)
        return chain

    def _token_limits(self, scope):
        keys = self._counter_key(scope)
        return {f"{kind}:{key}": ((self.policy.max_tokens + 1) // 2
                                 if kind == "resource" else self.policy.max_tokens)
                for kind, key in keys.items() if kind != "transport"}

    def scope_time_remaining(self, scope):
        return min([self.deadline_remaining(), *[
            self.active_limit_for(part) - self.active_seconds(part)
            for part in self._scope_chain(scope)]])


    @contextmanager
    def work_scope(self, scope):
        """Account local computation and I/O as well as model time; no API attempt."""
        chain = self._scope_chain(scope)
        with self._lock:
            for part in chain:
                key = part.key()
                self._active_scopes[key] = part
                if key not in self._active_open:
                    self.begin_active(part)
                    self._lease_active_owned.add(key)
                self._lease_active_counts[key] = self._lease_active_counts.get(key, 0) + 1
        done = threading.Event()
        def beat():
            for part in chain:
                self.heartbeat(part)
            self.persist()
        def monitor():
            while not done.wait(min(5.0, max(.05, self.policy.heartbeat_seconds))):
                beat()
        beat()
        monitor_thread = threading.Thread(target=monitor, name='budget-heartbeat', daemon=True)
        monitor_thread.start()
        try:
            self.check()
            yield
            # Preserve a completed stage's returned artifacts even if it consumed
            # the last budget. Stop subsequent dispatch; never discard its result.
            reason = self.would_exceed()
            if reason:
                self.stop(reason)
        finally:
            done.set()
            monitor_thread.join(timeout=1)
            with self._lock:
                for part in chain:
                    key = part.key()
                    self._lease_active_counts[key] = max(0, self._lease_active_counts.get(key, 0) - 1)
                    if not self._lease_active_counts[key] and key in self._lease_active_owned:
                        self.end_active(part)
                        self._lease_active_owned.discard(key)
            beat()

    # ── 时间
    def elapsed_minutes(self) -> float:
        now = self._run_pause_started if self._run_pause_started is not None else self._clock()
        return (self._historical_run_seconds + max(0.0, now - self._started - self._run_paused_seconds)) / 60.0

    def deadline_remaining(self) -> float:
        """还剩多少秒。可能为负（已超时）—— 调用方据此给子进程设超时。"""
        return self.policy.max_minutes * 60.0 - self.elapsed_minutes() * 60.0

    def remaining_tokens(self) -> int:
        with self._lock:
            if self.policy.max_tokens <= 0:
                return 0
            committed = (self.usage.tokens + self._historical_tokens + self._reserved
                         + self._unknown_reserved)
            return max(0, self.policy.max_tokens - committed)

    # ── 判定
    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def stop_reason(self) -> str:
        return self._stop_reason

    @property
    def paused(self) -> bool:
        return self._paused

    def would_exceed(self, estimated_tokens: int = 0) -> str | None:
        """返回"会超"的原因（`None` 表示还能派发）。**不抛异常**，给调用方决定怎么处理。"""
        with self._lock:
            if self._stopped:
                return self._stop_reason
            used = self.usage.tokens + self._historical_tokens
            reserved, unknown = self._reserved, self._unknown_reserved
        for key, part in list(self._active_scopes.items()):
            if key in self._active_open and self.active_seconds(part) >= self.active_limit_for(part):
                return f"scope 活跃时间达到上限：{key}"
        if self.policy.max_tokens > 0:
            committed = used + reserved + unknown + max(0, int(estimated_tokens))
            if committed > self.policy.max_tokens or used + unknown >= self.policy.max_tokens:
                return (f"token 预算达到上限：已用 {used:,} + 在途 {reserved:,} "
                        f"+ 未知保留 {unknown:,} + 本次预估 {int(estimated_tokens):,} "
                        f"≥ 上限 {self.policy.max_tokens:,}")
        if self.deadline_remaining() <= 0:
            return (f"时间预算已超：已跑 {self.elapsed_minutes():.1f} 分钟 "
                    f"≥ 上限 {self.policy.max_minutes:.1f} 分钟")
        return None

    def check(self, estimated_tokens: int = 0) -> None:
        """会超就**抛**（runner 把它翻成结构化失败，而不是静默继续）。"""
        reason = self.would_exceed(estimated_tokens)
        if reason:
            self.stop(reason)
            raise BudgetExceeded(reason)

    # ── scope 级 attempt
    def _counter_key(self, scope: BudgetScope) -> dict[str, str]:
        """各层计数键。**空层不参与计数**。

        ⚠️ 这一条是测试当场抓出来的：第一版把空 `resource_id` 也算成一层，
        键退化成 `r1|`，于是**所有没有资源的调用共用同一个 resource 桶** ——
        跑到第 9 次就凭空撞上"资源尝试上限 8"。空层不是"一个叫空的资源"。
        """
        keys: dict[str, str] = {}
        if scope.unit_id:
            keys["unit"] = f"{scope.run_id}|{scope.unit_id}"
        if scope.stage:
            keys["stage"] = f"{scope.run_id}|{scope.unit_id}|{scope.stage}"
        if scope.resource_id:
            keys["resource"] = f"{scope.run_id}|{scope.unit_id}|{scope.resource_id}"
        keys["transport"] = scope.key()
        return keys

    def attempts_for(self, scope: BudgetScope) -> dict[str, int]:
        keys = self._counter_key(scope)
        with self._lock:
            return {name: (self._attempts[name].get(keys[name], 0) if name in keys else 0)
                    for name in ("unit", "stage", "resource")}

    def _transport_key(self, scope: BudgetScope, fingerprint: str = "") -> str:
        return f"{scope.key()}|request:{fingerprint}" if fingerprint else scope.key()

    def restore_transport_attempts(self, scope: BudgetScope, fingerprint: str, used: int) -> None:
        """Upgrade legacy per-call ledger evidence without charging other requests."""
        with self._lock:
            key = self._transport_key(scope, fingerprint)
            self._attempts["transport"][key] = max(self._attempts["transport"].get(key, 0), int(used))

    def transport_attempts_used(self, scope: BudgetScope, fingerprint: str = "") -> int:
        with self._lock:
            return self._attempts["transport"].get(self._transport_key(scope, fingerprint), 0)

    def transport_attempts_left(self, scope: BudgetScope, fingerprint: str = "") -> int:
        with self._lock:
            used = self._attempts["transport"].get(self._transport_key(scope, fingerprint), 0)
        return max(0, int(self.policy.attempt_limits["transport"]) - used)

    def _check_attempt_limits(self, scope: BudgetScope) -> None:
        keys = self._counter_key(scope)
        for name in ("unit", "stage", "resource"):
            if name not in keys:
                continue
            limit = int(self.policy.attempt_limits[name])
            used = self._attempts[name].get(keys[name], 0)
            if used >= limit:
                label = {"unit": f"单元 {scope.unit_id}", "stage": f"阶段 {scope.stage}",
                         "resource": f"资源 {scope.resource_id}"}[name]
                raise BudgetExceeded(
                    f"{label} 的 API 尝试次数已达上限 {limit}（已用 {used}）—— "
                    f"§6.1：该上限**累计**所有 attempt（初次／审核／证据修复／运输重试），"
                    f"不再派发；未过的标准与最后证据保留，转 needs_review")

    def _count_attempt(self, scope: BudgetScope) -> None:
        for name, key in self._counter_key(scope).items():
            self._attempts[name][key] = self._attempts[name].get(key, 0) + 1

    # ── 活跃时间（区间并集）
    def begin_active(self, scope: BudgetScope) -> None:
        with self._lock:
            self._active_open.setdefault(scope.key(), self._clock())

    def end_active(self, scope: BudgetScope) -> float:
        with self._lock:
            started = self._active_open.pop(scope.key(), None)
            if started is not None and self._clock() > started:
                self._active.setdefault(scope.key(), []).append([started, self._clock()])
            return self.active_seconds(scope)

    def active_seconds(self, scope: BudgetScope) -> float:
        with self._lock:
            intervals = [list(pair) for pair in self._active.get(scope.key(), [])]
            historical = self._historical_active.get(scope.key(), 0.0)
            started = self._active_open.get(scope.key())
            now = self._clock()
        if started is not None and now > started:
            intervals.append([started, now])
        return historical + self._interval_seconds(intervals)

    @staticmethod
    def _interval_seconds(intervals: list[list[float]]) -> float:
        if not intervals:
            return 0.0
        intervals.sort()
        total = 0.0
        current_start, current_end = intervals[0]
        for start, end in intervals[1:]:
            if start > current_end:
                total += current_end - current_start
                current_start, current_end = start, end
            else:
                current_end = max(current_end, end)
        total += current_end - current_start
        return total

    def active_limit_for(self, scope: BudgetScope) -> float:
        """该 scope 的时间上限，并**取父级剩余的更小者**（§6.1）。"""
        limit = float("inf")
        if scope.resource_id:
            limit = min(limit, float(self.policy.active_seconds["resource"]))
        elif scope.stage:
            limit = min(limit, float(self.policy.active_seconds["stage"]))
        elif scope.unit_id:
            limit = min(limit, float(self.policy.active_seconds["unit"]))
        return limit

    def pause(self, reason: str = "") -> None:
        """暂停：关掉所有打开着的活跃区间。暂停时间**不计**执行时间。"""
        with self._lock:
            if self._paused:
                return
            now = self._clock()
            self._pause_reopen = sorted(self._active_open)
            for key, started in list(self._active_open.items()):
                if now > started:
                    self._active.setdefault(key, []).append([started, now])
                self._active_open.pop(key, None)
            self._run_pause_started = now
            self._paused = True
            self._pause_reason = reason

    def resume(self) -> None:
        with self._lock:
            if not self._paused:
                return
            now = self._clock()
            for key in self._pause_reopen:
                self._active_open[key] = now
            self._pause_reopen = []
            if self._run_pause_started is not None:
                self._run_paused_seconds += max(0.0, now - self._run_pause_started)
                self._run_pause_started = None
            self._paused = False
            self._pause_reason = ""

    # ── 租约
    def acquire_call(self, scope: BudgetScope, estimate_tokens: int = 0, *,
                     fingerprint: str = "", transport_limit: int | None = None,
                     media_review_resume: bool = False) -> CallLease:
        """**唯一**的调用入口：先判额度与 attempt，再原子占住额度，返回租约。"""
        with self._lock:
            reason = self.would_exceed(estimate_tokens)
            if reason:
                self.stop(reason)
                raise BudgetExceeded(reason)
            if self.scope_time_remaining(scope) <= 0:
                raise BudgetExceeded(f"scope 活跃时间达到上限：{scope.key()}")
            for key, limit in self._token_limits(scope).items():
                if limit > 0 and self._scope_committed.get(key, 0) + estimate_tokens > limit:
                    raise BudgetExceeded(f"scope token 预算达到上限：{key} / {limit}")
            self._check_attempt_limits(scope)
            if fingerprint:
                limit = int(self.policy.attempt_limits["transport"])
                if media_review_resume:
                    limit = min(4, limit * 2)
                if transport_limit is not None:
                    limit = min(limit, int(transport_limit))
                if self._attempts["transport"].get(self._transport_key(scope, fingerprint), 0) >= limit:
                    raise BudgetExceeded(f"逻辑请求的传输尝试次数已达上限 {limit}：{fingerprint}")
            self._count_attempt(scope)
            transport_attempt = 0
            if fingerprint:
                key = self._transport_key(scope, fingerprint)
                transport_attempt = self._attempts["transport"].get(key, 0) + 1
                self._attempts["transport"][key] = transport_attempt
            for key in self._token_limits(scope):
                self._scope_committed[key] = self._scope_committed.get(key, 0) + max(0, estimate_tokens)
            for part in self._scope_chain(scope):
                key = part.key()
                if key not in self._active_open:
                    self.begin_active(part)
                    self._lease_active_owned.add(key)
                self._lease_active_counts[key] = self._lease_active_counts.get(key, 0) + 1
            self._reserved += max(0, int(estimate_tokens))
            self._lease_seq += 1
            lease = CallLease(
                guard=self, scope=scope, estimate_tokens=max(0, int(estimate_tokens)),
                lease_id=f"{scope.key()}#{self._lease_seq}", attempt_index=self._lease_seq,
                started_at=self._clock(),
                deadline_seconds=float(self.policy.api_deadline_seconds),
                fingerprint=fingerprint, transport_attempt=transport_attempt)
            self._in_flight[lease.lease_id] = lease
            self.persist()
            return lease

    def _settle(self, lease: CallLease, actual_tokens: int | None, outcome: str) -> dict:
        with self._lock:
            self._reserved = max(0, self._reserved - lease.estimate_tokens)
            self.usage.calls += 1
            self.usage.api_calls += 1
            if actual_tokens is None:
                self.usage.unknown_events += 1
                self._unknown_reserved += lease.estimate_tokens
            else:
                self.usage.add(int(actual_tokens), lease.scope.stage or "other")
            if outcome in ("retry", "transport-retry"):
                self.usage.retries += 1
            self._in_flight.pop(lease.lease_id, None)
            if actual_tokens is not None:
                for key in self._token_limits(lease.scope):
                    self._scope_committed[key] = max(0, self._scope_committed.get(key, 0)
                                                     - lease.estimate_tokens + actual_tokens)
            for part in self._scope_chain(lease.scope):
                key = part.key()
                self._lease_active_counts[key] = max(0, self._lease_active_counts.get(key, 0) - 1)
                if not self._lease_active_counts[key] and key in self._lease_active_owned:
                    self.end_active(part)
                    self._lease_active_owned.discard(key)
            settlement = {"lease_id": lease.lease_id, "scope": lease.scope.key(),
                          "outcome": outcome, "tokens": actual_tokens,
                          "unknown": actual_tokens is None,
                          "estimate_tokens": lease.estimate_tokens,
                          "at": self._clock()}
        self.persist()
        return settlement

    def in_flight_leases(self) -> list[dict]:
        with self._lock:
            return [lease.as_dict() for lease in self._in_flight.values()]

    # ── 兼容旧接口（runner／gateway 已经在用）
    def reserve(self, estimated_tokens: int) -> None:
        with self._lock:
            reason = self.would_exceed(estimated_tokens)
            if reason:
                self.stop(reason)
                raise BudgetExceeded(reason)
            self._reserved += max(0, int(estimated_tokens))

    def release(self, estimated_tokens: int, actual_tokens: int | None = None,
                bucket: str = "other") -> None:
        """阶段结束：保留旧服务的消耗结算，但不把阶段释放冒充模型请求。"""
        with self._lock:
            self._reserved = max(0, self._reserved - max(0, int(estimated_tokens)))
            self.usage.calls += 1
            self.usage.stage_releases += 1
            if actual_tokens is None:
                self.usage.unknown_events += 1
                self._unknown_reserved += max(0, int(estimated_tokens))
            else:
                self.usage.add(actual_tokens, bucket)

    def record(self, tokens: int, bucket: str = "other") -> None:
        with self._lock:
            self.usage.add(tokens, bucket)

    def record_cached(self) -> None:
        with self._lock:
            self.usage.cached_hits += 1
            self.usage.calls += 1

    def record_retry(self) -> None:
        with self._lock:
            self.usage.retries += 1

    # ── 心跳
    def heartbeat(self, scope: BudgetScope, *, rounds: dict | None = None,
                  note: str = "") -> dict:
        """活跃操作的进度心跳。**不含密钥、不含完整敏感请求**（§6.1）。"""
        beat = {
            "scope": scope.key(),
            "at": self._clock(),
            "elapsed_seconds": round(self.active_seconds(scope), 3),
            "parent_deadline_seconds": round(self.deadline_remaining(), 3),
            "deadline_seconds": round(self.active_limit_for(scope), 3),
            "remaining_tokens": self.remaining_tokens(),
            "remaining_attempts": self.attempts_remaining(scope),
            "rounds": dict(rounds or {}),
            "paused": self._paused,
            "note": note,
        }
        with self._lock:
            self._heartbeats[scope.key()] = beat["at"]
        return beat

    def attempts_remaining(self, scope: BudgetScope) -> dict[str, int]:
        keys = self._counter_key(scope)
        with self._lock:
            return {name: (max(0, int(self.policy.attempt_limits[name])
                              - self._attempts[name].get(keys[name], 0))
                           if name in keys else int(self.policy.attempt_limits[name]))
                    for name in ("unit", "stage", "resource", "transport")}

    def heartbeat_staleness(self, scope: BudgetScope) -> float | None:
        with self._lock:
            last = self._heartbeats.get(scope.key())
        if last is None:
            return None
        return max(0.0, self._clock() - last)

    # ── 恢复
    def restore_usage_once(self, run_id: str, *, tokens: int = 0,
                           unknown_reserved: int = 0, unknown_events: int = 0) -> bool:
        """Carry persisted expenditure into admission checks once per guard/run."""
        with self._lock:
            key = f"{run_id}#ledger"
            if key in self._restored_runs:
                return False
            self._restored_runs.add(key)
            self._historical_tokens += max(0, int(tokens))
            self._historical_unknown_events += max(0, int(unknown_events))
            self._unknown_reserved += max(0, int(unknown_reserved))
            self._reported_usage["unknown_reserved_tokens"] = self._unknown_reserved
            return True

    def record_abandoned_leases(self, leases, *, reason: str = "崩溃时在途的租约") -> int:
        """把崩溃时**在途**的租约按未知费用保守保留。返回保留的 token 数。

        这些调用已经发出去了，只是我们没等到响应 —— 填 0 就是假装没花钱。
        """
        retained = 0
        with self._lock:
            for entry in leases or ():
                lease_key = f"abandoned:{entry.get('lease_id') or digest_of(entry)}"
                if lease_key in self._restored_runs:
                    continue
                self._restored_runs.add(lease_key)
                estimate = max(0, int((entry or {}).get("estimate_tokens") or 0))
                retained += estimate
                self._unknown_reserved += estimate
                self.usage.unknown_events += 1
        return retained

    def restore_from_snapshot(self, run_id: str, snapshot: dict, *,
                              restore_spend: bool = True) -> bool:
        """把上一段的账恢复进来。**同一 run 只恢复一次**（§6.1：两次 resume 计数不变）。

        崩溃时在途的租约按**未知费用**保守保留，并按"最后心跳 + 租约上限"结算崩溃区间 ——
        不凭重启归零，也不因为重启延迟把时长越算越大。

        `restore_spend=False` 供 runner 使用：花费以 `usage.jsonl` 账本为准（那里有逐行
        依据），这里只恢复 **scope 级状态**（attempt 计数、活跃区间、心跳），
        避免同一笔钱在两条恢复路径上各记一遍。
        """
        with self._lock:
            key = f"{run_id}#scope" if not restore_spend else f"{run_id}#scope-spend"
            if key in self._restored_runs:
                return False
            self._restored_runs.add(key)
            time_key = f"{run_id}#run-time"
            if time_key not in self._restored_runs and snapshot.get("guard_id") != self._guard_id:
                self._restored_runs.add(time_key)
                self._historical_run_seconds += max(0.0, float(snapshot.get("run_elapsed_seconds",
                    float(snapshot.get("elapsed_minutes") or 0) * 60)))
            usage = snapshot.get("usage") or {}
            if restore_spend:
                self._historical_tokens += max(0, int(usage.get("total_tokens", usage.get("tokens")) or 0))
                self._historical_unknown_events += max(0, int(usage.get("unknown_events") or 0))
                self._unknown_reserved += max(0, int(snapshot.get("unknown_reserved_tokens") or 0))
                self.usage.unknown_events += max(0, int(usage.get("unknown_events") or 0))
                self.usage.retries += max(0, int(usage.get("retries") or 0))
            for token_key, value in (snapshot.get("scope_committed") or {}).items():
                self._scope_committed[token_key] = max(self._scope_committed.get(token_key, 0), int(value))
            self._lease_seq = max(self._lease_seq, int(snapshot.get("lease_seq") or 0))
            for name, counters in (snapshot.get("attempts") or {}).items():
                if name not in self._attempts:
                    continue
                for counter_key, value in (counters or {}).items():
                    self._attempts[name][counter_key] = max(
                        self._attempts[name].get(counter_key, 0), int(value))
            # Monotonic timestamps belong to the original process/host epoch.
            # Reduce its intervals to durations before adding this process's work.
            prior_intervals: dict[str, list[list[float]]] = {}
            for scope_key, intervals in (snapshot.get("active_intervals") or {}).items():
                prior_intervals[scope_key] = [[float(pair[0]), float(pair[1])]
                    for pair in intervals or () if len(pair) == 2 and float(pair[1]) > float(pair[0])]
            heartbeats = snapshot.get("heartbeats") or {}
            for key, started in (snapshot.get("active_open") or {}).items():
                started = float(started)
                end = float(heartbeats.get(key, started)) + min(5, self.policy.heartbeat_seconds)
                if end > started:
                    prior_intervals.setdefault(key, []).append([started, end])
            for entry in snapshot.get("in_flight_leases") or ():
                estimate = max(0, int(entry.get("estimate_tokens") or 0))
                if restore_spend:
                    self._unknown_reserved += estimate
                    self.usage.unknown_events += 1
                scope_key = str((entry.get("scope") or {}).get("key") or "")
                if not scope_key:
                    continue
                started = float(entry.get("started_at") or 0.0)
                window = float(entry.get("deadline_seconds") or 0.0)
                last_beat = float(heartbeats.get(scope_key, started))
                end = last_beat + max(0.0, window)
                if end > started:
                    raw_scope = entry.get("scope") or {}
                    scope = BudgetScope(str(raw_scope.get("run_id") or run_id),
                        str(raw_scope.get("unit_id") or ""), str(raw_scope.get("stage") or ""),
                        str(raw_scope.get("resource_id") or ""))
                    for part in self._scope_chain(scope):
                        prior_intervals.setdefault(part.key(), []).append([started, end])
            if snapshot.get("guard_id") != self._guard_id:
                history = snapshot.get("historical_active_seconds") or {}
                for scope_key in set(prior_intervals) | set(history):
                    self._historical_active[scope_key] = (
                        self._historical_active.get(scope_key, 0.0)
                        + max(0.0, float(history.get(scope_key) or 0))
                        + self._interval_seconds(prior_intervals.get(scope_key, [])))
            self._reported_usage["unknown_reserved_tokens"] = self._unknown_reserved
            return True

    def usage_delta(self, usage: dict) -> dict:
        """A shared guard can finish preparation and generation separately."""
        with self._lock:
            previous = self._reported_usage
            keys = ("tokens", "unknown_events", "cached_hits", "calls", "retries",
                    "unknown_reserved_tokens", "api_calls", "stage_releases")
            delta = {key: max(0, int(usage.get(key) or 0) - int(previous.get(key) or 0))
                     for key in keys}
            delta["by_bucket"] = {
                bucket: max(0, amount - (previous.get("by_bucket") or {}).get(bucket, 0))
                for bucket, amount in (usage.get("by_bucket") or {}).items()}
            self._reported_usage = dict(usage)
            return delta

    # ── 停止
    def stop(self, reason: str) -> None:
        with self._lock:
            if not self._stopped:
                self._stopped = True
                self._stop_reason = reason

    def snapshot(self) -> dict:
        with self._lock:
            reserved, usage = self._reserved, self.usage.as_dict()
            unknown_reserved = self._unknown_reserved
            usage.update(historical_tokens=self._historical_tokens,
                         total_tokens=self._historical_tokens + self.usage.tokens,
                         historical_unknown_events=self._historical_unknown_events,
                         unknown_reserved_tokens=unknown_reserved)
            return {"policy": self.policy.as_dict(),
                    "guard_id": self._guard_id,
                    "policy_digest": self.policy.digest(),
                    "elapsed_minutes": round(self.elapsed_minutes(), 3),
                    "run_elapsed_seconds": self.elapsed_minutes() * 60.0,
                    "in_flight_reserved_tokens": reserved,
                    "unknown_reserved_tokens": unknown_reserved,
                    "usage": usage,
                    "attempts": {name: dict(counters)
                                 for name, counters in self._attempts.items()},
                    "scope_committed": dict(self._scope_committed),
                    "lease_seq": self._lease_seq,
                    "active_open": dict(self._active_open),
                    "historical_active_seconds": dict(self._historical_active),
                    "active_intervals": {key: [list(p) for p in pairs]
                                         for key, pairs in self._active.items()},
                    "in_flight_leases": [lease.as_dict()
                                         for lease in self._in_flight.values()],
                    "heartbeats": dict(self._heartbeats),
                    "stopped": self._stopped, "stop_reason": self._stop_reason,
                    "drain_seconds": self.policy.drain_seconds}


__all__ = ["DRAIN_SECONDS_DEFAULT", "REPAIR_POLICY_NAME", "REPAIR_POLICY_VERSION",
           "DEFAULT_ATTEMPT_LIMITS", "DEFAULT_ACTIVE_SECONDS", "DEFAULT_RENDER_LIMITS",
           "BudgetPolicy", "BudgetScope", "CallLease", "BudgetGuard", "Usage",
           "load_policy", "load_repair_policy"]
