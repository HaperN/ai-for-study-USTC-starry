"""Read-only, directory-owned source materials; never extract or solve questions."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .digest import digest_bytes, digest_file
from .jsonio import write_json_atomic


INPUTS = ('document.md', 'toc.excluded.json', 'toc.v2.json', 'headings.json')
SCHEMA_VERSION = '1.0'
_PAGE = re.compile(r'^\[第\s*\d+\s*页\]$')


def _input_hashes(root: Path) -> dict:
    return {name: digest_file(root/name) if (root/name).is_file() else None for name in INPUTS}


def build_source_materials(parse_root: str | Path) -> dict:
    """Locate directory materials and copy exact original lines without writes.

    An unresolved heading keeps its directory identity and page hints. A located
    parent retains its entire source even when a descendant cannot be located.
    Old exercise indexes, corrected selections and extraction caches are ignored.
    """
    # This read-only entry is reached before preparation has loaded adapters.
    # Use the same controlled application/bundled-wheel loader as execution;
    # a caller's cwd or source-data workspace must not supply parser modules.
    from ..pipeline.stage_io import _legacy
    _line_pages = _legacy('blocks.parse.toc.align')._line_pages
    build_regions = _legacy('blocks.parse.toc.regions').build_regions

    root = Path(parse_root)
    raw = (root/'document.md').read_bytes().decode('utf-8')
    lines = raw.splitlines(keepends=True)
    pages = _line_pages(raw)
    regions = build_regions(root)
    material_ids = set(regions['material_entry_ids'])
    materials = []
    for entry in regions['directory_entries']:
        identity = entry['entry_id']
        if identity not in material_ids:
            continue
        resolved = regions['entry_ranges'].get(identity) or {}
        span = resolved.get('source_lines')
        text = ''.join(lines[span[0]-1:span[1]]) if span else ''
        source_pages = sorted({pages[n] for n in range(span[0], span[1]+1)
            if pages.get(n) and lines[n-1].strip() and not _PAGE.fullmatch(lines[n-1].strip())}) if span else []
        materials.append({
            'id': 'material:' + str(identity), 'entry_id': identity,
            'title': entry.get('title') or '', 'kind': entry.get('category'),
            'order': entry['order'], 'level': entry['level'], 'number': entry.get('number') or '',
            'parent_entry_id': entry.get('parent_entry_id'), 'owner_toc_id': entry.get('owner_toc_id'),
            'source_lines': span, 'source_pages': source_pages, 'raw_markdown': text,
            'state': 'recorded' if span else 'unresolved',
            'reason': ('仅记录目录资料原文，未拆题、解题或核实题目质量' if span else
                       resolved.get('reason') or '目录资料范围尚不能唯一定位'),
            'source_sha256': digest_bytes(text.encode('utf-8')) if span else None,
            'source_hint': {'document': 'document.md',
                **{key: entry.get(key) for key in ('pdf_page', 'printed_page', 'toc_page', 'source_ids')}},
        })
    return {'schema_version': SCHEMA_VERSION, 'source': 'document.md',
            'line_numbering': '1-based-inclusive', 'input_hashes': _input_hashes(root),
            'directory_entries': regions['directory_entries'], 'materials': materials}


def write_source_materials(parse_root: str | Path) -> dict:
    root = Path(parse_root)
    result = build_source_materials(root)
    write_json_atomic(root/'source_materials.json', result)
    return result


def is_current(parse_root: str | Path) -> bool:
    """Require both matching source inputs and an intact deterministic material list."""
    root = Path(parse_root)
    try:
        saved = json.loads((root/'source_materials.json').read_text(encoding='utf-8'))
        if (not isinstance(saved, dict) or saved.get('schema_version') != SCHEMA_VERSION
                or saved.get('input_hashes') != _input_hashes(root)):
            return False
        return saved == build_source_materials(root)
    except (OSError, ValueError, KeyError, TypeError):
        return False


__all__ = ['build_source_materials', 'write_source_materials', 'is_current']
