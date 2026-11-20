"""三条**整本派生的视图**不变量：索引 / 概念登记 / 快照（规格 §5 判据 **A1 A2 A3**）。

## 为什么这三条要一起测

它们守的东西有一个共同点：**少了东西也不会报错**。

| 视图 | 并发出的事（规格 §3.1） | 现象 |
|---|---|---|
| `lessons.index.json` | 两个 worker 各读各改 → **后写的盖掉先写的**（S5） | 一整章的行凭空消失，进度表却说"没问题" |
| `concepts.json` 的 occurrence | 同上（S6）；另外概念重抽会把教案引用行**全部抹掉** | 概念抽屉里少列引用它的节，读者点不到 |
| `snapshot.json` | worker 每单元重建 → 读到别的章的半成品（S7） | 提示词里带过期状态，误导模型 / 判据误报 |

所以每条测试都走**同一个套路**：先造一个正确的产物理，再**故意弄坏一处**，
断言 doctor 真的报出来（而不是"看起来没问题的假绿"）。

## 产物从哪来（2026-10-03 改造）

原来这里有一个内联的 `_book()`，它自己就能在 `tmp_path` 里造产物；整模块之所以被
`pytestmark` 跳过，是因为模块里**还有一条**测试读真实交付页（`inv_delivery_pages_concepts_are_fresh`
跑在当前工作区上）。现在：

* 产物由 `tests/fixtures/synth_book.py` 统一造（可复用、支持 `broken=` 故意弄坏）；
* 读真实页面那条断言**下沉为 per-test 守卫**（`pytest.skip`），并补了一条合成等价覆盖。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from fixtures import synth_book                                        # noqa: E402


def _load_doctor():
    spec = importlib.util.spec_from_file_location("doctor_views_under_test",
                                                 ROOT / "tools" / "doctor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


doc = _load_doctor()


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _book(tmp_path: Path, *, units=None, lesson_overrides=None) -> dict:
    """一套最小但自洽的产物：解析 + 教案 + 索引 + 快照（由离线合成夹具造）。

    `units=None` 用夹具的默认形状（2 章 × 2 节 —— 见 M0 夹具规格）；
    传 `("CH01-S01",)` 这类子集仍照旧，教学内容按 unit 名现算。
    """
    return synth_book.build(tmp_path, units=units, lesson_overrides=lesson_overrides,
                            with_delivery=False)


def _use(monkeypatch, book) -> None:
    monkeypatch.setattr(doc, "PARSE", book["parse"])
    monkeypatch.setattr(doc, "LESSONS", book["lesson"])


# ────────────────────────────────────────── A1 索引不丢行

def test_a1_passes_on_a_consistent_book(tmp_path, monkeypatch):
    book = _book(tmp_path)
    _use(monkeypatch, book)
    assert doc.inv_lesson_index_is_complete() == []


def test_a1_catches_the_parallel_lost_update(tmp_path, monkeypatch):
    """**这就是并行丢行**：索引里只剩先写的那一章，后写的把另一章盖掉了。"""
    book = _book(tmp_path, units=("CH01-S01", "CH02-S01"))
    _use(monkeypatch, book)
    index = json.loads((book["lesson"] / "lessons.index.json").read_text(encoding="utf-8"))
    index["units"] = [r for r in index["units"] if r["unit_id"].startswith("CH01")]
    _write(book["lesson"] / "lessons.index.json", index)

    out = doc.inv_lesson_index_is_complete()
    assert len(out) == 1 and out[0]["不变量"] == "教案索引完整"
    assert "CH02-S01" in out[0]["说明"]


def test_a1_catches_a_ghost_row(tmp_path, monkeypatch):
    """反方向也要查：索引说"做过"，磁盘上没有 —— 幽灵行。"""
    book = _book(tmp_path)
    _use(monkeypatch, book)
    index = json.loads((book["lesson"] / "lessons.index.json").read_text(encoding="utf-8"))
    index["units"].append({"unit_id": "CH09-S99", "status": "done"})
    _write(book["lesson"] / "lessons.index.json", index)

    out = doc.inv_lesson_index_is_complete()
    assert len(out) == 1 and "CH09-S99" in out[0]["说明"]


# ────────────────────────────────────────── A2 概念登记不丢

def test_a2_passes_when_every_reference_is_registered(tmp_path, monkeypatch):
    book = _book(tmp_path)
    _use(monkeypatch, book)
    assert doc.inv_concept_registration_is_complete() == []


def test_a2_catches_a_lost_occurrence(tmp_path, monkeypatch):
    """**这就是概念重抽把教案引用行抹掉**：教案引用 C-0001，表里却只记了别的节。"""
    book = _book(tmp_path, units=("CH01-S01", "CH02-S01"))
    _use(monkeypatch, book)
    payload = json.loads((book["parse"] / "concepts.json").read_text(encoding="utf-8"))
    payload["concepts"][0]["occurrences"] = [{"unit_id": "CH01-S01", "anchor": "1.1"}]
    _write(book["parse"] / "concepts.json", payload)

    out = doc.inv_concept_registration_is_complete()
    assert len(out) == 1 and out[0]["不变量"] == "概念登记完整"
    assert "CH02-S01" in out[0]["说明"]


def test_a2_accepts_a_gap_instead_of_an_occurrence(tmp_path, monkeypatch):
    """概念根本不在表里 → 只要进了 gaps 就算合规（**不许偷偷新增概念**）。"""
    book = _book(tmp_path, lesson_overrides={
        "CH01-S01": {"concepts": [{"id": "C-9999", "role": "introduced"}]}})
    _use(monkeypatch, book)
    assert len(doc.inv_concept_registration_is_complete()) == 1      # 既不在表、也不在 gaps

    _write(book["parse"] / "concepts.gaps.json",
           {"gaps": [{"unit_id": "CH01-S01", "id": "C-9999", "name": "可疑新概念"}]})
    assert doc.inv_concept_registration_is_complete() == []


def test_a2_also_accepts_a_gap_shard(tmp_path, monkeypatch):
    """并行时 gaps 也是**分片**的（worker 不许读-改-写整份 gaps 文件）。"""
    book = _book(tmp_path, lesson_overrides={
        "CH01-S01": {"concepts": [{"id": "C-9999", "role": "introduced"}]}})
    _use(monkeypatch, book)
    _write(book["parse"] / "concepts.gaps.parts" / "CH01-S01.json",
           {"gaps": [{"unit_id": "CH01-S01", "id": "C-9999"}]})
    assert doc.inv_concept_registration_is_complete() == []


# ────────────────────────────────────────── A3 整本快照不过期

def test_a3_passes_when_the_snapshot_is_fresh(tmp_path, monkeypatch):
    book = _book(tmp_path)
    _use(monkeypatch, book)
    assert {v["不变量"] for v in doc.inv_book_snapshot_is_fresh()} == {"历史质量不可继承"}


def test_a3_catches_a_stale_snapshot(tmp_path, monkeypatch):
    """快照还写着"这一节要复核"，而产物里已经没问题了 → 会误导下一节的提示词。"""
    book = _book(tmp_path)
    _use(monkeypatch, book)
    snap = json.loads((book["lesson"] / "snapshot.json").read_text(encoding="utf-8"))
    snap["progress"]["needs_review"] = ["CH01-S01"]        # 手写一个过期结论
    _write(book["lesson"] / "snapshot.json", snap)

    out = doc.inv_book_snapshot_is_fresh()
    assert len(out) == 1 and out[0]["不变量"] == "历史质量不可继承"
    assert "旧快照仅供读取" in out[0]["说明"]


def test_a3_catches_a_snapshot_that_missed_a_finished_unit(tmp_path, monkeypatch):
    """反方向：教案做完了、快照里没有 → "概念覆盖"这类判据会误报、白重跑。"""
    book = _book(tmp_path)
    _use(monkeypatch, book)
    snap = json.loads((book["lesson"] / "snapshot.json").read_text(encoding="utf-8"))
    snap["progress"]["done"] = []
    snap["progress"]["done_count"] = 0
    _write(book["lesson"] / "snapshot.json", snap)
    assert len(doc.inv_book_snapshot_is_fresh()) == 1


# ────────────────────────────────────────── 位置护栏：这三条真的被接进 doctor 了吗

def test_the_three_whole_book_checks_are_wired_into_doctor():
    """**位置护栏**（本项目最贵的一类 bug 是"函数写对了但没被调用"）。

    单测三条函数各自正确，不等于 `doctor` 真的会跑它们 ——
    所以这里直接断言它们在 `CHECKS` 名单里、且名字对得上。
    """
    names = [name for name, _fn in doc.CHECKS]
    for expected in ("教案索引完整", "概念登记完整", "整本快照不过期"):
        assert expected in names, f"{expected} 没接进 doctor 的检查清单"
    # ⚠️ 断言"**至少**包含"而不是"恰好 10 条"（2026-09-26 改）：
    #    接手「整本并行」交接的任务 0 时加了第 11 条「交付页的概念表不过期」——
    #    一条"必须恰好 N 条"的护栏会让每次**加**判据都变红，那不是它想守的东西。
    #    它要守的是"这三条真的接进去了"（见上面的循环）+ "数量没有意外减少"。
    assert len(doc.CHECKS) >= 10, f"不变量少过 10 条：{len(doc.CHECKS)}"


# ── 合成夹具上的等价覆盖（离线，不依赖 data/ 下任何真实产物）──────────────────

def test_the_three_whole_book_checks_pass_on_the_synthetic_book(tmp_path, monkeypatch):
    """**离线等价覆盖**：一本正确的合成教材（2 章 × 2 节）上，这三条必须全部干净。

    原来这条能力只有"真实产物还在磁盘上"时才测得到；现在它由合成夹具无条件覆盖。
    """
    book = synth_book.build(tmp_path)
    assert book.unit_ids == ("CH01-S01", "CH01-S02", "CH02-S01", "CH02-S02")
    synth_book.use_for_doctor(monkeypatch, doc, book, include_delivery=False)
    assert doc.inv_lesson_index_is_complete() == []
    assert doc.inv_concept_registration_is_complete() == []
    assert {v["不变量"] for v in doc.inv_book_snapshot_is_fresh()} == {"历史质量不可继承"}
    assert {v["不变量"] for v in doc.inv_lesson_snapshots_are_fresh()} == {"历史质量不可继承"}
    assert doc.inv_chapter_records_are_current() == []
    assert doc.inv_stuck_record_matches_loop() == []


def test_synthetic_book_lessons_are_explicitly_unverified_historical_fixtures(tmp_path):
    book = synth_book.build(tmp_path)
    for unit in book.unit_ids:
        lesson = book.lesson(unit)
        assert lesson["checks"]["problems"] == [], f"{unit}: {lesson['checks']['problems']}"
        assert lesson["checks"]["synthetic"] is True
        assert lesson["checks"]["quality_verified"] is False
    # 形状要求：2 章、每章 2 节、每节一个公式 + 一个例题
    assert len(book.chapter_ids) == 2
    for chapter in book.chapter_ids:
        chapter_units = [u for u in book.unit_ids if u.startswith(chapter + "-")]
        assert len(chapter_units) == 2, chapter
        for unit in chapter_units:
            lesson = book.lesson(unit)
            assert [e["kind"] for e in lesson["explainables"]] == ["formula"], unit
            assert len(lesson["examples"]) == 1, unit


# ── 任务 0（接手「整本并行」交接）：交付页里那份**概念表副本**不能过期 ──────────
def test_stale_concept_copy_on_a_delivery_page_is_caught(tmp_path):
    """改一下概念表里**这页用到的**概念 → 第 11 条不变量必须报这一页过期。

    为什么要有这条（实测的真实缺陷）：交付页嵌着一份概念表副本（抽屉里的名字/解释/
    "出现在哪些节"），而 `built_from` 只钉教案 revision —— 概念表变了它**天然失明**。
    当时 3 个页面分别有 16/14/11 处副本与概念表不一致，**没有任何东西会报错**。
    """
    import json as _json
    import sys as _sys
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[1]
    _sys.path.insert(0, str(root))
    from blocks.render.build import concept_copy_fingerprint

    page_copy = {"C-1": {"id": "C-1", "name": "向量", "type": "definition",
                         "brief": "b", "explanation": "e", "formal": None,
                         "occurrences": [{"unit_id": "CH01"}]}}
    recorded = concept_copy_fingerprint(page_copy)
    # 未变 → 指纹一致
    assert concept_copy_fingerprint(page_copy)["fingerprint"] == recorded["fingerprint"]
    # 改了名字 → 指纹必须变（页面里嵌的就是名字）
    changed = _json.loads(_json.dumps(page_copy))
    changed["C-1"]["name"] = "向量（改名了）"
    assert concept_copy_fingerprint(changed)["fingerprint"] != recorded["fingerprint"]
    # 改了 occurrences → 也必须变（"这个记号出现在哪些节"就是抽屉里那行）
    changed2 = _json.loads(_json.dumps(page_copy))
    changed2["C-1"]["occurrences"] = [{"unit_id": "CH01"}, {"unit_id": "CH02"}]
    assert concept_copy_fingerprint(changed2)["fingerprint"] != recorded["fingerprint"]


def test_stale_concept_copy_on_a_synthetic_page_is_caught(tmp_path, monkeypatch):
    """**离线等价覆盖**：合成交付页上改一下概念表，第 11 条必须报这一页过期。

    原来的等价断言跑在**真实工作区**上（"当前交付页是干净的"），产物一归档就测不到；
    这里把它落到合成夹具上：先干净、再弄坏、再按判据修回来（0 token）。
    """
    from blocks.render.build import CONCEPT_COPY_FIELDS, concept_copy_fingerprint
    book = synth_book.build(tmp_path)
    synth_book.use_for_doctor(monkeypatch, doc, book)
    assert doc.inv_delivery_pages_concepts_are_fresh() == []

    payload = json.loads((book.parse / "concepts.json").read_text(encoding="utf-8"))
    payload["concepts"][0]["name"] = "逐分量相等（改名了）"
    _write(book.parse / "concepts.json", payload)

    out = doc.inv_delivery_pages_concepts_are_fresh()
    assert len(out) == 1 and out[0]["不变量"] == "交付页的概念表不过期"
    assert "已过期" in out[0]["说明"], out[0]["说明"]

    # 按当前概念表重算指纹（= 重建这一页，0 token）→ 恢复干净
    concepts = {c["id"]: c for c in payload["concepts"]}
    page_copy = {cid: {f: c.get(f) for f in CONCEPT_COPY_FIELDS} for cid, c in concepts.items()}
    idx = book.page_index()
    idx["built_from_concepts"] = concept_copy_fingerprint(page_copy)
    _write(book.page / "render.index.json", idx)
    assert doc.inv_delivery_pages_concepts_are_fresh() == []


def test_doctor_has_the_concept_copy_invariant_and_it_reads_real_pages():
    """位置护栏：第 11 条真的在清单里，而且**当前工作区**跑它是干净的。"""
    if not doc.delivery_pages():
        pytest.skip("工作区里没有真实交付页（产物已归档）—— "
                    "「读真实页面」这一条不适用；合成等价覆盖见 "
                    "test_stale_concept_copy_on_a_synthetic_page_is_caught")
    import importlib.util
    import sys as _sys
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("doc_cp", root / "tools" / "doctor.py")
    fresh_doc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh_doc)
    names = [n for n, _f in fresh_doc.CHECKS]
    assert "交付页的概念表不过期" in names
    assert fresh_doc.inv_delivery_pages_concepts_are_fresh() == [], \
        "工作区里的交付页概念副本过期了 —— 重建那几页即可（0 token）"
