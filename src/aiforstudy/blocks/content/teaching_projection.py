"""Compile presentation roles without changing the approved reader document."""
import hashlib
import json
from .content_contracts import as_dict, content_digest
from ..render.layout import _blocks
from ...errors import ContractError


def compile_reader_roles(reader, declarations):
    """Resolve exact reader-field declarations to complete Markdown tokens.

    This helper also serves hand-authored lessons; callers own the provenance
    and semantic review of their declarations, not this presentation compiler.
    """
    r = as_dict(reader)
    fields = {b['json_pointer']: b for b in r['blocks']}
    syntax = _blocks(r)
    assignments, selected = {}, []
    for evidence in declarations:
        pointer = evidence['reader_pointer']
        field = fields.get(pointer)
        if not field or field.get('raw_source'):
            raise ContractError('invalid teaching evidence pointer: ' + pointer)
        quote = evidence.get('quote', '')
        raw = field['raw_text']
        if quote:
            start = raw.find(quote)
            if start < 0 or raw.find(quote, start + 1) >= 0:
                raise ContractError('teaching evidence quote missing or ambiguous')
            end = start + len(quote)
        else:
            start, end = 0, len(raw)
        candidates = [b for b in syntax if b['target_pointer'] == pointer and b['source_start_cp'] < end and b['source_end_cp'] > start]
        if not candidates or any(raw[b['source_start_cp']:min(start,b['source_end_cp'])].strip() or raw[max(end,b['source_start_cp']):b['source_end_cp']].strip() for b in candidates):
            raise ContractError('teaching evidence must select complete Markdown blocks')
        role = evidence.get('reader_role')
        if role not in (None, 'main', 'proof', 'worked_example'):
            raise ContractError('invalid reader role')
        for block in candidates:
            if role is None: continue
            key = block['block_id']
            if key in assignments and assignments[key] != role:
                raise ContractError('conflicting teaching roles for Markdown block')
            assignments[key] = role
        selected.append([b['block_id'] for b in candidates])
    regions = [{'block_id': b['block_id'], 'reader_block_id': b['field_id'],
                'reader_pointer': b['target_pointer'], 'source_start_cp': b['source_start_cp'],
                'source_end_cp': b['source_end_cp'], 'reader_role': assignments[b['block_id']]}
               for b in syntax if b['block_id'] in assignments]
    return {'roles': assignments, 'regions': regions}, selected


def compile_teaching_presentation(content, reader):
    c, r = as_dict(content), as_dict(reader)
    if r.get('content_hash') != content_digest(c) or r.get('unit_id') != c.get('unit_id'):
        raise ContractError('reader document does not match content')
    claims = {x['id'] for x in c['teaching_plan']['claims']}
    evidence = c['teaching_evidence']
    for row in evidence:
        for claim in row['claim_refs']:
            if claim not in claims: raise ContractError('unknown teaching claim: ' + claim)
    projection, selections = compile_reader_roles(r, evidence)
    coverage = {key: [] for key in claims}
    for row, selected in zip(evidence, selections):
        field = next(b for b in r['blocks'] if b['json_pointer'] == row['reader_pointer'])
        visible = not field.get('collapsed') or field.get('disclosure_part') == 'core'
        if (row.get('reader_role') or 'main') == 'main' and visible:
            for claim in row['claim_refs']:
                for block_id in selected:
                    if projection['roles'].get(block_id, 'main') != 'main': continue
                    if block_id not in coverage[claim]: coverage[claim].append(block_id)
    return bind_teaching_presentation(r, projection, coverage)


def bind_teaching_presentation(reader, projection, coverage=None):
    """Bind a compiled projection to this exact final reader document."""
    r = as_dict(reader)
    binding = [r['unit_id'], r['content_hash']] + [[str(b.get(k, '')) for k in ('block_id','json_pointer','raw_text','markdown','visible_text','anchor','group','owner_id','field_label')] + [[str(x) for x in b.get('source_map', [])], [[str(span.get(k, '')) for k in ('visible_start','visible_end','raw_start','raw_end')] for span in b.get('marker_spans', [])]] for b in r['blocks']]
    binding_hash = 'sha256:' + hashlib.sha256(json.dumps(binding, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return {'schema_version': 'TeachingPresentationV1', 'content_hash': r['content_hash'], 'binding_hash': binding_hash,
            'reader_document_hash': content_digest(r), **projection, 'claim_main_blocks': coverage or {}}
