"""**接线护栏**：调用方与被调方的**签名**必须对得上。

为什么单开一个文件（2026-09-26 生产版 A6 实测抓到的真 bug）：
`run_book.py` 里写着 `doctor.fix_chapter_records(log=log)`，而 `doctor.py` 那边的签名是
`fix_chapter_records(verbose: bool = True)` —— **没有 `log`**。
后果：`run_book` **一启动就 `TypeError` 崩掉**（整本一次都没跑起来），
而**两边的单元测试各自都过**：一个只测 `run_book` 的调度逻辑（用假模型、替身 doctor），
一个只测 `doctor` 的函数本身。**没有任何测试覆盖"两边合起来调一次"**。

这正是本项目最贵的一类 bug（"函数写对了但没接线"/"改了 A 忘了 B"）。
所以这里把跨文件的**调用形状**写下来，签名一变就红。

⚠️ 用法：**改了调用方或别调方的签名，就同步改这里**（它是"契约"，不是"实现细节"）。
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    # ⚠️ **必须注册进 `sys.modules`**：`@dataclass` 在装饰时要靠
    #    `sys.modules[cls.__module__]` 找命名空间，不注册就抛
    #    `AttributeError: 'NoneType' object has no attribute '__dict__'`（实测踩过两次）。
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_run_book_to_doctor_call_sites_accept_the_arguments():
    """`run_book` 调 `doctor` 的两个对账函数（`log=`）必须被接受。

    （实测：这里崩过一次，整本连第一章都没跑起来。）
    """
    doc = _load("doc_for_wiring", "tools/doctor.py")
    noop = lambda *a, **k: None            # noqa: E731
    inspect.signature(doc.fix_chapter_records).bind(log=noop)
    inspect.signature(doc.fix_scope_records).bind(log=noop)
    assert isinstance(doc.CHECKS, tuple) and doc.CHECKS, "doctor 的检查清单没了"


def test_run_book_to_reconcile_call_site_accepts_the_arguments():
    """`run_book` 调 `reconcile.reconcile(parse, lessons, audit, delivery_dirs=…, log=…)`。"""
    rec = _load("rec_for_wiring", "tools/reconcile.py")
    inspect.signature(rec.reconcile).bind(
        None, None, None, delivery_dirs=[], check=False, log=lambda *a, **k: None)


def test_run_chapter_to_scopes_call_sites_accept_the_arguments():
    """`run_chapter` 调 `lib/scopes.py` 的四个函数（范围 / 页面新鲜 / 模型新鲜）。"""
    scopes = _load("scopes_for_wiring", "lib/scopes.py")
    inspect.signature(scopes.scope_of).bind(None, [])
    inspect.signature(scopes.sync_plan).bind(None, [])
    inspect.signature(scopes.models_are_fresh).bind(None, None, ["CH01"])




# ── 全局护栏②：不许假设"路径一定在仓库里" ─────────────────────────────
def test_no_module_assumes_paths_live_inside_the_repo():
    """**全局**护栏：`tools/` 与 `lib/` 里不许再出现裸的 `.relative_to(ROOT)`。

    为什么（2026-09-26 生产版 A6 实测，第二次 0 秒崩）：
        `run_book.py` 写 `worker["log"].relative_to(ROOT)`，而
        `--lessons /tmp/a6/serial/lesson/a6`（隔离树做串行/并行比对，**正是 A6 的用法**）
        的路径不在仓库里 → `ValueError: … is not in the subpath of …`
        → **整个整本跑在 reporting 阶段当场死掉**。

    这类坑**不止一处**：凡拿用户传进来的路径（`--lessons`/`--parse`/`--out-root`）
    去 `relative_to(ROOT)` 的地方都会在仓库外炸。所以用法收成一个实现
    `lib/files.py::display_path`，并用这条测试**全局**堵住复发。

    这是本项目要"之后能跑自动化"的必要条件之一：**跑在哪儿不该决定跑不跑得起来。**
    """
    offenders: list[str] = []
    for sub in ("tools", "lib"):
        for path in sorted((ROOT / sub).rglob("*.py")):
            if path.name == "files.py":
                continue            # display_path 自己的实现里当然要用 relative_to
            # 用 AST 而不是文本匹配：注释和字符串里的同款写法不该算（也不该漏）
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Attribute) and node.attr == "relative_to"):
                    continue
                base = node.value
                if isinstance(base, ast.Name) and base.id in ("ROOT", "HERE"):
                    offenders.append(f"    {path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, (
        "这些地方把路径当成了『一定在仓库里』—— 仓库外（隔离树）会直接崩。\n"
        "改用 lib/files.py::display_path：\n" + "\n".join(offenders))






def test_sum_tokens_rejects_positional_run_id():
    """`usage.sum_tokens(log, run_id)` 这种**位置**用法必须**当场报错**，不许静默算成 0。

    为什么（2026-09-26 生产版 A6 实测抓出，第 7 个接线 bug）：
        `sum_tokens(log=…, run_id=…)` 的第一个位置参数是 `log`，而 `run_book.py` 原来写
        `usage.sum_tokens(worker["run_id"])` → 位置参数绑到 `log` → 去读一个名叫
        `book-2026…/CH03` 的"文件" → 读不到返回 `[]` → **合计恒为 0**。

    后果是**静默失效**，不是报错：
        `run_book.spent()` 恒等于 0 → **`--budget-tokens` 永远不会触发**，
        只有墙钟那道闸还在工作。自动跑最怕的就是"闸门静默失效"。

    所以签名改成仅限关键字（`*, run_id`），并在这里把两种错法都钉住。
    """
    U = _load("usage_for_wiring", "lib/usage.py")
    sig = inspect.signature(U.sum_tokens)
    assert sig.parameters["run_id"].kind is inspect.Parameter.KEYWORD_ONLY, \
        "run_id 必须仅限关键字 —— 否则位置用法又会静默绑到 log 上、算成 0"
    with pytest.raises(TypeError):
        U.sum_tokens("x", "run-A")           # ← 就是原来那个错法（第二个位置参数）
    # 关键字用法正常；不存在的账目文件 → 0，但不抛（"账目读不出来不该让流水线崩"）
    assert U.sum_tokens("/nope/没有这个文件.jsonl", run_id="run-A") == 0
    # 没有 run_id = 全量合计，老语义不变
    assert U.sum_tokens("/nope/没有这个文件.jsonl") == 0






def test_curriculum_context_is_reproducible_across_hash_seeds(tmp_path):
    """同一份输入，不同 `PYTHONHASHSEED` → `curriculum_context()` 必须**逐字节相同**。

    为什么（2026-09-26 生产版 A6 实测抓出，第 7 个 bug）：
    它返回的三个列表原来从 **set** 和 `dict.values()` 直接取 —— set 的迭代顺序受
    字符串 hash 随机化影响，**每个进程都可能不同**。这些列表会进模型步骤的 prompt，
    于是"同一节、同样的输入"在两个进程里算出的 prompt 不一样：

      · 模型这一步**永远命不中缓存**（实测：含 context 的 `model:` 调用 4 次全部未命中，
        不含 context 的 `lesson:` 调用全部命中）
      · 产出**不可复现**

    实证（修复前，同一份 concepts.json）：seed=1/2/3 → 三个不同的 md5。
    这条测试就是把它钉死：**一份派生视图的内容，不该取决于你怎么调用它。**
    """
    parse = tmp_path / "parse"
    parse.mkdir()
    (parse / "chapters.json").write_text(
        json.dumps({"lesson_units": [{"unit_id": "U1"}, {"unit_id": "U2"}]}), encoding="utf-8")
    (parse / "concepts.json").write_text(
        json.dumps({"concepts": [
            {"id": "C-9", "name": "九", "occurrences": [{"unit_id": "U1"}]},
            {"id": "C-1", "name": "一", "occurrences": [{"unit_id": "U1"}]},
            {"id": "C-5", "name": "五", "occurrences": [{"unit_id": "U2"}]},
            {"id": "C-3", "name": "三", "occurrences": [{"unit_id": "U2"}]},
        ]}, ensure_ascii=False), encoding="utf-8")
    code = (
        "import sys, json, pathlib;"
        f"sys.path.insert(0, {str(ROOT / 'lib')!r});"
        f"sys.path.insert(0, {str(ROOT / 'blocks' / 'model')!r});"
        "from model_generate import curriculum_context;"
        f"print(json.dumps(curriculum_context(pathlib.Path({str(parse)!r}), 'U1'),"
        " ensure_ascii=False))"
    )
    outs = set()
    for seed in ("1", "2", "3", "4"):
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              env={**os.environ, "PYTHONHASHSEED": seed})
        assert proc.returncode == 0, proc.stderr[-800:]
        outs.add(proc.stdout.strip())
    assert len(outs) == 1, (
        "同一份输入在不同 hash 种子下算出了不同的 context（顺序不稳）→ "
        f"prompt 不可复现、模型这一步永远命不中缓存：{outs}")


def test_display_path_works_outside_the_repo(tmp_path):
    """`display_path` 的行为：仓库内给相对路径（好看），仓库外给绝对路径（不炸）。"""
    files = _load("files_for_wiring", "lib/files.py")
    inside = files.ROOT / "tools" / "run_book.py"
    assert files.display_path(inside) == "tools/run_book.py"
    outside = tmp_path / "a6" / "CH03.log"
    assert files.display_path(outside) == str(outside), "仓库外必须原样给绝对路径，不能抛异常"
    # 这就是原来会炸的那一行
    assert files.display_path(files.ROOT / "x" / "y.log", base=files.ROOT) == "x/y.log"




# ── 模型返回的 JSON 非法转义：必须**逐级修**，且合法转义不许误伤 ──────────────
def test_lenient_json_repairs_mixed_latex_escapes():
    """LaTeX 反斜杠**混合**（写对的和漏写的都有）时必须能修出来。

    为什么（2026-09-27 全流程试跑实测，阻断点）：
    CH07-S02 的模型返回里既有合法的 `\\\\varepsilon`、也有漏写的 `\\varepsilon`，
    而原来**只修一次**，扫不干净就抛 ModelError →
    **212 个单元的概念抽取在 34/212 处整体中止**（前面 34 节的钱全白等）。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("model_under_test", ROOT / "lib" / "model.py")
    sys.path.insert(0, str(ROOT / "lib"))
    M = importlib.util.module_from_spec(spec)
    sys.modules["model_under_test"] = M      # dataclass 要求模块先注册进 sys.modules
    spec.loader.exec_module(M)

    # 混合：`\\varepsilon` 合法、`\v` 非法
    raw = '{"a": "用 $\\\\varepsilon$ 与 \\v 一起", "b": 1}'
    data, repaired = M._loads_lenient(raw, tag="t")
    assert repaired is True
    assert data["a"] == "用 $\\varepsilon$ 与 \\v 一起"
    assert data["b"] == 1

    # 合法 JSON 一律不动
    ok = '{"x": "\\u4e2d\\n\\t\\\\"}'
    data2, repaired2 = M._loads_lenient(ok)
    assert repaired2 is False and data2["x"] == "中\n\t\\"

    # LaTeX 的 \u 开头（\underline）不能把合法的 \uXXXX 一起误伤
    raw2 = '{"c": "\\underline{AB} 与 \\u4e2d"}'
    data3, _ = M._loads_lenient(raw2, tag="t2")
    assert data3["c"] == "\\underline{AB} 与 中", data3






