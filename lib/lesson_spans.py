"""**定位**：在产物文本里找一句「原句」的唯一落点（容差版）。

为什么单独抽出来（2026-09-26）：这东西原来住在 `blocks/lesson/generate.py` 里，
但**审计块也要用**（建工单时先把引文对齐到产物真实文字，见 `feedback.align_findings`）——
审计块不能反向依赖教案块，所以抽到 `lib/` 里，两边共用同一份判据。

两条判据：① 逐字唯一；② 归一化（抹掉空白 + 统一标点）后唯一。
两条都要求**唯一** —— 出现两次宁可不改，这是「没被点到的段落物理上改不坏」的支点。
"""
from __future__ import annotations

import re

_PUNCT_MAP = str.maketrans({"，": ",", "。": ".", "；": ";", "：": ":", "（": "(", "）": ")",
                            "、": ",", "！": "!", "？": "?", "％": "%", "－": "-", "—": "-",
                            "“": '"', "”": '"', "‘": "'", "’": "'", "　": " "})


def normalize_for_match(text: str) -> tuple[str, list[int]]:
    """返回 (归一化文本, 每个字符对应的原文下标)：空白全丢、标点统一。"""
    chars, index = [], []
    for i, ch in enumerate(str(text or "")):
        for piece in ch.translate(_PUNCT_MAP):
            if piece.isspace():
                continue
            chars.append(piece)
            index.append(i)
    return "".join(chars), index


def find_span(text: str, old: str):
    """在 `text` 里找 `old` 的**唯一**落点；返回 (start, end) 或"为什么没找到"的说明。

    两条判据：① 逐字唯一（原行为）；② **归一化后唯一**（容差：空白与标点差异）。
    两条都要求"唯一"—— 出现两次宁可不改，这正是"物理上改不坏"的那条护栏。
    """
    body = str(text or "")
    if body.count(old) == 1:
        start = body.index(old)
        return start, start + len(old)
    norm_body, index = normalize_for_match(body)
    norm_old, _ = normalize_for_match(old)
    if not norm_old:
        return "old 全是空白"
    hits, from_pos = [], 0
    while True:
        found = norm_body.find(norm_old, from_pos)
        if found < 0:
            break
        hits.append(found)
        from_pos = found + 1
    if not hits:
        return "没找到"
    if len(hits) > 1:
        return f"出现 {len(hits)} 次"
    start = index[hits[0]]
    end = index[hits[0] + len(norm_old) - 1] + 1
    return start, end




__all__ = ["find_span", "normalize_for_match"]
