"""返修反馈与关单核验（§5.2）。**新包内的 `feedback.py`**，与顶层旧 `blocks/audit/feedback.py` 不是同一份。

## 这一层治的两个病（调研 §1.2 的原话）

**病一：教材依据被"要求"挤掉了。**

> 转成 Finding 时 evidence 被 required_condition 占用，原教材依据未保留

旧 `Finding` 只有六个格子，`evidence` 那一格装的是"要补什么"，于是"教材里哪句话支持这个
判断"根本没地方放。生成器收到的是「这句话有问题，改一改」。
本模块把两者**分成两格、永不互相覆盖**：

* `source_evidence` —— 教材依据（真实切片，附 `verified` 核对结果）；
* `required_condition` —— 旧链路那一格的原文，原样留档并写明来历，
  **绝不**冒充教材依据。

**病二：关闭判词的解释与证据被拒的原因都丢了。**

> 关闭核验 | 返回 closed/open、quote、why | 只判断是否关闭；why 及证据拒绝原因被丢掉

旧 `apply_verdicts` 把 `why` 截断到 160 字，而且没有任何字段承载"这条证据为什么不被接受"。
`build_repair_payload` 因此必须带：**完整的**前次 `verdict.why`、前次证据被拒的原因、
前次补丁被拒的信息、已通过／未通过的 criterion 列表。

## 上下文怎么裁剪

按问题裁剪当前正文（锚点附近的段落），但**全书目录／概念索引的引用与相关教材片段
一格都不裁**（§5.2：不回到缺上下文的孤立短提示）。裁剪决策写进
`current_text.crop`，让"为什么只给了这一段"可复核。

## 关单核验的硬规矩（§5.2）

1. `artifact_hash` 必须是**当前** revision 的（判词、以及判词引用的每个 span）；
2. `criteria_version` 必须是工单**冻结**的那一版 —— 直接复用
   `contracts.assert_verdict_matches_ticket`，**不另写一套版本检查**；
3. 全部 `required` criterion 都要有**独立的**有效证据（`method` 与 `verifier.role` 必须对得上，
   否则"独立核验"退化成"同一个人说了两遍"）；
4. **生成器说"已修好"不算**：`ticket.status == "closed"` 与 `verdict.status == "pass"`
   的自述都不是关单依据，只有证据是。

刻意**不**用"新引用与旧句不同"当判据：要允许"旧原句没改、但补了正确前提"真正解决问题的那种修法。
"""
from __future__ import annotations

from typing import Any

from ...errors import ContractError
from .contracts import (ArtifactRevision, ClosureDecision, Criterion, CriterionVerdict,
                        RepairTicket, assert_verdict_matches_ticket)

#: 责任 owner → 现有配置里的生成／核验角色（§5.2）。
#: **正文返修用 lesson 生成角色，judge 只做核验** —— 旧链路拿 judge 去改正文，
#: 是"责任分派错误"里最贵的一条。
OWNER_ROLES: dict[str, str] = {
    "lesson": "lesson",
    "interaction": "interaction",
    "visual_planner": "figures",
    "visual_renderer": "figures",
    "reviewer": "judge",
}

#: criterion.method → 能提供**独立**证据的 verifier 角色。
_METHOD_ROLE: dict[str, str] = {"rule": "rule", "math": "math", "reviewer": "judge",
                                "frame": "frame", "browser": "browser"}

#: 当前正文片段的裁剪参数。
_MAX_CONTEXT_CHARS = 4000
_CONTEXT_PAD = 600


def owner_role(owner: str) -> str:
    """责任 owner → 生成／核验角色。未知 owner 一律按 `lesson`（生成角色）。"""
    return OWNER_ROLES.get(str(owner or "").strip(), "lesson")


