"""修改计划 B6（2026-10-20）：取消时不排返修、不扣轮次；续跑范围外的中断批次写明原因。

CH09-S02-SS06：返修批次在取消瞬间排出（扣 1 轮），恢复程序只跑 render/delivery，
批次被关闭却没有重核，剩余轮次从未使用，也没有任何记录说明原因。
"""
from types import SimpleNamespace

from aiforstudy.pipeline.repair import UnitRepairState
from aiforstudy.pipeline.runner import Runner


class FakeStore:
    def __init__(self, repair_states):
        self.state = {'status': 'running', 'repair_states': repair_states}
        self.events = []

    def load_state(self):
        return self.state

    def set_status(self, status, **fields):
        self.state.update(status=status, **fields)

    def emit(self, kind, **data):
        self.events.append((kind, data))


def fake_runner(repair_states, *, cancelled=False):
    runner = SimpleNamespace(store=FakeStore(repair_states), _repair_states={}, _repair_resume_rerun=set(),
                             services=SimpleNamespace(cancel=SimpleNamespace(cancelled=cancelled)),
                             graph=SimpleNamespace(graph_version='legacy-test'))
    runner._repair_state = lambda unit: Runner._repair_state(runner, unit)
    runner._persist_repair_states = lambda: Runner._persist_repair_states(runner)
    runner._dependent_closure = lambda ids: {'render:CH09'}
    return runner


def test_repair_request_during_cancel_is_deferred_without_spending_a_round():
    runner = fake_runner({}, cancelled=True)
    runner._repair = SimpleNamespace(plan=lambda *a: (_ for _ in ()).throw(AssertionError('must not plan')))
    directive = SimpleNamespace(unit_id='CH09-S02-SS06', artifact_hashes={}, ticket_ids=('t1',))
    node = SimpleNamespace(node_id='text_audit:CH09-S02-SS06', stage='text_audit')
    Runner._handle_repair_requests(runner, node, SimpleNamespace(repair_requests=(directive,), repair_routes=()), {})
    saved = runner.store.state['repair_states']['CH09-S02-SS06']
    assert saved['content_rounds_used'] == 0
    assert saved['active_batch_id'].startswith('deferred-on-cancel:')
    assert runner.store.events[-1][0] == 'repair.deferred'


def _state(batch_id, context):
    return UnitRepairState(unit_id='CH09-S02-SS06', content_rounds_used=1, active_batch_id=batch_id,
                           active_batch_context=context).as_dict()


def test_deferred_repair_is_rechecked_on_resume_when_unit_is_in_scope():
    runner = fake_runner({'CH09-S02-SS06': _state('deferred-on-cancel:x',
                          {'origin_node_id': 'text_audit:CH09-S02-SS06', 'deferred': True})})
    Runner._recover_interrupted_repairs(runner, {'text_audit:CH09-S02-SS06': object()})
    saved = runner.store.state['repair_states']['CH09-S02-SS06']
    assert saved['active_batch_id'] is None and saved['history'][-1]['state'] == 'deferred_resumed'
    assert 'text_audit:CH09-S02-SS06' in runner._repair_resume_rerun


def test_deferred_repair_waits_for_a_run_that_includes_the_unit():
    runner = fake_runner({'CH09-S02-SS06': _state('deferred-on-cancel:x',
                          {'origin_node_id': 'text_audit:CH09-S02-SS06', 'deferred': True})})
    Runner._recover_interrupted_repairs(runner, {'render:CH09': object()})
    assert runner.store.state['repair_states']['CH09-S02-SS06']['active_batch_id'] == 'deferred-on-cancel:x'


def test_out_of_scope_interrupted_batch_records_why_no_repair_happened():
    runner = fake_runner({'CH09-S02-SS06': _state('sha256:f0eb6', {'origin_node_id': 'text_audit:CH09-S02-SS06'})})
    Runner._recover_interrupted_repairs(runner, {'render:CH09': object()})
    saved = runner.store.state['repair_states']['CH09-S02-SS06']
    assert saved['active_batch_id'] is None
    assert saved['history'][-1]['rerun'] is False and '不是额度耗尽' in saved['history'][-1]['not_rerun_reason']
    assert not runner._repair_resume_rerun
