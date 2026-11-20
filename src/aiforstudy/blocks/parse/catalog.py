"""统一 catalog 构建器：把**现有产物**读成一份带身份的 catalog。

方案 §7：

> 首次迁移优先按已确认的**来源区间、归属与内容哈希**建立唯一映射；重排不换逻辑 key，
> 原文变化更新内容实例和摘要。拆分/合并写 replaces/replaced_by；同名、重复正文、
> 多处匹配等歧义**不能只按标题自动合并**。身份登记**不需要模型**；不能唯一匹配时先
> **阻断**受影响迁移并提供审阅表。

这一段决定了两件事，本模块都照做：

1. **不用模型**：纯确定性读取 `toc.v2.json` / `body.json` / `chapters.json`，离线可跑、可复现；
2. **不自动裁决**：拿不准的一律进 `ambiguities`（阻断项）或 `exclusions`（带理由的显式排除），
   **绝不悄悄合并或丢弃**。

方案 §6 还要求"正文清单：body 与 catalog 是下游单位集合权威；非教学、容器、空缺、孤儿
**分别解释**，不能凭空数组通过"。所以 `exclusions` 的键是单元键、值是**中文理由**，
并且 `Catalog.verify()` 会检查"非教学单元是否都有理由"。
"""
from __future__ import annotations

import json
from pathlib import Path

from ...core.identity import (ROLE_CHAPTER_INTRO, ROLE_CONTAINER, ROLE_EMPTY,
                             ROLE_NON_TEACHING, ROLE_ORPHAN, ROLE_SECTION,
                             ROLE_SECTION_INTRO, ROLE_SUBSECTION, Ambiguity, Catalog,
                             Lineage, NeedsReview, TocNodeRef, UnitRef, detect_ambiguities,
                             make_node_key)
from ...errors import ConfigError
from .review import (ReviewApplication, apply_review, expected_inputs_digest,
                     load_review, review_needs)

#: 目录节点的 kind → 我们的教学角色
_KIND_TO_ROLE = {
    "chapter": ROLE_CHAPTER_INTRO,
    "section": ROLE_SECTION,
    "subsection": ROLE_SUBSECTION,
}

#: body 的 level → 教学角色（用于 undeclared 单元）
_LEVEL_TO_ROLE = {
    "chapter": ROLE_CHAPTER_INTRO,
    "section": ROLE_SECTION,
    "subsection": ROLE_SUBSECTION,
}


