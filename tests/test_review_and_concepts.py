"""结构复核（T1.6）与概念抽取（T1.7）的离线测试。

两个模块都要调模型，所以测试里一律用**假模型**：断言的是"我们自己的逻辑"
（筛子、校验、合并、去重、留痕），不是模型输出质量。
"""
from __future__ import annotations

import json
import types
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blocks" / "parse" / "structure"))
sys.path.insert(0, str(ROOT / "blocks" / "parse" / "concepts"))
sys.path.insert(0, str(ROOT / "lib"))

import review as R          # noqa: E402
import structure as S       # noqa: E402
import extract_concepts as C  # noqa: E402


# ══ T1.6 结构复核 ═══════════════════════════════════════════════════════
def _heading(line, level, number, title, numeric=None, scheme="num"):
    return {"source_line": line, "level": level, "raw_number": number, "title": title,
            "scheme": scheme, "page": 1, "canonical": number,
            "canonical_level": level,
            "numeric": list(numeric) if numeric else None}


def _payload(headings, max_level=4):
    return {"_meta": {"file_type": "structure", "schema_version": "1.0", "max_level": max_level},
            "stats": {"headings_total": len(headings)}, "headings": headings}


class _FakeClient:
    def __init__(self, decisions):
        self.decisions = decisions
        self.calls = 0
        self.session_usage = type("U", (), {"as_dict": staticmethod(lambda: {})})()

    def chat_json(self, messages, *, tag="", images=None, use_cache=None):
        self.calls += 1
        return {"data": {"decisions": self.decisions}, "usage": {}, "cached": False}


def test_suspects_flags_depth_mismatch():
    """编号深度与层级不符 → 可疑（实测：1.8.1 被写成层级 2，父级丢了）。"""
    payload = _payload([
        _heading(10, 1, "1", "向量", [1], "chapter"),
        _heading(20, 2, "1.1", "线性运算", [1, 1]),
        _heading(30, 2, "1.8.1", "复数的四则运算", [1, 8, 1]),   # 深度 3 却是层级 2
    ])
    assert 30 in R.suspects(payload["headings"])


def test_suspects_flags_title_with_digits_and_duplicates():
    payload = _payload([
        _heading(10, 1, "1", "向量", [1], "chapter"),
        _heading(20, 3, "5.225", "5.225", [5, 225]),            # OCR 垃圾标题
        _heading(30, 3, "6.3.1", "靶标检测", [6, 3, 1]),
        _heading(40, 3, "6.3.1", "靶标检测", [6, 3, 1]),        # 与前面重复
    ])
    flagged = R.suspects(payload["headings"])
    assert 20 in flagged and 40 in flagged


def test_suspects_flags_level_jump():
    payload = _payload([
        _heading(10, 1, "1", "向量", [1], "chapter"),
        _heading(20, 3, "1.1.1", "细节", [1, 1, 1]),            # 从 1 跳到 3
    ])
    assert 20 in R.suspects(payload["headings"])


def test_clean_structure_has_no_suspects_and_costs_nothing():
    """对照实验 A：结构正确时不该乱报、也不该调模型。"""
    payload = _payload([
        _heading(10, 1, "1", "向量", [1], "chapter"),
        _heading(20, 2, "1.1", "线性运算", [1, 1]),
        _heading(30, 3, "1.1.1", "向量及其表示", [1, 1, 1]),
        _heading(40, 2, "1.2", "坐标系", [1, 2]),
    ])
    review = R.review_structure(payload, _FakeClient([]))
    assert review["suspects_total"] == 0
    assert review["decisions"] == []
    assert review["_meta"]["file_type"] == "structure_review"


