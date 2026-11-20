"""审核块（方案 §3 的 `blocks/audit/`：发现、工单、关闭核验、独立验收）。

M5 落地 `loop.py` —— **有界返修循环**：严格状态、轮次不可超、独立验收失败消耗同一轮次池、
改正文时连带失效下游（§6/§9）。它不调模型：`repair_fn`/`verify_fn`/`accept_fn` 由调用方注入，
所以能离线可复现地被测。
"""
from __future__ import annotations

from .loop import (BLOCKING_SEVERITIES, DEFAULT_MAX_REPAIR_ROUNDS,
                   DOWNSTREAM_ON_BODY_CHANGE, SEV_P0, SEV_P1, SEV_P2, TICKET_CLOSED,
                   TICKET_FIXED, TICKET_OPEN, TICKET_WONTFIX, AuditOutcome, Finding,
                   RepairRound, Ticket, interaction_requires_body_change, run_bounded_loop)

__all__ = ["BLOCKING_SEVERITIES", "DEFAULT_MAX_REPAIR_ROUNDS", "DOWNSTREAM_ON_BODY_CHANGE",
           "SEV_P0", "SEV_P1", "SEV_P2", "TICKET_CLOSED", "TICKET_FIXED", "TICKET_OPEN",
           "TICKET_WONTFIX", "AuditOutcome", "Finding", "RepairRound", "Ticket",
           "interaction_requires_body_change", "run_bounded_loop"]
