"""三源对账：候选 → `toc.json`(v2) 的 `nodes` / `disagreements` / `unresolved` / `suspicious`。

## 对账在回答什么问题

一句话：**"这句话，几个源能证明它？它们说的是同一句话吗？"**

| 情况 | 结论 | 落到哪 |
|---|---|---|
| 多源、写法一致 | 可信 | `confirmed: true`，`confidence` 拉高 |
| 多源、写法**不一致** | 有一条是错的 | `disagreements`（含各源原文，等模型视觉裁决） |
| 只有一源 | 可信度低 | `confirmed: true` 但 `confidence` 只到 medium |
| 只有一源且**落不了页** | 存疑 | `confirmed: false`，进 `counts.unconfirmed` |

## 四条铁规矩（设计稿 §2.4）

* **R-a 只增不丢**：只要有一个源说了这一条，它就进 `nodes`。
  印刷目录里"书签没有"的条目**也算数**（记一笔 `source-extra`），绝不因为"骨架是书签"
  就把它扔掉 —— 这正是老实现 C1 的病。
* **R-b 不确定要说出来**：抽不出来的进 `unresolved`，对不上的进 `disagreements`，
  可疑的进 `suspicious`。
* **R-c 不猜**：两边层级不一致就**如实记冲突**，不挑一个"看起来更合理"的。
  （裁决是 M3 模型视觉复核的活；这一步只负责把冲突摆出来。）
* **R-d 书的目录是唯一骨架**：正文形状（`body-heading-shapes`）**只用来提供旁证**，
  永远不会因此新增或删除一个节点。

## 幂等（A4）

`toc_id` 按"节点在最终序列里的位置"编号（`T-0001`…），层级按 `parent` 显式挂链，
所有派生字段都在这里**算**出来（不复制、不留时间戳）。
"""
from __future__ import annotations

import re

from .contract import (SOURCE_BODY, SOURCE_BOOKMARKS, SOURCE_PRINTED, build_toc,
                       empty_toc, number_key)
from .sources import (Candidate, PageMap, SourceResult, align_by_key, identity,
                      is_exercise_group)
from ..structure.structure import title_similarity

#: 目录标题与正文标题"还算同一个"的相似度下限。低于它就**不算同一节**
#: （实测 `基级数` vs 正文的 `幂级数` 只有 0.67，但它们确实是同一条 —— 这是 OCR 错字；
#: 而 0.5 以下基本是彻底不同的两条，不该互相作证）。
TITLE_AGREE_SIMILARITY = 0.5

#: 同名正文条目的**页码消歧窗口**（印刷页差多少以内算"就是这一条"）。
#: 实测《数分讲义第二册》：6 个「第 N 章综合习题」同名，章间距 40~90 页，
#: 而同一节的正文首行离目录页码通常在个位数以内 —— 40 页足够分开，又不会误伤。
#: 2026-09-28 核验抓出：不加这个窗口时，第 9–13 章的旁证全被写成第 8 章那条。
BODY_PAGE_WINDOW = 40
from ..structure.structure import is_non_teaching

#: 编号跳跃的判据：同层、同父前缀下"上一号 + 1 != 这一号"
_SPLIT_NUMBER = re.compile(r"^(?P<prefix>.*?)(?P<tail>\d+)$")


def classify(number: str, title: str, level: int) -> str:
    """章 / 节 / **非教学**（前言·目录·附录·习题集）。

    为什么非教学要单独一档：`tools/parse_report.py` 的"章数对账"用它；
    《数分讲义第二册》的 6 个 `第 N 章综合习题` 就是被这一档挡在"章"之外的
    （实测：老实现把习题集算成假章，章数对账立刻误报）。
    """
    if is_non_teaching(title) or is_exercise_group(title):
        return "non-teaching"
    return "chapter" if level == 1 else "section"