# ── 全流程试跑（2026-09-27）抓出的 B-7～B-11：护栏 ──────────────────────────






# ── B-13：结构层把「目录」和「习题答案」当成了章 ────────────────────────────
def test_structure_layer_rejects_toc_entries_with_inline_page_numbers():
    """目录条目（页码在**行尾括号**里、省略号引导）必须被判为目录，不能变成章。

    为什么（2026-09-27 全流程试跑实测抓出 B-13）：
    这本书的目录是 `第2章 留数及其应用 ……（107）` —— 页码在行内括号里。
    原来的 `TOC_ENTRY` 只认"点/空格引导直接接数字"，`_looks_like_toc_entry()` 只认
    "下一行是裸页码"，两种都不匹配 → **整段目录没被丢掉** → 17 个目录条目变成"章"
    （实测 CH02–CH05 的标题是 `留数及其应用 …… (107)`，与 CH11–CH14 的真章重复），
    白跑 17 个单元的生成 + 审核。
    """
    st = _load("structure_for_wiring", "blocks/parse/structure/structure.py")
    for line in ("第2章 留数及其应用 ……（107）",
                 "第三章 解析开拓 …… (134)",
                 "1.1 复数 ············································ (3)"):
        assert st.TOC_ENTRY_INLINE.search(line), f"没认出目录条目：{line}"
    # 正常章标题不能被当成目录
    assert not st.TOC_ENTRY_INLINE.search("第2章 留数及其应用")
    assert not st.TOC_ENTRY_INLINE.search("2.1 复变函数的极限")


