"""Read-only reuse of authored theorem explanations, keyed by their real origin.

This derives display/prompt data from existing products; it is not a second
catalog, a quality gate, or a writer of lesson/source records.
"""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re

from .reader_document import project_reader, _anchor
from .review_outcome import outcome, validate_content_identity
from ..audit.contracts import validate_ticket
from ..media.media_requirements import content_hash
from ...contracts.artifact import ArtifactRef
from ...errors import ConfigError, ContractError

_LINK = re.compile(r'\]\(#(reader-[A-Za-z0-9_-]+)\)')
_CORE = re.compile(r'/explainables/\d+/(?:name|what|why)$')
_OPTIONAL_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, ConfigError, ContractError)


def _first_objects(row):
    content = row['content']
    catalog = {e['id']: e for e in row['reference_catalog'].get('entries', []) if e.get('kind') == 'CONCEPT'}
    objects = [e for e in content.get('explainables', []) if e.get('kind') == 'theorem' and e.get('concept_id')]
    counts = Counter(e['concept_id'] for e in objects)
    owner_counts = Counter(e.get('id') for e in content.get('explainables', []))
    for obj in objects:
        entry = catalog.get(obj['concept_id'])
        # A number/name or a later re-explanation is not an identity match.
        if (entry and entry.get('unit_id') == content['unit_id'] and counts[obj['concept_id']] == 1
                and obj.get('id') and owner_counts[obj['id']] == 1):
            yield obj, entry


def _dependency_blocks(blocks, selected):
    """Include only actual reference targets reachable from the explanation."""
    anchors = {b['anchor'] for b in selected}
    pending = list(selected)
    while pending:
        for target in _LINK.findall(pending.pop().get('markdown', '')):
            if target in anchors:
                continue
            anchors.add(target)
            pending.extend(b for b in blocks if b.get('anchor') == target)
    return [deepcopy(b) for b in blocks if b.get('anchor') in anchors]


def build_theorem_index(rows):
    """Return scoped theorem entries and scoped reader-anchor references."""
    by_unit = {}
    for row in rows:
        content = row['content']; unit = content['unit_id']
        blocks = row['reader_document']['blocks']
        for obj, source in _first_objects(row):
            own = [b for b in blocks if b.get('group') == 'explainables' and b.get('owner_id') == obj['id']]
            if not own:
                continue
            core = [deepcopy(b) for b in own if _CORE.fullmatch(b.get('json_pointer', ''))]
            proof = [deepcopy(b) for b in own if not _CORE.fullmatch(b.get('json_pointer', ''))]
            by_unit.setdefault(unit, {})[obj['concept_id']] = {
                'concept_id': obj['concept_id'], 'unit_id': unit, 'name': obj['name'],
                'anchor': own[0]['anchor'], 'source_label': source.get('label', ''),
                'core_blocks': core, 'proof_blocks': proof,
                'reader_document': {'schema_version': 'ReaderDocumentV1', 'unit_id': unit,
                    'blocks': _dependency_blocks(blocks, own), 'learning_checks': deepcopy(row['reader_document'].get('learning_checks', []))},
                'review_outcome': deepcopy(row.get('review_outcome') or {'status': 'unverified'}),
            }
    refs = {}
    for row in rows:
        unit = row['content']['unit_id']; mapping = refs.setdefault(unit, {})
        for source in row['reference_catalog'].get('entries', []):
            cid, origin = source.get('id'), source.get('unit_id')
            if source.get('kind') == 'CONCEPT' and cid in by_unit.get(origin, {}):
                mapping[_anchor('references', cid)] = {'unit_id': origin, 'concept_id': cid}
        for cid, entry in by_unit.get(unit, {}).items():
            mapping[entry['anchor']] = {'unit_id': unit, 'concept_id': cid}
    return {'by_unit': by_unit, 'refs_by_unit': refs}


