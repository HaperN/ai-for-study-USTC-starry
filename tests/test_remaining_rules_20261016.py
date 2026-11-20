"""Rules confirmed 2026-10-16 (tmp/intent-policy-20261016/剩余规则-确认记录.md).

1. Media generation reads related excerpts + a section outline, never the book catalog.
2. A capacity failure (finish_reason=length) gets one hinted retry within three attempts.
4. A needs_review verdict with a concrete `action` gets one repair; pure doubt does not.
7. Content prompts put unit-independent material first for provider prefix caching.
"""
import json

import pytest

from aiforstudy.core.review_mode import review_mode
from aiforstudy.pipeline.gateway_client import ModelCallFailed
from test_balanced_media_review import CONTENT, PROGRAM, REQUEST, ROOT, Maker, Reviewer, verdict


# ── Rule 1 ───────────────────────────────────────────────────────────────────
LESSON = {
    'unit_id': 'U', 'title': '共轭复数',
    'body_markdown': '# 共轭复数\n\n第一段讲记号。\n\n几何上，z 与共轭关于实轴对称。可打开[[EXPLAIN:GEO|几何意义]]，'
                     '并回忆[[CONCEPT:C-8|共轭]]。\n\n第三段讲运算性质。\n\n第四段讲除法。\n\n第五段无关。',
    'explainables': [{'id': 'GEO', 'label': '几何意义', 'what': '共轭是关于实轴的反射。'},
                     {'id': 'OTHER', 'label': '其他', 'what': '与本图无关的解释。'}],
    'examples': [{'id': 'EX1', 'label': '例1', 'statement': '计算 z 加共轭。', 'steps': []}],
}
CATALOG = {'entries': [{'id': 'C-8', 'kind': 'CONCEPT', 'label': '共轭', 'markdown': '共轭定义'},
                       *({'id': f'C-{i}', 'kind': 'CONCEPT', 'label': f'无关{i}', 'markdown': '无关定义' * 20}
                         for i in range(100, 400))]}
APPROVAL = {'schema_version': 'ContentIdentityV1', 'content_hash': 'sha256:x', 'reference_catalog': CATALOG}


def _media_request(**placement):
    return {'request_id': 'R1', 'kind': 'drawing', 'caption': '共轭对称',
            'placement': {'target_pointer': '/body_markdown', 'position': 'after', **placement}}


def test_media_context_keeps_anchor_paragraph_links_and_outline_only():
    from aiforstudy.blocks.media.media_context import focused_media_context
    ctx = focused_media_context(LESSON, APPROVAL, _media_request(anchor_text='关于实轴对称'))
    body = next(e for e in ctx['related_excerpts'] if e['pointer'] == '/body_markdown')['content']
    assert '关于实轴对称' in body and '第一段' in body and '第三段' in body
    assert '第五段' not in body
    assert [e['pointer'] for e in ctx['related_excerpts']] == ['/body_markdown', '/explainables/0']
    assert [e['id'] for e in ctx['referenced_entries']] == ['C-8']
    assert 'reference_catalog' not in ctx['approval_binding']
    assert ctx['approval_binding']['content_hash'] == 'sha256:x'
    outline = ctx['section_outline']
    assert outline['body'][0] == '# 共轭复数' and len(outline['body']) == 6
    assert [b['id'] for b in outline['blocks']['explainables']] == ['GEO', 'OTHER']


def test_media_context_falls_back_to_whole_body_and_to_pointed_item():
    from aiforstudy.blocks.media.media_context import focused_media_context
    lost = focused_media_context(LESSON, APPROVAL, _media_request(anchor_text='不存在的句子'))
    assert lost['related_excerpts'][0]['content'] == LESSON['body_markdown']
    example = focused_media_context(LESSON, APPROVAL, {
        'request_id': 'R2', 'kind': 'drawing',
        'placement': {'target_pointer': '/examples/0/statement', 'anchor_text': '计算'}})
    assert example['related_excerpts'][0] == {'pointer': '/examples/0', 'content': LESSON['examples'][0]}


