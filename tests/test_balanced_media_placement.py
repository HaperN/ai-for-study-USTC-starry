"""Limited review keeps the author's landing intent without guessing prose semantics."""
from copy import deepcopy
from pathlib import Path

import pytest

from aiforstudy.blocks.media.author_requirements import bind_author_requirements
from aiforstudy.blocks.media.media_planning import plan_media_requirements
from aiforstudy.blocks.render.layout import build_reader_layout
from aiforstudy.core.review_mode import review_mode

ROOT = Path(__file__).resolve().parents[1]
BODY = '先定义共轭复数。\n\n两个点关于实轴对称。\n\n再讲四则运算。'


def compile_request(placement):
    content = {'unit_id': 'U', 'book_id': 'B', 'title': '共轭复数', 'body_markdown': BODY}
    requirements = {'decisions': [], 'requests': [
        {'request_key': 'conjugate', 'kind': 'image', 'placement': deepcopy(placement)}]}
    before = deepcopy(requirements)
    author = bind_author_requirements(requirements, content, author_response_hash='raw')
    plan = plan_media_requirements(content, {}, author_requirements=author,
                                   capabilities={'code_generation': 'GeneratedMediaCodeV1'})
    assert requirements == before
    return plan['requests'][0]


@pytest.mark.parametrize('mode', ['balanced', 'json_only'])
@pytest.mark.parametrize('instruction', [
    '在共轭复数和模的定义之后、四则运算规则之前。', 'after_properties'])
def test_natural_language_placement_survives_compilation_with_honest_fallback(mode, instruction):
    with review_mode(mode):
        request = compile_request(instruction)
    placement = request['placement']
    assert placement['author_intent'] == instruction
    assert placement['reason']
    assert placement['anchor_text'] == ''  # No invented sentence to pretend the hint was resolved.
    assert placement['position'] == 'after'


@pytest.mark.parametrize('mode', ['balanced', 'json_only'])
def test_structured_author_anchor_still_mounts_between_actual_paragraphs(mode):
    authored = {'target_pointer': '/body_markdown', 'anchor_text': '两个点关于实轴对称。',
                'position': 'after'}
    with review_mode(mode):
        request = compile_request(authored)
        placement = request['placement']
        document = {'schema_version': 'ReaderDocumentV1', 'unit_id': 'U', 'blocks': [
            {'json_pointer': '/body_markdown', 'owner_id': 'U', 'raw_text': BODY,
             'markdown': BODY, 'group': 'body', 'anchor': 'body'}]}
        mount = {'source_kind': 'media', 'source_id': request['request_id'], 'source_hash': 'image',
                 'anchor_id': 'media-test', 'target_pointer': placement['target_pointer'],
                 'requested_span_cp': placement['span'], 'anchor_text': placement['anchor_text'],
                 'edge': placement['position']}
        layout = build_reader_layout(document, [mount], workspace_root=ROOT, display_policy={})
    assert all(placement[key] == value for key, value in authored.items())
    actual = layout['mounts'][0]
    block = next(row for row in layout['blocks'] if row['block_id'] == actual['resolved_block_id'])
    assert BODY[block['source_start_cp']:block['source_end_cp']].strip() == '两个点关于实轴对称。'
    assert actual['edge'] == 'after'
