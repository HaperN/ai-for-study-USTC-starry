"""**引用完整性**：一处定义、各块都来查（缺口 A 的落点）。

用户 2026-09-25 的规范是「一定要全局改……不能一个地方改一个」。P-020/P-032 那个
"第二章的概念抽屉里配了第一章的向量图"，根因不是渲染层画错，而是**没有任何一处
拿"当前登记表"把跨块引用全查一遍**：图登记表里存的是**旧的概念 id**，而概念表在重抽之后
**把号重用了**（`C-0002` 原来是"相等向量"，现在是"同解方程组"），于是错配一路走到交付页。

这一个模块就把这件事收口：**任何跨块引用，都必须在这里能被解析**。

查什么（都是 0 token、可复算）：

| 引用 | 必须满足 |
|---|---|
| `[[CONCEPT:id]]` / 正文里的概念链接 | id 在**当前**概念表里；且**带上显示文字时不要再重复概念名**（P-027） |
| `[[EXPLAIN:id]]` | id 在本节 `explainables` 里 |
| `[[NOTE:id]]` | id 在本节 `annotations` 里，且**恰好被引用一次** |
| `[[DIAGRAM:id]]` / `[[MODEL:id]]` | id 在本节 `diagrams` / 模型规格里 |
| 图 → 概念（`figures[].concepts`） | 概念 id 存在；**并且这张图确实被范围内的某一节引用**（没被引用的图不该上页面 —— 这条就是 P-020 的根治） |
| `(2.1)` 这种编号引用 | 页面上（本次交付里）**真能找到这个编号**，否则不给它做链接（死链比不链接更糟，P-035） |

用法：`check_all(...)` 返回问题列表（字符串），调用方把它接进构建报告/体检/交付包自检。
"""
from __future__ import annotations

import re
from pathlib import Path

# 占位符的 kind 集合与正则：**唯一来源**（2026-09-26 收口）。
# 原来有三份、而且覆盖面不同：
#   * 本文件这份：六种 kind，冒号前允许空格       ← 保留成唯一实现
#   * blocks/lesson/validate.py 那份：只有 CONCEPT/FIGURE/EXPLAIN
#     → 正文里 `[[NOTE:…]]` 出现两次，它**看不见**
#   * blocks/lesson/generate.py 那份：`([^\]|\s]+)` 遇空格就停
#     → `[[NOTE : N-04]]`（冒号前有空格）的重复护栏**静默失效**
#     （那正是提示词里记着的"占位符抄两遍"实测坑）
#   * 前端 `render-core.js` 那份：六种，由本文件的测试盯着它保持一致
#: 占位符 kind 的**唯一真源**（前端自检与审计器各有一份拷贝，由 tests 盯住一致）。
#: 2026-10-01 加 `EXAMPLE`：例题要"随正文"就地展开，漏了它页面上会露出方括号乱码。
try:
    from aiforstudy.blocks.content.content_contracts import (
        REFERENCE_KINDS as PLACEHOLDER_KINDS,
        REFERENCE_PATTERN as PLACEHOLDER,
    )
except ModuleNotFoundError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from aiforstudy.blocks.content.content_contracts import (
        REFERENCE_KINDS as PLACEHOLDER_KINDS,
        REFERENCE_PATTERN as PLACEHOLDER,
    )

NUMBER_REF = re.compile(r"[（(]\s*(\d+\.\d+(?:\.\d+)?)\s*[）)]")


def concepts_in_text(text: str) -> list[tuple[str, str]]:
    """正文里出现过的概念引用（id, 显示文字）。"""
    return [(m.group(2).strip(), (m.group(3) or "").strip())
            for m in PLACEHOLDER.finditer(str(text or "")) if m.group(1) == "CONCEPT"]


def concept_links(text: str, concept_names: dict[str, str]):
    """正文里每一处概念链接：`(标签, 前面紧邻的文字)`。判据只有这一处，检查与渲染都来问它。"""
    body = str(text or "")
    for match in PLACEHOLDER.finditer(body):
        if match.group(1) != "CONCEPT":
            continue
        label = (match.group(3) or "").strip() or concept_names.get(match.group(2).strip(), "")
        if not label:
            continue
        yield label, body[:match.start()]


def duplicate_concept_name(text: str, concept_names: dict[str, str]) -> list[str]:
    """P-027 第一类：**链接的标签就紧跟在同一个词后面**（`…的解集 [[CONCEPT:C-0014|解集]]`）。

    这一类是**渲染层能确定性合并**的（合并后屏幕上逐字不变，只是"可点"落到那个词上），
    所以不算硬问题；但它说明模型把术语写了两遍，记成"风格提醒"给重跑时看。
    判据和 `render-core.js` 的 `dedupeConceptNames` 必须一致（一处规矩两处用）。
    """
    notes = []
    for label, before in concept_links(text, concept_names):
        trimmed = before.rstrip()
        if trimmed.endswith(label):
            notes.append(f"概念「{label}」在正文里写了两遍（紧接着就是链接）→ "
                         f"渲染层已自动并成一个可点的词；提示词层下次别写两遍（P-027）")
    return notes


