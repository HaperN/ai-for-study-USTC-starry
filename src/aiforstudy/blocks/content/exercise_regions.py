"""Bind author-selected exercise ranges to immutable, lossless source text."""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy

from pydantic import ValidationError

from .content_contracts import ExerciseRegionV1, as_dict, content_digest
from ...core.source_boundaries import MATERIAL_HEADING, source_material_spans
from ...errors import ContractError


def numbered_source_lines(source_text):
    return [{'line': index, 'text': text}
            for index, text in enumerate(source_text.splitlines(keepends=True), 1)]


class IncompleteExerciseRegions(ContractError):
    """Existing regions passed strict checks, but clear source material is missing."""


def _explicit_materials(context):
    lines = context['source_text'].splitlines(keepends=True)
    offsets = [0]
    for line in lines: offsets.append(offsets[-1]+len(line))
    rows = []
    for begin, finish in source_material_spans(context['source_text']):
        start, end = offsets.index(begin), offsets.index(finish)
        headings = [i for i in range(start,end) if MATERIAL_HEADING.match(lines[i])]
        if not headings or headings[0] != start:
            raise ContractError('exercise material lacks an explicit source heading')
        for first, last in zip(headings, [*headings[1:],end]):
            title = re.sub(r'^\s*#{1,6}\s*', '', lines[first]).strip().strip('*').strip()
            kind = 'answers' if re.match(r'^(?:参考答案|习题答案|答案|Answers?)', title, re.I) else 'exercise'
            rows.append(dict(title=title, kind=kind, source_lines=[first+1,last]))
    return lines, rows


def bind_exercise_regions(regions, source, *, stored=False):
    """Validate locators and supply only host-owned identities and original bytes."""
    context = as_dict(source)
    lines = context['source_text'].splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    if not isinstance(regions, list):
        raise ContractError('exercise regions must be an array')
    bound, spans = [], []
    for raw in regions:
        try:
            row = ExerciseRegionV1.model_validate(raw).as_dict()
        except (ValidationError, TypeError) as exc:
            raise ContractError('exercise region schema is invalid') from exc
        start, end = row['source_lines']
        if not row['title'].strip() or not 1 <= start <= end <= len(lines):
            raise ContractError('exercise region source range or title is invalid')
        if any(start <= old_end and old_start <= end for old_start, old_end in spans):
            raise ContractError('exercise region source ranges overlap')
        spans.append((start, end))
        if any(start <= item['source_line'] <= end for item in context['source_examples']):
            raise ContractError('exercise region cannot hide a frozen source example')
        begin, finish = offsets[start - 1], offsets[end]
        for obligation in context['source_obligations']:
            for ref in obligation['source_refs']:
                first, last = ref.get('start'), ref.get('end')
                quote = ref.get('quote') or obligation['source_excerpt']
                if first is None or last is None:
                    first = context['source_text'].find(quote)
                    last = first + len(quote)
                if first >= begin and last <= finish and last > first:
                    raise ContractError('exercise region cannot hide a frozen teaching obligation')
        original = ''.join(lines[start - 1:end])
        if not original.strip():
            raise ContractError('exercise region cannot contain only blank source')
        digest = 'sha256:' + hashlib.sha256(original.encode('utf-8')).hexdigest()
        identity = 'ER-' + content_digest({'unit': context['unit_id'],
            'source': context['original_sha256'], 'lines': [start, end], 'raw': digest}).split(':')[-1][:24]
        host = {'id': identity, 'raw_markdown': original, 'source_sha256': digest}
        for key, value in host.items():
            if (stored or row[key]) and row[key] != value:
                raise ContractError('exercise region changed host-owned ' + key)
            row[key] = value
        bound.append(row)
    known = source_material_spans(context['source_text'])
    missing = [index for index, line in enumerate(lines, 1) if line.strip()
        and any(first <= offsets[index - 1] < last for first, last in known)
        and not any(first <= index <= last for first, last in spans)]
    if missing:
        raise IncompleteExerciseRegions('exercise region uncovered source lines ' + ', '.join(map(str, missing)) +
                            '; record the complete source material under its explicit heading, without rewriting or solving it')
    return sorted(bound, key=lambda row: row['source_lines'])


def is_exercise_pointer(pointer):
    return isinstance(pointer, str) and pointer.startswith('/exercise_regions/')
