"""预检（M2，P5 定案）：**0 token、秒级**回答"这本书值不值得跑"。

## 为什么要有这一步

网站形态下学生一次传一堆书，不能每本都全量跑。预检先看一眼：

* 有没有内嵌书签（最可靠的一源）；
* 目录页在哪几页（PDF 页码 + 印刷页码）；
* 目录页上有几行"长得像目录条目"（`标题 …… 页码`）；
* 按当前口径，大约要送几次模型、几张图；
* ⇒ **`can_auto_parse`**：这本书能不能自动解析。

## 判据（全部可复现、不猜）

| 结论 | 判据 |
|---|---|
| `can_auto_parse: true` | 有书签（≥3 条 1 级/2 级），**或**目录页里认出 ≥5 条条目 |
| `false` | 两者都没有 —— 那就只能靠模型从截图重抽（本次重构**不做**这条路，见 P1 的本意） |

预检**只看 PDF**，不读已有产物 —— 所以新书能立刻预检，不需要先跑一遍解析。
"""
from __future__ import annotations

import re
from pathlib import Path

from .contract import GENERATOR, SCHEMA_VERSION, sha256_file
from .sources import read_raw_bookmarks

TOC_MARK = re.compile(r"^\s*(目\s*录|目\s*次|Contents|CONTENTS|Table of Contents)\s*$", re.I)
#: 一行"长得像目录条目"：标题 + 引导点/省略号 + 页码（阿拉伯或罗马）
TOC_LINE = re.compile(
    r"^\S.{0,70}?[…⋯.．·\s]{2,}[（(]?\s*(?:\d{1,4}|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+)\s*[）)]?\s*$")
_ROMAN = re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+$")

#: 目录区最多往后看这么多页（实测四本都在 2～4 页内）
TOC_MAX_PAGES = 12
#: 关掉自动解析的阈值：认出这么多条才算"目录页真的在"
MIN_TOC_ENTRIES = 5
MIN_BOOKMARKS = 3


def has_text_layer(doc, *, sample_pages: int = 12) -> bool:
    """这份 PDF 有没有文字层（＝是不是扫描件）。

    为什么必须判：**扫描件读不出目录页文字**，而这一步是 0 token 的规则抽取。
    实测《复变函数》：239 页 `get_text()` **一行都没有**（纯扫描），
    它的 47 条印刷目录条目其实来自 MinerU 的 OCR 产物。
    这种情况必须**如实报**，否则人会以为"这本书目录页找不到"是程序的 bug（R-b 不确定要说出来）。
    """
    for page_no in range(1, min(doc.page_count, sample_pages) + 1):
        try:
            if len((doc[page_no - 1].get_text("text") or "").strip()) > 50:
                return True
        except Exception:                                 # noqa: BLE001
            continue
    return False


