"""Balanced review inspects a complete unit without reinstating representation gates."""
from copy import deepcopy
import json

import pytest

from aiforstudy.core.review_mode import review_mode
from aiforstudy.blocks.content.review import review_content
from support.content_case import teaching_case


IDS = ('CONTENT-KNOWLEDGE', 'CONTENT-STEPS', 'CONTENT-SELF-CHECKS')


def verdicts(status='pass'):
    return {'verdicts': [{'criterion_id': key, 'version': 1, 'status': status,
                         'reason': '正文定义与条件正确；本节无额外推导或自测要求。',
                         'evidence_refs': []} for key in IDS]}


class Client:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = []

    def chat_json(self, messages, **kwargs):
        self.calls.append((deepcopy(messages), kwargs))
        answer = next(self.answers)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else {'data': deepcopy(answer)}


def review(client, **kwargs):
    content, source = teaching_case()
    content.update(teaching_plan={'claims': [{'id': 'broken', 'source_refs': []}]},
                   math_models=[{'unmodeled': True}])
    with review_mode('balanced'):
        return review_content(content, {}, source, (), None, client, **kwargs).as_dict()


def test_one_complete_unit_call_ignores_extra_fields_and_evidence_ids():
    answer = verdicts()
    answer['extra'] = 'kept in original response'
    answer['verdicts'][0]['evidence_refs'] = [{'evidence_id': 'not-host-bound'}]
    answer['verdicts'].append({'criterion_id': 'not-a-host-id', 'status': 'pass', 'reason': '全文术语与前后条件一致。'})
    client = Client([answer])
    report = review(client)
    assert len(client.calls) == 1
    request = json.loads(client.calls[0][0][1]['content'])['frozen_context']
    assert len(request['criteria']) == 3
    assert request['candidate_content']['body_markdown'] == teaching_case()[0]['body_markdown']
    assert report['decision'] == 'accepted'
    assert report['responses'] == [answer]
    assert report['verdicts'] == answer['verdicts']
    assert '定义和概念不要求推导' in client.calls[0][0][0]['content']
    assert 'output_json_schema' not in json.loads(client.calls[0][0][1]['content'])


def test_unknown_and_pass_rows_are_retained_without_hiding_uncertainty():
    answer = verdicts('unknown')
    answer['verdicts'] = answer['verdicts'][:1] + [dict(answer['verdicts'][0], status='pass')]
    report = review(Client([answer]))
    assert report['verdicts'] == answer['verdicts']
    assert {row['status'] for row in report['verdicts']} == {'unknown', 'pass'}
    assert report['decision'] != 'accepted'
    assert report['blocking_issues'] == []


def test_format_corrections_are_bounded_durable_and_preserve_valid_rows(tmp_path):
    partial = verdicts('fail')
    partial['verdicts'][1] = {'criterion_id': IDS[1], 'status': 'invalid'}
    client = Client([partial, {'verdicts': 'wrong'}, {'verdicts': 'still wrong'}])
    seen = []
    report = review(client, correction_journal=tmp_path / 'review.json', response_sink=seen.append)
    assert len(client.calls) == 3
    assert seen == [partial, {'verdicts': 'wrong'}, {'verdicts': 'still wrong'}]
    assert report['review_failure']['kind'] == 'protocol'
    assert {row['criterion_id'] for row in report['blocking_issues']} == {IDS[0], IDS[2]}
    replay = review(client, correction_journal=tmp_path / 'review.json')
    assert len(client.calls) == 3
    assert replay['verdicts'] == report['verdicts']


def test_invalid_json_is_corrected_without_rewriting_body(tmp_path):
    client = Client(['{"verdicts":', verdicts()])
    report = review(client, correction_journal=tmp_path / 'review.json')
    assert len(client.calls) == 2
    assert report['decision'] == 'accepted'
    feedback = json.loads(client.calls[1][0][-1]['content'])
    assert feedback['original_response'] == '{"verdicts":'
    assert feedback['errors'][0]['path']


def test_transport_failure_remains_actual_error():
    with pytest.raises(RuntimeError, match='provider outage 503'):
        review(Client([RuntimeError('provider outage 503')]))


def test_malformed_status_is_correctable_and_non_string_extra_id_is_ignored():
    answer = verdicts()
    answer['verdicts'][0]['status'] = ['pass']
    answer['verdicts'].append({'criterion_id': []})
    client = Client([answer, verdicts()])
    assert review(client)['decision'] == 'accepted'
    assert len(client.calls) == 2


def test_repeated_same_status_with_different_optional_evidence_does_not_block():
    answer = verdicts()
    answer['verdicts'].append(dict(answer['verdicts'][0], reason='独立检查也确认知识正确。',
                                  evidence_refs=[{'arbitrary': 'location hint'}]))
    report = review(Client([answer]))
    assert report['decision'] == 'accepted'
    assert report['responses'] == [answer]
