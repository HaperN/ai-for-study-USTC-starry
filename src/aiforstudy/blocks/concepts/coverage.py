"""Shared concept-name coverage for extraction and frozen-index rechecks.

This deliberately differs from the index's identity normalizer: coverage may
ignore a type prefix and a parenthetical qualifier; identity cannot merge them.
"""
import re


def normalize_coverage_name(name: str) -> str:
    text = re.sub(r"[\s　]+", "", str(name or ""))
    text = re.sub(r"^(定义|定理|命题|引理|推论|公式|性质)[:：]?", "", text)
    text = re.sub(r"^\d+(?:\.\d+)*[:：.、]*", "", text)
    without_paren = re.sub(r"[（(].*?[)）]", "", text)
    if without_paren.strip():
        text = without_paren
    else:
        while len(text) >= 2 and text[0] in "（(" and text[-1] in "）)":
            text = text[1:-1]
    text = re.sub(r"[:：.。，,、；;'\"“”‘’]+$", "", text)
    # A numbered theorem must match itself, never normalize to an empty name.
    if not text:
        return re.sub(r"[\s　]+", "", str(name or "")).strip().lower()
    return text.strip().lower()


def covers_name(name: str, rows) -> bool:
    """A normalized name is covered by containment in either direction."""
    wanted = normalize_coverage_name(name)
    if not wanted:
        return False
    for row in rows:
        original = str((row or {}).get('name') or '')
        have = normalize_coverage_name(original)
        if not have:
            continue
        if wanted in have or have in wanted:
            return True
        core = re.sub(r"[\s（）()]", "", str(name))
        if core and core in re.sub(r"[\s（）()]", "", original):
            return True
    return False
