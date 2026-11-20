"""画面检查：**实际帧**的检查，和几何／数学判断**分开记录**（§5.3、§5.4/V02）。

## 为什么必须单独一个模块、单独一份报告

计划 §1.3 的原话是：**像素是否合法、数学是否成立、学生是否看得懂，是三种不同验收**。
改造前的独立评审输入是**规格 JSON**，没有看到实际渲染图 —— 于是"审核通过"这句话
在图画这件事上什么也没证明。所以这里把画面检查独立出来，并且：

* `checks[].kind` 区分 `structure`（资源齐不齐）／`pixels`（尺寸、是否空白、文件是否对得上
  hash）／`motion`（动作段真的变了吗）／`text`（字幕与文字稿）／`visual`（**看过图之后的**判断）；
* **没有注入视觉评审时，整体状态只能是 `needs_review`，绝不能是 `pass`**。
  本地能做的只有结构与像素级检查；把它写成"画面通过"就是拿规格审核冒充看过图。
* 空报告**不许**当 pass（与 T06 的 `MathReport` 同一条规矩）。

## 「看过图」必须在数据上看得见（T08）

一个只吃 JSON 的"假视觉评审"（签名里没有 `images`、从不读图字节）曾经可以让整体状态
变成 `pass` —— 报告上一切正常，而没有任何人看过画面。所以 T08 加了三条：

1. `images_required=True` 时，评审**必须**显式接收 `images`；否则抛 `CapabilityError`，
   绝不"静默退成文本评审"。`accepts_images()` 是唯一的判据（只认接口，不认自称）。
2. 送审的图片被读成**真实**的 `(path, sha256, bytes, mime)` 清单（`image_inputs`），
   并原样记进 `FrameReport.images`。`bytes` 与 `sha256` 来自磁盘，不是声明。
3. `FrameReport.frame_hashes` 记录**这一档**实际被评审的关键帧哈希 ——
   `final` 必须重新评审自己那批帧，不能凭 draft 合格跳过最终画面。

## 「超图像数量按固定步骤抽帧」的准确含义

模型一次能收的图片有上限。固定做法是：**按固定步长抽帧**并逐条记录被丢弃的帧与原因，
而不是"悄悄少发几张"。更要紧的是**每个步骤至少要留一帧** —— 否则某一步的错误画面
会因为抽帧而根本没人看到，那种"漏检"比"看错"更危险。**装不下就明确报错**
（`ContractError`），不静默丢掉整步：上限不够是调用方要解决的问题，不是可以瞒过去的事。

## 失败反馈必须是**可派发**的（T08）

`{"status":"fail","reason":"画面不对"}` 这种判词在旧实现里会被原样收下 —— 下游拿到它
既不知道改哪一步、哪个对象、改完该长什么样，也不知道按什么标准算改好了。所以
`FrameFinding` 在**构造期**就要求六件事齐全：step / timestamp / 具体对象 / 修复动作 /
预期画面 / 通过标准；缺一条就抛 `ContractError`。并且：

* `word` 责任角色由 `kind` 决定：**数学／高亮／公式错 → `visual_planner`（改规格）**；
  `visual_renderer` 只拥有取景／编码，所以数学错**不能只调镜头**（`is_camera_only`）；
* `to_repair_ticket()` 把发现变成 T01 的 `RepairTicket`（走 `validate_ticket`），
  证据是**真实切片**（`EvidenceSpan.check_against` 能过），不是编出来的引文。

## 已知边界（不许写成"已经看过画面"）

* 字形级检查（中文字体有没有静默回退成方框／缺字）需要真实渲染的像素分布或 OCR；
  本模块只做**有界代理**：帧不得是单一色（整帧空白）、尺寸必须与档位一致、
  标签文字必须在字幕／文字稿里出现。真正的字形判断留给注入了视觉评审的那条路。
* 遮挡与裁切的**语义**判断同样依赖视觉评审；像素级只能发现"整帧均匀""尺寸不对"这类。
  本模块做的是把这些发现**结构化**、把含糊判词挡在门外，而不是自己去"看图"。
"""
from __future__ import annotations

import hashlib
import inspect
import math
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ...core.digest import digest_of
from ...errors import CapabilityError, ContractError
from ...contracts.review import MEDIA_VERDICT_STATUSES, MEDIA_REPORT_STATUSES
from ..audit.contracts import (SEVERITIES as FINDING_SEVERITIES, OWNERS as FINDING_OWNERS,
                               METHODS as CRITERION_METHODS, EVIDENCE_TYPES as CRITERION_EVIDENCE_TYPES)

#: 检查类别：画面判断与数学／几何判断**分开记**
CHECK_KINDS = ("structure", "pixels", "motion", "text", "visual")
#: 单条检查的状态（`blocked` 只由调用方在缺环境时使用）
CHECK_STATUSES = MEDIA_VERDICT_STATUSES
REPORT_STATUSES = MEDIA_REPORT_STATUSES

#: 渲染档位 → (宽, 高)。与 §5.4 的 final/draft 参数一致。
from ..media.presentation_policy import RENDER_PROFILES
PROFILE_SIZES = {k:(v["pixel_width"],v["pixel_height"]) for k,v in RENDER_PROFILES.items()}

#: 一帧至少要有多少种颜色才不算"整帧空白"
MIN_DISTINCT_COLOURS = 3
#: 每步字幕上限（§5.3）
MAX_CAPTION_CHARS = 120

