"""New-goal authority composes with, but never broadens, request-scoped repair."""
from copy import deepcopy
import json

import pytest

from aiforstudy.blocks.media.author_requirements import bind_author_requirements, repair_author_requirements
from aiforstudy.core.digest import digest_of
from aiforstudy.errors import ContractError
from support.content_case import none_decision
from test_author_media_requirements import math_case
from test_media_plan_stage import ctx


def repair_case(tmp_path, monkeypatch, change):
    from aiforstudy.pipeline import media_stages as M
    content, approval, author, caps, _ = math_case()
    if change == 'semantics':
        # 改语义但**不引入未声明符号**：共享夹具的符号表为空，
        # 所以只能改条件结构，不能引用任何变量名（否则解析会报未声明符号）。
        content['math_models'][0]['conditions'] = [
            {'op': 'gt', 'left': {'op': 'const', 'value': 1}, 'right': {'op': 'const', 'value': 0}}]
        content['math_models'][0]['claims'][0]['conditions'] = deepcopy(
            content['math_models'][0]['conditions'])
    elif change == 'placement':
        content['body_markdown'] = '当前正文的新锚句。'
    content['teaching_plan']['goals'].append({**content['teaching_plan']['goals'][0], 'id': 'G2'})
    content['teaching_evidence'].append({**content['teaching_evidence'][0], 'goal_id': 'G2'})
    stale = bind_author_requirements(author['requirements'], content,
        author_response_hash=author['author_response_hash'], previous=author, allow_stale=True)
    monkeypatch.setattr(M, '_load_content', lambda _: (content, approval))
    monkeypatch.setattr(M, '_load_author_media', lambda *a, **k: stale)
    monkeypatch.setattr(M, '_capabilities', lambda _: caps)
    result = M.media_plan_stage(ctx(tmp_path, 'media_plan'))
    assert result.quality_state.value == 'needs_review', result.error
    tickets = json.loads((tmp_path / 'products/media_plan/U.json').read_text())['payload']['tickets']
    decisions = deepcopy(author['requirements']['decisions'])
    decisions.append({**none_decision(), 'target_id': 'G2', 'goal_id': 'G2'})
    operations = [{'op': 'replace', 'path': '/decisions', 'value': decisions}]
    if change == 'semantics':
        operations.append({'op': 'replace', 'path': '/requests/0/mathematical_objects',
                           'value': '当前修正后的标量对象 X 的数值为 1。'})
    elif change == 'placement':
        operations.append({'op': 'replace', 'path': '/requests/0/placement', 'value': {
            **author['requirements']['requests'][0]['placement'], 'anchor_text': content['body_markdown']}})
    patch = {'schema_version': 'AuthorMediaPatchV1', 'base_author_media_hash': digest_of(stale),
             'ticket_refs': [t['ticket_id'] for t in tickets], 'operations': operations}
    return content, author, stale, tickets, patch


@pytest.mark.parametrize('change', ['semantics', 'placement'])
def test_new_goal_and_independently_authorized_request_repair_can_close_together(tmp_path, monkeypatch, change):
    content, author, stale, tickets, patch = repair_case(tmp_path, monkeypatch, change)
    repaired = repair_author_requirements(stale, patch, tickets, content)
    assert repaired['status'] == 'bound' and repaired['errors'] == []
    assert len(repaired['requirements']['decisions']) == 2
    old = author['requirements']['requests'][0]
    new = repaired['requirements']['requests'][0]
    assert new['request_key'] == old['request_key']
    assert new['acceptance_criteria'] == old['acceptance_criteria']
    assert repaired['repair_provenance']['ticket_refs'] == patch['ticket_refs']
    field = 'mathematical_objects' if change == 'semantics' else 'placement'
    assert new[field] == patch['operations'][1]['value']


@pytest.mark.parametrize('change', [None, 'semantics', 'placement'])
def test_new_goal_bulk_request_write_cannot_change_unselected_fields(tmp_path, monkeypatch, change):
    content, _, stale, tickets, patch = repair_case(tmp_path, monkeypatch, change)
    requests = deepcopy(stale['requirements']['requests'])
    requests[0]['caption'] = '没有任何工单授权重写的图注。'
    patch['operations'].insert(0, {'op': 'replace', 'path': '/requests', 'value': requests})
    with pytest.raises(ContractError, match='授权|新增目标'):
        repair_author_requirements(stale, patch, tickets, content)


def test_combined_authority_cannot_weaken_frozen_criteria(tmp_path, monkeypatch):
    content, _, stale, tickets, patch = repair_case(tmp_path, monkeypatch, 'semantics')
    requests = deepcopy(stale['requirements']['requests'])
    requests[0]['acceptance_criteria'][0]['statement'] = '不再要求原有观察证据。'
    patch['operations'].insert(0, {'op': 'replace', 'path': '/requests', 'value': requests})
    with pytest.raises(ContractError, match='冻结语义'):
        repair_author_requirements(stale, patch, tickets, content)
