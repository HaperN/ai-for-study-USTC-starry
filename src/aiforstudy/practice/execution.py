"""Reconcile a practice stage's execution ledger with durable dispatch accounting."""

import json
import os
import re

from ..core.jsonio import write_json_atomic
from ..errors import ContractError
from ..pipeline.execution_control import ExecutionControl, ExecutionPolicy, ExecutionScope


def _read_record(path):
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ContractError(f'执行记录无法读取：{path.name}') from exc
    if not isinstance(record, dict):
        raise ContractError(f'执行记录格式无效：{path.name}')
    return record


def _accounting(ledger, scope, request_hash):
    """Snapshot the attempt identities and outcomes that determine retry budgets."""
    try:
        rows = ledger['attempts']
        if not isinstance(rows, list):
            raise ValueError('invalid attempts list')
        attempts = []
        for row in rows:
            fingerprint = row['fingerprint']
            if (row['scope'] != scope.as_dict() or not re.fullmatch(r'sha256:[0-9a-f]{64}', fingerprint)
                    or not re.fullmatch(r'[0-9a-f]{32}', row['attempt_id'])
                    or type(row['attempt']) is not int or row['attempt'] < 1):
                raise ValueError('invalid dispatch identity')
            state = row['state']
            attempts.append({
                'attempt_id': row['attempt_id'], 'attempt': row['attempt'], 'fingerprint': fingerprint,
                # An in-flight call and its interrupted resume both spend an attempt.
                'state': 'interrupted' if state == 'dispatched' else state,
            })
        if len({row['fingerprint'] for row in attempts}) > 1:
            raise ValueError('different footprints in one stage')
        return {
            'schema_version': 'PracticeDispatchV1', 'scope': scope.as_dict(),
            'request_sha256': request_hash, 'attempts': attempts,
            'policy_digest': ledger['policy_digest'], 'run_started_at': ledger['run_started_at'],
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError('执行尝试身份无效；禁止重置尝试次数') from exc


def _check_responses(root, ledger, scope, mode, request_hash):
    attempts = {row['attempt_id']: row for row in ledger['attempts']}
    fingerprints = {row['fingerprint'] for row in attempts.values()}
    for path in (root / 'model-responses').glob('*.json'):
        row = attempts.get(path.stem)
        received = _read_record(path)
        if (
            row is None or received.get('schema_version') != 'ModelResponseEvidenceV1'
            or received.get('run_id') != scope.run_id
            or any(received.get(key) != row[key] for key in ('scope', 'attempt_id', 'attempt', 'fingerprint'))
            or type(received.get('attempt')) is not int
        ):
            raise ContractError('执行尝试身份与模型响应证据不一致；禁止重置尝试次数')
    summary = root / 'response.json'
    if summary.exists():
        received = _read_record(summary)
        fingerprint = received.get('fingerprint')
        if (received.get('request_sha256') != request_hash or received.get('mode') != mode
                or not isinstance(fingerprint, str) or fingerprint not in fingerprints):
            raise ContractError('执行尝试身份与阶段响应不一致；禁止重置尝试次数')
    if any(path.stem not in fingerprints for path in (root / 'cache').glob('*.json')):
        raise ContractError('执行尝试身份与阶段缓存不一致；禁止重置尝试次数')


class _PracticeExecution(ExecutionControl):
    def __init__(self, accounting_path, scope, request_hash, *, legacy_read_only):
        super().__init__(ExecutionPolicy(parallelism=1, call_timeout_seconds=420, api_deadline_seconds=1000))
        self._accounting_path = accounting_path
        self._practice_scope = scope
        self._request_hash = request_hash
        self._legacy_read_only = legacy_read_only

    def _persist(self):
        if self._legacy_read_only:
            return
        # The base controller calls this under its lock, including before dispatch.
        # An interrupted write between files leaves a mismatch that blocks resume.
        super()._persist()
        proof = _accounting(self.snapshot(), self._practice_scope, self._request_hash)
        try:
            write_json_atomic(self._accounting_path, proof)
            fd = os.open(self._accounting_path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            raise ContractError('执行派发记录无法持久化；禁止继续派发') from exc

    def transport_attempts_used(self, scope, fingerprint):
        attempts = self.snapshot()['attempts']
        if scope != self._practice_scope or any(row['fingerprint'] != fingerprint for row in attempts):
            raise ContractError('执行阶段或调用指纹已改变；禁止重置尝试次数')
        return super().transport_attempts_used(scope, fingerprint)

    def acquire_call(self, scope, *, fingerprint, cancel=None):
        if self._legacy_read_only:
            raise ContractError('旧执行目录缺少独立派发记录；禁止新增调用')
        return super().acquire_call(scope, fingerprint=fingerprint, cancel=cancel)


def bind_practice_execution(root, *, request_hash, mode):
    """Called while holding the stage directory lock."""
    key = request_hash[:16]
    scope = ExecutionScope('practice-' + key, unit_id=key, stage='practice')
    ledger_path = root / 'execution.json'
    accounting_path = root / 'dispatch.json'
    resuming = any(root.iterdir())
    legacy_read_only = False
    if resuming:
        ledger = _read_record(ledger_path)
        expected = _accounting(ledger, scope, request_hash)
        if accounting_path.exists():
            saved = _read_record(accounting_path)
            if json.dumps(saved, sort_keys=True) != json.dumps(expected, sort_keys=True):
                raise ContractError('执行记录与独立派发记录不一致；禁止重置尝试次数')
        else:
            # Read old cache hits and preserve an exhausted budget, but never seed a
            # dispatch record from an old ledger whose history cannot be proved.
            legacy_read_only = True
        _check_responses(root, ledger, scope, mode, request_hash)
    control = _PracticeExecution(accounting_path, scope, request_hash, legacy_read_only=legacy_read_only)
    control.bind(ledger_path, run_id=scope.run_id, require_existing=resuming)
    return control
