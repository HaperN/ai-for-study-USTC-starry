"""用量账的**归属**：并行下不许串账（规格 §5 判据 **A5**，链路审计 D-5）。

## 为什么这条判据值钱

原来 `run_chapter.py` 算"这一步花了多少"的办法是：跑之前读一遍 `usage.jsonl` 求和，
跑之后再读一遍求和，相减。**单章串行时它是对的**，所以一直没暴露。

但整本要**按章并行**，两章的 `usage.jsonl` 是同一个文件（追加写本身安全），
于是 A 章的"跑后总数"里已经混进了 B 章的花费 —— **两章互相把对方花的钱算进自己头上**。
这不是"文件写坏了"，而是"这笔钱是谁花的"从来没被记下来。

堵法（规格 S8）：每条账目记 `run_id`，统计一律按 `run_id` 分组。

## A5 判据

> 按 `run_id` 汇总的 token 之和 == `usage.jsonl` 全量之和（差 0）。

这条断言的真正含义是：**没有一笔账落在分组之外**。历史行（2026-09-26 之前没有
`run_id` 字段的老账）被归到 `usage.LEGACY_RUN`，所以等式对老账同样成立 ——
如果哪天有人把"没有 run_id 的行"丢掉不算，这条测试会立刻变红。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import usage as U          # noqa: E402


def _row(run_id, tokens, *, tag="发现", cached=False):
    row = {"at": "2026-09-26T19:45:00+0800", "tag": tag, "total_tokens": tokens,
           "cached": cached, "model": "deepseek-flash"}
    if run_id is not None:                 # None = 模拟"历史账"（根本没有这个字段）
        row["run_id"] = run_id
    return row


def _write_log(tmp_path: Path, rows: list[dict]) -> Path:
    log = tmp_path / "usage.jsonl"
    log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    return log


# ────────────────────────────────────────── A5：分组求和 == 全量求和

def test_grouped_sum_equals_total(tmp_path):
    """A5：造两条不同 run_id 的记录，断言分组求和 == 总量（差 0）。"""
    log = _write_log(tmp_path, [_row("run-A", 100), _row("run-B", 250),
                                _row(None, 7)])          # 历史账也在分组里
    grouped = U.by_run(log)
    assert sum(v["token"] for v in grouped.values()) == U.sum_tokens(log) == 357


def test_each_run_only_counts_its_own_spend(tmp_path):
    """**这就是并行串账的直接回归**：A 趟求和不能把 B 趟的钱算进来。"""
    log = _write_log(tmp_path, [_row("run-A", 100), _row("run-B", 250),
                                _row("run-A", 1)])
    assert U.sum_tokens(log, run_id="run-A") == 101
    assert U.sum_tokens(log, run_id="run-B") == 250
    assert U.sum_tokens(log, run_id="run-B") + U.sum_tokens(log, run_id="run-A") == U.sum_tokens(log)


def test_legacy_rows_without_run_id_are_not_lost(tmp_path):
    """老账（没有 run_id 字段）必须**照样算进总量**，只是归到历史那一组。"""
    log = _write_log(tmp_path, [_row(None, 40), _row("run-A", 60), _row("", 10)])
    assert U.sum_tokens(log) == 110
    assert U.sum_tokens(log, run_id=U.LEGACY_RUN) == 50      # 40 + 10（空串也算历史）
    assert U.run_of({}) == U.LEGACY_RUN


def test_cached_calls_are_counted_but_cost_nothing(tmp_path):
    """缓存回放也要记一笔（否则"调用了几次"对不上），但 token 为 0。"""
    log = _write_log(tmp_path, [_row("run-A", 0, cached=True), _row("run-A", 500)])
    bucket = U.by_run(log)["run-A"]
    assert bucket == {"token": 500, "调用": 2, "缓存命中": 1}


def test_by_tag_separates_steps_inside_one_run(tmp_path):
    """同一趟内按步骤（tag）分组 —— "哪一步最贵"要能一行算出来。"""
    log = _write_log(tmp_path, [_row("run-A", 100, tag="发现"), _row("run-A", 30, tag="改写"),
                                _row("run-B", 999, tag="发现")])
    assert U.by_tag(log, "run-A") == {"发现": 100, "改写": 30}


def test_a_broken_line_does_not_break_the_ledger(tmp_path):
    """账目里混进一行坏 JSON，不该让整条流水线崩掉（读不出来就当没有这行）。"""
    log = tmp_path / "usage.jsonl"
    log.write_text('{"total_tokens": 5, "run_id": "run-A"}\n{坏行\n\n', encoding="utf-8")
    assert U.sum_tokens(log, run_id="run-A") == 5


def test_empty_or_missing_log_reads_as_zero(tmp_path):
    assert U.sum_tokens(tmp_path / "没有这个文件.jsonl") == 0
    assert U.by_run(tmp_path / "没有这个文件.jsonl") == {}


# ────────────────────────────────────────── run_id 怎么传到子进程

def test_env_for_run_is_empty_when_there_is_no_run_id():
    """没设 run_id 时不许注入空变量 —— 否则子进程会以为自己属于一个叫 "" 的趟。"""
    assert U.env_for_run("") == {}
    assert U.env_for_run("run-A") == {U.RUN_ID_ENV: "run-A"}


def test_new_run_id_is_sortable_and_unique():
    ids = {U.new_run_id("chapter") for _ in range(50)}
    assert len(ids) == 50                              # 同一秒内也不撞
    assert all(i.startswith("chapter-") for i in ids)


# ────────────────────────────────────────── 接线护栏：写入端真的带上 run_id 了吗

def test_log_usage_stamps_the_current_run_id(tmp_path, monkeypatch):
    """**位置护栏**（这类测试抓过真 bug）：`usage.py` 读得对，但写入端忘了带 run_id，
    那么"按 run_id 分组"永远只有一个空组 —— 读法再对也没用。
    所以这里直接调真正的写入函数，断言盘上那一行里有 run_id。"""
    import model as M

    monkeypatch.setenv(U.RUN_ID_ENV, "run-接线-护栏")
    client = M.DeepSeekClient(api_key="test", cache_dir=tmp_path,
                              usage_log=tmp_path / "usage.jsonl", use_cache=False)
    client._log_usage("fingerprint-x", M.Usage(prompt_tokens=3, total_tokens=9),
                      seconds=1.5, cached=False, tag="发现")

    row = U.read_rows(tmp_path / "usage.jsonl")[0]
    assert row["run_id"] == "run-接线-护栏"
    assert row["tag"] == "发现" and row["total_tokens"] == 9
    assert U.sum_tokens(tmp_path / "usage.jsonl", run_id="run-接线-护栏") == 9



# Production Gateway accounting retired in T2; usage-independence lives in test_execution_control.py.
