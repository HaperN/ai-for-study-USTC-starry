"""预览（M5，P4 定案）：**人只看一眼**的那一屏。

## 边界（P3/P4 写死的）

* 人**只说两件事**：走下去 / 停。**不能改内容** —— 不做 `toc.override.json`，
  不给人改目录的入口（用户原话："p3 不要得自动化"）。
* 所以预览里**没有一个可编辑字段**，只有"条数、样张、不确定项、花销、下一步"。

## `verdict` 只有三档（判据是确定的，不看心情）

| verdict | 什么时候 |
|---|---|
| `不对劲` | `confidence: low`、或有待裁决冲突、或一条样张都拿不出来 |
| `有几处要你看` | `unconfirmed` / `suspicious` / `unresolved` 非空 |
| `看起来对` | **`uncertain` 为空**才敢说（这是 M5 的验收判据：不能让"不确定为空"和"没检查"看起来一样） |
"""
from __future__ import annotations

from .contract import GENERATOR, SCHEMA_VERSION, sha256_text
from .contract import write_json  # noqa: F401  （外部按需引用）

VERDICT_BAD = "不对劲"
VERDICT_CHECK = "有几处要你看"
VERDICT_OK = "看起来对"

SAMPLE_LIMIT = 12


def preview(toc: dict, *, review: dict | None = None,
            cost: dict | None = None) -> dict:
    """`toc.json`(v2) [+ `toc.review.json`] → `toc.preview.json`。"""
    nodes = toc.get("nodes") or []
    meta = toc.get("_meta") or {}

    uncertain = _uncertain(toc, review)
    shape = {
        "chapters": sum(1 for n in nodes if n.get("level") == 1
                        and n.get("kind") != "non-teaching"),
        "sections": sum(1 for n in nodes if int(n.get("level") or 0) >= 2
                        and n.get("kind") != "non-teaching"),
        "non_teaching": sum(1 for n in nodes if n.get("kind") == "non-teaching"),
        "levels": sorted({int(n.get("level") or 0) for n in nodes}),
        "nodes": len(nodes),
    }
    samples = [{"toc_id": n["toc_id"],
                "line": _sample_line(n),
                "page": (n.get("page") or {}).get("printed")
                        or (n.get("page") or {}).get("pdf"),
                "confirmed": bool(n.get("confirmed", True)),
                "sources": (n.get("evidence") or {}).get("from") or []}
               for n in nodes[:SAMPLE_LIMIT]]

    verdict = _verdict(meta, uncertain, samples)
    return {
        "_meta": {"file_type": "toc_preview", "schema_version": SCHEMA_VERSION,
                  "generator": GENERATOR + ".preview",
                  "toc_sha256": sha256_text(_canonical(toc)),
                  "toc_confidence": meta.get("confidence"),
                  "slug": meta.get("slug", "")},
        "verdict": verdict,
        "shape": shape,
        "samples": samples,
        "uncertain": uncertain,
        "ledger": _ledger(toc),
        "cost": cost or _cost_from(review, toc),
        "next": _next(verdict),
    }


