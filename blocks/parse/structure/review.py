"""结构复核（任务 T1.6）：用模型修"章节层级"的错误，**不碰原文一个字节**。

## 为什么必要（不是冗余）

T1.5 管"图在哪"，T1.6 管"**这一节的原文归谁**"。层级错了会连带三件事：
`chapters.json` 的树、`lesson_units` 的成员、以及教案块的逐节边界。

实测例子：OCR 在某页把页码和标题连成了 `1.6 (原文 1) 5.225` 这样的垃圾标题，
结果本该是**节**的 `高维数组向量` 被挤成了 `1.6.2` 小节 ——
**这节课的原文就被切进了错误的单元**。

## 边界（必须划死）

* `original_text/*.md`：**绝不改动**。复核只调层级归属，不碰一个字；
  应用后要能验证原文文件的 sha256 没变（全局约束 G5）。
* `structure.json` 的 `level` / `canonical` / 树形归属 / `lesson_units`：**会改**，这就是目的。
* 每次改动写进 `revisions`：改了什么、依据什么、模型怎么说。

## 两步走（你不会被模型偷偷改掉东西）

1. `review_structure()` → 产出 `review.json`（**建议稿**，不落盘到正式文件）
2. `apply_review()`   → 应用建议，写回 `structure.json` 并记 `revisions`
   —— 支持 dry-run，先看会改成什么样
"""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA_VERSION = "1.0"
GENERATOR = "aiforstudy.parse.structure.review"
ACTIONS = {"set_level", "drop", "keep"}

LEVEL_MEANING = {1: "章", 2: "节", 3: "小节", 4: "更细的知识块"}




# ── 第一步：免费规则先挑出"可疑项"（省钱的筛子） ────────────────────────
def suspects(headings: list[dict]) -> list[int]:
    """按确定性规则挑出可能错的标题的 source_line。

    只有可疑项才送模型，其余原样保留 —— 一本书几百个标题，全送既慢又贵。
    """
    out: list[int] = []
    seen: dict[tuple, int] = {}
    for index, heading in enumerate(headings):
        line = heading["source_line"]
        title = str(heading.get("title", ""))
        level = heading.get("level")
        numeric = heading.get("numeric")
        previous = headings[index - 1] if index else None

        reasons = []
        # 标题里混进了数字/括号（OCR 把页码或公式碎片连上来了）
        if any(ch.isdigit() for ch in title):
            reasons.append("标题含数字")
        if len(title.strip()) <= 1:      # 中文两字标题是正常的（如"向量""极限"），只有单字才可疑
            reasons.append("标题过短")
        # 层级跳跃
        if previous is not None and level is not None and previous.get("level") is not None:
            if level > previous["level"] + 1:
                reasons.append("层级跳变")
        # 编号与层级不匹配：数字路径的**深度应当等于层级**
        # （`1.8.1` 深度 3 → 就该是层级 3。实测漏判过：1.8.1 被写成层级 2，
        #   原因是它的父级 `1.8` 那行没被识别出来）
        if isinstance(numeric, list) and level is not None and len(numeric) != level:
            reasons.append("编号深度与层级不符")
        # 同标题重复出现（页眉/重复排版）
        key = (heading.get("scheme"), heading.get("raw_number"), title.replace(" ", ""))
        if key in seen:
            reasons.append("与前文重复")
        seen[key] = line
        # 相邻标题层级相同但编号不连续（漏了中间层）
        if previous is not None and level is not None and previous.get("level") == level:
            prev_path = previous.get("numeric")
            if (isinstance(numeric, list) and isinstance(prev_path, list)
                    and len(numeric) == len(prev_path) and numeric[:-1] == prev_path[:-1]
                    and numeric[-1] != prev_path[-1] + 1):
                reasons.append("同层编号不连续")

        if reasons:
            out.append(line)
    return out


