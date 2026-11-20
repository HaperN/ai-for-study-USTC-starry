"""引用完整性（`lib/refs.py`）的回归测试 —— 缺口 A 的机器判据。

为什么这个文件必须存在：这一块是"程序要自动跑"的那道闸门（0 token、可复算），
而它挡的是**已经走到交付页上**的错（P-020 第一章的向量图配给第二章的概念、
P-027「解集 解集」、P-035 点了没处可去的 `(2.1)`）。判据一旦悄悄失效，
整条流水线会一路绿着把错的东西交给读者。
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import refs  # noqa: E402


CONCEPTS = {"C-0001": "向量", "C-0002": "同解方程组", "C-0014": "解集"}


# ── P-020：图的引用与作用域 ────────────────────────────────────────────────
def test_figure_without_any_referencing_unit_is_not_a_hard_problem():
    """没人引用的图**不上页面**就够了（渲染块记 notes），不该报成硬问题。

    为什么：这种图多半只是属于别的章（实测第一章那 12 张向量图）；报成硬问题会让
    "必须挡住"的信号被 12 条说明淹掉，闸门就形同虚设。
    """
    assert refs.check_figures(
        {"FIG-vector-segment": {"id": "FIG-vector-segment", "concepts": ["C-0001"]}},
        units={"CH02": {"figures": []}},
        concept_names=CONCEPTS) == []


def test_figure_referenced_and_concept_resolves_is_clean():
    problems = refs.check_figures(
        {"FIG-vector-segment": {"id": "FIG-vector-segment", "concepts": ["C-0001"]}},
        units={"CH01-S01": {"figures": [{"id": "FIG-vector-segment"}]}},
        concept_names=CONCEPTS)
    assert problems == []


def test_figure_pointing_at_a_stale_concept_id_is_reported():
    """概念表重抽后号被重用 —— 图登记的旧号在新表里查不到，要报出来。"""
    problems = refs.check_figures(
        {"FIG-vector-segment": {"id": "FIG-vector-segment", "concepts": ["C-0133"]}},
        units={"CH01-S01": {"figures": [{"id": "FIG-vector-segment"}]}},
        concept_names=CONCEPTS)
    assert len(problems) == 1 and "C-0133" in problems[0]


# ── P-027：概念名写了两遍 ─────────────────────────────────────────────────
def test_exact_duplicate_is_class_one_auto_merged():
    """`…的解集 [[CONCEPT:C-0014|解集]]` → 第一类：渲染层能确定性合并。"""
    notes = refs.duplicate_concept_name("称为该方程组的解集 [[CONCEPT:C-0014|解集]]。", CONCEPTS)
    assert len(notes) == 1 and "写了两遍" in notes[0]
    assert refs.overlapping_concept_name("称为该方程组的解集 [[CONCEPT:C-0014|解集]]。",
                                         CONCEPTS) == []


def test_duplicate_also_caught_when_link_has_no_display_text():
    """省略显示文字的老写法同样要抓（这时标签就是概念名本身）。"""
    notes = refs.duplicate_concept_name("称为向量 [[CONCEPT:C-0001]] 的量。", CONCEPTS)
    assert len(notes) == 1 and "向量" in notes[0]


def test_overlapping_name_is_class_two_needs_rewording():
    """`…称线性方程组不相容 [[CONCEPT|相容与不相容]]` → 第二类：机械合并会改意思，只能改措辞。"""
    named = dict(CONCEPTS, **{"C-0009": "相容与不相容"})
    text = "否则，称线性方程组不相容 [[CONCEPT:C-0009|相容与不相容]]。"
    notes = refs.overlapping_concept_name(text, named)
    assert len(notes) == 1 and "不相容" in notes[0]
    assert refs.duplicate_concept_name(text, named) == [], "它不是第一类（标签≠前面的那个词）"


def test_a_link_that_does_not_repeat_is_not_reported():
    assert refs.duplicate_concept_name("我们把 [[CONCEPT:C-0001|向量]] 加起来。", CONCEPTS) == []
    assert refs.overlapping_concept_name("我们把 [[CONCEPT:C-0001|向量]] 加起来。", CONCEPTS) == []


# ── P-035：编号引用要跳得到 ───────────────────────────────────────────────
def test_number_in_this_unit_original_is_fine():
    lesson = {"unit_id": "CH02-S02", "body_markdown": "线性方程组 (2.1) 的系数矩阵。"}
    assert refs.check_number_refs([lesson], {"CH02-S02": "本节里就有 (2.1) 这个编号。"}) == []


def test_number_defined_in_another_unit_is_not_a_problem():
    """编号在**别的节**的原文里 → 链接跨节跳过去，页面上仍然跳得到，不算问题。"""
    lesson = {"unit_id": "CH02-S03-SS02", "body_markdown": "上一节的一般线性方程组记作 (2.1)。"}
    assert refs.check_number_refs([lesson], {"CH02-S02": "这里定义 (2.1)。"}) == []


def test_number_nowhere_in_the_delivery_is_reported():
    """谁都没有这个编号 → 这一处做不成链接 = 死链，必须报。"""
    lesson = {"unit_id": "CH02", "body_markdown": "见 (9.9) 的说明。"}
    problems = refs.check_number_refs([lesson], {"CH02": "本节原文里没有那个编号。"})
    assert len(problems) == 1 and "死链" in problems[0]


def test_number_index_is_built_from_the_page_text():
    index = refs.number_index({"CH02-S02": "矩阵 (2.2) 与方程组 (2.1)。",
                               "CH02-S03": "(2.2) 的阶梯形。"})
    assert index["2.1"] == ["CH02-S02"]
    assert index["2.2"] == ["CH02-S02", "CH02-S03"]


# ── 一处定义：正则必须和渲染层那份一致 ─────────────────────────────────────
def test_number_regex_matches_the_one_in_the_renderer():
    """Python 判"有没有地方可跳"，JS 负责"做成链接"。两边的正则一旦不一致，
    就会一边说有、另一边说没有（一个说"跳得到"、另一个不给链接）。

    所以这里**不是比字符串**，而是把 JS 那行里的正则抠出来，在 Python 里换掉
    `(?:` 这种 JS 专属写法后，拿同一批样本比对匹配结果。
    """
    import re

    js_line = [x for x in (ROOT / "blocks/render/assets/render-core.js")
               .read_text(encoding="utf-8").splitlines() if "var NUM_REF" in x]
    assert js_line, "render-core.js 里找不到 NUM_REF"
    literal = js_line[0].split("/", 1)[1].rsplit("/", 1)[0]
    js_re = re.compile(literal.replace("(?:", "("))

    samples = ["线性方程组 (2.1) 的矩阵", "见（5.16）式", "定理 2.3.1 不是编号引用",
               "（2.2.2）小节号", "没有编号的一句话", "两处 (2.1) 与 (2.2)"]
    for text in samples:
        # JS 侧多一层括号（`(?:` 改写成 `(` 留下的），所以只比"第一个捕获组"
        mine = [m if isinstance(m, str) else m[0] for m in refs.NUMBER_REF.findall(text)]
        theirs = [m if isinstance(m, str) else m[0] for m in js_re.findall(text)]
        assert mine == theirs, text


# ── 词表对不上就不挂（宁可不配图，也不能配错图）────────────────────────────
def test_checked_in_figures_registry_declares_names_and_resolves_cleanly():
    """拿仓库里真实的图登记表跑一遍。两条都要成立：

    1. **每张图都按名字声明**（`concept_names`）—— 只登记 id 的图在概念表重抽之后
       必然错配（P-020/P-032 的病根），所以"有没有名字"本身就是要守的约；
    2. `concepts` 里的 id **必须在当前概念表里查得到** —— 它是按名字解析出来的缓存，
       出现查不到的 id 就说明有人手改过、或者忘了跑 `figures.cli rehang`。
    """
    registry = ROOT / "figures/index.json"
    concepts_path = ROOT / "data/parse/linear-algebra-full/concepts.json"
    if not (registry.is_file() and concepts_path.is_file()):
        return
    figures = json.loads(registry.read_text(encoding="utf-8"))["figures"]
    names = {str(c.get("name") or ""): c["id"] for c in
             json.loads(concepts_path.read_text(encoding="utf-8"))["concepts"]}
    no_names = [row["id"] for row in figures if not row.get("concept_names")]
    assert not no_names, f"这些图只登记了 id、没法按名字重挂：{no_names}"
    stale = [f"{row['id']}: {cid}" for row in figures
             for cid in row.get("concepts") or [] if cid not in set(names.values())]
    assert not stale, f"图登记表里有查不到的概念 id（该跑 rehang 了）：{stale}"
    # 当前概念表里没有第一章的概念 → 第一章那批图必须如实报"对不上"，而不是硬挂一个号
    unresolved = {row["id"]: row.get("unresolved") or [] for row in figures
                  if row.get("unresolved")}
    for fid, missing in unresolved.items():
        assert not any(n in names for n in missing), f"{fid} 明明能解析，却记成 unresolved"


def test_rehang_keeps_only_what_the_current_table_knows():
    """`registry.rehang()`：名字查得到就挂、查不到就不挂并记进 `unresolved`。"""
    sys.path.insert(0, str(ROOT / "blocks/figures"))
    import registry  # noqa: E402

    entries = [{"id": "FIG-a", "concept_names": ["向量", "已删掉的概念"], "concepts": ["C-9999"]},
               {"id": "FIG-b", "concepts": ["C-0001"]}]     # 只登记过 id 的老图
    result = registry.rehang(entries, [{"id": "C-0007", "name": "向量"}])
    first, second = result["figures"]
    assert first["concepts"] == ["C-0007"], "按名字挂到了新号上"
    assert first["unresolved"] == ["已删掉的概念"], "查不到的不挂、如实记下来"
    assert second["concepts"] == ["C-0001"], "没有名字的老图原样保留"
    assert result["legacy"] and result["legacy"][0]["id"] == "FIG-b"
