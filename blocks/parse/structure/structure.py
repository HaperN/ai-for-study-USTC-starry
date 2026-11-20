"""结构层：把 PDF 抽出来的纯文本变成"带规范标题的 markdown + 结构清单"。

为什么需要这一层（实测依据）：
  * 真实 PDF 抽出来的文本里 **0 个 `#` 标题**；
  * 编号体系可能混用、甚至自相矛盾 —— 例如某报告里 `第二部分` 下面出现 `4.1`，
    按数字看像"第四章第一节"，其实是"第二部分第四节的第 1 小点"；
  * **约一半的行是被 PDF 排版折断的半句**（实测 151/303 行）。

这一层做三件事：**去噪 → 合折行 → 判层级并重新编号**。

层级判定用"**数字前缀嵌套**"这个确定性规则（不依赖模型、可测、免费）：
  * 把每个标题解析成一个数字路径：`第1章`→(1,)、`四、`→(4,)、`4.1`→(4,1)、`4.1.1`→(4,1,1)
  * 若某标题的数字路径去掉末位后，能匹配到前面某个标题 → 它就是那个标题的**子级**
  * 于是 `4.1` 自动成为 `四、`（(4,)）的子级；`第1章` 下的 `1.1` 也自动成为它的子级
  * 匹配不到父级的（如 `第一部分`）按方案默认层级落位

规范编号**只按文档顺序生成**，不信任原文数字（原文可能自相矛盾），
矛盾处记进 `warnings`。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

SCHEMA_VERSION = "1.0"
DEFAULT_MAX_LEVEL = 4   # 章 / 节 / 小节 / 知识点块；教案块自己挑用哪一层
ANCHOR_MAX_LEVEL = 3    # 下游 normalize-source 只认三层（章/节/小节）

# ── 去噪用的模式 ────────────────────────────────────────────────────────
PAGE_MARK = re.compile(r"^\s*\[\s*第\s*(\d+)\s*页\s*\]\s*$")
PAGE_COUNTER = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
BARE_NUMBER = re.compile(r"^\s*\d{1,4}\s*$")
DOT_LEADER = re.compile(r"\.{6,}")
# 目录标记（目录页的标题行）
TOC_MARKER = re.compile(r"^\s*(目\s*录|目\s*次|Contents|CONTENTS|Table of Contents)\s*$")
# 目录条目：标题 + 尾部页码（可能有引导点）
TOC_ENTRY = re.compile(r"^\s*\S.{0,58}?[\s.．·]{2,}\d{1,4}\s*$")
# 目录条目（页码在**行尾括号里**、用省略号引导）—— 中文教材常见：
#     `第2章 留数及其应用 ……（107）` / `1.1 复数 …… (3)`
# ⚠️ 为什么单列一条（2026-09-27 全流程试跑实测抓出 B-13）：
#    这本书的目录**页码在行内括号里**，而原来的 `TOC_ENTRY` 只认"点/空格引导直接接数字"、
#    `_looks_like_toc_entry()` 只认"下一行是裸页码" —— 两种都不匹配它的排布，
#    于是**整段目录没被丢掉**：17 个目录条目变成了"章"
#    （实测 CH02–CH05 的标题是 `留数及其应用 …… (107)` 这种，与 CH11–CH14 的真章重复），
#    白白多跑了 17 个单元的生成 + 审核。
TOC_ENTRY_INLINE = re.compile(r"[…⋯.．·\s]{2,}[（(]\s*\d{1,4}\s*[）)]\s*$")
# 习题答案行（**不是标题**）：一行里出现两个以上 `(数字)` 小问编号，例如
#     `1. (1) $8 + \mathrm{i}3\pi$ (2) $8 - \mathrm{i}3\pi$`
# 实测这本书的答案附录因此被判成了 CH15/CH16/CH17 三个"章"（26 个单元）。
# 判据取"**两个以上**小问编号"是为了保守：正文标题不会长这样。
ANSWER_LIKE = re.compile(r"(?:[（(]\s*\d+\s*[）)][^\n]{0,40}){2,}")

# ── 标题模式：(scheme, 正则, 固定层级) ──────────────────────────────────
CN_NUM = r"[一二三四五六七八九十百零〇]+"
AR_NUM = r"\d+"
HEADING_PATTERNS: list[tuple[str, re.Pattern[str], int | None]] = [
    ("chapter", re.compile(rf"^第\s*({CN_NUM}|{AR_NUM})\s*([章篇])[\s、.．·:：]*\s*(.*)$"), 1),
    ("chapter", re.compile(rf"^第\s*({CN_NUM}|{AR_NUM})\s*部分[\s、.．·:：]*\s*(.*)$"), 1),
    ("chapter", re.compile(r"^\s*(?:Chapter|CHAPTER)\s*(\d+)[\s.．:：]*\s*(.*)$"), 1),
    # `绪论`/`引言`：**正文的第 0 章**（没有「第N章」编号，但节号是 `0.1` `0.2`）。
    # ⚠️ 为什么必须认（2026-09-27 实测《数值计算方法》）：它排在目录之后、第 1 章之前，
    #    里面是 `0.1 数值计算方法与算法`、`0.2 误差与有效数字`、`0.3 矩阵和向量范数`
    #    —— 实打实的教学内容（约 13 页）。不认它，这 13 页就没有单元。
    #    编号给 `0` 而不是"无编号"：`0.1` 的父级正好是它（`numeric_path` 见下）。
    ("chapter", re.compile(r"^\s*()(绪\s*论|引\s*言|导\s*论|绪\s*言)\s*$"), 1),
    ("cn",      re.compile(rf"^\s*({CN_NUM})\s*[、.．]\s*(.*)$"), None),
    # `§` 被 OCR 误认的字符不止 8，还有 `$` / `S`（实测 `$1.8复数*` 就是 `§1.8 复数*`）
    ("num",     re.compile(r"^\s*[*＊]?\s*[§$S]?\s*(\d+(?:\.\d+)+)[\s、.．:：]*\s*(\S.*)$"), None),
    ("paren",   re.compile(rf"^\s*[（(]\s*({CN_NUM})\s*[）)]\s*(.*)$"), None),
    # 普通编号：`、`/`．` 作分隔，或 `12.` 后面**不能**再跟数字
    # （否则 `4.1 2` 会被拆成 number=4 / title="1 2"，把页码当成标题）
    # ⚠️ 允许最前面有**选学标记 `*`**（2026-09-27 实测）：`*1.3 Hermite 插值`、
    #    `*5.4 经典迭代格式的统一` 这些是**真节**，不认它们就整节没有单元
    #    （《数值计算方法》实测 4 节）。
    ("num",     re.compile(r"^\s*[*＊]?\s*[§$S]?\s*(\d+)\s*(?:[、．]|\.(?!\d))\s*(\S.*)$"), None),
]

NOT_HEADING = [
    re.compile(r"^\s*[-*+>]\s"),      # 列表
    re.compile(r"^\s*\|"),             # 表格
    re.compile(r"^\s*```"),            # 代码围栏
    re.compile(r"^\s*\$\$"),           # 公式块
]

CN_DIGITS = {c: i for i, c in enumerate("零一二三四五六七八九", 0)}
SENTENCE_END = "。！？；：!?;:…）)」』】》\"'"
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\(?\d+[）).]|[（(]\d+[）)])\s")


def cn_to_int(value: str) -> int | None:
    """中文数字 → 整数（一 / 十 / 十一 / 二十三 / 一百 这类常见写法）。"""
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if any(c not in CN_DIGITS and c not in "十百" for c in value):
        return None
    if "百" in value:
        head, _, tail = value.partition("百")
        total = (CN_DIGITS.get(head, 1) if head else 1) * 100
        if tail:
            if "十" in tail:
                tens, _, ones = tail.partition("十")
                total += (CN_DIGITS.get(tens, 1) if tens else 1) * 10 + CN_DIGITS.get(ones, 0)
            else:
                total += CN_DIGITS.get(tail, 0)
        return total
    if "十" in value:
        tens, _, ones = value.partition("十")
        return (CN_DIGITS.get(tens, 1) if tens else 1) * 10 + CN_DIGITS.get(ones, 0)
    if len(value) == 1:
        return CN_DIGITS.get(value)
    return None


# ── 数据结构 ────────────────────────────────────────────────────────────
@dataclass
class Line:
    page: int
    text: str
    source_line: int
    is_heading: bool = False
    #: 这一行是**裸编号**（`2.` 这种，没有点号路径）—— 本书用点号编号时它多半是列表项，
    #: 见 `detect_headings`。它先照常当标题，等习题题干判完再决定去留。
    bare_number: bool = False


@dataclass
class Heading:
    scheme: str
    raw_number: str
    title: str
    source_line: int
    page: int
    numeric: tuple[int, ...] | None = None   # 数字路径，(4,1) 表示 4.1
    is_exercise_item: bool = False            # **是习题题干，不是"节"**（见 `mark_exercise_items`）
    bare_number: bool = False                 # 编号是裸编号（`2.`）—— 多半是列表项（见 `detect_headings`）
    toc_confirmed: bool = False               # **书自己的目录里有它** → 题干/裸编号规则不许再动
    level: int = 0                            # 层级 1..max_level
    canonical_level: int = 0                  # 规范层级（重新编号后）
    canonical: str = ""                       # 规范锚点：1 / 1.2 / 1.2.3


@dataclass
class StructureResult:
    structured_markdown: str
    headings: list[Heading] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    max_level: int = DEFAULT_MAX_LEVEL
    # Both Markdown exports must use the same filtered, authority-protected view.
    render_lines: list[Line] = field(default_factory=list, repr=False)

    def to_json(self, source_sha256: str = "") -> dict:
        return {
            "_meta": {
                "file_type": "structure",
                "schema_version": SCHEMA_VERSION,
                "generator": "aiforstudy.parse.structure",
                "source_sha256": source_sha256,
                "max_level": self.max_level,
                "field_notes": {
                    "headings[].scheme": "命中的编号体系：chapter/cn/num/paren",
                    "headings[].numeric": "数字路径，(4,1) 即 4.1；用于前缀嵌套判定",
                    "headings[].level": "推断出的层级（1=章 2=节 3=小节 4=知识点块）",
                    "headings[].canonical": "按文档顺序重新编号后的锚点",
                    "headings[].source_line": "在输入 document.md 里的 1-based 行号",
                },
            },
            "warnings": self.warnings,
            "stats": self.stats,
            "headings": [
                {**asdict(h), "numeric": list(h.numeric) if h.numeric else None}
                for h in self.headings
            ],
        }


# ── 第一步：切页 + 去噪 ─────────────────────────────────────────────────
def split_pages(text: str) -> tuple[list[Line], int]:
    lines: list[Line] = []
    page = 0
    for index, raw in enumerate(text.splitlines(), start=1):
        marker = PAGE_MARK.match(raw)
        if marker:
            page = int(marker.group(1))
            continue
        lines.append(Line(page=page, text=raw, source_line=index))
    if page == 0:
        for line in lines:
            line.page = 1
        page = 1
    return lines, page


def _drop_toc_region(lines: list[Line]) -> tuple[list[Line], int]:
    """丢掉"目录"标记到第一个章标题之间的整段。

    为什么必须整段丢：OCR 出来的目录条目（`1.1向量的线性运算`）和正文标题
    **长得一模一样**，只按行过滤会把整份目录当成章节结构。
    """
    marker_indexes = [i for i, line in enumerate(lines) if TOC_MARKER.match(line.text.strip())]
    if not marker_indexes:
        return lines, 0
    # 只处理前 20% 里的目录标记（书末索引不算）
    limit = max(int(len(lines) * 0.2), 50)
    drop: set[int] = set()
    for marker in marker_indexes:
        if marker > limit:
            continue
        # 目录区一直延伸到"第一个不像目录条目的章标题"为止。
        # 关键：目录里本身也会有一条 `第一章向量`，不能停在它上面。
        end = None
        for j in range(marker + 1, len(lines)):
            matched = heading_match(lines[j].text)
            if matched is None or matched[0] != "chapter":
                continue
            if not _looks_like_toc_entry(lines, j):
                end = j
                break
        if end is None:
            continue
        drop.update(range(marker, end))
    if not drop:
        return lines, 0
    return [line for i, line in enumerate(lines) if i not in drop], len(drop)


def _looks_like_toc_entry(lines: list[Line], index: int) -> bool:
    """判断第 index 行是不是目录条目。

    目录的典型排布（OCR 后一行一个框）：
        第一章向量          ← 章条目
        1.1向量的线性运算    ← 节条目
        7                   ← 页码
    所以不能只看紧邻的下一行，要往后多看两行非空行。
    """
    # ⚠️ 先看**行内页码**（`……（107）`）——这本书的目录就是这个排布，
    #    只靠"下一行是裸页码"认不出来（B-13）。
    if TOC_ENTRY_INLINE.search(lines[index].text if 0 <= index < len(lines) else ""):
        return True

    def bare(i: int) -> bool:
        if not (0 <= i < len(lines)):
            return False
        text = lines[i].text.strip()
        return bool(text) and bool(BARE_NUMBER.match(text))

    # 注意：这里**不看上一行**。目录最后一行的页码正好会落在正文第一个章标题前面，
    # 看上一行会把真正的章开头误判成目录条目，导致目录整段丢不掉。
    seen = 0
    for j in range(index + 1, min(index + 6, len(lines))):
        if not lines[j].text.strip():
            continue
        if bare(j):
            return True
        seen += 1
        if seen >= 2:
            break
    return False


def denoise(lines: list[Line], *, skip: "set[int] | None" = None) -> tuple[list[Line], list[str], dict]:
    """删页码、跨页重复的页眉页脚、目录整段、目录条目行。

    `skip` = **不许动的行号**（代码块的行）：它们既不是页码也不是页眉，
    而且会被反复出现（`end for`），交给这里删就整块没了。
    """
    protected_lines = skip or set()
    def _norm(text: str) -> str:
        return re.sub(r"\s+", "", text)

    seen: dict[str, set[int]] = {}
    for line in lines:
        if line.source_line in protected_lines:
            continue                       # 代码行不参与"跨页重复"的统计
        text = line.text.strip()
        if 0 < len(text) <= 40:
            seen.setdefault(_norm(text), set()).add(line.page)
    recurring_keys = {t for t, pages in seen.items() if len(pages) >= 3}
    recurring = {t for t in recurring_keys}

    # 每页"非空行的前 2 行 / 后 2 行"才算页眉页脚位置（裸数字只在这里才是页码）
    edge: set[int] = set()
    by_page: dict[int, list[int]] = {}
    for line in lines:
        if line.text.strip():
            by_page.setdefault(line.page, []).append(line.source_line)
    for source_lines in by_page.values():
        edge.update(source_lines[:2])
        edge.update(source_lines[-2:])

    # 跨页重复的页眉里，如果它本身长得像章标题，保留**第一次**出现
    # —— 那通常是真的章开头，后面的才是页眉。
    first_occurrence: dict[str, int] = {}
    for index, line in enumerate(lines):
        stripped = line.text.strip()
        key = _norm(stripped)
        if key in recurring and key not in first_occurrence:
            first_occurrence[key] = index
    protected: set[int] = set()
    for key, index in first_occurrence.items():
        matched = heading_match(lines[index].text.strip())
        # **第一次出现的标题形状行一律保护**，不只是章标题。
        # 实测踩过两次：`§1.7 集合与映射*`（适配块里修的）和 `§2.3 一般线性方程组的
        # Gauss 消元法`（页脚在 3 页以上重复出现，`denoise` 把真标题一起删了，
        # 导致它下面两个小节被提升成节）。页脚是"后面的副本"，第一次出现通常就是真标题。
        if matched is not None and matched[0] in ("chapter", "num", "cn", "exercise"):
            protected.add(index)

    kept: list[Line] = []
    dropped_pages = dropped_furniture = dropped_toc = 0
    for index, line in enumerate(lines):
        if line.source_line in protected_lines:      # 代码块的行：原样留
            kept.append(line)
            continue
        stripped = line.text.strip()
        if not stripped:
            kept.append(line)
            continue
        if PAGE_COUNTER.match(stripped) or (BARE_NUMBER.match(stripped)
                                            and line.source_line in edge):
            dropped_pages += 1
            continue
        if _norm(stripped) in recurring and index not in protected:
            dropped_furniture += 1
            continue
        if DOT_LEADER.search(stripped) or TOC_ENTRY.match(stripped):
            dropped_toc += 1
            continue
        kept.append(line)

    warnings: list[str] = []
    if dropped_furniture:
        warnings.append(f"删掉 {dropped_furniture} 行跨页重复的页眉/页脚")
    if dropped_toc:
        warnings.append(f"删掉 {dropped_toc} 行目录（含点线）——目录不进教案")
    stats = {"dropped_page_numbers": dropped_pages,
             "dropped_page_furniture": dropped_furniture,
             "dropped_toc_lines": dropped_toc}
    return kept, warnings, stats


# ── 第二步：识别标题（必须在合折行之前） ─────────────────────────────────
def heading_match(text: str) -> tuple[str, str, str] | None:
    """返回 (scheme, 原始编号, 标题)。"""
    stripped = text.strip()
    if not stripped or len(stripped) > 80:
        return None
    for pattern in NOT_HEADING:
        if pattern.match(stripped):
            return None
    # ⚠️ 习题答案行**不是标题**（2026-09-27 全流程试跑实测抓出 B-13）：
    #    答案附录里的 `1. (1) $8 + \mathrm{i}3\pi$ (2) …` 会被普通编号规则当成"章/节"，
    #    实测这本书因此多出 CH15/CH16/CH17 三个"章"（26 个单元）。
    #    判据取"一行里**两个以上**小问编号"，保守：正文标题不会长这样。
    if ANSWER_LIKE.search(stripped):
        return None
    if EXERCISE_MARK.match(stripped):
        # 习题区标记：不给编号（它不是"第几节"），标题就是 `习题一` 本身。
        # 下游靠它划出区域边界，并且**不把它当教案单元**（见导出层）。
        return "exercise", "", re.sub(r"\s+", "", stripped)
    for scheme, pattern, _fixed in HEADING_PATTERNS:
        match = pattern.match(stripped)
        if not match:
            continue
        groups = match.groups()
        number = groups[0]
        title = (groups[-1] or "").strip(" \t·-—:：.．")
        if not title:
            continue
        if re.search(r"[。；，,;]$", title):
            continue
        # OCR 常见污染：标题位置其实是被并进来的页码（如 `1.72 8` → 标题变成 `8`）。
        # 这类行不当标题，避免出现"标题是个数字"的假结构。
        if re.fullmatch(r"\d{1,4}", title):
            continue
        # 去掉标点后没剩几个字的，多半是公式碎片（如 `(a))`、`=+`）
        if len(re.sub(r"[^0-9A-Za-z\u3400-\u9fff]", "", title)) < 2:
            continue
        return scheme, number, title
    return None


# 编号独占一行：`1.2` / `1.2.3` / `§1.2` / `第一章`
# 注意：纯整数（如 OCR 出来的页码 `7`）**不算**，否则会把页码粘到标题上变成 `71.1.1`
STANDALONE_NUMBER = re.compile(
    r"^\s*(?:§\s*)?(?:\d+(?:\.\d+)+|第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇])\s*[.．、]?\s*$")


def join_split_headings(lines: list[Line]) -> tuple[list[Line], list[str]]:
    """把"编号一行、标题一行"接回去。

    OCR 对横向间距大的标题会把编号和标题拆成两个文本框，例如：
        `1.2`
        `坐标系．`
    这在正文里和目录里都会出现，必须在认标题之前接好。
    """
    out: list[Line] = []
    joined = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        text = line.text.strip()
        if STANDALONE_NUMBER.match(text) and len(text) <= 24:
            # 找下一行非空
            nxt_index = index + 1
            while nxt_index < len(lines) and not lines[nxt_index].text.strip():
                nxt_index += 1
            if nxt_index < len(lines):
                nxt = lines[nxt_index]
                candidate = f"{text}{nxt.text.strip()}"
                if not STANDALONE_NUMBER.match(nxt.text.strip()) and len(candidate) <= 80:
                    line.text = candidate
                    out.append(line)
                    out.extend(lines[index + 1:nxt_index])
                    index = nxt_index + 1
                    joined += 1
                    continue
        out.append(line)
        index += 1
    warnings = [f"接回 {joined} 处被 OCR 拆开的标题（编号与标题分行）"] if joined else []
    return out, warnings


def detect_headings(lines: list[Line], *, not_heading_lines: "set[int] | None" = None) -> int:
    """逐行判标题。**习题区内的编号条目不算标题**（R19），**裸编号行也不算**。

    为什么：习题的编号是 `1.` `2.`（每章从 1 开始），跟节的编号规则一模一样；
    不划区域的话，一道题就变成一个"节"（实测全本 126 个假节）。
    区域从 `习题一` 这样的标记开始，到下一个章标题结束。

    返回**看见的裸编号行数**（给 stats 用；真正的去留在 `build_structure` 里做，
    因为要先让 `mark_exercise_items` 把混在里面的习题题干挑走）。
    """
    # 先看这本书是不是用 `§` 标节：是的话，**不带 `§` 的"节级"数字行一律当正文**。
    # 自动判断而不是写死：别的书可能不用 `§`，那时这条收紧规则不生效。
    #
    # ⚠️ **必须只收紧到 `§` 所在的那一层**（2026-09-28 核验抓出）：
    #    原实现是"不带 `§` 的 num 行一律丢"，而《数分讲义第二册》只有**节**带 `§`
    #    （`§8.1 向量与坐标系`），**小节不带**（`8.1.1 向量的定义与向量的加法和数乘`）
    #    —— 于是 80 个小节全被判成"正文行"丢掉：`structure.json` 只剩 39 个标题、
    #    导出层只切出 38 个单元（节级），112 个三级目录节点里 81 个**绑不上正文**。
    #    现在按**层级深度**收紧：只看深度 ≤ `§` 那一层的 num 行。
    # ⚠️ 取编号要**容忍 `§` 后面那个空格**（`§ 1.1`）：用正则直接抓数字路径，
    #    不要 `strip(" §")` 之后交给 numeric_path —— 实测那样会返回空，
    #    于是 `strict_section_depth` 恒为 0、规则整条失效（2026-09-28 自己的测试抓出来的）。
    section_depths = [len(m.group(1).split("."))
                      for line in lines
                      if (m := SECTION_MARK_DEPTH.match(line.text))]
    strict_section_depth = min(section_depths) if section_depths else 0
    marked = sum(1 for line in lines if SECTION_MARK.match(line.text))
    strict_sections = marked >= 3
    # ⚠️ **第二条同类收紧规则：裸编号行（`2.`）不是节**（2026-09-27 实测《复变函数》）：
    #    正文里的编号列表项（`2. 设原点在 $l$ 的外部…`）和证明步骤会被认成"节"，
    #    实测 6 处、全都是正文段落或题干（标题长 17～61 字），还把底下的真小节
    #    （指数函数/对数函数）挂到了错父级（`CH02-S06-SS01`）。
    #    判据与上面 `§` 那条同一思路、同样自动判断：**这本书的节用点号编号**
    #    （`2.1` 这种，≥3 个）时，光秃秃的 `N.` 就不是节 —— 真节会带完整点号路径。
    #    别的书如果节本来就用裸编号（`1. 集合`），点号编号数为 0 → 这条不生效。
    dotted = 0
    for line in lines:
        probe = heading_match(line.text)
        if probe and probe[0] == "num":
            path = numeric_path("num", probe[1])
            if path is not None and len(path) >= 2:
                dotted += 1
    strict_plain = dotted >= 3
    plain_dropped = 0
    in_exercise = False
    blocked = not_heading_lines or set()
    for line in lines:
        if line.source_line in blocked:
            # 来自 `code` 块的行：**不许当标题**（伪代码长得像编号标题），
            # 但它在正文里照旧保留（render 用的是同一份 lines）。
            line.is_heading = False
            continue
        matched = heading_match(line.text)
        scheme = matched[0] if matched else None
        if (strict_sections and scheme == "num" and not SECTION_MARK.match(line.text)):
            depth = len(numeric_path("num", matched[1]) or ())
            if depth <= strict_section_depth:
                # 与 `§` 同级或更浅、却不带 `§` ⇒ 列表/步骤/枚举/习题条目，不是节标题。
                # **更深的一层放行**：小节常常不带 `§`（《数分讲义》就是），
                # 一律丢掉会把整本书的小节抹平（2026-09-28 核验：81 个空节的根因）。
                line.is_heading = False
                continue
        if strict_plain and scheme == "num":
            path = numeric_path("num", matched[1])
            if path is not None and len(path) == 1:
                # ⚠️ 这里**只打标记、不当场丢**：裸编号行里混着两种东西 ——
                #    列表项/步骤（要丢）和**习题题干**（要丢但得交给习题块）。
                #    当场丢的话题干也跟着从 `headings` 里消失，习题层就少题
                #    （实测《复变函数》这样丢了 61 道题）。所以留给
                #    `mark_exercise_items` 判完题干，再在 `build_structure` 里一次性清掉。
                line.bare_number = True
                plain_dropped += 1
        if scheme == "exercise":
            in_exercise = True
        elif scheme == "chapter":
            in_exercise = False
        elif in_exercise and scheme == "num" and len(numeric_path("num", matched[1]) or ()) >= 2:
            # ⚠️ **节标题也结束习题区**（2026-09-27 实测《数分讲义第二册》）：
            #    那本书的 `习题 11.1` 在 MinerU 里是 **text 块**（不是 title），
            #    所以区域状态机没翻；而这里原来只在**章标题**处复位 ——
            #    于是 `§11.2` 之后 6 个真节全被当成"习题条目"拒掉（整章掉成 1 个单元）。
            #    判据与 `adapt` 的区域规则同一条：**带点号的编号 = 节标题**。
            in_exercise = False
        if matched is not None and in_exercise and scheme in ("num", "cn", "paren"):
            line.is_heading = False        # 习题条目 / 小问，是正文
            continue
        line.is_heading = matched is not None
    return plain_dropped


# ── 第三步：合折行 ──────────────────────────────────────────────────────
def unwrap(lines: list[Line], *, skip: "set[int] | None" = None) -> tuple[list[Line], list[str]]:
    """把被 PDF 折断的半句接回去；**标题行绝不参与合并**（否则标题会被正文粘坏）。

    `skip` = **不许合并的行号**（代码块的行）：代码一行就是一行，
    被"接半句"的规则粘起来就毁了（伪代码会变成一大段）。
    """
    protected_lines = skip or set()
    merged: list[Line] = []
    merges = 0
    for line in lines:
        text = line.text.rstrip()
        if line.source_line in protected_lines:
            merged.append(line)              # 代码行原样过，既不合并别人、也不被别人合并
            continue
        if not text.strip():
            merged.append(line)
            continue
        if not merged or not merged[-1].text.strip():
            merged.append(line)
            continue
        prev = merged[-1]
        if prev.source_line in protected_lines:   # 上一行是代码：不合并
            merged.append(line)
            continue
        prev_text = prev.text.rstrip()
        prev_ends_sentence = bool(prev_text) and prev_text[-1] in SENTENCE_END
        starts_new = (line.is_heading or prev.is_heading or bool(_LIST_ITEM.match(text)))
        if prev_ends_sentence or starts_new:
            merged.append(line)
            continue
        joiner = "" if _is_cjk(prev_text[-1:]) or _is_cjk(text[:1]) else " "
        prev.text = prev_text + joiner + text.strip()
        merges += 1
    warnings = [f"合并了 {merges} 处被 PDF 折断的换行"] if merges else []
    return merged, warnings


def _is_cjk(ch: str) -> bool:
    return bool(ch) and ("\u3400" <= ch <= "\u9fff" or ch in "，。！？；：、（）「」『』【】《》")


# ── 第四步：层级判定（数字前缀嵌套） ─────────────────────────────────────
def numeric_path(scheme: str, raw: str) -> tuple[int, ...] | None:
    """把原文编号解析成数字路径。解析不出来返回 None。"""
    raw = raw.strip()
    if scheme == "chapter" and not raw:
        # `绪论`/`引言`：无编号，但它是**第 0 章**（节号 `0.1` 的父级就是它）
        return (0,)
    if scheme in {"chapter", "cn", "paren"}:
        value = cn_to_int(raw)
        return (value,) if value is not None else None
    if scheme == "num":
        if raw and all(part.isdigit() for part in raw.split(".")):
            return tuple(int(part) for part in raw.split("."))
        return None
    return None


SCHEME_FALLBACK_LEVEL = {"chapter": 1, "cn": 2, "paren": 3, "exercise": 2}

# 习题区标记：`习题一` / `练习 2` / `复习题三` / `总习题`。
# 它**不遵守编号规则**（没有"第"字、没有分隔符），所以单独认。
# 不认它的后果（实测全本）：区域内那些编号条目（`1.` `2.` `(1)`）会被当成标题 ——
# 全书 278 个"节"里 **126 个（45%）是习题和证明步骤**，教案会按错的单元跑。
# 书里真正的节标题都带 `§`（实测这本：`§2.1 Gauss 消元法`、`§2.3.1 算法描述`）。
# 不带 `§` 的数字行在数学书里到处都是：算法步骤、情况枚举（`1. 当…时无解.`）、
# 问题列表、习题条目 —— 它们**长得像节标题**，但不是。
SECTION_MARK = re.compile(r"^\s*§\s*\d+(?:\.\d+)+\s*\S")
#: 从 `§` 行里**抓出编号本身**（`§ 1.1 标题` → `1.1`）—— 用于判断 `§` 管到哪一层。
#: 单独一条正则而不是 `strip(" §")`：后者去不掉 `§` 与数字之间的空格，
#: 实测会让深度算成 0、把收紧规则整条失效（2026-09-28）。
SECTION_MARK_DEPTH = re.compile(r"^\s*§\s*(\d+(?:\.\d+)+)")

EXERCISE_MARK = re.compile(
    r"^\s*(?:(?:第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇])\s*)?"
    r"(?:综合习题|习题|练习|复习题|总习题)\s*[\d.．\-—]*"
    r"[一二三四五六七八九十百零〇]*\s*$")


def align_numeric_paths(headings: list[Heading], warnings: list[str]) -> None:
    """把编号开头被 OCR 粘进去的前导数字剥掉。

    最常见的来源：`§1.1` 里的 `§` 被 OCR 认成 `8`，于是页眉 `§1.1 向量的线性运算`
    变成 `81.1向量的线性运算` —— 多出一个假小节。
    判据：编号第一段不在"本文档出现过的章号集合"里时，试着从左边逐位砍掉多余的
    前导数字，砍到能对上已知章号为止（优先匹配最长的那个）。
    """
    chapter_numbers = {h.numeric[0] for h in headings
                       if h.scheme == "chapter" and h.numeric}
    if not chapter_numbers:
        return
    for heading in headings:
        if heading.scheme != "num" or not heading.numeric or len(heading.numeric) < 2:
            continue
        head = heading.numeric[0]
        if head in chapter_numbers:
            continue
        text = str(head)
        candidates = [int(text[i:]) for i in range(1, len(text))
                      if text[i:].isdigit() and int(text[i:]) in chapter_numbers]
        if not candidates:
            continue
        fixed_head = max(candidates)          # 匹配最长的那个，避免歧义
        new_path = (fixed_head, *heading.numeric[1:])
        suffix = ".".join(str(x) for x in heading.numeric[1:])
        warnings.append(
            f"第 {heading.source_line} 行编号 {heading.raw_number} 的第一段 {head} "
            f"不属于本文档的章号 {sorted(chapter_numbers)}（多半是 `§` 被 OCR 认成别的字符），"
            f"已还原为 {fixed_head}.{suffix}"
        )
        heading.numeric = new_path
        heading.raw_number = f"{fixed_head}.{suffix}"


def drop_duplicate_headings(headings: list[Heading], warnings: list[str]) -> list[Heading]:
    """同一份文档里 `编号+标题` 完全相同 = 页眉重复，只保留第一次。"""
    seen: set[tuple[str, str]] = set()
    kept: list[Heading] = []
    dropped = 0
    for heading in headings:
        key = (heading.scheme, heading.raw_number, re.sub(r"\s+", "", heading.title))
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        kept.append(heading)
    if dropped:
        warnings.append(f"去重：{dropped} 个标题与前面完全相同（页眉/重复排版），只保留第一次")
    return kept


def strip_page_number_prefix(headings: list[Heading], warnings: list[str]) -> None:
    """把"编号前被粘上的页码"剥掉。

    OCR 有时把页眉的页码和紧跟的标题读成一行，例如第 8 页上出现 `81.5向量的混合积`
    —— 真实编号是 `1.5`。判据很直接：页号正好是编号的前缀。
    """
    for heading in headings:
        if heading.scheme != "num" or not heading.numeric:
            continue
        page = str(heading.page)
        raw = heading.raw_number
        if page and raw.startswith(page) and len(raw) > len(page):
            # `1.1` 出现在第 1 页：`1` 后面紧跟着 `.`，那是正常的分隔符，不是粘上的页码。
            # 真正的污染长这样：第 8 页上的 `81.5`（`8` 后面跟的是数字）。
            if raw[len(page)] in ".．":
                continue
            rest = raw[len(page):].lstrip(".．")
            if rest and all(part.isdigit() for part in rest.split(".")):
                heading.raw_number = rest
                heading.numeric = tuple(int(part) for part in rest.split("."))
                warnings.append(
                    f"第 {heading.source_line} 行（第 {heading.page} 页）编号 {raw} "
                    f"前粘着页号，已还原为 {rest}"
                )


def resolve_levels(headings: list[Heading], max_level: int, warnings: list[str]) -> None:
    """定层级：先按数字路径找父级，找不到再按方案默认层级。"""
    assigned: list[Heading] = []
    for index, heading in enumerate(headings):
        level: int | None = None
        path = heading.numeric
        if index == 0:
            level = 1
        elif path is not None and len(path) >= 2:
            parent = _find_prefix_parent(assigned, path[:-1])
            if parent is not None:
                level = parent.level + 1
        if level is None:
            fallback = SCHEME_FALLBACK_LEVEL.get(heading.scheme)
            if fallback is None:                       # num 且没找到父级
                level = 2
            else:
                level = fallback
        if assigned and level > assigned[-1].level + 1:
            warnings.append(
                f"第 {heading.source_line} 行「{heading.title}」图层级从 "
                f"{assigned[-1].level} 跳到 {level}，已夹到 {assigned[-1].level + 1}"
            )
            level = assigned[-1].level + 1
        if level > max_level:
            warnings.append(
                f"第 {heading.source_line} 行「{heading.title}」推算在第 {level} 层，"
                f"超过上限 {max_level}，已压到第 {max_level} 层"
            )
            level = max_level
        heading.level = level
        assigned.append(heading)


def _find_prefix_parent(assigned: list[Heading], prefix: tuple[int, ...]) -> Heading | None:
    for heading in reversed(assigned):
        if heading.numeric == prefix:
            return heading
    return None


# ── 第五步：按文档顺序重新编号 ──────────────────────────────────────────
def renumber(headings: list[Heading], warnings: list[str]) -> None:
    """按文档顺序编号。**`绪论`（无编号的第 0 章）不推进章号计数器**。

    为什么（2026-09-27 实测《数值计算方法》）：它的 `绪论` 排在第 1 章之前，
    节号是 `0.1` `0.2` `0.3` —— 如果按文档顺序给它 `第1章`，那么书里的
    **第 1 章会变成第 2 章**，后面全错一位。所以它固定拿 `0`，
    下一章照旧拿 `1`，全书的编号与书本身一致。
    """
    counters = [0] * (DEFAULT_MAX_LEVEL + 1)
    chapter_started = False                    # 已经出现过 1 级标题（包括第 0 章的 `绪论`）
    used_chapters: set[int] = set()            # 已经用掉的章号（防重号）
    for heading in headings:
        level = heading.level
        path = heading.numeric
        is_intro = level == 1 and path == (0,)
        if level == 1:
            # ⚠️ **章号跟书走**（2026-09-27 用户定的口径）：正文里写了 `第8章`，
            #    编号就用 8，而不是按出现顺序编成 1 ——《数分讲义第二册》是第一册的
            #    延续（书里是第 8–13 章），按顺序编号会让交付的单元编号与教材对不上。
            #    重号/缺号（OCR 出错）才退回顺序编号，并记一条 warning。
            explicit = path[0] if (path and len(path) == 1) else None
            if is_intro:
                counters[1] = 0               # 第 0 章：不推进计数器
            elif explicit is not None and explicit > 0 and explicit not in used_chapters:
                if counters[1] and explicit != counters[1] + 1:
                    warnings.append(
                        f"第 {heading.source_line} 行「{heading.title}」的章号从 "
                        f"{counters[1]} 跳到 {explicit} —— 按**书自己的编号**记（不重编）")
                counters[1] = explicit
            else:
                counters[1] += 1
                if explicit is not None and explicit > 0:
                    warnings.append(
                        f"第 {heading.source_line} 行「{heading.title}」原文章号 "
                        f"{explicit} 已出现过，改按顺序编号为 {counters[1]}")
            used_chapters.add(counters[1])
            chapter_started = True
        else:
            counters[level] += 1
        for deeper in range(level + 1, len(counters)):
            counters[deeper] = 0
        # ⚠️ `and not chapter_started`：绪论的节（`0.1`/`0.2`）也是 2 级标题，
        #    少了这个条件，这条兜底会把章号从 0 顶成 1 —— 绪论的节就变成 `1.1`/`1.2`。
        if counters[1] == 0 and level != 1 and not chapter_started:
            counters[1] = 1
            warnings.append("文档开头就是下级标题，已补建第 1 章")
        heading.canonical = ".".join(str(counters[i]) for i in range(1, level + 1))
        heading.canonical_level = level

        if path and level <= 2 and path[0] != counters[1]:
            warnings.append(
                f"第 {heading.source_line} 行「{heading.title}」原文编号是 "
                f"{heading.raw_number}，按文档顺序落在第 {counters[1]} 章 —— "
                f"已按文档顺序编号为 {heading.canonical}"
            )


# ── 第六步：渲染规范 markdown ───────────────────────────────────────────
def canonical_title(heading: Heading) -> str:
    if heading.canonical_level == 1:
        return f"第{heading.canonical}章 {heading.title}".strip()
    if heading.canonical_level == 2:
        return f"§{heading.canonical} {heading.title}".strip()
    return f"{heading.canonical} {heading.title}".strip()


# ── 区域（来自 MinerU 的 `headings.json`）：教材正文以外的东西不是教案单元 ─────────
#
# ⚠️ 为什么必须有这一层（2026-09-27 全流程试跑实测）：
#   一本 **9 章**的教材被解析成 **17 章** —— 多出来的 8 个是「前言段 1 + 目录条目 4 + 答案页 3」。
#   它们和正文标题**长得一样**（目录条目就是 `第2章 留数及其应用`，答案页就是 `第1章`），
#   所以必须在**区域**这一层排除，而不是靠逐行正则。
#   区域的判据来自 MinerU 自己标成 `title` 的 `目次`/`习题`/`习题参考答案` 等标记，
#   由 `<parse_dir>/headings.json` 带过来（见 `blocks/parse/mineru/adapt.py`）。
EXCLUDED_REGIONS = ("front", "toc", "exercise", "answer", "back")


@dataclass
class RegionMap:
    """"每一行属于哪个区域"。`body` 才是教材正文，其余都不进教案单元。"""
    by_line: dict[int, str] = field(default_factory=dict)   # document.md 行号 → 区域
    by_page: dict[int, str] = field(default_factory=dict)   # 页号 → 区域

    @staticmethod
    def _norm(region: str) -> str:
        return "body" if str(region or "").strip() in ("", "body") else str(region).strip()

    def of_line(self, source_line: int, page: int | None = None) -> str:
        """这一行属于哪个区域。

        ⚠️ 必须按**行**判，不能只按页（2026-09-27 实测抓出）：
        区域是在**标记那一行**变的，而标记经常落在页中间 ——
        《复变函数》的 `习题参考答案` 在 p227，`第1章` 的答案也在 p227，
        按页记就会把第 1 章的答案算成"习题"（实测 14 道题因此配不上答案）。
        所以：**取"这一行之前最近的那个标记"的区域**；前面没有标记才退回按页。
        """
        if self.by_line:
            import bisect
            keys = self._sorted_lines()
            i = bisect.bisect_right(keys, int(source_line)) - 1
            if i >= 0:
                return self._norm(self.by_line[keys[i]])
        if page is not None and page in self.by_page:
            return self._norm(self.by_page[page])
        return "body"                                   # 没有区域信息 → 当正文（保守，不误杀）

    def _sorted_lines(self) -> list[int]:
        cached = getattr(self, "_keys", None)
        if cached is None:
            cached = sorted(self.by_line)
            object.__setattr__(self, "_keys", cached)
        return cached

    def is_body(self, source_line: int, page: int | None = None) -> bool:
        return self.of_line(source_line, page) == "body"


def load_regions(source_path: str | Path) -> RegionMap | None:
    """在 document.md 旁边找 `headings.json`，读成 `RegionMap`。没有就返回 None。"""
    path = Path(source_path)
    for candidate in (path.parent / "headings.json", path.parent.parent / "headings.json"):
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        by_line = {}
        for row in payload.get("headings") or []:
            try:
                by_line[int(row["line"])] = str(row.get("region") or "body")
            except (KeyError, TypeError, ValueError):
                continue
        by_page = {}
        for page, region in (payload.get("page_region") or {}).items():
            try:
                by_page[int(page)] = str(region)
            except (TypeError, ValueError):
                continue
        return RegionMap(by_line=by_line, by_page=by_page)
    return None


def load_toc(source_path: str | Path) -> list[str]:
    """在 document.md 旁边找 `toc.json`，取出**书自己声明的章标题**（校验基准）。"""
    path = Path(source_path)
    for candidate in (path.parent / "toc.json", path.parent.parent / "toc.json"):
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return [str(x) for x in (payload.get("chapters") or [])]
    return []


def load_code_lines(source_path: str | Path) -> set[int]:
    """读 `headings.json` 里的 `code_lines`：来自 `code` 块的行号（这些行不许当标题）。

    ⚠️ 为什么（2026-09-27 实测《数值计算方法》）：伪代码行 `2.2 fx:=fx+tmp*y$_{i}$`、
    `2.1 选取非对角线按模最大元素 …` 跟编号标题长得一模一样，被当成三级标题、
    成了单元（3 个假单元）。`code` 块是 MinerU 明确标出来的，不用猜形状。
    **只影响"标题识别"，这些行在正文里一个字都不删**。
    """
    path = Path(source_path)
    for candidate in (path.parent / "headings.json", path.parent.parent / "headings.json"):
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows: set[int] = set()
        for span in payload.get("code_lines") or []:
            try:
                start, end = int(span[0]), int(span[1])
            except (TypeError, ValueError, IndexError):
                continue
            rows.update(range(start, end + 1))
        return rows
    return set()


def load_toc_entries(source_path: str | Path) -> list[dict]:
    """读 `toc.json` 的**全部条目**（含层级）—— 骨架要用它，不只是章标题。

    `toc.json` 的层级在 `adapt` 里已经归一化过（见 `normalize_outline_levels`）：
    书签多一层包装时整体上移，所以这里的 `level` 可以直接用。
    """
    path = Path(source_path)
    for candidate in (path.parent / "toc.json", path.parent.parent / "toc.json"):
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = []
        for row in payload.get("entries") or []:
            text = str(row.get("text") or "").strip()
            if text:
                entries.append({"text": text, "level": int(row.get("level") or 0)})
        return entries
    return []


def norm_title(text: str) -> str:
    """比较用的标题归一：**只保留"是哪个标题"这一件事**，把所有排版差异折平。

    ⚠️ 这里是**全程序唯一的标题比较口径**（体检表、目录对账都用它），
    因为实测（2026-09-27《数值计算方法》）同一节的三种写法会同时出现：

    | 来源 | 写法 |
    |---|---|
    | PDF 书签 | `52Gauss－Seidel迭代`（全角连接号） |
    | 印刷目录 OCR | `52Gauss-Seidel迭代`（半角连字符）+ 选学标记 `*` |
    | 正文标题 | `5.2 Gauss-Seidel 迭代`（带空格） |

    不折平就会**误报**：明明对上了，却报"目录有但没解析出来"。
    折平规则：空白/点线、全半角括号、各种连接号、选学标记 `*`、尾部页码。
    """
    out = re.sub(r"[\s.．·…⋯]+", "", str(text or ""))
    out = re.sub(r"[－—–‐‑~～]+", "-", out)          # 连接号的各写法 → 半角 -
    out = out.translate(str.maketrans("（）［］【】", "()[][]"))   # 全角括号 → 半角
    out = out.lstrip("*＊")                        # 选学/必学标记（`*1.3 Hermite插值`）
    out = re.sub(r"[（(]\d{1,4}[）)]$", "", out)
    # ⚠️ **ASCII 折成小写**（2026-09-29 外部基准抓出，D-15）：
    #    英文书里同一节在两条源里大小写不同 —— 实测《Introduction to Swiss Law》
    #    目录写 `1. Federal Assembly`、正文印 `1. FEDERAL ASSEMBLY`，
    #    不折大小写时 `title_similarity` 只有 **0.133**（几乎判成两个标题），
    #    召回率因此只有 0.44。汉字不受影响（没有大小写）。
    return out.lower()


#: 旧名保留（外部/体检表已经引用过 `_norm_title`）—— 实际实现只有上面那一个。
_norm_title = norm_title

#: 编号前缀：`第N章` / `第N篇` / `1.2.3`
_TITLE_NUMBER = re.compile(r"^(?:第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇]|\d+(?:\.\d+)*)")
#: LaTeX 命令 → 对应字符（只收数学书里常见的那些；认不出的一律去掉）
_LATEX_GREEK = {"alpha": "α", "beta": "β", "gamma": "γ", "Gamma": "Γ", "delta": "δ",
                "Delta": "Δ", "epsilon": "ε", "varepsilon": "ε", "theta": "θ",
                "lambda": "λ", "Lambda": "Λ", "mu": "μ", "pi": "π", "rho": "ρ",
                "sigma": "σ", "Sigma": "Σ", "tau": "τ", "phi": "φ", "varphi": "φ",
                "omega": "ω", "Omega": "Ω"}


def title_key(text: str) -> str:
    """骨架比较键：在 `norm_title` 基础上再**去掉编号、折平 LaTeX**。

    为什么还要再折一层（2026-09-27 四本实测）：
      * 目录里带编号、正文标题不带（或反过来）：`5.2 基级数` vs `幂级数`；
      * 同一个符号两种写法：书签 `含复参变量积分及Γ函数` vs 正文 `…及 $\\Gamma$ 函数`。
    不折平就会出现"目录里有、正文里找不到"的假象 —— 而骨架要靠它决定去留。
    """
    out = _TITLE_NUMBER.sub("", norm_title(text))
    out = re.sub(r"\\([A-Za-z]+)", lambda m: _LATEX_GREEK.get(m.group(1), ""), out)
    return out.replace("$", "").replace("{", "").replace("}", "").replace("\\", "")


def titles_agree(left: str, right: str) -> bool:
    """两个标题是不是同一个（包含关系，短的那边至少 3 个字）。

    目录条目常常比正文标题多一截（`第1章 复数和平面点集` vs `复数和平面点集`），
    所以允许包含。短于 3 个字的不允许包含匹配（`复数` 会命中一切）。
    """
    left, right = title_key(left), title_key(right)
    if not left or not right:
        return False
    if left == right:
        return True
    shorter = left if len(left) <= len(right) else right
    return len(shorter) >= 3 and (left in right or right in left)


def title_similarity(left: str, right: str) -> float:
    """标题相似度 0～1（目录自己会有 OCR 错字，只能靠它救）。"""
    import difflib
    left, right = title_key(left), title_key(right)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()


#: 相似度低于这个值就不认（实测：`幂级数` vs 目录 OCR 的 `基级数` = 0.67 要救回来；
#: 伪标题 `确定旋转角度 …` 与任何目录条目都 < 0.5，必须剔掉）。
SKELETON_RESCUE_SIMILARITY = 0.6

#: 骨架**至少**管到章、节这两层（四本实测都覆盖得住）。
SKELETON_LEVELS = (1, 2)

#: 更深的层级要"目录覆盖得住"才纳入骨架：目录条目数 ≥ 5、正文检测到 ≥ 3 个、
#: 且 目录数/正文数 ≥ 0.8 —— 说明这本书的目录**把小节也列全了**。
#: ⚠️ 为什么必须自动判（2026-09-27 四本实测）：
#:   * 《数值计算方法》书签 lv3 有 47 条、正文 42 个 → 覆盖得住；
#:     伪代码行 `2.1 选取非对角线按模最大元素 …` 就是靠这一层清掉的（3 个假单元）；
#:   * 《线性代数》目录 lv3 只有 1 条、正文 55 个 → 覆盖不住，硬管网级 3 会**误删 55 个真小节**。
SKELETON_COVER_MIN_TOC = 5
SKELETON_COVER_MIN_DETECTED = 3
SKELETON_COVER_RATIO = 0.8


def skeleton_levels(headings: list[Heading], toc_entries: list[dict]) -> tuple[int, ...]:
    """哪些层级"目录说了算"：章、节总是；更深的小节只有在**目录覆盖得住**时才算。"""
    levels = set(SKELETON_LEVELS)
    toc_by_level: dict[int, int] = {}
    for entry in toc_entries:
        level = int(entry.get("level") or 0)
        if level >= 1:
            toc_by_level[level] = toc_by_level.get(level, 0) + 1
    detected_by_level: dict[int, int] = {}
    for heading in headings:
        detected_by_level[heading.level] = detected_by_level.get(heading.level, 0) + 1
    for level, toc_count in toc_by_level.items():
        if level in SKELETON_LEVELS or toc_count < SKELETON_COVER_MIN_TOC:
            continue
        detected = detected_by_level.get(level, 0)
        if detected < SKELETON_COVER_MIN_DETECTED:
            continue
        if toc_count / detected >= SKELETON_COVER_RATIO:
            levels.add(level)
    return tuple(sorted(levels))

#: **只给报告用**：目录里的这些条目不是正文（前言/序/目录/附录/参考文献/习题集/答案），
#: "目录有、正文没配上"要把它们分开算 —— 否则真问题会被噪音淹掉
#: （实测《数值计算方法》18 条未配里 18 条都是这类）。
# ── 非正文判据：**下沉到 `lib/non_teaching.py`**（D-19，2026-09-29）──────────
# 原来这里与 `blocks/parse/mineru/adapt.py` 各写一套（中文/英文/习题集各差一点），
# 结果同一条 `titlepage` 在两边得到不同答案 → 体检"章数对账"报假警报。
# 现在**全程序唯一一份**；这里只做再导出，既有 `from ..structure.structure import
# is_non_teaching` 的调用方不受影响。
try:
    from lib.non_teaching import FRONT_TITLE as NON_TEACHING_TITLE
    from lib.non_teaching import FRONT_TITLE_EN as NON_TEACHING_TITLE_EN
    from lib.non_teaching import is_non_teaching
except ImportError:                      # 包内相对导入失败时的兜底（详见 lib/non_teaching.py）
    import sys as _sys
    from pathlib import Path as _Path
    _root = _Path(__file__).resolve().parents[3]
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    from lib.non_teaching import FRONT_TITLE as NON_TEACHING_TITLE   # noqa: F811
    from lib.non_teaching import FRONT_TITLE_EN as NON_TEACHING_TITLE_EN  # noqa: F811
    from lib.non_teaching import is_non_teaching                      # noqa: F811


def toc_covers(toc_entries: list[dict], title: str) -> bool:
    """目录里**逐字**有没有这一条 —— 只用于"保护"判定（题干/裸编号规则不许吃目录里的节）。

    ⚠️ 必须用**严格相等**（`title_key` 折平后全等），不能用 `titles_agree` 的包含匹配：
    目录里短条目太多（`调和函数`），包含匹配会把**任何含它的题干**保护下来 ——
    实测《复变函数》因此多出 3 个假单元（`设 $u$ 是调和函数…问` 成了 CH04-S03）。
    """
    wanted = title_key(title)
    return bool(wanted) and any(
        title_key(entry["text"]) == wanted for entry in toc_entries
        if int(entry.get("level") or 0) in SKELETON_LEVELS)


def apply_skeleton(headings: list[Heading], toc_entries: list[dict],
                   warnings: list[str]) -> dict:
    """**目录当骨架**：章、节这两层以书自己的目录为准 —— 目录里没有的就不是节。

    为什么（2026-09-27 用户定方向 + 四本实测）：正文排版推断总会被骗到，
    实测当过"节"的垃圾有：`习题二`、`习题11.1`、`fx:=fx+tmp*y$_{i}$`、
    `选取非对角线按模最大元素 …`、`确定旋转角度 …`、`节中给出的 Givens 矩阵 …`
    —— 而书自己的目录里一个都没有。

    两条保守设计（都是拿真数据试出来的，不是想当然）：

    * **只剔"目录里找不到"的**，不新增、不改层级 —— 目录里有而正文没检测到的，
      记进 `missing` 让人看见，**不猜位置**（猜错比不猜更坏）。
    * **允许一次"模糊救回"**：目录自身有 OCR 错字（`狭利克雷`←`狄利克雷`、
      `基级数`←`幂级数`），只按字面比会把**真节**剔掉。所以字面不过、但相似度
      ≥ `SKELETON_RESCUE_SIMILARITY` 的，保留并逐条记进 `rescued` 供核对。

    返回可直接进 `stats` 的统计（剔了谁、救了谁、目录里谁没配上）。
    """
    pool = [entry for entry in toc_entries if int(entry.get("level") or 0) >= 1]
    levels = skeleton_levels(headings, pool)
    dropped: list[dict] = []
    rescued: list[dict] = []
    if not pool:
        return {"目录条目": 0, "骨架层级": list(levels), "剔除": dropped,
                "救回": rescued, "目录未配": []}

    kept: list[Heading] = []
    for heading in headings:
        if heading.level not in levels:
            kept.append(heading)     # 目录覆盖不住的层级（多半是小节）：正文说了算
            continue
        if any(titles_agree(heading.title, entry["text"]) for entry in pool):
            heading.toc_confirmed = True          # 目录说了算（见 build_structure 的顺序说明）
            kept.append(heading)
            continue
        best = max(((title_similarity(heading.title, entry["text"]), entry["text"])
                    for entry in pool), default=(0.0, ""))
        if best[0] >= SKELETON_RESCUE_SIMILARITY:
            rescued.append({"source_line": heading.source_line, "page": heading.page,
                            "title": heading.title, "toc": best[1],
                            "similarity": round(best[0], 3)})
            kept.append(heading)
            continue
        dropped.append({"source_line": heading.source_line, "page": heading.page,
                        "level": heading.level, "title": heading.title})

    # 目录里有、正文（章/节这一层）里没检测到的条目
    # ⚠️ 两个分桶：**非正文**（前言/附录/文献/习题集）与**正文**（可能是真漏检）。
    #    分桶是为了让"正文未配"这一桶保持干净 —— 它是给人看的行动项。
    #    另外：**已经救回来的条目算配上**，不再重复计进未配（否则一处问题报两次）。
    rescued_toc = {item["toc"] for item in rescued}
    missing: list[dict] = []
    missing_matter: list[dict] = []
    for entry in pool:
        if int(entry.get("level") or 0) not in levels:
            continue
        if entry["text"] in rescued_toc:
            continue
        if any(titles_agree(heading.title, entry["text"]) for heading in kept):
            continue
        row = {"text": entry["text"], "level": int(entry.get("level") or 0)}
        (missing_matter if is_non_teaching(entry["text"]) else missing).append(row)

    headings[:] = kept
    if dropped:
        warnings.append(
            f"目录当骨架：剔掉 {len(dropped)} 个**目录里没有**的章/节标题"
            f"（正文排版把它们当成了标题，实测有 `习题二`、代码行、句子碎片这类）")
    if rescued:
        warnings.append(
            f"目录当骨架：{len(rescued)} 个标题字面与目录不符、但相似度够高已保留"
            f"（目录自己的 OCR 错字，例如 `幂级数` ← `基级数`）—— 见 stats.skeleton.rescued")
    if missing:
        warnings.append(
            f"目录当骨架：目录里有 {len(missing)} 条**正文**章/节在正文里没找到"
            f"—— 可能是正文没编号，或漏检，见 stats.skeleton.目录未配")
    return {"目录条目": len(pool), "骨架层级": list(levels), "剔除": dropped,
            "救回": rescued, "目录未配": missing, "目录未配-非正文": missing_matter}


def compare_with_toc(headings: list[Heading], toc_chapters: list[str]) -> dict:
    """把**解析出来的 1 级标题**与**书自己声明的目录**对账。

    ⚠️ 为什么要有这一步（2026-09-27）：目录是**书自己声明的结构**，
    而解析出来的树是排版推断的结果 —— 两者不一致时必须能**当场看见**，
    而不是等跑完 200 个单元才发现。《复变函数》那本真值 9 章、当时解析出 17 章，
    这个对账只要 0 token 就能把差额摆出来。
    """
    detected = [norm_title(h.title) for h in headings if h.canonical_level == 1]
    claimed = [norm_title(t) for t in toc_chapters]
    used = set()
    missing = []
    for want in claimed:
        hit = next((i for i, got in enumerate(detected)
                    if i not in used and (want in got or got in want)), None)
        if hit is None:
            missing.append(want)
        else:
            used.add(hit)
    extra = [got for i, got in enumerate(detected) if i not in used]
    return {"目录声明章数": len(claimed), "解析出章数": len(detected),
            "对上的": len(used), "目录有但没解析出来": missing, "解析出但目录没有": extra}


#: 题干的语气词 —— 这些开头的"标题"多半是题目而不是小节
# ⚠️ **不许放裸 `解`**（2026-09-27 实测踩到）：它会把 `解析函数的积分表示`、`解析开拓`
#    这类**真章标题**判成题干 —— 复变函数因此从 9 章掉到 6 章（三章名字都以「解析」开头）。
#    同理 `设` 要挡掉「设计/设施」。
EXERCISE_VERB = re.compile(
    r"^\s*(?:求|试|证明|求证|计算|讨论|验证|判断|作出|画出|已知|"
    r"求解|解下|解方|设(?!计|施|备|立))")
#: **只靠动词不够** —— `验证`、`计算机基础`、`讨论课` 这些真标题也会以动词开头。
#: 所以要再要一个"题干特征"：题目通常带 `下列/以下/：/？/$公式` 这类东西，主题名不会。
#: （2026-09-27 两次踩坑：`解` 吃掉了「解析开拓」，`验证` 吃掉了「五、验证」。）
EXERCISE_HINT = re.compile(
    r"(下列|以下|下述|如下|上面|下面|(?<![A-Za-z0-9])[:：]|[?？]|\$|\\[A-Za-z])")


def looks_like_exercise(title: str) -> bool:
    """标题像不像**题干**：既要动词开头，也要有题干特征。"""
    text = str(title or "")
    return bool(EXERCISE_VERB.match(text)) and bool(EXERCISE_HINT.search(text))


EXERCISE_ITEM_MAX_CHARS = 800


def mark_exercise_items(headings: list[Heading], lines: list[Line]) -> int:
    """把**正文里内联的习题题干**标出来：它们不是"节"，是题目。

    为什么需要（2026-09-27 实测抓出）：
    区域排除能治"有 `习题` 标记的整段"，但那本书的习题**被分页切断了** ——
    题目从第 26 页就开始了，而 `习题` 标记在第 27 页，于是前几道题落在了 `body` 区域，
    被当成小节：`1.6 设 x+iy=√(a+bi)，求 x,y`、`2.6 设原点在 $l$ 的内部` …（29 个）。

    判据（两条同时成立，保守）：
      ① 标题以题干语气词开头（求/试/证明/设/计算/…）；
      ② **紧跟它到下一个标题之间的正文极短**（`EXERCISE_ITEM_MAX_CHARS` 以内）——
         真小节后面是成段讲解；题目后面要么直接是下一道题，要么只有一两行小问。

    只标记、不删除：调用方把 `is_exercise_item` 的标题**从树里剔掉**，
    原文仍随所属章节保留；这里只防题号被误建为教学章节，不拆题或输出习题索引。
    """
    if not headings:
        return 0
    marked = 0
    order = sorted(headings, key=lambda h: h.source_line)

    def body_chars(heading: Heading, index: int) -> int:
        end = order[index + 1].source_line if index + 1 < len(order) else len(lines) + 1
        return sum(len(line.text.strip()) for line in lines
                   if heading.source_line < line.source_line < end)

    # ── 第一遍：**单条判据** —— 题干语气 + 紧跟正文短 ─────────────────────
    for index, heading in enumerate(order):
        if not looks_like_exercise(heading.title):
            continue
        if body_chars(heading, index) <= EXERCISE_ITEM_MAX_CHARS:
            heading.is_exercise_item = True
            marked += 1

    # ── 第二遍：**题号序列** —— 连续同层的裸条目（`4.` `5.` `6.` …）就是题目序列 ────
    #
    # 为什么还要这一遍（2026-09-27 实测，第一遍之后仍残留 6 个）：
    #   题干正文可能很长（实测 1791 / 7885 字），或者标题只是光秃秃一句 `证明`
    #   （没有 `下列/：/？/$` 这些题干特征）—— 两种都过不了第一遍。
    #   但它们**在编号上连成一串**：`求…` `证明` `设…` `求…` `证明` …，
    #   一眼就是"章末那一批题"，这比放宽长度阈值稳得多。
    #
    # 保守三条件：① 同层且编号是**裸数字**（`4.` 而不是 `4.1`）；
    #            ② 编号**严格递增 1**（中间断了就不算同一串）；
    #            ③ 一串**至少 3 条**（两条可能是"补充说明"，三条以上才像题目序列）。
    run: list[Heading] = []

    def flush() -> int:
        if len(run) < 3:
            run.clear()
            return 0
        added = 0
        for item in run:
            if not item.is_exercise_item:
                item.is_exercise_item = True
                added += 1
        run.clear()
        return added

    for heading in order:
        numeric = heading.numeric
        # ⚠️ **只认裸题号（scheme == "num"）**，章标题一律不算：
        #    `第5章 解析函数的级数展开` 的编号也是 `(5,)`，跟题目序列连上就会被一起吞掉
        #    （实测 2026-09-27：一版不做这个限制，真章标题被当成题干剔掉了）。
        plain_item = (heading.scheme == "num" and isinstance(numeric, tuple)
                      and len(numeric) == 1)
        if not plain_item:
            marked += flush()
            continue
        if run and run[-1].numeric and numeric[0] == run[-1].numeric[0] + 1:
            run.append(heading)
        else:
            marked += flush()
            run = [heading]
    marked += flush()

    return marked


def render(headings: list[Heading], lines: list[Line], *, anchor_level: int | None = None) -> str:
    """输出规范 markdown。

    anchor_level=None → 忠实输出所有层级（`#`..`####`）
    anchor_level=3    → 只把前三层写成标题，更深的降级成粗体正文行，
                        这样产出的文件可以直接喂给下游 normalize-source
    """
    head_at = {h.source_line: h for h in headings}
    out: list[str] = []
    for line in lines:
        heading = head_at.get(line.source_line)
        if heading is None:
            out.append(line.text)
            continue
        title = canonical_title(heading)
        if out and out[-1].strip():
            out.append("")
        if anchor_level is None or heading.canonical_level <= anchor_level:
            out.append(f"{'#' * heading.canonical_level} {title}")
        else:
            out.append(f"**{title}**")
        out.append("")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return text.strip() + "\n"


# ── 编排 ────────────────────────────────────────────────────────────────
def build_structure(text: str, *, max_level: int = DEFAULT_MAX_LEVEL,
                    regions: "RegionMap | None" = None,
                    source_path: "Path | None" = None,
                    authority: "list[dict] | None" = None) -> StructureResult:
    """从 `document.md` 建结构。

    `authority`（**方案 A**，2026-09-28）非空时，**标题判定与层级全部来自它** ——
    它是"书的目录（层级权威）× MinerU 的 title 块（标题候选）"对齐后的产物
    （见 `blocks/parse/toc/align.py`），每项形如
    `{"line", "level", "number", "title", "toc_id"}`。

    为什么要有这条路（实测两个反例）：老做法从正文文字里用编号正则推层级，
    《电路分析基础》的节号是 `§ 1-1`（短横线）→ 331 页只切出 **7 个单元**；
    《电磁学》的第 11 章写作 `* 第 11 章 …`（前置星号）→ **整章丢失**。
    两条都指向同一个病：**层级不该从正文的文字形状里推**。
    """

    lines, page_count = split_pages(text)
    # 只按目录隔离独立习题/答案章；内嵌习题与例题随原章节保留。
    # 不读取旧 exercises.json / document.selected.md 删除投影。
    excluded_lines = 0
    excluded_pages: list[int] = []
    # 方案 A：**目录声明的标题行，不许被区域排除吃掉**（D-13，2026-09-28 实测抓出）。
    # 《电磁学》的绪论章标题写作 `绪论 电磁学的建立和应用`（副标题同行），
    # 区域状态机没认出"绪论"→ 整章被判成章前材料排除（目录说 12 章、解析出 11 章）。
    # 书自己的目录说"这是章"，那就是章 —— 区域启发式不该有否决权（R-d 同一条道理）。
    authority_lines_early: set[int] = set()
    if authority is not None:
        authority_lines_early = {int(d.get("line") or 0) for d in authority}
    authoritative_exclusions = {}
    if source_path and (Path(source_path).parent / 'toc.excluded.json').is_file():
        from blocks.parse.toc.regions import build_regions, require_teaching_boundaries, span_at, teaching_at
        authoritative_exclusions = build_regions(Path(source_path).parent)
        require_teaching_boundaries(authoritative_exclusions)
        conflicts = [n for n in authority_lines_early if span_at(authoritative_exclusions, n)]
        if conflicts:
            raise ValueError(f'教学标题与目录排除范围冲突：{conflicts}')
        lines = [line for line in lines if not span_at(authoritative_exclusions, line.source_line)]
    if regions is not None:
        kept_lines = [line for line in lines
                      if regions.of_line(line.source_line, line.page) not in {'front', 'toc', 'back'}
                      or (authoritative_exclusions and teaching_at(authoritative_exclusions, line.source_line))
                      or line.source_line in authority_lines_early]
        excluded_lines = len(lines) - len(kept_lines)
        if excluded_lines:
            kept_source_lines = {line.source_line for line in kept_lines}
            excluded_pages = sorted({line.page for line in lines
                                     if line.source_line not in kept_source_lines})
        lines = kept_lines
    # 目录区必须在 denoise 之前丢：`目录` 这两个字会跨好几页重复出现，
    # denoise 的"跨页重复短行"规则会先把它当成页眉/页脚删掉，标记就没了。
    lines, dropped_region = _drop_toc_region(lines)
    code_lines = load_code_lines(source_path) if source_path else set()
    # 方案 A：authority（书的目录 × MinerU 标题候选的对齐结果）指到的行，
    # **不许被 denoise 当页眉/页脚删掉**。实测《电路分析基础》（2026-09-28）：
    # 章标题同时是**书眉**（每页重复）→ denoise 按"跨页重复短行"把它删了，
    # 于是对齐明明配上的 7 个章在结构层里"找不到行"（章节全部消失）。
    authority_lines = authority_lines_early
    from aiforstudy.core.source_boundaries import MATERIAL_HEADING
    material_heading_lines = {line.source_line for line in lines if MATERIAL_HEADING.match(line.text)}
    # 章末重复“习题”是作者标区的来源边界，不能当页眉删除或与前文合并。
    lines, warn_denoise, stats = denoise(lines, skip=code_lines | authority_lines | material_heading_lines)
    if dropped_region:
        warn_denoise = [*warn_denoise, f"丢掉整段目录区 {dropped_region} 行"]
        stats = {**stats, "dropped_toc_region": dropped_region}
    lines, warn_join = join_split_headings(lines)   # 编号与标题被 OCR 拆成两行
    present_code = len(code_lines & {line.source_line for line in lines})
    if present_code:
        warn_join = [*warn_join,
                     f"有 {present_code} 行来自**代码块**，没有参与标题识别"
                     f"（伪代码的 `2.2 fx:=…` 长得像编号标题，实测当过假单元）"]
    if authority is None:
        bare_number_seen = detect_headings(lines, not_heading_lines=code_lines)
    else:
        # 方案 A：标题判定**不由文字形状决定** —— 把 authority 指到的行标成标题，
        # 其余一律不是（连"裸编号""习题题干"这些启发式都不用跑：目录说了算）。
        bare_number_seen = 0
        for line in lines:
            line.is_heading = line.source_line in authority_lines
    lines, warn_unwrap = unwrap(lines, skip=code_lines | material_heading_lines)     # 再合折行

    if authority is not None:
        return _build_from_authority(lines, page_count, authority, max_level,
                                     stats={**stats,
                                            "excluded_region_lines": excluded_lines,
                                            "excluded_region_pages": excluded_pages,
                                            "code_lines_skipped": present_code},
                                     warnings=[*warn_denoise, *warn_join, *warn_unwrap])

    headings: list[Heading] = []
    for line in lines:
        # **尊重 detect_headings 的判定**，不要在这里重判一遍：
        # 重判会把"习题区里的编号条目不算标题"这条规则绕过去 ——
        # 实测全本 278 个"节"里 126 个是习题和证明步骤，就是这么来的。
        if not line.is_heading:
            continue
        matched = heading_match(line.text)
        if matched is None:                # 合折行之后标题可能变了，再确认一次
            continue
        scheme, number, title = matched
        headings.append(Heading(scheme=scheme, raw_number=number, title=title,
                                source_line=line.source_line, page=line.page,
                                numeric=numeric_path(scheme, number),
                                bare_number=line.bare_number))

    warnings = [*warn_denoise, *warn_unwrap]
    if not headings:
        warnings.append("一个标题都没识别出来 —— 这份文档的编号体系可能完全不在已知模式里")
    strip_page_number_prefix(headings, warnings)
    align_numeric_paths(headings, warnings)
    headings = drop_duplicate_headings(headings, warnings)
    # 内联习题题干：**先标出来**（它们要交给习题块），去留随后定
    marked_items = mark_exercise_items(headings, lines)
    exercise_items = [
        {"source_line": h.source_line, "page": h.page, "raw_number": h.raw_number,
         "title": h.title, "page_region": "body"}
        for h in headings if h.is_exercise_item]
    resolve_levels(headings, max_level, warnings)
    toc_entries = load_toc_entries(source_path) if source_path else []
    # ── 目录最终说了算（2026-09-27）：**目录里真有的章/节，题干/裸编号规则不许吃** ────
    #    为什么单独做这一步：题干规则按"文字形状"判，目录按"书自己声明"判；
    #    形状规则万一把目录里真有的节判成题干，那一节就**静悄悄消失**（最难发现的那类错）。
    #    ⚠️ 这里**只用字面判据**（`toc_covers`），**不做模糊救回** ——
    #    实测：一长串题干文本跟某个目录条目也能"像"到 0.6 以上，
    #    放进来会凭空多出 13 个假节（《数值计算方法》实测）。
    protected = {id(h) for h in headings
                 if (h.is_exercise_item or h.bare_number)
                 and toc_covers(toc_entries, h.title)}
    # 裸编号标题：题干已经挑走了，剩下的**都不是节**（列表项/步骤/枚举），在这里清掉。
    plain_number_dropped = sum(1 for h in headings
                               if h.bare_number and not h.is_exercise_item
                               and id(h) not in protected)
    if plain_number_dropped:
        warnings.append(
            f"正文里有 {plain_number_dropped} 个**裸编号**（`2.` 这种）标题 —— "
            f"这本书的节用点号编号，光秃秃的编号是列表项/步骤，已从章节树里剔除")
    headings = [h for h in headings
                if id(h) in protected or (not h.is_exercise_item and not h.bare_number)]
    if marked_items:
        warnings.append(
            f"正文里有 {marked_items} 个标题其实是**习题题干**"
            f"（后面紧跟的正文极短）→ 已从章节树里剔除，原文保留在所属章节"
            + (f"；其中 {len(protected)} 个**书自己的目录里有**，按目录保留"
               if protected else ""))
    # ── 目录当骨架：章/节这一层以**书自己的目录**为准（详见 `apply_skeleton`）──────
    #    顺序：先定层级、先剔题干/裸编号，最后套骨架 —— 骨架只该看到"候选的节"。
    skeleton = apply_skeleton(headings, toc_entries, warnings) if toc_entries else {}
    renumber(headings, warnings)

    toc_chapters = load_toc(source_path) if source_path else []
    toc_check = compare_with_toc(headings, toc_chapters) if toc_chapters else {}
    if toc_check and (toc_check["目录有但没解析出来"] or toc_check["解析出但目录没有"]):
        warnings.append(
            "**解析出来的章和书自己的目录对不上**：目录 "
            f"{toc_check['目录声明章数']} 章 / 解析出 {toc_check['解析出章数']} 章；"
            f"目录有但没解析出来 {toc_check['目录有但没解析出来']}；"
            f"解析出但目录没有 {toc_check['解析出但目录没有']}")
    stats = {
        **stats,
        "pages": page_count,
        "toc_check": toc_check,
        # 目录当骨架的账（0 token 可核）：剔了谁、救了谁、目录里谁没配上
        "skeleton": skeleton,
        # 区域排除的账（0 token 可核）：滤了多少行、涉及哪些页
        "excluded_region_lines": excluded_lines,
        # 代码块的行：只挡"当标题"，正文里没删（0 token 可核）
        "code_lines_skipped": present_code,
        "excluded_region_pages": excluded_pages,
        "headings_total": len(headings),
        "exercise_items_marked": marked_items,
        "plain_number_dropped": plain_number_dropped,
        "bare_number_seen": bare_number_seen,
        "exercise_items": exercise_items,
        **{f"level_{i}": sum(1 for h in headings if h.canonical_level == i)
           for i in range(1, max_level + 1)},
        "body_lines_after_unwrap": sum(1 for l in lines if l.text.strip()),
    }
    return StructureResult(
        structured_markdown=render(headings, lines),
        headings=headings, warnings=warnings, stats=stats, max_level=max_level,
        render_lines=lines,
    )


#: authority 的行号找不到时，允许在这么多行以内找"合并后的行"（见 `_locate`）。
AUTHORITY_JOIN_WINDOW = 3


#: authority 的章号：`第11章` / `*第11章` / `第 8 章`（书里怎么写都认）
_AUTH_CHAPTER = re.compile(r"^\*{0,2}\s*第\s*([0-9一二三四五六七八九十百零〇]+)\s*[章篇]")
#: authority 标题里"没被拆出来的裸章号"：`1 集总参数电路中电压、电流的约束关系`
_AUTH_LEAD_NUMBER = re.compile(r"^(\d{1,2})\s+(\S.*)$")


def _authority_anchor(number: str) -> str:
    """authority 的编号 → **锚点写法**（`第一章`→`1`、`1-1`→`1.1`、`11.3.2` 原样）。

    为什么必须折：`render()` 对一级标题会拼 `第{canonical}章` ——
    直接把书里的 `第一章` 当 canonical 会渲染出 `第第一章章`（实测），
    下游 normalize 就认不出这是章了（电路分析一度 `chapters: 1`、49 个节全挂错父级）。
    """
    text = str(number or "").strip()
    match = _AUTH_CHAPTER.match(text)
    if match:
        value = cn_to_int(match.group(1))
        if value is not None:
            return str(value)
    text = text.lstrip("*＊§ \t").replace("．", ".")
    return re.sub(r"(\d)\s*[-–—－]\s*(\d)", r"\1.\2", text)


def _authority_number_title(decision: dict, *, fallback: str = "") -> "tuple[str, str]":
    """authority 的一条 → `(编号, 标题)`，顺手把"没拆出来的裸章号"从标题里剥掉。"""
    number = str(decision.get("number") or "").strip()
    title = str(decision.get("title") or fallback).strip()
    lead = _AUTH_LEAD_NUMBER.match(title)
    if lead:
        if not number and int(decision.get("level") or 0) == 1:
            # 书签把章写成 `1 集总参数电路…`：编号没被拆出来 → 这里拆
            number, title = lead.group(1), lead.group(2).strip()
        elif _authority_anchor(number) == lead.group(1):
            # 编号已经从另一源拿到了（`第一章` → 1），标题里还残留同一个裸号
            # → 只剥标题里那截（实测渲染出 `第1章 1 集总参数电路…`）
            title = lead.group(2).strip()
    return number, title


def _build_from_authority(lines: list["Line"], page_count: int,
                          authority: list[dict], max_level: int, *,
                          stats: dict, warnings: list[str]) -> "StructureResult":
    """方案 A 的收口：**标题与层级全部来自"目录权威 × MinerU 标题候选"的对齐结果**。

    与老路的四处不同（每一处都是为了治实测抓到的病）：

    | 老路 | 这条路 |
    |---|---|
    | `detect_headings()` 从文字形状认标题 | 只认 `authority` 指到的行（`:func:`blocks.parse.toc.align.align` 的产物） |
    | `resolve_levels()` 按编号形状推层级 | **层级直接取自书的目录**（目录给几级就是几级） |
    | `apply_skeleton()` 拿目录过滤正文标题 | **不用过滤**：authority 本身就是骨架 |
    | `renumber()` 重新编号 | **不重编**：编号就是书里目录的编号（`canonical` 直接用它） |

    不再对目录已确认的条目另作习题识别；习题区由作者同次标记来源范围。
    """
    by_line = {line.source_line: line for line in lines}
    ordered = sorted(by_line)
    near_hits = 0

    def _locate(decision: dict) -> "tuple[Line | None, str]":
        """把 authority 的行号落到结构层的行上。

        ⚠️ **必须有邻近兜底**（2026-09-28 实测《电路分析基础》抓出）：
        `join_split_headings()` 会把"编号一行 + 标题一行"合并成一行，
        **行号取前一行** —— 于是 MinerU 指到标题那一行（132）在合并后就找不到了，
        7 个章标题全部"行对不上"、整层章节消失（标题 58 → 51）。
        所以精确找不到时，在 ±`AUTHORITY_JOIN_WINDOW` 行内找最近的合并结果。
        """
        line_no = int(decision.get("line") or 0)
        if line_no in by_line:
            return by_line[line_no], "exact"
        near = [x for x in ordered if abs(x - line_no) <= AUTHORITY_JOIN_WINDOW]
        if near:
            return by_line[min(near, key=lambda x: abs(x - line_no))], "near"
        return None, ""

    headings: list["Heading"] = []
    unmatched: list[dict] = []
    # ── 没有编号的标题：**按目录位置合成内部锚点**（D-23，2026-09-29 实测抓出）──
    # 为什么必须合成：下游 `stem_runner` 的锚点是**从标题文字里解析编号**的
    # （只认 `第N章` / `N.M` / `N.M.K`）。英文书（如 *Boats to Burn*，标题就是
    # `Chapter 1: Contested Rights of Access`、没有中文式编号）因此**一个锚点都解析不出**
    # —— normalize 直接报 `Markdown part has no body anchor`，整本跑不下去。
    # 合成的锚点只是**内部骨架编号**（不声称书里印了它），并且**显式标注**：
    # `number_source="synthesized"` 会进 structure.json，谁看都知道这不是书上的编号。
    counters: dict[int, int] = {}
    anchors: dict[int, str] = {}
    for decision in authority:
        line, how = _locate(decision)
        if line is not None and how == "near":
            near_hits += 1
            line.is_heading = True          # 合并后的行：在这里补标，unwrap 才不会并走它
        if line is None:
            # 行对不上（document.md 换过？）→ 如实记账，不静默丢
            unmatched.append({"line": decision.get("line"),
                              "title": decision.get("title"),
                              "why": "这一行不在结构层的行视图里（多半被区域排除滤掉了）"})
            continue
        number, title = _authority_number_title(decision, fallback=line.text)
        level = max(1, min(int(decision.get("level") or 1), max_level))
        canonical = _authority_anchor(number)
        number_source = "toc" if canonical else "synthesized"
        if not canonical:
            # 按层级栈计数：`1`、`1.1`、`1.1.1`…（父锚点 + 本级序号）
            for deeper in [key for key in counters if key > level]:
                counters.pop(deeper, None)
                anchors.pop(deeper, None)
            counters[level] = counters.get(level, 0) + 1
            parent = anchors.get(level - 1, "")
            canonical = f"{parent}.{counters[level]}" if parent else str(counters[level])
        anchors[level] = canonical
        headings.append(Heading(
            scheme="toc", raw_number=number, title=title,
            source_line=line.source_line, page=line.page,
            numeric=numeric_path("num", canonical),
            toc_confirmed=True, level=level, canonical_level=level,
            canonical=canonical))

    headings.sort(key=lambda h: h.source_line)
    if unmatched:
        warnings.append(
            f"方案 A：{len(unmatched)} 条目录节点在结构层的行视图里找不到行"
            f"（多半落在被排除的区域里）—— 见 stats.authority.unmatched")
    full = {
        **stats, "pages": page_count,
        "heading_source": "authority",
        "headings_total": len(headings),
        "authority": {"matched": len(headings), "unmatched": unmatched,
                      "synthesized_anchors": sum(
                          1 for h in headings if getattr(h, "canonical", "")
                          and not getattr(h, "raw_number", "")),
                      "near_window_hits": near_hits},
        "toc_check": {}, "skeleton": {}, "plain_number_dropped": 0,
        "bare_number_seen": 0,
        **{f"level_{i}": sum(1 for h in headings if h.canonical_level == i)
           for i in range(1, max_level + 1)},
        "body_lines_after_unwrap": sum(1 for line in lines if line.text.strip()),
    }
    return StructureResult(structured_markdown=render(headings, lines),
                           headings=headings, warnings=warnings, stats=full,
                           max_level=max_level, render_lines=lines)


def build_from_file(path: str | Path, *, max_level: int = DEFAULT_MAX_LEVEL,
                    use_regions: bool = True,
                    authority: "list[dict] | None" = None) -> StructureResult:
    """从 document.md 建结构。`headings.json` 在旁边就自动用它的区域信息。

    `authority` 非空时走**方案 A**：标题与层级全部取自它（见 `build_structure`）。
    """
    path = Path(path)
    raw = path.read_bytes()
    regions = load_regions(path) if use_regions else None
    result = build_structure(raw.decode("utf-8", errors="replace"), max_level=max_level,
                             regions=regions, source_path=path, authority=authority)
    result.stats["source_sha256"] = hashlib.sha256(raw).hexdigest()
    result.stats["regions_used"] = bool(regions)
    return result


def render_with_anchors(result: StructureResult, lines: list[Line], anchor_level: int) -> str:
    return render(result.headings, lines, anchor_level=anchor_level)


def write_outputs(result: StructureResult, out_dir: str | Path) -> dict[str, str]:
    """落盘：structure.json + structured.md（忠实版，含全部层级）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sha = str(result.stats.get("source_sha256", ""))
    structure_path = out_dir / "structure.json"
    structure_path.write_text(
        json.dumps(result.to_json(sha), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    markdown_path = out_dir / "structured.md"
    markdown_path.write_text(result.structured_markdown, encoding="utf-8")
    return {"structure": str(structure_path), "structured_markdown": str(markdown_path)}


__all__ = [
    "build_structure", "build_from_file", "write_outputs", "render_with_anchors",
    "StructureResult", "Heading", "Line",
    "split_pages", "denoise", "detect_headings", "unwrap", "heading_match",
    "numeric_path", "resolve_levels", "renumber", "render", "canonical_title",
    "cn_to_int", "DEFAULT_MAX_LEVEL", "ANCHOR_MAX_LEVEL",
    "_drop_toc_region", "join_split_headings", "strip_page_number_prefix",
    "align_numeric_paths", "drop_duplicate_headings",
]
