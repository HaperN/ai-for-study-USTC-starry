"""Durable failed-response evidence scoped to the existing execution attempt."""
import json
from pathlib import Path

from ..core.jsonio import write_json_atomic
from ..errors import ContractError
from .response import response_metadata


def evidence_path(control, attempt_id):
    if control.path is None:
        raise ContractError('响应留证必须绑定当前运行 execution.json')
    return Path(control.path).parent / 'model-responses' / (attempt_id + '.json')


def usage_counts(usage):
    """Only integer token counts (including nested reasoning details) are kept."""
    if not isinstance(usage, dict):
        return {}
    out = {}
    for key, value in usage.items():
        if not isinstance(key, str) or len(key) > 80:
            continue
        if type(value) is int and value >= 0:
            out[key] = value
        elif isinstance(value, dict):
            nested = {k: v for k, v in value.items() if isinstance(k, str) and len(k) <= 80
                      and type(v) is int and v >= 0}
            if nested:
                out[key] = nested
    return out


def preserve_response(control, scope, lease, *, text, metadata, max_tokens, problem, usage=None):
    path = evidence_path(control, lease.lease_id)
    payload = {'schema_version': 'ModelResponseEvidenceV1', 'run_id': control.run_id,
        'scope': scope.as_dict(), 'attempt_id': lease.lease_id, 'attempt': lease.transport_attempt,
        'fingerprint': lease.fingerprint, 'max_tokens': max_tokens,
        'metadata': response_metadata(metadata), 'problem': problem, 'text': text}
    if usage_counts(usage):
        # 修改计划 A2：空答复要能看出推理 token 花在了哪里。
        payload['usage'] = usage_counts(usage)
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != payload:
            raise ContractError('已有模型响应证据不同，禁止覆盖：' + str(path))
    else:
        write_json_atomic(path, payload)
    return path


def previous_length_response(control, scope, fingerprint, *, before_attempt=None):
    """Read original attempts, including a crash after evidence but before finish."""
    found = None
    for attempt in control.snapshot()['attempts']:
        if attempt['scope'] != scope.as_dict() or attempt['fingerprint'] != fingerprint:
            continue
        if before_attempt is not None and attempt['attempt'] >= before_attempt:
            continue
        path = evidence_path(control, attempt['attempt_id'])
        if not path.is_file():
            if str(attempt.get('error', '')).startswith('capacity_failure:'):
                raise ContractError('截断响应证据缺失，禁止按原额度重发：' + str(path))
            continue
        try:
            row = json.loads(path.read_text(encoding='utf-8'))
            expected = {'schema_version':'ModelResponseEvidenceV1', 'run_id':control.run_id,
                'scope':scope.as_dict(), 'attempt_id':attempt['attempt_id'],
                'attempt':attempt['attempt'], 'fingerprint':fingerprint}
            if (not isinstance(row, dict) or any(row.get(k) != v for k,v in expected.items())
                    or type(row.get('max_tokens')) is not int or row['max_tokens'] < 0):
                raise ValueError('response identity mismatch')
        except (ValueError, OSError, TypeError) as exc:
            raise ContractError('模型响应证据损坏，禁止重置容量：' + str(path)) from exc
        if response_metadata(row.get('metadata')).get('finish_reason') == 'length':
            found = (row['max_tokens'], path)
    return found