def _load_json(path: Path, what: str) -> dict:
    if not path.is_file():
        raise ConfigError(f"缺 {what}：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError(f"{what}（{path}）不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{what}（{path}）顶层必须是对象")
    return payload


def _parent_of(unit_key: str) -> str | None:
    parts = unit_key.split("-")
    if len(parts) <= 1:
        return None
    return "-".join(parts[:-1])


def _span_of(entry: dict) -> tuple[int, int] | None:
    raw = entry.get("source_lines")
    if isinstance(raw, list) and len(raw) == 2:
        try:
            return (int(raw[0]), int(raw[1]))
        except (TypeError, ValueError):
            return None
    return None


def build_toc_nodes(toc: dict) -> tuple[tuple[TocNodeRef, ...], dict[int, str]]:
    """目录节点 → `TocNodeRef`。返回 `(节点表, {level: 当前父节点})` 形式的父链辅助。"""
    nodes = toc.get("nodes")
    if not isinstance(nodes, list):
        raise ConfigError("toc.v2.json 里没有 nodes 数组")
    out: list[TocNodeRef] = []
    # 目录节点的父子关系在源数据里用 `parent`（T 号）表示，但 T 号会随插条变，
    # 所以要把它换成**持久 node key**：先建 T→node_key 的映射，再回头解析父链。
    t_to_key: dict[str, str] = {}
    chapter_of: dict[str, str] = {}
    current_chapter = ""
    for entry in nodes:
        if not isinstance(entry, dict):
            continue
        toc_id = str(entry.get("toc_id") or "")
        if not toc_id:
            continue
        anchor = str(entry.get("number") or entry.get("anchor") or "").strip()
        level = int(entry.get("level") or 0)
        if level <= 1 and _KIND_TO_ROLE.get(str(entry.get("kind") or "")) == ROLE_CHAPTER_INTRO:
            current_chapter = ""
        key = make_node_key(anchor, level=level) if anchor else f"TMP-{toc_id}"
        t_to_key[toc_id] = key
        if level == 1:
            current_chapter = key
        chapter_of[toc_id] = current_chapter
    for entry in nodes:
        if not isinstance(entry, dict) or not entry.get("toc_id"):
            continue
        toc_id = str(entry["toc_id"])
        parent = entry.get("parent")
        out.append(TocNodeRef(
            node_key=t_to_key[toc_id], toc_id=toc_id,
            title=str(entry.get("title") or ""), level=int(entry.get("level") or 0),
            parent_node_key=(t_to_key.get(str(parent)) if parent else None),
            chapter_key=(chapter_of.get(toc_id) or None),
            anchor=str(entry.get("number") or entry.get("anchor") or ""),
            kind=str(entry.get("kind") or ""), confirmed=bool(entry.get("confirmed")),
            legacy_aliases=(toc_id,)))
    return tuple(out), {}


#: 产物里两种 `body.json` 形状（迁移期都要认）
SHAPE_TWO_KEY = "two-key"        # body_node=结构键 + unit_id=U-内容实例（v2 产物）
SHAPE_SINGLE_KEY = "single-key"  # 只有一个 id，且它本身长得像结构键（早期产物/合成夹具）
_CONTENT_ID_RE = __import__("re").compile(r"^U-[0-9a-f]{6,}$")


def detect_shape(body: dict) -> str:
    """判断这份 `body.json` 是哪一种形状。

    ⚠️ 为什么要认两种（2026-10-05 实测）：真实产物是 **两个键**（`body_node` 结构键 +
    `unit_id` 内容寻址 `U-…`），而离线合成夹具与早期产物只有**一个** id 且它本身就是结构键。
    只认前者，夹具上就会把每个单元都判成"没有结构键"，于是整本进歧义 ——
    那不是数据的问题，是读取方假设过窄。**但也不能反过来瞎猜**：分辨依据写死在这里，
    并记进产物（`shape`），这样"跑的是哪种形状"是可查的。
    """
    rows = list(body.get("units") or []) + list(body.get("undeclared") or [])
    for entry in rows:
        if isinstance(entry, dict) and entry.get("body_node"):
            return SHAPE_TWO_KEY
    for entry in rows:
        if isinstance(entry, dict):
            token = str(entry.get("unit_id") or "")
            if token and not _CONTENT_ID_RE.match(token):
                return SHAPE_SINGLE_KEY
    return SHAPE_TWO_KEY


def build_units(body: dict, *, roles_by_toc: dict[str, str] | None = None
                ) -> tuple[tuple[UnitRef, ...], dict[str, str], list[Ambiguity]]:
    """`body.json` → 单元表 + 显式排除理由 + 歧义。**两种形状都认。**"""
    roles_by_toc = roles_by_toc or {}
    shape = detect_shape(body)
    units: list[UnitRef] = []
    exclusions: dict[str, str] = {}
    ambiguities: list[Ambiguity] = []

    def add(entry: dict, *, declared: bool) -> None:
        raw_id = str(entry.get("unit_id") or "")
        if shape == SHAPE_TWO_KEY:
            unit_key = str(entry.get("body_node") or "")
            content_id = raw_id or None
        else:
            unit_key = raw_id
            content_id = None
        if not unit_key:
            ambiguities.append(Ambiguity(
                kind="missing", subject=raw_id or "?",
                candidates=(), why="这个正文单元没有可用的结构键（body_node / 结构型 unit_id），"
                                 "无法给它持久逻辑身份",
                needs="自动归属仍未决，记录缺失位置"))
            return
        level = str(entry.get("level") or "")
        role = _LEVEL_TO_ROLE.get(level, ROLE_SECTION)
        content_sha = str(entry.get("content_sha256") or "")
        if not content_sha:
            ambiguities.append(Ambiguity(
                kind="missing", subject=unit_key, candidates=(),
                why="没有 content_sha256 —— 无法判断内容有没有变，也就无法安全复用缓存",
                needs="重新跑解析层生成内容哈希"))
            return
        parent = entry.get("parent_body_node") or _parent_of(unit_key)
        # 章节导言（CH01）与节导言（CH02-S05）用 body 的 level 区分不出"是不是导言"，
        # 但目录节点的 kind 可以；两边都拿不到时按 section 处理 —— "导言 vs 正文"
        # 只影响展示，不影响身份。
        if level == "section" and entry.get("is_intro") is True:
            role = ROLE_SECTION_INTRO
        # ⚠️ **容器节点**（2026-10-05 实测抓到的分类缺口）：有些节自己的正文是空的，
        # 内容全在子节点里（`content_in_children: true`，本书有 8 个）。
        # 它们**是**目录声明的教学节点，但**不该**要求有自己的教案 —— 教什么都在子节里。
        # 第一版把它们当成普通 section，于是"4 个 body 单元没有对应 lesson_unit"看起来像丢内容。
        # 方案 §6 的原话是"非教学、容器、空缺、孤儿**分别解释**"，所以这里单列一类。
        if entry.get("content_in_children") is True:
            role = ROLE_CONTAINER
        legacy = [raw_id] if raw_id and raw_id != unit_key else []
        units.append(UnitRef(
            unit_key=unit_key, chapter_key=unit_key.split("-", 1)[0],
            content_sha256=content_sha, teaching_role=role,
            display_anchor=str(entry.get("anchor") or ""),
            content_unit_id=content_id,
            toc_ref=(str(entry["toc_node"]) if entry.get("toc_node") else None),
            parent_unit_key=(str(parent) if parent else None),
            legacy_aliases=tuple(legacy),
            source_span=_span_of(entry), normalized_span=None, declared=declared,
            title=str(entry.get("title") or ""), level=level,
            original_text=str(entry.get("original_text") or "")))
        if not entry.get("confirmed", True):
            exclusions[unit_key] = "目录层标了 confirmed:false，原样带下来（未确认节点）"

    for entry in body.get("units") or []:
        if isinstance(entry, dict):
            add(entry, declared=True)
    for entry in body.get("undeclared") or []:
        if isinstance(entry, dict):
            add(entry, declared=False)

    # 容器节点：显式记下理由（内容在子节点里），并写清它覆盖了哪些子节点
    covers: dict[str, list[str]] = {}
    for entry in list(body.get("units") or []) + list(body.get("undeclared") or []):
        if isinstance(entry, dict) and entry.get("content_in_children") is True:
            key = str(entry.get("body_node") or "")
            if key:
                covers[key] = [str(x) for x in entry.get("covers") or []]
                exclusions[key] = (
                    f"容器节点：本节正文为空，内容都在子节点里"
                    f"（{len(covers[key])} 个：{covers[key][:3]}{'…' if len(covers[key]) > 3 else ''}）；"
                    f"不要求它有自己的教案")

    # ── 目录声明了、但没有内容承载的节点：**显式排除并写理由**（§6：空缺要单独解释）
    for entry in body.get("empty_nodes") or []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("body_node") or entry.get("toc_node") or "?")
        exclusions[key] = (f"目录声明了但原文没有内容（空缺节点）："
                           f"{entry.get('why') or 'body.json 的 empty_nodes'}")
    for entry in body.get("orphans") or []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("body_node") or entry.get("unit_id") or "?")
        exclusions[key] = (f"有内容但目录没声明、且找不到父（孤儿）："
                           f"{entry.get('why') or 'body.json 的 orphans'}")
    for entry in body.get("unconfirmed") or []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("body_node") or entry.get("toc_node") or "?")
        exclusions.setdefault(key, f"未确认节点：{entry.get('why') or '待裁决'}")

    # ── 父节点没有正文单元的情况：显式解释（有的书只有节、没有章级导言）
    have = {u.unit_key for u in units}
    for unit in units:
        parent = unit.parent_unit_key
        if parent and parent not in have:
            exclusions.setdefault(
                parent, f"父节点在正文层没有独立单元（{unit.unit_key} 等 {sum(1 for u in units if u.parent_unit_key == parent)} 个单元的父）；"
                        f"这是书的结构形态，不是丢内容")

    # ── 重复正文 / 同名：一律进歧义，不自动合并
    titles = {u.unit_key: u.title for u in units}
    ambiguities.extend(detect_ambiguities(units, titles=titles))
    return tuple(units), exclusions, ambiguities