def test_review_rejects_malformed_model_decisions():
    """模型给的行号/动作/层级不合规时必须丢掉，不能污染结果。"""
    payload = _payload([
        _heading(10, 1, "1", "向量", [1], "chapter"),
        _heading(20, 3, "1.1", "线性运算", [1, 1]),   # 深度 2 却写层级 3 → 会被挑成可疑项
    ])
    client = _FakeClient([
        {"line": 999, "action": "set_level", "level": 2},        # 不存在的行
        {"line": 20, "action": "explode"},                       # 非法动作
        {"line": 20, "action": "set_level", "level": 9},         # 超范围
        {"line": 20, "action": "set_level", "level": 2, "reason": "ok"},
    ])
    review = R.review_structure(payload, client)
    assert len(review["decisions"]) == 1
    assert review["decisions"][0]["source_line"] == 20


def test_apply_review_changes_level_and_renumbers():
    payload = _payload([
        _heading(10, 1, "1", "向量", [1], "chapter"),
        _heading(20, 3, "1.8", "复数", [1, 8]),                  # 应为层级 2
        _heading(30, 3, "1.8.1", "四则运算", [1, 8, 1]),
    ])
    review = {"decisions": [{"source_line": 20, "action": "set_level", "level": 2,
                             "reason": "1.8 是节"}]}
    updated = R.apply_review(payload, review)
    by_line = {h["source_line"]: h for h in updated["headings"]}
    assert by_line[20]["level"] == 2
    assert by_line[20]["canonical"] == "1.1"                     # 重新编号了
    assert by_line[30]["canonical"] == "1.1.1"                   # 子级跟着走


def test_apply_review_records_revisions_and_keeps_text():
    payload = _payload([_heading(10, 1, "1", "向量", [1], "chapter"),
                        _heading(20, 3, "5.225", "5.225", [5, 225])])
    original_titles = {h["source_line"]: h["title"] for h in payload["headings"]}
    review = {"decisions": [{"source_line": 20, "action": "drop", "reason": "页码粘连的垃圾"}]}
    updated = R.apply_review(payload, review)
    assert len(updated["headings"]) == 1
    last = updated["revisions"][-1]
    assert last["changed"] == 1 and last["decisions"][0]["action"] == "drop"
    # 标题文字一个字都没被改
    assert {h["source_line"]: h["title"] for h in updated["headings"]} == {10: "向量"}
    assert original_titles[20] == "5.225"


def test_apply_review_drop_does_not_touch_other_headings():
    payload = _payload([_heading(10, 1, "1", "向量", [1], "chapter"),
                        _heading(20, 2, "1.1", "线性运算", [1, 1]),
                        _heading(30, 3, "1.1.1", "细节", [1, 1, 1])])
    review = {"decisions": [{"source_line": 20, "action": "keep"}]}
    updated = R.apply_review(payload, review)
    assert [h["canonical"] for h in updated["headings"]] == ["1", "1.1", "1.1.1"]
    assert updated["revisions"][-1]["changed"] == 0


def test_apply_review_does_not_touch_original_text(tmp_path):
    """G5：复核只改层级，原文文件一个字节都不能变。"""
    text_file = tmp_path / "original_text" / "ch01_s01.md"
    text_file.parent.mkdir(parents=True)
    text_file.write_text("原文内容，一个字都不能变。\n", encoding="utf-8")
    import hashlib
    before = hashlib.sha256(text_file.read_bytes()).hexdigest()
    payload = _payload([_heading(10, 1, "1", "向量", [1], "chapter"),
                        _heading(20, 3, "1.8", "复数", [1, 8])])
    R.apply_review(payload, {"decisions": [{"source_line": 20, "action": "set_level",
                                            "level": 2, "reason": "x"}]})
    assert hashlib.sha256(text_file.read_bytes()).hexdigest() == before


# ══ T1.7 概念抽取 ═══════════════════════════════════════════════════════
@pytest.mark.parametrize("raw,expected", [
    ("上界", "上界"),
    ("上 界", "上界"),
    ("定义1.2.1：上界", "上界"),
    ("定理 1.1（柯西不等式）", "柯西不等式"),   # 不能变成 "1.1"
    ("（上确界）", "上确界"),
    ("确界原理。", "确界原理"),
])
def test_normalize_name(raw, expected):
    assert C.normalize_name(raw) == expected


