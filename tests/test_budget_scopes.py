"""T03：共享额度、scope 上限、恢复与心跳的行为测试。

这一组针对的是"**同一个 run 的额度只有一个**"这件事。改造前的 `BudgetGuard` 只做 run 级
token／时间护栏，缺的是三样（调研 §1.2、计划 §6.1）：

| 缺什么 | 后果 |
|---|---|
| unit／stage／resource 级 **attempt 上限** | 一个单元可以在审核里反复重试到把额度吃光，报告上只看到"跑了很久" |
| 预约的**持久化**与**未知在途**结算 | 崩溃／resume 后计数归零，同一份调用被记两次或一次都没记 |
| **活跃区间并集**计时 | 并行子任务时长被简单相加，父层额度被凭空放大 |

判据刻意都用 **fake clock + 替身传输**，所以它证明的是记账语义，不是真实网络快慢。
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from aiforstudy.errors import BudgetExceeded, ConfigError  # noqa: E402
from aiforstudy.pipeline.budget import (  # noqa: E402
    BudgetGuard,
    BudgetPolicy,
    BudgetScope,
    load_repair_policy,
)


class FakeClock:
    """可推的时钟。用真实 `time.monotonic` 会让"暂停 100 秒"变成真的等 100 秒。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _policy(**kw) -> BudgetPolicy:
    base = dict(max_tokens=1_000_000, max_minutes=10, parallelism=1)
    base.update(kw)
    return BudgetPolicy(**base)


# --------------------------------------------------------------------------- #
# 冻结的护栏数值：必须来自版本化配置文件，不是散落在代码里的魔数
# --------------------------------------------------------------------------- #
def test_repair_policy_file_matches_planned_guardrails():
    policy = load_repair_policy(Path("tests/fixtures/legacy_budget/repair_policy.json"))
    assert policy["policy_version"] == 1
    assert policy["content_repair_batches_per_unit"] == 2
    assert policy["evidence_repairs_per_verdict"] == 1
    assert policy["attempt_limits"] == {"unit": 32, "stage": 16, "resource": 8,
                                       "transport": 2}
    assert policy["active_seconds"] == {"unit": 1800, "stage": 1200, "resource": 600}
    assert policy["api_deadline_seconds"] == 300
    assert policy["connect_timeout_seconds"] == 15
    assert policy["cancel_grace_seconds"] == 10
    assert policy["heartbeat_seconds"] == 5
    assert policy["render"] == {"draft_seconds": 120, "final_seconds": 300,
                               "cpu_cores": 2, "memory_bytes": 2147483648,
                               "output_bytes": 134217728,
                               "max_concurrent_workers": 1}


def test_budget_policy_carries_the_frozen_limits():
    policy = _policy()
    assert policy.attempt_limits["unit"] == 32
    assert policy.attempt_limits["stage"] == 16
    assert policy.attempt_limits["resource"] == 8
    assert policy.attempt_limits["transport"] == 2
    assert policy.active_seconds["unit"] == 1800
    assert policy.api_deadline_seconds == 300
    assert policy.cancel_grace_seconds == 10
    assert policy.heartbeat_seconds == 5
    assert policy.content_repair_batches_per_unit == 2
    assert policy.evidence_repairs_per_verdict == 1
    assert policy.render["final_seconds"] == 300
    assert policy.render["max_concurrent_workers"] == 1


def test_policy_digest_is_stable_and_changes_with_limits():
    a = _policy()
    b = _policy()
    assert a.digest() == b.digest(), "同一份护栏必须得到同一个 digest（resume 要认它）"
    c = _policy(max_tokens=2_000_000)
    assert c.digest() != a.digest(), "改了额度就必须换 digest，否则'静默加预算'查不出来"


