"""`StageResult` —— 一个阶段跑完之后的**完整交代**（方案 §6 契约表）。

> | StageResult | execution_state、quality_state、inputs/outputs、findings、usage ref、
>   attempt、时间、错误、可恢复位置 |

它替掉的是过去那种"函数返回 bool 或抛异常"。那种写法丢掉了三样东西，而三样都是必须的：

1. **跑完了但内容不合格**（`succeeded + rejected`）——出错了图就是这一类；
2. **根本没跑成**（`blocked/failed`）——缺后端、缺缓存回放；
3. **合法的"不适用"**（`not_applicable`）——但不能由"缺工具/没运行"冒充。

方案 §9 把规则写死了，这里把它变成构造时就会失败的断言：

* `blocked/failed/cancelled` **必须**带 `error`（否则下游只能看到"没结果"却不知道为什么）；
* `not_applicable` **必须**带 `policy` 与 `evidence`；
* `quality_state=accepted` **必须**建立在 `succeeded/cached` 之上。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..errors import ContractError
from .artifact import ArtifactRef
from .enums import ExecutionState, QualityState

_NEED_ERROR = (ExecutionState.BLOCKED, ExecutionState.FAILED, ExecutionState.CANCELLED)


@dataclass
class StageResult:
    stage: str
    execution_state: ExecutionState
    quality_state: QualityState = QualityState.NOT_CHECKED
    inputs: tuple[ArtifactRef, ...] = ()
    outputs: tuple[ArtifactRef, ...] = ()
    findings: list[dict] = field(default_factory=list)
    usage_ref: str | None = None
    attempt: int = 1
    started_at: float | None = None
    finished_at: float | None = None
    error: dict | None = None
    resumable_at: str | None = None
    #: 本次用量的**结构化**记账（方案 §11：失败、重试、缓存命中分别记账）。
    #: 约定键：`tokens`（整数）或 `unknown=True`（花了但数不出来）。
    #: **空字典 = 这个阶段不产生模型用量**（确定性阶段），不是"用量为 0 所以免费"。
    usage: dict = field(default_factory=dict)
    #: only for `not_applicable`：是哪条政策允许它不适用
    policy: str | None = None
    #: only for `not_applicable`：证据（不能让"没运行"冒充）
    evidence: str | None = None
    #: §4.2.2：阶段**只提交请求**，由 `RepairCoordinator` 决定修不修。
    #: 默认空元组；旧结果缺这个字段时**只解释为空**，不补造工单。
    #: 类型是 `tuple[RepairDirective, ...]`，JSON 里序列化成列表。
    repair_requests: tuple = ()
    #: 这个阶段**实际读过**的工作文件（相对 run 工作目录的路径，或绝对路径）。
    #
    # ⚠️ 为什么需要它：有些阶段读的文件不在任何节点的**输出**清单里
    # （例如 `audit` 读 `work/legacy/lesson/lessons/<单元>.json`，而 `lesson` 阶段登记的
    # 产物是 `products/lesson/<单元>.json` 这个外层封装）。那些文件于是对"缓存复用"
    # 与"上游指纹"两条路径都**不可见** —— 外部改了它，下游照样拿旧判词。
    # 实测：`test_final_evidence_cannot_hide_changed_inputs_or_artifacts[lesson]`。
    #
    # 声明在这里的路径会被 runner 记进节点状态，并在算输入指纹时**重新读盘**校验。
    # 语义很直白：**"我读过这个文件"是必须声明的事实，声明了才会进缓存键。**
    input_files: tuple = ()
    #: Routing envelope is independent of strict RepairTicket v2. Old results default empty.
    repair_routes: tuple = ()

    def __post_init__(self) -> None:
        if not str(self.stage or "").strip():
            raise ContractError("StageResult.stage 必填")
        object.__setattr__(self, "repair_requests", _coerce_repair_requests(self))
        from ..pipeline.repair import RepairRouteV1
        routes = tuple(r.as_dict() if isinstance(r, RepairRouteV1) else RepairRouteV1.from_dict(r).as_dict()
                       for r in self.repair_routes)
        if len({r["ticket_id"] for r in routes}) != len(routes):
            raise ContractError("repair_routes 包含重复 ticket_id")
        object.__setattr__(self, "repair_routes", routes)
        try:
            object.__setattr__(self, "execution_state", ExecutionState(str(self.execution_state)))
        except ValueError as exc:
            raise ContractError(
                f"execution_state 必须是 {[e.value for e in ExecutionState]} 之一，"
                f"实际是 {self.execution_state!r}") from exc
        try:
            object.__setattr__(self, "quality_state", QualityState(str(self.quality_state)))
        except ValueError as exc:
            raise ContractError(
                f"quality_state 必须是 {[q.value for q in QualityState]} 之一，"
                f"实际是 {self.quality_state!r}") from exc
        if self.execution_state in _NEED_ERROR and not self.error:
            raise ContractError(
                f"{self.stage}: execution_state={self.execution_state.value} 必须带 error —— "
                f"只说'没成'却不说为什么，下游只能重跑或静默跳过")
        if self.execution_state is ExecutionState.NOT_APPLICABLE:
            if not self.policy or not self.evidence:
                raise ContractError(
                    f"{self.stage}: not_applicable 必须同时给出 policy 与 evidence —— "
                    f"方案 §9 不允许'缺工具/没运行'冒充不适用")
        if self.quality_state is QualityState.ACCEPTED and \
                not self.execution_state.can_feed_required_stage:
            raise ContractError(
                f"{self.stage}: execution_state={self.execution_state.value} 之上不能是 accepted —— "
                f"required 阶段必须 succeeded/cached 且 accepted")
        if self.attempt < 1:
            raise ContractError(f"{self.stage}: attempt 从 1 起算，实际 {self.attempt}")

    # ── 便捷构造（让调用点写起来短，但规则仍在 __post_init__ 里）
    @classmethod
    def ok(cls, stage: str, **kw) -> "StageResult":
        return cls(stage=stage, execution_state=ExecutionState.SUCCEEDED, **kw)

    @classmethod
    def cached(cls, stage: str, **kw) -> "StageResult":
        return cls(stage=stage, execution_state=ExecutionState.CACHED, **kw)

    @classmethod
    def blocked(cls, stage: str, reason: str, **kw) -> "StageResult":
        return cls(stage=stage, execution_state=ExecutionState.BLOCKED,
                   error={"reason": reason}, **kw)

    @classmethod
    def failed(cls, stage: str, reason: str, **kw) -> "StageResult":
        return cls(stage=stage, execution_state=ExecutionState.FAILED,
                   error={"reason": reason}, **kw)

    @classmethod
    def not_applicable(cls, stage: str, policy: str, evidence: str, **kw) -> "StageResult":
        return cls(stage=stage, execution_state=ExecutionState.NOT_APPLICABLE,
                   policy=policy, evidence=evidence, **kw)

    # ── 语义
    @property
    def feeds_downstream(self) -> bool:
        """它的输出能不能被下一阶段当输入吃。

        `not_applicable` 在这里是 **False**：它没有产物可传。
        """
        return self.execution_state.can_feed_required_stage

    @property
    def blocks_release(self) -> bool:
        """它会不会挡住正式发布。

        ⚠️ 这**不是** `not feeds_downstream`（第一版就是这么写的，被本包的测试当场抓住）：
        合法的"不适用"没有产物可传，但它**不该**挡住发布 —— 方案 §8 的原话是
        "每个请求终态只能是 fulfilled 或**经规则/审核认可的** not_applicable"。
        真正挡发布的是"没跑成"（blocked/failed/cancelled）与"跑了但不合格"（非 accepted）。
        """
        if self.execution_state is ExecutionState.NOT_APPLICABLE:
            return False                  # policy + evidence 已在 __post_init__ 里强制校验过
        if not self.execution_state.can_feed_required_stage:
            return True
        return self.quality_state.blocks_release

    @property
    def duration(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return self.finished_at - self.started_at

    def finish(self, state: ExecutionState | None = None) -> "StageResult":
        if state is not None:
            object.__setattr__(self, "execution_state", ExecutionState(str(state)))
        if self.finished_at is None:
            self.finished_at = time.time()
        return self

    def fingerprints(self) -> dict:
        """**进指纹的部分**：不含时间、attempt、路径、usage。"""
        return {"stage": self.stage, "execution_state": self.execution_state.value,
                "quality_state": self.quality_state.value,
                "inputs": [i.identity() for i in self.inputs],
                "outputs": [o.identity() for o in self.outputs],
                "findings": [f.get("code") for f in self.findings if isinstance(f, dict)]}

    def as_dict(self) -> dict:
        return {"stage": self.stage, "execution_state": self.execution_state.value,
                "quality_state": self.quality_state.value,
                "inputs": [i.as_dict() for i in self.inputs],
                "outputs": [o.as_dict() for o in self.outputs],
                "findings": list(self.findings), "usage_ref": self.usage_ref,
                "attempt": self.attempt, "started_at": self.started_at,
                "finished_at": self.finished_at, "duration": self.duration,
                "error": self.error, "resumable_at": self.resumable_at,
                "usage": dict(self.usage),
                "policy": self.policy, "evidence": self.evidence,
                "repair_requests": [request.as_dict() for request in self.repair_requests],
                "input_files": list(self.input_files), "repair_routes": list(self.repair_routes)}


def _coerce_repair_requests(result: "StageResult") -> tuple:
    """把 `repair_requests` 归一成 `tuple[RepairDirective, ...]`。

    三件事在这里被固定下来：

    * 旧结果**缺这个字段**（None／空）→ 解释为空元组（§4.2.2：不补造工单）；
    * 传进来的 dict（例如从 JSON 读回来的）→ 用契约的 `from_dict` 还原，
      而不是留一个"看着像 directive 的字典"让下游鸭子类型地猜；
    * 传了别的东西 → 当场报错，不静默丢弃（丢一条返修请求 = 悄悄少修一个问题）。
    """
    from ..blocks.audit.contracts import RepairDirective
    raw = result.repair_requests
    if raw is None:
        return ()
    if isinstance(raw, RepairDirective):
        raw = (raw,)
    elif isinstance(raw, dict):
        raw = (raw,)
    elif isinstance(raw, (list, tuple)):
        raw = tuple(raw)
    else:
        raise ContractError(
            f"{result.stage}: repair_requests 必须是 RepairDirective 的序列，"
            f"实际 {type(result.repair_requests).__name__}")
    out = []
    for item in raw:
        if isinstance(item, RepairDirective):
            out.append(item)
        elif isinstance(item, dict):
            out.append(RepairDirective.from_dict(item))
        else:
            raise ContractError(
                f"{result.stage}: repair_requests 里有不可识别的项 "
                f"{type(item).__name__} —— 返修请求不许是自由形状的字典")
    return tuple(out)


__all__ = ["StageResult"]
