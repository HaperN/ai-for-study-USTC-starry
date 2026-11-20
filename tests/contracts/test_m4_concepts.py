"""M4 概念索引契约回归：**单写方合并**、覆盖、冻结门。

守三件在方案里写得很细、做错了不会报错的事：

1. **身份不许只按名字**（§7："同名、重复正文、多处匹配等歧义不能只按标题自动合并"）。
   旧实现恰恰是按名字合并的，实测代价：448 个概念只有 438 个进了注册表，
   **10 个概念从来没出现在读者面前**，而且横跨 7 章的「推论 1」被并成了同一个概念；
2. **"没概念"与"没跑"是两件事**（§6："逐项完成/明确无概念"）。
   "抽完确认本节没有具名结论"是**合法完成态**，不是失败分片；
3. **失败分片不能用旧片冒充新鲜**（§6）。分片自报"有漏项"就必须挡住冻结。
"""
from __future__ import annotations

import pytest

from aiforstudy.blocks.concepts.index import (ConceptEntry, ConceptIndex, Occurrence,
                                              PartStatus, merge_parts, normalize_name)
from aiforstudy.core.identity import UnitRef
from aiforstudy.errors import ConfigError


def unit(key: str, *, role: str = "section") -> UnitRef:
    return UnitRef(unit_key=key, chapter_key=key.split("-", 1)[0], content_sha256="c",
                   teaching_role=role, parent_unit_key=None)


def catalog_with(*keys: str):
    from aiforstudy.core.identity import Catalog
    return Catalog(book_id="b", revision="r", units=tuple(unit(k) for k in keys))


def part(name: str, ctype: str = "theorem", *, quote: str = "") -> dict:
    return {"name": name, "type": ctype, "formal": "…", "source_quote": quote}


# ────────────────────────────────────────── 归一化

def test_normalisation_only_removes_whitespace():
    """多做一步归一化就多一步**会误合并**的操作，而误合并是无声的。"""
    assert normalize_name("定 义 3") == normalize_name("定义3")
    assert normalize_name("The Rem") == normalize_name("therem")
    assert normalize_name("定义3") != normalize_name("定义4")


# ────────────────────────────────────────── 身份键

def test_identity_key_is_name_and_type_and_chapter_not_name_alone():
    a = ConceptEntry(concept_id="C-0001", name="推论1", type="corollary",
                     ownership_unit="CH03-S01", ownership_chapter="CH03")
    b = ConceptEntry(concept_id="C-0002", name="推论1", type="corollary",
                     ownership_unit="CH07-S02", ownership_chapter="CH07")
    assert a.key != b.key, "不同章的「推论 1」是两个概念 —— 旧实现把它们并成了一个"


def test_concept_entry_rejects_bad_shapes():
    with pytest.raises(ConfigError, match="概念名不能为空"):
        ConceptEntry(concept_id="C-1", name="  ", type="theorem", ownership_unit="CH01")
    with pytest.raises(ConfigError, match="类型必须是"):
        ConceptEntry(concept_id="C-1", name="甲", type="猜的", ownership_unit="CH01")
    with pytest.raises(ConfigError, match="归属单元必填"):
        ConceptEntry(concept_id="C-1", name="甲", type="theorem", ownership_unit="")


# ────────────────────────────────────────── 单写方合并

def test_merge_keeps_one_concept_per_name_per_chapter():
    cat = catalog_with("CH01-S01", "CH02-S01")
    payloads = {"CH01-S01": {"concepts": [part("定理1")]},
                "CH02-S01": {"concepts": [part("定理1")]}}
    status = (PartStatus("CH01-S01", "complete", 1), PartStatus("CH02-S01", "complete", 1))
    index = merge_parts(cat, payloads, status, registry={}, book_id="b")
    assert len(index.concepts) == 2
    assert {c.chapter for c in index.concepts} == {"CH01", "CH02"}
    assert len({c.concept_id for c in index.concepts}) == 2, "两个概念必须拿到两个 id"


def test_merge_reuses_the_registry_id_for_the_same_identity():
    cat = catalog_with("CH01-S01")
    payloads = {"CH01-S01": {"concepts": [part("虚单位", "definition")]}}
    status = (PartStatus("CH01-S01", "complete", 1),)
    registry = {("虚单位", "definition", "CH01"): "C-0001"}
    index = merge_parts(cat, payloads, status, registry=registry, book_id="b")
    assert index.concepts[0].concept_id == "C-0001", "重抽时同一概念要沿用旧号"


