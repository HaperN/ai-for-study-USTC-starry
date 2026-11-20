"""Explicit legacy ledger/tool semantics. Production cost coupling retired by T2; replacement in test_execution_control.py."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from aiforstudy.core.usage import UsageLedger, UsageRecord
from aiforstudy.pipeline.budget import BudgetGuard, BudgetPolicy, BudgetScope
from aiforstudy.pipeline.runner import Runner, RunResult
from aiforstudy.pipeline.state import RunStore
from aiforstudy.pipeline.gateway_client import GatewayClient
from aiforstudy.providers.gateway import CallRequest, Gateway


PROVIDER_USAGE = {
    "prompt_tokens": 4535, "completion_tokens": 12812, "total_tokens": 17347,
    "prompt_tokens_details": {"cached_tokens": 384},
    "completion_tokens_details": {"reasoning_tokens": 3361},
    "prompt_cache_hit_tokens": 384, "prompt_cache_miss_tokens": 4151,
}


def guard():
    return BudgetGuard(BudgetPolicy(max_tokens=100000, max_minutes=10, parallelism=1))














def test_ledger_excludes_run_summaries_but_retains_them_for_budget_recovery(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.jsonl", run_id="r")
    for row in [
        UsageRecord(run_id="r", stage="toc", bucket="prepare", tokens=17347),
        UsageRecord(run_id="r", stage="lesson", bucket="chapter", tokens=None),
        UsageRecord(run_id="r", stage="__run__", tokens=None,
                    extra={"known_tokens": 17347, "unknown_events": 1}),
        UsageRecord(run_id="r", stage="lesson", bucket="chapter", tokens=50),
        UsageRecord(run_id="r", stage="__run__", tokens=50, attempt=2),
    ]:
        ledger.record(row)
    summary = ledger.totals()
    assert summary["tokens"] == 17397
    assert summary["calls"] == 3
    assert summary["unknown_events"] == 1
    assert summary["retries"] == 0
    assert ledger.by_run()["r"]["tokens"] == 17397
    split = ledger.split_prepare_vs_chapter(run_id="r")
    assert split["prepare"]["tokens"] == 17347
    assert split["chapters"]["tokens"] == 50
    assert len(ledger.records()) == 5


def test_summary_only_legacy_runs_keep_spend_and_unknown_cost_warnings(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.jsonl")
    for row in [
        UsageRecord(run_id="legacy", stage="__run__", tokens=12),
        UsageRecord(run_id="legacy", stage="__run__", tokens=None),
        UsageRecord(run_id="current", stage="toc", bucket="prepare", tokens=30),
        UsageRecord(run_id="current", stage="__run__", tokens=30),
    ]:
        ledger.record(row)
    assert ledger.totals()["tokens"] == 42
    assert ledger.totals()["unknown_events"] == 1
    assert ledger.by_run()["legacy"]["tokens"] == 12
    # Filtering out the detailed prepare row must not resurrect its summary as
    # chapter spending; legacy-only summaries remain visible in their own run.
    assert ledger.read_all(run_id="current", bucket="other")["summary"]["tokens"] == 0
    assert ledger.split_prepare_vs_chapter(run_id="current")["chapters"]["tokens"] == 0
    assert ledger.split_prepare_vs_chapter(run_id="legacy")["chapters"]["tokens"] == 12


def test_stage_release_keeps_legacy_spend_without_becoming_an_api_call():
    budget = guard()
    budget.reserve(20)
    budget.release(20, 12, bucket="legacy")
    budget.acquire_call(BudgetScope("r", "book", "toc"), 100).finish(30)
    budget.record_cached()
    budget.release(0, 0, bucket="toc")
    usage = budget.snapshot()["usage"]
    assert usage["tokens"] == 42
    assert usage["calls"] == 4  # backward-compatible settlement/cache aggregate
    assert usage.get("api_calls") == 1
    assert usage.get("stage_releases") == 2
    first = budget.usage_delta(usage)
    assert (first["api_calls"], first["stage_releases"]) == (1, 2)
    budget.release(10, None, bucket="legacy")
    budget.acquire_call(BudgetScope("r", "book", "toc", "retry"), 25).finish(None)
    second = budget.usage_delta(budget.snapshot()["usage"])
    assert (second["api_calls"], second["stage_releases"], second["tokens"]) == (1, 1, 0)
    assert budget.snapshot()["unknown_reserved_tokens"] == 35
