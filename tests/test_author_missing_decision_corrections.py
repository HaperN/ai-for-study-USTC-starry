"""Missing goal decisions are author choices under append-only host authority."""
from copy import deepcopy
import json

import pytest

from aiforstudy.blocks.content.generation import generate_content
from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.errors import ContractError
from support.content_case import teaching_case, authoring, none_decision
from test_author_media_grammar_corrections import case as media_case
from test_generation_corrections import SequenceClient


def missing_case(required=False):
    source,good = media_case()
    goal = deepcopy(good['content']['teaching_plan']['goals'][0]); goal['id'] = 'G2'
    if required: goal['visual_requirement'] = {'required':True,'reason':'比较零的可见位置。'}
    good['content']['teaching_plan']['goals'].append(goal)
    good['content']['teaching_evidence'].append({'goal_id':'G2','purpose':'definition','reader_pointer':'/body_markdown','claim_refs':['K']})
    bad = deepcopy(good)
    decision = {**none_decision(), 'goal_id':'G2', 'target_id':'G2', 'not_produced_reason':''}
    if required:
        decision.update(requirement='required', request_ids=['new-goal-image'])
        request = deepcopy(good['media_requirements']['requests'][0])
        request.update(request_key='new-goal-image', goal_refs=['G2'])
        good['media_requirements']['requests'].append(request)
    good['media_requirements']['decisions'].append(decision)
    return source,bad,good


@pytest.mark.parametrize('required', [False,True])
def test_author_can_append_a_missing_goal_decision_and_its_actual_request(required,tmp_path):
    """R3-1：内层纠正轮取消 —— 缺 decision 答复一次即判，反馈写进 journal。"""
    source,bad,good = missing_case(required)
    client = SequenceClient(bad); journal = tmp_path/'generation.json'
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source,load_content_policy(),client,correction_journal=journal)
    assert len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    assert set(feedback['allowed_paths']) == {'/media_requirements/decisions','/media_requirements/requests'}
    assert feedback['errors'][0]['goal_candidates'] == [{'goal_id':'G2','target_id':'G2','claim_ids':['K'],'required':required}]
    # 合法答复另起一次调用即通过，journal 定稿后重放不再花调用
    ok_journal = tmp_path/'generation-ok.json'
    result = generate_content(source,load_content_policy(),SequenceClient(good),correction_journal=ok_journal)
    assert result['media_requirements'] == good['media_requirements']
    replay = SequenceClient()
    assert generate_content(source,load_content_policy(),replay,correction_journal=ok_journal) == result
    assert replay.calls == []


def test_empty_decisions_are_not_implicitly_filled_with_none(tmp_path):
    source,_,good = missing_case(); good['media_requirements']['decisions'][0]['requirement'] = 'required'
    bad = deepcopy(good); bad['media_requirements']['decisions'] = []
    client = SequenceClient(bad); journal = tmp_path/'generation.json'
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source,load_content_policy(),client,correction_journal=journal)
    assert len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    assert feedback['errors'][0]['path'] == '/media_requirements/decisions'
    assert [row['goal_id'] for row in feedback['errors'][0]['goal_candidates']] == ['G','G2']
    result = generate_content(source,load_content_policy(),SequenceClient(good))
    assert result['media_requirements']['decisions'][0]['requirement'] == 'required'
    assert result['media_requirements']['requests'] == bad['media_requirements']['requests']


@pytest.mark.parametrize('damage', ['old_decision','old_request','drop_request','old_goal','unknown_claim','duplicate_key','content','withdraw_required'])
def test_missing_goal_repair_revalidates_bindings_but_allows_related_teaching_edits(damage, tmp_path):
    source,bad,good = missing_case(True)
    if damage == 'old_decision': good['media_requirements']['decisions'][0]['reason'] = '新理由'
    elif damage == 'old_request': good['media_requirements']['requests'][0]['observe'] = '新图意'
    elif damage == 'drop_request': good['media_requirements']['requests'].pop(0)
    elif damage == 'old_goal': good['media_requirements']['requests'][1]['goal_refs'] = ['G']
    elif damage == 'unknown_claim': good['media_requirements']['requests'][1]['claim_ids'] = ['FOREIGN']
    elif damage == 'duplicate_key': good['media_requirements']['requests'][1]['request_key'] = 'zero-position'
    elif damage == 'content': good['content']['teaching_plan']['goals'][0]['level'] = 'support'
    else: good['media_requirements']['decisions'][1].update(requirement='none',request_ids=[])
    client = SequenceClient(bad); journal = tmp_path/'generation.json'
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source,load_content_policy(),client,correction_journal=journal)
    assert len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    assert set(feedback['allowed_paths']) == {'/media_requirements/decisions','/media_requirements/requests'}
    # 完整候选另起一次调用，按**自身**是否合法重新校验绑定与身份
    contender = SequenceClient(good)
    if damage in {'old_decision', 'old_request', 'content'}:
        result = generate_content(source,load_content_policy(),contender)
        assert result['media_requirements'] == good['media_requirements']
    else:
        with pytest.raises(ContractError): generate_content(source,load_content_policy(),contender)
    assert len(contender.calls) == 1


def test_still_missing_decisions_are_rejected_once_persisted_and_replay_without_a_call(tmp_path):
    """R3-1：原"两轮配额共享"用例改写为验证新机制（一次即判 + 反馈持久 + 重放不调用）。"""
    content,source = teaching_case(); bad = authoring(content);bad['media_requirements']['decisions'] = []
    client = SequenceClient(bad); journal = tmp_path/'generation.json'
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source,load_content_policy(),client,correction_journal=journal)
    assert len(client.calls) == 1
    state = json.loads(journal.read_text())
    assert len(state['attempts']) == 1 and state['merged_feedback']['errors']
    assert state['merged_feedback']['allowed_paths'] == ['/media_requirements/decisions','/media_requirements/requests']
    replay = SequenceClient()
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source,load_content_policy(),replay,correction_journal=journal)
    assert replay.calls == []


@pytest.mark.parametrize('damage',['foreign_goal','duplicate_decision'])
def test_existing_decision_identity_conflicts_are_not_append_authority(damage):
    source,bad,_ = missing_case()
    if damage == 'foreign_goal':bad['media_requirements']['decisions'][0]['target_id'] = 'FOREIGN'
    else: bad['media_requirements']['decisions'] *= 2
    client = SequenceClient(bad)
    with pytest.raises(ContractError): generate_content(source,load_content_policy(),client)
    assert len(client.calls) == 1


@pytest.mark.parametrize('smuggle', [False,True])
def test_missing_goal_append_can_also_update_existing_request_intent(smuggle, tmp_path):
    source,bad,good = missing_case(True)
    bad['media_requirements']['requests'][0]['placement']['owner_id'] = 'mistyped-owner'
    if smuggle: good['media_requirements']['requests'][0]['observe'] = '不同的图意'
    client = SequenceClient(bad); journal = tmp_path/'generation.json'
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source,load_content_policy(),client,correction_journal=journal)
    assert len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    assert set(feedback['allowed_paths']) == {
        '/media_requirements/decisions','/media_requirements/requests',
        '/media_requirements/requests/0/placement/owner_id'}
    # 整份重写时现有请求的"图意"本就是模型可改的：另起一次调用后按全量校验通过
    contender = SequenceClient(good)
    result = generate_content(source,load_content_policy(),contender)
    assert result['media_requirements'] == good['media_requirements']
    assert len(contender.calls) == 1
