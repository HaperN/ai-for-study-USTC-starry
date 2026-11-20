"""Optional local-test records for the unmodified real model transport.

This module cannot construct providers, dispatch calls, rewrite responses or set
budgets. The explicit test launcher injects its callable into Providers. Logs
contain call identities and numeric provider usage only, never book text or keys.
"""
from __future__ import annotations

from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid


SCHEMA_VERSION = 'LocalModelObservationV1'
EVENTS = frozenset({'started', 'response', 'failed', 'cache_hit'})
IDENTITY_FIELDS = ('run_id', 'book_id', 'stage', 'unit_id', 'resource_id', 'role',
                   'model', 'effort', 'fingerprint', 'scope_kind')
TOKEN_FIELDS = ('prompt_tokens', 'completion_tokens', 'total_tokens',
                'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens',
                'input_tokens', 'output_tokens')
DETAIL_FIELDS = {'prompt_tokens_details':('cached_tokens',),
                 'completion_tokens_details':('reasoning_tokens',)}
ROW_FIELDS = frozenset((*IDENTITY_FIELDS, 'schema_version', 'event', 'session_id',
    'at', 'attempt_id', 'attempt', 'max_tokens', 'wall_seconds', 'error_type',
    'usage_status', 'usage'))


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def _usage(raw):
    if not isinstance(raw, dict):
        return {}
    result = {key:raw[key] for key in TOKEN_FIELDS
              if key in raw and _nonnegative_int(raw[key])}
    for group, fields in DETAIL_FIELDS.items():
        details = raw.get(group)
        if isinstance(details, dict):
            selected = {key:details[key] for key in fields
                        if key in details and _nonnegative_int(details[key])}
            if selected:
                result[group] = selected
    return result