def test_merge_allocates_new_ids_after_the_max():
    cat = catalog_with("CH01-S01")
    payloads = {"CH01-S01": {"concepts": [part("新概念")]}}
    status = (PartStatus("CH01-S01", "complete", 1),)
    index = merge_parts(cat, payloads, status,
                        registry={("旧的", "theorem", "CH09"): "C-0007"}, book_id="b")
    assert index.concepts[0].concept_id == "C-0008"


def test_merge_reports_name_collisions_instead_of_merging():
    """同名多 id 是**如实记账**，不是自动合并也不是丢弃。"""
    cat = catalog_with("CH02-S01")
    payloads = {"CH02-S01": {"concepts": [part("单叶性区域", "property")]}}
    status = (PartStatus("CH02-S01", "complete", 1),)
    index = merge_parts(cat, payloads, status,
                        registry={("单叶性区域", "property", "CH08"): "C-0112"}, book_id="b")
    # 章不同 → 新概念；名字相同 → 记进 collisions
    assert index.concepts[0].concept_id != "C-0112"
    assert "单叶性区域" in index.collisions
    assert set(index.collisions["单叶性区域"]) == {"C-0112", index.concepts[0].concept_id}


def test_merge_records_multi_chapter_names_with_a_reason():
    """条数变了必须说明为什么 —— 否则就是静默改了读者看到的东西。"""
    cat = catalog_with("CH03-S01", "CH07-S01")
    payloads = {"CH03-S01": {"concepts": [part("推论1", "corollary")]},
                "CH07-S01": {"concepts": [part("推论1", "corollary")]}}
    status = (PartStatus("CH03-S01", "complete", 1), PartStatus("CH07-S01", "complete", 1))
    index = merge_parts(cat, payloads, status,
                        registry={("推论1", "corollary", "CH03"): "C-0208"}, book_id="b")
    assert "推论1" in index.multi_chapter_names
    row = index.multi_chapter_names["推论1"]
    assert row["chapters"] == ["CH03", "CH07"] and "不…自动合并" in row["why"] or "自动合并" in row["why"]


def test_merge_is_order_independent():
    """并行 worker 的完成先后不许影响合并结果（§10：协调器单写）。"""
    cat = catalog_with("CH01-S01", "CH02-S01")
    payloads = {"CH01-S01": {"concepts": [part("甲"), part("乙")]},
                "CH02-S01": {"concepts": [part("丙")]}}
    status = (PartStatus("CH01-S01", "complete", 2), PartStatus("CH02-S01", "complete", 1))
    a = merge_parts(cat, payloads, status, registry={}, book_id="b")
    # 换一个写入顺序（dict 顺序不影响，因为按 catalog 顺序遍历）
    shuffled = {k: payloads[k] for k in reversed(list(payloads))}
    b = merge_parts(cat, shuffled, status, registry={}, book_id="b")
    assert [c.as_dict() for c in a.concepts] == [c.as_dict() for c in b.concepts]


# ────────────────────────────────────────── 分片三态与冻结门

def test_no_concepts_is_a_legitimate_completion_not_an_incomplete_part():
    """"明确无概念"不挡冻结；"有漏项"才挡。"""
    assert PartStatus("CH01", "no-concepts", why="本节没有可核对的概念条目").blocks_freeze is False
    assert PartStatus("CH01", "incomplete", why="有漏项").blocks_freeze is True
    assert PartStatus("CH01", "missing", why="没跑过").blocks_freeze is True


def test_freezing_is_blocked_by_incomplete_parts():
    index = ConceptIndex(book_id="b",
                         concepts=(ConceptEntry("C-1", "甲", "theorem", "CH01-S01", "CH01"),),
                         parts=(PartStatus("CH01-S01", "complete", 1),
                                PartStatus("CH02-S01", "incomplete", 4, why="有漏项")),
                         no_concept_units={"CH03": "本节没有可核对的概念条目"})
    with pytest.raises(ConfigError, match="失败分片"):
        index.assert_freezable(("CH01-S01", "CH02-S01", "CH03"))