def test_structure_layer_rejects_answer_appendix_lines():
    """习题答案行不能被当成标题（实测这本书多出 CH15/CH16/CH17 三个"章"）。"""
    st = _load("structure_for_wiring", "blocks/parse/structure/structure.py")
    for line in (r"1. (1) $8 + \mathrm{i}3\pi$ (2) $8 - \mathrm{i}3\pi$",
                 r"2. (1) 不存在；(2) 不存在；(3) 存在， $f(z)=\frac{1}{z}$"):
        assert st.heading_match(line) is None, f"答案行被当成了标题：{line}"
    # 正常的编号标题**必须照旧能认出来**（别把护栏做成"一刀切"）
    assert st.heading_match("2.1 复变函数的极限") is not None
    assert st.heading_match("第2章 留数及其应用") is not None
    assert st.heading_match("1.2.3 柯西积分公式") is not None


# ── B-14：打包侧"读错书的问题汇总" + 交付物里不该出现成本 ────────────────────
def test_packaging_uses_the_book_scoped_problems_file():
    """`--problems` 的默认值必须跟着 `--book` 走（B-14a）。

    实测：`--book complex-analysis` 时它去**读并重写线性代数**的 problems.json，
    拿去校验的声明也是别本书的（数字当然对不上）。
    """
    src = (ROOT / "tools" / "make_delivery_package.py").read_text(encoding="utf-8")
    assert 'ap.add_argument("--problems", default=None' in src, \
        "--problems 又写死成默认那本书了"
    assert "problems_path = Path(args.problems) if args.problems else ctx.problems" in src, \
        "problems 路径没有跟着 --book 解析"


