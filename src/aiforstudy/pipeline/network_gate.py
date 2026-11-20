"""Run-wide network outage gate: wait for connectivity instead of spending attempts.

用户 2026-10-20 规则（修改计划 N 节）：断网时整轮暂停派发，按退避探测网络，
恢复后原请求重发；等待期间的失败不占传输、候选、纠正或返修额度。连续断网
超过 2 小时整轮安全暂停，可 resume。网络正常而同一请求连续 3 次失败，才判
该请求传输失败。

没有探测器（离线替身传输）时网关保持原来的有界重试；只有真实传输带探测器。
"""
from __future__ import annotations

import threading
import time

from ..errors import Cancelled

#: 探测退避：第 1 次等 10 秒，之后逐步拉长，最长每 5 分钟一次。
PROBE_SCHEDULE_SECONDS = (10.0, 30.0, 60.0, 120.0, 300.0)
#: 连续断网超过这一时长，整轮安全暂停（可 resume，额度不变）。
PAUSE_AFTER_SECONDS = 2 * 3600.0
#: 网络正常时同一请求连续失败的上限（含限流 429），达到即判该请求传输失败。
REQUEST_FAILURE_LIMIT = 3
#: 限流 429 的单请求退避，不触发全局暂停。
RATE_LIMIT_BACKOFF_SECONDS = (60.0, 120.0, 300.0)
#: 网络正常时同一请求失败后的短等待。
REQUEST_RETRY_DELAY_SECONDS = 5.0

NETWORK_PAUSE_REASON = 'network_paused'
#: 执行记录中不计入运输次数的尝试状态。
NETWORK_WAIT = 'network_wait'      # 探测确认断网：不计任何额度
NETWORK_RETRY = 'network_retry'    # 网络正常而本请求失败：只计单请求连续失败上限
NETWORK_STATES = frozenset({NETWORK_WAIT, NETWORK_RETRY})


class NetworkPaused(Cancelled):
    """连续断网超过上限；整轮按取消路径安全停止，记录保持可续。"""


class NetworkGate:
    """所有调用线程共用一个门：一个线程负责探测，其余线程等待结果。"""

    def __init__(self, probe=None, *, clock=time.monotonic, sleep=time.sleep,
                 schedule=None, pause_after=None, observer=None):
        self.probe = probe
        self.clock = clock
        self.sleep = sleep
        self.schedule = tuple(schedule or PROBE_SCHEDULE_SECONDS)
        self.pause_after = float(PAUSE_AFTER_SECONDS if pause_after is None else pause_after)
        self.observer = observer
        self._cond = threading.Condition()
        self._outage_since = None
        self._probe_round = 0
        self._prober = False
        self.paused = False
        self.outages = 0

    @property
    def outage(self):
        with self._cond:
            return self._outage_since is not None or self.paused

    def _notify(self, event, **fields):
        if self.observer is None:
            return
        try:
            self.observer(event, fields)
        except Exception:
            pass  # 观察记录失败不能影响调用与额度

    def probe_now(self):
        """True=网络与服务可用，False=不可用，None=没有探测器。"""
        if self.probe is None:
            return None
        try:
            return bool(self.probe())
        except Exception:
            return False

    def enter_outage(self, reason=''):
        with self._cond:
            if self._outage_since is None and not self.paused:
                self._outage_since = self.clock()
                self._probe_round = 0
                self.outages += 1
                started = True
            else:
                started = False
        if started:
            self._notify('outage_started', reason=reason)

    def _pause_message(self):
        hours = self.pause_after / 3600.0
        return (f'{NETWORK_PAUSE_REASON}：连续断网超过 {hours:g} 小时，整轮安全暂停；'
                '网络恢复后 resume 继续，额度不变')

    def _sleep_until(self, until, checkpoint):
        while True:
            checkpoint()
            remaining = until - self.clock()
            if remaining <= 0:
                return
            self.sleep(min(.5, remaining))

    def wait_until_healthy(self, checkpoint=lambda: None):
        """阻塞到探测通过；超过上限抛 NetworkPaused。不发模型请求、不计额度。

        调用方先 `enter_outage()`；断网已被别的线程确认恢复时立即返回。
        """
        while True:
            checkpoint()
            with self._cond:
                if self.paused:
                    raise NetworkPaused(self._pause_message())
                if self._outage_since is None:
                    return
                lead = not self._prober
                if lead:
                    self._prober = True
                    since = self._outage_since
                    delay = self.schedule[min(self._probe_round, len(self.schedule) - 1)]
                    self._probe_round += 1
                else:
                    self._cond.wait(timeout=.5)
            if not lead:
                continue
            try:
                deadline = since + self.pause_after
                self._sleep_until(min(self.clock() + delay, deadline), checkpoint)
                ok = self.probe_now()
                if ok is None:
                    ok = True  # 无探测器：交给请求本身检验线路
                if ok:
                    with self._cond:
                        self._outage_since = None
                        self._cond.notify_all()
                    self._notify('recovered', waited_seconds=round(self.clock() - since, 1))
                    return
                self._notify('probe_failed', waited_seconds=round(self.clock() - since, 1))
                if self.clock() >= deadline:
                    with self._cond:
                        self.paused = True
                        self._cond.notify_all()
                    self._notify('paused', waited_seconds=round(self.clock() - since, 1))
                    raise NetworkPaused(self._pause_message())
            finally:
                with self._cond:
                    self._prober = False
                    self._cond.notify_all()

    def wait_request_retry(self, seconds, checkpoint=lambda: None):
        """单请求退避（网络正常时的重发、限流 429）；不进入全局暂停。"""
        self._sleep_until(self.clock() + float(seconds), checkpoint)


__all__ = ['NetworkGate', 'NetworkPaused', 'NETWORK_PAUSE_REASON', 'NETWORK_WAIT',
           'NETWORK_RETRY', 'NETWORK_STATES', 'PROBE_SCHEDULE_SECONDS', 'PAUSE_AFTER_SECONDS',
           'REQUEST_FAILURE_LIMIT', 'RATE_LIMIT_BACKOFF_SECONDS', 'REQUEST_RETRY_DELAY_SECONDS']
