"""MinerU 适配块：把 MinerU 的产物拼成"跟 OCR 那一层长得一样"的东西。

**核心设计只有一句**：这个块假装自己是 OCR —— 产出同名的 `document.md`、
同名的 `figures.json`，所以结构层、规范层、导出层一行都不用改。

输入（原样放好，一个字节都不改）：
    <mineru 目录>/layout.json        3.x legacy 格式（顶层 `pdf_info`）—— 主力
    <mineru 目录>/images/*.jpg       裁好的单图（网页端 ZIP 里的，或应急从 CDN 下的）
    （也接受直接给一个 JSON 文件路径；文件名叫什么都行，认内容）

输出（写到 --out）：
    document.md          `[第 N 页]` + 逐块正文
    figures.json         图片索引（导出层本来就在读这个名字，所以它不用改）
    mineru.audit.json    丢了什么 + 块统计 + 页↔行号↔块 的映射 + 版本 + 输入 sha256
    extract.json         engine = mineru-cloud-<版本>

三条硬规则（不是风格偏好，是防"静默产出错东西"）：
  1. **独立公式写成单行 `$$...$$`**：`denoise()` 会把"≤40 字、出现在 ≥3 页"的行
     当成跨页重复的页眉删掉；拆成三行的 `$$` 正好中招，公式会无声消失。
  2. **标题必须裸行、不带 `#`**：`heading_match()` 不认 `# ` 前缀（`#` 不在 `[§$S]` 里），
     带了 `#` 的标题会被当普通正文，全书的层级就没了。
  3. **页码/页眉不进正文，但必须记进 audit**：MinerU 明确列出 `discarded_blocks`，
     这个"丢得可查"的好处要照抄。
  4. **相邻公式之间插一个空格**：两个相邻的行内公式直接拼会得到 `$A$$B$`，
     渲染器会把中间那段当显示公式吞掉（实测：一处公式渲染失败 + 13 个内部槽位泄漏）。
  5. **块类型要跟上 MinerU 的变化**：全本跑出来才见到的 `index`（目录）、`table`、
     `chart`、`list`、`ref_text` —— `index` 整块丢（否则整份目录变章节结构）、
     `table`/`chart` 按**图**处理（它们的内容就是一张图片，按文本处理会渲染成空行、静默丢内容）。

版本门（见设计稿第四节）：只认白名单里的版本；认不出就**中文报错、一个文件都不写**。
MinerU 4.0 换了契约（`middle_json.json` / `schema: docvortex.middle`），
官方明确说 `_backend`/`pdf_info`/`_version_name` 不属于新契约，所以必须认版本。
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "lib"))          # lib/

from files import sha256_file, write_json                  # noqa: E402
from source_blocks import SourceBlocks, validate_layout, running_headers, OUTPUT_FILES  # noqa: E402

from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[3]

SCHEMA_VERSION = "1.0"
GENERATOR = "aiforstudy.parse.mineru"
PAGE_SCALE = 2.0                      # MinerU 的 pt → 我们页图像素（跟图片层同一比例）
SUPPORTED_VERSIONS = ("3.4.4",)       # 白名单：新版字段要人工确认过才放开
BLOCK_KINDS = ("text", "title", "interline_equation", "image")
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
MATH_WRAP = re.compile(r"^\s*\${1,2}(?P<body>.*?)\${1,2}\s*$", re.S)
TOC_MARK = re.compile(r"^\s*(目\s*录|目\s*次|Contents|CONTENTS)\s*$", re.I)
# 目录条目的样子：标题 + 页码收尾（MinerU 常把引导点压成空格，所以这里只要求**至少一个**分隔符）
#
# ⚠️ 页码**可能在括号里**（2026-09-27 实测抓出，《复变函数》整段目录因此漏掉）：
#    它的条目写成 `5.2 基级数 …… (82)` —— 分隔符后面跟的是 `(`，不是裸数字。
#    原正则 `[\s.．·]{1,}\d{1,4}\s*$` 只认裸数字，于是**跨页目录的续页整段漏掉**，
#    条目被当成章节标题 → 那本书凭空多出 4 个"章"（CH02–CH05，标题是 `留数及其应用 …… (107)` 这种）。
#    同时把中文省略号 `…`/`⋯` 也补进分隔符类（它就是这本书用的引导符）。
# ⚠️ 页码还**可能是罗马数字**（2026-09-27 实测抓出，《复变函数》因此丢了第 1 章）：
#    它的前置材料用罗马数字编页，而**第 1 章就落在里面** ——
#    `第1章 复数和平面点集 …… (Ⅰ)` / `1.2 平面点集 …… (Ⅴ)` 这 5 行整行认不出，
#    于是对账基准里少了第 1 章，体检表显示"目录说 8 章 / 解析出 9 章"（**解析是对的、基准错了**）。
#    这是通用情形（前置材料用罗马数字编页很常见），不是某一本书的怪癖。
PAGE_TOKEN = r"(?:\d{1,4}|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]{1,4}|[ivxlcdmIVXLCDM]{1,7})"
LISTING_LINE = re.compile(
    rf"^\s*\S.{{0,90}}?[\s.．·…⋯]{{1,}}[（(]?\s*{PAGE_TOKEN}\s*[）)]?\s*$")

# ── 区域标记（**MinerU 自己把这些行标成了 `title`**，所以判据来自排版而不是猜）──────
#
# 为什么要有区域（2026-09-27 全流程试跑实测）：一本书里只有一部分是"教材正文"，
# 其余是章前材料 / 目录 / 习题 / 答案。它们**都不是教案单元**，但长得跟正文标题一样
# （目录条目就是 `第2章 留数及其应用`、答案页就是 `第1章`）。
# 结果实测：一本 **9 章**的书被解析成 **17 章** —— 多出来的 8 个分别是
# 前言段 1 + 目录条目 4 + 答案页 3，白白跑了 43 个单元的生成与审核。
REGION_MARKS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("answer", re.compile(r"^\s*(?:Answers?|Solutions?)(?:\s+\d+(?:[.\-]\d+)*)?\s*$", re.I)),
    ("exercise", re.compile(r"^\s*(?:(?:Review|Supplementary)\s+)?(?:Exercises?|Problems?)"
                            r"(?:\s+\d+(?:[.\-]\d+)*)?\s*$", re.I)),
    ("answer", re.compile(r"^\s*(习题参考答案|参考答案|习题答案|答案|附录\s*[:：]?\s*答案)\s*$")),
    # ⚠️ 习题区标记必须**认编号挂在后面的写法**（2026-09-27 实测《数分讲义第二册》）：
    #    它的节末习题写成 `习题 8.1`、章末写成 `第 8 章综合习题`，原正则只认光秃秃的
    #    `习题`/`习题一`，于是**一个习题区都没划出来**：习题层抽出 0 条，
    #    而 `第 8 章综合习题` 因为带「第N章」被当成章 —— 那本书凭空多出 6 个假章。
    ("exercise", re.compile(
        r"^\s*(?:(?:第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇])\s*)?"
        r"(?:综合习题|习题|练习|复习题|总习题)\s*[\d.．\-—]*"
        r"[一二三四五六七八九十百零〇]*\s*$")),
    ("toc", re.compile(r"^\s*(目\s*录|目\s*次|Contents|CONTENTS)\s*$", re.I)),
    ("back", re.compile(r"^\s*(参考文献|参考书目|索引|后记|跋|致谢|附录\s*[A-Za-z一二三四五六七八九十\d]?)\s*$")),
    # ⚠️ `绪论`/`引言` 是**正文开始**，不是章前材料（2026-09-27 实测《数值计算方法》）：
    #    它排在目录之后、第 1 章之前，里面是 `0.1 数值计算方法与算法`、`0.2 误差与有效数字`、
    #    `0.3 矩阵和向量范数` —— 实打实的教学内容（约 13 页）。
    #    原状态机只在「第N章」才进正文，于是这 13 页被判成章前材料**整段丢掉**。
    # ⚠️ 必须容忍**带副标题**的写法（2026-09-28 实测《电磁学》抓出）：
    #    那本书的绪论章标题是 `绪论 电磁学的建立和应用`（副标题同在一行），
    #    原正则要求整行只有"绪论" → 认不出 → 绪论整章（含 0.1/0.2/0.3 三节）
    #    被当成章前材料排除，**整章从结构里消失**（体检表报"目录说 12 章 / 解析出 11 章"）。
    ("body", re.compile(r"^\s*(绪\s*论|引\s*言|导\s*论|绪\s*言)(\s+\S.*)?$")),
    ("front", re.compile(r"^\s*(内容简介|图书在版编目.*|总序|序言|序|前言|编写说明|使用说明|"
                         r"出版说明|修订说明)\s*$")),
)
#: 章标题（有标题的那种）：它**结束**章前材料/习题区/目录区，回到正文
CHAPTER_TITLE = re.compile(r"^\s*第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇]\s*\S")
#: **节标题**（`§1.2 xxx` / `1.2 xxx`）：它同样结束习题区。
#: ⚠️ 为什么习题区不能只等下一个章（2026-09-27 实测《数分讲义第二册》）：
#:    那本书的习题是**每节末尾**一道（`习题 8.1` 在 §8.1 之后、§8.2 之前），
#:    只等下一个「第N章」的话，§8.2～§8.4 的正文会全被划进习题区丢掉。
SECTION_TITLE = re.compile(r"^\s*(?:§\s*)?\d+(?:\.\d+)+\s*\S")


#: 适配层**声明**：控制字符/U+FFFD 由我们来剥（preflight 的 F2 见到这个声明会从"拦"降为"提醒"）。
#: 为什么必须剥（2026-09-29 实测《Basic Analysis》）：那份 PDF 的字体编码有问题，
#: MinerU 输出里有 **1680 处 U+FFFD**；不剥的话原文里会混进看不见的字符，
#: 渲染时还会和内部占位符撞车。**剥掉必须记账**（H7/R-b：改动要说出来）。
STRIPS_CONTROL = True
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffd]")
_control_stats: dict = {"count": 0, "sample": []}


def _span_text(span: dict) -> str:
    """span 文本（顺手剥掉控制字符/U+FFFD 并记账）。**是 adapt 里读 span 文本的唯一入口。**

    ⚠️ 这里必须**直接读** `span["content"]` —— 2026-09-29 用批量替换加这个函数时，
    它把自己体内那句也替换成了 `_span_text(span)`，搞出无限递归。
    """
    text = span.get("content") or ""
    if not text:
        return text
    cleaned, hits = _CONTROL_RE.subn("", text)
    if hits:
        _control_stats["count"] += hits
        if len(_control_stats["sample"]) < 10:
            _control_stats["sample"].append(
                [hex(ord(c)) for c in set(_CONTROL_RE.findall(text))])
    return cleaned


def _spans_of(page_region: dict[int, str]) -> list[dict]:
    """把"每页区域"折成区间（人看的）。从末端派生，不做增量记账 —— 增量那种写法会写重复。"""
    spans: list[dict] = []
    for page in sorted(page_region):
        region = page_region[page]
        if spans and spans[-1]["region"] == region:
            spans[-1]["end_page"] = page
        else:
            spans.append({"region": region, "start_page": page, "end_page": page})
    return spans


#: 目录条目的层级提示：`第N章` → 1、`N.M` → 2、`N.M.K` → 3
TOC_CHAPTER = re.compile(r"^\s*第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇]\s*\S")
TOC_SECTION = re.compile(r"^\s*\d+(?:\.\d+)+\s*\S")
# ⚠️ 分隔符只要 **1 个**就够（2026-09-27）：线性代数的目录是 `第一章 向量 7` ——
#    页码前只有一个空格，写成 `{2,}` 会把它的目录整份漏掉（对账因此显示"声明 0 章"）。
#    这里不担心误判：能走到这一行的，已经先过了 `LISTING_LINE`（整行以页码收尾）。
TOC_TAIL = re.compile(rf"[\s.．·…⋯]{{1,}}[（(]?\s*({PAGE_TOKEN})\s*[）)]?\s*$")

#: 罗马数字 → 整数。前置材料用罗马数字编页时目录页就长这样（`(Ⅰ)`、`(Ⅴ)`）。
_ROMAN_TABLE = {"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V", "Ⅵ": "VI",
                "Ⅶ": "VII", "Ⅷ": "VIII", "Ⅸ": "IX", "Ⅹ": "X", "Ⅺ": "XI", "Ⅻ": "XII"}
_ROMAN_VALUE = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def page_number(token: str) -> int | None:
    """目录里的页码 → 整数；**罗马数字也认**，认不出返回 None。

    为什么单独一个函数：目录页码要拿去算"目录声明的章序"，罗马数字必须能转成数。
    `Ⅴ` 和 `V` 两种写法都要认（OCR 两种都会出）。
    """
    text = str(token or "").strip()
    if text.isdigit():
        return int(text)
    roman = _ROMAN_TABLE.get(text.upper()) or text.upper()
    if not roman or any(ch not in _ROMAN_VALUE for ch in roman):
        return None
    total = 0
    for index, char in enumerate(roman):
        value = _ROMAN_VALUE[char]
        nxt = _ROMAN_VALUE[roman[index + 1]] if index + 1 < len(roman) else 0
        total += -value if value < nxt else value
    return total or None


def repair_pdf_text(text: str) -> str:
    """修 PDF 文字层/书签里的 **GBK 乱码**。

    实测（2026-09-27，《数分讲义第二册》）：书签是 `¿Õ¼ä½âÎö¼¸ºÎ`，按 latin-1 读回来
    再按 GBK 解就是 `空间解析几何` —— 这类"整体被当成 latin-1"的乱码**能修**。

    ⚠️ 但**正文里的中文修不了**：那本书正文字体是 CID 子集且缺 `ToUnicode`，
    汉字映射到任意码位（修出来是 `榤)跘` 这种），只能靠 OCR —— 这里只处理前一种。
    """
    if not text or any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return text                       # 已经是正常中文（或纯 ASCII）→ 不动
    for encoding in ("gbk", "gb18030"):
        try:
            fixed = text.encode("latin-1", "ignore").decode(encoding, "ignore")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if any("\u4e00" <= ch <= "\u9fff" for ch in fixed):
            return fixed
    return text


def read_pdf_outline(pdf_path: str | Path | None) -> list[dict]:
    """读 PDF 的**内嵌书签**（这就是"书自己声明的结构"，比文字层可靠得多）。

    实测两本书都有：数值计算方法 109 条、数分讲义 155 条。
    有书签时它就是最权威的目录 —— 既当骨架校验基准，也能在没有印刷目录页时兜底。
    """
    if not pdf_path:
        return []
    try:
        import pymupdf                                    # 延迟导入：没装也不影响别的书
    except ImportError:
        return []
    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception:                                     # noqa: BLE001
        return []
    out: list[dict] = []
    try:
        for level, title, page in doc.get_toc(simple=True) or []:
            text = repair_pdf_text(str(title or "")).strip()
            if text:
                out.append({"text": text, "level": int(level), "pdf_page": int(page)})
    finally:
        doc.close()
    return out


# ── 非正文判据：**下沉到 `lib/non_teaching.py`**（D-19，2026-09-29）──────────
# 原来 adapt 与 structure 各写一套，英文判据只加在了解析侧 → 体检"章数对账"的
# 基线（`toc.json` 的 chapters）与解析侧不一致，报假警报。
# `is_book_matter` 是历史名字，共享模块里作为同义词保留。
try:
    from lib.non_teaching import CHAPTER_PREFIX as _CHAP_PREFIX_TMP  # noqa: F401
    from lib.non_teaching import is_book_matter
except ImportError:                      # `cli.py` 以顶层模块方式导入本文件时的兜底
    import sys as _sys
    from pathlib import Path as _Path
    _root = _Path(__file__).resolve().parents[3]
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    from lib.non_teaching import is_book_matter            # noqa: F811

def normalize_outline_levels(entries: list[dict]) -> tuple[list[dict], int]:
    """PDF 书签的层级**按标题形状归一化**，返回 `(新条目, 位移量)`。

    为什么需要（2026-09-27 实测两本书都不一样）：
      * 《数值计算方法》：书签根节点是 `目录`，于是**章在 lv2**、节 lv3、小节 lv4
        —— `level == 1` 的条目只有一个 `目录`，`chapters` 因此完全没用。
      * 《数分讲义第二册》：章就在 lv1，但标题**不带**「第N章」（叫 `空间解析几何`），
        不能只靠标题形状判层级。
    所以试位移量 `s ∈ {0,1,2}`，取"书签层级 - s"与**标题形状层级**最一致的那个：
    带编号的条目（`第N章`→1、`N.M`→2、`N.M.K`→3）给出约束，不带编号的（章名那种）
    跟着同一个 s 走。并列时取小的 s（少动为上）。
    """
    if not entries:
        return entries, 0

    def shape_level(text: str) -> int | None:
        title = str(text or "").strip()
        if is_book_matter(title):        # 前言/附录/习题集：不提供层级约束（见 is_book_matter）
            return None
        if TOC_CHAPTER.match(title):
            return 1
        section = re.match(r"^\s*(\d+(?:\.\d+)+)\s*\S", title)
        if section:
            return len(section.group(1).split("."))
        return None

    scored: list[tuple[int, int]] = []
    for shift in (0, 1, 2):
        hits = 0
        for item in entries:
            want = shape_level(item.get("text"))
            if want is None:
                continue
            if int(item.get("level") or 0) - shift == want:
                hits += 1
        scored.append((-hits, shift))
    shift = min(scored)[1]

    out: list[dict] = []
    for item in entries:
        moved = dict(item)
        moved["level"] = max(0, int(item.get("level") or 0) - shift)
        out.append(moved)
    return out, shift


def _pdf_from_ingest(out_dir: Path) -> Path | None:
    """从 `ingest.json` 里找这次入库用的 PDF（适配时要读它的书签）。"""
    payload = None
    try:
        payload = json.loads((Path(out_dir) / "ingest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw = str((payload or {}).get("pdf") or "")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path if path.is_file() else None


def _toc_entry(row: str, page_no: int) -> dict | None:
    """把一行目录条目解析成 `{text, page, level}`；不像条目就返回 None。"""
    text = str(row or "").strip()
    if len(text) < 2:
        return None
    tail = TOC_TAIL.search(text)
    if not tail:
        return None
    title = text[:tail.start()].strip(" .．·…⋯")
    if not title:
        return None
    level = 1 if TOC_CHAPTER.match(title) else (2 if TOC_SECTION.match(title) else 3)
    return {"text": title, "toc_page": page_number(tail.group(1)),
            "toc_page_label": tail.group(1), "level": level, "from_page": page_no}


def region_mark(text: str) -> str | None:
    """这一行是不是区域标记；是就返回区域名。"""
    stripped = str(text or "").strip()
    if not stripped:
        return None
    for name, pattern in REGION_MARKS:
        if pattern.match(stripped):
            return name
    return None


class MinerUFormatError(ValueError):
    """认不出产物格式，或者版本不在白名单里 —— 一律停下，不猜。"""


# ── 版本门 ──────────────────────────────────────────────────────────────
def detect_format(payload: dict) -> dict:
    """认出这是哪一代 MinerU 产物；认不出就抛错（调用方负责一个文件都不写）。"""
    if not isinstance(payload, dict):
        raise MinerUFormatError("MinerU 产物不是一个 JSON 对象。")
    if "pdf_info" in payload:                     # 3.x legacy layout.json
        version = str(payload.get("_version_name") or "")
        if version not in SUPPORTED_VERSIONS:
            raise MinerUFormatError(
                f"这份 MinerU 产物的版本是「{version or '未知'}」，不在已知白名单 {SUPPORTED_VERSIONS} 里。\n"
                "新版可能换了字段（MinerU 4.0 已改成 middle_json 的 pages/blocks，官方明确说 "
                "_backend/pdf_info/_version_name 不属于新契约）。\n"
                "为了不悄悄产出错东西，这里直接停下 —— 一个文件都没写。\n"
                "确认过字段没变之后，把版本号加进 adapt.py 的 SUPPORTED_VERSIONS 再跑。")
        return {"family": "legacy-layout", "version": version,
                "backend": payload.get("_backend"), "effort": payload.get("_effort"),
                "ocr_enable": payload.get("_ocr_enable")}
    schema = str(payload.get("schema") or "")
    if schema.startswith("docvortex."):
        raise MinerUFormatError(
            f"这是 MinerU 4.0 的产物（schema={schema}），字段跟 3.x 的 layout.json 不是一套。\n"
            "适配块目前只认 3.x 的 layout.json。要用 4.0，先按设计稿第四节补一层字段映射。")
    raise MinerUFormatError(
        "认不出这是不是 MinerU 产物：既没有 pdf_info（3.x layout.json），也没有 schema=docvortex.*（4.0）。")


def find_layout(mineru_dir: str | Path) -> Path:
    """找 layout.json。给目录也行、直接给那个 JSON 文件也行（文件名叫什么都行，认内容）。"""
    path = Path(mineru_dir)
    if path.is_file():
        return path
    if not path.is_dir():
        raise MinerUFormatError(f"找不到 MinerU 产物目录或文件：{path}")
    direct = path / "layout.json"
    if direct.is_file():
        return direct
    candidates = sorted(path.glob("*layout*.json")) or sorted(path.glob("*.json"))
    for candidate in candidates:
        if candidate.name.endswith(".fetch.json"):
            continue
        try:
            head = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if '"pdf_info"' in head:                  # 只认内容，不认文件名
            return candidate
    raise MinerUFormatError(f"在 {path} 里没找到带 pdf_info 的 layout.json（3.x 格式）。")


# ── 块渲染 ──────────────────────────────────────────────────────────────
def math_body(text: str) -> str:
    """去掉外层 `$` / `$$`（MinerU 的公式 span 里**没有**定界符，但万一带了也不许出现 `$$$$`）。"""
    match = MATH_WRAP.match(text or "")
    return (match.group("body") if match else (text or "")).strip()


def render_span(span: dict) -> str:
    kind = str(span.get("type") or "")
    content = _span_text(span)
    if kind == "text":
        return content
    if kind == "inline_equation":
        body = math_body(content)
        return f"${body}$" if body else ""
    if kind == "interline_equation":
        body = math_body(content)
        return f"$${body}$$" if body else ""
    if kind == "image":                      # 图走 figures.json，不进正文
        return ""
    return content


def render_line(line: dict) -> str:
    r"""把一行里的 span 拼起来。

    **相邻公式之间必须插一个空格**（硬规则 4）：MinerU 常把 `\sigma(1)=2,\sigma(2)=3,`
    和 `\sigma(3)=1` 拆成两个相邻的行内公式 span，直接拼起来就是 `$A$$B$` ——
    渲染器的显示公式正则 `\$\$[\s\S]+?\$\$` 会把这里的 `$$` 当显示公式起点，
    把中间那一段整个吞掉（实测：第 1.7.4 节一处公式直接渲染失败、13 个内部槽位泄漏到页面上）。
    """
    out: list[str] = []
    for span in line.get("spans") or []:
        piece = render_span(span)
        if not piece:
            continue
        if out and out[-1].endswith("$") and piece.startswith("$"):
            out.append(" ")
        out.append(piece)
    return "".join(out)


def explode(text: str) -> list[str]:
    """把一个块渲染出来的文本拆成**物理行**。

    实测踩过：MinerU 的一个 span 里可以塞进多个换行（目录页就是一整块 span、内容里
    带着 `\\n`）。**不拆的话**：写进文件的物理行数（717）比 audit 里记的（606）多，
    `check_document()` 检查的是内存里的逻辑行、不是文件里真正的行 ——
    三条硬规则就"看着过了"。所以这里必须拆开，保证 audit 的行号 == structure 看到的行号。
    """
    return text.splitlines()


def block_lines(block: dict) -> list[str]:
    """一个块 → 若干输出行。

    - text：MinerU 的每个 line 出一行（跟 OCR 路径的粒度一致，结构层的合折行逻辑照旧生效）
    - title：合成一行（裸行，不带 `#`）
    - interline_equation：**单行** `$$...$$`

    返回的每一项都不含换行符（见 `explode`）。
    """
    kind = str(block.get("type") or "")
    lines = block.get("lines") or []
    if kind == "title":
        joined = "".join(render_line(line) for line in lines).strip()
        return [piece for piece in explode(joined) if piece.strip()]
    if kind == "interline_equation":
        body = ""
        for line in lines:
            for span in line.get("spans") or []:
                if span.get("type") == "interline_equation" and (_span_text(span)).strip():
                    body = math_body(span["content"])
                    break
            if body:
                break
        if not body:
            for line in lines:
                body = math_body("".join(render_span(s) for s in line.get("spans") or []))
                if body:
                    break
        return [f"$${body}$$"] if body else []
    out: list[str] = []
    for line in lines:
        for piece in explode(render_line(line).rstrip()):
            if piece.strip():
                out.append(piece)
    return out


def image_parts(block: dict) -> tuple[str | None, str]:
    """图块 → (image_path, 图题)。MinerU 把图切成 image_body / image_caption 两块。"""
    return image_body_path(block), " ".join(caption_texts(block))


BODY_TYPES = ("image_body", "table_body", "chart_body")
CAPTION_TYPES = ("image_caption", "table_caption", "chart_caption")
FIGURE_BLOCK_TYPES = ("image", "table", "chart")


def image_body_path(block: dict) -> str | None:
    """图块的**内容**在哪。没有 `*_body` 的块（只剩图题）不算一张图。

    认三种体：`image_body`（插图）/ `table_body`（表格）/ `chart_body`（图/表）。
    后两种是**整本跑出来才见到的**（前 40 页一个都没有）：MinerU 把表格和图
    也做成一张图片，所以它们该按"图"处理 —— 之前按文本处理的结果是**渲染成空行、静默丢内容**
    （实测：全本 2 张表 + 1 张图，共 3 块）。
    """
    for sub in block.get("blocks") or []:
        if str(sub.get("type") or "") not in BODY_TYPES:
            continue
        for line in sub.get("lines") or []:
            for span in line.get("spans") or []:
                if span.get("image_path"):
                    return str(span["image_path"])
    return None


def caption_texts(block: dict) -> list[str]:
    """这一块自己带的图题（可能不止一条 —— 实测第 29 页把三张图的图题全挂在最右边那块上）。"""
    out: list[str] = []
    for sub in block.get("blocks") or []:
        if str(sub.get("type") or "") not in CAPTION_TYPES:
            continue
        text = "".join(_span_text(span)
                       for line in sub.get("lines") or []
                       for span in line.get("spans") or []).strip()
        if text:
            out.append(text)
    return out


# 图题也认表格题（`表 1.1 …`）：全本实测有 2 张表，只认"图"会把表题当不存在，
# 于是"1 条图题 + 2 个图体"被误判成"一张图的多个面板"，表格被并进图里。
FIG_CAPTION = re.compile(r"[图表]\s*\d+(?:[.．]\d+)*")


def plan_page_figures(page: dict) -> dict[int, dict]:
    """一页里"哪一块才是图、它的图题是哪条"—— 用整页的图题池重新配对。

    为什么要在**页**这一级重配（三条都是实测）：
      • 第 29 页：三张并排的小图，MinerU 把 `图 1.11 / 图 1.12 / 图 1.13` **三条图题
        全挂在最右边那块**上，另外两块一条都没有 → 不给它重配就是"两张没名字、
        一张挂着三个名字"。
      • 第 15 页：左边那张图只剩图题 `(b) 左手系`、**没有 image_body** →
        不能凭空给它造一张图（曾经的 bug：按页码前缀兜底找图，会配到隔壁那张）。
      • 第 21 页：图 1.9 分 (a)(b) 两个面板、两块都有图 → 图题给带 `图 x.y` 的那块，
        `(a)`/`(b)` 记成 `panel_label`，不冒充图题。

    返回 `{id(块): {"caption":…, "panel_label":…, "figure_caption":bool, "merge_with":[…]}}`。

    **D6-A（2026-09-24 拍板）**：同一张图被 MinerU 拆成多个面板时（第 15 页图 1.5、第 21 页图 1.9），
    把这些面板**并回一张图**：bbox 取并集，渲染时从整页图裁（`page_image`），
    这样产物还是"一张图一条图题"，跟书上版式一致。
    并框有几何护栏：只有这些块**并排**（竖直方向有一半以上重叠）才并 ——
    上下叠放的两个真图不会被误并成一张。
    """
    blocks = [b for b in (page.get("para_blocks") or page.get("preproc_blocks") or [])
              if str(b.get("type") or "") in FIGURE_BLOCK_TYPES]
    bodies = [b for b in blocks if image_body_path(b)]
    pool = [cap for b in blocks for cap in caption_texts(b)]
    figure_captions = [cap for cap in pool if FIG_CAPTION.search(cap)]
    labels = [cap for cap in pool if not FIG_CAPTION.search(cap)]

    plan: dict[int, dict] = {id(b): {"caption": None, "panel_label": None, "figure_caption": False,
                                     "merge_with": []} for b in blocks}
    merged = False
    # 并框要三件事同时成立：只有一条图题、有多个图体、它们并排，**而且能看到面板标签**。
    # 最后那条护栏是实测加的：没有它时，"一页里一张表 + 一张图（表题没被认出）"
    # 会被误判成"一张图的多个面板"，表格被并进图里丢掉。
    if (len(figure_captions) == 1 and len(bodies) > 1 and side_by_side(bodies)
            and len(labels) >= len(bodies) - 1):
        merged = True
        for index, body in enumerate(bodies):
            plan[id(body)] = {"caption": figure_captions[0] if index == 0 else None,
                              "panel_label": None, "figure_caption": index == 0,
                              "merge_with": [id(b) for b in bodies if b is not body]}
    elif bodies and len(figure_captions) == len(bodies):          # 正常情况（含第 29 页）
        for body, caption in zip(bodies, figure_captions):
            plan[id(body)] = {"caption": caption, "panel_label": None, "figure_caption": True,
                              "merge_with": []}
    elif len(bodies) == 1 and figure_captions:                   # 一图多题（含第 15 页单块）
        plan[id(bodies[0])] = {"caption": figure_captions[0], "panel_label": None,
                               "figure_caption": True, "merge_with": []}
    else:                                                        # 配不上就退回块自己的图题
        for body in bodies:
            own = [cap for cap in caption_texts(body) if FIG_CAPTION.search(cap)]
            plan[id(body)] = {"caption": own[0] if own else None, "panel_label": None,
                              "figure_caption": bool(own), "merge_with": []}
    for body, label in zip(bodies, labels):                      # 面板标签按左右顺序贴
        if plan[id(body)]["panel_label"] is None and label:
            plan[id(body)]["panel_label"] = label
    return plan, merged


def union_bbox(boxes) -> list[float]:
    """多个框的并集（D6-A 面板并框用）。"""
    rows = [[float(v) for v in (box or [])] for box in boxes]
    rows = [row for row in rows if len(row) == 4]
    if not rows:
        return []
    return [round(min(row[0] for row in rows), 2), round(min(row[1] for row in rows), 2),
            round(max(row[2] for row in rows), 2), round(max(row[3] for row in rows), 2)]


def side_by_side(bodies: list[dict]) -> bool:
    """这些图块是不是**并排**放的（竖直方向至少一半重叠）。

    只用来给并框加护栏：上下叠放的两个真图不算并排，不许并成一张。
    """
    boxes = []
    for body in bodies:
        bbox = body.get("bbox") or []
        if len(bbox) != 4:
            return False
        boxes.append([float(v) for v in bbox])
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            _, y0a, _, y1a = boxes[i]
            _, y0b, _, y1b = boxes[j]
            overlap = min(y1a, y1b) - max(y0a, y0b)
            smaller = min(y1a - y0a, y1b - y0b)
            if smaller <= 0 or overlap < smaller * 0.5:
                return False
    return True


def resolve_image(mineru_dir: Path, image_path: str, page_idx: int) -> Path | None:
    """在 <mineru 目录>/images/ 里找到这张图。

    认三种命名，按可靠程度排：
      1. image_path 的文件名（3.x 云端就是 `<sha256>.jpg`，ZIP 里同名）
      2. 同名的其它常见扩展名
      3. 4.0 的命名 `page_<page_idx>_<parent_type>_<parent_index>.<ext>`
    """
    images = mineru_dir / "images"
    if not images.is_dir():
        return None
    name = str(image_path).rsplit("/", 1)[-1].strip()
    if name:
        direct = images / name
        if direct.is_file():
            return direct
        stem = Path(name).stem
        for ext in ("jpg", "jpeg", "png", "webp"):
            candidate = images / f"{stem}.{ext}"
            if candidate.is_file():
                return candidate
    # A page number is not an image identity: several unrelated crops can share
    # the page. Missing exact paths must remain missing, never borrow a crop.
    return None


# ── 目录页：MinerU 的目录条目我们的规则认不出来，所以在这里处理掉 ────────
def block_text(block: dict) -> str:
    return "".join(_span_text(span)
                   for line in block.get("lines") or []
                   for span in line.get("spans") or [])


def listing_rows(block: dict) -> list[str]:
    """这个块里的行（供目录形状判定用）。"""
    rows: list[str] = []
    for line in block.get("lines") or []:
        rows.extend(piece for piece in explode(render_line(line)) if piece.strip())
    return rows


def listing_ratio(block: dict) -> float:
    """块里"像目录条目"的行占比（0～1）。续页判定用它，比"全都像"宽容一档。"""
    rows = listing_rows(block)
    if not rows:
        return 0.0
    return sum(1 for row in rows if LISTING_LINE.match(row)) / len(rows)


def is_listing_block(block: dict) -> bool:
    """这一块是不是"目录清单"。

    实测：MinerU 把一整页目录塞进**一个 text 块**，行与行之间是 `\\n`
    （第 3 页那块就是 `第一章 向量 7  \\n1.1 向量的线性运算 7  \\n…`）。
    它的条目长这样：标题 + **空格** + 页码 —— 没有引导点，页码也不另起一行。
    结构层原有的目录规则（`TOC_ENTRY`/`DOT_LEADER`）是为 OCR 那种
    "点线 + 页码单独一行"写的，认不出这一种，于是整份目录被当成章节结构
    （实测：直接跑结构层会认出 **71 个标题**，而真值是 29 个）。

    所以在这里按 MinerU 自己的排版特征先判出来。判据保守：**每一行都以页码收尾**，
    且至少 3 行 —— 正文段落不可能长这样。
    """
    if str(block.get("type") or "") != "text":
        return False
    rows = listing_rows(block)
    if len(rows) < 3:
        return False
    return all(LISTING_LINE.match(row) for row in rows)


def is_listing_continuation(block: dict, *, min_rows: int = 2, ratio: float = 0.6) -> bool:
    """**目录续页**上的块判定：比 `is_listing_block` 宽容一档。

    为什么需要（2026-09-27 实测）：整页丢弃那条规则只看"这一页有没有 `目录/目次` 标记"，
    而目录**经常跨页**（本书 `目次` 在第 8 页，条目一直排到第 9–10 页）。
    续页没有标记，只能按形状判；而续页常常"只剩两行"（页码分页把它切碎了），
    于是原来的"≥3 行且全像"就漏了 —— 这正是 CH02–CH05 四个假章的来源。

    所以：**一旦确认进入目录区**，后续页放宽到"≥2 行且 60% 像"，直到某页不再像为止。
    """
    if str(block.get("type") or "") != "text":
        return False
    rows = listing_rows(block)
    if len(rows) < min_rows:
        return False
    return listing_ratio(block) >= ratio


def title_key(block: dict) -> str:
    """标题的比较键：折叠所有空白（页眉里的 `§1.7 集合与映射*` 和正文里的一模一样）。"""
    return re.sub(r"\s+", "", block_text(block))


def toc_blocks(page: dict, *, continuing: bool = False) -> list[dict]:
    """这一页里该丢的目录块（正文一个字节不动）。

    两条判据，先看强的：
      1. **这一页有 `目录/目次` 标记标题**（MinerU 自己认出来的）→ 这一页的 title/text 全丢。
         为什么必须整页丢：目录条目和正文标题**长得一模一样**，只按行过滤会把整份目录
         当成章节结构。实测第 6 页那种"只剩两行"的续页，光靠行的形状判不出来。
      2. 没有标记的续页：整块都是"条目 + 页码"形状才丢。保守一点，宁可漏丢也不误伤正文。

    ⚠️ `continuing=True` 表示**上一页已经是目录**（2026-09-27 实测抓出）：
    目录跨页时，续页没有标记，而"≥3 行且全像"这条会把只剩两行的续页漏掉 ——
    《复变函数》的 CH02–CH05 四个假章就是这么来的。所以续页放宽成 `is_listing_continuation`。
    """
    blocks = page.get("para_blocks") or page.get("preproc_blocks") or []
    has_marker = any(str(block.get("type") or "") == "title" and TOC_MARK.match(block_text(block))
                     for block in blocks)
    dropped: list[dict] = []
    for block in blocks:
        kind = str(block.get("type") or "")
        if kind in FIGURE_BLOCK_TYPES:                        # 目录页上的图表照留
            continue
        if kind == "index":
            # MinerU 的 pipeline 后端把目录清单标成 `index`（网页端的 hybrid 后端标成 text）。
            # 结构层认不出它 → 整份目录会被当成章节结构（实测前 40 页因此多出 690 个汉字）。
            dropped.append(block)
            continue
        if has_marker and kind in ("title", "text"):
            dropped.append(block)
        elif kind == "title" and TOC_MARK.match(block_text(block)):
            dropped.append(block)
        elif is_listing_block(block):
            dropped.append(block)
        elif continuing and is_listing_continuation(block):
            dropped.append(block)
    return dropped


# ── 自检：三条硬规则必须成立 ────────────────────────────────────────────
def check_document(lines: list[str], *, code_lines: "list[list[int]] | None" = None) -> list[str]:
    problems: list[str] = []
    # 代码块的行**不参与三条硬规则**：它们是代码（`#include`、不配对的 `$`），
    # 不是 markdown 正文 —— 硬规则是给正文写的。
    skip: set[int] = set()
    for span in code_lines or []:
        try:
            skip.update(range(int(span[0]), int(span[1]) + 1))
        except (TypeError, ValueError, IndexError):
            continue
    for number, raw in enumerate(lines, 1):
        if number in skip:
            continue
        text = raw.strip()
        pure_display = text.startswith("$$") and text.endswith("$$") and text.count("$$") == 2
        if "$$" in text and not pure_display:
            problems.append(
                f"第 {number} 行里出现了 $$，但它不是一条独立的显示公式：{text[:40]}"
                f"（两个相邻的行内公式会拼出 $$，渲染器会把中间那段当显示公式吞掉）")
        if text == "$$":
            problems.append(f"第 {number} 行是单独一行的 $$（denoise 会当跨页页眉删掉，公式会消失）")
        elif text.startswith("$$") and not text.endswith("$$"):
            problems.append(f"第 {number} 行的显示公式没有在同一行收尾：{text[:40]}")
        elif text.startswith("$") and text.count("$") % 2:
            problems.append(f"第 {number} 行的 $ 是奇数个：{text[:40]}")
        if text.startswith("#"):
            problems.append(f"第 {number} 行以 # 开头（heading_match 不认，标题会变成正文）")
    return problems


@dataclass
class Adapted:
    document: str = ""
    lines: list[str] = field(default_factory=list)
    figures: dict = field(default_factory=dict)
    audit: dict = field(default_factory=dict)
    extract: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    source_blocks: dict = field(default_factory=dict)


# ── 主流程 ──────────────────────────────────────────────────────────────
def adapt(mineru_dir: str | Path, out_dir: str | Path, *,
          book_title: str | None = None, note: str = "",
          copy_images: bool = True, dry_run: bool = False,
          pdf: str | Path | None = None) -> Adapted:
    """MinerU 产物 → document.md / figures.json / mineru.audit.json / extract.json。

    `dry_run=True` 时只算不写（用来先看统计对不对）。
    """
    mineru_dir = Path(mineru_dir)
    layout_path = find_layout(mineru_dir)
    base_dir = layout_path.parent
    payload = json.loads(layout_path.read_text(encoding="utf-8"))
    fmt = detect_format(payload)                       # 认不出会在这里抛错，下面一个文件都不会写
    try:
        validate_layout(payload)
        source = SourceBlocks(payload["pdf_info"], layout_path)
    except (ValueError, TypeError) as error:
        raise MinerUFormatError(str(error)) from error
    pages = payload.get("pdf_info") or []

    out_dir = Path(out_dir)
    lines: list[str] = []
    figures: list[dict] = []
    blocks_log: list[dict] = []
    discarded: list[dict] = []
    page_log: list[dict] = []
    warnings: list[str] = []
    by_type: dict[str, int] = {}
    discarded_by_type: dict[str, int] = {}
    dropped_toc = 0
    dropped_repeat = 0
    orphan_captions: list[str] = []
    merged_panels: list[dict] = []
    empty_figures: list[dict] = []
    merged_count = 0
    header_ids = running_headers(pages)
    inline_eq = 0
    missing_images: list[dict] = []
    # ── 区域状态机（见 `REGION_MARKS`）：一本教材里只有一部分是正文 ──────────────
    current_region = "front"        # 开卷默认是章前材料，遇到第一个「第N章 <标题>」才进正文
    toc_continuing = False          # 上一页是不是目录（跨页目录要靠它）
    region_before_toc = "front"     # 进目录之前是什么区（目录结束要回到这里）
    headings: list[dict] = []       # MinerU 认出来的标题（给结构层用）
    page_region: dict[int, str] = {}
    toc_entries: list[dict] = []    # **书自己声明的目录**（给结构层当校验基准）
    # 来自 `code` 块的行区间（给结构层：**这些行不许当标题**，但正文里照旧保留）。
    # ⚠️ 为什么（2026-09-27 实测《数值计算方法》）：伪代码里那些 `2.2 fx:=fx+tmp*y$_{i}$`、
    #    `2.1 选取非对角线按模最大元素 …` 长得跟编号标题一模一样，被当成三级标题进了单元
    #    （实测 3 个假单元）。代码块是 MinerU 明确标出来的，不用猜形状。
    code_lines: list[list[int]] = []
    _control_stats.update(count=0, sample=[])
    source.prepare_assets(base_dir, out_dir, resolve_image, copy_images=copy_images, dry_run=dry_run)

    def emit_figure_text(block, page_no):
        """Preserve all nested text at its raw location, including mislabelled headings.

        Figure caption pooling only affects figures.json. Reassigning those
        captions in document.md would give a source row another block's text.
        """
        nonlocal current_region, region_before_toc
        row = source.by_identity[id(block)]
        rendered_any = False

        def children(node, path=()):
            yield node, list(path)
            for index, child in enumerate(node.get("blocks") or []):
                yield from children(child, (*path, "blocks", index))

        for child, path in children(block):
            rendered = block_lines(child)
            if not rendered:
                continue
            first = len(lines) + 1
            for piece in rendered:
                mark = region_mark(piece)
                is_heading = (child.get("type") == "title" or bool(mark)
                              or bool(CHAPTER_TITLE.match(piece)) or bool(SECTION_TITLE.match(piece)))
                if is_heading:
                    if mark in ("answer", "exercise"):
                        current_region = mark
                    elif mark == "toc":
                        region_before_toc, current_region = current_region, "toc"
                    elif mark == "body" or CHAPTER_TITLE.match(piece):
                        current_region = "body"
                    elif current_region == "exercise" and SECTION_TITLE.match(piece):
                        current_region = "body"
                    headings.append({"page": page_no, "line": len(lines) + 1,
                                     "text": piece, "level": child.get("level"),
                                     "region": current_region, "source_block_id": row["block_id"],
                                     "source_path": path, "bbox": child.get("bbox")})
                lines.append(piece)
            row.setdefault("fragments", []).append({"source_path": path,
                "source_lines": [first, len(lines)], "bbox": child.get("bbox"),
                "original_type": child.get("type"), "region": current_region})
            rendered_any = True
        return rendered_any

    for page in sorted(pages, key=lambda p: int(p.get("page_idx") or 0)):
        page_idx = int(page.get("page_idx") or 0)
        page_no = page_idx + 1
        lines.append(f"[第 {page_no} 页]")
        start_line = len(lines) + 1

        page_blocks = page.get("para_blocks") or page.get("preproc_blocks") or []
        toc = {id(block) for block in toc_blocks(page, continuing=toc_continuing)}
        # 目录跨页：这一页丢了目录块，就把"还在目录里"传给下一页
        toc_continuing = bool(toc) or any(
            str(b.get("type") or "") == "title" and TOC_MARK.match(block_text(b))
            for b in page_blocks)
        # ⚠️ **目录区要有出口**（2026-09-27 实测《数值计算方法》）：
        #    原状态机进了 `toc` 之后只在「第N章」才出来，于是目录页之后的 `绪论`、
        #    `0.1`、`0.2`、`0.3` 全被算成目录区（第 11–28 页），**13 页正文被丢掉**。
        #    出口就一条：这一页已经没有任何目录块了 → 回到进目录之前的状态。
        if current_region == "toc" and not toc:
            current_region = region_before_toc
        page_region[page_no] = current_region
        figure_plan, _page_merged = plan_page_figures(page)   # 整页图题池重新配对 + D6-A 面板并框
        for block in page_blocks:
            kind = str(block.get("type") or "unknown")
            by_type[kind] = by_type.get(kind, 0) + 1
            block_index = block.get("index")
            first_line = len(lines) + 1

            # Running headers must be removed BEFORE any region transition.
            # Identical exercise titles in different chapters remain real titles.
            if id(block) in header_ids and id(block) not in toc:
                label = "title-repeat" if kind == "title" else "running-header"
                discarded_by_type[label] = discarded_by_type.get(label, 0) + 1
                discarded.append({"page": page_no, "type": label, "reason": "running_header",
                                  "text": block_text(block)[:200]})
                dropped_repeat += kind == "title"
                source.mark(block, disposition="discarded", reason="running_header", region=current_region)
                continue

            # 区域推进：**在丢弃判定之前**做，因为区域标记本身也可能是被丢的目录块
            if kind == "title":
                text_now = block_text(block)
                mark = region_mark(text_now)
                if mark == "answer":
                    if current_region != "answer":
                        current_region = "answer"
                elif mark == "exercise":
                    if current_region in ("front", "body"):
                        current_region = "exercise"
                elif mark == "toc":
                    if current_region in ("front", "body"):
                        region_before_toc = current_region
                        current_region = "toc"
                elif mark == "body":
                    # `绪论`/`引言`：正文开始（见 `REGION_MARKS` 里的说明）
                    if current_region in ("front", "toc"):
                        current_region = "body"
                elif mark == "front":
                    pass                                  # 章前材料：保持 front
                elif CHAPTER_TITLE.match(text_now):
                    # 「第N章 <标题>」= 正文开始，或习题区结束、下一章开始。
                    # ⚠️ 答案区里的 `第N章` **没有标题**（本书 p228：`第2章` 光秃秃一行），
                    #    所以用 `CHAPTER_TITLE` 要求后面必须跟着标题文字，避免把答案区分段当成新章。
                    if current_region in ("front", "exercise", "toc"):
                        current_region = "body"
                elif current_region == "exercise" and SECTION_TITLE.match(text_now):
                    # 节末习题（`习题 8.1` 在 §8.1 之后、§8.2 之前）：
                    # 遇到下一个**节标题**就回到正文，否则后面的正文会被吞进习题区。
                    current_region = "body"
            source.mark(block, region=current_region)

            if id(block) in toc:                     # 目录页：内容不进正文，但**记账**
                label = "index" if kind == "index" else "toc"
                discarded_by_type[label] = discarded_by_type.get(label, 0) + 1
                discarded.append({"page": page_no, "type": label,
                                  "text": block_text(block)[:400]})
                dropped_toc += 1
                source.mark(block, disposition="discarded", reason="table_of_contents")
                # **顺手把目录条目解析出来**：这是"书自己声明的结构"，
                # 拿它当校验基准（《复变函数》靠它才知道真值是 9 章）。
                for row in listing_rows(block):
                    entry = _toc_entry(row, page_no)
                    if entry:
                        toc_entries.append(entry)
                continue

            if kind == "title":
                # 记下这个标题（结构层要按"行号 → 区域"把非正文的标题剔掉）
                headings.append({"page": page_no, "line": first_line,
                                 "text": block_text(block).strip(),
                                 "level": block.get("level"),
                                 "region": current_region})

            if kind in FIGURE_BLOCK_TYPES:
                plan = figure_plan.get(id(block), {})
                image_path = image_body_path(block)
                caption = plan.get("caption")
                rendered_text = emit_figure_text(block, page_no)
                if image_path is None:                     # 只有图题、没有图 → 不造图
                    texts = caption_texts(block)
                    orphan_captions.extend(texts)
                    if not texts:
                        # 既没有图体也没有图题：**不能静默消失**，记进 audit 并报警
                        empty_figures.append({"page": page_no, "block_index": block_index,
                                              "type": kind})
                        warnings.append(f"第 {page_no} 页有一个 {kind} 块既没有图也没有文字，"
                                        f"可能是内容丢失，建议人工看一眼")
                    lines.extend(source.asset_lines(block, caption_present=rendered_text))
                    source.mark(block, lines=lines, first_line=first_line,
                                reason="empty_asset_block" if not texts else "caption_only")
                    blocks_log.append({"page": page_no, "block_index": block_index, "type": kind,
                                       "lines": [first_line, len(lines)],
                                       "preview": "（只有图题，没有图）" if texts else "（空块）"})
                    continue
                if plan.get("merge_with") and not caption:
                    # 面板的"小弟"块：图已经并到大弟那块上了，这里只留档，不再出一条
                    merged_panels.append({"page": page_no, "mineru_file": image_path,
                                          "bbox_pt": [round(float(v), 2) for v in (block.get("bbox") or [])]})
                    lines.extend(source.asset_lines(block, caption_present=rendered_text))
                    source.mark(block, lines=lines, first_line=first_line)
                    continue
                members = [block] + [b for b in page_blocks
                                     if id(b) in (plan.get("merge_with") or [])]
                merged_now = len(members) > 1
                bbox_pt = union_bbox([m.get("bbox") for m in members])
                resolved = resolve_image(base_dir, image_path, page_idx)
                if resolved is None:
                    missing_images.append({"page": page_no, "block_index": block_index,
                                           "image_path": image_path, "caption": caption})
                    warnings.append(f"第 {page_no} 页有张图在 images/ 里找不到（{image_path}），本条不入索引")
                else:
                    figure_id = f"IMG-{len(figures) + 1:04d}"
                    target = out_dir / "images" / f"mineru-{figure_id}{resolved.suffix.lower()}"
                    if copy_images and not dry_run:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(resolved, target)
                    page_name = f"page-{page_no:05d}.png"
                    page_render = out_dir / "images" / page_name
                    has_page_render = page_render.is_file()
                    labels = [figure_plan[id(m)].get("panel_label") for m in members]
                    labels = [x for x in labels if x]
                    figures.append({
                        "id": figure_id,
                        "source_block_ids": [source.by_identity[id(m)]["block_id"] for m in members],
                        "asset_ids": [a for m in members for a in source.by_identity[id(m)]["asset_ids"]],
                        "page": page_no,
                        # D6-A：面板并框之后必须从整页图裁（单图拼不出来），所以给 page_image；
                        # 没有整页图才退回 MinerU 裁好的那张单图。
                        "page_image": f"images/{page_name}" if has_page_render else None,
                        "page_image_sha256": sha256_file(page_render) if has_page_render else None,
                        "bbox": [round(v * PAGE_SCALE, 2) for v in bbox_pt],
                        "bbox_pt": bbox_pt,
                        "bbox_source": "mineru-merged" if merged_now else "mineru",
                        "bbox_picked": "mineru-merged" if merged_now else "mineru",
                        "confidence": "high",
                        "needs_review": False,
                        "local_bbox": None,
                        "model_bbox": None,
                        "candidates": [{"source": "mineru", "bbox": [round(float(v), 2)
                                                                     for v in (m.get("bbox") or [])]}
                                       for m in members],
                        "kind": "diagram",
                        "caption": caption or None,
                        "panel_label": "、".join(labels) or None,
                        "caption_from_pool": bool(plan.get("figure_caption")),
                        "panels": [{"bbox_pt": [round(float(v), 2) for v in (m.get("bbox") or [])],
                                    "mineru_file": str(resolve_image(base_dir, image_body_path(m) or "", page_idx) or "")}
                                   for m in members] if merged_now else [],
                        "file": None if has_page_render else (str(target.relative_to(out_dir)) if copy_images else None),
                        "sha256": sha256_file(resolved),
                        "mineru_file": str(resolved.relative_to(base_dir)) if base_dir in resolved.parents else resolved.name,
                        "concepts": [],
                        "occurrences": [],
                    })
                    if merged_now:
                        merged_count += 1
                lines.extend(source.asset_lines(block, caption_present=rendered_text))
            else:
                rendered = block_lines(block)
                if kind == "code" and not rendered:
                    # ⚠️ MinerU 的 `code` 块（`sub_type=algorithm`）把正文藏在**嵌套的**
                    #    `blocks[].lines` 里，顶层 `lines` 是空的 —— 只渲染顶层就会
                    #    **整块丢内容**（实测《数值计算方法》58 个算法块一个都没进
                    #    `document.md`，而 MinerU 自己的 `full.md` 里它们是以 ```txt 存在的）。
                    rendered = [piece for inner in (block.get("blocks") or [])
                                for line in (inner.get("lines") or [])
                                for piece in explode(render_line(line)) if piece.strip()]
                inline_eq += sum(1 for line in block.get("lines") or []
                                 for span in line.get("spans") or []
                                 if span.get("type") == "inline_equation")
                lines.extend(rendered)
                if kind == "code" and rendered:
                    # 代码/算法块的行**缩进 4 格**再落盘：markdown 里这就是代码块，
                    # 里面 `#include …`、`2.2 fx:=…` 这类行不会被当成标题，
                    # 也不会踩"行首 #"那条硬规则。原内容一个字节不动，只是加了缩进。
                    lines[first_line - 1:] = [f"    {piece}" for piece in rendered]
                    code_lines.append([first_line, len(lines)])

            source.mark(block, lines=lines, first_line=first_line)

            blocks_log.append({
                "page": page_no, "block_index": block_index, "type": kind,
                "lines": [first_line, len(lines)],
                "preview": " ".join("".join(render_span(s) for s in (l.get("spans") or []))
                                    for l in (block.get("lines") or []))[:60],
            })

        for block in (page.get("discarded_blocks") or []):
            source.mark(block, region=current_region)
            kind = str(block.get("type") or "unknown")
            discarded_by_type[kind] = discarded_by_type.get(kind, 0) + 1
            text = "".join(_span_text(span)
                           for line in block.get("lines") or []
                           for span in line.get("spans") or []).strip()
            discarded.append({"page": page_no, "type": kind, "text": text[:200]})

        page_log.append({"page": page_no, "lines": [start_line, len(lines)]})

    document = "\n".join(lines) + "\n"
    source_payload = source.payload(document)
    problems = check_document(lines, code_lines=code_lines)

    text_only = [line for line in lines if not line.startswith("[第 ")]
    counts = {
        "pages": len(pages),
        "blocks": sum(by_type.values()),
        "by_type": by_type,
        "discarded": sum(discarded_by_type.values()),
        "discarded_by_type": discarded_by_type,
        "dropped_toc_blocks": dropped_toc,
        "dropped_repeat_titles": dropped_repeat,
        "formulas": {"inline": inline_eq, "interline": by_type.get("interline_equation", 0)},
        "figures": len(figures),
        "text_chars": sum(len(line) for line in text_only),
        "cjk_chars": sum(len(CJK.findall(line)) for line in text_only),
        "document_lines": len(lines),
    }
    if problems:
        warnings.extend(problems)

    inputs = [{"path": layout_path.name, "role": "layout", "bytes": layout_path.stat().st_size,
               "sha256": sha256_file(layout_path)}]
    for extra in ("content_list.json", "full.md"):
        candidate = base_dir / extra
        if candidate.is_file():
            inputs.append({"path": extra, "role": extra.split(".")[0],
                           "bytes": candidate.stat().st_size, "sha256": sha256_file(candidate)})

    field_notes = {
        "document.md": "`[第 N 页]` + 逐块正文；行内公式 $...$；独立公式单行 $$...$$；标题是裸行（不带 #）",
        "mineru.audit.json": "block 是哪些、丢了什么、每一块落在 document.md 的哪几行、版本与输入 sha256",
        "figures.json": "跟图片层同名同格式，导出层照旧读它（source=mineru 的框直接来自 MinerU）",
        "extract.json": "engine=mineru-cloud-<版本>，替代 OCR 抽取层的同名产物",
    }
    audit = {
        # 剥掉的不可见字符（控制字符 / U+FFFD）——**必须记账**，否则就是静默改原文
        "stripped_control_chars": {
            "count": _control_stats["count"],
            "note": "适配层剥掉的不可见字符（PDF 字体编码问题会产出 U+FFFD）；"
                    "只删不可见字符，正文一个字不改",
            "sample_codepoints": _control_stats["sample"][:5],
        },
        "_meta": {"file_type": "mineru-audit", "schema_version": SCHEMA_VERSION,
                  "generator": GENERATOR, "source_sha256": inputs[0]["sha256"],
                  "field_notes": field_notes},
        "format": fmt,
        "book_title": book_title,
        "note": note,
        "inputs": inputs,
        "counts": counts,
        "pages": page_log,
        "blocks": blocks_log,
        "discarded": discarded,
        "images": [{"id": f["id"], "page": f["page"], "bbox_pt": f["bbox_pt"],
                    "file": f["file"], "mineru_file": f["mineru_file"],
                    "caption": f["caption"]} for f in figures],
        "images_missing": missing_images,
        "captions_without_image": orphan_captions,
        "merged_panels": merged_panels,
        "empty_figure_blocks": empty_figures,
        "merged_figures": merged_count,
        "checks": {"hard_rules": problems},
        "warnings": warnings,
    }
    figures_payload = {
        "_meta": {"file_type": "figures", "schema_version": SCHEMA_VERSION,
                  "generator": GENERATOR, "source_sha256": inputs[0]["sha256"],
                  "field_notes": {
                      "images[].bbox": "插图在整页图上的像素框 [x0,y0,x1,y1]；由 MinerU 的 pt 坐标 ×2 换算",
                      "images[].bbox_pt": "MinerU 原始 pt 坐标（换算依据，留档用）",
                      "images[].bbox_source": "mineru=MinerU 直接给的框（与我们的独立实现互校差 <10pt）",
                      "images[].file": "裁好的单图文件（相对 data/parse/）；展示层直接用，不再从整页图裁",
                      "images[].caption": "MinerU 给的图题（image_caption / table_caption），同时作为正文一行出现在 document.md 里",
                      "empty_figure_blocks": "既没有图体也没有图题的图表块（可能是内容丢失，audit 里留着页号）",
                  }},
        "pdf": None,
        "scale": PAGE_SCALE,
        "pages": [],
        "pages_with_candidates": 0,
        "pages_reviewed": 0,
        "confidence_counts": {"high": len(figures)},
        "images": figures,
        "page_index": {},
    }
    for figure in figures:
        figures_payload["page_index"].setdefault(str(figure["page"]), []).append(figure["id"])

    extract = {
        "_meta": {"file_type": "extract", "schema_version": SCHEMA_VERSION,
                  "generator": GENERATOR, "source_sha256": inputs[0]["sha256"],
                  "field_notes": {"engine": "抽取引擎；mineru-cloud-<版本> 表示这一份来自 MinerU 而不是本地 OCR",
                                  "inputs": "这次适配读了哪些 MinerU 产物（含 sha256）",
                                  "counts": "页数/块数/公式数/图片数"}},
        "engine": f"mineru-cloud-{fmt['version']}",
        "format": fmt,
        "inputs": inputs,
        "counts": counts,
        "warnings": warnings,
    }

    # ── headings.json：**MinerU 认出来的标题 + 它落在哪个区域**（给结构层用）──────
    #
    # 为什么要有这份产物（2026-09-27 实测抓出）：
    #   `document.md` 是**拍平**的（标题写成裸行、不带 `#`），于是结构层只能拿正则重新猜标题 ——
    #   而 MinerU 本来就把标题标出来了（`type=title` + `level`），还标出了
    #   `目次`/`习题`/`习题参考答案` 这些**区域标记**。这份文件把这些信息留档，
    #   结构层据此把"非正文的标题"（目录条目 / 答案页 / 章前材料）剔掉 ——
    #   一本书因此从 17 个假章收敛回真实的 9 章。
    headings_payload = {
        "_meta": {"file_type": "mineru_headings", "schema_version": SCHEMA_VERSION,
                  "generator": GENERATOR, "source_sha256": inputs[0]["sha256"],
                  "field_notes": {
                      "headings": "MinerU 标成 `title` 的块：页码、在 document.md 里的行号、"
                                  "文字、它给的 level、以及**它落在哪个区域**",
                      "headings[].region": "front=章前材料 / toc=目录 / body=正文 / "
                                           "exercise=习题 / answer=答案；**只有 body 才是教案单元**",
                      "page_region": "每一页属于哪个区域（跨页目录、章后习题都靠它）",
                      "regions": "区域的页码区间（人看的）",
                      "code_lines": "来自 `code` 块的行区间 —— **这些行不许当标题**"
                                    "（伪代码的 `2.2 fx:=…` 长得像编号标题，实测当过假单元），"
                                    "但正文里一个字都不删"}},
        "book_title": book_title,
        "headings": headings,
        "page_region": {str(k): v for k, v in sorted(page_region.items())},
        "regions": _spans_of(page_region),
        "code_lines": code_lines,
    }

    # ⚠️ **优先用 PDF 内嵌书签**（2026-09-27）：它是"书自己声明的结构"，比印刷目录页可靠
    #    （印刷目录页会被 OCR 弄丢/串行；实测《复变函数》只 OCR 出一半）。
    #    实测《数值计算方法》109 条、《数分讲义第二册》155 条，两本都完整。
    outline = read_pdf_outline(pdf or _pdf_from_ingest(out_dir))
    outline, outline_shift = normalize_outline_levels(outline) if outline else (outline, 0)
    toc_source = "pdf-bookmarks" if outline else "printed-toc"
    toc_all = outline or toc_entries
    chapter_entries = [e for e in toc_all
                       if int(e.get("level") or 0) == 1
                       and not is_book_matter(str(e.get("text") or ""))]
    chapters = [e["text"] for e in chapter_entries]
    numbered = [t for t in chapters if TOC_CHAPTER.match(str(t).strip())]
    toc_payload = {
        "_meta": {"file_type": "toc", "schema_version": SCHEMA_VERSION,
                  "generator": GENERATOR, "source_sha256": inputs[0]["sha256"],
                  "field_notes": {
                      "source": "这份目录从哪来：pdf-bookmarks=PDF 内嵌书签（最权威）；printed-toc=从目录页文字解析",
                      "entries": "目录条目（书**自己声明的结构**）",
                      "entries[].level": "**已按标题形状归一化**的层级；1 级就是章（0 = 比章还高，如书签根节点 `目录`）",
                      "outline_level_shift": "书签原始层级减掉了几层才归一化（PDF 书签常有 `目录` 这类包装层）",
                      "printed_entries": "另外记一份「从目录页解析出来的」，用于交叉核对",
                      "chapters": "1 级条目（已去掉前言/序/目录这类），**校验「解析出来的章」对不对就看它**",
                      "chapters_numbered": "其中带 `第N章` 编号的那些 —— 对账时严格比；`绪论`/`附录` 这类无编号的只提示"}},
        "book_title": book_title,
        "source": toc_source,
        "outline_count": len(outline),
        "outline_level_shift": outline_shift,
        "entries": toc_all,
        "printed_entries": toc_entries,
        "chapters": chapters,
        "chapters_numbered": numbered,
    }

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "document.md").write_text(document, encoding="utf-8")
        write_json(out_dir / "toc.json", toc_payload)
        write_json(out_dir / "figures.json", figures_payload)
        write_json(out_dir / "mineru.audit.json", audit)
        write_json(out_dir / "extract.json", extract)
        write_json(out_dir / "headings.json", headings_payload)
        source_payload["_meta"]["output_sha256"] = {
            name: sha256_file(out_dir / name) for name in OUTPUT_FILES}
        write_json(out_dir / "source.blocks.json", source_payload)

    return Adapted(document=document, lines=lines, figures=figures_payload,
                   audit=audit, extract=extract, counts=counts, source_blocks=source_payload)


__all__ = ["MinerUFormatError", "Adapted", "adapt", "detect_format", "find_layout",
           "block_lines", "check_document", "image_parts", "resolve_image"]
