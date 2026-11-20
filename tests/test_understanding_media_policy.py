"""Understanding goals do not impose a quiz on each concept or media request."""
from copy import deepcopy

import pytest

from aiforstudy.blocks.content.content_policy import applicable_items, check_content, load_content_policy
from aiforstudy.blocks.content.learning_checks import validate_learning_checks
from support.content_case import teaching_case
from test_learning_checks import check_fixture


def test_explained_core_goal_needs_no_extra_quiz_or_understanding_binding():
    content, source = teaching_case()
    content['body_markdown'] = '本节 n 表示非负整数，取值为 0、1、2、3、……。'
    content['learning_checks'] = []
    content['teaching_evidence'] = [e for e in content['teaching_evidence']
                                    if e['purpose'] != 'understanding_check']
    assert validate_learning_checks(content, source) == []
    report = check_content(content, source, load_content_policy())
    assert not report.blocking_issues
    assert not any(r['rule_id'] == 'CONTENT-LEARNING-CHECKS' for r in report.warnings)


def test_assessment_review_reads_whole_unit_instead_of_requiring_tasks_per_goal():
    content, source = teaching_case()
    content['teaching_plan']['goals'].append({**deepcopy(content['teaching_plan']['goals'][0]), 'id': 'G2'})
    content['learning_checks'] = []
    items = applicable_items(content, source, load_content_policy())
    assessments = [i for i in items if i['rule_id'] == 'ASSESSMENT-ALIGNMENT']
    assert [i['subject_id'] for i in assessments] == ['unit:U']
    assert assessments[0]['required']


def test_new_here_without_teaching_link_defers_explanation_to_semantic_review():
    content, source = teaching_case()
    content['teaching_plan']['prerequisites'] = [{'id': 'P', 'dependency_id': 'K', 'treatment': 'new_here'}]
    content['teaching_evidence'] = []
    policy = load_content_policy()
    report = check_content(content, source, policy)
    assert not report.blocking_issues
    assert any(r['rule_id'] == 'CONTENT-PREREQUISITES' for r in report.warnings)
    assert any(i['rule_id'] == 'CONTENT-PREREQUISITES' and i['required']
               for i in applicable_items(content, source, policy))


@pytest.mark.parametrize('basis,refs', [('', []), ('按照本节 n 为非负整数的约定，0 在取值范围中。', [])])
def test_step_explanation_without_separate_basis_binding_is_not_a_machine_error(basis, refs):
    content, source = teaching_case()
    content['explainables'] = [{'id': 'X', 'kind': 'procedure', 'name': '判断取值',
        'markdown': '检查 n 的取值范围。', 'steps': [{'id': 'S',
        'markdown': '本节约定 n 为非负整数，而 0 是非负整数，所以 n 可以取 0。',
        'basis': basis, 'basis_refs': refs}]}]
    report = check_content(content, source, load_content_policy())
    assert not report.blocking_issues
    assert any(r['rule_id'] == 'CONTENT-BASIS' for r in report.warnings)


def test_existing_automatic_quiz_with_wrong_scoring_is_still_blocked():
    content, source = teaching_case()
    check = check_fixture()
    check['scoring']['accepted_inputs'].append('b')
    content['learning_checks'] = [check]
    report = check_content(content, source, load_content_policy())
    assert any(r['rule_id'] == 'CONTENT-LEARNING-CHECKS' and r['issue_key'].startswith('regression_mismatch')
               for r in report.blocking_issues)


def test_partial_teaching_link_does_not_become_harder_than_omitted_link():
    content, source = teaching_case()
    content['teaching_evidence'][0]['claim_refs'] = []
    report = check_content(content, source, load_content_policy())
    assert not report.blocking_issues
    assert any(r['issue_key'] == 'unbound-reader-claims' for r in report.warnings)


def test_step_can_refer_to_existing_visible_correction():
    content, source = teaching_case()
    content['corrections'] = [{'id': 'CORR', 'original': 'n 是正整数', 'corrected': 'n 是非负整数',
                               'reason': '本节约定允许取零。', 'source_refs': [{'kind': 'content', 'claim_id': 'K'}]}]
    content['explainables'] = [{'id': 'X', 'kind': 'procedure', 'name': '检查取值', 'markdown': '使用已说明的约定。',
        'steps': [{'id': 'S', 'markdown': 'n 可以取 0。', 'basis': '勘误已说明取值范围含零。', 'basis_refs': ['CORR']}]}]
    report = check_content(content, source, load_content_policy())
    assert not report.blocking_issues
    content['explainables'][0]['steps'][0]['basis_refs'] = ['MISSING-CORRECTION']
    assert any(r['rule_id'] == 'CONTENT-BASIS' for r in check_content(content, source, load_content_policy()).blocking_issues)