def load_theorem_rows(run_dir, *, read=None, unit_ids=None):
    """Read current lesson/audit products, including sources outside the page.

    A renderer can supply its existing dependency-tracking reader. Unavailable
    optional explanations do not stop production or manufacture an approval.
    """
    run = Path(run_dir).resolve()
    def local_read(relative):
        path = run / relative
        if not path.resolve().is_relative_to(run) or path.is_symlink():
            return {}
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    read = read or local_read
    paths = sorted((run / 'work/products/lesson').glob('*.json'))
    rows = []
    for path in paths:
        unit = path.stem
        if unit_ids is not None and unit not in unit_ids:
            continue
        try:
            payload = read(f'work/products/lesson/{unit}.json').get('payload') or {}
            content = payload.get('content') or {}
            ch = content_hash(content)
            if content.get('unit_id') != unit or payload.get('content_hash') != ch:
                continue
            identity = payload.get('content_identity')
            if identity is not None:
                validate_content_identity(content, identity)
            head_path = f'work/products/content/{unit}/current.json'
            head = read(head_path)
            revision = payload.get('content_ref')
            if head or (run / head_path).exists():
                if (head.get('content_hash') != ch or not head.get('content_ref')
                        or revision and revision != head['content_ref']):
                    continue
                revision = head['content_ref']
            if revision is not None:
                ref = ArtifactRef.from_dict(revision)
                path = Path(ref.path).resolve()
                if not path.is_relative_to(run):
                    continue
                ref.verify()
                if read(str(path.relative_to(run))) != content:
                    continue
            # An unusable audit cannot certify the otherwise intact lesson.
            try:
                audit = read(f'work/products/text_audit/{unit}.json').get('payload') or {}
                if audit.get('content_hash') != ch:
                    audit = {}
                elif audit.get('content_identity') is not None:
                    validate_content_identity(content, audit['content_identity'])
            except _OPTIONAL_ERRORS:
                audit = {}
            catalog = (identity or {}).get('reference_catalog') or (
                (audit.get('content_identity') or {}).get('reference_catalog') or
                (audit.get('review_document') or {}).get('reference_catalog'))
            if not catalog:
                continue
            review = deepcopy(audit.get('review_outcome') or outcome('unverified'))
            # Media can discover a body error after its text review completed.
            for stage in ('figures', 'audit'):
                media = read(f'work/products/{stage}/{unit}.json').get('payload') or {}
                for raw in media.get('tickets') or []:
                    try:
                        ticket = validate_ticket(raw)
                    except ContractError:
                        continue
                    if (ticket.is_blocking and ticket.target.get('rule') == 'POST_MEDIA_CONTENT'
                            and ticket.target.get('artifact_hash') == ch):
                        review = outcome('known_error', [ticket.problem], upstream=[review])
            from .concept_corrections import project_current_catalog, catalog_review_outcome
            current_catalog = project_current_catalog(run/'work/products', catalog,
                read=lambda relative: read('work/products/'+relative))
            if current_catalog != catalog:
                review = catalog_review_outcome(content, current_catalog, audit, review)
            catalog = current_catalog
            reader = project_reader(content, catalog).as_dict()
            rows.append({'content': content, 'reader_document': reader, 'reference_catalog': catalog,
                'review_outcome': review})
        except _OPTIONAL_ERRORS:
            continue
    return rows


def available_theorem_explanations(run_dir, unit_id, *, unit_order=None):
    """Only already-reviewed earlier teaching is offered to a later author."""
    if not unit_order or unit_id not in unit_order:
        return []
    preceding = set(unit_order[:unit_order.index(unit_id)])
    rows = load_theorem_rows(run_dir, unit_ids=preceding)
    entries = []
    for row in rows:
        if row['review_outcome'].get('status') != 'verified':
            continue
        for obj, source in _first_objects(row):
            entries.append({key: deepcopy(obj.get(key, [] if key == 'steps' else ''))
                            for key in ('concept_id', 'name', 'what', 'why', 'markdown', 'steps')})
            entries[-1].update(unit_id=row['content']['unit_id'], source_label=source.get('label', ''),
                               review_outcome=deepcopy(row['review_outcome']))
    return entries
