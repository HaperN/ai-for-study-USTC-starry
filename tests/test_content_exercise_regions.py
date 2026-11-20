"""Authors locate original exercise material; the host preserves its exact bytes."""
from copy import deepcopy
import hashlib
import json

import pytest

from aiforstudy.blocks.content.content_contracts import parse_content
from aiforstudy.blocks.content.content_policy import applicable_items, check_content, load_content_policy
from aiforstudy.blocks.content.generation import generate_content
from aiforstudy.blocks.content.source_context import SourceContext, source_context_from_frozen
from aiforstudy.errors import ContractError
from support.content_case import authoring, teaching_case


def exercise_case():
    content, source = teaching_case()
    source['source_text'] += '\n\n习题 1\r\n1. 判断 n=0 是否允许。\r\n\n参考答案\n原书答案：允许。\n'
    source['original_sha256'] = 'sha256:' + hashlib.sha256(source['source_text'].encode()).hexdigest()
    source['source_inputs'] = {'textbook': source['original_sha256']}
    content['exercise_regions'] = [dict(title='习题 1', kind='exercise', source_lines=[3, 4]),
                                   dict(title='参考答案', kind='answers', source_lines=[6, 7])]
    return content, SourceContext.model_validate(source).as_dict()


class Author:
    def __init__(self, content):
        self.content = content
        self.messages = []

    def chat_json(self, messages, **kwargs):
        self.messages.append(deepcopy(messages))
        return {'data': authoring(deepcopy(self.content))}


def generated(content=None, source=None):
    original, frozen = exercise_case()
    client = Author(original if content is None else content)
    result = generate_content(frozen if source is None else source, load_content_policy(), client)
    return result['content'], client


def test_author_ranges_preserve_questions_answers_and_line_endings_without_solving():
    content, client = generated()
    regions = content['exercise_regions']
    assert len(client.messages) == 1
    assert regions[0]['raw_markdown'] == '习题 1\r\n1. 判断 n=0 是否允许。\r\n'
    assert regions[1]['raw_markdown'] == '参考答案\n原书答案：允许。\n'
    assert regions[0]['source_sha256'] == 'sha256:' + hashlib.sha256(regions[0]['raw_markdown'].encode()).hexdigest()
    assert regions[0]['id'] and regions[0]['id'] != regions[1]['id']
    assert not content['examples'] and not content['math_models'] and not content['learning_checks']
    assert set(regions[0]) == {'id', 'title', 'kind', 'source_lines', 'raw_markdown', 'source_sha256'}
    assert generated()[0]['exercise_regions'] == regions


def test_author_receives_numbered_lines_matching_inclusive_source_range():
    _, client = generated()
    payload = json.loads(client.messages[0][1]['content'])
    lines = payload['source_lines']
    assert payload['frozen_context']['source_text'] == {'$source_lines': '/source_lines', 'join': ''}
    assert lines[2] == {'line': 3, 'text': '习题 1\r\n'}
    assert lines[3] == {'line': 4, 'text': '1. 判断 n=0 是否允许。\r\n'}
    assert ''.join(row['text'] for row in lines) == exercise_case()[1]['source_text']


@pytest.mark.parametrize('ranges', [[[0, 4]], [[4, 3]], [[3, 100]], [[3, 4], [4, 7]]])
def test_region_ranges_cannot_escape_or_overlap_frozen_source(ranges):
    content, source = exercise_case()
    content['exercise_regions'] = [dict(title='题区', kind='exercise', source_lines=value) for value in ranges]
    with pytest.raises(ContractError, match='exercise region'):
        generated(content, source)


@pytest.mark.parametrize(('field', 'value'), [('raw_markdown', '伪造原题'), ('source_sha256', 'sha256:forged'), ('id', 'foreign-region')])
def test_author_cannot_forge_host_preserved_region_fields(field, value):
    content, source = exercise_case()
    content['exercise_regions'][0][field] = value
    with pytest.raises(ContractError, match='exercise region'):
        generated(content, source)


def test_stored_region_tampering_is_a_source_check_failure():
    content, _ = generated()
    source = exercise_case()[1]
    content['exercise_regions'][0]['raw_markdown'] = '篡改题目'
    report = check_content(content, source, load_content_policy())
    assert any(row['rule_id'] == 'CONTENT-EXERCISE-SOURCES' for row in report.blocking_issues)