# ── T08：可派发的画面发现 ────────────────────────────────────────────────────
#: 画面问题的种类（与 `blocks/audit/contracts.KINDS` 的两个视觉类同名）
FINDING_KINDS = ("visual_math", "visual_layout")
#: 取景／编码类动作的前缀。只有这些动作才归 `visual_renderer`。
CAMERA_OP_PREFIX = "camera."
#: 公式／高亮／对象语义的修复动作前缀（归 `visual_planner`）
SPEC_OP_PREFIX = "spec."


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "as_dict", None)
    if callable(to_dict):
        return to_dict()
    raise ContractError(f"期望 dict 或带 as_dict() 的对象，实际 {type(value).__name__}")


def accepts_images(reviewer: Any) -> bool:
    """接口能力判据：这个评审**是否显式接收 `images`**。

    只看接口，不看它自称什么。一个 `def reviewer(*, frames, steps, spec)` 的替身
    —— 无论名字里写多少遍"视觉"—— 都拿不到图片字节，所以它**不是**视觉评审。
    只有显式 `**kwargs` **并且**声明 `supports_images = True` 的对象才算，
    免得"顺手收个 kwargs"被当成图片能力。
    """
    if reviewer is None:
        return False
    try:
        parameters = inspect.signature(reviewer).parameters
    except (TypeError, ValueError):                       # 内建／C 扩展等
        return False
    if "images" in parameters:
        return True
    has_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
    return bool(has_var_kw and getattr(reviewer, "supports_images", False))


