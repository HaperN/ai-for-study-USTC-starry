"""Syntax-only corrections keep their own durable budget and wire scope."""
from copy import deepcopy
import json

import pytest

from aiforstudy.blocks.content.generation_corrections import LocalCorrectionRequired, run_local_generation
from aiforstudy.core.review_mode import review_mode
from aiforstudy.errors import ContractError
from aiforstudy.pipeline.execution_control import ExecutionControl
from aiforstudy.pipeline.gateway_client import GatewayClient
from aiforstudy.providers.gateway import Gateway


BASE = [{'role': 'system', 'content': 'AUTHOR_TASK_MUST_NOT_BE_REPEATED'},
        {'role': 'user', 'content': 'TEXTBOOK_MUST_NOT_BE_REPEATED'}]
BAD = '{"body":"保留数学 $x^2$"}}'


def client_for(root, responses):
    calls = []
    pending = iter(responses)
    def transport(**request):
        calls.append(deepcopy(request))
        return next(pending), {}
    control = ExecutionControl()
    control.bind(root/'execution.json', run_id='syntax-policy')
    gateway = Gateway(transport=transport, roles={'judge': {
        'model': 'offline', 'effort': 'high', 'max_tokens': 200000}},
        control=control, cache_dir=root/'cache')
    client = GatewayClient(gateway, role='judge', model='offline', effort='high',
                           stage='text_audit', unit_key='U', bucket='content')
    return client, calls


def run(client, path, *, validator=lambda value: value, limit=0):
    return run_local_generation(BASE, client, validator, tag='author:U',
        correction_tag='correction:U', correction_journal=path,
        max_corrections=limit, correction_guard=False)


def test_syntax_repair_drops_author_context_and_uses_low_on_wire(tmp_path):
    client, calls = client_for(tmp_path, [BAD, '{"body":"保留数学 $x^2$"}'])
    with review_mode('balanced'):
        result = run(client, tmp_path/'journal.json')
    assert result == {'body': '保留数学 $x^2$'}
    assert len(calls) == 2
    assert calls[0]['effort'] == 'high'
    repaired = calls[1]
    assert repaired['effort'] == 'low' and repaired['max_tokens'] == 200000
    encoded = json.dumps(repaired['messages'], ensure_ascii=False)
    assert 'AUTHOR_TASK_MUST_NOT_BE_REPEATED' not in encoded
    assert 'TEXTBOOK_MUST_NOT_BE_REPEATED' not in encoded
    payload = json.loads(repaired['messages'][-1]['content'])
    assert payload['original_response'] == BAD
    error = payload['errors'][0]
    assert error['position']['char'] == len(BAD)-1
    assert '⟪错误位置⟫}' in error['context']
    assert 'U+007D' in error['fix'] and '删除' in error['fix']


def test_fifth_syntax_correction_can_succeed_without_non_syntax_allowance(tmp_path):
    client, calls = client_for(tmp_path, [BAD]*5 + ['{"ok":true}'])
    with review_mode('balanced'):
        assert run(client, tmp_path/'journal.json') == {'ok': True}
    assert len(calls) == 6


def test_syntax_exhaustion_and_replay_do_not_get_sixth_correction(tmp_path):
    path = tmp_path/'journal.json'
    client, calls = client_for(tmp_path, [BAD]*6)
    with review_mode('balanced'), pytest.raises(ContractError, match='json_syntax'):
        run(client, path, limit=2)
    assert len(calls) == 6
    replay, replay_calls = client_for(tmp_path, [])
    with review_mode('balanced'), pytest.raises(ContractError, match='json_syntax'):
        run(replay, path, limit=2)
    assert replay_calls == []
    assert len(json.loads(path.read_text())['attempts']) == 6


def test_syntax_budget_survives_restart_between_completed_attempts(tmp_path):
    path = tmp_path/'journal.json'
    client, calls = client_for(tmp_path, [BAD]*6)
    saved = []
    def stop_after_third(raw):
        saved.append(raw)
        if len(saved) == 3:
            raise RuntimeError('offline process interruption after durable response')
    with review_mode('balanced'), pytest.raises(RuntimeError, match='offline process'):
        run_local_generation(BASE, client, lambda value: value, tag='author:U',
            correction_tag='correction:U', correction_journal=path, max_corrections=0,
            response_sink=stop_after_third)
    assert len(calls) == 3
    resumed, resumed_calls = client_for(tmp_path, [BAD]*3)
    with review_mode('balanced'), pytest.raises(ContractError, match='json_syntax'):
        run(resumed, path)
    assert len(resumed_calls) == 3


def needs_field(value):
    if not value.get('ok'):
        raise LocalCorrectionRequired('missing ok', errors=[{'path':'/ok', 'code':'missing_field',
            'fix':'补齐 ok'}], allowed_paths=['/ok'], kind='schema')
    return value


