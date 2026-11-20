"""用户 2026-10-20 定：概念 ID 重复时先审核，再决定是否合并（不再让整个概念阶段失败）。

两条不同身份键（名+类型+归属章）的条目拿到同一个 id，通常是注册表冲突。
对每组重复，请审核模型判断是否同一数学对象：
  * 同一对象 → 合并为一条（保留第一条的身份，出现位置取并集）；
  * 不是同一对象，或审核调用失败 → 分开，后面的条目换新 id，记一条警告。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import json
import re

REVIEW_PROMPT = ('你在核对一本数学教材的概念索引。下面几条概念条目被分到了同一个 id。'
                 '判断它们是否是同一个数学对象（同一定义、定理或公式，只是名称写法或出现位置不同）。'
                 '只返回 JSON：{"same": true 或 false, "reason": "一句话依据"}。'
                 '不确定时返回 false（宁可分开，不误合并）。')


def _number(cid):
    match = re.search(r'(\d+)$', cid or '')
    return int(match.group(1)) if match else 0


def _summary(entry):
    return {'name': entry.name, 'type': entry.type, 'chapter': entry.chapter,
            'ownership_unit': entry.ownership_unit, 'formal': entry.formal[:600],
            'source_quotes': list(entry.source_quotes[:2])}


def resolve_duplicate_ids(index, judge=None):
    """Return a list of decision records; ``index.concepts`` is rewritten in place."""
    groups = defaultdict(list)
    for entry in index.concepts:
        groups[entry.concept_id].append(entry)
    duplicates = {cid: rows for cid, rows in groups.items() if len(rows) > 1}
    if not duplicates:
        return []
    width = max((len(re.search(r'(\d+)$', c.concept_id).group(1)) for c in index.concepts
                 if re.search(r'(\d+)$', c.concept_id)), default=4)
    prefix = re.sub(r'\d+$', '', sorted(duplicates)[0]) or 'C-'
    next_number = max((_number(c.concept_id) for c in index.concepts), default=0) + 1
    keep, records = [], []
    for entry in index.concepts:
        if entry.concept_id not in duplicates:
            keep.append(entry)
    for cid in sorted(duplicates):
        rows = duplicates[cid]
        decision = {'concept_id': cid, 'entries': [_summary(e) for e in rows]}
        same, reason = False, '未配置审核模型，按不同概念分开'
        if judge is not None:
            try:
                answer = judge.chat_json([{'role': 'system', 'content': REVIEW_PROMPT},
                                          {'role': 'user', 'content': json.dumps(decision['entries'], ensure_ascii=False)}],
                                         tag='concept-duplicate:' + cid)
                data = answer.get('data') if isinstance(answer, dict) else None
                if isinstance(data, dict) and isinstance(data.get('same'), bool):
                    same, reason = data['same'], str(data.get('reason') or '')
                else:
                    reason = '审核答复无法读取，按不同概念分开'
            except Exception as exc:                                  # noqa: BLE001
                reason = f'审核调用失败（{type(exc).__name__}），按不同概念分开'
        first = rows[0]
        if same:
            occurrences, seen = [], set()
            for row in rows:
                for occ in row.occurrences:
                    token = (occ.unit_key, occ.anchor) if hasattr(occ, 'anchor') else repr(occ)
                    if token not in seen:
                        seen.add(token)
                        occurrences.append(occ)
            keep.append(replace(first, occurrences=tuple(occurrences)))
            decision.update(decision='merged', reason=reason, kept=first.name)
        else:
            keep.append(first)
            renamed = []
            for row in rows[1:]:
                new_id = f'{prefix}{next_number:0{width}d}'
                next_number += 1
                keep.append(replace(row, concept_id=new_id))
                renamed.append({'name': row.name, 'chapter': row.chapter, 'new_id': new_id})
            decision.update(decision='separated', reason=reason, renamed=renamed)
        records.append(decision)
    index.concepts = tuple(sorted(keep, key=lambda c: c.concept_id))
    return records


__all__ = ['resolve_duplicate_ids', 'REVIEW_PROMPT']