def reconcile_with_lesson_units(catalog: Catalog, chapters: dict) -> dict:
    """把 `chapters.json.lesson_units` 与 catalog 逐项对账。

    方案 §7：*当前 body 的 80 个内容单元与 76 个 lesson_units **先逐项分类对账**，
    不能用固定数量硬套迁移。*

    所以这里不做"数量相等就算过"，而是给出**逐项去向**：
    每个 lesson unit 落在哪个 catalog 单元上；catalog 里哪些单元不是 lesson unit（以及为什么）。
    """
    lesson_rows = chapters.get("lesson_units")
    if not isinstance(lesson_rows, list):
        raise ConfigError("chapters.json 里没有 lesson_units 数组")
    by_key = catalog.by_key()
    by_content = catalog.by_content_id()

    matched: list[dict] = []
    unmatched: list[dict] = []
    for row in lesson_rows:
        if not isinstance(row, dict):
            continue
        unit_id = str(row.get("unit_id") or "")
        hit = by_key.get(unit_id) or by_content.get(unit_id)
        if hit is not None:
            matched.append({"lesson_unit": unit_id, "unit_key": hit.unit_key,
                            "role": hit.teaching_role, "declared": hit.declared})
        else:
            unmatched.append({"lesson_unit": unit_id, "title": row.get("title"),
                              "level": row.get("level"),
                              "why": "catalog 里没有对应的正文单元"})
    lesson_keys = {row.get("lesson_unit") for row in matched}
    catalog_only = [{"unit_key": u.unit_key, "role": u.teaching_role,
                     "declared": u.declared, "title": u.title}
                    for u in catalog.units if u.unit_key not in lesson_keys]
    return {"lesson_units": len(lesson_rows), "matched": matched,
            "unmatched": unmatched, "catalog_only": catalog_only,
            "consistent": not unmatched}


