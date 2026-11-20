"""修改计划 A2（2026-10-20）：空答复单独成类，带提示重发原请求，占协议额度。"""
import json

import pytest

from aiforstudy.blocks.content.generation_corrections import (EMPTY_RESPONSE_HINT, MAX_EMPTY_RESENDS,
                                                              EmptyResponseExhausted, run_local_generation)
from aiforstudy.pipeline.execution_control import ExecutionControl
from aiforstudy.pipeline.gateway_client import GatewayClient
from aiforstudy.providers.gateway import CallRequest, Gateway
from aiforstudy.providers.response import TransportResponse
from aiforstudy.providers.review_failures import ReviewCallFailure, classify_review_failure

USAGE = {'completion_tokens': 900, 'completion_tokens_details': {'reasoning_tokens': 900}, 'note': 'x'}


def _wire(replies, seen):
    def wire(**kwargs):
        seen.append(kwargs['messages'])
        return TransportResponse(replies.pop(0), USAGE, metadata={'finish_reason': 'stop'})
    return wire


def _gateway(tmp_path, wire):
    control = ExecutionControl()
    control.bind(tmp_path / 'execution.json', run_id='run')
    return Gateway(transport=wire, roles={'author': {'model': 'offline', 'max_tokens': 1000}}, control=control), control


def _client(gateway):
    return GatewayClient(gateway, role='author', model='offline', effort='high', stage='content',
                         unit_key='U1', bucket='content')


def test_gateway_classifies_empty_visible_output_and_keeps_usage(tmp_path):
    seen = []
    gateway, control = _gateway(tmp_path, _wire(['   '], seen))
    result = gateway.call(CallRequest(role='author', messages=[{'role': 'user', 'content': 'x'}], protocol_errors=True))
    assert not result.ok and result.failure_kind == 'empty_response' and len(seen) == 1
    evidence = json.loads(next((tmp_path / 'model-responses').glob('*.json')).read_text(encoding='utf-8'))
    assert evidence['problem'].startswith('empty_response')
    assert evidence['usage'] == {'completion_tokens': 900, 'completion_tokens_details': {'reasoning_tokens': 900}}


def test_gateway_without_protocol_errors_reports_empty_response_not_protocol(tmp_path):
    seen = []
    gateway, _ = _gateway(tmp_path, _wire(['', ''], seen))
    result = gateway.call(CallRequest(role='author', messages=[]))
    assert not result.ok and result.failure_kind == 'empty_response' and len(seen) == 2


@pytest.mark.parametrize('max_corrections', [0, 2])
def test_empty_then_valid_is_accepted_after_one_hinted_resend(tmp_path, max_corrections):
    seen = []
    gateway, _ = _gateway(tmp_path, _wire(['', json.dumps({'body_markdown': '正文'})], seen))
    journal = tmp_path / 'journal.json'
    value = run_local_generation([{'role': 'user', 'content': '写正文'}], _client(gateway), lambda v: v,
                                 tag='content:U1', correction_tag='content-fix:U1',
                                 correction_journal=journal, max_corrections=max_corrections)
    assert value == {'body_markdown': '正文'}
    assert len(seen) == 2
    assert seen[1][0] == {'role': 'user', 'content': '写正文'}          # 原请求原样重发
    assert EMPTY_RESPONSE_HINT in seen[1][-1]['content']
    statuses = [a['status'] for a in json.loads(journal.read_text(encoding='utf-8'))['attempts']]
    assert statuses == ['empty_response', 'accepted']


def test_consecutive_empty_responses_stop_as_empty_response(tmp_path):
    seen = []
    gateway, _ = _gateway(tmp_path, _wire([''] * 10, seen))
    with pytest.raises(EmptyResponseExhausted) as caught:
        run_local_generation([{'role': 'user', 'content': '写正文'}], _client(gateway), lambda v: v,
                             tag='content:U1', correction_tag='content-fix:U1', max_corrections=0)
    assert classify_review_failure(caught.value) == 'empty_response'
    assert len(seen) == 1 + MAX_EMPTY_RESENDS


def test_empty_answer_to_syntax_repair_resends_the_syntax_request(tmp_path):
    seen = []
    gateway, _ = _gateway(tmp_path, _wire(['{"a": 1,}', '', '{"a": 1}'], seen))
    value = run_local_generation([{'role': 'user', 'content': '写正文'}], _client(gateway), lambda v: v,
                                 tag='content:U1', correction_tag='content-fix:U1', max_corrections=0)
    assert value == {'a': 1}
    assert seen[1][0]['role'] == 'system' and seen[2][0] == seen[1][0]   # 仍是语法修复请求
    resent = json.loads(seen[2][1]['content'])
    assert resent['empty_response_notice'] == EMPTY_RESPONSE_HINT
    assert resent['original_response'] == '{"a": 1,}'                     # 底稿没有被空答复覆盖


