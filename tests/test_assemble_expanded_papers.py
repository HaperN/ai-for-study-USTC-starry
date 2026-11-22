"""Publication gates for the standalone expanded-paper selector."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aiforstudy.practice.catalog import content_digest


def question(ident, unit="CH01-S01", *, stem=None, basic=True):
    q = {"id": ident, "title": ident, "stem": stem or f"判断{ident}中的函数是否解析。",
         "answer": "满足所述条件。", "solution": f"先检查{ident}的定义域。再用柯西黎曼方程验证。",
         "topic": "解析性", "unit_id": unit, "priority": "必会", "priority_reason": "核心条件训练",
         "difficulty": 3, "basic": basic, "methods": ["条件检查"], "pitfalls": ["漏定义域"],
         "knowledge_ids": [unit + "-C1"], "task_kind": "条件辨析", "training_goal": "条件检查",
         "source": {"kind": "generated_variant", "course": "复变函数A"}}
    q["review"] = {"status": "accepted", "content_sha256": content_digest(q), "author": "fixture-author",
                   "blind_solver": "fixture-solver", "blind_solution_sha256": "b" * 64,
                   "comparison": "fixture-only", "issues": []}
    return q


def paper(ident, kind="coverage", slots=None, **kw):
    return {"id": ident, "kind": kind, "edition": "full", "title": ident, "purpose": "检查训练目标",
            "slots": slots or [{"id": "s1", "points": 10, "selector": {"unit_prefixes": ["CH01"]}}], **kw}


def plan(papers, **kw):
    return {"schema_version": "ExpandedPaperPlanV1", "papers": papers,
            "target_counts": {k: sum(p["kind"] == k for p in papers) for k in ("coverage", "fitted", "pressure")},
            "basic_target_counts": {k: sum(p["kind"] == k and p["edition"] == "basic" for p in papers)
                                    for k in ("coverage", "fitted", "pressure")},
            "max_same_kind_jaccard": 0.35, "reference_trials": [], **kw}


class ExpandedPaperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "tools/assemble_expanded_papers.py"
        if not path.exists():
            raise AssertionError("expanded-paper selector has not been implemented")
        spec = importlib.util.spec_from_file_location("expanded_papers", path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_changed_reviewed_content_is_rejected_before_selection(self):
        q = question("one"); q["solution"] = "Changed after review"
        with self.assertRaisesRegex(ValueError, "digest"):
            self.module.assemble([q], plan([paper("p")]), evidence_verified=True)

    def test_dry_selection_cannot_be_published_without_evidence_files(self):
        result = self.module.assemble([question("one")], plan([paper("p")]))
        self.assertFalse(result["publishable"])
        self.assertIn("evidence_not_verified", {g["code"] for g in result["gaps"]})

    def test_basic_selector_does_not_silently_take_full_only_question(self):
        p = paper("p"); p["edition"] = "basic"
        result = self.module.assemble([question("one", basic=False)], plan([p]), evidence_verified=True)
        self.assertFalse(result["publishable"])
        self.assertIn("slot_has_no_candidates", {g["code"] for g in result["gaps"]})

    def test_selection_is_deterministic_and_avoids_same_kind_copies(self):
        qs = [question(x) for x in ["alpha", "beta", "gamma", "delta"]]
        slots = [{"id": "a", "points": 5, "selector": {"unit_prefixes": ["CH01"]}},
                 {"id": "b", "points": 5, "selector": {"unit_prefixes": ["CH01"]}}]
        params = plan([paper("p1", slots=slots), paper("p2", slots=slots)])
        one = self.module.assemble(qs, params, evidence_verified=True)
        two = self.module.assemble(list(reversed(qs)), params, evidence_verified=True)
        self.assertTrue(one["publishable"], one["gaps"])
        self.assertEqual(one["papers"], two["papers"])
        ids = [set(i["question_id"] for i in p["items"]) for p in one["papers"]]
        self.assertFalse(ids[0] & ids[1])

    def test_renaming_identical_stems_does_not_evade_overlap_gate(self):
        qs = [question("one", stem="计算同一积分。"), question("two", stem="计算同一积分。")]
        params = plan([paper("p1"), paper("p2")])
        result = self.module.assemble(qs, params, evidence_verified=True)
        self.assertFalse(result["publishable"])

    def test_changing_math_delimiters_does_not_create_another_paper(self):
        qs = [question("one", stem=r"求 $\left(z+1\right)^2$ 的零点。"),
              question("two", stem=r"求 \((z + 1)^2\) 的零点。")]
        result = self.module.assemble(qs, plan([paper("p1"), paper("p2")]), evidence_verified=True)
        self.assertFalse(result["publishable"])

    def test_coverage_prefers_untrained_knowledge_when_usage_is_equal(self):
        a, b, c = [question(x) for x in ("anchor", "bravo", "charlie")]
        a["knowledge_ids"], b["knowledge_ids"], c["knowledge_ids"] = ["known"], ["new"], ["known"]
        fixed = {"id": "fixed", "kind": "coverage", "edition": "full", "title": "fixed", "purpose": "old",
                 "items": [{"question_id": "anchor", "points": 10}], "publication_status": "formal_training"}
        specs = [paper("fixed", fixed_paper=fixed),
                 paper("next", slots=[{"id": "s", "points": 10, "selector": {"ids": ["bravo", "charlie"]}}])]
        result = self.module.assemble([a, b, c], plan(specs), evidence_verified=True)
        self.assertTrue(result["publishable"])
        self.assertEqual(result["papers"][1]["items"][0]["question_id"], "bravo")

    def test_source_count_overrides_incorrect_single_question_mapping(self):
        q = question("bundle"); q.update(source_task_id="TB-BUNDLE", subquestion_count=1)
        params = plan([paper("coverage")], source_task_counts={"TB-BUNDLE": 10})
        result = self.module.assemble([q], params, evidence_verified=True)
        self.assertFalse(result["publishable"])
        self.assertIn("oversized_independent_bundle", {g["code"] for g in result["gaps"]})

    def test_coverage_does_not_prefer_a_longer_bundle_just_for_more_labels(self):
        large, focused = question('many'), question('focused')
        large.update(subquestion_count=4, knowledge_ids=['K1', 'K2', 'K3', 'K4'])
        focused.update(subquestion_count=1, knowledge_ids=['K1'])
        result = self.module.assemble([large, focused], plan([paper('p')]), evidence_verified=True)
        self.assertTrue(result['publishable'])
        self.assertEqual(result['papers'][0]['items'][0]['question_id'], 'focused')

    def test_parent_view_conflict_is_rejected_but_distinct_siblings_can_share(self):
        parent = question("parent")
        children = []
        for number in (6, 8):
            child = question(f"view{number}")
            child.update(paper_only=True, source_parent_question_id="parent",
                         parent_content_sha256=parent["review"]["content_sha256"],
                         subquestion_count=1, source_subquestion_numbers=[number], new_source_materials=0)
            child["source"].update(source_parent_question_id="parent", parent_content_sha256=child["parent_content_sha256"])
            child["review"]["content_sha256"] = content_digest(child)
            children.append(child)
        qs = [parent, *children]
        slots = [{"id": "a", "points": 5, "selector": {"ids": ["parent"]}},
                 {"id": "b", "points": 5, "selector": {"ids": ["view6"]}}]
        self.assertFalse(self.module.assemble(qs, plan([paper("p", slots=slots)]), evidence_verified=True)["publishable"])
        slots[0]["selector"]["ids"] = ["view8"]
        result = self.module.assemble(qs, plan([paper("p", slots=slots)]), evidence_verified=True)
        self.assertTrue(result["publishable"], result["gaps"])
        self.assertEqual({i['question_id'] for i in result['papers'][0]['items']}, {'view6', 'view8'})

    def test_workload_reports_source_subquestions_points_and_estimate(self):
        q = question("three"); q.update(source_task_id="TB-THREE", subquestion_count=1)
        result = self.module.assemble([q], plan([paper("p")], source_task_counts={"TB-THREE": 3}), evidence_verified=True)
        workload = result["workload"][0]
        self.assertEqual(workload["subquestion_count"], 3)
        self.assertEqual(workload["items"][0]["points"], 10)
        self.assertEqual(workload["items"][0]["subquestion_count"], 3)
        self.assertGreater(workload["estimated_minutes"], 0)
        self.assertFalse(workload["student_timing_measured"])

    def test_related_unit_counts_only_for_current_evidenced_question(self):
        from aiforstudy.practice.coverage import statement_digest
        q=question('shared',unit='CH07-S01')
        q['additional_mounts']=[{'unit_id':'CH07','coverage_evidence':{'statement_sha256':statement_digest(q)}}]
        spec=paper('p',slots=[{'id':'s','points':10,'selector':{'unit_ids':['CH07']}}],required_units=['CH07','CH07-S01'])
        params=plan([spec],required_coverage_units=['CH07','CH07-S01'])
        result=self.module.assemble([q],params,evidence_verified=True)
        self.assertTrue(result['publishable'],result['gaps'])
        self.assertEqual(result['coverage']['question_count'],1)
        q['additional_mounts'][0]['coverage_evidence']['statement_sha256']='0'*64
        result=self.module.assemble([q],params,evidence_verified=True)
        self.assertFalse(result['publishable'])

    def test_fitted_same_unit_and_score_without_task_evidence_is_blocked(self):
        slot = {"id": "source-slot", "points": 10, "section": "计算", "selector": {"unit_prefixes": ["CH01"]},
                "source": {"pdf_page": 11, "source_quote": "原题任务"},
                "required_operations": ["恢复导数", "积分并使用第二初值"], "points_basis": "original_explicit"}
        p = paper("fit", "fitted", [slot], reference={"year": "2023-2024", "physical_pages": [11]})
        result = self.module.assemble([question("one")], plan([p]), evidence_verified=True)
        self.assertFalse(result["publishable"])
        self.assertIn("fitted_task_evidence_missing", {g["code"] for g in result["gaps"]})

    def test_original_atomic_subquestions_stay_in_one_numbered_slot(self):
        qs = [question("one"), question("two")]
        for n, q in enumerate(qs):
            q["source"] = {"kind": "past_exam", "course": "复变函数A", "pdf_page": 11,
                           "question_number": "1(1)" + str(n), "source_quote": q["stem"]}
            q["review"]["content_sha256"] = content_digest(q)
        slot = {"id": "A2023-Q1-1", "points": 5, "section": "基础知识",
                "points_basis": "training_split_with_source_total", "required_operations": ["两子问"],
                "source_questions": [{"id": q["id"], "stem": q["stem"], "pdf_page": 11, "points": score}
                                     for q, score in zip(qs, [2, 3])]}
        p = paper("fit", "fitted", [slot], reference={"year": "2023-2024", "physical_pages": [11], "section_points": {"基础知识": 5}})
        result = self.module.assemble(qs, plan([p]), evidence_verified=True)
        self.assertTrue(result["publishable"], result["gaps"])
        self.assertEqual(result["papers"][0]["numbered_slot_count"], 1)
        self.assertEqual(len(result["papers"][0]["items"]), 2)
        self.assertEqual(result["papers"][0]["total_points"], 5)
        self.assertEqual([i["points"] for i in result["papers"][0]["items"]], [2, 3])

    def test_pressure_entry_must_quote_the_selected_reviewed_solution(self):
        q = question("one")
        binding = {"question_ids": ["one"], "question_hashes": {"one": q["review"]["content_sha256"]},
                   "obstacle": "需先确认定义域", "entry": {"question_id": "one", "solution_quote": "不存在的步骤", "points": 2},
                   "blocked_shortcut": "不可忽略定义域"}
        slot = {"id": "s", "points": 10, "candidates": [binding]}
        p = paper("pressure", "pressure", [slot], obstacle="定义域与选法")
        result = self.module.assemble([q], plan([p]), evidence_verified=True)
        self.assertFalse(result["publishable"])
        self.assertIn("pressure_entry_invalid", {g["code"] for g in result["gaps"]})

    def test_private_source_marker_cannot_bypass_task_evidence(self):
        slot = {"id": "s", "points": 10, "section": "基础", "source": {"pdf_page": 11, "source_quote": "源题"},
                "points_basis": "original_explicit", "required_operations": ["先重构再积分"],
                "candidates": [{"question_ids": ["one"], "_original_source": True}]}
        p = paper("fit", "fitted", [slot], reference={"year": "2023-2024", "section_points": {"基础": 10}})
        result = self.module.assemble([question("one")], plan([p]), evidence_verified=True)
        self.assertFalse(result["publishable"])

    def test_shared_pressure_pool_still_obeys_unit_constraints(self):
        q = question("one")
        binding = {"question_ids": ["one"], "question_hashes": {"one": q["review"]["content_sha256"]},
                   "obstacle": "先检查定义域", "blocked_shortcut": "不可直接套公式",
                   "entry": {"question_id": "one", "solution_quote": "先检查one的定义域。", "points": 2}}
        p = paper("pressure", "pressure", [{"id": "s", "binding_pool": "pressure", "points": 10,
                    "selector": {"unit_ids": ["CH01-S01"]}}], obstacle="先确认条件")
        result = self.module.assemble([q], plan([p], task_bindings={"pressure": [binding]}), evidence_verified=True)
        self.assertTrue(result["publishable"], result["gaps"])

    def test_global_coverage_cannot_be_claimed_by_unselected_catalog_items(self):
        qs = [question("one"), question("two", unit="CH02-S01")]
        result = self.module.assemble(qs, plan([paper("p")], required_coverage_units=["CH01-S01", "CH02-S01"]), evidence_verified=True)
        self.assertFalse(result["publishable"])
        self.assertIn("required_unit_uncovered", {g["code"] for g in result["gaps"]})

    def test_fit_binding_cannot_survive_changed_source_task_contract(self):
        q = question("one")
        slot = {"id": "s", "source_slot_id": "A2023-Q1-2", "section": "基础", "points": 10,
                "points_basis": "original_explicit", "source": {"pdf_page": 11, "source_quote": "恢复实部"},
                "required_operations": ["用CR恢复实部"]}
        candidate = {"question_ids": ["one"], "question_hashes": {"one": q["review"]["content_sha256"]},
                     "match_status": "matched", "source_slot_digest": self.module.slot_contract_digest(slot),
                     "fit_rationale": "完整保留CR操作", "condition_changes": ["更换解析分量"],
                     "operation_evidence": [{"operation_index": 0, "question_id": "one", "field": "solution",
                                             "quote": "再用柯西黎曼方程验证。", "reason": "使用CR方程"}]}
        slot["candidates"] = [candidate]
        p = paper("fit", "fitted", [slot], reference={"year": "2023-2024", "section_points": {"基础": 10}})
        self.assertTrue(self.module.assemble([q], plan([p]), evidence_verified=True)["publishable"])
        slot["required_operations"] = ["恢复导数后再次积分并定第二常数"]
        result = self.module.assemble([q], plan([p]), evidence_verified=True)
        self.assertFalse(result["publishable"])
        self.assertIn("fitted_source_binding_invalid", {g["code"] for g in result["gaps"]})
        slot["required_operations"] = ["用CR恢复实部"]
        slot["minimum_preserved_conditions"] = ["增加新的非退化条件"]
        self.assertFalse(self.module.assemble([q], plan([p]), evidence_verified=True)["publishable"])

    def test_selection_evidence_checks_record_outcome_and_file_hash(self):
        q = question("one")
        profile = {"question_id": "one", "usable": True, "reason": "条件检查",
                   "obstacle": "定义域影响选法", "obstacle_quote": q["stem"], "blocked_shortcut": "不能忽略定义域",
                   "entry": {"question_id": "one", "solution_quote": "先检查one的定义域。", "points": 2}}
        request = {"mode": "independent_pressure_task_match", "instruction": "fixture",
                   "payload": {"pressure": [{"id": "one", "content_sha256": q["review"]["content_sha256"]}]}}
        request["sha256"] = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        response = {"ok": True, "mode": request["mode"], "request_sha256": request["sha256"], "payload": {"profiles": [profile]}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "request.json").write_text(json.dumps(request))
            path = root / "response.json"; path.write_text(json.dumps(response))
            binding = {k: v for k, v in profile.items() if k not in ("question_id", "usable", "reason")}
            binding.update(question_ids=["one"], question_hashes={"one": q["review"]["content_sha256"]},
                           match_evidence={"path": "response.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                           "request_sha256": request["sha256"]})
            p = plan([paper("p")], task_bindings={"pressure": [binding]})
            self.module.validate_binding_evidence(p, root)
            response["payload"]["profiles"][0]["usable"] = False
            path.write_text(json.dumps(response))
            with self.assertRaisesRegex(ValueError, "hash"):
                self.module.validate_binding_evidence(p, root)
            binding["match_evidence"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "matching outcome"):
                self.module.validate_binding_evidence(p, root)

    def test_native_review_uses_its_own_schema_and_frozen_input(self):
        q = question("one")
        profile = {"question_id": "one", "usable": True, "obstacle": "需要判断定义域",
                   "obstacle_quote": q["stem"], "blocked_shortcut": "不能直接套公式",
                   "entry": {"question_id": "one", "solution_quote": "先检查one的定义域。", "points": 2}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packet = {"schema_version": "NativeTaskMatchInputV1", "kind": "pressure",
                      "materials": [{"question_id": "one", "content_sha256": q["review"]["content_sha256"],
                                     "stem": q["stem"], "solution": q["solution"]}]}
            ip = root / "input.json"; ip.write_text(json.dumps(packet))
            ih = hashlib.sha256(ip.read_bytes()).hexdigest()
            review = {"schema_version": "NativeTaskMatchReviewV1", "engine": "native_agent", "kind": "pressure",
                      "reviewer": {"id": "fixture-independent-reviewer", "independent_of_authoring": True},
                      "input_sha256": ih, "reviews": [profile]}
            rp = root / "native-review.json"; rp.write_text(json.dumps(review))
            binding = {k: v for k, v in profile.items() if k not in ("question_id", "usable")}
            binding.update(question_ids=["one"], question_hashes={"one": q["review"]["content_sha256"]},
                           match_evidence={"engine": "native_agent", "path": "native-review.json",
                                           "sha256": hashlib.sha256(rp.read_bytes()).hexdigest(),
                                           "input_path": "input.json", "input_sha256": ih,
                                           "reviewer_id": "fixture-independent-reviewer"})
            p = plan([paper("p")], task_bindings={"pressure": [binding]})
            self.module.validate_binding_evidence(p, root)
            packet["materials"][0]["solution"] = "Changed input after review"
            ip.write_text(json.dumps(packet))
            with self.assertRaisesRegex(ValueError, "native input hash"):
                self.module.validate_binding_evidence(p, root)

    def test_native_review_cannot_claim_a_different_engine_or_self_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packet = {"schema_version": "NativeTaskMatchInputV1", "kind": "pressure", "materials": []}
            ip = root / "input.json"; ip.write_text(json.dumps(packet)); ih = hashlib.sha256(ip.read_bytes()).hexdigest()
            review = {"schema_version": "NativeTaskMatchReviewV1", "engine": "external_model", "kind": "pressure",
                      "reviewer": {"id": "fixture-reviewer", "independent_of_authoring": True},
                      "input_sha256": ih, "reviews": []}
            rp = root / "native-review.json"; rp.write_text(json.dumps(review))
            binding = {"question_ids": ["one"], "match_evidence": {"engine": "native_agent", "path": rp.name,
                       "sha256": hashlib.sha256(rp.read_bytes()).hexdigest(), "input_path": ip.name, "input_sha256": ih,
                       "reviewer_id": "fixture-reviewer"}}
            p = plan([paper("p")], task_bindings={"pressure": [binding]})
            with self.assertRaisesRegex(ValueError, "native review"):
                self.module.validate_binding_evidence(p, root)
            review["engine"] = "native_agent"; review["reviewer"]["independent_of_authoring"] = False
            rp.write_text(json.dumps(review)); binding["match_evidence"]["sha256"] = hashlib.sha256(rp.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "independent"):
                self.module.validate_binding_evidence(p, root)


if __name__ == "__main__":
    unittest.main()
