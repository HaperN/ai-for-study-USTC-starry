"""Production execution limits and durable attempts; no usage or cumulative budget.

The run's BookLease owns cross-process exclusivity. A shared controller serializes
threads, fsyncs dispatch records before I/O, and never refunds interrupted calls.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid

from ..core.digest import digest_of
from ..errors import CapabilityError, Cancelled, ConfigError, ContractError
from .network_gate import NETWORK_RETRY, NETWORK_STATES, NetworkGate

# A charged dispatch may finish after the owner recorded an interruption.
INTERRUPTED_RESPONSE_STATES = frozenset({'interrupted', 'transport_failed', 'cancelled'})
TERMINAL_RESPONSE_STATES = frozenset({'response_received', 'protocol_failed'})
RECOVERABLE_RESPONSE_STATES = INTERRUPTED_RESPONSE_STATES | TERMINAL_RESPONSE_STATES

MAX_PARALLELISM = 32
DEFAULT_ATTEMPT_LIMITS = {'transport': 2}
DEFAULT_RENDER_LIMITS = {'draft_seconds':120.0, 'final_seconds':300.0,
    'cpu_cores':2, 'memory_bytes':2*1024**3, 'output_bytes':128*1024**2,
    'max_concurrent_workers':1}


def remaining_cancel_grace(seconds, cancel=None):
    """One cancellation window, measured from the original stop request."""
    requested = getattr(cancel, 'cancel_requested_at', None)
    elapsed = 0.0 if requested is None else max(0.0, time.monotonic() - requested)
    return max(0.0, float(seconds) - elapsed)


class AttemptLimitExceeded(CapabilityError):
    """This logical request is exhausted; unrelated objects may continue."""


#: 修改计划 E7：并发字段不属于"续跑必须一致"的策略身份；停止后续跑可改并发并留交接记录。
CONCURRENCY_FIELDS = ('parallelism', 'media_resource_parallelism', 'content_parallelism', 'media_api_parallelism')


def concurrency_neutral(policy):
    return {key: value for key, value in (policy or {}).items() if key not in CONCURRENCY_FIELDS}


@dataclass
class ExecutionPolicy:
    parallelism: int = 2
    media_resource_parallelism: int = 3
    # Optional split of the total API gate. Text has cross-unit dependencies
    # (later units only see already-reviewed theorem names), media does not.
    # None keeps the old split: text may use all slots, media all but one.
    content_parallelism: int | None = None
    media_api_parallelism: int | None = None
    api_deadline_seconds: float | None = 1000.0  # null disables the run deadline
    call_timeout_seconds: float = 900.0
    connect_timeout_seconds: float = 15.0
    cancel_grace_seconds: float = 10.0
    drain_seconds: float = 30.0
    heartbeat_seconds: float = 5.0
    content_repair_rounds_per_object: int = 2
    evidence_repairs_per_verdict: int = 1
    attempt_limits: dict = field(default_factory=lambda:dict(DEFAULT_ATTEMPT_LIMITS))
    render: dict = field(default_factory=lambda:dict(DEFAULT_RENDER_LIMITS))
    allow_real_services: bool = True
    release_ready_possible: bool = True
    policy_version: int = 2
    policy_source: str = 'builtin-defaults'

    def __post_init__(self):
        if type(self.parallelism) is not int or not 1 <= self.parallelism <= MAX_PARALLELISM:
            raise ConfigError(f'parallelism 必须在 1..{MAX_PARALLELISM}')
        if type(self.media_resource_parallelism) is not int or self.media_resource_parallelism < 1:
            raise ConfigError('media_resource_parallelism 必须为正整数')
        for name in ('content_parallelism', 'media_api_parallelism'):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or not 1 <= value <= self.parallelism):
                raise ConfigError(f'{name} 必须为空或在 1..parallelism')
        if self.policy_version != 2: raise ConfigError('执行策略版本必须为2')
        for name in ('api_deadline_seconds','call_timeout_seconds','connect_timeout_seconds','cancel_grace_seconds',
                     'drain_seconds','heartbeat_seconds'):
            raw=getattr(self,name)
            if name == 'api_deadline_seconds' and raw is None:
                continue
            try: value=float(raw)
            except (TypeError,ValueError) as exc: raise ConfigError(f'{name} 必须为有限正数') from exc
            if isinstance(raw,bool) or not math.isfinite(value) or value <= 0: raise ConfigError(f'{name} 必须为有限正数')
        self.attempt_limits={**DEFAULT_ATTEMPT_LIMITS,**self.attempt_limits}
        if (set(self.attempt_limits) != {'transport'} or type(self.attempt_limits['transport']) is not int
                or not 1 <= self.attempt_limits['transport'] <= 2):
            raise ConfigError('transport 必须为 1..2；内容返修按对象单独计数')
        if (type(self.content_repair_rounds_per_object) is not int
                or self.content_repair_rounds_per_object not in (2, 6)
                or self.evidence_repairs_per_verdict != 1):
            raise ConfigError('内容返修最多2轮（兼容旧运行6轮）；单判词证据修复最多1轮')
        self.render={**DEFAULT_RENDER_LIMITS,**self.render}

    def as_dict(self):
        # Unset split limits are omitted so policies written before they existed
        # keep the same digest and their runs can still resume.
        return {key:deepcopy(value) for key,value in vars(self).items() if key!='policy_source'
                and not (key in ('content_parallelism','media_api_parallelism') and value is None)}

    @property
    def content_slots(self):
        return self.content_parallelism or self.parallelism

    @property
    def media_api_slots(self):
        return self.media_api_parallelism or max(1, self.parallelism - 1)

    def clamped(self, parallelism):
        """Lower the total gate (e.g. a smaller --parallel) without breaking the split."""
        parallelism = min(parallelism, self.parallelism)
        clamp = lambda value: None if value is None else min(value, parallelism)
        return replace(self, parallelism=parallelism,
                       content_parallelism=clamp(self.content_parallelism),
                       media_api_parallelism=clamp(self.media_api_parallelism))

    def digest(self): return digest_of(self.as_dict())

    @property
    def policy_digest(self): return self.digest()


def load_policy(workspace, profile, goal='deliver'):
    cfg=workspace.config_json('execution.json')
    profiles=cfg.get('profiles') or {}
    if profile not in profiles: raise ConfigError(f'执行配置缺档位 {profile!r}')
    values={key:value for key,value in cfg.items() if key in ExecutionPolicy.__dataclass_fields__}
    values.update(profiles[profile])
    from ..core.review_mode import configured_review_mode
    if configured_review_mode(workspace) == 'balanced':
        values['content_repair_rounds_per_object'] = 2
    return ExecutionPolicy(**values,policy_source=str(workspace.config/'execution.json'))


@dataclass(frozen=True)
class ExecutionScope:
    run_id: str
    unit_id: str = ''
    stage: str = ''
    resource_id: str = ''

    def __post_init__(self):
        if not isinstance(self.run_id,str) or not self.run_id.strip(): raise ContractError('ExecutionScope.run_id 必填')
        if any(not isinstance(v,str) for v in vars(self).values()): raise ContractError('执行scope字段必须为字符串')

    def key(self): return digest_of(self.as_dict(),check=False)
    def as_dict(self): return vars(self).copy()
    def with_unit(self,unit_id):
        return ExecutionScope(self.run_id,unit_id,self.stage,self.resource_id)


@dataclass
class CallAttempt:
    control: object
    lease_id: str
    fingerprint: str
    transport_attempt: int
    started_at: float
    deadline_seconds: float | None
    closed: bool = False
    recovered: bool = False

    def timeout_seconds(self):
        if self.deadline_seconds is None:
            return None
        return max(0.0,self.deadline_seconds-(self.control.clock()-self.started_at))

    def finish(self, outcome='completed', *, error=''):
        if not self.closed:
            if self.recovered:
                self.control.finish_recovered(self.lease_id, outcome, error=error)
            else:
                self.control.finish(self.lease_id,outcome,error=error)
            self.closed=True


class ExecutionControl:
    def __init__(self,policy=None,*,clock=time.monotonic):
        self.policy=policy or ExecutionPolicy()
        self.clock=clock
        self._lock=threading.RLock()
        self._slots=threading.BoundedSemaphore(self.policy.parallelism)
        # Resource workers share this run-wide gate. A waiting media call must
        # not take the general API slot needed by prose generation/review.
        self._media_slots=threading.BoundedSemaphore(self.policy.media_api_slots)
        self._content_slots=threading.BoundedSemaphore(self.policy.content_slots)
        # 修改计划 E3/E5（并入 G1）：各通道实际在途数与等待原因，只计数、不改调度。
        self._lane_waiting={'content':0,'media_api':0}; self._total_waiting={'content':0,'media_api':0}
        self._lane_active={'content':0,'media_api':0}
        self.path=None
        self._bound=False
        self.run_id=''
        self._attempts=[]
        self.stop_reason=''
        self.stop_requested_at=None
        self._run_started_clock = None
        self._run_started_wall = None
        # Run-wide outage gate (plan N). Real transports install a probe; without
        # one the gateway keeps the bounded transport retry.
        self.network_gate = NetworkGate()

    def bind(self,path,*,run_id,require_existing=False):
        with self._lock:
            if not isinstance(run_id,str) or not run_id.strip(): raise ContractError('执行记录run_id必填')
            path=Path(path)
            if require_existing and not path.is_file():
                raise ContractError("恢复缺持久执行记录；禁止重置尝试次数")
            if self.path is not None:
                if not self._bound: raise ContractError("执行记录绑定失败；禁止派发")
                if self.path != path or self.run_id != run_id: raise ContractError('执行控制不可跨运行重绑')
                return
            self.path=path; self.run_id=run_id
            self._run_started_clock = self.clock()
            self._run_started_wall = time.time()
            if path.exists():
                try: data=json.loads(path.read_text(encoding='utf-8'))
                except (ValueError,OSError) as exc: raise ContractError('执行记录损坏；禁止重置尝试次数') from exc
                saved_policy = data.get('policy')
                if (require_existing and isinstance(saved_policy, dict)
                        and data.get('policy_digest') == digest_of(saved_policy)):
                    # New defaults must not shorten an existing run's allowance.
                    # Other execution-policy changes still fail the check below.
                    self.policy = replace(self.policy, content_repair_rounds_per_object=
                        saved_policy.get('content_repair_rounds_per_object',
                                         self.policy.content_repair_rounds_per_object))
                handoffs=deepcopy(data.get('concurrency_handoffs') or [])
                if (require_existing and isinstance(saved_policy, dict)
                        and data.get('policy_digest') == digest_of(saved_policy)
                        and data.get('policy_digest') != self.policy.digest()
                        and digest_of(concurrency_neutral(saved_policy))
                            == digest_of(concurrency_neutral(self.policy.as_dict()))):
                    # 修改计划 E7：只有并发变了。写交接记录（旧值、新值、时间），全部尝试账与返修账保留。
                    handoffs.append({'at':time.time(),
                        'from':{k:saved_policy.get(k) for k in CONCURRENCY_FIELDS if k in saved_policy},
                        'to':{k:v for k,v in self.policy.as_dict().items() if k in CONCURRENCY_FIELDS}})
                    data['policy_digest']=self.policy.digest()
                if (data.get('schema_version')!='ExecutionControlV1' or data.get('run_id')!=run_id
                        or data.get('policy_digest')!=self.policy.digest()):
                    raise ContractError('执行记录身份或策略变化；禁止静默重置尝试次数')
                self._handoffs=handoffs
                attempts=data.get('attempts')
                if not isinstance(attempts,list) or any(not isinstance(a,dict) or
                    not all(k in a for k in ('attempt_id','fingerprint','scope','state','attempt')) for a in attempts):
                    raise ContractError('执行尝试记录不完整；禁止恢复')
                sequences={}
                identifiers=set()
                for attempt in attempts:
                    scope=attempt['scope']
                    if (not isinstance(scope,dict) or set(scope)!=set(ExecutionScope.__dataclass_fields__)
                            or scope.get('run_id')!=run_id
                            or any(not isinstance(v,str) for v in scope.values())
                            or not isinstance(attempt['fingerprint'],str) or not attempt['fingerprint']
                            or not isinstance(attempt['attempt_id'],str) or not attempt['attempt_id']
                            or attempt['attempt_id'] in identifiers
                            or attempt['state'] not in {'dispatched','interrupted','transport_failed',
                                'protocol_failed','cancelled','blocked','failed','response_received','completed',
                                *NETWORK_STATES}):
                        raise ContractError('执行尝试记录身份或状态损坏；禁止恢复')
                    identifiers.add(attempt['attempt_id'])
                    if attempt['state'] in NETWORK_STATES:
                        continue  # network waits are recorded but never spend a transport attempt
                    key=digest_of([scope,attempt['fingerprint']],check=False)
                    expected=sequences.get(key,0)+1
                    if type(attempt['attempt']) is not int or attempt['attempt']!=expected or expected>self.policy.attempt_limits['transport']:
                        raise ContractError('执行尝试序号损坏；禁止恢复')
                    sequences[key]=expected
                self._attempts=deepcopy(attempts)
                started = data.get('run_started_at')
                valid_started = isinstance(started, (int, float)) and not isinstance(started, bool) and math.isfinite(started)
                if valid_started:
                    self._run_started_wall = started
                if self.policy.api_deadline_seconds is not None:
                    if not valid_started:
                        raise ContractError('恢复缺整轮期限起点；禁止重置时限')
                    self._run_started_clock -= max(0.0, time.time() - started)
                for a in self._attempts:
                    if a['state']=='dispatched': a['state']='interrupted'
            self._persist()
            self._bound=True

    def channel_status(self):
        """修改计划 E3/E5：各通道在途与等待（通道满 / 等总闸），以及尝试账的分类统计。"""
        with self._lock:
            channels = {name: {'limit': limit, 'active': self._lane_active[name],
                               'waiting_for_channel': self._lane_waiting[name],
                               'waiting_for_total': self._total_waiting[name]}
                        for name, limit in (('content', self.policy.content_slots),
                                            ('media_api', self.policy.media_api_slots))}
            states = {}
            for attempt in self._attempts:
                states[attempt['state']] = states.get(attempt['state'], 0) + 1
            return {'channels': channels,
                    'total': {'limit': self.policy.parallelism,
                              'active': sum(self._lane_active.values())},
                    'attempts': {'dispatched': states.get('dispatched', 0),
                                 'replied': states.get('response_received', 0) + states.get('completed', 0),
                                 'failed': {k: v for k, v in states.items() if k in
                                            ('transport_failed', 'protocol_failed', 'failed', 'blocked', 'interrupted')},
                                 'network_waits': states.get('network_wait', 0) + states.get('network_retry', 0),
                                 'cancelled': states.get('cancelled', 0)}}

    def snapshot(self):
        with self._lock:
            snapshot={'schema_version':'ExecutionControlV1','run_id':self.run_id,
                'policy_digest':self.policy.digest(),'policy':self.policy.as_dict(),
                'attempts':deepcopy(self._attempts), 'run_started_at':self._run_started_wall}
            if getattr(self,'_handoffs',None):
                snapshot['concurrency_handoffs']=deepcopy(self._handoffs)
            return snapshot

    def _persist(self):
        if self.path is None: return
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_name(self.path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            with tmp.open('w',encoding='utf-8') as stream:
                json.dump(self.snapshot(),stream,ensure_ascii=False,allow_nan=False)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(tmp,self.path)
            fd=os.open(self.path.parent,os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            if tmp.exists(): tmp.unlink()

    def check(self):
        if self.stop_reason: raise Cancelled(self.stop_reason)
        remaining = self.run_timeout_seconds()
        if remaining is not None and remaining <= 0:
            raise TimeoutError('整轮 API deadline 已耗尽；禁止派发新的调用')

    def run_timeout_seconds(self):
        if self.policy.api_deadline_seconds is None or self._run_started_clock is None:
            return None
        return max(0.0, self.policy.api_deadline_seconds - (self.clock() - self._run_started_clock))

    def stop(self,reason):
        if self.stop_requested_at is None: self.stop_requested_at=time.monotonic()
        self.stop_reason=str(reason)

    @contextmanager
    def work_scope(self,scope):
        self.check()
        yield
        self.check()

    def transport_attempts_used(self,scope,fingerprint):
        with self._lock:
            return sum(a['scope']==scope.as_dict() and a['fingerprint']==fingerprint
                       and a['state'] not in NETWORK_STATES for a in self._attempts)

    def request_network_failures(self,scope,fingerprint):
        """Failures of this request while the network probe was healthy (plan N4)."""
        with self._lock:
            return sum(a['scope']==scope.as_dict() and a['fingerprint']==fingerprint
                       and a['state']==NETWORK_RETRY for a in self._attempts)

    def acquire_call(self,scope,*,fingerprint,cancel=None):
        def checkpoint():
            if cancel is not None: cancel.checkpoint()
            self.check()
        checkpoint()
        lane_acquired = total_acquired = False
        lane = self._media_slots if scope.stage == 'figures' else self._content_slots
        name = 'media_api' if scope.stage == 'figures' else 'content'
        try:
            with self._lock: self._lane_waiting[name]+=1
            try:
                while not lane.acquire(timeout=.05): checkpoint()
            finally:
                with self._lock: self._lane_waiting[name]-=1
            lane_acquired = True
            with self._lock: self._total_waiting[name]+=1
            try:
                while not self._slots.acquire(timeout=.05): checkpoint()
            finally:
                with self._lock: self._total_waiting[name]-=1
            total_acquired = True
            with self._lock:
                checkpoint()
                if not self._bound: raise ContractError('派发前必须绑定持久化 execution.json')
                if scope.run_id != self.run_id: raise ContractError('执行尝试的 run_id 不一致')
                used=self.transport_attempts_used(scope,fingerprint)
                if used >= self.policy.attempt_limits['transport']:
                    raise AttemptLimitExceeded(f'逻辑请求运输尝试已达上限 {used}；重启不重置')
                ident=uuid.uuid4().hex
                self._attempts.append({'attempt_id':ident,'fingerprint':fingerprint,
                    'scope':scope.as_dict(),'attempt':used+1,'state':'dispatched','at':time.time()})
                self._persist()  # must succeed before the transport can be invoked
                self._lane_active[name]+=1
                remaining = self.run_timeout_seconds()
                timeout = self.policy.call_timeout_seconds
                if remaining is not None:
                    timeout = min(timeout, remaining)
                return CallAttempt(self,ident,fingerprint,used+1,self.clock(),timeout)
        except BaseException:
            if total_acquired: self._slots.release()
            if lane_acquired: lane.release()
            raise

    def finish_recovered(self, ident, outcome, *, error=''):
        # This dispatch predates the current process and holds no semaphore.
        with self._lock:
            attempt = next(a for a in self._attempts if a['attempt_id'] == ident)
            if attempt['state'] == outcome and attempt.get('error', '') == error:
                return
            if attempt['state'] not in INTERRUPTED_RESPONSE_STATES or outcome not in TERMINAL_RESPONSE_STATES:
                raise ContractError('Recovered response has no matching completed dispatch')
            attempt.update(state=outcome, error=str(error), finished_at=time.time())
            self._persist()

    def finish(self,ident,outcome,*,error=''):
        lane = None
        try:
            with self._lock:
                attempt=next(a for a in self._attempts if a['attempt_id']==ident)
                lane = self._media_slots if attempt['scope']['stage'] == 'figures' else self._content_slots
                name = 'media_api' if attempt['scope']['stage'] == 'figures' else 'content'
                self._lane_active[name]=max(0,self._lane_active[name]-1)
                attempt.update(state=outcome,error=str(error),finished_at=time.time())
                self._persist()
        finally:
            self._slots.release()
            if lane is not None: lane.release()
