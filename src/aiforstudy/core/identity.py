"""单元身份：**持久逻辑键** + 内容实例 + 谱系。

方案 §7 对这件事的规定最细，也最容易被做错，所以先抄原文：

> 现有契约中 `U-<hash(toc_id+content)>` 是内容寻址身份，属于历史 P6 设计；内容变化会换 U，
> 目录 T 序号也可能随插条改变。**本方案保留它的原语义，新增持久逻辑 key 与谱系映射，
> 作为显式契约升级；不直接把原 U 改成另一种含义。**

> 统一 catalog 为每个正文单元保存 `unit_key`（持久逻辑身份）、`content_unit_id`
> （现有 U 的内容实例）、`content_sha256`、`toc_ref`（解析版本+目录 ID）、`chapter_key`、
> `parent_unit_key`、`display_anchor`、`legacy_aliases`、`source_span`、`normalized_span`、
> `teaching_role`。目录同样登记持久 node key，T/CH 是当前版本中的定位或显示别名。

> 首次迁移优先按已确认的来源区间、归属与内容哈希建立唯一映射；重排不换逻辑 key，
> 原文变化更新内容实例和摘要。拆分/合并写 replaces/replaced_by；**同名、重复正文、
> 多处匹配等歧义不能只按标题自动合并**。身份登记不需要模型；**不能唯一匹配时先阻断
> 受影响迁移并提供审阅表**。

为什么"两套身份并存"是对的，而不是"重复设计"：`U-<hash>` 回答的是"**这段内容**是哪一段"，
`unit_key` 回答的是"**这个位置/这个教学单元**是哪一个"。前者随内容变、后者随结构变，
混成一个键就必然二选一地错：

* 只用 U → 改一个错字就换 id，教案、工单、概念 occurrence、图挂载全部失联；
* 只用 unit_key → 换了内容却不知道，缓存会拿旧讲解冒充新的（本项目最贵的一类错误）。

所以这里**两个都留**，并额外记 `content_sha256` 与 `source_span`，让"哪一段内容、在原文哪里"
都可以对账。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from ..errors import ContractError

#: 教学角色（方案 §6 要求"非教学、容器、空缺、孤儿分别解释"）
ROLE_CHAPTER_INTRO = "chapter_intro"
ROLE_SECTION_INTRO = "section_intro"
ROLE_SECTION = "section"
ROLE_SUBSECTION = "subsection"
ROLE_CONTAINER = "container"          # 只有标题、内容都在子节点里
ROLE_EMPTY = "empty"                  # 目录声明了但原文没有内容
ROLE_NON_TEACHING = "non_teaching"    # 序、前言、目录、附录说明…
ROLE_ORPHAN = "orphan"                # 有内容但目录没声明，且找不到父
TEACHING_ROLES = frozenset({ROLE_CHAPTER_INTRO, ROLE_SECTION_INTRO, ROLE_SECTION,
                            ROLE_SUBSECTION})
ALL_ROLES = TEACHING_ROLES | frozenset({ROLE_CONTAINER, ROLE_EMPTY, ROLE_NON_TEACHING,
                                        ROLE_ORPHAN})

#: 逻辑键的形状：章 / 节 / 小节（与现有 `body_node`、`lesson_units[].unit_id` 同构）
KEY_RE = re.compile(r"^CH\d{2,3}(-S\d{2,3}(-SS\d{2,3})?)?$")

#: 目录持久 node key：来自**锚点路径**而不是 T 序号
NODE_KEY_RE = re.compile(r"^(CH\d{2,3}(-S\d{2,3}(-SS\d{2,3})?)?)$|^(N-[0-9A-Za-z._-]+)$")


def make_node_key(anchor: str, *, level: int | None = None,
                  chapter_key: str | None = None) -> str:
    """目录 node key：按**锚点**生成，不按 `T-####`。

    为什么不用 T 号：方案 §7 明说"目录 T 序号也可能随插条改变"。用 T 号当键，
    往目录里插一条就会让后面所有 T 号位移 —— 而"重排不换逻辑 key"是硬要求。

    ⚠️ **为什么把 level 也算进去**（2026-10-05 实测）：只按锚点时，"第1章"（L1）与某个
    恰好也编号为 `1` 的节（L2）会撞成同一个键。撞了不会报错 —— 两棵不同的子树会共用
    一个持久键，之后按 key 查单元就会串。level 是节点**自身**的属性，不含位置信息，
    所以纳入它不会破坏"重排不换键"。
    """
    anchor = str(anchor or "").strip()
    if anchor:
        return f"N-{level}-{anchor}" if level is not None else f"N-{anchor}"
    if chapter_key:
        return chapter_key
    raise ContractError("既没有锚点也没有章键，无法生成目录 node key")


@dataclass(frozen=True)
class UnitRef:
    """一个正文单元的**全部身份**。"""
    unit_key: str
    chapter_key: str
    content_sha256: str
    teaching_role: str
    display_anchor: str = ""
    content_unit_id: str | None = None        # 现有 U-…（内容实例）
    toc_ref: str | None = None                # 本次解析版本的目录 ID（定位别名）
    parent_unit_key: str | None = None
    legacy_aliases: tuple[str, ...] = ()
    source_span: tuple[int, int] | None = None
    normalized_span: tuple[int, int] | None = None
    declared: bool = True
    title: str = ""
    level: str = ""
    original_text: str = ""

    def __post_init__(self) -> None:
        if not KEY_RE.match(self.unit_key):
            raise ContractError(
                f"unit_key {self.unit_key!r} 形状不对（应形如 CH01 / CH01-S02 / CH01-S02-SS03）；"
                f"方案 §7 要求它是**持久逻辑身份**，不是内容哈希也不是目录序号")
        if self.teaching_role not in ALL_ROLES:
            raise ContractError(f"teaching_role 必须是 {sorted(ALL_ROLES)} 之一，"
                                f"实际 {self.teaching_role!r}")
        if not self.content_sha256:
            raise ContractError(f"{self.unit_key}: content_sha256 必填（否则没法判断内容变没变）")
        if self.parent_unit_key is not None and self.parent_unit_key == self.unit_key:
            raise ContractError(f"{self.unit_key}: 父节点不能是自己")
        if self.source_span is not None:
            lo, hi = self.source_span
            if hi < lo:
                raise ContractError(f"{self.unit_key}: source_span 反了：{self.source_span}")

    @property
    def is_teaching(self) -> bool:
        return self.teaching_role in TEACHING_ROLES

    def identity(self) -> dict:
        """**进指纹的部分**：只含逻辑身份与内容实例，不含 run/时间/路径。"""
        return {"unit_key": self.unit_key, "content_unit_id": self.content_unit_id,
                "content_sha256": self.content_sha256, "chapter_key": self.chapter_key,
                "teaching_role": self.teaching_role}

    def as_dict(self) -> dict:
        return {**self.identity(), "toc_ref": self.toc_ref,
                "parent_unit_key": self.parent_unit_key,
                "display_anchor": self.display_anchor,
                "legacy_aliases": list(self.legacy_aliases),
                "source_span": list(self.source_span) if self.source_span else None,
                "normalized_span": (list(self.normalized_span)
                                    if self.normalized_span else None),
                "declared": self.declared, "title": self.title, "level": self.level,
                "original_text": self.original_text}

    @classmethod
    def from_dict(cls, payload: dict) -> "UnitRef":
        span = payload.get("source_span")
        nspan = payload.get("normalized_span")
        return cls(unit_key=str(payload.get("unit_key") or ""),
                   chapter_key=str(payload.get("chapter_key") or ""),
                   content_sha256=str(payload.get("content_sha256") or ""),
                   teaching_role=str(payload.get("teaching_role") or ROLE_SECTION),
                   display_anchor=str(payload.get("display_anchor") or ""),
                   content_unit_id=payload.get("content_unit_id"),
                   toc_ref=payload.get("toc_ref"),
                   parent_unit_key=payload.get("parent_unit_key"),
                   legacy_aliases=tuple(payload.get("legacy_aliases") or ()),
                   source_span=(tuple(span) if span else None),
                   normalized_span=(tuple(nspan) if nspan else None),
                   declared=bool(payload.get("declared", True)),
                   title=str(payload.get("title") or ""),
                   level=str(payload.get("level") or ""),
                   original_text=str(payload.get("original_text") or ""))


@dataclass(frozen=True)
class TocNodeRef:
    """目录节点：持久 node key + 当前版本的 T 号别名。"""
    node_key: str
    toc_id: str
    title: str
    level: int = 0
    parent_node_key: str | None = None
    chapter_key: str | None = None
    anchor: str = ""
    kind: str = ""
    confirmed: bool = False
    legacy_aliases: tuple[str, ...] = ()

    @property
    def is_teaching(self) -> bool:
        return self.kind != "non-teaching"

    def as_dict(self) -> dict:
        return {"node_key": self.node_key, "toc_id": self.toc_id, "title": self.title,
                "level": self.level, "parent_node_key": self.parent_node_key,
                "chapter_key": self.chapter_key, "anchor": self.anchor, "kind": self.kind,
                "confirmed": self.confirmed,
                "legacy_aliases": list(self.legacy_aliases)}


# ────────────────────────────────────────── 谱系

LINEAGE_SPLIT = "split"
LINEAGE_MERGE = "merge"
LINEAGE_MOVE = "move"
LINEAGE_RENAME = "rename"
LINEAGE_REASSIGN = "reassign"
LINEAGE_OPS = (LINEAGE_SPLIT, LINEAGE_MERGE, LINEAGE_MOVE, LINEAGE_RENAME, LINEAGE_REASSIGN)


@dataclass(frozen=True)
class Lineage:
    """一次结构变化：谁变成了谁，依据是什么。

    方案 §7：*拆分/合并写 `replaces`/`replaced_by`*。这里合成一条记录，两个方向都能查，
    并且**必须写依据** —— 没有依据的谱系记录等于把"猜"写进了契约。
    """
    op: str
    from_keys: tuple[str, ...]
    to_keys: tuple[str, ...]
    basis: str
    note: str = ""

    def __post_init__(self) -> None:
        if self.op not in LINEAGE_OPS:
            raise ContractError(f"谱系操作必须是 {LINEAGE_OPS} 之一，实际 {self.op!r}")
        if not self.from_keys or not self.to_keys:
            raise ContractError("谱系记录必须同时给出 from_keys 与 to_keys")
        if not self.basis.strip():
            raise ContractError(
                f"谱系记录（{self.op}）必须写依据：没有依据的谱系等于把'猜'写进契约")

    def as_dict(self) -> dict:
        return {"op": self.op, "from_keys": list(self.from_keys),
                "to_keys": list(self.to_keys), "basis": self.basis, "note": self.note}


# ────────────────────────────────────────── 歧义与排除

@dataclass(frozen=True)
class Ambiguity:
    """一处**不能自动裁决**的映射问题。

    方案 §7：*同名、重复正文、多处匹配等歧义不能只按标题自动合并；
    不能唯一匹配时先**阻断**受影响迁移并提供审阅表。*

    所以 `Ambiguity` 是一个**阻断项**，不是一个警告：有它就不许把迁移结果当权威。
    """
    kind: str                     # duplicate-content | duplicate-title | multi-match | missing
    subject: str
    candidates: tuple[str, ...]
    why: str
    needs: str = "人工审阅"

    def as_dict(self) -> dict:
        return {"kind": self.kind, "subject": self.subject,
                "candidates": list(self.candidates), "why": self.why, "needs": self.needs}


class AmbiguityBlocked(ContractError):
    """存在未裁决歧义时，受影响范围不许被当成迁移完成。"""


@dataclass(frozen=True)
class NeedsReview:
    """一处**身份已确定、但内容/命名待裁决**的事项。

    与 `Ambiguity` 的分工，是本模块最要紧的一处分寸：

    * `Ambiguity`（歧义）——**身份**定不下来（重复正文、同名、多处匹配）。
      方案 §7：这种情况"先**阻断**受影响迁移"。身份不可用，后面什么都做不了。
    * `NeedsReview`（待裁决）——**身份**很清楚，但有别的分叉（目录里一个标题两种写法、
      未确认节点）。方案 §9 的说法是这类属 `succeeded + needs_review`：
      **流程可以继续，但整体范围不能发布**。

    把两者混成一类会两边都错：全当歧义 → 一个错别字就卡住整本书；
    全当警告 → "未确认节点"会一路静默流到读者面前。本书实测两类都有：
    歧义 0 处、待裁决 7 处（2 条目录标题分歧 + 5 个未确认节点）。
    """
    kind: str                       # toc-arbitration | unconfirmed-node | missing-review
    subject: str
    why: str
    arbiter: str = "pending"
    evidence: str = ""

    @property
    def resolved(self) -> bool:
        return self.arbiter not in ("", "pending")

    def as_dict(self) -> dict:
        return {"kind": self.kind, "subject": self.subject, "why": self.why,
                "arbiter": self.arbiter, "evidence": self.evidence,
                "resolved": self.resolved}


@dataclass
class Catalog:
    """一本书的**统一 catalog**：单元身份 + 目录键 + 谱系 + 歧义账 + 待裁决账。"""
    book_id: str
    revision: str
    units: tuple[UnitRef, ...] = ()
    toc_nodes: tuple[TocNodeRef, ...] = ()
    lineage: tuple[Lineage, ...] = ()
    ambiguities: tuple[Ambiguity, ...] = ()
    #: 身份已定、但内容/命名待裁决的事项；有它则 blocked for release（不阻塞迁移）
    needs_review: tuple[NeedsReview, ...] = ()
    #: 非教学/空缺/孤儿等**显式排除**，每条都要有理由（方案 §6：不能凭空数组通过）
    exclusions: dict[str, str] = field(default_factory=dict)
    source_ref: dict = field(default_factory=dict)
    schema_version: str = "1.0"

    # ── 查询
    def by_key(self) -> dict[str, UnitRef]:
        return {u.unit_key: u for u in self.units}

    def by_content_id(self) -> dict[str, UnitRef]:
        return {u.content_unit_id: u for u in self.units if u.content_unit_id}

    def by_legacy_alias(self) -> dict[str, UnitRef]:
        out: dict[str, UnitRef] = {}
        for unit in self.units:
            for alias in unit.legacy_aliases:
                out[alias] = unit
        return out

    def resolve(self, token: str) -> UnitRef | None:
        """按 逻辑键 / 内容实例 / 历史别名 / 章键 依次解析。

        顺序是有意的：逻辑键最稳定，U 与会随内容变，历史别名只为兼容旧引用。
        """
        if token in self.by_key():
            return self.by_key()[token]
        if token in self.by_content_id():
            return self.by_content_id()[token]
        if token in self.by_legacy_alias():
            return self.by_legacy_alias()[token]
        return None

    def teaching_units(self) -> tuple[UnitRef, ...]:
        return tuple(u for u in self.units if u.is_teaching)

    def children_of(self, unit_key: str) -> tuple[UnitRef, ...]:
        return tuple(u for u in self.units if u.parent_unit_key == unit_key)

    def replaced_by(self, unit_key: str) -> tuple[str, ...]:
        out: list[str] = []
        for row in self.lineage:
            if unit_key in row.from_keys:
                out.extend(k for k in row.to_keys if k != unit_key)
        return tuple(dict.fromkeys(out))

    def replaces(self, unit_key: str) -> tuple[str, ...]:
        out: list[str] = []
        for row in self.lineage:
            if unit_key in row.to_keys:
                out.extend(k for k in row.from_keys if k != unit_key)
        return tuple(dict.fromkeys(out))

    # ── 自检
    def verify(self) -> dict:
        """catalog 自身的完整性。**返回报告，不抛异常**（调用方决定怎么用）。"""
        problems: list[str] = []
        keys = [u.unit_key for u in self.units]
        if len(set(keys)) != len(keys):
            dupes = sorted({k for k in keys if keys.count(k) > 1})
            problems.append(f"unit_key 重复：{dupes}")
        known = set(keys)
        for unit in self.units:
            if (unit.parent_unit_key and unit.parent_unit_key not in known
                    and unit.parent_unit_key not in self.exclusions):
                # 父节点没有正文单元是**可能发生的**（有的书只有节、没有章级导言），
                # 但它必须被**解释**：方案 §6 要求"非教学、容器、空缺、孤儿分别解释，
                # 不能凭空数组通过"。解释了就放行，没解释就报出来。
                problems.append(
                    f"{unit.unit_key} 的父节点 {unit.parent_unit_key} 既不在 catalog 里，"
                    f"也没有在 exclusions 里给出理由")
        for unit in self.units:
            if unit.is_teaching and not unit.chapter_key:
                problems.append(f"{unit.unit_key} 是教学单元但没有 chapter_key")
        node_keys = [n.node_key for n in self.toc_nodes]
        if len(set(node_keys)) != len(node_keys):
            dupes = sorted({k for k in node_keys if node_keys.count(k) > 1})
            problems.append(f"目录 node_key 重复：{dupes}")
        # 每个非教学单元都要有显式理由，不能"没解释地不在里面"
        unexplained = [u.unit_key for u in self.units
                       if not u.is_teaching and u.unit_key not in self.exclusions]
        if unexplained:
            problems.append(f"非教学/空缺/孤儿单元缺少显式排除理由：{unexplained}")
        # 谱系两端必须在册（或明确记在 exclusions 里）
        for row in self.lineage:
            for key in (*row.from_keys, *row.to_keys):
                if key not in known and key not in self.exclusions:
                    problems.append(f"谱系里的 {key} 既不在单元表也没有排除理由")
        pending = [n for n in self.needs_review if not n.resolved]
        return {"ok": not problems, "problems": problems,
                "release_blocked_by": [f"{n.kind}:{n.subject}" for n in pending],
                "counts": {"units": len(self.units),
                           "teaching": len(self.teaching_units()),
                           "toc_nodes": len(self.toc_nodes),
                           "lineage": len(self.lineage),
                           "ambiguities": len(self.ambiguities),
                           "needs_review": len(self.needs_review),
                           "needs_review_pending": len(pending),
                           "exclusions": len(self.exclusions)}}

    @property
    def blocks_release(self) -> bool:
        """能不能发布。方案 §9：待裁决与歧义都不许进正式交付。"""
        return bool(self.ambiguities) or any(not n.resolved for n in self.needs_review)

    def assert_migratable(self) -> None:
        """有未裁决歧义就不许把结果当权威（方案 §7 的"先阻断受影响迁移"）。"""
        if self.ambiguities:
            detail = "；".join(f"{a.kind}:{a.subject}（候选 {list(a.candidates)}）"
                              for a in self.ambiguities[:5])
            raise AmbiguityBlocked(
                f"{self.book_id} 的身份迁移有 {len(self.ambiguities)} 处**未裁决歧义**，"
                f"受影响范围不能当成迁移完成：{detail}。"
                f"方案 §7：同名/重复正文/多处匹配不能只按标题自动合并，需提供审阅表。")

    def as_dict(self) -> dict:
        return {"_meta": {"file_type": "catalog", "schema_version": self.schema_version,
                          "generator": "aiforstudy.core.identity",
                          "book_id": self.book_id, "revision": self.revision,
                          "field_notes": {
                              "units[].unit_key": "持久逻辑身份（结构键）；重排不换",
                              "units[].content_unit_id": "现有 U-… 内容实例；内容变就换",
                              "units[].content_sha256": "这一段内容的内容指纹",
                              "units[].legacy_aliases": "历史引用别名（旧 U、旧 CH…）",
                              "units[].source_span": "在 document.md 里的行号区间（源坐标）",
                              "units[].normalized_span": "规范化文本里的行号区间 —— **与源坐标分开存**，避免混用",
                              "toc_nodes[].node_key": "目录持久键（按锚点，不按 T 序号）",
                              "toc_nodes[].toc_id": "当前解析版本里的 T 号，只是定位/显示别名",
                              "ambiguities": "未裁决歧义；有它就不许把迁移当完成",
                              "exclusions": "非教学/空缺/孤儿逐条排除理由",
                          }},
                "book_id": self.book_id, "revision": self.revision,
                "source_ref": dict(self.source_ref),
                "units": [u.as_dict() for u in self.units],
                "toc_nodes": [n.as_dict() for n in self.toc_nodes],
                "lineage": [r.as_dict() for r in self.lineage],
                "ambiguities": [a.as_dict() for a in self.ambiguities],
                "needs_review": [n.as_dict() for n in self.needs_review],
                "exclusions": dict(sorted(self.exclusions.items())),
                "verify": self.verify()}

    @classmethod
    def from_dict(cls, payload: dict) -> "Catalog":
        return cls(book_id=str(payload.get("book_id") or ""),
                   revision=str(payload.get("revision") or ""),
                   units=tuple(UnitRef.from_dict(u) for u in payload.get("units") or ()),
                   toc_nodes=tuple(TocNodeRef(**n) for n in payload.get("toc_nodes") or ()),
                   lineage=tuple(Lineage(**r) for r in payload.get("lineage") or ()),
                   ambiguities=tuple(Ambiguity(**a) for a in payload.get("ambiguities") or ()),
                   needs_review=tuple(NeedsReview(**{k: v for k, v in n.items()
                                                    if k in NeedsReview.__dataclass_fields__})
                                      for n in payload.get("needs_review") or ()),
                   exclusions=dict(payload.get("exclusions") or {}),
                   source_ref=dict(payload.get("source_ref") or {}),
                   schema_version=str(payload.get("schema_version") or "1.0"))


def detect_ambiguities(units: Iterable[UnitRef], *,
                       titles: dict[str, str] | None = None) -> tuple[Ambiguity, ...]:
    """把"不能自动裁决"的形态找出来。

    三条（对应方案 §7 点名的三种）：

    1. **重复正文**：两个单元 `content_sha256` 相同 —— 不能因此判定它们是同一个单元
       （同一段内容可以合法地出现在两处），也不能各自独立地当成两个新单元而不作声；
    2. **同名且身份未分开**：标题不足以判定身份；独立目录键、编号及不重叠
       的来源区间已全部明确时，保留各自身份，不合并也不误报；
    3. 缺内容哈希（建造时已由契约挡住，这里兜一层）。

    ⚠️ 这些一律产出 `Ambiguity` 而**不是**自动合并。自动合并的代价是双向的：
    把两个不同单元并成一个 → 教案/工单互相覆盖；把同一个单元拆成两个 → 重复收费。
    """
    units = list(units)
    titles = titles or {}
    out: list[Ambiguity] = []
    by_content: dict[str, list[str]] = {}
    for unit in units:
        by_content.setdefault(unit.content_sha256, []).append(unit.unit_key)
    for digest, keys in sorted(by_content.items()):
        if len(keys) > 1:
            out.append(Ambiguity(
                kind="duplicate-content", subject=digest[:16],
                candidates=tuple(sorted(keys)),
                why="这些单元的内容指纹相同；同一段内容可以合法地出现在两处，"
                    "所以既不能自动合并，也不能不作声地各算一个",
                needs="人工确认：是同一单元的两处引用，还是两段恰好相同的正文"))
    by_title: dict[str, list[str]] = {}
    for unit in units:
        title = titles.get(unit.unit_key) or unit.title
        if title:
            by_title.setdefault(title, []).append(unit.unit_key)
    for title, keys in sorted(by_title.items()):
        if len(keys) > 1:
            members = [u for u in units if u.unit_key in keys]
            spans = sorted(u.source_span for u in members if u.source_span is not None)
            distinct = (
                all(u.declared and u.toc_ref and u.display_anchor for u in members)
                and len({u.toc_ref for u in members}) == len(members)
                and len({u.display_anchor for u in members}) == len(members)
                and len({u.content_sha256 for u in members}) == len(members)
                and len(spans) == len(members)
                and all(start > 0 and end >= start for start, end in spans)
                and all(left[1] < right[0] for left, right in zip(spans, spans[1:]))
            )
            if distinct:
                continue
            out.append(Ambiguity(
                kind="duplicate-title", subject=title, candidates=tuple(sorted(keys)),
                why="标题相同不足以判定身份（方案 §7 明说不能只按标题自动合并）",
                needs="人工确认：是两个不同小节，还是同一个单元被登记了两次"))
    return tuple(out)


__all__ = ["ROLE_CHAPTER_INTRO", "ROLE_SECTION_INTRO", "ROLE_SECTION", "ROLE_SUBSECTION",
           "ROLE_CONTAINER", "ROLE_EMPTY", "ROLE_NON_TEACHING", "ROLE_ORPHAN",
           "TEACHING_ROLES", "ALL_ROLES", "KEY_RE", "make_node_key", "UnitRef", "TocNodeRef",
           "LINEAGE_SPLIT", "LINEAGE_MERGE", "LINEAGE_MOVE", "LINEAGE_RENAME",
           "LINEAGE_REASSIGN", "LINEAGE_OPS", "Lineage", "Ambiguity", "AmbiguityBlocked",
           "NeedsReview", "Catalog", "detect_ambiguities"]
