"""概念块（方案 §3 的 `blocks/concepts/`：概念抽取与**单写方合并**）。

M4 落地两件：

* `index.py` —— 全书概念索引：身份、覆盖、单写方合并、冻结与新鲜度。
  它替掉的是"按概念名合并"的旧做法；本里程碑实测证明那个做法**丢东西**（见模块注释）。

抽取本身（调模型那一步）仍由旧 `blocks/parse/concepts/` 承担，M5 前后迁入。
"""
from __future__ import annotations

from .index import (CONCEPT_TYPES, OWNERSHIP_UNKNOWN, ConceptEntry, ConceptIndex, Occurrence,
                    PartStatus, build_index, index_fingerprint, load_legacy_registry, seed_registry, merge_parts,
                    normalize_name, read_parts)

__all__ = ["CONCEPT_TYPES", "OWNERSHIP_UNKNOWN", "ConceptEntry", "ConceptIndex", "Occurrence",
           "PartStatus", "build_index", "index_fingerprint", "load_legacy_registry",
           "seed_registry", "merge_parts",
           "normalize_name", "read_parts"]
