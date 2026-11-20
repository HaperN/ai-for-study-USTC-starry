"""Retain independently valid current verdicts after a review protocol failure.

This is an incomplete diagnostic report, never an approval report. The host
binds every exchange before salvaging rows; a duplicate criterion is ambiguous
and neither its first nor last occurrence is selected.
"""
from collections import Counter
from copy import deepcopy

from .content_contracts import as_dict, content_digest
from .review_aliases import EVIDENCE_ENCODING, build_evidence_aliases
from ...errors import ContractError


def _row(value):
    return as_dict(value) if hasattr(value, 'as_dict') else deepcopy(dict(value))


def _frozen(content, source_context, items, catalog):
    content, context = _row(content), _row(source_context)
    frozen = [_row(item) for item in items]
    h = content_digest(content)
    if not frozen or len({i.get('criterion_id') for i in frozen}) != len(frozen):
        raise ContractError('partial review requires unique current host criteria')
    if any(not isinstance(i.get('criterion_id'), str) or not i['criterion_id']
           or i.get('content_hash') != h or type(i.get('version')) is not int
           or i['version'] < 1 or not isinstance(i.get('policy_digest'), str)
           or not i['policy_digest'] for i in frozen):
        raise ContractError('partial review criteria are not bound to current content/policy')
    if len({i['policy_digest'] for i in frozen}) != 1:
        raise ContractError('partial review mixes policy versions')
    if not isinstance(catalog, dict) or any(not isinstance(entry, dict)
            or entry.get('content_hash') != h for entry in catalog.values()):
        raise ContractError('partial review evidence is not bound to current content')
    source_hash = context.get('source_context_hash') or content_digest(context)
    if content.get('source_context_hash') and content['source_context_hash'] != source_hash:
        raise ContractError('partial review content belongs to another source context')
    return frozen, h, source_hash


def validate_review_exchange(exchange, content, source_context, items, catalog, *, batch_size=12):
    """Return the exact current batch/catalog/aliases, rejecting mixed inputs."""
    frozen, h, source_hash = _frozen(content, source_context, items, catalog)
    if (not isinstance(exchange, dict) or set(exchange) != {
            'schema_version', 'binding', 'criteria', 'evidence_catalog',
            'evidence_aliases', 'response'}
            or exchange.get('schema_version') != 'ContentReviewExchangeV1'):
        raise ContractError('invalid content review exchange envelope')
    binding = exchange.get('binding')
    index = binding.get('batch_index') if isinstance(binding, dict) else None
    if (type(batch_size) is not int or batch_size < 1 or type(index) is not int
            or index < 0 or index * batch_size >= len(frozen)):
        raise ContractError('invalid current review batch index')
    batch = frozen[index * batch_size:(index + 1) * batch_size]
    relevant = catalog
    aliases = {key: eid for key, eid in build_evidence_aliases(catalog).items()
               if eid in relevant}
    expected = {'encoding': EVIDENCE_ENCODING, 'content_hash': h,
        'source_context_hash': source_hash, 'policy_digest': batch[0]['policy_digest'],
        'items_digest': content_digest({'items': batch}),
        'catalog_digest': content_digest(relevant), 'batch_index': index}
    if (binding != expected or content_digest({'items':exchange['criteria']}) != content_digest({'items':batch})
            or content_digest({'catalog':exchange['evidence_catalog']}) != content_digest({'catalog':relevant})
            or exchange['evidence_aliases'] != aliases):
        raise ContractError('content review exchange differs from current frozen inputs')
    return deepcopy(batch), deepcopy(relevant), deepcopy(aliases)


def build_partial_review_report(content, source_context, items, evidence_catalog,
                                exchanges, error, *, decode_response):
    """Salvage single valid verdicts; synthesize unknown for every other item.

    Decoding and validation run independently per unique expected row. No
    response is rewritten, no status is upgraded, and no additional API request
    is made. A partial report cannot enter the approval interface.
    """
    from .review import validate_verdicts

    frozen, h, source_hash = _frozen(content, source_context, items, evidence_catalog)
    exchanges = deepcopy(list(exchanges))
    accepted, unresolved, seen_batches = {}, {}, set()
    errors = [{'type': type(error).__name__, 'message': str(error),
               'errors': deepcopy(getattr(error, 'errors', []))}]
    for exchange in exchanges:
        batch, relevant, aliases = validate_review_exchange(
            exchange, content, source_context, frozen, evidence_catalog)
        index = exchange['binding']['batch_index']
        if index in seen_batches:
            raise ContractError('duplicate review exchange batch is ambiguous')
        seen_batches.add(index)
        raw = exchange['response']
        rows = raw.get('verdicts') if isinstance(raw, dict) else None
        rows = rows if isinstance(rows, list) else []
        counts = Counter(row.get('criterion_id') for row in rows
                         if isinstance(row, dict) and isinstance(row.get('criterion_id'), str))
        by_id = {row['criterion_id']: row for row in rows
                 if isinstance(row, dict) and isinstance(row.get('criterion_id'), str)
                 and counts[row['criterion_id']] == 1}
        for item in batch:
            cid = item['criterion_id']
            if counts[cid] != 1:
                unresolved[cid] = 'duplicate_criterion' if counts[cid] > 1 else 'missing_criterion'
                continue
            try:
                decoded = decode_response({'verdicts': [by_id[cid]]}, aliases)
                report = validate_verdicts([item], decoded, relevant).as_dict()
                accepted[cid] = report['verdicts'][0]
            except (ContractError, KeyError, TypeError, ValueError) as exc:
                unresolved[cid] = 'invalid_verdict'
                errors.append({'batch_index': index, 'criterion_id': cid,
                    'type': type(exc).__name__, 'message': str(exc),
                    'errors': deepcopy(getattr(exc, 'errors', []))})
    verdicts, blocking, warnings, unknown_ids = [], [], [], []
    for item in frozen:
        cid = item['criterion_id']
        row = accepted.get(cid)
        if row is None:
            code = unresolved.get(cid, 'not_run')
            row = {'criterion_id': cid, 'version': item['version'], 'status': 'unknown',
                'reason': '当前标准没有唯一且协议有效的审核结果：' + code,
                'reason_code': code, 'evidence_refs': []}
            unknown_ids.append(cid)
        verdicts.append(row)
        if row['status'] not in {'pass', 'not_applicable'}:
            finding = {**deepcopy(row), 'rule_id': item['rule_id'],
                'subject_id': item['subject_id'], 'expected': item['expected'],
                'repair_hint': item.get('repair_hint', '')}
            (blocking if item.get('required', True) else warnings).append(finding)
    return {'schema_version': 'PartialReviewReportV1', 'input_hash': h,
        'source_context_hash': source_hash, 'policy_digest': frozen[0]['policy_digest'],
        'item_digest': content_digest({'items': frozen}),
        'evidence_digest': content_digest(evidence_catalog), 'complete': False,
        'decision': 'needs_review', 'verdicts': verdicts, 'blocking_issues': blocking,
        'warnings': warnings, 'review_items': frozen,
        'evidence_catalog': deepcopy(evidence_catalog), 'exchanges': exchanges,
        'responses': [deepcopy(e['response']) for e in exchanges],
        'protocol_errors': errors, 'unresolved_criteria': unknown_ids}