def _canonical(payload: dict) -> str:
    """算预览的输入指纹用的规范串：**只认目录内容**，不认字段顺序以外的杂质。"""
    import json
    body = {k: v for k, v in payload.items() if k != "_meta"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


def _sample_line(node: dict) -> str:
    indent = "  " * (int(node.get("level") or 1) - 1)
    number = str(node.get("number") or "").strip()
    title = str(node.get("title") or "").strip()
    text = f"{number} {title}".strip() if number else title
    mark = "" if node.get("confirmed", True) else "  ⚠️未确认"
    tag = "  [非正文]" if node.get("kind") == "non-teaching" else ""
    return f"{indent}{text}{tag}{mark}"


def _uncertain(toc: dict, review: dict | None) -> list[dict]:
    """把所有"不确定"汇成一串（**逐条**，带原因）。这是预览里最值钱的部分。"""
    out: list[dict] = []
    decisions = {}
    if review:
        for row in review.get("decisions") or []:
            if row.get("toc_id"):
                decisions[row["toc_id"]] = row
    for node in toc.get("nodes") or []:
        if node.get("confirmed", True):
            continue
        decision = decisions.get(node["toc_id"]) or {}
        out.append({
            "toc_id": node["toc_id"], "title": node.get("title"),
            "why": "这一条没被确认（只有一个源、或落不了页）",
            "model_says": decision.get("resolution") or decision.get("reason") or None,
            "confidence": "low",
        })
    for row in toc.get("suspicious") or []:
        decision = decisions.get(row.get("toc_id")) or {}
        out.append({"toc_id": row.get("toc_id"), "title": row.get("title"),
                    "why": row.get("why"),
                    "model_says": decision.get("resolution") or None,
                    "confidence": decision.get("confidence") or "low"})
    for row in toc.get("unresolved") or []:
        out.append({"toc_id": None, "title": row.get("raw") or "（整本）",
                    "why": row.get("why"), "model_says": None, "confidence": "low"})
    for row in toc.get("disagreements") or []:
        if row.get("arbiter") == "pending":
            out.append({"toc_id": row.get("toc_id"), "title": row.get("title"),
                        "why": row.get("why") or "源间冲突，等复核裁决",
                        "model_says": None, "confidence": "low"})
    return out


def _ledger(toc: dict) -> dict:
    """账本摘要：吃了什么 / 产出什么 / 丢了什么（设计稿 §8 的四问）。"""
    meta = toc.get("_meta") or {}
    counts = meta.get("counts") or {}
    return {
        "我吃了什么": {
            "pdf_sha256": (meta.get("pdf_sha256") or "")[:12],
            "document_sha256": (meta.get("document_sha256") or "")[:12],
            "sources_used": meta.get("sources_used") or [],
        },
        "我产出了什么": counts,
        "我丢了什么/没配上什么": {
            "disagreements": len(toc.get("disagreements") or []),
            "unresolved": len(toc.get("unresolved") or []),
            "suspicious": len(toc.get("suspicious") or []),
            "unconfirmed": counts.get("unconfirmed", 0),
        },
        "我什么时候能信": {
            "confidence": meta.get("confidence"),
            "重跑条件": "pdf_sha256 或 document_sha256 变了就必须重跑这一层",
        },
    }


def _cost_from(review: dict | None, toc: dict) -> dict:
    if review:
        meta = review.get("_meta") or {}
        return {"model_calls": meta.get("model_calls", 0),
                "images": meta.get("images", 0),
                "chunks": review.get("chunks", 0),
                "tokens": meta.get("tokens", 0),
                "downgraded": meta.get("downgraded", False)}
    return {"model_calls": 0, "images": 0, "chunks": 0, "tokens": 0,
            "downgraded": False, "note": "还没跑模型视觉复核（review）"}


def _verdict(meta: dict, uncertain: list[dict], samples: list[dict]) -> str:
    if str(meta.get("confidence")) == "low" or not samples:
        return VERDICT_BAD
    if uncertain:
        return VERDICT_CHECK
    return VERDICT_OK


def _next(verdict: str) -> str:
    if verdict == VERDICT_BAD:
        return "目录层自己说不可信：先看 uncertain 清单，决定是重跑目录层还是换一份 PDF"
    if verdict == VERDICT_CHECK:
        return "确认这些条目能接受 → 跑正文解析（body）；不能接受 → 停在这里"
    return "看起来对：跑正文解析（body）"


def render_text(payload: dict) -> str:
    """一屏能看懂（M5 的验收判据）。"""
    shape = payload["shape"]
    lines = [f"=== 目录预览：{payload['_meta'].get('slug','')} "
             f"（{payload['verdict']}）==="]
    lines.append(f"  规模：{shape['chapters']} 章 / {shape['sections']} 节 / "
                 f"非正文 {shape['non_teaching']} 条，管到层级 {shape['levels']}")
    lines.append(f"  置信度：{payload['_meta'].get('toc_confidence')}"
                 f"   样张 {len(payload['samples'])} 条：")
    for row in payload["samples"][:SAMPLE_LIMIT]:
        page = f"  P{row['page']}" if row.get("page") else "  P?"
        src = ",".join(s.split("-")[0] for s in row.get("sources") or [])
        lines.append(f"    {row['line']}{page}  [{src}]")
    uncertain = payload["uncertain"]
    lines.append(f"  不确定 {len(uncertain)} 条："
                 + ("（空 —— 所以 verdict 才敢说「看起来对」）" if not uncertain else ""))
    for row in uncertain[:12]:
        said = f" ｜ 模型说：{row['model_says']}" if row.get("model_says") else ""
        lines.append(f"    ⚠️ {row['toc_id'] or '（整本）'} {str(row['title'])[:30]}："
                     f"{row['why']}{said}")
    cost = payload["cost"]
    lines.append(f"  花销：{cost.get('model_calls', 0)} 次调用 / {cost.get('images', 0)} 张图"
                 + ("（**降级了**）" if cost.get("downgraded") else ""))
    lines.append(f"  下一步：{payload['next']}")
    return "\n".join(lines)


__all__ = ["VERDICT_BAD", "VERDICT_CHECK", "VERDICT_OK", "preview", "render_text"]
