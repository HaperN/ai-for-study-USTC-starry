"""Focused lesson context for one media code request (prompt input only).

A media program needs the passage it illustrates, not the whole book. The full
approval used to carry the book-wide reference catalog (~550 entries) into every
request, so media generation spent most of its budget reading and thinking about
unrelated material. Validation still uses the full content and approval; this
module only decides what the model reads.

Confirmed rule (2026-10-16): related excerpts plus a one-line-per-block outline
of the current section.
"""
from __future__ import annotations

from copy import deepcopy
import re

OUTLINE_CHARS = 90
LINK = re.compile(r'\[\[([A-Z]+):([^|\]]+)(?:\|[^\]]*)?\]\]')
BLOCK_LISTS = ('examples', 'explainables', 'annotations', 'learning_checks')


def _one_line(text, limit=OUTLINE_CHARS):
    text = ' '.join(str(text or '').split())
    match = re.search(r'[。；！？.!?]', text)
    if match and match.end() <= limit:
        return text[:match.end()]
    return text if len(text) <= limit else text[:limit] + '…'


def _paragraphs(markdown):
    return [part for part in re.split(r'\n\s*\n', markdown or '') if part.strip()]


def _block_summary(item):
    label = item.get('label') or item.get('title') or item.get('name') or ''
    body = next((item[key] for key in ('statement', 'what', 'markdown', 'prompt', 'question', 'conclusion')
                 if isinstance(item.get(key), str) and item[key].strip()), '')
    return {'id': item.get('id', ''), 'label': label, 'summary': _one_line(body)}


def section_outline(content):
    """Headings, one lead line per body paragraph, and one line per teaching block."""
    body = []
    for paragraph in _paragraphs(content.get('body_markdown')):
        line = paragraph.strip()
        body.append(line if line.startswith('#') else _one_line(line.lstrip('> ')))
    blocks = {name: [_block_summary(item) for item in content.get(name) or [] if isinstance(item, dict)]
              for name in BLOCK_LISTS if content.get(name)}
    return {'title': content.get('title', ''), 'body': body, 'blocks': blocks}


def _pointer_parts(pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        return []
    return [part.replace('~1', '/').replace('~0', '~') for part in pointer[1:].split('/')]


def _top_item(content, pointer):
    """The whole top-level teaching item a pointer lands in (e.g. one example)."""
    parts = _pointer_parts(pointer)
    if len(parts) >= 2 and isinstance(content.get(parts[0]), list) and parts[1].isdigit():
        items = content[parts[0]]
        index = int(parts[1])
        if index < len(items):
            return '/' + parts[0] + '/' + parts[1], items[index]
    if parts and parts[0] in content and parts[0] != 'body_markdown':
        return '/' + parts[0], content[parts[0]]
    return None, None


def _body_excerpt(content, anchor):
    paragraphs = _paragraphs(content.get('body_markdown'))
    anchor = (anchor or '').strip()
    hits = [i for i, paragraph in enumerate(paragraphs) if anchor and anchor in paragraph]
    if not hits:
        # Unknown position: the body itself is small, so keep all of it rather
        # than risk omitting the passage the media illustrates.
        return '\n\n'.join(paragraphs)
    start, end = max(0, hits[0] - 1), min(len(paragraphs), hits[-1] + 2)
    return '\n\n'.join(paragraphs[start:end])


def related_excerpts(content, request):
    excerpts = []
    seen = set()

    def add(pointer, value):
        if pointer and pointer not in seen and value not in (None, '', [], {}):
            seen.add(pointer)
            excerpts.append({'pointer': pointer, 'content': deepcopy(value)})

    placement = request.get('placement') or {}
    target = placement.get('target_pointer') or '/body_markdown'
    if target == '/body_markdown' or not _top_item(content, target)[0]:
        add('/body_markdown', _body_excerpt(content, placement.get('anchor_text')))
    else:
        add(*_top_item(content, target))
    for pointer in request.get('dependency_paths') or []:
        add(*_top_item(content, pointer))
    # Same-unit blocks the excerpt explicitly links to (e.g. [[EXPLAIN:ID|…]]).
    linked = {ident for text in (str(e['content']) for e in excerpts)
              for kind, ident in LINK.findall(text) if kind != 'CONCEPT'}
    for name in BLOCK_LISTS:
        for index, item in enumerate(content.get(name) or []):
            if isinstance(item, dict) and item.get('id') in linked:
                add(f'/{name}/{index}', item)
    return excerpts


def referenced_catalog_entries(excerpts, approval):
    """Only catalog entries the excerpts link to; never the book-wide catalog."""
    catalog = (approval or {}).get('reference_catalog') or {}
    wanted = {ident for text in (str(e['content']) for e in excerpts) for _, ident in LINK.findall(text)}
    return [deepcopy(entry) for entry in catalog.get('entries') or []
            if isinstance(entry, dict) and entry.get('id') in wanted]


def approval_binding(approval):
    """Identity hashes only; the full catalog stays with the host for validation."""
    return {key: deepcopy(value) for key, value in (approval or {}).items() if key != 'reference_catalog'}


def focused_media_context(content, approval, request):
    excerpts = related_excerpts(content, request)
    return {'section_outline': section_outline(content),
            'related_excerpts': excerpts,
            'referenced_entries': referenced_catalog_entries(excerpts, approval),
            'approval_binding': approval_binding(approval)}
