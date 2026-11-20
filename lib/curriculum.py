"""**课程时间线**：到某一节为止，书里已经讲过哪些概念（P-016 的机器判据基础）。

用户 2026-09-25 的批评：「解方程有时候不需要线性代数后面的那些矩阵和线性空间……
换个知识点换个书还是这样的不行，一定得是整个程序的」。所以"前置知识"这件事必须做成
**与书无关的机器判据**，而不是靠提示词里叮嘱一句。

做法（只用已有的两份产物，0 token）：

* `parse/chapters.json` 的 `lesson_units` 顺序 = **书里的先后顺序**；
* `parse/concepts.json` 里每个概念的 `occurrences[].unit_id` = 它在哪几节出现；
* 于是：某一节的 `introduced` = **首次出现不晚于这一节**的那些概念。

有了它，校验器就能判"这个模型 / 这段正文，是不是借了还没讲的东西"——
换一本书、换一个知识点，只要这两份产物在，判据照样成立。
"""
from __future__ import annotations

import json
from pathlib import Path


def unit_order(parse_dir: str | Path) -> list[str]:
    """书里的单元顺序（`chapters.json` 的 `lesson_units`）。"""
    path = Path(parse_dir) / "chapters.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [str(u.get("unit_id")) for u in (payload.get("lesson_units") or [])]


def concept_first_unit(parse_dir: str | Path) -> dict[str, str]:
    """每个概念**第一次出现**在哪一节。"""
    path = Path(parse_dir) / "concepts.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    order = unit_order(parse_dir)
    rank = {uid: i for i, uid in enumerate(order)}
    first: dict[str, str] = {}
    for concept in payload.get("concepts") or []:
        cid = str(concept.get("id") or "")
        if not cid:
            continue
        units = [str(o.get("unit_id")) for o in (concept.get("occurrences") or [])]
        # 取"书里最靠前"的那一节（occurrences 的顺序不一定等于书的顺序）
        units = sorted([u for u in units if u in rank], key=lambda u: rank[u])
        if units:
            first[cid] = units[0]
    return first


def timeline(parse_dir: str | Path) -> dict[str, set[str]]:
    """{单元 → 到这一节为止**已经讲过**的概念 id 集合}。

    注意是"到这一节为止（含本节）"：本节自己首次引入的概念，本节当然可以用。
    """
    order = unit_order(parse_dir)
    first = concept_first_unit(parse_dir)
    out: dict[str, set[str]] = {}
    seen: set[str] = set()
    for uid in order:
        seen = set(seen)
        for cid, where in first.items():
            if where == uid:
                seen.add(cid)
        out[uid] = seen
    return out


def introduced_by(parse_dir: str | Path, unit_id: str) -> set[str]:
    """到 `unit_id` 为止已经讲过的概念（找不到这一节就返回全书概念）。"""
    table = timeline(parse_dir)
    if unit_id in table:
        return table[unit_id]
    return set(concept_first_unit(parse_dir))


def later_than(parse_dir: str | Path, unit_id: str) -> set[str]:
    """**这一节之后**才会讲的概念（用来判"借了后面的东西"）。"""
    first = concept_first_unit(parse_dir)
    return {cid for cid in first if cid not in introduced_by(parse_dir, unit_id)}


def concept_names(parse_dir: str | Path) -> dict[str, str]:
    path = Path(parse_dir) / "concepts.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(c.get("id")): str(c.get("name") or "") for c in (payload.get("concepts") or [])}


__all__ = ["unit_order", "concept_first_unit", "timeline", "introduced_by",
           "later_than", "concept_names"]
