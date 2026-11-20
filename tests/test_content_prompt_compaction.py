"""Model-only source compaction keeps real content, identities and exact locations."""
from copy import deepcopy
import json

import pytest

from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.generation import generate_content
from aiforstudy.blocks.content.prompt import build_content_messages
from aiforstudy.blocks.content.prompt_context import compact_content_context
from aiforstudy.blocks.content.review import review_content
from aiforstudy.blocks.content.source_context import SourceContext
from aiforstudy.core.review_mode import review_mode
from aiforstudy.pipeline import content_stages as stages
from support.content_case import teaching_case, authoring
from support.prompt_context import expand_prompt_material as expand
from test_balanced_content_review import Client, verdicts
from test_content_generation_v3 import context as stage_context


def rich_source():
    content, source = teaching_case()
    source['source_text'] += '\n' + ('原文必须保留的条件与完整说明。' * 30)
    source['source_text'] += '\n此处依赖相关定义。[[CONCEPT:C-RELATED|相关定义]]'
    own = '本节完整定义与条件。' * 30
    related = '实际引用定义的完整条件。' * 30
    source['concept_index'] = {'concepts': [
        {'id': 'C-HERE', 'name': '本节定义', 'type': 'definition', 'first_unit': 'U', 'formal': own},
        {'id': 'C-RELATED', 'name': '相关定义', 'type': 'definition', 'first_unit': 'P', 'formal': related},
        {'id': 'C-UNUSED', 'name': '无关概念', 'type': 'definition', 'first_unit': 'L', 'formal': '无关详细资料不应重复传入' * 30},
        {'id': 'T-EARLIER', 'name': '前文定理', 'type': 'theorem', 'first_unit': 'P', 'formal': '此前完整证明不可恢复' * 30},
        {'id': 'T-NUMBER-ONE', 'name': '定理1', 'type': 'theorem', 'first_unit': 'P', 'formal': '旧定理1证明'},
        {'id': 'T-NUMBER-TWO', 'name': '定理1', 'type': 'theorem', 'first_unit': 'Q', 'formal': '另章定理1证明'},
    ]}
    source['reference_catalog'] = {'entries': [
        {'id': row['id'], 'kind': 'CONCEPT', 'label': row['name'], 'version': 'frozen-version',
         'unit_id': row['first_unit'], 'markdown': row['formal']}
        for row in source['concept_index']['concepts']]}
    source['source_examples'] = [{'id': 'E1', 'label': '例1', 'statement': source['source_text'],
                                  'book_solution': own, 'source_line': 1}]
    source['source_obligations'][0].update(source_excerpt=source['source_text'],
        source_refs=[{'kind': 'textbook', 'quote': source['source_text'], 'start': 0,
                      'end': len(source['source_text']), 'unit_id': 'U', 'path': 'U.md', 'sha256': 'sha'}])
    source['source_obligations'].append({**deepcopy(source['source_obligations'][0]), 'id': 'SO-2'})
    content['body_markdown'] += ' [[CONCEPT:T-EARLIER|前文定理]] 当前应用仍应核对非零条件。'
    return content, source


@pytest.mark.parametrize('mode', ['balanced', 'full'])
@pytest.mark.parametrize('role', ['author', 'reviewer', 'repair'])
def test_compact_input_retains_whole_source_candidate_ids_and_exact_evidence(mode, role):
    content, source = rich_source()
    frozen = source if role == 'author' else {'source_context': source,
        'candidate_content' if role == 'reviewer' else 'content': content,
        'available_theorem_names': ['前文定理']}
    before = deepcopy(frozen)
    with review_mode(mode):
        messages = build_content_messages(role, frozen, load_content_policy())
    payload = json.loads(messages[1]['content'])
    view = payload['frozen_context'] if role == 'author' else payload['frozen_context']['source_context']
    assert frozen == before
    assert expand(view['source_text'], payload) == source['source_text']
    assert view['original_sha256'] == source['original_sha256']
    assert view['source_inputs'] == source['source_inputs']
    expanded = expand(view, payload)
    for key in ('source_examples', 'source_obligations'):
        assert expanded[key] == source[key]
    for key, rows in [('concept_index', 'concepts'), ('reference_catalog', 'entries')]:
        assert [row['id'] for row in view[key][rows]] == [row['id'] for row in source[key][rows]]
        assert expanded[key][rows][:2] == source[key][rows][:2]
        assert all(row.get('formal') is None and row.get('markdown') is None for row in view[key][rows][2:])
    assert len(json.dumps(view, ensure_ascii=False)) < len(json.dumps(source, ensure_ascii=False)) * .65
    if mode == 'balanced':
        assert json.dumps(view, ensure_ascii=False).count(json.dumps(source['source_text'], ensure_ascii=False)) == 1
    else:
        assert ''.join(row['text'] for row in payload['source_lines']) == source['source_text']
    assert '此前完整证明不可恢复' not in json.dumps(payload, ensure_ascii=False)
    if role != 'author':
        key = 'candidate_content' if role == 'reviewer' else 'content'
        assert payload['frozen_context'][key] == content
        assert payload['frozen_context']['available_theorem_names'] == ['前文定理']
    assert '不要把 $text_ref 写进正文' in payload['input_context_notes']