def reconcile(*, slug: str, book_title: str, bookmarks: SourceResult,
              printed: SourceResult, body: SourceResult,
              page_map: PageMap | None = None, pdf_sha256: str = "",
              document_sha256: str = "") -> dict:
    """三源 ⇒ `toc.json`(v2)。"""
    if not bookmarks.candidates and not printed.candidates:
        payload = empty_toc(slug=slug, book_title=book_title,
                            pdf_sha256=pdf_sha256,
                            document_sha256=document_sha256,
                            sources_used=[], reason="三个源都没抽出任何目录条目")
        payload["unresolved"].extend(bookmarks.unresolved)
        payload["unresolved"].extend(printed.unresolved)
        return payload

    # ⚠️ 一个标题键可能对应**好几条**正文条目：实测《线性代数》第 1 章里
    # `1.1.2 向量的线性运算` 与 `1.1 向量的线性运算` 同名，用单值 dict 会被后者覆盖，
    # 于是 `1.1` 那一行被拿去跟"其实是 1.1.2 的正文条目"比层级，凭空报出 3 条假冲突。
    # 所以这里存**列表**，比对时按层级挑最合适的那条。
    body_by_key: dict[str, list[Candidate]] = {}
    body_by_number: dict[str, Candidate] = {}
    for entry in body.candidates:
        body_by_key.setdefault(identity(entry.number, entry.title), []).append(entry)
        if entry.number:
            body_by_number.setdefault(number_key(entry.number), entry)

    # 书签候选 ⇄ 印刷目录条目：用同一套对齐口径（见 sources.align_by_key 的说明）。
    # 候选已经过了位移换算，这里比较的就是"真实的层级"，容差不放宽（3 只是上限）。
    pairs, _, _ = align_by_key(
        [{"text": c.text, "level": c.level, "pdf_page": c.pdf}
         for c in bookmarks.candidates], printed.candidates)
    bm_pairs = dict(pairs)

    used_printed: set[int] = set()
    nodes: list[dict] = []
    disagreements: list[dict] = []
    unresolved: list[dict] = list(bookmarks.unresolved) + list(printed.unresolved)

    # ① 以书签为骨架（有书签时它就是"书自己声明的顺序"；没有就整个跳过）
    for position, candidate in enumerate(bookmarks.candidates):
        matched_index = bm_pairs.get(position)
        matched = printed.candidates[matched_index] if matched_index is not None else None
        if matched is not None:
            used_printed.add(matched_index)
        nodes.append(_node_from(candidate, matched=matched, body=body_by_key,
                                body_by_number=body_by_number, page_map=page_map,
                                disagreements=disagreements,
                                unresolved=unresolved, source=SOURCE_BOOKMARKS))

    # ② 印刷目录里有、书签没有的：**照样进 nodes**（R-a）
    for index, candidate in enumerate(printed.candidates):
        if index in used_printed:
            continue
        node = _node_from(candidate, matched=None, body=body_by_key,
                          body_by_number=body_by_number, page_map=page_map,
                          disagreements=disagreements, unresolved=unresolved,
                          source=SOURCE_PRINTED)
        # ⚠️ 这里**不许**直接赋值 `confirmed` —— 那样会把 `_node_from` 已经判定的
        # "源间不一致（正文说的和目录不一样）"覆盖掉，冲突被静默抹平（实测踩过：
        # 《复变函数》的 `5.2 基级数` 明明记了 disagreement，节点却还是 confirmed:true）。
        # 只允许"**加严**"：原本就不确认的，绝不在这里变回确认。
        node["confirmed"] = (bool(node["confirmed"]) and bool(candidate.printed)
                             and bool(candidate.structural))
        if bookmarks.candidates:
            disagreements.append({
                "kind": "source-extra", "toc_id": None, "title": candidate.text,
                "bookmarks": None, "printed": candidate.text, "body": None,
                "resolution": "以印刷目录为准（书签缺这一条），条目照常保留",
                "arbiter": "rule", "confidence": "medium",
                "why": "书签里没有这条，但印刷目录里有 —— 书签不是全本（R-a 只增不丢）",
            })
        nodes.append(node)

    nodes = _attach_parents(nodes)
    if page_map is not None:
        disagreements.extend(page_map.conflicts)
    _attach_ids(disagreements, nodes)
    suspicious = detect_suspicious(nodes)
    unresolved.extend(_page_gaps(nodes, page_map))
    confidence = grade(nodes, disagreements, unresolved, suspicious,
                       has_bookmarks=bool(bookmarks.candidates))
    # D-3 的诚实约束：位移量是**按书签形状兜底**估的（印刷目录不可用）时，
    # 层级可信度**降一档**（high → medium）—— 树形对，但绝对层级没被第二只眼睛确认过。
    if (bookmarks.detail or {}).get("shift_votes", {}).get("method") == "bookmark-shape" \
            and confidence == "high":
        confidence = "medium"
    sources_used = []
    if bookmarks.available:
        sources_used.append(SOURCE_BOOKMARKS)
    if printed.available:
        sources_used.append(SOURCE_PRINTED)
    if body.available:
        sources_used.append(SOURCE_BODY)

    return build_toc(
        slug=slug, book_title=book_title, nodes=nodes, disagreements=disagreements,
        unresolved=unresolved, suspicious=suspicious, sources_used=sources_used,
        confidence=confidence, pdf_sha256=pdf_sha256, document_sha256=document_sha256,
        page_offset=(page_map.offset if page_map else None),
        field_notes=_notes_with_sources(bookmarks, printed, body, page_map),
    )


