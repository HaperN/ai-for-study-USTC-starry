"""Frozen review accepts JSON without silently reviewing or rewriting its content."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from aiforstudy.core.review_mode import review_mode
from aiforstudy.errors import ContractError
from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.content_contracts import LessonContentV4, parse_content
from aiforstudy.blocks.content.generation import generate_content
from aiforstudy.blocks.content.prompt import build_content_messages
from aiforstudy.blocks.content.source_context import SourceContext
from aiforstudy.blocks.content.source_inventory import extract_source_inventory, validate_source_inventory
from aiforstudy.blocks.media.author_requirements import (
    bind_author_requirements, project_author_requests, rewrite_author_requirements,
    validate_author_artifact, validate_author_projection,
)
from support.content_case import teaching_case


class Answer:
    def __init__(self, value):
        self.value = value

    def chat_json(self, messages, **kwargs):
        return {'data': deepcopy(self.value)}


def loose_author():
    return {'content': {'body_markdown': '保留正文 [[NOTE:missing]]。',
                        'math_models': [{'not_a_math_model': True}],
                        'examples': [{'statement': '原题不能丢失。'}],
                        'unknown_field': {'keep': 1}},
            'media_requirements': {'requests': [{'request_key': 'R', 'kind': 'unlisted'}]}}


def test_json_only_author_keeps_content_without_schema_math_or_marker_review(tmp_path):
    raw = loose_author()
    source = teaching_case()[1]
    with review_mode('json_only'):
        value = generate_content(source, load_content_policy(), Answer(raw),
                                 correction_journal=tmp_path/'generation.json')
    assert value['content']['body_markdown'] == '保留正文 [[NOTE:missing]]。'
    assert value['content']['math_models'] == [{'not_a_math_model': True}]
    assert value['content']['examples'][0]['statement'] == '原题不能丢失。'
    assert value['content']['unknown_field'] == {'keep': 1}
    assert value['content']['unit_id'] == 'U'
    assert value['content']['annotations'] == []
    assert raw == loose_author()


def test_json_only_host_identity_is_assembled_without_rewriting_author_prose(tmp_path, monkeypatch):
    from aiforstudy.pipeline import content_stages
    raw = loose_author()
    raw['content'].update(book_id='wrong-book', unit_id='../../wrong-unit',
                          original_text='wrong.md', original_sha256='wrong-hash',
                          source_context_hash='wrong-context', anchor='wrong-anchor',
                          chapter='wrong-chapter', section='wrong-section', level='wrong-level')
    original = deepcopy(raw)
    source = deepcopy(teaching_case()[1])
    source['unit'].update(anchor='host-anchor', chapter='CH01', section='S01', level='core')
    ctx = SimpleNamespace(node=SimpleNamespace(scope_key='U', stage='lesson'),
                          context=SimpleNamespace(book_id='B'))
    monkeypatch.setattr(content_stages, '_root', lambda context: tmp_path)
    preserved = []
    with review_mode('json_only'):
        source_record = SourceContext.model_validate(source)
        source = source_record.as_dict()
        source['source_context_hash'] = source_record.digest
        value = generate_content(source, load_content_policy(), Answer(raw), preserved.append)
        content_stages._publish_revision(ctx, value['content'], source)
        loaded, approval = content_stages.load_content(ctx, approved=True)
        # Parsing without a host context is a view of supplied data, not rebinding.
        unbound = parse_content(original['content']).as_dict()
    assert loaded['book_id'] == 'B' and loaded['unit_id'] == 'U'
    for field in ('original_text', 'original_sha256', 'source_context_hash'):
        assert loaded[field] == source[field]
    for field in ('anchor', 'chapter', 'section', 'level'):
        assert loaded[field] == source['unit'][field]
    assert loaded['body_markdown'] == original['content']['body_markdown']
    assert loaded['math_models'] == original['content']['math_models']
    assert preserved == [original] and raw == original
    assert unbound['book_id'] == 'wrong-book' and unbound['unit_id'] == '../../wrong-unit'
    assert approval == {}


@pytest.mark.parametrize('mode', ['balanced','json_only'])
@pytest.mark.parametrize('raw,expected_title', [
    ({'title':'作者标题','content':'# 正文标题\n\n完整正文。'},'作者标题'),
    ({'title':'作者标题','content':{'body_markdown':'# 正文标题\n\n完整正文。'}},'作者标题'),
    ({'title':'作者标题','content':{'title':'  ','body_markdown':'# 正文标题\n\n完整正文。'}},'作者标题'),
    ({'content':{'title':'  ','body_markdown':'# 正文标题\n\n完整正文。'}},'目录中的标题'),
    ({'content':{'body_markdown':'# 正文标题\n\n完整正文。'}},'目录中的标题'),
    ({'content':{'title':'作者正文标题','body_markdown':'# 正文标题\n\n完整正文。'}},'作者正文标题'),
])
def test_frozen_author_retains_title_or_uses_known_catalog_title(mode,raw,expected_title):
    source=deepcopy(teaching_case()[1]);source['unit']['title']='目录中的标题'
    original=deepcopy(raw);preserved=[]
    with review_mode(mode):
        value=generate_content(source,load_content_policy(),Answer(raw),preserved.append)
    assert value['content']['title']==expected_title
    assert value['content']['body_markdown']=='# 正文标题\n\n完整正文。'
    assert preserved==[original] and raw==original


def test_json_only_still_rejects_unparseable_json(tmp_path):
    class RawAnswer:
        def chat_json(self, messages, **kwargs):
            return '{"content":'
    with review_mode('json_only'), pytest.raises(ContractError, match='JSON|json_syntax'):
        generate_content(teaching_case()[1], load_content_policy(), RawAnswer(),
                         correction_journal=tmp_path/'broken.json')


@pytest.mark.parametrize('raw', [23, True, ['保留原返回']])
def test_json_only_rejects_json_values_without_a_body(raw):
    """修改计划 A4（2026-10-20）：没有正文的合法 JSON 是字段形状错误，不再规范化成空正文接收。"""
    with review_mode('json_only'), pytest.raises(ContractError, match='empty_body'):
        generate_content(teaching_case()[1], load_content_policy(), Answer(raw))


def test_json_only_still_accepts_plain_text_as_body():
    with review_mode('json_only'):
        value = generate_content(teaching_case()[1], load_content_policy(), Answer('一段完整正文'))
    assert value['content']['body_markdown'] == '一段完整正文'


def test_full_mode_restores_author_schema_gate():
    with review_mode('full'), pytest.raises(ContractError):
        generate_content(teaching_case()[1], load_content_policy(), Answer(loose_author()))


def test_json_only_prompt_does_not_review_learner_profile_equality():
    source = deepcopy(teaching_case()[1])
    source['learner_profile'] = {'background': '保留另一份画像'}
    with review_mode('json_only'):
        messages = build_content_messages('author', source, load_content_policy())
    assert '保留另一份画像' in messages[1]['content']
    with review_mode('full'), pytest.raises(ContractError, match='learner profile'):
        build_content_messages('author', source, load_content_policy())


def test_json_only_prompt_freezes_legacy_payload_size_gate(monkeypatch):
    import aiforstudy.blocks.content.prompt as prompt
    monkeypatch.setattr(prompt, 'MAX_REQUEST_BYTES', 1)
    with review_mode('json_only'):
        assert build_content_messages('reviewer', {}, load_content_policy())
    with review_mode('full'), pytest.raises(ContractError, match='exceeds'):
        build_content_messages('reviewer', {}, load_content_policy())


def test_json_only_parse_preserves_unknown_and_invalid_math_without_approval():
    raw = loose_author()['content']
    with review_mode('json_only'):
        content = parse_content(raw)
        value = content.as_dict()
    assert isinstance(content, LessonContentV4)
    assert value['body_markdown'] == raw['body_markdown']
    assert value['math_models'] == raw['math_models']
    assert value['unknown_field'] == {'keep': 1}
    assert value['teaching_plan']['claims'] == []
    assert value.get('review_outcome', {}).get('status') != 'verified'
    with review_mode('full'), pytest.raises(ContractError):
        parse_content(raw)


def test_json_only_media_does_not_require_bindings_or_a_changed_ticket_field():
    content = {'unit_id': 'U', 'book_id': 'B', 'body_markdown': '正文。'}
    requirements = {'requests': [{'request_key': 'R', 'kind': 'image',
                                  'math_model_ids': ['missing'], 'unknown': '保留'}]}
    with review_mode('json_only'):
        record = bind_author_requirements(requirements, content, author_response_hash='raw')
        same = rewrite_author_requirements(record, record['requirements'], [], content)
        changed = rewrite_author_requirements(record, {'requests': []}, [], content)
        checked = validate_author_artifact(record, content)
        validate_author_projection({'author_media': record, 'requests': []}, content)
    assert record['status'] == same['status'] == changed['status'] == checked['status'] == 'unverified'
    assert record['review_mode'] == 'json_only'
    assert record['requirements']['requests'][0]['unknown'] == '保留'
    assert changed['requirements']['requests'] == []


def test_json_only_media_projection_consumes_missing_fields_without_claiming_binding():
    with review_mode('json_only'):
        requirements, requests = project_author_requests(
            {'requests': [{'kind': 'image', 'math_model_ids': ['missing'], 'observe': '看清关系'}]},
            {'unit_id': 'U', 'body_markdown': '正文。'})
    assert requests[0]['observe'] == '看清关系'
    assert requests[0]['request_id']
    assert requests[0]['object_refs'] == []
    assert requirements['decisions'] == []


def test_json_only_source_inventory_skips_model_and_keeps_structural_source():
    class NoReview:
        def chat_json(self, *args, **kwargs):
            pytest.fail('frozen source review must not call a model')
    source = teaching_case()[1]
    with review_mode('json_only'):
        result = extract_source_inventory(source, NoReview())
        replay = validate_source_inventory(source, {'wrong_schema': True})
    assert result.source_text == replay.source_text == source['source_text']
    assert result.source_obligations[0].id == 'SO'
    assert result.obligation_extraction_status == 'structural_only'


def test_json_only_source_context_adds_defaults_without_dropping_source_text():
    raw = {'source_text': '完整原书文字。', 'unit_id': 'U', 'extra_source': 7}
    with review_mode('json_only'):
        source = SourceContext.model_validate(raw)
    assert source.source_text == '完整原书文字。'
    assert source.source_obligations == []
    assert source.as_dict()['extra_source'] == 7
