"""概念的"出现在哪些单元"（occurrence）—— **整本表由单线程重扫产物生成**。

## 问题（规格 §3.1 **S6**）

`concepts.json` 的 `occurrences[]` 原来有两个写者：

1. `blocks/parse/concepts` 合并分片时写"抽到这条概念的单元"；
2. `blocks/lesson/generate.backfill_concepts` 在每节教案跑完时，**读整份 `concepts.json`
   → 往本节引用到的概念上追加一行 → 整份写回**。

单章串行时没问题。按章并行时，两个 worker 各读各改同一份文件 ——
**后写的把先写的 occurrence 盖掉**，而且没有任何一行代码会报错。

## 堵法：occurrence 是**教案产物的派生视图**，不再单独存一份

关键观察：**"这一节引用了哪些概念"这件事，教案产物里本来就有**
（`lesson["concepts"]`，由正文占位符确定性推出）。所以：

* worker **什么都不用额外写** —— 它已经写了 `lessons/<节>.json`，那就是唯一的真相；
* 整本 `concepts.json` 的 occurrence 由本模块的 `rebuild()` 在**所有 worker 停之后**
  单线程重扫 `concepts.parts/`（定义在哪）+ `lessons/*.json`（谁引用了它）生成。

> 为什么**不**另外做一份"每节一份的 uses 分片"：那会把同一份信息
> （本节引用了哪些概念）存两处 —— 而"同一份信息写了两处"正是本项目 119 条错误里
> 最大的那个母体（见 `为什么总出错-调研与自动化方案.md` §2.1 的 M 类）。
> 教案产物已经是"每节一份"了，再抄一遍只会多一个会漂移的副本。

## 为什么 occurrence 的顺序是有语义的（不能随便排）

`derive_references()` 用 `occurrences[0].unit_id` 判断一个概念是本节
"introduced" 还是 "reused" —— 所以**第一条必须是"原文里抽到它的那个单元"**
（来自 `concepts.parts/`），教案引用产生的行只能往后追加。

## G4 边界

`brief` / `explanation` / `formal` **只在 `concepts.json` 里存在一份**，
本模块**只动 `occurrences`**，一个解释字段都不碰（定义仍归概念抽取块所有）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "lib"))     # lib/files.py

from files import load_json, write_json              # noqa: E402

GAPS_NAME = "concepts.gaps.json"
GENERATOR = "aiforstudy.lesson.occurrences"

_EXTRACT = None


def _extract():
    """概念抽取块（拿它的 `normalize_name` / `ALLOWED_TYPES`）—— **不另写一份**。

    名称归一化与"收哪些类型"这两条规则的权威实现在 `extract_concepts`，
    （2026-09-29 起是七类：定义/定理/定律/性质/命题/推论/公式），
    这里如果抄一份，两边迟早不一致（这正是"同一份信息写两处"的老毛病）。
    """
    global _EXTRACT
    if _EXTRACT is None:
        path = HERE.parents[1] / "blocks" / "parse" / "concepts" / "extract_concepts.py"
        spec = importlib.util.spec_from_file_location("extract_concepts_for_occurrences", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _EXTRACT = module
    return _EXTRACT


def _differs(path: Path, payload) -> bool:
    path = Path(path)
    if not path.is_file():
        return True
    try:
        import json
        return json.loads(path.read_text(encoding="utf-8")) != payload
    except ValueError:
        return True


def referenced_concepts(parse_dir: Path, lesson: dict) -> tuple[int, list[dict]]:
    """这一节引用了哪些概念（纯计算，**不写盘**）→ `(表里有的条数, 缺口列表)`。

    与老的 `backfill_concepts` 返回的 `{"backfilled", "gaps"}` 同源，
    所以索引里的 `units[].backfill` 字段含义不变。
    """
    known = {c["id"] for c in (load_json(Path(parse_dir) / "concepts.json") or {})
             .get("concepts", [])}
    unit_id = str(lesson.get("unit_id") or "")
    found, gaps = 0, []
    for item in lesson.get("concepts") or []:
        ref = str(item.get("id") or "")
        if not ref:
            continue
        if ref in known:
            found += 1
        else:
            gaps.append({"unit_id": unit_id, "id": ref, "name": item.get("name"),
                         "role": item.get("role")})
    return found, gaps


def _part_occurrences(parse_dir: Path, units: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """从 `concepts.parts/<节>.json` 取"原文里抽到这条概念的单元"（顺序 = 书的单元顺序）。

    行内字段与顺序**逐字对齐 `extract_concepts.merge_parts`** —— 重建出来的
    `concepts.json` 必须与原来一模一样（验收要求"产物与改前一致"）。
    """
    ex = _extract()
    out: dict[tuple[str, str], list[dict]] = {}
    seen: set[tuple[tuple[str, str], str]] = set()
    for unit in units:
        uid = str(unit.get("unit_id"))
        part = load_json(Path(parse_dir) / "concepts.parts" / f"{uid}.json")
        if not part:
            continue
        for row in part.get("concepts") or []:
            if row.get("type") not in ex.ALLOWED_TYPES or not row.get("name"):
                continue
            key = (ex.normalize_name(row["name"]), row["type"])
            if (key, uid) in seen:      # 同一节里重复出现只记一次（模型偶尔分两条返回）
                continue
            seen.add((key, uid))
            out.setdefault(key, []).append({
                "unit_id": uid, "chapter": unit.get("chapter"), "section": unit.get("section"),
                "title": unit.get("title"), "anchor": unit.get("anchor")})
    return out


def _lesson_occurrences(lessons_dir: Path, units: list[dict]) -> dict[str, list[dict]]:
    """教案产物贡献的引用行（按书的单元顺序，逐字对齐老 `backfill_concepts` 的字段与次序）。

    只认**磁盘上的教案产物** —— 它由 worker 独占写，所以并行时读它是安全的
    （读的是"每节私有"的东西，不是整本视图）。
    """
    out: dict[str, list[dict]] = {}
    for unit in units:
        uid = str(unit.get("unit_id"))
        lesson = load_json(Path(lessons_dir) / f"{uid}.json")
        if not lesson:
            continue
        for item in lesson.get("concepts") or []:
            ref = str(item.get("id") or "")
            if not ref:
                continue
            # 字段顺序与老实现一致（unit_id → anchor → chapter → section → title → role）
            out.setdefault(ref, []).append({
                "unit_id": uid, "anchor": lesson.get("anchor"), "chapter": lesson.get("chapter"),
                "section": lesson.get("section"), "title": lesson.get("title"),
                "role": item.get("role")})
    return out


def plan(parse_dir: Path, lessons_dir: Path) -> dict:
    """算出"重建之后应该长什么样"（**纯函数，不写盘**）。

    拆出来的用处：`reconcile --check` 要能**先看会改什么、但一个字节都不动**。
    返回 `{"payload", "gaps_doc", "changed", "concepts", "occurrences", "gaps"}`。
    """
    parse_dir, lessons_dir = Path(parse_dir), Path(lessons_dir)
    chapters = load_json(parse_dir / "chapters.json") or {}
    units = chapters.get("lesson_units") or []
    order = {str(u.get("unit_id")): i for i, u in enumerate(units)}

    payload = load_json(parse_dir / "concepts.json") or {}
    concepts = payload.get("concepts") or []
    ex = _extract()
    from_parts = _part_occurrences(parse_dir, units)
    from_lessons = _lesson_occurrences(lessons_dir, units)

    changed = 0
    for concept in concepts:
        key = (ex.normalize_name(str(concept.get("name") or "")), concept.get("type"))
        existing = list(concept.get("occurrences") or [])
        merged: list[dict] = []
        seen: set[str] = set()

        def _add(row: dict) -> None:
            uid = str(row.get("unit_id") or "")
            if uid and uid not in seen:
                seen.add(uid)
                merged.append(row)

        # 顺序**有语义**：`occurrences[0]` 必须是"原文里抽到它的那个单元"
        # （`derive_references()` 靠它判 introduced / reused），所以定义行排最前。
        for row in from_parts.get(key, []):
            _add(row)
        # 然后把**已有行原样保留**（既保序、也保历史）。
        # 为什么不做"以重算结果为准、多余的删掉"：出现位置是一份**只增的账**，
        # 删错了没有任何东西会报错；而"多一行"最多是抽屉里多一条链接。
        # 这与交付范围那条「范围记录不缩水」是同一个取舍。
        for row in existing:
            _add(row)
        # 最后追加教案产物里新发现的引用（按书的单元顺序）
        for row in from_lessons.get(str(concept.get("id")), []):
            _add(row)

        if merged != existing:
            concept["occurrences"] = merged
            changed += 1

    # 缺口：教案引用了表里没有的概念 → 记下来交人定，**不许偷偷新增概念**
    gaps: list[dict] = []
    for unit in units:
        uid = str(unit.get("unit_id"))
        lesson = load_json(lessons_dir / f"{uid}.json")
        if lesson:
            gaps.extend(referenced_concepts(parse_dir, lesson)[1])
    gaps_doc = None
    if gaps:
        gaps_doc = {
            "_meta": {"file_type": "concept_gaps", "schema_version": "1.0", "generator": GENERATOR},
            "note": "教案块遇到的概念缺口：模型认为这里有概念、但 concepts.json 里没有。"
                    "不自动新增，交人定。",
            "gaps": gaps}
    return {"payload": payload, "gaps_doc": gaps_doc, "changed": changed,
            "concepts": len(concepts),
            "occurrences": sum(len(c.get("occurrences") or []) for c in concepts),
            "gaps": len(gaps)}


def rebuild(parse_dir: Path, lessons_dir: Path) -> dict:
    """**整本 occurrence 的唯一写法**：重扫 `concepts.parts/` + `lessons/*.json`。

    幂等：同一批产物重跑多少次，结果都一样（这是它敢"事后重建"的前提，规格 §4）。
    只改 `occurrences`，不动任何解释字段（G4）。
    """
    parse_dir, lessons_dir = Path(parse_dir), Path(lessons_dir)
    result = plan(parse_dir, lessons_dir)
    if result["changed"]:                # 没变就不动盘（幂等 = 内容不变，就不用改 mtime）
        write_json(parse_dir / "concepts.json", result["payload"])
    gaps_path = parse_dir / GAPS_NAME
    if result["gaps_doc"] is not None:
        if _differs(gaps_path, result["gaps_doc"]):
            write_json(gaps_path, result["gaps_doc"])
    elif gaps_path.is_file():
        gaps_path.unlink()
    return {"concepts": result["concepts"], "changed": result["changed"],
            "occurrences": result["occurrences"], "gaps": result["gaps"]}


__all__ = ["GAPS_NAME", "referenced_concepts", "plan", "rebuild"]
