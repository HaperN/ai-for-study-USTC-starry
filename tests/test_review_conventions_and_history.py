"""Offline transport/routing tests, not proof of an LLM's semantic judgement."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.prompt import build_content_messages
from aiforstudy.core.review_mode import review_mode
from aiforstudy.pipeline import content_stages as stage, stage_io
from support.content_case import teaching_case
from test_balanced_content_review import Client
from test_content_generation_v3 import context


@pytest.mark.parametrize('role', ['author', 'repair', 'reviewer'])
def test_production_messages_distinguish_conventions_from_actual_errors(role):
    content, source = teaching_case()
    source['source_text'] = '本书明确约定符号 a 的适用范围，当前用法保持自洽。'
    request = {'source_context': source, 'content': content}
    before = deepcopy(request)
    with review_mode('balanced'):
        messages = build_content_messages(role, request, load_content_policy())
    assert request == before
    system = messages[0]['content']
    for rule in ('教材明确约定', '具体矛盾、反例或推导问题', '不同于模型常用约定',
                 '不擅自改正文', '公式结束符外', '公共概念', 'corrections'):
        assert rule in system
    if role == 'reviewer':
        assert '首次审核尽量集中指出' in system
        assert '改变上一轮判断' in system
        assert 'previous_verdicts' in system
        assert '新增裁决模型' not in system


def test_rereview_receives_prior_suggestion_and_failure_without_an_extra_call(tmp_path, monkeypatch):
    draft = {'content': {'title': '记号约定', 'body_markdown': '本节 n 为正整数，不包括零。'}}
    fixed = {'content': {'title': '记号约定', 'body_markdown': '本节 n 为非负整数，包括零。'}}
    previous = [
        {'status': 'fail', 'reason': '正文把非负写为正，漏掉零；应恢复包括零。'},
        {'status': 'pass', 'reason': '建议：可以另加数轴类比，已有条件完整时不必扩展。'},
    ]
    client = Client([draft, {'verdicts': previous}, fixed,
                     {'verdicts': [{'status': 'pass', 'reason': '离线判词：已恢复零；此前建议不要求返修。'}]}])
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *a, **k: (client, 'offline'))
    with review_mode('balanced'):
        assert stage.lesson_stage(context(tmp_path)).execution_state.value == 'succeeded'
        first = stage.text_audit_stage(context(tmp_path, 'text_audit'))
        assert len(first.repair_requests) == 1
        old_content = stage.load_content(context(tmp_path))[0]
        repair = context(tmp_path)
        repair.repair_batch = SimpleNamespace(batch_id='stable-review', round=1,
            jobs=[SimpleNamespace(owner='lesson', kind='text_content',
                                  ticket_ids=first.repair_requests[0].ticket_ids, artifact_id='lesson:U')])
        assert stage.lesson_stage(repair).execution_state.value == 'succeeded'
        assert stage.text_audit_stage(context(tmp_path, 'text_audit')).quality_state.value == 'accepted'
    assert [call[1]['tag'].split(':')[0] for call in client.calls] == [
        'content-author', 'content-review', 'content-repair', 'content-review']
    frozen = json.loads(client.calls[-1][0][1]['content'])['frozen_context']
    history = frozen['repair_context']
    assert history['previous_verdicts'] == previous
    from aiforstudy.blocks.content.content_contracts import content_digest
    assert history['previous_content_hash'] == content_digest(old_content)
    assert frozen['candidate_content']['body_markdown'] == fixed['content']['body_markdown']
    assert 'candidate_content' not in history and 'content' not in history
    assert len(frozen['criteria']) == 3
    # The immutable response remains historical evidence, not a rewritten pass.
    receipt = json.loads((tmp_path/'work/products/content/U/review_responses/000001.json').read_text())
    assert receipt['response']['verdicts'] == previous