def find_toc_pages(doc, *, max_pages: int = TOC_MAX_PAGES) -> dict:
    """找目录页：先按书签里的 `目录` 节点，再按"整页条目密度"。

    返回 `{"pdf_pages": [...], "printed_labels": [...], "method": ...}`。
    页码一律是 **PDF 页码（1 起）**，因为截图/跳页都用它。
    """
    pages: list[int] = []
    method = "none"

    # ① 书签里明确写了"目录"的节点：从它那一页起，往后连着都是目录
    for level, title, page in doc.get_toc(simple=True) or []:
        if TOC_MARK.match(str(title or "").strip()):
            method = "bookmark"
            start = int(page)
            for offset in range(max_pages):
                probe = start + offset
                if probe > doc.page_count:
                    break
                count = _toc_line_count(doc, probe)
                if count >= MIN_TOC_ENTRIES:
                    pages.append(probe)
                elif pages:
                    break
                elif offset >= 2:
                    break
            break

    # ② 没书签（或书签没标目录）：扫前 40 页，挑"条目密度高"的连续页
    if not pages:
        method = "density"
        dense: list[int] = []
        for page_no in range(1, min(doc.page_count, 40) + 1):
            if _toc_line_count(doc, page_no) >= MIN_TOC_ENTRIES:
                dense.append(page_no)
        # ②a 页面上**明写"目录"**的那一页也要算进来：有些排版只在目录首页写"目录"，
        #     而首页的条目被标题挤掉了几行、密度没过阈值
        #     （实测《线性代数：原理与应用》第 3 页：正文里只有 `目录` 两个字 + 条目）。
        try:
            marked = [page_no for page_no in range(1, min(doc.page_count, 40) + 1)
                      if _has_toc_mark(doc, page_no)]
        except Exception:                                 # noqa: BLE001
            marked = []
        if marked:
            method = "marker"
            start = marked[0]
            page_no = start
            picked: list[int] = []
            # 从标记页往后连着的"有内容"的页都算目录（允许某页密度偏低，比如首页）
            while page_no <= doc.page_count and len(picked) < max_pages:
                # ⚠️ 阈值不能低到 3：目录页里确实有几行"…… 页码"的**正文页**（章首、
                # 参考文献）会被带进来，于是复核时把封面/正文页也当目录页送模型
                # （实测《数值计算方法》把封面页也送进去了）。6 行是个保守的下限。
                if _toc_line_count(doc, page_no) >= 6 or page_no == start:
                    picked.append(page_no)
                    page_no += 1
                    continue
                break
            dense = picked
        if dense:
            # 只取第一段连续区间（目录页是连续的；后面偶尔出现的"参考文献"页不算）
            run = [dense[0]]
            for page_no in dense[1:]:
                if page_no == run[-1] + 1:
                    run.append(page_no)
                else:
                    break
            pages = run[:max_pages]

    labels = [_first_printed_label(doc, page_no) for page_no in pages]
    return {"pdf_pages": pages, "printed_labels": labels, "method": method}


def _toc_line_count(doc, page_no: int) -> int:
    """这一页有几行"长得像目录条目"。"""
    try:
        text = doc[page_no - 1].get_text("text")
    except Exception:                                     # noqa: BLE001
        return 0
    return sum(1 for line in text.splitlines() if TOC_LINE.match(line.strip()))


def _has_toc_mark(doc, page_no: int) -> bool:
    """这一页上有没有单独一行写着"目录/目次/Contents"。"""
    try:
        lines = doc[page_no - 1].get_text("text").splitlines()
    except Exception:                                     # noqa: BLE001
        return False
    return any(TOC_MARK.match(line.strip()) for line in lines[:6])


def _first_printed_label(doc, page_no: int) -> str | None:
    """这一页自己的**印刷页码**（页面上下角那个孤立数字/罗马数字）。

    只在前 3 行与后 3 行里找，且要求整行就是个页码 —— 目录页正文本就会带页码，
    这里要的是"这页自己印着几"。找不到就返回 `None`（不猜）。
    """
    try:
        lines = [ln.strip() for ln in doc[page_no - 1].get_text("text").splitlines()]
    except Exception:                                     # noqa: BLE001
        return None
    edges = [ln for ln in lines[:3] + lines[-3:] if ln]
    for line in edges:
        if re.fullmatch(r"\d{1,4}", line) or _ROMAN.fullmatch(line):
            return line
    return None