# ── 第二步：把（可疑项+上下文）送模型 ───────────────────────────────────
SYSTEM_PROMPT = """你在校对一本教材的章节结构。

这份结构是从 PDF 自动抽取的：可能因为 OCR 或原书编号混乱而出现错误。
层级定义：1 = 章，2 = 节，3 = 小节，4 = 更细的知识块。

请只判断**层级是否正确**，并遵守：
1. **不要新增标题**，不要合并标题，**不要修改标题文字**。
2. 只输出确实需要改的项；判断没问题的不要输出。
3. 允许的动作只有三种：
   - {"action": "set_level", "level": 1~4} —— 层级不对，改成正确的
   - {"action": "drop"} —— 这一行根本不是标题（例如页码/公式碎片被误认）
   - {"action": "keep"} —— 你一开始怀疑，但确认没问题
4. 依据是标题的文字内容与它的编号：例如"1.6 高维数组向量"是**节**（层级 2），
   写成层级 3 就错了；再例如标题是"5.225"这种纯数字，多半是垃圾，应 drop。

只输出 JSON：
{"decisions": [{"line": <整数，原样抄给出的 line>, "action": "...", "level": <整数，仅 set_level 时给>, "reason": "<简短中文理由>"}]}"""


def _chunk(rows: list[dict], size: int) -> list[list[dict]]:
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def review_structure(payload: dict, client, *, chunk_size: int = 50,
                     only_suspects: bool = True, progress=None) -> dict:
    """调模型复核结构，返回**建议稿**（不改动任何正式文件）。

    只把可疑项送模型（`only_suspects=False` 才全送）。分片送、按片缓存。
    """
    headings = payload.get("headings", [])
    if not headings:
        raise ValueError("structure.json 里没有 headings，无法复核")
    max_level = int(payload.get("_meta", {}).get("max_level", 4))
    flagged = set(suspects(headings))

    if only_suspects and not flagged:
        return _empty_review(payload, max_level, reason="规则没发现可疑项")

    # 送模型时带上邻域上下文：只看孤立一行容易误判
    rows = []
    for index, heading in enumerate(headings):
        if only_suspects and heading["source_line"] not in flagged:
            continue
        rows.append({
            "line": heading["source_line"],
            "level": heading["level"],
            "number": heading.get("raw_number"),
            "title": heading.get("title"),
            "prev": (headings[index - 1].get("title") if index else None),
            "next": (headings[index + 1].get("title") if index + 1 < len(headings) else None),
        })

    valid_lines = {h["source_line"] for h in headings}
    decisions: list[dict] = []
    chunks = _chunk(rows, chunk_size)
    for number, part in enumerate(chunks, 1):
        if progress is not None:
            progress(number, len(chunks))
        message = ("请复核下面这份章节结构片段，按约定的 JSON 格式给出需要修改的项。\n\n"
                   + json.dumps({"headings": part}, ensure_ascii=False, indent=1))
        result = client.chat_json(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": message}],
            tag=f"structure-review-{number}")
        for row in result["data"].get("decisions", []) or []:
            try:
                line = int(row.get("line"))
            except (TypeError, ValueError):
                continue
            action = str(row.get("action", "")).strip()
            if line not in valid_lines or action not in ACTIONS:
                continue
            level = row.get("level")
            if action == "set_level":
                try:
                    level = int(level)
                except (TypeError, ValueError):
                    continue
                if not 1 <= level <= max_level:
                    continue
            else:
                level = None
            decisions.append({"source_line": line, "action": action, "level": level,
                              "reason": str(row.get("reason", ""))[:200],
                              "model_said_level": row.get("level")})

    review = {
        "_meta": {
            "file_type": "structure_review",
            "schema_version": SCHEMA_VERSION,
            "generator": GENERATOR,
            "source_sha256": payload.get("_meta", {}).get("source_sha256", ""),
            "field_notes": {
                "decisions[].source_line": "对应 structure.json 里 headings[].source_line",
                "decisions[].action": "set_level 改层级 / drop 删掉这个假标题 / keep 确认无误",
                "decisions[].reason": "模型给的中文理由（用于人工判断，不参与计算）",
                "suspects": "规则挑出的可疑项行号（免费筛子），模型只复核了这些",
                "model_calls": "本次真实模型请求数（缓存命中不计）",
            },
        },
        "suspects": sorted(flagged),
        "suspects_total": len(flagged),
        "headings_total": len(headings),
        "decisions": decisions,
        "chunks": len(chunks),
    }
    return review


