"""修改计划 A5/A6（2026-10-20）：失败类别如实记录；格式纠正不能改变实质判词。"""
import json

import pytest

from aiforstudy.blocks.content.generation_corrections import run_local_generation
from aiforstudy.blocks.content.review import review_content
from aiforstudy.core.review_mode import review_mode
from aiforstudy.pipeline.gateway_client import ModelCallFailed
from support.content_case import teaching_case


class Raising:
    def __init__(self, exc):
        self.exc = exc

    def chat_json(self, messages, **kwargs):
        raise self.exc


@pytest.mark.parametrize('kind, status, feedback', [
    ('network', 'transport_failed', 'transport'),
    ('timeout', 'transport_failed', 'transport'),
    ('truncation', 'truncation_failed', 'capacity'),
    ('empty_response', 'empty_response_failed', 'empty_response'),
    ('configuration', 'configuration_failed', 'configuration'),
])
def test_failed_call_records_its_actual_category(tmp_path, kind, status, feedback):
    journal = tmp_path / 'journal.json'
    with pytest.raises(ModelCallFailed):
        run_local_generation([{'role': 'user', 'content': 'x'}], Raising(ModelCallFailed('失败', failure_kind=kind)),
                             lambda v: v, tag='t', correction_tag='c', correction_journal=journal)
    saved = json.loads(journal.read_text(encoding='utf-8'))
    assert saved['attempts'][0]['status'] == status
    assert saved['attempts'][0]['failure_kind'] == kind
    assert saved['merged_feedback']['kind'] == feedback


class Replies:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def chat_json(self, messages, **kwargs):
        self.calls.append(messages)
        return {'data': self.replies.pop(0)}


PASS = {'status': 'pass', 'reason': '已核对本节，讲解与条件完整。'}
FAIL_NEW = {'status': 'fail', 'reason': '新增要求：算术根须加括号。'}


def _run(replies, tmp_path):
    content, source = teaching_case()
    client = Replies(replies)
    with review_mode('balanced'):
        report = review_content(content, {}, source, (), None, client,
                                correction_journal=tmp_path / 'review.json').as_dict()
    return report, client


def test_bare_verdict_array_is_wrapped_locally_without_a_correction_call(tmp_path):
    """CH01-S01-SS04 000005 的形态：裸数组、判 pass → 直接 pass，0 次纠正调用。"""
    report, client = _run([[PASS]], tmp_path)
    assert len(client.calls) == 1
    assert report['verdicts'] == [PASS] and report['decision'] == 'accepted'


def test_format_correction_cannot_flip_a_readable_verdict(tmp_path):
    """纠正中把 pass 改成 fail、或新增判词 → 保留原判，留下漂移记录。"""
    broken = {'verdicts': [PASS, {'status': 'pass'}]}
    fixed_bad_row = {'status': 'pass', 'reason': '例题步骤完整。'}
    corrected = {'verdicts': [dict(FAIL_NEW), fixed_bad_row, dict(FAIL_NEW, reason='另一条新增问题。')]}
    report, client = _run([broken, corrected], tmp_path)
    assert len(client.calls) == 2
    assert report['verdicts'] == [PASS, fixed_bad_row]
    assert report['decision'] == 'accepted'
    drift = report['format_correction_drift']
    assert [row['index'] for row in drift] == [0, 2]
    assert drift[0]['before'] == PASS and drift[0]['after'] == FAIL_NEW
    assert any('漂移' in w for w in report['warnings'])


def test_unreadable_first_answer_lets_correction_supply_the_verdicts(tmp_path):
    report, client = _run([{'verdict': 'pass'}, {'verdicts': [FAIL_NEW]}], tmp_path)
    assert report['verdicts'] == [FAIL_NEW] and report['format_correction_drift'] == []


def test_bad_row_omitted_by_correction_is_withdrawn_not_requested_again(tmp_path):
    report, client = _run([{'verdicts': [{'status': ['pass']}, {'criterion_id': []}]}, {'verdicts': [PASS]}], tmp_path)
    assert len(client.calls) == 2 and report['verdicts'] == [PASS] and report['decision'] == 'accepted'