def image_inputs(paths: Iterable[str | Path]) -> tuple[dict, ...]:
    """把**真实图片文件**读成 `(path, sha256, bytes, mime)` 清单。

    缺文件是**明确错误**：静默丢图会让评审看到缺图的产物却以为完整。哈希对着**字节**
    算，所以"送审的就是盘上那一版"在数据上可复核，而不是靠调用方口头保证。
    """
    out: list[dict] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            raise ContractError(
                f"送审图片不存在：{path} —— 缺图是明确错误，"
                f"静默丢图会让画面评审看到缺图的产物却以为完整")
        data = path.read_bytes()
        out.append({"path": str(path),
                    "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                    "mime": mimetypes.guess_type(path.name)[0] or "image/png"})
    return tuple(out)


@dataclass(frozen=True)
class FrameCheck:
    """一条画面检查。`evidence` 是**可复核的哈希／路径**，不是一句结论。"""
    check_id: str
    kind: str
    status: str
    reason: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(bool(self.check_id.strip()), "FrameCheck.check_id 必填")
        _require(self.kind in CHECK_KINDS,
                 f"FrameCheck.kind 必须是 {list(CHECK_KINDS)}，实际 {self.kind!r}")
        _require(self.status in CHECK_STATUSES,
                 f"FrameCheck.status 必须是 {list(CHECK_STATUSES)}，实际 {self.status!r}")
        _require(bool(self.reason.strip()),
                 f"FrameCheck {self.check_id}: reason 必填 —— 没有理由的判词无法复核")
        object.__setattr__(self, "evidence", tuple(self.evidence))

    def as_dict(self) -> dict:
        return {"check_id": self.check_id, "kind": self.kind, "status": self.status,
                "reason": self.reason, "evidence": list(self.evidence)}


# --------------------------------------------------------------------------- #
# T08：可派发的画面发现
# --------------------------------------------------------------------------- #
def action_op(action: str) -> str:
    """修复动作的**操作前缀**：`"camera.zoom: 拉近看清公式"` → `"camera.zoom"`。

    没有前缀就返回空串 —— 空动作会在 `FrameFinding` 构造期被拒（没有动作的工单
    派不下去）。
    """
    text = str(action or "").strip()
    if not text:
        return ""
    head = re.split(r"[:：]", text, maxsplit=1)[0].strip()
    first = head.split()[0] if head.split() else ""
    return first


def is_camera_only(action: str) -> bool:
    """这个修复动作**只是取景／编码**吗（不碰规格里的数学与对象）？

    判据刻意做成机器可判的：动作必须写成 `"<op>: <说明>"`，op 用 `camera.` /
    `spec.` 前缀。于是"数学错只调镜头"不再是评审的自觉，而是构造期就拒绝的形态。
    多个操作可以用 `+` / `;` / `,` 连接 —— **全部**是 `camera.` 才算只调镜头。
    """
    op = action_op(action)
    if not op:
        return False
    ops = [item.strip() for item in re.split(r"[+;,，；]", op) if item.strip()]
    return bool(ops) and all(item.startswith(CAMERA_OP_PREFIX) for item in ops)


def owner_for_kind(kind: str, action: str = "") -> str:
    """问题种类 → 责任角色。**数学错不许派给取景方**。

    * `visual_math`（公式／高亮对象／数学语义）→ `visual_planner`，永远；
    * `visual_layout`（遮挡／裁切／构图）→ 纯取景动作可派 `visual_renderer`，
      否则仍归 `visual_planner`（规格里的布局问题不是镜头能修的）。
    """
    _require(kind in FINDING_KINDS,
             f"画面问题种类必须是 {list(FINDING_KINDS)}，实际 {kind!r}")
    if kind == "visual_math":
        return "visual_planner"
    return "visual_renderer" if is_camera_only(action) else "visual_planner"


@dataclass(frozen=True)
class FrameFinding:
    """一条**可派发**的画面失败发现（T08）。

    六件必要信息一件都不能少 —— 缺任何一件，下游就只能"再跑一次看看"，而那正是
    改造前返修循环空转的原因：`{"status":"fail","reason":"画面不对"}` 派下去，
    生成器既不知道改哪里，也不知道改完算不算好。

    | 字段 | 是什么 | 为什么必须 |
    |---|---|---|
    | `step_id` | 哪一步 | 没有它就不知道改哪段分镜 |
    | `timestamp` | 该帧在视频里的秒数 | 编辑与复核要能定位到同一帧 |
    | `object_id` | 具体哪个对象／公式 | "画面不对"定位不到对象 |
    | `action` | 修复动作（`<op>: 说明`） | 没有动作的工单派不下去 |
    | `expected_screen` | 改完应当看到的画面 | 否则验收只能靠感觉 |
    | `criteria` | 通过标准（可核验） | 没有标准的返修永远关不了单 |
    """
    finding_id: str
    kind: str
    severity: str
    owner: str
    step_id: str
    timestamp: float
    object_id: str
    problem: str
    action: str
    expected_screen: str
    criteria: tuple[dict, ...]
    frame_hash: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("finding_id", "step_id", "object_id", "problem", "action",
                     "expected_screen", "frame_hash"):
            _require(bool(str(getattr(self, name) or "").strip()),
                     f"FrameFinding.{name} 必填 —— 失败反馈必须含 step／timestamp／"
                     f"具体对象／修复动作／预期画面／通过标准，含糊判词不许当反馈")
        _require(self.kind in FINDING_KINDS,
                 f"FrameFinding.kind 必须是 {list(FINDING_KINDS)}，实际 {self.kind!r}")
        _require(self.severity in FINDING_SEVERITIES,
                 f"FrameFinding.severity 必须是 {list(FINDING_SEVERITIES)}，"
                 f"实际 {self.severity!r}")
        _require(self.owner in FINDING_OWNERS,
                 f"FrameFinding.owner 必须是 {list(FINDING_OWNERS)}，实际 {self.owner!r}")
        _require(isinstance(self.timestamp, (int, float))
                 and math.isfinite(float(self.timestamp)) and float(self.timestamp) >= 0,
                 f"FrameFinding.timestamp 必须是非负有限秒数，实际 {self.timestamp!r}"
                 f"（没有时间戳就无法定位到同一帧）")
        object.__setattr__(self, "timestamp", float(self.timestamp))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        criteria = tuple(dict(c) for c in self.criteria)
        _require(criteria,
                 "FrameFinding.criteria 不能为空 —— 没有通过标准的返修永远关不了单")
        seen: set[str] = set()
        for criterion in criteria:
            cid = str(criterion.get("criterion_id") or "").strip()
            _require(bool(cid), "criterion_id 必填")
            _require(cid not in seen, f"criterion_id {cid!r} 重复 —— 重复标准数不清验了几条")
            seen.add(cid)
            method = str(criterion.get("method") or "")
            _require(method in CRITERION_METHODS,
                     f"criterion {cid}: method 必须是 {list(CRITERION_METHODS)}，"
                     f"实际 {method!r}")
            evidence_type = str(criterion.get("evidence_type") or "frame")
            _require(evidence_type in CRITERION_EVIDENCE_TYPES,
                     f"criterion {cid}: evidence_type 必须是 "
                     f"{list(CRITERION_EVIDENCE_TYPES)}，实际 {evidence_type!r}")
            criterion["evidence_type"] = evidence_type
            _require(bool(str(criterion.get("expected") or "").strip()),
                     f"criterion {cid}: expected 必填 —— 没有 expected 的标准只能靠"
                     f"'看起来还行'来判，那正是要治的病")
        object.__setattr__(self, "criteria", criteria)
        # ── owner 必须与 kind 相符：数学错不能派给取景方
        want_owner = owner_for_kind(self.kind, self.action)
        if self.kind == "visual_math":
            _require(self.owner == "visual_planner",
                     f"{self.finding_id}: kind=visual_math 的 owner 必须是 "
                     f"visual_planner（改规格／公式／高亮对象），实际 {self.owner!r}")
            _require(not is_camera_only(self.action),
                     f"{self.finding_id}: 数学／公式错**不能只调镜头**（action="
                     f"{self.action!r}）。取景变了，错误的公式还是错的 —— "
                     f"必须用 spec.* 动作改规格，或明确说明为何数学结论要变")
        elif self.owner == "visual_renderer":
            _require(is_camera_only(self.action),
                     f"{self.finding_id}: owner=visual_renderer 只拥有取景／编码，"
                     f"所以 action 必须是 camera.* 前缀；实际 {self.action!r}。"
                     f"内容问题应当派 visual_planner")
        _require(self.owner == want_owner or self.owner == "visual_planner",
                 f"{self.finding_id}: owner={self.owner!r} 与 kind/action 不符"
                 f"（按 kind={self.kind!r}、action={self.action!r} 应为 {want_owner!r}）")

    @property
    def blocking(self) -> bool:
        return self.severity in ("P0", "P1")

    def as_dict(self) -> dict:
        return {"finding_id": self.finding_id, "kind": self.kind, "severity": self.severity,
                "owner": self.owner, "step_id": self.step_id,
                "timestamp": self.timestamp, "object_id": self.object_id,
                "problem": self.problem, "action": self.action,
                "expected_screen": self.expected_screen,
                "criteria": [dict(c) for c in self.criteria],
                "frame_hash": self.frame_hash, "evidence": list(self.evidence),
                "blocking": self.blocking, "camera_only": is_camera_only(self.action)}

    def to_repair_ticket(self, *, unit_id: str, artifact_id: str, artifact_hash: str,
                         rule: str, transcript: str, severity: str | None = None,
                         allowed_paths: tuple[str, ...] = (),
                         preserve: tuple[str, ...] = (),
                         hard_acceptance: bool = True) -> Any:
        """变成 T01 的 `RepairTicket`（**走 `validate_ticket`**，不自造契约）。

        `transcript` 是这一档真实字幕／文字稿文本；证据是它的**真实切片**
        （`start=0, end=len(transcript)`），所以 `EvidenceSpan.check_against` 能过。
        空文本直接拒绝 —— 没有真实证据就不开单，不编造引文。
        """
        _require(bool(str(transcript or "")),
                 f"{self.finding_id}: 没有真实字幕／文字稿就不能开单 —— 不许编造证据")
        from ..audit.contracts import SCHEMA_VERSION, Criterion, EvidenceSpan, validate_ticket

        span = EvidenceSpan(artifact_id=artifact_id, artifact_hash=artifact_hash,
                            json_pointer=f"/steps/{self.step_id}", start=0,
                            end=len(transcript), quote=transcript,
                            frame_hash=self.frame_hash, step_id=self.step_id,
                            timestamp=self.timestamp)
        span.check_against(transcript)
        data = {
            "schema_version": SCHEMA_VERSION, "unit_id": unit_id,
            "kind": self.kind, "severity": severity or self.severity, "owner": self.owner,
            "status": "ready",
            "target": {"artifact_id": artifact_id, "artifact_hash": artifact_hash,
                       "rule": rule, "json_pointer": f"/steps/{self.step_id}/objects/"
                                                     f"{self.object_id}",
                       "step_id": self.step_id, "frame_hash": self.frame_hash},
            "problem": self.problem,
            "learner_obstacle": self.expected_screen,
            "source_evidence": [span.as_dict()],
            "repair_instructions": {
                "locate": f"step {self.step_id} @ {self.timestamp:.3f}s 的 {self.object_id}",
                "action": self.action,
                "rationale": self.problem,
                "expected_change": self.expected_screen},
            "allowed_paths": list(allowed_paths or (f"steps[{self.step_id}]",)),
            "preserve": list(preserve or ("其他步骤的数学结论与画布尺寸",)),
            "criteria": [Criterion(**c).as_dict() for c in self.criteria],
            "hard_acceptance": bool(hard_acceptance),
        }
        ticket = validate_ticket(data)
        return ticket


def finding_from_verdict(verdict: Mapping, *, step: Mapping | None = None,
                         step_id: str | None = None, frame: Mapping | None = None,
                         kind: str | None = None, owner: str | None = None,
                         frame_hash: str | None = None,
                         timestamp: float | None = None) -> FrameFinding:
    """把评审的一条失败判词变成 `FrameFinding`。**缺件就抛，不补默认值。**

    `step` / `frame` 是这一步与这一帧的上下文（来自规格与 `AnimationResult` 的
    关键帧资源）：判词自己没写 step／timestamp／frame_hash 时从上下文取 ——
    取不到就报错。**绝不**用 0.0 或空串糊过去：那会让工单指向"第 0 秒的某个东西"。
    """
    data = dict(verdict or {})
    step = dict(step or {})
    frame = dict(frame or {})
    resolved_step = str(data.get("step_id") or step_id or step.get("step_id") or "")
    resolved_hash = str(data.get("frame_hash") or frame_hash
                        or frame.get("sha256") or "")
    raw_ts = data.get("timestamp", timestamp)
    if raw_ts is None:
        raw_ts = frame.get("timestamp")
    _require(raw_ts is not None and str(raw_ts) != "",
             f"失败判词缺 timestamp，而且这一步没有关键帧时间戳可用（step={resolved_step!r}）"
             f"—— 没有时间戳就无法定位到同一帧")
    criteria = data.get("criteria")
    _require(isinstance(criteria, (list, tuple)) and criteria,
             f"失败判词缺 criteria（通过标准）：step={resolved_step!r} 的反馈必须写明"
             f"按什么标准算改好了，否则返修永远关不了单")
    finding_id = str(data.get("finding_id") or "").strip() or \
        "FR-" + digest_of({"step": resolved_step, "object": data.get("object_id"),
                           "hash": resolved_hash}).split(":")[1][:16]
    action = str(data.get("action") or "")
    # 判词没写 kind 时按动作判：纯取景动作 = 布局问题（可归渲染方），
    # 其余按内容问题（归规格方）—— 宁可更严，也不把数学错当取景错。
    default_kind = "visual_layout" if is_camera_only(action) else "visual_math"
    resolved_kind = str(kind or data.get("kind") or default_kind)
    return FrameFinding(
        finding_id=finding_id,
        kind=resolved_kind,
        severity=str(data.get("severity") or "P1"),
        owner=str(owner or data.get("owner") or owner_for_kind(
            resolved_kind, action)),
        step_id=resolved_step,
        timestamp=float(raw_ts),
        object_id=str(data.get("object_id") or ""),
        problem=str(data.get("problem") or data.get("reason") or ""),
        action=action,
        expected_screen=str(data.get("expected_screen") or ""),
        criteria=tuple(dict(c) for c in criteria),
        frame_hash=resolved_hash,
        evidence=tuple(str(x) for x in (data.get("evidence") or ())),
    )


@dataclass(frozen=True)
class FrameReport:
    """画面检查报告。**空报告不是 pass**（§5.3）。"""
    status: str
    checks: tuple[FrameCheck, ...]
    evidence_hashes: tuple[str, ...] = ()
    dropped_frames: tuple[dict, ...] = ()
    reviewer_inputs: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    findings: tuple[FrameFinding, ...] = ()
    profile: str | None = None
    frame_hashes: tuple[str, ...] = ()
    images: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        _require(self.status in REPORT_STATUSES,
                 f"FrameReport.status 必须是 {list(REPORT_STATUSES)}，实际 {self.status!r}")
        object.__setattr__(self, "checks", tuple(self.checks))
        _require(self.checks, "FrameReport 至少要有检查项 —— 空报告不能当通过")
        object.__setattr__(self, "evidence_hashes", tuple(self.evidence_hashes))
        object.__setattr__(self, "dropped_frames",
                           tuple(dict(d) for d in self.dropped_frames))
        object.__setattr__(self, "reviewer_inputs", tuple(self.reviewer_inputs))
        object.__setattr__(self, "notes", tuple(self.notes))
        object.__setattr__(self, "findings", tuple(self.findings))
        object.__setattr__(self, "frame_hashes", tuple(self.frame_hashes))
        object.__setattr__(self, "images", tuple(dict(i) for i in self.images))
        if self.profile is not None:
            _require(self.profile in PROFILE_SIZES,
                     f"FrameReport.profile 只能是 {sorted(PROFILE_SIZES)}，"
                     f"实际 {self.profile!r}")
        if self.status == "pass":
            bad = [c.check_id for c in self.checks if c.status != "pass"]
            _require(not bad, f"FrameReport: 有未通过的检查 {bad}，不能是 pass")
            visual = [c for c in self.checks if c.kind == "visual"]
            _require(visual and all(c.status == "pass" for c in visual),
                     "FrameReport: 没有一条通过的**视觉**检查就不能是 pass —— "
                     "只做结构与像素检查不等于看过画面")
            _require(not self.findings,
                     f"FrameReport: 带着失败发现 "
                     f"{[f.finding_id for f in self.findings]} 不能是 pass")

    @property
    def failed(self) -> tuple[FrameCheck, ...]:
        return tuple(c for c in self.checks if c.status == "fail")

    @property
    def needs_review(self) -> tuple[FrameCheck, ...]:
        return tuple(c for c in self.checks if c.status == "needs_review")

    def as_dict(self) -> dict:
        return {"status": self.status, "checks": [c.as_dict() for c in self.checks],
                "evidence_hashes": list(self.evidence_hashes),
                "dropped_frames": [dict(d) for d in self.dropped_frames],
                "reviewer_inputs": list(self.reviewer_inputs),
                "notes": list(self.notes),
                "findings": [f.as_dict() for f in self.findings],
                "profile": self.profile,
                "frame_hashes": list(self.frame_hashes),
                "images": [dict(i) for i in self.images],
                "counts": {"pass": sum(c.status == "pass" for c in self.checks),
                           "fail": len(self.failed),
                           "needs_review": len(self.needs_review)}}


# --------------------------------------------------------------------------- #
# 资源与帧
# --------------------------------------------------------------------------- #
def _resources(result: dict) -> list[dict]:
    resources = result.get("resources")
    _require(isinstance(resources, list) and resources,
             "AnimationResult.resources 必须是非空列表 —— 没有资源的'完成'没有意义")
    return [dict(r) for r in resources]


def _by_kind(result: dict, kind: str) -> list[dict]:
    return [r for r in _resources(result) if str(r.get("kind") or "") == kind]


def _step_frames(spec: dict, result: dict) -> dict[str, list[dict]]:
    """`step_id → 落在该步骤时段内的关键帧`（按时间戳升序）。"""
    steps = spec.get("steps") or []
    frames = sorted(_by_kind(result, "keyframe"),
                    key=lambda r: float(r.get("timestamp") or 0.0))
    out: dict[str, list[dict]] = {}
    for step in steps:
        step_id = str(step.get("step_id") or "")
        start, end = float(step.get("start") or 0.0), float(step.get("end") or 0.0)
        out[step_id] = [f for f in frames
                        if start <= float(f.get("timestamp") or 0.0) <= end]
    return out


def select_frames(spec: dict, result: dict, *, max_images: int = 8) -> tuple[list[dict], list[dict]]:
    """按**固定步长**抽帧；返回 `(选中的帧, 被丢弃的帧+原因)`。

    两条硬规则：

    1. **每个步骤至少留一帧**：某一步的错误画面不能因为抽帧而根本没人看到。
       如果固定步长装不下"每步一帧"，就**明确报错**（提高上限或分多次评审），
       绝不静默丢掉整步 —— 那种漏检比看错更危险。
    2. 丢弃**逐条记录**（含原因与时间戳），不做静默截断。
    """
    _require(max_images >= 1, "max_images 至少为 1")
    if spec.get("schema_version")=="2.0":
        return _select_v2_frames(spec,result,max_images=max_images)
    per_step = _step_frames(spec, result)
    ordered_steps = [str(s.get("step_id") or "") for s in (spec.get("steps") or [])]
    # 每步先取**最早**一帧（确定性：不依赖字典顺序或文件系统顺序）
    chosen: list[dict] = []
    extras: list[dict] = []
    for step_id in ordered_steps:
        frames = per_step.get(step_id) or []
        if frames:
            chosen.append(frames[0])
            extras.extend(frames[1:])
    if not chosen:
        return [], [dict(f, reason="该帧不属于任何步骤时段") for f in _by_kind(result, "keyframe")]
    if len(chosen) > max_images:
        raise ContractError(
            f"本次图像上限 {max_images} 装不下 {len(chosen)} 个步骤的关键帧："
            f"按固定步长抽帧就会丢掉某些步骤的唯一帧，那一步的错误画面就根本没人看到"
            f"（漏检比看错更危险）。请把上限提到 ≥ {len(chosen)}，或分多次评审。")

    dropped: list[dict] = []
    remaining = max_images - len(chosen)
    if remaining <= 0:
        dropped.extend(dict(f, reason="超过本次图像上限，未送评审") for f in extras)
        return chosen, dropped
    if extras:
        stride = max(1, math.ceil(len(extras) / remaining))
        picked: list[dict] = []
        for index, frame in enumerate(extras):
            if index % stride == 0 and len(picked) < remaining:
                picked.append(frame)
            elif len(picked) >= remaining:
                dropped.append(dict(frame, reason="超过本次图像上限，未送评审"))
            else:
                dropped.append(dict(frame, reason=f"固定步长抽帧（每 {stride} 张取 1）"))
        chosen.extend(picked)
    return chosen, dropped


def _frame_path(result: dict, frame: dict) -> Path | None:
    """相对资源 → 绝对路径。`result` 里可以给 `base_dir` 作为解码根。"""
    rel = str(frame.get("relative_path") or "")
    if not rel:
        return None
    base = result.get("base_dir")
    if not base:
        return None
    return Path(base) / rel


def _inspect_pixels(path: Path, profile: str) -> tuple[str, str, tuple[str, ...]]:
    """像素级检查：尺寸与"是否整帧空白"。返回 `(status, reason, evidence)`。"""
    try:
        from PIL import Image
    except ImportError:                                   # pragma: no cover
        return ("needs_review", "本机没有 Pillow，无法做像素检查", ())
    if not path.is_file():
        return ("fail", f"关键帧文件不存在：{path}", ())
    with Image.open(path) as image:
        size = image.size
        # ⚠️ `getcolors(maxcolors=N)` 在颜色数**超过** N 时返回 `None`。
        #    第一版把 None 当成 0 种颜色，于是"颜色很丰富的真实渲染帧"会被判成"整帧空白" ——
        #    这种假失败比漏检更坏：它会让人开始不信任画面检查。
        #    所以 None = "颜色多到数不完"，直接认为不是空白。
        colours = image.convert("RGB").getcolors(maxcolors=4096)
    rich = colours is None
    distinct = 4096 if rich else len(colours)
    expected = PROFILE_SIZES.get(profile)
    evidence = (f"{path.name}:{size[0]}x{size[1]}:colours={distinct}",)
    if expected and tuple(size) != expected:
        return ("fail",
                f"帧尺寸 {size[0]}x{size[1]} 与 {profile} 档要求的 "
                f"{expected[0]}x{expected[1]} 不符（裁切或缩放错误）", evidence)
    if distinct < MIN_DISTINCT_COLOURS:
        return ("fail",
                f"帧只有 {distinct} 种颜色，接近整帧空白 —— 渲染没画出东西", evidence)
    shown = ">4096" if rich else str(distinct)
    return ("pass", f"尺寸 {size[0]}x{size[1]}，{shown} 种颜色", evidence)


def _actions_of(step: dict) -> list[dict]:
    """规格里的动作。⚠️ T06 的规格用 `action` 键（`{"action":"pause"}`），
    老测试夹具用 `kind` —— **两个都认**。第一版只看 `kind`，于是纯 pause 步骤
    被当成"有动作"，静止画面被判成"画面上什么都没发生"（假失败）。"""
    actions = step.get("actions") or ()
    out = []
    for action in actions:
        row = action if isinstance(action, dict) else _as_dict(action)
        row = dict(row)
        row.setdefault("kind", row.get("action") or "")
        out.append(row)
    return out


def _step_lookup(spec: dict) -> dict[str, dict]:
    return {str(s.get("step_id") or ""): dict(s) for s in (spec.get("steps") or [])}


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def verify_frames(spec: Any, result: Any, *, reviewer: Callable[..., Any] | None = None,
                  max_images: int = 8, profile: str | None = None,
                  images_required: bool = False) -> FrameReport:
    """检查**实际帧**。`reviewer` 缺省时整体只能是 `needs_review`。

    `reviewer(frames=[...], steps=[...], spec=...)` 必须返回可迭代的逐条判词：
    `{"step_id":..., "status": "pass"/"fail"/"needs_review", "reason":...}`。
    它收到的是**真实图片路径**，所以"看过图"这件事在数据上看得见。

    `images_required=True`（T08 生产路径）时：评审**必须**显式接收 `images`，
    并且会拿到 `image_inputs()` 读出的真实 bytes/hash；否则抛 `CapabilityError`。
    失败的判词必须可派发（`FrameFinding` 六件信息齐全），否则抛 `ContractError`。
    """
    spec_dict = _as_dict(spec)
    if spec_dict.get("schema_version")=="2.0":
        from .spec import parse_visual_spec
        spec_dict=parse_visual_spec(spec_dict).as_dict()
    result_dict = _as_dict(result)
    chosen_profile = str(profile or spec_dict.get("render_profile") or "final")
    steps_by_id = _step_lookup(spec_dict)
    checks: list[FrameCheck] = []
    evidence: list[str] = []
    findings: list[FrameFinding] = []

    # ── structure：资源齐不齐
    for kind, label in (("mp4", "视频"), ("poster", "封面图"), ("captions", "字幕"),
                        ("transcript", "文字稿")):
        present = bool(_by_kind(result_dict, kind))
        checks.append(FrameCheck(
            check_id=f"structure.{kind}", kind="structure",
            status="pass" if present else "fail",
            reason=f"{label}资源{'存在' if present else '缺失'}"))
    frames = _by_kind(result_dict, "keyframe")
    checks.append(FrameCheck(
        check_id="structure.keyframe", kind="structure",
        status="pass" if frames else "fail",
        reason=f"关键帧 {len(frames)} 张" if frames else "没有任何关键帧"))

    # ── 每步：帧落在时段内 / 字幕 / 预期的变化必须写明
    step_frames = _step_frames(spec_dict, result_dict)
    captions = " ".join(str(c) for c in (result_dict.get("captions") or ()))
    for step in spec_dict.get("steps") or []:
        step_id = str(step.get("step_id") or "?")
        caption = str(step.get("caption") or "")
        inside = step_frames.get(step_id) or []
        checks.append(FrameCheck(
            check_id=f"step.{step_id}.frame_in_window", kind="structure",
            status="pass" if inside else "fail",
            reason=(f"该步时段内有 {len(inside)} 张关键帧" if inside
                    else "该步时段内没有任何关键帧 —— 这一步的真实画面没人看到"),
            evidence=tuple(str(f.get("sha256") or "") for f in inside[:3])))
        text_ok = bool(caption.strip()) and len(caption) <= MAX_CAPTION_CHARS
        checks.append(FrameCheck(
            check_id=f"step.{step_id}.caption", kind="text",
            status="pass" if text_ok else "fail",
            reason=(f"字幕 {len(caption)} 字" if text_ok else
                    (f"字幕超过 {MAX_CAPTION_CHARS} 字（{len(caption)}）" if caption.strip()
                     else "缺字幕"))))
        if captions:
            appears = caption.strip() and caption.strip()[:12] in captions
            checks.append(FrameCheck(
                check_id=f"step.{step_id}.caption_in_resource", kind="text",
                status="pass" if appears else "needs_review",
                reason=("字幕出现在 captions 资源里" if appears else
                        "字幕文本没有出现在 captions 资源里（可能只存在规格里，播放器读不到）")))
        change = str(step.get("expected_change") or "").strip()
        checks.append(FrameCheck(
            check_id=f"step.{step_id}.expected_change", kind="text",
            status="pass" if change else "fail",
            reason=("已声明预期变化" if change else "没写这一步应当出现什么变化，无法判断画面变没变")))

        # ── motion：动作段必须真的变；纯 pause 允许静止
        actions = _actions_of(step)
        moving = [a for a in actions if str(a.get("kind") or "") != "pause"]
        hashes = [str(f.get("sha256") or "") for f in inside]
        if len(hashes) >= 2:
            changed = len(set(hashes)) > 1
            if moving:
                checks.append(FrameCheck(
                    check_id=f"step.{step_id}.motion", kind="motion",
                    status="pass" if changed else "fail",
                    reason=("动作段的关键帧确实不同" if changed else
                            "这一步有动作（" + "、".join(str(a.get('kind')) for a in moving) +
                            "）但关键帧完全相同 —— 画面上什么都没发生"),
                    evidence=tuple(hashes[:4])))
            else:
                checks.append(FrameCheck(
                    check_id=f"step.{step_id}.motion", kind="motion", status="pass",
                    reason="纯 pause 步骤允许静止（这是唯一允许帧相同的情形）"))
        elif inside:
            checks.append(FrameCheck(
                check_id=f"step.{step_id}.motion", kind="motion", status="needs_review",
                reason="该步只有一张关键帧，无法比较变化 —— 需要视觉评审看那张图"))

    # ── pixels：逐帧尺寸与"整帧空白"
    for frame in chosen_or_all(spec_dict, result_dict, frames, max_images):
        path = _frame_path(result_dict, frame)
        digest = str(frame.get("sha256") or "")
        if digest:
            evidence.append(digest)
        if path is None:
            checks.append(FrameCheck(
                check_id=f"pixels.{frame.get('sha256') or '?'}", kind="pixels",
                status="needs_review",
                reason="拿不到绝对路径（缺 base_dir）—— 像素检查没跑，不能算通过"))
            continue
        status, reason, ev = _inspect_pixels(path, chosen_profile)
        if spec_dict.get("schema_version")=="2.0" and frame.get("phase")=="before" and status=="fail" and "空白" in reason:
            status,reason="pass","动作前隐藏对象允许空白；须结合动作后实帧审核"
        checks.append(FrameCheck(check_id=f"pixels.{path.stem}", kind="pixels",
                                 status=status, reason=reason, evidence=ev))
        evidence.extend(ev)

    # ── visual：看过图之后才有的话事权
    selected, dropped = select_frames(spec_dict, result_dict, max_images=max_images)
    reviewer_inputs = [str(_frame_path(result_dict, f) or f.get("relative_path") or "")
                       for f in selected]
    frame_hashes = tuple(str(f.get("sha256") or "") for f in selected)
    images: tuple[dict, ...] = ()
    if reviewer is None:
        checks.append(FrameCheck(
            check_id="visual.review", kind="visual", status="needs_review",
            reason=("没有注入视觉评审：本地只做了结构与像素检查，"
                    "**不能**据此声称看过画面（§1.3：像素合法／数学成立／学生看得懂是三种验收）"),
            evidence=tuple(reviewer_inputs[:4])))
    else:
        real_images = [path for path in reviewer_inputs if Path(path).is_file()]
        images = image_inputs(real_images)
        if images_required and not accepts_images(reviewer):
            raise CapabilityError(
                "画面评审必须收到**真实图片输入**，但当前评审的接口不接收 `images`"
                "（只吃 JSON／消息文本）。§15 A26：缺能力要明确 blocked，"
                "不能静默退成文本评审 —— 拿规格审核冒充看过图正是要治的病。")
        kwargs: dict[str, Any] = {"frames": reviewer_inputs,
                                  "steps": spec_dict.get("steps") or [], "spec": spec_dict}
        if accepts_images(reviewer):
            kwargs["images"] = images
        verdicts = list(reviewer(**kwargs) or ())
        if not verdicts:
            checks.append(FrameCheck(
                check_id="visual.review", kind="visual", status="needs_review",
                reason="视觉评审没有返回任何判词 —— 空白判词不能当通过"))
        covered = {}
        for verdict in verdicts:
            data = dict(verdict)
            status = str(data.get("status") or "needs_review")
            step_id = str(data.get("step_id") or "")
            step = steps_by_id.get(step_id, {})
            step_scene = step_frames.get(step_id) or []
            covered[step_id] = covered.get(step_id, 0) + 1
            if not step or not step_scene:
                checks.append(FrameCheck(check_id=f"visual.unknown.{step_id}", kind="visual",
                    status="needs_review", reason="判词未绑定实际步骤及该步骤真实关键帧"))
                continue
            if status == "fail":
                _require(bool(step_scene),
                         f"视觉评审对 step={step_id!r} 判 fail，但这一步时段内没有任何"
                         f"关键帧 —— 没有那一步的画面就不能声称看到了它（漏检比看错更危险）")
                first = step_scene[0]
                # 失败必须可派发：六件信息缺一就抛，绝不让含糊判词进返修循环
                finding = finding_from_verdict(data, step=step, step_id=step_id or None,
                                               frame=first, owner=None)
                findings.append(finding)
                checks.append(FrameCheck(
                    check_id=f"visual.{step_id or '?'}", kind="visual", status="fail",
                    reason=(f"{finding.finding_id}：{finding.problem} "
                            f"→ 动作 {finding.action}；预期画面：{finding.expected_screen}"),
                    evidence=(finding.frame_hash,)))
                continue
            checks.append(FrameCheck(
                check_id=f"visual.{step_id or '?'}", kind="visual",
                status=status if status in CHECK_STATUSES else "needs_review",
                reason=str(data.get("reason") or "（视觉评审没有给理由）"),
                evidence=tuple(reviewer_inputs[:2])))

        for step_id in steps_by_id:
            if covered.get(step_id, 0) != 1:
                checks.append(FrameCheck(check_id=f"visual.coverage.{step_id}", kind="visual",
                    status="needs_review", reason="每个实际步骤必须恰有一条独立判词；当前缺失或重复"))

    # ── 汇总：fail > needs_review > pass
    if any(c.status == "fail" for c in checks):
        status = "fail"
    elif any(c.status == "needs_review" for c in checks):
        status = "needs_review"
    else:
        status = "pass"
    notes = []
    if dropped:
        notes.append(f"按固定步长抽帧丢弃 {len(dropped)} 张（逐条记录，未静默截断）")
    notes.append(f"抽帧上限 {max_images}，送评审 {len(selected)} 张；"
                 f"每个步骤至少保留 1 张")
    return FrameReport(status=status, checks=tuple(checks),
                       evidence_hashes=tuple(sorted(set(h for h in evidence if h))),
                       dropped_frames=tuple(dropped), reviewer_inputs=tuple(reviewer_inputs),
                       notes=tuple(notes), findings=tuple(findings),
                       profile=chosen_profile, frame_hashes=frame_hashes, images=images)


def chosen_or_all(spec: dict, result: dict, frames: Iterable[dict],
                  max_images: int) -> list[dict]:
    """像素检查的对象：优先用抽帧结果；没有可选帧时返回全部（宁可多检查也不少检查）。"""
    selected, _dropped = select_frames(spec, result, max_images=max_images)
    return selected or list(frames)


__all__ = ["CHECK_KINDS", "CHECK_STATUSES", "REPORT_STATUSES", "PROFILE_SIZES",
           "MIN_DISTINCT_COLOURS", "MAX_CAPTION_CHARS",
           "FINDING_KINDS", "FINDING_SEVERITIES", "FINDING_OWNERS",
           "CAMERA_OP_PREFIX", "SPEC_OP_PREFIX", "CRITERION_METHODS",
           "CRITERION_EVIDENCE_TYPES",
           "FrameCheck", "FrameReport", "FrameFinding", "select_frames", "verify_frames",
           "image_inputs", "accepts_images", "finding_from_verdict", "owner_for_kind",
           "is_camera_only", "action_op"]


def _select_v2_frames(spec, result, *, max_images):
    """The proof of a change needs both sides of the *same* targeted action."""
    per_step=_step_frames(spec,result);chosen=[]
    for step in spec['steps']:
        frames=per_step.get(step['step_id'],[])
        requirements=[]
        if step['kind']=='observe': requirements=[('observe',None,None)]
        else:
            for action in step['actions']:
                phases=('before','during','after') if action['action']=='highlight' else ('before','after')
                requirements.extend((phase,action['action'],action['object_id']) for phase in phases)
        for phase,action,oid in requirements:
            found=next((f for f in frames if f.get('phase')==phase and
                        (action is None or (f.get('action')==action and f.get('object_id')==oid))),None)
            if found is None:
                raise ContractError(f"步骤 {step['step_id']} 缺少 {oid or 'observation'} 的 {phase} 实帧证据")
            if found not in chosen: chosen.append(found)
    if len(chosen)>max_images:
        raise ContractError(f'关键变化前后实帧需要 {len(chosen)} 张，当前 max_images={max_images}；必须分批审核，不能丢弃变化一侧')
    dropped=[dict(f,reason='非必要补充观察帧') for f in _by_kind(result,'keyframe') if f not in chosen]
    return chosen,dropped
