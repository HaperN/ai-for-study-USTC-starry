#!/usr/bin/env python3
"""Select source-bound training papers without manufacturing review approvals.

The plan describes pedagogical intent. Catalog validation proves only that the
question content passed its recorded review. Task-chain bindings are separate,
inspectable selection evidence, never a substitute for that mathematical review.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiforstudy.practice.assembly import validate_papers
from aiforstudy.practice.catalog import validate_catalog, validate_review_files
from tools.build_practice_delivery import validate_paper_view_parents, validate_paper_view_excerpts

KINDS = ("coverage", "fitted", "pressure")


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _stem_key(q):
    # Removing whitespace/LaTeX spacing stops formatting and renamed IDs from
    # masquerading as distinct questions, without equating different numbers.
    value = re.sub(r"\s+|\\(?:,|;|!|qquad|quad|displaystyle|textstyle|left|right)", "", q["stem"])
    return re.sub(r"\$|\\[\[\]()]", "", value)


def _jaccard(a, b):
    return len(a & b) / len(a | b) if a or b else 0.0


def _question_ids(candidate):
    return candidate.get("question_ids") or ([candidate["question_id"]] if candidate.get("question_id") else [])


def _task_count(question, source_counts):
    if question.get('paper_only') is True:
        return question['subquestion_count'], 'independently_reviewed_view_scope'
    keys = [question['id'], question.get('source_task_id')]
    known = [source_counts[k] for k in keys if isinstance(k, str) and k in source_counts]
    if known:
        return max(known), 'source_bank_subquestion_count'
    references = question.get('source', {}).get('reference_question_ids', [])
    count = max(question.get('subquestion_count', 1), len(references) or 1)
    if type(count) is not int or count < 1:
        raise ValueError(question['id'] + ': invalid subquestion count')
    return count, 'record_or_reference_subquestions'


def _parent_conflict(ids, byid):
    return any(byid[i].get('paper_only') is True and byid[i].get('source_parent_question_id') in ids for i in ids)


def _workload(paper, byid, source_counts):
    items = []
    for item in paper['items']:
        question = byid[item['question_id']]
        count, basis = _task_count(question, source_counts)
        # A declared heuristic, not a claim about measured student performance.
        minutes = math.ceil(max(item['points'] * 0.9, count * {1: 2, 2: 4, 3: 7, 4: 10, 5: 15}[question['difficulty']]))
        items.append({'question_id': question['id'], 'subquestion_count': count, 'count_basis': basis,
                      'points': item['points'], 'estimated_minutes': minutes,
                      'source_parent_question_id': question.get('source_parent_question_id')})
    return {'paper_id': paper['id'], 'question_records': len(items),
            'subquestion_count': sum(i['subquestion_count'] for i in items),
            'points': sum(i['points'] for i in items), 'estimated_minutes': sum(i['estimated_minutes'] for i in items),
            'student_timing_measured': False,
            'estimate_basis': '每独立小问按难度1至5暂估2/4/7/10/15分钟，并不低于训练分值×0.9；仅供负荷检查，未经真人试作。',
            'items': items}


def slot_contract_digest(slot):
    """Bind semantic matching to the exact source and its required operations."""
    return _hash({k: slot.get(k) for k in ("source_slot_id", "required_operations", "source", "source_questions",
                                         "minimum_preserved_conditions", "matching_policy", "explicit_method_requirements")})


def _learning_units(q):
    """A shared subquestion may train another unit without becoming a new ID."""
    from aiforstudy.practice.coverage import statement_digest
    units={q['unit_id']}
    digest=statement_digest(q)
    for site in q.get('additional_mounts',[]):
        if (isinstance(site.get('unit_id'),str) and
                site.get('coverage_evidence',{}).get('statement_sha256')==digest):
            units.add(site['unit_id'])
    return units


def _matches(q, selector, basic=False):
    if basic and q.get("basic") is not True:
        return False
    if selector.get("ids") and q["id"] not in selector["ids"]:
        return False
    if q["id"] in selector.get("exclude_ids", []):
        return False
    units=_learning_units(q)
    if selector.get("unit_ids") and not units.intersection(selector["unit_ids"]):
        return False
    if selector.get("unit_prefixes") and not any(uid == u or uid.startswith(u + "-") for u in selector["unit_prefixes"] for uid in units):
        return False
    if not selector.get("difficulty_min", 1) <= q["difficulty"] <= selector.get("difficulty_max", 5):
        return False
    if selector.get("task_kinds") and q.get("task_kind") not in selector["task_kinds"]:
        return False
    if selector.get("source_kinds") and q["source"]["kind"] not in selector["source_kinds"]:
        return False
    for stem, field in (("knowledge", "knowledge_ids"), ("facet", "facet_ids")):
        actual = set(q.get(field, []))
        if selector.get(stem + "_any") and not actual.intersection(selector[stem + "_any"]):
            return False
        if not set(selector.get(stem + "_all", [])).issubset(actual):
            return False
    return True


def _binding_problem(slot, candidate, byid, kind):
    ids = _question_ids(candidate)
    if not ids or len(set(ids)) != len(ids) or any(i not in byid for i in ids):
        return "candidate_questions_missing", "候选题缺失或重复"
    if candidate.get("_original_source"):
        return None
    if kind not in ("fitted", "pressure"):
        return None
    hashes = candidate.get("question_hashes", {})
    if any(hashes.get(i) != byid[i]["review"]["content_sha256"] for i in ids):
        return "candidate_content_binding_invalid", "选题依据未绑定当前已审内容"
    if kind == "fitted":
        if candidate.get("match_status") != "matched" or candidate.get("source_slot_digest") != slot_contract_digest(slot):
            return "fitted_source_binding_invalid", "匹配记录未绑定当前来源任务链，或没有匹配结论"
        operations = slot.get("required_operations", [])
        evidence = candidate.get("operation_evidence", [])
        if not operations or {r.get("operation_index") for r in evidence} != set(range(len(operations))):
            return "fitted_task_evidence_missing", "任务链没有逐项对应证据"
        for row in evidence:
            qid, field, quote = row.get("question_id"), row.get("field", "stem"), row.get("quote")
            if qid not in ids or field not in ("stem", "solution") or not quote or quote not in byid[qid][field] or not row.get("reason"):
                return "fitted_task_evidence_invalid", "任务链证据不是所选题当前题面或解析中的原文"
        if not candidate.get("condition_changes") or not candidate.get("fit_rationale"):
            return "fitted_condition_changes_missing", "缺少条件改变与任务链保持说明"
    if kind == "pressure":
        entry = candidate.get("entry", {})
        qid, quote, points = entry.get("question_id"), entry.get("solution_quote"), entry.get("points")
        if qid not in ids or not quote or quote not in byid[qid]["solution"] or type(points) not in (int, float) or not 0 < points < slot["points"]:
            return "pressure_entry_invalid", "入口分未对应所选题真实解题步骤"
        if not candidate.get("obstacle") or not candidate.get("blocked_shortcut"):
            return "pressure_obstacle_missing", "缺少真实障碍与不能直接套用的方法说明"
    return None


def _source_candidate(slot, byid):
    sources = slot.get("source_questions", [])
    if not sources or slot.get("allow_source_questions") is False:
        return None
    ids = []
    for source in sources:
        q = byid.get(source["id"])
        if not q or source.get("ready_for_authoring") is False:
            return None
        if q["source"].get("kind") != "past_exam" or q["source"].get("pdf_page") != source.get("pdf_page"):
            return None
        if q["stem"] != source.get("stem") and q["source"].get("source_quote") != source.get("stem"):
            return None
        ids.append(source["id"])
    result = {"question_ids": ids, "_original_source": True,
            "fit_rationale": "同一来源编号题位的全部原子子问；依据原题内容逐问保留。",
            "condition_changes": ["使用历史原题；未声称新编。"], "novelty": "source_original"}
    scores = [s.get("points") for s in sources]
    if all(type(p) in (int, float) and p > 0 for p in scores):
        if not math.isclose(sum(scores), slot["points"]):
            return None
        result.update(point_split=scores, atomic_points_basis="source_explicit")
    return result


def _slot_candidates(slot, paper, byid, shared_bindings, source_counts=None):
    kind = paper["kind"]
    candidates = deepcopy(slot.get("candidates", []))
    pool = slot.get("binding_pool", slot.get("source_slot_id", slot["id"]))
    candidates.extend(deepcopy(shared_bindings.get(pool, [])))
    # Source identity is established here, never trusted from plan/model data.
    candidates = [{k: v for k, v in row.items() if not k.startswith("_")} for row in candidates]
    source = _source_candidate(slot, byid) if kind == "fitted" else None
    if source:
        candidates.append(source)
    if kind == "coverage" and not candidates:
        candidates = [{"question_ids": [q["id"]]} for q in byid.values()]
    valid, rejected, seen = [], [], set()
    for candidate in candidates:
        problem = _binding_problem(slot, candidate, byid, kind)
        if problem:
            rejected.append({"code": problem[0], "reason": problem[1], "question_ids": _question_ids(candidate)})
            continue
        ids = _question_ids(candidate)
        if not all(_matches(byid[i], slot.get("selector", {}), paper.get("edition") == "basic") for i in ids):
            continue
        if kind in ('coverage', 'pressure') and sum(_task_count(byid[i], source_counts or {})[0] for i in ids) > 4:
            source_bound = all(_task_count(byid[i], source_counts or {})[1] == 'source_bank_subquestion_count' for i in ids)
            rejected.append({'code': 'oversized_independent_bundle' if source_bound else 'oversized_question_block',
                             'question_ids': ids,
                             'reason': '题位聚集超过4个小问；改用更集中的已审题或独立审核的自足视图，不以整包题量充作难度。'})
            continue
        key = tuple(ids)
        if key not in seen:
            seen.add(key)
            candidate["question_ids"] = ids
            valid.append(candidate)
    if not candidates and kind == "fitted":
        rejected.append({"code": "fitted_task_evidence_missing", "reason": "尚无来源原题或经逐操作核对的适配题"})
    if not candidates and kind == "pressure":
        rejected.append({"code": "pressure_obstacle_missing", "reason": "尚无含真实障碍和入口得分的适配题"})
    return valid, rejected


def _paper_from_choices(spec, choices, byid):
    p = {k: deepcopy(v) for k, v in spec.items() if k not in ("slots", "fixed_paper", "legacy_fit_blueprint")}
    p["items"], p["task_slots"] = [], []
    for slot in spec["slots"]:
        chosen = choices[slot["id"]]
        ids = chosen["question_ids"]
        points = chosen.get("point_split")
        if points is None:
            # Equal atomic shares are only a training rubric. The numbered
            # source task still has the original aggregate score.
            points = [slot["points"] / len(ids)] * len(ids)
        if len(points) != len(ids) or any(type(x) not in (int, float) or x <= 0 for x in points) or not math.isclose(sum(points), slot["points"]):
            raise ValueError(f"{p['id']}/{slot['id']}: invalid atomic point allocation")
        for qid, score in zip(ids, points):
            item = {"question_id": qid, "points": score, "slot_id": slot["id"]}
            if slot.get("section"):
                item["section"] = slot["section"]
            p["items"].append(item)
        p["task_slots"].append({"id": slot["id"], "question_ids": ids, "points": slot["points"],
                                "points_basis": slot.get("points_basis", "training_allocation"),
                                "source_slot_id": slot.get("source_slot_id"),
                                "required_operations": slot.get("required_operations", []),
                                "selection_evidence": {k: v for k, v in chosen.items() if not k.startswith("_")},
                                "source_original": bool(chosen.get("_original_source")),
                                "atomic_points_note": ("子题分数保持来源中明示的分配。" if chosen.get("atomic_points_basis") == "source_explicit" else "子题分数仅为训练分配；来源题位总分保持不变。") if len(ids) > 1 else None})
    p["numbered_slot_count"] = len(spec["slots"])
    p["publication_status"] = "formal_training"
    if p["kind"] == "pressure":
        p["entry_rubric"] = [{"slot_id": s["id"], **choices[s["id"]]["entry"],
                              "obstacle": choices[s["id"]]["obstacle"],
                              "blocked_shortcut": choices[s["id"]]["blocked_shortcut"]} for s in spec["slots"]]
        p["entry_points"] = sum(row["points"] for row in p["entry_rubric"])
    p.setdefault("time_basis", "训练时长为建议，未声称原卷时长或学生实测难度等值。")
    # The complete catalog is validated once at assemble() entry. Validate the
    # selected records and paper here, without scanning unrelated questions at
    # every backtracking leaf.
    return validate_papers([byid[qid] for qid in {i['question_id'] for i in p['items']}], [p])[0]


def _local_problems(p, spec, byid):
    problems = []
    ids = [i["question_id"] for i in p["items"]]
    if _parent_conflict(set(ids), byid):
        problems.append({'code': 'parent_view_conflict', 'paper_id': p['id']})
    if len({_stem_key(byid[i]) for i in ids}) != len(ids):
        problems.append({"code": "duplicate_content_in_paper", "paper_id": p["id"]})
    if p["kind"] == "fitted" and not spec.get("fixed_paper"):
        expected = p.get("reference", {}).get("section_points", {})
        if isinstance(expected, dict):
            actual = Counter()
            for item in p["items"]:
                actual[item.get("section", "")] += item["points"]
            if set(actual) != set(expected) or any(not math.isclose(actual[k], expected[k]) for k in expected):
                problems.append({"code": "source_section_points_mismatch", "paper_id": p["id"], "actual": dict(actual), "expected": expected})
        changed = sum(not t["source_original"] for t in p.get("task_slots", []))
        if changed < spec.get("minimum_adapted_slots", 0):
            problems.append({"code": "insufficient_adapted_slots", "paper_id": p["id"], "actual": changed, "required": spec["minimum_adapted_slots"]})
    covered = {unit for ident in ids for unit in _learning_units(byid[ident])}
    for missing in sorted(set(spec.get('required_units', [])) - covered):
        problems.append({"code": "paper_required_unit_uncovered", "paper_id": p["id"], "unit_id": missing})
    return problems


def _difference(a, b, byid):
    ai, bi = ({i["question_id"] for i in p["items"]} for p in (a, b))
    ac, bc = ({_stem_key(byid[i]) for i in ids} for ids in (ai, bi))
    return {"paper_a": a["id"], "paper_b": b["id"], "kind": a["kind"],
            "question_jaccard": _jaccard(ai, bi), "content_jaccard": _jaccard(ac, bc),
            "shared_question_ids": sorted(ai & bi), "only_a": sorted(ai - bi), "only_b": sorted(bi - ai)}


def _select_paper(spec, byid, existing, bindings, limit, usage, max_nodes, source_counts=None):
    source_counts = source_counts or {}
    if spec.get("fixed_paper"):
        p = validate_papers(list(byid.values()), [deepcopy(spec["fixed_paper"])])[0]
        p["retained_from_previous_release"] = True
        problems = _local_problems(p, spec, byid)
        if p['kind'] in ('coverage', 'pressure'):
            problems.extend({'code': 'oversized_independent_bundle', 'paper_id': p['id'], 'question_id': item['question_id']}
                            for item in p['items'] if _task_count(byid[item['question_id']], source_counts)[0] > 4)
        return p, problems, {"nodes": 0, "retained": True}
    slots = spec.get("slots", [])
    if not slots or len({s["id"] for s in slots}) != len(slots):
        raise ValueError(f"{spec['id']}: nonempty distinct slot ids required")
    pools, gaps, stats = {}, [], {"nodes": 0, "rejected_candidate_evidence": []}
    covered_knowledge = {k for p in existing if p["kind"] == "coverage" for i in p["items"]
                         for k in byid[i["question_id"]].get("knowledge_ids", [])}
    covered_facets = {k for p in existing if p["kind"] == "coverage" for i in p["items"]
                      for k in byid[i["question_id"]].get("facet_ids", [])}
    for slot in slots:
        if type(slot.get("points")) not in (int, float) or not 0 < slot["points"] <= 100:
            raise ValueError(f"{spec['id']}/{slot['id']}: invalid points")
        if spec["kind"] == "fitted" and (not slot.get("required_operations") or not slot.get("points_basis") or not (slot.get("source") or slot.get("source_questions"))):
            gaps.append({"code": "fitted_source_contract_missing", "paper_id": spec["id"], "slot_id": slot["id"]})
        candidates, rejected = _slot_candidates(slot, spec, byid, bindings, source_counts)
        for item in rejected:
            stats["rejected_candidate_evidence"].append({"slot_id": slot["id"], **item})
        candidates.sort(key=lambda c: (sum(_task_count(byid[i],source_counts)[0] for i in c['question_ids']) if spec['kind'] in ('coverage','pressure') else 0,
                                      sum(usage[i] for i in c["question_ids"]),
                                      -len({k for i in c["question_ids"] for k in byid[i].get("knowledge_ids", [])} - covered_knowledge) if spec["kind"] == "coverage" else 0,
                                      -len({k for i in c["question_ids"] for k in byid[i].get("facet_ids", [])} - covered_facets) if spec["kind"] == "coverage" else 0,
                                      bool(c.get("_original_source")),
                                      _hash([spec["id"], slot["id"], c["question_ids"]])))
        pools[slot["id"]] = candidates
        if not candidates:
            gaps.append({"code": "slot_has_no_candidates", "paper_id": spec["id"], "slot_id": slot["id"],
                         "selector": slot.get("selector", {}), "required_operations": slot.get("required_operations", []),
                         "source_questions": slot.get("source_questions", []), "rejections": rejected})
            gaps.extend({"paper_id": spec["id"], "slot_id": slot["id"], **r} for r in rejected)
    stats["candidate_counts"] = {k: len(v) for k, v in pools.items()}
    if gaps:
        return None, gaps, stats
    ordered = sorted(slots, key=lambda s: (len(pools[s["id"]]), s["id"]))
    peers = [p for p in existing if p["kind"] == spec["kind"]]
    prior_sets = [{_stem_key(byid[i["question_id"]]) for i in p["items"]} for p in peers]
    choices, result = {}, None
    remaining_max = [0] * (len(ordered) + 1)
    for n in range(len(ordered) - 1, -1, -1):
        remaining_max[n] = remaining_max[n + 1] + max(len(c["question_ids"]) for c in pools[ordered[n]["id"]])

    def walk(index, used, contents):
        nonlocal result
        if stats["nodes"] >= max_nodes:
            return False
        stats["nodes"] += 1
        if index == len(ordered):
            p = _paper_from_choices(spec, choices, byid)
            if _local_problems(p, spec, byid):
                return False
            if any(max(_difference(p, peer, byid)[key] for key in ("question_jaccard", "content_jaccard")) > limit + 1e-12 for peer in peers):
                return False
            result = p
            return True
        slot = ordered[index]
        for candidate in pools[slot["id"]]:
            ids = set(candidate["question_ids"])
            keys = {_stem_key(byid[i]) for i in ids}
            if len(keys) != len(ids) or ids & used or keys & contents or _parent_conflict(ids | used, byid):
                continue
            new_contents = contents | keys
            # The largest possible final union is a safe lower bound for
            # overlap. This prunes impossible duplicates, never valid choices.
            if any(len(new_contents & prev) / (len(new_contents | prev) + remaining_max[index + 1]) > limit + 1e-12 for prev in prior_sets):
                continue
            choices[slot["id"]] = candidate
            if walk(index + 1, used | ids, new_contents):
                return True
            del choices[slot["id"]]
        return False

    walk(0, set(), set())
    if result is None:
        gaps.append({"code": "selection_search_exhausted" if stats["nodes"] >= max_nodes else "paper_constraints_unsatisfied",
                     "paper_id": spec["id"], "searched_nodes": stats["nodes"], "max_same_kind_jaccard": limit,
                     "note": "当前候选与约束未得到可发布组合；未把它宣称为数学上的不可满足证明。"})
    return result, gaps, stats


def assemble(questions, plan, *, evidence_verified=False, max_nodes=20000):
    validate_catalog(questions)
    views = [q for q in questions if q.get('paper_only') is True]
    if views:validate_paper_view_parents([q for q in questions if q.get('paper_only') is not True], views)
    if plan.get("schema_version") != "ExpandedPaperPlanV1":
        raise ValueError("unsupported paper plan schema")
    specs = plan.get("papers", [])
    if not specs or len({s["id"] for s in specs}) != len(specs):
        raise ValueError("nonempty distinct planned paper ids required")
    if any(s.get("kind") not in KINDS or s.get("edition") not in ("basic", "full") for s in specs):
        raise ValueError("invalid planned kind or edition")
    limit = plan.get("max_same_kind_jaccard", 0.35)
    if type(limit) not in (int, float) or not 0 <= limit <= 0.35:
        raise ValueError("same-kind overlap limit must be at most 0.35")
    byid = {q["id"]: q for q in sorted(questions, key=lambda q: q["id"])}
    source_counts = plan.get('source_task_counts', {})
    if not isinstance(source_counts, dict) or any(type(n) is not int or n < 1 for n in source_counts.values()):
        raise ValueError('source task counts must be positive integers')
    papers, gaps, search, usage = [], [], {}, Counter()
    if not evidence_verified:
        gaps.append({"code": "evidence_not_verified", "note": "仅候选规划；尚未验证题库审核证据文件。"})
    # Keep old formal papers first, so new papers must differ from them.
    ordered = sorted(specs, key=lambda s: (not bool(s.get("fixed_paper")), s.get("selection_order", 100), s["id"]))
    for spec in ordered:
        p, problems, stats = _select_paper(spec, byid, papers, plan.get("task_bindings", {}), limit, usage, max_nodes, source_counts)
        gaps.extend(problems)
        search[spec["id"]] = stats
        if p:
            papers.append(p)
            usage.update(i["question_id"] for i in p["items"])
    differences = [_difference(a, b, byid) for i, a in enumerate(papers) for b in papers[i + 1:] if a["kind"] == b["kind"]]
    for row in differences:
        if max(row["question_jaccard"], row["content_jaccard"]) > limit + 1e-12:
            gaps.append({"code": "same_kind_overlap", **row, "limit": limit})
    counts, basic_counts = Counter(p["kind"] for p in papers), Counter(p["kind"] for p in papers if p.get("edition") == "basic")
    for field, actual in (("target_counts", counts), ("basic_target_counts", basic_counts)):
        for kind in KINDS:
            expected = plan.get(field, {}).get(kind, 12 if field == "target_counts" else 3)
            if actual[kind] != expected:
                gaps.append({"code": field + "_mismatch", "kind": kind, "actual": actual[kind], "expected": expected})
    coverage_ids = {i["question_id"] for p in papers if p["kind"] == "coverage" for i in p["items"]}
    coverage_units = {unit for i in coverage_ids for unit in _learning_units(byid[i])}
    for unit in sorted(set(plan.get("required_coverage_units", [])) - coverage_units):
        gaps.append({"code": "required_unit_uncovered", "unit_id": unit})
    trials = deepcopy(plan.get("reference_trials", []))
    if any(p.get("publication_status") != "reference_trial" for p in trials):
        raise ValueError("reference-trial archive must not contain formal papers")
    if trials:
        validate_papers(questions, trials)
    workload = [_workload(p, byid, source_counts) for p in papers]
    for paper, row in zip(papers, workload):
        paper['workload'] = row
    return {"schema_version": "ExpandedPaperSelectionV1", "publishable": not gaps,
            "plan_sha256": _hash(plan), "catalog_content_sha256": _hash({q["id"]: q["review"]["content_sha256"] for q in byid.values()}),
            "papers": papers, "reference_trials": trials, "gaps": gaps, "differences": differences,
            "counts": dict(counts), "basic_counts": dict(basic_counts), "search": search,
            "workload": workload,
            "coverage": {"units": sorted(coverage_units), "question_count": len(coverage_ids),
                         "knowledge_ids": sorted({k for i in coverage_ids for k in byid[i].get("knowledge_ids", [])}),
                         "facet_ids": sorted({k for i in coverage_ids for k in byid[i].get("facet_ids", [])}),
                         "note": "仅报告所选覆盖卷实际题目的元数据；不把题库中未入卷的知识点记作覆盖。"},
            "review_scope": "题目数学审核由既有题库及证据文件负责；本工具只校验选题约束和可追溯任务对应。"}


def load_catalogs(paths):
    byid, provenance, view_roots = {}, [], []
    for value in paths:
        path = Path(value).resolve()
        questions = json.loads(path.read_text(encoding="utf-8"))
        validate_catalog(questions)
        validate_review_files(questions, path.parent)
        if any(q.get('paper_only') is True for q in questions):
            view_roots.append(([q for q in questions if q.get('paper_only') is True],path.parent))
        for q in questions:
            if q["id"] in byid and byid[q["id"]]["review"]["content_sha256"] != q["review"]["content_sha256"]:
                raise ValueError("conflicting reviewed content: " + q["id"])
            byid.setdefault(q["id"], q)
        provenance.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "question_count": len(questions), "evidence_verified": True})
    parents = [q for q in byid.values() if q.get('paper_only') is not True]
    for views, root in view_roots:
        validate_paper_view_parents(parents,views)
        validate_paper_view_excerpts(parents,views,root)
    return list(byid.values()), provenance


def validate_binding_evidence(plan, root):
    """Verify independent selection records, including their frozen inputs.

    Hashing a response alone does not establish that its conclusion was positive
    or concerned these questions. Both claims are checked against saved records.
    """
    base, cache = Path(root).resolve(), {}
    candidates = [c for rows in plan.get("task_bindings", {}).values() for c in rows]
    candidates.extend(c for p in plan["papers"] if p["kind"] in ("fitted", "pressure")
                      for s in p.get("slots", []) for c in s.get("candidates", []))

    def objects(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from objects(child)
        elif isinstance(value, list):
            for child in value:
                yield from objects(child)

    for candidate in candidates:
        evidence = candidate.get("match_evidence", {})
        path = (base / evidence.get("path", "")).resolve()
        if not path.is_relative_to(base):
            raise ValueError("selection evidence path escape")
        if evidence.get("engine") == "native_agent":
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != evidence.get("sha256"):
                raise ValueError("native review file hash mismatch")
            review = json.loads(path.read_text(encoding="utf-8"))
            if review.get("schema_version") != "NativeTaskMatchReviewV1" or review.get("engine") != "native_agent":
                raise ValueError("native review schema or engine mismatch")
            reviewer = review.get("reviewer", {})
            if not reviewer.get("id") or reviewer.get("id") != evidence.get("reviewer_id") or reviewer.get("independent_of_authoring") is not True:
                raise ValueError("native reviewer must identify an independent reviewer")
            input_path = (base / evidence.get("input_path", "")).resolve()
            if not input_path.is_relative_to(base) or not input_path.is_file():
                raise ValueError("native input path invalid")
            input_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
            if input_hash != evidence.get("input_sha256") or input_hash != review.get("input_sha256"):
                raise ValueError("native input hash mismatch")
            packet = json.loads(input_path.read_text(encoding="utf-8"))
            fitted = "operation_evidence" in candidate
            kind = "fit" if fitted else "pressure"
            if packet.get("schema_version") != "NativeTaskMatchInputV1" or packet.get("kind") != kind or review.get("kind") != kind:
                raise ValueError("native review kind mismatch")
            ids = _question_ids(candidate)
            fields = ("operation_evidence", "fit_rationale", "condition_changes", "novelty") if fitted else ("obstacle", "obstacle_quote", "blocked_shortcut", "entry")
            rows = [r for r in review.get("reviews", []) if _question_ids(r) == ids]
            if not any((r.get("fit_status") == "matched" and r.get("issues") == [] if fitted else r.get("usable") is True)
                       and all(r.get(k) == candidate.get(k) for k in fields) for r in rows):
                raise ValueError("native matching outcome does not support candidate")
            for qid in ids:
                material = next((m for m in packet.get("materials", []) if m.get("question_id") == qid), {})
                if material.get("content_sha256") != candidate.get("question_hashes", {}).get(qid):
                    raise ValueError("native input does not bind reviewed question content")
                if fitted and (material.get("source_slot_digest") != candidate.get("source_slot_digest")
                               or slot_contract_digest(material.get("source_contract", {})) != candidate.get("source_slot_digest")):
                    raise ValueError("native input does not bind source operations")
            continue
        key = (str(path), evidence.get("sha256"))
        if key not in cache:
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != evidence.get("sha256"):
                raise ValueError("selection evidence file hash mismatch")
            response = json.loads(path.read_text(encoding="utf-8"))
            request = json.loads(path.with_name("request.json").read_text(encoding="utf-8"))
            request_digest = hashlib.sha256(json.dumps({k: v for k, v in request.items() if k != "sha256"},
                                                      ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if request.get("sha256") != request_digest or response.get("request_sha256") != request_digest or response.get("ok") is not True:
                raise ValueError("selection evidence frozen request mismatch")
            cache[key] = response, request
        response, request = cache[key]
        if evidence.get("request_sha256") != response.get("request_sha256"):
            raise ValueError("selection evidence request identity mismatch")
        fitted = "operation_evidence" in candidate
        expected_mode = "independent_fit_task_match" if fitted else "independent_pressure_task_match"
        if response.get("mode") != expected_mode or request.get("mode") != expected_mode:
            raise ValueError("selection evidence is not an independent matching record")
        ids = _question_ids(candidate)
        rows = response.get("payload", {}).get("matches" if fitted else "profiles", [])
        matches = [r for r in rows if _question_ids(r) == ids]
        fields = ("operation_evidence", "fit_rationale", "condition_changes", "novelty") if fitted else ("obstacle", "obstacle_quote", "blocked_shortcut", "entry")
        if not any((r.get("fit_status") == "matched" and r.get("issues") == [] if fitted else r.get("usable") is True)
                   and all(r.get(k) == candidate.get(k) for k in fields) for r in matches):
            raise ValueError("selection matching outcome does not support candidate")
        materials = list(objects(request.get("payload", {})))
        for qid in ids:
            if not any((m.get("id") == qid or isinstance(m.get("candidate"), dict) and m["candidate"].get("id") == qid)
                       and m.get("content_sha256") == candidate.get("question_hashes", {}).get(qid) for m in materials):
                raise ValueError("selection request does not bind reviewed question content")
        if fitted and not any(slot_contract_digest(m) == candidate.get("source_slot_digest") for m in materials if "required_operations" in m):
            raise ValueError("selection request does not bind source operations")
    return True


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", action="append", required=True, help="Accepted catalog.json; evidence paths are relative to its directory. Repeat to merge.")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--source-bank", help="Verified source bank with actual independent subquestion counts.")
    parser.add_argument("--max-search-nodes", type=int, default=20000)
    args = parser.parse_args(argv)
    output = Path(args.out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    try:
        questions, provenance = load_catalogs(args.catalog)
        frozen_plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        if args.source_bank:
            source_path = Path(args.source_bank)
            source_bank = json.loads(source_path.read_text(encoding='utf-8'))
            frozen_plan['source_task_counts'] = {q['id']: q.get('subquestion_count', 1) for q in source_bank['questions']}
            frozen_plan['source_task_count_evidence'] = {'path': str(source_path.resolve()), 'sha256': hashlib.sha256(source_path.read_bytes()).hexdigest()}
        validate_binding_evidence(frozen_plan, Path(args.plan).parent)
        result = assemble(questions, frozen_plan, evidence_verified=True, max_nodes=args.max_search_nodes)
        result["catalog_sources"] = provenance
    except (ValueError, KeyError, OSError, TypeError) as exc:
        result = {"publishable": False, "papers": [], "reference_trials": [], "differences": [],
                  "gaps": [{"code": "input_validation_failed", "message": str(exc)}]}
    _write_json(output / "candidate-papers.json", result["papers"])
    _write_json(output / "reference-trials.json", result["reference_trials"])
    _write_json(output / "gaps.json", result["gaps"])
    _write_json(output / "paper-differences.json", result["differences"])
    _write_json(output / "paper-workload.json", result.get('workload', []))
    _write_json(output / "selection-report.json", {k: v for k, v in result.items() if k not in ("papers", "reference_trials", "differences")})
    published = output / "papers.json"
    if result["publishable"]:
        _write_json(published, result["papers"])
    elif published.exists():
        published.unlink()
    print(json.dumps({"publishable": result["publishable"], "selected_papers": len(result["papers"]), "gap_count": len(result["gaps"]), "output": str(output)}, ensure_ascii=False))
    return 0 if result["publishable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
