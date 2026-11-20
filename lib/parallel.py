""""我现在是不是**多个并行 worker 中的一个**" —— 整本并行里最要紧的一个判断。

## 为什么需要它（规格 §3 的核心）

并行下会打架的不是配额，是磁盘上"读-改-写"的语义。规格 §3.2 把规矩写成一句话：

> **worker 只写"每节 / 每章"的产物；"整本"级的四样东西
> （索引、概念表、快照、汇总）一律由事后一个单线程 reconcile 步骤重扫产物生成。**

这条规矩要**在代码里可判**，否则就只是文档里的一句话。所以做成一个环境变量：

    AIFORSTUDY_WORKER=CH02      # 这个进程是 CH02 章的 worker

worker 是**子进程**（`run_book.py` 用 subprocess 起 `run_chapter.py`），环境变量是唯一
能穿透进程边界的传递方式。

## 一条判据，三个用处

`worker_mode()` 为真时，**禁止写整本视图**：

| 整本视图 | 谁写 | 为什么 worker 不能写 |
|---|---|---|
| `lessons.index.json` | `reconcile.py` | 两个 worker 各读各改 → 后写的盖掉先写的，另一章的行消失（S5） |
| `concepts.json` 的 occurrence | `reconcile.py` | 同上，丢 occurrence（S6） |
| `snapshot.json` | `run_book.py` 起跑前冻结一次 | worker 每单元重建会读到"别的章还没写完"的版本 → 提示词输入不确定（S7） |

## 为什么不做成"加锁"

调研的结论（`为什么总出错-调研与自动化方案.md` §4，poka-yoke / 单一事实来源）：
**让 bug 结构上不可能发生，而不是检测它**。加锁是"记得加锁才对"，漏一处就静默丢数据；
而"worker 结构上不允许写整本视图"是漏不掉的 —— 判据只有一处，写在 `worker_mode()`。
"""
from __future__ import annotations

import os

# 值是这个 worker 负责的章号（如 "CH02"）。空 / 未设 = 不在并行编排里，可以写整本视图。
WORKER_ENV = "AIFORSTUDY_WORKER"


def worker_chapter() -> str:
    """本进程负责哪一章；不是 worker 就返回空串。"""
    return (os.environ.get(WORKER_ENV) or "").strip()


def worker_mode() -> bool:
    """**整本视图禁止写**的判据。所有"要不要重建整本"的地方都必须经过它。"""
    return bool(worker_chapter())


def env_for_worker(chapter: str, run_id: str = "") -> dict[str, str]:
    """要传给 worker 子进程的环境变量。

    `run_id` 一并注入（`lib/usage.py` 的 `RUN_ID_ENV`）：账目按 run_id 分组，
    所以**归属**和**隔离**是同一份信息，不能各记一份。
    """
    env: dict[str, str] = {}
    if chapter:
        env[WORKER_ENV] = chapter
    if run_id:
        from usage import RUN_ID_ENV          # lib/ 在 sys.path 上（与其它 lib 模块一致）
        env[RUN_ID_ENV] = run_id
    return env


__all__ = ["WORKER_ENV", "worker_chapter", "worker_mode", "env_for_worker"]
