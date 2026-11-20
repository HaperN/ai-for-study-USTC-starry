"""用量账（`usage.jsonl`）的**唯一**读法 + 运行标识（`run_id`）。

## 为什么需要它（三份调研共同的结论）

用户 2026-09-26 问「这样的程序如何之后跑自动化呢」。调研的结论里有一条专门的账目问题
（链路审计 **D-5**，也写在 `整本并行-规格与验收.md` §3.1 的 **S8**）：

> `usage.jsonl` 是**追加**写（单行 `O_APPEND` 安全），但**记账是全局前后差值** ——
> 于是并行跑两章时，两章会互相把对方花的 token 算进自己头上。

根因不是"文件并发写坏了"（追加本身安全），而是**没有把"这笔钱是谁花的"记下来**，
所以只能靠"跑之前取一个总数、跑之后取一个总数、相减"——这个差值在并行下必然串账。

## 两个东西，一个文件

1. **`run_id`**：一次编排（比如"整本跑一趟"）的标识。`tools/run_book.py` 生成一个，
   通过环境变量 `AIFORSTUDY_RUN_ID` **传给所有子进程**（worker 是子进程，这是唯一能
   穿透进程边界的办法）。每个 `_log_usage` 都把它写进账目行。
2. **账目读法**：`sum_tokens(run_id=...)` 只算这一趟；`by_run()` 分组对账。

    A5 判据：`sum(by_run().values()) == sum_tokens()`（分组求和 == 全量求和，差 0）。
    这条断言的意义是"**没有一笔账落在分组之外**"—— 历史行（没有 `run_id` 的老账）
    归到 `LEGACY_RUN`，所以它也在分组里，等式对老账同样成立。

## 一条边界（刻意写死）

`run_id` 只解决**归属**，不解决**幂等**：同一趟里同一步跑两次，就是两笔账、两条记录。
幂等键是 `(run_id, tag)`（对应 Temporal 那套 `run_id + activity_id`，见调研 §3），
去重要在**业务层**做（比如缓存命中会记 `cached=true`、token 为 0）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# 并行 worker 是**子进程**，只能靠环境变量把 run_id 传下去
RUN_ID_ENV = "AIFORSTUDY_RUN_ID"

# 历史账（2026-09-26 之前）没有 run_id 字段 —— 归到这一个分组，不丢、也不假装知道它属于谁
LEGACY_RUN = "（无 run_id／历史账）"

DEFAULT_USAGE_LOG = Path(__file__).resolve().parent.parent / ".cache" / "model" / "usage.jsonl"


def new_run_id(prefix: str = "run") -> str:
    """生成一个 run_id：`run-20000101-000000-4ff963`（可按时间排序 + 短随机防撞）。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tail = os.urandom(3).hex()
    return f"{prefix}-{stamp}-{tail}"


def current_run_id() -> str:
    """本进程所属的 run_id；没设就是空串（= 单跑，不属于任何编排）。"""
    return (os.environ.get(RUN_ID_ENV) or "").strip()


def env_for_run(run_id: str) -> dict[str, str]:
    """要传给子进程的环境变量（`subprocess` 用 `{**os.environ, **env_for_run(x)}`）。"""
    return {RUN_ID_ENV: run_id} if run_id else {}


def read_rows(log=DEFAULT_USAGE_LOG) -> list[dict]:
    """读全部账目行。**坏行跳过而不是抛异常** —— 账目读不出来不该让整条流水线崩掉。"""
    path = Path(log)
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def run_of(row: dict) -> str:
    """一条账目属于哪一趟。没有 / 空 → 历史账那一组（**不返回空串**，否则分组对账会漏）。"""
    return str(row.get("run_id") or "").strip() or LEGACY_RUN


def sum_tokens(log=DEFAULT_USAGE_LOG, *, run_id: str | None = None) -> int:
    """token 合计。给了 `run_id` 就**只算这一趟**（这就是并行下不再串账的原因）。

    ⚠️ **`run_id` 必须是关键字参数**（2026-09-26 生产版 A6 实测抓出）：
    它的前面还有一个 `log` 参数，而 `run_book.py` 原来写的是
    `usage.sum_tokens(worker["run_id"])` —— **位置参数绑到了 `log`**，
    于是它去读一个名叫 `book-2026…/CH03` 的"文件"，读不到就返回 `[]`，
    **合计恒为 0**。后果不是报错，而是**静默失效**：

        def spent() -> int:                       # run_book 里的预算判据
            return sum(usage.sum_tokens(w["run_id"]) for w in running.values()) + …  # 恒 0
        → `--budget-tokens` **永远不会触发**（只有墙钟那道闸还在工作）

    所以这里改成**仅限关键字**：错的调法会**立刻 TypeError**，而不是偷偷算成 0。
    自动跑最怕的从来不是报错，是"闸门静默失效"。

    旧调用方的语义（`run_id=None` = 全量合计）保持不变 —— `run_chapter.py` 原来
    就是"跑前取总数、跑后相减"，在单章串行时结果一样，所以现有行为不变。
    """
    if run_id is None:
        return sum(int(r.get("total_tokens") or 0) for r in read_rows(log))
    return sum(int(r.get("total_tokens") or 0) for r in read_rows(log)
               if run_of(r) == run_id)


def by_run(log=DEFAULT_USAGE_LOG) -> dict[str, dict]:
    """按 run_id 分组：`{run_id: {"token": n, "调用": n, "缓存命中": n}}`。

    A5 判据用它：`sum(v["token"] for v in by_run().values()) == sum_tokens()`。
    """
    out: dict[str, dict] = {}
    for row in read_rows(log):
        key = run_of(row)
        bucket = out.setdefault(key, {"token": 0, "调用": 0, "缓存命中": 0})
        bucket["token"] += int(row.get("total_tokens") or 0)
        bucket["调用"] += 1
        if row.get("cached"):
            bucket["缓存命中"] += 1
    return out


def by_tag(log=DEFAULT_USAGE_LOG, run_id: str | None = None) -> dict[str, int]:
    """按步骤（tag）分组 —— "哪一步最贵"是一行统计的事（`tag` 字段就是为此加的）。"""
    out: dict[str, int] = {}
    for row in read_rows(log):
        if run_id is not None and run_of(row) != run_id:
            continue
        key = str(row.get("tag") or "（未命名）")
        out[key] = out.get(key, 0) + int(row.get("total_tokens") or 0)
    return out


__all__ = ["RUN_ID_ENV", "LEGACY_RUN", "DEFAULT_USAGE_LOG", "new_run_id", "current_run_id",
           "env_for_run", "read_rows", "run_of", "sum_tokens", "by_run", "by_tag"]
