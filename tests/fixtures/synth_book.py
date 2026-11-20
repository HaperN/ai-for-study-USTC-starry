"""**离线合成教材夹具** —— 在 `tmp_path` 里造一本最小但契约合法的产物树。

## 为什么需要它

2026-10-03 用户把旧产物全部归档挪开（`data/_archive/...`）之后，
`tests/test_delivery_scope.py` / `tests/test_book_views.py` / `tests/test_doctor.py`
三个模块的 `pytestmark = pytest.mark.skipif(...)` 整模块跳过了 —— 一共 45 条测试，
离线环境下这些能力**零覆盖**。这个夹具把那 45 条断言要读的产物**全部**造出来，
于是测试不再依赖 `data/` 下任何真实文件。

## 造出来的东西（字段以 `tools/doctor.py` 与 `tools/make_delivery_package.py`
## 的真实读取路径为准，不是猜的）

    解析产物（book.parse）
        chapters.json            doctor 的 build_snapshot/fresh_checks 读
                                 `book.title` / `chapters[].id,title` / `lesson_units[]`
        toc.v2.json              目录树（后续里程碑用）
        body.json                正文节点清点（后续里程碑用）
        concepts.json            doctor 的 inv_concept_registration_is_complete 读
                                 `concepts[].id/name/type/occurrences[].unit_id`
        concepts.parts/<unit>.json   分片概念（inv_scope_records_never_shrink 认它算"有产物"）
        concepts.gaps.parts/     分片缺口（可选）
        exercises.json           习题（后续里程碑用）
        images.json / images/    图（后续里程碑用）
        ingest.json              摄入记录（后续里程碑用）
        original_text/<unit>.md  原文 —— fresh_checks 的篇幅地板/编号条目覆盖都读它

    教案产物（book.lessons）
        lessons/<unit>.json      正文 + 公式详解 + 例题；`checks` **由产品自己的
                                 `generate.fresh_checks` 现算后存盘**（所以初始状态是新鲜的，
                                 和现有 test_book_views 里"快照用产品自己的重建函数写"同一个套路）
        lessons.index.json       行集合 == 磁盘 lessons/*.json（A1 索引不丢行）
        snapshot.json            由产品自己的 `snapshot.build_snapshot` 写出（A3 整本快照）
        chapter-runs/<CH>.json   章记录（doctor 的 inv_chapter_records_are_current 读
                                 `chapter` / `节[].unit_id` / `节[].steps.回路.结论`）

    审核产物（book.audit）
        <unit>.loop.json         回路结论（`结论` 字段）
        <unit>.stuck.jsonl       「卡在哪」记录，最后一行结论必须与回路一致
                                 （doctor 的 inv_stuck_record_matches_loop 读）

    交付页（book.page）
        render.index.json        scope / counts.units / built_from / built_from_concepts
        scope.json（范围.json）  原本要做 —— 范围记录只许长大
        index.html / data/data.js / assets/app.js   交付页三件套
        models/<unit>.json       交互模型（lib/scopes.models_are_fresh 读
                                 `_meta.source_lesson_revision`）

## 合成书的形状

默认 **2 章 × 2 个教学单元**（`units=` 可以覆盖），每个单元都含
**一个公式**（`explainables[]` 里 kind=formula 的那条）与**一个例题**（`examples[]`）。
`units=` 覆盖时教学内容按 unit 名里的章号/节号现算，所以任意子集都合法。

## "故意弄坏一处"

`broken=<名字>` 在**正确产物建好之后**精确地弄坏一处，供三个模块那套
"先造正确产物 → 弄坏 → 断言 doctor 真报出来"的测试用。可用名字见 `BREAKAGES`。
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

#: 仓库根（tests/fixtures/synth_book.py → 上三级）
ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ROOT / "lib"))

#: 1×1 的合法 PNG（`images/` 里放一张真的图，别放字节垃圾）
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

#: 每个教学单元的"教学料"：符号、公式名、例题数据。
#: 顺序无关 —— 按 unit_id 里解析出来的章号/节号取模选用，保证任意 `units=` 子集都自洽。
_UNIT_RECIPES = (
    {
        "title": "向量与线性运算",
        "object": "向量",
        "symbol": r"\boldsymbol{a}",
        "formula": "向量加法",
        "formula_what": "把两个同样长度的向量按位置相加，得到一个新的同样长度的向量",
        "formula_why": "不先规定怎么加，两个向量就只是两列数，拼不成线性组合",
        "a": "(1,2)", "b": "(3,4)", "sum": "(4,6)",
        "example_text": r"设 $\boldsymbol{a}=(1,2)$，$\boldsymbol{b}=(3,4)$，求 $\boldsymbol{a}+\boldsymbol{b}$。",
    },
    {
        "title": "数乘与线性组合",
        "object": "向量",
        "symbol": r"\boldsymbol{a}",
        "formula": "向量数乘",
        "formula_what": "用一个数去乘向量的每个分量，得到方向相同或相反的新向量",
        "formula_why": "没有数乘就只有两个向量相加，凑不出三个以上向量的任意搭配",
        "a": "(1,2)", "b": "(3,4)", "sum": "(7,10)",
        "example_text": r"设 $\boldsymbol{a}=(1,2)$，$\boldsymbol{b}=(3,4)$，求 $2\boldsymbol{a}+\boldsymbol{b}$。",
    },
    {
        "title": "矩阵与逐项运算",
        "object": "矩阵",
        "symbol": r"\boldsymbol{A}",
        "formula": "矩阵加法",
        "formula_what": "把两个同样大小的矩阵按位置相加，得到一个新的同样大小的矩阵",
        "formula_why": "不先规定怎么加，两个表就只是两张表，凑不成线性映射的运算",
        "a": "(1,2)", "b": "(3,4)", "sum": "(4,6)",
        "example_text": r"设 $\boldsymbol{A}=\begin{pmatrix}1&2\\3&4\end{pmatrix}$，"
                        r"$\boldsymbol{B}=\begin{pmatrix}1&1\\1&1\end{pmatrix}$，求 $\boldsymbol{A}+\boldsymbol{B}$。",
    },
    {
        "title": "矩阵乘法",
        "object": "矩阵",
        "symbol": r"\boldsymbol{A}",
        "formula": "矩阵乘法",
        "formula_what": "左矩阵的每一行与右矩阵的每一列逐项相乘再相加，得到乘积的一个元素",
        "formula_why": "不先规定怎么乘，矩阵就只能表示一张表，表示不了映射的复合",
        "a": "(1,2)", "b": "(3,4)", "sum": "(7,10)",
        "example_text": r"设 $\boldsymbol{A}=\begin{pmatrix}1&2\\3&4\end{pmatrix}$，"
                        r"$\boldsymbol{B}=\begin{pmatrix}1&0\\0&1\end{pmatrix}$，求 $\boldsymbol{A}\boldsymbol{B}$。",
    },
)

#: 合成书的默认单元（2 章 × 2 节）
DEFAULT_UNITS = ("CH01-S01", "CH01-S02", "CH02-S01", "CH02-S02")


@dataclass(frozen=True)
class SynthBook:
    """一本合成教材的产物位置（字段名对齐 `lib/book.py` 的 `BookContext`）。"""

    root: Path
    parse: Path
    lessons: Path
    audit: Path
    pages_root: Path
    page: Path
    book_id: str
    title: str
    unit_ids: tuple[str, ...]
    chapter_ids: tuple[str, ...]
    broken: str | None = None

    #: 兼容老的 `_book()` 返回值（`book["parse"]` / `book["lesson"]`）
    def __getitem__(self, key: str):
        return {"parse": self.parse, "lesson": self.lessons, "audit": self.audit,
                "page": self.page}[key]

    def lesson(self, unit_id: str) -> dict:
        return json.loads((self.lessons / "lessons" / f"{unit_id}.json").read_text("utf-8"))

    def page_index(self) -> dict:
        return json.loads((self.page / "render.index.json").read_text("utf-8"))

    def scope_plan(self) -> dict:
        return json.loads((self.page / "范围.json").read_text("utf-8"))

    def snapshot(self) -> dict:
        return json.loads((self.lessons / "snapshot.json").read_text("utf-8"))

    def index(self) -> dict:
        return json.loads((self.lessons / "lessons.index.json").read_text("utf-8"))


# ────────────────────────────────────────── 小工具

def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chapter_of(unit_id: str) -> str:
    return unit_id.split("-")[0]


def _anchor_of(unit_id: str, index: int) -> str:
    """`CH01-S02` → `1.2`（章号取数字、节号取 unit 名里的 S 编号）。"""
    chapter_no = "".join(ch for ch in _chapter_of(unit_id) if ch.isdigit()).lstrip("0") or "1"
    digits = "".join(ch for ch in unit_id.split("-")[-1] if ch.isdigit()).lstrip("0") or str(index)
    return f"{chapter_no}.{digits}"


def _recipe(unit_id: str) -> dict:
    """按 unit_id 选一份教学料（同一章的两个节拿到不同的料）。"""
    chapter_no = int("".join(ch for ch in _chapter_of(unit_id) if ch.isdigit()) or "1")
    section_no = int("".join(ch for ch in unit_id.split("-")[-1] if ch.isdigit()) or "1")
    return _UNIT_RECIPES[(chapter_no - 1) * 2 + (section_no - 1) % len(_UNIT_RECIPES)]


def _lesson_content(unit_id: str, anchor: str, recipe: dict, concepts: list[dict]) -> dict:
    """一个单元的教学内容：原文 + 正文 + 公式详解 + 例题。

    这里的正文是**照着校验规则写的**：篇幅过地板、编号条目全覆盖、例题有 2 步、
    公式详解被人引用 —— 所以 `fresh_checks` 对它是 0 条硬问题（不是靠把 checks 写空）。
    """
    tag = anchor  # 1.1 / 1.2 / 2.1 / 2.2
    definition_label = f"定义{tag}.1"
    example_label = f"例{tag}.1"
    # ⚠️ 每个概念占位符在正文里**恰好出现一次**（校验规则是硬问题，不是提醒）——
    #    所以这里逐条注入，不用 `"、".join(...)` 那种会在两处重复的写法。
    links = {c["id"]: f"[[CONCEPT:{c['id']}|{c['name']}]]" for c in concepts}
    primary = concepts[0]
    extra = ""
    if len(concepts) > 1:
        second = concepts[1]
        extra = (f"\n\n同样地，{links[second['id']]}这一类对象也按逐分量的方式规定相等。"
                 f"\n")
    original = f"""# {tag} {recipe['title']}

