"""M3 身份与原文契约回归。

守三件在方案里写得很细、但**做错了不会报错**的事：

1. **两套身份并存**（§7）。`U-<hash>`（内容实例）与 `unit_key`（持久逻辑键）混成一个键
   就必然二选一地错：只用 U，改一个错字就换 id、教案/工单/概念全部失联；
   只用 unit_key，换了内容也不知道，缓存会拿旧讲解冒充新的；
2. **歧义与待裁决是两类**（§7 vs §9）。歧义（身份定不下来）**阻断迁移**；
   待裁决（身份清楚、命名/内容有分叉）**只阻断发布**。混成一类会两边都错；
3. **复核按输入指纹应用**（§6 / §15 A08）。只记结论不记依据，就会拿三天前对旧截图做的
   判断套今天的原文，而且没有任何地方能看出来。
"""
from __future__ import annotations

import pytest

from aiforstudy.core.identity import (Ambiguity, AmbiguityBlocked, Catalog, Lineage,
                                     NeedsReview, TocNodeRef, UnitRef, detect_ambiguities,
                                     make_node_key)
from aiforstudy.core.source import (CanonicalSource, RepairRecord, SourceRepairPolicy,
                                    SELECTED_TEXT_CONSUMERS)
from aiforstudy.errors import ContractError

D = "sha256:" + "a" * 64


def unit(key="CH01-S01", *, sha="h1", role="section", parent="CH01", **kw) -> UnitRef:
    return UnitRef(unit_key=key, chapter_key=key.split("-", 1)[0], content_sha256=sha,
                   teaching_role=role, parent_unit_key=parent, **kw)


# ────────────────────────────────────────── 两套身份

def test_unit_key_must_be_a_structural_key_not_a_content_hash():
    """逻辑键的形状被写死：内容哈希不许冒充逻辑键。"""
    with pytest.raises(ContractError, match="形状不对"):
        unit("U-fb3378b472")
    with pytest.raises(ContractError, match="形状不对"):
        unit("第一部分")


@pytest.mark.parametrize("key", ["CH01", "CH01-S02", "CH01-S02-SS03", "CH123-S45"])
def test_valid_keys(key):
    parent = None if key == "CH01" else "-".join(key.split("-")[:-1])
    assert unit(key, parent=parent).unit_key == key


def test_a_unit_keeps_both_identities():
    """方案 §7 的正题：保留 U 的原语义，**新增**持久逻辑键，两者并存。"""
    ref = unit(content_unit_id="U-fb3378b472", legacy_aliases=("U-fb3378b472",))
    assert ref.unit_key == "CH01-S01" and ref.content_unit_id == "U-fb3378b472"
    payload = ref.as_dict()
    assert payload["unit_key"] == "CH01-S01"
    assert payload["content_unit_id"] == "U-fb3378b472"


def test_content_hash_is_required():
    with pytest.raises(ContractError, match="content_sha256"):
        unit(sha="")


def test_source_span_must_not_be_reversed():
    with pytest.raises(ContractError, match="source_span 反了"):
        unit(source_span=(40, 10))


def test_parent_cannot_be_itself():
    with pytest.raises(ContractError, match="父节点不能是自己"):
        unit("CH01-S01", parent="CH01-S01")


def test_catalog_resolves_by_logical_key_content_id_and_alias():
    """解析顺序：逻辑键 → 内容实例 → 历史别名。"""
    ref = unit(content_unit_id="U-abc123def4", legacy_aliases=("U-old0000001",))
    cat = Catalog(book_id="b", revision="r1", units=(ref,))
    for token in ("CH01-S01", "U-abc123def4", "U-old0000001"):
        assert cat.resolve(token) is ref, token
    assert cat.resolve("不存在") is None


# ────────────────────────────────────────── 歧义 vs 待裁决