def test_repair_policy_loader_rejects_bad_files(tmp_path):
    bad = tmp_path / "repair_policy.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_repair_policy(bad)
    with pytest.raises(ConfigError):
        load_repair_policy(tmp_path / "missing.json")
    wrong_version = tmp_path / "v9.json"
    wrong_version.write_text(json.dumps({"policy_version": 9}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_repair_policy(wrong_version)


# --------------------------------------------------------------------------- #
# scope 级 attempt 上限
# --------------------------------------------------------------------------- #
def test_unit_stage_resource_attempt_limits_are_enforced():
    guard = BudgetGuard(_policy())
    # unit：第 1..32 次允许，第 33 次拒绝。每次换一个 stage，确保只有 unit 计数在涨。
    for i in range(32):
        lease = guard.acquire_call(BudgetScope(run_id="r1", unit_id="CH04-S01",
                                               stage=f"probe-{i}"), 10)
        lease.finish(10, "ok")
    with pytest.raises(BudgetExceeded) as excinfo:
        guard.acquire_call(BudgetScope(run_id="r1", unit_id="CH04-S01",
                                       stage="probe-final"), 10)
    assert "单元" in str(excinfo.value) and "32" in str(excinfo.value)
    assert guard.attempts_remaining(
        BudgetScope(run_id="r1", unit_id="CH04-S01", stage="probe-final"))["unit"] == 0

    # stage：第 1..16 次允许，第 17 次拒绝（同单元阶段；单元上限 32 尚未耗尽）
    guard = BudgetGuard(_policy())
    for i in range(16):
        lease = guard.acquire_call(BudgetScope(run_id="r1", unit_id="U",
                                               stage="audit"), 10)
        lease.finish(10, "ok")
    with pytest.raises(BudgetExceeded) as excinfo:
        guard.acquire_call(BudgetScope(run_id="r1", unit_id="U", stage="audit"), 10)
    assert "阶段" in str(excinfo.value) and "16" in str(excinfo.value)

    # resource：第 1..8 次允许，第 9 次拒绝
    guard = BudgetGuard(_policy())
    for i in range(8):
        lease = guard.acquire_call(BudgetScope(run_id="r1", unit_id="U", stage="figures",
                                               resource_id="figure:IMG-1"), 10)
        lease.finish(10, "ok")
    with pytest.raises(BudgetExceeded) as excinfo:
        guard.acquire_call(BudgetScope(run_id="r1", unit_id="U", stage="figures",
                                       resource_id="figure:IMG-1"), 10)
    assert "资源" in str(excinfo.value) and "8" in str(excinfo.value)


def test_empty_scope_levels_do_not_share_a_counter_bucket():
    """空层不是"一个叫空的资源"：不写 resource 的调用不该共用一个 resource 额度。

    这条是 T03 自己踩出来的：第一版把空 `resource_id` 也算一层，键退化成 `r1|`，
    于是所有不带资源的调用跑到第 9 次就凭空撞上"资源尝试上限 8"。
    """
    guard = BudgetGuard(_policy())
    for i in range(30):
        # stage 每轮不同，否则会先撞上 stage 上限（那本身是对的，只是不是这条要验的事）
        scope = BudgetScope(run_id="r1", unit_id=f"U{i}", stage=f"lesson-{i}")
        guard.acquire_call(scope, 10).finish(10, "ok")
        assert guard.attempts_for(scope)["resource"] == 0, (
            "没有 resource_id 的调用不该被计进 resource 桶")


def test_retries_and_evidence_repairs_also_consume_attempts():
    """§6.1：初次、审核、**证据修复**、运输重试**全都**进同一个 attempt 账。"""
    guard = BudgetGuard(_policy())
    scope = BudgetScope(run_id="r1", unit_id="CH04-S01", stage="audit",
                        resource_id="figure:IMG-1")
    for i in range(8):
        guard.acquire_call(scope, 10).finish(None, "transport-retry" if i else "ok")
    assert guard.attempts_for(scope) == {"unit": 8, "stage": 8, "resource": 8}
    with pytest.raises(BudgetExceeded):
        guard.acquire_call(scope, 10)          # 第 9 次：resource 额度已用尽
    # 结算（含 unknown）不退还 attempt 计数 —— 重试不是"重新开始"
    assert guard.attempts_for(scope)["resource"] == 8


def test_transport_attempt_limit_is_two():
    policy = _policy()
    assert policy.attempt_limits["transport"] == 2
    guard = BudgetGuard(policy)
    scope = BudgetScope(run_id="r1", unit_id="U", stage="lesson")
    assert guard.transport_attempts_left(scope) == 2
    guard.acquire_call(scope, 10).finish(10, "ok")
    assert guard.transport_attempts_left(scope) == 1
    guard.acquire_call(scope, 10).finish(10, "ok")
    assert guard.transport_attempts_left(scope) == 0, "没有剩余额度就不该再重试"


# --------------------------------------------------------------------------- #
# 并发预约不超父余额
# --------------------------------------------------------------------------- #
def test_concurrent_reservations_do_not_exceed_parent_balance():
    guard = BudgetGuard(_policy(max_tokens=1000))
    acquired: list = []
    rejected: list = []
    barrier = threading.Barrier(16)

    def worker(i: int) -> None:
        barrier.wait()
        try:
            lease = guard.acquire_call(BudgetScope(run_id="r1", unit_id=f"U{i}",
                                                   stage="lesson"), 100)
            acquired.append(lease)
        except BudgetExceeded as exc:
            rejected.append(str(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(acquired) == 10, f"1000 额度 / 每次 100 恰好 10 次，实际 {len(acquired)}"
    assert len(rejected) == 6
    assert guard.snapshot()["in_flight_reserved_tokens"] == 1000, (
        "在途预约必须把额度占住，否则并行的 16 个请求会同时看到'还有余额'")


# --------------------------------------------------------------------------- #
# 恢复：计数只恢复一次，暂停不加时间，未知在途保守保留
# --------------------------------------------------------------------------- #
def test_resume_preserves_all_limits_once():
    clock = FakeClock()
    first = BudgetGuard(_policy(), clock=clock)
    scope = BudgetScope(run_id="r1", unit_id="CH04-S01", stage="audit")
    first.begin_active(scope)
    for _ in range(5):
        first.acquire_call(scope, 100).finish(100, "ok")
    first.acquire_call(scope, 100).finish(None, "timeout")     # 未知用量
    clock.advance(120)
    first.end_active(scope)
    snapshot = first.snapshot()
    assert first.attempts_for(scope)["unit"] == 6
    assert snapshot["usage"]["unknown_events"] == 1
    assert snapshot["unknown_reserved_tokens"] == 100

    second = BudgetGuard(_policy(), clock=clock)
    assert second.restore_from_snapshot("r1", snapshot) is True
    assert second.restore_from_snapshot("r1", snapshot) is False, "同一 run 只恢复一次"
    assert second.restore_from_snapshot("r1", snapshot) is False
    assert second.attempts_for(scope)["unit"] == 6, "两次 resume 不许把计数翻倍"
    assert second.snapshot()["unknown_reserved_tokens"] == 100, (
        "崩溃时在途的未知费用必须保守保留，不能归零")
    assert second.active_seconds(scope) == pytest.approx(120)

    # 同一份快照喂给两个冷启动 guard：各自得到同一份账，不是各记一遍
    third = BudgetGuard(_policy(), clock=clock)
    third.restore_from_snapshot("r1", snapshot)
    assert third.attempts_for(scope)["unit"] == 6
    assert third.snapshot()["unknown_reserved_tokens"] == 100


def test_pause_does_not_add_execution_time_but_keeps_history():
    clock = FakeClock()
    guard = BudgetGuard(_policy(), clock=clock)
    scope = BudgetScope(run_id="r1", unit_id="U", stage="lesson")
    guard.begin_active(scope)
    clock.advance(60)
    guard.pause("用户取消前的收尾")
    clock.advance(600)                       # 暂停期间不能算执行时间
    guard.resume()
    clock.advance(30)
    guard.end_active(scope)
    assert guard.active_seconds(scope) == pytest.approx(90), (
        "活跃时间是区间的并集，暂停的 600 秒不算；但暂停前的 60 秒不许丢")


def test_active_time_union_does_not_sum_parallel_children():
    clock = FakeClock()
    guard = BudgetGuard(_policy(), clock=clock)
    parent = BudgetScope(run_id="r1", unit_id="U", stage="audit")
    guard.begin_active(parent)
    for i in range(4):
        child = BudgetScope(run_id="r1", unit_id="U", stage="audit", resource_id=f"fig-{i}")
        guard.begin_active(child)
        clock.advance(10)
        guard.end_active(child)
    guard.end_active(parent)
    assert guard.active_seconds(parent) == pytest.approx(40)
    assert guard.active_seconds(
        BudgetScope(run_id="r1", unit_id="U", stage="audit")) == pytest.approx(40), (
        "父层时长不能被并行子任务相加放大")


def test_crash_interval_is_settled_conservatively_from_last_heartbeat():
    """§6.1：崩溃区间用**最后心跳 + 在途租约上限**保守结算，不凭重启归零。"""
    clock = FakeClock()
    first = BudgetGuard(_policy(), clock=clock)
    scope = BudgetScope(run_id="r1", unit_id="U", stage="audit", resource_id="fig-1")
    lease = first.acquire_call(scope, 500)          # 租约在途，然后"崩溃"
    first.heartbeat(scope)
    snapshot = first.snapshot()
    assert snapshot["in_flight_leases"], "在途租约必须进快照，否则崩溃后无从保守结算"
    clock.advance(9999)
    second = BudgetGuard(_policy(), clock=clock)
    second.restore_from_snapshot("r1", snapshot)
    assert second.snapshot()["unknown_reserved_tokens"] >= 500
    # 崩溃区间计入的时间被**租约上限**封住，不会因为重启延迟而无限膨胀
    assert second.active_seconds(scope) <= 601


# --------------------------------------------------------------------------- #
# 绝对 deadline、租约幂等、心跳
# --------------------------------------------------------------------------- #
def test_absolute_deadline_is_bounded_by_parent():
    clock = FakeClock()
    guard = BudgetGuard(_policy(max_minutes=1, api_deadline_seconds=300), clock=clock)
    scope = BudgetScope(run_id="r1", unit_id="U", stage="lesson")
    lease = guard.acquire_call(scope, 10)
    assert lease.timeout_seconds() == pytest.approx(60), (
        "父层只剩 60 秒时，单次调用的 deadline 必须取更小的那个")
    clock.advance(50)
    assert lease.timeout_seconds() == pytest.approx(10)
    clock.advance(60)
    assert lease.timeout_seconds() <= 0, "父层已超时就不能再给出正的调用窗口"


def test_call_lease_finish_is_idempotent():
    guard = BudgetGuard(_policy())
    scope = BudgetScope(run_id="r1", unit_id="U", stage="lesson")
    lease = guard.acquire_call(scope, 100)
    first = lease.finish(42, "ok")
    second = lease.finish(999, "ok")
    assert first["tokens"] == 42
    assert second == first, "重复结算必须返回同一结果，不能把 999 再记一遍"
    assert guard.snapshot()["usage"]["tokens"] == 42
    assert guard.snapshot()["usage"]["calls"] == 1
    assert guard.snapshot()["in_flight_reserved_tokens"] == 0


def test_unknown_usage_is_never_zeroed():
    guard = BudgetGuard(_policy())
    scope = BudgetScope(run_id="r1", unit_id="U", stage="lesson")
    lease = guard.acquire_call(scope, 300)
    lease.finish(None, "timeout")
    snapshot = guard.snapshot()
    assert snapshot["usage"]["tokens"] == 0
    assert snapshot["usage"]["unknown_events"] == 1
    assert snapshot["unknown_reserved_tokens"] == 300, "未知用量要保守保留，不能填 0"
    assert guard.remaining_tokens() <= _policy().max_tokens - 300


def test_heartbeat_reports_progress_and_never_leaks_secrets():
    clock = FakeClock()
    guard = BudgetGuard(_policy(), clock=clock)
    scope = BudgetScope(run_id="r1", unit_id="CH04-S01", stage="audit",
                        resource_id="figure:IMG-1")
    guard.begin_active(scope)
    clock.advance(7)
    beat = guard.heartbeat(scope, rounds={"content": 1, "evidence": 2},
                           note="审核中")
    for key in ("elapsed_seconds", "deadline_seconds", "remaining_tokens",
                "remaining_attempts", "rounds", "at"):
        assert key in beat, f"心跳缺 {key} —— 没有它就无法判断'是在跑还是卡住了'"
    assert beat["rounds"] == {"content": 1, "evidence": 2}
    assert beat["elapsed_seconds"] == pytest.approx(7)
    blob = json.dumps(beat, ensure_ascii=False).lower()
    for forbidden in ("api_key", "authorization", "bearer", "sk-", "password"):
        assert forbidden not in blob


def test_heartbeat_staleness_seconds_uses_fake_clock():
    clock = FakeClock()
    guard = BudgetGuard(_policy(), clock=clock)
    scope = BudgetScope(run_id="r1", unit_id="U", stage="lesson")
    assert guard.heartbeat_staleness(scope) is None
    guard.heartbeat(scope)
    clock.advance(3)
    assert guard.heartbeat_staleness(scope) == pytest.approx(3)
    assert guard.policy.heartbeat_seconds == 5


def test_scope_keys_have_a_parent_chain():
    root = BudgetScope(run_id="r1")
    unit = BudgetScope(run_id="r1", unit_id="CH04-S01")
    stage = BudgetScope(run_id="r1", unit_id="CH04-S01", stage="audit")
    resource = BudgetScope(run_id="r1", unit_id="CH04-S01", stage="audit",
                           resource_id="figure:IMG-1")
    assert root.parents() == ()
    assert unit.parents() == (root.key(),)
    assert stage.parents() == (unit.key(), root.key())
    assert resource.parents() == (stage.key(), unit.key(), root.key())
    assert resource.key() == "r1|CH04-S01|audit|figure:IMG-1"


def test_sub_limits_are_not_extra_allowance():
    """§6.1：unit／stage／resource 的 token 上限都不得突破 run 的上限。"""
    guard = BudgetGuard(_policy(max_tokens=100))
    scope = BudgetScope(run_id="r1", unit_id="U", stage="lesson")
    guard.acquire_call(scope, 60).finish(60, "ok")
    with pytest.raises(BudgetExceeded):
        guard.acquire_call(scope, 60)          # 60+60 > 100，父层额度说了算
