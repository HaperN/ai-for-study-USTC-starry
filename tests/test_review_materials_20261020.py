"""修改计划 B2/B3/B4/B7/C2/C5（2026-10-20）：提示词、审核材料与公共卡修正。"""
import json

import pytest

from aiforstudy.blocks.content.concept_corrections import correction_listing, correction_shape_errors
from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.generation_corrections import LocalCorrectionRequired
from aiforstudy.blocks.content.json_only import normalize_author_output
from aiforstudy.blocks.content.prompt import build_content_messages
from aiforstudy.core.review_mode import review_mode
from support.content_case import teaching_case

OLD, NEW = r'z^a=e^{a\ln z}', r'z^a=e^{a\operatorname{Ln} z}'
CONTEXT = {'concept_index': {'concepts': [{'id': 'C-0522', 'name': '一般幂函数', 'formal': OLD}]}}


def correction(original, corrected, cid='C-0522', pointer='/formal'):
    return {'id': 'X1', 'original': original, 'corrected': corrected, 'reason': '多值性须用 Ln。',
            'source_refs': [{'kind': 'concept', 'concept_id': cid, 'json_pointer': pointer}]}


@pytest.mark.parametrize('row, bad', [
    (correction(OLD, NEW), False),
    (correction(OLD, OLD), True),                       # 只写了说明，正式字段没改
    (correction(OLD, NEW, pointer='/definition'), True),  # 目标字段不存在
    (correction(OLD, NEW, cid='C-9999'), True),
])
def test_shared_correction_must_really_change_the_formal_field(row, bad):
    assert bool(correction_shape_errors({'corrections': [row]}, CONTEXT)) is bad


def test_unchanged_shared_correction_only_warns_and_keeps_the_body():
    """用户 2026-10-20 定：技术检查放宽——无效公共卡修正不打回整稿，只记警告。"""
    env = normalize_author_output({'content': {'body_markdown': '正文', 'corrections': [correction(OLD, OLD)]}}, CONTEXT)
    assert env['content']['body_markdown'] == '正文'
    assert 'corrected 与 original 相同' in env['correction_warnings'][0]


def test_effective_proposal_is_listed_as_retained_with_old_public_value():
    """C-0522 的情形：本节提案已发布生效，审核材料仍能看到它、状态为本节保留、before 是旧错式。"""
    content = {'body_markdown': '见 [[CONCEPT:C-0522|一般幂函数]]。', 'corrections': [correction(OLD, NEW)]}
    published = [{'concept_id': 'C-0522', 'field': 'formal', 'before': OLD, 'after': NEW,
                  'origin_unit': 'CH02-S05-SS07', 'version': 'v1'}]
    rows = correction_listing(content, published, [], unit_id='CH02-S05-SS07')
    assert rows == [{'concept_id': 'C-0522', 'field': 'formal', 'before': OLD, 'after': NEW,
                     'origin_unit': 'CH02-S05-SS07', 'version': 'v1', 'status': '本节保留'}]
    other = correction_listing({'body_markdown': '见 [[CONCEPT:C-0522|幂]]'}, published, [], unit_id='CH03-S01')
    assert other[0]['status'] == '已发布'


def test_prompts_carry_review_and_repair_rules():
    _, source = teaching_case()
    with review_mode('balanced'):
        reviewer = build_content_messages('reviewer', source, load_content_policy())[0]['content']
        repair = build_content_messages('repair', {'source_context': source}, load_content_policy())[0]['content']
    for phrase in ('不是公共卡片的现值', '已修 / 仍在 / 撤回', '轮次将尽', 'ticket_responses',
                   '只可作为润色建议', '所用分支', '不能因为“显示值已经等于修正版”'):
        assert phrase in reviewer, phrase
    assert 'ticket_responses' in repair and 'corrected 是改正后的完整值' in repair


def test_ticket_responses_survive_normalization_but_not_the_reader():
    from aiforstudy.blocks.content.reader_document import project_reader
    responses = [{'ticket_id': 'T1', 'decision': 'kept', 'basis': '反例：a=0 时条件仍成立。'}]
    env = normalize_author_output({'content': {'body_markdown': '正文'}, 'ticket_responses': responses})
    assert env['content']['ticket_responses'] == responses
    content, _ = teaching_case()
    content['ticket_responses'] = responses
    with review_mode('balanced'):
        reader = project_reader(normalize_author_output({'content': content})['content'], {'entries': []}).as_dict()
    assert '反例' not in json.dumps(reader, ensure_ascii=False)
