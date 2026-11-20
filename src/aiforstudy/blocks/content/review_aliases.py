"""Exact transport aliases; canonical evidence remains the review authority."""
from copy import deepcopy
import json
import re

from ...errors import ContractError


EVIDENCE_ENCODING = 'EvidenceAliasesV1'
EVIDENCE_ID_CONTRACT = (
    '本次 evidence_catalog 的 E0001 等短键是宿主提供的精确证据 ID。'
    '引用是可选的；若提供 evidence_refs.evidence_id，逐字复制本次 evidence_catalog 中的短键；'
    'subject_ids 只辅助定位，当前目录中的证据可跨对象引用，数学相关性由审核判断。'
    '不要复制 block_id、长哈希、历史工单中的旧编号或自行猜编号。'
    'quote 可省略或留空，不必重复 reason 中的依据；若填写则使用字符串提供忠实摘录或概述，保留完整条件与结论。'
    '以短 ID 对应的冻结原文为判断依据，不要求逐字匹配。'
)


def build_evidence_aliases(catalog):
    """Assign once from the full catalog; batches retain the same numbering."""
    if not isinstance(catalog, dict) or any(not isinstance(k, str) or not k for k in catalog):
        raise ContractError('evidence alias catalog requires nonblank canonical IDs')
    return {f'E{index:04d}':key for index,key in enumerate(sorted(catalog), 1)}


def decode_evidence_response(response, aliases):
    """Change only known evidence_id leaves, never a quotation or verdict."""
    if (not isinstance(aliases, dict)
            or any(not isinstance(k, str) or not re.fullmatch(r'E[0-9]{4,}', k)
                   or not isinstance(v, str) or not v for k,v in aliases.items())
            or len(set(aliases.values())) != len(aliases)):
        raise ContractError('evidence aliases are not a one-to-one host mapping')
    decoded = deepcopy(response)
    if not isinstance(decoded, dict) or not isinstance(decoded.get('verdicts'), list):
        return decoded
    for index, row in enumerate(decoded['verdicts']):
        if not isinstance(row, dict) or not isinstance(row.get('evidence_refs'), list):
            continue
        for ref_index, ref in enumerate(row['evidence_refs']):
            if not isinstance(ref, dict):
                continue
            alias = ref.get('evidence_id')
            if not isinstance(alias, str) or alias not in aliases:
                raise ContractError(f'unknown evidence alias at /verdicts/{index}/evidence_refs/{ref_index}/evidence_id: {alias!r}')
            ref['evidence_id'] = aliases[alias]
    return decoded


def map_protocol_errors(errors, id_map):
    """Translate only identified protocol ID fields, leaving quotes untouched.

    Persist known references canonically. Project them into the current alias
    space for a later request; candidates absent from that space grant nothing.
    """
    result = deepcopy(errors)
    for error in result:
        if not isinstance(error, dict):
            continue
        actual = error.get('actual')
        if str(error.get('path', '')).endswith('/evidence_id') and isinstance(actual, str):
            error['actual'] = id_map.get(actual, actual)
        elif isinstance(actual, dict) and isinstance(actual.get('evidence_id'), str):
            actual['evidence_id'] = id_map.get(actual['evidence_id'], actual['evidence_id'])
        if isinstance(error.get('allowed_evidence_ids'), list):
            error['allowed_evidence_ids'] = [id_map[x] for x in error['allowed_evidence_ids']
                                             if isinstance(x, str) and x in id_map]
        if isinstance(error.get('candidates'), list):
            error['candidates'] = [{**x, 'evidence_id':id_map[x['evidence_id']]}
                for x in error['candidates'] if isinstance(x, dict)
                and isinstance(x.get('evidence_id'), str) and x['evidence_id'] in id_map]
    return result


def project_repair_context(context, canonical_to_alias):
    """Project old protocol diagnostics, preserving the persisted ticket bytes."""
    projected = deepcopy(context)

    def diagnostic(text):
        if not isinstance(text, str):
            return text
        prefix = 'ReviewProtocolError: ' if text.startswith('ReviewProtocolError: ') else ''
        try:
            data = json.loads(text[len(prefix):])
        except (TypeError, ValueError):
            return text
        if (not isinstance(data, dict) or data.get('schema_version') != 'ContentReviewProtocolErrorsV1'
                or not isinstance(data.get('errors'), list)):
            return text
        data['errors'] = map_protocol_errors(data['errors'], canonical_to_alias)
        return prefix + json.dumps(data, ensure_ascii=False)

    if isinstance(projected, dict):
        for ticket in projected.get('previous_tickets', []):
            if not isinstance(ticket, dict):
                continue
            if 'problem' in ticket:
                ticket['problem'] = diagnostic(ticket['problem'])
            previous = ticket.get('previous_attempt')
            if isinstance(previous, dict) and 'closure_reason' in previous:
                previous['closure_reason'] = diagnostic(previous['closure_reason'])
    return projected
