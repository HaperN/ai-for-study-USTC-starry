"""The lightweight author example must survive the actual reader/media adapters."""
import json

import pytest

from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.json_only import normalize_author_output
from aiforstudy.blocks.content.prompt import build_content_messages
from aiforstudy.blocks.content.reader_document import project_reader
from aiforstudy.blocks.content.review import review_content
from aiforstudy.blocks.media.author_requirements import project_author_requests
from aiforstudy.core.review_mode import review_mode
from support.content_case import teaching_case
from test_balanced_content_review import Client, verdicts


def test_balanced_author_example_is_visible_linked_gradeable_and_placeable():
    _, source = teaching_case()
    with review_mode('balanced'):
        messages = build_content_messages('author', source, load_content_policy())
        payload = json.loads(messages[1]['content'])
        raw = payload['output_schema_example']
        assert isinstance(raw['content'], dict)
        content = normalize_author_output(raw, source)['content']
        reader = project_reader(content, source['reference_catalog']).as_dict()
        assert len(reader['learning_checks']) == 1
        from aiforstudy.blocks.content.learning_checks import grade_learning_check
        check = content['learning_checks'][0]
        assert grade_learning_check(check, True)['status'] == 'correct'
        assert grade_learning_check(check, False)['status'] == 'incorrect'
        visible = '\n'.join(b['visible_text'] for b in reader['blocks'])
        assert check['statement'] in visible and check['answer'] in visible
        links = [b for b in reader['blocks'] if '](#reader-' in b['markdown']]
        assert links
        import re
        targets = {b['anchor'] for b in reader['blocks']}
        assert all(x in targets for b in links for x in re.findall(r'\]\(#([^)]*)\)', b['markdown']))
        _, requests = project_author_requests(raw['media_requirements'], content)
        placement = requests[0]['placement']
        assert placement['target_pointer'] == '/body_markdown'
        assert content['body_markdown'].count(placement['anchor_text']) == 1
        assert not any(k in payload for k in ('output_json_schema', 'math_contract', 'math_schema_example'))


@pytest.mark.parametrize('role', ['author', 'repair'])
def test_example_animation_request_survives_real_reader_and_media_projection(role):
    _, source = teaching_case()
    with review_mode('balanced'):
        messages = build_content_messages(role, source, load_content_policy())
        example = json.loads(messages[1]['content'])['animation_requirement_example']
        content = normalize_author_output(example, source)['content']
        reader = project_reader(content, source['reference_catalog']).as_dict()
        _, requests = project_author_requests(example['media_requirements'], content)
    assert len(requests) == 1 and requests[0]['kind'] == 'animation'
    assert len(requests[0]['animation']['steps']) == 3
    from aiforstudy.blocks.audit.evidence import resolve_json_pointer
    found, text = resolve_json_pointer(content, requests[0]['placement']['target_pointer'])
    assert found and text.count(requests[0]['placement']['anchor_text']) == 1
    assert any(requests[0]['placement']['anchor_text'] == block['raw_text'] for block in reader['blocks'])
    assert content['math_models'] == []
    # This is the production writing example, exercised through the real adapters.
    assert {row['kind'] for row in content['annotations']} >= {'intuition', 'pitfall'}
    sample = content['examples'][0]
    assert sample['approach'] and sample['conclusion']
    assert sample['key_points'] and sample['pitfalls']
    text = '\n'.join(block['raw_text'] for block in reader['blocks'])
    for literal in [r'w=a+ib', r'\bar w=a-ib', r'w=1+i', r'2w=2+2i',
                    r'\bar w=\bar z_1z_2=z_2\bar z_1']:
        assert literal in text
    for row in content['annotations']:
        assert row['markdown'] in text
    for field in ('approach', 'conclusion', 'key_points', 'pitfalls'):
        values = sample[field] if isinstance(sample[field], list) else [sample[field]]
        assert all(value in text for value in values)
    import re
    links = [target for block in reader['blocks']
             for target in re.findall(r'\]\(#([^)]*)\)', block['markdown'])]
    assert links and set(links) <= {block['anchor'] for block in reader['blocks']}
    assert any(block['json_pointer'].startswith('/annotations/') and block['anchor'] in links
               for block in reader['blocks'])
    assert len(reader['learning_checks']) == 1
    from aiforstudy.blocks.content.learning_checks import grade_learning_check
    assert grade_learning_check(content['learning_checks'][0], True)['status'] == 'correct'
    assert grade_learning_check(content['learning_checks'][0], False)['status'] == 'incorrect'


@pytest.mark.parametrize('role', ['author', 'repair', 'reviewer'])
def test_balanced_roles_keep_teaching_quality_without_old_gate_instructions(role):
    content, source = teaching_case()
    with review_mode('balanced'):
        messages = build_content_messages(role, {'source_context':source, 'content':content}, load_content_policy())
    task = messages[0]['content']
    assert '定义和概念不要求推导' in task
    assert '背景概念说明不额外变成证明任务' in task
    assert '性质、推论' in task
    assert '只列公式' in task
    assert '含义' in task and '适用条件' in task
    if role != 'reviewer':
        payload = json.loads(messages[1]['content'])
        assert '[[CONCEPT:' in payload['reader_reference_contract']['rules']
        assert '[[EXPLAIN:' in payload['reader_reference_contract']['rules']
        assert '连续变化' in task and '教学收益' in task
    if role == 'repair':
        assert '仅修复' in task and '自行逐项对比上一版' in task


def test_reviewer_gets_same_three_criteria_with_explanation_and_property_proof_scope():
    content, source = teaching_case()
    content['body_markdown'] = '共轭的五条性质如下。性质都不难证明，留给读者做练习。'
    client = Client([verdicts('fail')])
    with review_mode('balanced'):
        report = review_content(content, {}, source, (), None, client).as_dict()
    request = json.loads(client.calls[0][0][1]['content'])['frozen_context']
    criteria = {row['criterion_id']:row['expected'] for row in request['criteria']}
    assert len(criteria) == 3 and len(client.calls) == 1
    assert '含义' in criteria['CONTENT-KNOWLEDGE']
    assert '性质、推论' in criteria['CONTENT-STEPS']
    assert '概念' in criteria['CONTENT-STEPS']
    assert report['decision'] != 'accepted'
