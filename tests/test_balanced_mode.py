"""Balanced reviews resume deliberately without restoring legacy machine gates."""
import importlib

import pytest

from aiforstudy.contracts.enums import QualityState
from aiforstudy.contracts.stage import StageResult
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.pipeline.engine import build_engine
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy
from aiforstudy.pipeline.graph import ExecutionGraph, Node
from aiforstudy.pipeline.repair import RepairCoordinator, RepairDirective, RepairBatch, StopDecision, UnitRepairState
from aiforstudy.pipeline.runner import Runner
from aiforstudy.pipeline.services import Providers, StageRegistry
from aiforstudy.pipeline.state import RunStore
from support.runner_v5 import setup_book, _request, UNIT


def test_balanced_freezes_machine_checks_but_enables_substantive_review():
    policy = importlib.import_module('aiforstudy.core.review_mode')
    from aiforstudy.core.digest import digest_of
    with policy.review_mode('balanced'):
        assert policy.current_review_mode() == 'balanced'
        assert policy.reviews_frozen() and policy.substantive_reviews_enabled()
        assert digest_of({'duration': 2})
        report = policy.frozen_review()
        assert report['review_mode'] == 'balanced' and report['review_status'] == 'not_run'
        assert 'json_only' not in str(report) and '仅检查 JSON' not in str(report)
    for mode in ('full', 'json_only'):
        with policy.review_mode(mode):
            assert not policy.substantive_reviews_enabled()


def test_new_policy_stops_after_two_repairs_and_keeps_old_six_round_policy(tmp_path):
    coordinator = RepairCoordinator(ExecutionPolicy())
    state = UnitRepairState(unit_id=UNIT)
    directive = RepairDirective(unit_id=UNIT, ticket_ids=('open-issue',), owner='lesson',
        kind='text_content', reason='答案错误', artifact_hashes={'lesson:'+UNIT:'sha256:'+'a'*64})
    for count in (0, 1):
        state.content_rounds_by_artifact['lesson:'+UNIT] = count
        assert isinstance(coordinator.plan([directive], state), RepairBatch)
    state.content_rounds_by_artifact['lesson:'+UNIT] = 2
    stopped = coordinator.plan([directive], state)
    assert isinstance(stopped, StopDecision) and stopped.stopped_by == 'content_rounds_exhausted'
    assert stopped.unresolved == ('open-issue',)
    old_policy = ExecutionPolicy(content_repair_rounds_per_object=6)
    ExecutionControl(old_policy).bind(tmp_path/'execution.json', run_id='old')
    restored = ExecutionControl(ExecutionPolicy(**old_policy.as_dict()))
    restored.bind(tmp_path/'execution.json', run_id='old', require_existing=True)
    assert isinstance(RepairCoordinator(restored.policy).plan([directive], state), RepairBatch)


def _runner(tmp_path, audit_quality, mode='balanced'):
    workspace = setup_book(tmp_path)
    write_json_atomic(workspace.root/'config/review_mode.json', {'mode':mode})
    if mode != 'balanced':
        # Model a historical six-round run even after repository defaults change.
        import json
        path = workspace.root/'config/execution.json'
        execution = json.loads(path.read_text())
        execution['content_repair_rounds_per_object'] = 6
        write_json_atomic(path, execution)
    seen = []
    def stage(ctx):
        policy = importlib.import_module('aiforstudy.core.review_mode')
        seen.append((policy.current_review_mode(), policy.reviews_frozen(), policy.substantive_reviews_enabled()))
        write_json_atomic(workspace.root/'config/review_mode.json', {'mode':'full'})
        quality = audit_quality if ctx.node.stage == 'text_audit' else (
            QualityState.NOT_CHECKED if ctx.node.stage == 'lesson' else QualityState.ACCEPTED)
        return StageResult.ok(ctx.node.stage, quality_state=quality)
    stages = ('lesson', 'text_audit', 'audit', 'render')
    engine = build_engine(_request(), workspace, providers=Providers(),
        stage_registry=StageRegistry({name:stage for name in stages}), until='render')
    nodes = tuple(Node(name+':'+UNIT, name, UNIT,
        requires=(stages[i-1]+':'+UNIT,) if i else ()) for i,name in enumerate(stages))
    graph = ExecutionGraph(nodes, goal='deliver', profile='release')
    store = RunStore.create(tmp_path/'runs', engine.request, engine.plan, control=engine.services.control)
    return Runner(engine.services, graph, store), seen


@pytest.mark.parametrize('quality', [QualityState.NEEDS_REVIEW, QualityState.REJECTED, QualityState.NOT_CHECKED])
def test_balanced_runner_keeps_failed_or_missing_reviews_unresolved(tmp_path, quality):
    runner, seen = _runner(tmp_path, quality)
    result = runner.run()
    # 用户 2026-10-20 定：balanced 按正文判定——正文已生成且页面正常即运行通过（退出码 0）；
    # 审核未通过仍如实记在 review_status，不授予正式发布认证。
    assert result.status == 'succeeded' and result.exit_code == 0
    assert result.review_status == 'needs_review'
    assert not result.release_ready and result.scope_accepted
    assert not result.quality_overrides
    assert seen == [('balanced', True, True)] * 4
    assert runner._repair.max_content_rounds == 2


def test_balanced_runner_passes_reviews_without_certifying_frozen_checks_and_resumes_mode(tmp_path):
    runner, _ = _runner(tmp_path, QualityState.ACCEPTED)
    result = runner.run()
    assert result.status == 'succeeded' and result.review_status == 'passed'
    assert result.quality_counts['not_checked'] == 1
    assert not result.release_ready and result.scope_accepted   # 按正文判定通过；仍不授予正式发布认证
    resumed = Runner(runner.services, runner.graph, runner.store, resume=True).run()
    assert resumed.review_mode == 'balanced' and resumed.review_status == 'passed'


@pytest.mark.parametrize('mode', ['full', 'json_only'])
def test_old_run_resume_preserves_six_rounds_after_new_default_changes(tmp_path, mode):
    runner, _ = _runner(tmp_path, QualityState.ACCEPTED, mode)
    assert runner._repair.max_content_rounds == 6
    workspace = runner.services.workspace
    write_json_atomic(workspace.root/'config/review_mode.json', {'mode':'balanced'})
    # A fresh controller reflects current defaults; resume must use the old
    # run's persisted content allowance without resetting its attempts.
    from dataclasses import replace
    runner.services.control = ExecutionControl(replace(runner.services.control.policy,
        content_repair_rounds_per_object=2))
    resumed = Runner(runner.services, runner.graph, runner.store, resume=True)
    assert resumed._review_mode == mode
    assert resumed._repair.max_content_rounds == 6


def test_stage_cache_separates_review_modes_without_invalidating_preparation(tmp_path):
    runner, _ = _runner(tmp_path, QualityState.ACCEPTED)
    runner._input_snapshot = {}
    review = Node('text_audit:'+UNIT, 'text_audit', UNIT)
    preparation = Node('body', 'body', 'book')
    runner._review_mode = 'json_only'
    frozen_review = runner._stage_snapshot(review)
    frozen_preparation = runner._stage_snapshot(preparation)
    runner._review_mode = 'balanced'
    substantive_review = runner._stage_snapshot(review)
    assert frozen_review != substantive_review, 'a not-run cached review cannot satisfy balanced'
    assert runner._stage_snapshot(preparation) == frozen_preparation
    write_json_atomic(runner.services.workspace.root/'config/review_mode.json', {'mode':'full'})
    assert runner._stage_snapshot(review) == substantive_review, 'running mode is frozen, not live config'
