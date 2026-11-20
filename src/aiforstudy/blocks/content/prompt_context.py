"""A compact model view of frozen sources; never changes the stored source record."""
from __future__ import annotations

from copy import deepcopy
import json
import re


CONTEXT_NOTES = (
    '输入资料按需展开，冻结来源与身份未改。source_text 是完整本节原文；'
    '某个资料字段若为 {"$text_ref":路径,"start":起点,"end":终点}，'
    '表示引用本次 user JSON 中该路径字符串的指定片段，字符从0起、左闭右开；'
    '没有 start/end 则引用完整字符串。这是完整原文的无损定位，不是省略或缺失证据。'
    '若 source_text 为 {"$source_lines":"/source_lines","join":""}，完整原文就是'
    'source_lines 各行 text 按顺序直接连接；该表直接展示从1起的原行号与完整原文，保留原换行。'
    '标注习题区时直接使用该表的 line，不需要按字符数推算行号。'
    'concept_index 与 reference_catalog 保留全书全部真实身份、名称、归属与版本；'
    '详细资料仅附本节和实际相关条目，不把未展开的目录条目当作已阅读的讲解。'
    '前文定理只提供名称提示，不要求补交前文证明；当前正文条件和必要推导照常检查。'
    '这些输入定位对象不是输出字段；不要把 $text_ref 写进正文或教学输出。'
)

_CONCEPT_IDENTITY = frozenset(('id', 'name', 'kind', 'type', 'first_unit', 'unit_id',
                              'role_here', 'version', 'aliases'))
_REFERENCE_IDENTITY = frozenset(('id', 'kind', 'label', 'version', 'unit_id'))
_TEXT_FIELDS = frozenset(('formal', 'definition', 'markdown', 'source_excerpt', 'quote',
                         'conclusion', 'statement', 'book_solution'))
_THEOREM_KINDS = frozenset(('theorem', 'lemma', 'corollary', 'proposition', '定理', '命题', '引理', '推论'))


def _pointer(parts):
    return '/' + '/'.join(str(p).replace('~', '~0').replace('/', '~1') for p in parts)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def compact_content_context(context):
    """Keep the candidate intact, retaining only needed detail from source indexes.

    References replace exact text only when shorter. Source offsets, IDs and
    original source hashes stay unchanged. Unknown fields on relevant records
    remain visible; the immutable SourceContext itself is never transformed.
    """
    result = deepcopy(context)
    source = result.get('source_context', result)
    if not isinstance(source, dict) or not isinstance(source.get('source_text'), str):
        return result, False
    original = deepcopy(source)
    prefix = ['frozen_context', 'source_context'] if 'source_context' in result else ['frozen_context']
    source_pointer = _pointer([*prefix, 'source_text'])
    source_text = source['source_text']
    unit_id = source.get('unit_id')
    concepts = (source.get('concept_index') or {}).get('concepts', [])
    entries = (source.get('reference_catalog') or {}).get('entries', [])

    # Matching the whole catalog against itself would make every item relevant.
    focus = {k: v for k, v in source.items()
             if k not in ('concept_index', 'reference_catalog', 'chapters')}
    if source is not result:
        focus['current_request'] = {k: v for k, v in result.items()
                                    if k not in ('source_context', 'available_theorem_explanations')}
    focused_text = '\n'.join(_strings(focus))
    names = {}
    for row in concepts:
        if isinstance(row, dict) and row.get('name'):
            names.setdefault(row['name'], set()).add(row.get('id'))
    relevant = set()
    prior_theorems = set()
    for row in concepts:
        if not isinstance(row, dict):
            continue
        cid, name = str(row.get('id') or ''), row.get('name') or ''
        belongs_here = (row.get('first_unit') or row.get('unit_id')) == unit_id
        if not belongs_here and (row.get('kind') or row.get('type')) in _THEOREM_KINDS:
            prior_theorems.add(cid)
        explicit_id = bool(cid and re.search(r'(?<![\w-])' + re.escape(cid) + r'(?![\w-])', focused_text))
        # A repeated source number/name cannot establish a cross-chapter identity.
        named = (len(name) >= 2 and len(names.get(name, ())) == 1 and name in focused_text
                 and not re.fullmatch(r'(?:定理|性质|命题|推论|引理|例)\s*[\d一二三四五六七八九十.．-]+', name))
        if belongs_here or explicit_id or named:
            relevant.add(cid)
    # Catalog-only identities are still resolvable when explicitly used.
    for row in entries:
        if not isinstance(row, dict):
            continue
        cid = str(row.get('id') or '')
        if row.get('unit_id') == unit_id or (cid and re.search(
                r'(?<![\w-])' + re.escape(cid) + r'(?![\w-])', focused_text)):
            relevant.add(cid)
    detailed = relevant - prior_theorems
    if isinstance((source.get('concept_index') or {}).get('concepts'), list):
        source['concept_index']['concepts'] = [
            row if not isinstance(row, dict) or row.get('id') in detailed else
            {key: value for key, value in row.items() if key in _CONCEPT_IDENTITY or not value}
            for row in concepts]
    if isinstance((source.get('reference_catalog') or {}).get('entries'), list):
        source['reference_catalog']['entries'] = [
            row if not isinstance(row, dict) or row.get('id') in detailed else
            {key: value for key, value in row.items() if key in _REFERENCE_IDENTITY or not value}
            for row in entries]

    canonical = {source_text: source_pointer} if source_text else {}

    def compact(value, path, field=''):
        if isinstance(value, dict):
            return {key: compact(item, [*path, key], key) for key, item in value.items()}
        if isinstance(value, list):
            return [compact(item, [*path, index], field) for index, item in enumerate(value)]
        if field not in _TEXT_FIELDS or not isinstance(value, str) or not value:
            return value
        reference = None
        if value in canonical:
            reference = {'$text_ref': canonical[value]}
        elif value in source_text:
            start = source_text.index(value)
            reference = {'$text_ref': source_pointer, 'start': start, 'end': start + len(value)}
        # Tiny literal definitions cost less and are easier to read than pointers.
        if reference is not None and len(json.dumps(reference, ensure_ascii=False)) < len(json.dumps(value, ensure_ascii=False)):
            return reference
        canonical.setdefault(value, _pointer(path))
        return value

    # Only source material is compacted. In a direct author envelope, feedback
    # can contain a previous response and must remain exactly as received.
    compacted = {key: compact(value, [*prefix, key], key)
                 if key in ('concept_index', 'reference_catalog', 'source_examples', 'source_obligations')
                 else value for key, value in source.items()}
    if source is result:
        # Author context may also contain prompt-only names and rejection input.
        result = compacted
    else:
        result['source_context'] = compacted
    return result, compacted != original
