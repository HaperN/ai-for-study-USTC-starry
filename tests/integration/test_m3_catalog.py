"""M3 catalog 集成回归：**在真实形状的产物上**跑统一 catalog。

为什么不用现成的合成夹具：`tests/fixtures/synth_book.py` 是为 doctor/交付层造的，
它的 `toc.v2.json` / `body.json` 是**简化形状**（只有节、没有章级单元，锚点会撞）。
拿它测身份层会把"读取方假设过窄"和"数据有问题"混在一起 —— 本次实测就撞上了两处。

所以这里按**真实产物的形状**现造一棵小树（两个键的 body、章/节/小节、未确认节点、
容器节、空缺节点、习题区），并在真实书在位时再对真实产物跑一遍。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiforstudy.blocks.parse import build_catalog
from aiforstudy.blocks.parse.catalog import (SHAPE_SINGLE_KEY, SHAPE_TWO_KEY, detect_shape,
                                             verify_references)

ROOT = Path(__file__).resolve().parents[2]
REAL_BOOK = "complex-analysis-v2"
D2 = "sha256:" + "b" * 64


def _w(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def mini_tree(tmp_path: Path, *, duplicate_content: bool = False) -> Path:
    """一棵**形状真实**的迷你解析树。"""
    parse = tmp_path / "parse"
    toc = {
        "_meta": {"file_type": "toc", "schema_version": "2.0",
                  "pdf_sha256": "pdf-hash", "document_sha256": "doc-hash",
                  "sources_used": ["printed-toc", "body-heading-shapes"]},
        "book_title": "迷你教材",
        "nodes": [
            {"toc_id": "T-0001", "level": 1, "number": "", "title": "总序", "parent": None,
             "order": 1, "kind": "non-teaching", "confirmed": False},
            {"toc_id": "T-0002", "level": 1, "number": "第1章", "title": "第一章 甲",
             "parent": None, "order": 2, "kind": "chapter", "confirmed": True},
            {"toc_id": "T-0003", "level": 2, "number": "1.1", "title": "第一节",
             "parent": "T-0002", "order": 3, "kind": "section", "confirmed": True},
            {"toc_id": "T-0004", "level": 3, "number": "1.1.1", "title": "第一小节",
             "parent": "T-0003", "order": 4, "kind": "subsection", "confirmed": True},
            {"toc_id": "T-0005", "level": 1, "number": "第2章", "title": "第二章 乙",
             "parent": None, "order": 5, "kind": "chapter", "confirmed": True},
            {"toc_id": "T-0006", "level": 2, "number": "2.1", "title": "乙之一节",
             "parent": "T-0005", "order": 6, "kind": "section", "confirmed": True},
            {"toc_id": "T-0007", "level": 1, "number": "第3章", "title": "第三章 空",
             "parent": None, "order": 7, "kind": "chapter", "confirmed": True},
        ],
        "disagreements": [
            {"kind": "body-title", "toc_id": "T-0003", "printed": "1.1 第一节",
             "body": "1.1 第1节", "arbiter": "pending", "why": "两种写法"}],
        "unresolved": [], "suspicious": [], "revisions": [],
    }
    body = {
        "_meta": {"file_type": "body", "schema_version": "2.0", "toc_sha256": "t",
                  "document_sha256": "doc-hash", "source_sha256": "src-hash"},
        "book": {"title": "迷你教材"},
        "units": [
            {"unit_id": "U-0000000001", "body_node": "CH01", "declared": True, "level": "chapter",
             "anchor": "1", "title": "第一章 甲", "toc_node": "T-0002",
             "content_sha256": "c-ch01", "source_lines": [1, 2], "confirmed": True},
            {"unit_id": "U-0000000002", "body_node": "CH01-S01", "declared": True,
             "level": "section", "anchor": "1.1", "title": "第一节", "toc_node": "T-0003",
             "content_sha256": "c-ch01s01", "source_lines": [3, 4], "confirmed": True,
             "content_in_children": True, "covers": ["CH01-S01-SS01"]},
            {"unit_id": "U-0000000003", "body_node": "CH01-S01-SS01", "declared": True,
             "level": "subsection", "anchor": "1.1.1", "title": "第一小节", "toc_node": "T-0004",
             "content_sha256": ("c-ch01s01ss01" if not duplicate_content else "c-dup"),
             "source_lines": [5, 40], "confirmed": True},
            {"unit_id": "U-0000000004", "body_node": "CH02", "declared": True, "level": "chapter",
             "anchor": "2", "title": "第二章 乙", "toc_node": "T-0005",
             "content_sha256": "c-ch02", "source_lines": [41, 42], "confirmed": True},
            {"unit_id": "U-0000000005", "body_node": "CH02-S01", "declared": True,
             "level": "section", "anchor": "2.1", "title": "乙之一节", "toc_node": "T-0006",
             "content_sha256": ("c-ch02s01" if not duplicate_content else "c-dup"),
             "source_lines": [43, 80], "confirmed": True},
        ],
        "undeclared": [
            {"unit_id": "U-0000000006", "body_node": "CH02-S01-SS01", "declared": False,
             "level": "subsection", "anchor": "2.1.1", "title": "乙之小节",
             "toc_node": "T-0006", "parent_body_node": "CH02-S01",
             "content_sha256": "c-ch02s01ss01", "source_lines": [81, 120], "confirmed": True},
        ],
        "empty_nodes": [{"body_node": "CH03", "toc_node": "T-0007",
                         "why": "目录声明了第3章，正文里没有内容"}],
        "orphans": [], "content_overlaps": [], "anchor_conflicts": [], "unmatched_titles": [],
        "unconfirmed": [{"toc_node": "T-0001", "title": "总序",
                         "why": "目录层标了 confirmed:false"}],
    }
    chapters = {
        "_meta": {"file_type": "chapters", "schema_version": "2.0"},
        "book": {"title": "迷你教材"},
        "chapters": [{"id": "CH01", "title": "第一章 甲"}, {"id": "CH02", "title": "第二章 乙"}],
        "lesson_units": [
            {"unit_id": "CH01", "level": "chapter_intro", "chapter": "CH01", "title": "第一章 甲"},
            {"unit_id": "CH01-S01-SS01", "level": "subsection", "chapter": "CH01",
             "title": "第一小节"},
            {"unit_id": "CH02", "level": "chapter_intro", "chapter": "CH02", "title": "第二章 乙"},
            {"unit_id": "CH02-S01", "level": "section", "chapter": "CH02", "title": "乙之一节"},
        ],
    }
    exercises = {
        "_meta": {"file_type": "exercises", "schema_version": "1.0"},
        "entries": [
            {"id": "EX-0001", "kind": "exercise", "chapter": "第1章", "number": "1",
             "source_lines": [30, 32], "text": "落在 CH01-S01-SS01 区间内"},
            {"id": "EX-0002", "kind": "exercise", "chapter": "第2章", "number": "2",
             "source_lines": [130, 132], "text": "全书单元区间之后 → 同章最近前一个"},
            {"id": "EX-0003", "kind": "exercise", "chapter": "第1章", "number": "3",
             "source_lines": [95, 97],
             "text": "声明的章与源坐标的章**矛盾**（落在 CH02-S01-SS01 的区间里）"},
        ],
    }
    _w(parse / "toc.v2.json", toc)
    _w(parse / "body.json", body)
    _w(parse / "chapters.json", chapters)
    _w(parse / "exercises.json", exercises)
    (parse / "original_text").mkdir(parents=True, exist_ok=True)
    (parse / "original_text" / "CH01.md").write_text("甲", encoding="utf-8")
    return parse


# ────────────────────────────────────────── 形状识别

def test_both_body_shapes_are_recognised():
    two = {"units": [{"unit_id": "U-fb3378b472", "body_node": "CH01-S01"}]}
    single = {"units": [{"unit_id": "CH01-S01"}]}
    assert detect_shape(two) == SHAPE_TWO_KEY
    assert detect_shape(single) == SHAPE_SINGLE_KEY


def test_two_key_shape_keeps_both_identities(tmp_path):
    parse = mini_tree(tmp_path)
    catalog, details = build_catalog(parse, book_id="mini", revision="parse-v2")
    assert details["shape"] == SHAPE_TWO_KEY
    ref = catalog.by_key()["CH01-S01-SS01"]
    assert ref.content_unit_id == "U-0000000003"
    assert ref.content_sha256 == "c-ch01s01ss01"
    assert ref.legacy_aliases == ("U-0000000003",)
    # 旧 U 号也能解析回来（审计工单/旧概念表就是按它写的）
    assert catalog.resolve("U-0000000003").unit_key == "CH01-S01-SS01"


# ────────────────────────────────────────── 分类与排除

def test_container_section_is_classified_not_treated_as_lost_content(tmp_path):
    """容器节（内容在子节点里）不是"丢内容"，它有理由、有自己的角色。"""
    catalog, details = build_catalog(mini_tree(tmp_path), book_id="mini", revision="parse-v2")
    container = catalog.by_key()["CH01-S01"]
    assert container.teaching_role == "container"
    assert "容器节点" in details["exclusions"]["CH01-S01"]


def test_empty_and_non_teaching_nodes_are_each_explained(tmp_path):
    catalog, details = build_catalog(mini_tree(tmp_path), book_id="mini", revision="parse-v2")
    exclusions = details["exclusions"]
    assert any("第3章" in v or "空缺" in v or "正文里没有内容" in v for v in exclusions.values())
    assert any("非教学" in v for v in exclusions.values())
    assert any("未确认" in v for v in exclusions.values())
    assert catalog.verify()["ok"] is True, catalog.verify()["problems"]


def test_duplicate_content_becomes_an_ambiguity_that_blocks_migration(tmp_path):
    from aiforstudy.core.identity import AmbiguityBlocked
    catalog, details = build_catalog(mini_tree(tmp_path, duplicate_content=True),
                                     book_id="mini", revision="parse-v2")
    assert details["verify"]["counts"]["ambiguities"] >= 1
    assert catalog.blocks_release
    with pytest.raises(AmbiguityBlocked):
        catalog.assert_migratable()


# ────────────────────────────────────────── 对账

def test_lesson_units_are_reconciled_item_by_item(tmp_path):
    catalog, details = build_catalog(mini_tree(tmp_path), book_id="mini", revision="parse-v2")
    recon = details["reconciliation"]
    assert recon["lesson_units"] == 4 and len(recon["matched"]) == 4
    assert recon["unmatched"] == []
    # CH01-S01 是容器节，没有自己的 lesson_unit —— 它必须出现在 catalog_only 里并带角色
    only = {row["unit_key"]: row for row in recon["catalog_only"]}
    assert "CH01-S01" in only and only["CH01-S01"]["role"] == "container"


def test_unmatched_lesson_unit_is_reported_not_ignored(tmp_path):
    parse = mini_tree(tmp_path)
    chapters = json.loads((parse / "chapters.json").read_text(encoding="utf-8"))
    chapters["lesson_units"].append({"unit_id": "CH99-S01", "level": "section",
                                     "chapter": "CH99", "title": "不存在的一节"})
    _w(parse / "chapters.json", chapters)
    _catalog, details = build_catalog(parse, book_id="mini", revision="parse-v2")
    recon = details["reconciliation"]
    assert len(recon["unmatched"]) == 1
    assert recon["unmatched"][0]["lesson_unit"] == "CH99-S01"
    assert recon["consistent"] is False


# ────────────────────────────────────────── 复核

def test_missing_review_blocks_release_but_not_migration(tmp_path):
    catalog, details = build_catalog(mini_tree(tmp_path), book_id="mini", revision="parse-v2")
    assert details["review"]["artifact_present"] is False
    assert details["review"]["stale"] is True
    assert any(n.kind == "toc-arbitration" for n in catalog.needs_review)
    assert any(n.kind == "missing-review" for n in catalog.needs_review)
    assert catalog.blocks_release is True
    catalog.assert_migratable()          # 身份是清楚的，迁移不阻断


def test_review_is_applied_only_when_the_inputs_match(tmp_path):
    from aiforstudy.blocks.parse import review as R
    parse = mini_tree(tmp_path)
    expected = R.expected_inputs_digest(parse)

    # 输入对得上 → 应用
    _w(parse / "toc.review.json", {
        "inputs_digest": expected,
        "decisions": [{"subject": "T-0003", "verdict": "accept-printed",
                       "reason": "印刷目录更可信"}]})
    _catalog, details = build_catalog(parse, book_id="mini", revision="parse-v2")
    assert details["review"]["stale"] is False and details["review"]["applied"] == 1

    # 换一份输入指纹（等价于"改来源截图"）→ 拒绝，一条都不应用
    _w(parse / "toc.review.json", {
        "inputs_digest": "sha256:" + "c" * 64,
        "decisions": [{"subject": "T-0003", "verdict": "accept-printed",
                       "reason": "印刷目录更可信"}]})
    _catalog, details = build_catalog(parse, book_id="mini", revision="parse-v2")
    assert details["review"]["stale"] is True and details["review"]["applied"] == 0
    assert "不可消费旧决策" in details["review"]["reason"]


def test_review_inputs_digest_changes_when_the_draft_changes(tmp_path):
    from aiforstudy.blocks.parse import review as R
    parse = mini_tree(tmp_path)
    before = R.expected_inputs_digest(parse)
    _w(parse / "toc.draft.json", {"nodes": [{"toc_id": "T-0001"}]})
    assert R.expected_inputs_digest(parse) != before


# ────────────────────────────────────────── 真实产物（在位时才跑）

REAL_PARSE = ROOT / "data" / "parse" / REAL_BOOK
REAL_LESSONS = ROOT / "data" / "lesson" / REAL_BOOK


@pytest.mark.skipif(not (REAL_PARSE / "chapters.json").is_file(),
                    reason="真实解析产物不在位（已归档）")
def test_real_book_catalog_is_self_consistent():
    catalog, details = build_catalog(REAL_PARSE, book_id=REAL_BOOK, revision="parse-v2")
    assert catalog.verify()["ok"] is True, catalog.verify()["problems"]
    counts = details["verify"]["counts"]
    assert counts["units"] == 80 and counts["toc_nodes"] == 47
    # 身份是干净的：没有未裁决歧义
    assert counts["ambiguities"] == 0


@pytest.mark.skipif(not (REAL_PARSE / "chapters.json").is_file(),
                    reason="真实解析产物不在位（已归档）")
def test_real_book_lesson_units_all_reconcile():
    _catalog, details = build_catalog(REAL_PARSE, book_id=REAL_BOOK, revision="parse-v2")
    recon = details["reconciliation"]
    assert recon["lesson_units"] == 76 and len(recon["matched"]) == 76
    assert recon["unmatched"] == []


@pytest.mark.skipif(not (REAL_PARSE / "chapters.json").is_file(),
                    reason="真实解析产物不在位（已归档）")
def test_real_book_every_reference_resolves(tmp_path):
    """§14 M3：**CH/U 谱系与原文坐标可对账**。"""
    catalog, _details = build_catalog(REAL_PARSE, book_id=REAL_BOOK, revision="parse-v2")
    # 教案属于可清理的生成产物；用保留的真实单元名单生成仅含引用的本次夹具。
    # 文件名验证 CH 谱系，内容验证对应 U 谱系，避免依赖已删除的旧课程正文。
    lessons = tmp_path / 'data' / 'lesson' / REAL_BOOK
    audit = tmp_path / 'data' / 'audit' / REAL_BOOK
    units = json.loads((REAL_PARSE / 'chapters.json').read_text('utf-8'))['lesson_units']
    assert len(units) == 76
    for row in units:
        key = row['unit_id']
        ref = catalog.resolve(key)
        assert ref is not None, key
        _w(lessons / 'lessons' / f'{key}.json', {'unit_id': ref.content_unit_id})
        _w(audit / f'{key}.audit.json', {'unit_id': key})
    _w(audit / '原文疑似有错.json', {'summary': []})
    refs = verify_references(REAL_PARSE, lessons, catalog)
    assert refs["ok"] is True, refs["missing"][:5]
    assert refs["concept_occurrences"] > 900 and refs["lesson_files"] == 76
    assert refs['audit_files'] == 76
    assert refs["non_unit_files"] == ['原文疑似有错.json']
    # 故意破坏一份正文内引用：证明检查实际读了新夹具，而非只数文件名。
    _w(lessons / 'lessons' / f"{units[0]['unit_id']}.json", {'unit_id': 'U-fffffffffe'})
    broken = verify_references(REAL_PARSE, lessons, catalog)
    assert broken['ok'] is False
    assert broken['missing'] == [{'kind': 'lesson-unit_id', 'token': 'U-fffffffffe',
                                  'where': f"{units[0]['unit_id']}.json"}]
