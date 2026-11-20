"""唯一返修协调器：**分类、批次、去重、失效、早停**都在这里。

## 为什么必须"唯一"

改造前的返修有三个**互不知情**的轮次池（调研 §1.2 最后一行）：

* 已退役的 `audit_stage` 内循环（早期实现，已不存在）；
* `audit` 内部自己调 `figures_stage()` 重新出图，图后端还自带收费 retry loop；
* 末尾还有一次"整篇重新生成"。

于是"每个对象最多两轮返修"这句话**在代码里没有一处是真的**：三个池各花各的，
报告上只看得到"跑了很久"。本模块把那句话变成一个**只有一条路**的入口：
阶段函数跑一遍、返回 `StageResult.repair_requests`，由 `RepairCoordinator.plan()`
决定要不要修、修什么、谁去修、算不算重新获得轮次。

## 两类返修，两个不同的账

| 类别 | kind | 消耗内容批次 | 上限 |
|---|---|---|---|
| 内容返修（正文／数学规格／分镜／布局） | `text_content` / `visual_math` / `visual_layout` | **是** | 2 轮／对象，**跨阶段、跨 resume 共用** |
| 证据格式修复（引用定位、span、quote） | `evidence_format` | 否 | 每个实质 verdict 1 次；**重复内容继承次数** |

"证据修复不消耗内容批次"这一条有个必须防的漏洞：既然不消耗批次，那就能无限次修证据？
所以证据修复按 **verdict 内容身份**（`contracts.verdict_identity`，**不含 verdict_id**）
计数，同一个判词换个 ID 也不能重获一次机会（§4.7）。

## 本模块不做的事

不写文件、不调模型、不跑阶段、不看时钟。它只有纯逻辑与可序列化状态，
所以"两批跨阶段共享""证据修复不重置预算""同单元不许并发改"这些都能离线、可复现地测。
真正的调度在 `runner.py`（唯一一次接线），失效集合在 `resource_binding.py`（T05）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from ..blocks.audit.contracts import (OWNERS, CriterionVerdict, PatchOutcome, RepairDirective,
                                       RepairTicket, verdict_identity, normalize_verdict_content)
from ..core.digest import canonical_json
from ..errors import ContractError

@dataclass(frozen=True)
class RepairRouteV1:
    ticket_id: str
    origin_stage: str
    target_artifact_kind: str
    target_artifact_id: str

    def __post_init__(self):
        from .graph import STAGE_BY_NAME
        if not self.ticket_id or not self.target_artifact_id:
            raise ContractError("RepairRouteV1 ticket_id/target_artifact_id 必填")
        if self.origin_stage not in STAGE_BY_NAME:
            raise ContractError(f"RepairRouteV1 未知 origin_stage {self.origin_stage!r}")
        if self.target_artifact_kind not in self.TARGETS:
            raise ContractError(f"RepairRouteV1 未知 target_artifact_kind {self.target_artifact_kind!r}")

    TARGETS = {"content": "lesson", "author_media": "lesson", "media_requirements": "media_plan",
               "visual_spec": "figures", "media_resource": "figures",
               "interaction_spec": "interaction", "evidence": None, "media_evidence": "figures"}

    @property
    def target_stage(self):
        return self.TARGETS[self.target_artifact_kind] or self.origin_stage

    def as_dict(self):
        return {"schema_version": "RepairRouteV1", **{k: getattr(self,k) for k in ("ticket_id","origin_stage","target_artifact_kind","target_artifact_id")}}

    @classmethod
    def from_dict(cls, row):
        keys={"ticket_id","origin_stage","target_artifact_kind","target_artifact_id"}
        if "schema_version" in row:
            if row["schema_version"] != "RepairRouteV1":
                raise ContractError("RepairRouteV1 schema_version 错误")
            row = {k:v for k,v in row.items() if k != "schema_version"}
        if set(row) != keys:
            raise ContractError(f"RepairRouteV1 字段必须为 {sorted(keys)}")
        return cls(**row)


#: 消耗**内容返修批次**的 kind
CONTENT_KINDS = ("text_content", "visual_math", "visual_layout")
#: 走**证据格式修复**账（每个判词一次）的 kind
EVIDENCE_KINDS = ("evidence_format",)


def content_ticket_dispatch(ticket: RepairTicket) -> tuple[str, str, str]:
    """One classification for content directives and their registered routes.

    Reviewer work changes evidence only; unverified source disputes create no
    repair directive.
    """
    if (ticket.kind == 'evidence_format' or ticket.owner == 'reviewer'
            or not ticket.is_dispatchable):
        return 'reviewer', 'evidence_format', 'evidence'
    return ticket.owner, ticket.kind, 'content'

#: 内容改动后必须失效的下游（§4.2.3 的失效集合：只有依赖仍相同的才能复用）
INVALIDATION_BY_KIND: dict[str, tuple[str, ...]] = {
    "text_content": ("lesson", "text_audit", "media_plan", "figures", "audit", "interaction", "render",
                     "delivery"),
    # 数学规格变了：正文不用动，但图、实际帧与终审要重来
    "visual_math": ("figures", "audit", "interaction", "render", "delivery"),
    # 只动布局／镜头：来源与数学证据可复用（§6.2 的失效表）
    "visual_layout": ("figures", "audit", "render", "delivery"),
    # 证据格式修复：内容与全部媒体字节都保留，只重做证据定位与关闭核验
    "evidence_format": ("audit",),
}


def _digest(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
@dataclass
class UnitRepairState:
    """一个单元的返修账。**这个对象就是"轮次"的唯一真相**，续跑时原样恢复。"""
    unit_id: str
    content_rounds_used: int = 0
    content_rounds_by_artifact: dict[str, int] = field(default_factory=dict)
    evidence_attempts_by_verdict: dict[str, int] = field(default_factory=dict)
    active_batch_id: str | None = None
    active_batch_context: dict = field(default_factory=dict)
    artifact_heads: dict[str, str] = field(default_factory=dict)
    last_candidate_signature: str = ""
    #: R3-3：上次判词对应的正文摘要。早停必须"**正文和判词都没变**"，
    #: 否则"改了内容但审核话没变"会被误判为没进展而提前停下。
    last_candidate_content: tuple = ()
    patch_feedback_by_artifact: dict[str, dict] = field(default_factory=dict)
    policy_digest: str = ""
    history: list[dict] = field(default_factory=list)
    stopped_reason: str = ""

    def __post_init__(self) -> None:
        if not str(self.unit_id or "").strip():
            raise ContractError("UnitRepairState.unit_id 必填")
        if self.content_rounds_used < 0:
            raise ContractError("content_rounds_used 不能为负")
        if any(type(v) is not int or v < 0 for v in self.content_rounds_by_artifact.values()):
            raise ContractError('对象返修次数必须是非负整数')

    # ── 序列化（resume 要能原样恢复）
    def as_dict(self) -> dict:
        return {"unit_id": self.unit_id, "content_rounds_used": self.content_rounds_used,
                'content_rounds_by_artifact': dict(sorted(self.content_rounds_by_artifact.items())),
                "evidence_attempts_by_verdict": dict(sorted(
                    self.evidence_attempts_by_verdict.items())),
                "active_batch_id": self.active_batch_id,
                'active_batch_context': dict(self.active_batch_context),
                "artifact_heads": dict(sorted(self.artifact_heads.items())),
                "last_candidate_signature": self.last_candidate_signature,
                "last_candidate_content": list(self.last_candidate_content),
                "patch_feedback_by_artifact": dict(self.patch_feedback_by_artifact),
                "policy_digest": self.policy_digest,
                "history": [dict(row) for row in self.history],
                "stopped_reason": self.stopped_reason}

    @classmethod
    def from_dict(cls, data: dict) -> "UnitRepairState":
        return cls(unit_id=str(data.get("unit_id") or ""),
                   content_rounds_used=int(data.get("content_rounds_used") or 0),
                   content_rounds_by_artifact=dict(data.get('content_rounds_by_artifact') or {}),
                   evidence_attempts_by_verdict=dict(
                       data.get("evidence_attempts_by_verdict") or {}),
                   active_batch_id=data.get("active_batch_id"),
                   active_batch_context=dict(data.get('active_batch_context') or {}),
                   artifact_heads=dict(data.get("artifact_heads") or {}),
                   last_candidate_signature=str(data.get("last_candidate_signature") or ""),
                   last_candidate_content=tuple(tuple(row) for row in data.get("last_candidate_content") or ()),
                   patch_feedback_by_artifact=dict(data.get('patch_feedback_by_artifact') or {}),
                   policy_digest=str(data.get("policy_digest") or ""),
                   history=[dict(row) for row in (data.get("history") or [])],
                   stopped_reason=str(data.get("stopped_reason") or ""))


# --------------------------------------------------------------------------- #
# 批次与作业
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RepairJob:
    """一个**具名**的返修作业：谁、改哪个 artifact、关哪些工单。

    `unit_id` / `kind` 比 §T04 列的最低字段多两个：没有它们，`accept_result`
    只能靠解析 `artifact_id` 反推单元与失效范围 —— 那种"从字符串里猜身份"的做法
    正是本项目反复出现问题的来源。
    """
    job_id: str
    owner: str
    artifact_id: str
    ticket_ids: tuple[str, ...]
    unit_id: str = ""
    kind: str = "text_content"

    def __post_init__(self) -> None:
        for name in ("job_id", "artifact_id"):
            if not str(getattr(self, name) or "").strip():
                raise ContractError(f"RepairJob.{name} 必填")
        if self.owner not in OWNERS:
            raise ContractError(f"RepairJob.owner 必须是 {list(OWNERS)}，实际 {self.owner!r}")
        if not self.ticket_ids:
            raise ContractError("RepairJob 至少要指向一个工单")

    def as_dict(self) -> dict:
        return {"job_id": self.job_id, "owner": self.owner,
                "artifact_id": self.artifact_id, "ticket_ids": list(self.ticket_ids),
                "unit_id": self.unit_id, "kind": self.kind}

    @classmethod
    def from_dict(cls, data: dict) -> "RepairJob":
        """JSON → job。**必须有**：靠调用方自己拼 kwargs 会漏掉 tuple 化，
        于是"读回来"的对象与原件不相等，而它看上去一切正常。"""
        return cls(job_id=str(data.get("job_id") or ""),
                   owner=str(data.get("owner") or ""),
                   artifact_id=str(data.get("artifact_id") or ""),
                   ticket_ids=tuple(data.get("ticket_ids") or ()),
                   unit_id=str(data.get("unit_id") or ""),
                   kind=str(data.get("kind") or "text_content"))


@dataclass(frozen=True)
class RepairBatch:
    """一批**串行**执行的返修作业。同一 artifact 只有一个 owner，不并发写同一版本。"""
    batch_id: str
    unit_id: str
    ticket_ids: tuple[str, ...]
    jobs: tuple[RepairJob, ...]
    base_hashes: dict[str, str]
    policy_digest: str
    round: int = 0
    consumes_content_round: bool = False

    def __post_init__(self) -> None:
        if not self.jobs:
            raise ContractError("RepairBatch 至少要有一个 job")
        artifact_ids = [job.artifact_id for job in self.jobs]
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ContractError(
                "同一批里有多个 job 指向同一个 artifact —— 那会让两个 job 竞争写同一版本")
        if self.consumes_content_round and self.round < 1:
            raise ContractError("消耗内容批次的 RepairBatch.round 必须从 1 起算")

    def as_dict(self) -> dict:
        return {"batch_id": self.batch_id, "unit_id": self.unit_id, "round": self.round,
                "ticket_ids": list(self.ticket_ids),
                "jobs": [job.as_dict() for job in self.jobs],
                "base_hashes": dict(self.base_hashes),
                "policy_digest": self.policy_digest,
                "consumes_content_round": bool(self.consumes_content_round)}

    @classmethod
    def from_dict(cls, data: dict) -> "RepairBatch":
        return cls(batch_id=str(data.get("batch_id") or ""),
                   unit_id=str(data.get("unit_id") or ""),
                   ticket_ids=tuple(data.get("ticket_ids") or ()),
                   jobs=tuple(RepairJob.from_dict(job) for job in data.get("jobs") or ()),
                   base_hashes=dict(data.get("base_hashes") or {}),
                   policy_digest=str(data.get("policy_digest") or ""),
                   round=int(data.get("round") or 0),
                   consumes_content_round=bool(data.get("consumes_content_round")))


@dataclass(frozen=True)
class StopDecision:
    """**不再自动返修**的判定。必须写清还剩什么、下一步谁做什么。"""
    reason: str
    unresolved: tuple[str, ...] = ()
    next_action: str = ""
    stopped_by: str = ""

    def __post_init__(self) -> None:
        if not str(self.reason or "").strip():
            raise ContractError("StopDecision.reason 必填 —— 停下来的理由必须能复核")
        if not str(self.next_action or "").strip():
            raise ContractError(
                "StopDecision.next_action 必填 —— 只说'停了'不给出路，等于把问题丢回给用户")

    def as_dict(self) -> dict:
        return {"decision": "stop", "reason": self.reason,
                "unresolved": list(self.unresolved), "next_action": self.next_action,
                "stopped_by": self.stopped_by}

    @classmethod
    def from_dict(cls, data: dict) -> "StopDecision":
        return cls(reason=str(data.get("reason") or ""),
                   unresolved=tuple(data.get("unresolved") or ()),
                   next_action=str(data.get("next_action") or ""),
                   stopped_by=str(data.get("stopped_by") or ""))


# --------------------------------------------------------------------------- #
# 协调器
# --------------------------------------------------------------------------- #
class RepairCoordinator:
    """**唯一**的返修入口。阶段只提交 `RepairDirective`，不自己调用别的阶段。"""

    def __init__(self, policy, *, max_content_rounds: int | None = None,
                 max_evidence_per_verdict: int | None = None) -> None:
        self.policy = policy
        self.max_content_rounds = int(
            max_content_rounds if max_content_rounds is not None
            else getattr(policy, "content_repair_rounds_per_object", 6))
        self.max_evidence_per_verdict = int(
            max_evidence_per_verdict if max_evidence_per_verdict is not None
            else getattr(policy, "evidence_repairs_per_verdict", 1))
        if self.max_content_rounds < 0 or self.max_evidence_per_verdict < 0:
            raise ContractError("返修上限不能为负")

    @property
    def policy_digest(self) -> str:
        return str(getattr(self.policy, "digest", lambda: "")())

    # ── 分类
    @staticmethod
    def classify(kind: str) -> str:
        """`content` / `evidence`。未知 kind 明确报错，不默认当内容。"""
        if kind in CONTENT_KINDS:
            return "content"
        if kind in EVIDENCE_KINDS:
            return "evidence"
        raise ContractError(
            f"未知的返修类别 {kind!r}；已定义：内容 {list(CONTENT_KINDS)}、"
            f"证据 {list(EVIDENCE_KINDS)}")

    def signature(self, directives: Iterable[RepairDirective],
                  artifact_heads: dict[str, str] | None = None,
                  patch_feedback: dict[str, dict] | None = None) -> str:
        """Compare substantive judgments across versions; IDs are not progress.

        Stable artifact names keep independent objects separate. Actual criteria,
        reasons and host patch feedback identify genuinely new repair information.
        """
        rows = []
        for directive in directives:
            content = directive.verdict_content or {'why': directive.reason}
            rows.append({'owner': directive.owner, 'kind': directive.kind,
                         'artifacts': sorted(directive.artifact_hashes),
                         'verdict': normalize_verdict_content(content)})
        rows.sort(key=canonical_json)
        payload = {'directives': rows}
        relevant = {artifact for row in rows for artifact in row['artifacts']}
        feedback = {artifact: normalize_verdict_content(row.get('feedback') or row)
                    for artifact, row in (patch_feedback or {}).items()
                    if artifact in relevant and isinstance(row, dict) and row.get('feedback_digest')}
        if feedback:
            payload['patch_feedback'] = feedback
        return _digest(payload)

    def plan(self, directives: list[RepairDirective],
             unit_state: UnitRepairState) -> RepairBatch | StopDecision:
        """决定这一轮修什么。返回批次或停止判定 —— **不返回"随便试试"**。"""
        if not directives:
            return StopDecision(reason="没有待返修的工单",
                                next_action="按现有产物继续；若标准未过则转 needs_review",
                                stopped_by="no_directives")
        units = {directive.unit_id for directive in directives}
        if units != {unit_state.unit_id}:
            raise ContractError(
                f"同一批 directive 混了多个单元 {sorted(units)}，而 state 是 "
                f"{unit_state.unit_id!r} —— 返修批次必须按单元隔离")

        if unit_state.active_batch_id:
            return StopDecision(
                reason=f"单元 {unit_state.unit_id} 还有未收尾的返修批次 "
                       f"{unit_state.active_batch_id}",
                unresolved=tuple(sorted({t for d in directives for t in d.ticket_ids})),
                next_action="等当前批次 accept_result 之后再排下一批，"
                            "不得让下游并发消费旧产物",
                stopped_by="batch_in_flight")

        content = [d for d in directives if self.classify(_kind_of(d)) == "content"]
        evidence = [d for d in directives if self.classify(_kind_of(d)) == "evidence"]

        all_ticket_ids = tuple(sorted({t for d in directives for t in d.ticket_ids}))
        # Exhausted evidence stays unresolved but must never be dispatched again.
        blocked_evidence = []
        eligible_evidence = []
        for directive in evidence:
            allowed = tuple(t for t in directive.ticket_ids
                if unit_state.evidence_attempts_by_verdict.get(t, 0) < self.max_evidence_per_verdict)
            blocked_evidence.extend(t for t in directive.ticket_ids if t not in allowed)
            if allowed:
                eligible_evidence.append(replace(directive, ticket_ids=allowed))
        evidence = eligible_evidence
        # Evidence for an artifact precedes its content edit. The reviewer sees
        # all tickets, while only one owner may write a given revision.
        evidence_artifacts = {_artifact_of(d) for d in evidence}
        content = [d for d in content if _artifact_of(d) not in evidence_artifacts]
        exhausted = [d for d in content if unit_state.content_rounds_by_artifact.get(
            _artifact_of(d), 0) >= self.max_content_rounds]
        content = [d for d in content if d not in exhausted]
        exhausted_content = bool(exhausted)
        consumes = bool(content)
        if not content and not evidence:
            return StopDecision(
                reason=(f"这些对象的内容返修次数已用尽：{sorted({_artifact_of(d) for d in exhausted})}"
                        f"（每对象上限 {self.max_content_rounds}）"
                        if exhausted_content else f"这些判词的证据修复次数已用尽：{sorted(set(blocked_evidence))}"),
                unresolved=all_ticket_ids,
                next_action="保留候选、未过标准与账本，标记自动处理未决，其他对象继续；不得重置次数",
                stopped_by="content_rounds_exhausted" if exhausted_content else "evidence_attempts_exhausted")

        # ── 无进展早停（R3-3：**正文和判词都没变**才算没进展）
        # 旧口径只看判词签名，会把"这一轮真的改了正文、但审核又给了同样的话"误停。
        signature = self.signature(directives, unit_state.artifact_heads, unit_state.patch_feedback_by_artifact)
        content_now = tuple(sorted((str(key), str(value))
                                   for key, value in (unit_state.artifact_heads or {}).items()))
        if (signature and signature == unit_state.last_candidate_signature
                and content_now == tuple(unit_state.last_candidate_content or ())):
            return StopDecision(
                reason=("同一判词内容重复出现且正文未变；审核没有提供新信息，停止自动返修"),
                unresolved=tuple(sorted({t for d in directives for t in d.ticket_ids})),
                next_action="停止自动返修；保留最后证据，列出剩余未过标准并继续其他对象",
                stopped_by="no_progress")

        # ── 合并：同一 artifact 的多个问题只派给唯一 owner，绝不两个 job 抢同一版本
        merged: dict[tuple[str, str], list[RepairDirective]] = {}
        by_artifact = {}
        for directive in content + evidence:
            by_artifact.setdefault(_artifact_of(directive), []).append(directive)
        for artifact, group in by_artifact.items():
            owner = min((d.owner for d in group), key=lambda o:
                ({"reviewer":0, "visual_planner":1, "visual_renderer":2}.get(o, 3), o))
            merged[(owner, artifact)] = group

        jobs = []
        for index, ((owner, artifact_id), group) in enumerate(sorted(merged.items()), start=1):
            ticket_ids = sorted({t for d in group for t in d.ticket_ids})
            kinds = {_kind_of(d) for d in group}
            job_kind = "evidence_format" if kinds == {"evidence_format"} else (
                sorted(kinds)[0] if len(kinds) == 1 else "visual_math" if "visual_math" in kinds else "text_content")
            jobs.append(RepairJob(
                job_id=f"{unit_state.unit_id}::{owner}::{artifact_id}::{index}",
                owner=owner, artifact_id=artifact_id, ticket_ids=tuple(ticket_ids),
                unit_id=unit_state.unit_id, kind=job_kind))

        base_hashes = {job.artifact_id: (unit_state.artifact_heads.get(job.artifact_id)
                                         or _hash_of_directive(directives, job.artifact_id))
                       for job in jobs}
        round_number = max((unit_state.content_rounds_by_artifact.get(_artifact_of(d), 0)+1
                            for d in content), default=0)
        batch_id = _digest({"unit": unit_state.unit_id, "round": round_number,
                            "jobs": [job.as_dict() for job in jobs],
                            "consumes": consumes})[:24]
        return RepairBatch(
            batch_id=batch_id, unit_id=unit_state.unit_id,
            ticket_ids=all_ticket_ids,
            jobs=tuple(jobs), base_hashes=base_hashes, policy_digest=self.policy_digest,
            round=round_number, consumes_content_round=consumes)

    # ── 结算
    def accept_result(self, job: RepairJob, outcome: PatchOutcome,
                      *, revision_hash: str = "") -> dict:
        """提交一个作业的结果，并报告本次是否**真的改了内容**。

        ⚠️ 实际计费口径（以代码为准，2026-10-11 核对）：内容轮次在**批次排队时**就已预扣
        （`runner.py:1003-1007`），**被打回也不退还** —— 那是刻意的，为的是
        "失败与崩溃在 resume 时不能把次数还回来"。因此这里返回的
        `content_round_consumed` 只是**上报事实**，**没有任何调用方拿它回滚计数**；
        耗尽判定只看 `content_rounds_by_artifact`（`repair.py:389-391`）。

        实际效果：每个对象是"最多 2 次**提交**机会"，被接口问题（如 outdated hash）
        打回同样吃掉一次。这不是 bug，是"防崩溃"与"被打回免费重试"两个目标取舍后
        选了前者；要改成后者必须同时保住"崩溃不回退"。
        """
        if not isinstance(outcome, PatchOutcome):
            raise ContractError("accept_result 需要 PatchOutcome；"
                                "证据修复请走 accept_evidence，不要伪造内容结果")
        class_kind = self.classify(job.kind)
        consumed = class_kind == "content" and outcome.applied
        invalidated = INVALIDATION_BY_KIND.get(job.kind, ()) if outcome.applied else ()
        return {"job_id": job.job_id, "unit_id": job.unit_id,
                "content_round_consumed": consumed,
                "invalidated": list(invalidated),
                "revision_hash": revision_hash or None,
                "rejected": [r.as_dict() for r in outcome.rejected],
                "reason": outcome.reason}

    def accept_evidence(self, verdict: CriterionVerdict,
                        unit_state: UnitRepairState) -> dict:
        """结算一次**证据格式修复**：不碰内容批次，也**不重置任何预算**。"""
        identity = verdict_identity(verdict)
        used = unit_state.evidence_attempts_by_verdict.get(identity, 0) + 1
        unit_state.evidence_attempts_by_verdict[identity] = used
        return {"verdict_identity": identity, "attempts_used": used,
                "remaining": max(0, self.max_evidence_per_verdict - used),
                "content_rounds_used": unit_state.content_rounds_used}


def _kind_of(directive: RepairDirective) -> str:
    """directive 的返修类别。**必须显式给**，不给就报错。

    第一版打算"从 `reason` 文本前缀里猜类别"。那是错的：猜出来的类别决定
    这条工单算不算消耗内容批次，于是**改一句提示词写法就能让"每单元两批"失效**。
    类别是账目依据，必须由调用方显式声明（`RepairDirective.kind`）。
    """
    if not str(directive.kind or "").strip():
        raise ContractError(
            f"RepairDirective({directive.unit_id}) 没有给 kind —— 返修类别决定它"
            f"是否消耗内容批次，不能靠猜。请显式给 {list(CONTENT_KINDS + EVIDENCE_KINDS)} 之一")
    return directive.kind


def _artifact_of(directive: RepairDirective) -> str:
    hashes = sorted(directive.artifact_hashes)
    return hashes[0] if hashes else f"unit:{directive.unit_id}"


def _hash_of_directive(directives: Iterable[RepairDirective], artifact_id: str) -> str:
    for directive in directives:
        if artifact_id in directive.artifact_hashes:
            return directive.artifact_hashes[artifact_id]
    return ""


__all__ = ["CONTENT_KINDS", "EVIDENCE_KINDS", "INVALIDATION_BY_KIND",
           "UnitRepairState", "RepairJob", "RepairBatch", "StopDecision",
           "RepairCoordinator"]
