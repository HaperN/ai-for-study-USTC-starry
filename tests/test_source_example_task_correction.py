"""Empty structural example scaffolds admit only the exact frozen task quote."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from aiforstudy.blocks.content.generation_corrections import LocalCorrectionRequired
from aiforstudy.blocks.content.source_inventory import (
    SourceInventoryV1, build_inventory_messages, extract_source_inventory, validate_source_inventory,
)
from aiforstudy.errors import ContractError
from test_generation_corrections import SequenceClient
from test_source_inventory import context, response


def example_case():
    source = context('例2 求 $x/x$。解 结果为1。例3 求实部。解 代入可得。'
                     '例4 证明所给关系。证 由定义。例5 求模。解 按公式。')
    good = response(source, extra=False)
    for row, original in zip(good['obligations'], source.source_examples):
        row['conclusion'] = original.statement
    bad = deepcopy(good)
    for row in bad['obligations']:
        row['conclusion'] = ''
    return source, bad, good


@pytest.mark.parametrize('blank', ['', '  \n'])
def test_all_empty_structural_example_tasks_are_reported_with_exact_candidates(blank):
    source, bad, _ = example_case()
    for row in bad['obligations']:
        row['conclusion'] = blank
    with pytest.raises(LocalCorrectionRequired) as caught:
        validate_source_inventory(source, bad)
    error = caught.value
    assert error.allowed_paths == [f'/obligations/{i}/conclusion' for i in range(4)]
    assert [row['candidates'] for row in error.errors] == [[e.statement] for e in source.source_examples]
    assert all(row['code'] == 'blank_source_example_task' and row['value'] == blank for row in error.errors)


def test_exact_task_correction_saves_both_responses_and_replays_without_a_call(tmp_path):
    """R3-1：内层纠正轮取消 —— 空结论答复一次即判，反馈（4 条）写进 journal。"""
    source, bad, good = example_case(); journal = tmp_path / 'journal.json'; saved = []
    client = SequenceClient(bad)
    with pytest.raises(ContractError, match='逐条修正'):
        extract_source_inventory(source, client, saved.append, correction_journal=journal)
    assert saved == [bad] and len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    assert len(feedback['errors']) == 4
    # 合法答复另起一次调用通过，journal 定稿后重放不再花调用
    ok_journal = tmp_path / 'journal-ok.json'
    result = extract_source_inventory(source, SequenceClient(good), correction_journal=ok_journal)
    assert [o.conclusion for o in result.source_obligations] == [e.statement for e in source.source_examples]
    assert result.source_examples == source.source_examples
    replay = SequenceClient()
    assert extract_source_inventory(source, replay, correction_journal=ok_journal).digest == result.digest
    assert replay.calls == []


@pytest.mark.parametrize('field,reason', [
    ('conditions', 'structural_requirements'), ('source_excerpt', 'example_source'),
    ('kind', 'structural_kind'), ('id', 'missing_structural'),
    ('coverage', 'block_coverage'), ('source_identity', 'source_identity'),
])
def test_task_correction_still_rejects_invalid_source_and_identity_changes(field, reason):
    source, bad, good = example_case()
    if field == 'conditions':
        first = source.source_obligations[0].model_copy(update={'conditions': ['x 非零']})
        source = source.model_copy(update={'source_obligations': [first] + source.source_obligations[1:]})
        source = source.model_copy(update={'source_context_hash': source.digest})
        for candidate in (bad, good):
            candidate['source_context_hash'] = source.digest
            candidate['obligations'][0]['conditions'] = ['x 非零']
        good['obligations'][0]['conditions'] = []
    elif field == 'source_excerpt': good['obligations'][0][field] = '换一个题目。'
    elif field == 'kind': good['obligations'][0][field] = 'theorem'
    elif field == 'id': good['obligations'][0][field] = 'NEW-replacement'
    elif field == 'coverage': good[field] = []
    else: good['unit_id'] = 'OTHER'
    client = SequenceClient(bad, good)
    with pytest.raises(ContractError, match=reason):
        extract_source_inventory(source, client, max_corrections=1)
    assert len(client.calls) == 2


@pytest.mark.parametrize('value', ['', '  \n', '自行算得答案是1。', '换一种没有逐字依据的任务摘要。'])
def test_blank_scaffold_only_admits_the_unique_frozen_task_candidate(value, tmp_path):
    """R3-1：**首份答复**只接受两种 conclusion —— 空的，或精确等于唯一冻结候选。

    为什么首份就要判：内层纠正轮取消后，原先只在纠正轮运行的
    `_guard_inventory_correction`（"纠正值必须等于冻结候选"）永远不会执行
    （`prior_error` 在生产参数下始终为 None），于是"自行算得答案是1。"这类
    **非空错值**会被悄悄接受。等式检查已搬进 `validate_source_inventory`。
    """
    source, bad, good = example_case(); good['obligations'][0]['conclusion'] = value
    journal = tmp_path / 'journal.json'
    with pytest.raises(ContractError, match='逐条修正'):
        extract_source_inventory(source, SequenceClient(good), correction_journal=journal)
    feedback = json.loads(journal.read_text())['merged_feedback']
    assert feedback['allowed_paths'] == ['/obligations/0/conclusion']
    assert feedback['errors'][0]['candidates'] == [source.source_examples[0].statement]
    assert feedback['errors'][0]['path'] == '/obligations/0/conclusion'
    if value.strip():
        # 非空错值必须判"不等于候选"，不是当普通文案放行
        assert feedback['errors'][0]['code'] == 'source_example_task_mismatch'
    exact = deepcopy(good); exact['obligations'][0]['conclusion'] = source.source_examples[0].statement
    result = extract_source_inventory(source, SequenceClient(exact))
    assert result.source_obligations[0].conclusion == source.source_examples[0].statement


@pytest.mark.parametrize('problem', ['nonempty_prior', 'no_example', 'ambiguous_example', 'new_id', 'other_kind'])
def test_no_guessing_authority_without_an_empty_known_unique_source_example(problem):
    source, bad, _ = example_case()
    # Keep one known row to make eligibility and ambiguity explicit.
    source = source.model_copy(update={'source_obligations': source.source_obligations[:1],
                                       'source_examples': source.source_examples[:1]})
    bad['obligations'] = bad['obligations'][:1]
    if problem == 'nonempty_prior':
        source = source.model_copy(update={'source_obligations': [source.source_obligations[0].model_copy(update={'conclusion': '已有冻结结论'})]})
    elif problem == 'no_example': source = source.model_copy(update={'source_examples': []})
    elif problem == 'ambiguous_example': source = source.model_copy(update={'source_examples': source.source_examples * 2})
    elif problem == 'new_id': bad['obligations'][0]['id'] = 'NEW-example'
    else:
        source = source.model_copy(update={'source_obligations': [source.source_obligations[0].model_copy(update={'kind': 'theorem', 'extraction_method': 'source_heading'})]})
        bad['obligations'][0]['kind'] = 'theorem'
    source = source.model_copy(update={'source_context_hash': source.digest})
    bad['source_context_hash'] = source.digest
    with pytest.raises(ContractError) as caught:
        validate_source_inventory(source, bad)
    assert not isinstance(caught.value, LocalCorrectionRequired)


def test_schema_still_requires_nonempty_conclusions_and_prompt_explains_scaffolds():
    source, bad, _ = example_case()
    with pytest.raises(ValidationError): SourceInventoryV1.model_validate(bad)
    messages = build_inventory_messages(source); system = messages[0]['content']
    assert '非空' in system and '任务' in system and '不得补算答案' in system
    assert json.loads(messages[1]['content'])['source_example_tasks'] == [
        {'obligation_id': obligation.id, 'statement': example.statement}
        for obligation, example in zip(source.source_obligations, source.source_examples)]


def test_empty_tasks_then_coverage_each_get_one_shot_and_replay_without_a_call(tmp_path):
    """R3-1：原"任务 + coverage 共享两轮配额"用例改写为新机制。

    一次答复即判：空结论先被拦（反馈持久），coverage 问题要在**下一次**答复里独立判；
    合法的完整答复通过，且 journal 定稿后重放不再花调用。
    """
    source, bad, good = example_case(); bad['coverage'] = []
    tasks_only = deepcopy(good); tasks_only['coverage'] = []
    journal = tmp_path / 'journal.json'
    client = SequenceClient(bad)
    with pytest.raises(ContractError, match='逐条修正'):
        extract_source_inventory(source, client, correction_journal=journal)
    assert len(client.calls) == 1
    state = json.loads(journal.read_text())
    assert len(state['attempts']) == 1
    assert {row['code'] for row in state['merged_feedback']['errors']} == {'blank_source_example_task'}
    # 结论修好但 coverage 仍缺：仍是一次即判
    coverage_client = SequenceClient(tasks_only)
    with pytest.raises(ContractError, match='逐条修正'):
        extract_source_inventory(source, coverage_client)
    assert len(coverage_client.calls) == 1
    ok_journal = tmp_path / 'journal-ok.json'
    result = extract_source_inventory(source, SequenceClient(good), correction_journal=ok_journal)
    replay = SequenceClient()
    assert extract_source_inventory(source, replay, correction_journal=ok_journal).digest == result.digest
    assert replay.calls == []
