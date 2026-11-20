"""Content identity, review evidence and displayability are separate contracts."""
from copy import deepcopy

from ...core.digest import digest_of
from ...errors import ContractError
from .content_contracts import as_dict, content_digest, parse_content
from .reader_document import project_reader

STATUSES = frozenset({'verified', 'unverified', 'known_error', 'missing'})


def outcome(status, reasons=(), *, upstream=(), warnings=(), attempts=None):
    if status not in STATUSES:
        raise ContractError('unknown review outcome: '+str(status))
    result = {'status': status, 'reasons': list(dict.fromkeys(str(r) for r in reasons if r)),
              'upstream': deepcopy(list(upstream)), 'warnings': deepcopy(list(warnings))}
    if attempts is not None:
        result['attempts'] = deepcopy(attempts)
    return result


def inherit_outcome(own, upstream):
    """A downstream pass cannot certify an upstream candidate."""
    parent = deepcopy(upstream or outcome('unverified', ['上游核实状态缺失']))
    own = deepcopy(own)
    if parent.get('status') != 'verified' and own['status'] == 'verified':
        own['status'] = 'unverified'
        own.setdefault('reasons', []).append('依赖内容尚未核实')
    own.setdefault('upstream', []).append(parent)
    return own


def aggregate_outcomes(rows):
    rows = list(rows)
    if not rows:
        return outcome('unverified', ['没有可核实对象'])
    from ...core.review_mode import substantive_reviews_enabled
    if substantive_reviews_enabled():
        # Limited review keeps existing work visible even when it has errors.
        # Reserve missing for absent work, not a negative review of real bytes.
        status = ('missing' if any(r.get('status') == 'missing' for r in rows)
                  else 'known_error' if any(r.get('status') == 'known_error' for r in rows)
                  else 'unverified' if any(r.get('status') != 'verified' for r in rows) else 'verified')
    else:
        status = ('missing' if any(r.get('status') in {'known_error', 'missing'} for r in rows)
                  else 'unverified' if any(r.get('status') != 'verified' for r in rows) else 'verified')
    return outcome(status, [reason for row in rows for reason in row.get('reasons', [])], upstream=rows)


def content_identity(content, reference_catalog, *, policy_digest):
    """Bind parseable immutable reader input; this deliberately grants no approval."""
    c = as_dict(content)
    parse_content(c)
    reader = project_reader(c, reference_catalog).as_dict()
    return {'schema_version': 'ContentIdentityV1', 'book_id': c.get('book_id'),
            'unit_id': c['unit_id'], 'content_hash': content_digest(c),
            'content_revision': c.get('revision'), 'source_context_hash': c.get('source_context_hash'),
            'reader_document_hash': digest_of(reader), 'reference_catalog': reader['reference_catalog'],
            'reference_catalog_hash': reader['reference_catalog_hash'], 'policy_digest': policy_digest}


def validate_content_identity(content, identity):
    if not isinstance(identity, dict) or identity.get('schema_version') != 'ContentIdentityV1':
        raise ContractError('current content identity required')
    expected = content_identity(content, identity.get('reference_catalog'),
                                policy_digest=identity.get('policy_digest'))
    if expected != identity or not identity.get('policy_digest'):
        raise ContractError('content identity differs from current reader/source binding')
    return True


def verify_content_audit(content, audit):
    """Reproduce a real receipt from its current source, math and criterion evidence."""
    from .content_approval import approve_content
    from .content_policy import load_content_policy
    from .review_document import build_review_document
    source=audit.get('source_context') or {}
    policy=load_content_policy(source.get('learner_profile'))
    approval=audit.get('approval') or {}
    document=build_review_document(content,source.get('reference_catalog'))
    expected=approve_content(content,document,audit.get('check_reports') or [],policy,
        source_context=source,math_report=audit.get('math_report'),
        book_id=content.get('book_id'),content_revision=approval.get('content_revision'),
        content_ref=approval.get('content_ref'),closed_ticket_ids=approval.get('closed_ticket_ids',[]),
        repair_authorities=audit.get('repair_authorities') or []).as_dict()
    if expected!=approval or document.as_dict()!=audit.get('review_document'):
        raise ContractError('content approval differs from actual current review evidence')
    return True


def media_outcome(row):
    """Only bound definite findings establish an error; absent evidence stays unknown."""
    explicit = row.get('review_outcome') or {}
    if explicit.get('status') in {'known_error', 'missing'}:
        return deepcopy(explicit)
    report = row.get('validation') or {}
    issues = report.get('issues') or []
    reasons = [i.get('message', '') for i in issues] or [row.get('reason', '')]
    codes = {i.get('code') for i in issues}
    if not row.get('resources') or codes & {'MEDIA_RESOURCE_INVALID', 'RESOURCE_INVALID', 'MEDIA_DECODE'}:
        return outcome('missing', reasons or ['缺可展示资源'])
    protocol_bad = any(str(code).startswith('MEDIA_REVIEW_') for code in codes)
    definite = (codes & {'MEDIA_FUNCTIONAL', 'MEDIA_LABEL_CLIPPED'} or
                (not protocol_bad and any(v.get('status') == 'fail' for v in report.get('verdicts', []))))
    product=row.get('static_product') or {}
    definite=definite or (product.get('technical_report') or {}).get('mathematical_status')=='fail' or any(
        relation.get('status')=='fail' for relation in product.get('relations',[]))
    if protocol_bad and report.get('review_items'):
        from ..media.validation import actionable_media_failures, result_digest
        if report.get('result_hash')==result_digest(row):
            definite=definite or bool(actionable_media_failures(report,items=report['review_items']))
    if definite:
        return outcome('known_error', reasons)
    if report.get('ok') is True and report.get('decision') == 'accepted' and not issues:
        return outcome('verified', warnings=report.get('warnings', []))
    return outcome('unverified', reasons or ['审核尚未核实'], warnings=report.get('warnings', []))
