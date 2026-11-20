from aiforstudy.blocks.audit.contracts import RepairDirective
from aiforstudy.contracts.enums import ExecutionState
from aiforstudy.pipeline.execution_control import ExecutionPolicy
from aiforstudy.pipeline.graph import Node, dispatch_quality_gate
from aiforstudy.pipeline.repair import RepairBatch, RepairCoordinator, StopDecision, UnitRepairState
from aiforstudy.pipeline.runner import _satisfies
from aiforstudy.pipeline.state import NodeState


def directive(artifact):
    return RepairDirective(unit_id='CH01-S01', ticket_ids=(artifact,), owner='visual_renderer',
        reason='visible local error', kind='visual_layout', artifact_hashes={artifact:'sha256:'+'a'*64})


def test_each_object_has_six_persistent_repairs_without_starving_siblings():
    # D16-4：每对象内容返修上限 2 → 6。用满的那个产物必须停下，**同单元**的另一个
    # 产物仍要拿到自己的额度 —— 这正是"跨阶段、跨 resume 共享、按产物记账"的意思。
    state = UnitRepairState(unit_id='CH01-S01', content_rounds_used=6,
                            content_rounds_by_artifact={'figure:first':6})
    restored = UnitRepairState.from_dict(state.as_dict())
    c = RepairCoordinator(ExecutionPolicy())
    assert isinstance(c.plan([directive('figure:first')], restored), StopDecision)
    batch = c.plan([directive('figure:first'), directive('figure:second')], restored)
    assert isinstance(batch, RepairBatch)
    assert [job.artifact_id for job in batch.jobs] == ['figure:second']
    assert batch.round == 1


def test_only_reader_aggregation_accepts_terminal_local_failures():
    upstream = Node('audit:CH01-S01', 'audit', 'CH01-S01')
    state = NodeState(upstream.node_id, upstream.stage, upstream.scope_key,
                      execution_state=ExecutionState.FAILED)
    assert _satisfies(upstream, state, consumer=Node('render:CH01','render','CH01'))
    assert not _satisfies(upstream, state, consumer=Node('interaction:CH01-S01','interaction','CH01-S01'))
    state.execution_state = ExecutionState.CANCELLED
    assert not _satisfies(upstream, state, consumer=Node('render:CH01','render','CH01'))


def test_unknown_review_is_not_an_accepted_only_dispatch_gate():
    assert dispatch_quality_gate('media_plan', 'text_audit') is None
    assert dispatch_quality_gate('render', 'audit') is None


def test_actual_runner_resume_cannot_recreate_missing_attempt_journal(tmp_path):
    import pytest
    from support.runner_v5 import run_book, TeachingTransport
    from aiforstudy.pipeline.engine import resume_request
    from aiforstudy.pipeline.services import Providers
    result, store, engine, _ = run_book(tmp_path, until='text_audit')
    journal = store.root/'execution.json'
    assert journal.is_file()
    journal.unlink()
    client = TeachingTransport()
    with pytest.raises(Exception, match='execution|尝试|记录'):
        resume_request(engine.request, store.run_id, engine.workspace,
                       providers=Providers(llm=client), until='text_audit')
    assert client.author_calls == client.review_calls == 0
    assert not journal.exists()


def test_interrupted_repair_closes_reservation_without_refund_or_author_regeneration(tmp_path, monkeypatch):
    from support.runner_v5 import run_book, TeachingTransport
    from aiforstudy.pipeline.engine import resume_request
    from aiforstudy.pipeline.services import Providers
    from aiforstudy.pipeline.runner import Runner
    from aiforstudy.errors import Cancelled
    original = Runner._run_repair_job
    def interrupt(*args, **kwargs):
        raise Cancelled('controlled interrupted object repair')
    monkeypatch.setattr(Runner, '_run_repair_job', interrupt)
    _, store, engine, first = run_book(tmp_path, transport=TeachingTransport(reject_first=True), until='text_audit')
    before = store.load_state()['repair_states']['CH01-S01']
    assert before['active_batch_id']
    assert before['content_rounds_by_artifact']['lesson:CH01-S01'] == 1
    monkeypatch.setattr(Runner, '_run_repair_job', original)
    second = TeachingTransport(reject_first=True)
    _, resumed, _ = resume_request(engine.request, store.run_id, engine.workspace,
        providers=Providers(llm=second), until='text_audit')
    after = resumed.load_state()['repair_states']['CH01-S01']
    assert after['active_batch_id'] is None
    assert after['content_rounds_by_artifact']['lesson:CH01-S01'] == 2
    assert second.author_calls == 0 and second.patch_calls == 1
    assert any(row.get('state') == 'interrupted_closed' for row in after['history'])
