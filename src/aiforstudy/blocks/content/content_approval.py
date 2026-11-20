"""Host-issued content approval with independently inspectable coverage evidence."""

from __future__ import annotations
from .content_contracts import ContentApprovalV1, JsonRecord, as_dict, content_digest, parse_content
from .content_policy import load_content_policy
from .review_document import build_review_document
from ...errors import ContractError


class ContentApprovalV2(JsonRecord):
    pass


def _math_binding(content, report):
    from ..math.contracts import parse_math_model
    from .math_checks import NO_FALLBACK, MATH_REPORT_VERSION
    report = as_dict(report) if report is not None else None
    if (not isinstance(report, dict) or report.get('schema_version') != MATH_REPORT_VERSION
            or report.get('input_hash') != content_digest(content)):
        raise ContractError('missing/stale mathematical report')
    models = [parse_math_model(m) for m in content['math_models']]
    bindings = {m.model_id:m.digest for m in models}
    if report.get('models') != bindings:
        raise ContractError('mathematical report does not cover current models')
    expected = {(m.model_id,c['relation_id']):c['claim_id'] for m in models for c in m.claims}
    actual = [(r.get('model_id'),r.get('relation_id')) for r in report.get('checks',[])]
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ContractError('mathematical report omits or adds model claims')
    for row in report.get('checks',[]):
        if row.get('claim_id') != expected[(row['model_id'],row['relation_id'])]:
            raise ContractError('mathematical relation has a different teaching claim')
        if row.get('model_digest') != bindings[row['model_id']]:
            raise ContractError('mathematical claim has stale model digest')
        if row.get('status') not in {'pass','unknown'}:
            raise ContractError('confirmed mathematical failure cannot be overridden')
        if row.get('status') == 'unknown' and row.get('reason_code') in NO_FALLBACK:
            raise ContractError('unexecuted/resource-limited mathematics requires rerun')
    if report.get('status') == 'fail':
        raise ContractError('mathematical failure prevents approval')


def _approve_v4(content, document, reports, policy, source_context, math_report,
                *, book_id=None, content_revision=None, content_ref=None, closed_ticket_ids=(), repair_authorities=()):
    from .content_policy import check_content, applicable_items
    from .reader_document import project_reader
    from .source_context import SourceContext
    from .review import build_evidence_catalog, validate_verdicts, external_review_items
    from .math_checks import math_review_items
    c = parse_content(as_dict(content)).as_dict()
    source = SourceContext.model_validate(as_dict(source_context))
    if c.get('source_context_hash') != source.digest:
        raise ContractError('content does not bind approved source context')
    expected_doc = build_review_document(c, source.reference_catalog)
    if content_digest(document) != content_digest(expected_doc):
        raise ContractError('approval review document differs from actual reader projection')
    local = check_content(c, source, policy).as_dict()
    if local['blocking_issues']:
        raise ContractError('unresolved local content checks')
    supplied = [as_dict(r) for r in reports]
    local_reports = [r for r in supplied if r.get('schema_version') == 'ContentCheckReportV2']
    reviews = [r for r in supplied if r.get('schema_version') == 'ReviewReportV2']
    if len(local_reports)!=1 or content_digest(local_reports[0])!=content_digest(local):
        raise ContractError('local report is missing, unexecuted, or differs from actual check')
    if len(reviews)!=1 or len(supplied)!=2:
        raise ContractError('approval requires exactly current local and semantic reports')
    _math_binding(c, math_report)
    items = applicable_items(c, source, policy) + external_review_items(c, policy, repair_authorities) + math_review_items(c, policy, math_report)
    evidence = build_evidence_catalog(c, document, source, math_report)
    review = reviews[0]
    if (review.get('review_items') != list(items)
            or review.get('evidence_catalog') != evidence):
        raise ContractError('review replaced host criteria or source evidence')
    verified = validate_verdicts(items, {'verdicts':review.get('verdicts')}, evidence)
    if verified.decision != 'accepted':
        raise ContractError('required semantic criteria unresolved')
    # Unknown results remain unknown; every claim needs its own source review.
    if any(r['status'] in {'unknown','not_run'} for r in math_report.get('checks',[])):
        fallbacks = math_review_items(c, policy, math_report)
        passed = {v['criterion_id'] for v in verified.verdicts if v['status']=='pass'}
        if not fallbacks or not all(i['criterion_id'] in passed for i in fallbacks) or 'source_semantic_review' not in policy.allowed_semantic_fallbacks:
            raise ContractError('unknown mathematics lacks allowed independent source review')
    return ContentApprovalV2(
        schema_version='ContentApprovalV2', book_id=book_id or c['book_id'], unit_id=c['unit_id'],
        content_revision=content_revision or c['revision'], content_ref=content_ref or {},
        content_hash=content_digest(c), source_context_hash=source.digest,
        source_inventory_hash=content_digest({'obligations':[o.as_dict() for o in source.source_obligations]}),
        policy_digest=policy.digest, learner_profile=source.learner_profile.as_dict(),
        review_document_hash=content_digest(document),
        reader_document_hash=project_reader(c, source.reference_catalog).digest,
        reference_catalog_hash=source.reference_catalog.digest,
        math_report_hash=content_digest(math_report), review_items_hash=content_digest({'items':list(items)}),
        check_report_refs=[content_digest(r) for r in supplied],
        closed_ticket_ids=sorted(closed_ticket_ids), repair_authorities_hash=content_digest({'tickets':[as_dict(t) for t in repair_authorities]}), warnings=local['warnings']+verified.warnings,
        decision='accepted',
    )