def _notes_with_sources(bookmarks: SourceResult, printed: SourceResult,
                        body: SourceResult, page_map: PageMap | None) -> dict:
    """把"这三源各自抽了多少、怎么抽的"写进 `_meta.field_notes`（人看得见账）。"""
    from .contract import DEFAULT_FIELD_NOTES
    notes = dict(DEFAULT_FIELD_NOTES)
    shift_detail = (bookmarks.detail or {}).get("shift_votes") or {}
    notes["_meta.sources_used"] = (
        f"pdf-bookmarks={len(bookmarks.candidates)} 条"
        f"（层级判定 {shift_detail.get('method', bookmarks.reason or '无书签')}）; "
        f"printed-toc={len(printed.candidates)} 条（{printed.reason or 'ok'}）; "
        f"body-heading-shapes={len(body.candidates)} 条"
        f"（{body.reason or 'ok'}，**只作旁证，无增删权**）")
    notes["_meta.page_offset"] = (
        "印刷页 → PDF 页 的偏移量 "
        + (f"{page_map.offset}（{page_map.method}；证据 {page_map.clean_votes} 条）"
           if page_map is not None and page_map.offset is not None
           else "**算不出来**（没有书签落点可对，或证据不足）—— 不猜，"
                "`page.pdf` 留空由 A2 空率反映"))
    return notes