def test_freezing_is_blocked_by_unexplained_coverage():
    """§6：逐项完成/明确无概念 —— 既没概念也不在"无概念"名单里就是没解释。"""
    index = ConceptIndex(book_id="b",
                         concepts=(ConceptEntry("C-1", "甲", "theorem", "CH01-S01", "CH01"),),
                         parts=(PartStatus("CH01-S01", "complete", 1),
                                PartStatus("CH02-S01", "complete", 0)))
    cov = index.coverage(("CH01-S01", "CH02-S01"))
    assert cov["unexplained"] == ["CH02-S01"] and cov["ok"] is False
    with pytest.raises(ConfigError, match="覆盖不完整"):
        index.assert_freezable(("CH01-S01", "CH02-S01"))


def test_a_unit_cannot_be_both_covered_and_declared_empty():
    index = ConceptIndex(book_id="b",
                         concepts=(ConceptEntry("C-1", "甲", "theorem", "CH01-S01", "CH01"),),
                         no_concept_units={"CH01-S01": "自相矛盾"})
    cov = index.coverage(("CH01-S01",))
    assert cov["claimed_both"] == ["CH01-S01"] and cov["ok"] is False


def test_freezable_index_has_no_blocking_parts():
    index = ConceptIndex(book_id="b",
                         concepts=(ConceptEntry("C-1", "甲", "theorem", "CH01-S01", "CH01"),),
                         parts=(PartStatus("CH01-S01", "complete", 1),
                                PartStatus("CH02", "no-concepts", 0, why="没有可核对的条目")),
                         no_concept_units={"CH02": "没有可核对的条目"})
    index.assert_freezable(("CH01-S01", "CH02"))     # 不抛
    assert index.coverage(("CH01-S01", "CH02"))["ok"] is True


# ────────────────────────────────────────── 注册表缺口与新鲜度

def test_registry_gaps_are_reported_not_silently_patched():
    """只覆盖一部分的注册表**不是**完整索引；缺口必须出现在报告里。"""
    index = ConceptIndex(book_id="b",
                         concepts=(ConceptEntry("C-1", "甲", "theorem", "CH01-S01", "CH01"),
                                   ConceptEntry("C-2", "乙", "theorem", "CH01-S01", "CH01")),
                         registry={("甲", "theorem", "CH01"): "C-1"})
    assert index.registry_gaps() == ("C-2",)
    assert index.verify()["counts"]["registry_gaps"] == 1


def test_freshness_is_a_fingerprint_not_a_file_existence_check():
    """§11：文件存在不是缓存有效证据。"""
    index = ConceptIndex(book_id="b", source_digest="sha256:aaa", frozen=True)
    assert index.is_fresh("sha256:aaa") is True
    assert index.is_fresh("sha256:bbb") is False
    unfrozen = ConceptIndex(book_id="b", source_digest="sha256:aaa", frozen=False)
    assert unfrozen.is_fresh("sha256:aaa") is False


def test_occurrence_dedup_and_introduced_wins():
    cat = catalog_with("CH01-S01")
    payloads = {"CH01-S01": {"concepts": [
        {"name": "甲", "type": "theorem", "anchor": "1.1"},
        {"name": "甲", "type": "theorem", "anchor": "1.1"},   # 重复
    ]}}
    status = (PartStatus("CH01-S01", "complete", 2),)
    index = merge_parts(cat, payloads, status, registry={}, book_id="b")
    assert len(index.concepts) == 1
    assert len(index.concepts[0].occurrences) == 1, "同一（单元, 锚点）只记一次"


def test_part_with_bad_json_is_incomplete_not_fresh(tmp_path):
    """坏分片不能冒充新鲜：它是 incomplete，会挡住冻结。"""
    from aiforstudy.blocks.concepts.index import read_parts
    from aiforstudy.core.identity import Catalog
    parse = tmp_path / "parse"
    (parse / "concepts.parts").mkdir(parents=True)
    (parse / "concepts.parts" / "CH01-S01.json").write_text("{ 坏 json", encoding="utf-8")
    cat = Catalog(book_id="b", revision="r",
                  units=(UnitRef(unit_key="CH01-S01", chapter_key="CH01",
                                 content_sha256="c", teaching_role="section",
                                 parent_unit_key=None),))
    _payloads, status = read_parts(parse, cat)
    assert status[0].state == "incomplete" and status[0].blocks_freeze