def _directory_review(parse_root: Path, toc: dict) -> tuple[ReviewApplication, dict]:
    """Prefer the new authority certificate without manufacturing a legacy review."""
    manifest = parse_root / 'toc.dual-source.json'
    if manifest.is_file():
        from ...pipeline.stage_io import _legacy
        cli = _legacy('blocks.parse.toc.cli')
        dual = _legacy('blocks.parse.toc.dual_source')
        pdf = cli._pdf_for(parse_root)
        current = bool(pdf and pdf.is_file() and dual.is_current(parse_root, pdf)
                       and cli.authority_is_current(parse_root))
        reason = ('双源目录与正文权威的输入、输出指纹一致' if current else
                  '双源目录或正文权威缺失、未通过或已过期；旧复核不能代替，请重新准备目录')
        pending = () if current else (NeedsReview(
            kind='directory-dual-source', subject='book', why=reason, arbiter='pending'),)
        applied = ReviewApplication(stale=not current, pending=pending, reason=reason)
        return applied, {'workflow': 'dual-source', 'artifact_present': True,
                         'stale': not current, 'reason': reason,
                         'evidence': ['toc.dual-source.json', 'toc.authority.json'],
                         'applied': len(toc.get('nodes') or []) if current else 0,
                         'rejected': 0}
    review = load_review(parse_root)
    expected = expected_inputs_digest(parse_root)
    applied = apply_review(review, expected_inputs_digest=expected,
                           disagreements=list(toc.get('disagreements') or []))
    return applied, {'workflow': 'legacy-review', 'expected_inputs_digest': expected,
                     'artifact_present': review is not None,
                     'artifact_inputs_digest': review.inputs_digest if review else None,
                     'stale': applied.stale, 'reason': applied.reason,
                     'applied': len(applied.applied), 'rejected': len(applied.rejected)}


def build_catalog(parse_root: Path, *, book_id: str, revision: str) -> tuple[Catalog, dict]:
    """读齐三份产物，构建 catalog 与对账明细。**只读。**"""
    parse_root = Path(parse_root)
    toc = _load_json(parse_root / "toc.v2.json", "目录产物")
    body = _load_json(parse_root / "body.json", "正文归属产物")
    chapters = _load_json(parse_root / "chapters.json", "导出产物")

    toc_nodes, _ = build_toc_nodes(toc)
    roles_by_toc = {n.toc_id: n.kind for n in toc_nodes}
    units, exclusions, ambiguities = build_units(body, roles_by_toc=roles_by_toc)

    # 目录里声明了但 body 里完全没出现的节点 → 显式排除（空缺/非教学）
    seen_toc = {u.toc_ref for u in units if u.toc_ref}
    for node in toc_nodes:
        if node.toc_id in seen_toc:
            continue
        if not node.is_teaching:
            exclusions.setdefault(node.node_key,
                                  f"非教学节点（kind={node.kind}）：{node.title}")
        else:
            exclusions.setdefault(
                node.node_key,
                f"目录声明为教学节点但正文层没有对应单元：{node.title}（自动归属未决，保留缺失记录）")

    # ── 目录复核：**按输入指纹应用**（方案 §6/§15 A08）
    applied, review_details = _directory_review(parse_root, toc)
    # 待裁决项 = 复核留下的 + 不依赖复核也能看出的（未确认节点、未裁决分歧）
    needs = tuple(applied.pending) + review_needs(
        list(toc.get("disagreements") or []), list(body.get("unconfirmed") or []))
    seen: set[tuple[str, str]] = set()
    deduped = []
    for row in needs:
        token = (row.kind, row.subject)
        if token in seen:
            continue
        seen.add(token)
        deduped.append(row)

    catalog = Catalog(book_id=book_id, revision=revision, units=units,
                      toc_nodes=toc_nodes, exclusions=exclusions,
                      ambiguities=ambiguities, needs_review=tuple(deduped),
                      source_ref={"_meta": body.get("_meta") or {}})

    reconciliation = reconcile_with_lesson_units(catalog, chapters)
    details = {"shape": detect_shape(body), "toc_nodes": len(toc_nodes), "units": len(units),
               "review": review_details,
               "exclusions": dict(sorted(exclusions.items())),
               "ambiguities": [a.as_dict() for a in ambiguities],
               "reconciliation": reconciliation,
               "toc_disagreements": list(toc.get("disagreements") or []),
               "toc_unresolved": list(toc.get("unresolved") or []),
               "verify": catalog.verify()}
    return catalog, details


