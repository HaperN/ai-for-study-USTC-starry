"""方案 A 的对齐器：**书的目录（权威）⇄ 正文标题候选（MinerU 的 title 块）**。

## 为什么要有它（2026-09-28 定的方向）

老做法是：**从正文的文字里用编号正则"推断"层级**（`第N章` / `§N.M` / `N.M.K`），
再拿目录去校验。实测两个反例（各坏在不同地方，但**同一个病**）：

| 书 | 正文写法 | 老做法的结果 |
|---|---|---|
| 《电路分析基础》 | `§ 1-1 电路及集总电路模型`（短横线） | `heading_match()` 返回 **None** → 331 页只切出 **7 个单元** |
| 《电磁学》 | `* 第 11 章 相对论电磁学`（前置星号） | 章规则要求行首是"第" → **整章丢失**，其节被安到不存在的编号上 |

而学术界（HiPS: Hierarchical PDF Segmentation of Textbooks, arXiv 2509.00909）的结论是反过来的：

> **书的目录是"层级"的唯一权威；正文只负责回答"哪些行是标题"。**

本模块就做这件事。**它不猜层级、不认编号体系**：
层级一律取自目录（`toc.v2.json`，三源对账 + 模型视觉复核的产物），
正文侧只提供"候选"（`headings.json`，来自 MinerU 的 `type=title` 块，带行号与页码）。

## 三件已验证的事（都是实测，不是想当然）

1. `headings.json` **已经带** `line`（document.md 行号）/ `page` / `text` / `region`
   —— 电路分析 56 条 `§ N-M`、电磁学的 `* 第 11 章` 都在里面；
2. 仓库**已有的**相似度函数就能匹配上这两个反例：
   `title_similarity('§ 1-1 电路及集总电路模型', '1.1 电路及集总电路模型') = 0.818`、
   `title_similarity('* 第 11 章 相对论电磁学', '第11章相对论电磁学') = 1.000`
   —— 病根不是"函数不行"，而是**这些函数只被用来"过滤"，从没被用来"找标题"**；
3. 所以本模块**不引入新依赖、不调模型**（0 token）。

## 匹配策略（从严到松，逐级回退）

1. **归一化精确**：`identity(number, title)` 折平后相等；
2. **子串**：一方归一化后包含另一方（短的一方 ≥ `SUBSTRING_MIN` 字）；
3. **模糊**：`title_similarity ≥ FUZZY_MIN`。

**页码窗口**是防错配的关键：两源页码能换算时（目录层给了 `page_offset`），
只接受 `|Δ页| ≤ PAGE_WINDOW` 的候选；换算不出来时**放行但记 `page_unknown: true`**（不猜）。

一对一：每个候选只能被一个目录节点认领（按分数贪心）；一个目录节点只能认领一个候选。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..structure.structure import norm_title, title_similarity
from .contract import number_key
from .sources import canonical_number, identity, mathematical_title_key, parse_title

SCHEMA_VERSION = "1.0"
GENERATOR = "aiforstudy.parse.toc.align"

#: 模糊匹配的下限。0.6 是拿两个反例实测选的：
#: `§ 1-1 …` ↔ `1.1 …` = 0.818 要过；而"目录条目 ↔ 无关标题"通常在 0.3 以下。
FUZZY_MIN = 0.6
#: 子串匹配时，短的一方至少这么长（防 `前言` 这种短词把一堆标题都吃进来）
SUBSTRING_MIN = 4
#: 页码窗口：两源页码能换算时，允许差这么多页（标题常在页首/页尾漂移）
PAGE_WINDOW = 3
#: 目录节点被跳过的种类（非正文不进骨架）
SKIP_KINDS = ("non-teaching",)
#: **线搜索**：目录里有、MinerU 没标成 title 的条目，按顺序在 document.md 里找落点。
#: 相似度门槛取 0.82（标题级匹配；低于它的多半是正文里提到了这个名词）。
LINE_SEARCH_MIN = 0.82
#: 线搜索只认"像标题的行"：不超过这么多字、不以句读结尾
LINE_SEARCH_MAX = 120
#: **排版兜底**：块的平均行高 ≥ 本页正文行高中位数 × 这个系数 → 当成标题候选。
#: 为什么用行高：MinerU 的 span 只给 `bbox`（没有字号），而行高就是字号的代理。
#: 实测（2026-09-29 外部基准）：MinerU 的 title 块只覆盖金标的 **57%** ——
#: 《Introduction to Swiss Law》里的章作者标题（`MARC THOMMEN Criminal Procedure`）
#: 被它当成普通 `text` 块。这条兜底就是冲着这类漏检去的。
TYPO_HEIGHT_RATIO = 1.15
#: 排版兜底的文本长度上限（标题不会太长）
TYPO_MAX_CHARS = 90
_LINE_TAIL_BAD = ("。", "；", "，", ",", ";", "：", ":")


#: **编号形状兜底**：`6.2.1 标题` / `1.2 标题`（点号路径 ≥2 层）后跟文字。
#: 为什么要这条兜底（D-10，2026-09-28 实测《复变函数》）：MinerU **会漏标**标题 ——
#: 实测 `6.2.1 $\int_0^{2\pi}R(...)\mathrm{d}\theta$ 型积分` 这类**含大量公式的长标题**
#: 没被标成 `title`，于是新链少了 3 个小节（老链靠编号正则反而找到了）。
#: 所以候选 = **MinerU 的 title 块 ∪ 编号形状严格合格的行**（MinerU 管精度、形状管召回）。
_SHAPE_CANDIDATE = re.compile(r"^(\d+(?:\.\d+){1,3})\s+(\S.*)$")
#: 形状兜底的排除项：太长（那是段落）、以句读结尾（那是句子）、带表格/图片标记
#: 含重公式的标题本来就长（实测 `6.2.3 $I_1=...$ 及 $I_2=...$ 型的积分` 约 130 字），
#: 而"以点号编号路径开头"本身已是强约束（段落极少这样开头），所以放到 200。
_SHAPE_MAX_CHARS = 200
_SHAPE_TAIL_BAD = ("。", "；", "，", ",", ";", "：", ":")
#: 形状兜底的排除词：习题/思考题这类**题干的编号**（`1.2 真空中有一点电荷…`）
#: 长得和节号一模一样（实测《电磁学》收了 6 条题干，D-10 复核抓出）。
_SHAPE_BAD_WORDS = ("习题", "练习", "思考题", "作业", "解答", "参考答案")


def _line_pages(text: str) -> dict[int, int]:
    """`document.md` 的 `行号 → 页码`（adapt 写了 `[第 N 页]` 标记）。"""
    pages: dict[int, int] = {}
    current = 0
    for index, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        m = re.match(r"^\[\s*第\s*(\d+)\s*页\s*\]$", stripped)
        if m:
            current = int(m.group(1))
            continue
        pages[index] = current
    return pages


def load_candidates(parse_dir: str | Path) -> list[dict]:
    """正文标题候选 ← `headings.json`（MinerU 的 title 块）**∪ 编号形状兜底**。"""
    parse_dir = Path(parse_dir)
    payload = _load(parse_dir / "headings.json") or {}
    out: list[dict] = []
    seen_lines: set[int] = set()
    for row in payload.get("headings") or []:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        line = int(row.get("line") or 0)
        seen_lines.add(line)
        out.append({
            "line": line,
            "page": row.get("page"),
            "text": text,
            "region": str(row.get("region") or ""),
            "mineru_level": row.get("level"),
            "from": "mineru-title",
        })

    # ── 编号形状兜底：MinerU 漏标的、但**形状严格合格**的标题行 ──────────────
    doc = parse_dir / "document.md"
    if doc.is_file():
        try:
            text = doc.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if text:
            pages = _line_pages(text)
            page_region = {int(k): str(v) for k, v in (payload.get("page_region") or {}).items()}
            # `code_lines` 是**行号区间**的列表（`[[660,665],[668,668]]`）——与
            # `structure.load_code_lines()` 同一口径（那边也是展开成行号集合）。
            code_lines: set[int] = set()
            for span in payload.get("code_lines") or []:
                try:
                    start, end = int(span[0]), int(span[1])
                except (TypeError, ValueError, IndexError):
                    continue
                code_lines.update(range(start, end + 1))
            for index, line in enumerate(text.splitlines(), 1):
                if index in seen_lines:
                    continue
                if index in code_lines:
                    continue                      # 伪代码：实测 `2.2 fx:=fx+tmp*y$_{i}$` 会被收
                stripped = line.strip()
                if not stripped or len(stripped) > _SHAPE_MAX_CHARS:
                    continue
                if stripped.endswith(_SHAPE_TAIL_BAD):
                    continue
                if any(word in stripped for word in _SHAPE_BAD_WORDS):
                    continue                      # 习题题干（编号与节号同形）
                matched = _SHAPE_CANDIDATE.match(stripped)
                if not matched:
                    continue
                # ⚠️ **只收三级编号（`N.M.K`）** —— 这是兜底的第二条闸，作用是挡"伪代码/句子碎片"：
                #    实测《数值计算方法》的假标题全是**二级**（`2.2 fx:=fx+tmp*y$_{i}$`、
                #    `2.1 选取非对角线按模最大元素…`、`8.3 节中给出的 Givens 矩阵…`）——
                #    算法伪代码与证明步骤正好用二级列表编号；而 MinerU 漏标的真小节
                #    （《复变函数》的 `6.2.1/6.2.2/6.2.3`）都是三级。
                if len(matched.group(1).split(".")) < 3:
                    continue
                page = pages.get(index)
                region = page_region.get(int(page)) if page else None
                # ⚠️ 区域判定**必须过两道**：① 页码能查到区域时必须是 body；
                #    ② 查不到页码时**保守跳过**（宁可少收，也别把习题/前言收进来）。
                if page is None or region != "body":
                    continue
                out.append({
                    "line": index, "page": page, "text": stripped,
                    "region": region or "body", "mineru_level": None,
                    "from": "shape-fallback",
                })

    # ── 排版兜底：行高明显大于正文的块（字号代理）────────────────────────
    layout = _load(parse_dir / "mineru" / "layout.json") or {}
    page_region = {int(k): str(v) for k, v in (payload.get("page_region") or {}).items()}
    seen_norm = {norm_title(c["text"]) for c in out}
    for page in layout.get("pdf_info") or []:
        page_no = int(page.get("page_idx") or 0) + 1        # MinerU 是 0-based
        region = page_region.get(page_no)
        if region and region != "body":
            continue
        blocks = page.get("para_blocks") or []
        heights = []
        for blk in blocks:
            hs = [float(sp.get("bbox")[3]) - float(sp.get("bbox")[1])
                  for l in (blk.get("lines") or []) for sp in (l.get("spans") or [])
                  if sp.get("bbox")]
            if hs:
                heights.append((blk, sum(hs) / len(hs)))
        body = sorted(h for _, h in heights)
        if len(body) < 4:
            continue
        median = body[len(body) // 2]
        if median <= 0:
            continue
        for blk, mean_h in heights:
            if mean_h < median * TYPO_HEIGHT_RATIO:
                continue
            text = "".join(
                str(sp.get("content") or "")
                for l in (blk.get("lines") or []) for sp in (l.get("spans") or [])).strip()
            if not text or len(text) > TYPO_MAX_CHARS:
                continue
            if not re.search(r"[A-Za-z\u4e00-\u9fff]", text):
                continue
            key = norm_title(text)
            if not key or key in seen_norm:
                continue
            seen_norm.add(key)
            out.append({"line": None, "page": page_no, "text": text,
                        "region": region or "body", "mineru_level": None,
                        "from": "typography-fallback"})

    out.sort(key=lambda c: (c["page"] or 0, c["text"]))
    return out


def load_authority(toc: dict) -> list[dict]:
    """层级权威 ← `toc.v2.json` 的 `nodes`（跳过非正文；章节层级就是它给的）。"""
    return [n for n in (toc.get("nodes") or [])
            if str(n.get("kind") or "") not in SKIP_KINDS and n.get("level")]


def _score(auth: dict, cand: dict) -> tuple[float, str]:
    """给一个（目录节点, 候选）打分：返回 `(分数, 怎么match的)`；0 分 = 不匹配。"""
    a_text = f"{auth.get('number') or ''} {auth.get('title') or ''}".strip()
    c_text = cand["text"]
    a_number, a_title, _ = parse_title(a_text)
    c_number, c_title, _ = parse_title(c_text)
    if (canonical_number(a_number) == canonical_number(c_number)
            and mathematical_title_key(a_title) == mathematical_title_key(c_title)):
        return 1.0, "exact"
    a_id, c_id = identity("", a_text), identity("", c_text)
    if a_id and a_id == c_id:
        return 1.0, "exact"
    if len(a_id) >= SUBSTRING_MIN and len(c_id) >= SUBSTRING_MIN:
        if a_id in c_id or c_id in a_id:
            return 0.95, "substring"
    score = title_similarity(a_text, c_text)
    if score >= FUZZY_MIN:
        return score, "fuzzy"
    return 0.0, ""


def _math_signature(title: str) -> str:
    """Protect mathematical meaning before legacy fuzzy spelling comparison.

    Only listed presentation commands and symbol equivalences are folded.
    Unknown TeX commands remain in the signature instead of disappearing.
    """
    text = mathematical_title_key(title)
    # Chinese prose can still use legacy spelling similarity. Everything in
    # a mathematical expression (including Unicode letters) remains protected.
    return re.sub(r'[\u3400-\u4dbf\u4e00-\u9fff，。；：、]', '', text)


def _math_compatible(auth: dict, text: str) -> bool:
    return _math_signature(str(auth.get('title') or '')) == _math_signature(parse_title(text)[1])


def _page_ok(auth: dict, cand: dict, page_offset: "int | None") -> tuple[bool, bool]:
    """页码窗口判定：返回 `(过不过, 页码是不是没得比)`。

    为什么必须有：同名标题在真书里跨章重复（实测《数分讲义》6 个"综合习题"），
    只按标题匹配会把它们互相错配 —— 页码是唯一能分开它们的信号。
    换算不出来时**放行并如实标注**（不猜，R-c）。
    """
    auth_pdf = (auth.get("page") or {}).get("pdf")
    cand_page = cand.get("page")
    if auth_pdf in (None, "") or cand_page in (None, ""):
        if page_offset is None and (auth.get("page") or {}).get("printed") not in (None, ""):
            return True, True
        return True, True
    try:
        delta = abs(int(cand_page) - int(auth_pdf))
    except (TypeError, ValueError):
        return True, True
    return delta <= PAGE_WINDOW, False


def _ordered_pairs(pairs: list[tuple], candidates: list[dict]) -> list[tuple]:
    """Maximum ordered matching; one source line can serve only one TOC node."""
    if not pairs:
        return []
    authorities = sorted({p[2] for p in pairs})
    lines = sorted({int(candidates[p[3]]['line']) for p in pairs})
    edges = {}
    for pair in pairs:
        key = (pair[2], int(candidates[pair[3]]['line']))
        if key not in edges or pair[0] > edges[key][0]:
            edges[key] = pair
    scores = [[(0, 0.0)] * (len(lines) + 1) for _ in range(len(authorities) + 1)]
    moves = [[0] * (len(lines) + 1) for _ in range(len(authorities) + 1)]
    for i, ai in enumerate(authorities, 1):
        for j, line in enumerate(lines, 1):
            best, move = scores[i][j - 1], 1
            if scores[i - 1][j] > best:
                best, move = scores[i - 1][j], 2
            edge = edges.get((ai, line))
            if edge:
                count, score = scores[i - 1][j - 1]
                matched = (count + 1, score + edge[0])
                if matched > best:
                    best, move = matched, 3
            scores[i][j], moves[i][j] = best, move
    out = []
    i, j = len(authorities), len(lines)
    while i and j:
        move = moves[i][j]
        if move == 3:
            out.append(edges[(authorities[i - 1], lines[j - 1])])
            i, j = i - 1, j - 1
        elif move == 2:
            i -= 1
        else:
            j -= 1
    return list(reversed(out))


def _hierarchical_pairs(pairs: list[tuple], authority: list[dict], candidates: list[dict]) -> list[tuple]:
    """Anchor chapters first, then match ordered children inside parent windows."""
    selected = []
    line_of = {}
    for level in sorted({int(a['level']) for a in authority}):
        eligible = []
        for pair in pairs:
            ai, ci = pair[2:]
            if int(authority[ai]['level']) != level:
                continue
            line = int(candidates[ci]['line'])
            lo = max((found for index, found in line_of.items() if index < ai), default=0)
            hi = min((found for index, found in line_of.items() if index > ai), default=float('inf'))
            if lo < line < hi:
                eligible.append(pair)
        for pair in _ordered_pairs(eligible, candidates):
            selected.append(pair)
            line_of[pair[2]] = int(candidates[pair[3]]['line'])
    return selected


def align(toc: dict, parse_dir: str | Path, *,
          page_offset: "int | None" = None,
          fuzzy_min: float = FUZZY_MIN, resolve_regions: bool = True) -> dict:
    """目录（权威）× 候选（正文）→ 对齐结果 + 账本。**0 token。**"""
    parse_dir = Path(parse_dir)
    authority = load_authority(toc)
    candidates = load_candidates(parse_dir)
    dual_source = (parse_dir / 'toc.excluded.json').is_file()
    non_body = {'toc', 'front', 'exercise', 'answer', 'back'}
    blocked_lines: set[int] = set()
    directory_pages: set[int] = set()
    if dual_source:
        excluded_payload = _load(parse_dir / 'toc.excluded.json') or {}
        directory_pages = {int(e['toc_page']) for e in excluded_payload.get('all_entries', [])
                           if isinstance(e.get('toc_page'), int) and e['toc_page'] > 0}
        from ..structure.structure import load_regions
        region_map = load_regions(parse_dir / 'document.md')
        if region_map is not None:
            doc_lines = _load_lines(parse_dir)
            doc_pages = _line_pages('\n'.join(doc_lines))
            blocked_lines = {n for n in range(1, len(doc_lines) + 1)
                             if region_map.of_line(n, doc_pages.get(n)) in non_body
                             or doc_pages.get(n) in directory_pages}
    # ⚠️ **没有行号的候选不参与匹配**（D-20，2026-09-29 抓到）：
    # 排版兜底候选是按"页 + 文本"给的、没有 `line`（见 `load_candidates`），
    # 而结构层是**按行切正文**的 —— 行号是这套交接的必需字段。
    # 不排掉的话，`line_of[ai] = None` 会在下面的线搜索里炸出
    # `TypeError: '>' not supported between 'NoneType' and 'int'`，
    # 而那个异常被 `_write_alignment` 吞掉 → `toc.decisions.json` **悄悄留旧**。
    line_less = [c for c in candidates if not c.get("line")]
    if line_less:
        candidates = [c for c in candidates if c.get("line")]
    if page_offset is None:
        page_offset = (toc.get("_meta") or {}).get("page_offset")

    # 先把所有（节点, 候选）的分数算出来，再按分数从高到低贪心认领 ——
    # 这样"高置信的配对"不会被"先到先得"的低置信配对抢掉。
    pairs: list[tuple[float, str, int]] = []
    for ai, auth in enumerate(authority):
        for ci, cand in enumerate(candidates):
            if dual_source and (cand.get('region') in non_body or int(cand['line']) in blocked_lines
                                or cand.get('page') in directory_pages):
                continue
            if dual_source:
                actual_number = parse_title(cand['text'])[0]
                if (actual_number and auth.get('number')
                        and canonical_number(actual_number) != canonical_number(str(auth['number']))):
                    continue
                if not _math_compatible(auth, cand['text']):
                    continue
            score, how = _score(auth, cand)
            if not score:
                continue
            ok, _ = _page_ok(auth, cand, page_offset)
            if not ok:
                continue
            pairs.append((score, how, ai, ci))          # type: ignore[arg-type]
    pairs.sort(key=lambda x: (-x[0], x[2], x[3]))
    if dual_source:
        pairs = _hierarchical_pairs(pairs, authority, candidates)

    taken_a: set[int] = set()
    taken_c: set[int] = set()
    matches: list[dict] = []
    for score, how, ai, ci in pairs:                    # type: ignore[misc]
        if ai in taken_a or ci in taken_c:
            continue
        auth, cand = authority[ai], candidates[ci]
        _, page_unknown = _page_ok(auth, cand, page_offset)
        taken_a.add(ai)
        taken_c.add(ci)
        matches.append({
            "toc_id": auth.get("toc_id"),
            "number": auth.get("number"), "level": int(auth.get("level") or 0),
            "authority_title": auth.get("title"),
            "line": cand["line"], "page": cand["page"],
            "candidate_text": cand["text"], "region": cand["region"],
            "how": how, "score": round(float(score), 3),
            "page_unknown": bool(page_unknown),
        })

    # ── 第二遍：**线搜索**（目录里有、MinerU 没标成 title 的条目）────────────────
    # 为什么必须有（2026-09-29 外部基准抓出）：法律书/科普书的小标题**不带编号**，
    # 我们的"编号形状兜底"一条也收不到 —— 《boats to burn》目录 233 条只配上 91 条。
    # 做法与他们（HiPS）的 whitespace 线索同源，但加了**顺序约束**：
    # 每个未配条目只在"上一条已配行 → 下一条已配行"这个窗口里找，找到的头一个够像的就算。
    # 顺序约束是防误配的关键（正文里提到某名词不会改变顺序）。
    lines = _load_lines(parse_dir)
    if lines:
        # 只统计**有行号**的匹配；`None` 一律当"没配上"（防御：绝不让它进 max 比较）
        line_of: dict[int, int] = {}
        for match in matches:
            if not match.get("line"):
                continue
            for index, node in enumerate(authority):
                if node.get("toc_id") == match["toc_id"]:
                    line_of[index] = int(match["line"])
                    break
        searched = 0
        cursor = 0
        for ai, auth in enumerate(authority):
            if ai in taken_a:
                cursor = max(cursor, line_of.get(ai) or cursor)
                continue
            nxt = None
            for aj in range(ai + 1, len(authority)):
                if aj in taken_a:
                    nxt = line_of.get(aj)
                    break
            title = f"{auth.get('number') or ''} {auth.get('title') or ''}".strip()
            hit = _search_line(lines, title, cursor + 1, nxt if nxt else len(lines) + 1,
                               blocked_lines=blocked_lines, authority=auth if dual_source else None)
            if hit is None:
                continue
            score, index, text = hit
            taken_a.add(ai)
            searched += 1
            cursor = index
            line_of[ai] = index
            matches.append({
                "toc_id": auth.get("toc_id"),
                "number": auth.get("number"), "level": int(auth.get("level") or 0),
                "authority_title": auth.get("title"),
                "line": index, "page": None,
                "candidate_text": text, "region": "body",
                "how": "line-search", "score": round(float(score), 3),
                "page_unknown": True,
            })

    # A visual teaching title may be misclassified by the old region state
    # machine. Recover only after both body-only matching passes failed, and
    # only from one exact numbered title outside front matter / TOC pages.
    if dual_source:
        authority_index = {a['toc_id']: i for i, a in enumerate(authority)}
        for ai, auth in enumerate(authority):
            if ai in taken_a:
                continue
            recovery = []
            found_lines = {authority_index[m['toc_id']]: int(m['line']) for m in matches}
            lo = max((line for index, line in found_lines.items() if index < ai), default=0)
            hi = min((line for index, line in found_lines.items() if index > ai), default=float('inf'))
            for ci, cand in enumerate(candidates):
                if ci in taken_c or cand.get('region') not in {'exercise', 'answer', 'front', 'back'}:
                    continue
                if not lo < int(cand['line']) < hi or cand.get('page') in directory_pages:
                    continue
                if cand.get('region') in {'front', 'back'} and (
                        not directory_pages or not isinstance(cand.get('page'), int)):
                    continue
                actual_number, actual_title, _ = parse_title(cand['text'])
                if (canonical_number(actual_number) != canonical_number(str(auth.get('number') or ''))
                        or mathematical_title_key(actual_title) != mathematical_title_key(str(auth.get('title') or ''))):
                    continue
                if not _math_compatible(auth, cand['text']):
                    continue
                if _page_ok(auth, cand, page_offset)[0]:
                    recovery.append((ci, cand))
            if len(recovery) == 1:
                ci, cand = recovery[0]
                taken_a.add(ai)
                taken_c.add(ci)
                matches.append({'toc_id': auth.get('toc_id'), 'number': auth.get('number'),
                                'level': int(auth.get('level') or 0), 'authority_title': auth.get('title'),
                                'line': cand['line'], 'page': cand['page'], 'candidate_text': cand['text'],
                                'region': 'body', 'previous_region': cand.get('region'),
                                'how': 'authority-region-recovery', 'score': 1.0,
                                'page_unknown': _page_ok(auth, cand, page_offset)[1]})
    matches.sort(key=lambda m: (m["line"], m["toc_id"] or ""))
    if dual_source:
        positions = {m['toc_id']: int(m['line']) for m in matches}
        ordered_lines = [positions[a['toc_id']] for a in authority if a['toc_id'] in positions]
        if ordered_lines != sorted(set(ordered_lines)):
            raise ValueError('教学目录的正文落点发生交叉或重复，不能作为切分权威')

    unmatched_authority = [
        {"toc_id": a.get("toc_id"), "number": a.get("number"),
         "level": a.get("level"), "title": a.get("title"),
         "why": "目录里有、正文候选里找不到（正文可能没这一节，或 MinerU 没标成 title）"}
        for i, a in enumerate(authority) if i not in taken_a]
    extra_candidates = [
        {"line": c["line"], "page": c["page"], "text": c["text"], "region": c["region"],
         # ⚠️ `from` 必须带出来（D-17，2026-09-29 抓出）：下游按它决定"要不要收这条" ——
         # 少了它，`heading_decisions` 里"是 MinerU 标的就是标题"这条分支永远不触发，
         # 21 条 extras 一条也进不了结构层（实测决策数 281 = 全是 declared，未声明 0）。
         "from": c.get("from"),
         "why": "正文（MinerU）标成了标题、但目录里没有 —— 可能是真节，也可能是页眉/噪声"}
        for i, c in enumerate(candidates) if i not in taken_c]

    levels = {}
    for m in matches:
        levels[int(m["level"])] = levels.get(int(m["level"]), 0) + 1

    exclusions = {}
    if resolve_regions and (parse_dir / 'toc.excluded.json').is_file():
        from .regions import build_regions, require_resolved
        exclusions = build_regions(parse_dir, toc, teaching_matches=matches)
        require_resolved(exclusions)
    return {
        "_meta": {
            "file_type": "toc_alignment", "schema_version": SCHEMA_VERSION,
            "generator": GENERATOR,
            "toc_sha256": (toc.get("_meta") or {}).get("toc_sha256") or "",
            "page_offset": page_offset,
            "counts": {
                "authority": len(authority), "candidates": len(candidates),
                "matched": len(matches), "unmatched_authority": len(unmatched_authority),
                "extra_candidates": len(extra_candidates),
                "dropped_no_line": len(line_less),
                "by_how": {how: sum(1 for m in matches if m["how"] == how)
                           for how in ("exact", "substring", "fuzzy", "line-search")},
                "by_level": {str(k): v for k, v in sorted(levels.items())},
            },
            "field_notes": {
                "matches[]": "目录节点 → 正文候选的落点（line 是 document.md 里的行号）",
                "matches[].how": "怎么匹配上的：exact / substring / fuzzy",
                "matches[].page_unknown": "两源页码换算不出来 → 没做页码校验（不猜，如实标注）",
                "unmatched_authority[]": "目录有、正文没配上 —— 不是错误，是**事实**（要看得见）",
                "extra_candidates[]": "正文标成标题、目录没有 —— 真节候选 或 页眉噪声",
            },
        },
        "matches": matches,
        "unmatched_authority": unmatched_authority,
        "excluded_regions": exclusions,
        "extra_candidates": extra_candidates,
    }


def _load_lines(parse_dir: Path) -> list[str]:
    doc = parse_dir / "document.md"
    if not doc.is_file():
        return []
    try:
        return doc.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _search_line(lines: list[str], title: str, lo: int, hi: int, *,
                 blocked_lines: set[int] | None = None,
                 authority: dict | None = None) -> "tuple[float, int, str] | None":
    """在 `lines[lo:hi)`（1-based 行号）里找最像 `title` 的**标题行**。

    只认"像标题的行"：非空、不太长、不以句读结尾。
    返回 `(分, 行号, 行文)`；没有够像的就 None（不猜）。
    """
    wanted = norm_title(title)
    wanted_number, wanted_title, _ = parse_title(title)
    wanted_number = number_key(wanted_number)
    if not wanted:
        return None
    best = None
    upper = min(hi, len(lines) + 1)
    for index in range(max(lo, 1), upper):
        if blocked_lines and index in blocked_lines:
            continue
        text = lines[index - 1].strip()
        if not text or len(text) > LINE_SEARCH_MAX:
            continue
        if text.endswith(_LINE_TAIL_BAD):
            continue
        if authority is not None and not _math_compatible(authority, text):
            continue
        candidate_number, candidate_title, _ = parse_title(text)
        candidate_number = number_key(candidate_number)
        if wanted_number and candidate_number and wanted_number != candidate_number:
            continue
        # Fold § / optional marks / number separators, but retain the number.
        # A title-only identity can wrongly select an earlier section's header.
        if (candidate_number == wanted_number
                and mathematical_title_key(candidate_title) == mathematical_title_key(wanted_title)):
            return 1.0, index, text
        score = title_similarity(title, text)
        if score >= LINE_SEARCH_MIN and (best is None or score > best[0]):
            best = (score, index, text)
    return best


#: 句子片段起头（**只用于额外候选**；目录声明过的标题一律不过滤）。
#: 来历（2026-09-30 实测《电路分析基础》）：MinerU 把习题第 2-25 题那一行
#: （`2-25 若节点方程为…`）标成了 title，编号 `2-25` 解析成两层 → 被当成真节 →
#: 多出一个 390 字的假小节 `CH02-S06 若节点方程为`。题号行的文字多数是句子片段，
#: 而真节标题极少以这些虚词起头。
_FRAGMENT_LEAD = re.compile(
    r"^(?:若|则|当|设|由|故|所以|因为|如果|试|求|已|如|亦即|而且|但|而|又|于是|因此)")


def looks_like_sentence_fragment(title: str) -> bool:
    """额外候选的标题是不是"句子片段"（题号行/续行）。"""
    return bool(_FRAGMENT_LEAD.match(str(title or "").strip()))


def heading_decisions(alignment: dict, *, include_extras: bool = True,
                      dropped: list | None = None) -> list[dict]:
    """对齐结果 → 结构层要的"标题判定"：`[(line, level, title, toc_id, declared)]`。

    层级**直接取自目录**（方案 A 的核心）；`line` 取自 MinerU 的候选。
    排序按行号 —— 结构层按行号切正文。

    ## `include_extras`：目录没声明、但承载正文的标题（方案「乙」的入口）

    实测教训（2026-09-28）：只把"目录声明的标题"交给结构层时，《复变函数》的
    单元数从 **75 掉到 42** —— 因为它的印刷目录**只列到"节"**，那 37 个小节的正文
    仍留在正文里，却没有任何标题把它们切出来，于是**内容被并回父节、小节消失**。
    所以这里把 `extra_candidates` 里"**看着像真节**"的那些也交给结构层：

    * 只在 `region == "body"` 里收（前言/目录/习题/答案一律不收）；
    * 必须**带完整编号**（`1.1.1` 这种，`parse_title` 的深度 ≥ 2）——
      这一条把页眉、例题行、图注挡在外面（它们通常没有编号路径）；
    * 标 `declared: false`：**它们不进骨架**（导航仍以目录为准），
      但承载正文、要切成单元（R9/R10）。
    """
    out = []
    for m in alignment.get("matches") or []:
        out.append({"line": int(m["line"]), "level": int(m["level"]),
                    "number": str(m.get("number") or ""),
                    "title": str(m.get("authority_title") or ""),
                    "what": str(m.get("candidate_text") or ""),
                    "toc_id": m["toc_id"], "how": m["how"], "score": m["score"],
                    "declared": True})
    if include_extras:
        # ── 编号去重：**编号已经被声明的 extras 不收**（D-22，2026-09-29 实测抓到）──
        # 《电路分析基础》的习题页边上印着**书眉**（`§ 4-9 T 形网络和 II 形网络的等效变换`），
        # MinerU 把它标成了 title → 成了 extra → 结构层里多出一个**假的 §4.9**
        # （与真 §4.9 重复，而且带 OCR 错的「II 形」）。
        # 判据用**编号**而不是标题：书眉与真标题文字可能只差一个字（π/II），
        # 但编号一定一样（`§4-9` 与 `4.9` 折平后同键）—— 这是精确信号，不猜。
        declared_numbers = {number_key(str(d.get("number") or ""))
                            for d in out if d.get("declared") and d.get("number")}
        declared_numbers.discard("")
        dropped_dup: list[dict] = []
        for e in alignment.get("extra_candidates") or []:
            if str(e.get("region") or "") != "body":
                continue
            number, title, depth = parse_title(str(e.get("text") or ""))
            # ⚠️ **试过放开"MinerU 说是 title 就收（哪怕没编号）"，实测后回退**（2026-09-29）：
            #    * 英文书上收益 **0** —— 那 21 条 extras 的 `region` 全是 `front`
            #      （书标题 / `sui generis` / 章作者名），本来就该被区域排除；
            #    * 中文书上代价明显 —— 《数值计算方法》标题 90 → **154**、
            #      《数分讲义》119 → **149**，**单元数一条没多**，结构一致率 1.0 → 0.988
            #      （未声明的标题插在中间会改变后续节点的父子归属）。
            #    结论：**"带编号路径"这条要求留着**（它是精度闸），脏信号（形状/排版）更得留着。
            if not number or depth < 2:
                continue          # 没编号路径的（页眉/例题/图注）不收
            if looks_like_sentence_fragment(title):
                dropped_dup.append({"line": int(e["line"]), "number": number, "title": title,
                                    "why": "标题像句子片段（题号行/续行）—— 不当真节（2026-09-30）"})
                continue
            if number_key(number) in declared_numbers:
                dropped_dup.append({"line": int(e["line"]), "number": number,
                                    "title": title,
                                    "why": "编号与已声明的节相同 —— 判定为书眉/重复条（D-22）"})
                continue
            out.append({"line": int(e["line"]), "level": min(max(depth, 1), 4),
                        "number": number, "title": title,
                        "what": str(e.get("text") or ""),
                        "toc_id": None, "how": "extra", "score": None,
                        "declared": False, "level_source": "number-depth"})
        if dropped is not None:
            dropped.extend(dropped_dup)
    # 未声明节点的层级：**就近推断**（前一条节点的层级 + 1，封顶 `max_level`），
    # 并标 `level_source=inferred` —— 方案 A 的铁律是"层级由目录给"，
    # 所以凡不是目录给的一律**显式标出来**，交 LLM 复核（P7）或下游自行处置。
    from .regions import span_at
    kept = []
    for decision in out:
        excluded = span_at(alignment.get('excluded_regions') or {}, decision['line'])
        if excluded:
            if decision.get('declared'):
                raise ValueError(f'教学目录条目与排除范围冲突：{decision.get("toc_id")}')
            if dropped is not None:
                dropped.append({**decision, 'why': '位于已确认的非教学目录范围',
                                'excluded_entry_id': excluded['entry_id']})
        else:
            kept.append(decision)
    out = kept
    out.sort(key=lambda d: d["line"])
    last_level = 0
    for item in out:
        if item.get("declared"):
            last_level = int(item.get("level") or 0)
            continue
        if item.get("level_source") in (None, "", "inferred"):
            item["level"] = min(max(last_level + 1, 1), 4)
            item["level_source"] = "inferred"
    return out


def render_text(alignment: dict) -> str:
    """给人看的一页。"""
    c = alignment["_meta"]["counts"]
    lines = [
        "方案 A 对齐：目录（权威）⇄ 正文候选（MinerU title 块）",
        f"  目录节点 {c['authority']} ｜ 正文候选 {c['candidates']} ｜ "
        f"配上 {c['matched']}（exact {c['by_how']['exact']} / "
        f"substring {c['by_how']['substring']} / fuzzy {c['by_how']['fuzzy']}）",
        f"  目录有正文没有 {c['unmatched_authority']} ｜ 正文有目录没有 {c['extra_candidates']}",
        f"  配上来的层级分布：{c['by_level']}",
    ]
    for m in (alignment.get("matches") or [])[:6]:
        lines.append(f"    ✓ [{m['how']} {m['score']}] lv{m['level']} {m['authority_title']}"
                     f"  ←  正文第 {m['line']} 行「{m['candidate_text'][:34]}」")
    for u in (alignment.get("unmatched_authority") or [])[:4]:
        lines.append(f"    ✗ 目录未配：{u.get('number')} {u.get('title')}")
    for e in (alignment.get("extra_candidates") or [])[:4]:
        lines.append(f"    + 正文多出：第 {e['line']} 行「{e['text'][:34]}」")
    return "\n".join(lines)


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


__all__ = ["align", "heading_decisions", "load_authority", "load_candidates",
           "render_text", "FUZZY_MIN", "PAGE_WINDOW", "SUBSTRING_MIN"]