def test_drop_bad_sections_does_not_use_a_free_variable():
    """`drop_bad_sections` 不许引用外部自由变量（B-14b：`ctx` 未定义 → NameError）。"""
    pkg = _load("pkg_for_wiring", "tools/make_delivery_package.py")
    import inspect as _inspect
    params = _inspect.signature(pkg.drop_bad_sections).parameters
    assert "parse" in params and "lessons" in params, \
        "parse/lessons 必须显式传进来，不能靠外面的 ctx"
    src = _inspect.getsource(pkg.drop_bad_sections)
    assert "ctx." not in src, "还在引用外面的 ctx"


def test_problems_claims_can_hide_cost():
    """交付物里**不出现耗时/花费**；但 `metrics` 里的 token 账照旧保留（内部对账用）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "collect_for_wiring", ROOT / "blocks" / "problems" / "collect.py")
    col = importlib.util.module_from_spec(spec)
    sys.modules["collect_for_wiring"] = col
    spec.loader.exec_module(col)
    payload = {"metrics": {"token": {"逐章": {"CH01": 123456}},
                           "篇幅": {}, "详解": {}, "模型": 3}}
    with_cost = [c["说"] for c in col.claims(payload, include_cost=True)]
    without = [c["说"] for c in col.claims(payload, include_cost=False)]
    assert any("token" in c for c in with_cost), "带成本时应有一条成本声明"
    assert not any("token" in c or "成本" in c for c in without), \
        f"隐藏成本后仍有成本声明：{without}"
    # 账目数据本身还在（内部对账要它）
    assert payload["metrics"]["token"]["逐章"]["CH01"] == 123456


def test_packaging_pins_hide_cost_so_it_cannot_grow_back():
    """打包侧必须**自己**带上 `--hide-cost` —— 否则重汇总会把它又写回来（实测踩过）。"""
    src = (ROOT / "tools" / "make_delivery_package.py").read_text(encoding="utf-8")
    # 取"重汇总那一处"的代码窗口（用 `"-m", "blocks.problems.cli"` 定位，避免命中文档字符串）
    window = src[src.index('"-m", "blocks.problems.cli"'):][:400]
    assert '"--hide-cost"' in window, "打包时的重汇总没有带 --hide-cost"


# ── 解析层「目录当骨架 + 区域排除」（2026-09-27，用户要求）─────────────────────
def test_listing_line_accepts_parenthesized_page_numbers():
    """目录条目里**页码在括号里**（`5.2 基级数 …… (82)`）也必须被认出来。

    为什么（2026-09-27 实测抓出，最贵的一条）：目录**跨页**，而整页丢弃只覆盖
    "有 `目次` 标记的那一页"；续页靠形状判，而这本书的条目页码**在括号里**，
    原正则 `[\\s.．·]{1,}\\d{1,4}\\s*$` 只认裸数字 → 续页整段漏掉 →
    条目变成章：**一本 9 章的书解析出 17 章**，多跑的 43 个单元白白烧掉 4000 万 token 的一部分。
    """
    ad = _load("adapt_for_wiring", "blocks/parse/mineru/adapt.py")
    for line in ("5.2 基级数 …… (82)", "第6章 留数及其应用 ……（107）",
                 "第一章 向量 7", "1.1 向量的线性运算 7"):
        assert ad.LISTING_LINE.match(line), f"没认出目录条目：{line}"
    # 正常正文不该被判成目录条目
    assert not ad.LISTING_LINE.match("这一节讨论复数的四则运算，先给出定义。")
    assert not ad.LISTING_LINE.match("定理 1.1 设 $f$ 在区域 $D$ 内解析")


def test_region_state_machine_marks_exercises_and_answers():
    """区域状态机：`习题` → exercise、`习题参考答案` → answer、`第N章 <标题>` 回到正文。

    关键细节（实测这本书）：答案区里的 `第N章` **没有标题**（光秃秃一行），
    所以它**不能**把区域带回正文 —— 否则答案页会重新变成章（本书 CH15–CH17 就是这么来的）。
    """
    ad = _load("adapt_for_wiring", "blocks/parse/mineru/adapt.py")
    assert ad.region_mark("习题") == "exercise"
    assert ad.region_mark("习题一") == "exercise"
    assert ad.region_mark("习题参考答案") == "answer"
    assert ad.region_mark("目次") == "toc"
    assert ad.region_mark("前言") == "front"
    assert ad.region_mark("第3章 留数及其应用") is None       # 章标题不是区域标记
    # 「第N章 <标题>」才算正文开始；光秃秃的「第2章」（答案页）不算
    assert ad.CHAPTER_TITLE.match("第3章 留数及其应用")
    assert not ad.CHAPTER_TITLE.match("第3章")


def test_structure_layer_excludes_non_body_regions():
    """结构层必须**按区域**把非正文的行与标题剔掉（前言/目录/习题/答案都不是教案单元）。"""
    st = _load("structure_for_wiring", "blocks/parse/structure/structure.py")
    assert hasattr(st, "RegionMap") and hasattr(st, "load_regions")
    assert hasattr(st, "compare_with_toc") and hasattr(st, "load_toc")
    rm = st.RegionMap(by_line={10: "answer", 20: "exercise"}, by_page={5: "front", 6: "body"})
    assert rm.of_line(10) == "answer"             # 标记那一行就生效
    assert rm.of_line(15) == "answer"             # 就近前一个标记决定区域
    assert rm.of_line(20) == "exercise"
    assert rm.of_line(999) == "exercise"
    assert rm.is_body(999) is False
    assert rm.of_line(5, 5) == "front"            # 第一个标记之前 → 退回按页
    rm2 = st.RegionMap(by_line={}, by_page={5: "front", 6: "body"})
    assert rm2.of_line(1, 6) == "body"
    assert rm2.of_line(1, 5) == "front"
    assert rm2.of_line(1, 99) == "body"           # 没有任何区域信息 → 当正文（不误杀）


def test_toc_cross_check_reports_mismatch():
    """目录对账：解析出来的章与**书自己声明的目录**对不上时必须报出来（0 token）。"""
    st = _load("structure_for_wiring", "blocks/parse/structure/structure.py")
    class H:
        def __init__(self, title):
            self.title = title
            self.canonical_level = 1
    got = st.compare_with_toc([H("复数和平面点集"), H("复变数函数"), H("空气")],
                              ["第2章 复变数函数"])
    assert got["目录声明章数"] == 1 and got["解析出章数"] == 3
    assert got["对上的"] == 1
    assert got["解析出但目录没有"] == ["复数和平面点集", "空气"]


# ── 习题层（2026-09-27，用户要求"习题抽出来单独放、先不用"）────────────────────
def test_exercise_verb_does_not_swallow_real_section_titles():
    """题干语气词**不许把真章标题吃掉** —— `解析…` 曾经被我误判成题干。

    为什么（2026-09-27 实测踩到）：我把裸 `解` 放进语气词表，
    于是 `解析函数的积分表示`、`解析函数的级数展开`、`解析开拓` **三个真章**
    被判成"习题题干"剔掉 —— 复变函数因此从 **9 章掉到 6 章**。
    """
    st = _load("structure_for_wiring", "blocks/parse/structure/structure.py")
    for real in ("解析函数的积分表示", "解析函数的级数展开", "解析开拓", "解析函数的积分",
                 "设计一个算法", "设施布局"):
        assert not st.EXERCISE_VERB.match(real), f"真标题被当成题干：{real}"
    for item in ("求下列积分", "证明：四点共圆", "设原点在 $l$ 的外部", "解下列方程",
                 "试问在复数域中", "计算下列积分"):
        assert st.EXERCISE_VERB.match(item), f"题干没认出来：{item}"


def test_exercise_region_is_line_based_not_page_based():
    """区域在**标记那一行**变，不是页首变（实测：`习题参考答案` 与 `第1章` 同在 p227）。"""
    st = _load("structure_for_wiring", "blocks/parse/structure/structure.py")
    rm = st.RegionMap(by_line={100: "exercise", 200: "answer"}, by_page={1: "body"})
    assert rm.of_line(150) == "exercise"        # 100 之后、200 之前
    assert rm.of_line(199) == "exercise"
    assert rm.of_line(200) == "answer"          # 标记那一行就生效
    assert rm.of_line(999) == "answer"
    assert rm.of_line(50) == "body"             # 第一个标记之前 → 退回按页






def test_item_run_rule_never_swallows_a_chapter_title():
    """题号序列判据**不许把章标题一起吞掉** —— 章号 `(5,)` 也会跟题目序列连上。

    为什么（2026-09-27 实测踩到）：`第5章 解析函数的级数展开` 的编号路径也是 `(5,)`，
    上一道题的编号是 `(4,)` → 判据以为"连续" → **真章标题被当成题干剔掉**。
    所以序列判据只认 `scheme == "num"` 的**裸题号**，`scheme == "chapter"` 一律不算。
    靠这条护栏，`mark_exercise_items` 在本项目两本书上都跑出 **线性代数 0 误剔**。
    """
    st = _load("structure_for_wiring", "blocks/parse/structure/structure.py")
    lines = [st.Line(page=1, text=f"第 {i} 行", source_line=i) for i in range(1, 30)]
    def item(num, title, line):
        return st.Heading(scheme="num", raw_number=str(num), title=title,
                          source_line=line, page=1, numeric=(num,))
    def chapter(num, title, line):
        return st.Heading(scheme="chapter", raw_number=str(num), title=title,
                          source_line=line, page=1, numeric=(num,))
    heads = [item(1, "求下列积分", 1), item(2, "证明", 5), item(3, "设 $x$ 满足", 9),
             chapter(5, "解析函数的级数展开", 20), item(6, "证明", 25)]
    marked = st.mark_exercise_items(heads, lines)
    assert heads[0].is_exercise_item and heads[1].is_exercise_item and heads[2].is_exercise_item, \
        "连续题号 1,2,3 应整串判为题干"
    assert not heads[3].is_exercise_item, "**章标题被当成题干了**"
    # 章后**孤立的**裸条目（没有题干特征、也不成序列）→ 两条判据都不该命中
    assert not heads[4].is_exercise_item, "章后的孤立条目不该只因为跟章号相邻就被吃掉"
    assert marked == 3, marked


# ── 内嵌书签（2026-09-27：两本新书都自带权威目录）────────────────────────────
def test_repair_pdf_text_fixes_gbk_mojibake_only():
    """PDF 文字层/书签的 GBK 乱码要能修；**已经是正常中文的不许动**。

    实测（《数分讲义第二册》）：书签 `¿Õ¼ä½âÎö¼¸ºÎ` → `空间解析几何`。
    但正文那种 CID 缺 ToUnicode 的乱码修不了（修出来是 `榤)跘`），这里不假装能修。
    """
    ad = _load("adapt_for_wiring", "blocks/parse/mineru/adapt.py")
    assert ad.repair_pdf_text("¿Õ¼ä½âÎö¼¸ºÎ") == "空间解析几何"
    assert ad.repair_pdf_text("Ï°Ìâ 8.1") == "习题 8.1"
    # 正常中文、纯 ASCII、纯数字 → 原样返回（不能越修越坏）
    for keep in ("空间解析几何", "Chapter 1", "1.2 向量", "", "a · b = 0"):
        assert ad.repair_pdf_text(keep) == keep, keep


def test_read_pdf_outline_reads_embedded_bookmarks(tmp_path):
    """能读 PDF 的内嵌书签；坏了/没有也不许抛异常（返回空表即可）。"""
    ad = _load("adapt_for_wiring", "blocks/parse/mineru/adapt.py")
    assert ad.read_pdf_outline(None) == []
    assert ad.read_pdf_outline(tmp_path / "不存在.pdf") == []
    pdf = ROOT / "data" / "raw" / "数分讲义第二册.pdf"
    if not pdf.is_file():
        pytest.skip("本机没有这本 PDF")
    rows = ad.read_pdf_outline(pdf)
    assert len(rows) > 100, f"书签没读出来：{len(rows)}"
    assert rows[0]["level"] == 1 and rows[0]["text"], rows[0]
    # 乱码必须已经修好（否则目录会是一堆 `¿Õ¼ä`）
    assert any("第" in r["text"] or "空间" in r["text"] or "向量" in r["text"] for r in rows), \
        [r["text"] for r in rows[:5]]


@pytest.mark.parametrize('name',['run_book','run_chapter','audit_loop'])
def test_historical_orchestration_has_no_execution_path(name,tmp_path):
    from support.retired_cli import assert_retired_cli
    assert_retired_cli(tmp_path,name)