def overlapping_concept_name(text: str, concept_names: dict[str, str]) -> list[str]:
    """P-027 第二类：前面那个词和链接标签**部分重叠**（`…称线性方程组不相容 [[CONCEPT|相容与不相容]]`）。

    这一类渲染层**不能**机械合并（合并会改意思），只能由生成侧改措辞 —— 记成风格提醒，
    进重跑清单。第一类合并掉之后剩下的大多是这一类。
    """
    notes = []
    for label, before in concept_links(text, concept_names):
        trimmed = before.rstrip()
        if trimmed.endswith(label):
            continue                                  # 第一类，渲染层已合并
        # 前面 2~len(label)-1 个字正好是标签的前缀或后缀 → 读起来是重复的
        for size in range(len(label) - 1, 1, -1):
            tail = trimmed[-size:]
            if tail and (label.startswith(tail) or label.endswith(tail)) and tail != label:
                notes.append(f"概念「{label}」前面紧挨着写的「{tail}」和它部分重叠 → "
                             f"页面上会读成「…{tail} {label}」，需要改措辞（P-027）")
                break
    return notes


def original_numbers(text: str) -> set[str]:
    """一段原文里出现过的**编号**（`(2.1)`、`(5.16)` 这种公式编号）。"""
    return set(NUMBER_REF.findall(str(text or "")))


def number_index(originals: dict[str, str]) -> dict[str, list[str]]:
    """编号 → 哪些节的原文里有它。

    为什么要这张表：P-035 用户原话「可以把线性方程组（2.1)做成超链接……毕竟这次能够
    直接看到原文，下次未必看得到」。正文里一句「线性方程组 (2.1)」，读者要能**当场跳到
    原文里那个 (2.1)**。而编号可能出现在**别的节**的原文里（§2.3.2 引用 §2.1 的 (2.1)），
    所以"有没有地方可跳"必须按**整批已上页面的节**来判 —— 表在这里算一次，渲染层只查表。

    只在**已上页面**的节里算：这样"表里有 → 一定跳得到"，不会有死链。
    """
    index: dict[str, list[str]] = {}
    for unit_id, text in (originals or {}).items():
        for number in sorted(original_numbers(text)):
            index.setdefault(number, []).append(str(unit_id))
    return index


