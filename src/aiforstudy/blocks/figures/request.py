"""M5 图请求闭环契约 + 受限求值。

方案 §8 的原文（本模块逐条对应）：

> FigureRequest 增加 `request_id`、`unit_key`、服务对象/锚点、教学目的、教材依据、
> 约束与状态；先匹配当前教材图和已校验图库，再尝试把请求转换成受支持的声明式 FigureSpec。
> 产出 FigureResult，携带规格摘要、后端、资源 refs、几何/数值检查、引用绑定与失败原因。

> 给模型的可用能力来自预检结果；**未安装的后端不作为可选项**。必需图无法表达或依赖缺失时
> `blocked`，**不能静默换成与教学含义不同的图**。

> 每个请求**终态只能是 `fulfilled` 或经规则/审核认可的 `not_applicable`**；
> `unsupported`/`failed`/`pending` **均不是正式完成**。终审检查**挂图与服务对象一致**，
> 不能仅验证 SVG 文件存在。

> 已有图库与请求产生的资源**按规格哈希复用**，不按容易重名的文件名覆盖。

## 这个模块要防的三件事

1. **"提了请求"当成"已配图"**：请求是有状态的，只有 `fulfilled` 与经政策认可的
   `not_applicable` 算完成；`pending` 不是完成、`unsupported` 也不是。
2. **静默换图**：必需图表达不出来时要 `blocked`，绝不能换一张"长得差不多"的图交差 ——
   读者看到的是图，换图等于换了教学内容，而且**页面看起来完全正常**。
3. **按文件名覆盖**：两张不同的图重名会互相覆盖；所以复用与落盘都按**规格哈希**。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ...core.digest import digest_of
from ...errors import ContractError

#: 请求的生命周期状态
STATE_PENDING = "pending"
STATE_MATCHED = "matched"          # 命中现有教材图 / 已校验图库
STATE_SPECCED = "specced"          # 已转成受支持的声明式规格
STATE_DRAWN = "drawn"              # 已绘制出资源
STATE_VERIFIED = "verified"        # 几何/数值检查通过
STATE_FULFILLED = "fulfilled"      # 终态：完成
STATE_NOT_APPLICABLE = "not_applicable"   # 终态：经规则/审核认可的不适用
STATE_UNSUPPORTED = "unsupported"
STATE_BLOCKED = "blocked"
STATE_FAILED = "failed"
STATES = (STATE_PENDING, STATE_MATCHED, STATE_SPECCED, STATE_DRAWN, STATE_VERIFIED,
          STATE_FULFILLED, STATE_NOT_APPLICABLE, STATE_UNSUPPORTED, STATE_BLOCKED,
          STATE_FAILED)

#: **只有这两个是正式完成**（§8）
TERMINAL_OK = (STATE_FULFILLED, STATE_NOT_APPLICABLE)

#: 后端白名单（必须在预检里可用才会被选；未安装的后端不作为可选项）
#: `manim` 是 T07/T08 的**动画**后端：它产出的不是 SVG，而是 `AnimationResult`
#: （mp4／poster／关键帧／VTT／文字稿）。它同样只在预检可用时才是选项。
BACKENDS = ("vector", "plot", "schemdraw", "circuitikz", "three", "manim")


@dataclass(frozen=True)
class FigureRequest:
    """一条配图请求。字段即 §8 点名的那几个。"""
    request_id: str
    unit_key: str
    serves: str                      # 服务对象（概念名 / 定理名 —— 按**名字**，不是 id）
    anchor: str = ""                 # 正文锚点
    purpose: str = ""                # 教学目的
    basis: str = ""                  # 教材依据
    constraints: tuple[str, ...] = ()
    required: bool = True            # 必需图 vs 可选配图

    def __post_init__(self) -> None:
        for name in ("request_id", "unit_key", "serves"):
            if not str(getattr(self, name) or "").strip():
                raise ContractError(f"FigureRequest.{name} 必填（§8）")

    def as_dict(self) -> dict:
        return {"request_id": self.request_id, "unit_key": self.unit_key,
                "serves": self.serves, "anchor": self.anchor, "purpose": self.purpose,
                "basis": self.basis, "constraints": list(self.constraints),
                "required": self.required}

    def identity(self) -> dict:
        """进指纹的部分：不含时间/路径。"""
        return {"request_id": self.request_id, "unit_key": self.unit_key,
                "serves": self.serves, "anchor": self.anchor, "required": self.required}


@dataclass(frozen=True)
class FigureResource:
    """图的一份资源（SVG / PNG / scene / geometry…）。"""
    kind: str
    path: str
    spec_digest: str = ""            # **按规格哈希**复用，不按文件名
    sha256: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "path": self.path, "spec_digest": self.spec_digest,
                "sha256": self.sha256}


@dataclass(frozen=True)
class FigureResult:
    """一条请求的**终态**交代。"""
    request_id: str
    state: str
    backend: str = ""
    spec_summary: dict = field(default_factory=dict)
    resources: tuple[FigureResource, ...] = ()
    checks: dict = field(default_factory=dict)
    binding: dict = field(default_factory=dict)
    reason: str = ""
    policy: str = ""                 # not_applicable 必须有政策依据
    evidence: str = ""               # not_applicable 必须有证据

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ContractError(f"图请求状态必须是 {STATES} 之一，实际 {self.state!r}")
        if self.backend and self.backend not in BACKENDS:
            raise ContractError(f"未知图后端 {self.backend!r}；合法值 {BACKENDS}")
        if self.state == STATE_NOT_APPLICABLE and (not self.policy or not self.evidence):
            raise ContractError(
                "not_applicable 必须同时给出 policy 与 evidence —— 方案 §9："
                "不允许'缺工具/没运行'冒充不适用")
        if self.state in (STATE_BLOCKED, STATE_FAILED, STATE_UNSUPPORTED) and not self.reason:
            raise ContractError(f"{self.state} 必须写 reason（只说'没成'，下游只能重跑）")
        if self.state == STATE_FULFILLED:
            if not self.resources:
                raise ContractError("fulfilled 必须带资源 refs —— 没有资源的'完成'是空话")
            if self.checks.get("ok") is not True:
                raise ContractError(
                    "fulfilled 必须带**通过的**几何/数值检查。方案 §8："
                    "终审检查挂图与服务对象一致，**不能仅验证 SVG 文件存在**")
            if not self.binding:
                raise ContractError("fulfilled 必须记录引用绑定（挂给谁、绑在哪）")

    @property
    def is_complete(self) -> bool:
        """§8：**只有 fulfilled 与经认可的 not_applicable 是正式完成**。"""
        return self.state in TERMINAL_OK

    @property
    def blocks_release(self) -> bool:
        return not self.is_complete

    def as_dict(self) -> dict:
        return {"request_id": self.request_id, "state": self.state, "backend": self.backend,
                "spec_summary": dict(self.spec_summary),
                "resources": [r.as_dict() for r in self.resources],
                "checks": dict(self.checks), "binding": dict(self.binding),
                "reason": self.reason, "policy": self.policy, "evidence": self.evidence,
                "is_complete": self.is_complete, "blocks_release": self.blocks_release}


def spec_digest(spec: dict) -> str:
    """规格哈希：**复用与落盘都按它**，不按文件名（§8 末句）。"""
    return digest_of(spec)


@dataclass
class FigureLedger:
    """一次运行里全部图请求的账。**终审就查它。**"""
    results: dict[str, FigureResult] = field(default_factory=dict)

    def record(self, result: FigureResult) -> None:
        self.results[result.request_id] = result

    def incomplete(self) -> tuple[FigureResult, ...]:
        return tuple(r for r in self.results.values() if not r.is_complete)

    def by_state(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for result in self.results.values():
            out[result.state] = out.get(result.state, 0) + 1
        return dict(sorted(out.items()))

    def assert_releaseable(self, *, required_ids: set[str] | None = None) -> None:
        """方案 §8：*每个请求终态只能是 fulfilled 或经认可的 not_applicable*。"""
        bad = [r for r in self.results.values()
               if r.blocks_release and (required_ids is None or
                                        r.request_id in required_ids)]
        if bad:
            detail = "；".join(f"{r.request_id}={r.state}（{r.reason or r.policy}）"
                              for r in bad[:5])
            raise ContractError(
                f"有 {len(bad)} 个配图请求没有到终态：{detail}。"
                f"方案 §8：unsupported/failed/pending 均**不是**正式完成。")

    def verify_binding(self, unit_key: str, *, expected_serves: str,
                       catalog_names: set[str]) -> list[str]:
        """终审：**挂图与服务对象一致**，而且服务对象要在当前概念表里存在。

        只说"SVG 文件在"是不够的：图存在但挂错了概念，读者看到的是**另一段的图**，
        而页面完全正常。本项目已经栽过一次（P-020/P-032：概念 id 被复用，
        章节配错了图），所以这里按**名字**核对，并要求名字确实在册。
        """
        problems: list[str] = []
        for result in self.results.values():
            if result.request_id.split(":")[0] not in ("", unit_key) and \
                    result.binding.get("unit_key") not in (None, unit_key):
                continue
            serves = str(result.binding.get("serves") or "")
            if result.is_complete and not serves:
                problems.append(f"{result.request_id}: 完成但没有记录服务对象")
            elif serves and expected_serves and serves != expected_serves:
                problems.append(
                    f"{result.request_id}: 挂在 {serves!r} 上，本节需要的是 {expected_serves!r}")
            elif serves and serves not in catalog_names:
                problems.append(
                    f"{result.request_id}: 服务对象 {serves!r} 不在当前概念表里 "
                    f"—— 名字对不上就不许挂（宁可不配图，也不能配错图）")
        return problems

    def as_dict(self) -> dict:
        return {"_meta": {"file_type": "figure_ledger", "schema_version": "1.0",
                          "generator": "aiforstudy.blocks.figures.request",
                          "field_notes": {
                              "results[].state": "只有 fulfilled / not_applicable 是正式完成",
                              "results[].checks": "几何/数值检查；仅验证 SVG 存在不算",
                              "results[].binding": "引用绑定：挂给谁、绑在哪个锚点",
                          }},
                "by_state": self.by_state(), "results": [r.as_dict()
                                                         for r in self.results.values()]}


def choose_backend(purpose: str, available: tuple[str, ...]) -> str | None:
    """按需求选后端；**只从预检可用的里选**（§8：未安装的不作为可选项）。

    返回 `None` 表示当前能力表达不了这个需求 —— 调用方应当 `blocked`，
    **不许挑一个"差不多"的后端顶上**。
    """
    wants = {
        "vector": ("向量", "矢量", "几何", "区域", "映射", "曲线"),
        "plot": ("函数", "曲线", "图像", "幅频", "相频"),
        "schemdraw": ("电路", "元件", "节点", "回路"),
        "circuitikz": ("电路", "元件", "回路"),
        "three": ("立体", "三维", "曲面", "空间"),
        "manim": ("动画", "视频", "媒体", "动态演示", "分镜"),
    }
    for backend in BACKENDS:
        if backend not in available:
            continue
        if any(word in purpose for word in wants[backend]):
            return backend
    return None


__all__ = ["STATE_PENDING", "STATE_MATCHED", "STATE_SPECCED", "STATE_DRAWN", "STATE_VERIFIED",
           "STATE_FULFILLED", "STATE_NOT_APPLICABLE", "STATE_UNSUPPORTED", "STATE_BLOCKED",
           "STATE_FAILED", "STATES", "TERMINAL_OK", "BACKENDS", "FigureRequest",
           "FigureResource", "FigureResult", "spec_digest", "FigureLedger", "choose_backend"]