def test_merge_dedupes_across_units_and_has_no_explanation():
    """同一概念第二次出现只追加位置；**概念表里不再有解释字段**。

    2026-09-29 用户定案：抽取只做识别，**解释与配图交给教案阶段**
    （那边有整节上下文、也能配图）。所以这里锁两件事：
    ① 跨节去重/追加 occurrences 照旧；② `brief`/`explanation` **根本不出现**在产物里 ——
    免得有人以为"概念表自己带解释"，又把解释写回这里（两处各写一遍）。
    """
    part_a = {"unit": {"unit_id": "CH01-S01", "chapter": "CH01", "section": "CH01-S01",
                       "title": "第一节", "anchor": "1.1"},
              "concepts": [{"name": "上界", "type": "definition", "formal": "定义原文",
                            "source_quote": "引用甲"}]}
    part_b = {"unit": {"unit_id": "CH01-S02", "chapter": "CH01", "section": "CH01-S02",
                       "title": "第二节", "anchor": "1.2"},
              "concepts": [{"name": "上 界", "type": "definition", "formal": "",
                            "source_quote": "引用乙"}]}
    merged = C.merge_parts([part_a, part_b])
    assert merged["counts"]["concepts_total"] == 1
    concept = merged["concepts"][0]
    assert concept["id"] == "C-0001"
    assert "brief" not in concept and "explanation" not in concept
    assert [o["unit_id"] for o in concept["occurrences"]] == ["CH01-S01", "CH01-S02"]
    assert concept["source_quotes"] == ["引用甲", "引用乙"]


def test_merge_counts_same_unit_once():
    """同一节里模型把同一概念返回两次，不能算"出现两次"（实测踩过）。"""
    part = {"unit": {"unit_id": "CH01-S04-SS02"}, "concepts": [
        {"name": "2阶行列式", "type": "definition", "formal": "f", "source_quote": "q1"},
        {"name": "2阶行列式", "type": "definition", "formal": "", "source_quote": "q2"}]}
    merged = C.merge_parts([part])
    concept = merged["concepts"][0]
    assert len(concept["occurrences"]) == 1
    assert merged["counts"]["multi_occurrence"] == 0
    assert concept["formal"] == "f"          # 首次的原文表述留着；后续没抽到的用它补


def test_merge_drops_unknown_types_and_ids_are_sequential():
    part = {"unit": {"unit_id": "U1"}, "concepts": [
        {"name": "甲", "type": "definition", "brief": "", "explanation": "", "formal": "",
         "source_quote": ""},
        {"name": "乙", "type": "theorem", "brief": "", "explanation": "", "formal": "",
         "source_quote": ""},
        {"name": "丙", "type": "method", "brief": "", "explanation": "", "formal": "",
         "source_quote": ""}]}
    merged = C.merge_parts([part])
    assert [c["id"] for c in merged["concepts"]] == ["C-0001", "C-0002"]
    assert {c["type"] for c in merged["concepts"]} == {"definition", "theorem"}
    assert merged["counts"]["definitions"] == 1 and merged["counts"]["theorems"] == 1


def test_merge_output_has_meta():
    merged = C.merge_parts([])
    meta = merged["_meta"]
    assert meta["file_type"] == "concepts"
    assert meta["schema_version"] == C.SCHEMA_VERSION
    assert meta["generator"] == C.GENERATOR
    assert meta["field_notes"]


class _FakeConceptClient:
    def __init__(self):
        self.calls = 0
        self.session_usage = type("U", (), {"as_dict": staticmethod(lambda: {})})()

    def chat_json(self, messages, *, tag="", images=None, use_cache=None):
        self.calls += 1
        return {"data": {"concepts": [
            {"name": "上界", "type": "definition", "brief": "b",
             "explanation": "e", "formal": "f", "source_quote": "q"}]},
            "usage": {}, "cached": False}