def _empty_review(payload: dict, max_level: int, *, reason: str) -> dict:
    return {
        "_meta": {"file_type": "structure_review", "schema_version": SCHEMA_VERSION,
                  "generator": GENERATOR,
                  "source_sha256": payload.get("_meta", {}).get("source_sha256", ""),
                  "field_notes": {}},
        "suspects": [], "suspects_total": 0,
        "headings_total": len(payload.get("headings", [])),
        "decisions": [], "chunks": 0, "note": reason,
    }


# ── 第三步：应用建议（可 dry-run，可拒绝） ──────────────────────────────
def apply_review(payload: dict, review: dict, *, max_level: int | None = None) -> dict:
    """把建议稿应用到结构上。返回**新的 payload**（不落盘），并附 revisions。

    只会改 `level` / 删除假标题；**标题文字、source_line、原文一律不动**。
    """
    max_level = max_level or int(payload.get("_meta", {}).get("max_level", 4))
    decisions = {int(d["source_line"]): d for d in review.get("decisions", [])}
    headings = payload.get("headings", [])

    kept: list[dict] = []
    revisions: list[dict] = []
    for heading in headings:
        line = heading["source_line"]
        decision = decisions.get(line)
        if decision is None:
            kept.append(dict(heading))
            continue
        action = decision["action"]
        if action == "drop":
            revisions.append({"source_line": line, "action": "drop",
                              "before": {"level": heading["level"], "title": heading["title"],
                                         "canonical": heading["canonical"]},
                              "after": None, "reason": decision.get("reason", "")})
            continue
        if action == "set_level" and decision.get("level") != heading["level"]:
            updated = dict(heading)
            revisions.append({"source_line": line, "action": "set_level",
                              "before": {"level": heading["level"],
                                         "canonical": heading["canonical"]},
                              "after": {"level": decision["level"]},
                              "reason": decision.get("reason", "")})
            updated["level"] = int(decision["level"])
            kept.append(updated)
            continue
        kept.append(dict(heading))          # keep 或层级没变

    # 应用后必须重新编号（层级变了，编号一定跟着变）
    _write_back_numbers(kept)

    updated = dict(payload)
    updated["headings"] = kept
    updated.setdefault("revisions", [])
    updated["revisions"] = [*payload.get("revisions", []), {
        "at": _now(), "source": "model_structure_review",
        "reviewed": len(decisions), "changed": len(revisions),
        "decisions": revisions,
    }]
    updated["stats"] = {**payload.get("stats", {}),
                        "headings_total": len(kept),
                        "headings_dropped": sum(1 for r in revisions if r["action"] == "drop"),
                        "headings_releveled": sum(1 for r in revisions if r["action"] == "set_level")}
    return updated


def _as_objects(rows: list[dict]):
    """renumber 期望的是带 numeric/level 的对象；这里用一个轻量壳复用它的逻辑。"""
    import structure                        # noqa: PLC0415  同目录模块
    Heading = structure.Heading
    objects = []
    for row in rows:
        numeric = row.get("numeric")
        objects.append(Heading(scheme=row.get("scheme", "num"),
                               raw_number=row.get("raw_number", ""),
                               title=row.get("title", ""),
                               source_line=row["source_line"], page=row.get("page", 0),
                               numeric=tuple(numeric) if numeric else None,
                               level=row["level"]))
    return objects


def _write_back_numbers(rows: list[dict]) -> list[str]:
    """把重新编号的结果写回 dict（按 source_line 对齐）。返回警告。"""
    import structure                        # noqa: PLC0415
    objects = _as_objects(rows)
    warnings: list[str] = []
    structure.renumber(objects, warnings)
    by_line = {o.source_line: o for o in objects}
    for row in rows:
        source = by_line[row["source_line"]]
        row["canonical"] = source.canonical
        row["canonical_level"] = source.canonical_level
    return warnings


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ── 落盘辅助 ────────────────────────────────────────────────────────────
def write_json(path: str | Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


__all__ = ["review_structure", "apply_review", "suspects", "write_json", "read_json",
           "SCHEMA_VERSION", "GENERATOR", "ACTIONS", "LEVEL_MEANING"]
