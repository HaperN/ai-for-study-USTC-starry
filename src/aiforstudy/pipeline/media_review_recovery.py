"""Frozen media evidence recovery, bounded by the durable execution journal.

This projection is never an approval receipt and cannot grant extra attempts.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ..core.digest import digest_of
from ..core.jsonio import read_json_soft, write_json_atomic
from ..errors import CapabilityError, ContractError
from ..contracts.review import MEDIA_VERDICT_STATUSES
from .execution_control import AttemptLimitExceeded
from .network_gate import NETWORK_WAIT


def require_compatible_pending_evidence(request, row, *, dependencies, math_context_digest):
    """A dependency upgrade cannot turn unfinished evidence into a new candidate.

    Compare against the frozen row before either the scheduler or executor can
    replace its dependency identity. A distinct production request must own any
    remake; ordinary resume only preserves or completes this evidence task.
    """
    from .media_stages import _pending_protocol_review
    if not row or not _pending_protocol_review(row):
        return False
    expected = {'request_hash':digest_of(request), 'math_context_digest':math_context_digest,
        'generation_dependency_hash':(dependencies or {}).get('generation_dependency_hash'),
        'review_dependency_hash':(dependencies or {}).get('review_dependency_hash')}
    if any(row.get(key) != value for key,value in expected.items()):
        raise CapabilityError('待补审资源 '+str(request['request_id'])+
            ' 的请求、批准上下文或生成/审核依赖已变化，不能证明同证据恢复；'
            '原资源、候选、工单及累计执行记录保持不变。新运行使用当前依赖重新制作。')
    return True


class MediaReviewRecovery:
    def __init__(self, path, *, identity, session_id, batch=None):
        self.path = Path(path)
        self.identity = deepcopy(identity)
        self.session_id = session_id
        saved = read_json_soft(self.path)
        if self.path.exists() and not isinstance(saved, dict):
            raise ContractError('媒体补审恢复记录损坏；不能重置尝试次数')
        if saved is not None and (saved.get('schema_version') != 'MediaReviewRecoveryV2'
                or not isinstance(saved.get('attempts'),list)
                or any(not isinstance(a,dict) for a in saved.get('attempts',[]))):
            raise ContractError('媒体补审恢复记录版本或尝试数据无效；禁止重置')
        if saved is not None and saved.get('identity') != self.identity:
            raise ContractError('媒体补审恢复身份不一致')
        self.data = saved or {'schema_version': 'MediaReviewRecoveryV2',
            'identity': self.identity, 'created_session_id': session_id,
            'state': 'pending', 'attempts': [], 'attempt_limit': 2, 'batch_size': 2,
            'resume_session_id': '', 'batch': deepcopy(batch)}

    def snapshot(self):
        return deepcopy(self.data)

    def save(self):
        write_json_atomic(self.path, self.data)

    def authorize_resume(self, session_id, execution_policy):
        """Resume can finish an unused slot; it never grants another batch."""
        if self.data['state'] not in ('transport_failed','in_flight',NETWORK_WAIT):
            return False
        if self._spent() >= self.data['attempt_limit']:
            return False
        self.session_id=session_id
        self.data['resume_session_id']=session_id
        self.save()
        return True

    def _spent(self):
        # Network waits (plan N) are recorded for evidence but spend no attempt.
        return sum(a.get('state') != NETWORK_WAIT for a in self.data['attempts'])

    def begin(self,fingerprint,previous_attempts,*,batch_size=2):
        self.data['attempt_limit']=min(2,batch_size)
        recorded=sum(a.get('fingerprint')==fingerprint and a.get('state') != NETWORK_WAIT
                     for a in self.data['attempts'])
        # execution.json is authoritative even if a crash occurred between its
        # dispatch write and this evidence projection's write.
        for _ in range(recorded,previous_attempts):
            self.data['attempts'].append({'fingerprint':fingerprint,'state':'interrupted'})
        if self.data['state'] in ('valid_review_received','review_protocol_invalid'):
            raise AttemptLimitExceeded('当前证据已有审核响应，不能当运输失败重试')
        remaining=self.data['attempt_limit']-self._spent()
        if remaining<=0: raise AttemptLimitExceeded('当前证据运输尝试已达上限；恢复不重置')
        self.save()
        return remaining

    def started(self, lease):
        self.data['attempts'].append({'lease_id': lease.lease_id,
            'fingerprint': lease.fingerprint, 'transport_attempt': lease.transport_attempt,
            'state': 'in_flight'})
        self.data['state'] = 'in_flight'
        self.save()

    def finished(self, state, *, error='', payload=None):
        if state == 'response_received':
            verdicts = payload.get('verdicts') if isinstance(payload, dict) else None
            semantic = bool(isinstance(verdicts, list) and verdicts and all(
                isinstance(v, dict) and v.get('criterion_id') and v.get('version')
                and v.get('status') in MEDIA_VERDICT_STATUSES for v in verdicts))
            # Actual citation, coverage and pass/fail validation stays in validate_media.
            state = 'valid_review_received' if semantic else 'review_protocol_invalid'
        self.data['state'] = state
        self.data['attempts'][-1].update(state=state, error=error)
        self.save()


def recovery_identity(ctx, request, row):
    from .media_stages import _media_evidence_basis
    from ..blocks.media.review_policy import media_review_policy
    return {'run_id': ctx.services.store.run_id, 'unit_id': ctx.node.scope_key,
        'request_id': request['request_id'], 'request_hash': digest_of(request),
        'resource_hash': _media_evidence_basis(row), 'criteria_digest': request['criteria_digest'],
        'policy_digest': digest_of(media_review_policy(request['kind'])),
        'review_dependency_hash': row.get('review_dependency_hash'),
        'math_context_digest': row.get('math_context_digest'),
        'approved_reader_document_hash': row.get('approved_reader_document_hash'),
        'evidence_basis': _media_evidence_basis(row)}


def recovery_for(ctx, request, row):
    identity = recovery_identity(ctx, request, row)
    path = Path(ctx.services.store.work)/'media-review-recovery'/(digest_of(identity, check=False)[7:]+'.json')
    batch = getattr(ctx, 'repair_batch', None)
    return MediaReviewRecovery(path, identity=identity,
        session_id=getattr(ctx.services, 'media_review_session_id', ''),
        batch=batch.as_dict() if batch else None)


class EvidenceReviewClient:
    """Attach recovery only while the existing client dispatches this evidence job."""
    def __init__(self, client, recovery):
        self.client = client
        self.after_evidence_client = client
        self.recovery = recovery

    def __getattr__(self, name):
        return getattr(self.client, name)

    def validate_frozen_evidence(self, request, row):
        from .media_stages import _media_evidence_basis
        identity = self.recovery.identity
        if (digest_of(request) != identity['request_hash']
                or _media_evidence_basis(row) != identity['evidence_basis']
                or any(row.get(key) != identity.get(key) for key in (
                    'math_context_digest', 'approved_reader_document_hash', 'review_dependency_hash'))):
            raise ContractError('恢复目标与当前实际读取的冻结媒体证据不一致；未派发请求')

    def chat_json(self, messages, *, images=None, **kwargs):
        gateway = self.client.gateway
        previous = getattr(gateway, 'media_review_recovery', None)
        gateway.media_review_recovery = self.recovery
        try:
            return self.client.chat_json(messages, images=images, **kwargs)
        finally:
            gateway.media_review_recovery = previous


def evidence_reviewer(ctx, client, request, row):
    if client is None or not hasattr(client, 'gateway'):
        return client
    return EvidenceReviewClient(client, recovery_for(ctx, request, row))


def resume_media_review_batch(runner, node, directives, state):
    """Resume the original semantic repair, without incrementing its ticket counts."""
    if not runner.resume or node.stage != 'figures':
        return None
    from types import SimpleNamespace
    from .media_stages import _path, _pending_protocol_review, _intact_media_resources
    from .repair import RepairBatch
    ctx = SimpleNamespace(node=node, services=runner.services)
    manifest = (read_json_soft(_path(ctx, 'figures')) or {}).get('payload') or {}
    wanted = {tid for directive in directives for tid in directive.ticket_ids}
    if not wanted or any(d.owner != 'reviewer' or d.kind != 'evidence_format' for d in directives):
        return None
    eligible = []
    for row in manifest.get('requests', []):
        row_tickets = {t['ticket_id'] for t in row.get('repair_tickets', [])
            if t.get('kind') == 'evidence_format'}
        if not row_tickets & wanted:
            continue
        if not _pending_protocol_review(row) or not _intact_media_resources(row):
            return None
        recovery = recovery_for(ctx, row['request'], row)
        if not recovery.path.is_file():
            return None
        saved = recovery.snapshot()
        batch_data = saved.get('batch')
        if not batch_data or saved.get('state') not in ('transport_failed', 'in_flight', NETWORK_WAIT):
            return None
        batch = RepairBatch.from_dict(batch_data)
        if (not wanted.issubset(set(batch.ticket_ids)) or batch.consumes_content_round
                or any(j.kind != 'evidence_format' for j in batch.jobs)
                or any(state.evidence_attempts_by_verdict.get(tid, 0) != 1 for tid in wanted)
                or state.active_batch_id not in (None, batch.batch_id)):
            return None
        eligible.append((recovery, batch, row_tickets & wanted))
    if not eligible or set().union(*(item[2] for item in eligible)) != wanted:
        return None
    # All rows belong to one already persisted coordinator batch. Never infer a
    # new semantic repair allowance from an arbitrary media error or job ID.
    batch = eligible[0][1]
    if any(item[1].batch_id != batch.batch_id for item in eligible):
        return None
    session = runner.services.media_review_session_id
    policy = runner.services.control.policy.as_dict()
    if any(not item[0].authorize_resume(session, policy) for item in eligible):
        return None
    from dataclasses import replace
    jobs = tuple(replace(j, ticket_ids=tuple(t for t in j.ticket_ids if t in wanted))
        for j in batch.jobs if set(j.ticket_ids) & wanted)
    runner.store.emit('media.review.transport_resume', stage=node.stage,
        node_id=node.node_id, scope_key=node.scope_key,
        message='同证据补审仅恢复运输；历史工单次数、代码候选与执行记录保持累计',
        batch_id=batch.batch_id, ticket_ids=sorted(wanted), total_transport_limit=2)
    return replace(batch, ticket_ids=tuple(sorted(wanted)), jobs=jobs)


def pending_media_transport_resume(runner, node):
    """A cached needs_review stage must still reach the bounded coordinator."""
    if not runner.resume or node.stage != 'figures':
        return False
    from types import SimpleNamespace
    from .media_stages import _path, _pending_protocol_review
    ctx = SimpleNamespace(node=node, services=runner.services)
    manifest = (read_json_soft(_path(ctx, 'figures')) or {}).get('payload') or {}
    return any(_pending_protocol_review(row) and row.get('repair_tickets')
               for row in manifest.get('requests', []))

