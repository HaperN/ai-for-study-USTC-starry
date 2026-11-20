"""Shared conservative material boundaries; source text is never rewritten here."""
from __future__ import annotations

import re


MATERIAL_HEADING = re.compile(
    r'^\s*(?:#{1,6}\s*|\*\*)?'
    r'(?:习题|练习题?(?:与思考)?|思考题|复习题|参考答案|习题答案|答案(?:与提示)?|Exercises?|Problems?|Answers?)'
    r'(?:\s*[\d一二三四五六七八九十][^\n]*|\s*[:：]?\s*(?:\*\*)?\s*)$', re.I)
TEACHING_BOUNDARY = re.compile(r'^\s*(?:#{1,6}\s*|\*\*)?(例|定义|定理|命题|推论|引理|注|习题|练习)\s*\d')


def source_material_spans(text: str) -> list[tuple[int, int]]:
    """Return half-open Unicode spans after clear exercise/answer headings.

    This bounds existing structural heuristics. It does not parse questions,
    assign directory ownership, or replace the author's source-region choices.
    A new numbered teaching label or Markdown section resumes teaching source.
    """
    spans=[]; start=None; offset=0
    for line in text.splitlines(keepends=True):
        if MATERIAL_HEADING.match(line):
            if start is None: start=offset
        elif start is not None and (TEACHING_BOUNDARY.match(line) or re.match(r'^\s*#{1,6}\s+\S',line)):
            spans.append((start,offset)); start=None
        offset+=len(line)
    if start is not None: spans.append((start,len(text)))
    return spans
