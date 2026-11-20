"""全局状态快照（T2.1）。

教案块是**逐节串行**生成的一节一节写。每写完一节就更新这个快照，下一节只带
"快照 + 本节原文 + 相关概念"，**不累积前面各节的全文**（G6：输入不随节号增长）。

快照里放什么：
  progress   进度（做完哪些、下一个是谁）
  confirmed  已讲过的概念（后面只引用、不重新解释）—— 概念的唯一来源仍是 concepts.json
  notation   已建立的记号（$\boldsymbol{a}$ 是向量 这种），保证全书记法一致
  recap      最近几节的一句话小结，给下一节接话用
  style_rules 风格规则（写死，便于审计）
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))     # lib/files.py
from files import load_json, write_json             # noqa: E402

STYLE_RULES = [
    "只写原文没说的东西，不复述原文句子",
    "公式一律 LaTeX，记号沿用原文",
    "同一概念只在首次定义处解释，之后只引用",
    "不编造原文没有的定理与结论，不自己出题",
]
RECAP_LIMIT = 3          # 快照里保留最近几节的小结
CONFIRMED_LIMIT = 80     # 已讲过的概念最多带多少个进提示词（护住 G6）


def build_snapshot(parse_dir: Path, lesson_dir: Path, *, book: str = "",
                   units: set[str] | None = None) -> dict:
    """扫一遍已完成的教案 + concepts.json，重建快照。**不调模型**。

    `units`（2026-09-26 加，规格 §3.1 **S7**）：**只允许这些单元**贡献内容。
    并行时这是必须的 —— 否则 worker 会读到"别的章正在写"的半成品版本，
    于是**同一份输入在不同调度下得出不同提示词**，产物就不再可复现了。

    谁来传它：`generate.run()` 在 worker 模式下传
    "起跑前冻结的那一批 + 本 worker 自己这一章的单元"（见 `_snapshot_scope()`）。
    传 `None` = 不限制（原来的行为，串行/独立跑时用）。
    """
    parse_dir = Path(parse_dir)
    lesson_dir = Path(lesson_dir)
    chapters = load_json(parse_dir / "chapters.json") or {}
    concepts = load_json(parse_dir / "concepts.json") or {}
    book_units = chapters.get("lesson_units", [])
    allowed = set(units) if units is not None else None

    done: list[dict] = []
    notation: list[dict] = []
    seen_symbols: set[str] = set()
    for unit in book_units:
        if allowed is not None and unit["unit_id"] not in allowed:
            continue
        lesson = load_json(lesson_dir / "lessons" / f"{unit['unit_id']}.json")
        if lesson is None:
            continue
        checks = lesson.get("checks") or {}
        done.append({
            "unit_id": unit["unit_id"],
            "anchor": unit.get("anchor"),
            "title": unit.get("title"),
            "one_line": lesson.get("one_line", ""),
            # ⚠️ 不看快照里的 `needs_review`（那是上一次那套规则写的，规则一改就骗人），
            #    改看**产物里真实存在的问题**。`snapshot.json` 会进下一节的提示词，
            #    过期值会误导模型（2026-09-26 结构审计 B-5）。
            "needs_review": bool(checks.get("problems")),
        })
        for item in lesson.get("notation") or []:
            symbol = str(item.get("符号", "")).strip()
            if symbol and symbol not in seen_symbols:
                seen_symbols.add(symbol)
                notation.append({"符号": symbol, "含义": item.get("含义", ""),
                                 "unit_id": unit["unit_id"]})

    done_ids = {row["unit_id"] for row in done}
    confirmed: list[dict] = []
    for concept in concepts.get("concepts", []):
        anchors = [o for o in concept.get("occurrences", []) if o.get("unit_id") in done_ids]
        if anchors:
            confirmed.append({"id": concept["id"], "name": concept["name"],
                              "anchor": anchors[0].get("anchor")})
    # 已经讲过、但下一节还没做的：下一步要处理的那个单元。
    # ⚠️ 也按 `units` 收窄 —— 否则 worker 会看到"别的章还没做的单元"（那是别人的活），
    #    `progress.next` 就不再是"我接下来要做谁"，而且会随调度漂移。
    scope = [u for u in book_units
             if allowed is None or u["unit_id"] in allowed]
    remaining = [u for u in scope if u["unit_id"] not in done_ids]
    snapshot = {
        "_meta": {
            "file_type": "lesson_snapshot",
            "schema_version": "1.0",
            "generator": "aiforstudy.lesson.snapshot",
            "field_notes": {
                "progress": "进度：总数/已完成/下一个要做的单元",
                "confirmed[]": "已讲过的概念（后面只引用不重讲；概念的唯一来源仍是 concepts.json）",
                "notation[]": "已建立的记号，保证全书记法一致",
                "recap[]": f"最近 {RECAP_LIMIT} 节的一句话小结，给下一节接话用",
                "style_rules[]": "风格规则（写死，便于审计）",
            },
        },
        "book": book,
        "progress": {
            "total": len(book_units),
            "done_count": len(done),
            "done": [row["unit_id"] for row in done],
            "next": remaining[0]["unit_id"] if remaining else None,
            "needs_review": [row["unit_id"] for row in done if row["needs_review"]],
        },
        "confirmed": confirmed,
        "notation": notation,
        "recap": done[-RECAP_LIMIT:],
        "style_rules": STYLE_RULES,
    }
    return snapshot


def prompt_view(snapshot: dict) -> dict:
    """快照进提示词的精简版（护住 G6：只带必要字段、有上限）。"""
    return {
        "progress": {k: v for k, v in snapshot["progress"].items() if k != "done"},
        "confirmed": snapshot["confirmed"][-CONFIRMED_LIMIT:],
        "notation": snapshot["notation"],
        "recap": snapshot["recap"],
        "style_rules": snapshot["style_rules"],
    }


__all__ = ["build_snapshot", "prompt_view", "load_json", "write_json",
           "STYLE_RULES", "RECAP_LIMIT", "CONFIRMED_LIMIT"]