def preflight(*, parse_dir: str | Path, pdf: str | Path | None, slug: str = "",
              book_title: str = "") -> dict:
    """跑预检，返回 `toc.preflight.json` 的内容。"""
    parse_dir = Path(parse_dir)
    payload = {
        "_meta": {"file_type": "toc_preflight", "schema_version": "1.0",
                  "generator": GENERATOR + ".preflight", "slug": slug or parse_dir.name,
                  "book_title": book_title},
        "pdf": str(pdf) if pdf else None,
        "pdf_sha256": "",
        "has_pdf": False,
        "page_count": 0,
        "bookmarks": {"count": 0, "levels": [], "has_root_wrapper": False},
        "toc_pages": {"pdf_pages": [], "printed_labels": [], "method": "none"},
        "toc_entry_lines": 0,
        "has_text_layer": None,
        "images_planned": 0,
        "model_calls_planned": 0,
        "can_auto_parse": False,
        "reasons": [],
    }
    reasons: list[str] = payload["reasons"]

    raw = read_raw_bookmarks(pdf)
    if raw:
        levels = sorted({int(item.get("level") or 0) for item in raw})
        wrapper = bool(levels) and levels[0] == 1 and any(
            TOC_MARK.match(str(item.get("text") or "").strip()) for item in raw)
        payload["bookmarks"] = {"count": len(raw), "levels": levels,
                                "has_root_wrapper": wrapper}
        reasons.append(f"有内嵌书签 {len(raw)} 条，层级 {levels}"
                       + ("（带包装根节点，会整体位移）" if wrapper else ""))
    else:
        reasons.append("没有内嵌书签 —— 只能靠目录页文字或正文形状")

    if not pdf or not Path(pdf).is_file():
        reasons.append(f"没有可读的 PDF（{pdf}）—— 预检停在书签这一步")
        payload["reasons"] = reasons
        _decide(payload)
        return payload

    payload["has_pdf"] = True
    payload["pdf_sha256"] = sha256_file(pdf)
    payload["has_text_layer"] = False
    try:
        import pymupdf
    except ImportError:
        reasons.append("没装 pymupdf，读不了目录页")
        payload["reasons"] = reasons
        _decide(payload)
        return payload

    doc = pymupdf.open(str(pdf))
    try:
        payload["page_count"] = doc.page_count
        payload["has_text_layer"] = has_text_layer(doc)
        if not payload["has_text_layer"]:
            reasons.append("**这份 PDF 没有文字层（扫描件）** —— 规则抽不出目录页文字；"
                           "要继续跑，只有两条路：① PDF 书签；② 先走 MinerU 云端 OCR，"
                           "从它的产物里读印刷目录条目（本块会优先用 `toc.json` 里已有的）")
        found = find_toc_pages(doc)
        payload["toc_pages"] = found
        payload["toc_entry_lines"] = sum(_toc_line_count(doc, page_no)
                                         for page_no in found["pdf_pages"])
    finally:
        doc.close()

    if found["pdf_pages"]:
        reasons.append(f"目录页在 PDF 第 {found['pdf_pages']} 页"
                       f"（印刷页 {found['printed_labels']}，判据：{found['method']}），"
                       f"认出 {payload['toc_entry_lines']} 条条目")
    else:
        reasons.append("没找到目录页（前 40 页没有整页的条目密集区）")

    # 送图预算（Q-b 定案：优先全送目录页，超上限就降级）
    pages = found["pdf_pages"]
    payload["images_planned"] = min(len(pages), 8)
    payload["model_calls_planned"] = 1 if pages else 0
    if len(pages) > 8:
        reasons.append(f"目录页有 {len(pages)} 页 > 上限 8 → 复核时会降级为只送可疑项所在页")
    payload["reasons"] = reasons
    _decide(payload)
    return payload


def _decide(payload: dict) -> None:
    bookmarks = payload["bookmarks"]["count"]
    entries = payload["toc_entry_lines"]
    payload["can_auto_parse"] = bool(bookmarks >= MIN_BOOKMARKS or entries >= MIN_TOC_ENTRIES)
    if not payload["can_auto_parse"]:
        payload["reasons"].append(
            f"书签 {bookmarks} 条、目录页条目 {entries} 条，都不够"
            f"（阈值 {MIN_BOOKMARKS}/{MIN_TOC_ENTRIES}）—— **不能自动解析**这份 PDF")


def render_text(payload: dict) -> str:
    """给人看的一屏。"""
    lines = [f"=== 目录层预检：{payload['_meta']['slug']} ==="]
    lines.append(f"  PDF：{payload['pdf'] or '（没有）'}"
                 f"  页数 {payload['page_count']}"
                 + (f"  指纹 {payload['pdf_sha256'][:12]}" if payload["pdf_sha256"] else ""))
    marks = "✅" if payload["can_auto_parse"] else "⚠️"
    lines.append(f"  {marks} 能不能自动解析：{payload['can_auto_parse']}")
    for reason in payload["reasons"]:
        lines.append(f"      · {reason}")
    lines.append(f"  预计：{payload['model_calls_planned']} 次模型调用 / "
                 f"{payload['images_planned']} 张目录页截图")
    return "\n".join(lines)


__all__ = ["MIN_TOC_ENTRIES", "find_toc_pages", "preflight", "render_text"]
