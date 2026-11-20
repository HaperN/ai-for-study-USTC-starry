"""Missing citation/binding metadata must not substitute for content review."""
from copy import deepcopy

import pytest

from aiforstudy.blocks.content.content_contracts import parse_content
from aiforstudy.blocks.content.content_policy import applicable_items, check_content
from aiforstudy.blocks.content.content_approval import approve_content
from aiforstudy.blocks.content.math_checks import check_content_math
from aiforstudy.blocks.content.review import build_evidence_catalog, validate_verdicts
from aiforstudy.blocks.content.review_document import build_review_document
from aiforstudy.errors import ContractError
from support.content_case import approved_inputs, teaching_case
from test_learning_checks import check_fixture
from test_teaching_review_evidence import evidence, item, verdict


@pytest.mark.parametrize('status', ['pass', 'fail'])
@pytest.mark.parametrize('refs', [[], [{'evidence_id': 'source:T1', 'quote': 'For a nonzero x'}]])
def test_missing_reader_citation_does_not_discard_semantic_verdict(status, refs):
    row = verdict(status=status, evidence_refs=refs,
        reason='位置：例1第2步；原式 x/x=1；问题：未说明 x≠0；修改：补上非零条件。')
    report = validate_verdicts([item()], {'verdicts': [row]}, evidence())
    assert report['verdicts'] == [row]
    assert report['decision'] == ('accepted' if status == 'pass' else 'needs_review')


def test_binding_absence_does_not_override_review_of_existing_body():
    catalog = evidence()
    catalog['absence:T1'] = {'kind': 'absence', 'content_hash': 'sha256:current',
        'subject_ids': ['T1'], 'parent_pointer': '/teaching_evidence', 'source_id': 'T1'}
    row = verdict(evidence_refs=[{'evidence_id': 'absence:T1'}],
        reason='例1第2步明确写出 For x != 0, x/x = 1；缺少绑定不影响该条件实际存在。')
    report = validate_verdicts([item(required_evidence_kinds=['absence'])], {'verdicts': [row]}, catalog)
    assert report['decision'] == 'accepted'


def test_current_reader_location_does_not_require_manual_subject_binding():
    catalog = evidence()
    catalog['reader:step']['subject_ids'] = ['unit:U']
    report = validate_verdicts([item()], {'verdicts': [verdict()]}, catalog)
    assert report['decision'] == 'accepted'


def test_missing_teaching_links_warn_but_real_scoring_failure_still_blocks():
    from aiforstudy.blocks.content.content_policy import load_content_policy
    c, source = teaching_case()
    c['teaching_evidence'] = []
    c['learning_checks'] = [check_fixture()]
    source['source_obligations'].append({**deepcopy(source['source_obligations'][0]), 'id': 'SO-UNBOUND'})
    policy = load_content_policy(source['learner_profile'])
    report = check_content(c, source, policy)
    assert not report.blocking_issues
    assert {'CONTENT-COVERAGE', 'CONTENT-LEARNING-CHECKS'} <= {r['rule_id'] for r in report.warnings}
    assert all(r['status'] == 'pass' for r in report.rule_results
               if r['rule_id'] in ('CONTENT-COVERAGE', 'CONTENT-LEARNING-CHECKS'))
    assert any(i['subject_id'] == 'SO' and i['required'] for i in applicable_items(c, source, policy))
    c['learning_checks'][0]['test_cases'][0]['expected'] = 'incorrect'
    failed = check_content(c, source, policy)
    assert any(r['issue_key'] == 'regression_mismatch:right' for r in failed.blocking_issues)


@pytest.mark.parametrize('semantic_status', ['pass', 'fail', 'unknown'])
def test_approval_with_missing_links_depends_on_actual_semantic_review(semantic_status):
    c, source, policy, _, _, _ = approved_inputs()
    c['teaching_evidence'] = []
    c = parse_content(c).as_dict()
    document = build_review_document(c, source['reference_catalog'])
    local = check_content(c, source, policy)
    assert not local.blocking_issues
    items = applicable_items(c, source, policy)
    catalog = build_evidence_catalog(c, document, source)
    rows = [{'criterion_id': i['criterion_id'], 'version': i['version'], 'status': 'pass',
             'reason': '正文说明 n 是非负整数且包含零，并给出 n=0 的判断任务。', 'evidence_refs': []}
            for i in items]
    row = next(r for r in rows if r['criterion_id'] == 'SOURCE-FIDELITY:SO')
    row.update(status=semantic_status, reason='核对 body_markdown 中 n 的取值条件及零的边界。')
    review = validate_verdicts(items, {'verdicts': rows}, catalog).as_dict()
    review.update(review_items=list(items), evidence_catalog=catalog)
    if semantic_status == 'pass':
        approval = approve_content(c, document, [local, review], policy,
            source_context=source, math_report=check_content_math(c))
        assert approval['content_hash']
    else:
        with pytest.raises(ContractError, match='semantic criteria unresolved'):
            approve_content(c, document, [local, review], policy,
                source_context=source, math_report=check_content_math(c))


def test_missing_goal_binding_keeps_visible_context_for_media_review():
    from aiforstudy.blocks.media.media_requirements import candidates, goal_reader_context
    c, _ = teaching_case()
    c['teaching_evidence'] = []
    context = goal_reader_context(c, candidates(c)[0])
    assert context['explicit_reader_evidence'] == []
    assert c['body_markdown'] in context['reader_text']
    assert '/body_markdown' in context['repairable_reader_pointers']


def test_actual_math_failure_remains_blocking_without_reader_citation():
    catalog = evidence()
    catalog['math:T1'] = {'kind': 'math_check', 'content_hash': 'sha256:current',
                          'subject_ids': ['T1'], 'status': 'fail'}
    with pytest.raises(ContractError, match='math_evidence_cannot_pass'):
        validate_verdicts([item()], {'verdicts': [verdict(evidence_refs=[{'evidence_id': 'math:T1'}])]}, catalog)