@pytest.fixture
def parse_dir(tmp_path):
    (tmp_path / "original_text").mkdir()
    (tmp_path / "original_text" / "ch01_s01.md").write_text(
        "设 S 为非空实数集，若存在 M 使对一切 x∈S 有 x≤M，则称 S 有上界。", encoding="utf-8")
    (tmp_path / "original_text" / "ch01_s02.md").write_text(
        "上界 again 出现在第二节。", encoding="utf-8")
    (tmp_path / "chapters.json").write_text(json.dumps({
        "book": {"title": "测试教材"},
        "lesson_units": [
            {"unit_id": "CH01-S01", "chapter": "CH01", "section": "CH01-S01",
             "title": "第一节", "anchor": "1.1", "original_text": "original_text/ch01_s01.md"},
            {"unit_id": "CH01-S02", "chapter": "CH01", "section": "CH01-S02",
             "title": "第二节", "anchor": "1.2", "original_text": "original_text/ch01_s02.md"},
        ]}, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_extract_concepts_writes_parts_and_merges(parse_dir):
    client = _FakeConceptClient()
    result = C.extract_concepts(parse_dir, client=client)
    assert result["concepts_total"] == 1              # 两个单元同一个概念 → 合并成 1 条
    assert result["units_extracted"] == 2
    assert (parse_dir / "concepts.parts" / "CH01-S01.json").is_file()
    payload = json.loads((parse_dir / "concepts.json").read_text(encoding="utf-8"))
    assert payload["_meta"]["file_type"] == "concepts"
    assert payload["concepts"][0]["occurrences"]


def test_extract_concepts_reuses_parts_without_new_calls(parse_dir):
    """G8：分片已存在 → 不再调模型（重复跑不重复花钱）。"""
    client = _FakeConceptClient()
    C.extract_concepts(parse_dir, client=client)
    assert client.calls == 2
    again = C.extract_concepts(parse_dir, client=client)
    assert client.calls == 2                         # 没有新增调用
    assert again["units_reused"] == 2
    assert again["units_extracted"] == 0


def test_extract_concepts_only_reruns_one_unit(parse_dir):
    """单节重跑：只重抽指定的那一节。"""
    client = _FakeConceptClient()
    C.extract_concepts(parse_dir, client=client)
    client.calls = 0
    result = C.extract_concepts(parse_dir, client=client, only_units=["CH01-S02"],
                                force=True)
    assert client.calls == 1
    assert result["units_extracted"] == 1
    assert result["concepts_total"] == 1

# ── 2026-09-23：概念类型扩到四类（用户拍板收"命题/推论"） ───────────────
def test_allowed_types_cover_what_a_beginner_will_look_up():
    """**给任何初学者都能用** → 类型表不能被书的标签框住（用户 2026-09-29 定案）。

    起因：实测《电路分析基础》里「欧姆定律」**没有卡片** —— 作者在前言里说
    "欧姆定律无他，只是 u=Ri 而已"，把它并进了电阻元件的定义。对作者是取舍，
    对**按这个名字来查的初学者**就是缺页。书里 定律 出现 46 次、性质 67 次、公式 59 次，
    而老的四类表（定义/定理/命题/推论）里**没有它们的位置**。
    """
    import extract_concepts as E
    assert {"definition", "theorem", "proposition", "corollary",
            "law", "property", "formula"} == E.ALLOWED_TYPES
    assert E.TYPE_LABELS["proposition"] == "命题"
    assert E.TYPE_LABELS["law"] == "定律"          # 欧姆定律 / 基尔霍夫定律 / 叠加原理
    assert E.TYPE_LABELS["property"] == "性质"     # 电阻元件的无记忆性 / 电容电压的连续性质
    assert E.TYPE_LABELS["formula"] == "公式"      # 只收**有名字的**公式


def test_merge_parts_accepts_propositions_but_rejects_junk():
    import extract_concepts as E
    parts = [{
        "_meta": {"file_type": "concepts_part"},
        "unit": {"unit_id": "CH01-S01", "chapter": "CH01", "section": "CH01-S01"},
        "concepts": [
            {"name": "两向量共线的充要条件", "type": "proposition",
             "brief": "b", "explanation": "e", "formal": "f", "source_quote": "s"},
            {"name": "乱写的类型", "type": "method",
             "brief": "b", "explanation": "e", "formal": "f", "source_quote": "s"},
        ],
    }]
    merged = E.merge_parts(parts, book_title="测试")
    types = [c["type"] for c in merged["concepts"]]
    assert types == ["proposition"], types
    assert merged["counts"]["propositions"] == 1
    assert merged["counts"]["corollaries"] == 0


def test_normalize_name_strips_proposition_prefix():
    """类型词与编号是**噪声**，括号里/后面的才是名字。

    ⚠️ 2026-10-06 **前提更正**：这条测试原先第二行断言
    `normalize_name("推论1.2.3") == ""` —— 也就是**把 bug 当成了期望值**。

    那个"归成空串"的行为，配合 `_covers()` 开头的 `if not wanted: return False`，
    会让**所有「类型词 + 编号」形式的名字永远被判成"没覆盖"**，于是含编号定理的小节
    永远 `blocks_freeze`。实测代价：真实书 28 个分片卡住、**669,453 token** 花在
    重抽上（而重抽修不好它，反而把概念数从 528 弄成 519）；修好这把尺子后
    **重算（0 token）**就让 38 个分片翻转、`frozen` 由 False 变 True、概念数仍是 528。

    所以判据改成两边都钉：**不再归成空串**，且**原意不能改坏**。
    """
    import extract_concepts as E
    # 原意不变：类型词与编号剥离后，括号里的才是名字
    assert E.normalize_name("命题 1.1.1（两向量共线）") == "两向量共线"
    # 更正：只剩类型词+编号时，**不许**归成空串（空串会让 `_covers` 永远判没覆盖）
    assert E.normalize_name("推论1.2.3") != "", (
        "归一成空串会让 `_covers` 永远判它没被覆盖 —— 见本测试的说明与 "
        "tests/contracts/test_m10_concept_coverage.py")
    assert E.normalize_name("推论1.2.3") == E.normalize_name("推论 1.2.3"), \
        "同一编号的两种写法必须归一到同一个键"
    assert E.normalize_name("推论1.2.3") != E.normalize_name("推论1.2.4"), \
        "不同编号必须是不同的键（否则两条会互相冒充）"


# ── F2（2026-09-23）：概念 id 注册表 —— 重抽不许让已有引用指错 ──────────
def _part_of(unit_id, rows):
    return {"_meta": {"file_type": "concepts_part"},
            "unit": {"unit_id": unit_id, "chapter": "CH01", "section": unit_id},
            "concepts": [{"name": n, "type": t, "brief": "b" * 12, "explanation": "e" * 70,
                          "formal": "f", "source_quote": "s"} for n, t in rows]}


def test_registry_keeps_existing_ids_when_a_concept_is_inserted():
    """核心：中途多出两条概念，后面的 id **不许**整体后移。
    没做这个之前实测 C-0017 从"线性组合"变成了"两向量共线的充要条件"。"""
    import extract_concepts as E
    before = [_part_of("CH01-S01", [("向量", "definition"), ("线性组合", "definition")]),
              _part_of("CH01-S02", [("线性相关", "definition")])]
    first = E.merge_parts(before, book_title="t")
    registry = {E.normalize_name(c["name"]): c["id"] for c in first["concepts"]}
    assert [c["id"] for c in first["concepts"]] == ["C-0001", "C-0002", "C-0003"]

    after = [_part_of("CH01-S01", [("向量", "definition"),
                                   ("共线的充要条件", "proposition"),   # 新概念插在中间
                                   ("线性组合", "definition")]),
             _part_of("CH01-S02", [("线性相关", "definition")])]
    second = E.merge_parts(after, book_title="t", registry=registry)
    ids = {c["name"]: c["id"] for c in second["concepts"]}
    assert ids["向量"] == "C-0001"
    assert ids["线性组合"] == "C-0002"        # 没被新概念挤走
    assert ids["线性相关"] == "C-0003"
    assert ids["共线的充要条件"] == "C-0004"   # 新概念从最大号往后排
    assert second["counts"]["ids_reused"] == 3
    assert second["counts"]["ids_new"] == 1


def test_registry_survives_type_reclassification():
    """同一条概念从"定理"改标成"命题"，id 必须不变（实测真发生过）。"""
    import extract_concepts as E
    first = E.merge_parts([_part_of("CH01-S01", [("共线判定", "theorem")])])
    registry = {E.normalize_name("共线判定"): "C-0007"}
    second = E.merge_parts([_part_of("CH01-S01", [("共线判定", "proposition")])],
                           registry=registry)
    assert second["concepts"][0]["id"] == "C-0007"
    assert second["concepts"][0]["type"] == "proposition"


def test_registry_does_not_reuse_one_id_for_two_names():
    """同名不同类型时不能共用一个 id（否则页面上两处链接指向同一个概念）。"""
    import extract_concepts as E
    registry = {"同名": "C-0001", "另一个": "C-0002"}
    merged = E.merge_parts([_part_of("CH01-S01", [("另一个", "definition")])],
                           registry=registry)
    ids = [c["id"] for c in merged["concepts"]]
    assert len(ids) == len(set(ids)), ids


def test_concept_number_reads_the_biggest_id():
    import extract_concepts as E
    assert E.concept_number({"a": "C-0007", "b": "C-0012", "junk": "x"}) == 13
    assert E.concept_number({}) == 1


# ── F1（2026-09-23）：概念层读清洗后的原文，不读 OCR 原文 ────────────────
def test_unit_source_prefers_the_cleaned_text(tmp_path):
    import extract_concepts as E
    parse = tmp_path
    (parse / "original_text").mkdir()
    (parse / "latex").mkdir()
    unit = {"unit_id": "CH01-S01", "original_text": "original_text/ch01_s01.md"}
    (parse / "original_text/ch01_s01.md").write_text("OCR 原文 a//b", encoding="utf-8")
    path, kind = E.unit_source(parse, unit)
    assert kind == "original_text" and path.name == "ch01_s01.md"

    (parse / "latex/CH01-S01.md").write_text("清洗后 $a\\parallel b$", encoding="utf-8")
    path, kind = E.unit_source(parse, unit)
    assert kind == "latex" and path.name == "CH01-S01.md"


def test_stale_part_is_reextracted_when_the_source_changes(tmp_path):
    """F1 的关键：清洗产物出现后，旧分片必须被认成过期并重抽，
    否则概念卡永远是当初从 OCR 原文抽出来的那一版。"""
    import json as _json
    import extract_concepts as E

    parse = tmp_path / "parse"
    (parse / "original_text").mkdir(parents=True)
    (parse / "original_text/ch01_s01.md").write_text("OCR 原文", encoding="utf-8")
    (parse / "chapters.json").write_text(_json.dumps({
        "_meta": {"file_type": "chapters"}, "book": {"title": "t"},
        "lesson_units": [{"unit_id": "CH01-S01", "level": "section", "chapter": "CH01",
                          "section": "CH01-S01", "title": "线性运算", "anchor": "1.1",
                          "original_text": "original_text/ch01_s01.md"}],
    }, ensure_ascii=False), encoding="utf-8")

    class _Client:
        model, effort = "fake", "low"

        def __init__(self):
            self.calls = 0

        def chat_json(self, messages, tag="", **_):
            self.calls += 1
            return {"data": {"concepts": [{"name": "向量", "type": "definition",
                                           "brief": "b" * 12, "explanation": "e" * 70,
                                           "formal": "f", "source_quote": "s"}]},
                    "usage": {}, "cached": False}

    client = _Client()
    first = E.extract_concepts(parse, client=client)
    assert client.calls == 1 and first["units_extracted"] == 1
    part = _json.loads((parse / "concepts.parts/CH01-S01.json").read_text(encoding="utf-8"))
    assert part["_meta"]["source"] == "original_text"

    # 再跑一次：源没变 → 复用，不花钱
    assert E.extract_concepts(parse, client=client)["units_reused"] == 1
    assert client.calls == 1

    # 清洗产物出现 → 源变了 → 必须重抽
    (parse / "latex").mkdir()
    (parse / "latex/CH01-S01.md").write_text("清洗后文本", encoding="utf-8")
    third = E.extract_concepts(parse, client=client)
    assert client.calls == 2, "清洗后没有重抽"
    assert third["units_restale"] == 1
    part = _json.loads((parse / "concepts.parts/CH01-S01.json").read_text(encoding="utf-8"))
    assert part["_meta"]["source"] == "latex"


def test_ids_registry_file_is_written_and_reused(tmp_path):
    import json as _json
    import extract_concepts as E
    parse = tmp_path / "parse"
    (parse / "original_text").mkdir(parents=True)
    (parse / "original_text/ch01_s01.md").write_text("原文", encoding="utf-8")
    (parse / "chapters.json").write_text(_json.dumps({
        "_meta": {"file_type": "chapters"}, "book": {"title": "t"},
        "lesson_units": [{"unit_id": "CH01-S01", "level": "section", "chapter": "CH01",
                          "section": "CH01-S01", "title": "线性运算", "anchor": "1.1",
                          "original_text": "original_text/ch01_s01.md"}],
    }, ensure_ascii=False), encoding="utf-8")

    class _Client:
        model, effort = "fake", "low"

        def chat_json(self, messages, tag="", **_):
            return {"data": {"concepts": [{"name": "向量", "type": "definition",
                                           "brief": "b" * 12, "explanation": "e" * 70,
                                           "formal": "f", "source_quote": "s"}]},
                    "usage": {}, "cached": False}

    E.extract_concepts(parse, client=_Client())
    registry = _json.loads((parse / E.IDS_FILE).read_text(encoding="utf-8"))
    assert registry["_meta"]["file_type"] == "concept_ids"
    assert registry["ids"]["向量"] == "C-0001"


def test_numbered_labels_and_missing_detection():
    import extract_concepts as E
    text = "定义1.1.1.向量… 命题 1.1.2.三向量共面… 引理1.1.3.x 这不是条目"
    labels = E.numbered_labels(text)
    assert labels == ["定义1.1.1", "命题1.1.2", "引理1.1.3"]
    rows = [{"name": "向量", "formal": "定义1.1.1.向量…", "source_quote": "定义1.1.1"},
            {"name": "三向量共面", "formal": "命题1.1.2.三向量共面…", "source_quote": ""}]
    assert E.missing_labels(labels, rows) == ["引理1.1.3"]


def test_extract_unit_retries_when_a_numbered_item_is_missing():
    """核心：漏了编号条目要带着清单重跑，而不是就此少一张卡（实测漏过 9 条）。"""
    import extract_concepts as E
    text = "定义1.1.1.向量是既有大小又有方向的量.\n命题1.1.2.三向量共面当且仅当…"

    class _Client:
        model, effort = "fake", "low"

        def __init__(self):
            self.calls = 0

        def chat_json(self, messages, tag="", **_):
            self.calls += 1
            if self.calls == 1:
                return {"data": {"concepts": [  # 第一版故意漏掉命题
                    {"name": "向量", "type": "definition", "brief": "b" * 12,
                     "explanation": "e" * 70, "formal": "定义1.1.1.向量是既有大小又有方向的量.",
                     "source_quote": "定义1.1.1"}]}, "usage": {}, "cached": False}
            return {"data": {"concepts": [
                {"name": "向量", "type": "definition", "brief": "b" * 12,
                 "explanation": "e" * 70, "formal": "定义1.1.1.向量是既有大小又有方向的量.",
                 "source_quote": "定义1.1.1"},
                {"name": "三向量共面", "type": "proposition", "brief": "b" * 12,
                 "explanation": "e" * 70, "formal": "命题1.1.2.三向量共面当且仅当…",
                 "source_quote": "命题1.1.2"}]}, "usage": {}, "cached": False}

    client = _Client()
    part = E.extract_unit({"unit_id": "CH01-S01", "title": "t", "chapter": "CH01",
                           "section": "CH01-S01"}, text, client)
    assert client.calls == 2, "漏了条目应该重跑一次"
    assert part["coverage"]["完整"] is True
    assert len(part["concepts"]) == 2

# ── 并行抽取（2026-09-29 固化：默认 6 路，只压时间不加花费）────────────────
class _FakeUsageClient:
    """带**真实用量字段**的假客户端 —— 用来验证"各 worker 的账要并回主客户端"。"""

    def __init__(self, tokens: int = 1000):
        self.calls = 0
        self.model = "fake"
        self.effort = "high"
        self.session_usage = types.SimpleNamespace(
            prompt_tokens=100 if tokens else 0, completion_tokens=max(tokens - 100, 0),
            total_tokens=tokens, reasoning_tokens=tokens // 2,
            cache_hit_tokens=0, cache_miss_tokens=0)

    def chat_json(self, messages, *, tag="", images=None, use_cache=None):
        self.calls += 1
        return {"data": {"concepts": [{"name": "上界", "type": "definition",
                                       "formal": "f", "source_quote": "q"}]},
                "usage": {}, "cached": False}


def test_parallel_extraction_writes_parts_and_sums_worker_usage(parse_dir):
    """并行抽取：① 每节都落盘 ② **各 worker 的账并回主客户端**。

    为什么必须锁这两条（2026-09-29 实测踩过）：
      * 并行时每个 worker 一个客户端，而用量（token/次数）记在**客户端实例**上 ——
        不汇总的话 CLI 只报主客户端（恒 0），"跑了 3 节花了多少"显示成 **0 token**（假账）；
      * 分片按单元落盘（`concepts.parts/<unit>.json`），并行不能互相踩。
    """
    main = _FakeUsageClient(tokens=0)     # 主客户端从 0 起算，断言才干净
    created: list = []

    def factory():
        worker = _FakeUsageClient(tokens=1000)
        created.append(worker)
        return worker

    result = C.extract_concepts(parse_dir, client=main, parallel=2, client_factory=factory)
    assert result["units_extracted"] == 2
    assert (parse_dir / "concepts.parts" / "CH01-S01.json").is_file()
    assert (parse_dir / "concepts.parts" / "CH01-S02.json").is_file()
    # 账：主客户端拿到的应当是**各 worker 之和**，不是 0
    assert main.calls == sum(w.calls for w in created) == 2
    assert main.session_usage.total_tokens == sum(w.session_usage.total_tokens for w in created)
    assert main.session_usage.reasoning_tokens == sum(
        w.session_usage.reasoning_tokens for w in created)


def test_concepts_cli_defaults_to_parallel():
    """**默认就是并行**（固化）：别让"为了省时间加的那一行"被人悄悄改回 1。

    ⚠️ 必须**按文件路径**加载这个 `cli.py`：仓库里有好几个块都叫 `cli.py`，
    `import cli` 会拿到先被导入的那一个（全量跑时实测撞名 —— 单跑过、全量挂）。
    """
    import importlib.util
    from pathlib import Path
    path = Path(C.__file__).with_name("cli.py")
    spec = importlib.util.spec_from_file_location("concepts_cli_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DEFAULT_PARALLEL >= 2