# --------------------------------------------------------------------------- #
# 上下文裁剪
# --------------------------------------------------------------------------- #
def _crop_context(text: str, anchor: str, *, max_chars: int = _MAX_CONTEXT_CHARS,
                  pad: int = _CONTEXT_PAD) -> tuple[str, dict]:
    """按问题裁剪当前正文片段；返回 `(片段, 裁剪说明)`。"""
    text = str(text or "")
    info: dict = {"cropped": False, "original_chars": len(text), "kept_chars": len(text),
                  "crop_reason": "正文不长，没有裁剪", "anchor": str(anchor or "")[:120],
                  "anchor_found": False}
    if len(text) <= max_chars:
        return text, info
    needle = str(anchor or "")
    position = text.find(needle) if needle else -1
    if position < 0:
        info["crop_reason"] = ("锚点在正文里找不到：给出整段正文，"
                              "**不**退回成缺上下文的孤立短提示")
        return text, info
    info["anchor_found"] = True
    start = max(0, position - pad)
    end = min(len(text), position + len(needle) + pad)
    head = text.rfind("\n\n", 0, start)
    start = 0 if head < 0 else head + 2
    tail = text.find("\n\n", end)
    end = len(text) if tail < 0 else tail
    if end - start > max_chars:
        start = max(0, min(start, position - max_chars // 2))
        end = min(len(text), start + max_chars)
    snippet = text[start:end]
    info.update({"cropped": True, "kept_chars": len(snippet),
                 "crop_reason": f"以锚点为中心取 {len(snippet)} 字（段落边界对齐）",
                 "span": [start, end]})
    return snippet, info


# --------------------------------------------------------------------------- #
# 教材依据 / 旧 required_condition
# --------------------------------------------------------------------------- #
def _source_text(source_context: dict) -> tuple[str, str]:
    """教材依据的正文来源。返回 `(文本, 出处说明)`。"""
    explicit = source_context.get("source_text")
    if isinstance(explicit, str) and explicit.strip():
        return explicit, "source_context.source_text"
    artifact = source_context.get("source_artifact")
    if isinstance(artifact, dict):
        for key in ("body", "text", "content", "body_markdown"):
            value = artifact.get(key)
            if isinstance(value, str) and value.strip():
                return value, f"source_context.source_artifact.{key}"
    return "", "（没有提供教材正文：依据只能标为未核对）"


def _evidence_rows(ticket: RepairTicket, source_context: dict) -> tuple[list[dict], list[dict]]:
    """展开工单的 `source_evidence`，并**在教材正文里核对**。

    核对不通过不是"悄悄丢掉这一行"，而是记进 `source_evidence_rejections`：
    依据被篡改／过期本身就是要让人看见的事实。
    """
    source_text, origin = _source_text(source_context)
    source_artifact = source_context.get("source_artifact")
    rows: list[dict] = []
    rejections: list[dict] = []
    for index, span in enumerate(ticket.source_evidence):
        row = dict(span.as_dict())
        row["index"] = index
        row["origin"] = origin
        verified: bool | None = None
        reason = ""
        if isinstance(source_artifact, dict):
            from .evidence import resolve_json_pointer
            found, value = resolve_json_pointer(source_artifact, span.json_pointer)
            declared = str(source_artifact.get("hash")
                           or source_artifact.get("artifact_hash") or "")
            if not found or not isinstance(value, str):
                verified = False
                reason = f"教材 artifact 里 {span.json_pointer!r} 不是字符串"
            elif declared and declared != str(span.artifact_hash):
                verified = False
                reason = (f"依据绑定的教材版本 {str(span.artifact_hash)[:24]}… 与当前教材 "
                          f"{declared[:24]}… 不一致")
            else:
                actual = value[span.start:span.end]
                verified = actual == span.quote
                reason = "" if verified else (
                    f"依据声明的切片 {span.quote[:24]!r}… 与教材真实切片 {actual[:24]!r}… 不符")
        elif source_text:
            actual = source_text[span.start:span.end]
            verified = actual == span.quote
            reason = "" if verified else (
                f"依据声明的切片 {span.quote[:24]!r}… 与教材真实切片 {actual[:24]!r}… 不符")
        row["verified"] = verified
        if verified is False:
            row["rejection_reason"] = reason
            rejections.append({"index": index, "json_pointer": span.json_pointer,
                               "quote": span.quote, "reason": reason})
        rows.append(row)
    return rows, rejections


# --------------------------------------------------------------------------- #
# 前次失败（why / 证据被拒 / 补丁被拒）
# --------------------------------------------------------------------------- #
def _previous_failure(ticket: RepairTicket) -> dict:
    """把工单里的前次失败信息**完整**展开（不截断、不丢字段）。"""
    attempt = ticket.previous_attempt if isinstance(ticket.previous_attempt, dict) else {}

    def pick(*keys: str) -> Any:
        for key in keys:
            if key in attempt and attempt[key] not in (None, "", [], {}):
                return attempt[key]
        return None

    verdict = pick("verdict", "last_verdict", "verdicts")
    verdict_why = ""
    if isinstance(verdict, dict):
        verdict_why = str(verdict.get("why") or "")
    elif isinstance(verdict, list) and verdict and isinstance(verdict[0], dict):
        verdict_why = str(verdict[0].get("why") or "")
    verdict_why = str(pick("verdict_why", "why") or verdict_why or "")

    evidence_rejection = pick("evidence_rejection", "evidence_rejections", "evidence_rejected",
                              "evidence_rejection_reason", "locate_evidence")
    patch_rejection = pick("patch_rejection", "patch_rejected", "patch_outcome", "patch")
    return {
        "attempt_number": int(attempt.get("attempt") or attempt.get("round") or 0),
        "verdict_why": verdict_why,
        "verdict_why_chars": len(verdict_why),
        "evidence_rejection": evidence_rejection,
        "evidence_rejection_reason": (
            str(evidence_rejection.get("reason")) if isinstance(evidence_rejection, dict)
            else str(evidence_rejection or "")),
        "patch_rejection": patch_rejection,
        "patch_rejection_reason": (
            str(patch_rejection.get("reason")) if isinstance(patch_rejection, dict)
            else str(patch_rejection or "")),
        "raw": dict(attempt),
        "note": ("" if attempt else
                 "工单里没有 previous_attempt：这是第一次派发，没有前次失败可传"),
    }


def _criteria_view(ticket: RepairTicket, source_context: dict) -> dict:
    """逐条 criterion + 已通过／未通过／缺证据的划分。"""
    attempt = ticket.previous_attempt if isinstance(ticket.previous_attempt, dict) else {}
    raw = (source_context.get("verdicts") or source_context.get("criterion_verdicts")
           or attempt.get("verdicts") or ())
    status_by_criterion: dict[str, str] = {}
    for item in raw or ():
        if isinstance(item, CriterionVerdict):
            status_by_criterion[item.criterion_id] = item.status
        elif isinstance(item, dict) and item.get("criterion_id"):
            status_by_criterion[str(item["criterion_id"])] = str(item.get("status") or "")
    all_rows: list[dict] = []
    passed: list[str] = []
    failed: list[str] = []
    missing: list[str] = []
    for criterion in ticket.criteria:
        row = criterion.as_dict()
        row["frozen_version"] = criterion.version
        row["status"] = status_by_criterion.get(criterion.criterion_id, "missing")
        all_rows.append(row)
        if row["status"] == "pass":
            passed.append(criterion.criterion_id)
        elif row["status"] == "fail":
            failed.append(criterion.criterion_id)
        else:
            missing.append(criterion.criterion_id)
    return {"all": all_rows, "passed": passed, "failed": failed, "missing": missing,
            "status_source": ("source_context.verdicts" if source_context.get("verdicts")
                              else ("ticket.previous_attempt.verdicts" if attempt.get("verdicts")
                                    else "（没有判词记录：全部按缺证据计）"))}


# --------------------------------------------------------------------------- #
# 冻结接口 1：给生成器的返修载荷
# --------------------------------------------------------------------------- #
def build_repair_payload(ticket: RepairTicket, revision: ArtifactRevision,
                         source_context: dict) -> dict:
    """把工单装配成**实际发给责任生成器**的 JSON 载荷（§5.2 冻结接口）。

    返回普通 JSON（`json.dumps` 可直接序列化），因此可以被 spy transport 抓下来断言
    "provider 真的收到了来源、逐条 criterion、前次 why、拒绝原因"。
    """
    source_context = dict(source_context or {})
    target = dict(ticket.target or {})
    pointer = str(target.get("json_pointer") or "")
    anchor = str(target.get("location") or "")

    current_text = source_context.get("current_text")
    text_origin = "source_context.current_text"
    if not isinstance(current_text, str) or not current_text:
        artifact = source_context.get("artifact")
        current_text = ""
        text_origin = "（没有提供当前正文：调用方必须显式给出 current_text 或 artifact）"
        if isinstance(artifact, dict):
            from .evidence import resolve_json_pointer
            found, value = resolve_json_pointer(artifact, pointer)
            if found and isinstance(value, str):
                current_text, text_origin = value, f"source_context.artifact{pointer}"

    snippet, crop = _crop_context(current_text, anchor)
    evidence_rows, evidence_rejections = _evidence_rows(ticket, source_context)
    previous = _previous_failure(ticket)
    criteria = _criteria_view(ticket, source_context)

    attempt = ticket.previous_attempt if isinstance(ticket.previous_attempt, dict) else {}
    curriculum = {
        "toc": source_context.get("toc") or [],
        "concept_index": source_context.get("concept_index") or [],
        "related_units": source_context.get("related_units") or [],
        "source_excerpts": (source_context.get("source_excerpts")
                            or source_context.get("source_segments") or []),
        "note": "全书目录／概念索引／相关教材片段在按问题裁剪时**一格都不裁**："
                "缺上下文的孤立短提示正是本轮要治的毛病",
    }

    return {
        "kind": "repair_payload",
        "schema_version": ticket.schema_version,
        "ticket_id": ticket.ticket_id,
        "unit_id": ticket.unit_id,
        "ticket_kind": ticket.kind,
        "severity": ticket.severity,
        "hard_acceptance": bool(ticket.hard_acceptance),
        "owner": ticket.owner,
        "owner_role": owner_role(ticket.owner),
        "round": int(attempt.get("attempt") or attempt.get("round") or 0) + 1,
        # ── 教材依据与"要求"是**两格**，不许互相覆盖 ──────────────────────────
        "source_evidence": evidence_rows,
        "source_evidence_rejections": evidence_rejections,
        "required_condition": ticket.legacy_required_condition,
        "required_condition_origin": (
            "旧链路的 `Finding.evidence` 那一格原本装的是『要补什么』（required_condition），"
            "不是教材依据；这里原样留档，**不**冒充 source_evidence"
            if ticket.legacy_required_condition else ""),
        "repair_instructions": dict(ticket.repair_instructions or {}),
        "allowed_paths": list(ticket.allowed_paths),
        "preserve": list(ticket.preserve),
        "criteria": criteria,
        "current_text": {
            "artifact_id": revision.artifact_id,
            "artifact_hash": revision.hash,
            "revision": revision.revision,
            "json_pointer": pointer,
            "origin": text_origin,
            "text": snippet,
            "crop": crop,
        },
        "previous_failure": previous,
        "curriculum_context": curriculum,
        "response_contract": {
            "format": "JSON",
            "shape": {"replacements": [{"issue": "工单里的第几条",
                                        "old": "当前正文里的原文（逐字）",
                                        "new": "改后的文字"}]},
            "rules": [
                "`old` 必须逐字来自上面给你的当前正文片段，且只出现一次；",
                "只改被点到的内容，`preserve` 列出的内容一个字都不许动；",
                "`allowed_paths` 之外的路径不许出现；",
                "逐条对照 `criteria[].expected` 自检：达不到就写清做不到的依据交复核，"
                "不要用『差不多』糊过去。",
            ],
        },
        "history": [dict(item) for item in ticket.history],
        "usage_refs": list(ticket.usage_refs),
    }


# --------------------------------------------------------------------------- #
# 冻结接口 2：关单核验
# --------------------------------------------------------------------------- #
class _BoundTicket:
    """喂给 `assert_verdict_matches_ticket` 的最小工单视图。

    存在的理由只有一个：**复用契约里那一套版本判据**（criterion 在不在工单里、
    `criteria_version` 是否冻结、`expected` 是否可核验、artifact_hash 对不对），
    而不是在关单逻辑里再写一遍 —— 两套版本检查迟早不一致。
    """

    def __init__(self, ticket: RepairTicket, criterion: Criterion | None = None) -> None:
        self.ticket_id = ticket.ticket_id
        self.criteria = (criterion,) if criterion is not None else tuple(ticket.criteria)
        self.target = dict(ticket.target or {})


def _independence_hint(criterion: Criterion, verdict: CriterionVerdict) -> str:
    """这条判词为什么**不能**算作该 criterion 的独立有效证据（空串 = 可以）。"""
    method = str(getattr(criterion, "method", ""))
    expected_role = _METHOD_ROLE.get(method, "")
    role = str(verdict.verifier.get("role") or "") if verdict.verifier else ""
    if not role:
        return (f"判词没有记录 verifier 角色 —— 无法证明 criterion "
                f"{criterion.criterion_id}（method={method}）是被**独立**核验的")
    if expected_role and role != expected_role:
        return (f"criterion {criterion.criterion_id} 的 method={method} 需要 {expected_role!r} "
                f"角色的独立证据，这条判词来自 {role!r} —— 不能拿同一方的说法自证")
    if not verdict.spans and not verdict.depends_on_hashes:
        return f"判词 {verdict.verdict_id} 没有任何真实 span 或独立依赖哈希"
    return ""


def evaluate_closure(ticket: RepairTicket, revision: ArtifactRevision,
                     verdicts: list[CriterionVerdict]) -> ClosureDecision:
    """能不能关单（§5.2 冻结接口）。

    关单门槛 = 正确的 `artifact_hash` + 冻结的 `criteria_version` + 全部 `required`
    criterion 的**独立**有效证据。`ticket.status` 与 `verdict.status == "pass"`
    的自述都不算。
    """
    incoming = [item for item in (verdicts or ()) if isinstance(item, CriterionVerdict)]
    malformed = [item for item in (verdicts or ()) if not isinstance(item, CriterionVerdict)]
    required = [criterion for criterion in ticket.criteria if criterion.required]

    summary: list[dict] = [
        {"criterion_id": str(getattr(item, "criterion_id", "?")), "status": "malformed",
         "why": f"判词不是 CriterionVerdict（实际 {type(item).__name__}）—— 无法核验"}
        for item in malformed]
    failed: list[str] = []
    missing: list[str] = []
    detail: list[str] = []

    if not required:
        return ClosureDecision(
            closed=False,
            reason=(f"工单 {ticket.ticket_id} 没有 required criterion —— 没有可独立核验的"
                    f"通过标准就不能关单（缺标准不等于『没什么可验』）"),
            failed_criteria=(), missing_evidence=(), verdicts=tuple(summary))

    frozen = {criterion.criterion_id: criterion for criterion in ticket.criteria}
    for extra in incoming:
        if extra.criterion_id not in frozen:
            summary.append({"criterion_id": extra.criterion_id, "status": "unknown_criterion",
                            "verdict_id": extra.verdict_id,
                            "why": f"工单 {ticket.ticket_id} 里没有这条 criterion —— "
                                   f"新问题要新建工单，不许拿别的标准来关单"})
            if extra.criterion_id not in failed:
                failed.append(extra.criterion_id)

    for criterion in required:
        candidates = [item for item in incoming
                      if item.criterion_id == criterion.criterion_id]
        if not candidates:
            missing.append(criterion.criterion_id)
            summary.append({"criterion_id": criterion.criterion_id, "status": "missing",
                            "why": "这次核验没有给出这条 criterion 的判词"})
            continue
        satisfied = False
        saw_fail = False
        hints: list[str] = []
        for verdict in candidates:
            try:
                assert_verdict_matches_ticket(_BoundTicket(ticket, criterion), verdict)
            except ContractError as exc:
                hints.append(str(exc))
                summary.append({"criterion_id": criterion.criterion_id, "status": "invalid",
                                "verdict_id": verdict.verdict_id, "why": str(exc)})
                continue
            if str(verdict.artifact_hash) != str(revision.hash):
                hints.append(f"判词针对 artifact {str(verdict.artifact_hash)[:24]}…，"
                             f"当前 revision 是 {str(revision.hash)[:24]}…")
                summary.append({"criterion_id": criterion.criterion_id, "status": "stale_hash",
                                "verdict_id": verdict.verdict_id, "why": hints[-1]})
                continue
            stale_spans = [span for span in verdict.spans
                           if str(span.artifact_hash) != str(revision.hash)]
            if stale_spans:
                hints.append(f"判词引用的 span 绑定的是旧 hash "
                             f"{str(stale_spans[0].artifact_hash)[:24]}…")
                summary.append({"criterion_id": criterion.criterion_id, "status": "stale_hash",
                                "verdict_id": verdict.verdict_id, "why": hints[-1]})
                continue
            if verdict.status == "fail":
                saw_fail = True
                summary.append({"criterion_id": criterion.criterion_id, "status": "fail",
                                "verdict_id": verdict.verdict_id, "why": verdict.why})
                continue
            if verdict.status != "pass":
                hints.append(f"判词状态是 {verdict.status!r}（既不是 pass 也不是 fail）—— "
                             f"缺证据")
                summary.append({"criterion_id": criterion.criterion_id,
                                "status": verdict.status, "verdict_id": verdict.verdict_id,
                                "why": verdict.why})
                continue
            independence = _independence_hint(criterion, verdict)
            if independence:
                hints.append(independence)
                summary.append({"criterion_id": criterion.criterion_id,
                                "status": "not_independent",
                                "verdict_id": verdict.verdict_id, "why": independence})
                continue
            satisfied = True
            summary.append({"criterion_id": criterion.criterion_id, "status": "pass",
                            "verdict_id": verdict.verdict_id,
                            "spans": [span.as_dict() for span in verdict.spans],
                            "depends_on_hashes": list(verdict.depends_on_hashes),
                            "why": verdict.why})
        if satisfied and not saw_fail and len(candidates) == 1:
            continue
        if saw_fail or len(candidates) != 1:
            if criterion.criterion_id not in failed:
                failed.append(criterion.criterion_id)
        elif criterion.criterion_id not in missing:
            missing.append(criterion.criterion_id)
        if hints:
            summary.append({"criterion_id": criterion.criterion_id, "status": "rejected",
                            "why": "；".join(hints)})
            detail.append(f"{criterion.criterion_id}: " + "；".join(hints))

    closed = not failed and not missing and not malformed
    reasons: list[str] = []
    if closed:
        reasons.append(f"{len(required)} 条 required criterion 全部有独立有效证据"
                       f"（artifact {str(revision.hash)[:24]}…）")
    if failed:
        reasons.append(f"未通过：{failed}")
    if missing:
        reasons.append(f"缺有效证据：{missing}")
    if malformed:
        reasons.append(f"判词类型不合法：{len(malformed)} 条")
    if not closed and detail:
        # 把"为什么这条证据不算"写进理由：只说"缺证据"会让生成器下一轮再猜一次。
        reasons.append("阻塞原因：" + " | ".join(detail))
    reasons.append("生成器自报『已修好』不参与关单判断")
    return ClosureDecision(closed=closed, reason="；".join(reasons),
                           failed_criteria=tuple(failed),
                           missing_evidence=tuple(missing), verdicts=tuple(summary))


__all__ = ["OWNER_ROLES", "build_repair_payload", "evaluate_closure", "owner_role"]
