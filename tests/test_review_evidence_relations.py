"""Current evidence can support another subject without a metadata backlink."""
from copy import deepcopy
import json

import pytest

from aiforstudy.blocks.content.content_contracts import content_digest
from aiforstudy.blocks.content.reader_document import project_reader
from aiforstudy.blocks.content.review import ReviewProtocolError, review_content, validate_verdicts
from test_feedback import review_case
from test_teaching_review_evidence import evidence, item, verdict


@pytest.mark.parametrize('kind', ['source', 'math_check'])
@pytest.mark.parametrize('status', ['pass', 'fail', 'unknown'])
def test_current_cross_subject_evidence_retains_the_semantic_verdict(kind, status):
    entry = {'kind': kind, 'content_hash': 'sha256:current',
             'subject_ids': ['inverse'], 'status': 'pass', 'text': 'For x != 0, x/x = 1.'}
    ref = {'evidence_id': 'current:inverse'}
    if kind == 'source':
        ref['quote'] = entry['text']
    row = verdict(status=status, evidence_refs=[ref])
    before = deepcopy(row)

    report = validate_verdicts([item(subject_id='example-1')],
                              {'verdicts': [row]}, {'current:inverse': entry})

    assert report.verdicts == [before]
    assert report.decision == ('accepted' if status == 'pass' else 'needs_review')
    assert row == before


@pytest.mark.parametrize('kind', ['reader', 'source'])
def test_quote_correction_can_locate_current_evidence_for_another_subject(kind):
    catalog = evidence()
    eid = 'reader:step' if kind == 'reader' else 'source:T1'
    catalog[eid]['subject_ids'] = ['OTHER']
    row = verdict(evidence_refs=[{'evidence_id': eid, 'quote': []}])

    with pytest.raises(ReviewProtocolError) as caught:
        validate_verdicts([item()], {'verdicts': [row]}, catalog)

    error = next(e for e in caught.value.errors if e['code'] == 'invalid_evidence_quote')
    assert eid in error['allowed_evidence_ids']
    assert error['candidates'] == [{'evidence_id': eid, 'quote': catalog[eid]['text']}]


@pytest.mark.parametrize('kind', ['reader', 'source'])
@pytest.mark.parametrize('quote_fields', [{}, {'quote': ''}, {'quote': '  '}])
@pytest.mark.parametrize('status', ['pass', 'fail'])
def test_reason_can_supply_the_basis_without_repeating_a_text_quote(kind, quote_fields, status):
    eid = 'reader:step' if kind == 'reader' else 'source:T1'
    row = verdict(status=status, evidence_refs=[{'evidence_id': eid, **quote_fields}])
    before = deepcopy(row)

    report = validate_verdicts([item()], {'verdicts': [row]}, evidence())

    assert report.verdicts == [before]
    assert report.decision == ('accepted' if status == 'pass' else 'needs_review')
    assert row == before


@pytest.mark.parametrize('status', ['pass', 'fail'])
def test_saved_evidence_kind_requirements_cannot_force_optional_citations(status):
    row = verdict(status=status, evidence_refs=[])
    criterion = item(required_evidence_kinds=['reader', 'source', 'absence', 'math_check'])

    report = validate_verdicts([criterion], {'verdicts': [row]}, evidence())

    assert report.verdicts == [row]
    assert report.decision == ('accepted' if status == 'pass' else 'needs_review')


@pytest.mark.parametrize('bad_quote', [None, 1, False, [], {}])
def test_explicit_nonstring_quote_is_still_a_protocol_error(bad_quote):
    row = verdict(evidence_refs=[{'evidence_id': 'source:T1', 'quote': bad_quote}])

    with pytest.raises(ReviewProtocolError) as caught:
        validate_verdicts([item()], {'verdicts': [row]}, evidence())

    assert {error['code'] for error in caught.value.errors} == {'invalid_evidence_quote'}


@pytest.mark.parametrize('kind', ['reader', 'source', 'math_check', 'absence'])
@pytest.mark.parametrize('status', ['pass', 'fail'])
def test_repeated_optional_references_preserve_the_original_verdict(kind, status):
    entry = {'kind': kind, 'content_hash': 'sha256:current', 'subject_ids': ['T1'],
             'status': 'pass', 'text': 'For x != 0, x/x = 1.',
             'parent_pointer': '/teaching_plan/claims', 'source_id': 'T1'}
    ref = {'evidence_id': 'current:inverse'}
    if kind in {'reader', 'source'}:
        ref['quote'] = entry['text']
    row = verdict(status=status, evidence_refs=[ref, deepcopy(ref)])
    before = deepcopy(row)

    report = validate_verdicts([item()], {'verdicts': [row]}, {'current:inverse': entry})

    assert report.verdicts == [before]
    assert report.decision == ('accepted' if status == 'pass' else 'needs_review')
    assert row == before


