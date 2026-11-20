"""Project PDF-verified textbook errata without rewriting frozen source snapshots.

The caller retains the original text/document/concepts and uses only returned
copies for display. Canonical definitions and teaching references use the actual
printed alpha; diagnostic quotations of the old OCR deliberately keep its a.
Exact target identity and text signatures are mandatory. A nonempty
``signature_mismatches`` report means the projection was declined atomically.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

from tools.prepare_support_corrections import _digest, _references, _reproject_block

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / 'data/study-revision-20261029/wholebook/textbook-display-corrections.json'
POLICY = 'PDF-VERIFIED-TEXTBOOK-DISPLAY-V2'
# This is the exact global formal in parse/concepts.json, rather than the
# longer source quotation stored in the reference catalog. Both come from the
# same p33 passage verified in the rule file's PDF evidence.
GLOBAL_C0124_FORMAL = (
    r'并称 a = argb - arga 为 z 沿曲线 l 的辐角变化，记为 $\Delta_{z}$ argb。'
    r'显然， $\Delta_{z}$ argz 与 arga 的选定值无关。'
)


@lru_cache(maxsize=1)
def _rules():
    value = json.loads(RULES_PATH.read_text(encoding='utf-8'))
    if value.get('schema_version') != 'TextbookDisplayCorrectionsV2':
        raise ValueError('Unsupported textbook display correction schema')
    return value


def _report(kind, value, unit_id=None):
    report = {'schema_version': 'TextbookDisplayProjectionReportV1',
              'correction_policy': POLICY, 'projection_kind': kind,
              'status': 'not_applicable', 'input_sha256': _digest(value),
              'changes': [], 'signature_mismatches': []}
    if unit_id is not None:
        report['unit_id'] = unit_id
    return report


def _mismatch(report, pointer, reason, actual=None):
    row = {'pointer': pointer, 'reason': reason}
    if actual is not None:
        row['actual_sha256'] = _digest(actual)
    report['signature_mismatches'].append(row)


def _finish(original, projected, report, already=False):
    if report['signature_mismatches']:
        # Do not return a mixture of accepted and unverified source versions.
        projected = deepcopy(original)
        report['changes'] = []
        report['status'] = 'signature_mismatch'
    elif report['changes']:
        report['status'] = 'corrected'
    elif already:
        report['status'] = 'already_corrected'
    report['display_sha256'] = _digest(projected)
    report['input_unchanged'] = _digest(original) == report['input_sha256']
    assert report['input_unchanged']
    return projected, report


def project_textbook_display(raw_text: str, unit_id: str) -> tuple[str, dict]:
    """Return ``(display_text, report)`` after canonically unique replacements.

    Missing or duplicated exact source spans are signature mismatches. Already
    projected text is accepted unchanged, making the API safe to compose.
    """
    if not isinstance(raw_text, str):
        raise TypeError('textbook source must be a string')
    report = _report('textbook', raw_text, unit_id)
    projected, already = raw_text, False
    for index, rule in enumerate(_rules()['units'].get(unit_id, [])):
        old_count, new_count = projected.count(rule['old']), projected.count(rule['new'])
        pointer = f'/units/{unit_id}/{index}'
        if old_count == 1 and new_count == 0:
            projected = projected.replace(rule['old'], rule['new'], 1)
            report['changes'].append({'pointer': pointer, 'before': rule['old'],
                                      'after': rule['new'], 'reason': rule['reason'],
                                      'source_note': rule.get('source_note')})
        elif old_count == 0 and new_count == 1:
            already = True
        else:
            _mismatch(report, pointer,
                      f'Expected one exact old span or one exact projected span; old={old_count}, new={new_count}')
    return _finish(raw_text, projected, report, already)


def _field_state(value, rule, report, pointer):
    if value == rule['old']:
        return 'old'
    if value == rule['new']:
        return 'new'
    _mismatch(report, pointer, 'Exact display text signature differs', value)
    return 'mismatch'


def correct_display_document(document: dict, unit_id: str) -> tuple[dict, dict]:
    """Return a deep-copied display ReaderDocumentV1 plus its audit report.

    Catalog rules match a known concept identity. Reader blocks additionally
    match owner/pointer and, where provided, their logical block identity. The
    existing reader projection helper rebuilds all text-derived source offsets,
    while block IDs, anchors, owners, pointers and required IDs remain unchanged.
    """
    if not isinstance(document, dict) or not isinstance(document.get('blocks'), list):
        raise ValueError('Reader document with blocks is required')
    if document.get('unit_id') not in (None, unit_id):
        raise ValueError('Reader document unit identity mismatch: ' + unit_id)
    report = _report('reader_document', document, unit_id)
    projected, already, operations = deepcopy(document), False, []
    entries = projected.get('reference_catalog', {}).get('entries', [])
    catalog_rules = _rules().get('reference_catalog_replacements', [])
    applicable_catalogs = {}
    for rule in catalog_rules:
        matching = [(i, e) for i, e in enumerate(entries) if e.get('id') == rule['owner_id']]
        if len(matching) > 1:
            _mismatch(report, '/reference_catalog', 'Ambiguous concept identity: ' + rule['owner_id'])
            continue
        if not matching:
            continue
        index, entry = matching[0]
        pointer = f'/reference_catalog/entries/{index}/markdown'
        if entry.get('kind') != 'CONCEPT' or entry.get('unit_id') != rule['unit_id']:
            _mismatch(report, pointer, 'Catalog kind/origin identity differs', entry)
            continue
        state = _field_state(entry.get('markdown'), rule, report, pointer)
        already |= state == 'new'
        applicable_catalogs[(rule['owner_id'], pointer)] = rule
        if state == 'old':
            operations.append(('catalog_entry', entry, rule, pointer))

    explicit = [r for r in _rules().get('support_replacements', []) if r['unit_id'] == unit_id]
    handled = set()
    for rule in explicit:
        pointer = rule['json_pointer']
        matching = [b for b in projected['blocks'] if b.get('owner_id') == rule['owner_id']
                    and b.get('json_pointer') == pointer]
        if len(matching) != 1:
            _mismatch(report, pointer, f'Expected one exact owner/pointer block; found {len(matching)}')
            continue
        block = matching[0]
        handled.add((block.get('owner_id'), pointer))
        if rule.get('block_id') and block.get('block_id') != rule['block_id']:
            _mismatch(report, pointer, 'Logical block identity differs', block.get('block_id'))
            continue
        state = _field_state(block.get('raw_text', block.get('markdown')), rule, report, pointer)
        # These registered fields have no reference markers, so their approved
        # markdown is exactly the admitted raw display text. Do not overwrite an
        # independently edited markdown projection merely because raw matches.
        expected = rule['new'] if state == 'new' else rule['old']
        if state != 'mismatch' and block.get('markdown') != expected:
            _mismatch(report, pointer, 'Raw/markdown projection signature differs', block.get('markdown'))
        already |= state == 'new'
        if state == 'old':
            operations.append(('reader_block', block, rule, pointer))

    # A matching catalog definition may also be mounted as a reference block in
    # another unit. Its exact catalog pointer, owner and source signature still
    # apply; diagnostic /corrections/... fields are never in this branch.
    for block in projected['blocks']:
        key = (block.get('owner_id'), block.get('json_pointer'))
        if key in handled or key not in applicable_catalogs:
            continue
        rule = applicable_catalogs[key]
        if block.get('group') != 'references':
            _mismatch(report, key[1], 'Catalog mirror is not a reference block')
            continue
        state = _field_state(block.get('raw_text', block.get('markdown')), rule, report, key[1])
        expected = rule['new'] if state == 'new' else rule['old']
        if state != 'mismatch' and block.get('markdown') != expected:
            _mismatch(report, key[1], 'Raw/markdown projection signature differs', block.get('markdown'))
        already |= state == 'new'
        if state == 'old':
            operations.append(('reader_block', block, rule, key[1]))

    if report['signature_mismatches']:
        projected, report = _finish(document, projected, report, already)
    else:
        for kind, item, rule, pointer in operations:
            if kind == 'catalog_entry':
                previous_version = item.get('version')
                item['markdown'] = rule['new']
                item['version'] = _digest({'policy': POLICY, 'source_version': previous_version,
                                           'id': item['id'], 'markdown': rule['new']})
        refs = _references(projected)
        for kind, item, rule, pointer in operations:
            if kind == 'reader_block':
                _reproject_block(item, rule['new'], refs)
            report['changes'].append({'kind': kind, 'pointer': pointer,
                                      'owner_id': rule['owner_id'], 'before': rule['old'],
                                      'after': rule['new'], 'reason': rule['reason'],
                                      'projection_role': rule.get('projection_role', 'teaching_reference')})
        if operations:
            if any(kind == 'catalog_entry' for kind, *_ in operations):
                projected['reference_catalog_hash'] = _digest(projected['reference_catalog'])
            projected['content_hash'] = _digest({'source_content_hash': document.get('content_hash'),
                                                'policy': POLICY, 'blocks': projected['blocks'],
                                                'reference_catalog': projected.get('reference_catalog')})
        projected, report = _finish(document, projected, report, already)
    for flag, field in [('anchors_preserved', 'anchor'), ('block_ids_preserved', 'block_id'),
                        ('owner_ids_preserved', 'owner_id'), ('pointers_preserved', 'json_pointer')]:
        report[flag] = [b.get(field) for b in projected['blocks']] == [b.get(field) for b in document['blocks']]
        assert report[flag]
    report['required_block_ids_preserved'] = projected.get('required_block_ids') == document.get('required_block_ids')
    assert report['required_block_ids_preserved']
    return projected, report


def correct_display_concepts(concepts: dict) -> tuple[dict, dict]:
    """Synchronize only exact C-0124 definition fields, preserving provenance.

    ``source_quotes``, occurrence records and longer diagnostic explanations are
    kept exactly. Global data currently may be empty; that is not an error.
    """
    if not isinstance(concepts, dict):
        raise TypeError('Global concepts must be a dictionary keyed by concept ID')
    projected, report, already = deepcopy(concepts), _report('global_concepts', concepts), False
    rules = _rules().get('reference_catalog_replacements', [])
    for rule in rules:
        ident = rule['owner_id']
        entry = projected.get(ident)
        if entry is None:
            continue
        if not isinstance(entry, dict) or entry.get('id', ident) != ident or entry.get('name') != '辐角变化':
            _mismatch(report, '/' + ident, 'Global concept identity/name differs', entry)
            continue
        accepted = {rule['old'], GLOBAL_C0124_FORMAL}
        for field in ('formal', 'markdown'):
            value = entry.get(field)
            if value is None:
                continue
            pointer = '/' + ident + '/' + field
            if value == rule['new']:
                already = True
            elif value in accepted:
                entry[field] = rule['new']
                report['changes'].append({'concept_id': ident, 'pointer': pointer,
                                          'before': value, 'after': rule['new'],
                                          'reason': rule['reason']})
            else:
                _mismatch(report, pointer, 'Global definition signature differs', value)
        if any(c.get('concept_id') == ident for c in report['changes']) and 'version' in entry:
            entry['version'] = _digest({'source_version': concepts[ident]['version'],
                                        'policy': POLICY, 'entry': entry})
    return _finish(concepts, projected, report, already)
