"""JSON-only mode checks wire syntax, including bounded repair and replay."""
import json

import pytest

from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.generation import generate_content
from aiforstudy.core.review_mode import review_mode
from aiforstudy.errors import ContractError
from aiforstudy.pipeline.execution_control import ExecutionControl
from aiforstudy.pipeline.gateway_client import GatewayClient
from aiforstudy.providers.gateway import Gateway, CallRequest
from support.content_case import teaching_case


def wire_client(root, text):
    sent = []
    def transport(**request):
        sent.append(request)
        return text, {}
    control = ExecutionControl()
    control.bind(root/'execution.json', run_id='json-only')
    gateway = Gateway(transport=transport, roles={'author': {'model': 'offline'}},
                      control=control, cache_dir=root/'cache')
    client = GatewayClient(gateway, role='author', model='offline', effort='high',
                           stage='lesson', unit_key='U', bucket='content')
    return gateway, client, sent


@pytest.mark.parametrize('wire, expected', [
    ('"abc"', 'abc'), ('"23"', '23'), ('"null"', 'null'),
])
def test_json_values_survive_gateway_cache_and_generation_replay(tmp_path, wire, expected):
    gateway, client, sent = wire_client(tmp_path, wire)
    journal = tmp_path/'generation.json'
    with review_mode('json_only'):
        first = generate_content(teaching_case()[1], load_content_policy(), client,
                                 correction_journal=journal)
        replayed = generate_content(teaching_case()[1], load_content_policy(), client,
                                    correction_journal=journal)
        # A separate generation journal must consume the gateway's cached wire text.
        cached = generate_content(teaching_case()[1], load_content_policy(), client,
                                  correction_journal=tmp_path/'second.json')
    assert first['unreviewed_response'] == expected
    assert replayed == cached == first
    assert len(sent) == 1


@pytest.mark.parametrize('wire', ['[1, "保留"]', '23', 'true', 'null'])
def test_json_values_without_body_are_shape_errors_and_replay_identically(tmp_path, wire):
    """修改计划 A4（2026-10-20）：合法 JSON 但规范化后正文为空 → 字段形状错误，协议纠正 2 次后不接收。"""
    _, client, sent = wire_client(tmp_path, wire)
    journal = tmp_path/'generation.json'
    with review_mode('json_only'):
        with pytest.raises(ContractError, match='empty_body'):
            generate_content(teaching_case()[1], load_content_policy(), client, correction_journal=journal)
        with pytest.raises(ContractError, match='empty_body'):
            generate_content(teaching_case()[1], load_content_policy(), client, correction_journal=journal)
    assert len(sent) == 3
    assert [a['status'] for a in json.loads(journal.read_text())['attempts']] == ['needs_correction'] * 3


@pytest.mark.parametrize('wire, position', [
    ('{"content":', 11),
    ('NaN', 0), ('Infinity', 0), ('-Infinity', 0),
    ('{"quoted":"NaN", "n":NaN}', 21),
    ('["Infinity", Infinity]', 13),
])
def test_invalid_json_keeps_wire_and_exact_error_location(tmp_path, wire, position):
    _, client, sent = wire_client(tmp_path, wire)
    journal = tmp_path/'generation.json'
    with review_mode('json_only'), pytest.raises(ContractError, match='json_syntax'):
        generate_content(teaching_case()[1], load_content_policy(), client,
                         correction_journal=journal)
    saved = json.loads(journal.read_text())
    assert len(sent) == 6
    assert all(call['effort'] == 'low' for call in sent[1:])
    assert saved['attempts'][0]['response'] == wire
    error = saved['merged_feedback']['errors'][0]
    assert error['code'] == 'json_syntax'
    assert error['position']['char'] == position
    assert '⟪错误位置⟫' in error['context']
    assert not list((tmp_path/'cache').glob('*.json'))


def test_full_mode_keeps_the_gateway_object_contract(tmp_path):
    gateway, _, sent = wire_client(tmp_path, '[1]')
    with review_mode('full'):
        result = gateway.call(CallRequest(role='author', messages=[], protocol_errors=True))
    assert not result.ok and result.failure_kind == 'unparsed'
    assert 'JSON 对象' in result.error
    assert len(sent) == 1
