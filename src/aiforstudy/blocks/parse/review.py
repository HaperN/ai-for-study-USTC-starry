"""目录复核（review）：**按输入 + 决策指纹应用**，过期就拒绝。

方案 §6 的接通要求：

> 目录：extract 产出 source bundle，build **实际消费**；review **按输入+决策指纹应用**；
> recover 在结构之前完成并被**同趟消费**。

方案 §15 A08 把这条变成验收项：

> 改 review 内容但条数不变、改来源截图；首次生成 recover →
> **指纹失效且重新应用；本次 structure/body 已包含恢复结果；不可消费旧决策。**

这三句合起来说明：review 产物必须带**它是对哪一份输入做的判断**的指纹。
只记"复核结论"而不记"基于哪份源"，就会出现最坏的一种情况 ——
复核是三天前对旧截图做的，今天原文变了，程序**照样把旧决策当有效**，
而且没有任何地方能看出来。

所以 `DirectoryReview` 的构造会强制：输入指纹 + 决策 + 决策指纹 + 逐条依据。
`apply()` 在输入指纹对不上时**拒绝**，并说清是"哪一份输入变了"。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ...core.digest import digest_of
from ...core.identity import NeedsReview
from ...errors import ContractError

#: 复核可以做的裁决
#: ⚠️ 命名按**来源的名字**来（印刷目录/正文标题/PDF 书签），不叫笼统的 `accept-source` ——
#: 笼统的名字会让"到底采信了哪一份"从裁决记录里消失，而那正是复核要回答的唯一问题。
VERDICTS = ("accept-printed", "accept-body", "accept-bookmarks", "manual-title",
            "manual-drop", "manual-split", "manual-merge", "leave-pending")


@dataclass(frozen=True)
class ReviewDecision:
    """一条复核裁决。"""
    subject: str                 # 目录 toc_id 或 node_key
    verdict: str
    title: str = ""              # manual-title 时的最终标题
    reason: str = ""
    arbiter: str = "model"       # model | human
    evidence: str = ""           # 依据（截图/页码/书签）

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ContractError(f"复核裁决必须是 {VERDICTS} 之一，实际 {self.verdict!r}")
        if not self.subject.strip():
            raise ContractError("复核裁决必须指明对象（toc_id 或 node_key）")
        if self.verdict == "manual-title" and not self.title.strip():
            raise ContractError("manual-title 裁决必须给出最终标题")
        if self.verdict != "leave-pending" and not self.reason.strip():
            raise ContractError(
                f"裁决 {self.verdict!r} 必须写 reason —— 没有理由的裁决等于'我说了算'，"
                f"下次没人能复核这次复核")
        if self.arbiter == "human" and self.verdict == "leave-pending":
            raise ContractError("人工裁决不该留成 pending（那等于没裁决）")

    def as_dict(self) -> dict:
        return {"subject": self.subject, "verdict": self.verdict, "title": self.title,
                "reason": self.reason, "arbiter": self.arbiter, "evidence": self.evidence}


@dataclass(frozen=True)
class DirectoryReview:
    """一次目录复核的完整交代。"""
    inputs_digest: str                       # 它是对**哪一份输入**做的判断
    decisions: tuple[ReviewDecision, ...] = ()
    produced_by: str = ""
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if not str(self.inputs_digest).startswith("sha256:"):
            raise ContractError(
                f"复核产物必须带输入指纹，实际 {self.inputs_digest!r}；"
                f"方案 §6 要求'review 按输入+决策指纹应用' —— 只记结论不记依据，"
                f"就会拿三天前对旧截图做的判断套今天的原文")

    @property
    def decisions_digest(self) -> str:
        return digest_of([d.as_dict() for d in self.decisions])

    @property
    def is_empty(self) -> bool:
        return not self.decisions

    def as_dict(self) -> dict:
        return {"_meta": {"file_type": "toc_review", "schema_version": self.schema_version,
                          "generator": "aiforstudy.blocks.parse.review",
                          "field_notes": {
                              "inputs_digest": "这份复核是对哪一份输入（目录来源 bundle + 截图）做的；对不上就拒绝应用",
                              "decisions[].reason": "每条裁决必须写理由",
                              "decisions_digest": "决策指纹：内容变了但条数没变也能发现",
                          }},
                "inputs_digest": self.inputs_digest,
                "decisions_digest": self.decisions_digest,
                "produced_by": self.produced_by,
                "decisions": [d.as_dict() for d in self.decisions]}

    @classmethod
    def from_dict(cls, payload: dict) -> "DirectoryReview":
        return cls(inputs_digest=str(payload.get("inputs_digest") or ""),
                   decisions=tuple(ReviewDecision(**d) for d in payload.get("decisions") or ()),
                   produced_by=str(payload.get("produced_by") or ""),
                   schema_version=str(payload.get("schema_version") or "1.0"))


@dataclass
class ReviewApplication:
    """应用结果：哪些裁决被采信、哪些因为输入变了被拒、还剩下什么待裁决。"""
    applied: tuple[ReviewDecision, ...] = ()
    rejected: tuple[tuple[ReviewDecision, str], ...] = ()
    pending: tuple[NeedsReview, ...] = ()
    stale: bool = False
    reason: str = ""

    def as_dict(self) -> dict:
        return {"applied": [d.as_dict() for d in self.applied],
                "rejected": [{"decision": d.as_dict(), "why": why}
                             for d, why in self.rejected],
                "pending": [n.as_dict() for n in self.pending],
                "stale": self.stale, "reason": self.reason}


def inputs_digest(sources: dict) -> str:
    """复核输入的指纹。

    ⚠️ 只用**逻辑身份与内容**，不含路径、时间、run_id（方案 §11）。
    来源 bundle 里"条数不变但内容变了"的情况（A08 专门测这个）必须能区分出来，
    所以这里对每一项的来源指纹逐个入账，而不是只记一个条数。
    """
    from ...core.digest import digest_of as _d
    return _d(sources)


def expected_inputs_digest(parse_root: Path) -> str:
    """本轮复核**应当基于**的输入指纹。

    入账的东西就是复核真正看到的那些：PDF 内容、正文文本、用了哪些来源、
    以及被判断的那份目录草稿。方案 §15 A08 要求"改来源截图"能失效 ——
    截图由 PDF 页渲染而来，所以 `pdf_sha256` 变了它就会变；草稿变了同理。
    """
    from ...core.digest import digest_file
    parse_root = Path(parse_root)
    meta: dict = {}
    toc_path = parse_root / "toc.v2.json"
    if toc_path.is_file():
        try:
            meta = (json.loads(toc_path.read_text(encoding="utf-8")).get("_meta") or {})
        except ValueError:
            meta = {}
    draft = parse_root / "toc.draft.json"
    return inputs_digest({
        "pdf_sha256": meta.get("pdf_sha256"),
        "document_sha256": meta.get("document_sha256"),
        "sources_used": sorted(meta.get("sources_used") or []),
        "draft_digest": (digest_file(draft) if draft.is_file() else None),
    })


def load_review(parse_root: Path) -> DirectoryReview | None:
    path = Path(parse_root) / "toc.review.json"
    if not path.is_file():
        return None
    try:
        return DirectoryReview.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as exc:
        raise ContractError(f"{path} 不是合法 JSON：{exc}") from exc


def apply_review(review: DirectoryReview | None, *, expected_inputs_digest: str,
                 disagreements: list[dict] | None = None
                 ) -> ReviewApplication:
    """把复核裁决应用到本轮目录上。

    三个分支都要能走通，而且**各自的语义不同**：

    * 没有复核产物 → `stale=True`，理由写"缺 toc.review.json"，未被裁决的分歧进 pending。
      这正是本书的现状（实测：`toc.v2.json` 有 2 条分歧、`arbiter=pending`，
      而 `toc.review.json` 不存在）。
    * 有产物但输入指纹对不上 → `stale=True`，**所有裁决都不应用**（方案 §15 A08）。
    * 指纹一致 → 逐条应用；`leave-pending` 的留在 pending 里，不假装已裁决。
    """
    disagreements = list(disagreements or [])
    if review is None:
        pending = [NeedsReview(kind="missing-review",
                               subject=str(d.get("toc_id") or "?"),
                               why=f"没有 toc.review.json，这条分歧未被裁决："
                                   f"{d.get('why') or d.get('kind')}",
                               arbiter="pending",
                               evidence=str(d.get("resolution") or ""))
                   for d in disagreements]
        return ReviewApplication(stale=True, pending=tuple(pending),
                                 reason="缺 toc.review.json：本轮没有可消费的复核结论")

    if review.inputs_digest != expected_inputs_digest:
        return ReviewApplication(
            stale=True, reason=(
                f"复核产物基于另一份输入：产物 {review.inputs_digest} ≠ 本轮 "
                f"{expected_inputs_digest}。方案 §15 A08：**不可消费旧决策**。"),
            pending=tuple(NeedsReview(kind="missing-review", subject=str(d.get("toc_id") or "?"),
                                      why="复核结论已过期，本轮无有效裁决",
                                      arbiter="pending")
                          for d in disagreements))

    applied: list[ReviewDecision] = []
    rejected: list[tuple[ReviewDecision, str]] = []
    pending: list[NeedsReview] = []
    by_subject = {str(d.get("toc_id")): d for d in disagreements}
    for decision in review.decisions:
        if decision.verdict == "leave-pending":
            pending.append(NeedsReview(kind="toc-arbitration", subject=decision.subject,
                                       why=decision.reason or "复核留待后续裁决",
                                       arbiter="pending", evidence=decision.evidence))
            continue
        if decision.subject in by_subject or decision.verdict.startswith("manual"):
            applied.append(decision)
        else:
            rejected.append((decision, "本轮目录里没有这个对象（可能已被删/改名）"))
    return ReviewApplication(applied=tuple(applied), rejected=tuple(rejected),
                             pending=tuple(pending))


def review_needs(disagreements: list[dict], unconfirmed: list[dict]) -> tuple[NeedsReview, ...]:
    """**不需要复核产物**也能列出的待裁决项 —— 用于"复核还没做"的时候如实交代。

    本书实测：2 条目录标题分歧（`arbiter=pending`）+ 5 个未确认节点。
    这些都不会改变身份，但会阻止正式发布（方案 §9 的 `needs_review`）。
    """
    out: list[NeedsReview] = []
    for row in disagreements or []:
        if not isinstance(row, dict):
            continue
        out.append(NeedsReview(
            kind="toc-arbitration", subject=str(row.get("toc_id") or "?"),
            why=str(row.get("why") or row.get("kind") or "目录来源分歧"),
            arbiter=str(row.get("arbiter") or "pending"),
            evidence=str(row.get("resolution") or "")))
    for row in unconfirmed or []:
        if not isinstance(row, dict):
            continue
        out.append(NeedsReview(
            kind="unconfirmed-node", subject=str(row.get("toc_node") or "?"),
            why=str(row.get("why") or "未确认节点"), arbiter="pending"))
    return tuple(out)


__all__ = ["VERDICTS", "ReviewDecision", "DirectoryReview", "ReviewApplication",
           "inputs_digest", "expected_inputs_digest", "load_review", "apply_review",
           "review_needs"]


# ────────────────────────────────────────── 建议稿 → **可应用的复核**

def review_from_draft(draft: dict, parse_root: Path, *,
                      disagreements: list[dict] | None = None,
                      arbiter: str = "model",
                      produced_by: str = "") -> DirectoryReview:
    """把 `review_toc(...)` 的**建议稿**转成 `apply_review` 认的**复核**。

    ## 为什么必须有这一环（实测缺口）

    `review_toc` 产出的是"建议稿"（顶层键 `suspects`/`decisions`/`summary`），
    **`inputs_digest` 是 `None`**；而 `apply_review` 要求
    `review.inputs_digest == expected_inputs_digest(parse_root)`，否则一律按
    "不可消费旧决策"（§15 A08）拒绝。

    **没有任何代码在两者之间搭桥**，于是真实书上：那两条标题分歧**已经被带图的
    视觉复核裁决过了**（`conflict` + `chosen_source=body-heading-shapes`），
    结论却永远停在 run 目录里进不来 —— 每次运行都重报一遍"未裁决"，
    还被 `toc` 与 `export` **各报一次**（CH04 那 32 条里，22 条就是这么来的）。

    ## 匹配规则

    草稿按 `line` 记裁决、`toc_id` 是 `null`；分歧按 `toc_id`（`T-0020`）记。
    实测草稿的 `title` 正好是**分歧里 `body`（正文）那一边**的标题，`number`（`4.2`）
    也对得上。所以按"标题互相包含"匹配，并用 `number` 佐证；
    **匹配不上就报错，不静默丢** —— 悄悄丢一条裁决，读的人会以为"复核没提到它"。
    """
    from ...errors import ContractError
    rows = list(disagreements or [])
    expected = expected_inputs_digest(parse_root)

    def _strip(text: str) -> str:
        return re.sub(r"[\s　]+", "", str(text or ""))

    def _match(row: dict) -> dict | None:
        title = _strip(row.get("title"))
        number = _strip(row.get("number"))
        for item in rows:
            body = _strip(item.get("body"))
            printed = _strip(item.get("printed"))
            if title and (title in body or title in printed):
                if not number or number in body or number in printed:
                    return item
        return None

    decisions: list[ReviewDecision] = []
    for row in draft.get("decisions") or []:
        matched = _match(row)
        action = str(row.get("action") or "")
        title = str(row.get("title") or "")
        reason = str(row.get("relation") or row.get("resolution") or row.get("reason") or "")[:400]
        evidence = str(row.get("reason") or "")[:400]
        if matched is None:
            if action == "unsure":
                # 弃权：没有对应分歧时**不算错**（模型可以对"本轮没附截图"整体弃权）
                continue
            raise ContractError(
                f"建议稿里有一条裁决对不上本轮目录的分歧：action={action!r} "
                f"title={title!r} number={row.get('number')!r}。"
                f"本轮分歧：{[(d.get('toc_id'), d.get('title')) for d in rows]}。"
                f"方案 §11：不静默丢 —— 对不上就说出来，别让读的人以为复核没提到它。")
        subject = str(matched.get("toc_id") or matched.get("subject") or "?")
        if action == "conflict" and title:
            decisions.append(ReviewDecision(subject=subject, verdict="manual-title",
                                            title=title, reason=reason, arbiter=arbiter,
                                            evidence=evidence))
        elif action == "unsure":
            decisions.append(ReviewDecision(subject=subject, verdict="leave-pending",
                                            reason=reason or "复核弃权（缺依据）",
                                            arbiter=arbiter, evidence=evidence))
        else:
            decisions.append(ReviewDecision(subject=subject, verdict="leave-pending",
                                            reason=f"未识别的 action={action!r}，留待人工",
                                            arbiter=arbiter, evidence=evidence))
    return DirectoryReview(inputs_digest=expected, decisions=tuple(decisions),
                           produced_by=produced_by or "review_from_draft")
