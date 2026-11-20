"""有界返修循环：**严格状态 + 不许隐藏追加轮次**。

方案 §6 的原话：

> 审核返修是**有界循环**，不是无条件向后继续。每轮修订后重新判断配图、规则与审核依赖；
> 内容通过后**冻结教案**，再生成交互。若交互检查发现必须修改正文，**回到内容循环并使下游失效**；
> 达到配置的返修上限仍未通过则 `needs_review`，不发布。默认 `max_repair_rounds=2`，
> **一轮定义为一次 patch/full 内容修订及其依赖复查**；**独立验收失败所引起的修订也消耗剩余轮次，
> 不允许隐藏追加**。独立验收本身和所有模型请求都记入费用预算。

方案 §9 再补一刀：

> 审核成功写出"冻结"报告属于 `succeeded + needs_review`；
> …required 阶段必须 succeeded/cached 且 accepted。

## 三处必须做成"结构上做不到"而不是"记得别做"

1. **轮次不可超**：`max_rounds` 是循环的边界，不是计数器；到顶就出 `needs_review`，
   并把"还剩什么没关"如实列出来。
2. **不许隐藏追加**：独立验收失败引起的修订走**同一个**轮次池。
   实现上只有一个 `rounds_used`，没有第二条加轮次的路 ——
   如果独立验收自己有一个"额外重试"，那 max_repair_rounds 就是装饰。
3. **上游变了要连带失效**：交互检查要求改正文时，`downstream_invalidated` 必须列清楚
   （教案/图/交互/页面/包），否则页面会拿着旧正文的交互去渲染。

本模块不调模型：`repair_fn` / `verify_fn` / `accept_fn` 由调用方注入，
所以它**可以离线、可复现地被测**（替身），真实调用在 M6 接账本之后。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ...contracts.enums import ExecutionState, QualityState
from ...errors import ContractError
from .contracts import SEVERITIES, BLOCKING_SEVERITIES

#: 严重度：P0/P1 必须关闭才能通过；P2 是建议
SEV_P0, SEV_P1, SEV_P2 = SEVERITIES

#: 工单状态
TICKET_OPEN = "open"
TICKET_FIXED = "fixed"            # 改了，但还没核验
TICKET_CLOSED = "closed"          # 核验通过（**只有这个算关闭**）
TICKET_WONTFIX = "wontfix"        # 经认可的"不改"（必须写理由）
TICKET_STATES = (TICKET_OPEN, TICKET_FIXED, TICKET_CLOSED, TICKET_WONTFIX)

#: 返修触及下游时应当失效的东西（§9 的失效表）
DOWNSTREAM_ON_BODY_CHANGE = ("lesson", "figures", "audit", "interaction", "render", "delivery")

DEFAULT_MAX_REPAIR_ROUNDS = 2


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    unit_key: str
    why: str
    evidence: str = ""
    where: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ContractError(f"严重度必须是 {SEVERITIES} 之一，实际 {self.severity!r}")
        if not self.why.strip():
            raise ContractError(f"{self.code}: 发现必须写 why（没有理由的发现无法复核）")

    def key(self) -> tuple[str, str, str]:
        return (self.code, self.unit_key, self.where)

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "unit_key": self.unit_key,
                "why": self.why, "evidence": self.evidence, "where": self.where}


@dataclass
class Ticket:
    finding: Finding
    state: str = TICKET_OPEN
    reason: str = ""
    round_opened: int = 0
    round_closed: int | None = None

    def __post_init__(self) -> None:
        if self.state not in TICKET_STATES:
            raise ContractError(f"工单状态必须是 {TICKET_STATES} 之一，实际 {self.state!r}")
        if self.state == TICKET_WONTFIX and not self.reason.strip():
            raise ContractError(
                f"{self.finding.code}: wontfix 必须写理由 —— "
                f"否则它和'忘了改'在数据上无法区分")

    @property
    def is_blocking(self) -> bool:
        return self.finding.severity in BLOCKING_SEVERITIES and self.state != TICKET_CLOSED

    def as_dict(self) -> dict:
        return {"ticket_id": f"{self.finding.code}@{self.finding.unit_key}",
                "state": self.state, "reason": self.reason,
                "round_opened": self.round_opened, "round_closed": self.round_closed,
                "is_blocking": self.is_blocking, "finding": self.finding.as_dict()}


@dataclass(frozen=True)
class RepairRound:
    index: int
    changed: bool
    opened: tuple[str, ...] = ()
    closed: tuple[str, ...] = ()
    still_open: tuple[str, ...] = ()
    note: str = ""

    def as_dict(self) -> dict:
        return {"index": self.index, "changed": self.changed, "opened": list(self.opened),
                "closed": list(self.closed), "still_open": list(self.still_open),
                "note": self.note}


@dataclass
class AuditOutcome:
    execution_state: ExecutionState
    quality_state: QualityState
    rounds: tuple[RepairRound, ...] = ()
    tickets: tuple[Ticket, ...] = ()
    frozen_report: dict | None = None
    downstream_invalidated: tuple[str, ...] = ()
    reason: str = ""

    @property
    def repair_rounds(self) -> tuple[RepairRound, ...]:
        """真正的**返修**轮次。

        ⚠️ 第 0 轮只是"发现阶段的现状快照"，不是一次返修。第一版把 `len(self.rounds)`
        当轮次用，于是"没有任何发现、根本不需要返修"的情况也报"用了 1 轮" ——
        轮次账目从一开始就虚高，而 `max_repair_rounds` 正是靠这个数当边界的。
        """
        return tuple(r for r in self.rounds if r.index > 0)

    @property
    def max_rounds_used(self) -> int:
        return len(self.repair_rounds)

    @property
    def blocking_open(self) -> tuple[Ticket, ...]:
        return tuple(t for t in self.tickets if t.is_blocking)

    @property
    def can_freeze(self) -> bool:
        """能不能冻结教案。**冻结是内容通过之后的动作**（§6）。"""
        return (self.execution_state.can_feed_required_stage
                and self.quality_state is QualityState.ACCEPTED
                and not self.blocking_open)

    def as_dict(self) -> dict:
        return {"execution_state": self.execution_state.value,
                "quality_state": self.quality_state.value,
                "rounds": [r.as_dict() for r in self.rounds],
                "rounds_used": self.max_rounds_used,
                "tickets": [t.as_dict() for t in self.tickets],
                "blocking_open": [t.as_dict() for t in self.blocking_open],
                "can_freeze": self.can_freeze,
                "frozen_report": self.frozen_report,
                "downstream_invalidated": list(self.downstream_invalidated),
                "reason": self.reason}


def run_bounded_loop(*, unit_key: str, initial_findings: tuple[Finding, ...],
                     repair_fn: Callable[[int, tuple[Ticket, ...]], bool],
                     verify_fn: Callable[[], tuple[Finding, ...]],
                     accept_fn: Callable[[], tuple[bool, str]] | None = None,
                     max_rounds: int = DEFAULT_MAX_REPAIR_ROUNDS,
                     body_changed_fn: Callable[[], bool] | None = None) -> AuditOutcome:
    """跑有界的"发现 → 工单 → 返修 → 关闭核验 → 独立验收"循环。

    参数（都由调用方注入，便于离线替身测试）：

    * `repair_fn(round_index, open_tickets) -> changed?` —— 做一次 patch/full 修订；
    * `verify_fn() -> findings` —— **重新判断**（不是复用上一轮的结论）；
    * `accept_fn() -> (passed, reason)` —— 独立验收；**失败也消耗轮次**；
    * `body_changed_fn() -> bool` —— 本轮是否改了正文；改了就连带失效下游。
    """
    if max_rounds < 1:
        raise ContractError(f"max_rounds 必须 ≥1，实际 {max_rounds}")

    tickets: dict[tuple[str, str, str], Ticket] = {}
    for finding in initial_findings:
        tickets[finding.key()] = Ticket(finding=finding, round_opened=0)

    rounds: list[RepairRound] = []
    invalidated: tuple[str, ...] = ()
    index = 0

    def repairs_done() -> int:
        """已经做过的**返修**轮数（不含第 0 轮的现状快照）。"""
        return sum(1 for r in rounds if r.index > 0)

    def open_blocking() -> tuple[Ticket, ...]:
        return tuple(t for t in tickets.values() if t.is_blocking)

    def snapshot_round(idx: int, changed: bool, opened: list[str], closed: list[str],
                       note: str = "") -> None:
        rounds.append(RepairRound(index=idx, changed=changed, opened=tuple(opened),
                                  closed=tuple(closed),
                                  still_open=tuple(f"{t.finding.code}@{t.finding.unit_key}"
                                                   for t in open_blocking()),
                                  note=note))

    # 第 0 轮：只看现状。**发现阶段没抓到 P0/P1 就直接过**（不为了"跑满"而返修）。
    if not open_blocking():
        verify_findings = verify_fn()
        for finding in verify_findings:
            key = finding.key()
            if key not in tickets:
                tickets[key] = Ticket(finding=finding, round_opened=0)
        snapshot_round(0, changed=False, opened=[f.key() for f in verify_findings], closed=[],
                       note="发现阶段没有 P0/P1")

    while open_blocking():
        if index >= max_rounds:
            break
        index += 1
        before = {k: t.state for k, t in tickets.items()}
        changed = bool(repair_fn(index, open_blocking()))
        body_changed = bool(body_changed_fn()) if body_changed_fn else changed
        if body_changed and not invalidated:
            invalidated = DOWNSTREAM_ON_BODY_CHANGE

        # 修订后**重新判断**依赖（§6：每轮修订后重新判断配图、规则与审核依赖）
        fresh = verify_fn()
        opened: list[str] = []
        for finding in fresh:
            key = finding.key()
            if key not in tickets:
                tickets[key] = Ticket(finding=finding, round_opened=index)
                opened.append(f"{finding.code}@{finding.unit_key}")

        closed: list[str] = []
        fresh_keys = {f.key() for f in fresh}
        for key, ticket in tickets.items():
            if ticket.state in (TICKET_CLOSED, TICKET_WONTFIX):
                continue
            if key not in fresh_keys:
                ticket.state = TICKET_CLOSED
                ticket.round_closed = index
                closed.append(f"{ticket.finding.code}@{ticket.finding.unit_key}")
            elif ticket.state == TICKET_OPEN and changed:
                # ⚠️ 只有**这一轮真的改了东西**才把它标成 fixed。
                #    第一版无条件标 fixed，于是"什么都没改"的一轮也会让工单看起来
                #    往前走了一步，提前停止的判断因此永不触发（实测：本该 1 轮停，
                #    实际跑满 2 轮）。
                ticket.state = TICKET_FIXED
        snapshot_round(index, changed, opened, closed)
        # 若这一轮什么都没变、也没有工单被关，再转下去只是烧钱
        if not changed and not opened and not closed and \
                all(t.state == before.get(k, t.state) for k, t in tickets.items()):
            rounds[-1] = RepairRound(index=index, changed=False,
                                     opened=tuple(opened), closed=tuple(closed),
                                     still_open=rounds[-1].still_open,
                                     note="本轮没有产生任何变化 —— 提前停止，不空转")
            break

    # 内容侧结论
    blocking = open_blocking()
    if blocking:
        return AuditOutcome(
            execution_state=ExecutionState.SUCCEEDED,
            quality_state=QualityState.NEEDS_REVIEW,
            rounds=tuple(rounds), tickets=tuple(tickets.values()),
            downstream_invalidated=invalidated,
            reason=(f"达到返修上限 {max_rounds} 轮仍有 {len(blocking)} 个 P0/P1 未关闭："
                    f"{[f'{t.finding.code}@{t.finding.unit_key}' for t in blocking[:5]]}。"
                    f"方案 §6：达到上限仍未通过则 needs_review，**不发布**；"
                    f"独立验收失败引起的修订也消耗同一轮次池，不允许隐藏追加。"))

    # 独立验收：**失败也消耗轮次**
    if accept_fn is not None:
        passed, why = accept_fn()
        if not passed:
            return AuditOutcome(
                execution_state=ExecutionState.SUCCEEDED,
                quality_state=QualityState.NEEDS_REVIEW,
                rounds=tuple(rounds), tickets=tuple(tickets.values()),
                downstream_invalidated=invalidated,
                reason=(f"独立验收未通过：{why}。已消耗 {repairs_done()}/{max_rounds} 轮；"
                        f"它引起的修订走同一个轮次池，**不允许隐藏追加**（方案 §6）。"))

    frozen = {"unit_key": unit_key, "rounds_used": repairs_done(),
              "frozen": True,
              "frozen_after": "内容通过并已冻结教案，之后才允许生成交互（方案 §6）"}
    return AuditOutcome(
        execution_state=ExecutionState.SUCCEEDED, quality_state=QualityState.ACCEPTED,
        rounds=tuple(rounds), tickets=tuple(tickets.values()), frozen_report=frozen,
        downstream_invalidated=invalidated,
        reason=f"内容通过：{repairs_done()} 轮内关闭全部 P0/P1，独立验收通过")


def interaction_requires_body_change(findings: tuple[Finding, ...]) -> bool:
    """交互检查发现"必须修改正文"——这时要**回到内容循环并让下游失效**（§6）。"""
    return any(f.code.startswith("INTERACTION_BODY") and f.severity in BLOCKING_SEVERITIES
               for f in findings)


__all__ = ["SEV_P0", "SEV_P1", "SEV_P2", "SEVERITIES", "BLOCKING_SEVERITIES", "TICKET_OPEN",
           "TICKET_FIXED", "TICKET_CLOSED", "TICKET_WONTFIX", "TICKET_STATES",
           "DOWNSTREAM_ON_BODY_CHANGE", "DEFAULT_MAX_REPAIR_ROUNDS", "Finding", "Ticket",
           "RepairRound", "AuditOutcome", "run_bounded_loop",
           "interaction_requires_body_change"]