def test_not_exact_text_stays_literal_and_ambiguous_name_does_not_select_details():
    _, source = rich_source()
    source['source_text'] += '\n由定理1得到。'
    source['source_obligations'][0]['source_refs'][0]['quote'] = '勘误后的来源措辞不同，不能猜测来源位置。' * 30
    context, _ = compact_content_context(source)
    assert context['source_obligations'][0]['source_refs'][0]['quote'] == source['source_obligations'][0]['source_refs'][0]['quote']
    assert 'formal' not in context['concept_index']['concepts'][4]
    assert 'formal' not in context['concept_index']['concepts'][5]


def test_actual_author_reviewer_and_repair_use_compact_view_with_same_frozen_source(tmp_path, monkeypatch):
    content, source = rich_source()
    before = deepcopy(source)
    ctx = stage_context(tmp_path)
    monkeypatch.setattr(stages, '_available_theorem_names', lambda *_: ['前文定理'])
    client = Client([authoring(content), verdicts(), authoring(content)])
    with review_mode('balanced'):
        source_digest = SourceContext.model_validate(source).digest
        authored = generate_content(source, load_content_policy(), client, available_theorem_names=['前文定理'])
        review_content(content, {}, source, (), None, client, available_theorem_names=['前文定理'])
        stages._repair_content(ctx, content, source, load_content_policy(), client, [], lambda *_: None)
    assert len(client.calls) == 3
    assert source == before
    assert authored['content']['source_context_hash'] == source_digest
    for messages, _meta in client.calls:
        payload = json.loads(messages[1]['content'])
        context = payload['frozen_context']
        assert context['available_theorem_names'] == ['前文定理']
        view = context.get('source_context', context)
        assert expand(view['source_obligations'], payload) == source['source_obligations']
        assert 'formal' not in view['concept_index']['concepts'][2]
        assert '此前完整证明不可恢复' not in json.dumps(payload, ensure_ascii=False)
    frozen_repair = json.loads(stages._generation_journal(ctx, phase='repair').with_suffix('.input.json').read_text())
    assert frozen_repair['source_context'] == source
    assert frozen_repair['content'] == content


def test_current_theorem_keeps_complete_source_definition_even_with_same_external_name():
    _, source = rich_source()
    source['concept_index']['concepts'][4]['first_unit'] = 'U'
    source['reference_catalog']['entries'][4]['unit_id'] = 'U'
    result, _ = compact_content_context(source)
    assert result['concept_index']['concepts'][4]['formal'] == '旧定理1证明'
    assert 'formal' not in result['concept_index']['concepts'][5]


def test_new_compact_review_journal_replays_without_an_additional_api_call(tmp_path):
    content, source = rich_source()
    client = Client(['{"verdicts":', verdicts()])
    with review_mode('balanced'):
        first = review_content(content, {}, source, (), None, client,
            correction_journal=tmp_path/'review.json', available_theorem_names=['前文定理'])
        replay = review_content(content, {}, source, (), None, client,
            correction_journal=tmp_path/'review.json', available_theorem_names=['前文定理'])
    assert len(client.calls) == 2 and replay.decision == first.decision == 'accepted'
    initial = json.loads(client.calls[0][0][1]['content'])
    correction = json.loads(client.calls[1][0][1]['content'])
    assert 'frozen_context' in initial
    assert correction['original_response'] == '{"verdicts":'
    assert 'frozen_context' not in correction
    assert '前文定理' not in json.dumps(correction, ensure_ascii=False)
    assert client.calls[1][1]['syntax_repair'] is True


@pytest.mark.parametrize('text', ['正文\r\n习题\r\n末行', '正文\n习题\n', '', '无换行'])
def test_full_numbered_source_is_readable_and_exactly_reconstructs_line_endings(text):
    _, source = rich_source()
    source['source_text'] = text
    with review_mode('full'):
        payload = json.loads(build_content_messages('author', source, load_content_policy())[1]['content'])
    lines = payload['source_lines']
    assert [row['line'] for row in lines] == list(range(1, len(lines)+1))
    assert ''.join(row['text'] for row in lines).encode() == text.encode()
    assert expand(payload['frozen_context']['source_text'], payload) == text


def test_author_previous_rejection_and_output_remain_literal():
    content, source = rich_source()
    source['previous_rejection'] = {'content': content, 'reason': source['source_text'],
                                  'original_response': {'markdown': source['source_text']}}
    result, _ = compact_content_context(source)
    assert result['previous_rejection'] == source['previous_rejection']
