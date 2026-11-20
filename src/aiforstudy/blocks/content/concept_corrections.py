"""Reviewed, run-local revisions of shared concept entries.

The original index and old source/review snapshots stay immutable. Authors use
the existing corrections/source_refs fields; only an accepted existing body
review may publish a proposed shared revision. The book's run lease excludes
other processes, while this lock serializes its concurrent lesson workers.
"""
from copy import deepcopy
from pathlib import Path
from threading import RLock

from .content_contracts import content_digest
from .source_context import SourceContext
from ...core.jsonio import read_json, write_json_atomic

_LOCK = RLock()
RELATIVE_PATH = 'concepts/corrections.json'


def load_corrections(products, *, read=None):
    path = Path(products) / RELATIVE_PATH
    value = read(RELATIVE_PATH) if read else read_json(path, default={})
    return deepcopy((value or {}).get('revisions') or [])


def _version(row):
    return content_digest({key:row['entry'].get(key) for key in ('id','formal','definition')})


def proposed_corrections(content, context):
    """Resolve explicit field replacements, without guessing from review prose."""
    concepts = {row['id']:deepcopy(row) for row in context.get('concept_index', {}).get('concepts', [])}
    proposals, conflicts = [], []
    for correction in content.get('corrections') or []:
        if not isinstance(correction, dict):
            continue
        for ref in correction.get('source_refs') or []:
            if (not isinstance(ref, dict) or ref.get('kind') != 'concept'
                    or ref.get('json_pointer') not in ('/formal', '/definition')):
                continue  # Ordinary textbook corrections keep their old meaning.
            cid, field = ref.get('concept_id'), ref['json_pointer'][1:]
            before, after = correction.get('original'), correction.get('corrected')
            entry = concepts.get(cid)
            if entry and entry.get(field) == after:
                continue  # A retained correction is not a fresh proposal.
            if (not entry or not isinstance(before, str) or not before.strip()
                    or not isinstance(after, str) or not after.strip()
                    or not str(correction.get('reason') or '').strip() or entry.get(field) != before):
                conflicts.append({'correction':deepcopy(correction), 'concept_id':cid,
                    'reason':'公共概念原值或身份已变化；保留提案，不能覆盖其他修正。'})
                continue
            entry[field] = after
            row = {'concept_id':cid, 'field':field, 'before':before, 'after':after,
                   'entry':deepcopy(entry), 'correction':deepcopy(correction)}
            row['version'] = _version(row)
            proposals.append(row)
    return proposals, conflicts


def correction_shape_errors(content, context):
    """修改计划 C2：公共卡修正必须真的改了正式字段。

    Returns correctable shape rows (protocol budget): a concept correction whose
    corrected value equals its original, or whose target entry/field does not
    exist. Retained, already-effective corrections are fine.
    """
    concepts = {row.get('id'):row for row in (context.get('concept_index') or {}).get('concepts', [])
                if isinstance(row, dict)}
    errors = []
    for index, correction in enumerate(content.get('corrections') or []):
        if not isinstance(correction, dict):
            continue
        for ref in correction.get('source_refs') or []:
            if (not isinstance(ref, dict) or ref.get('kind') != 'concept'
                    or ref.get('json_pointer') not in ('/formal', '/definition')):
                continue
            cid, field = ref.get('concept_id'), ref['json_pointer'][1:]
            entry = concepts.get(cid)
            before, after = correction.get('original'), correction.get('corrected')
            if entry is None or not isinstance(entry.get(field), str) or not entry.get(field).strip():
                problem = f'公共修正指向的 {cid}{ref["json_pointer"]} 不存在'
            elif not isinstance(after, str) or not after.strip() or (
                    isinstance(before, str) and after.strip() == before.strip()):
                problem = f'{cid}{ref["json_pointer"]} 的 corrected 与 original 相同，正式字段没有真正修改'
            else:
                continue
            errors.append({'path': f'/content/corrections/{index}/corrected', 'code': 'shared_correction_unchanged',
                'detail': problem,
                'fix': '问题出在公共卡时，corrected 写出该字段改正后的完整值（与 original 不同）；只在正文里解释不算修复。',
                'passing_criterion': 'source_refs 指向真实存在的卡片字段，corrected 是不同于 original 的完整修正版。'})
    return errors


def correction_listing(content, published, proposals, *, unit_id):
    """修改计划 C5：本节引用概念上的全部修正——公共原值、现值、来源单元与状态。"""
    import json
    import re
    text = json.dumps(content, ensure_ascii=False)
    referenced = set(re.findall(r'\[\[CONCEPT:(C-[0-9A-Za-z_-]+)', text))
    own = {}
    for correction in content.get('corrections') or []:
        for ref in (correction.get('source_refs') or []) if isinstance(correction, dict) else []:
            if isinstance(ref, dict) and ref.get('kind') == 'concept' and ref.get('json_pointer') in ('/formal', '/definition'):
                referenced.add(ref.get('concept_id'))
                own[(ref.get('concept_id'), ref['json_pointer'][1:])] = correction.get('corrected')
    rows, first_before = {}, {}
    for revision in published:
        key = (revision['concept_id'], revision['field'])
        if revision['concept_id'] not in referenced:
            continue
        first_before.setdefault(key, revision['before'])
        retained = revision.get('origin_unit') == unit_id and own.get(key) == revision['after']
        rows[key] = {'concept_id':key[0], 'field':key[1], 'before':first_before[key], 'after':revision['after'],
                     'origin_unit':revision.get('origin_unit', ''), 'version':revision.get('version'),
                     'status':'本节保留' if retained else '已发布'}
    for proposal in proposals:
        key = (proposal['concept_id'], proposal['field'])
        rows[key] = {'concept_id':key[0], 'field':key[1], 'before':first_before.get(key, proposal['before']),
                     'after':proposal['after'], 'origin_unit':unit_id, 'version':proposal['version'],
                     'status':'本节提案'}
    return [rows[key] for key in sorted(rows)]


