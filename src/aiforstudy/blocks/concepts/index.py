"""全书概念索引：身份、覆盖、**单写方合并**、冻结与新鲜度。

方案 §6 的接通要求：

> 概念：全书有内容的教学单元**逐项完成/明确无概念**，**完整索引冻结后**才生成讲解；
> **失败分片不能用旧片冒充新鲜**。

方案 §7：

> 完整索引指**身份、名称、类型、归属、原文锚点**，不把全书正文与所有详解反复送入每节。

## 本里程碑实测到的一件事（决定了这个模块怎么写）

旧实现用**概念名**当身份键（`concepts.ids.json` 是 `名字 → id`）。实测这本书：

| 现象 | 数字 |
|---|---:|
| `concepts.json` 里的概念 | 448 |
| 名字注册表里的 id | **438** |
| **没有注册表条目的概念** | **10** |
| 归一化后重名的概念组 | 5 |

那 10 个不是"少抽了"，而是**名字撞了**：

* 5 组真重名（`单叶性区域` C-0112/C-0117、`长大不等式` C-0203/C-0294、
  `柯西积分公式` C-0215/C-0269、`共轭调和函数` C-0226/C-0384、
  `儒可夫斯基变换` C-0355/C-0403）—— 注册表一个名字只留一个 id，另一个就没了；
* 5 个通用编号名（`推论1`、`引理1`、`定义3`、`性质6`…）**根本不在注册表里**。

更要紧的是 `推论1` —— 它的 occurrences 横跨 **7 个章**。用名字当身份，
等于把 7 个不同章节里的「推论 1」**合并成同一个概念**。
方案 §7 对单元已经说过"同名、重复正文、多处匹配等歧义**不能只按标题自动合并**"；
概念这一层是一样的道理，只是旧实现没照做，而且丢得无声无息
（页面载荷按注册表生成，于是 448 个概念里有 10 个从来没出现在读者面前）。

所以本模块的身份键是 **（归一化名, 类型, 归属单元）**，不是名字。
同名不同归属 → 两个概念；并额外把"名字碰撞"如实记账（`collisions`），不自动合并。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ...core.digest import digest_file, digest_of
from ...core.identity import Catalog
from ...errors import ConfigError
from .coverage import normalize_coverage_name as _normalize_name, covers_name as _covers

#: 概念类型（基线 G-05 已确认从二分类扩到七类）
CONCEPT_TYPES = ("definition", "theorem", "proposition", "corollary", "law", "property",
                 "formula")

#: 归属未知时的占位（不是"没有归属"，是"这次没记"——两者要能区分）
OWNERSHIP_UNKNOWN = "<unknown>"

_SPACE_RE = re.compile(r"[\s\u3000]+")


def normalize_name(name: str) -> str:
    """概念名的归一化：只去空白、统一小写。**不做同义/近义合并**。

    为什么不做更多归一化：`定义3` 与 `定义 3` 是同一个名字，但 `定义3` 与 `定义4` 不是，
    而 `性质6` 与 `性质 6` 是。多做一步就多一步会误合并的操作，而**误合并是无声的**。
    """
    return _SPACE_RE.sub("", str(name or "")).lower()


@dataclass(frozen=True)
class Occurrence:
    unit_key: str
    chapter: str = ""
    anchor: str = ""
    role: str = "introduced"          # introduced | reused
    title: str = ""

    def as_dict(self) -> dict:
        return {"unit_key": self.unit_key, "chapter": self.chapter, "anchor": self.anchor,
                "role": self.role, "title": self.title}

    @classmethod
    def from_dict(cls, payload: dict) -> "Occurrence":
        return cls(unit_key=str(payload.get("unit_key") or payload.get("unit_id") or ""),
                   chapter=str(payload.get("chapter") or ""),
                   anchor=str(payload.get("anchor") or ""),
                   role=str(payload.get("role") or "introduced"),
                   title=str(payload.get("title") or ""))


@dataclass(frozen=True)
class ConceptEntry:
    """一个概念：身份 + 名称 + 类型 + **归属** + 原文锚点（§7 的完整索引口径）。"""
    concept_id: str
    name: str
    type: str
    ownership_unit: str
    ownership_chapter: str = ""
    occurrences: tuple[Occurrence, ...] = ()
    formal: str = ""
    source_quotes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.concept_id.strip():
            raise ConfigError("概念必须有 id")
        if not self.name.strip():
            raise ConfigError(f"{self.concept_id}: 概念名不能为空（空名无法在正文里被引用）")
        if self.type not in CONCEPT_TYPES:
            raise ConfigError(f"{self.concept_id}: 类型必须是 {CONCEPT_TYPES} 之一，"
                              f"实际 {self.type!r}")
        if not self.ownership_unit.strip():
            raise ConfigError(f"{self.concept_id}: 归属单元必填 —— 归属是区分同名的唯一依据")

    @property
    def chapter(self) -> str:
        return self.ownership_chapter or self.ownership_unit.split("-", 1)[0]

    @property
    def key(self) -> tuple[str, str, str]:
        """**身份键**：归一化名 + 类型 + **归属章**。

        ⚠️ 归属粒度取"章"而不是"单元"，是本次实测定下来的：
        通用编号名（`定理1`/`推论1`/`定义3`/`性质6`）在每节里都会出现，
        按单元归属会把**同一个概念的多次复用**炸成几十个新概念
        （实测：559 个概念、115 个注册表缺口 —— 明显是错的）；
        而按名字归属又会把不同章的「定理 1」并成一个（旧实现的错法）。
        取"章"正好落在两者之间，并且**恰好解释了本书记录在案的 5 组重名**：
        它们全都分属不同的章。
        """
        return (normalize_name(self.name), self.type, self.chapter)

    @property
    def chapters(self) -> tuple[str, ...]:
        return tuple(sorted({o.chapter for o in self.occurrences if o.chapter}))

    def as_dict(self) -> dict:
        return {"id": self.concept_id, "name": self.name, "type": self.type,
                "ownership_unit": self.ownership_unit,
                "ownership_chapter": self.chapter,
                "occurrences": [o.as_dict() for o in self.occurrences],
                "formal": self.formal, "source_quotes": list(self.source_quotes)}

    def identity(self) -> dict:
        return {"id": self.concept_id, "name": self.name, "type": self.type,
                "ownership_unit": self.ownership_unit, "ownership_chapter": self.chapter}

    @classmethod
    def from_dict(cls, payload: dict) -> "ConceptEntry":
        # ⚠️ 归属要从 occurrences 推，而且字段名是 **`unit_id`**（现有产物用的名字），
        #    不是 `unit_key`。第一版只认 `unit_key`，于是 448 条历史概念的归属全变成
        #    `<unknown>` —— 身份键与合并时算出来的 `(名, 类型, 章)` 对不上，
        #    结果**每一个概念都被当成新概念**重新发号（448 → 528，缺口 528）。
        #    这类"字段名猜错"不会报错，只会让号全变。
        occs = tuple(Occurrence.from_dict(o) for o in payload.get("occurrences") or ())
        owner = str(payload.get("ownership_unit")
                    or (occs[0].unit_key if occs else "")
                    or OWNERSHIP_UNKNOWN)
        chapter = str(payload.get("ownership_chapter")
                      or (occs[0].chapter if occs and occs[0].chapter else "")
                      or owner.split("-", 1)[0])
        return cls(concept_id=str(payload.get("id") or ""),
                   name=str(payload.get("name") or ""),
                   type=str(payload.get("type") or ""),
                   ownership_unit=owner,
                   ownership_chapter=chapter,
                   occurrences=occs,
                   formal=str(payload.get("formal") or ""),
                   source_quotes=tuple(str(x) for x in payload.get("source_quotes") or ()))


# ────────────────────────────────────────── 分片状态

@dataclass(frozen=True)
class PartStatus:
    """一个单元的概念分片状态。**"没概念"和"没跑"是两件事。**"""
    unit_key: str
    state: str      # complete | incomplete | no-concepts | missing | no-content
    concepts: int = 0
    why: str = ""
    text_sha256: str = ""

    @property
    def is_no_concept(self) -> bool:
        return self.state == "no-concepts"

    @property
    def blocks_freeze(self) -> bool:
        # ⚠️ `no-concepts` **不**挡冻结：方案 §6 要的是"逐项完成/**明确无概念**"，
        #    "抽完确认本节没有具名结论"是**合法的完成态**，不是失败分片。
        return self.state in ("incomplete", "missing")

    def as_dict(self) -> dict:
        return {"unit_key": self.unit_key, "state": self.state, "concepts": self.concepts,
                "why": self.why, "text_sha256": self.text_sha256,
                "blocks_freeze": self.blocks_freeze}


# ────────────────────────────────────────── 索引

@dataclass
class ConceptIndex:
    book_id: str
    concepts: tuple[ConceptEntry, ...] = ()
    parts: tuple[PartStatus, ...] = ()
    #: 明确"没有概念"的单元 → 理由（§6：逐项完成/**明确无概念**）
    no_concept_units: dict[str, str] = field(default_factory=dict)
    #: 名字碰撞：归一化名 → 多个 concept_id（如实记账，**不自动合并**）
    collisions: dict[str, list[str]] = field(default_factory=dict)
    #: 同名跨章：历史上一度按名字合并且横跨多章的名字。
    #: **不是错误**，是"按名字合并"这个旧规则的后果；这里如实记账（含受影响 id）。
    multi_chapter_names: dict[str, dict] = field(default_factory=dict)
    #: id 注册表：归一化名 → 现有 id（用于重抽时沿用旧号）
    registry: dict = field(default_factory=dict)
    #: 旧注册表的条目数（用于把"旧表表达不了哪些概念"讲清楚）
    legacy_registry_size: int = 0
    source_digest: str = ""
    frozen: bool = False
    schema_version: str = "1.0"

    # ── 查询
    def by_id(self) -> dict[str, ConceptEntry]:
        return {c.concept_id: c for c in self.concepts}

    def by_name(self, name: str) -> tuple[ConceptEntry, ...]:
        target = normalize_name(name)
        return tuple(c for c in self.concepts if normalize_name(c.name) == target)

    def concepts_of_unit(self, unit_key: str) -> tuple[ConceptEntry, ...]:
        return tuple(c for c in self.concepts
                     if any(o.unit_key == unit_key for o in c.occurrences))

    def introduced_in(self, unit_key: str) -> tuple[ConceptEntry, ...]:
        return tuple(c for c in self.concepts if c.ownership_unit == unit_key)

    # ── 覆盖（§6：逐项完成 / 明确无概念）
    def coverage(self, teaching_units: tuple[str, ...]) -> dict:
        # ⚠️ 归属单元也算"覆盖到"：概念是**在那里被引入**的，即使 occurrences 为空
        #    （历史产物里首次出现的记录可能没写进 occurrences，只体现在归属上）。
        #    第一版只数 occurrences，于是"有概念但没有 occurrence 记录"的单元
        #    会被报成"没有解释" —— 那是把"记录不全"误判成"内容缺失"。
        covered = {o.unit_key for c in self.concepts for o in c.occurrences}
        covered |= {c.ownership_unit for c in self.concepts}
        explicit = set(self.no_concept_units)
        unexplained = [u for u in teaching_units if u not in covered and u not in explicit]
        both = sorted(covered & explicit)
        return {"teaching_units": len(teaching_units), "covered": len(covered & set(teaching_units)),
                "explicit_no_concept": len(explicit & set(teaching_units)),
                "unexplained": unexplained, "claimed_both": both,
                "ok": not unexplained and not both}

    # ── 注册表缺口（本次实测的那 10 个）
    def registry_gaps(self) -> tuple[str, ...]:
        """概念里有、注册表里**没有**的那些 id。

        方案 §7 要求"完整索引"；一个只覆盖 438/448 的注册表不是完整索引。
        这里不抛异常、只报数，因为**缺口本身要能出现在报告里**（静默补齐更坏：
        那会把"名字撞了"掩盖成"一切正常"）。
        """
        known = set(self.registry.values())
        return tuple(sorted(c.concept_id for c in self.concepts
                            if c.concept_id not in known))

    # ── 冻结门
    def blocking_parts(self) -> tuple[PartStatus, ...]:
        return tuple(p for p in self.parts if p.blocks_freeze)

    def assert_freezable(self, teaching_units: tuple[str, ...]) -> None:
        """方案 §6：**完整索引冻结后才生成讲解**；失败分片不能冒充新鲜。"""
        blocking = self.blocking_parts()
        if blocking:
            detail = "；".join(f"{p.unit_key}={p.state}（{p.why}）" for p in blocking[:5])
            raise ConfigError(
                f"概念索引不能冻结：有 {len(blocking)} 个单元的分片未完成 → {detail}。"
                f"失败分片**不能用旧片冒充新鲜**（方案 §6）。")
        cov = self.coverage(teaching_units)
        if not cov["ok"]:
            raise ConfigError(
                f"概念索引不能冻结：覆盖不完整 —— 没有解释的单元 {cov['unexplained'][:5]}；"
                f"同时声称有概念与无概念的单元 {cov['claimed_both'][:5]}。"
                f"方案 §6 要求'逐项完成/明确无概念'。")

    # ── 新鲜度
    def is_fresh(self, current_source_digest: str) -> bool:
        return self.frozen and bool(self.source_digest) and \
            self.source_digest == current_source_digest

    # ── 序列化
    def verify(self) -> dict:
        problems: list[str] = []
        ids = [c.concept_id for c in self.concepts]
        if len(set(ids)) != len(ids):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            problems.append(f"概念 id 重复：{dupes[:5]}")
        keys = [c.key for c in self.concepts]
        if len(set(keys)) != len(keys):
            dupes = sorted({k for k in keys if keys.count(k) > 1})
            problems.append(f"身份键（名+类型+归属）重复：{dupes[:3]}")
        gaps = self.registry_gaps()
        return {"ok": not problems, "problems": problems,
                "counts": {"concepts": len(self.concepts),
                           "ids": len(set(ids)),
                           "parts": len(self.parts),
                           "parts_blocking": len(self.blocking_parts()),
                           "no_concept_units": len(self.no_concept_units),
                           "colliding_names": len(self.collisions),
                           "multi_chapter_names": len(self.multi_chapter_names),
                           "registry_entries": len(self.registry),
                           "legacy_registry_entries": self.legacy_registry_size,
                           "registry_gaps": len(gaps)},
                "registry_gap_ids": list(gaps[:12])}

    def as_dict(self) -> dict:
        return {"_meta": {"file_type": "concept_index", "schema_version": self.schema_version,
                          "generator": "aiforstudy.blocks.concepts.index",
                          "book_id": self.book_id, "frozen": self.frozen,
                          "source_digest": self.source_digest,
                          "field_notes": {
                              "concepts[].ownership_unit": "首次出现的单元 —— 它是**区分同名**的依据",
                              "concepts[].occurrences": "在哪些单元出现；重复出现只加这里",
                              "conespts[].key": "身份键 = 归一化名 + 类型 + 归属（不是名字）",
                              "parts[]": "逐单元分片状态；incomplete/missing 会挡住冻结",
                              "no_concept_units": "明确'无概念'的单元及理由",
                              "collisions": "同名多 id：如实记账，**不自动合并**",
                              "registry": "归一化名 → 现有 id（重抽时沿用旧号，保住 [[CONCEPT:…]] 引用）",
                          }},
                "book_id": self.book_id, "frozen": self.frozen,
                "source_digest": self.source_digest,
                "concepts": [c.as_dict() for c in self.concepts],
                "parts": [p.as_dict() for p in self.parts],
                "no_concept_units": dict(sorted(self.no_concept_units.items())),
                "collisions": {k: sorted(v) for k, v in sorted(self.collisions.items())},
                "multi_chapter_names": {k: v for k, v in sorted(
                    self.multi_chapter_names.items())},
                "registry": {f"{k[0]}|{k[1]}|{k[2]}": v
                             for k, v in sorted(self.registry.items())
                             } if self.registry and isinstance(
                                 next(iter(self.registry)), tuple) else dict(self.registry),
                "legacy_registry_size": self.legacy_registry_size,
                "verify": self.verify()}


# ────────────────────────────────────────── 读分片

# ────────────────────────────────────────── 覆盖度：**按内容重算，不信任自报标记**

def recompute_coverage(payload: dict) -> dict:
    """按分片**自己的内容**重算具名条目的覆盖情况。

    为什么不能信任分片里存的 `coverage.完整`：那个字段是**旧版本用坏尺子写的**，
    而它会被索引当成权威。方案的口径是"读取前验证、不信任陈旧标记"——
    一个派生标记能过期，就该重算，而不是照着念。

    返回 `{"missing_named": [...], "complete_by_content": bool|None}`。
    `complete_by_content` 为 `None` 表示"这一节没有具名条目可核对"（**明确无概念**，
    与"没跑成"是两件事，方案 §6 要的就是这个区分）。
    """
    coverage = payload.get("coverage") or {}
    named = [str(n) for n in (payload.get("named") or []) if str(n).strip()]
    rows = payload.get("concepts") or []
    if coverage.get("完整") is None and not named:
        return {"missing_named": [], "complete_by_content": None}
    missing_named = [n for n in named if not _covers(n, rows)]
    return {"missing_named": missing_named, "complete_by_content": not missing_named}


def read_parts(parse_root: Path, catalog: Catalog) -> tuple[dict[str, dict], tuple[PartStatus, ...]]:
    """读 `concepts.parts/*.json` 与每个单元的分片状态。

    ⚠️ 判据是**逐单元**的：方案 §6 说"失败分片不能用旧片冒充新鲜"。
    所以这里逐个看：分片在不在、它 `_meta.text_sha256` 与当前原文是否一致、
    它自报的 `coverage.完整` 是不是 true。
    """
    parse_root = Path(parse_root)
    parts_dir = parse_root / "concepts.parts"
    payloads: dict[str, dict] = {}
    status: list[PartStatus] = []

    for unit in catalog.units:
        if not unit.is_teaching:
            # 容器节点也要求分片：它自己没正文，但它的子节点有；
            # 这里只对**非教学的容器**放行，其余仍按教学单元要求。
            if unit.teaching_role == "container":
                status.append(PartStatus(unit_key=unit.unit_key, state="no-content",
                                         why="容器节点：正文在子节点里，概念在子节点里抽"))
            else:
                status.append(PartStatus(unit_key=unit.unit_key, state="no-content",
                                         why=f"非教学单元（{unit.teaching_role}），不要求概念"))
            continue
        path = parts_dir / f"{unit.unit_key}.json"
        if not path.is_file():
            status.append(PartStatus(
                unit_key=unit.unit_key, state="missing",
                why="没有概念分片：这一节还没抽过概念，不能用别的节的旧片顶上"))
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            status.append(PartStatus(unit_key=unit.unit_key, state="incomplete",
                                     why=f"分片不是合法 JSON：{exc}"))
            continue
        payloads[unit.unit_key] = payload
        coverage = payload.get("coverage") or {}
        stored_complete = coverage.get("完整")
        text_sha = str((payload.get("_meta") or {}).get("text_sha256") or "")
        n_concepts = len(payload.get("concepts") or [])
        note = str(coverage.get("完整说明") or "")

        # ⚠️ **按内容重算**，不照念 `coverage.完整`。那个字段可能是旧版本用坏尺子写的
        #    （`定理1` 被归成空串 → 判成"没覆盖"）。重算与自报**不一致时显式记出来**，
        #    不静默覆盖 —— 静默覆盖会让"尺子换过了"这件事在产物里看不见。
        recomputed = recompute_coverage(payload)
        content_missing = recomputed["complete_by_content"]
        numbers_missing = list(coverage.get("漏掉的条目") or [])
        diverged = (stored_complete is False and content_missing is True
                    and not numbers_missing)
        if numbers_missing:
            # 编号条目那一半依赖正文，本次不重算；如实说明它的来源
            complete = False
        elif content_missing is None:
            complete = None
        else:
            complete = bool(content_missing)
        if diverged:
            note = (f"自报不完整，但按内容重算**完整**（{len(recomputed['missing_named'])}"
                    f" 项具名条目都在卡片里）：旧尺子把「类型词+编号」的名字归成了空串")
        if complete is True:
            status.append(PartStatus(unit_key=unit.unit_key, state="complete",
                                     concepts=n_concepts, text_sha256=text_sha))
        elif complete is None:
            # `完整: None` + "本节没有编号条目、也没有具名结论可核对" —— 这是
            # **明确的无概念**，不是"没跑成"。两者的区别正是方案 §6 要的
            # "逐项完成/**明确无概念**"。实测本书有 10 个这样的分片。
            status.append(PartStatus(unit_key=unit.unit_key, state="no-concepts",
                                     concepts=n_concepts, text_sha256=text_sha,
                                     why=note or "分片判定本节没有可核对的概念条目"))
        else:
            missed = list(recomputed["missing_named"]) + numbers_missing
            status.append(PartStatus(
                unit_key=unit.unit_key, state="incomplete", concepts=n_concepts,
                text_sha256=text_sha,
                why=(f"分片未完整覆盖（按内容重算）：{note or '有漏项'}"
                     + (f"；漏掉 {len(missed)} 项（{missed[:3]}）" if missed else ""))))
    return payloads, tuple(status)


# ────────────────────────────────────────── 单写方合并

def merge_parts(catalog: Catalog, payloads: dict[str, dict], status: tuple[PartStatus, ...],
                *, registry: dict[str, str] | None = None,
                book_id: str = "", source_digest: str = "") -> ConceptIndex:
    """**协调器单写**地把逐单元分片合并成全书索引。

    方案 §10：*概念 ID 分配/合并、快照、索引、occurrences、范围清单都由**协调器单写***。

    合并规则（每条都对应一处在旧实现里出过问题的地方）：

    1. **按教材顺序**处理分片 —— 并行 worker 的完成先后不许影响结果；
    2. **身份键 = （归一化名, 类型, 归属单元）**。同名不同归属是两个概念。
       旧实现按名字合并，本书因此丢了 10 个概念、并把横跨 7 章的「推论 1」并成一个；
    3. **id 沿用注册表**（归一化名 → 现有 id），保住教案正文里的 `[[CONCEPT:C-0001]]`；
       注册表缺号的**新概念**从当前最大号往后排；
    4. **occurrences 取并集**、按（单元, 锚点）去重，`introduced` 优先于 `reused`；
    5. **同名多 id 记进 `collisions`**，不合并、不丢弃。
    """
    registry = dict(registry or {})
    ordered_units = [u.unit_key for u in catalog.units if u.is_teaching]
    # 容器节点自己没正文，但它所属的章里可能有概念 —— 分片照样读，
    # 只是归属落到**章**上（身份键用章）。
    ordered_units += [u.unit_key for u in catalog.units
                      if not u.is_teaching and u.teaching_role == "container"]

    entries: dict[tuple[str, str, str], ConceptEntry] = {}
    occ_seen: dict[tuple[str, str, str], set[tuple[str, str]]] = defaultdict(set)
    name_to_ids: dict[str, list[str]] = defaultdict(list)
    #: 旧形状（名字→id）也接受：那种情况下所有权重键共用同一个 id —— 会撞，
    #: 所以调用方应当传三元组键（`seed_registry` 给的就是）。这里只做兼容。
    legacy_shaped = bool(registry) and not isinstance(next(iter(registry)), tuple)

    used_ids = {cid for cid in registry.values() if cid}
    max_number = 0
    for cid in used_ids:
        m = re.match(r"^C-(\d+)$", cid)
        if m:
            max_number = max(max_number, int(m.group(1)))

    def next_id() -> str:
        nonlocal max_number
        max_number += 1
        return f"C-{max_number:04d}"

    for unit_key in ordered_units:
        payload = payloads.get(unit_key)
        if not payload:
            continue
        chapter = unit_key.split("-", 1)[0]
        for raw in payload.get("concepts") or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            ctype = str(raw.get("type") or "").strip()
            if not name or ctype not in CONCEPT_TYPES:
                # 名字/类型不合规的条目不能进索引（空名无法被正文引用）
                continue
            norm = normalize_name(name)
            key = (norm, ctype, chapter)
            cid = (entries[key].concept_id if key in entries
                   else registry.get(key) or (registry.get(norm) if legacy_shaped else None)
                   or next_id())
            if key not in entries:
                entries[key] = ConceptEntry(
                    concept_id=cid, name=name, type=ctype, ownership_unit=unit_key,
                    ownership_chapter=chapter,
                    formal=str(raw.get("formal") or ""),
                    source_quotes=tuple(x for x in [str(raw.get("source_quote") or "")] if x))
                name_to_ids[norm].append(cid)
            occ = Occurrence(unit_key=unit_key, chapter=chapter,
                             anchor=str(raw.get("anchor") or ""),
                             role=str(raw.get("role") or "introduced"),
                             title=str(raw.get("title") or ""))
            token = (unit_key, occ.anchor)
            if token in occ_seen[key]:
                continue
            occ_seen[key].add(token)
            existing = entries[key]
            entries[key] = ConceptEntry(
                concept_id=existing.concept_id, name=existing.name, type=existing.type,
                ownership_unit=existing.ownership_unit,
                ownership_chapter=existing.ownership_chapter,
                occurrences=existing.occurrences + (occ,),
                formal=existing.formal, source_quotes=existing.source_quotes)

    # 把"在别的单元被复用"也补进 occurrences（跨单元出现来自各分片自己的记录）
    for unit_key in ordered_units:
        payload = payloads.get(unit_key) or {}
        for raw in payload.get("concepts") or []:
            if not isinstance(raw, dict):
                continue
            norm = normalize_name(str(raw.get("name") or ""))
            ctype = str(raw.get("type") or "")
            for occurrence in raw.get("also_in") or []:
                occ_unit = str(occurrence.get("unit_key") or "")
                target = (norm, ctype, occ_unit.split("-", 1)[0])
                if target in entries:
                    token = (unit_key, str(occurrence.get("anchor") or ""))
                    if token in occ_seen[target]:
                        continue
                    occ_seen[target].add(token)
                    got = entries[target]
                    entries[target] = ConceptEntry(
                        concept_id=got.concept_id, name=got.name, type=got.type,
                        ownership_unit=got.ownership_unit,
                        ownership_chapter=got.ownership_chapter,
                        occurrences=got.occurrences + (Occurrence.from_dict(occurrence),),
                        formal=got.formal, source_quotes=got.source_quotes)

    # 同名多 id：把**注册表里同名的那些 id**也算进来，否则"碰撞"只看得见新号，
    # 看不见"这个名字历史上已经有另一个 id 了"——而那正是要记账的那件事。
    if legacy_shaped:
        registry_names: dict[str, list[str]] = defaultdict(list)
        for raw_name, cid in registry.items():
            registry_names[normalize_name(str(raw_name))].append(str(cid))
    else:
        registry_names = defaultdict(list)
        for key3, cid in registry.items():
            registry_names[normalize_name(str(key3[0]))].append(str(cid))
    for norm, ids in registry_names.items():
        name_to_ids[norm].extend(ids)
    collisions = {name: sorted(set(ids)) for name, ids in name_to_ids.items()
                  if len(set(ids)) > 1}

    # ── 同名跨章：历史按名合并、现在按（名, 类型, 章）拆开的名字，逐条记账
    #    为什么必须报出来：这会**改变概念表的条数**（本书 448 → 528）。
    #    条数变了却不说，就是"静默改了读者看到的东西"。
    by_norm: dict[str, list[ConceptEntry]] = defaultdict(list)
    for entry in entries.values():
        by_norm[normalize_name(entry.name)].append(entry)
    multi_chapter: dict[str, dict] = {}
    for norm, group in sorted(by_norm.items()):
        chapters = sorted({e.chapter for e in group})
        if len(chapters) <= 1:
            continue
        historical = sorted({cid for cid in (registry.get((norm, e.type, e.chapter))
                                            for e in group) if cid})
        multi_chapter[norm] = {
            "name": group[0].name, "type": group[0].type, "chapters": chapters,
            "ids_after_split": sorted(e.concept_id for e in group),
            "historical_ids": historical,
            "why": ("同名在不同章出现。旧规则按名字合并 → 7 个不同章的「推论 1」被当成同一个概念；"
                    "方案 §7 不允许'只按标题自动合并'，所以按（名, 类型, 章）拆开并在此记账。"
                    "受影响的跨章引用需人工确认指向哪一个。")}
    no_concept = {p.unit_key: p.why or "分片判定本节无可核对的概念条目"
                  for p in status if p.is_no_concept or
                  (p.state == "complete" and p.concepts == 0)}

    return ConceptIndex(book_id=book_id, concepts=tuple(
        sorted(entries.values(), key=lambda c: c.concept_id)),
        parts=status, no_concept_units=no_concept,
        collisions=collisions, multi_chapter_names=multi_chapter,
        registry=registry, source_digest=source_digest)


def load_legacy_registry(parse_root: Path) -> dict[str, str]:
    """读**旧**的 id 注册表（`concepts.ids.json`，形状是 `名字 → id`）。

    ⚠️ 这份表是**有损**的：一个名字只能对应一个 id。实测本书它有 438 条，
    而概念有 448 个 —— 差的 10 个正是"名字撞了"的那些。
    所以它只用来**报告缺口**与**沿用旧号**，不再当身份权威。
    """
    path = Path(parse_root) / "concepts.ids.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError(f"{path} 不是合法 JSON：{exc}") from exc
    ids = payload.get("ids")
    return {str(k): str(v) for k, v in (ids or {}).items()} if isinstance(ids, dict) else {}


def seed_registry(parse_root: Path) -> tuple[dict[tuple[str, str, str], str],
                                            dict[str, list[str]]]:
    """从**现有 `concepts.json`** 播下身份注册表。

    为什么用 `concepts.json` 而不是 `concepts.ids.json`：前者每个概念都有自己的 id
    （本书 448 个、id 互不相同），是**权威**；后者只有 438 条、按名字索引，是**有损**的投影。
    拿有损的那份当权威，就等于把"名字撞了"这件事固定成永久损失。

    返回 `(身份键 → id, 归一化名 → [id, ...])`。后者用来发现并如实记账同名多 id。
    """
    path = Path(parse_root) / "concepts.json"
    if not path.is_file():
        return {}, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError(f"{path} 不是合法 JSON：{exc}") from exc
    by_key: dict[tuple[str, str, str], str] = {}
    by_name: dict[str, list[str]] = defaultdict(list)
    for raw in payload.get("concepts") or []:
        if not isinstance(raw, dict):
            continue
        entry = ConceptEntry.from_dict(raw)
        by_key.setdefault(entry.key, entry.concept_id)
        by_name[normalize_name(entry.name)].append(entry.concept_id)
    return by_key, dict(by_name)


def build_index(parse_root: Path, catalog: Catalog, *, book_id: str = "") -> ConceptIndex:
    """从产物构建全书概念索引（**只读**）。

    `registry` 传的是**身份键 → id** 的权威表（由 `seed_registry` 播下）。
    旧的名字→id 表只用来算 `legacy_registry_gaps`（旧表表达不了哪些概念）。
    """
    parse_root = Path(parse_root)
    payloads, status = read_parts(parse_root, catalog)
    seeded, _by_name = seed_registry(parse_root)
    legacy = load_legacy_registry(parse_root)
    source = digest_of({"parts": {k: v.get("_meta", {}).get("text_sha256")
                                  for k, v in sorted(payloads.items())},
                        "registry": {f"{k[0]}|{k[1]}|{k[2]}": v
                                     for k, v in sorted(seeded.items())}})
    index = merge_parts(catalog, payloads, status, registry=seeded,
                        book_id=book_id or catalog.book_id, source_digest=source)
    index.legacy_registry_size = len(legacy)
    index.frozen = not index.blocking_parts()
    return index


def index_fingerprint(parse_root: Path) -> dict:
    """分片的**当前**指纹（用于判断索引是否新鲜，而不只是"文件存在"）。"""
    parse_root = Path(parse_root)
    parts_dir = parse_root / "concepts.parts"
    rows = {}
    if parts_dir.is_dir():
        for path in sorted(parts_dir.glob("*.json")):
            rows[path.stem] = digest_file(path)
    return rows


__all__ = ["CONCEPT_TYPES", "OWNERSHIP_UNKNOWN", "normalize_name", "Occurrence",
           "ConceptEntry", "PartStatus", "ConceptIndex", "read_parts", "merge_parts",
           "load_legacy_registry", "seed_registry", "build_index", "index_fingerprint"]