{definition_label} 由若干个数排成的一列

$${recipe['symbol']}=({recipe['a']})$$

称为{recipe['object']}，其中的每个数称为它的分量。

{recipe['object']}可以相加，规则是对应分量分别相加。

{example_label} {recipe['example_text']}

解. 按对应分量相加，得答案 {recipe['sum']}。
"""
    body = f"""上一节建立了集合与映射的写法，这里接着用它描述{recipe['object']}。本节先把{recipe['object']}是什么讲清楚，下面几节再讲它的运算。

{definition_label} 说的其实是打包：把若干个数按固定次序排成一列，整体就叫{recipe['object']}，写成 $${recipe['symbol']}=({recipe['a']})$$。这一列里的每个数都有固定位置，位置上的数就是它的分量。两个{recipe['object']}相等，指的是位置对位置、分量对分量全都相同；次序不同就是两个不同的{recipe['object']}。{links[primary['id']]}说的就是这个意思，后面每一条运算都建立在它上面。{extra}
单个{recipe['object']}什么也做不了。所以紧接着规定{recipe['formula']}：把对应位置上的分量分别处理，结果仍然是同样长度的{recipe['object']}。例如 {recipe['a']} 与 {recipe['b']} 按位处理，就得到 {recipe['sum']}。这条规则只有一句话，但它把"一列数"变成了"可以计算的对象"，下面每一步都要用到它。为什么必须逐位处理、而不能整体当成一个数，[[EXPLAIN:F-0001|{recipe['formula']}]]那条详解里有说明。