@pytest.mark.parametrize(('damage', 'expected_error'), [
    ('stale', 'stale_evidence'), ('missing', 'unknown_evidence_id'),
    ('math_fail', 'math_evidence_cannot_pass')])
def test_cross_subject_references_still_require_current_identity_and_honest_math(damage, expected_error):
    entry = {'kind': 'math_check', 'content_hash': 'sha256:current',
             'subject_ids': ['inverse'], 'status': 'pass'}
    catalog = {'math:M:inverse': entry}
    if damage == 'stale':
        entry['content_hash'] = 'sha256:old'
    elif damage == 'missing':
        catalog = {}
    else:
        entry['status'] = 'fail'
    row = verdict(evidence_refs=[{'evidence_id': 'math:M:inverse'}])

    with pytest.raises(ReviewProtocolError) as caught:
        validate_verdicts([item(subject_id='example-1')], {'verdicts': [row]}, catalog)

    assert {error['code'] for error in caught.value.errors} == {expected_error}


def test_each_criterion_can_access_source_and_math_evidence_from_the_current_content():
    content, source, _, criterion, _, _ = review_case()
    criterion = {**criterion, 'subject_id': 'G'}
    math_report = {'input_hash': content_digest(content), 'checks': [
        {'model_id': 'M', 'relation_id': 'inverse', 'claim_id': 'K', 'status': 'pass'}]}

    class Reviewer:
        def chat_json(self, messages, **kwargs):
            self.request = json.loads(messages[1]['content'])['frozen_context']
            refs = []
            for eid, entry in self.request['evidence_catalog'].items():
                if entry['kind'] == 'source' and entry['subject_ids'] == ['unit:U']:
                    refs.append({'evidence_id': eid, 'quote': entry['text']})
                elif entry['kind'] == 'math_check':
                    refs.append({'evidence_id': eid})
            return {'data': {'verdicts': [{
                'criterion_id': criterion['criterion_id'], 'version': criterion['version'],
                'status': 'pass', 'reason': 'Current candidate preserves the mathematical condition.',
                'evidence_refs': refs}]}}

    client = Reviewer()
    report = review_content(content, project_reader(content, source['reference_catalog']),
                            source, [criterion], math_report, client)
    aliases = report.exchanges[0]['evidence_aliases']
    allowed = client.request['criterion_evidence_ids'][criterion['criterion_id']]

    assert 'source:unit:U' in aliases.values()
    assert 'math:M:inverse' in aliases.values()
    assert {aliases[alias] for alias in allowed} >= {'source:unit:U', 'math:M:inverse'}
    assert {ref['evidence_id'] for ref in report.verdicts[0]['evidence_refs']} == {
        'source:unit:U', 'math:M:inverse'}


def test_partial_review_preserves_cross_subject_failure_when_another_row_has_bad_id():
    content, source, _, criterion, _, _ = review_case()
    first = {**criterion, 'subject_id': 'G'}
    second = {**first, 'criterion_id': 'second'}

    class Reviewer:
        def chat_json(self, messages, **kwargs):
            request = json.loads(messages[1]['content'])['frozen_context']
            eid, entry = next((eid, entry) for eid, entry in request['evidence_catalog'].items()
                              if entry['kind'] == 'source' and entry['subject_ids'] == ['unit:U'])
            return {'data': {'verdicts': [
                {'criterion_id': first['criterion_id'], 'version': first['version'],
                 'status': 'fail', 'reason': 'The source condition was omitted.',
                 'evidence_refs': [{'evidence_id': eid, 'quote': entry['text']}]},
                {'criterion_id': second['criterion_id'], 'version': second['version'],
                 'status': 'pass', 'reason': 'Another review result.',
                 'evidence_refs': [{'evidence_id': 'E9999', 'quote': 'Invalid ID'}]}]}}

    with pytest.raises(ReviewProtocolError) as caught:
        review_content(content, project_reader(content, source['reference_catalog']),
                       source, [first, second], None, Reviewer())

    report = caught.value.partial_report
    assert [row['status'] for row in report['verdicts']] == ['fail', 'unknown']
    assert report['verdicts'][0]['evidence_refs'][0]['evidence_id'] == 'source:unit:U'
    assert report['unresolved_criteria'] == ['second']
