"""Historical page scope remains readable; chapter writers cannot reconcile old approvals."""
import json
from lib.scopes import scope_of
from support.retired_cli import assert_retired_cli

def test_page_scope_keeps_existing_sections_when_topping_up_one(tmp_path):
    """补跑一节，不能把页面上其它节挤掉 —— 这正是"4 节变 1 节"那个 bug。"""
    (tmp_path / "render.index.json").write_text(
        json.dumps({"scope": ["A", "B", "C"]}), encoding="utf-8")
    assert scope_of(tmp_path, ["D"]) == ["A", "B", "C", "D"]

def test_page_scope_prefers_the_planned_record(tmp_path):
    """`范围.json` 是"本来要做哪几节"的正式记录，剔除操作不动它 → 以它为准。"""
    (tmp_path / "范围.json").write_text(
        json.dumps({"原本要做": ["A", "B", "C", "D"]}), encoding="utf-8")
    (tmp_path / "render.index.json").write_text(
        json.dumps({"scope": ["A"]}), encoding="utf-8")   # 上一趟被收窄过
    assert scope_of(tmp_path, ["B"]) == ["A", "B", "C", "D"]

def test_page_scope_no_duplicate_when_rerunning_a_section_already_on_page(tmp_path):
    (tmp_path / "render.index.json").write_text(
        json.dumps({"scope": ["A", "B"]}), encoding="utf-8")
    assert scope_of(tmp_path, ["B"]) == ["A", "B"]

def test_page_scope_falls_back_to_what_ran(tmp_path):
    """全新的一页：没有记录，范围就是这一趟跑的节。"""
    assert scope_of(tmp_path, ["A", "B"]) == ["A", "B"]

def test_retired_chapter_writer_cannot_merge_or_revalidate_snapshots(tmp_path):
    assert_retired_cli(tmp_path,'run_chapter')
