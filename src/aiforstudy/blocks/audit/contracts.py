"""返修契约 `2.0`：**一条能派出去的工单长什么样**。

## 这个模块为什么存在

旧链路的审核不是"没有反馈"，而是反馈里**关键信息丢失、责任分派错误**（调研 §1.2）：

| 环节 | 旧链路实际发生了什么 |
|---|---|
| 发现问题 | `Finding.evidence` 这一格被 `required_condition` 占用了，**教材依据没地方放** |
| 内容返修 | 用 judge 角色去改正文；提示词里没有教材片段、没有独立通过标准、没有上一轮为什么失败 |
| 应用补丁 | 补丁被拒的原因没有沉淀成下一轮可消费的反馈 |
| 关闭核验 | 早期实现的 `_verdict_closed` 只回一个 bool，**`why` 与拒绝原因被丢掉** |
| 终止 | "改了几条"被当成"有进展" |

后果是：生成器收到「这句话有问题，改一改」，它既不知道卡在哪、也不知道凭什么标准算改好了。
本模块把「一条工单必须回答六件事」做成**构造期就会失败的断言**——
把它变成"结构上做不到"，而不是在 prompt 里加一句"请认真填写"。

## 六件事（§5.1）

1. 哪里卡住 —— `problem`
2. 为什么初学者会卡 —— `learner_obstacle`
3. 教材依据是什么 —— `source_evidence`（必须是**真实切片**：`quote` 等于 `[start,end)`）
4. 具体改哪里、怎么改 —— `repair_instructions.{locate,action,rationale,expected_change}`
5. 哪些内容不得改 —— `preserve`（配合 `allowed_paths` 构成补丁白名单）
6. 逐条达到什么标准才通过 —— `criteria[]`（每条自带 `expected` / `method` / `version`）

少任何一条，工单**不许**自称 `status="ready"`；诊断确实不充分时只能以 `needs_evidence`
存在，先由 `owner="reviewer"` 补齐（§5.1）。这条区分很重要：`needs_evidence` 不是"跳过"，
而是"这条工单还没资格派出去"。

## 两个刻意的设计选择

**ID 用 UUIDv5（确定性），不用 uuid4（随机）。** §5.1 说"第一次发现时生成 UUID 并持久化"，
§4.7 又说"重复 verdict 内容不能通过换 ID 重获次数"。随机 UUID 会让后半条**结构上不成立**：
同一个问题每次发现都拿到新 ID。所以 ID 由 `(rule, unit_id, artifact_id, 稳定锚点)` 派生，
既是 UUID 又可复算；持久化仍然必须做，历史仍不可覆盖。

**锚点优先用结构化指针，其次才是正文。** §5.1 明确"不能用现有原句作为唯一 ID"。
`target.anchor_kind` 如实记录用的是哪一种（`pointer` / `step` / `frame` / `ordinal` / `text`），
所以"这句话改了"到底算不算同一个问题，在数据里是**看得见**的，不靠猜。

## 本模块不做的事

不调度、不重试、不调用模型、不写文件。它只有类型、校验与纯函数；
调度在 `pipeline/repair.py`（T04），补丁落盘在 `patches.py`（T02）。
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from ...core.digest import canonical_json
from ...errors import ContractError

#: 契约版本。**不兼容变更必须改版本号**：旧工单不许被当新契约用。
SCHEMA_VERSION = "2.0"

#: 旧的 schema 版本号（只用来给出清楚的拒绝理由）
_LEGACY_SCHEMA_VERSIONS = frozenset({"1.0", "1.1", "1.2", "1.3"})

#: 问题种类（§5.1）
KINDS = ("text_content", "visual_math", "visual_layout", "evidence_format")
#: 责任角色。**生成正文用 lesson，评审用 reviewer**（§5.2：不许用 judge 角色改正文）。
OWNERS = ("lesson", "visual_planner", "visual_renderer", "interaction",
          "reviewer")
#: 工单状态机（§5.1）
STATUSES = ("needs_evidence", "ready", "closed")
#: 严重度：沿用现有 P0/P1/P2 发布政策
SEVERITIES = ("P0", "P1", "P2")
#: 需要关闭才能发布的严重度
BLOCKING_SEVERITIES = ("P0", "P1")
#: 判据的验证方式（§5.1）
METHODS = ("rule", "math", "reviewer", "frame", "browser")
#: 判据的证据形态
EVIDENCE_TYPES = ("text", "formula", "figure", "frame", "file")
#: 判词状态
VERDICT_STATUSES = ("pass", "fail", "needs_evidence")
#: verifier 的角色。`judge` 是**评审**角色，不许拿去生成正文。
VERIFIER_ROLES = ("rule", "math", "judge", "frame", "browser")
#: 补丁只允许这三种操作（§5.1）
PATCH_OPS = ("test", "replace", "insert_after")

#: 不需要 `expected` 就合法的判据形态：证据格式类判据判的是"引用是否可定位"，
#: 不是"内容达到什么水平"。这是首期唯一允许的例外，且必须显式列在这里。
_NO_EXPECTED_METHODS = frozenset()


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _require_text(value: Any, field_name: str, *, where: str = "") -> str:
    text = "" if value is None else str(value)
    if not text.strip():
        prefix = f"{where}：" if where else ""
        raise ContractError(f"{prefix}{field_name} 必填且不能是空白")
    return text


def _reject_unknown(data: dict, allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ContractError(
            f"{where} 里有未知字段 {unknown} —— 契约不静默吞字段："
            f"悄悄丢掉一个字段，等于悄悄丢掉一条通过标准")


def _normalize_anchor(text: str) -> str:
    """锚点归一：折叠空白、去掉大小写差异。

    只做这一层，**不做**相似度、不做同义改写 —— 相似度匹配正是"凭感觉关单"的入口。
    """
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def _stable_digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


#: 工单 ID 的命名空间。固定值，改它等于把所有历史工单的 ID 全部作废。
TICKET_NAMESPACE = uuid.UUID("6f6d2b1c-1f4e-5a7b-9c3d-2b8e4a5f6d70")


def make_ticket_id(*, rule: str, unit_id: str, artifact_id: str, location: str) -> str:
    """由**稳定身份**派生工单 ID。

    `location` 必须是**稳定锚点**（结构化指针优先），不是"现有原句"本身。
    同一规则在不同位置 → 不同 ID；同一位置重复发现 → 同一 ID（幂等，续跑不会重复开单）。
    """
    _require_text(rule, "rule", where="make_ticket_id")
    _require_text(unit_id, "unit_id", where="make_ticket_id")
    _require_text(artifact_id, "artifact_id", where="make_ticket_id")
    _require_text(location, "location", where="make_ticket_id")
    identity = f"{rule.strip()}|{unit_id.strip()}|{artifact_id.strip()}|{location.strip()}"
    return str(uuid.uuid5(TICKET_NAMESPACE, identity))


def target_location_key(target: dict) -> str:
    """从 `target` 里取稳定锚点，并给出 `anchor_kind`。"""
    pointer = str(target.get("json_pointer") or "").strip()
    if pointer:
        return f"p:{pointer}", "pointer"
    step_id = str(target.get("step_id") or "").strip()
    if step_id:
        return f"s:{step_id}", "step"
    frame_hash = str(target.get("frame_hash") or "").strip()
    if frame_hash:
        return f"f:{frame_hash}", "frame"
    ordinal = target.get("ordinal")
    if ordinal is not None and str(ordinal).strip():
        return f"n:{ordinal}", "ordinal"
    text = str(target.get("location") or "").strip()
    if text:
        return f"t:{_normalize_anchor(text)}", "text"
    raise ContractError(
        "target 必须给出稳定锚点：json_pointer / step_id / frame_hash / ordinal / location 五选一。"
        "没有锚点就无法判断'同一个问题'，也无法在续跑时不重复开单")


def verdict_identity(verdict: dict | "CriterionVerdict") -> str:
    """判词的**内容身份**（§4.7）。

    刻意不包含 `verdict_id`：换一个 ID 不能让同一段判词重新获得一次证据修复机会。
    也不包含 `spans`／时间：证据补齐**不该**换身份（那正是 S01/S02 要做的事）。
    """
    data = verdict.as_dict() if isinstance(verdict, CriterionVerdict) else dict(verdict)
    payload = {
        "ticket_id": str(data.get("ticket_id") or ""),
        "criterion_id": str(data.get("criterion_id") or ""),
        "criteria_version": data.get("criteria_version"),
        "artifact_hash": str(data.get("artifact_hash") or ""),
        "status": str(data.get("status") or ""),
        "why": _normalize_anchor(str(data.get("why") or "")),
    }
    return "sha256:" + _stable_digest(payload)



def normalize_verdict_content(value):
    """Actual judgment content, independent of transport/artifact identities."""
    ignored = {'ticket_id', 'verdict_id', 'artifact_hash', 'evidence_id', 'evidence_ids', 'evidence_refs',
               'feedback_digest', 'authorization_digest', 'base_content_hash', 'base_hash',
               'content_hash', 'source_context_hash', 'response_id', 'timestamp',
               'created_at', 'updated_at', 'at'}
    if isinstance(value, dict):
        return {key: normalize_verdict_content(
                    re.sub(r'external:[^:]+:', 'external:', str(item)) if key == 'criterion_id'
                    and str(item).startswith('external:') else item)
                for key, item in sorted(value.items()) if key not in ignored}
    if isinstance(value, (tuple, list)):
        return [normalize_verdict_content(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    return value


def ticket_verdict_content(ticket):
    """Preserve current reviewer information while omitting revision identities."""
    data = ticket.as_dict() if hasattr(ticket, 'as_dict') else dict(ticket)
    previous = data.get('previous_attempt') or {}
    return normalize_verdict_content({
        'target': {key:value for key,value in (data.get('target') or {}).items()
                   if key != 'artifact_hash'},
        'problem': data.get('problem'),
        'learner_obstacle': data.get('learner_obstacle'),
        'criteria': data.get('criteria') or [],
        'repair_instructions': data.get('repair_instructions') or {},
        'source_evidence': data.get('source_evidence') or [],
        'discovery': previous.get('discovery'),
        'closure_reason': previous.get('closure_reason') or data.get('problem'),
    })

# --------------------------------------------------------------------------- #
# 证据与判据
# --------------------------------------------------------------------------- #
_EVIDENCE_FIELDS = frozenset({"artifact_id", "artifact_hash", "json_pointer", "start",
                              "end", "quote", "frame_hash", "step_id", "timestamp"})


@dataclass(frozen=True)
class EvidenceSpan:
    """引用的**真实切片**（§5.1）。

    `start`/`end` 是"所指的那个字符串"的 Unicode 字符下标，**半开区间**；
    `quote` 必须等于真实切片。图／帧另用 `frame_hash` / `step_id` / `timestamp`。
    """
    artifact_id: str
    artifact_hash: str
    json_pointer: str
    start: int
    end: int
    quote: str
    frame_hash: str | None = None
    step_id: str | None = None
    timestamp: float | None = None

    def __post_init__(self) -> None:
        _require_text(self.artifact_id, "artifact_id", where="EvidenceSpan")
        _require_text(self.artifact_hash, "artifact_hash", where="EvidenceSpan")
        _require_text(self.json_pointer, "json_pointer", where="EvidenceSpan")
        _require(isinstance(self.start, int) and isinstance(self.end, int),
                 f"EvidenceSpan: start/end 必须是整数，实际 {type(self.start).__name__}/"
                 f"{type(self.end).__name__}")
        _require(self.start >= 0, f"EvidenceSpan: start 不能为负，实际 {self.start}")
        _require(self.end > self.start,
                 f"EvidenceSpan: end({self.end}) 必须大于 start({self.start})，"
                 f"空区间不可能是一条证据")
        _require_text(self.quote, "quote", where="EvidenceSpan")

    def check_against(self, text: str) -> bool:
        """`quote` 是不是 `text[start:end]`。**不等就是证据造假或过期**，直接报错。"""
        _require(isinstance(text, str), "check_against 需要一个字符串")
        if self.end > len(text):
            raise ContractError(
                f"EvidenceSpan: end={self.end} 超出文本长度 {len(text)} —— "
                f"证据指向了不存在的位置（多半是引用了旧 revision）")
        actual = text[self.start:self.end]
        if actual != self.quote:
            raise ContractError(
                f"EvidenceSpan: quote 与真实切片不符。"
                f"声明 {self.quote[:40]!r}…，实际 {actual[:40]!r}…")
        return True

    def as_dict(self) -> dict:
        out = {"artifact_id": self.artifact_id, "artifact_hash": self.artifact_hash,
               "json_pointer": self.json_pointer, "start": self.start, "end": self.end,
               "quote": self.quote}
        if self.frame_hash is not None:
            out["frame_hash"] = self.frame_hash
        if self.step_id is not None:
            out["step_id"] = self.step_id
        if self.timestamp is not None:
            out["timestamp"] = self.timestamp
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceSpan":
        _reject_unknown(data, _EVIDENCE_FIELDS, "EvidenceSpan")
        return cls(artifact_id=str(data.get("artifact_id") or ""),
                   artifact_hash=str(data.get("artifact_hash") or ""),
                   json_pointer=str(data.get("json_pointer") or ""),
                   start=int(data.get("start", -1)), end=int(data.get("end", -1)),
                   quote=str(data.get("quote") or ""),
                   frame_hash=data.get("frame_hash"), step_id=data.get("step_id"),
                   timestamp=data.get("timestamp"))


_CRITERION_FIELDS = frozenset({"criterion_id", "version", "required", "expected", "method",
                               "tolerance", "evidence_type", "dependency_paths", "pending"})


@dataclass(frozen=True)
class Criterion:
    """一条**独立的**通过标准（§5.1）。

    "独立"是要紧的：`method="reviewer"` 的判据不能拿 `method="rule"` 的结果当证据，
    否则"独立核验"就退化成"同一个人说了两遍"。

    `pending=True` 是给**旧工单导入**留的唯一缺口：旧 `Finding` 里根本没有通过标准，
    导入时既不能编造标准，也不能因为"标准还没写"就拒绝建档。所以它可以存在、
    但 `expected` 为空、且**永远不能拿来核验**（见 `assert_verdict_matches_ticket`），
    因此这种工单必然停在 `needs_evidence`。
    """
    criterion_id: str
    version: int
    required: bool
    expected: str
    method: str
    tolerance: float | None = None
    evidence_type: str = "text"
    dependency_paths: tuple[str, ...] = ()
    pending: bool = False

    def __post_init__(self) -> None:
        _require_text(self.criterion_id, "criterion_id", where="Criterion")
        _require(isinstance(self.version, int) and self.version >= 1,
                 f"Criterion {self.criterion_id}: version 从 1 起算，实际 {self.version!r}")
        _require(self.method in METHODS,
                 f"Criterion {self.criterion_id}: method 必须是 {list(METHODS)}，"
                 f"实际 {self.method!r}")
        _require(self.evidence_type in EVIDENCE_TYPES,
                 f"Criterion {self.criterion_id}: evidence_type 必须是 "
                 f"{list(EVIDENCE_TYPES)}，实际 {self.evidence_type!r}")
        if self.tolerance is not None:
            _require(isinstance(self.tolerance, (int, float)) and self.tolerance >= 0,
                     f"Criterion {self.criterion_id}: tolerance 必须是非负数")
        if self.pending:
            return
        _require(self.expected.strip(),
                 f"Criterion {self.criterion_id}: expected 必填 —— "
                 f"没有 expected 的标准只能靠'看起来还行'来判，那正是要治的病")

    @property
    def usable(self) -> bool:
        """能不能拿来核验。待补标准不能。"""
        return (not self.pending) and bool(self.expected.strip())

    @property
    def fingerprint(self) -> str:
        """标准内容指纹：版本号没变但内容变了，也要被认出来。"""
        return _stable_digest(self.as_dict())

    def as_dict(self) -> dict:
        out = {"criterion_id": self.criterion_id, "version": self.version,
               "required": bool(self.required), "expected": self.expected,
               "method": self.method, "evidence_type": self.evidence_type,
               "dependency_paths": list(self.dependency_paths),
               "pending": bool(self.pending)}
        if self.tolerance is not None:
            out["tolerance"] = self.tolerance
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "Criterion":
        _reject_unknown(data, _CRITERION_FIELDS, "Criterion")
        return cls(criterion_id=str(data.get("criterion_id") or ""),
                   version=int(data.get("version", 0)),
                   required=bool(data.get("required", True)),
                   expected=str(data.get("expected") or ""),
                   method=str(data.get("method") or ""),
                   tolerance=data.get("tolerance"),
                   evidence_type=str(data.get("evidence_type") or "text"),
                   dependency_paths=tuple(data.get("dependency_paths") or ()),
                   pending=bool(data.get("pending", False)))


# --------------------------------------------------------------------------- #
# 证据定位与关单（§5.2 的返回类型；实现在 evidence.py / feedback.py）
# --------------------------------------------------------------------------- #
#: 证据定位结果
RESOLUTION_STATUSES = ("valid", "invalid", "ambiguous")


@dataclass(frozen=True)
class EvidenceResolution:
    """`locate_evidence` 的结果。

    `ambiguous` 是**必须存在**的一档：归一之后正文里出现多处匹配时，
    "挑第一处"就是凭感觉关单。宁可返回 ambiguous 让人来看（§5.2）。
    """
    status: str
    reason: str
    spans: tuple[EvidenceSpan, ...] = ()
    #: 归一映射：`{"removed": [...], "mapped_offsets": [...], "rule": "..."}`；
    #: 用来证明"只允许控制序列／分组边界的展示空白归一"，且能映射回原文 offset。
    normalization: dict = field(default_factory=dict)
    matched_offsets: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        _require(self.status in RESOLUTION_STATUSES,
                 f"EvidenceResolution.status 必须是 {list(RESOLUTION_STATUSES)}，"
                 f"实际 {self.status!r}")
        _require_text(self.reason, "reason", where="EvidenceResolution")
        object.__setattr__(self, "spans", tuple(self.spans))
        object.__setattr__(self, "matched_offsets", tuple(tuple(o) for o in self.matched_offsets))
        if self.status == "valid":
            _require(self.spans,
                     "EvidenceResolution: status=valid 必须给出至少一个真实 span —— "
                     "没有 span 的 valid 就是'我说它有效'")
        if self.status == "ambiguous":
            _require(len(self.matched_offsets) > 1,
                     "EvidenceResolution: ambiguous 必须给出多于一处匹配位置，"
                     "否则它其实是 valid 或 invalid")

    def as_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason,
                "spans": [s.as_dict() for s in self.spans],
                "normalization": dict(self.normalization),
                "matched_offsets": [list(o) for o in self.matched_offsets]}


@dataclass(frozen=True)
class ClosureDecision:
    """能不能关单（§5.2）。

    `closed=True` 的门槛是：正确的 `artifact_hash`、冻结的 `criteria_version`、
    以及**全部 required criterion 的独立有效证据**。生成器说"已修好"不算。
    """
    closed: bool
    reason: str
    failed_criteria: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    #: 逐条判词的出处，便于复核（verdict_id → 状态）
    verdicts: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.reason, "reason", where="ClosureDecision")
        object.__setattr__(self, "failed_criteria", tuple(self.failed_criteria))
        object.__setattr__(self, "missing_evidence", tuple(self.missing_evidence))
        object.__setattr__(self, "verdicts", tuple(dict(v) for v in self.verdicts))
        if self.closed:
            _require(not self.failed_criteria and not self.missing_evidence,
                     "ClosureDecision: 有关单失败项或缺证据时不能 closed")

    def as_dict(self) -> dict:
        return {"closed": bool(self.closed), "reason": self.reason,
                "failed_criteria": list(self.failed_criteria),
                "missing_evidence": list(self.missing_evidence),
                "verdicts": [dict(v) for v in self.verdicts]}


def assert_verdict_matches_ticket(ticket: "RepairTicket",
                                  verdict: "CriterionVerdict") -> None:
    """判词必须**对得上工单**：同一条 criterion、同一个冻结版本、同一个 artifact 版本。

    三件事分开报错，因为它们的含义完全不同：

    * criterion 不在工单里 —— 拿着别的标准在判；
    * 版本不一致 —— 拿另一套通过标准判（§5.2 标准版本不可变）；
    * `artifact_hash` 不一致 —— 拿**旧版本**的通过去关新版本的工单（A04 的"旧 hash 不能关单"）。
    """
    _require(verdict.ticket_id == ticket.ticket_id,
             f"判词属于工单 {verdict.ticket_id!r}，却拿来核验 {ticket.ticket_id!r}")
    frozen = {c.criterion_id: c for c in ticket.criteria}
    criterion = frozen.get(verdict.criterion_id)
    if criterion is None:
        raise ContractError(
            f"判词引用了工单 {ticket.ticket_id} 里没有的 criterion "
            f"{verdict.criterion_id!r} —— 新问题要新建工单，不许拿别的标准来关单")
    if verdict.criteria_version != criterion.version:
        raise ContractError(
            f"criterion {criterion.criterion_id} 冻结版本是 {criterion.version}，"
            f"判词用的是 {verdict.criteria_version} —— 通过标准的版本不可变")
    if not criterion.usable:
        raise ContractError(
            f"criterion {criterion.criterion_id} 的 expected 还是空的（待补标准），"
            f"没有任何人能核验它 —— 先补标准再验，不许拿空标准判通过")
    declared = str(ticket.target.get("artifact_hash") or "")
    if declared and verdict.artifact_hash != declared:
        raise ContractError(
            f"判词针对 artifact {verdict.artifact_hash[:24]}…，"
            f"而工单绑定的是 {declared[:24]}… —— 旧版本的通过不能用来关新版本的工单")


def criteria_versions(ticket: "RepairTicket") -> dict[str, int]:
    """工单冻结的 `{criterion_id: version}`。"""
    return {c.criterion_id: c.version for c in ticket.criteria}


def assert_criteria_unchanged(ticket: "RepairTicket", criteria: Any) -> None:
    """重验时必须用**同一套**标准（§5.2）。

    两类都必须拒绝，且理由不同：
    * 版本被改 —— 偷改通过标准；
    * 版本没变但内容变了 —— 暗改标准（更难发现，所以必须单独判）。
    """
    incoming = [c if isinstance(c, Criterion) else Criterion.from_dict(c)
                for c in criteria]
    frozen = {c.criterion_id: c for c in ticket.criteria}
    seen: set[str] = set()
    for criterion in incoming:
        seen.add(criterion.criterion_id)
        original = frozen.get(criterion.criterion_id)
        if original is None:
            raise ContractError(
                f"重验引用了工单里没有的 criterion {criterion.criterion_id!r} —— "
                f"新发现的问题要新建工单，不许往旧工单上挂新标准")
        if criterion.version != original.version:
            raise ContractError(
                f"criterion {criterion.criterion_id} 的版本从 {original.version} "
                f"变成 {criterion.version} —— 通过标准的版本不可变，"
                f"要改标准就新建 criterion 版本并留裁定依据")
        if criterion.fingerprint != original.fingerprint:
            raise ContractError(
                f"criterion {criterion.criterion_id} 版本号没变（{original.version}）"
                f"但标准内容变了 —— 这是**暗改**通过标准，同样拒绝")
    missing = set(frozen) - seen
    if missing:
        raise ContractError(
            f"重验缺少 required criterion {sorted(missing)} —— "
            f"少验一条不许算通过")


@dataclass(frozen=True)
class CriterionVerdict:
    """一条判词的**完整交代**（§5.1）。

    旧链路只回一个 bool，`why` 与拒绝原因就丢了。这里 `why` 必填，
    且 `criteria_version` 必须与工单冻结的一致 —— 判词不能拿另一套标准来判。
    """
    ticket_id: str
    criterion_id: str
    criteria_version: int
    artifact_hash: str
    status: str
    why: str
    verdict_id: str = ""
    spans: tuple[EvidenceSpan, ...] = ()
    verifier: dict | None = None
    depends_on_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.ticket_id, "ticket_id", where="CriterionVerdict")
        _require_text(self.criterion_id, "criterion_id", where="CriterionVerdict")
        _require_text(self.artifact_hash, "artifact_hash", where="CriterionVerdict")
        _require(self.status in VERDICT_STATUSES,
                 f"CriterionVerdict: status 必须是 {list(VERDICT_STATUSES)}，实际 {self.status!r}")
        _require_text(self.why, "why", where="CriterionVerdict")
        _require(isinstance(self.criteria_version, int) and self.criteria_version >= 1,
                 f"CriterionVerdict: criteria_version 从 1 起算，实际 {self.criteria_version!r}")
        if self.verifier is not None:
            _reject_unknown(self.verifier, frozenset({"role", "implementation", "model",
                                                      "version"}), "CriterionVerdict.verifier")
            role = str(self.verifier.get("role") or "")
            _require(role in VERIFIER_ROLES,
                     f"CriterionVerdict.verifier.role 必须是 {list(VERIFIER_ROLES)}，"
                     f"实际 {role!r}")
        object.__setattr__(self, "spans", tuple(self.spans))
        object.__setattr__(self, "depends_on_hashes", tuple(self.depends_on_hashes))
        if not self.verdict_id:
            object.__setattr__(self, "verdict_id", verdict_identity(self))
        # `pass` 必须带证据：没有 span 的"通过"只是模型说它过了
        if self.status == "pass" and not self.spans and not self.depends_on_hashes:
            raise ContractError(
                f"CriterionVerdict {self.criterion_id}: 判 pass 必须给出真实 span "
                f"或独立依赖哈希 —— 只回一句'已修好'不算通过")

    @property
    def identity(self) -> str:
        return verdict_identity(self)

    def as_dict(self) -> dict:
        return {"verdict_id": self.verdict_id, "ticket_id": self.ticket_id,
                "criterion_id": self.criterion_id,
                "criteria_version": self.criteria_version,
                "artifact_hash": self.artifact_hash, "status": self.status,
                "why": self.why,
                "spans": [s.as_dict() for s in self.spans],
                "verifier": dict(self.verifier) if self.verifier else None,
                "depends_on_hashes": list(self.depends_on_hashes)}


# --------------------------------------------------------------------------- #
# 不可变 revision
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ArtifactRevision:
    """一个 artifact 的**不可变**版本（§5.1）。旧文件不可覆盖。"""
    artifact_id: str
    unit_id: str
    revision: int
    hash: str
    parent_hash: str | None = None
    context_digest: str = ""

    def __post_init__(self) -> None:
        _require_text(self.artifact_id, "artifact_id", where="ArtifactRevision")
        _require_text(self.unit_id, "unit_id", where="ArtifactRevision")
        _require(isinstance(self.revision, int) and self.revision >= 1,
                 f"ArtifactRevision: revision 从 1 起算，实际 {self.revision!r}")
        _require_text(self.hash, "hash", where="ArtifactRevision")

    def as_dict(self) -> dict:
        return {"artifact_id": self.artifact_id, "unit_id": self.unit_id,
                "revision": self.revision, "hash": self.hash,
                "parent_hash": self.parent_hash, "context_digest": self.context_digest}

    @classmethod
    def from_dict(cls, data: dict) -> "ArtifactRevision":
        return cls(artifact_id=str(data.get("artifact_id") or ""),
                   unit_id=str(data.get("unit_id") or ""),
                   revision=int(data.get("revision", 0)), hash=str(data.get("hash") or ""),
                   parent_hash=data.get("parent_hash"),
                   context_digest=str(data.get("context_digest") or ""))


class RevisionLedger:
    """一条 artifact 的 revision 链。**只增不改**。

    三件事在这里被做成结构性保证：

    * 同一个 revision 换个内容盖回去 → 拒绝（改历史）；
    * 同一个 revision 原样重放 → 幂等返回（续跑不会炸）；
    * 跳号追加 → 拒绝（跳过的版本会让"上一版是什么"变成猜谜）。
    """

    def __init__(self, *, artifact_id: str, unit_id: str,
                 revisions: tuple[ArtifactRevision, ...] = ()) -> None:
        self.artifact_id = _require_text(artifact_id, "artifact_id", where="RevisionLedger")
        self.unit_id = _require_text(unit_id, "unit_id", where="RevisionLedger")
        self._revisions: list[ArtifactRevision] = list(revisions)
        for expected, item in enumerate(self._revisions, start=1):
            _require(item.revision == expected,
                     f"RevisionLedger: revision 必须从 1 连续递增，第 {expected} 个是 "
                     f"{item.revision}")

    # ── 查询
    @property
    def revisions(self) -> tuple[ArtifactRevision, ...]:
        return tuple(self._revisions)

    @property
    def head(self) -> ArtifactRevision | None:
        return self._revisions[-1] if self._revisions else None

    def get(self, revision: int) -> ArtifactRevision | None:
        for item in self._revisions:
            if item.revision == revision:
                return item
        return None

    # ── 追加
    def append(self, *, hash: str, context_digest: str = "",
               revision: int | None = None) -> ArtifactRevision:
        _require_text(hash, "hash", where="RevisionLedger.append")
        head = self.head
        next_revision = (head.revision + 1) if head is not None else 1
        target = next_revision if revision is None else int(revision)

        existing = self.get(target)
        if existing is not None:
            if existing.hash == hash and existing.context_digest == context_digest:
                return existing                      # 幂等重放
            raise ContractError(
                f"revision {target} 已存在（hash {existing.hash[:24]}…），"
                f"不能用 {hash[:24]}… 覆盖 —— revision 不可变，历史不许改写")
        if target != next_revision:
            raise ContractError(
                f"只能追加到 revision {next_revision}，"
                f"不能跳到 {target} —— 跳号会让'上一版是什么'无从判断")

        item = ArtifactRevision(artifact_id=self.artifact_id, unit_id=self.unit_id,
                                revision=target, hash=hash,
                                parent_hash=head.hash if head else None,
                                context_digest=context_digest)
        self._revisions.append(item)
        return item

    # ── 序列化
    def as_dict(self) -> dict:
        return {"artifact_id": self.artifact_id, "unit_id": self.unit_id,
                "revisions": [r.as_dict() for r in self._revisions]}

    @classmethod
    def from_dict(cls, data: dict) -> "RevisionLedger":
        return cls(artifact_id=str(data.get("artifact_id") or ""),
                   unit_id=str(data.get("unit_id") or ""),
                   revisions=tuple(ArtifactRevision.from_dict(r)
                                   for r in data.get("revisions") or ()))


# --------------------------------------------------------------------------- #
# 工单
# --------------------------------------------------------------------------- #
_TARGET_FIELDS = frozenset({"artifact_id", "artifact_hash", "rule", "json_pointer",
                            "step_id", "frame_hash", "ordinal", "location", "anchor_kind"})
_INSTRUCTION_FIELDS = frozenset({"locate", "action", "rationale", "expected_change"})
_TICKET_FIELDS = frozenset({
    "schema_version", "ticket_id", "unit_id", "kind", "severity", "owner", "status",
    "target", "problem", "learner_obstacle", "source_evidence", "repair_instructions",
    "allowed_paths", "preserve", "criteria", "previous_attempt", "history", "usage_refs",
    "hard_acceptance", "legacy_required_condition",
})


@dataclass
class RepairTicket:
    """一条返修工单。字段语义见模块文档与 §5.1。"""
    ticket_id: str
    unit_id: str
    kind: str
    severity: str
    owner: str
    status: str
    target: dict
    problem: str
    learner_obstacle: str
    source_evidence: tuple[EvidenceSpan, ...]
    repair_instructions: dict
    allowed_paths: tuple[str, ...]
    preserve: tuple[str, ...]
    criteria: tuple[Criterion, ...]
    previous_attempt: dict | None = None
    history: tuple[dict, ...] = ()
    usage_refs: tuple[str, ...] = ()
    hard_acceptance: bool = False
    legacy_required_condition: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION,
                 f"工单 schema_version 必须是 {SCHEMA_VERSION!r}，实际 {self.schema_version!r}")
        _require_text(self.ticket_id, "ticket_id", where="RepairTicket")
        _require_text(self.unit_id, "unit_id", where="RepairTicket")
        _require(self.kind in KINDS,
                 f"kind 必须是 {list(KINDS)}，实际 {self.kind!r}")
        _require(self.severity in SEVERITIES,
                 f"severity 必须是 {list(SEVERITIES)}，实际 {self.severity!r}")
        _require(self.owner in OWNERS,
                 f"owner 必须是 {list(OWNERS)}，实际 {self.owner!r}")
        _require(self.status in STATUSES,
                 f"status 必须是 {list(STATUSES)}，实际 {self.status!r}")
        _require_text(self.problem, "problem", where=f"RepairTicket {self.ticket_id}")
        _reject_unknown(self.target, _TARGET_FIELDS, "RepairTicket.target")
        _require_text(self.target.get("artifact_id"), "target.artifact_id",
                      where=f"RepairTicket {self.ticket_id}")
        object.__setattr__(self, "source_evidence", tuple(self.source_evidence))
        object.__setattr__(self, "criteria", tuple(self.criteria))
        object.__setattr__(self, "allowed_paths", tuple(self.allowed_paths))
        object.__setattr__(self, "preserve", tuple(self.preserve))
        object.__setattr__(self, "usage_refs", tuple(self.usage_refs))
        object.__setattr__(self, "history", tuple(self.history))
        seen: set[str] = set()
        for criterion in self.criteria:
            _require(criterion.criterion_id not in seen,
                     f"criterion_id {criterion.criterion_id!r} 在工单里重复 —— "
                     f"重复的标准会让'验了几条'数不清")
            seen.add(criterion.criterion_id)

    # ── 语义
    @property
    def is_blocking(self) -> bool:
        """会不会挡住发布。

        ⚠️ 硬验收失败**恒为** blocking：不能把 severity 改成 P2 就放行（§5.1）。
        """
        if self.hard_acceptance:
            return self.status != "closed"
        return self.severity in BLOCKING_SEVERITIES and self.status != "closed"

    @property
    def is_dispatchable(self) -> bool:
        """有没有资格派给生成器。`ready` 是唯一可以派的状态（§5.1）。"""
        return self.status == "ready"

    @property
    def missing_answers(self) -> tuple[str, ...]:
        """六件事里还缺哪几件。空元组 = 齐全。"""
        missing: list[str] = []
        if not self.learner_obstacle.strip():
            missing.append("learner_obstacle")
        if not self.source_evidence:
            missing.append("source_evidence")
        instructions = self.repair_instructions or {}
        for key in ("locate", "action", "rationale", "expected_change"):
            if not str(instructions.get(key) or "").strip():
                missing.append(f"repair_instructions.{key}")
        # preserve 清单已随 preserve 护栏删除（用户决策）：工单不再被要求回答"哪些不许改"。
        if not self.allowed_paths:
            missing.append("allowed_paths")
        if not self.criteria:
            missing.append("criteria")
        else:
            for criterion in self.criteria:
                if criterion.method not in _NO_EXPECTED_METHODS and \
                        not criterion.expected.strip():
                    missing.append(f"criteria.{criterion.criterion_id}.expected")
        return tuple(missing)

    def criteria_versions(self) -> dict[str, int]:
        return criteria_versions(self)

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "ticket_id": self.ticket_id,
            "unit_id": self.unit_id,
            "kind": self.kind,
            "severity": self.severity,
            "owner": self.owner,
            "status": self.status,
            "target": dict(self.target),
            "problem": self.problem,
            "learner_obstacle": self.learner_obstacle,
            "source_evidence": [s.as_dict() for s in self.source_evidence],
            "repair_instructions": dict(self.repair_instructions or {}),
            "allowed_paths": list(self.allowed_paths),
            "preserve": list(self.preserve),
            "criteria": [c.as_dict() for c in self.criteria],
            "previous_attempt": dict(self.previous_attempt) if self.previous_attempt else None,
            "history": [dict(h) for h in self.history],
            "usage_refs": list(self.usage_refs),
            "hard_acceptance": bool(self.hard_acceptance),
            "legacy_required_condition": self.legacy_required_condition,
        }


def validate_ticket(data: dict | RepairTicket) -> RepairTicket:
    """校验并构造工单。**不完整可派发的工单不许自称 `ready`**（A01）。"""
    if isinstance(data, RepairTicket):
        data = data.as_dict()
    _require(isinstance(data, dict), f"工单必须是 JSON 对象，实际 {type(data).__name__}")
    _reject_unknown(data, _TICKET_FIELDS, "RepairTicket")

    version = str(data.get("schema_version") or "")
    if version in _LEGACY_SCHEMA_VERSIONS:
        raise ContractError(
            f"这是旧契约的工单（schema_version={version}）—— 旧工单只读留档，"
            f"要按新契约使用必须先显式导入并重跑新契约校验，不自动升级（§6.3）")
    if version != SCHEMA_VERSION:
        raise ContractError(
            f"工单 schema_version 必须是 {SCHEMA_VERSION!r}，实际 {version!r}")

    target = data.get("target")
    _require(isinstance(target, dict), "target 必须是 JSON 对象")
    location_key, anchor_kind = target_location_key(target)
    rule = str(target.get("rule") or "").strip()
    _require_text(rule, "target.rule", where="RepairTicket")
    declared_kind = str(target.get("anchor_kind") or "").strip()
    if declared_kind and declared_kind != anchor_kind:
        raise ContractError(
            f"target.anchor_kind 声明为 {declared_kind!r}，但实际用的是 {anchor_kind!r} —— "
            f"锚点类型必须如实标注，否则'同一问题'的判定依据就不可信")

    expected_id = make_ticket_id(rule=rule, unit_id=str(data.get("unit_id") or ""),
                                 artifact_id=str(target.get("artifact_id") or ""),
                                 location=location_key)
    supplied_id = str(data.get("ticket_id") or "")
    if not supplied_id:
        data = {**data, "ticket_id": expected_id}
    elif supplied_id != expected_id:
        raise ContractError(
            f"ticket_id 与稳定身份不符：声明 {supplied_id!r}，按 "
            f"(rule={rule!r}, 锚点={location_key!r}) 应当是 {expected_id!r} —— "
            f"工单 ID 必须由稳定身份派生，不能自报（否则重复判词换个 ID 就能重获一次机会）")

    ticket = RepairTicket(
        ticket_id=str(data.get("ticket_id") or ""),
        unit_id=str(data.get("unit_id") or ""),
        kind=str(data.get("kind") or ""),
        severity=str(data.get("severity") or ""),
        owner=str(data.get("owner") or ""),
        status=str(data.get("status") or ""),
        target={**target, "anchor_kind": anchor_kind},
        problem=str(data.get("problem") or ""),
        learner_obstacle=str(data.get("learner_obstacle") or ""),
        source_evidence=tuple(EvidenceSpan.from_dict(s)
                              for s in data.get("source_evidence") or ()),
        repair_instructions=dict(data.get("repair_instructions") or {}),
        allowed_paths=tuple(data.get("allowed_paths") or ()),
        preserve=tuple(data.get("preserve") or ()),
        criteria=tuple(Criterion.from_dict(c) for c in data.get("criteria") or ()),
        previous_attempt=data.get("previous_attempt"),
        history=tuple(data.get("history") or ()),
        usage_refs=tuple(data.get("usage_refs") or ()),
        hard_acceptance=bool(data.get("hard_acceptance", False)),
        legacy_required_condition=str(data.get("legacy_required_condition") or ""),
    )
    _reject_unknown(ticket.repair_instructions, _INSTRUCTION_FIELDS,
                    "RepairTicket.repair_instructions")

    missing = ticket.missing_answers
    if ticket.status == "ready" and missing:
        raise ContractError(
            f"工单 {ticket.ticket_id} 缺 {list(missing)}，**不能**标 ready 派给生成器 —— "
            f"§5.1：一条工单必须回答'哪里卡住／为什么初学者会卡／教材依据／具体改哪里怎么改／"
            f"哪些不许改／逐条达到什么标准'。诊断不充分请标 needs_evidence，"
            f"由 reviewer 补齐后再派")
    if ticket.status == "closed" and missing:
        raise ContractError(
            f"工单 {ticket.ticket_id} 缺 {list(missing)}，不能进入 "
            f"{ticket.status!r} —— 没有可执行标准就没人能核验它")
    return ticket


# --------------------------------------------------------------------------- #
# 旧 Finding → 候选工单（只读适配）
# --------------------------------------------------------------------------- #
_FINDING_FIELDS = ("code", "severity", "unit_key", "why", "evidence", "where")


def _finding_field(finding: Any, name: str) -> str:
    if isinstance(finding, dict):
        value = finding.get(name)
    else:
        value = getattr(finding, name, None)
    return "" if value is None else str(value)


def legacy_finding_to_ticket(finding: Any, *, artifact_id: str, artifact_hash: str,
                             unit_id: str | None = None,
                             severity: str | None = None) -> RepairTicket:
    """旧 `Finding`／`Ticket.finding` → 候选工单（**只读**适配，§T01）。

    关键约束：旧 `Finding.evidence` 那一格在旧链路里装的其实是 `required_condition`
    （也就是"要求怎么做"），**不是**教材依据。所以它只能进
    `legacy_required_condition` 留档，**绝不能**被塞进 `source_evidence` ——
    否则等于把"依据被占用"这个 bug 原样搬到新契约里，而且这次还带着 `ready` 的通行证。

    结果必然停在 `needs_evidence`、`owner="reviewer"`：先补齐依据与逐条标准，
    再决定派给谁。不编造 `expected`、不编造来源。
    """
    code = _finding_field(finding, "code") or "UNCLASSIFIED"
    raw_severity = severity or _finding_field(finding, "severity") or "P1"
    if raw_severity not in SEVERITIES:
        raise ContractError(
            f"旧工单的 severity={raw_severity!r} 不在 {list(SEVERITIES)} 里 —— "
            f"不猜、不降级，请先确认严重度")
    resolved_unit = unit_id or _finding_field(finding, "unit_key")
    _require_text(resolved_unit, "unit_id", where="legacy_finding_to_ticket")
    where = _finding_field(finding, "where")
    location_key, anchor_kind = target_location_key({"location": where or code})
    target = {"artifact_id": artifact_id, "artifact_hash": artifact_hash, "rule": code,
              "location": where or code, "anchor_kind": anchor_kind}
    ticket_id = make_ticket_id(rule=code, unit_id=resolved_unit,
                               artifact_id=artifact_id, location=location_key)
    why = _finding_field(finding, "why") or "（旧工单没有写为什么）"
    return RepairTicket(
        ticket_id=ticket_id,
        unit_id=resolved_unit,
        kind="text_content",
        severity=raw_severity,
        owner="reviewer",
        status="needs_evidence",
        target=target,
        problem=why,
        learner_obstacle="",
        source_evidence=(),
        repair_instructions={},
        allowed_paths=(),
        preserve=(),
        criteria=(Criterion(criterion_id=f"{code}-待补标准", version=1, required=True,
                            expected="", method="reviewer", pending=True,
                            dependency_paths=(where,) if where else ()),),
        previous_attempt={"legacy_where": where, "legacy_why": why} if where or why else None,
        history=({"event": "imported_from_legacy_finding", "fields": list(_FINDING_FIELDS)},),
        hard_acceptance=raw_severity == "P0",
        legacy_required_condition=_finding_field(finding, "evidence"),
    )


# --------------------------------------------------------------------------- #
# 补丁
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PatchOperation:
    """一次**原子**编辑操作（§5.1）：只允许 test／replace／insert_after。

    只有 JSON pointer + 精确范围，没有任何"写文件"入口 —— 白名单是结构性的，
    不是靠调用方自觉。
    """
    op: str
    path: str
    start: int | None = None
    end: int | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        _require(self.op in PATCH_OPS,
                 f"补丁只允许 {list(PATCH_OPS)}，实际 {self.op!r} —— "
                 f"任意写入／删除／eval 一律拒绝")
        _require_text(self.path, "path", where="PatchOperation")
        if self.op == "insert_after":
            _require(self.start is not None,
                     "insert_after 必须给出插入点 start")
            _require_text(self.text, "text", where="insert_after")
        else:
            _require(self.start is not None and self.end is not None,
                     f"{self.op} 必须给出 start 与 end")
            _require(self.end > self.start,
                     f"{self.op}: end({self.end}) 必须大于 start({self.start})")
            _require_text(self.text, "text", where=self.op)

    def as_dict(self) -> dict:
        return {"op": self.op, "path": self.path, "start": self.start, "end": self.end,
                "text": self.text}

    @classmethod
    def from_dict(cls, data: dict) -> "PatchOperation":
        _reject_unknown(data, frozenset({"op", "path", "start", "end", "text"}),
                        "PatchOperation")
        return cls(op=str(data.get("op") or ""), path=str(data.get("path") or ""),
                   start=data.get("start"), end=data.get("end"), text=data.get("text"))


@dataclass(frozen=True)
class PatchSet:
    """一批原子补丁，绑定 `base_hash`（§5.2）。过期 hash 必须拒绝，不能模糊定位后盲改。"""
    base_hash: str
    criterion_ids: tuple[str, ...]
    operations: tuple[PatchOperation, ...]
    explanation: str
    ticket_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.base_hash, "base_hash", where="PatchSet")
        _require_text(self.explanation, "explanation", where="PatchSet")
        _require(self.criterion_ids, "PatchSet 必须说明它要满足哪些 criterion")
        _require(self.operations, "PatchSet 至少要有一个操作")
        object.__setattr__(self, "operations", tuple(self.operations))
        object.__setattr__(self, "criterion_ids", tuple(self.criterion_ids))
        object.__setattr__(self, "ticket_ids", tuple(self.ticket_ids))

    def as_dict(self) -> dict:
        return {"ticket_ids": list(self.ticket_ids), "base_hash": self.base_hash,
                "criterion_ids": list(self.criterion_ids),
                "operations": [o.as_dict() for o in self.operations],
                "explanation": self.explanation}

    @classmethod
    def from_dict(cls, data: dict) -> "PatchSet":
        return cls(ticket_ids=tuple(data.get("ticket_ids") or ()),
                   base_hash=str(data.get("base_hash") or ""),
                   criterion_ids=tuple(data.get("criterion_ids") or ()),
                   operations=tuple(PatchOperation.from_dict(o)
                                    for o in data.get("operations") or ()),
                   explanation=str(data.get("explanation") or ""))


@dataclass(frozen=True)
class Rejection:
    """一次拒绝。**必须带可执行的下一步**，否则生成器只能重试同一个错误（§5.1）。"""
    code: str
    message: str
    next_step: str

    def __post_init__(self) -> None:
        _require_text(self.code, "code", where="Rejection")
        _require_text(self.message, "message", where="Rejection")
        _require_text(self.next_step, "next_step", where="Rejection")

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "next_step": self.next_step}


@dataclass(frozen=True)
class PatchOutcome:
    """补丁应用结果。`applied=False` 时每个拒绝都要能告诉生成器下一步做什么。"""
    applied: bool
    reason: str = ""
    rejected: tuple[Rejection, ...] = ()
    result_revision: int | None = None
    operation_results: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rejected", tuple(self.rejected))
        object.__setattr__(self, "operation_results", tuple(self.operation_results))
        if self.rejected and not self.reason.strip():
            raise ContractError("有拒绝项时必须写明整体 reason")
        if self.applied and self.rejected:
            raise ContractError(
                "applied=True 与 rejected 非空并存 —— 部分成功必须显式表达为"
                "applied=False + 逐条 operation_results，不能让调用方猜")

    def as_dict(self) -> dict:
        return {"applied": bool(self.applied), "reason": self.reason,
                "rejected": [r.as_dict() for r in self.rejected],
                "result_revision": self.result_revision,
                "operation_results": [dict(r) for r in self.operation_results]}


# --------------------------------------------------------------------------- #
# 调度指令（纯数据）
# --------------------------------------------------------------------------- #
_DIRECTIVE_FIELDS = frozenset({"unit_id", "ticket_ids", "artifact_hashes", "owner",
                               "reason", "kind", "verdict_content"})


@dataclass(frozen=True)
class RepairDirective:
    """阶段交给协调器的**纯数据**请求（§4.2.1）。

    它只说"哪些工单、针对哪几个哈希、期望谁去修、为什么"，
    **不夹带可执行代码** —— 否则"阶段自己偷偷跑别的阶段"就从结构上又回来了。

    `kind` 是 §T04 的最低字段之外的补充，而且是**必要**的：协调器要按类别分账
    （内容批次 vs 证据修复），如果靠解析 `reason` 里的自由文本去猜，那"每个单元两批"
    就会因为一句提示词写法变化而失效。`kind` 为空时协调器会拒绝该 directive，
    而不是替它猜一个。
    """
    unit_id: str
    ticket_ids: tuple[str, ...]
    owner: str
    reason: str = ""
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    kind: str = ""
    verdict_content: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.unit_id, "unit_id", where="RepairDirective")
        _require(self.owner in OWNERS,
                 f"RepairDirective.owner 必须是 {list(OWNERS)}，实际 {self.owner!r}")
        _require(self.ticket_ids, "RepairDirective 必须指向至少一个工单")
        _require_text(self.reason, "reason", where="RepairDirective")
        if self.kind:
            _require(self.kind in KINDS,
                     f"RepairDirective.kind 必须是 {list(KINDS)} 之一，实际 {self.kind!r}")
        object.__setattr__(self, "ticket_ids", tuple(self.ticket_ids))
        object.__setattr__(self, "artifact_hashes", dict(self.artifact_hashes or {}))
        _require(isinstance(self.verdict_content, dict), "RepairDirective.verdict_content 必须为对象")
        object.__setattr__(self, "verdict_content", normalize_verdict_content(self.verdict_content))

    def as_dict(self) -> dict:
        return {"unit_id": self.unit_id, "ticket_ids": list(self.ticket_ids),
                "owner": self.owner, "artifact_hashes": dict(self.artifact_hashes),
                "reason": self.reason, "kind": self.kind, "verdict_content": self.verdict_content}

    @classmethod
    def from_dict(cls, data: dict) -> "RepairDirective":
        _reject_unknown(data, _DIRECTIVE_FIELDS, "RepairDirective")
        raw = data.get("ticket_ids")
        ticket_ids = (raw,) if isinstance(raw, str) else tuple(raw or ())
        return cls(unit_id=str(data.get("unit_id") or ""), ticket_ids=ticket_ids,
                   owner=str(data.get("owner") or ""),
                   artifact_hashes=dict(data.get("artifact_hashes") or {}),
                   reason=str(data.get("reason") or ""), kind=str(data.get("kind") or ""),
                   verdict_content=dict(data.get("verdict_content") or {}))


__all__ = [
    "SCHEMA_VERSION", "KINDS", "OWNERS", "STATUSES", "SEVERITIES",
    "BLOCKING_SEVERITIES", "METHODS", "EVIDENCE_TYPES", "VERDICT_STATUSES",
    "VERIFIER_ROLES", "PATCH_OPS", "TICKET_NAMESPACE",
    "EvidenceSpan", "Criterion", "CriterionVerdict", "ArtifactRevision",
    "RevisionLedger", "RepairTicket", "PatchOperation", "PatchSet", "PatchOutcome",
    "Rejection", "RepairDirective", "EvidenceResolution", "ClosureDecision",
    "RESOLUTION_STATUSES",
    "make_ticket_id", "target_location_key", "verdict_identity", "criteria_versions",
    "assert_criteria_unchanged", "validate_ticket", "legacy_finding_to_ticket",
    "assert_verdict_matches_ticket", "normalize_verdict_content", "ticket_verdict_content",
]
