"""合成夹具的**自检**：它自己必须是一本契约合法的书，而且每种"故意弄坏"都真的会被抓到。

为什么单独一个模块（M0 要求）：夹具是三个测试模块共同的地基 —— 地基如果悄悄塌了
（比如产品校验规则改了、夹具的教案变成有硬问题），上面那些测试会**变成假绿**。
所以这里：

* 断言夹具的**形状**（2 章 × 每章 2 节，每节一个公式 + 一个例题，产物文件齐全）；
* 断言历史夹具结构一致，同时 `doctor` 明确不继承历史质量；
* 结构破坏由对应不变量检出；三种历史快照破坏保持原字节/缺失可查且不能获得新批准。

`pytest --collect-only -q` 会把本模块与夹具一起收集（夹具本身不是测试模块，只被 import）。
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
    spec = importlib.util.spec_from_file_location("doctor_fixture_under_test",
                                                 ROOT / "tools" / "doctor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


doc = _load_doctor()


class _Patcher:
    """最小的 monkeypatch 替身（只需要 `setattr`，并在 finally 里还原）。"""

    def __init__(self):
        self.undo: list[tuple[object, str, object]] = []

    def setattr(self, obj, name, value):
        self.undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def restore(self):
        for obj, name, value in reversed(self.undo):
            setattr(obj, name, value)
        self.undo.clear()


def _violations(book) -> set[str]:
    """跑一遍 doctor 的**全部**不变量，返回被触发的那些名字。"""
    patch = _Patcher()
    try:
        synth_book.use_for_doctor(patch, doc, book)
        hit: set[str] = set()
        for name, fn in doc.CHECKS:
            try:
                out = fn()
            except Exception as exc:                                  # noqa: BLE001
                hit.add(f"{name}（doctor 自己坏了：{type(exc).__name__}: {exc}）")
                continue
            if out:
                hit.update(str(row.get("不变量")) for row in out)
        return hit
    finally:
        patch.restore()


# ── 形状与内容

def test_fixture_builds_a_two_by_two_contract_legal_book(tmp_path):
    book = synth_book.build(tmp_path)
    assert book.chapter_ids == ("CH01", "CH02")
    assert book.unit_ids == ("CH01-S01", "CH01-S02", "CH02-S01", "CH02-S02")
    for chapter in book.chapter_ids:
        assert len([u for u in book.unit_ids if u.startswith(chapter + "-")]) == 2
    for unit in book.unit_ids:
        lesson = book.lesson(unit)
        assert lesson["checks"]["synthetic"] is True and lesson["checks"]["quality_verified"] is False
        assert lesson["checks"]["problems"] == [], f"{unit}: {lesson['checks']['problems']}"
        assert [e["kind"] for e in lesson["explainables"]] == ["formula"], unit
        assert len(lesson["examples"]) == 1, unit
        assert len(lesson["examples"][0]["steps"]) >= 2, unit


def test_fixture_writes_every_file_the_contract_needs(tmp_path):
    """逐个点名（以 doctor.py / make_delivery_package.py 的真实读取路径为准）。"""
    book = synth_book.build(tmp_path)
    parse_files = ("chapters.json", "toc.v2.json", "body.json", "concepts.json",
                   "exercises.json", "images.json", "ingest.json")
    for name in parse_files:
        assert (book.parse / name).is_file(), name
    for name in ("concepts.parts", "concepts.gaps.parts", "original_text", "images"):
        assert (book.parse / name).is_dir(), name
    for unit in book.unit_ids:
        assert (book.parse / "concepts.parts" / f"{unit}.json").is_file(), unit
        assert (book.parse / "original_text" / f"{unit}.md").is_file(), unit
    for name in ("lessons.index.json", "snapshot.json"):
        assert (book.lessons / name).is_file(), name
    assert (book.lessons / "chapter-runs" / "CH01.json").is_file()
    for unit in book.unit_ids:
        assert (book.lessons / "lessons" / f"{unit}.json").is_file(), unit
        assert (book.audit / f"{unit}.loop.json").is_file(), unit
        assert (book.audit / f"{unit}.stuck.jsonl").is_file(), unit
    for name in ("render.index.json", "范围.json", "index.html"):
        assert (book.page / name).is_file(), name
    for name in ("data/data.js", "assets/app.js"):
        assert (book.page / name).is_file(), name
    # 交付页三件套里医生点名要的两个字段
    idx = book.page_index()
    assert idx["counts"]["units"] == len(idx["scope"]) == 4
    assert set(idx["built_from"]) == set(book.unit_ids)
    assert idx["built_from_concepts"]["fingerprint"]
    assert book.scope_plan()["原本要做"] == list(book.unit_ids)


def test_fixture_units_override_still_builds_a_legal_book(tmp_path):
    """`units=` 子集也必须契约合法（A1/A2 那几条测试就是靠它）。"""
    book = synth_book.build(tmp_path, units=("CH01-S01", "CH02-S01"))
    assert book.unit_ids == ("CH01-S01", "CH02-S01")
    assert _violations(book) == {"历史质量不可继承"}


# ── 结构一致的历史产物仍然没有当前质量批准

def test_every_doctor_invariant_passes_on_the_correct_book(tmp_path):
    book = synth_book.build(tmp_path)
    assert _violations(book) == {"历史质量不可继承"}


@pytest.mark.parametrize("broken", sorted(synth_book.BREAKAGES))
def test_every_breakage_is_caught_by_its_invariant(tmp_path, broken):
    """`broken=` 的**契约**：每种破坏都必须被它声明的那条不变量抓住。"""
    book = synth_book.build(tmp_path, broken=broken)
    expected = ("历史质量不可继承" if broken in {"lesson_checks","no_snapshot","snapshot"}
                else synth_book.BREAKAGE_INVARIANTS[broken])
    got = _violations(book)
    assert expected in got, f"broken={broken!r} 没被「{expected}」抓住；实际报的是 {sorted(got)}"
    if broken == "lesson_checks":
        assert "旧规则的结论" in book.lesson(book.unit_ids[0])["checks"]["problems"]
    elif broken == "no_snapshot":
        assert not (book.lessons/"snapshot.json").exists()
    elif broken == "snapshot":
        assert book.snapshot()["progress"]["needs_review"] == [book.unit_ids[0]]


def test_breakages_are_distinct_from_a_correct_book(tmp_path):
    """反向护栏：每种破坏都**真的**破坏了东西（不能是 no-op）。"""
    correct = synth_book.build(tmp_path / "correct")
    assert _violations(correct) == {"历史质量不可继承"}
    for broken in sorted(synth_book.BREAKAGES):
        book = synth_book.build(tmp_path / f"b_{broken}")
        before={str(p.relative_to(book.root)):p.read_bytes() for p in book.root.rglob("*") if p.is_file()}
        synth_book.BREAKAGES[broken](book)
        after={str(p.relative_to(book.root)):p.read_bytes() for p in book.root.rglob("*") if p.is_file()}
        assert after != before, f"broken={broken!r} 其实什么都没弄坏"
        if broken not in {"lesson_checks","no_snapshot","snapshot"}:
            assert _violations(book) != {"历史质量不可继承"}


def test_unknown_breakage_name_is_rejected_loudly(tmp_path):
    with pytest.raises(ValueError, match="未知的 broken"):
        synth_book.build(tmp_path, broken="不存在的破坏方式")


def test_fixture_does_not_read_the_real_artifact_tree(tmp_path, monkeypatch):
    """**离线保证**：夹具只写 tmp_path，`data/` 下任何路径都不读。

    做法：把 `data/` 换成一个不存在的路径 —— 造出来的书仍然必须完整可用
    （`fresh_checks` / `build_snapshot` 都只认传进去的目录）。
    """
    book = synth_book.build(tmp_path)
    missing = tmp_path / "并没有这个目录"
    monkeypatch.setattr(doc, "PARSE", book.parse)
    monkeypatch.setattr(doc, "LESSONS", book.lessons)
    monkeypatch.setattr(doc, "AUDIT", book.audit)
    monkeypatch.setattr(doc, "DELIVERY_ROOT", missing)
    monkeypatch.setattr(doc, "_PKG", synth_book.fake_package(book))
    assert doc.inv_lesson_index_is_complete() == []
    assert doc.inv_concept_registration_is_complete() == []
    assert {r["不变量"] for r in doc.inv_book_snapshot_is_fresh()} == {"历史质量不可继承"}
    assert {r["不变量"] for r in doc.inv_lesson_snapshots_are_fresh()} == {"历史质量不可继承"}
    assert doc.inv_chapter_records_are_current() == []
    assert doc.inv_stuck_record_matches_loop() == []
    assert doc.inv_delivery_pages_concepts_are_fresh() == []
    assert doc.inv_delivery_pages_are_not_stale() == []
    assert json.loads((book.page / "render.index.json").read_text("utf-8"))["scope"]
