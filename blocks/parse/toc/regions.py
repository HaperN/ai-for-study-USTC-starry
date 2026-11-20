"""Locate independent directory materials without removing inline exercises.

Only directory-owned standalone exercise/answer chapters leave the teaching
view. Their immutable source remains available as materials. PDF TOC screenshot
pages are never interpreted as body pages.
"""
from __future__ import annotations

import hashlib
import bisect
import json
import re
import unicodedata
from pathlib import Path

from .sources import canonical_number, parse_title


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _key(text: str) -> str:
    # Keep mathematical operators and digits; only remove formatting whitespace.
    normalized = unicodedata.normalize('NFC', text)
    normalized = ''.join(chr(ord(ch) - 0xFEE0) if 0xFF01 <= ord(ch) <= 0xFF5E
                         else ch for ch in normalized)
    return re.sub(r'\s+', '', normalized)


def _title_matches(entry: dict, text: str) -> bool:
    number, title, _ = parse_title(text)
    wanted_number = canonical_number(str(entry.get('number') or ''))
    actual_number = canonical_number(number)
    if wanted_number and actual_number and wanted_number != actual_number:
        return False
    _, wanted_title, _ = parse_title(str(entry.get('title') or ''))
    return bool(_key(wanted_title)) and _key(wanted_title) == _key(title)


def span_at(report: dict, line: int) -> dict | None:
    matches = [s for s in report.get('spans', [])
               if s['source_lines'][0] <= line <= s['source_lines'][1]]
    return max(matches, key=lambda s: int(s.get('level') or 0), default=None)


def teaching_at(report: dict, line: int) -> bool:
    return not span_at(report, line) and any(
        s['source_lines'][0] <= line <= s['source_lines'][1]
        for s in report.get('teaching_spans', []))


def require_resolved(report: dict) -> bool:
    """Report completeness without suppressing already located source regions.

    Unlocated exclusions remain in the report and source material list; one
    missing material heading must not prevent unrelated teaching units.
    Invalid schemas/coordinates still raise at their validation boundary.
    """
    return not bool(report.get('unresolved'))


def require_teaching_boundaries(report: dict) -> None:
    """Do not let an unlocated independent material flow into a teaching cut.

    Directory review and source preservation remain readable when unresolved.
    Structure currently runs for the whole book, so its caller must stop that
    stage if a top-level material has no complete range. A missing child anchor
    inside a located parent does not affect this boundary.
    """
    failures = []
    for entry in report.get('directory_entries', []):
        if int(entry['level']) != 1 or entry.get('category') not in ('exercise', 'answers'):
            continue
        located = report.get('entry_ranges', {}).get(entry['entry_id']) or {}
        if not located.get('source_lines'):
            failures.append(f"{entry['entry_id']} {entry.get('title') or ''}"
                            f"（{located.get('reason') or '缺少完整来源范围'}）")
    if failures:
        raise ValueError('独立资料来源定位失败，不能生成教学正文：' + '；'.join(failures)
                         + '。原文与目录资料记录保留；需明确起止锚点后重建结构。')