class LocalRunObserver:
    """Append-only per-run observer; construction is read-only and makes no calls.

    Reopening a nonempty log requires resume_run_id. Distinct invocations use
    distinct session IDs; durable production attempt IDs correlate concurrent
    calls and prevent a restarted test observer from resetting execution state.
    Ordinary recording failures are visible in summary and never escape __call__.
    """
    def __init__(self, output_dir, *, book_id: str, resume_run_id: str | None = None):
        if not isinstance(book_id, str) or not book_id:
            raise ValueError('book_id is required for observation')
        if resume_run_id is not None and (not isinstance(resume_run_id, str) or not resume_run_id):
            raise ValueError('resume_run_id must be a nonempty run identity')
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / 'model-observations.jsonl'
        self.book_id = book_id
        self.run_id = resume_run_id
        self.session_id = uuid.uuid4().hex
        self._rows = []
        self._lock = threading.RLock()
        self.write_errors = 0
        if self.path.exists():
            try:
                text = self.path.read_text(encoding='utf-8')
                if text and not text.endswith('\n'):
                    raise ValueError('observation log ends with an incomplete record')
                for line in text.splitlines():
                    row = json.loads(line)
                    self._validate_row(row)
                    if (resume_run_id is None or row['run_id'] != resume_run_id
                            or row['book_id'] != self.book_id):
                        raise ValueError('observation log belongs to a different run/book; explicit matching resume required')
                    self._rows.append(row)
            except (OSError, UnicodeError, TypeError, KeyError, json.JSONDecodeError) as exc:
                raise ValueError('observation log is unreadable or malformed; preserve it and use a separate output directory') from exc

    @staticmethod
    def _validate_row(row):
        if (not isinstance(row, dict) or set(row) != ROW_FIELDS
                or row.get('schema_version') != SCHEMA_VERSION or row.get('event') not in EVENTS
                or any(not isinstance(row.get(field), str) for field in IDENTITY_FIELDS)
                or not row.get('run_id') or not row.get('book_id')
                or not isinstance(row.get('session_id'), str) or not row['session_id']
                or not _nonnegative_int(row.get('attempt'))
                or not _nonnegative_int(row.get('max_tokens'))
                or not isinstance(row.get('error_type'), str)):
            raise ValueError('observation schema or call identity is invalid')
        for field in ('at', 'wall_seconds'):
            value = row.get(field)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError('observation timing is invalid')
        cache = row['event'] == 'cache_hit'
        if ((cache and (row['attempt'] != 0 or row['attempt_id'] is not None))
                or (not cache and (row['attempt'] < 1 or not isinstance(row['attempt_id'], str) or not row['attempt_id']))):
            raise ValueError('observation attempt identity is invalid')
        usage = row['usage']
        if not isinstance(usage, dict) or _usage(usage) != usage:
            raise ValueError('observation usage must contain numeric token fields only')
        expected = ('not_applicable' if cache else 'pending' if row['event'] == 'started'
                    else 'reported' if row['event'] == 'response' and 'total_tokens' in usage else 'unknown')
        if row['usage_status'] != expected or (row['event'] != 'response' and usage):
            raise ValueError('observation usage status is invalid')

    def __call__(self, event: str, metadata: dict) -> None:
        with self._lock:
            try:
                usage = _usage(metadata.get('usage')) if event == 'response' else {}
                row = {field:metadata.get(field, '') for field in IDENTITY_FIELDS}
                row.update(schema_version=SCHEMA_VERSION, event=event, session_id=self.session_id,
                    at=time.time(), attempt_id=metadata.get('attempt_id'), attempt=metadata.get('attempt', 0),
                    max_tokens=metadata.get('max_tokens', 0), wall_seconds=metadata.get('wall_seconds'),
                    error_type=metadata.get('error_type', ''), usage=usage,
                    usage_status=('not_applicable' if event == 'cache_hit' else 'pending' if event == 'started'
                                  else 'reported' if event == 'response' and 'total_tokens' in usage else 'unknown'))
                self._validate_row(row)
                if row['book_id'] != self.book_id or (self.run_id is not None and row['run_id'] != self.run_id):
                    raise ValueError('observer received a different run/book identity')
                self.run_id = row['run_id']
                # Keep the current session's observations available even if the
                # filesystem fails. summary() states the missing durable records.
                self._rows.append(row)
                self.output_dir.mkdir(parents=True, exist_ok=True)
                encoded = (json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    # Different launcher processes can inspect the same empty
                    # directory before either writes. Recheck under a file lock.
                    # A telemetry writer must not hold the model pipeline while
                    # some other process owns this optional output file.
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    size = os.fstat(fd).st_size
                    if size:
                        if os.pread(fd, 1, size - 1) != b'\n':
                            raise ValueError('observation log has an incomplete tail; preserve it')
                        first = json.loads(os.pread(fd, min(size, 65536), 0).split(b'\n', 1)[0])
                        self._validate_row(first)
                        if first['book_id'] != self.book_id or first['run_id'] != self.run_id:
                            raise ValueError('observation output was claimed by a different run/book')
                    if os.write(fd, encoded) != len(encoded):
                        raise OSError('incomplete observation write')
                    os.fsync(fd)
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
            except Exception:
                self.write_errors += 1

    def _execution_attempts(self, path):
        """Read the durable dispatcher journal; never bind it or change attempts."""
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or data.get('schema_version') != 'ExecutionControlV1'
                or not isinstance(data.get('run_id'), str) or not data['run_id']
                or (self.run_id is not None and data['run_id'] != self.run_id)
                or not isinstance(data.get('attempts'), list)):
            raise ValueError('execution journal identity does not match observation')
        attempts = {}
        for row in data['attempts']:
            scope = row.get('scope') if isinstance(row, dict) else None
            if (not isinstance(row, dict) or not isinstance(row.get('attempt_id'), str)
                    or not row['attempt_id'] or row['attempt_id'] in attempts
                    or not isinstance(row.get('fingerprint'), str) or not row['fingerprint']
                    or not _nonnegative_int(row.get('attempt')) or row['attempt'] < 1
                    or not isinstance(scope, dict) or scope.get('run_id') != data['run_id']
                    or set(scope) != {'run_id', 'unit_id', 'stage', 'resource_id'}
                    or any(not isinstance(value, str) for value in scope.values())):
                raise ValueError('execution journal attempt identity is invalid')
            attempts[row['attempt_id']] = row
        return data['run_id'], attempts

    def summary(self, *, execution_path=None) -> dict:
        """Optionally reconcile missing observations against the execution journal.

        After a process crash, absent observer rows cannot mean zero usage. The
        journal is an independent dispatch source, not a token accounting source.
        Unreadable or mismatched journal evidence is reported without throwing.
        """
        with self._lock:
            attempts = {}
            cache_hits = 0
            for row in self._rows:
                if row['event'] == 'cache_hit':
                    cache_hits += 1
                    continue
                attempts[row['attempt_id']] = row
            run_id = self.run_id
            reconciliation = 'not_requested'
            unobserved = 0
            if execution_path is not None:
                try:
                    run_id, dispatched = self._execution_attempts(execution_path)
                    if set(attempts) - set(dispatched):
                        raise ValueError('observed attempt absent from execution journal')
                    for ident, actual in dispatched.items():
                        observed = attempts.get(ident)
                        if observed is not None:
                            if (observed['fingerprint'] != actual['fingerprint']
                                    or observed['attempt'] != actual['attempt']
                                    or any(observed[field] != actual['scope'][field]
                                           for field in ('run_id', 'unit_id', 'stage', 'resource_id'))):
                                raise ValueError('observed attempt identity differs from execution journal')
                        else:
                            unobserved += 1
                            attempts[ident] = {'event':'unobserved', 'usage_status':'unknown', 'usage':{}}
                    reconciliation = 'matched'
                except Exception:
                    reconciliation = 'failed'
            totals = {}
            for row in attempts.values():
                if row['event'] != 'response':
                    continue
                for field, value in row['usage'].items():
                    if isinstance(value, dict):
                        for detail, count in value.items():
                            key = field + '.' + detail
                            totals[key] = totals.get(key, 0) + count
                    else:
                        totals[field] = totals.get(field, 0) + value
            unknown = sum(row['usage_status'] in ('unknown', 'pending') for row in attempts.values())
            pending = sum(row['event'] == 'started' for row in attempts.values())
            return deepcopy({'schema_version':SCHEMA_VERSION, 'book_id':self.book_id,
                'run_id':run_id, 'session_id':self.session_id,
                'observation_log':str(self.path), 'observed_events':len(self._rows),
                'session_events':sum(row['session_id'] == self.session_id for row in self._rows),
                'transport_attempts':len(attempts), 'responses':sum(row['event'] == 'response' for row in attempts.values()),
                'failed_attempts':sum(row['event'] == 'failed' for row in attempts.values()),
                'cache_hits':cache_hits, 'unknown_usage_attempts':unknown, 'pending_attempts':pending,
                'unobserved_attempts':unobserved, 'execution_reconciliation':reconciliation,
                'known_reported_token_totals':totals, 'write_errors':self.write_errors,
                'token_accounting_complete':not unknown and not self.write_errors and reconciliation != 'failed',
                'note':'Known provider counters only; no price, estimated tokens or cumulative production budget.'})
