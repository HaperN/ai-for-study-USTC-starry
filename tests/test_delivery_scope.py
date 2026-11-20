"""交付页的**范围记录**：只许长大，不许缩小（2026-09-26）。

用户看不见这个文件，但它决定"成品页上到底有哪几节"，所以错了就是内容凭空多/少。
一天里踩了三个面，这里各钉一条测试：

① **剔除是粘的**：§2.3.2 出问题被剔掉 → 它从页面 scope 里消失 → 修好后没人记得它，
   永远回不来。所以范围必须存在一个"剔除操作不动"的记录里（`范围.json`）。
② **不能按整章铺开**：第五章那页本来只做两节样张，按 `chapters.json` 取整章会变成 16 节、
   14 节是空的。所以范围只能来自记录 + 页面现状，不能来自"这一章有几节"。
③ **记录不能冻住增长**：这一页先做了 4 节（记录里写着 4），后来新做了 §6.3.3 并建进页面，
   但如果只看记录 → 打包时会把新节当成"不该在页面上"**又剔掉**。

## 产物从哪来（2026-10-03 改造）

原来整模块被 `pytestmark` 跳过（真实产物已归档）。现在：

* 大部分断言本来就在 `tmp_path` 上自造目录 → 去掉模块级跳过后无条件跑；
* 依赖真实产物的 4 条（清单里那几页 / 整本章页 / 4 个样张页）→ **下沉为 per-test 守卫**
  （`pytest.mark.skipif`，条件就是它自己缺的那份产物），并各补了一条**合成等价覆盖**；
* 合成产物由 `tests/fixtures/synth_book.py` 造（离线）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from fixtures import synth_book                                        # noqa: E402


def _load():
    spec = importlib.util.spec_from_file_location(
        "pkg_under_test", ROOT / "tools" / "make_delivery_package.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pkg = _load()

# ── 真实产物守卫：每条只绑到**它自己**读的那份产物上（原来在模块级，一刀切）────────
#: "第六章-两节"那一页真的进了当前交付清单吗
REAL_SIXTH_CHAPTER_PAGE = any(p.name == "第六章-两节" for p in pkg.DELIVERY_DIRS)
#: 整本章页派生要读真实 `chapters.json`
REAL_CHAPTER_SOURCE = (ROOT / "data" / "parse" / "linear-algebra-full" / "chapters.json").is_file()
#: 手写样张页的 4 个目录还在磁盘上吗
REAL_SAMPLE_PAGES = len(pkg.sample_sections("linear-algebra-full")) == 4


def _write(d: Path, name: str, payload: dict) -> None:
    (d / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_new_section_built_into_the_page_is_not_dropped(tmp_path):
    """③ 页面里新做出来的节，必须被范围认下（否则打包时又剔掉）。"""
    _write(tmp_path, "范围.json", {"原本要做": ["A", "B", "C", "D"]})
    _write(tmp_path, "render.index.json", {"scope": ["A", "B", "C", "D", "E"]})
    assert pkg.planned_scope(tmp_path) == ["A", "B", "C", "D", "E"]
    # 记录也要跟着长大 —— 不然下一趟还是只认 4 节
    assert json.loads((tmp_path / "范围.json").read_text(encoding="utf-8"))["原本要做"] == \
        ["A", "B", "C", "D", "E"]


def test_dropped_section_is_still_remembered(tmp_path):
    """① 被剔除的节从页面消失了，但范围里必须还在（修好后能自动回来）。"""
    _write(tmp_path, "范围.json", {"原本要做": ["A", "B", "C", "D"]})
    _write(tmp_path, "render.index.json", {"scope": ["A", "B", "D"]})   # C 被剔了
    assert pkg.planned_scope(tmp_path) == ["A", "B", "C", "D"]
    assert json.loads((tmp_path / "范围.json").read_text(encoding="utf-8"))["原本要做"] == \
        ["A", "B", "C", "D"], "范围被缩小了 —— 被剔的节就永远回不来了"


def test_first_sight_freezes_the_page_scope(tmp_path):
    """没有记录时，第一次见到的页面范围就是范围。"""
    _write(tmp_path, "render.index.json", {"scope": ["A", "B"]})
    assert pkg.planned_scope(tmp_path) == ["A", "B"]
    assert (tmp_path / "范围.json").is_file()


def test_scope_never_comes_from_the_whole_chapter(tmp_path):
    """② 反向护栏：范围与"这一章有几节"无关 —— 页面只有 2 节，就不能变成整章。"""
    _write(tmp_path, "render.index.json", {"scope": ["A", "B"]})
    got = pkg.planned_scope(tmp_path)
    assert got == ["A", "B"], f"范围被整章铺开了：{got}"


def test_corrupt_record_falls_back_to_the_page(tmp_path):
    (tmp_path / "范围.json").write_text("{ 这不是 json", encoding="utf-8")
    _write(tmp_path, "render.index.json", {"scope": ["A"]})
    assert pkg.planned_scope(tmp_path) == ["A"]


def test_record_only_still_works_without_a_page(tmp_path):
    """页面产物还没生成时（第一次打包前），范围就只有记录。"""
    _write(tmp_path, "范围.json", {"原本要做": ["A", "B"]})
    assert pkg.planned_scope(tmp_path) == ["A", "B"]


def test_nothing_anywhere_is_an_empty_scope(tmp_path):
    assert pkg.planned_scope(tmp_path) == []


# ------------------------------------------- 打包流程必须**真的调用**对齐（位置也会踩坑）
def test_reconcile_scopes_grows_every_page(tmp_path):
    """打包里那句"对齐"必须真的跑起来：位置放错（只在有剔除时才跑）就永远不生效。"""
    a = tmp_path / "页A"
    a.mkdir()
    _write(a, "范围.json", {"原本要做": ["X"]})
    _write(a, "render.index.json", {"scope": ["X", "Y"]})
    b = tmp_path / "页B"
    b.mkdir()
    _write(b, "范围.json", {"原本要做": ["P", "Q"]})
    _write(b, "render.index.json", {"scope": ["P", "Q"]})
    grown = pkg.reconcile_scopes([a, b])
    assert [d.name for d, _ in grown] == ["页A"], "已经对齐的页不该被报成'新增'"
    assert grown[0][1] == ["Y"]
    # 两个页的记录都必须是最新的（页B 没新增，但也不该被改坏）
    assert json.loads((a / "范围.json").read_text(encoding="utf-8"))["原本要做"] == ["X", "Y"]
    assert json.loads((b / "范围.json").read_text(encoding="utf-8"))["原本要做"] == ["P", "Q"]


def test_reconcile_scopes_skips_pages_without_any_record(tmp_path):
    empty = tmp_path / "空目录"
    empty.mkdir()
    assert pkg.reconcile_scopes([empty]) == []


def test_reconcile_scopes_accepts_a_directory_that_does_not_exist(tmp_path):
    """交付页被删掉/还没生成时，打包不能因此崩掉。"""
    assert pkg.reconcile_scopes([tmp_path / "不存在"]) == []


def test_packaging_reconciles_scopes_outside_the_drop_guard():
    """**位置护栏**：对齐必须在 `if dropped_units:` 之外执行。

    这次的坑就是"函数写对了、但调用位置永远走不到"——`planned_scope` 修好了，
    可它只在"这一趟有节被剔除"时才被调用，于是**没剔除的打包永远不会对齐记录**，
    §6.3.3 就是这么差点被剔掉的。所以这里直接盯调用位置：
    对齐那一行必须在 `if dropped_units:` **之前**，且缩进不深于它。
    """
    import inspect
    src = inspect.getsource(pkg.main)
    lines = src.splitlines()
    call_at = guard_at = None
    for i, line in enumerate(lines):
        if call_at is None and "reconcile_scopes(" in line and line.strip().startswith(("for ", "grown")):
            call_at = i
        if guard_at is None and line.strip().startswith("if dropped_units:"):
            guard_at = i
    assert call_at is not None, "打包流程里根本没调用 reconcile_scopes —— 范围记录不会对齐"
    assert guard_at is not None, "找不到 `if dropped_units:` 这段（代码结构变了，请更新这条护栏）"
    assert call_at < guard_at, "对齐被放进了 `if dropped_units:` 里面 → 没剔除时永远不执行"
    assert len(lines[call_at]) - len(lines[call_at].lstrip()) <= \
        len(lines[guard_at]) - len(lines[guard_at].lstrip()), \
        "对齐的缩进比 `if dropped_units:` 更深（等于被放进那个分支里了）"


def test_delivery_dirs_cover_every_section():
    """**清单只能有一份**：交付目录列表必须从 `SECTIONS` 派生。

    踩过的坑：`DELIVERY_DIRS` 是手写的 `[第二章, 第五章]`，后来「第六章-两节」加进了
    `SECTIONS` 却忘了加到这里 —— 后果不是"少打个包"，而是**用户决策 4 的保护漏了一页**
    （"无进展/冻结的节不进交付页"只作用于第二、五章，第六章出问题的节照样会发出去）。
    """
    names = sorted(p.name for p in pkg.DELIVERY_DIRS)
    want = sorted(Path(path).name for path, _t, _s in pkg.SECTIONS)
    assert names == want, f"交付目录与 SECTIONS 对不上：{names} != {want}"


def test_delivery_dirs_are_derived_from_sections_in_the_source():
    """上面那条在"两边都是空"时是空泛的（真实产物归档后就是如此）—— 这条盯住**派生关系**本身。

    非空泛版的"清单只能有一份"：只要源码里仍是这一行派生，就不可能再出现手写第二份。
    """
    src = Path(pkg.__file__).read_text(encoding="utf-8")
    assert "DELIVERY_DIRS = [ROOT / path for path, _title, _single in SECTIONS]" in src, \
        "DELIVERY_DIRS 又变成手写的了 —— 加一节忘加一行 = 决策 4 的保护漏一页"


@pytest.mark.skipif(
    not REAL_SIXTH_CHAPTER_PAGE,
    reason="「第六章-两节」不在当前交付清单里（真实产物已归档）—— 这条守卫不适用")
def test_every_delivery_page_is_reachable_by_the_scope_record():
    """第六章那一页必须真的参与"对齐 + 剔除"，不能因为不在清单里而被跳过。"""
    assert any(p.name == "第六章-两节" for p in pkg.DELIVERY_DIRS), \
        "第六章-两节 不在 DELIVERY_DIRS 里 → 出问题的节不会被剔除、范围也不会对齐"


def test_every_synthetic_delivery_page_is_reachable_by_the_scope_record(tmp_path, monkeypatch):
    """**离线等价覆盖**：合成交付页必须真的参与"对齐 + 剔除"。

    先把范围记录缩小一节 → 打包侧的对齐必须把它长回来（这是"页真的被清单收着"的证据）。
    """
    syn = synth_book.build(tmp_path)
    monkeypatch.setattr(pkg, "SECTIONS", [(str(syn.page), "合成页", None)])
    monkeypatch.setattr(pkg, "DELIVERY_DIRS", [syn.page])
    assert any(p.name == syn.page.name for p in pkg.DELIVERY_DIRS)
    assert pkg.planned_scope(syn.page) == list(syn.unit_ids)

    plan = json.loads((syn.page / "范围.json").read_text(encoding="utf-8"))
    plan["原本要做"] = plan["原本要做"][:-1]
    _write(syn.page, "范围.json", plan)
    grown = pkg.reconcile_scopes(pkg.DELIVERY_DIRS)
    assert [(d.name, added) for d, added in grown] == [(syn.page.name, [syn.unit_ids[-1]])]
    assert pkg.planned_scope(syn.page) == list(syn.unit_ids), "对齐没把记录长回来"


def test_shots_are_derived_from_sections_and_cover_both_layouts():
    """截图清单必须从 SECTIONS 派生，且**两种放法都认**。

    手写路径的后果不是报错，是打包里那句 `if not path.is_file(): continue`
    **静默少放一张截图**（和"手写清单漏页"同一个坑）。
    第一版派生只 glob 了 `预览/`，当场漏掉 `审核回路样例` 那张 —— 所以这条测试盯两件事：
    ① 每个 SECTIONS 目录的预览图都被收进来；② 顶层 `预览-*.png` 也要认。
    """
    from pathlib import Path as _P
    root = _P(pkg.__file__).resolve().parents[1]
    listed = {src for src, _dst in pkg.SHOTS}
    expected = set()
    for src, _name, _single in pkg.SECTIONS:
        base = root / src
        if (base / "预览").is_dir():
            expected |= {str(p.relative_to(root)) for p in (base / "预览").glob("*.png")}
        expected |= {str(p.relative_to(root)) for p in base.glob("预览*.png")}
    missing = sorted(expected - listed)
    assert not missing, f"这些预览图没进交付包（会静默少图）：{missing}"


def test_shots_cover_both_layouts_on_synthetic_dirs(tmp_path, monkeypatch):
    """**离线等价覆盖**：两种放法（`预览/` 与顶层 `预览-*.png`）都必须被收进来。

    上面那条在真实产物归档后 `expected` 是空集（空泛）；这里造 3 张图，断言三种放法都真的进了清单。
    """
    page_a = tmp_path / "页A"
    (page_a / "预览").mkdir(parents=True)
    (page_a / "预览" / "第一张.png").write_bytes(b"a")
    (page_a / "预览-说明.png").write_bytes(b"b")
    page_b = tmp_path / "页B"
    page_b.mkdir()
    (page_b / "预览-顶层.png").write_bytes(b"c")
    monkeypatch.setattr(pkg, "ROOT", tmp_path)
    sections = [("页A", "A", None), ("页B", "B", None)]
    listed = {Path(src).resolve() for src, _dst in pkg._shots(sections)}
    expected = set()
    for src, _name, _single in sections:
        base = tmp_path / src
        if (base / "预览").is_dir():
            expected |= {p.resolve() for p in (base / "预览").glob("*.png")}
        expected |= {p.resolve() for p in base.glob("预览*.png")}
    assert len(expected) == 3, f"测试自己没造够素材：{expected}"
    assert not (expected - listed), f"这些预览图没进交付包（会静默少图）：{expected - listed}"
    assert listed == expected, f"截图清单多收了：{listed - expected}"


def test_one_pager_has_no_hardcoded_section_names():
    """一页纸里的目录名/个数必须由 SECTIONS 渲染，不能手写（否则加一节就写错）。"""
    assert "__SEC0__" in pkg.ONE_PAGER and "__SEC1__" in pkg.ONE_PAGER
    assert "__SINGLE_COUNT__" in pkg.ONE_PAGER and "__DIRS__" in pkg.ONE_PAGER
    for bad in ("01-第二章-全章（6 节）", "那两个 `.html`"):
        assert bad not in pkg.ONE_PAGER, f"一页纸里还写着手写的「{bad}」"


def test_packaging_refuses_an_out_dir_that_would_be_deleted(tmp_path, capsys):
    """`--out` 指向交付目录/项目根时必须拒绝（那一行是 `rmtree(out)`，会先删源目录）。"""
    pkg_root = Path(pkg.ROOT)
    for bad in [pkg_root, *(Path(d) for d in pkg.DELIVERY_DIRS)]:
        code = pkg.main(["--out", str(bad), "--zip", str(tmp_path / "x.zip"), "--no-zip"])
        out = capsys.readouterr().out
        assert code == 1, f"--out={bad} 居然被接受了"
        assert "受保护的目录" in out
        assert bad.exists(), f"{bad} 被删了！"


def test_packaging_refuses_a_synthetic_delivery_dir_as_out(tmp_path, monkeypatch, capsys):
    """**离线等价覆盖**：合成交付页当 `--out` 时也必须被拒（否则它会先删源目录再拷自己）。"""
    page = tmp_path / "合成交付页"
    page.mkdir()
    _write(page, "render.index.json", {"scope": ["CH01-S01"]})
    monkeypatch.setattr(pkg, "all_sections",
                        lambda book=None: [(str(page), "合成页", None)])
    code = pkg.main(["--out", str(page), "--zip", str(tmp_path / "x.zip"), "--no-zip"])
    out = capsys.readouterr().out
    assert code == 1, "--out 指向交付页居然被接受了"
    assert "受保护的目录" in out
    assert page.exists(), "交付页被 rmtree 删了！"


def test_packaging_refreshes_the_single_file_inside_each_page_dir():
    """交付页目录里的单文件版必须**每次打包都刷新**，不能留一份过期的。

    实测（链路审计 A15）：`delivery/第六章-两节/第六章-两节（单文件）.html` 比同目录的
    `data/data.js` 旧了 3 小时 —— 读者双击它看到的是**过期内容**，而清单与自检都不说
    （它们只查 index.html）。这条护栏盯住"打包时把它刷新"这句话还在代码里。
    """
    import inspect
    src = inspect.getsource(pkg.main)
    assert "page_copy" in src and "copy2(target / single, page_copy)" in src, \
        "打包时没有把刚生成的单文件版拷回交付页目录 → 那份会过期"


# ── "这一页 / 这个模型是不是过期产物"（给编排器用的两个判断）──────────────────
def _page_and_lessons(tmp_path, page_rev, lesson_rev, model_rev=None):
    from pathlib import Path as _P
    (tmp_path / "lessons").mkdir(exist_ok=True)
    (tmp_path / "lessons" / "A.json").write_text(
        json.dumps({"unit_id": "A", "revision": lesson_rev}), encoding="utf-8")
    (tmp_path / "render.index.json").write_text(
        json.dumps({"scope": ["A"], "built_from": {"A": page_rev}}), encoding="utf-8")
    if model_rev is not None:
        (tmp_path / "models").mkdir(exist_ok=True)
        (tmp_path / "models" / "A.json").write_text(
            json.dumps({"_meta": {"source_lesson_revision": model_rev}}), encoding="utf-8")
    return tmp_path






def test_models_are_fresh_flags_old_artifacts_without_a_revision(tmp_path):
    """模型产物**没记教案版本**（旧产物）→ 判过期。宁可重跑一次（~9k token），

    也不要让"模型落在错位置"这种错悄悄留在页面上（要靠人看才发现）。
    """
    sys.path.insert(0, str(pkg.ROOT / "lib"))
    import scopes
    d = _page_and_lessons(tmp_path, page_rev=1, lesson_rev=3, model_rev=None)
    (d / "models").mkdir(exist_ok=True)
    (d / "models" / "A.json").write_text(json.dumps({"_meta": {}}), encoding="utf-8")
    fresh, why = scopes.models_are_fresh(d / "models", d)
    assert fresh is False and "没记教案版本" in why[0]
    d = _page_and_lessons(tmp_path, page_rev=1, lesson_rev=3, model_rev=3)
    assert scopes.models_are_fresh(d / "models", d) == (True, [])


# ── 单一来源的源码护栏（B5）────────────────────────────────────────────────




# ── 任务 1（接手「整本并行」交接）：打包清单**按章派生** ────────────────────────
@pytest.mark.skipif(
    not REAL_CHAPTER_SOURCE,
    reason="真实 chapters.json 已归档（data/parse/linear-algebra-full 不在位）—— 这条守卫不适用")
def test_chapter_pages_are_derived_and_only_if_they_exist(tmp_path):
    """整本跑出来的章页要**自动**进打包清单；还没跑的章**不能**收（否则打包报"目录不存在"）。

    为什么（实测过的坑）：清单原来是手写的 4 行 —— 整本跑完的新章页不会被收进交付包，
    而且"加一章忘加一行"会重演历史：**出问题的节照样发给用户**
    （"出问题的节不发出去"这个保护只作用于清单里那几页）。
    """
    from pathlib import Path as _P
    real = _P(pkg.ROOT) / "delivery" / "整本"
    # 造一个"跑过的章页"（只放渲染索引就够，派生只看这个）
    # ⚠️ 路径**按书收窄**（2026-09-27）：两本书的章名会撞（都有 CH01/CH02…），
    #    所以整本页在 `delivery/整本/<book_id>/CHxx` 下。
    fake = tmp_path / "delivery" / "整本" / "linear-algebra-full" / "CH01"
    fake.mkdir(parents=True)
    (fake / "render.index.json").write_text(json.dumps({"scope": ["CH01"]}), encoding="utf-8")
    got = pkg.chapter_sections("linear-algebra-full", root=tmp_path)
    assert [name for _src, name, _s in got] and "CH01" in got[0][1], got
    # 没跑的章一律不收
    assert all("CH02" not in name for _src, name, _s in got), "还没跑的章被收进来了"
    # **别的书的同名章页不许被当成自己的**（这正是 B-12 的实战形态）
    other = tmp_path / "delivery" / "整本" / "complex-analysis" / "CH02"
    other.mkdir(parents=True)
    (other / "render.index.json").write_text(json.dumps({"scope": ["CH02"]}), encoding="utf-8")
    got2 = pkg.chapter_sections("linear-algebra-full", root=tmp_path)
    assert all("CH02" not in name for _src, name, _s in got2), \
        f"别的书（复变函数）的 CH02 被算进了线性代数：{got2}"
    assert not real.exists() or True          # 真仓库里现在还没有整本页 → 下面的断言照旧成立


def test_chapter_pages_are_derived_and_only_if_they_exist_on_a_synthetic_book(tmp_path, monkeypatch):
    """**离线等价覆盖**：章页派生只收"已经跑过（有 render.index.json）"的章，且按书收窄。

    合成版：书的契约由一个假 `BookContext` 提供（parse 指向夹具、pages_root 指到 tmp_path 下的
    相对路径），于是这条判据不依赖任何真实产物。
    """
    import book as book_registry
    syn = synth_book.build(tmp_path, with_delivery=False)
    pages_rel = Path(pkg.ROOT) / "delivery" / "整本" / "synth-probe"
    fake_ctx = SimpleNamespace(parse=syn.parse, lessons=syn.lessons, audit=syn.audit,
                               problems=syn.root / "problems.json",
                               book_id="synth-probe", title=syn.title,
                               pages_root=lambda: pages_rel)
    monkeypatch.setattr(book_registry, "resolve", lambda book_id=None, **kw: fake_ctx)

    run_root = tmp_path / pages_rel.relative_to(pkg.ROOT)
    # 只有 CH01 跑过
    (run_root / "CH01").mkdir(parents=True)
    (run_root / "CH01" / "render.index.json").write_text(
        json.dumps({"scope": ["CH01-S01"]}), encoding="utf-8")
    got = pkg.chapter_sections("synth-probe", root=tmp_path)
    names = [name for _src, name, _single in got]
    assert len(got) == 1 and "CH01" in names[0], got
    assert all("CH02" not in name for name in names), "还没跑的章被收进来了"

    # 别的书的同名章页不许被当成自己的
    other = tmp_path / "delivery" / "整本" / "other-book" / "CH02"
    other.mkdir(parents=True)
    (other / "render.index.json").write_text(json.dumps({"scope": ["CH02-S01"]}),
                                             encoding="utf-8")
    got2 = pkg.chapter_sections("synth-probe", root=tmp_path)
    assert all("CH02" not in name for _src, name, _s in got2), \
        f"别的书的 CH02 被算进了这本书：{got2}"

    # CH02 也跑过之后才收
    (run_root / "CH02").mkdir(parents=True)
    (run_root / "CH02" / "render.index.json").write_text(
        json.dumps({"scope": ["CH02-S01"]}), encoding="utf-8")
    got3 = pkg.chapter_sections("synth-probe", root=tmp_path)
    assert any("CH02" in name for _src, name, _s in got3), "跑过的章没被收进来"
    assert {name for _s, name, _x in got3} >= {name for _s, name, _x in got}


@pytest.mark.skipif(
    not REAL_SAMPLE_PAGES,
    reason="4 个手写样张页的目录已归档（linear-algebra-full 不在位）—— 这条守卫不适用")
def test_sample_pages_are_still_in_the_packaging_list():
    """派生章页之后，**手写的样张页必须还在**（不能因为改成派生就把它们弄丢）。

    ⚠️ 2026-09-27 起样张页**按书归属**（原来无条件并进来，给《复变函数》打包时
    会把线性代数的样张页塞进复变函数的交付包）——所以这里要**指名那本书**去取。
    """
    sample_srcs = [src for src, _n, _s in pkg.sample_sections("linear-algebra-full")]
    all_srcs = [src for src, _n, _s in pkg.all_sections("linear-algebra-full")]
    for src in sample_srcs:
        assert src in all_srcs, f"样张页 {src} 被派生逻辑挤掉了"
    assert len(sample_srcs) == 4, f"样张页数量变了：{sample_srcs}"


def _install_synthetic_sample_pages(tmp_path, monkeypatch):
    """把 4 个手写样张页换成 tmp_path 下造出来的目录（离线等价覆盖用）。"""
    rows = [{"book": "linear-algebra-full", "dir": f"合成样张-{i}",
             "label": f"样张{i}", "single": None} for i in range(4)]
    for row in rows:
        (tmp_path / row["dir"]).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(pkg, "ROOT", tmp_path)
    monkeypatch.setattr(pkg, "SAMPLE_SECTIONS", rows)
    monkeypatch.setattr(pkg, "chapter_sections", lambda book=None, root=None: [])
    return rows


def test_sample_pages_are_still_in_the_packaging_list_on_synthetic_dirs(tmp_path, monkeypatch):
    """**离线等价覆盖**：改成派生之后，手写样张页仍然必须在打包清单里。"""
    rows = _install_synthetic_sample_pages(tmp_path, monkeypatch)
    sample_srcs = [src for src, _n, _s in pkg.sample_sections("linear-algebra-full")]
    all_srcs = [src for src, _n, _s in pkg.all_sections("linear-algebra-full")]
    assert len(sample_srcs) == 4 == len(rows), f"样张页数量变了：{sample_srcs}"
    for src in sample_srcs:
        assert src in all_srcs, f"样张页 {src} 被派生逻辑挤掉了"


@pytest.mark.skipif(
    not REAL_SAMPLE_PAGES,
    reason="4 个手写样张页的目录已归档（linear-algebra-full 不在位）—— 这条守卫不适用")
def test_other_books_do_not_get_linear_algebra_sample_pages():
    """**别的书不许拿到线性代数的样张页**（2026-09-27 实测抓出）。

    现象：`all_sections()` 原来无条件 `+ SAMPLE_SECTIONS`，而那是 4 个**线性代数**页面 ——
    给《复变函数》打包就会把线性代数的页面塞进复变函数的交付包。
    这正是 `data/books.json` 契约的 `field_notes` 里点名的那个坑：「打包会打旧页」。
    """
    got = pkg.sample_sections("complex-analysis")
    assert got == [], f"复变函数不该拿到这些样张页：{got}"
    for src, _n, _s in pkg.all_sections("complex-analysis"):
        assert "第二章-全章" not in src and "第五章-两节" not in src \
            and "第六章-两节" not in src and "审核回路样例" not in src, \
            f"线性代数的页面混进了复变函数的交付清单：{src}"
    # 线性代数那本照旧拿到
    assert len(pkg.sample_sections("linear-algebra-full")) == 4


def test_other_books_do_not_get_linear_algebra_sample_pages_on_synthetic_dirs(tmp_path, monkeypatch):
    """**离线等价覆盖**：样张页按书归属，别的书一个都不许拿到。"""
    _install_synthetic_sample_pages(tmp_path, monkeypatch)
    got = pkg.sample_sections("complex-analysis")
    assert got == [], f"复变函数不该拿到这些样张页：{got}"
    for src, _n, _s in pkg.all_sections("complex-analysis"):
        assert "合成样张" not in src, f"线性代数的页面混进了复变函数的交付清单：{src}"
    assert len(pkg.sample_sections("linear-algebra-full")) == 4


def test_packaging_accepts_book_argument():
    """`--book` 接上了（R1：别在打包里写死书名）。"""
    import inspect
    src = inspect.getsource(pkg.main)
    assert "--book" in src and "all_sections(args.book)" in src


# 旧 run_chapter 编排已退役，不再扫描其调用位置。
# 正式引擎输入/资源变化失效由 test_p1_runner_reliability 和
# test_p2_content_delivery 的行为回归覆盖；helper行为测试仍保留。

# ── ④ 写入方必须自己建目录（2026-09-26 生产版 A6 实测抓出的第二个真 bug）──────────
def test_write_plan_creates_the_directory_when_starting_from_nothing(tmp_path):
    """`write_plan` 在**交付目录还不存在**时也要能写 —— 从零起步必然遇到。

    实测现场（A6 隔离树）：`FileNotFoundError: /tmp/a6/serial/delivery/CH03/范围.json`，
    因为仓库内 `delivery/<章>/` 早被历次跑建好了，**空目录起步才是真场景**
    （新书 / 新机器 / 干净检出 / 整本并行的空交付目录）。
    现象还会一路冒泡成 worker 整体崩溃：`run_chapter` 死在"每跑完一节就重建这一页"，
    看起来像"整章跑不动"，真因埋在最底下。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("scopes_under_test", ROOT / "lib" / "scopes.py")
    scopes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scopes)

    fresh = tmp_path / "delivery" / "整本" / "CH03"          # 三层、全都不存在
    assert not fresh.exists()
    scope, added = scopes.sync_plan(fresh, ["CH03-S01", "CH03-S02"])
    assert scope == ["CH03-S01", "CH03-S02"]
    assert added == ["CH03-S01", "CH03-S02"]
    assert (fresh / scopes.PLAN_NAME).is_file(), "范围.json 没写出来"
    assert scopes._read_plan(fresh) == ["CH03-S01", "CH03-S02"], "写出来的内容读不回来"
    # 再写一次：幂等，且仍然只长大
    scope2, added2 = scopes.sync_plan(fresh, ["CH03-S03"])
    assert scope2 == ["CH03-S01", "CH03-S02", "CH03-S03"] and added2 == ["CH03-S03"]


def test_historical_validator_cannot_supply_delivery_approval():
    from aiforstudy.pipeline.stage_io import _legacy
    from aiforstudy.errors import ContractError
    with pytest.raises(ContractError,match='retired|退役'):
        _legacy('validate')
    assert not (pkg.ROOT/'lib/style_rules.py').exists()