def test_media_prompt_no_longer_carries_the_book_catalog():
    from aiforstudy.blocks.media.code_generation import _messages
    messages = _messages(_media_request(anchor_text='关于实轴对称'), LESSON, APPROVAL, None)
    payload = json.loads(messages[-1]['content'])
    assert '无关定义' not in messages[-1]['content']
    assert set(payload['approved_content']) == {'section_outline', 'related_excerpts', 'referenced_entries'}
    assert 'reference_catalog' not in payload['approval']


# ── Rule 2 ───────────────────────────────────────────────────────────────────
def _truncated():
    return ModelCallFailed('capacity_failure: finish_reason=length', failure_kind='truncation')


@pytest.mark.parametrize('responses,calls,state', [([_truncated(), PROGRAM], 2, 'fulfilled'),
                                                   ([_truncated(), _truncated(), PROGRAM], 2, 'failed')])
def test_output_limit_gets_one_hinted_retry_inside_three_attempts(tmp_path, responses, calls, state):
    from aiforstudy.blocks.media.code_execution import execute_code
    maker, reviewer = Maker(responses), Reviewer([verdict('pass')])
    with review_mode('balanced'):
        row = execute_code(REQUEST, content=CONTENT, approval={}, out_dir=tmp_path, client=maker,
                           reviewer=reviewer, workspace_root=ROOT)
    assert len(maker.calls) == calls
    assert '输出额度全部用于思考' in maker.calls[1][-1]['content']
    assert row['code_attempts'][0]['output_limit'] is True
    assert row['state'] == state


# ── Rule 4 ───────────────────────────────────────────────────────────────────
def doubt(action=''):
    row = {'status': 'needs_review', 'reason': '橙色弧角标看起来是 +φ，字号小无法确认。'}
    if action:
        row['action'] = action
    return {'verdicts': [row]}


@pytest.mark.parametrize('responses,calls,outcome', [
    ([doubt('若为 +φ 改为 −φ'), verdict('pass')], 2, 'verified'),
    ([doubt('若为 +φ 改为 −φ'), doubt('仍需改为 −φ')], 2, 'unverified'),
    ([doubt()], 1, 'unverified'),
])
def test_concrete_needs_review_gets_exactly_one_repair(tmp_path, responses, calls, outcome):
    from aiforstudy.blocks.media.code_execution import execute_code
    maker, reviewer = Maker(), Reviewer(responses)
    with review_mode('balanced'):
        row = execute_code(REQUEST, content=CONTENT, approval={}, out_dir=tmp_path, client=maker,
                           reviewer=reviewer, workspace_root=ROOT)
    assert len(maker.calls) == calls == len(row['code_attempts'])
    assert row['review_outcome']['status'] == outcome
    if calls > 1:
        assert '改为 −φ' in maker.calls[1][-1]['content']
        assert row['code_attempts'][0]['needs_review_repair'] is True


# ── Rule 7 ───────────────────────────────────────────────────────────────────
def test_content_prompt_puts_unit_independent_material_first():
    from aiforstudy.blocks.content.prompt import _cache_friendly_order
    payload = {'role_task': 'author',
               'frozen_context': {'unit_id': 'U', 'source_text': '本节', 'concept_index': {'c': 1},
                                  'chapters': {'t': 1}, 'reference_catalog': {'e': 1}},
               'learner_profile': {'p': 1}, 'reader_reference_contract': {'r': 1},
               'output_schema_example': {}, 'theorem_index_example': {}}
    ordered = _cache_friendly_order(payload)
    assert list(ordered)[-1] == 'frozen_context'
    assert list(ordered)[:3] == ['role_task', 'learner_profile', 'reader_reference_contract']
    assert list(ordered['frozen_context']) == ['chapters', 'concept_index', 'reference_catalog',
                                               'unit_id', 'source_text']
    assert json.loads(json.dumps(ordered)) == payload  # content unchanged
    envelope = _cache_friendly_order({'role_task': 'repair', 'frozen_context': {
        'content': {}, 'source_context': {'source_text': 'x', 'chapters': {}}}})
    assert list(envelope['frozen_context']) == ['source_context', 'content']
    assert list(envelope['frozen_context']['source_context']) == ['chapters', 'source_text']