def test_unparsed_failure_kind_is_no_longer_rewritten_to_internal():
    assert ReviewCallFailure('x', failure_kind='unparsed').failure_kind == 'unparsed'
    assert ReviewCallFailure('x', failure_kind='empty_response').failure_kind == 'empty_response'


def test_syntax_repair_returning_identical_text_gets_unchanged_notice(tmp_path):
    """修改计划 A3：原样返回不加次数、不提前终止，下一轮反馈注明“未改动”。"""
    seen = []
    bad = '{"a": 1,\n "b": 2,}'
    gateway, _ = _gateway(tmp_path, _wire([bad, bad, '{"a": 1, "b": 2}'], seen))
    value = run_local_generation([{'role': 'user', 'content': '写正文'}], _client(gateway), lambda v: v,
                                 tag='content:U1', correction_tag='content-fix:U1', max_corrections=0)
    assert value == {'a': 1, 'b': 2}
    first, second = json.loads(seen[1][1]['content']), json.loads(seen[2][1]['content'])
    assert 'unchanged_notice' not in first
    assert '逐字相同' in second['unchanged_notice'] and '第2行' in second['unchanged_notice']
    assert second['correction_round'] == 2


# ── 修改计划 A4：纠正包装被当正文、空正文被接收

from aiforstudy.blocks.content.generation_corrections import LocalCorrectionRequired  # noqa: E402
from aiforstudy.blocks.content.json_only import normalize_author_output  # noqa: E402
from aiforstudy.errors import ContractError  # noqa: E402

GOOD = json.dumps({'content': {'title': '双曲函数', 'body_markdown': '## 双曲函数\n\n正文'}}, ensure_ascii=False)
BAD = GOOD[:-2] + ',}}'


def _echo_wire(replies, seen):
    """双曲函数 000002 的形态：语法纠正时，模型把纠正请求原样交回。"""
    def wire(**kwargs):
        messages = kwargs['messages']
        seen.append(messages)
        reply = replies.pop(0)
        if reply == 'ECHO':
            reply = messages[-1]['content']
        return TransportResponse(reply, {}, metadata={'finish_reason': 'stop'})
    return wire


def test_echoed_correction_wrapper_is_a_failed_syntax_round_not_a_body(tmp_path):
    seen = []
    gateway, _ = _gateway(tmp_path, _echo_wire([BAD, 'ECHO', GOOD], seen))
    value = run_local_generation([{'role': 'user', 'content': '写正文'}], _client(gateway), normalize_author_output,
                                 tag='content:U1', correction_tag='content-fix:U1', max_corrections=0)
    assert value['content']['body_markdown'].startswith('## 双曲函数')
    third = json.loads(seen[2][-1]['content'])
    assert seen[2][0]['role'] == 'system'                       # 仍是语法纠正，计语法次数
    assert third['errors'][0]['code'] == 'correction_wrapper_returned'
    assert third['original_response'] == BAD                    # 底稿仍是原坏 JSON，不是包装
    assert third['correction_round'] == 2


def test_always_echoing_wrapper_never_becomes_accepted_content(tmp_path):
    seen = []
    gateway, _ = _gateway(tmp_path, _echo_wire([BAD] + ['ECHO'] * 10, seen))
    journal = tmp_path / 'journal.json'
    with pytest.raises(ContractError):
        run_local_generation([{'role': 'user', 'content': '写正文'}], _client(gateway), normalize_author_output,
                             tag='content:U1', correction_tag='content-fix:U1', correction_journal=journal,
                             max_corrections=0)
    statuses = [a['status'] for a in json.loads(journal.read_text(encoding='utf-8'))['attempts']]
    assert 'accepted' not in statuses and len(seen) == 6         # 1 原答复 + 5 次语法纠正


@pytest.mark.parametrize('raw', [
    {'content': {'title': '双曲函数'}},
    {'role_task': 'local_generation_correction', 'original_response': BAD, 'errors': []},
    ['不是对象'],
])
def test_normalize_author_output_rejects_missing_body_as_shape_error(raw):
    with pytest.raises(LocalCorrectionRequired) as caught:
        normalize_author_output(raw)
    assert caught.value.kind == 'shape'


def test_shape_error_gets_protocol_corrections_even_when_caller_budget_is_zero(tmp_path):
    seen = []
    empty_body = json.dumps({'content': {'title': '双曲函数', 'body_markdown': ''}})
    gateway, _ = _gateway(tmp_path, _echo_wire([empty_body, GOOD], seen))
    value = run_local_generation([{'role': 'user', 'content': '写正文'}], _client(gateway), normalize_author_output,
                                 tag='content:U1', correction_tag='content-fix:U1', max_corrections=0)
    assert value['content']['body_markdown']
    assert seen[1][0] == {'role': 'user', 'content': '写正文'}
    assert json.loads(seen[1][-1]['content'])['errors'][0]['code'] == 'empty_body'
