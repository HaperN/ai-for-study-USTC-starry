"""核心底座：工作区、书籍身份、原文、文件、引用、用量。

方案 §3 的 `core/` 一栏。M1 落地：`digest`（规范化指纹）、`workspace`（工作区解析）、
`registry`（书籍注册表）、`jsonio`（原子读写）。其余（身份、原文、引用、用量）
随 M3/M6 迁入，不在这里放空文件。
"""
from __future__ import annotations

from .digest import DigestError, canonical_json, digest_bytes, digest_file, digest_of, digest_text
from .jsonio import read_json, read_json_soft, write_json_atomic
from .identity import (Ambiguity, AmbiguityBlocked, Catalog, Lineage, TocNodeRef, UnitRef,
                     detect_ambiguities, make_node_key)
from .registry import BookContext, BookRegistry, BookSpec, SourceRef, load_registry
from .source import CanonicalSource, RepairRecord, SourceRepairPolicy
from .usage import BUCKETS, KIND_CACHE, KIND_CALL, KIND_RETRY, UsageLedger, UsageRecord, summarize
from .workspace import ENV_WORKSPACE, Workspace, find_workspace, open_workspace

__all__ = [
    "DigestError", "canonical_json", "digest_bytes", "digest_file", "digest_of", "digest_text",
    "read_json", "read_json_soft", "write_json_atomic",
    "BookContext", "BookRegistry", "BookSpec", "SourceRef", "load_registry",
    "Ambiguity", "AmbiguityBlocked", "Catalog", "Lineage", "TocNodeRef", "UnitRef",
    "detect_ambiguities", "make_node_key",
    "CanonicalSource", "RepairRecord", "SourceRepairPolicy",
    "BUCKETS", "KIND_CALL", "KIND_CACHE", "KIND_RETRY", "UsageLedger", "UsageRecord",
    "summarize",
    "ENV_WORKSPACE", "Workspace", "find_workspace", "open_workspace",
]