有了这条规则，两个{recipe['object']}就能拼在一起。{example_label} 是最简单的一次：先确认两边规模相同，再逐位处理。

[[EXAMPLE:E-01]]

这一节的结论只有一句：{recipe['object']}的相等与运算都是逐分量定义的。下一节用它来讨论更一般的线性组合。
"""
    explainables = [{
        "id": "F-0001", "kind": "formula", "name": recipe["formula"],
        "what": recipe["formula_what"], "why": recipe["formula_why"],
        "markdown": f"设两个{recipe['object']}规模相同，把对应位置上的分量分别处理，"
                    f"就得到一个新的同样规模的{recipe['object']}。",
    }]
    examples = [{
        "id": "E-01", "label": example_label, "statement": recipe["example_text"],
        "steps": [
            {"step": 1, "markdown": f"先看规模：两边的长度相同，可以逐位处理。",
             "basis": definition_label},
            {"step": 2, "markdown": f"逐位处理，得到 {recipe['sum']}。",
             "basis": recipe["formula"]},
        ],
    }]
    return {"original": original, "body": body, "explainables": explainables,
            "examples": examples, "primary": primary["id"]}


def _concept_layout(units: tuple[str, ...]) -> list[dict]:
    """概念表：`C-0001` 被**所有**单元引用；`C-0002` 只被 CH02 的单元引用。

    为什么 C-0001 要跨单元共享：`test_a2_catches_a_lost_occurrence` 的套路是
    "把 concepts[0] 的 occurrences 砍掉一节，断言 doctor 报出那一节丢了引用" ——
    共享概念才测得出"登记在本节名下"这条收窄的判据。
    """
    chapters = sorted({_chapter_of(u) for u in units})
    occurrences = [{"unit_id": u, "anchor": _anchor_of(u, i + 1), "chapter": _chapter_of(u)}
                   for i, u in enumerate(units)]
    concepts = [{
        "id": "C-0001", "name": "逐分量相等", "type": "definition",
        "brief": "位置对位置、分量对分量相同", "explanation": "两个对象相等，指的是对应分量全都相同",
        "formal": "$(a_1,\\dots,a_n)=(b_1,\\dots,b_n)\\iff a_i=b_i$",
        "occurrences": occurrences,
    }]
    ch2 = [u for u in units if _chapter_of(u) == "CH02"]
    if len(chapters) > 1 and ch2:
        concepts.append({
            "id": "C-0002", "name": "矩阵", "type": "definition",
            "brief": "数排成的矩形表", "explanation": "把数排成若干行若干列的矩形表，整体叫矩阵",
            "formal": "$\\boldsymbol{A}=(a_{ij})_{m\\times n}$",
            "occurrences": [{"unit_id": u, "anchor": _anchor_of(u, i + 1), "chapter": "CH02"}
                            for i, u in enumerate(ch2)],
        })
    return concepts


# ────────────────────────────────────────── 主构建

def build(root, *, book_id: str = "synth-book", title: str = "合成教材",
          units=None, broken: str | None = None, revision: int = 1,
          lesson_overrides: dict | None = None,
          with_delivery: bool = True) -> SynthBook:
    """在 `root` 里造一本合成教材，返回它的产物位置。

    `units`            覆盖默认的 2 章 × 2 节（例如只要 `("CH01-S01",)`）。
    `lesson_overrides` 按 unit_id 覆盖教案字段（在重算 checks **之前**合并，
                       所以覆盖之后 checks 仍然是"按当前规则算出来的"）。
    `broken`           建好之后故意弄坏一处，见 `BREAKAGES`。
    """
    from blocks.lesson import snapshot as S

    root = Path(root)
    unit_ids = tuple(units or DEFAULT_UNITS)
    chapter_ids = tuple(sorted({_chapter_of(u) for u in unit_ids}))
    parse = root / "parse"
    lessons = root / "lesson"
    audit = root / "audit"
    pages_root = root / "delivery" / "整本" / book_id
    page = pages_root / chapter_ids[0]

    # ── 解析产物 ────────────────────────────────────────────────
    concepts = _concept_layout(unit_ids)
    concepts_by_unit: dict[str, list[dict]] = {u: [] for u in unit_ids}
    for concept in concepts:
        for occ in concept["occurrences"]:
            concepts_by_unit.setdefault(occ["unit_id"], []).append(concept)

    lesson_units, chapter_meta = [], []
    for chapter in chapter_ids:
        chapter_units = [u for u in unit_ids if _chapter_of(u) == chapter]
        sections = []
        for i, unit in enumerate(chapter_units):
            anchor = _anchor_of(unit, i + 1)
            rel = f"original_text/{unit}.md"
            sections.append({"id": unit, "kind": "section", "anchor": anchor,
                             "title": _recipe(unit)["title"], "original_text": rel})
            lesson_units.append({
                "unit_id": unit, "level": "section", "chapter": chapter, "section": unit,
                "title": _recipe(unit)["title"], "anchor": anchor, "original_text": rel,
            })
        chapter_meta.append({"id": chapter, "kind": "chapter", "anchor": chapter[-1],
                             "title": f"第{chapter[-1]}章 合成内容", "sections": sections})

    _write_json(parse / "chapters.json", {
        "_meta": {"file_type": "chapters", "schema_version": "2.0",
                  "generator": "aiforstudy.parse.structure", "book_id": book_id},
        "book": {"title": title, "source_sha256": _sha256(title)},
        "chapters": chapter_meta, "lesson_units": lesson_units, "frontmatter": [],
    })
    _write_json(parse / "toc.v2.json", {
        "_meta": {"file_type": "toc_v2", "schema_version": "1.0"},
        "book_title": title,
        "nodes": [{"toc_id": f"T-{i:04d}", "level": 2, "number": unit[-2:],
                   "title": _recipe(unit)["title"], "parent": None, "order": i + 1,
                   "page": {"printed": str(i + 1), "pdf": None}, "kind": "teaching",
                   "confirmed": True}
                  for i, unit in enumerate(unit_ids)],
        "disagreements": [], "unresolved": [], "suspicious": [], "revisions": [],
    })
    _write_json(parse / "body.json", {
        "_meta": {"file_type": "body", "schema_version": "1.0"},
        "book": {"title": title, "source_sha256": _sha256(title)},
        "units": [{"unit_id": unit, "declared": True, "anchor": _anchor_of(unit, i + 1),
                   "title": _recipe(unit)["title"], "level": "section",
                   "original_text": f"original_text/{unit}.md",
                   "content_sha256": _sha256(_lesson_content(unit, _anchor_of(unit, i + 1),
                                                             _recipe(unit),
                                                             concepts_by_unit[unit])["original"])}
                  for i, unit in enumerate(unit_ids)],
        "empty_nodes": [], "undeclared": [], "orphans": [], "unconfirmed": [],
        "content_overlaps": [], "anchor_conflicts": [], "unmatched_titles": [],
    })
    _write_json(parse / "concepts.json", {
        "_meta": {"file_type": "concepts", "schema_version": "1.0",
                  "generator": "aiforstudy.parse.concepts"},
        "counts": {"units_total": len(unit_ids), "units_with_concepts": len(unit_ids),
                   "concepts_total": len(concepts)},
        "source_parts": [{"unit_id": u, "concepts": len(concepts_by_unit[u])} for u in unit_ids],
        "concepts": concepts,
    })
    for unit in unit_ids:
        _write_json(parse / "concepts.parts" / f"{unit}.json", {
            "_meta": {"file_type": "concepts_part", "schema_version": "1.0",
                      "unit_id": unit},
            "unit_id": unit,
            "concepts": [{k: c[k] for k in ("id", "name", "type", "formal")}
                         for c in concepts_by_unit[unit]],
        })
    (parse / "concepts.gaps.parts").mkdir(parents=True, exist_ok=True)
    _write_json(parse / "exercises.json", {
        "_meta": {"file_type": "exercises", "schema_version": "1.0"}, "书": title,
        "segments": [], "entries": [], "统计": {"习题": 0, "答案": 0},
    })
    _write_json(parse / "images.json", {
        "_meta": {"file_type": "images", "schema_version": "1.0"},
        "note": "合成样本：一张 1×1 的占位图",
        "sources": {"markdown_refs": 0, "page_figures": 1, "figures_json": None},
        "images": [{"id": "IMG-0001", "source": "page", "page": 1,
                    "file": "images/synth-IMG-0001.png", "confidence": "high",
                    "needs_review": False}],
    })
    (parse / "images").mkdir(parents=True, exist_ok=True)
    (parse / "images" / "synth-IMG-0001.png").write_bytes(_PNG_1X1)
    _write_json(parse / "ingest.json", {
        "_meta": {"file_type": "ingest", "schema_version": "1.0"},
        "route": "synthetic", "via": "tests/fixtures/synth_book.py",
        "reason": "离线合成夹具，没有 PDF", "page_ranges": None, "dry_run": True,
        "produced": ["chapters.json", "concepts.json", "exercises.json"],
    })

    # ── 历史教案形状夹具（不执行已退役作者或质量规则）──
    lesson_rows = []
    for i, unit in enumerate(unit_ids):
        anchor = _anchor_of(unit, i + 1)
        recipe = _recipe(unit)
        content = _lesson_content(unit, anchor, recipe, concepts_by_unit[unit])
        (parse / "original_text" / f"{unit}.md").parent.mkdir(parents=True, exist_ok=True)
        (parse / "original_text" / f"{unit}.md").write_text(content["original"], encoding="utf-8")
        lesson = {
            "_meta": {"file_type": "lesson", "schema_version": "2.0",
                      "generator": "tests/fixtures/synth_book"},
            "unit_id": unit, "anchor": anchor, "title": recipe["title"], "level": "section",
            "chapter": _chapter_of(unit), "section": unit,
            "original_text": f"original_text/{unit}.md",
            "original_sha256": _sha256(content["original"]),
            "one_line": f"{recipe['object']}的相等与运算都是逐分量定义的",
            "revision": revision,
            "body_markdown": content["body"],
            "concepts": [{"id": c["id"], "role": "introduced", "explained_where": "body"}
                         for c in concepts_by_unit[unit]],
            "figures": [], "annotations": [],
            "explainables": content["explainables"], "examples": content["examples"],
            "figure_requests": [], "diagrams": [], "corrections": [],
            "notation": [{"符号": recipe["symbol"], "含义": recipe["object"]}],
        }
        if lesson_overrides and unit in lesson_overrides:
            lesson.update(lesson_overrides[unit])
        # 产品自己的重算 —— 存进去的就是"按当前规则算出来"的那一份
        # Historical specimen data, never a current approval. Production smoke
        # uses synthetic.* artifacts and cannot publish these saved lessons.
        lesson["checks"] = {"problems":[],"warnings":[],"stats":{},"synthetic":True,
                            "quality_verified":False}
        _write_json(lessons / "lessons" / f"{unit}.json", lesson)
        lesson_rows.append({
            "unit_id": unit, "anchor": anchor, "title": recipe["title"],
            "status": "done" if not lesson["checks"]["problems"] else "needs_review",
            "path": f"lessons/{unit}.json", "problems": lesson["checks"]["problems"],
            "warnings": lesson["checks"]["warnings"], "stats": lesson["checks"]["stats"],
            "why": "合成夹具初始状态", "backfill": {"backfilled": 0, "gaps": 0},
        })

    _write_json(lessons / "lessons.index.json", {
        "_meta": {"file_type": "lesson_index", "schema_version": "1.0"},
        "book": title, "units": lesson_rows,
        "totals": {"units_total": len(unit_ids)},
        "max_request_bytes": 0, "original_text_sha256": {}, "original_text_unchanged": True,
    })
    _write_json(lessons / "snapshot.json", S.build_snapshot(parse, lessons, book=title))

    # ── 审核产物：回路结论 + 「卡在哪」记录（两边必须一致）─────────────
    for unit in unit_ids:
        _write_json(audit / f"{unit}.loop.json", {
            "_meta": {"file_type": "audit_loop", "schema_version": "1.0"},
            "unit_id": unit, "结论": "已通过", "轮次": 1,
        })
        (audit / f"{unit}.stuck.jsonl").parent.mkdir(parents=True, exist_ok=True)
        (audit / f"{unit}.stuck.jsonl").write_text(
            json.dumps({"unit": unit, "结论": "已通过", "卡在哪": None}, ensure_ascii=False) + "\n",
            encoding="utf-8")
    for chapter in chapter_ids:
        rows = [{"unit_id": u, "steps": {"回路": {"结论": "已通过"}}}
                for u in unit_ids if _chapter_of(u) == chapter]
        _write_json(lessons / "chapter-runs" / f"{chapter}.json", {
            "_meta": {"file_type": "chapter_run", "schema_version": "1.0"},
            "chapter": chapter, "节": rows,
        })

    # ── 交付页 ──────────────────────────────────────────────────
    if with_delivery:
        _write_delivery(page, parse, lessons, unit_ids, revision)

    book = SynthBook(root=root, parse=parse, lessons=lessons, audit=audit,
                     pages_root=pages_root, page=page, book_id=book_id, title=title,
                     unit_ids=unit_ids, chapter_ids=chapter_ids, broken=broken)
    if broken:
        if broken not in BREAKAGES:
            raise ValueError(f"未知的 broken={broken!r}；可用：{sorted(BREAKAGES)}")
        BREAKAGES[broken](book)
    return book


def _write_delivery(page: Path, parse: Path, lessons: Path, unit_ids, revision: int) -> None:
    """交付页：三件套 + render.index.json + 范围.json + 模型产物。"""
    page.mkdir(parents=True, exist_ok=True)
    (page / "data").mkdir(exist_ok=True)
    (page / "assets").mkdir(exist_ok=True)
    (page / "models").mkdir(exist_ok=True)
    (page / "index.html").write_text(
        "<!doctype html>\n<html lang=\"zh\"><head><meta charset=\"utf-8\">"
        "<title>合成教材</title></head><body><div id=\"app\"></div>"
        "<script src=\"data/data.js\"></script></body></html>\n", encoding="utf-8")
    (page / "assets" / "app.js").write_text("// 合成夹具：交付页脚本占位\n", encoding="utf-8")
    (page / "data" / "data.js").write_text(
        "window.__SYNTH__ = " + json.dumps({"units": list(unit_ids)}, ensure_ascii=False) + ";\n",
        encoding="utf-8")

    from blocks.render.build import CONCEPT_COPY_FIELDS, concept_copy_fingerprint
    concepts = {c["id"]: c for c in _read_json(Path(parse) / "concepts.json")["concepts"]}
    page_copy = {cid: {f: c.get(f) for f in CONCEPT_COPY_FIELDS} for cid, c in concepts.items()}
    built = {}
    for unit in unit_ids:
        lesson = _read_json(Path(lessons) / "lessons" / f"{unit}.json")
        built[unit] = int(lesson.get("revision") or 0)
        _write_json(page / "models" / f"{unit}.json", {
            "_meta": {"file_type": "model", "source_lesson_revision": built[unit]},
            "unit_id": unit,
        })
    _write_json(page / "render.index.json", {
        "_meta": {"file_type": "render_index", "schema_version": "1.0"},
        "scope": list(unit_ids), "counts": {"units": len(unit_ids)},
        "built_from": built,
        "built_from_concepts": concept_copy_fingerprint(page_copy),
    })
    _write_json(page / "范围.json", {
        "_meta": {"file_type": "delivery_scope"},
        "原本要做": list(unit_ids),
    })


# ────────────────────────────────────────── 故意弄坏一处

def _break_index(book: SynthBook) -> None:
    """A1：索引里少一整章的行 —— 并行丢更新。"""
    index = book.index()
    index["units"] = [r for r in index["units"] if not str(r["unit_id"]).startswith("CH02")]
    _write_json(book.lessons / "lessons.index.json", index)


def _break_index_ghost(book: SynthBook) -> None:
    """A1 反方向：索引里有、磁盘上没有 —— 幽灵行。"""
    index = book.index()
    index["units"].append({"unit_id": "CH99-S99", "status": "done"})
    _write_json(book.lessons / "lessons.index.json", index)


def _break_concept(book: SynthBook) -> None:
    """A2：概念重抽把教案引用行抹掉。"""
    path = book.parse / "concepts.json"
    payload = _read_json(path)
    occurrences = payload["concepts"][0]["occurrences"]
    payload["concepts"][0]["occurrences"] = occurrences[:1]
    _write_json(path, payload)


def _break_concept_unknown(book: SynthBook) -> None:
    """A2 反方向：教案引用了一个既不在概念表、也不在 gaps 的 id。"""
    unit = book.unit_ids[0]
    lesson = book.lesson(unit)
    lesson["concepts"] = [{"id": "C-9999", "role": "introduced", "explained_where": "body"}]
    _write_json(book.lessons / "lessons" / f"{unit}.json", lesson)


def _break_snapshot(book: SynthBook) -> None:
    """A3：整本快照停在一个过期结论上。"""
    snap = book.snapshot()
    snap["progress"]["needs_review"] = [book.unit_ids[0]]
    _write_json(book.lessons / "snapshot.json", snap)


def _break_no_snapshot(book: SynthBook) -> None:
    """A3：有教案产物、却没有 snapshot.json。"""
    (book.lessons / "snapshot.json").unlink()


def _break_lesson_checks(book: SynthBook) -> None:
    """教案快照不过期：`checks` 停在旧规则上（存盘的不等于按当前规则重算的）。"""
    unit = book.unit_ids[0]
    lesson = book.lesson(unit)
    lesson["checks"]["problems"] = list(lesson["checks"]["problems"]) + ["旧规则的结论"]
    _write_json(book.lessons / "lessons" / f"{unit}.json", lesson)


def _break_chapter_record(book: SynthBook) -> None:
    """章记录写了两份产物不一致：记录里少了磁盘上真实存在的教案。"""
    path = book.lessons / "chapter-runs" / f"{book.chapter_ids[0]}.json"
    rec = _read_json(path)
    rec["节"] = rec["节"][1:]
    _write_json(path, rec)


def _break_stuck(book: SynthBook) -> None:
    """「卡在哪」记录停在旧状态（与回路结论不一致）。"""
    unit = book.unit_ids[0]
    (book.audit / f"{unit}.stuck.jsonl").write_text(
        json.dumps({"unit": unit, "结论": "还卡着", "卡在哪": "旧记录"}, ensure_ascii=False) + "\n",
        encoding="utf-8")


def _break_delivery_scope(book: SynthBook) -> None:
    """范围记录缩水：页面里有这一节，记录里没有。"""
    plan = book.scope_plan()
    plan["原本要做"] = plan["原本要做"][:-1]
    _write_json(book.page / "范围.json", plan)


def _break_delivery_scope_dangling(book: SynthBook) -> None:
    """范围记录里有一节，既没有教案也没有概念产物（页面上会是空的）。"""
    plan = book.scope_plan()
    plan["原本要做"] = list(plan["原本要做"]) + ["CH99-S99"]
    _write_json(book.page / "范围.json", plan)


def _break_delivery_files(book: SynthBook) -> None:
    """交付页产物不全（双击打不开）。"""
    (book.page / "data" / "data.js").unlink()


def _break_delivery_counts(book: SynthBook) -> None:
    """counts.units 与 scope 对不上。"""
    idx = book.page_index()
    idx["counts"]["units"] = len(idx["scope"]) + 1
    _write_json(book.page / "render.index.json", idx)


def _break_delivery_stale(book: SynthBook) -> None:
    """交付页是用旧版教案建的（built_from 对不上 revision）。"""
    idx = book.page_index()
    first = idx["scope"][0]
    idx["built_from"][first] = int(idx["built_from"][first]) + 1
    _write_json(book.page / "render.index.json", idx)


def _break_delivery_concept_copy(book: SynthBook) -> None:
    """交付页里嵌的概念表副本过期（改了概念表、页面没重建）。"""
    path = book.parse / "concepts.json"
    payload = _read_json(path)
    payload["concepts"][0]["name"] = "逐分量相等（改名了）"
    _write_json(path, payload)


BREAKAGES = {
    "index": _break_index,
    "index_ghost": _break_index_ghost,
    "concept": _break_concept,
    "concept_unknown": _break_concept_unknown,
    "snapshot": _break_snapshot,
    "no_snapshot": _break_no_snapshot,
    "lesson_checks": _break_lesson_checks,
    "chapter_record": _break_chapter_record,
    "stuck": _break_stuck,
    "delivery_scope": _break_delivery_scope,
    "delivery_scope_dangling": _break_delivery_scope_dangling,
    "delivery_files": _break_delivery_files,
    "delivery_counts": _break_delivery_counts,
    "delivery_stale": _break_delivery_stale,
    "delivery_concept_copy": _break_delivery_concept_copy,
}

#: 每种破坏对应"应该被哪条不变量抓住"（映射记录与测试都用它对齐）
BREAKAGE_INVARIANTS = {
    "index": "教案索引完整",
    "index_ghost": "教案索引完整",
    "concept": "概念登记完整",
    "concept_unknown": "概念登记完整",
    "snapshot": "整本快照不过期",
    "no_snapshot": "整本快照不过期",
    "lesson_checks": "教案快照不过期",
    "chapter_record": "章记录不陈旧",
    "stuck": "卡在哪记录不过期",
    "delivery_scope": "范围记录不缩水",
    "delivery_scope_dangling": "范围记录不缩水",
    "delivery_files": "交付页产物齐全",
    "delivery_counts": "交付页产物齐全",
    "delivery_stale": "交付页不过期",
    "delivery_concept_copy": "交付页的概念表不过期",
}


def fake_package(book: SynthBook, *, extra_dirs=()) -> SimpleNamespace:
    """给 `doctor._pkg()` 用的假打包模块：只暴露它读的两个常量。

    `doctor.delivery_pages()` 走 `_pkg().DELIVERY_DIRS`、`inv_delivery_lists_agree()`
    还要 `_pkg().SECTIONS`（名字必须与 DELIVERY_DIRS 一致）—— 只需要这两样，
    所以 monkeypatch `doc._PKG` 到一个 SimpleNamespace 就够，**不用改生产代码**。
    """
    dirs = [book.page, *extra_dirs]
    sections = [(str(Path(d)), str(Path(d).name), None) for d in dirs]
    return SimpleNamespace(DELIVERY_DIRS=dirs, SECTIONS=sections, ROOT=book.root)


def use_for_doctor(monkeypatch, doc, book: SynthBook, *, include_delivery: bool = True):
    """把 `tools/doctor.py` 的检查范围整体指到这本合成书上（**只 monkeypatch 模块级常量**）。"""
    monkeypatch.setattr(doc, "PARSE", book.parse)
    monkeypatch.setattr(doc, "LESSONS", book.lessons)
    monkeypatch.setattr(doc, "AUDIT", book.audit)
    monkeypatch.setattr(doc, "BOOK", book.book_id)
    monkeypatch.setattr(doc, "DELIVERY_ROOT", book.pages_root)
    if include_delivery:
        monkeypatch.setattr(doc, "_PKG", fake_package(book))


__all__ = ["SynthBook", "build", "fake_package", "use_for_doctor",
           "BREAKAGES", "BREAKAGE_INVARIANTS", "DEFAULT_UNITS", "ROOT"]
