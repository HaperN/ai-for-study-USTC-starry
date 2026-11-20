"""Loose author envelopes must not lose already-written teaching content."""
from copy import deepcopy
import json

import pytest

from aiforstudy.blocks.content.json_only import normalize_author_output, normalize_media_requirements
from aiforstudy.blocks.content.reader_document import project_reader
from aiforstudy.blocks.content.review import review_content
from aiforstudy.core.review_mode import review_mode
from support.content_case import teaching_case


@pytest.mark.parametrize('mode', ['balanced', 'json_only'])
def test_root_teaching_fields_survive_string_content_into_reader(mode):
    raw = {'content': '完整正文。', 'examples': [
        {'id': 'EX', 'label': '例1', 'statement': '例题题目', 'solution': '不能丢的完整解答'}],
        'learning_checks': [{'id': 'Q', 'prompt': '选择共轭积', 'options': ['5', '13'],
            'answer_index': 1, 'feedback': {'correct': '对，模平方为13', 'incorrect': '应平方后相加'}}],
        'annotations': [{'id': 'N', 'markdown': '批注解释'}],
        'explainables': [{'id': 'E', 'markdown': '定理的完整推导'}],
        'concepts': [{'id': 'C', 'markdown': '作者补充概念说明'}]}
    original = deepcopy(raw)
    with review_mode(mode):
        content = normalize_author_output(raw, teaching_case()[1])['content']
        doc = project_reader(content, teaching_case()[1]['reference_catalog']).as_dict()
    assert len(content['examples']) == len(content['learning_checks']) == 1
    text = '\n'.join(b['raw_text'] for b in doc['blocks'])
    for value in ['完整正文。', '例题题目', '不能丢的完整解答', '选择共轭积',
                  '5', '13', '对，模平方为13', '应平方后相加', '批注解释',
                  '定理的完整推导', '作者补充概念说明']:
        assert value in text
    assert any(b['field_label'] == '答案' and '13' in b['raw_text'] for b in doc['blocks'])
    assert doc['learning_checks'] == []  # No invented automatic scoring.
    assert 'scoring' not in content['learning_checks'][0]
    assert raw == original


def test_nested_priority_keeps_distinct_root_rows_even_with_same_id_or_title():
    nested = {'id': 'same', 'title': '同名题', 'statement': '嵌套题目'}
    other = {'id': 'same', 'title': '同名题', 'statement': '根级另一道题'}
    raw = {'title': '根标题', 'body_markdown': '根级补充正文',
           'content': {'title': '嵌套标题', 'body_markdown': '嵌套正文',
                       'examples': [nested], 'learning_checks': []},
           'examples': [deepcopy(nested), other, deepcopy(other)],
           'learning_checks': [{'id': 'Q', 'prompt': '根题', 'answer': '根答案'}],
           'summary': '根级小结'}
    original = deepcopy(raw)
    with review_mode('balanced'):
        content = normalize_author_output(raw)['content']
    assert content['title'] == '嵌套标题'
    assert content['body_markdown'] == '嵌套正文\n\n根级补充正文'
    assert [r['statement'] for r in content['examples']] == ['嵌套题目', '根级另一道题', '根级另一道题']
    assert content['learning_checks'][0]['prompt'] == '根题'
    assert content['summary'] == '根级小结'
    assert raw == original


def test_existing_checks_and_authored_scoring_reach_actual_reviewer_candidate():
    raw = {'content': '正文', 'learning_checks': [{'id': 'Q', 'prompt': '计算',
           'answer': '13', 'scoring': {'method': 'custom', 'accepted_inputs': ['13']},
           'feedback': {'incorrect': '请重新核对'}}],
           'examples': [{'id': 'E', 'statement': '题目', 'solution': '解答'}]}
    class Capture:
        def chat_json(self, messages, **kwargs):
            self.candidate = json.loads(messages[1]['content'])['frozen_context']['candidate_content']
            return {'data': {'verdicts': [dict(criterion_id=key, status='unknown',
                reason='仅用于核对送审原文', evidence_refs=[]) for key in
                ['CONTENT-KNOWLEDGE', 'CONTENT-STEPS', 'CONTENT-SELF-CHECKS']]}}
    client = Capture()
    with review_mode('balanced'):
        content = normalize_author_output(raw, teaching_case()[1])['content']
        review_content(content, {}, teaching_case()[1], (), None, client)
    assert client.candidate['learning_checks'][0]['prompt'] == '计算'
    assert client.candidate['learning_checks'][0]['scoring'] == raw['learning_checks'][0]['scoring']
    assert client.candidate['examples'][0]['solution'] == '解答'


