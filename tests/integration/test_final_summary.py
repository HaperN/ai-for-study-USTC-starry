"""Every normally returned engine outcome leaves a final, inspectable conclusion."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from aiforstudy.contracts.enums import QualityState
from aiforstudy.contracts.stage import StageResult
from aiforstudy.pipeline.engine import build_engine, execute
from test_m2_engine import make_workspace, request_for

#: 仓库根（用来取受版本控制的那份权威策略副本）
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('outcome', [
    'succeeded', 'needs_review', 'blocked', 'failed', 'cancelled'])
def test_final_summary_exists_for_all_normal_outcomes(tmp_path, outcome):
    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id, ('CH01-S01',))
    engine = build_engine(request, workspace, until='mineru')
    original = engine.services.stages.get('mineru')

    def stage(context):
        if outcome == 'blocked':
            return StageResult.blocked('mineru', 'controlled missing input')
        if outcome == 'failed':
            return StageResult.failed('mineru', 'controlled local failure')
        result = original(context)
        if outcome == 'needs_review':
            return replace(result, quality_state=QualityState.NEEDS_REVIEW)
        return result

    engine.services.stages.register('mineru', stage)
    if outcome == 'cancelled':
        engine.services.cancel.cancel('controlled cancellation')
    result, store = execute(engine)
    expected_status = 'failed' if outcome == 'blocked' else outcome
    assert result.status == expected_status
    if outcome == 'blocked':
        assert result.blocked_nodes == {'mineru': 'controlled missing input'}
    final = json.loads((store.root / 'final.json').read_text())
    assert final == {**result.as_dict(), 'scope': ['CH01-S01'], 'scope_kind': 'units'}
    assert final['publication'] == {} and final['release_ready'] is False
    assert final['status'] == store.load_state()['status']


def test_final_summary_does_not_invent_an_unresolved_scope(tmp_path):
    workspace, book = make_workspace(tmp_path)
    (book.parse / 'chapters.json').unlink()
    engine = build_engine(request_for(book.book_id, ('CH01-S01',)), workspace, until='mineru')
    engine.services.stages.register('mineru',
        lambda context: StageResult.blocked('mineru', 'controlled unavailable preparation'))
    result, store = execute(engine)
    assert result.blocked_nodes
    final = json.loads((store.root / 'final.json').read_text())
    assert final['scope'] is None and final['scope_kind'] == 'units'
    assert final['scope_accepted'] is False


def test_final_summary_and_state_agree_when_preparation_cannot_resolve_scope(tmp_path):
    workspace, book = make_workspace(tmp_path)
    (book.parse / 'chapters.json').unlink()
    engine = build_engine(request_for(book.book_id, ('CH01-S01',)), workspace)
    result, store = execute(engine)
    assert result.status == 'failed' and result.failure_stage == 'scope'
    state = store.load_state()
    for field in ('status', 'exit_code', 'scope_accepted', 'release_ready', 'failure_stage'):
        assert state[field] == result.as_dict()[field]
    final = json.loads((store.root / 'final.json').read_text())
    assert final == {**result.as_dict(), 'scope': None, 'scope_kind': 'units'}


def test_final_summary_records_the_execution_policy_file(tmp_path):
    workspace, book = make_workspace(tmp_path)
    engine = build_engine(request_for(book.book_id, ('CH01-S01',)), workspace, until='mineru')
    result, store = execute(engine)
    final = json.loads((store.root / 'final.json').read_text())
    assert final['policy_source'] == result.policy_source == str(workspace.config / 'execution.json')
    assert final['policy_digest'] == engine.services.control.policy.digest()
    assert final['policy_digest'] == result.policy_digest


def test_missing_execution_policy_is_an_explicit_configuration_error(tmp_path):
    from aiforstudy.errors import ConfigError
    workspace, book = make_workspace(tmp_path)
    (workspace.config / 'execution.json').unlink()
    with pytest.raises(ConfigError):
        build_engine(request_for(book.book_id), workspace)


def test_policy_source_is_provenance_only_and_never_enters_the_digest():
    """来源（含**机器相关的绝对路径**）不许进护栏摘要。

    这不是洁癖：`digest()` 是给"额度有没有被改"用的，resume 会认它（§6.1）。
    如果来源路径进了摘要，那么**换一台机器／换个工作区目录**就会让摘要变化，
    于是"额度没改"的运行被误判成"改了额度"而拒绝续跑。
    所以：`policy_source` 是字段、会进运行记录，但**不在** `as_dict()` 里。
    """
    from aiforstudy.pipeline.execution_control import ExecutionPolicy

    base = ExecutionPolicy(parallelism=1)
    moved = ExecutionPolicy(parallelism=1,
                         policy_source='/somewhere/else/config/execution.json')

    assert 'policy_source' not in base.as_dict(), (
        "policy_source 不该进 as_dict：它是溯源信息，不是护栏")
    assert base.digest() == moved.digest(), (
        "来源不同但额度相同 → 摘要必须相同，否则换目录就会拒绝 resume")
    assert moved.policy_source.endswith('execution.json')
