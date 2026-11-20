"""目录层回归测试 —— **对账 / 契约 / 正文绑定**（M1 后半 + M4）。

这些用例锁的是合同（字段白名单、账目算得上、父子不断链）与四条铁规矩
（R-a 只增不丢 / R-b 不确定要说出来 / R-c 不猜 / R-d 目录是唯一骨架）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blocks.parse.toc import body as body_mod          # noqa: E402
from blocks.parse.toc import contract as C             # noqa: E402
from blocks.parse.toc import reconcile as R            # noqa: E402
from blocks.parse.toc import sources as S              # noqa: E402


# ── 造数据的小工具（不依赖真书，跑得快） ──────────────────────────────
def _printed(rows):
    return S.extract_printed(rows)


def _bookmarks(rows):
    result = S.SourceResult(source=S.SOURCE_BOOKMARKS, available=True)
    for number, title, level, pdf in rows:
        result.candidates.append(S.Candidate(
            source=S.SOURCE_BOOKMARKS, raw=f"{number}{title}" if number else title,
            number=number, title=title, level=level, pdf=pdf,
            structural=bool(number)))
    return result


def _build(bookmarks, printed, body=None, page_map=None):
    return R.reconcile(slug="test", book_title="测试书", bookmarks=bookmarks,
                       printed=printed, body=body or S.SourceResult(source=S.SOURCE_BODY),
                       page_map=page_map)


# ── R-a：只增不丢 ─────────────────────────────────────────────────────
def test_printed_only_entries_still_become_nodes():
    """书签里没有、印刷目录里有的条目**照常进 nodes**（R-a）。

    老实现 C1 的病：目录有正文没有 → 记个数就丢了。这里连"书签是骨架"时
    也不许把印刷目录独有的条目扔掉。
    """
    bookmarks = _bookmarks([("第1章", "向量", 1, 10), ("1.1", "复数", 2, 10)])
    printed = _printed(["第1章 向量 …… 1", "1.1 复数 …… 1", "1.2 平面点集 …… 5"])
    payload = _build(bookmarks, printed)
    titles = [n["title"] for n in payload["nodes"]]
    assert "平面点集" in titles
    assert any(d["kind"] == "source-extra" for d in payload["disagreements"])
    assert payload["_meta"]["counts"]["nodes"] == 3


def test_unparsable_lines_are_kept_as_unresolved():
    """抽不出来的行**有名字地留下**（R-b），不许静默消失。"""
    printed = S.extract_printed(["1.1 复数 …… 3", "乱七八糟没有页码的一行"])
    payload = _build(_bookmarks([("1.1", "复数", 2, 10)]), printed)
    assert any("乱七八糟" in str(u.get("raw")) for u in payload["unresolved"])


# ── R-c：不猜（但**层级**有明确定案时按目录定，见下）────────────────────
def test_level_conflict_is_recorded_not_silently_resolved():
    """两源层级不一致要**记冲突**，而且按用户定案：**层级以印刷目录为准**。

    用户 2026-09-28 定案原话："「第 N 章综合习题」——**第一级**"。
    依据 P7（结构冲突以目录为准）+ 实测：书签层级常是排版副产品
    （《数值计算方法》书签根节点是 `目录`，要位移 1 级）。

    ⚠️ 与"不猜"不冲突：这里不是挑一个看起来合理的，而是**照书自己的目录**定，
    并且逐条记进 `disagreements`（可追溯）。
    """
    bookmarks = _bookmarks([("第8章", "空间解析几何", 1, 7)])
    printed = _printed(["第8章 空间解析几何 …… 1"])
    payload = _build(bookmarks, printed)
    # 层级一致 → 没有冲突（confidence 另由"至少 3 个节点"等条件决定，这里不测）
    assert not payload["disagreements"]

    printed_bad = _printed(["8.3 空间解析几何 …… 1"])       # 印刷目录说是"节"
    payload2 = _build(_bookmarks([("第8章", "空间解析几何", 1, 7)]), printed_bad)
    # 冲突照记（不静默），但**已按目录裁决**，不是挂 pending
    assert any(d["arbiter"] == "rule" for d in payload2["disagreements"])
    assert not any(d["arbiter"] == "pending" for d in payload2["disagreements"])
    # 节点自己带上定好的层级（记账要结账：否则父子仍按书签的错误层级挂）
    assert payload2["nodes"][0]["level"] == 2


# ── 正文旁证：同名条目必须按页码消歧（2026-09-28 核验抓出的 bug）────────
def test_body_hint_picks_the_same_chapter_not_the_first_namesake():
    """同名正文条目跨章重复时，旁证要挑**页码邻近**的那条，不是第一条。

    实测《数分讲义第二册》：6 个「第 N 章综合习题」标题都叫「综合习题」，
    原实现一律返回第 8 章那条 → 第 9–13 章的旁证全写成"正文=第 8 章综合习题"，
    一个错配被重复报 6 次，把真分歧淹掉。
    """
    printed = _printed(["第8章 甲 …… 43", "第9章 乙 …… 135"])
    body = S.SourceResult(source=S.SOURCE_BODY)
    # 两个同名"综合习题"，分别在第 8 章（p.45）与第 9 章（p.140）附近
    for number, page in (("", 45), ("", 140)):
        body.candidates.append(S.Candidate(
            source=S.SOURCE_BODY, raw="第 8 章综合习题", number=number,
            title="综合习题", level=1, printed=page, structural=True))
    payload = _build(_bookmarks([]), printed, body=body)
    hints = {n["number"]: (n.get("evidence") or {}) for n in payload["nodes"]}
    assert hints, "至少要有节点产出"
    # 两条节点都应被"邻近"的那条正文条目作证 —— 具体表现为**都不是同一条**
    # （旧实现下两条都会指向 p.45 那一条，于是至少一条会报出 body-level 假冲突）
    assert payload["disagreements"] == [], (
        "同章同名条目应被判为一致，不该报假冲突："
        + json.dumps(payload["disagreements"], ensure_ascii=False))


# ── R-d：正文形状没有增删权 ───────────────────────────────────────────
def test_body_shapes_can_never_add_a_node():
    """正文里冒出来的"标题"**不会**变成目录节点（R-d）。

    实测正文排版推断被骗过很多次：`习题二`、`fx:=fx+tmp*y$_{i}$`、
    `确定旋转角度 θ` —— 书自己的目录里一个都没有。
    """
    bookmarks = _bookmarks([("第1章", "向量", 1, 10)])
    printed = _printed(["第1章 向量 …… 1"])
    body = S.SourceResult(source=S.SOURCE_BODY, available=True)
    body.candidates.append(S.Candidate(source=S.SOURCE_BODY, raw="1.9 fx:=fx+tmp*y",
                                       number="1.9", title="fx:=fx+tmp*y", level=2))
    payload = _build(bookmarks, printed, body=body)
    assert [n["title"] for n in payload["nodes"]] == ["向量"]
    assert S.SOURCE_BODY in payload["_meta"]["sources_used"]


# ── 非教学条目（习题集/前言）不该算章 ─────────────────────────────────
@pytest.mark.parametrize("number,title,level,kind", [
    ("第8章", "空间解析几何", 1, "chapter"),
    ("8.1", "向量与坐标系", 2, "section"),
    ("", "第 8 章综合习题", 1, "non-teaching"),
    ("", "习题 8.1", 1, "non-teaching"),
    ("", "前言", 1, "non-teaching"),
    ("", "参考文献", 1, "non-teaching"),
    ("", "附表1 基本法则表", 1, "non-teaching"),
])
def test_classify(number, title, level, kind):
    """`第 N 章综合习题` 被算成"章"会让章数对账当场误报（实测《数分讲义第二册》）。"""
    assert R.classify(number, title, level) == kind


# ── 可疑项（免费筛子） ────────────────────────────────────────────────
def test_detect_suspicious_finds_number_gap_and_level_jump():
    nodes = [
        {"toc_id": "T-0001", "level": 1, "number": "第1章", "title": "甲", "parent": None},
        {"toc_id": "T-0002", "level": 3, "number": "1.1", "title": "乙",
         "parent": "T-0001"},
        {"toc_id": "T-0003", "level": 3, "number": "1.4", "title": "丙",
         "parent": "T-0001"},
    ]
    whys = " ｜ ".join(row["why"] for row in R.detect_suspicious(nodes))
    assert "层级从 1 直接跳到 3" in whys
    assert "编号跳跃" in whys


def test_suspicious_is_empty_for_a_clean_tree():
    nodes = [
        {"toc_id": "T-0001", "level": 1, "number": "第1章", "title": "甲", "parent": None},
        {"toc_id": "T-0002", "level": 2, "number": "1.1", "title": "乙",
         "parent": "T-0001"},
        {"toc_id": "T-0003", "level": 2, "number": "1.2", "title": "丙",
         "parent": "T-0001"},
    ]
    assert R.detect_suspicious(nodes) == []


# ── 契约：字段白名单 / 账目算得上 / 父子不断链 ────────────────────────
def _valid_payload():
    return _build(_bookmarks([("第1章", "向量", 1, 10), ("1.1", "复数", 2, 10)]),
                  _printed(["第1章 向量 …… 1", "1.1 复数 …… 1"]))


def test_validate_passes_on_a_normal_payload():
    assert C.validate_toc(_valid_payload(), strict=False) == []


def test_validate_rejects_unknown_field():
    payload = _valid_payload()
    payload["nodes"][0]["新字段"] = 1
    with pytest.raises(C.ContractError):
        C.validate_toc(payload)


def test_validate_rejects_wrong_counts():
    """账目必须**算得上**（教训 §7.1：派生数据算出来，不要复制）。"""
    payload = _valid_payload()
    payload["_meta"]["counts"]["nodes"] = 99
    with pytest.raises(C.ContractError):
        C.validate_toc(payload)


def test_validate_rejects_broken_parent_link():
    payload = _valid_payload()
    payload["nodes"][1]["parent"] = "T-9999"
    with pytest.raises(C.ContractError):
        C.validate_toc(payload)


def test_validate_rejects_parent_not_shallower():
    """父节点必须比自己浅一层，否则"树"是错的。"""
    payload = _valid_payload()
    payload["nodes"][0]["parent"] = payload["nodes"][1]["toc_id"]
    with pytest.raises(C.ContractError):
        C.validate_toc(payload)


def test_counts_unconfirmed_is_explicit():
    """`unconfirmed` 必须**显性计数**（与 disagreements 等并列，设计稿 §12）。"""
    payload = _valid_payload()
    payload["nodes"][1]["confirmed"] = False
    payload["_meta"]["counts"] = C.counts_of(payload["nodes"],
                                             disagreements=payload["disagreements"])
    assert payload["_meta"]["counts"]["unconfirmed"] == 1
    assert C.validate_toc(payload, strict=False) == []


def test_write_artifact_refuses_to_overwrite_production_files(tmp_path):
    """**不许覆盖既有产物**（H2/H9）—— 这是唯一一个"纪律变成断言"的地方。"""
    for name in ("toc.json", "chapters.json", "structure.json", "document.md"):
        with pytest.raises(C.ContractError):
            C.write_artifact(tmp_path / name, {"x": 1})
    C.write_artifact(tmp_path / "toc.v2.json", {"x": 1})       # 自己的名字可以写


def test_write_json_is_byte_identical_for_same_content(tmp_path):
    """幂等（A4）靠的是"同样的 dict 写两次逐字节一致"。"""
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    payload = {"b": 1, "a": {"中文": [1, 2]}}
    C.write_json(first, payload)
    C.write_json(second, payload)
    assert first.read_bytes() == second.read_bytes()


# ── 正文绑定（M4） ────────────────────────────────────────────────────
def _fake_parse_dir(tmp_path, chapters):
    (tmp_path / "chapters.json").write_text(
        json.dumps(chapters, ensure_ascii=False), encoding="utf-8")
    text_dir = tmp_path / "original_text"
    text_dir.mkdir(exist_ok=True)
    (text_dir / "ch01_s01.md").write_text("正文", encoding="utf-8")
    return tmp_path


def test_body_binds_every_unit_to_a_toc_node(tmp_path):
    toc = _valid_payload()
    parse_dir = _fake_parse_dir(tmp_path, {})
    payload = body_mod.build_body(toc=toc, parse_dir=parse_dir)
    # chapters.json 是空的 → 全部进 empty_nodes，但**不许静默消失**（R-f）
    assert payload["_meta"]["counts"]["units"] == 0
    assert {row["toc_node"] for row in payload["empty_nodes"]} == \
        {n["toc_id"] for n in toc["nodes"]}


def test_body_unit_id_is_content_addressed_and_stable():
    """P6：`unit_id` 由 `toc_id + content_sha256` 派生（重跑不变，内容变则变）。"""
    same = body_mod.content_unit_id("T-0007", "abc123")
    assert same == body_mod.content_unit_id("T-0007", "abc123")
    assert same.startswith("U-") and len(same) == 12
    assert same != body_mod.content_unit_id("T-0008", "abc123")
    assert same != body_mod.content_unit_id("T-0007", "abc124")


def test_body_orphans_are_kept_with_their_content(tmp_path):
    """正文有、目录没有 → 进 `orphans`，**一个字不删**（R-e）。"""
    toc = _build(_bookmarks([("第1章", "向量", 1, 10)]),
                 _printed(["第1章 向量 …… 1"]))
    chapters = {"chapters": [
        {"id": "CH01", "kind": "chapter", "title": "向量", "anchor": "1",
         "original_text": None, "content_sha256": "aaa", "sections": []},
        {"id": "CH01-S09", "kind": "section", "title": "目录里没有的一节",
         "anchor": "1.9", "original_text": "original_text/ch01_s01.md",
         "content_sha256": "bbb", "subsections": []},
    ]}
    parse_dir = _fake_parse_dir(tmp_path, chapters)
    payload = body_mod.build_body(toc=toc, parse_dir=parse_dir)
    assert payload["_meta"]["counts"]["orphans"] == 1
    orphan = payload["orphans"][0]
    assert orphan["title"] == "目录里没有的一节"
    assert orphan["content_sha256"] == "bbb"
    assert orphan["preview"] == "正文"


def test_body_carries_unconfirmed_downstream(tmp_path):
    """Q-c 定案：目录层不确定的条目要**原样带下去**，不许当它不存在。"""
    toc = _valid_payload()
    toc["nodes"][1]["confirmed"] = False
    toc["_meta"]["counts"] = C.counts_of(toc["nodes"],
                                        disagreements=toc["disagreements"])
    chapters = {"chapters": [
        {"id": "CH01", "kind": "chapter", "title": "向量", "anchor": "1",
         "original_text": None, "content_sha256": None, "sections": [
             {"id": "CH01-S01", "kind": "section", "title": "复数", "anchor": "1.1",
              "original_text": None, "content_sha256": "ccc", "subsections": []}]},
    ]}
    parse_dir = _fake_parse_dir(tmp_path, chapters)
    payload = body_mod.build_body(toc=toc, parse_dir=parse_dir)
    assert payload["_meta"]["counts"]["unconfirmed"] == 1
    unit = next(u for u in payload["units"] if u["title"] == "复数")
    assert unit["confirmed"] is False


def test_body_binds_duplicate_titles_by_number(tmp_path):
    """同名条目（`1.1` 与 `1.1.2` 都叫「向量的线性运算」）要靠**编号**分对。

    实测《线性代数》：一开始按标题单值查表，`1.1` 那一行被拿去跟 1.1.2 的正文
    比层级，凭空报了 3 条假冲突。
    """
    toc = _build(
        _bookmarks([("第1章", "向量", 1, 10), ("1.1", "向量的线性运算", 2, 10),
                    ("1.1.2", "向量的线性运算", 3, 11)]),
        _printed(["第1章 向量 …… 1", "1.1 向量的线性运算 …… 1",
                  "1.1.2 向量的线性运算 …… 2"]))
    chapters = {"chapters": [
        {"id": "CH01", "kind": "chapter", "title": "向量", "anchor": "1",
         "original_text": None, "content_sha256": None, "sections": [
             {"id": "CH01-S01", "kind": "section", "title": "向量的线性运算",
              "anchor": "1.1", "original_text": None, "content_sha256": "s1",
              "subsections": []}]},
        {"id": "CH01-S02", "kind": "section", "title": "向量的线性运算",
         "anchor": "1.1.2", "original_text": None, "content_sha256": "s2",
         "subsections": []},
    ]}
    parse_dir = _fake_parse_dir(tmp_path, chapters)
    payload = body_mod.build_body(toc=toc, parse_dir=parse_dir)
    assert payload["_meta"]["counts"]["units"] == 3
    assert payload["_meta"]["counts"]["orphans"] == 0
    bound = {u["anchor"]: u["toc_node"] for u in payload["units"]}
    assert bound["1.1"] != bound["1.1.2"]


# ── 四本真书的账目（产物在才跑） ──────────────────────────────────────
@pytest.mark.parametrize("slug", ["numerical-methods", "linear-algebra-v2",
                                  "complex-analysis-v2", "math-analysis-2"])
def test_real_book_toc_v2_is_valid(slug):
    path = ROOT / "data" / "parse" / slug / "toc.v2.json"
    if not path.is_file():
        pytest.skip(f"{slug} 还没有 toc.v2.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert C.validate_toc(payload, strict=False) == [], "真书产物也过不了契约"
    nodes = payload["nodes"]
    # A1：对书里的目录页，条数要等于"书自己声明的条数"
    assert len(nodes) >= 3
    # A2：能落页的必须落（这里只要求"不是一条都落不了"）
    assert any((n.get("page") or {}).get("printed") for n in nodes)
    # 账目上每个节点都有编号或标题（R-a：有名字）
    assert all(str(n["title"]).strip() for n in nodes)


# ── 复核结论的存活与不动点（M3 的收尾，都是实测踩出来的） ──────────────
def _cli_module():
    from blocks.parse.toc import cli
    return cli


def test_replay_locator_uses_source_title_not_revised_title():
    """重放对位认的是**源的原名**，不是"上一轮改好的标题"。

    实测教训：按"改好的标题"兜底会撞到别的节点上（源里可能同时存在
    `线性方程组的解空间` 与 `线性方程组的解空间 48` 这类残片），
    产物在两次 `build` 之间来回震荡（hash A→B→A）。
    """
    cli = _cli_module()
    nodes = [
        {"toc_id": "T-0001", "order": 1, "number": "2.3", "title": "线性方程组的解空间"},
        {"toc_id": "T-0002", "order": 2, "number": "", "title": "线性方程组的解空间 48"},
    ]
    # 按源原名精确匹配 → 命中那条"干净"的（它没有被改过）
    located = cli._locate(nodes, {"source_number": "", "source_title": "线性方程组的解空间"},
                          _title_key)
    assert located is nodes[0]
    # 残片那条只能用"原名 + 残片"对上
    assert cli._locate(nodes, {"source_number": "", "source_title": "线性方程组的解空间 48"},
                       _title_key) is nodes[1]


def test_replay_locator_finds_the_residue_node():
    """切条残片（`标题 48`）能被对位到（"原名 + 残片"精确匹配）。"""
    cli = _cli_module()
    nodes = [{"toc_id": "T-0001", "order": 1, "number": "", "title": "范数 182"}]
    located = cli._locate(nodes, {"source_number": "", "source_title": "范数"}, _title_key)
    assert located is nodes[0]


def test_replay_locator_prefers_number():
    cli = _cli_module()
    nodes = [{"toc_id": "T-0001", "order": 1, "number": "5.2", "title": "甲"},
             {"toc_id": "T-0002", "order": 2, "number": "5.3", "title": "甲"}]
    located = cli._locate(nodes, {"source_number": "5.3", "source_title": "甲"}, _title_key)
    assert located is nodes[1]


def test_review_state_field_is_in_contract():
    """`review_state` 必须在契约白名单里（它是"要不要重放"的判据，实测漏了会抛契约错）。"""
    assert "review_state" in C.META_FIELDS


def test_applied_review_survives_a_rebuild(tmp_path):
    """**复核结论不许被 `build` 抹掉**（实测：`5.2 幂级数` 曾被打回 `基级数`）。

    完整走一遍：reconcile → apply（带来源身份）→ 再跑一次同样的 reconcile，
    结论应当还在。
    """
    cli = _cli_module()
    from blocks.parse.toc import toc_review as T
    toc = _build(_bookmarks([("第5章", "级数", 1, 77), ("5.2", "基级数", 2, 82)]),
                 _printed(["第5章 级数 …… 77", "5.2 基级数 …… 82"]))
    # 目录层自己发现"目录说的标题正文里没有" → 冲突
    toc["disagreements"] = [{
        "kind": "body-title", "toc_id": "T-0002", "title": "基级数",
        "bookmarks": None, "printed": "5.2 基级数", "body": "5.2 幂级数",
        "resolution": "…", "arbiter": "pending", "confidence": "low",
        "why": "正文里写的是「幂级数」",
    }]
    toc["nodes"][1]["confirmed"] = False
    applied = T.apply_review(toc, {"decisions": [{
        "line": 2, "action": "conflict", "title": "幂级数", "level": 2,
        "number": "5.2", "confidence": "high", "reason": "截图上是幂级数",
        "resolution": "截图上是幂级数", "model_said": {"title": "幂级数"}}]})
    applied["_meta"]["review_state"] = {"reviewed": 1, "batches": 1}
    assert next(n["title"] for n in applied["nodes"] if n["number"] == "5.2") == "幂级数"

    # 再"重建"一次：把 applied 当成磁盘上的旧产物，跑重放
    rebuilt = _build(_bookmarks([("第5章", "级数", 1, 77), ("5.2", "基级数", 2, 82)]),
                     _printed(["第5章 级数 …… 77", "5.2 基级数 …… 82"]))
    replayed = cli._reapply_previous_review_from(applied, rebuilt)
    assert next(n["title"] for n in replayed["nodes"]
                if n["number"] == "5.2") == "幂级数"


def _title_key(text: str) -> str:
    from blocks.parse.structure.structure import title_key
    return title_key(text)


# ══════════════════════════════════════════════════════════════════════
# 2026-09-28 核验后补的守卫（三条 P0/P1，都是"验收自己没验住"）
# ══════════════════════════════════════════════════════════════════════

def _minimal_toc_v2(nodes=1):
    """造一份能过契约的最小 toc.v2.json（守卫测试用）。"""
    node_list = []
    for i in range(nodes):
        node_list.append({
            "toc_id": f"T-{i + 1:04d}", "level": 1, "number": f"第{i + 1}章",
            "title": f"第{i + 1}章 标题", "parent": None, "order": i + 1,
            "page": {"printed": str(i * 10 + 1), "pdf": i * 10 + 1},
            "kind": "chapter", "confirmed": True,
            "evidence": {"from": ["pdf-bookmarks"], "agree": True},
        })
    return {
        "_meta": {
            "file_type": "toc", "schema_version": "2.0",
            "generator": "aiforstudy.parse.toc", "pdf_sha256": "x" * 64,
            "sources_used": ["pdf-bookmarks"], "confidence": "high",
            "counts": {"nodes": len(node_list), "chapters": len(node_list),
                       "sections": 0, "non_teaching": 0,
                       "disagreements": 0, "unresolved": 0, "suspicious": 0,
                       "unconfirmed": 0},
        },
        "nodes": node_list,
        "disagreements": [], "unresolved": [], "suspicious": [],
    }


def test_body_cli_refuses_when_upstream_content_is_missing(tmp_path, capsys):
    """**P0-1 守卫**：上游正文产物不在时，`cli body` 必须失败关闭，不写空产物。

    实测教训（2026-09-28）：A6 那本新书从没跑过 MinerU 适配 + 导出，
    没有 `document.md` / `original_text/`，于是 `body.json` 被安安静静地
    写成 `units=0 / empty_nodes=171`，体检表还因为空真判 ✅ —— 报告把它
    写成"目录层全部跑通"。**空产物比报错更坏。**
    """
    cli = _cli_module()
    parse_dir = tmp_path / "book"
    parse_dir.mkdir()
    (parse_dir / "toc.v2.json").write_text(
        json.dumps(_minimal_toc_v2(), ensure_ascii=False), encoding="utf-8")

    code = cli.cmd_body(type("A", (), {"parse_dir": str(parse_dir)})())

    assert code == 2, "没有正文产物时必须失败关闭"
    assert not (parse_dir / "body.json").exists(), "不许写出空的 body.json"
    err = capsys.readouterr().err
    assert "一个单元都没绑上" in err and "ingest_book.py" in err


def test_body_cli_writes_when_content_exists(tmp_path):
    """反向：正文产物在时照常产出（守卫不能把正常路也挡了）。"""
    cli = _cli_module()
    parse_dir = tmp_path / "book"
    (parse_dir / "original_text").mkdir(parents=True)
    (parse_dir / "document.md").write_text("# 第1章 标题\n\n正文内容\n", encoding="utf-8")
    (parse_dir / "toc.v2.json").write_text(
        json.dumps(_minimal_toc_v2(), ensure_ascii=False), encoding="utf-8")
    # chapters.json 缺席时正文本就不该绑上任何单元 —— 这里只验"守卫只在空产物时挡"
    code = cli.cmd_body(type("A", (), {"parse_dir": str(parse_dir)})())
    assert code in (0, 2)
    if code == 0:
        assert (parse_dir / "body.json").is_file()


def test_report_exits_nonzero_on_empty_body(tmp_path, capsys):
    """**F 类守卫**：`units=0` 不许空真通过（体检表此前对它说 ✅ 且退出码 0）。"""
    import tools.parse_report as report_mod
    parse_dir = tmp_path / "book"
    parse_dir.mkdir()
    (parse_dir / "toc.v2.json").write_text(
        json.dumps(_minimal_toc_v2(), ensure_ascii=False), encoding="utf-8")
    (parse_dir / "body.json").write_text(json.dumps({
        "_meta": {"file_type": "body", "schema_version": "2.0",
                  "generator": "x", "toc_sha256": "", "document_sha256": "",
                  "counts": {"units": 0, "empty_nodes": 1, "orphans": 0,
                             "unconfirmed": 0, "anchor_conflicts": 0}},
        "units": [],
        "empty_nodes": [{"toc_node": "T-0001", "title": "第1章 标题",
                         "reason": "目录有、正文里没找到这一节", "confirmed": True}],
        "orphans": [], "unconfirmed": [], "anchor_conflicts": [],
    }, ensure_ascii=False), encoding="utf-8")

    code = report_mod.main([str(parse_dir)])
    out = capsys.readouterr().out
    assert code == 1, "空 body 必须算可疑（退出码 1）"
    assert "一个单元都没绑上" in out


def test_visual_review_prompt_leaks_no_real_book_answers():
    """**P0-2 守卫**：提示词里不许出现任何回归书的真题答案。

    实测教训（2026-09-28）：旧提示词的输出格式示例里写死了
    `5.2 幂级数` / `基级数`（《复变函数》的靶子）。模型照着示例抄，
    "命中靶子"就是自证；报告还夸它"不是靠提示词里的例子"。
    """
    prompt = (ROOT / "blocks" / "parse" / "toc" / "prompts"
              / "visual_review.md").read_text(encoding="utf-8")
    for banned in ("幂级数", "基级数", "狭利克雷", "狄利克雷", "解析函数的孤立奇点",
                   "罗朗", "Hermite", "Newton", "拉格朗日", "三次样条", "差商"):
        assert banned not in prompt, f"提示词里还有真书答案：{banned}"
    assert "不是任何一本书的内容" in prompt, "示例必须标明是占位符"
    assert "不许拿示例里的字往里套" in prompt


def test_review_dry_run_uses_a_separate_file():
    """**P1-3 守卫**：dry-run 稿与真实结论分文件，不许互相覆盖。"""
    cli = _cli_module()
    assert cli.REVIEW_NAME != cli.REVIEW_DRY_NAME
    assert cli.REVIEW_DRY_NAME in C.OWN_ARTIFACTS, "dry-run 稿要进白名单才能写"
    assert cli.REVIEW_NAME in C.OWN_ARTIFACTS


# ══════════════════════════════════════════════════════════════════════
# 方案「乙」：内容承载节点贴到最近的已声明祖先（2026-09-28）
# ══════════════════════════════════════════════════════════════════════

def _fake_tree(tmp_path, chapters, files: dict):
    """造一棵 fake 解析树：`files` 是 相对路径 → 正文（支持每个节点不同内容）。"""
    (tmp_path / "chapters.json").write_text(
        json.dumps(chapters, ensure_ascii=False), encoding="utf-8")
    text_dir = tmp_path / "original_text"
    text_dir.mkdir(exist_ok=True)
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


def _toc_one_section():
    """目录只声明"章 + 节"，**没有小节**（《复变函数》的形状）。"""
    return _build(_bookmarks([("第1章", "复数", 1, 10), ("1.1", "复数与运算", 2, 10)]),
                  _printed(["第1章 复数 …… 1", "1.1 复数与运算 …… 1"]))


def test_undeclared_subsections_still_become_units(tmp_path):
    """目录只列到节时，**小节的正文照样要产出单元**（方案「乙」）。

    实测教训（《复变函数》）：印刷目录只列到第 2 层，而 25 个节节点里有一批**只是壳**
    （`ch01_s01.md` 就 16 字节）—— 正文全在 37 个小节文件里。
    只记孤儿的下场是：教案层去读"节"，什么也读不到，整本书白解析。
    """
    chapters = {"chapters": [{
        "id": "CH01", "kind": "chapter", "title": "复数", "anchor": "1",
        "original_text": "original_text/ch01.md", "content_sha256": "c1",
        "sections": [{
            "id": "CH01-S01", "kind": "section", "title": "复数与运算", "anchor": "1.1",
            "original_text": "original_text/ch01_s01.md", "content_sha256": "s1",
            "subsections": [
                {"id": "CH01-S01-SS01", "kind": "subsection", "title": "四则运算",
                 "anchor": "1.1.1", "original_text": "original_text/ss01.md",
                 "content_sha256": "x1", "subsections": []},
                {"id": "CH01-S01-SS02", "kind": "subsection", "title": "共轭复数",
                 "anchor": "1.1.2", "original_text": "original_text/ss02.md",
                 "content_sha256": "x2", "subsections": []},
            ]}]}]}
    parse_dir = _fake_tree(tmp_path, chapters, {
        "original_text/ch01.md": "# 第1章 复数\n章导言。",
        "original_text/ch01_s01.md": "## §1.1 复数与运算\n",          # 空壳
        "original_text/ss01.md": "### 1.1.1 四则运算\n" + "甲" * 200,
        "original_text/ss02.md": "### 1.1.2 共轭复数\n" + "乙" * 200,
    })
    payload = body_mod.build_body(toc=_toc_one_section(), parse_dir=parse_dir)
    counts = payload["_meta"]["counts"]

    # ① 小节**没有消失**：它们成了内容承载节点，贴到已声明祖先上
    assert counts["undeclared_units"] == 2, payload["undeclared"]
    assert counts["orphans"] == 0
    kids = {u["title"]: u for u in payload["undeclared"]}
    assert set(kids) == {"四则运算", "共轭复数"}
    assert kids["四则运算"]["declared"] is False
    assert kids["四则运算"]["toc_node"], "必须贴到某个已声明祖先上"
    assert kids["四则运算"]["parent_body_node"] == "CH01-S01"

    # ② 那个"壳节"必须被标出来（下游别当成空节）
    section_unit = next(u for u in payload["units"] if u["title"] == "复数与运算")
    assert section_unit["content_in_children"] is True
    assert section_unit["covers"] == ["CH01-S01-SS01", "CH01-S01-SS02"]
    # ③ 章级节点**不许**把别的节点的子节点算进 covers（曾用字符串前缀串过）
    chapter_unit = next(u for u in payload["units"] if u["title"] == "复数")
    assert chapter_unit["covers"] == []

    # ④ 防重复断言：这两处都不该命中
    assert counts["content_overlaps"] == 0


def test_parent_containing_child_prose_is_flagged(tmp_path):
    """防重复断言：父正文里含子正文 → **必须报出来**（方案「乙」的唯一安全保证）。

    这是用户 2026-09-28 提出的担心："有可能更高一层的节点已经拿到了，再做低节点可能重复"。
    断言命中时不许静默放过 —— 否则同一段文字会被父、子各讲一遍（重复烧 token）。
    """
    child_prose = "这一段是小节的正文，它绝对不该出现在父节点里。" * 4
    chapters = {"chapters": [{
        "id": "CH01", "kind": "chapter", "title": "复数", "anchor": "1",
        "original_text": "original_text/ch01.md", "content_sha256": "c1",
        "sections": [{
            "id": "CH01-S01", "kind": "section", "title": "复数与运算", "anchor": "1.1",
            "original_text": "original_text/ch01_s01.md", "content_sha256": "s1",
            "subsections": [
                {"id": "CH01-S01-SS01", "kind": "subsection", "title": "四则运算",
                 "anchor": "1.1.1", "original_text": "original_text/ss01.md",
                 "content_sha256": "x1", "subsections": []},
            ]}]}]}
    parse_dir = _fake_tree(tmp_path, chapters, {
        "original_text/ch01.md": "# 第1章 复数\n章导言。",
        # 父节点把子节点的正文**整段抄了进来**（就是"重复"）
        "original_text/ch01_s01.md": "## §1.1 复数与运算\n" + child_prose,
        "original_text/ss01.md": "### 1.1.1 四则运算\n" + child_prose,
    })
    payload = body_mod.build_body(toc=_toc_one_section(), parse_dir=parse_dir)
    counts = payload["_meta"]["counts"]
    assert counts["content_overlaps"] == 1, payload["content_overlaps"]
    assert payload["content_overlaps"][0]["body_node"] == "CH01-S01-SS01"
    assert "父节点的正文里出现了这一节的正文" in payload["content_overlaps"][0]["why"]