def test_schema_budget_stays_two_when_syntax_errors_are_interleaved(tmp_path):
    # Initial schema error, schema correction 1 -> bad JSON, syntax repair 1 ->
    # schema error, schema correction 2 -> bad JSON, syntax repair 2 -> schema error.
    client, calls = client_for(tmp_path, ['{}', BAD, '{}', BAD, '{}'])
    with review_mode('balanced'), pytest.raises(ContractError, match='missing_field'):
        run(client, tmp_path/'journal.json', validator=needs_field, limit=2)
    assert len(calls) == 5
    assert [c['effort'] for c in calls] == ['high', 'high', 'low', 'high', 'low']
    assert calls[1]['messages'][:2] == BASE and calls[3]['messages'][:2] == BASE


def test_zero_schema_allowance_is_not_expanded_by_five_syntax_rounds(tmp_path):
    client, calls = client_for(tmp_path, ['{}'])
    with review_mode('balanced'), pytest.raises(ContractError, match='missing_field'):
        run(client, tmp_path/'journal.json', validator=needs_field)
    assert len(calls) == 1


def test_five_syntax_repairs_leave_only_the_original_two_schema_repairs(tmp_path):
    client, calls = client_for(tmp_path, ['{}'] + [BAD] * 5 + ['{}', '{"ok":true}'])
    with review_mode('balanced'):
        assert run(client, tmp_path/'journal.json', validator=needs_field, limit=2) == {'ok': True}
    assert len(calls) == 8
    assert [c['effort'] for c in calls] == ['high', 'high'] + ['low'] * 5 + ['high']


def test_client_with_only_protocol_errors_option_does_not_get_unknown_keyword(tmp_path):
    class ProtocolOnlyClient:
        def __init__(self):
            self.calls = 0
        def chat_json(self, messages, *, tag, protocol_errors=False):
            assert protocol_errors
            self.calls += 1
            return BAD if self.calls == 1 else {'data': {'ok': True}}
    client = ProtocolOnlyClient()
    with review_mode('balanced'):
        assert run(client, tmp_path/'journal.json') == {'ok': True}
    assert client.calls == 2


def test_legacy_journal_does_not_gain_five_new_attempts_on_resume(tmp_path):
    from aiforstudy.blocks.content.content_contracts import content_digest
    path = tmp_path/'legacy.json'
    path.write_text(json.dumps({'schema_version':'GenerationCorrectionsV1',
        'input_hash': content_digest({'messages':BASE, 'tag':'author:U', 'max_corrections':2}),
        'max_corrections':2, 'attempts': [
            {'index':i, 'status':'received', 'response':BAD,
             'response_hash':content_digest({'response':BAD})} for i in range(3)]}))
    client, calls = client_for(tmp_path, [])
    with review_mode('balanced'), pytest.raises(ContractError, match='json_syntax'):
        run(client, path, limit=2)
    assert calls == []
    assert 'max_syntax_corrections' not in json.loads(path.read_text())


def test_source_formatter_gets_five_syntax_repairs_without_more_format_repairs(tmp_path):
    from aiforstudy.blocks.content.material_formatting import format_material
    client, calls = client_for(tmp_path, ['{"display_markdown":"x"}}']*5 +
                               ['{"display_markdown":"x"}'])
    with review_mode('balanced'):
        assert format_material({'id':'answers', 'raw_markdown':'x'}, client=client,
            correction_journal=tmp_path/'journal.json')['display_markdown'] == 'x'
    assert len(calls) == 6
    assert all(c['effort'] == 'low' for c in calls[1:])


def test_explicit_syntax_request_discards_images_before_fingerprinting(tmp_path):
    client, calls = client_for(tmp_path, ['{"ok":true}', '{"ok":true}'])
    # Models the existing media wrappers, which attach images on every request.
    with review_mode('balanced'):
        assert client.chat_json(BASE, tag='syntax', syntax_repair=True,
            images=[str(tmp_path/'image-not-needed-for-syntax.png')])['data'] == {'ok': True}
        client.chat_json(BASE, tag='normal')
    assert calls[0]['images'] is None and calls[0]['effort'] == 'low'
    assert calls[1]['effort'] == 'high'


@pytest.mark.parametrize('text, expected', [
    ('{"a": 1) }', ')'),
    ('{"a":"hello"world"}', '引号'),
    ('{"a":"\\q"}', '转义'),
    ('{"a":1 "b":2}', '逗号'),
])
def test_diagnostics_suggest_the_relevant_syntax_change(tmp_path, text, expected):
    client, calls = client_for(tmp_path, [text, '{"ok":true}'])
    with review_mode('balanced'):
        run(client, tmp_path/'journal.json')
    error = json.loads(calls[1]['messages'][-1]['content'])['errors'][0]
    assert expected in error['fix']
