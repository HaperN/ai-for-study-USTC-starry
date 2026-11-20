import importlib
import pytest
from aiforstudy.errors import ContractError


def mod():
    assert importlib.util.find_spec(
        "aiforstudy.blocks.content.review_document"
    ), "complete visible review document missing"
    return importlib.import_module("aiforstudy.blocks.content.review_document")


def test_all_visible_fields_and_exact_marker_mapping():
    m = mod()
    raw = "条件 [[CONCEPT:C|变量$x^2$]] 与 [[EXPLAIN:E|解释]]。"
    c = {
        "unit_id": "U",
        "title": "标题",
        "body_markdown": raw,
        "annotations": [{"id": "A", "title": "批注题", "body": "批注"}],
        "examples": [
            {
                "id": "X",
                "basis": "依据",
                "warmup": ["预热"],
                "steps": [{"id": "s", "text": "步骤", "basis": "理由"}],
                "key_points": ["关键"],
                "pitfalls": ["陷阱"],
            }
        ],
        "explainables": [
            {
                "id": "E",
                "what": "是什么",
                "why": "为什么",
                "markdown": "解释正文",
                "warmup": ["热身"],
                "walkthrough": [{"text": "推导", "basis": "依据2"}],
            }
        ],
        "corrections": [{"original": "原文", "corrected": "整理", "reason": "原因"}],
        "summary": "小结",
    }
    d = m.build_review_document(c, {})
    pointers = {b["json_pointer"] for b in d.blocks}
    for path in (
        "/examples/0/basis",
        "/examples/0/warmup/0",
        "/examples/0/steps/0/basis",
        "/examples/0/pitfalls/0",
        "/explainables/0/walkthrough/0/basis",
        "/annotations/0/title",
        "/corrections/0/reason",
        "/summary",
    ):
        assert path in pointers
    b = next(b for b in d.blocks if b["json_pointer"] == "/body_markdown")
    assert b["visible_text"] == "条件 变量$x^2$ 与 解释。"
    start = b['visible_text'].index('变量')
    end = start + len('变量$x^2$')
    assert raw[b['source_map'][start]:b['source_map'][end-1]+1] == '变量$x^2$'


def test_block_identity_survives_array_reorder_and_text_rewrite():
    m = mod()
    c = {
        "unit_id": "U",
        "examples": [{"id": "X", "basis": "a"}, {"id": "Y", "basis": "b"}],
    }
    a = m.build_review_document(c, {})
    c["examples"].reverse()
    c["examples"][1]["basis"] = "new"
    b = m.build_review_document(c, {})
    assert {x["block_id"] for x in a.blocks} == {x["block_id"] for x in b.blocks}




def test_unlabelled_markers_keep_actual_visible_concept_name():
    m = mod()
    raw = "采用[[CONCEPT:C]]。"
    d = m.build_review_document(
        {"unit_id": "U", "body_markdown": raw},
        {"concept_index": {"concepts": [{"id": "C", "name": "向量"}]}},
    )
    b = d.blocks[0]
    assert b["visible_text"] == "采用向量。"
    assert b["marker_spans"][0]["synthetic"] is True


def test_renderer_placeholder_grammar_and_duplicate_label_projection():
    m = mod()
    raw = "这是向量 [[CONCEPT : C | 向量 ]]。[[NOTE : N ]]"
    b = m.build_review_document(
        {"unit_id": "U", "body_markdown": raw},
        {"concept_index": {"concepts": [{"id": "C", "name": "向量"}]}},
    ).blocks[0]
    assert b["visible_text"] == "这是向量。"
    assert b["source_map"][b["visible_text"].index("向量")] == raw.rindex("向量")


def test_review_inventory_covers_renderer_consumed_text_fields():
    import re
    from pathlib import Path

    m = mod()
    root = Path(__file__).parents[1] / "blocks/render/assets"
    renderer = "\n".join((root / f).read_text() for f in ("render-core.js", "app.js"))
    expected = {
        "statement",
        "warmup",
        "basis",
        "conclusion",
        "key_points",
        "pitfalls",
        "name",
        "what",
        "why",
        "claim",
        "markdown",
        "title",
        "one_line",
    }
    assert all(re.search(r"\." + field + r"\b", renderer) for field in expected)
    c = {
        "unit_id": "U",
        "title": "t",
        "one_line": "s",
        "examples": [
            {
                "id": "X",
                "statement": "p",
                "warmup": "w",
                "steps": [{"no": 1, "markdown": "m", "basis": "b"}],
                "conclusion": "c",
                "key_points": ["k"],
                "pitfalls": ["p"],
            }
        ],
        "explainables": [
            {
                "id": "F",
                "name": "n",
                "what": "w",
                "why": "y",
                "markdown": "m",
                "warmup": "w",
                "walkthrough": [{"no": 1, "claim": "q", "basis": "b"}],
            }
        ],
        "annotations": [{"id": "N", "title": "t", "markdown": "m"}],
    }
    pointers = {b["json_pointer"] for b in m.build_review_document(c, {}).blocks}
    assert all(any("/" + f in path for path in pointers) for f in expected)
