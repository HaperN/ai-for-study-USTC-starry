"""正文解析（M4）：把现有章节树**显式绑定**到 `toc_id`，并把账目补齐。

## 这个块**只做绑定**，不重新切正文

`chapters.json`（`blocks/parse/export` 产出）已经把正文切好了：每个节点带
`id/anchor/title/original_text/content_sha256`。本次重构**不重切**（H2：不动现产物），
只做三件事：

1. **显式绑定**：每个 unit 写清 `toc_node`（现在完全没有这个字段 —— 设计稿 C2）；
2. **稳定 id**（P6）：`unit_id = "U-" + sha1(toc_id + content_sha256)[:10]`，
   不再用 `CH01-S01` 这种"章-节号拼出来的" id（正文一重排就挂错节）；
3. **账目**：
   * `empty_nodes`：目录有、正文没有（R-f：补成空节，不静默消失）；
   * `orphans`：正文有、贴不上目录（R-e：一个字不删，原样留下）；
   * `unconfirmed`：目录层标了 `confirmed: false` 的，**原样带下来**（Q-c 定案）。

## 三条规矩（设计稿 §3.2）

| # | 规矩 | 落法 |
|---|---|---|
| **R-e** | 正文贴不上的进 `orphans`，一个字不删 | `orphans[]` 带 `original_text` + `content_sha256` |
| **R-f** | 骨架有、正文没有 → 补成空节 | `empty_nodes[]`（**不改** `chapters.json`，只在本产物里点名） |
| **R-g** | 归属（目录说）与切分（正文说）**分开记账**，错位时报出来，不互相改 | `unmatched_titles` / `anchor_conflicts` 两本小账 |

## A5（两步独立）

`build_body()` **只读** `toc.json`，返回的 payload 里带 `toc_sha256`。
落盘前后比 `toc.json` 的哈希必须一致（`cli.py body` 会当场比并报错）。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .contract import GENERATOR, SCHEMA_VERSION, number_key, sha256_file
from .sources import identity

SCHEMA_VERSION_BODY = "2.0"

#: 比"父节点里有没有子节点正文"时，快照取多少字。
#: 20 字会误判（短句在导言里也可能出现），40 字实测四本 0 误报、也能抓住整段重复。
OVERLAP_SNIPPET = 40


def content_unit_id(toc_id: str, content_sha256: str) -> str:
    """P6 的内容寻址 id：`U-` + `sha1(toc_id + content_sha256)[:10]`。

    为什么不用 `CH01-S01`：那是"章-节号拼出来的"位置 id，目录一重排、
    正文一切分就全错位（老实现的事故来源）。内容寻址的好处是**重跑不变**、
    而且"内容变了 id 就变"——下游一眼能看出该节被改过。
    """
    raw = f"{toc_id or ''}{content_sha256 or ''}".encode("utf-8")
    return "U-" + hashlib.sha1(raw).hexdigest()[:10]


def build_body(*, toc: dict, parse_dir: str | Path, toc_sha256: str = "",
               document_sha256: str = "") -> dict:
    """`toc.json`(v2) + `chapters.json` → `body.json`。"""
    parse_dir = Path(parse_dir)
    chapters = _load_json(parse_dir / "chapters.json") or {}
    tree = chapters.get("chapters") or []
    if not tree:
        # **不许静默返回空正文**：新书还没跑 MinerU / 导出时 `chapters.json` 不存在，
        # 这时"0 个单元"容易被误读成"这本书没有正文"。写成一条有名字的账（R-b）。
        from .contract import build_toc as _unused  # noqa: F401  （保持 import 面不变）
        chapters = dict(chapters)
        chapters.setdefault("_meta", {})
        chapters["_meta"] = {**chapters["_meta"],
                             "note": "没有 chapters.json（或它是空的）—— 正文层还没有输入；"
                                     "先跑 MinerU 适配 + 导出，再来绑 toc_node"}
    if not toc_sha256 and (parse_dir / "toc.v2.json").is_file():
        toc_sha256 = sha256_file(parse_dir / "toc.v2.json")
    if not document_sha256 and (parse_dir / "document.md").is_file():
        document_sha256 = sha256_file(parse_dir / "document.md")

    index = _toc_index(toc.get("nodes") or [])
    units: list[dict] = []
    orphans: list[dict] = []
    #: **内容承载节点**：目录没声明它，但它承载正文 → 贴到最近的已声明祖先（方案「乙」）
    undeclared: list[dict] = []
    #: 防重复断言的命中表：`正文节点 id → 命中的那一段`
    overlap_hits: dict[str, str] = {}
    #: 正文节点 → 它的**直接父节点 id**（父节点据此记 `covers`；精确，不用前缀）
    carry_by_id: dict[str, str] = {}
    matched_ids: set[str] = set()
    bound_by_id: dict[str, dict] = {}
    anchor_conflicts: list[dict] = []
    unmatched_titles: list[dict] = []

    # ── 防重复断言（先跑，后面两处都用它的结果）─────────────────────────
    # 父节点的正文里**不许**含子节点的正文；含了就必须报出来（不许静默重复计价）。
    children_of = {str(n.get("id")): (n.get("subsections") or [])
                   for n in _walk(tree) if str(n.get("kind")) == "section"}
    for node in _walk(tree):
        node_id = str(node.get("id") or "")
        kids = children_of.get(node_id) or []
        if not kids:
            continue
        parent_text = _node_text(parse_dir, node)
        if not parent_text:
            continue
        for kid in kids:
            hit = _parent_contains_child(parent_text, _node_text(parse_dir, kid))
            if hit:
                overlap_hits[str(kid.get("id"))] = hit

    for node, ancestors in _walk_with_ancestors(tree):
        node_id = str(node.get("id") or "")
        title = str(node.get("title") or "")
        anchor = str(node.get("anchor") or "")
        original = node.get("original_text")
        content_sha = str(node.get("content_sha256") or "")
        toc_node = _bind(index, title, anchor, str(node.get("kind") or ""),
                         matched_ids)

        if toc_node is None:
            # 目录里没这一条（书的事实：目录只列到节，正文却分小节）。
            # 尽力给它一个**最近的已声明祖先**，**并且照样产出正文单元**（方案「乙」）：
            # 实测《复变函数》的印刷目录只列到第 2 层，37 个真小节全不在目录里，
            # 而 25 个节节点里有一批**只是空壳**（`ch01_s01.md` 就 16 字节）——
            # 只记孤儿的下场是：教案层去读"节"，什么也读不到，整本书白解析。
            parent = _nearest_bound(ancestors, bound_by_id, index)
            if parent is None:
                # 连祖先都没绑上（目录里一个相关条目都没有）→ 只能记孤儿
                orphans.append({
                    "why": "toc 里没有这一节，也没有可归属的已声明祖先",
                    "parent_toc_node": None, "parent_title": None,
                    "node_id": node_id, "anchor": anchor, "title": title,
                    "original_text": original, "content_sha256": content_sha,
                    "preview": _preview(parse_dir / str(original) if original else None),
                })
                unmatched_titles.append({"node_id": node_id, "title": title,
                                         "why": "目录里找不到同名条目，且无祖先可归"})
                continue
            undeclared.append({
                "unit_id": content_unit_id(parent["toc_id"], content_sha),
                "toc_node": parent["toc_id"],       # 贴到**已声明祖先**上
                "declared": False,                  # 目录没声明它，但它承载正文
                "body_node": node_id,               # 正文树里的身份（显示层挂父节点用）
                # 精确父指针：`covers` 靠它判父子。
                # ⚠️ 不许用字符串前缀（`CH01-S01` 是 `CH01-S01-SS01` 的前缀，
                #    但 `CH01` 也是 —— 用前缀会把**别的节**甚至整章都算进 covers，实测串了）
                "parent_body_node": str(_body_parent_id(ancestors)),
                "anchor": anchor, "title": title,
                "level": str(node.get("kind") or "subsection"),
                "original_text": original,
                "content_sha256": content_sha,
                "pages": {"printed": _pages(parent, "printed"),
                          "pdf": _pages(parent, "pdf")},
                "source_lines": list(node.get("source_lines") or []) or None,
                "blocks": [], "confirmed": bool(parent.get("confirmed", True)),
                "evidence": parent.get("evidence") or {},
                #: 防重复断言：父节点的正文里**不许**含这一节的内容
                "parent_overlaps": bool(overlap_hits.get(node_id)),
            })
            bound_by_id[node_id] = parent
            carry_by_id[node_id] = str(_body_parent_id(ancestors))
            continue

        matched_ids.add(toc_node["toc_id"])
        bound_by_id[node_id] = toc_node
        carry_by_id[node_id] = str(_body_parent_id(ancestors))
        if anchor and toc_node.get("number") and not _anchor_agrees(anchor, toc_node):
            anchor_conflicts.append({
                "toc_id": toc_node["toc_id"], "node_id": node_id,
                "toc_number": toc_node.get("number"), "body_anchor": anchor,
                "why": "目录的编号与正文锚点对不上（归属与切分错位，R-g 只报不改）",
            })
        units.append({
            "unit_id": content_unit_id(toc_node["toc_id"], content_sha),
            "toc_node": toc_node["toc_id"],
            "declared": True,
            "body_node": node_id,          # 正文树里的身份（covers 靠它判父子）
            "anchor": anchor, "title": title,
            "level": str(node.get("kind") or "section"),
            "original_text": original,
            "content_sha256": content_sha,
            "pages": {"printed": _pages(toc_node, "printed"),
                      "pdf": _pages(toc_node, "pdf")},
            # 正文行号区间：习题层靠它把题挂到稳定 id 上（设计稿 §7 的交接面）
            "source_lines": list(node.get("source_lines") or []) or None,
            "blocks": [],
            "confirmed": bool(toc_node.get("confirmed", True)),
            "evidence": toc_node.get("evidence") or {},
        })

    # `covers` 要等循环走完才写得准（子节点可能排在父节点之后处理）：
    # 一个"节"如果只是壳、正文全在小节里，下游必须看得见这件事（否则会当成空节）。
    for unit in units:
        own = str(unit.get("body_node") or "")
        covered = sorted(kid for kid, parent in carry_by_id.items() if parent == own)
        # Declared and undeclared children both carry content. Only a readable,
        # heading-only parent is a container; an introduction or missing source
        # must still require its own concept evidence.
        original = unit.get("original_text")
        try:
            text = (parse_dir / str(original)).read_text(encoding="utf-8") if original else None
        except OSError:
            text = None
        container = bool(covered) and text is not None and not _body_only(text)
        unit["covers"] = covered if container else []
        unit["content_in_children"] = container

    # 目录有、正文没有 → empty_nodes（R-f）。**只在本产物里点名，不改 chapters.json。**
    empty_nodes: list[dict] = []
    for node in toc.get("nodes") or []:
        if node["toc_id"] in matched_ids:
            continue
        if node.get("kind") == "non-teaching":
            continue
        empty_nodes.append({
            "toc_node": node["toc_id"], "number": node.get("number"),
            "title": node.get("title"), "level": node.get("level"),
            "page": node.get("page"),
            "reason": "目录有、正文里没找到这一节",
            "confirmed": bool(node.get("confirmed", True)),
        })

    unconfirmed = [{"toc_node": n["toc_id"], "title": n.get("title"),
                    "why": "目录层标了 confirmed:false，原样带下来（Q-c 定案）"}
                   for n in toc.get("nodes") or [] if not n.get("confirmed", True)]

    return {
        "_meta": {
            "file_type": "body", "schema_version": SCHEMA_VERSION_BODY,
            "generator": GENERATOR + ".body",
            "toc_sha256": toc_sha256, "document_sha256": document_sha256,
            "source_sha256": str(chapters.get("_meta", {}).get("source_sha256") or ""),
            "counts": counts_of(units, empty_nodes, orphans, unconfirmed,
                                anchor_conflicts, undeclared, overlap_hits),
            "field_notes": {
                "units[].unit_id": "内容寻址：U- + sha1(toc_id + content_sha256)[:10]（P6）",
                "units[].toc_node": "目录层的 toc_id —— 这就是「显式绑定」",
                "units[].pages": "节起止页码（从目录层带下来；目录没落页就是 null）",
                "units[].source_lines": "这一节在 document.md 里的行号区间"
                                        "（习题层靠它把题挂到稳定 id 上）",
                "empty_nodes": "骨架（目录）有、正文没有 → 补成空节（R-f），不静默消失",
                "orphans": "正文有、目录里没有 → 一个字不删，原样留下（R-e）；"
                           "parent_toc_node 是「最近的已绑定祖先」（部分归属，不是绑定）",
                "unconfirmed": "目录层不确定的条目原样带下来（Q-c：照样出页并标注）",
                "anchor_conflicts": "目录编号与正文锚点错位（R-g：只报不改）",
                "unmatched_titles": "两源标题对不上的清单（人工核查用）",
            },
        },
        "book": chapters.get("book") or {},
        "units": units,
        "empty_nodes": empty_nodes,
        "undeclared": undeclared,
        "orphans": orphans,
        "unconfirmed": unconfirmed,
        "content_overlaps": [
            {"body_node": kid, "why": "父节点的正文里出现了这一节的正文 —— "
                                      "内容只许归一处（方案「乙」的防重复断言）",
             "hit": hit[:60]}
            for kid, hit in sorted(overlap_hits.items())
        ],
        "anchor_conflicts": anchor_conflicts,
        "unmatched_titles": unmatched_titles,
    }


def counts_of(units: list[dict], empty_nodes: list[dict], orphans: list[dict],
              unconfirmed: list[dict], anchor_conflicts: list[dict],
              undeclared: "list[dict] | None" = None,
              overlap_hits: "dict | None" = None) -> dict:
    """账目**算出来**（不手工复制：教训 §7.1）。"""
    undeclared = undeclared or []
    return {
        "units": len(units) + len(undeclared),
        "declared_units": len(units),
        "empty_nodes": len(empty_nodes),
        "orphans": len(orphans),
        "unconfirmed": len(unconfirmed),
        "anchor_conflicts": len(anchor_conflicts),
        #: **内容承载节点**：目录没声明、但承载正文，已贴到最近的已声明祖先（方案「乙」）
        "undeclared_units": len(undeclared),
        #: 防重复断言命中数 —— **必须是 0**；不是 0 说明同一段正文被算了两遍
        "content_overlaps": len(overlap_hits or {}),
        "units_with_toc_node": sum(1 for u in units + undeclared if u.get("toc_node")),
        "units_without_content": sum(1 for u in units + undeclared
                                     if not u.get("content_sha256")),
        #: 孤儿里有多少**能说清它属于哪一节**（部分归属；其余是真的无家可归）
        "orphans_with_parent": sum(1 for o in orphans if o.get("parent_toc_node")),
        #: 有多少个"节"是**壳**：正文全在未声明小节里（下游别当成空节）
        "sections_carrying_in_children": sum(
            1 for u in units if u.get("content_in_children")),
    }


# ── 绑定 ───────────────────────────────────────────────────────────────
KIND_LEVEL = {"chapter": 1, "section": 2, "subsection": 3}


def _toc_index(nodes: list[dict]) -> dict:
    """目录索引：`键 → [节点]`，键**优先带编号**，没有编号的才退回标题。

    为什么要两套键：正文锚点是 `1.1`、目录条目可能是 `§1.1` 或没编号。
    带编号的键先命中（更精确），标题键兜底。
    """
    by_number: dict[str, list[dict]] = {}
    by_title: dict[str, list[dict]] = {}
    for node in nodes:
        number = str(node.get("number") or "")
        title = str(node.get("title") or "")
        if number:
            by_number.setdefault(number_key(number), []).append(node)
        if title:
            by_title.setdefault(identity("", title), []).append(node)
    return {"by_number": by_number, "by_title": by_title}


def _bind(index: dict, title: str, anchor: str, kind: str,
          claimed: set[str]) -> dict | None:
    """正文节点 → 目录节点。

    三步，**先精确后兜底**：

    1. 锚点的编号在目录里（`1.1` → 目录的 `1.1`）—— 最可靠；
    2. 同名条目里**别人没认领过**的（同名条目在真书里很常见：
       `向量的线性运算` 既是节又是小节）；
    3. 同名条目里层级与正文 `kind` 最接近的。

    实测教训（《线性代数》）：不做 2/3 的话，两个叫 `向量的线性运算` 的正文节点
    会双双绑到同一个目录条目上 —— 于是"每个 unit 都有 toc_node"看起来成立了，
    实际是**一条目录被两节共用**，等于没绑。
    """
    if not title:
        return None
    wanted_level = KIND_LEVEL.get(kind)
    for node in index["by_number"].get(number_key(anchor), []):
        if node["toc_id"] not in claimed:
            return node
    same_title = index["by_title"].get(identity("", title), [])
    fresh = [n for n in same_title if n["toc_id"] not in claimed]
    pool = fresh or same_title
    if not pool:
        return None
    if wanted_level:
        return min(pool, key=lambda n: abs(int(n.get("level") or 0) - wanted_level))
    return pool[0]


#: 编号里的数字路径：`第1章`→`1`、`§1.1`→`1.1`、`1.1.2`→`1.1.2`；`第N章` 的中文号要转
_DIGITS = re.compile(r"\d+(?:\.\d+)*")
_CN_CHAPTER = re.compile(r"^第\s*([一二三四五六七八九十百零〇]+)\s*[章篇]$")


def _number_signature(number: str) -> str:
    """编号的**可比签名**：只留数字路径，把 `第N章`/`§N.M`/`N.M` 归一到同一口径。

    为什么不能直接用 `identity()` 比：它是"标题身份"口径，会把 `第一章` 与 `1`
    折成两个不同的东西，于是**每一条**章级条目都报"锚点错位"
    （实测《复变函数》42 条 unit **全部**被误报，这种警报响了等于没响）。
    认不出的编号返回空串 → **不参与比对**（不猜）。
    """
    text = str(number or "").strip()
    chinese = _CN_CHAPTER.match(text)
    if chinese:
        from ..structure.structure import cn_to_int
        value = cn_to_int(chinese.group(1))
        return str(value) if value is not None else ""
    hit = _DIGITS.search(text)
    return hit.group(0) if hit else ""


def _anchor_agrees(anchor: str, toc_node: dict) -> bool:
    """正文锚点与目录编号是不是同一个编号（`1`↔`第1章`、`1.1`↔`§1.1`）。"""
    left = _number_signature(anchor)
    right = _number_signature(str(toc_node.get("number") or ""))
    return bool(left) and left == right


def _walk(tree: list[dict]):
    """深度优先走章节树（章 → 节 → 小节），产出**所有**节点。"""
    for node, _ in _walk_with_ancestors(tree):
        yield node


def _walk_with_ancestors(tree: list[dict], ancestors: list[dict] | None = None):
    """同上，但连**祖先链**一起给（孤儿要靠它找"最近的已绑定祖先"）。"""
    ancestors = ancestors or []
    for node in tree:
        yield node, ancestors
        children = node.get("sections") or []
        if children:
            yield from _walk_with_ancestors(children, ancestors + [node])
        for child in children:
            for grand in child.get("subsections") or []:
                yield grand, ancestors + [node, child]


def _nearest_bound(ancestors: list[dict], bound_by_id: dict,
                   index: dict) -> dict | None:
    """祖先链里**最近的**那个已绑定目录节点（没有就返回 None）。"""
    for ancestor in reversed(ancestors):
        hit = bound_by_id.get(str(ancestor.get("id") or ""))
        if hit is not None:
            return hit
    return None


def _pages(toc_node: dict, key: str) -> list:
    value = (toc_node.get("page") or {}).get(key)
    return [value] if value not in (None, "") else []


# ── 防重复：父节点里不许含子节点的正文（2026-09-28） ────────────────────
#
# 为什么必须有这一条：设计方案「乙」允许**内容承载节点**（目录没声明的小节）
# 贴到最近的已声明祖先上。它只有在"内容只归一处"时才安全 —— 否则同一段文字
# 会被父、子各算一遍（用户在 2026-09-28 明确提出的担心）。
#
# 判据**只认正文**，不认标题字符串：
#   实测拿"子标题出现在父里"当判据会误报 20 处 —— 那些父文件只有 31～649 字节
#   （标题，或标题 + 一段自己的导言），子标题只是**被导言提到过**
#   （"下面讲向量范数"），并非正文重复。
# 四本实测（73 个"带小节的节"）：父子正文重叠 **0 处**。
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[，。；：、（）()【】\[\]“”\"'’‘\-—…·．.,$\\{}^_]")


def _norm_for_overlap(text) -> str:
    """比正文重叠用的归一：去掉空白与标点（不同来源的标点会不一样）。"""
    return _PUNCT.sub("", _WS.sub("", str(text or "")))


def _body_only(text: str) -> str:
    """去掉标题行，只留正文。"""
    return "\n".join(line for line in str(text or "").splitlines()
                     if line.strip() and not line.strip().startswith("#")).strip()


def _overlap_snippets(text: str, size: int = OVERLAP_SNIPPET) -> list[str]:
    """抽两段正文快照：**开头**与**中间**各 `size` 字（中间那段防"父里塞了半篇"）。"""
    body = _body_only(text)
    if len(body) < size:
        return []
    middle = max(0, len(body) // 2 - size // 2)
    return [body[:size], body[middle:middle + size]]


def _parent_contains_child(parent_text: str, child_text: str) -> str | None:
    """父正文里有没有子正文（返回命中的那一段，没有则 None）。"""
    parent_norm = _norm_for_overlap(parent_text)
    if not parent_norm:
        return None
    for snippet in _overlap_snippets(child_text):
        if _norm_for_overlap(snippet) in parent_norm:
            return snippet
    return None


def _body_parent_id(ancestors: list[dict]) -> str:
    """正文树里的**直接父节点 id**（祖先链最后一个就是父）。"""
    return str(ancestors[-1].get("id") or "") if ancestors else ""


def _node_text(parse_dir: Path, node: dict) -> str:
    """读一个正文节点的原文（读不到就返回空串，不抛）。"""
    rel = node.get("original_text")
    if not rel:
        return ""
    path = parse_dir / str(rel)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _preview(path: Path | None, limit: int = 80) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return text[:limit].replace("\n", " ").strip()


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


__all__ = ["SCHEMA_VERSION_BODY", "build_body", "content_unit_id"]
