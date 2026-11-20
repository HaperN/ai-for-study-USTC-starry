"""修改计划 C3/E4/E2（2026-10-20）：卡片更新后定向重审一次；目录 version 按条目；节点按粒度匹配。"""
from types import SimpleNamespace

from aiforstudy.blocks.content.content_contracts import content_digest
from aiforstudy.blocks.content.source_context import reference_catalog_from_context
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.core.review_mode import review_mode
from aiforstudy.pipeline.concept_repairs import collect_review_refreshes
from support.content_case import teaching_case

OLD, NEW = r'z^a=e^{a\ln z}', r'z^a=e^{a\operatorname{Ln} z}'


def _catalog():
    return {'entries': [{'id': 'C-0522', 'kind': 'CONCEPT', 'label': '一般幂函数', 'version': 'v-old',
                         'unit_id': 'CH02', 'markdown': OLD}]}


def _unit(products, unit, body, status='verified'):
    content, _ = teaching_case()
    content.update(unit_id=unit, body_markdown=body)
    h = content_digest(content)
    identity = {'reference_catalog': _catalog()}
    write_json_atomic(products / 'lesson' / f'{unit}.json',
                      {'payload': {'content': content, 'content_hash': h, 'content_identity': identity}})
    write_json_atomic(products / 'text_audit' / f'{unit}.json',
                      {'payload': {'content_hash': h, 'review_outcome': {'status': status},
                                   'content_identity': identity}})


def _publish(products):
    entry = {'id': 'C-0522', 'name': '一般幂函数', 'formal': NEW}
    write_json_atomic(products / 'concepts/corrections.json', {'schema_version': 'SharedConceptCorrectionsV1',
        'revisions': [{'concept_id': 'C-0522', 'field': 'formal', 'before': OLD, 'after': NEW,
                       'entry': entry, 'version': 'v-new', 'origin_unit': 'CH02-S05-SS07'}]})


def test_only_verified_units_referencing_the_changed_card_are_rereviewed_once(tmp_path):
    products = tmp_path
    body = '一般幂函数见 [[CONCEPT:C-0522|一般幂函数]]，这里使用它的多值定义并讨论主值分支的选择。'
    with review_mode('balanced'):
        _unit(products, 'CH03-S01', body)
        _unit(products, 'CH03-S02', '本节不引用一般幂函数，只讨论积分路径的选取与估值。')
        _unit(products, 'CH03-S03', body, status='known_error')
        assert collect_review_refreshes(products) == {}            # 没有新版本时不触发
        _publish(products)
        found = collect_review_refreshes(products)
        assert list(found) == ['CH03-S01']
        assert found['CH03-S01']['changed'] == [['C-0522', 'v-new']]
        write_json_atomic(found['CH03-S01']['path'], {'dispatched': True})
        assert collect_review_refreshes(products) == {}            # 同一卡片版本只触发 1 次


def test_catalog_versions_are_per_entry_not_per_unit():
    concepts = {'concepts': [{'id': 'C-1', 'name': 'a', 'formal': 'x'}, {'id': 'C-2', 'name': 'b', 'formal': 'y'}]}
    first = reference_catalog_from_context({'source_inputs': {'unit': 'A'}, 'concept_index': concepts}).as_dict()
    second = reference_catalog_from_context({'source_inputs': {'unit': 'B'}, 'concept_index': concepts}).as_dict()
    assert first == second                                              # 不同单元，目录前缀相同
    changed = {'concepts': [{'id': 'C-1', 'name': 'a', 'formal': 'x2'}, concepts['concepts'][1]]}
    third = reference_catalog_from_context({'concept_index': changed}).as_dict()
    versions = lambda c: {e['id']: e['version'] for e in c['entries']}
    assert versions(third)['C-1'] != versions(first)['C-1']
    assert versions(third)['C-2'] == versions(first)['C-2']             # 只有真正改了的条目换 version


def test_unit_stage_lookup_never_falls_back_to_chapter_intro_unit():
    from aiforstudy.pipeline.graph import Node
    from aiforstudy.pipeline.runner import Runner
    nodes = [Node('text_audit:CH02', 'text_audit', 'CH02'), Node('lesson:CH02', 'lesson', 'CH02'),
             Node('render:CH02', 'render', 'CH02'), Node('text_audit:CH02-S01', 'text_audit', 'CH02-S01')]
    fake = SimpleNamespace(graph=SimpleNamespace(by_id=lambda: {n.node_id: n for n in nodes}))
    found = Runner._unit_node_ids(fake, 'CH02-S05')                   # 不在本次范围内的单元
    assert 'text_audit' not in found and 'lesson' not in found
    assert found['render'] == 'render:CH02'
    assert Runner._unit_node_ids(fake, 'CH02-S01')['text_audit'] == 'text_audit:CH02-S01'


# ── 修改计划 E7：停止后续跑可改并发，留交接记录；其他策略变化仍拒绝

import json  # noqa: E402
from dataclasses import replace  # noqa: E402

import pytest  # noqa: E402

from aiforstudy.errors import ContractError  # noqa: E402
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy  # noqa: E402


def _bound(path, policy, existing=False):
    control = ExecutionControl(policy)
    control.bind(path, run_id='run', require_existing=existing)
    return control


def test_resume_with_new_parallelism_keeps_attempts_and_writes_handoff(tmp_path):
    path = tmp_path / 'execution.json'
    first = _bound(path, ExecutionPolicy(parallelism=2))
    from aiforstudy.pipeline.execution_control import ExecutionScope
    lease = first.acquire_call(ExecutionScope('run', 'U', 'lesson', ''), fingerprint='f')
    lease.finish('response_received')
    resumed = _bound(path, ExecutionPolicy(parallelism=6), existing=True)
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert len(saved['attempts']) == 1
    assert saved['concurrency_handoffs'][0]['from']['parallelism'] == 2
    assert saved['concurrency_handoffs'][0]['to']['parallelism'] == 6
    assert resumed.policy.parallelism == 6


def test_resume_with_other_policy_change_is_still_refused(tmp_path):
    path = tmp_path / 'execution.json'
    _bound(path, ExecutionPolicy(parallelism=2))
    with pytest.raises(ContractError):
        _bound(path, ExecutionPolicy(parallelism=2, call_timeout_seconds=60.0), existing=True)


def test_cli_parallel_default_follows_profile_and_resume_accepts_parallel(tmp_path, monkeypatch):
    from aiforstudy import cli
    args = cli.build_parser().parse_args(['run', '--book', 'b', '--all', '--profile', 'release'])
    assert args.parallel is None
    monkeypatch.setattr('aiforstudy.pipeline.execution_control.load_policy',
                        lambda ws, profile, goal='deliver': ExecutionPolicy(parallelism=18 if profile == 'release' else 1))
    assert cli._resolve_parallel(args, None) == 18
    resume = cli.build_parser().parse_args(['resume', '--run', 'r', '--parallel', '4'])
    assert resume.parallel == 4
