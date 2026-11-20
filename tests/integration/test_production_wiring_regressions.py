"""Fresh offline acceptance counterexamples. No production modifications or real APIs."""


import copy


import json


import sys


import threading


from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


sys.path[:0] = [str(REPO/'tests'), str(REPO/'tests/integration')]


import pytest


from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy, AttemptLimitExceeded


from aiforstudy.pipeline.services import CancelToken, Providers


from aiforstudy.providers.gateway import Gateway, CallRequest


from aiforstudy.core.usage import UsageLedger


from aiforstudy.errors import BudgetExceeded, Cancelled


def test_default_production_graph_contains_text_gate():
    from aiforstudy.pipeline.graph import CANONICAL_STAGES
    stages = {s.name:s for s in CANONICAL_STAGES}
    print('ACTUAL_STAGE_ORDER', list(stages))
    print('ACTUAL_FIGURE_DEPENDENCIES', stages['figures'].requires)
    assert 'text_audit' in stages


def test_gateway_respects_unit_stage_limit_and_api_deadline(tmp_path):
    calls=[]
    def transport(**kw):
        calls.append(kw)
        return '{}', {'total_tokens': 1}
    guard=ExecutionControl(ExecutionPolicy(parallelism=1,attempt_limits={'transport':1},api_deadline_seconds=3))
    guard.bind(tmp_path/'execution.json',run_id='r')
    gateway=Gateway(transport=transport, roles={'lesson':{'model':'fake', 'effort':'off'}}, control=guard, run_id='r', book_id='b')
    request=CallRequest(role='lesson', messages=[{'role':'user','content':'x'}], unit_key='U', stage='lesson')
    assert gateway.call(request).ok
    stopped=False
    try:
        gateway.call(request)
    except AttemptLimitExceeded:
        stopped=True
    print('ACTUAL_GATEWAY', {'dispatches':len(calls), 'scope_attempts':len(guard.snapshot()['attempts']), 'timeout_values':[c['timeout'] for c in calls]})
    assert stopped, 'Second paid-boundary attempt dispatched although configured unit/stage limit is one'


def test_cancel_releases_actual_worker():
    from aiforstudy.providers.transport import CancelAwareCall
    cancel=CancelToken()
    started=threading.Event()
    release=threading.Event()
    def blocked():
        started.set()
        release.wait(3)
    call=CancelAwareCall(blocked,cancel=cancel,poll_seconds=.01,grace_seconds=.02)
    timer=threading.Timer(.04,lambda: cancel.cancel('acceptance'))
    timer.start()
    try:
        with pytest.raises(Cancelled):
            call()
        alive=call.thread.is_alive()
        print('ACTUAL_CANCELLATION', {'worker_alive_after_cancel':alive,'remote_unconfirmed':call.remote_unconfirmed})
    finally:
        release.set()
        timer.join()
        if call.thread:
            call.thread.join(1)
    assert not alive, 'Caller returned, but local worker remains alive'


def test_two_owners_in_one_batch_only_consume_one_round(tmp_path):
    import test_repair_runtime as R
    from test_m2_engine import make_workspace, request_for
    from aiforstudy.pipeline.stubs import smoke_registry
    from aiforstudy.pipeline.engine import build_engine, execute
    workspace,book=make_workspace(tmp_path)
    registry=smoke_registry()
    registry.register('lesson', R._make_emitter([]))
    registry.register('figures', R._make_emitter([]))
    registry.register('audit', R._make_emitter([[R._mk(tickets=('text-one',)),R._mk(owner='visual_planner',kind='visual_layout',tickets=('figure-one',))],[R._mk(tickets=('text-two',))],[]]))
    engine=build_engine(request_for(book.book_id,units=(R.UNIT,),parallel=1),workspace,stage_registry=registry,until='audit')
    _,store=execute(engine)
    events=[e for e in store.events_after() if e.kind.startswith('repair.')]
    planned=[e for e in events if e.kind=='repair.batch.planned']
    data={'planned_rounds':[e.data['round'] for e in planned], 'content_rounds_used':store.load_state()['repair_states'][R.UNIT]['content_rounds_used'], 'events':[e.as_dict() for e in events]}
    (tmp_path / 'multi-owner-batch.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
    print('ACTUAL_BATCHES',{k:v for k,v in data.items() if k!='events'})
    assert len(planned)==2, 'First batch has two jobs, but must leave one more batch available'



def test_retired_media_or_writer_protocol_cannot_be_registered():
    import pytest
    from aiforstudy.errors import ContractError
    from aiforstudy.pipeline.registry import release_registry
    for version in ('3.0','4.0'):
        with pytest.raises(ContractError,match='只读'):release_registry(graph_version=version)
