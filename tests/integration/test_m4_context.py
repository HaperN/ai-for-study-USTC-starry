"""M4 上下文集成回归：**三件套实际进入请求**、预算与拒发、指纹与并行度无关。

方案 §7 的三条硬要求，逐条在这里变成断言：

> 全书上下文按一次运行冻结：完整目录精简树、完整概念/定理/公式条目索引、教材顺序、
> 本节全文、本节与显式依赖条目、符号表、可用图。

> 本节原文、全书目录和完整条目索引**不得静默截断**。请求前计算上下文预算；
> 确定性紧凑表示仍超上限时标 `CONTEXT_TOO_LARGE`，**不发请求**。

> 相同起始快照下**并行 1/2/8 的请求输入指纹应一致**，不能让 worker 完成先后改变提示词。

最后一条最容易被"顺手优化"破掉：一旦前序信息取的是"本趟已通过"的实时结果，
第 3 节拿到的提示词就会取决于第 1、2 节谁先跑完 —— 而并行度是运行参数。
所以本实现把前序信息限制在**起跑时冻结的快照**里，并在这里用"打乱组装顺序"来证明它。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiforstudy.blocks.concepts import build_index
from aiforstudy.blocks.parse import build_catalog
from aiforstudy.core.source import CanonicalSource
from aiforstudy.pipeline.snapshot import (CONTEXT_MISSING, CONTEXT_TOO_LARGE, all_unit_contexts,
                                          freeze, unit_context)

ROOT = Path(__file__).resolve().parents[2]
REAL_BOOK = "complex-analysis-v2"
REAL_PARSE = ROOT / "data" / "parse" / REAL_BOOK


def _mini_catalog():
    from aiforstudy.core.identity import Catalog, TocNodeRef, UnitRef
    units = tuple(UnitRef(unit_key=k, chapter_key=k.split("-", 1)[0], content_sha256="c" + k,
                          teaching_role="section", parent_unit_key=None,
                          source_span=(i * 10 + 1, i * 10 + 9))
                  for i, k in enumerate(("CH01-S01", "CH01-S02", "CH02-S01")))
    nodes = (TocNodeRef(node_key="N-1-第1章", toc_id="T-0001", title="第一章", level=1,
                        kind="chapter", confirmed=True),
             TocNodeRef(node_key="N-2-1.1", toc_id="T-0002", title="1.1 甲", level=2,
                        kind="section", confirmed=True))
    return Catalog(book_id="mini", revision="parse-v2", units=units, toc_nodes=nodes)


def _mini_index(catalog):
    from aiforstudy.blocks.concepts.index import ConceptEntry, ConceptIndex, PartStatus
    concepts = tuple(
        ConceptEntry(concept_id=f"C-{i:04d}", name=name, type=ctype,
                     ownership_unit=unit_key, ownership_chapter=unit_key.split("-")[0])
        for i, (name, ctype, unit_key) in enumerate(
            (("甲", "definition", "CH01-S01"), ("乙", "theorem", "CH01-S01"),
             ("丙", "formula", "CH02-S01")), start=1))
    return ConceptIndex(book_id="mini", concepts=concepts,
                        parts=tuple(PartStatus(u.unit_key, "complete", 1)
                                    for u in catalog.units))


def _snapshot():
    catalog = _mini_catalog()
    index = _mini_index(catalog)
    source = CanonicalSource.build(b"raw text", b"raw text", revision="parse-v2")
    return freeze("mini", catalog, index, source, figures=("fig-1", "fig-2"))


# ────────────────────────────────────────── 三件套

def test_the_three_parts_are_all_present_in_the_request_payload():
    """§7 的三件套：全书目录 + 完整条目索引 + 本节全文，一起进请求。"""
    snap = _snapshot()
    bundle = unit_context(snap, "CH01-S01", unit_text="这是本节全文。" * 10,
                          concepts_here=({"id": "C-0001", "name": "甲"},))
    assert bundle.ok and bundle.has_the_three_parts
    payload = bundle.as_request_payload()
    assert payload["toc_tree"], "全书目录必须在请求里"
    assert payload["entry_index"], "完整条目索引必须在请求里"
    assert payload["unit_text"].strip(), "本节全文必须在请求里"
    assert payload["concepts_here"], "本节概念必须在请求里"


def test_entry_index_carries_the_five_identity_fields():
    """§7：完整索引指**身份、名称、类型、归属、原文锚点**。"""
    snap = _snapshot()
    row = snap.entry_index[0]
    assert {"id", "name", "type", "ownership_chapter", "ownership_unit", "anchor"} <= set(row)


def test_the_three_parts_are_never_truncated():
    """§7：三件套**不得静默截断**。所以无论单元多大，条数都等于快照里的条数。"""
    snap = _snapshot()
    bundle = unit_context(snap, "CH01-S01", unit_text="很长很长" * 100000,
                          max_chars=200)          # 故意把上限压到很小
    assert len(bundle.toc_tree) == len(snap.toc_tree)
    assert len(bundle.entry_index) == len(snap.entry_index)
    assert bundle.unit_text == "很长很长" * 100000, "原文不许被裁"


# ────────────────────────────────────────── 预算与拒发

def test_over_budget_is_refused_with_the_exact_marker():
    snap = _snapshot()
    bundle = unit_context(snap, "CH01-S01", unit_text="x" * 5000, max_chars=200)
    assert bundle.ok is False
    assert bundle.refuse_reason == CONTEXT_TOO_LARGE
    assert bundle.budget["chars"] > bundle.budget["max_chars"]
    assert "不发请求" in bundle.notes[0]


def test_missing_text_is_refused_not_papered_over():
    """§7：不用"模型可能记得"兜底。"""
    snap = _snapshot()
    bundle = unit_context(snap, "CH01-S01", unit_text="   ")
    assert bundle.refuse_reason == CONTEXT_MISSING


def test_within_budget_bundle_reports_its_size():
    snap = _snapshot()
    bundle = unit_context(snap, "CH01-S01", unit_text="正常长度")
    assert bundle.ok and bundle.budget["chars"] > 0
    assert bundle.budget["parts"]["toc_tree"] == len(snap.toc_tree)


# ────────────────────────────────────────── 指纹与并行度无关

def test_request_fingerprint_does_not_depend_on_assembly_order():
    """§7：不能让 worker 完成先后改变提示词。

    这里用"打乱组装顺序"模拟并行度不同（顺序是并行唯一能影响的东西）。
    """
    snap = _snapshot()
    texts = {u: f"{u} 的全文" for u in snap.unit_keys}
    forward = all_unit_contexts(snap, texts)
    backward = all_unit_contexts(snap, dict(reversed(list(texts.items()))))
    for key in snap.unit_keys:
        assert forward[key].digest() == backward[key].digest(), key
        assert forward[key].as_request_payload() == backward[key].as_request_payload()


def test_fingerprint_changes_when_the_snapshot_changes():
    """快照变了（原文/目录/索引）→ 请求指纹必须变，不能复用旧讲解。"""
    from aiforstudy.blocks.concepts.index import ConceptIndex, PartStatus
    snap_a = _snapshot()
    catalog = _mini_catalog()
    index = _mini_index(catalog)
    source_b = CanonicalSource.build(b"raw text", b"different text", revision="parse-v2")
    snap_b = freeze("mini", catalog, index, source_b, figures=("fig-1",))
    a = unit_context(snap_a, "CH01-S01", unit_text="同一段正文")
    b = unit_context(snap_b, "CH01-S01", unit_text="同一段正文")
    assert a.digest() != b.digest()
    assert snap_a.digest() != snap_b.digest()


def test_fingerprint_has_no_run_or_path_fields():
    """§11：指纹不含时间/run_id/绝对工作目录。"""
    snap = _snapshot()
    bundle = unit_context(snap, "CH01-S01", unit_text="正文")
    blob = json.dumps(bundle.as_request_payload(), ensure_ascii=False)
    for forbidden in ("run_id", "created_at", "timestamp", "/tmp", str(ROOT)):
        assert forbidden not in blob, forbidden


def test_prior_information_comes_from_the_frozen_snapshot():
    """前序信息只能来自起跑前冻结的快照 —— 否则并行度会改变提示词。"""
    catalog = _mini_catalog()
    index = _mini_index(catalog)
    source = CanonicalSource.build(b"raw", b"raw", revision="parse-v2")
    snap = freeze("mini", catalog, index, source,
                  prior_approved={"CH01-S01": "已通过：一句话摘要"})
    assert snap.prior_approved == {"CH01-S01": "已通过：一句话摘要"}
    # 快照指纹把它算进去：前序变了，指纹就变
    snap2 = freeze("mini", catalog, index, source,
                   prior_approved={"CH01-S01": "已通过：另一句摘要"})
    assert snap.digest() != snap2.digest()


# ────────────────────────────────────────── 真实书

@pytest.mark.skipif(not (REAL_PARSE / "chapters.json").is_file(),
                    reason="真实解析产物不在位（已归档）")
def test_real_book_snapshot_holds_the_full_index():
    catalog, _details = build_catalog(REAL_PARSE, book_id=REAL_BOOK, revision="parse-v2")
    index = build_index(REAL_PARSE, catalog, book_id=REAL_BOOK)
    source = CanonicalSource.build(
        (REAL_PARSE / "document.md").read_bytes(), revision="parse-v2",
        raw_path="document.md", selected_path="document.md")
    snap = freeze(REAL_BOOK, catalog, index, source)
    assert len(snap.toc_tree) == 47
    assert len(snap.entry_index) == len(index.concepts)
    assert len(snap.unit_keys) == len(catalog.teaching_units()) == 72
    # 三件套对真实书成立
    doc_lines = (REAL_PARSE / "document.md").read_text(encoding="utf-8").splitlines()
    unit = catalog.teaching_units()[2]
    lo, hi = unit.source_span
    bundle = unit_context(snap, unit.unit_key,
                          unit_text="\n".join(doc_lines[lo - 1:hi]),
                          concepts_here=tuple(c.as_dict() for c in index.introduced_in(unit.unit_key)))
    assert bundle.ok and bundle.has_the_three_parts
    assert bundle.budget["chars"] > 1000


@pytest.mark.skipif(not (REAL_PARSE / "chapters.json").is_file(),
                    reason="真实解析产物不在位（已归档）")
def test_real_book_recovers_the_ten_concepts_the_old_registry_dropped():
    """旧的名字→id 注册表只有 438 条，448 个概念里**10 个没有条目**。

    新索引按（名, 类型, 章）发号，这 10 个必须全部找回来。
    """
    catalog, _details = build_catalog(REAL_PARSE, book_id=REAL_BOOK, revision="parse-v2")
    index = build_index(REAL_PARSE, catalog, book_id=REAL_BOOK)
    lost = ["C-0117", "C-0208", "C-0269", "C-0292", "C-0294", "C-0307", "C-0331",
            "C-0384", "C-0403", "C-0424"]
    by_id = index.by_id()
    assert [cid for cid in lost if cid not in by_id] == [], "这 10 个概念必须都在新索引里"
    assert index.verify()["counts"]["legacy_registry_entries"] == 438
    assert index.verify()["counts"]["concepts"] >= 448


@pytest.mark.skipif(not (REAL_PARSE / "chapters.json").is_file(),
                    reason="真实解析产物不在位（已归档）")
def test_real_book_index_freezes_because_the_gaps_were_a_false_alarm():
    """真实书现在**可以冻结** —— 而那 28 个"有漏项"是**尺子不准**造成的假报。

    ⚠️ 2026-10-06 **前提更正**：这条测试原先断言 `blocking == 28` 且 `frozen is False`。
    那是当时的事实，但它的**成因**不是"内容不够"：旧 `normalize_name` 把
    「类型词 + 编号」的名字（`定理1`、`性质5`、`推论1`）归成了**空串**，
    而覆盖度判定把空串当成"没覆盖" —— 于是**每个含编号定理的小节都被误判**。
    为此花的 **669,453 token** 重抽没能修好它（反而把概念数从 528 弄成 519）。

    判据没有放宽，而是**换成两边都钉、并且钉住机制**：
      1. 索引现在能冻结，概念数与分片里的内容一致；
      2. **磁盘上那 28 个 `coverage.完整: false` 仍在** —— 也就是说结果**不是靠改数据**
         得来的，而是靠"按内容重算"（否则这条测试就成了"改完数据当然就过了"）；
      3. **真的漏项仍然挡冻结**（下面用一份人造分片验），否则这条判据会变成"永远通过"。
    """
    catalog, _details = build_catalog(REAL_PARSE, book_id=REAL_BOOK, revision="parse-v2")
    index = build_index(REAL_PARSE, catalog, book_id=REAL_BOOK)

    # 1) 现在能冻结
    assert index.blocking_parts() == (), \
        f"仍有挡住冻结的分片：{[(p.unit_key, p.why[:60]) for p in index.blocking_parts()][:4]}"
    assert index.frozen is True
    index.assert_freezable(tuple(u.unit_key for u in catalog.teaching_units()))

    # 2) 机制核对：磁盘上的自报标记**没有被改**
    import json as _json
    stale = [p for p in (REAL_PARSE / "concepts.parts").glob("*.json")
             if (_json.loads(p.read_text(encoding="utf-8")).get("coverage") or {}).get("完整") is False]
    assert stale, ("磁盘上应当仍留着当年那条坏尺子写下的 `完整: false` —— "
                   "若一个都没有，说明有人改了付费产物，而这条测试就不再能区分"
                   "「重算生效」与「数据被改」")
    # 3) **接到了状态上**：磁盘上写着 `完整: false` 的分片，现在被报成 `complete`。
    #    用**真实数据**验"重算确实改写了状态"，比造合成 parse 目录硬得多
    #    （合成目录要喂 toc/body/structure/export 一整套，脆，还容易测出假绿）。
    stale_units = {_json.loads(p.read_text(encoding="utf-8")).get("unit", {}).get("unit_id")
                   or p.stem for p in stale}
    states = {p.unit_key: p.state for p in index.parts}
    flipped = [u for u in stale_units if states.get(u) == "complete"]
    assert flipped, (f"磁盘上 {len(stale_units)} 个分片自报不完整，重算后**没有一个**"
                     f"变成 complete —— 状态没接到重算上。"
                     f"样例：{[(u, states.get(u)) for u in list(stale_units)[:4]]}")


def test_coverage_is_recomputed_from_content_in_both_directions():
    """**两个方向都要钉** —— 否则这条判据会退化成"永远通过"或"永远挡住"。

    左：自报「完整」但内容真漏 → 必须判**不完整**（不许照念自报标记）。
    右：自报「不完整」但编号条目其实都在卡片里 → 必须判**完整**（这正是真实书上那 28 个
        分片的形状；当年为此白花 669,453 token 重抽）。
    """
    from aiforstudy.blocks.concepts.index import recompute_coverage

    genuine_gap = {"named": ["定理9"], "concepts": [{"name": "完全别的东西"}],
                   "coverage": {"完整": True, "完整说明": "全部覆盖"}}
    out = recompute_coverage(genuine_gap)
    assert out["complete_by_content"] is False, "内容真漏却被判完整 —— 那是照念自报标记"
    assert out["missing_named"] == ["定理9"]

    false_alarm = {"named": ["定理1", "定理2", "性质5"], 
                   "concepts": [{"name": "定理1"}, {"name": "定理2"}, {"name": "性质5"}],
                   "coverage": {"完整": False, "完整说明": "有漏项",
                                "漏掉的具名结论": ["定理1", "定理2", "性质5"]}}
    out = recompute_coverage(false_alarm)
    assert out["complete_by_content"] is True, "编号条目明明都在卡片里，却被判漏掉"
    assert out["missing_named"] == []

    # 「本节没有可核对条目」是**明确无概念**，不是"没跑成"（方案 §6 的区分）
    empty = {"named": [], "concepts": [],
             "coverage": {"完整": None, "完整说明": "本节没有编号条目、也没有具名结论可核对"}}
    assert recompute_coverage(empty)["complete_by_content"] is None
