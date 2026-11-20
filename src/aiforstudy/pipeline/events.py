"""事件流：给界面与外部观察者一条**可续读**的进度线。

方案 §5：

> 采用**短轮询事件游标**，避免引入消息中间件。
> 建议最小 API：…… `GET /api/runs/{id}/events?after=N` ……

所以事件是**追加式**的，每条带一个单调递增的 `seq`；读取方给 `after=N` 拿 N 之后的部分。
不用消息中间件、不用长连接 —— 一个本地单用户工具引入消息队列，只会多一个会坏的东西。

两条与"账目"有关的要求（§11）也落在事件里：

> run_id、attempt_id、task_id、book_id、scope_kind、unit_key、request fingerprint、
> 模型、重试次数、真实/缓存、token、未知用量**都可追踪**。

所以事件的 `data` 里允许带这些键，但**不许**把密钥或完整提示词放进去（§5：密钥不写日志）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: 事件种类（机器可判，不用自由文本）
KIND_RUN_STARTED = "run.started"
KIND_RUN_FINISHED = "run.finished"
KIND_PLANNED = "run.planned"
KIND_STAGE_STARTED = "stage.started"
KIND_STAGE_FINISHED = "stage.finished"
KIND_STAGE_CACHED = "stage.cached"
KIND_STAGE_BLOCKED = "stage.blocked"
KIND_STAGE_FAILED = "stage.failed"
KIND_STAGE_SKIPPED = "stage.skipped"
KIND_BUDGET_STOP = "budget.stop"
KIND_CANCELLED = "run.cancelled"
KIND_NOTE = "run.note"

TERMINAL_KINDS = (KIND_RUN_FINISHED, KIND_CANCELLED)

#: 不许进事件数据的键（写日志的边界：密钥与原始提示词都不许落进事件流）
FORBIDDEN_DATA_KEYS = frozenset({
    "api_key", "apikey", "authorization", "token", "secret", "password",
    "prompt", "prompts", "messages", "system_prompt",
})


@dataclass(frozen=True)
class Event:
    seq: int
    kind: str
    at: float
    run_id: str = ""
    stage: str = ""
    node_id: str = ""
    scope_key: str = ""
    message: str = ""
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"seq": self.seq, "kind": self.kind, "at": self.at, "run_id": self.run_id,
                "stage": self.stage, "node_id": self.node_id, "scope_key": self.scope_key,
                "message": self.message, "data": dict(self.data)}

    @classmethod
    def from_dict(cls, payload: dict) -> "Event":
        return cls(seq=int(payload.get("seq", 0)), kind=str(payload.get("kind") or ""),
                   at=float(payload.get("at", 0.0)), run_id=str(payload.get("run_id") or ""),
                   stage=str(payload.get("stage") or ""), node_id=str(payload.get("node_id") or ""),
                   scope_key=str(payload.get("scope_key") or ""),
                   message=str(payload.get("message") or ""),
                   data=dict(payload.get("data") or {}))


def _check_data(data: dict) -> None:
    bad = sorted(k for k in data if str(k).lower() in FORBIDDEN_DATA_KEYS)
    if bad:
        raise ValueError(
            f"事件数据里出现了不该落盘的键 {bad}：方案 §5 要求密钥不回传浏览器、不写日志，"
            f"提示词也不该进事件流（它会随时间失效，且体积不可控）。"
            f"请只记引用（如 cache_key / usage_ref）而不是内容。")


class EventLog:
    """追加式事件日志（`events.jsonl`）。**只追加、不重写**。

    为什么不做"读-改-写"：并行 worker 各自读改写会互相覆盖（本项目 `lessons.index.json`
    上真发生过，见 `lib/parallel.py` 的 S5）。追加式天然没有这个问题。
    """

    def __init__(self, path: Path, run_id: str = "") -> None:
        self.path = Path(path)
        self.run_id = run_id
        self._seq = 0
        self._lock = None

    # ── 内部
    def _ensure_lock(self):
        if self._lock is None:
            import threading
            self._lock = threading.Lock()
        return self._lock

    def _scan_last_seq(self) -> int:
        if not self.path.is_file():
            return 0
        last = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                last = max(last, int(json.loads(line).get("seq", 0)))
            except ValueError:
                continue
        return last

    # ── 接口
    def emit(self, kind: str, *, stage: str = "", node_id: str = "", scope_key: str = "",
             message: str = "", at: float | None = None, **data: Any) -> Event:
        _check_data(data)
        with self._ensure_lock():
            self._seq += 1
            event = Event(seq=self._seq, kind=kind, at=at if at is not None else time.time(),
                          run_id=self.run_id, stage=stage, node_id=node_id,
                          scope_key=scope_key, message=message, data=dict(data))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.as_dict(), ensure_ascii=False) + "\n")
                fh.flush()
            return event

    def read(self, after: int = 0, limit: int | None = None) -> list[Event]:
        """短轮询读取：返回 `seq > after` 的事件（方案 §5 的 `?after=N`）。"""
        if not self.path.is_file():
            return []
        out: list[Event] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                # 半写的一行（进程被杀）：跳过并**不**当成"没有事件"
                continue
            if int(payload.get("seq", 0)) <= after:
                continue
            out.append(Event.from_dict(payload))
            if limit is not None and len(out) >= limit:
                break
        return out

    @property
    def last_seq(self) -> int:
        return max(self._seq, self._scan_last_seq())

    def open_for_append(self) -> "EventLog":
        """续写已有日志：把 seq 接到已有最大值之后，不重号。"""
        self._seq = self._scan_last_seq()
        return self


__all__ = ["Event", "EventLog", "FORBIDDEN_DATA_KEYS", "TERMINAL_KINDS",
           "KIND_RUN_STARTED", "KIND_RUN_FINISHED", "KIND_PLANNED", "KIND_STAGE_STARTED",
           "KIND_STAGE_FINISHED", "KIND_STAGE_CACHED", "KIND_STAGE_BLOCKED",
           "KIND_STAGE_FAILED", "KIND_STAGE_SKIPPED", "KIND_BUDGET_STOP", "KIND_CANCELLED",
           "KIND_NOTE"]
