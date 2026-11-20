"""三源抽取：**各自抽、互不干扰**（设计稿 §2.2 第 ① 步，0 token）。

| 源 | 从哪来 | 特点 |
|---|---|---|
| `pdf-bookmarks` | PDF 内嵌书签 | 最权威，但可能**多一层包装**（根节点 `目录`）、可能整本没有 |
| `printed-toc` | 目录页的文字行 | 有**印刷页码**（正文落点靠它），但会被 OCR 弄出错字/漏条 |
| `body-heading-shapes` | `document.md` 里**长得像标题**的行 | 只有标题形状，**没有权力增删章节点**（R-d），只用来交叉核对 |

## 为什么三源要分开抽

实施方案 §7.1 的教训：**同一份信息写两处 = 万恶之源**。如果这里"顺手"用书签去补
印刷目录、或用正文推断去纠正书签，三源就不再独立，"对账"立刻变成自证。

所以本模块的纪律是：

* 每个 `extract_*()` **只读自己的输入**，返回自己的候选；
* 抽不出来的行**不丢**，进 `unresolved`（带原始行文与页码）；
* 页码统一成 `{"printed": 书上印的, "pdf": PDF 页}` —— 两个都存（设计稿 §2.5）。
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..mineru import adapt                      # noqa: F401  复用书签读取与文本修复
from ..structure import structure                # noqa: F401  复用**唯一**的标题比较口径
from .contract import (SOURCE_BODY, SOURCE_BOOKMARKS, SOURCE_PRINTED,
                       sha256_file, sha256_text)


def mathematical_title_key(title: str) -> str:
    """Fold listed Unicode/TeX presentation equivalences, never algebra.

    Kept separate from identity(), whose punctuation stripping loses math.
    Unknown commands, style distinctions, operators and grouping survive.
    The original source title is never replaced with this comparison key.
    """
    from ..structure.structure import _LATEX_GREEK
    text = unicodedata.normalize('NFC', str(title or ''))
    text = ''.join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in text)
    double_struck = dict(zip('CHNPQRZ', 'ℂℍℕℙℚℝℤ'))
    text = re.sub(r'\\mathbb\{([CHNPQRZ])\}', lambda m: double_struck[m[1]], text)
    # Roman/text/italic wrappers are presentation; bold/calligraphic symbols
    # may denote different mathematical objects and remain explicit.
    text = re.sub(r'\\(?:mathrm|mathit|text)\{([^{}]*)\}', r'\1', text)
    symbols = {**_LATEX_GREEK, 'times': '×', 'div': '÷', 'cdot': '·',
               'pm': '±', 'leq': '≤', 'le': '≤', 'geq': '≥', 'ge': '≥',
               'neq': '≠', 'ne': '≠', 'infty': '∞', 'to': '→',
               'left': '', 'right': ''}
    # Delimit unknown control words so whitespace folding cannot equate
    # ``\unknown x`` with the different control word ``\unknownx``.
    text = re.sub(r'\\([A-Za-z]+)', lambda m: symbols.get(m[1], m[0] + '\0'), text)
    text = re.sub(r'\\[,!; ]', '', text).replace('$', '')
    for alphabet, operator in [
        ('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱᵃᵇᶜᵈᵉᶠᵍʰʲᵏˡᵐᵒᵖʳˢᵗᵘᵛʷˣʸᶻ', '^'),
        ('₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ', '_'),
    ]:
        text = re.sub('[' + alphabet + ']+',
                      lambda m: operator + '{' + unicodedata.normalize('NFKC', m[0]) + '}', text)
    # A bare TeX script consumes ONE token: x_1 2 is not x_{12}.
    text = re.sub(r'([\^_])\s*(\\[A-Za-z]+|[^\s{}\\])', r'\1{\2}', text)
    return re.sub(r'\s+', '', text)

# ── 编号体系（数学书实测会混用；每种一条规则，不合并） ───────────────────
_CN = r"[一二三四五六七八九十百零〇]"
#: ⚠️ **前置选学标记 `*` 也要认**（D-1，2026-09-28 实测《电磁学》）：
#: 书签里第 11 章写作 `<sup>*</sup>第11章相对论电磁学`（剥掉标记后是 `*第11章…`）
#: —— 不认 `*` 前缀，**整章就没有编号**（number 空），
#: 于是它下面的节在正文里被"还原"到别的章号上（实测 `11.3.2` 被误改成 `1.3.2`）。
_CHAPTER_HEAD = re.compile(
    rf"^(\*{{0,2}}\s*第\s*(?:{_CN}+|\d+)\s*[章篇])[\s、.．·:：]*\s*(.*)$")
#: 印刷目录里章标题会带空格：`第 8 章 空间解析几何` / `10 章 重积分`。
_CHAPTER_LOOSE = re.compile(
    rf"^(\*{{0,2}}\s*第?\s*(?:{_CN}+|\d+)\s*章)[\s、.．·:：]*\s*(.*)$")
#: `1.2` / `1.2.3` / `*1.3`（选学）/ `§1.2`
#: ⚠️ **必须带点**（`\d+(?:\.\d+)+`，至少一个点）：裸整数 `3` 也是合法正则匹配，
#: 实测《数值计算方法》正文里 `3 位和 5 位有效位数.` 因此被当成"第 3 章"的候选
#: （假章，实测抓出）。裸编号在目录里是页码、在正文里是正文，都不是标题。
#: ⚠️ 分隔符**必须允许短横线**（D-4/D-6，2026-09-28 实测《电路分析基础》抓出）：
#: 那本书的节号是 `§ 1-1`（短横线），而书签与页码用的是 `1.1`（点号）。
#: 原正则只认点号 → `§ 1-1 电路及集总电路模型` **拆不出编号**（编号留空、
#: 标题里还粘着 `§ 1-1`）→ 同一节被两源登记成**两个节点**（实测 54 条假分歧、
#: 骨架凭空多出一整套重复的节）。
_DECIMAL_HEAD = re.compile(
    r"^(\*{0,2}\s*§?\s*(\d+(?:[.\-–—－]\d+)+))[\s、.．·:：]*\s*(.*)$")
#: `一、` / `二、`（中文序号小节）
_CN_ITEM_HEAD = re.compile(rf"^({_CN}+)\s*[、.．]\s*(.+)$")
#: 习题集/答案集：`习题 8.1` / `第 8 章综合习题` / `习题参考答案`
_EXERCISE_HEAD = re.compile(
    r"^(第\s*\d+\s*章\s*)?(综合习题|总习题|习题|练习|复习题|习题解答|习题参考答案|参考答案|答案)"
    r"[\s、.．·:：\-—\d]*$")

#: 切条残片：标题尾巴上粘着的下一条条目（`第一章 线性空间 7 1.1 线性空间与子空间`）
#: —— 切条时只切到**第一个**页码，于是下一条的"编号 + 标题"留在了这一条的尾巴上。
#: 这条只清 `编号 + 短标题` 的形状，不动真正的长标题。
_TRAILING_NUMBER_FRAGMENT = re.compile(
    r"(?:\s+\*{0,2}§?\d+(?:\.\d+)+\s+[^\d\s][^\s]{0,24}){1,3}\s*$")

#: 目录条目尾部页码：`…… 107` / `...（107）` / `… (Ⅰ)`
_PAGE_TAIL = re.compile(r"[…⋯.．·\s]{2,}[（(]?\s*(\d{1,4}|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+)\s*[）)]?\s*$")
#: **超长标题护栏**（D-2，2026-09-28 实测《电磁学》抓出）：
#: 那本书的目录页是多栏排布，`fitz` 把整页拍平成一行（条目之间用 ` / ` 连着、
#: 页码与编号挤在一起），于是产出一个 **293 字的巨型"标题"候选**，
#: 它又污染了跨源对账（书签一条都对不上 → 238 条书签全废）。
#: 实测合法标题最长约 40 字，取 60 字作上限；超了就判"抽取坏了"，进 `unresolved`。
MAX_TITLE_CHARS = 60
#: 标题里得有**字母或汉字**（D-14 护栏用）：纯数字/标点的不是标题
_HAS_TEXT = re.compile(r"[A-Za-z\u4e00-\u9fff]")

#: 前置页/包装层标题：判"书签顶层的这些节点是不是包装"（D-3 的形状兜底用）
WRAPPER_TITLE = re.compile(
    r"^\s*(目\s*录|目\s*次|封\s*面|书\s*名|版\s*权|前\s*言|序\s*言|序|"
    r"内容简介|图书在版编目.*|总\s*序|出版说明|编写说明|使用说明|修订说明|"
    r"致\s*谢|参考文献|Contents|CONTENTS)\s*$")
_ROMAN = {"Ⅰ": "1", "Ⅱ": "2", "Ⅲ": "3", "Ⅳ": "4", "Ⅴ": "5", "Ⅵ": "6",
          "Ⅶ": "7", "Ⅷ": "8", "Ⅸ": "9", "Ⅹ": "10", "Ⅺ": "11", "Ⅻ": "12"}


def roman_to_int(value: str) -> str:
    """罗马数字页码 → 阿拉伯数字字符串（`Ⅰ`→`"1"`）。不认的**原样返回**（不猜）。"""
    text = str(value or "").strip().replace(" ", "")
    return _ROMAN.get(text, text)


# ── 候选（三源共用的形状） ─────────────────────────────────────────────
@dataclass
class Candidate:
    """一条**来自某一个源**的目录候选。三源各自产出一批，`reconcile` 负责对账。"""
    source: str
    raw: str                       # 源里的原始文字（**不许改**，对账要逐字比）
    number: str = ""               # 编号（`第一章` / `1.2` / `*1.3` / `一`）
    title: str = ""                # 去掉编号剩下的标题
    level: int = 2                 # 该源给出的层级（1=章，2=节，3=小节）
    printed: str | None = None     # 书上印的页码
    pdf: int | None = None         # PDF 页码（1 起）
    note: str = ""                 # 这一条为什么可疑（源自己的观察，不跨源）
    #: 层级是否**由编号形状定死**（`第一章`/`8.1.1` 定死；纯文字标题不定死）。
    #: 对账时：定死的才允许"两源说法不一致"这种争辩，不定死的按书声明顺序走。
    structural: bool = False

    @property
    def text(self) -> str:
        """`编号 + 标题` 还原成一行（对账比较与预览显示都用它）。"""
        return (self.raw or f"{self.number} {self.title}".strip())

    @property
    def depth(self) -> int:
        """编号给出的**形状深度**（章 1 / `N.M` 2 / `N.M.K` 3）；看不出就返回 0。"""
        return number_depth(self.number)

    def as_dict(self) -> dict:
        return {"source": self.source, "raw": self.raw, "number": self.number,
                "title": self.title, "level": self.level, "printed": self.printed,
                "pdf": self.pdf, "note": self.note, "structural": self.structural}


@dataclass
class SourceResult:
    """一个源的抽取结果：候选 + **没抽出来的**（不丢，R-b）。"""
    source: str
    candidates: list[Candidate] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    detail: dict = field(default_factory=dict)
    available: bool = False
    reason: str = ""

    def as_dict(self) -> dict:
        return {"source": self.source, "available": self.available,
                "reason": self.reason, "count": len(self.candidates),
                "unresolved": len(self.unresolved), "detail": self.detail}


# ── 标题编号解析（唯一一处） ────────────────────────────────────────────
def parse_title(text: str) -> tuple[str, str, int]:
    """把一行目录文字拆成 `(编号, 标题, 形状深度)`。

    形状深度只看**编号的形状**（章 1 / `N.M` 2 / `N.M.K` 3），看不出编号就返回 1
    （章/前言这类顶层条目多半不带 `N.M` 编号）。**看不出不猜**：返回 0 深度表示
    "没有编号信息"，由调用方决定。
    """
    stripped = str(text or "").strip()
    match = _CHAPTER_HEAD.match(stripped) or _CHAPTER_LOOSE.match(stripped)
    if match:
        # `*1.3` 的 `*` 是**书目事实**（选学标记），要留在 number 里给人看；
        # 折平它只是 `identity()` 比较时的事。
        return match.group(1).replace(" ", ""), match.group(2).strip(), 1
    match = _DECIMAL_HEAD.match(stripped)
    if match:
        raw_number = re.sub(r"\s+", "", match.group(1))
        # ⚠️ 编号**保留书里的原形**（`§8.1` / `*1.3`）——`*` 是选学标记，不能说丢就丢。
        #    "短横线 ↔ 点号"的折平放在**比较时**（`identity()` / `number_key()`）做。
        return raw_number, match.group(3).strip(), number_depth(raw_number)
    match = _CN_ITEM_HEAD.match(stripped)
    if match:
        return match.group(1), match.group(2).strip(), 2
    return "", stripped, 0


#: 书签/标题里混进来的 HTML 式标记（`<sup>*</sup>` / `<sub>2</sub>` / `<i>…</i>`）。
#: ⚠️ 实测《电磁学》（2026-09-28）：书签里第 11 章写作 `<sup>*</sup>第11章相对论电磁学`
#: ——章号规则要求行首是"第"，这个 `<sup>` 前缀让**整章被判定为没有编号**
#: （`number` 空、标题里还粘着标记），另有一个纯 `<sup>*</sup>` 的垃圾节点混进骨架。
_MARKUP = re.compile(r"</?\s*(?:sup|sub|i|b|em|strong|span|font)\b[^>]*>", re.I)


def clean_markup(text: str) -> str:
    """剥掉 HTML 式标记并折平空白（**只动标记，不动书里的字**）。"""
    return re.sub(r"\s{2,}", " ", _MARKUP.sub("", str(text or ""))).strip()


def canonical_number(number: str) -> str:
    """编号的**规范写法**：`第一章`→`1`，`§1.1`/`*1.3` 保持数字路径。

    为什么必须归一：实测《线性代数：原理与应用》的书签把章写成 `1`（纯数字），
    而印刷目录写 `第一章` —— 不归一的话，整个第 1 章的"编号"在两条源里长得完全不同，
    对账会把章级条目全部判成"配不上"（实测 107 条 unmatched），
    于是凭空多出一整套重复节点（290 条 vs 真实的 182 条）。
    """
    from ..structure.structure import cn_to_int
    text = str(number or "").strip()
    chinese = _CHAPTER_LOOSE.match(text)
    if chinese:
        head = re.sub(r"\s+", "", chinese.group(1))          # `第 8 章` → `第8章`
        digits = re.search(r"[一二三四五六七八九十百零〇\d]+", head)
        if digits:
            value = cn_to_int(digits.group(0))
            if value is not None:
                return str(value)
    return _number_key_raw(text)


#: 编号里的**短横线**（`1-1` / `1–1` / `1—1` / `1－1`）：与点号是**同一个东西**。
#: 实测《电路分析基础》：印刷目录写 `§ 1-1`、书签写 `1.1` —— 不折平就配不上。
_DASH_IN_NUMBER = re.compile(r"\d\s*[\-–—－]\s*\d")


def _number_key_raw(number: str) -> str:
    text = str(number or "").strip().lstrip("*＊§").strip()
    text = text.replace("．", ".").replace(" ", "")
    # 短横线 → 点号：只在"数字-短横线-数字"这种编号形状上折（不动别的字符）
    while _DASH_IN_NUMBER.search(text):
        text = _DASH_IN_NUMBER.sub(lambda m: m.group(0).replace("-", "."), text, count=1)
        text = text.replace("–", ".").replace("—", ".").replace("－", ".")
    return text


def number_depth(number: str) -> int:
    """编号的形状深度：`第一章`→1、`1.2`→2、`1.2.3`→3、`一`→2、其他→0。"""
    text = str(number or "").strip().lstrip("*＊").lstrip("§").strip()
    if not text:
        return 0
    if _CHAPTER_HEAD.match(text) or _CHAPTER_LOOSE.match(text):
        return 1
    decimal = re.match(r"^(\d+(?:[.\-–—－]\d+)+)$", text)
    if decimal:
        return len(re.split(r"[.\-–—－]", decimal.group(1)))
    if re.match(rf"^{_CN}+$", text):
        return 2
    return 0


#: 页码偏移量要至少这么多条"层级对得上"的条目投过票（太少不是证据，是巧合）
MIN_PAGE_VOTES = 5
#: 而且这些票里至少这么大比例要支持同一个偏移量。实测两本真书都是 100%；
#: 样本里混进 1 条异常落点就掉到 87.5% —— 那种情况该整本留空，不该硬给一个数
MIN_PAGE_AGREEMENT = 0.9


def is_exercise_group(title: str) -> bool:
    """习题集/答案集条目（**不是教学内容**，对账时单独一桶）。"""
    return bool(_EXERCISE_HEAD.match(str(title or "").strip()))


#: 标题身份键：**复用结构层唯一的比较口径** `structure.title_key`。
def identity(*parts: str) -> str:
    r"""一条目录条目的身份：折平编号、空白、全半角、LaTeX（用结构层的口径）。

    ⚠️ 这里踩过两次坑，都记下来（2026-09-27 实测）：

    1. 先用 `structure.norm_title`（"只折平排版差异、保留编号"）—— **配不上**：
       它把点也折掉，`§8.1 向量与坐标系` 成了 `§81向量与坐标系`，
       而书签那条是 `向量与坐标系`。而《数分讲义第二册》**书签几乎全不带编号**、
       **印刷目录全带编号**，于是两源一条都对不上（实测 155 条只对上 37 条，
       对上的全是 `习题 8.1` 这种两边都带同一个编号的）。
    2. 改用 `structure.title_key`（它专门去掉编号）**还是配不上**：它的编号正则是
       `^(?:第N[章篇]|\d+(\.\d+)*)`，**不认 `§` / `*` 前缀** ——
       `§8.1 向量` 里的 `§8.1` 不算编号，于是 `§81向量与坐标系` 与
       `向量与坐标系` 又撞不上。

    所以这里先把**选学标记与节符号**剥掉（`*1.3` / `§1.2` / `＊1.3` → `1.3`），
    再交给结构层的 `title_key`。两步都只是在折平排版写法，没有改动任何条目的内容。
    """
    joined = " ".join(p for p in parts if p)
    joined = joined.lstrip("*＊§ \t")
    # 短横线编号折成点号（D-6，2026-09-28）：《电路分析基础》印刷目录写 `§ 1-1`、
    # 书签写 `1.1` —— 不折平则同一节在两条源里身份不同，会被登记成**两个节点**
    # （实测 54 条假分歧、骨架凭空多一整套重复的节）。
    joined = _DASH_IN_NUMBER.sub(lambda m: m.group(0).replace("-", "."), joined)
    return structure.title_key(joined)


# ── 页码映射（印刷页 ↔ PDF 页） ────────────────────────────────────────
class PageMap:
    """印刷页码 ↔ PDF 页码：`pdf = printed + delta`。

    ## 为什么不能直接拿"目录页行"投票

    印刷目录页自己也带页码（`绪论 …… 1`），但目录页**不是**正文第 1 页 ——
    直接 `pdf - printed` 会把目录占的页数算成负数，偏移量当场错掉。
    **只有"正文里的落点"才有资格投票。**

    ## 所以 delta 从哪来

    PDF 书签的页码就是**正文落点**。于是拿印刷目录里记的 `印刷页 p` 与书签的
    `PDF页 q` 配对，`q - p` 就是偏移；取众数，并把少数派逐条记进 `conflicts`（R-c）。
    书签一层都没有的书（线性代数·复变函数）拿不到偏移，就**如实留空**，
    由 `page.printed` 填空率（A2）去反映，而不是编一个数。
    """

    def __init__(self) -> None:
        self.offsets: dict[int, int] = {}
        self.conflicts: list[dict] = []
        self._samples: dict[int, list[dict]] = defaultdict(list)
        self._printed_to_pdf: dict[str, int] = {}
        self._pdf_to_printed: dict[int, str] = {}
        self.method = "none"
        self.clean_votes = 0

    def observe(self, printed: str | None, pdf: int | None, *, raw: str = "") -> None:
        """记一条"**印刷页 p 的正文**落在 PDF 第 q 页"的观察（投票 + 样本）。"""
        if printed is None or pdf is None:
            return
        try:
            page = int(str(printed))
        except (TypeError, ValueError):
            return
        delta = int(pdf) - page
        self.offsets[delta] = self.offsets.get(delta, 0) + 1
        self._samples[delta].append({"printed": page, "pdf": int(pdf),
                                     "raw": str(raw)[:80]})
    
    @property
    def offset(self) -> int | None:
        """出现次数最多的偏移；并列取小的（少动为上，沿用 `normalize_outline_levels` 的口径）。"""
        if not self.offsets:
            return None
        return min(sorted(self.offsets.items(), key=lambda kv: (-kv[1], kv[0])))[0]

    def learn(self, bookmarks: list[dict], printed: list[Candidate], *,
              shift: int | None = None) -> None:
        """拿"印刷目录的 `印刷页` × 书签的 `PDF页`"学偏移量，并记下少数派。

        ## 只让**层级对得上**的条目投票

        《数分讲义第二册》实测：`习题 10.4` 的书签页码落在 PDF 第 40 页（正文 40 页处
        其实是别的章），这种噪声条目直接投票会把众数从 6 拉到 4。
        所以先按 `determine_shift()` 的结果筛一遍：只有"书签层级 - 位移 == 印刷层级"
        的条目才有资格投票。筛完不足 `MIN_PAGE_VOTES` 条就**如实留空**（不猜偏移）。
        """
        if not bookmarks or not printed:
            return
        if shift is None:
            shift, _ = determine_shift(bookmarks, printed)
        pairs, _, _ = align_by_key(bookmarks, printed, level_tolerance=3)
        clean = 0
        for bm_index, printed_index in pairs:
            entry = printed[printed_index]
            if not entry.printed:
                continue
            level = int(bookmarks[bm_index].get("level") or 0) - shift
            if level != entry.level:
                continue
            clean += 1
            self.observe(entry.printed, bookmarks[bm_index].get("pdf_page"),
                         raw=f"{bookmarks[bm_index].get('text','')} ⇄ {entry.text}")
        self.clean_votes = clean
        winner = self.offset
        agree = self.offsets.get(winner, 0) if winner is not None else 0
        if self.offsets:
            self.method = "bookmarks-vs-printed"
        # 票太散就**整本留空**：实测《数分讲义第二册》的 8 个样本里夹 1 条异常落点，
        # 众数就会被拉到错的值（4 而不是 6）。页码偏移错一层，全书 PDF 页全错，
        # 所以宁可空着（A2 空率会反映它），也不给一个"多数派但不多"的数。
        if clean and agree / clean < MIN_PAGE_AGREEMENT:
            self.conflicts.append({
                "kind": "page-offset", "delta": winner, "winner": None,
                "printed": "", "pdf": None, "raw": "",
                "why": f"页码偏移量的票太散（{agree}/{clean} 条支持 {winner}，"
                       f"低于共识线 {MIN_PAGE_AGREEMENT:.0%}）—— 整本留空，不去猜",
            })
            self.offsets.clear()
            self._samples.clear()
            return
        if clean < MIN_PAGE_VOTES:
            self.conflicts.append({
                "kind": "page-offset", "delta": None, "winner": None,
                "printed": "", "pdf": None, "raw": "",
                "why": f"只有 {clean} 条条目的层级与印刷目录一致，不足 {MIN_PAGE_VOTES} 条，"
                       f"偏移量不可靠 —— 留空，不去猜印刷页与 PDF 页的对应",
            })
            self.offsets.clear()
            self._samples.clear()
            return

        winner = self.offset
        if winner is not None:
            for delta, samples in sorted(self._samples.items()):
                if delta == winner:
                    continue
                for sample in samples:
                    self.conflicts.append({
                        "kind": "page-offset", "delta": delta, "winner": winner,
                        "printed": str(sample["printed"]), "pdf": sample["pdf"],
                        "raw": sample["raw"],
                        "why": f"这一条算出偏移 {delta}，但众数是 {winner}"
                               f"（页码可能印错，或书签落点偏了）",
                    })

    def to_pdf(self, printed: str | None) -> int | None:
        """印刷页 → PDF 页（**查表优先**：目录自己声明过的对应最可靠，其次才是偏移量）。"""
        if printed is None:
            return None
        key = str(printed)
        if key in self._printed_to_pdf:
            return self._printed_to_pdf[key]
        if self.offset is None:
            return None
        try:
            return int(key) + self.offset
        except (TypeError, ValueError):
            return None

    def to_printed(self, pdf: int | None) -> str | None:
        if pdf is None:
            return None
        if int(pdf) in self._pdf_to_printed:
            return self._pdf_to_printed[int(pdf)]
        if self.offset is None:
            return None
        return str(int(pdf) - self.offset)

    def report(self) -> dict:
        """给账本/预览看的自述：偏移量是多少、谁投的票、有几处对不上。"""
        return {
            "method": self.method,
            "offset": self.offset,
            "votes": dict(sorted(self.offsets.items())),
            "samples": {str(k): len(v) for k, v in sorted(self._samples.items())},
            "conflicts": self.conflicts,
        }


# ── 源 1：PDF 内嵌书签 ─────────────────────────────────────────────────
def read_raw_bookmarks(pdf_path: str | Path | None) -> list[dict]:
    """读**未归一化**的书签：`{text, level, pdf_page}`（`level` 是 PDF 给的原始层级）。"""
    return adapt.read_pdf_outline(pdf_path)


# ── 两源对齐（唯一一处"书签 ⇄ 印刷目录"的配对逻辑） ─────────────────────
def _cand_key(entry: Candidate) -> str:
    return identity(entry.number, entry.title)


def _bm_key(item: dict) -> str:
    number, title, _ = parse_title(item.get("text", ""))
    return identity(number, title)


def _keys(number: str, title: str) -> tuple[str, str]:
    """一条目的两把钥匙：`(带编号, 只有标题)`。

    为什么要两把：`identity()` 会**折掉编号**（这是为了跨源比对"同一个标题"），
    于是同名条目（`1.1 向量的线性运算` 与 `1.1.2 向量的线性运算`）只剩一把钥匙。
    对齐时必须**先试带编号那把**，避免把 `1.1` 那行配到 `1.1.2` 上
    （实测《线性代数》：单值索引会吃掉同名条目，正文绑定随之错位）。
    """
    return identity(canonical_number(number), title), identity("", title)


def align_by_key(bookmarks: list[dict], printed: list[Candidate], *,
                 level_tolerance: int = 3) -> tuple[
        list[tuple[int, int]], list[int], list[int]]:
    """按**标题身份键**把两源对齐（子序列匹配）。

    ## 为什么不是"按层分组、顺序补齐"

    试过，**实测会假配**：《数分讲义第二册》印刷目录的 1 级里，`第8章 空间解析几何`
    与 `第 8 章综合习题` 折平后同名，于是"第 8 章"被配到习题集上，整段错位，
    凭空算出 36 条假冲突。

    所以改成**子序列**口径：印刷目录是"书自己声明的一串条目"，书签也该是同一串
    （书名/版本变体可以少几条，但顺序不变）。只在**两边同名条目按顺序对得上**时才配，
    对不上就留作 `unmatched`，绝不硬塞一个"位置差不多"的对手（R-c 不猜）。

    `level_tolerance` 是**防撞的第二道闸**：同名条目（`二重积分的换元` 同时是节和小节）
    只有在层级差不超过它时才配。默认放到 3（等于不设限，因为书签层级本身不可靠），
    由调用方按场景收紧。

    返回 `(pairs, unmatched_bookmarks, unmatched_printed)`，索引都指回原列表。
    """
    # 每个键可能有多条（章与习题集同名、节与小节同名）：用列表按顺序消费。
    # `exact` 存"带编号"的钥匙，`loose` 存"只有标题"的钥匙 —— 先试 exact。
    exact: dict[str, list[int]] = defaultdict(list)
    loose: dict[str, list[int]] = defaultdict(list)
    for position, entry in enumerate(printed):
        numbered, titled = _keys(entry.number, entry.title)
        exact[numbered].append(position)
        loose[titled].append(position)

    pairs: list[tuple[int, int]] = []
    unmatched_bm: list[int] = []
    last = -1
    for position, item in enumerate(bookmarks):
        level = int(item.get("level") or 0)
        number, title, _ = parse_title(item.get("text", ""))
        numbered, titled = _keys(number, title)
        chosen = None
        for table, key in ((exact, numbered), (loose, titled)):
            for candidate_position in table.get(key, []):
                if candidate_position <= last:
                    continue
                if abs(level - printed[candidate_position].level) <= level_tolerance:
                    chosen = candidate_position
                    break
            if chosen is not None:
                break
        if chosen is None:
            unmatched_bm.append(position)
            continue
        last = chosen
        pairs.append((position, chosen))

    # ── 第二遍：**章级按章号补配**（D-9，2026-09-28 实测《电路分析基础》）──────────
    # 那本书的章 4：印刷目录写 `4 分解方法及单、双口电路`、书签写
    # `第四章 分解方法及单、双口网络` —— 标题里两个词不同（电路/网络），
    # key 匹配必然失败，于是**同一章被登记成两个一级节点**（8 章而非 7 章）、
    # 树被劈开（结构一致率因此只有 0.704）。
    #
    # 为什么敢在这里按章号配：**章数少、章号是强信号**（`4` 与 `第四章` 都归一成 `4`），
    # 而且配完还要**记一条待裁决冲突**（标题不一致 → 交模型视觉复核，P7），
    # 不是静默合并。正文/节的同名错配风险也小得多（层数一多才容易撞）。
    used_bm = {i for i, _ in pairs}
    used_pr = {j for _, j in pairs}
    by_no_pr: dict[str, list[int]] = defaultdict(list)
    for index in range(len(printed)):
        if index in used_pr or printed[index].level != 1:
            continue
        key = _chapter_number(printed[index].number, printed[index].title)
        if key:
            by_no_pr[key].append(index)
    for bm_index in list(unmatched_bm):
        item = bookmarks[bm_index]
        if int(item.get("level") or 0) != 1:
            continue
        number, title, _ = parse_title(item.get("text", ""))
        key = _chapter_number(number, title)
        pool = [i for i in by_no_pr.get(key, []) if i not in used_pr]
        if not key or not pool:
            continue
        printed_index = pool[0]
        used_pr.add(printed_index)
        used_bm.add(bm_index)
        pairs.append((bm_index, printed_index))

    pairs.sort()
    unmatched_bm = [i for i in range(len(bookmarks)) if i not in used_bm]
    unmatched_pr = [i for i in range(len(printed)) if i not in used_pr]
    return pairs, unmatched_bm, unmatched_pr


#: 章号里"只是编号"的那部分：`第四章` / `4 分解方法…` / `4.` / `第 4 章`
_CHAPTER_NO_LEAD = re.compile(r"^\s*(\d{1,2})\s*[\s、.．·:：]")
_CHAPTER_NO_CN = re.compile(r"^\s*\*{0,2}\s*第\s*([0-9一二三四五六七八九十百零〇]+)\s*[章篇]")


def _chapter_number(number: str, title: str) -> str:
    """一条**章级**条目的章号（归一成阿拉伯数字字符串）；看不出来返回空串（不猜）。"""
    for text in (number, title):
        stripped = str(text or "").strip()
        if not stripped:
            continue
        match = _CHAPTER_NO_CN.match(stripped)
        if match:
            # ⚠️ 这里拿到的是**中文数字本身**（`四`），不是 `第四章` ——
            # 交给 `canonical_number()` 认不出来（它要"第N章"整体），实测因此配对失败。
            value = structure.cn_to_int(match.group(1))
            if value is not None:
                return str(value)
        lead = _CHAPTER_NO_LEAD.match(stripped)
        if lead:
            return lead.group(1)
    return ""




def shape_shift(bookmarks: list[dict]) -> tuple[int, dict]:
    """**不看印刷目录**，只按书签自己的形状估位移量（D-3 的兜底）。

    为什么需要（2026-09-28 实测《电磁学》）：那本书的目录页是多栏排布，
    印刷目录抽出来基本是废的（护栏拦掉 293 字的假条目后只剩 3 条）——
    于是"用印刷目录当标尺"这条路走不通，而原来的实现会**把 238 条书签全部作废**
    （`method: unresolved` → 每一条都记"层级无法判定"），
    结果是 `toc.v2.json` 只剩 4 个节点。**印刷目录坏 ≠ 书签坏**：
    书签是书自己写在文件里的结构声明，它自己就能站住。

    ## 判据（保守，只动 0 或 1 层）

    * 取书签里最小的层级 `top`；
    * 看 `top` 这一层都是些什么：**半数以上是"包装/前置页"**（`目录`/`封面`/`前言`…）
      ⇒ 这一层是包装，`shift = top`（把包装层减掉）；
      否则 ⇒ `shift = top - 1`（把最小层级归一到 1）；
    * **位移量封顶 1 层**：整本要移两层以上，八成是判错（旧实现同一条纪律）。

    实测三本：电磁学（顶层只有 `目录`）→ shift=1；电路分析（顶层 8 条里 7 条是
    封面/书名/版权/前言/目录…）→ shift=1；数值计算方法（顶层只有 `目录`）→ shift=1。
    """
    levels = sorted({int(b.get("level") or 0) for b in bookmarks if int(b.get("level") or 0) > 0})
    if not levels:
        return 0, {"method": "bookmark-shape", "shift": 0, "top_level": None,
                   "why": "书签层级字段全为 0，无从判断"}
    top = levels[0]
    top_nodes = [b for b in bookmarks if int(b.get("level") or 0) == top]
    wrappers = [b for b in top_nodes
                if WRAPPER_TITLE.match(str(b.get("text") or "").strip())]
    majority_wrapper = len(wrappers) * 2 >= len(top_nodes)
    shift = top if majority_wrapper else max(0, top - 1)
    shift = min(shift, 1)
    return shift, {
        "method": "bookmark-shape", "shift": shift, "top_level": top,
        "top_count": len(top_nodes), "wrapper_count": len(wrappers),
        "majority_wrapper": majority_wrapper, "levels": levels,
        "why": ("印刷目录不可用（抽坏了或一条都对不上）→ 只按书签自己的形状定位移量，"
                "位移量封顶 1 层；**层级可信度降一档**，等模型视觉复核再定"),
    }


def determine_shift(bookmarks: list[dict], printed: list[Candidate]) -> tuple[int, dict]:
    """书签层级要整体减掉几层才是"真实的章/节层级"。

    ⚠️ 与 `adapt.normalize_outline_levels()` 的差别（为什么另写一份）：
    那边只用**标题形状**投票（`第N章`→1、`N.M`→2），遇到"章名不带编号"的书
    （《数分讲义第二册》的 `空间解析几何`）只能靠蒙；这边用**印刷目录的层级**当标尺 ——
    印刷目录的层级是从编号形状直接读出来的（`第8章`→1、`§8.1`→2），不依赖书签自己。

    ## 判据：**试层级容差、按段投票**

    * 层级容差 `k` 从 0 试到 3：只有"两边层级差不超过 k"的同名条目才允许配对。
      `k` 越大配得越多、越容易假配；所以取"**配对里层级差一致的那部分最多**"的 k
      （实测：数值计算方法 k=3 命中 98/104；数分讲义第二册 k=3 命中 77/116）。
    * 配对之后按**连续相同层级差的段**投票（每段按段长加权）。为什么不用逐条多数：
      《数分讲义第二册》里 `习题 8.1`/`第 8 章综合习题` 的书签层级是乱的
      （实测 lv4=27 条、lv3=2 条、lv2=6 条，而印刷层级是 2 或 1），逐条投票会被噪声带偏；
      分段投票让"正文条目那 77 条的 d=0"整段压住噪声。并列取**小的 |d|**（少动为上）。

    ## 证据不足时报不确定

    `|d| >= 2`（整本要移两层以上，八成是配错而不是真位移）或**一条都没对上**，
    都返回 `(0, {"method": "unresolved"})` —— `extract_bookmarks()` 会据此把
    **每一条**书签记成"层级无法判定"进 `unresolved`。宁可整本报不确定，
    也不猜一个会让整棵树错位的位移量（D8/H5）。
    """
    if not bookmarks:
        return 0, {"method": "no-bookmarks", "votes": {}, "matched": 0, "shift": 0}
    if not printed:
        # D-3：印刷目录空了（没抽出来，或被 D-2 护栏判成"抽坏了"）→ 走形状兜底
        return shape_shift(bookmarks)

    #: 逐步放宽层级容差，只在"支持条数**变多**"时才继续放宽（一旦变少就停）。
    #: 为什么不直接取全局最大：容差放宽会引入噪声配对，支持条数反而会掉
    #: （实测《数分讲义第二册》tol=0 支持 82、tol=1 掉到 77），
    #: 这时应该保留那个**干净**的口径，而不是为了让配对更多去换一个更脏的口径。
    candidates: list[tuple[int, int, int, list[tuple[int, int]], dict]] = []
    votes_by_shift: dict[int, dict] = {}
    previous = -1
    for tolerance in (0, 1, 2, 3):
        pairs, _, _ = align_by_key(bookmarks, printed, level_tolerance=tolerance)
        if not pairs:
            continue
        diffs = [int(bookmarks[i].get("level") or 0) - printed[j].level
                 for i, j in pairs]
        votes: dict[int, int] = defaultdict(int)
        for delta, count in _runs(diffs):
            votes[delta] = max(votes[delta], count)
        for delta, count in votes.items():
            votes_by_shift.setdefault(delta, {})[tolerance] = count
        # ⚠️ 支持度取**最长连续段**（不是逐条计数）。实测《数值计算方法》：
        # 书签是 `目录`(lv1)/`绪论`(lv2)/`0.1`(lv3)，印刷目录是 `绪论`(lv1)/`0.1`(lv2)。
        #   · s=1：三条**连着**都说"书签比印刷深一层" → 最长段 3 条，结构自洽；
        #   · s=0：只有 0.1/0.2 碰巧对上（绪论差一层把段切断）→ 最长段只有 2 条。
        # 逐条计数会误选 s=0（2 > 1），把绪论整章降一级；按连续段算才选对。
        delta, count = min(votes.items(), key=lambda kv: (-kv[1], abs(kv[0])))
        candidates.append((count, -abs(delta), tolerance, pairs,
                           {"shift": delta, "votes": dict(votes)}))
        if count < previous:
            break
        previous = count

    if not candidates:
        # D-3：印刷目录在，但一条都对不上（多半是它自己抽坏了）→ 走形状兜底，
        # **不再把整本书签作废**（实测《电磁学》：那样做会让 toc.v2 只剩 4 个节点）
        fallback = shape_shift(bookmarks)
        fallback[1]["printed_pairs"] = 0
        fallback[1]["why"] = ("书签与印刷目录一条都没对上（印刷目录多半抽坏了）→ "
                              + str(fallback[1].get("why") or ""))
        return fallback

    count, _, tolerance, pairs, winner = max(
        candidates, key=lambda row: (row[0], row[1], row[2]))
    best = winner["shift"]
    detail = {"method": "printed-toc", "pairs": len(pairs), "tolerance": tolerance,
              "support": count, "support_ratio": round(count / len(pairs), 3),
              "shift": best, "votes": winner["votes"],
              "support_curve": [row[0] for row in candidates],
              "votes_by_shift": {str(k): dict(sorted(v.items()))
                                 for k, v in sorted(votes_by_shift.items())},
              "levels": sorted({int(b.get("level") or 0) for b in bookmarks})}
    ratio = count / len(pairs) if pairs else 0.0
    if abs(best) >= 2:
        return 0, {**detail, "method": "unresolved",
                   "why": f"算出来的位移量是 {best}（整本要移 2 层以上），"
                          f"这不像真位移，更像两源配错了 —— 不猜"}
    if ratio < MIN_SHIFT_SUPPORT:
        return 0, {**detail, "method": "unresolved",
                   "why": f"最好的位移量 {best} 也只被 {ratio:.0%} 的配对支持"
                          f"（阈值 {MIN_SHIFT_SUPPORT:.0%}）—— 书签层级本身不规整，"
                          f"逐条报不确定，不猜"}
    return best, detail


#: 位移量至少要得到这么大比例的配对支持。实测：数值计算方法 94%、
#: 数分讲义第二册 66%（它把习题也塞进书签且层级乱）；后者会被判成"不确定"——
#: 这正是 H4/H5 要的：**宁可报不确定，也不给一个会让整棵树错位的层级**。
MIN_SHIFT_SUPPORT = 0.8


def _runs(values: list[int]) -> list[tuple[int, int]]:
    """把序列切成"连续相同值的段"，返回 `[(值, 段长), ...]`。"""
    out: list[tuple[int, int]] = []
    for value in values:
        if out and out[-1][0] == value:
            out[-1] = (value, out[-1][1] + 1)
        else:
            out.append((value, 1))
    return out


def extract_bookmarks(pdf_path: str | Path | None, *,
                      printed: list[Candidate] | None = None,
                      page_map: PageMap | None = None) -> SourceResult:
    """源 1：PDF 内嵌书签 → 候选。

    * 位移量由 `determine_shift()` 定（优先拿印刷目录当标尺）；
    * 位移后仍 `level < 1` 的条目（书签的 `目录` 包装根）**不丢**：进 `unresolved`，
      并在 `detail.packaging` 里记一笔 —— 这正是《数值计算方法》的靶子。
    """
    result = SourceResult(source=SOURCE_BOOKMARKS)
    raw = read_raw_bookmarks(pdf_path)
    if not raw:
        result.reason = "这份 PDF 没有内嵌书签"
        return result

    printed = printed or []
    shift, detail = determine_shift(raw, printed)
    result.available = True
    result.detail = {"raw_count": len(raw), "shift": shift, "shift_votes": detail}
    undecided = detail.get("method") == "unresolved"

    packaging: list[dict] = []
    for item in raw:
        text = clean_markup(item.get("text"))
        if not text:
            continue
        raw_level = int(item.get("level") or 0)
        level = raw_level - shift
        pdf_page = int(item.get("pdf_page") or 0) or None
        if undecided:
            # 位移量判不出来 → **每一条都报不确定**，不猜层级（D8/H5）
            result.unresolved.append({
                "raw": text, "source": SOURCE_BOOKMARKS, "page": pdf_page,
                "why": f"书签层级无法判定（{detail.get('why', '')}）—— "
                       f"原始层级 {raw_level}，不敢减一个猜出来的位移量",
            })
            continue
        if level < 1:
            # 包装层（根的 `目录`）：记账，不当节点
            packaging.append({"text": text, "raw_level": raw_level, "pdf": pdf_page})
            result.unresolved.append({
                "raw": text, "source": SOURCE_BOOKMARKS, "page": pdf_page,
                "why": f"书签的包装层（原始层级 {raw_level}，位移 {shift} 后不足 1 级）"
                       f"—— 只记账，不当目录节点",
            })
            continue
        number, title, depth = parse_title(text)
        if not title:
            result.unresolved.append({
                "raw": text, "source": SOURCE_BOOKMARKS, "page": pdf_page,
                "why": "书签标题为空",
            })
            continue
        result.candidates.append(Candidate(
            source=SOURCE_BOOKMARKS, raw=text, number=number, title=title,
            level=min(level, 3), printed=None, pdf=pdf_page,
            structural=bool(depth), note="" if depth else "标题不带编号，层级由书签定"))
    if packaging:
        result.detail["packaging"] = packaging
    return result


# ── 源 2：印刷目录页文字 ───────────────────────────────────────────────
def parse_printed_line(row: str) -> tuple[Candidate | None, dict | None]:
    """一行目录页文字 → 候选（`adapt._toc_entry` 的同口径重写，多留原始行）。

    返回 `(候选, 记不下的原因)`；两者必有其一。落不了页 / 认不出边界的**不丢**。
    """
    raw = str(row or "").strip()
    if len(raw) < 2:
        return None, {"raw": raw, "why": "行太短，连标题都没有"}
    tail = _PAGE_TAIL.search(raw)
    if not tail:
        return None, {"raw": raw, "why": "行尾没有页码（印刷目录的条目一定带页码）"}
    head = raw[:tail.start()].strip(" .．·…⋯")
    if not head:
        return None, {"raw": raw, "why": "行尾页码之前没有标题"}
    # D-2 护栏：标题长到这个地步，几乎一定是"多栏目录被拍平 / 多条挤成一行"，
    # 不是一条真条目。**判抽取坏了**（进 unresolved，不丢），不让它变成巨型候选
    # 去污染跨源对账（实测《电磁学》：293 字的假标题把 238 条书签全带废了）。
    if len(head) > MAX_TITLE_CHARS:
        return None, {"raw": raw[:MAX_TITLE_CHARS] + "…",
                      "why": f"标题长 {len(head)} 字，超过上限 {MAX_TITLE_CHARS} —— "
                             f"多半是多栏目录被拍平/多条挤成一行，判为抽取坏了"}
    # 切条时，章条目后面会捎上下一条的编号（`第一章 线性空间 7 1.1 线性空间与子空间`
    # 只切到第一个页码，把 `1.1` 那条的开头留在了标题尾巴上）—— 这里清掉。
    # ⚠️ 只清**行尾的编号形状残片**，不动标题正文（H7：不许改书里的字）。
    head = _TRAILING_NUMBER_FRAGMENT.sub("", head).strip(" .．·…⋯")
    # 还粘着东西（切条时后面几条挤在同一个数字 token 后面）：把行尾的页码摘出来。
    # 例：`10.3.3 贝尔不等式 295 附录A 几何与对称 298` —— 295 是 10.3.3 的页，
    # 后面那截是下一条（附录A）的开头。摘掉页码、剩下的碎片不再处理（下一条会自己成条）。
    tail_page = re.search(r"\s+(\d{1,4})\s+(?:附录|附表|参考文献|索引|后记|致谢)\S*", head)
    if tail_page:
        head = head[:tail_page.start()].strip()
        if tail_page.group(1) and not _PAGE_TAIL.search(head):
            head = f"{head} …… {tail_page.group(1)}"
    number, title, depth = parse_title(head)
    if not title:
        return None, {"raw": raw, "why": "拆不出标题文字"}
    # D-14 护栏：**标题里必须有字母或汉字**（纯数字/标点的"标题"是页码列被切成条目的产物）。
    # 实测《boats to burn》（2026-09-29 外部基准）：印刷目录页是多栏排布，抽出来一堆
    # `004 005`、`006 007` 这种"页码对"成了目录节点 —— 骨架被灌水（233 条里一大批），
    # 对齐覆盖率与 nTED 都被拉低。
    if not _HAS_TEXT.search(title):
        return None, {"raw": raw[:60], "why": "标题里没有字母/汉字（多半是页码列被切成了条目）"}
    # D-18 护栏：**被折断的碎片**不要（2026-09-29 实测《Basic Analysis》抓出）。
    # 那本书的印刷目录是"短行 + 折行"，抽出来 483 条里只有 44 条带编号，
    # 其余 446 条是 `in a metric space,`、`power series,` 这种**续行碎片**
    # （小写开头、逗号/分号结尾）—— 它们成了 446 个"目录节点"，
    # 权威被灌水：覆盖率掉到 20%、nTED 0.5。
    # 判据只用两条**不会误伤真标题**的：① 结尾是逗号/分号；② 没有编号且小写字母开头。
    stripped_title = title.strip()
    if stripped_title.endswith(("，", ",", "；", ";", "、", "：", ":")):
        return None, {"raw": raw[:60], "why": "标题以逗号/分号结尾（多半是折行的碎片）"}
    # ⚠️ **带列表标记的小写开头不算碎片**（2026-09-29 外部基准抓出的回退）：
    # `c) Private Arrangements`、`i) Types of Contractual Relationship` 是**真条目**
    # （法律书用小写字母编号分层），原来的判据把《Introduction to Swiss Law》
    # 的 114 条真条目当成碎片丢掉了 —— prec/rec 从 0.43/0.44 掉到 0.24/0.24。
    # 只有"小写开头**且不是**列表标记"才算续行碎片（`in a metric space,`）。
    _LIST_MARK = re.compile(r"^[a-z]\s*[)\].,、]")
    if (not number and re.match(r"^[a-z]", stripped_title)
            and not _LIST_MARK.match(stripped_title)):
        return None, {"raw": raw[:60], "why": "没有编号、且小写字母开头（多半是折行的碎片）"}
    level = depth or 2
    return Candidate(source=SOURCE_PRINTED, raw=head, number=number, title=title,
                     level=min(max(level, 1), 3), structural=bool(depth),
                     printed=roman_to_int(tail.group(1))), None


#: 标题形状（切条时判"这个 token 是不是标题"）：章 / 数字路径 / 习题集。
#: ⚠️ 后面必须跟**真正的文字**（`\S` 且不是点线）—— 否则一个孤零零的 `.`
#: 也会被当成标题开头，把上一条的尾巴吃掉（实测踩过：`1.2 线性组合… 13 1.2.1 线性组合`
#: 被切成 `1.2 线性组合与线性相关性 …… 13` + `1.2.1 线性组合`，中间那条丢了页）。
_NUMBER_ONLY = re.compile(
    rf"^(?:第\s*(?:{_CN}+|\d+)\s*[章篇]|\*{{0,2}}\s*§?\s*\d+(?:\.\d+)+|"
    rf"(?:综合习题|总习题|习题|练习|复习题|参考答案|答案)\s*[\d.\-—]*)$")
_TITLE_SHAPE = re.compile(
    rf"^(?:第\s*(?:{_CN}+|\d+)\s*[章篇]|\*{{0,2}}\s*§?\s*\d+(?:\.\d+)+|"
    rf"(?:综合习题|总习题|习题|练习|复习题|参考答案|答案)\s*[\d.\-—]*)\s+[^\s.．·…⋯]")
#: 纯页码 token（阿拉伯或罗马）
_PAGE_TOKEN = re.compile(r"^(\d{1,4}|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+)$")
#: 下一条的编号 token（`1.1` / `§1.1` / `第一章` / `一、`）
_NUMBER_TOKEN = re.compile(
    rf"^(\*{{0,2}}\s*§?\s*\d+(?:\.\d+)+|第\s*(?:{_CN}+|\d+)\s*[章篇]|"
    rf"(?:{_CN}+)\s*[、.．])$")
#: 引导点 / 省略号（token 级）
_DOTS = re.compile(r"^[.．·…⋯]+$")


def split_printed_lines(text: str) -> list[str]:
    """把目录页抽出来的文字切成**一条一行**（标题 …… 页码）。

    ## 为什么必须切（实测《线性代数：原理与应用》李思，2026-09-28）

    `fitz.get_text("text")` 在这些目录页上有两种排布，按"一行一条"解析都会瘸：

    * **摊成三行**：编号一行（`4.3.3`）、标题一行（`根子空间`）、点线+页码一行；
    * **挤成一行**：一页 3000 字里塞十几条，用空格连着
      （`1.1.1 线性空间 7 1.1.2 直和 9 …`）。

    所以这里先把整页并成一个 token 流，再按"**标题 …… 页码**"的节奏走：

    1. `标题形状` 的 token 起一条新条目；
    2. 后面的普通 token 拼进标题（先做 `token.rstrip(".")`，因为点线常和标题黏在一起，
       像 `直和.`）；
    3. 遇到点线就跳过；遇到**纯数字**时判断：**它后面是不是"下一条的编号形状"**
       （或它是流里最后一个有意义的 token）—— 是，它就是页码，本条收尾；
       不是，那它是标题里的数字（`3.2 n 阶行列式`），继续拼；
    4. 遇到下一条编号（`_NUMBER_TOKEN`）就收尾（本条没写页码）。

    ⚠️ 第 3 条的"往后看一步"是关键：章条目 `第一章 线性空间 7 1.1 …` 里，页码 `7`
    后面紧跟下一条的编号 `1.1`；而标题内部的 `n` 后面跟的是普通词。**区别在下一个
    token 的形状**，不在数字本身。
    """
    raw = str(text or "")
    # 引导点常常和标题/编号黏成一个 token（`直和.` / `4.3.3.`）：先剥掉。
    # `rstrip(".．·…⋯")` 不会伤到 `3.2` 这种内部点（只剥尾部）。
    tokens = [tok.rstrip(".．·…⋯") for tok in raw.split()]
    tokens = [tok for tok in tokens if tok]
    out: list[str] = []
    index = 0
    while index < len(tokens):
        if _DOTS.match(tokens[index]) or not (_TITLE_SHAPE.match(tokens[index])
                                              or _NUMBER_ONLY.match(tokens[index])):
            index += 1
            continue
        pieces = [tokens[index]]
        page: str | None = None
        index += 1
        while index < len(tokens):
            token = tokens[index]
            if _DOTS.match(token):
                index += 1
                continue
            core = token.rstrip(".．·…⋯")
            if _PAGE_TOKEN.match(core):
                following = None
                for probe in range(index + 1, len(tokens)):
                    if _DOTS.match(tokens[probe]):
                        continue
                    following = tokens[probe].rstrip(".．·…⋯")
                    break
                if following is None or _TITLE_SHAPE.match(following) \
                        or _NUMBER_TOKEN.match(following):
                    page = core
                    index += 1
                    break
                pieces.append(core)
                index += 1
                continue
            if _NUMBER_TOKEN.match(core):
                break
            pieces.append(core)
            index += 1
        title = " ".join(piece for piece in pieces if piece)
        if title:
            out.append(f"{title} …… {page}" if page else title)
    return out if out else [tok for tok in raw.splitlines() if tok.strip()]


def extract_printed(rows: list[str] | list[dict], *,
                    page_map: PageMap | None = None) -> SourceResult:
    """源 2：目录页文字行 → 候选。

    `rows` 可以是 `["行文", ...]`，也可以是 `adapt` 给的
    `[{"text","toc_page","toc_page_label","level","from_page"}, ...]`。

    ⚠️ `from_page` 是**目录行自己**落在哪一 PDF 页，**不是**这一节正文的 PDF 页 ——
    这里只把它记进 `Candidate.note` 供人看，**绝不拿它算页码偏移**（那会差出整段目录）。
    偏移量由 `PageMap.learn()` 用书签落点学（见 `PageMap` 的 docstring）。
    """
    result = SourceResult(source=SOURCE_PRINTED)
    if not rows:
        result.reason = "没有目录页文字（这本书的目录页没被识别出来）"
        return result
    result.available = True

    expanded: list[str] = []
    for row in rows:
        if isinstance(row, str):
            expanded.extend(split_printed_lines(row))
        else:
            expanded.append(row)
    rows = expanded

    for row in rows:
        if isinstance(row, dict):
            text = clean_markup(row.get("text"))
            printed = row.get("toc_page")
            printed = None if printed in ("", None) else str(printed)
            from_page = row.get("from_page")
            from_page = int(from_page) if from_page not in ("", None) else None
            candidate, why = parse_printed_line(text if printed is None
                                                else f"{text} …… {printed}")
            if candidate is not None:
                candidate.printed = printed or candidate.printed
                # 目录行所在页：只当**旁证**留着，不进 page.pdf（那是正文落点）
                candidate.note = (f"目录行在 PDF 第 {from_page} 页" if from_page else "")
                result.candidates.append(candidate)
            else:
                result.unresolved.append({**why, "source": SOURCE_PRINTED,
                                          "page": from_page})
            continue

        candidate, why = parse_printed_line(str(row))
        if candidate is None:
            result.unresolved.append({**why, "source": SOURCE_PRINTED, "page": None})
        else:
            result.candidates.append(candidate)
    result.detail = {"rows": len(rows)}
    return result


def printed_rows_from_pdf(pdf_path: str | Path, pages: list[int]) -> list[str]:
    """直接从 PDF 的目录页抽文字行（**不经过 MinerU**）。

    ## 为什么需要这条路（实测《线性代数：原理与应用》李思，2026-09-28）

    这本书是**新书**：还没有 MinerU 产物，也就没有 `toc.json#printed_entries`
    可以读。如果印刷目录这一源就这么空着，三源对账就只剩一源 ——
    而那正是这次重构要治的病（"只有一源"等于没有交叉验证）。

    所以这里自己读 PDF 文字层。**注意**：扫描件（没有文字层）读不出东西，
    那不是 bug —— `preflight` 会如实报"没有文字层"，这条源就空着，
    由 `sources_used` 与 `confidence` 反映出来（R-b 不隐瞒）。
    """
    try:
        import pymupdf
    except ImportError:
        return []
    if not pdf_path or not Path(pdf_path).is_file():
        return []
    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception:                                     # noqa: BLE001
        return []
    rows: list[str] = []
    try:
        for page_no in pages:
            if not 1 <= int(page_no) <= doc.page_count:
                continue
            rows.extend(split_printed_lines(doc[int(page_no) - 1].get_text("text")))
    finally:
        doc.close()
    return rows


def printed_candidates_from_toc(toc_payload: dict) -> list[str]:
    """从既有 `toc.json`(v1) 拿"目录页解析出来的条目"，用于**对照**（不是依赖）。

    只读、不改；读不出来就返回空列表（新块不因为老产物缺失而停摆）。
    """
    rows = []
    for entry in (toc_payload or {}).get("printed_entries") or []:
        text = str(entry.get("text") or "").strip()
        printed = entry.get("toc_page")
        if text and printed not in ("", None):
            rows.append(f"{text} …… {printed}")
    return rows


# ── 源 3：正文里"长得像标题"的行 ────────────────────────────────────────
#: 正文行的形状判据（**只看形状，不做版面推断** —— 版面推断交给结构层）
_BODY_HEADING = re.compile(
    rf"^(?:第\s*(?:{_CN}+|\d+)\s*[章篇]|\*{{0,2}}\s*§?\s*\d+(?:\.\d+)*\s+\S)")


def extract_body_titles(parse_dir: str | Path) -> SourceResult:
    """源 3（**方案 A 版**）：MinerU 的 `title` 块 → 候选。

    ## 为什么换掉 `extract_body_shapes()`（D-7，2026-09-28 实测抓出）

    后者从 `document.md` 的**文字形状**猜标题，实测《电磁学》把**被拍平的目录页文本**
    （`0.1 电磁学的建立 /1 0 1 1 宇宙的演化 /1 …`，整页被 `fitz` 挤成一行）
    也认成了正文标题 —— 凭空造出 **177 条"正文 vs 目录"的假分歧**，
    把 `confidence` 一直压在 `low`。

    MinerU 的 `title` 块是**版面模型**给的（带 `line`/`page`/`region`），
    既准、又能按区域过滤（前言/目录/习题/答案都不是正文标题）。
    这也正是方案 A 的分工：**上游管"这一行是不是标题"，书的目录管"第几级"**。
    """
    path = Path(parse_dir) / "headings.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    result = SourceResult(source=SOURCE_BODY)
    rows = payload.get("headings") or []
    if not rows:
        result.reason = "没有 headings.json（还没跑 MinerU 适配）"
        return result
    result.available = True
    for row in rows:
        text = clean_markup(row.get("text"))
        if not text:
            continue
        region = str(row.get("region") or "")
        if region and region != "body":
            continue                     # 前言/目录/习题/答案：不是正文标题
        number, title, depth = parse_title(text)
        line = row.get("line")
        result.candidates.append(Candidate(
            source=SOURCE_BODY, raw=text, number=number, title=title,
            level=min(max(depth or 2, 1), 3), structural=bool(depth),
            pdf=int(row.get("page") or 0) or None,
            note=(f"正文第 {line} 行" if line else "")))
    result.detail["from"] = "mineru-title-blocks"
    return result


def extract_body_shapes(document_md: str | Path | None, *,
                        page_map: PageMap | None = None,
                        current_page: dict | None = None) -> SourceResult:
    """源 3：`document.md` 里像标题的行 → 候选。

    **这一源没有任何权力**（R-d）：它只用来交叉核对"印刷目录里那一条在正文里长得对不对"。
    所以这里刻意**不做**版面推断、不猜层级 —— 层级取编号形状，形状看不出就置 2 并标注。

    `current_page`：`{行号: "印刷页码/PDF页码"}`，用来给候选落页（没有就留 `None`）。
    """
    result = SourceResult(source=SOURCE_BODY)
    if not document_md:
        result.reason = "没有 document.md（正文还没抽出来）"
        return result
    path = Path(document_md)
    if not path.is_file():
        result.reason = f"{path} 不存在"
        return result
    result.available = True

    text = path.read_text(encoding="utf-8")
    pages = current_page or {}
    seen: dict[str, int] = {}
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or len(stripped) > 80 or len(stripped) < 3:
            continue
        if stripped.startswith(("#", "!", "[", "|", "$", "```")):
            continue
        if not _BODY_HEADING.match(stripped):
            continue
        number, title, depth = parse_title(stripped)
        if not title or len(title) > 60:
            continue
        key = identity(number, title)
        if key in seen:                 # 同一条标题在正文里重复（页眉/目录残留）
            continue
        seen[key] = line_no
        printed = pages.get(line_no)
        result.candidates.append(Candidate(
            source=SOURCE_BODY, raw=stripped, number=number, title=title,
            level=min(depth or 2, 3), printed=printed, pdf=None,
            structural=bool(depth),
            note="" if depth else "编号形状看不出层级，按 2 记（不计入一致判定）"))
    result.detail = {"lines_scanned": len(text.splitlines())}
    return result


# ── 输入指纹（"我吃了什么"） ────────────────────────────────────────────
def fingerprint_inputs(*, pdf: str | Path | None = None,
                       document_md: str | Path | None = None,
                       toc_payload: dict | None = None) -> dict:
    """算出这次抽取"吃了什么"的指纹（设计稿 §8 账本第一问）。"""
    out = {"pdf_sha256": "", "document_sha256": ""}
    if pdf and Path(pdf).is_file():
        out["pdf_sha256"] = sha256_file(pdf)
    if document_md and Path(document_md).is_file():
        out["document_sha256"] = sha256_file(document_md)
    elif toc_payload:
        out["document_sha256"] = str((toc_payload.get("_meta") or {}).get("source_sha256") or "")
    return out


__all__ = [
    "Candidate", "PageMap", "SourceResult", "determine_shift", "extract_body_shapes",
    "clean_markup", "extract_bookmarks", "extract_body_titles", "extract_printed", "fingerprint_inputs", "identity",
    "canonical_number", "is_exercise_group", "number_depth", "parse_printed_line",
    "parse_title",
    "printed_candidates_from_toc", "printed_rows_from_pdf", "read_raw_bookmarks",
    "roman_to_int", "split_printed_lines",
]
