"""Real author-protocol regressions; only host policy and local scoring run."""
from copy import deepcopy

import pytest

from aiforstudy.blocks.content.content_policy import check_content, load_content_policy
from aiforstudy.blocks.content.learning_checks import validate_learning_checks
from aiforstudy.blocks.content.prompt import learning_check_examples
from support.content_case import teaching_case
from test_learning_checks import content_fixture


def concept_case(unit='PREVIOUS'):
    content, source = teaching_case()
    text = '整数包含零。'
    source['reference_catalog']['entries'] = [dict(id='C-PRIOR', kind='CONCEPT',
        label='整数', version='frozen-version', unit_id=unit, markdown=text)]
    content['prerequisite_recaps'] = [dict(id='R-PRIOR', title='整数', markdown=text,
        source_refs=[dict(kind='concept', id='C-PRIOR', concept_id='C-PRIOR', unit_id=unit)])]
    return content, source


def source_errors(content, source):
    report = check_content(content, source, load_content_policy(source['learner_profile']))
    return [issue for issue in report.blocking_issues if issue['issue_key'].startswith('invalid-source:')]


@pytest.mark.parametrize('unit', ['U', 'PREVIOUS'])
def test_frozen_concept_source_can_reference_its_actual_unit(unit):
    content, source = concept_case(unit)
    assert source_errors(content, source) == []


@pytest.mark.parametrize('identity_field', ['id', 'concept_id'])
def test_single_known_concept_identity_and_exact_catalog_quote_are_sufficient(identity_field):
    content, source = concept_case()
    content['prerequisite_recaps'][0]['source_refs'] = [dict(kind='concept',
        **{identity_field: 'C-PRIOR'}, quote=source['reference_catalog']['entries'][0]['markdown'])]
    assert source_errors(content, source) == []


@pytest.mark.parametrize('damage', ['unknown', 'wrong_unit', 'conflicting_id'])
def test_concept_source_cannot_invent_identity_or_location(damage):
    content, source = concept_case()
    ref = content['prerequisite_recaps'][0]['source_refs'][0]
    if damage == 'unknown': ref['id'] = ref['concept_id'] = 'C-UNKNOWN'
    elif damage == 'wrong_unit': ref['unit_id'] = 'OTHER'
    elif damage == 'conflicting_id': ref['id'] = 'C-OTHER'
    assert source_errors(content, source)


@pytest.mark.parametrize('kind', ['textbook', 'concept'])
def test_known_source_identity_does_not_require_identical_quote_wording(kind):
    if kind == 'concept':
        content, source = concept_case()
        ref = content['prerequisite_recaps'][0]['source_refs'][0]
        ref['quote'] = '零也是整数。'
    else:
        content, source = teaching_case()
        ref = content['teaching_plan']['claims'][0]['source_refs'][0]
        ref['quote'] = '这里的 n 允许等于零。'
    assert source_errors(content, source) == []
    report = check_content(content, source, load_content_policy(source['learner_profile']))
    fidelity = [r for r in report.rule_results if r['rule_id'] == 'SOURCE-FIDELITY']
    assert fidelity and all(r['status'] == 'not_run' for r in fidelity)


@pytest.mark.parametrize('field', ['markdown', 'unit_id'])
def test_empty_frozen_concept_content_or_location_does_not_ground_a_reference(field):
    content, source = concept_case()
    source['reference_catalog']['entries'][0][field] = ''
    assert source_errors(content, source)


def test_catalog_concept_can_use_only_its_frozen_file_provenance():
    content, source = concept_case()
    provenance = dict(unit_id='PREVIOUS', path='previous.md', sha256='prior-source-sha', start=0, end=6)
    source['concept_index'] = {'concepts': [dict(id='C-PRIOR', source_refs=[provenance])]}
    ref = content['prerequisite_recaps'][0]['source_refs'][0]
    ref.update(provenance)
    assert source_errors(content, source) == []
    ref['sha256'] = source['original_sha256']
    assert source_errors(content, source)


@pytest.mark.parametrize('field,value', [('path', 'unknown.md'), ('sha256', 'unknown-sha'),
    ('source_line', 0), ('obligation_id', 'SO'), ('claim_id', 'K')])
def test_catalog_identity_does_not_authorize_unproven_extra_provenance(field, value):
    content, source = concept_case()
    content['prerequisite_recaps'][0]['source_refs'][0][field] = value
    assert source_errors(content, source)


@pytest.mark.parametrize('field,value', [('unit_id', 'PREVIOUS'), ('sha256', 'prior-source-sha')])
def test_nonconcept_sources_still_require_current_unit_identity(field, value):
    content, source = teaching_case()
    content['teaching_plan']['claims'][0]['source_refs'][0][field] = value
    assert source_errors(content, source)


@pytest.mark.parametrize('feedback', [None, ''])
def test_unspecified_feedback_does_not_forbid_correct_misconception_branch(feedback):
    content = content_fixture('boolean')
    case = content['learning_checks'][0]['test_cases'][1]
    if feedback is None:
        case.pop('feedback')
    else:
        case['feedback'] = feedback
    assert validate_learning_checks(content) == []


@pytest.mark.parametrize('damage', ['wrong_status', 'wrong_feedback'])
def test_explicit_wrong_score_or_feedback_is_still_rejected(damage):
    content = content_fixture('boolean')
    case = content['learning_checks'][0]['test_cases'][1]
    case['expected' if damage == 'wrong_status' else 'feedback'] = 'correct' if damage == 'wrong_status' else 'OTHER'
    assert 'regression_mismatch' in {row['code'] for row in validate_learning_checks(content)}


def test_generated_learning_check_example_agrees_with_local_scoring():
    content, _ = teaching_case()
    check = deepcopy(learning_check_examples()[0])
    check['goal_refs'] = ['G']
    check['source_refs'] = [{'kind': 'content', 'claim_id': 'K'}]
    content['learning_checks'] = [check]
    assert validate_learning_checks(content) == []