def _node_from(candidate: Candidate, *, matched: Candidate | None, body: dict,
               body_by_number: dict, page_map: PageMap | None,
               disagreements: list[dict], unresolved: list[dict],
               source: str) -> dict:
    """一个候选 → 一个节点（含跨源比对与冲突记账）。"""
    sources = [source]
    evidence_agree = True
    # 层级：两源都"有结构"（带编号）却不一样时，**以印刷目录为准**（用户 2026-09-28 定案）。
    # 为什么在这里定而不是只在 disagreement 里记一句：节点自己得带上定好的层级，
    # 否则父子和 `lesson_units` 仍旧按书签的错误层级挂（记账不等于结账）。
    printed_candidate = matched if (matched is not None
                                    and matched.source == SOURCE_PRINTED) else None
    level = candidate.level
    if printed_candidate is not None and candidate.structural \
            and printed_candidate.structural and candidate.level != printed_candidate.level:
        level = printed_candidate.level
    # 先按**编号**找正文条目（`1.1.2` 与 `1.1` 同名时只有编号分得开），
    # 编号对不上再退回"同名条目里挑层级最接近的"
    body_hit = _pick_body_by_number(body_by_number, candidate.number,
                                    candidate.title, level) \
        or _pick_body(body.get(identity(candidate.number, candidate.title)),
                      level, printed_page=_printed_of(candidate, matched))

    # 页码：印刷目录自带 `printed`，PDF 页由偏移量换算（换不出来就留 None，A2 看空率）
    printed_page = candidate.printed or (matched.printed if matched else None)
    pdf_page = candidate.pdf if candidate.pdf else (matched.pdf if matched else None)
    if page_map is not None:
        if pdf_page is None and printed_page is not None:
            pdf_page = page_map.to_pdf(printed_page)
        if printed_page is None and pdf_page is not None:
            printed_page = page_map.to_printed(pdf_page)

    if matched is not None:
        sources.append(matched.source)
        # ── 两源配上了、但**标题文字不同** → 记一条待裁决冲突（D-12）─────────────
        # 实测《电路分析基础》章 4：印刷目录 `4 分解方法及单、双口电路`、
        # 书签 `第四章 分解方法及单、双口网络` —— 靠章号配上了（D-9），
        # 但"电路/网络"到底哪个对，**只有看图能定**（P7）。
        # 原来这里只比层级，标题差异**静默**带过去（下游会看到书上印的另一种写法）。
        if identity("", candidate.title) != identity("", matched.title):
            evidence_agree = False
            disagreements.append({
                "kind": "source-title", "toc_id": None, "title": candidate.title,
                "bookmarks": candidate.text if source == SOURCE_BOOKMARKS else None,
                "printed": matched.text, "body": None,
                "resolution": "留待模型视觉复核裁决（以目录页截图为准，P7）",
                "arbiter": "pending", "confidence": "low",
                "why": f"两源按编号配上了，但标题写法不同："
                       f"「{candidate.title}」 vs 「{matched.title}」",
            })
        if candidate.level != matched.level and candidate.structural and matched.structural:
            evidence_agree = False
            # ── 层级按**印刷目录**定（用户 2026-09-28 定案）────────────────────
            # 用户原话："「第 N 章综合习题」——**第一级**"。
            # 依据：P7 定的是"结构冲突以印刷目录/视觉复核为准"，而**书签常常多包一层、
            # 层级是排版的副产品**（实测《数值计算方法》书签根节点是 `目录`、要位移 1 级）。
            # 所以这里不再一律挂 `pending`：**能按目录定的当场定**，并逐条记账。
            # ⚠️ 只裁决**层级**；标题内容不一致（OCR 错字）仍走 `pending` 交视觉复核。
            printed_level = matched.level if matched.source == SOURCE_PRINTED else candidate.level
            disagreements.append({
                "kind": "source-conflict", "toc_id": None, "title": candidate.title,
                "bookmarks": candidate.text if source == SOURCE_BOOKMARKS else None,
                "printed": matched.text, "body": None,
                "resolution": f"按**书的目录**定层级：取目录的 {printed_level} 级"
                              f"（P7：结构冲突以目录为准；书签层级是排版副产品）",
                "arbiter": "rule", "confidence": "medium",
                "why": f"层级不一致：{source} 说是 {candidate.level} 级，"
                       f"{matched.source} 说是 {matched.level} 级 —— "
                       f"以印刷目录的 {printed_level} 级为准",
            })
    if body_hit is not None:
        sources.append(SOURCE_BODY)
        if identity("", body_hit.title) != identity("", candidate.title):
            evidence_agree = False
            disagreements.append({
                "kind": "body-title", "toc_id": None, "title": candidate.title,
                "bookmarks": None, "printed": candidate.text, "body": body_hit.text,
                "resolution": "留待模型视觉复核裁决（以目录页截图为准，P7）",
                "arbiter": "pending", "confidence": "low",
                "why": f"正文里写的是「{body_hit.title}」，目录里写的是「{candidate.title}」"
                       f"—— 其中一个多半是 OCR 错字",
            })
        if body_hit.level != level and body_hit.structural:
            evidence_agree = False
            disagreements.append({
                "kind": "body-level", "toc_id": None, "title": candidate.title,
                "bookmarks": None, "printed": candidate.text, "body": body_hit.text,
                "resolution": "正文形状只作旁证（R-d），不据此改层级",
                "arbiter": "rule", "confidence": "low",
                "why": f"正文里的编号形状是 {body_hit.level} 级，目录说 {level} 级",
            })

    # 编号：书签常常**不带编号**（实测《线性代数：原理与应用》的书签标题就是
    # `线性空间与子空间`，而印刷目录写 `1.1 线性空间与子空间`）。
    # 两源已经对上是同一条，就把印刷目录那份编号补上 —— 这不是猜，是**取两源的并集**。
    number = candidate.number or (matched.number if matched else "")
    kind = classify(number, candidate.title, level)
    has_page = bool(printed_page) or bool(pdf_page)
    # 确认的判据：有页码、或两源以上一致、或"印刷目录自带页码"
    confirmed = evidence_agree and (has_page or bool(matched)
                                    or source != SOURCE_BOOKMARKS)
    if not has_page:
        unresolved.append({
            "raw": candidate.text, "source": source, "page": pdf_page,
            "why": "这一条落不到页码（印刷页与 PDF 页都没有）—— 记账，条目照样保留"
                   "（R-a），A2 的空率会反映它",
        })
    return {
        "toc_id": "", "level": int(level), "number": number,
        "title": candidate.title, "parent": None, "order": 0,
        "page": {"printed": printed_page, "pdf": pdf_page},
        "kind": kind, "confirmed": bool(confirmed),
        "evidence": {"from": sorted(set(sources)), "agree": bool(evidence_agree)},
    }


