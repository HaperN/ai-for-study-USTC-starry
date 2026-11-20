from aiforstudy.pipeline.budget import BudgetGuard, BudgetPolicy, BudgetScope
from aiforstudy.errors import BudgetExceeded
import pytest


def test_stage_attempts_are_per_unit_not_shared_by_all_chapters():
    guard = BudgetGuard(BudgetPolicy(1000, 60, 1, attempt_limits={'stage': 1}))
    guard.acquire_call(BudgetScope('r', 'U1', 'lesson'), 1).finish(1)
    guard.acquire_call(BudgetScope('r', 'U2', 'lesson'), 1).finish(1)
    with pytest.raises(BudgetExceeded):
        guard.acquire_call(BudgetScope('r', 'U1', 'lesson'), 1)


def test_resource_half_run_limit_includes_inflight_and_unknown_across_stages():
    guard = BudgetGuard(BudgetPolicy(100, 60, 1))
    scope = BudgetScope('r', 'U', 'figures', 'v')
    guard.acquire_call(scope, 30).finish(None)
    with pytest.raises(BudgetExceeded):
        guard.acquire_call(BudgetScope('r', 'U', 'audit', 'v'), 21)
    guard.acquire_call(BudgetScope('r', 'U', 'audit', 'other'), 21).finish(21)


def test_call_deadline_shrinks_and_active_parent_limit_stops_next_call():
    now = [0.0]
    guard = BudgetGuard(BudgetPolicy(1000, 60, 1, api_deadline_seconds=10,
                         active_seconds={'unit': 5}), clock=lambda: now[0])
    scope = BudgetScope('r', 'U', 'lesson')
    lease = guard.acquire_call(scope, 1)
    now[0] = 3
    assert lease.timeout_seconds() == 2
    now[0] = 5
    lease.finish(1)
    with pytest.raises(BudgetExceeded):
        guard.acquire_call(scope, 1)


def test_scope_balances_survive_two_cold_resumes():
    guard = BudgetGuard(BudgetPolicy(100, 60, 1))
    scope = BudgetScope('r', 'U', 'figures', 'v')
    guard.acquire_call(scope, 40).finish(40)
    for _ in range(2):
        snap = guard.snapshot()
        guard = BudgetGuard(guard.policy)
        guard.restore_from_snapshot('r', snap)
        with pytest.raises(BudgetExceeded):
            guard.acquire_call(scope, 11)


def test_local_stage_work_consumes_scope_time_without_model_calls():
    now = [0.0]
    guard = BudgetGuard(BudgetPolicy(1000, 60, 1, active_seconds={'unit':5,'stage':4,'resource':3}), clock=lambda:now[0])
    with pytest.raises(BudgetExceeded):
        with guard.work_scope(BudgetScope('r','U','figures')):
            now[0] = 4
            guard.check()
    assert guard.active_seconds(BudgetScope('r','U')) == 4
    assert guard.usage.calls == 0


def test_run_minutes_survive_cold_resume_pause_and_increase():
    now = [0.0]
    policy = BudgetPolicy(1000, 1, 1)
    guard = BudgetGuard(policy, clock=lambda: now[0])
    now[0] = 59
    snap = guard.snapshot()
    now[0] = 1000  # offline interval is not execution time
    restored = BudgetGuard(policy, clock=lambda: now[0])
    restored.restore_from_snapshot('r', snap)
    assert restored.deadline_remaining() == pytest.approx(1)
    restored.restore_from_snapshot('r', snap)
    assert restored.deadline_remaining() == pytest.approx(1)
    restored.pause()
    now[0] += 500
    restored.resume()
    assert restored.deadline_remaining() == pytest.approx(1)
    now[0] += 1
    assert restored.would_exceed() is not None
    snap = restored.snapshot()
    again = BudgetGuard(BudgetPolicy(1000, 2, 1), clock=lambda: now[0])
    again.restore_from_snapshot('r', snap)
    assert again.deadline_remaining() == pytest.approx(60)


def test_same_guard_restore_does_not_double_count_elapsed_time():
    now = [0.0]
    guard = BudgetGuard(BudgetPolicy(1000, 1, 1), clock=lambda:now[0])
    now[0] = 20
    guard.restore_from_snapshot('r', guard.snapshot(), restore_spend=False)
    assert guard.deadline_remaining() == pytest.approx(40)
