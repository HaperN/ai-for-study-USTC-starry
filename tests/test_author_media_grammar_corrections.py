"""Recover exact media grammar without granting intent or identity rewrites."""
from copy import deepcopy
import json

import pytest

from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.generation import generate_content
from aiforstudy.errors import ContractError
from support.content_case import teaching_case
from test_author_media_requirements import math_case
from test_generation_corrections import SequenceClient


def case():
    content, _, record, _, _ = math_case()
    return teaching_case()[1], dict(schema_version='ContentAuthoringV1', content=content,
                                   media_requirements=record['requirements'])


def damage(envelope, kind):
    request = envelope['media_requirements']['requests'][0]
    root = '/media_requirements/requests/0'
    if kind == 'owner':
        request['placement']['owner_id'] = 'mistyped-owner'
        return root+'/placement/owner_id', 'U'
    if kind == 'evidence':
        request['acceptance_criteria'][0]['evidence_required'] = ['object_bindings', 'rendered_frame', 'object_bindings']
        return root+'/acceptance_criteria/0/evidence_required', ['object_bindings', 'rendered_frame']
    if kind == 'objects':
        request['object_refs'] *= 2
        return root+'/object_refs', [{'model_id':'M', 'object_id':'X'}]
    if kind == 'request_ids':
        envelope['media_requirements']['decisions'][0]['request_ids'] *= 2
        return '/media_requirements/decisions/0/request_ids', ['zero-position']
    request[kind] *= 2
    return root+'/'+kind, {'goal_refs':['G'], 'claim_ids':['K'], 'math_model_ids':['M']}[kind]


@pytest.mark.parametrize('kind', ['owner','evidence','objects','goal_refs','claim_ids','math_model_ids','request_ids'])
def test_exact_media_grammar_reaches_one_bounded_correction_and_replays(kind, tmp_path):
    """R3-1：内层纠正轮取消 —— 一次即判，同样的精确反馈改由 journal 持久化。"""
    source, good = case(); bad = deepcopy(good)
    path, candidate = damage(bad, kind)
    client = SequenceClient(bad); journal = tmp_path/'generation.json'
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source, load_content_policy(), client, correction_journal=journal)
    assert len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    # 反馈可能同时给出更浅的容器路径；关键是**精确路径必须在**
    assert path in feedback['allowed_paths'], feedback['allowed_paths']
    assert feedback['errors'][0]['candidates'] == [candidate]
    assert feedback['errors'][0]['fix'] and feedback['errors'][0]['passing_criterion']
    # 合法答复另起一次调用即通过，且 journal 定稿后重放不再花调用。
    ok_journal = tmp_path/'generation-ok.json'
    result = generate_content(source, load_content_policy(), SequenceClient(good), correction_journal=ok_journal)
    assert result['media_requirements'] == good['media_requirements']
    assert result['content']['body_markdown'] == good['content']['body_markdown']
    replay = SequenceClient()
    assert generate_content(source, load_content_policy(), replay, correction_journal=ok_journal) == result
    assert replay.calls == []


def test_independent_media_grammar_errors_are_collected_before_one_correction(tmp_path):
    source, good = case(); bad = deepcopy(good)
    expected = {damage(bad, kind)[0] for kind in ['owner','evidence','objects','goal_refs','claim_ids','math_model_ids','request_ids']}
    client = SequenceClient(bad); journal = tmp_path/'generation.json'
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source, load_content_policy(), client, correction_journal=journal)
    assert len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    assert set(feedback['allowed_paths']) == expected
    result = generate_content(source, load_content_policy(), SequenceClient(good))
    assert result['media_requirements'] == good['media_requirements']