def overlay_catalog(catalog, revisions):
    result = deepcopy(catalog)
    entries = {row.get('id'):row for row in result.get('entries', []) if row.get('kind') == 'CONCEPT'}
    for revision in revisions:
        entry = entries.get(revision['concept_id'])
        if entry is None:
            continue
        current = revision['entry']
        entry['markdown'] = current.get('formal') or current.get('definition') or ''
        entry['version'] = revision['version']
    return result


def overlay_context(context, revisions):
    if not revisions:
        return deepcopy(context)
    result = deepcopy(context)
    entries = {row['id']:row for row in result.get('concept_index', {}).get('concepts', [])}
    for revision in revisions:
        entry = entries.get(revision['concept_id'])
        if entry is None:
            continue
        entry[revision['field']] = revision['after']
        for obligation in result.get('source_obligations', []):
            if (obligation.get('concept_id') == revision['concept_id']
                    and obligation.get('conclusion') == revision['before']):
                obligation['conclusion'] = revision['after']
    result['reference_catalog'] = overlay_catalog(result['reference_catalog'], revisions)
    result['source_context_hash'] = SourceContext.model_validate(result).digest
    return result


def publish_corrections(products, proposals, *, content, reviewed_context, report, tickets=()):
    """Commit accepted review evidence with entry-local compare-and-swap."""
    if not proposals or report.get('decision') != 'accepted':
        return [], []
    with _LOCK:
        revisions = load_corrections(products)
        latest = {(r['concept_id'],r['field']):r for r in revisions}
        entries = {r['concept_id']:deepcopy(r['entry']) for r in revisions}
        added, conflicts = [], []
        for proposal in proposals:
            key = (proposal['concept_id'], proposal['field'])
            previous = latest.get(key)
            if previous and previous['after'] == proposal['after']:
                continue
            if previous and previous['after'] != proposal['before']:
                conflicts.append({**deepcopy(proposal), 'reason':'并发修正已更新该条目；未覆盖，需沿现有审核核对。'})
                continue
            row = {**deepcopy(proposal), 'origin_unit':content['unit_id'],
                   'content_hash':content_digest(content), 'source_context_hash':reviewed_context['source_context_hash'],
                   'review_report':deepcopy(report), 'repair_tickets':deepcopy(list(tickets))}
            row['reviewed_version'] = proposal['version']
            row['entry'] = deepcopy(entries.get(proposal['concept_id'],proposal['entry']))
            row['entry'][proposal['field']] = proposal['after']
            row['version'] = _version(row)
            revisions.append(row); added.append(row); latest[key] = row
            entries[proposal['concept_id']] = deepcopy(row['entry'])
        if added:
            write_json_atomic(Path(products)/RELATIVE_PATH,
                {'schema_version':'SharedConceptCorrectionsV1', 'revisions':revisions})
        if conflicts:
            path = Path(products)/'concepts/correction-conflicts'/(content_digest({'conflicts':conflicts}).split(':')[-1]+'.json')
            write_json_atomic(path, {'content_hash':content_digest(content), 'conflicts':conflicts})
        return added, conflicts


def project_current_catalog(products, catalog, *, read=None):
    return overlay_catalog(catalog, load_corrections(products, read=read))


def catalog_review_outcome(content, catalog, audit, previous):
    """An old pass cannot certify a changed reference that the reader can open."""
    from .reader_document import project_reader
    from .review_outcome import outcome
    reviewed = (audit.get('content_identity') or {}).get('reference_catalog') or {}
    old = {row.get('id'):row for row in reviewed.get('entries', []) if row.get('kind') == 'CONCEPT'}
    reader = project_reader(content, catalog).as_dict()
    used = {block.get('owner_id') for block in reader['blocks'] if block.get('group') == 'references'}
    changed_entries = [row for row in catalog.get('entries', []) if row.get('kind') == 'CONCEPT'
        and row['id'] in used and row != old.get(row['id'])]
    changed = [row['id'] for row in changed_entries]
    if changed and previous.get('status') == 'verified':
        return outcome('unverified', ['公共概念已修正，旧正文审核不证明新版引用已核实：'+', '.join(changed)], upstream=[previous])
    return deepcopy(previous)


def affected_body_paths(content, revision):
    """Locate linked obsolete text for context checking, not an error verdict."""
    from .reader_fields import iter_teaching_fields
    from .content_contracts import REFERENCE_PATTERN
    cid = revision['concept_id']
    def linked(value):
        if isinstance(value, dict):
            return value.get('concept_id') == cid or any(linked(v) for k,v in value.items() if k != 'corrections')
        if isinstance(value, list):
            return any(linked(v) for v in value)
        return isinstance(value, str) and any(m.group(1) == 'CONCEPT' and m.group(2).strip() == cid
                                              for m in REFERENCE_PATTERN.finditer(value))
    if not linked(content):
        return []
    from .source_context import _math_spans
    before, after = revision['before'], revision.get('after','')
    old_texts = [before, *[before[start:end] for start,end in _math_spans(before)
                          if before[start:end] not in after]]
    def obsolete(text):
        import re
        current_spans = [(m.start(),m.end()) for m in re.finditer(re.escape(after),text)] if after else []
        return any(not any(start <= m.start() and m.end() <= end for start,end in current_spans)
            for old in old_texts for m in re.finditer(re.escape(old),text))
    return [row['pointer'] for row in iter_teaching_fields(content)
            if not row['pointer'].startswith('/corrections/')
            and not row['pointer'].endswith('/book_solution')
            and obsolete(row['text'])]