def test_duplicate_content_is_reported_not_merged():
    """同名/重复正文**不能只按标题自动合并**（§7）。"""
    a = unit("CH01-S01", sha="same", title="甲", parent=None)
    b = unit("CH02-S01", sha="same", title="甲", parent=None)
    found = detect_ambiguities((a, b), titles={"CH01-S01": "甲", "CH02-S01": "甲"})
    kinds = {f.kind for f in found}
    assert "duplicate-content" in kinds and "duplicate-title" in kinds
    for row in found:
        assert len(row.candidates) == 2
        assert row.needs, "歧义必须写清需要什么裁决"


def test_ambiguity_blocks_migration_but_needs_review_only_blocks_release():
    """§7 的歧义阻断**迁移**；§9 的待裁决只阻断**发布**。这两件事不能混。"""
    blocked = Catalog(book_id="b", revision="r",
                      units=(unit("CH01-S01", parent=None),),
                      ambiguities=(Ambiguity(kind="duplicate-content", subject="x",
                                             candidates=("A", "B"), why="同内容"),))
    with pytest.raises(AmbiguityBlocked, match="未裁决歧义"):
        blocked.assert_migratable()
    assert blocked.blocks_release

    reviewable = Catalog(book_id="b", revision="r",
                         units=(unit("CH01-S01", parent=None),),
                         needs_review=(NeedsReview(kind="toc-arbitration", subject="T-0020",
                                                   why="两种写法"),))
    reviewable.assert_migratable()          # ← 不抛：身份是清楚的
    assert reviewable.blocks_release is True
    assert reviewable.verify()["release_blocked_by"] == ["toc-arbitration:T-0020"]


def test_resolved_needs_review_does_not_block_release():
    cat = Catalog(book_id="b", revision="r", units=(unit("CH01-S01", parent=None),),
                  needs_review=(NeedsReview(kind="toc-arbitration", subject="T-0020",
                                            why="x", arbiter="human"),))
    assert cat.blocks_release is False
    assert cat.verify()["release_blocked_by"] == []


# ────────────────────────────────────────── catalog 自检

def test_verify_explains_gaps_instead_of_passing_them_silently():
    """§6：非教学、容器、空缺、孤儿**分别解释**，不能凭空数组通过。"""
    silent = Catalog(book_id="b", revision="r",
                     units=(unit("CH01-S01", role="non_teaching", parent=None),))
    problems = silent.verify()["problems"]
    assert any("缺少显式排除理由" in p for p in problems)

    explained = Catalog(book_id="b", revision="r",
                        units=(unit("CH01-S01", role="non_teaching", parent=None),),
                        exclusions={"CH01-S01": "前言，非教学"})
    assert explained.verify()["ok"] is True


def test_verify_flags_dangling_parent_unless_explained():
    dangling = Catalog(book_id="b", revision="r", units=(unit("CH01-S01", parent="CH09"),))
    assert any("父节点 CH09" in p for p in dangling.verify()["problems"])
    explained = Catalog(book_id="b", revision="r", units=(unit("CH01-S01", parent="CH09"),),
                        exclusions={"CH09": "本书没有章级正文单元"})
    assert explained.verify()["ok"] is True


def test_verify_flags_duplicate_logical_keys():
    dupe = Catalog(book_id="b", revision="r",
                   units=(unit("CH01-S01", parent=None), unit("CH01-S01", sha="h2", parent=None)))
    assert any("unit_key 重复" in p for p in dupe.verify()["problems"])


def test_node_key_includes_level_so_it_cannot_collide():
    """只按锚点会撞：章「1」与小节「1」是两棵不同的子树，撞了就串。"""
    assert make_node_key("1", level=1) != make_node_key("1", level=2)
    assert make_node_key("1.1", level=2) == make_node_key("1.1", level=2)
    with pytest.raises(ContractError):
        make_node_key("", level=None, chapter_key=None)


# ────────────────────────────────────────── 谱系