def _pick_body_by_number(by_number: dict, number: str, title: str,
                         level: int) -> Candidate | None:
    """按**编号**找正文条目，并要求标题**还算同一个**。

    ⚠️ 为什么标题必须核对（实测《复变函数》）：目录页把 `5.2 幂级数` OCR 成 `5.2 基级数`，
    而正文里是 `5.2 幂级数`。如果只按编号认领，正文就会被当成"同意目录"，
    于是这条**静默变成"两源一致"** —— 正好把我们要治的错字藏起来。
    核对标题之后：正文会成为**第三个源**，把冲突摆到 `disagreements` 上（R-b/R-c）。
    """
    if not number:
        return None
    hit = by_number.get(number_key(number))
    if hit is None:
        return None
    if title and title_similarity(hit.title, title) < TITLE_AGREE_SIMILARITY:
        return None
    return hit


def _pick_body(candidates: list[Candidate] | None, level: int,
               *, printed_page=None) -> Candidate | None:
    """同名正文条目里挑一条。

    两道消歧，按顺序：

    1. **页码邻近**（2026-09-28 核验抓出的 bug）：同名条目可能跨章重复 ——
       实测《数分讲义第二册》6 个「第 N 章综合习题」标题都叫「综合习题」，
       原实现一律返回**第一条**（第 8 章那条），于是第 9–13 章的旁证全写成
       "正文=第 8 章综合习题"，**一个错配被重复报 6 次**，把真分歧淹掉。
       现在：目录给了印刷页时，优先挑页码在 ±`BODY_PAGE_WINDOW` 以内的那条。
    2. **层级最接近**（原有逻辑）：同名条目很常见（`向量的线性运算`
       同时是节和小节），挑层级最接近的，免得报假冲突。
    """
    if not candidates:
        return None
    pool = list(candidates)
    if printed_page is not None:
        near = [c for c in pool
                if c.printed is not None
                and abs(int(c.printed) - int(printed_page)) <= BODY_PAGE_WINDOW]
        if near:
            pool = near
    return min(pool, key=lambda c: (abs(int(c.level) - int(level)), c.level))


def _printed_of(candidate: Candidate, matched: Candidate | None):
    """这条目录条目的印刷页码（两源哪边有就用哪边）—— 给正文旁证消歧用。"""
    for source in (candidate, matched):
        if source is not None and source.printed is not None:
            return source.printed
    return None


def _attach_parents(nodes: list[dict]) -> list[dict]:
    """按层级栈挂 `parent`（显式父子，不靠下游"用 level 现推"）。

    栈里只留"比我浅"的最近一个；同级/更深的一律弹掉。
    ⚠️ 第一层如果没有父，就如实是 `null` —— 不为了"树好看"硬造一个根。
    """
    for index, node in enumerate(nodes, 1):
        node["toc_id"] = f"T-{index:04d}"
    stack: list[dict] = []
    for node in nodes:
        while stack and int(stack[-1]["level"]) >= int(node["level"]):
            stack.pop()
        node["parent"] = stack[-1]["toc_id"] if stack else None
        stack.append(node)
    return nodes


def _attach_ids(disagreements: list[dict], nodes: list[dict]) -> None:
    """给冲突补 `toc_id`（按标题找回对应节点；找不到就留 `null`，不硬编）。"""
    by_title: dict[str, str] = {}
    for node in nodes:
        by_title.setdefault(identity(node.get("number") or "", node["title"]),
                            node["toc_id"])
    for row in disagreements:
        if row.get("toc_id"):
            continue
        for text in (row.get("bookmarks"), row.get("printed"), row.get("body"),
                     row.get("title")):
            if not text:
                continue
            from .sources import parse_title
            number, title, _ = parse_title(str(text).split(" ⇄ ")[0])
            hit = by_title.get(identity(number, title))
            if hit:
                row["toc_id"] = hit
                break