__all__ = ["build_toc_nodes", "build_units", "reconcile_with_lesson_units",
           "build_catalog", "verify_references"]


# ────────────────────────────────────────── 交叉引用核对

def verify_references(parse_root: Path, lessons_root: Path | None, catalog: Catalog, *,
                      include_concepts: bool = True) -> dict:
    """把**现有产物里的每一处单元引用**拿去 catalog 里解析。

    方案 §7 的通过条件里有这一条：*CH/U 谱系与原文坐标可对账*。
    "可对账"的意思就是：拿旧产物里的每一个 id 去问 catalog"这是谁"，
    必须答得出来；答不出来就是谱系缺了一环，而不是"那个引用不重要"。

    核对的四类引用：

    | 来源 | 引用字段 | 为什么它会存在 |
    |---|---|---|
    | `concepts.json` | `occurrences[].unit_id` | 概念在哪几节出现过 |
    | `concepts.parts/*.json` | 分片名 + `unit_id` | 单写方合并的输入 |
    | `lesson/*.json` | 文件名 + `unit_id` | 教案与单元一一对应 |
    | `audit/*.json` | 文件名前缀 | 工单挂在单元上 |

    返回逐项缺失清单 —— **不静默跳过**（§6：不能凭空数组通过）。
    """
    parse_root = Path(parse_root)
    missing: list[dict] = []

    def check(kind: str, token: str, where: str) -> None:
        if catalog.resolve(token) is None:
            missing.append({"kind": kind, "token": token, "where": where})

    concepts_path = parse_root / "concepts.json"
    concept_refs = 0
    if include_concepts and concepts_path.is_file():
        payload = json.loads(concepts_path.read_text(encoding="utf-8"))
        for concept in payload.get("concepts") or []:
            for occ in (concept.get("occurrences") or []):
                token = str(occ.get("unit_id") or "")
                if not token:
                    continue
                concept_refs += 1
                check("concept-occurrence", token, str(concept.get("id") or "?"))

    parts_dir = parse_root / "concepts.parts"
    part_refs = 0
    if include_concepts and parts_dir.is_dir():
        for path in sorted(parts_dir.glob("*.json")):
            part_refs += 1
            check("concepts-part", path.stem, path.name)

    lesson_refs = 0
    lessons_root = Path(lessons_root) if lessons_root else None
    if lessons_root and (lessons_root / "lessons").is_dir():
        for path in sorted((lessons_root / "lessons").glob("*.json")):
            lesson_refs += 1
            check("lesson-file", path.stem, path.name)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            token = str(payload.get("unit_id") or "")
            if token:
                check("lesson-unit_id", token, path.name)

    audit_refs = 0
    non_unit_files: list[str] = []
    if lessons_root is not None:
        audit_root = lessons_root.parent.parent / "audit" / lessons_root.name
        if audit_root.is_dir():
            from ...core.identity import KEY_RE
            for path in sorted(audit_root.iterdir()):
                if not path.is_file():
                    continue
                unit_token = path.name.split(".", 1)[0]
                if not unit_token:
                    continue
                if not KEY_RE.match(unit_token):
                    # ⚠️ 不是单元引用的文件名（例如 `原文疑似有错.json` 这种汇总报告）。
                    # **仍然列出来**，只是分类成"非单元文件"——不静默跳过任何东西，
                    # 否则将来真出现一个拼错的单元名，也会被当成"非单元文件"混过去。
                    non_unit_files.append(path.name)
                    continue
                audit_refs += 1
                check("audit-file", unit_token, path.name)

    return {"concept_occurrences": concept_refs, "concepts_parts": part_refs,
            "lesson_files": lesson_refs, "audit_files": audit_refs,
            "non_unit_files": sorted(non_unit_files),
            "missing": missing, "ok": not missing,
            "note": ("每一处引用都要能在 catalog 里解析出来；解析不出来说明谱系缺环，"
                     "不许当成'那个引用不重要'")}