def test_teaching_support_and_media_survive_normalization_into_actual_review_request():
    from test_balanced_content_review import Client
    raw = {'content': {'body_markdown': '先看共轭的对应。[[NOTE:N|观察虚部]]',
        'annotations': [{'id': 'N', 'kind': 'intuition', 'title': '共轭对称',
                         'markdown': '实部相同，虚部相反，相加抵消虚部。'},
                        {'id': 'P', 'kind': 'pitfall', 'markdown': '1+i 与 1-i 不相等。'}],
        'examples': [{'id': 'E', 'statement': '计算共轭对之和。',
                      'approach': '先确认互为共轭，再合并为两倍实部。',
                      'steps': [{'markdown': '$(1+i)+(1-i)=2$。', 'basis': '虚部互相抵消。'}],
                      'key_points': ['先识别共轭关系，再使用两倍实部。'],
                      'pitfalls': ['不要写成 $2(1+i)$。']}],
        'explainables': [{'id': 'X', 'what': '共轭改变虚部符号。',
                          'why': '配对以后能消去虚部。',
                          'key_points': ['条件是两项互为共轭。'],
                          'pitfalls': ['不能对任意两项使用两倍实部。']}]},
        'media_requirements': {'requests': [{'request_key': 'M', 'kind': 'animation',
            'learning_goal': '跟随虚部抵消。',
            'placement': {'target_pointer': '/examples/0/steps/0/markdown',
                          'anchor_text': '$(1+i)+(1-i)=2$。', 'position': 'after'}}]}}
    original = deepcopy(raw)
    client = Client([{'verdicts': [{'status': 'pass', 'reason': '离线替身仅验证宿主完整送审。'}]}])
    with review_mode('balanced'):
        result = normalize_author_output(raw, teaching_case()[1])
        review_content(result['content'], {}, teaching_case()[1], (), None, client,
                       author_media=result['media_requirements'])
    request = json.loads(client.calls[0][0][1]['content'])['frozen_context']
    def assert_authored_fields(actual, authored):
        if isinstance(authored, dict):
            for key, value in authored.items():
                assert_authored_fields(actual[key], value)
        elif isinstance(authored, list):
            assert len(actual) == len(authored)
            for actual_row, authored_row in zip(actual, authored):
                assert_authored_fields(actual_row, authored_row)
        else:
            assert actual == authored
    for name in ('annotations', 'examples', 'explainables'):
        assert_authored_fields(request['candidate_content'][name], original['content'][name])
    assert request['author_media']['requests'] == original['media_requirements']['requests']
    assert raw == original


@pytest.mark.parametrize('kind', ['animation', 'image', 'figure'])
def test_explicit_media_type_is_not_silently_changed_to_a_picture(kind):
    raw = {'requests': [{'type': kind, 'description': '原始意图'}]}
    value = normalize_media_requirements(raw)
    assert value['requests'][0]['kind'] == ('animation' if kind == 'animation' else 'image')
    assert raw == {'requests': [{'type': kind, 'description': '原始意图'}]}


def test_valid_existing_kind_has_priority_over_type_alias():
    assert normalize_media_requirements({'requests': [{'kind':'animation','type':'figure'}]})['requests'][0]['kind'] == 'animation'


def test_false_answer_and_plain_feedback_remain_readable_without_guessed_scoring():
    raw = {'content': '正文', 'learning_checks': [
        {'id': 'Q', 'prompt': '判断命题', 'answer': False, 'answer_index': 0,
         'options': ['不能覆盖显式False'], 'feedback': '原作者的核对说明'}]}
    with review_mode('balanced'):
        content = normalize_author_output(raw, teaching_case()[1])['content']
        doc = project_reader(content, teaching_case()[1]['reference_catalog']).as_dict()
    assert content['learning_checks'][0]['answer'] is False
    text = '\n'.join(b['raw_text'] for b in doc['blocks'])
    assert 'false' in text and '原作者的核对说明' in text
    assert doc['learning_checks'] == []


def test_full_registry_does_not_enable_loose_aliases():
    from aiforstudy.blocks.content.reader_fields import _registry_tree, _leaves
    with review_mode('full'):
        assert list(_leaves({'examples':[{'solution':'宽松别名'}]}, _registry_tree())) == []
