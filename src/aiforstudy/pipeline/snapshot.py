"""一次运行冻结的上下文快照：**三件套** + 本节概念 + 符号表 + 可用图。

方案 §7 把这一层讲得最细，逐条抄下来并在下面逐条对应：

> 全书上下文**按一次运行冻结**：完整目录精简树、完整概念/定理/公式条目索引、教材顺序、
> 本节全文、本节与显式依赖条目、符号表、可用图。完整索引指**身份、名称、类型、归属、
> 原文锚点**，不把全书正文与所有详解反复送入每节。

> 本节原文、全书目录和完整条目索引**不得静默截断**。请求前计算上下文预算；
> 确定性紧凑表示仍超上限时标 `CONTEXT_TOO_LARGE`，**不发请求**。
> 可选回顾按固定政策裁剪并记录；后续超长书的分段检索策略另行验收，不用"模型可能记得"兜底。

> 章内可按教材顺序追加本趟已通过的前序信息；**跨章只读取起跑时冻结的快照**。
> 相同起始快照下并行 1/2/8 的**请求输入指纹应一致**，不能让 worker 完成先后改变提示词。

## 一处必须讲清楚的设计取舍

"章内可按教材顺序追加**本趟已通过**的前序信息"与"不能让 worker 完成先后改变提示词"
这两句**在并行下不能同时按字面成立**：如果第 3 节的提示词包含"第 1、2 节已通过"，
那么第 1 节先跑完还是第 2 节先跑完，会让第 3 节拿到的提示词不同 —— 而并行度是运行参数。

本实现的取舍：**前序信息只从起跑时冻结的快照里取**（`prior_approved`），
即"在本次运行开始之前就已经通过的内容"。理由：

* 它让"同一份快照 + 同一个单元 → 同一个请求指纹"成为**可证明的性质**（有测试）；
* 方案 §7 的上一条要求就在同一段里、而且更硬："**跨章只读取起跑时冻结的快照**" ——
  把"章内"放宽成读实时结果，会让同一段规则前后自相矛盾；
* 代价是"本趟刚通过的前序"不进提示词。这不是丢功能：那部分内容本来就在**完整条目索引**里
  （概念的身份/名称/类型/归属/锚点），而 §7 明确说"不把全书正文与所有详解反复送入每节"。

快照的 `digest` 进每个单元的请求指纹，所以"快照变了"与"单元变了"两种失效都不会漏。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.digest import digest_of
from ..core.identity import Catalog
from ..core.source import CanonicalSource
from ..errors import ContractError
from ..blocks.concepts.index import ConceptIndex

#: 上下文超限时的标记（§7 要求"标 CONTEXT_TOO_LARGE，不发请求"）
CONTEXT_TOO_LARGE = "CONTEXT_TOO_LARGE"
#: 缺上下文时的标记（缺快照/缺原文都属于这一类，不许拿"模型可能记得"兜底）
CONTEXT_MISSING = "CONTEXT_MISSING"

#: 字符→token 的粗略折算（用于**预算判断**，不是计费口径）
CHARS_PER_TOKEN = 1.6
#: 单次请求的上下文上限（字符）。超了先走"确定性紧凑表示"，仍超则不发请求。
DEFAULT_CONTEXT_CHARS = 900_000


@dataclass(frozen=True)
class RunSnapshot:
    """一次运行**冻结**的上下文来源。worker 只读它，不读"当前最新"。"""
    book_id: str
    catalog_digest: str
    concept_index_digest: str
    source_revision: str
    selected_text_sha256: str
    toc_tree: tuple[dict, ...] = ()
    entry_index: tuple[dict, ...] = ()
    unit_keys: tuple[str, ...] = ()
    prior_approved: dict[str, str] = field(default_factory=dict)   # unit_key → 一行摘要
    symbols: tuple[str, ...] = ()
    figures: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if not self.source_revision:
            raise ContractError("快照必须钉住 source revision（原文变了就不能复用旧讲解）")
        if not self.selected_text_sha256.startswith("sha256:"):
            raise ContractError("快照必须钉住 selected_text 指纹")

    def digest(self) -> str:
        """**不含时间、run_id、工作区路径**（方案 §11）—— 所以它可跨机器比较。"""
        return digest_of({
            "book_id": self.book_id, "catalog_digest": self.catalog_digest,
            "concept_index_digest": self.concept_index_digest,
            "source_revision": self.source_revision,
            "selected_text_sha256": self.selected_text_sha256,
            "toc_tree": list(self.toc_tree), "entry_index": list(self.entry_index),
            "unit_keys": list(self.unit_keys), "prior_approved": self.prior_approved,
            "symbols": list(self.symbols), "figures": list(self.figures),
        })

    def as_dict(self) -> dict:
        return {"_meta": {"file_type": "run_snapshot", "schema_version": self.schema_version,
                          "generator": "aiforstudy.pipeline.snapshot",
                          "field_notes": {
                              "toc_tree": "完整目录精简树（身份/标题/层级/锚点/归属），**不截断**",
                              "entry_index": "完整概念条目索引（身份/名称/类型/归属/原文锚点），**不截断**",
                              "prior_approved": "起跑前就已通过的前序信息；**不取本趟实时结果**，否则并行度会改变提示词",
                              "digest()": "不含时间/run_id/绝对路径，可跨机器比较",
                          }},
                "book_id": self.book_id, "catalog_digest": self.catalog_digest,
                "concept_index_digest": self.concept_index_digest,
                "source_revision": self.source_revision,
                "selected_text_sha256": self.selected_text_sha256,
                "toc_tree": list(self.toc_tree), "entry_index": list(self.entry_index),
                "unit_keys": list(self.unit_keys), "prior_approved": dict(self.prior_approved),
                "symbols": list(self.symbols), "figures": list(self.figures),
                "notes": list(self.notes),
                "digest": self.digest()}


@dataclass(frozen=True)
class ContextBundle:
    """送给某一节生成阶段的上下文。**三件套一起进请求**。"""
    unit_key: str
    snapshot_digest: str
    toc_tree: tuple[dict, ...]
    entry_index: tuple[dict, ...]
    unit_text: str
    concepts_here: tuple[dict, ...] = ()
    related_entries: tuple[dict, ...] = ()
    symbols: tuple[str, ...] = ()
    figures: tuple[str, ...] = ()
    #: None = 在预算内；否则是 CONTEXT_TOO_LARGE / CONTEXT_MISSING
    refuse_reason: str | None = None
    budget: dict = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.refuse_reason is None

    @property
    def has_the_three_parts(self) -> bool:
        """方案 §7 的"三件套"：全书目录 + 完整条目索引 + 本节全文。"""
        return bool(self.toc_tree) and bool(self.entry_index) and bool(self.unit_text)

    def digest(self) -> str:
        """请求输入指纹。**只取决于快照 + 单元**，与并行度无关。"""
        return digest_of({
            "unit_key": self.unit_key, "snapshot_digest": self.snapshot_digest,
            "toc_tree": list(self.toc_tree), "entry_index": list(self.entry_index),
            "unit_text_sha": digest_of(self.unit_text) if self.unit_text else "",
            "concepts_here": list(self.concepts_here),
            "related_entries": list(self.related_entries),
            "symbols": list(self.symbols), "figures": list(self.figures),
            "refuse_reason": self.refuse_reason,
        })

    def as_request_payload(self) -> dict:
        """实际会进请求的那一份。**不含**任何时间/run_id/绝对路径。"""
        return {"unit_key": self.unit_key, "snapshot_digest": self.snapshot_digest,
                "toc_tree": list(self.toc_tree), "entry_index": list(self.entry_index),
                "unit_text": self.unit_text, "concepts_here": list(self.concepts_here),
                "related_entries": list(self.related_entries),
                "symbols": list(self.symbols), "figures": list(self.figures),
                "refuse_reason": self.refuse_reason}

    def as_dict(self) -> dict:
        return {**self.as_request_payload(), "budget": dict(self.budget),
                "notes": list(self.notes), "digest": self.digest(),
                "has_the_three_parts": self.has_the_three_parts}


# ────────────────────────────────────────── 冻结

def freeze(book_id: str, catalog: Catalog, index: ConceptIndex, source: CanonicalSource, *,
           unit_keys: tuple[str, ...] | None = None,
           prior_approved: dict[str, str] | None = None,
           figures: tuple[str, ...] = (), symbols: tuple[str, ...] = (),
           notes: tuple[str, ...] = ()) -> RunSnapshot:
    """在运行开始时**冻结**一次上下文。之后所有 worker 只读它。"""
    units = unit_keys or tuple(u.unit_key for u in catalog.units if u.is_teaching)
    toc_tree = tuple({
        "node_key": n.node_key, "toc_id": n.toc_id, "title": n.title, "level": n.level,
        "anchor": n.anchor, "chapter_key": n.chapter_key, "kind": n.kind,
        "parent_node_key": n.parent_node_key,
    } for n in catalog.toc_nodes)
    # 条目索引 = **全部**概念的身份/名称/类型/归属/锚点（§7 点名的那五项）
    entry_index = tuple({
        "id": c.concept_id, "name": c.name, "type": c.type,
        "ownership_chapter": c.chapter, "ownership_unit": c.ownership_unit,
        "anchor": (c.occurrences[0].anchor if c.occurrences else ""),
    } for c in index.concepts)
    return RunSnapshot(
        book_id=book_id, catalog_digest=digest_of([u.identity() for u in catalog.units]),
        concept_index_digest=digest_of(index.source_digest or
                                       [c.identity() for c in index.concepts]),
        source_revision=source.revision,
        selected_text_sha256=source.selected_text_sha256,
        toc_tree=toc_tree, entry_index=entry_index, unit_keys=tuple(units),
        prior_approved=dict(prior_approved or {}), symbols=tuple(symbols),
        figures=tuple(figures), notes=tuple(notes))


# ────────────────────────────────────────── 组装

def unit_context(snapshot: RunSnapshot, unit_key: str, *, unit_text: str,
                 concepts_here: tuple[dict, ...] = (),
                 related_entries: tuple[dict, ...] = (),
                 max_chars: int = DEFAULT_CONTEXT_CHARS) -> ContextBundle:
    """组装一节的上下文。**先算预算，超限就不发请求。**

    三件套**一律完整**放进去，不做"取前 N 条"这种截断 —— 方案 §7 明说"不得静默截断"。
    超限时的正确处理是**拒绝发请求**并把确切的体积报出来，让人去改策略（分段检索），
    而不是悄悄砍掉目录的后半本。
    """
    concepts = tuple(concepts_here or ())
    related = tuple(related_entries or ())
    payload = {"toc_tree": list(snapshot.toc_tree), "entry_index": list(snapshot.entry_index),
               "unit_text": unit_text, "concepts_here": list(concepts),
               "related_entries": list(related), "symbols": list(snapshot.symbols),
               "figures": list(snapshot.figures)}
    import json
    chars = len(json.dumps(payload, ensure_ascii=False))
    budget = {"chars": chars, "max_chars": max_chars,
              "approx_tokens": int(chars / CHARS_PER_TOKEN),
              "parts": {"toc_tree": len(snapshot.toc_tree),
                        "entry_index": len(snapshot.entry_index),
                        "unit_text_chars": len(unit_text),
                        "concepts_here": len(concepts),
                        "related_entries": len(related)}}
    if not unit_text.strip():
        return ContextBundle(unit_key=unit_key, snapshot_digest=snapshot.digest(),
                             toc_tree=snapshot.toc_tree, entry_index=snapshot.entry_index,
                             unit_text="", refuse_reason=CONTEXT_MISSING, budget=budget,
                             notes=("本节原文为空 —— 缺原文不能用'模型可能记得'兜底"
                                    "（方案 §7）",))
    if chars > max_chars:
        return ContextBundle(unit_key=unit_key, snapshot_digest=snapshot.digest(),
                             toc_tree=snapshot.toc_tree, entry_index=snapshot.entry_index,
                             unit_text=unit_text, concepts_here=concepts,
                             related_entries=related, symbols=snapshot.symbols,
                             figures=snapshot.figures, refuse_reason=CONTEXT_TOO_LARGE,
                             budget=budget,
                             notes=(f"确定性紧凑表示仍有 {chars} 字符 > 上限 {max_chars}。"
                                    f"方案 §7：标 {CONTEXT_TOO_LARGE}，**不发请求**；"
                                    f"截断三件套是禁止的，分段检索属另一项验收。",))
    return ContextBundle(unit_key=unit_key, snapshot_digest=snapshot.digest(),
                         toc_tree=snapshot.toc_tree, entry_index=snapshot.entry_index,
                         unit_text=unit_text, concepts_here=concepts,
                         related_entries=related, symbols=snapshot.symbols,
                         figures=snapshot.figures, budget=budget)


def all_unit_contexts(snapshot: RunSnapshot, texts: dict[str, str], *,
                      index: ConceptIndex | None = None,
                      max_chars: int = DEFAULT_CONTEXT_CHARS) -> dict[str, ContextBundle]:
    """按**教材顺序**组装全部单元的上下文（顺序固定，便于比对指纹）。"""
    out: dict[str, ContextBundle] = {}
    for unit_key in snapshot.unit_keys:
        concepts_here = ()
        if index is not None:
            concepts_here = tuple(c.as_dict() for c in index.introduced_in(unit_key))
        out[unit_key] = unit_context(snapshot, unit_key,
                                     unit_text=texts.get(unit_key, ""),
                                     concepts_here=concepts_here, max_chars=max_chars)
    return out


__all__ = ["CONTEXT_TOO_LARGE", "CONTEXT_MISSING", "CHARS_PER_TOKEN", "DEFAULT_CONTEXT_CHARS",
           "RunSnapshot", "ContextBundle", "freeze", "unit_context", "all_unit_contexts"]