def test_lineage_needs_a_basis_and_works_both_ways():
    with pytest.raises(ContractError, match="必须写依据"):
        Lineage(op="split", from_keys=("CH01-S01",), to_keys=("CH01-S01-SS01",), basis="")
    row = Lineage(op="split", from_keys=("CH01-S01",),
                  to_keys=("CH01-S01-SS01", "CH01-S01-SS02"), basis="原节拆成两小节")
    cat = Catalog(book_id="b", revision="r",
                  units=(unit("CH01-S01-SS01", parent=None), unit("CH01-S01-SS02", parent=None)),
                  lineage=(row,), exclusions={"CH01-S01": "已被拆分，见 lineage"})
    assert cat.replaced_by("CH01-S01") == ("CH01-S01-SS01", "CH01-S01-SS02")
    assert set(cat.replaces("CH01-S01-SS01")) == {"CH01-S01"}
    assert cat.verify()["ok"] is True


def test_lineage_pointing_outside_the_catalog_is_a_problem():
    cat = Catalog(book_id="b", revision="r", units=(unit("CH01-S01", parent=None),),
                  lineage=(Lineage(op="merge", from_keys=("CH99",), to_keys=("CH01-S01",),
                                   basis="合并"),))
    assert any("CH99" in p for p in cat.verify()["problems"])


def test_lineage_basis_must_be_recorded_for_every_op():
    for op in ("split", "merge", "move", "rename", "reassign"):
        Lineage(op=op, from_keys=("A-CH01",), to_keys=("A-CH02",), basis="测试")


# ────────────────────────────────────────── CanonicalSource

def test_raw_is_preserved_and_repair_needs_basis_and_checks():
    raw = b"$x^2$"
    plain = CanonicalSource.build(raw, revision="parse-v2")
    assert plain.raw_preserved and not plain.is_repaired
    assert plain.raw_sha256 == plain.selected_text_sha256

    with pytest.raises(ContractError, match="basis"):
        RepairRecord(version="v1", basis="", scope="formula-notation",
                     diff_digest=D, enabled_by="user", checked={"公式一致": True})
    with pytest.raises(ContractError, match="enabled_by"):
        RepairRecord(version="v1", basis="OCR 错字", scope="formula-notation",
                     diff_digest=D, enabled_by="", checked={"公式一致": True})
    with pytest.raises(ContractError, match="差异检查"):
        RepairRecord(version="v1", basis="OCR 错字", scope="formula-notation",
                     diff_digest=D, enabled_by="user", checked={})


def test_failed_repair_checks_cannot_become_canonical():
    bad = RepairRecord(version="v1", basis="OCR 错字", scope="ocr-typo", diff_digest=D,
                       enabled_by="user", checked={"公式一致": False})
    with pytest.raises(ContractError, match="没有通过"):
        CanonicalSource.build(b"raw", b"fixed", revision="parse-v2", repairs=(bad,))


def test_changing_spans_requires_a_new_parse_revision():
    """§7：只修公式表示不动切分 → 升级 source revision；确需改切分 → 回到解析阶段。"""
    good = RepairRecord(version="v1", basis="公式符号规范化", scope="formula-notation",
                        diff_digest=D, enabled_by="user", checked={"公式一致": True})
    with pytest.raises(ContractError, match="新的解析版本"):
        CanonicalSource.build(b"raw", b"fixed", revision="source-v3", repairs=(good,),
                              spans_changed=True)
    ok = CanonicalSource.build(b"raw", b"fixed", revision="parse-v3", repairs=(good,),
                               spans_changed=True)
    assert ok.spans_changed


def test_selected_text_is_read_through_one_accessor():
    text = CanonicalSource.build(b"raw", b"sel", revision="parse-v2")
    for role in SELECTED_TEXT_CONSUMERS:
        view = text.consumer_view(role)
        assert view["selected_text_sha256"] == text.selected_text_sha256
    with pytest.raises(ContractError, match="不在 selected_text 的消费者名单"):
        text.consumer_view("随便一个角色")