def approve_content(
    content,
    review_document,
    check_reports,
    policy,
    *,
    book_id=None,
    content_revision=None,
    content_ref=None,
    source_context_hash=None,
    closed_ticket_ids=(),
    source_context=None,
    math_report=None,
    repair_authorities=(),
):
    c = as_dict(content)
    if (c.get('_meta') or {}).get('schema_version') == '4.0':
        if source_context is None:
            raise ContractError('V4 approval requires independently frozen SourceContext')
        return _approve_v4(c, review_document, check_reports, policy, source_context, math_report,
                          book_id=book_id, content_revision=content_revision, content_ref=content_ref,
                          closed_ticket_ids=closed_ticket_ids, repair_authorities=repair_authorities)
    raise ContractError('historical content is read-only; new approval requires LessonContentV4')


def validate_content_approval(
    content, approval, policy=None, source_context_hash=None, review_document=None
):
    c = as_dict(content)
    a = as_dict(approval)
    v4 = (c.get('_meta') or {}).get('schema_version') == '4.0'
    p = policy or load_content_policy(a.get('learner_profile') if v4 else None)
    expected = {
        "schema_version": "ContentApprovalV2" if v4 else "ContentApprovalV1",
        "decision": "accepted",
        "unit_id": c.get("unit_id"),
        "content_hash": content_digest(c),
        "policy_digest": p.digest,
    }
    if review_document is not None:
        expected["review_document_hash"] = content_digest(review_document)
    if not a.get("review_document_hash"):
        raise ContractError("approval lacks review document digest")
    if c.get("book_id"):
        expected["book_id"] = c["book_id"]
    if source_context_hash is not None:
        expected["source_context_hash"] = source_context_hash
    for key, value in expected.items():
        if a.get(key) != value:
            raise ContractError("content approval mismatch: " + key)
    if not a.get("check_report_refs"):
        raise ContractError("content approval lacks check report evidence")
    if v4:
        for field in ('source_inventory_hash','reader_document_hash','reference_catalog_hash',
                      'math_report_hash','review_items_hash','source_context_hash'):
            if not a.get(field):
                raise ContractError('approval lacks '+field)
        if a['source_context_hash'] != c.get('source_context_hash'):
            raise ContractError('approval source context mismatch')
        return ContentApprovalV2(a)
    return ContentApprovalV1(a)
