"""结构层：PDF 抽出的纯文本 → 规范标题 markdown + 结构清单。"""
from .structure import (
    build_from_file, build_structure, write_outputs, render, render_with_anchors,
    StructureResult, Heading, Line, cn_to_int, norm_title, looks_like_exercise,
    DEFAULT_MAX_LEVEL, ANCHOR_MAX_LEVEL, SCHEMA_VERSION,
)

__all__ = [
    "build_from_file", "build_structure", "write_outputs", "render",
    "render_with_anchors", "StructureResult", "Heading", "Line", "cn_to_int",
    "norm_title", "looks_like_exercise",
    "DEFAULT_MAX_LEVEL", "ANCHOR_MAX_LEVEL", "SCHEMA_VERSION",
]
