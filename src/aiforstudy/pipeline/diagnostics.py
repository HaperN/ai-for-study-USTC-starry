"""Explicit, persisted technical repair instructions. No model calls or retry loops."""
from ..blocks.audit.contracts import legacy_finding_to_ticket, validate_ticket
from ..blocks.audit.loop import Finding
from ..blocks.audit.evidence import locate_quote
from ..core.digest import digest_text


def technical_ticket(*, unit_id, artifact_id, artifact_hash, problem, source,
                     owner, kind, allowed_paths, action, expected, rule, evidence=False):
    base = legacy_finding_to_ticket(Finding(code=rule, severity='P1', unit_key=unit_id,
        where='/diagnostic', why=problem, evidence=problem), artifact_id=artifact_id,
        artifact_hash=artifact_hash, unit_id=unit_id).as_dict()
    proof = locate_quote(source, source, artifact_id=f'source:{unit_id}',
                         artifact_hash=digest_text(source), json_pointer='/text') if source else None
    base.update(owner=owner, kind=kind, status='ready' if proof and proof.status == 'valid' else 'needs_evidence',
        problem=problem, learner_obstacle='该产物未完成校验，读者不能可靠使用：' + problem,
        source_evidence=[s.as_dict() for s in proof.spans] if proof else [],
        allowed_paths=list(allowed_paths), preserve=[],  # preserve 护栏已删除
        repair_instructions={'locate':artifact_id, 'action':action,
            'rationale':problem, 'expected_change':expected},
        criteria=[{'criterion_id':rule.lower(), 'version':1, 'required':True,
                   'expected':expected, 'method':'frame' if evidence else 'rule',
                   'evidence_type':'frame' if evidence else 'file'}])
    return validate_ticket(base)
