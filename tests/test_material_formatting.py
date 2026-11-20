"""Independent source materials get a display copy with bounded format repair."""
from copy import deepcopy
import json

import pytest

from aiforstudy.errors import ContractError


class SequenceClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def chat_json(self, messages, **kwargs):
        self.calls.append({'messages': deepcopy(messages), **kwargs})
        assert self.responses, 'unexpected extra provider call'
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return deepcopy(response) if isinstance(response, str) else {'data': deepcopy(response)}


def material():
    return {
        'id': 'material:answers', 'title': '参考答案', 'kind': 'answers',
        'raw_markdown': '# 参考答案\n\n1. $\\frac { 1 } { 2 }$\n\n'
                        '![题图](images/answer-1.png)\n\n2. $a_{l}$（符号模糊）\n',
        'source_lines': [101, 109], 'source_sha256': 'original-source-digest',
    }


def test_display_copy_preserves_source_and_accepts_optional_notes():
    # Replacing raw_markdown with model output would lose the auditable source.
    from aiforstudy.blocks.content.material_formatting import format_material

    source = material()
    original = deepcopy(source)
    display = '# 参考答案\n\n1. $\\frac{1}{2}$\n\n![题图](images/answer-1.png)\n\n2. $a_l$（符号模糊）\n'
    client = SequenceClient({'display_markdown': display})
    result = format_material(source, client=client)

    assert source == original
    assert result == {'display_markdown': display, 'notes': []}
    payload = json.loads(client.calls[0]['messages'][-1]['content'])
    assert payload['material'] == {key: original[key] for key in ('title', 'kind', 'raw_markdown')}
    assert set(payload['output_format']) == {'display_markdown', 'notes'}


def test_json_and_empty_display_are_corrected_with_original_responses_retained(tmp_path):
    # Omitting either correction round or journal forwarding loses recoverability.
    from aiforstudy.blocks.content.material_formatting import format_material

    broken_json = '{"display_markdown": "1. $x=0$" "notes": []}'
    empty_display = {'display_markdown': '  '}
    good = {'display_markdown': '1. $x=0$', 'notes': ['原文下标无法确认，已保留。']}
    journal = tmp_path / 'formatting.json'
    client = SequenceClient(broken_json, empty_display, good)

    assert format_material(material(), client=client, correction_journal=journal) == good
    state = json.loads(journal.read_text())
    assert [row['response'] for row in state['attempts']] == [broken_json, empty_display, good]
    assert [row['status'] for row in state['attempts']] == ['needs_correction', 'needs_correction', 'accepted']
    replay = SequenceClient()
    assert format_material(material(), client=replay, correction_journal=journal) == good
    assert replay.calls == []


def test_format_failures_stop_after_two_corrections_and_replay_without_new_calls(tmp_path):
    # A new retry loop could silently spend more than the approved two corrections.
    from aiforstudy.blocks.content.material_formatting import format_material

    journal = tmp_path / 'failed-formatting.json'
    client = SequenceClient({}, [], {'display_markdown': None}, {'display_markdown': 'unused'})
    with pytest.raises(ContractError, match='display_markdown'):
        format_material(material(), client=client, correction_journal=journal)

    assert len(client.calls) == 3
    assert len(json.loads(journal.read_text())['attempts']) == 3
    replay = SequenceClient()
    with pytest.raises(ContractError, match='display_markdown'):
        format_material(material(), client=replay, correction_journal=journal)
    assert replay.calls == []


def test_format_feedback_never_requests_substantive_review(tmp_path):
    # Generic content feedback would broaden this task into a new math audit.
    from aiforstudy.blocks.content.material_formatting import format_material

    journal = tmp_path / 'feedback.json'
    client = SequenceClient('{', {}, {'display_markdown': '1. $a_l$', 'notes': []})
    format_material(material(), client=client, correction_journal=journal)

    feedback = [json.loads(call['messages'][-1]['content']) for call in client.calls[1:]]
    feedback.append(json.loads(journal.read_text())['merged_feedback'])
    for item in feedback:
        text = json.dumps(item, ensure_ascii=False)
        assert '教学内容仍须审核' not in text
        assert '质量仍须重新审核' not in text
        assert '通过数学验证' not in text
        assert '不得降低教学标准' not in text


def test_transport_failure_preserves_format_scope_without_automatic_resend(tmp_path):
    # A formatter must not add transport retries or route failures into math review.
    from aiforstudy.blocks.content.material_formatting import format_material

    journal = tmp_path / 'transport.json'
    client = SequenceClient(OSError('provider unavailable'), {'display_markdown': 'unused'})
    with pytest.raises(OSError, match='provider unavailable'):
        format_material(material(), client=client, correction_journal=journal)

    assert len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    assert feedback['kind'] == 'transport'
    assert '质量仍须重新审核' not in json.dumps(feedback, ensure_ascii=False)


def test_optional_note_shape_does_not_block_a_consumable_display():
    # Optional diagnostic notes must not become another hard output gate.
    from aiforstudy.blocks.content.material_formatting import format_material

    client = SequenceClient({'display_markdown': '1. $a_l$', 'notes': '下标无法确认，保留原文。'})
    assert format_material(material(), client=client) == {
        'display_markdown': '1. $a_l$', 'notes': ['下标无法确认，保留原文。'],
    }
