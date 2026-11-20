"""工作台反馈的可判定口径（计划 §T10）。

T10 要求工作台能显示两类东西，且**都在界面上看得见**：

1. **事件**：`text_audit`、以及媒体生命周期的"规划／draft／检查／final"。
2. **失败卡**：每张卡必须回答八件事 ——
   卡点／方法／标准／责任方／当前版本／前次失败／已耗轮次／停下原因。

这个模块是这两件事的**唯一一份口径**：

* 事件 → 阶段标签与媒体阶段（`stage_label` / `media_phase`）。界面不再自己写一份映射
  （两处映射一定会漂移，界面上就会出现一个阶段没有中文名）。
* 盘上的工单 + 返修账 → 失败卡（`collect_failure_cards`）。数据来源是**已经落盘的产物**：
  `work/products/text_audit/<unit>.json`、`work/products/audit/<unit>.json` 里的 `tickets`，
  以及 `state.json` 的 `repair_states`（`pipeline/repair.py::UnitRepairState` 是"轮次"的真相）。
  这里**不重新发明**工单或轮次，只把它摊平成界面能直接渲染的一行。

## 为什么"停下原因"要单独找

`content_rounds_used` 只说明"用了几轮"，不说明"为什么不再往下走"。停下来的理由在
`UnitRepairState.stopped_reason`（单元素）或 `StopDecision`（`reason`/`next_action`/`stopped_by`）。
把两者混成一个字段，界面就只能显示"已用 2 轮"，读者无法判断是"还没修完"还是"额度用尽"。

## 不做什么

不调用模型、不调度返修、不写盘。缺文件时返回空列表（**不是**"通过"）——
界面上宁可没有卡，也不要一张编出来的卡。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..core.jsonio import read_json_soft

#: 失败卡必须回答的八件事（计划 §T10 逐字对应的字段名）。
CARD_FIELDS = ("blocker", "method", "criterion", "owner", "current_version",
               "previous_failure", "attempts", "stop_reason")

#: 八件事的中文名（界面与测试共用一份，避免"卡上少一格"这种静默缺失）。
CARD_LABELS = {
    "blocker": "卡点",
    "method": "方法",
    "criterion": "标准",
    "owner": "责任方",
    "current_version": "当前版本",
    "previous_failure": "前次失败",
    "attempts": "已耗轮次",
    "stop_reason": "停下原因",
}

#: 阶段名 → （界面标签，媒体阶段）。媒体阶段只有 planning／draft／check／final 四个值，
#: 其余阶段是 `""`（不是媒体）。
STAGE_LABELS: dict[str, tuple[str, str]] = {
    "mineru": ("教材解析", ""),
    "toc": ("目录", ""),
    "structure": ("结构", ""),
    "export": ("目录导出", ""),
    "body": ("正文抽取", ""),
    "concepts": ("概念表", ""),
    "lesson": ("内容生成", ""),
    "text_audit": ("内容审核（text_audit）", ""),
    "media_plan": ("媒体需求（media_plan）", "planning"),
    "audit": ("媒体验收（audit）", "check"),
    "figures": ("媒体生成（figures）", "draft"),
    "interaction": ("交互装配", ""),
    "render": ("组合与浏览器验收", "final"),
    "delivery": ("交付", ""),
}

#: 媒体阶段的中文名（界面分组标题）。
MEDIA_PHASES = {"planning": "媒体规划", "draft": "媒体 draft",
                "check": "媒体检查", "final": "媒体 final"}

#: 渲染档位写在事件 data 里的可能键名。
_PROFILE_KEYS = ("render_profile", "profile", "档位")


def stage_label(stage: str) -> str:
    """阶段的中文标签；未知阶段**原样回显**，不编一个像样的名字。"""
    name = str(stage or "")
    return STAGE_LABELS.get(name, (name or "（未知阶段）", ""))[0]


def media_phase(stage: str, data: dict | None = None, message: str = "") -> str:
    """这个事件属于媒体的哪个阶段：planning／draft／check／final／`""`。

    判据顺序（先具体后笼统）：

    1. `media_plan` → planning（媒体需求），`figures` → draft（媒体生成）；
    2. `render` + 事件里写明 draft 档 → draft；
    3. `render` + final 档（或没写档位）→ final；
    4. `audit` + 消息/数据里出现"画面／关键帧／frame" → check；
    5. 其余 → `""`。
    """
    name = str(stage or "")
    payload = dict(data or {})
    haystack = " ".join([str(payload.get(key) or "") for key in _PROFILE_KEYS]
                        + [str(message or ""), str(payload.get("check") or "")])
    if name == "media_plan":
        return "planning"
    if name == "figures":
        return "draft"
    if name == "render":
        return "draft" if "draft" in haystack.lower() else "final"
    if name == "audit":
        return "check"
    return ""


def build_timeline(events: Iterable[Any]) -> list[dict]:
    """把事件流摊平成界面要用的时间线（label／media_phase／具体说明）。

    `events` 是 `pipeline.events.Event` 或它们的 `as_dict()`；两种都收。
    返回的行**保留原始字段**（`kind`/`stage`/`seq`/`message`），只在上面加
    `label` 与 `media_phase` —— 界面要能点回原始事件去核对。
    """
    rows: list[dict] = []
    for event in events:
        payload = event.as_dict() if hasattr(event, "as_dict") else dict(event or {})
        data = payload.get("data") or {}
        phase = media_phase(payload.get("stage"), data, payload.get("message"))
        rows.append({
            "seq": payload.get("seq"),
            "at": payload.get("at"),
            "kind": payload.get("kind") or "",
            "stage": payload.get("stage") or "",
            "node_id": payload.get("node_id") or "",
            "scope_key": payload.get("scope_key") or "",
            "message": payload.get("message") or "",
            "label": stage_label(payload.get("stage")),
            "media_phase": phase,
            "media_label": MEDIA_PHASES.get(phase, ""),
        })
    return rows


def _tickets_of(store_root: Path, unit_id: str) -> list[dict]:
    """合并本 run 的各阶段工单，以工单身份和产物版本保留审核结论。

    正文审核和媒体验收优先于下游保存的旧发现。活动 tickets 优先于同阶段
    冻结历史；冻结 closed 票仅用于压住同一版本的旧 open 副本。缺身份或
    版本的坏票不能证明彼此相同，逐条保留供界面诊断缺项。
    """
    root = Path(store_root) / "work" / "products"
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for stage in ("text_audit", "audit", "render", "interaction", "media_plan", "figures"):
        payload = (read_json_soft(root / stage / f"{unit_id}.json") or {}).get("payload") or {}
        tickets = [t for t in (payload.get("tickets") or []) if isinstance(t, dict)]
        tickets += [t for t in (payload.get("frozen_tickets") or [])
                    if isinstance(t, dict) and t.get("status") == "closed"]
        for ticket in tickets:
            target = ticket.get("target")
            target = target if isinstance(target, dict) else {}
            identity = str(ticket.get("ticket_id") or "").strip()
            version = str(target.get("artifact_hash") or "").strip()
            if identity and version:
                key = (identity, version)
                if key in seen:
                    continue
                seen.add(key)
            merged.append(ticket)
    return merged


def _criterion_text(ticket: dict) -> str:
    rows = []
    for criterion in ticket.get("criteria") or []:
        if not isinstance(criterion, dict):
            continue
        expected = str(criterion.get("expected") or "").strip()
        method = str(criterion.get("method") or "").strip()
        version = criterion.get("version")
        piece = expected or "（缺 expected）"
        if method:
            piece += f"（验证方式 {method}"
            piece += f"，判据版本 v{version}）" if version is not None else "）"
        rows.append(piece)
    return "；".join(rows)


def _method_text(ticket: dict) -> str:
    """方法 = **怎么改**（repair_instructions）+ **怎么验**（criteria[].method）。

    两者缺一，"方法"这一格就答不完整：只说怎么改会没法定论，只说怎么验会没法动手。
    """
    instructions = ticket.get("repair_instructions") or {}
    how = str(instructions.get("action") or "").strip()
    where = str(instructions.get("locate") or "").strip()
    methods = sorted({str(c.get("method") or "") for c in (ticket.get("criteria") or [])
                      if isinstance(c, dict) and str(c.get("method") or "").strip()})
    pieces = []
    if how:
        pieces.append(f"改法：{how}" + (f"（定位：{where}）" if where else ""))
    if methods:
        pieces.append("验证方式：" + "、".join(methods))
    return "；".join(pieces)


def _previous_failure_text(ticket: dict) -> str:
    previous = ticket.get("previous_attempt")
    if not isinstance(previous, dict) or not previous:
        return "（无前次失败：这是第一次派发）"
    parts = []
    for key, label in (("why", "前次为什么失败"), ("evidence_rejected", "被拒的证据"),
                       ("patch_rejected", "被拒的补丁"), ("round", "轮次"),
                       ("verdict", "上次判词"), ("note", "说明")):
        value = previous.get(key)
        if value in (None, "", [], {}):
            continue
        parts.append(f"{label}：{value if not isinstance(value, (dict, list)) else _brief(value)}")
    return "；".join(parts) or _brief(previous)


def _brief(value: Any, limit: int = 160) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _attempts_text(state: dict, unit_id: str) -> tuple[str, dict]:
    """已耗轮次：**内容返修轮次 + 每条判据的证据修复次数**，并带上限。"""
    repair = (state.get("repair_states") or {}).get(unit_id) or {}
    used = int(repair.get("content_rounds_used") or 0)
    evidence = repair.get("evidence_attempts_by_verdict") or {}
    limits = {}
    policy = state.get("attempt_limits") or {}
    if isinstance(policy, dict):
        limits = policy
    detail = {"content_rounds_used": used,
              "content_rounds_limit": limits.get("content"),
              "evidence_attempts": dict(sorted(evidence.items())),
              "evidence_attempts_limit": limits.get("evidence"),
              "history_rows": len(repair.get("history") or [])}
    parts = [f"内容返修 {used} 轮"
             + (f"／上限 {limits['content']}" if limits.get("content") is not None else "")]
    if evidence:
        worst = max(int(v) for v in evidence.values())
        parts.append(f"判词证据修复最多 {worst} 次"
                     + (f"／上限 {limits['evidence']}" if limits.get("evidence") is not None else ""))
    if repair.get("active_batch_id"):
        parts.append(f"进行中的批次 {repair['active_batch_id']}")
    return "；".join(parts), detail


def _stop_reason_text(state: dict, unit_id: str) -> str:
    """停下原因：单元级 `stopped_reason` → 运行级 `stop_reason`／`budget.stop` 事件。"""
    repair = (state.get("repair_states") or {}).get(unit_id) or {}
    unit_reason = str(repair.get("stopped_reason") or "").strip()
    if unit_reason:
        return unit_reason
    for row in reversed(repair.get("history") or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("decision") or "") == "stop" or row.get("reason"):
            reason = str(row.get("reason") or "").strip()
            next_action = str(row.get("next_action") or "").strip()
            stopped_by = str(row.get("stopped_by") or "").strip()
            if reason:
                return reason + (f"（下一步：{next_action}）" if next_action else "") \
                    + (f"｜触发者 {stopped_by}" if stopped_by else "")
    return str(state.get("stop_reason") or "").strip()


def collect_failure_cards(*, store_root: str | Path, state: dict) -> list[dict]:
    """把盘上的工单 + 运行状态摊平成失败卡。

    每个单元一张卡；卡里 `fields` 是八件事的**中文标签 → 值**，`missing` 是**没答上的**
    字段名列表 —— 界面上"少一格"要看得见，而不是悄悄空着。
    """
    root = Path(store_root)
    units: list[str] = []
    for key in sorted((state.get("repair_states") or {})):
        if str(key).strip():
            units.append(str(key))
    for node in (state.get("nodes") or {}).values():
        scope = str((node or {}).get("scope_key") or "")
        if not scope or scope in units:
            continue
        if (node or {}).get("quality_state") in {"needs_review", "rejected"} \
                or (node or {}).get("execution_state") in {"failed", "blocked"}:
            units.append(scope)
    cards: list[dict] = []
    for unit in units:
        tickets = _tickets_of(root, unit)
        open_tickets = [t for t in tickets if str(t.get("status") or "") != "closed"]
        attempts_text, attempts_detail = _attempts_text(state, unit)
        stop_text = _stop_reason_text(state, unit)
        if not open_tickets and not stop_text:
            continue
        rows = open_tickets or [{}]
        for ticket in rows:
            target = ticket.get("target")
            target = target if isinstance(target, dict) else {}
            artifact_hash = str(target.get("artifact_hash") or "")
            heads = ((state.get("repair_states") or {}).get(unit) or {}).get("artifact_heads") or {}
            current = artifact_hash or str(heads.get(str(target.get("artifact_id") or "")) or "")
            if not current and heads:
                current = "；".join(f"{k}={v}" for k, v in sorted(heads.items()))
            fields = {
                "blocker": str(ticket.get("problem") or "").strip()
                           or (f"{unit}：运行在 {state.get('status')} 停下" if not ticket else ""),
                "method": _method_text(ticket),
                "criterion": _criterion_text(ticket),
                "owner": str(ticket.get("owner") or ""),
                "current_version": (f"{target.get('artifact_id') or '（未指明产物）'} @ {current}"
                                    if current else str(target.get("artifact_id") or "")),
                "previous_failure": _previous_failure_text(ticket),
                "attempts": attempts_text,
                "stop_reason": stop_text or "（运行未停止：仍在自动返修或等待下一轮）",
            }
            missing = [name for name in CARD_FIELDS
                       if not str(fields.get(name) or "").strip()
                       or str(fields[name]).startswith("（缺")
                       or str(fields[name]).startswith("（未")]
            cards.append({
                "unit_id": unit,
                "ticket_id": str(ticket.get("ticket_id") or ""),
                "kind": str(ticket.get("kind") or ""),
                "severity": str(ticket.get("severity") or ""),
                "status": str(ticket.get("status") or ""),
                "fields": fields,
                "labels": dict(CARD_LABELS),
                "missing": missing,
                "attempts_detail": attempts_detail,
                "complete": not missing,
            })
    return cards


__all__ = ["CARD_FIELDS", "CARD_LABELS", "MEDIA_PHASES", "STAGE_LABELS",
           "collect_failure_cards", "build_timeline", "media_phase", "stage_label"]