def build_regions(parse_dir: str | Path, toc: dict | None = None, *,
                  teaching_matches: list[dict] | None = None) -> dict:
    root = Path(parse_dir)
    report = {'schema_version': '1.0', 'source': 'document.md',
              'line_numbering': '1-based-inclusive', 'enabled': False,
              'spans': [], 'teaching_spans': [], 'unresolved': [], 'warnings': [],
              'directory_entries': [], 'entry_ranges': {}, 'material_entry_ids': []}
    path = root / 'toc.excluded.json'
    if not path.is_file():
        return report
    report['enabled'] = True
    payload = _read(path)
    if payload.get('schema_version') != '1.0':
        raise ValueError('toc.excluded.json schema_version 必须是 1.0')
    exclusions = payload.get('entries') or []
    all_entries = payload.get('all_entries') or []
    if exclusions and not all_entries:
        raise ValueError('目录排除项缺 all_entries 全目录边界顺序')
    doc = root / 'document.md'
    lines = doc.read_text(encoding='utf-8').splitlines()
    report['document_sha256'] = hashlib.sha256(doc.read_bytes()).hexdigest()
    report['excluded_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    ids = [str(e.get('entry_id') or '') for e in all_entries]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError('目录排除全序 entry_id 缺失或重复')
    excluded_ids = {str(e.get('entry_id') or '') for e in exclusions}
    if not excluded_ids <= set(ids):
        raise ValueError('目录排除条目不在 all_entries')
    ordered = sorted(all_entries, key=lambda e: int(e.get('order') or 0))
    orders = [int(e.get('order') or 0) for e in ordered]
    if min(orders, default=1) < 1 or len(orders) != len(set(orders)):
        raise ValueError('目录排除全序 order 缺失或重复')
    # Ownership comes from the actual directory ancestry, never the preceding
    # teaching chapter. Keep the original full sequence for reader placement.
    ancestors = []
    material_ids = set()
    for raw in ordered:
        e = dict(raw)
        while ancestors and int(ancestors[-1]['level']) >= int(e['level']):
            ancestors.pop()
        e.setdefault('parent_entry_id', ancestors[-1]['entry_id'] if ancestors else None)
        e.setdefault('owner_toc_id', e.get('toc_id') or next(
            (a.get('toc_id') for a in reversed(ancestors) if a.get('toc_id')), None))
        if ((int(e['level']) == 1 and e.get('category') in ('exercise', 'answers'))
                or any(a['entry_id'] in material_ids for a in ancestors)):
            material_ids.add(e['entry_id'])
        report['directory_entries'].append(e)
        ancestors.append(e)
    ordered = report['directory_entries']
    report['material_entry_ids'] = [e['entry_id'] for e in ordered if e['entry_id'] in material_ids]
    if not ordered:
        return report
    toc = toc if toc is not None else _read(root / 'toc.v2.json')
    from . import align
    if teaching_matches is None:
        teaching_matches = align.align(toc, root, resolve_regions=False)['matches']
    ordered_teaching = sorted(teaching_matches, key=lambda m: int(m['line']))
    directory_pages = {int(e['toc_page']) for e in all_entries
                       if isinstance(e.get('toc_page'), int) and e['toc_page'] > 0}
    for index, match in enumerate(ordered_teaching):
        start = int(match['line'])
        end = int(ordered_teaching[index + 1]['line']) - 1 if index + 1 < len(ordered_teaching) else len(lines)
        report['teaching_spans'].append({'toc_id': match['toc_id'], 'source_lines': [start, end]})
    teaching_lines = {m['toc_id']: int(m['line']) for m in teaching_matches}
    candidates = [c for c in align.load_candidates(root) if c.get('line')]
    # A title omitted by MinerU can still be an exact line match. No page-wide
    # deletion or approximate heading is invented to make an exclusion pass.
    pages = align._line_pages('\n'.join(lines))
    heading_payload = _read(root / 'headings.json') if (root / 'headings.json').is_file() else {}
    region_marks = sorted((int(h['line']), str(h.get('region') or 'body'))
                          for h in heading_payload.get('headings', []) if h.get('line'))
    mark_lines = [m[0] for m in region_marks]
    def source_region(n):
        before = bisect.bisect_right(mark_lines, n) - 1
        return (region_marks[before][1] if before >= 0 else
                (heading_payload.get('page_region') or {}).get(str(pages.get(n)), ''))
    candidates += [{'line': n, 'text': text, 'page': pages.get(n), 'region': source_region(n)}
                   for n, text in enumerate(lines, 1)
                   if len(text.strip()) <= 180]
    located: dict[str, int] = {}
    for e in ordered:
        if e.get('category') == 'teaching' and e.get('toc_id') in teaching_lines:
            located[e['entry_id']] = teaching_lines[e['toc_id']]
    for e in ordered:
        if e['entry_id'] not in excluded_ids:
            continue
        lo = max((located[x['entry_id']] for x in ordered
                  if int(x['order']) < int(e['order']) and x['entry_id'] in located), default=0)
        hi = min((located[x['entry_id']] for x in ordered
                  if int(x['order']) > int(e['order']) and x['entry_id'] in located),
                 default=len(lines) + 1)
        possible = {int(c['line']) for c in candidates
                    if lo < int(c['line']) < hi and c.get('region') != 'toc'
                    and pages.get(int(c['line'])) not in directory_pages
                    and _title_matches(e, str(c['text']))}
        body_page = e.get('pdf_page')
        if isinstance(body_page, int) and not isinstance(body_page, bool) and body_page > 0:
            possible = {n for n in possible if pages.get(n) == body_page}
        if len(possible) == 1:
            located[e['entry_id']] = possible.pop()
    report['excluded_heading_lines'] = sorted(located[eid] for eid in excluded_ids if eid in located)
    for index, e in enumerate(ordered):
        following = next((x for x in ordered[index + 1:]
                          if int(x['level']) <= int(e['level'])), None)
        start = located.get(e['entry_id'])
        end = located.get(following['entry_id']) if following else len(lines) + 1
        failure = ('目录标题在正文没有唯一锚点' if start is None else
                   '范围的下一个同级/更高级目录条目没有正文锚点' if end is None else
                   '范围起止与目录顺序冲突' if end <= start else '')
        report['entry_ranges'][e['entry_id']] = {
            'source_lines': None if failure else [start, end - 1], 'reason': failure}
    ancestors = []
    for index, e in enumerate(ordered):
        while ancestors and int(ancestors[-1]['level']) >= int(e['level']):
            ancestors.pop()
        parent_ids = {a['entry_id'] for a in ancestors}
        ancestors.append(e)
        if e['entry_id'] not in excluded_ids:
            continue
        if e.get('category') in ('exercise', 'answers') and e['entry_id'] not in material_ids:
            continue  # Embedded exercises remain in their teaching source.
        covered_parent = next((span for span in report['spans']
                               if span['entry_id'] in parent_ids
                               and span['category'] == e.get('category')), None)
        if covered_parent:
            covered_parent.setdefault('covered_entry_ids', []).append(e['entry_id'])
            continue
        failure = None
        start = located.get(e['entry_id'])
        following = next((x for x in ordered[index + 1:]
                          if int(x['level']) <= int(e['level'])), None)
        end_anchor = located.get(following['entry_id']) if following else len(lines) + 1
        if start is None:
            failure = '排除标题在正文没有唯一锚点'
        elif end_anchor is None:
            failure = '排除范围的下一个同级/更高级目录条目没有正文锚点'
        elif end_anchor <= start:
            failure = '排除范围起止与目录顺序冲突'
        if failure:
            row = {'entry_id': e['entry_id'], 'title': e.get('title'),
                   'category': e.get('category'), 'reason': failure}
            target = 'unresolved' if e.get('category') in ('exercise', 'answers') else 'warnings'
            report[target].append(row)
            continue
        region = {'exercise': 'exercise', 'answers': 'answer',
                  'frontmatter': 'front', 'reference': 'back'}.get(e.get('category'))
        if not region:
            raise ValueError(f'未知目录排除类别：{e.get("category")!r}')
        report['spans'].append({**e, 'region': region,
                                'source_lines': [start, end_anchor - 1],
                                'end_before_entry_id': following['entry_id'] if following else None})
    # Parent exercise chapters already cover their pure-exercise descendants.
    # Keep one ownership range and all source IDs rather than duplicate text.
    merged = []
    for span in sorted(report['spans'], key=lambda s: (s['source_lines'][0], -s['source_lines'][1])):
        parent = next((s for s in merged if s['source_lines'][0] <= span['source_lines'][0]
                       and s['source_lines'][1] >= span['source_lines'][1]
                       and s['region'] == span['region']), None)
        if parent:
            parent.setdefault('covered_entry_ids', []).append(span['entry_id'])
        else:
            merged.append(span)
    report['spans'] = merged
    report['spans'].sort(key=lambda s: (s['source_lines'][0], -s['source_lines'][1]))
    return report


def write_regions(parse_dir: str | Path, toc: dict | None = None) -> dict:
    report = build_regions(parse_dir, toc)
    path = Path(parse_dir) / 'toc.regions.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report