def test_source_change_invalidates_every_consumer():
    before = CanonicalSource.build(b"raw1", b"sel1", revision="parse-v2")
    same = CanonicalSource.build(b"raw1", b"sel1", revision="parse-v2")
    assert same.invalidates(before) == ()
    after = CanonicalSource.build(b"raw1", b"sel2", revision="parse-v2")
    assert set(after.invalidates(before)) >= {"concepts", "lesson", "audit", "render"}


def test_repair_is_off_by_default_and_needs_a_requester():
    with pytest.raises(ContractError, match="请求方"):
        SourceRepairPolicy(enabled=True, requested_by="")
    policy = SourceRepairPolicy()
    with pytest.raises(ContractError, match="未启用"):
        policy.check("formula-notation")
    on = SourceRepairPolicy(enabled=True, requested_by="用户 2026-10-05")
    on.check("formula-notation")
    with pytest.raises(ContractError, match="不在允许清单"):
        on.check("spans")


# ────────────────────────────────────────── 目录复核

def test_review_must_record_which_inputs_it_judged():
    from aiforstudy.blocks.parse.review import DirectoryReview
    with pytest.raises(ContractError, match="输入指纹"):
        DirectoryReview(inputs_digest="")


def test_review_decision_must_state_a_reason():
    from aiforstudy.blocks.parse.review import ReviewDecision
    with pytest.raises(ContractError, match="reason"):
        ReviewDecision(subject="T-0001", verdict="accept-printed", reason="")
    with pytest.raises(ContractError, match="最终标题"):
        ReviewDecision(subject="T-0001", verdict="manual-title", reason="OCR 错字")
    ReviewDecision(subject="T-0001", verdict="leave-pending")     # 留待后续可以没理由


def test_stale_review_is_never_applied():
    """§15 A08：改来源/条数不变也要失效；**不可消费旧决策**。"""
    from aiforstudy.blocks.parse.review import (DirectoryReview, ReviewDecision,
                                                apply_review)
    disagree = [{"toc_id": "T-0001", "why": "两种写法", "arbiter": "pending"}]
    decision = ReviewDecision(subject="T-0001", verdict="accept-body", reason="正文更可信")

    missing = apply_review(None, expected_inputs_digest=D, disagreements=disagree)
    assert missing.stale and not missing.applied and len(missing.pending) == 1

    other = "sha256:" + "b" * 64
    stale = apply_review(DirectoryReview(inputs_digest=other, decisions=(decision,)),
                         expected_inputs_digest=D, disagreements=disagree)
    assert stale.stale and not stale.applied, "输入变了就不许应用旧决策"

    fresh = apply_review(DirectoryReview(inputs_digest=D, decisions=(decision,)),
                         expected_inputs_digest=D, disagreements=disagree)
    assert not fresh.stale and len(fresh.applied) == 1 and not fresh.pending


def test_review_decisions_digest_changes_when_content_changes_but_count_does_not():
    """A08 的另一半："改 review 内容但条数不变"也要能发现。"""
    from aiforstudy.blocks.parse.review import DirectoryReview, ReviewDecision
    a = DirectoryReview(inputs_digest=D, decisions=(
        ReviewDecision(subject="T-0001", verdict="accept-body", reason="甲"),))
    b = DirectoryReview(inputs_digest=D, decisions=(
        ReviewDecision(subject="T-0001", verdict="accept-printed", reason="乙"),))
    assert a.decisions_digest != b.decisions_digest


def test_toc_node_ref_keeps_a_persistent_key_apart_from_the_t_number():
    node = TocNodeRef(node_key="N-1-第1章", toc_id="T-0003", title="第一章", level=1,
                      kind="chapter", confirmed=True, legacy_aliases=("T-0003",))
    assert node.node_key != node.toc_id
    assert node.is_teaching