@pytest.mark.parametrize('mutation', ['drop_request','identity','intent','new_goal','drop_dependency','reorder_evidence'])
def test_grammar_correction_rechecks_dependencies_and_preserves_request_identity(mutation, tmp_path):
    """R3-1：反馈只圈定被破坏的语法路径；完整候选另起一次调用按全量规则重校验。"""
    source, good = case(); bad = deepcopy(good)
    damage(bad, 'evidence'); damage(bad, 'objects')
    candidate = deepcopy(good); request = candidate['media_requirements']['requests'][0]
    if mutation == 'drop_request': candidate['media_requirements']['requests'] = []
    elif mutation == 'identity': request['request_key'] = 'different'
    elif mutation == 'intent': request['observe'] = 'different mathematical intention'
    elif mutation == 'new_goal': request['goal_refs'] = ['OTHER']
    elif mutation == 'drop_dependency': request['object_refs'] = []
    else: request['acceptance_criteria'][0]['evidence_required'].reverse()
    client = SequenceClient(bad); journal = tmp_path/'generation.json'
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source, load_content_policy(), client, correction_journal=journal)
    assert len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    assert set(feedback['allowed_paths']) == {
        '/media_requirements/requests/0/acceptance_criteria/0/evidence_required',
        '/media_requirements/requests/0/object_refs'}
    # 纠正授权面不包含身份/意图/依赖：这些只能由模型在**全新一份**答复里自行主张，
    # 且必须重新通过全套校验（合法则通过，不合法一次即判）。
    assert not any('request_key' in p or 'observe' in p or 'goal_refs' in p
                   for p in feedback['allowed_paths'])
    contender = SequenceClient(candidate)
    if mutation in {'intent', 'reorder_evidence', 'drop_dependency'}:
        result = generate_content(source, load_content_policy(), contender)
        assert result['media_requirements'] == candidate['media_requirements']
    else:
        with pytest.raises(ContractError):
            generate_content(source, load_content_policy(), contender)
    assert len(contender.calls) == 1


def test_unchanged_media_grammar_is_rejected_once_persisted_and_replayed_without_a_call(tmp_path):
    """R3-1：原"两轮配额共享"用例改写为验证新机制（一次即判 + 反馈持久 + 重放不调用）。"""
    source, bad = case(); damage(bad, 'goal_refs')
    client = SequenceClient(bad); journal = tmp_path/'generation.json'
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source, load_content_policy(), client, correction_journal=journal)
    assert len(client.calls) == 1
    state = json.loads(journal.read_text())
    assert len(state['attempts']) == 1
    assert state['merged_feedback']['allowed_paths'] == ['/media_requirements/requests/0/goal_refs']
    assert state['merged_feedback']['errors']
    replay = SequenceClient()
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source, load_content_policy(), replay, correction_journal=journal)
    assert replay.calls == []


@pytest.mark.parametrize('damage_kind', ['foreign_refs','unlinked_pointer','foreign_owner','unknown_object','ambiguous_anchor'])
def test_no_candidate_is_inferred_from_conflicting_or_unrelated_identity(damage_kind):
    source, bad = case(); request = bad['media_requirements']['requests'][0]
    if damage_kind == 'foreign_refs': request['goal_refs'] = ['FOREIGN','FOREIGN']
    elif damage_kind == 'unlinked_pointer':
        request['placement'].update(owner_id='mistyped-owner', target_pointer='/summary', anchor_text=bad['content']['summary'])
    elif damage_kind == 'foreign_owner':
        bad['content']['explainables'] = [{'id':'OTHER','kind':'definition','name':'other', 'markdown':'Other text', 'source_refs':[]}]
        request['placement']['owner_id'] = 'OTHER'
    elif damage_kind == 'unknown_object': request['object_refs'] = [{'model_id':'M','object_id':'UNKNOWN'}]*2
    else:
        text = bad['content']['body_markdown']; bad['content']['body_markdown'] = text*2
        request['placement'].update(owner_id='mistyped-owner', occurrence=None)
    client = SequenceClient(bad, bad, bad)
    with pytest.raises(ContractError): generate_content(source, load_content_policy(), client)
    # R3-1：内层纠正轮取消 —— 一次答复即判。
    assert len(client.calls) == 1