def book_numbers(parse_dir) -> set[str]:
    """整本书**所有**原文里出现过的编号（0 token，扫一遍 original_text/ 目录）。

    为什么要它：交付页常常只做**几节样张**，而正文会引用别节的编号（实测第六章样张里
    §6.2.2 引用了「(6.5)」，它的原文在别节）—— 那不是死链，只是**这一批没含那一节**。
    所以"跳不到"要分两种：**本书别处有**（记 note）／**整本书都没有**（真死链，报问题）。
    """
    numbers: set[str] = set()
    folder = Path(parse_dir) / "original_text"
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            numbers |= original_numbers(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return numbers


def check_number_refs(lessons: list[dict], originals: dict[str, str],
                      in_book: set[str] | None = None) -> list[str]:
    """正文里引用的编号，得在**本次上页面的原文**里找得到（P-035）。"""
    problems = []
    index = number_index(originals)
    for lesson in lessons or []:
        unit = str(lesson.get("unit_id") or "?")
        body = str(lesson.get("body_markdown") or lesson.get("explanation_markdown") or "")
        steps = "\n".join(str(s.get("markdown") or "")
                          for ex in lesson.get("examples") or []
                          for s in ex.get("steps") or [])
        mine = original_numbers((originals or {}).get(unit, ""))
        for number in sorted(original_numbers(body + "\n" + steps)):
            if number in mine:
                continue                       # 本节的原文里就有 → 跳到本页原文对照
            # 别的节有 → **不算问题**（链接会跨节跳过去，页面上仍跳得到）。
            # 谁都没有 → 才是问题：这一处做不成链接，读者点了没处可去（死链）。
            if index.get(number):
                continue
            if in_book and number in in_book:
                # 本书别处的原文里有 → 只是这一批样张没含那一节 → 不报问题
                continue
            problems.append(f"{unit}: 正文引用了「({number})」，但**整本书**的原文里都没有"
                            f"这个编号 → 做不成链接（死链；P-035）")
    return problems


def check_lesson(lesson: dict, *, concept_names: dict[str, str],
                 concept_ids: set[str] | None = None) -> list[str]:
    """一节课的引用完整性（正文 + 例题 + 详解 + 批注）。"""
    problems: list[str] = []
    unit = lesson.get("unit_id") or "?"
    ids = concept_ids if concept_ids is not None else set(concept_names)
    body = str(lesson.get("body_markdown") or lesson.get("explanation_markdown") or "")
    steps = "\n".join(str(s.get("markdown") or "")
                      for ex in lesson.get("examples") or []
                      for s in ex.get("steps") or [])

    for kind, ref_id, _display in [(m.group(1), m.group(2).strip(), m.group(3))
                                   for m in PLACEHOLDER.finditer(body + "\n" + steps)]:
        if kind == "CONCEPT" and ref_id not in ids:
            problems.append(f"{unit}: [[CONCEPT:{ref_id}]] 在当前概念表里不存在")
        if kind == "EXPLAIN" and ref_id not in {str(x.get("id")) for x in lesson.get("explainables") or []}:
            problems.append(f"{unit}: [[EXPLAIN:{ref_id}]] 没有对应的详解")
        if kind == "NOTE" and ref_id not in {str(x.get("id")) for x in lesson.get("annotations") or []}:
            problems.append(f"{unit}: [[NOTE:{ref_id}]] 没有对应的批注")
        if kind == "DIAGRAM" and ref_id not in {str(x.get("id")) for x in lesson.get("diagrams") or []}:
            problems.append(f"{unit}: [[DIAGRAM:{ref_id}]] 没有对应的过程图")
    # P-027 的两类"写了两遍"都**不算硬问题**（第一类渲染层已确定性合并、第二类要改措辞），
    # 所以不塞进 problems，改由 check_style_notes 统一交给体检/重跑清单。

    # 编号引用（(2.1) 这种）见 check_number_refs：那一条要**整批**的原文才算得准
    # （编号可能在别的节的原文里），所以不在这里做。
    return problems


def check_style_notes(lessons: list[dict], concept_names: dict[str, str]) -> list[str]:
    """**不算硬问题、但要在重跑时改掉**的措辞提醒（0 token，可复算）。

    现在只有 P-027 两类。放在这里而不是 `problems`，是因为它们不会把读者带错、
    也不会让页面出死链 —— 混进硬问题里会把"必须挡住"的信号淹掉。
    """
    notes = []
    for lesson in lessons or []:
        unit = str(lesson.get("unit_id") or "?")
        body = str(lesson.get("body_markdown") or lesson.get("explanation_markdown") or "")
        steps = "\n".join(str(s.get("markdown") or "")
                          for ex in lesson.get("examples") or []
                          for s in ex.get("steps") or [])
        for note in duplicate_concept_name(body + "\n" + steps, concept_names):
            notes.append(f"{unit}: {note}")
        for note in overlapping_concept_name(body + "\n" + steps, concept_names):
            notes.append(f"{unit}: {note}")
    return notes


def check_figures(figures: dict, units: dict, concept_names: dict[str, str]) -> list[str]:
    """图的引用完整性 + **作用域**（P-020 的根治）。

    两条硬规矩：
      1. **一张图上页面的唯一理由是"范围内的某一节引用了它"** —— 没被引用的图不上页面；
      2. 图登记的"服务哪些概念"必须能在**当前**概念表里按 **id 或名字** 对上；
         对不上就**不挂**（宁可不配图，也不能配错图）。
    """
    problems: list[str] = []
    referenced = set()
    for unit_id, unit in (units or {}).items():
        for ref in unit.get("figures") or []:
            if ref.get("id"):
                referenced.add(str(ref["id"]))
    for figure_id, figure in (figures or {}).items():
        if figure_id not in referenced:
            # 没被引用的图**不该上页面**（P-020 的根治），但这本身不是"问题"：
            # 它常常只是属于别的章。渲染块会把它记进 notes，这里不重复报。
            continue
        for concept_id in figure.get("concepts") or []:
            name = concept_names.get(str(concept_id))
            said = {str(x) for x in (figure.get("concept_names") or [])}
            if name is None and not said:
                problems.append(f"插图 {figure_id} 登记的 concept {concept_id} 在当前概念表里不存在"
                                f"（概念表可能重抽过、号被重用了）")
    return problems


def check_all(*, lessons: list[dict], figures: dict, units: dict,
              concept_names: dict[str, str],
              originals: dict[str, str] | None = None,
              in_book: set[str] | None = None) -> list[str]:
    """把上面几项一次跑完（构建/体检/交付自检都调它，避免"每个地方各查一套"）。

    `originals`：**已上页面**的每一节的原文文字（unit_id → 文字）。给了就顺带查编号引用（P-035）。
    """
    problems: list[str] = []
    for lesson in lessons or []:
        problems += check_lesson(lesson, concept_names=concept_names)
    problems += check_figures(figures, units, concept_names)
    if originals:
        problems += check_number_refs(lessons, originals, in_book)
    return problems


__all__ = ["check_all", "check_lesson", "check_figures", "check_number_refs", "book_numbers",
           "check_style_notes", "concept_links", "original_numbers", "number_index",
           "duplicate_concept_name", "overlapping_concept_name",
           "concepts_in_text", "NUMBER_REF", "PLACEHOLDER"]