# ── 可疑项（免费筛子；规则只挑，不判） ─────────────────────────────────
def detect_suspicious(nodes: list[dict]) -> list[dict]:
    """挑出"形状可疑"的条目：编号跳跃 / 层级跳变 / 编号倒回。

    这里**只挑不判**（设计稿 §2.2 第 ③ 步的输入）：规则负责"找出哪里可能不对"，
    模型负责"说哪个对"。
    """
    out: list[dict] = []
    previous_same_parent: dict[str, dict] = {}
    previous: dict | None = None
    for node in nodes:
        level = int(node["level"])
        number = str(node.get("number") or "")
        key = str(node.get("parent") or "")
        # 模型捞回来的条目（`model-vision`）**位置本来就不确定**，不该报"层级跳变"
        # ——那是我们自己的不确定，不是书的形状可疑（Q-a 定案 α 已把它标 confirmed:false）
        from_model = "model-vision" in (node.get("evidence") or {}).get("from", [])
        if previous is not None and level > int(previous["level"]) + 1 and not from_model:
            out.append({"toc_id": node["toc_id"], "title": node.get("title"),
                        "why": f"层级从 {previous['level']} 直接跳到 {level}"
                               f"（上一级是「{previous.get('title')}」）"})
        sibling = previous_same_parent.get(key)
        if sibling is not None and number and sibling.get("number"):
            gap = _number_gap(str(sibling["number"]), number)
            if gap:
                out.append({"toc_id": node["toc_id"], "title": node.get("title"),
                            "why": gap})
        previous_same_parent[key] = node
        previous = node
    return _dedupe(out)


def _number_gap(left: str, right: str) -> str:
    """同层相邻两条的编号是不是"断了"；断了就返回人话。"""
    left_match = _SPLIT_NUMBER.match(str(left).strip())
    right_match = _SPLIT_NUMBER.match(str(right).strip())
    if not left_match or not right_match:
        return ""
    if left_match.group("prefix") != right_match.group("prefix"):
        return ""
    try:
        left_value = int(left_match.group("tail"))
        right_value = int(right_match.group("tail"))
    except ValueError:
        return ""
    if right_value == left_value + 1:
        return ""
    if right_value <= left_value:
        return f"编号倒回：{left} 之后是 {right}"
    return f"编号跳跃：{left} 之后直接是 {right}（缺 {right_value - left_value - 1} 条）"


def _dedupe(rows: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for row in rows:
        key = (row.get("toc_id"), row.get("why"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _page_gaps(nodes: list[dict], page_map: PageMap | None) -> list[dict]:
    """整本书**一条页码都没有**时，记一笔"这一层落不了页"（不是每条都报）。"""
    if not nodes:
        return []
    with_page = sum(1 for n in nodes if (n.get("page") or {}).get("printed")
                    or (n.get("page") or {}).get("pdf"))
    if with_page:
        return []
    return [{
        "raw": "（整本）", "source": "pipeline", "page": None,
        "why": f"{len(nodes)} 条目录**一条都没落到页**"
               + ("（这本书没有书签，印刷页 ↔ PDF 页的偏移量也算不出来）"
                  if page_map is None or page_map.offset is None else ""),
    }]


# ── 置信度（A6/A7 要的"能不能信"） ─────────────────────────────────────
def grade(nodes: list[dict], disagreements: list[dict], unresolved: list[dict],
          suspicious: list[dict], *, has_bookmarks: bool) -> str:
    """整本目录的置信度：`high` / `medium` / `low`。

    | 结论 | 条件 |
    |---|---|
    | **low** | 节点不足 3 条、或有待裁决的源间冲突、或有"层级无法判定" |
    | **medium** | 只有一个源、或有可疑项、或有落不了页/没配上的条目 |
    | **high** | 多源一致、无待裁决冲突、无账目缺口 |

    为什么"有书签"能抬一档：书签是书自己写在文件里的结构，比 OCR 出来的目录页可靠
    （老实现自己的结论，保留）。
    """
    if len(nodes) < 3:
        return "low"
    pending = [d for d in disagreements if d.get("arbiter") == "pending"]
    level_unknown = [u for u in unresolved if "层级无法判定" in str(u.get("why") or "")]
    # ⚠️ 判据从"**有没有**待裁决"改成"**占比**"（D-8，2026-09-28 实测抓出）：
    #    原实现只要有一条 pending 就判 low —— 实测《电磁学》**240 个节点里只有 3 条**
    #    （1.25%）也被打成 low，而 low 的语义是"目录层自己都不可信、先停下等人看"。
    #    占比判据才反映真实可信度：少量个别条目不确定，不该让整本书降级。
    ratio = (len(pending) + len(level_unknown)) / max(len(nodes), 1)
    if ratio > 0.10:
        return "low"
    if not disagreements and not unresolved and not suspicious and has_bookmarks:
        return "high"
    if len({s for node in nodes for s in (node.get("evidence") or {}).get("from", [])}) >= 2 \
            and not disagreements:
        return "high"
    return "medium"


__all__ = ["classify", "detect_suspicious", "grade", "reconcile"]