def test_duplicate_refs_do_not_hide_other_container_schema_errors():
    source, bad = case(); damage(bad, 'goal_refs')
    bad['media_requirements']['requests'][0]['kind'] = 'animation'
    client = SequenceClient(bad, bad, bad)
    with pytest.raises(ContractError, match='media|animation|duplicate'):
        generate_content(source, load_content_policy(), client)
    assert len(client.calls) == 1


def test_object_deduplication_preserves_distinct_dependencies_in_original_order(tmp_path):
    source, good = case()
    good['content']['math_models'][0]['objects'].append({'math_object_id':'Y', 'kind':'scalar', 'expr':{'op':'const','value':1}})
    request = good['media_requirements']['requests'][0]
    request['object_refs'] = [{'model_id':'M','object_id':'Y'}, {'model_id':'M','object_id':'X'}]
    bad = deepcopy(good); bad['media_requirements']['requests'][0]['object_refs'] *= 2
    client = SequenceClient(bad); journal = tmp_path/'generation.json'
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source, load_content_policy(), client, correction_journal=journal)
    assert len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    # 诊断候选保序去重，指向原顺序 [Y, X]
    assert feedback['errors'][0]['candidates'] == [[
        {'model_id':'M','object_id':'Y'}, {'model_id':'M','object_id':'X'}]]
    result = generate_content(source, load_content_policy(), SequenceClient(good))
    assert result['media_requirements']['requests'][0]['object_refs'] == [
        {'model_id':'M','object_id':'Y'}, {'model_id':'M','object_id':'X'}]


def test_step_identity_candidate_keeps_the_original_reader_pointer_and_owner(tmp_path):
    source, good = case(); content = good['content']
    content['explainables'] = [{'id':'EX', 'kind':'definition', 'name':'zero', 'markdown':'Zero.',
        'steps':[{'id':'STEP', 'markdown':'The nonnegative boundary is zero.'}]}]
    pointer = '/explainables/0/steps/0/markdown'
    content['teaching_evidence'].append({'goal_id':'G', 'purpose':'derivation', 'reader_pointer':pointer, 'claim_refs':['K']})
    good['media_requirements']['requests'][0]['placement'].update(
        owner_id='EX', step_id='STEP', target_pointer=pointer, anchor_text='The nonnegative boundary is zero.')
    bad = deepcopy(good); bad['media_requirements']['requests'][0]['placement']['step_id'] = 'mistyped-step'
    client = SequenceClient(bad); journal = tmp_path/'generation.json'
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source, load_content_policy(), client, correction_journal=journal)
    assert len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    # 反馈可能附带更浅的容器路径；精确的 step_id 路径必须在
    assert '/media_requirements/requests/0/placement/step_id' in feedback['allowed_paths']
    assert feedback['errors'][0]['candidates'] == ['STEP']
    result = generate_content(source, load_content_policy(), SequenceClient(good))
    assert result['media_requirements']['requests'][0]['placement'] == good['media_requirements']['requests'][0]['placement']


@pytest.mark.parametrize('part', ['nested_owner','criterion','objects','selected','models'])
def test_malformed_input_does_not_make_grammar_diagnostics_crash(part):
    from aiforstudy.blocks.media.author_diagnostics import author_grammar_errors
    _, bad = case(); request = bad['media_requirements']['requests'][0]
    if part == 'nested_owner':
        bad['content']['explainables'] = [{'id':['not-an-id'], 'markdown':'text'}]
    elif part == 'criterion': request['acceptance_criteria'] = [None, {'evidence_required':[{}]}]
    elif part == 'objects': request['object_refs'] = [{'model_id':[], 'object_id':[]}]
    elif part == 'selected': request['goal_refs'] = [{}]
    else: bad['content']['math_models'] = [{}]
    assert author_grammar_errors(bad) == []