def test_exercise_regions_have_boundary_review_but_no_teaching_or_math_claims():
    content, _ = generated()
    source = exercise_case()[1]
    policy = load_content_policy()
    assert not check_content(content, source, policy).blocking_issues
    items = applicable_items(content, source, policy)
    region_ids = {row['id'] for row in content['exercise_regions']}
    region_items = [row for row in items if row['subject_id'] in region_ids]
    assert len(region_items) == 2
    assert {row['rule_id'] for row in region_items} == {'EXERCISE-SOURCE-BOUNDARY'}
    content['teaching_evidence'][0]['reader_pointer'] = '/exercise_regions/0/raw_markdown'
    assert any(row['rule_id'] == 'CONTENT-EXERCISE-SOURCES' for row in check_content(content, source, policy).blocking_issues)


def test_source_example_stops_before_clear_exercise_heading_and_later_theory_survives():
    text = '例1.1 求 x。\n解. x=1。\n\n## 习题\n1. 求 y。\n\n## 定义1.2 正数\n正数大于零。'
    source = source_context_from_frozen(dict(book_id='B', unit_id='U', source_text=text,
        original_text='U.md', original_sha256='sha256:frozen', source_inputs={}, unit={}, chapters={}, concept_index={}))
    assert source.source_examples[0].book_solution == '解. x=1。'
    assert all('求 y' not in row.source_excerpt for row in source.source_obligations)
    assert any('正数大于零' in row.source_excerpt for row in source.source_obligations)


def test_region_cannot_hide_a_frozen_source_example():
    content, source = exercise_case()
    source['source_examples'] = [{'id': 'E1', 'label': '例1.1', 'statement': '原例题', 'book_solution': '', 'source_line': 3}]
    with pytest.raises(ContractError, match='exercise region.*example'):
        generated(content, source)


def test_original_marker_text_is_preserved_without_becoming_a_host_reference():
    content, source = exercise_case()
    source['source_text'] = source['source_text'].replace('1. 判断 n=0 是否允许。', '1. 原样写出 [[NOTE:literal]] 与 [[FIGURE:literal]]。')
    result, _ = generated(content, source)
    parse_content(result)
    assert '[[NOTE:literal]]' in result['exercise_regions'][0]['raw_markdown']


def test_no_exercises_keeps_existing_content_valid():
    content, source = teaching_case()
    result, _ = generated(content, source)
    assert result['exercise_regions'] == []


@pytest.mark.parametrize('regions', [None, [], [{'title':'习题', 'kind':'exercise', 'source_lines':[3,3]}]])
def test_clear_source_material_cannot_be_omitted_or_partly_recorded(regions):
    content, source = exercise_case()
    if regions is None:
        content.pop('exercise_regions')
    else:
        content['exercise_regions'] = regions
    with pytest.raises(ContractError, match='exercise region.*uncovered.*4|exercise region.*uncovered.*3'):
        generated(content, source)


def test_a_stored_empty_region_list_cannot_erase_clear_source_material():
    content, _ = generated()
    content['exercise_regions'] = []
    report = check_content(content, exercise_case()[1], load_content_policy())
    assert any(row['rule_id'] == 'CONTENT-EXERCISE-SOURCES' and 'uncovered' in row['problem']
               for row in report.blocking_issues)


def test_pure_exercise_instructions_do_not_create_definition_or_method_obligations():
    text = '## 习题\n定义函数 f(x)=x，证明它连续。\n方法提示：先用定义。\n'
    source = source_context_from_frozen(dict(book_id='B', unit_id='U', source_text=text,
        original_text='U.md', original_sha256='sha256:frozen', source_inputs={}, unit={}, chapters={}, concept_index={}))
    assert source.source_obligations == []


@pytest.mark.parametrize('heading', ['练习与思考', '习题：', '## EXERCISES', '**参考答案**'])
def test_clear_material_heading_keeps_following_questions_out_of_original_example(heading):
    from aiforstudy.blocks.content.source_context import extract_source_examples
    text = '例1.1 求 x。\n解. x=1。\n\n' + heading + '\n1. 求 y。\n'
    assert extract_source_examples(text)[0]['book_solution'] == '解. x=1。'


def test_exercise_source_cannot_be_reintroduced_as_an_author_teaching_claim():
    content, _ = generated()
    source = exercise_case()[1]
    content['teaching_plan']['claims'][0]['source_refs'] = [
        {'kind':'textbook', 'quote':'1. 判断 n=0 是否允许。'}]
    report = check_content(content, source, load_content_policy())
    assert any(row['rule_id'] == 'CONTENT-EXERCISE-SOURCES' for row in report.blocking_issues)
