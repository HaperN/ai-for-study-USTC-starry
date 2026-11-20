"""用量账本：**每条尝试一笔账，未知不填零**。

方案 §11 对记账的要求（逐条对应到本模块）：

> run_id、attempt_id、task_id、book_id、scope_kind=book/unit、unit_key、request fingerprint、
> 模型、重试次数、真实/缓存、token、未知用量**都可追踪**。

> 全书基础准备与章生成**分别汇总**；恢复展示历史累计与本次增量；
> **取消后的已消费用量不能丢失**。

> 响应到达**先记录 usage 再解析 JSON**；失败、重试、缓存命中**分别记账**。
> 网络超时导致用量不明时标 `unknown` 并**保守保留预约，不填写零**。

## 三条刻意的设计

1. **追加式 JSONL 是唯一写入方式**。并行 worker 各自"读-改-写"汇总文件必然互相覆盖
   （本项目在 `lessons.index.json` 上真发生过，见 `lib/parallel.py` 的 S5）。
   汇总**每次现算**，不落一份会过期的副本。
2. **`unknown` 是独立的一栏**，不是 0。填 0 会让报告看起来"还有余额"，而钱已经花了；
   而且"未知"必须**保留预约**，否则并行请求会同时看到余额然后一起超。
3. **每次尝试单独一行**（`attempt` 字段），所以"重试了两次"在原子里看得见，
   而不是只留下一个最后的总数。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

#: 记账分桶（方案 §11：全书基础准备与章生成分别汇总）
#: 分账桶（§6.1）。`text_audit` 是 T04 新增的正文前置门，**必须**在这里，
#: 否则它一记账就抛 ValueError，而那个异常会被上游当成"发现跑不起来"：
#: 一个纯粹的记账桶缺失，最后长成"正文门 blocked"这种看起来像内容问题的失败。
BUCKETS = ("prepare", "chapter", "lesson", "text_audit", "media_plan", "figures", "audit",
           "interaction", "delivery", "other")

#: 一条记录的性质
KIND_CALL = "call"            # 真实调用（有 usage 或 unknown）
KIND_CACHE = "cache"          # 缓存命中（0 新增费用）
KIND_RETRY = "retry"          # 一次重试（本身也是一次 call，但单独标出来）


@dataclass
class UsageRecord:
    run_id: str
    book_id: str = ""
    scope_kind: str = "book"          # book | unit
    unit_key: str = ""
    stage: str = ""
    bucket: str = "other"
    role: str = ""
    model: str = ""
    effort: str = ""
    fingerprint: str = ""
    attempt: int = 1
    kind: str = KIND_CALL
    tokens: int | None = 0            # `None` = **未知**（不是 0）
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_hit_tokens: int = 0
    seconds: float = 0.0
    error: str = ""
    at: float = field(default_factory=time.time)
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.bucket not in BUCKETS:
            raise ValueError(f"bucket 必须是 {BUCKETS} 之一，实际 {self.bucket!r}")
        if self.kind not in (KIND_CALL, KIND_CACHE, KIND_RETRY):
            raise ValueError(f"kind 必须是 call/cache/retry，实际 {self.kind!r}")
        if self.attempt < 1:
            raise ValueError("attempt 从 1 起算")

    @property
    def unknown(self) -> bool:
        return self.tokens is None

    def as_dict(self) -> dict:
        """落盘的那一份。**不含密钥、不含提示词内容**（方案 §5）。"""
        return {"run_id": self.run_id, "book_id": self.book_id,
                "scope_kind": self.scope_kind, "unit_key": self.unit_key,
                "stage": self.stage, "bucket": self.bucket, "role": self.role,
                "model": self.model, "effort": self.effort,
                "fingerprint": self.fingerprint, "attempt": self.attempt,
                "kind": self.kind,
                "tokens": self.tokens, "unknown": self.unknown,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "cached_hit_tokens": self.cached_hit_tokens,
                "seconds": round(self.seconds, 3), "error": self.error,
                "at": self.at, "extra": dict(self.extra)}


def _accounting_records(records: list[UsageRecord]) -> list[UsageRecord]:
    """Each run summary closes one incremental segment, not its entire history."""
    pending: dict[str, list[UsageRecord]] = {}
    selected = []
    for row in records:
        if row.stage == "__run__":
            details = pending.pop(row.run_id, [])
            if not details:
                selected.append(row)  # legacy segment with no per-call evidence
                continue
            accounted = _summarize_accounted(details)
            known = max(0, int(row.extra.get("known_tokens", row.tokens or 0))
                        - accounted["tokens"])
            missing = max(0, int(row.extra.get("unknown_events", row.tokens is None))
                          - accounted["unknown_events"])
            if known or missing:
                # A stage can report legacy service spend in the same segment
                # as Gateway detail. Keep only the proven, unitemized remainder.
                # This projection is never persisted or returned by records().
                extra = {"known_tokens": known, "unknown_events": missing}
                for key, detail_key in (("calls", "calls"), ("cached_hits", "cache_hits"),
                                        ("retries", "retries")):
                    extra[key] = max(0, int(row.extra.get(key, 0)) - accounted[detail_key])
                selected.append(replace(row, tokens=None if missing else known, extra=extra))
        else:
            selected.append(row)
            pending.setdefault(row.run_id, []).append(row)
    return selected


def summarize(records: list[UsageRecord]) -> dict:
    """逐段保留明细与汇总可证明的差额，不丢旧服务消耗或重复计算汇总。"""
    return _summarize_accounted(_accounting_records(records))


def _summarize_accounted(records: list[UsageRecord]) -> dict:
    """Sum already selected rows without losing segment boundaries a second time."""
    total = cache = unknown = retries = calls = 0
    by_bucket: dict[str, int] = {}
    by_role: dict[str, int] = {}
    by_unit: dict[str, int] = {}
    unknown_by_bucket: dict[str, int] = {}
    for row in records:
        if row.stage == "__run__":
            # Runner writes tokens=None for a mixed known/unknown segment; its
            # known portion and unknown event count are preserved in extra.
            tokens = int(row.extra.get("known_tokens", row.tokens or 0))
            missing = int(row.extra.get("unknown_events", row.tokens is None))
            total += tokens
            unknown += missing
            calls += int(row.extra.get("calls", 1))
            cache += int(row.extra.get("cached_hits", 0))
            retries += int(row.extra.get("retries", 0))
            by_bucket[row.bucket] = by_bucket.get(row.bucket, 0) + tokens
            if missing:
                unknown_by_bucket[row.bucket] = unknown_by_bucket.get(row.bucket, 0) + missing
            if row.role:
                by_role[row.role] = by_role.get(row.role, 0) + tokens
            continue
        if row.kind == KIND_CACHE:
            cache += 1
            calls += 1
            continue
        calls += 1
        if row.attempt > 1:
            retries += 1
        if row.unknown:
            unknown += 1
            unknown_by_bucket[row.bucket] = unknown_by_bucket.get(row.bucket, 0) + 1
            continue
        tokens = int(row.tokens or 0)
        total += tokens
        by_bucket[row.bucket] = by_bucket.get(row.bucket, 0) + tokens
        if row.role:
            by_role[row.role] = by_role.get(row.role, 0) + tokens
        if row.unit_key:
            by_unit[row.unit_key] = by_unit.get(row.unit_key, 0) + tokens
    return {"tokens": total, "calls": calls, "cache_hits": cache, "retries": retries,
            "unknown_events": unknown, "by_bucket": dict(sorted(by_bucket.items())),
            "by_role": dict(sorted(by_role.items())),
            "by_unit": dict(sorted(by_unit.items())),
            "unknown_by_bucket": dict(sorted(unknown_by_bucket.items())),
            "note": ("`unknown` 是**花了但数不出来**，不是 0；`cache_hits` 是 0 新增费用。"
                     "两者都不并进 tokens")}


class UsageLedger:
    """追加式账本。`records()` 现算汇总，不落会过期的副本。"""

    def __init__(self, path: Path, *, run_id: str = "") -> None:
        self.path = Path(path)
        self.run_id = run_id
        self._lock = threading.Lock()

    # ── 写
    def record(self, row: UsageRecord) -> UsageRecord:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # ⚠️ 崩溃会在文件末尾留下**没有换行的半行**。直接 append 会把新记录粘到它后面，
            #    于是第一次读取时两条一起变成坏行 —— **一笔已经花掉的账就这么没了**。
            #    所以追加前先确认末尾有换行。
            needs_newline = self.path.is_file() and self.path.stat().st_size > 0 and \
                not self.path.read_bytes().endswith(b"\n")
            with self.path.open("a", encoding="utf-8") as stream:
                if needs_newline:
                    stream.write("\n")
                stream.write(json.dumps(row.as_dict(), ensure_ascii=False) + "\n")
                stream.flush()
        return row

    # ── 读
    def records(self, *, run_id: str | None = None, book_id: str | None = None,
                scope_kind: str | None = None, bucket: str | None = None
                ) -> list[UsageRecord]:
        """读账本。**坏行跳过但不静默**（半写的行会被记进 `skipped`）。"""
        return self.read_all(run_id=run_id, book_id=book_id, scope_kind=scope_kind,
                             bucket=bucket)["records"]

    def read_all(self, *, run_id: str | None = None, book_id: str | None = None,
                 scope_kind: str | None = None, bucket: str | None = None) -> dict:
        out: list[UsageRecord] = []
        skipped = 0
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except ValueError:
                    # 进程被杀会留下半行。**跳过并计数**，不当成"没有这笔账"。
                    skipped += 1
                    continue
                if run_id is not None and payload.get("run_id") != run_id:
                    continue
                if book_id is not None and payload.get("book_id") != book_id:
                    continue
                out.append(UsageRecord(**{k: v for k, v in payload.items()
                                          if k in UsageRecord.__dataclass_fields__}))
        def selected(row):
            return ((scope_kind is None or row.scope_kind == scope_kind)
                    and (bucket is None or row.bucket == bucket))
        # Select accounting segments before bucket/scope filtering so removing
        # a prepare detail cannot resurrect its summary as chapter spending.
        return {"records": [row for row in out if selected(row)], "skipped_bad_lines": skipped,
                "summary": _summarize_accounted([row for row in _accounting_records(out)
                                                  if selected(row)])}

    def by_run(self) -> dict[str, dict]:
        """**按 run 分组**：恢复时要同时展示"历史累计"与"本次增量"（§11）。"""
        grouped: dict[str, list[UsageRecord]] = {}
        for row in self.records():
            grouped.setdefault(row.run_id or "（无 run_id）", []).append(row)
        return {run_id: summarize(rows) for run_id, rows in sorted(grouped.items())}

    def totals(self) -> dict:
        return self.read_all()["summary"]

    def split_prepare_vs_chapter(self, *, run_id: str | None = None) -> dict:
        """方案 §11：全书基础准备与章生成**分别汇总**。"""
        records = _accounting_records(self.records(run_id=run_id))
        prepare = [r for r in records if r.bucket == "prepare"]
        others = [r for r in records if r.bucket != "prepare"]
        return {"prepare": _summarize_accounted(prepare), "chapters": _summarize_accounted(others),
                "note": "两栏分开看，避免'整本准备'的钱被算进某一章"}


__all__ = ["BUCKETS", "KIND_CALL", "KIND_CACHE", "KIND_RETRY", "UsageRecord", "summarize",
           "UsageLedger"]
