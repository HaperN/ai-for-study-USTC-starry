"""Production execution safety is independent of billing and survives dispatch crashes."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest


def control(tmp_path, **kwargs):
    from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy
    result = ExecutionControl(ExecutionPolicy(**kwargs))
    result.bind(tmp_path / 'execution.json', run_id='run')
    return result


def gateway(tmp_path, transport, ctl=None):
    from aiforstudy.providers.gateway import Gateway
    from aiforstudy.pipeline.services import CancelToken
    return Gateway(transport=transport, roles={'lesson':{'model':'offline','effort':'high','max_tokens':100}},
        control=ctl or control(tmp_path), cancel=CancelToken(), run_id='run',book_id='book')


def request():
    from aiforstudy.providers.gateway import CallRequest
    return CallRequest(role='lesson',messages=[{'role':'user','content':'test'}],stage='lesson',unit_key='U1')


def test_production_request_has_no_cumulative_budget():
    from aiforstudy.contracts.request import RunRequest, Selectors
    req=RunRequest(book_id='book',selectors=Selectors('all')).validate()
    assert 'budget' not in req.as_dict()
    assert 'budget' not in req.plan_inputs()
    assert RunRequest.from_dict(req.as_dict()) == req


def test_real_transport_constructs_without_approved_budget_or_network():
    from aiforstudy.providers.transport import RealTransport
    t=RealTransport(model='offline',api_key='offline-not-a-real-key')
    assert t.calls == 0
    assert not any('budget' in key or 'tokens' in key for key in t.report())


def test_malformed_usage_cannot_break_valid_response_or_cache(tmp_path):
    calls=[]
    def transport(**kwargs):
        calls.append(kwargs)
        return '{"answer":42}',{'total_tokens':'broken','prompt_tokens':{'bad':True}}
    (tmp_path/'usage.jsonl').write_text('not json\n')
    g=gateway(tmp_path,transport); g.cache_dir=tmp_path/'cache'
    assert g.call(request()).payload == {'answer':42}
    assert g.call(request()).cached
    assert len(calls) == 1
    assert (tmp_path/'usage.jsonl').read_text() == 'not json\n'
    assert 'usage' not in next((tmp_path/'cache').glob('*.json')).read_text()


def test_attempt_is_durable_before_dispatch_and_restart_never_refunds(tmp_path):
    from aiforstudy.pipeline.execution_control import AttemptLimitExceeded
    calls=[]
    def crash(**kwargs):
        data=json.loads((tmp_path/'execution.json').read_text())
        assert data['attempts'][-1]['state'] == 'dispatched'
        calls.append(1)
        raise KeyboardInterrupt('crash after dispatch')
    with pytest.raises(KeyboardInterrupt): gateway(tmp_path,crash).call(request())
    def timeout(**kwargs): calls.append(1); raise TimeoutError('offline timeout')
    assert not gateway(tmp_path,timeout).call(request()).ok
    with pytest.raises(AttemptLimitExceeded): gateway(tmp_path,timeout).call(request())
    assert len(calls)==2
    assert len(json.loads((tmp_path/'execution.json').read_text())['attempts'])==2


def test_concurrent_same_request_cannot_exceed_total_attempts(tmp_path):
    from aiforstudy.pipeline.execution_control import AttemptLimitExceeded
    ctl=control(tmp_path,parallelism=2); calls=[]
    def timeout(**kwargs): calls.append(1); raise TimeoutError('offline')
    def run(_):
        try: return gateway(tmp_path,timeout,ctl).call(request())
        except AttemptLimitExceeded: return None
    with ThreadPoolExecutor(max_workers=6) as pool: list(pool.map(run,range(12)))
    assert len(calls)==2


def test_each_attempt_gets_full_timeout_without_cumulative_time_budget(tmp_path):
    times=[]
    def timeout(**kwargs): times.append(kwargs['timeout']); raise TimeoutError('offline')
    assert not gateway(tmp_path,timeout,control(tmp_path,api_deadline_seconds=300)).call(request()).ok
    assert len(times)==2 and all(299 < x <= 300 for x in times)


def test_missing_resume_journal_fails_closed(tmp_path):
    from aiforstudy.errors import ContractError
    from aiforstudy.pipeline.execution_control import ExecutionControl
    with pytest.raises(ContractError,match='执行记录'):
        ExecutionControl().bind(tmp_path/'missing.json',run_id='run',require_existing=True)


def test_corrupt_attempt_sequence_cannot_refund_slots(tmp_path):
    from aiforstudy.errors import ContractError
    ctl=control(tmp_path)
    path=tmp_path/'execution.json'
    data=json.loads(path.read_text())
    data['attempts']=[{'attempt_id':'x','fingerprint':'f','scope':{},'state':'failed','attempt':0}]
    path.write_text(json.dumps(data))
    with pytest.raises(ContractError,match='尝试'): control(tmp_path)


def test_queued_call_cancellation_does_not_consume_or_dispatch(tmp_path):
    from aiforstudy.pipeline.execution_control import ExecutionScope
    from aiforstudy.pipeline.services import CancelToken
    from aiforstudy.errors import Cancelled
    ctl=control(tmp_path,parallelism=1)
    held=ctl.acquire_call(ExecutionScope('run'),fingerprint='held')
    token=CancelToken(); ready=threading.Event(); called=[]
    g=gateway(tmp_path,lambda **kw:called.append(kw),ctl); g.cancel=token
    def queued():
        ready.set()
        with pytest.raises(Cancelled): g.call(request())
    with ThreadPoolExecutor(max_workers=1) as pool:
        f=pool.submit(queued); assert ready.wait(1)
        token.cancel('offline cancellation')
        try: f.result(timeout=1)
        finally: held.finish()
    assert not called
    assert len(ctl.snapshot()['attempts'])==1


def test_persistence_failure_prevents_transport_dispatch(tmp_path,monkeypatch):
    ctl=control(tmp_path); called=[]
    def fail(): raise OSError('offline disk failure')
    monkeypatch.setattr(ctl,'_persist',fail)
    with pytest.raises(OSError,match='disk failure'):
        gateway(tmp_path,lambda **kw:called.append(kw),ctl).call(request())
    assert not called


def test_cli_and_ui_have_no_budget_override_and_same_identity():
    from aiforstudy.ui.api import normalize_request,request_to_cli_args
    from aiforstudy.errors import ContractError
    req=normalize_request({'book_id':'book','all':True})
    assert not any('budget' in arg for arg in request_to_cli_args(req))
    with pytest.raises(ContractError): normalize_request({'book_id':'book','all':True,'budget_tokens':100})


def test_cancelled_inflight_attempt_consumed_but_next_slot_remains(tmp_path):
    from aiforstudy.errors import Cancelled
    g=gateway(tmp_path,lambda **kw: (_ for _ in ()).throw(Cancelled('offline stop')))
    with pytest.raises(Cancelled):g.call(request())
    assert g.control.snapshot()['attempts'][0]['state']=='cancelled'
    fresh=gateway(tmp_path,lambda **kw: ('{"ok":true}',{}))
    assert fresh.call(request()).ok
    assert len(fresh.control.snapshot()['attempts'])==2


def test_clock_advance_does_not_consume_cumulative_time(tmp_path):
    from aiforstudy.pipeline.execution_control import ExecutionControl,ExecutionPolicy,ExecutionScope
    now=[0.0]
    ctl=ExecutionControl(ExecutionPolicy(api_deadline_seconds=None),clock=lambda:now[0]);ctl.bind(tmp_path/'execution.json',run_id='run')
    now[0]=1000000
    ctl.check()
    lease=ctl.acquire_call(ExecutionScope('run'),fingerprint='f')
    assert lease.timeout_seconds()==900
    now[0]+=5
    assert lease.timeout_seconds()==895
    lease.finish()
    now[0]+=1000000
    fresh=ctl.acquire_call(ExecutionScope('run'),fingerprint='f')
    assert fresh.timeout_seconds()==900
    fresh.finish()


def test_stage_gateway_uses_runner_journal_not_work_subdirectory(tmp_path):
    from types import SimpleNamespace
    from aiforstudy.pipeline.gateway_client import _gateway_client
    from aiforstudy.pipeline.services import CancelToken
    ctl=control(tmp_path)
    store=SimpleNamespace(root=tmp_path,work=tmp_path/'work',run_id='run')
    ctx=SimpleNamespace(services=SimpleNamespace(control=ctl,cancel=CancelToken(),store=store,
        providers=SimpleNamespace(llm=lambda **kw: ('{"answer":true}',{'total_tokens':'bad'}))),
        workspace=SimpleNamespace(config_json=lambda _: {'roles':{'lesson':{'model':'offline','effort':'high'}}}),
        node=SimpleNamespace(stage='lesson',scope_key='U'),context=SimpleNamespace(book_id='book'))
    client,_=_gateway_client(ctx,role='lesson',bucket='lesson')
    assert client.chat_json([])['data']=={'answer':True}
    assert len(ctl.snapshot()['attempts'])==1
    assert not (tmp_path/'work/execution.json').exists()


def test_failed_restore_cannot_be_bypassed_by_reusing_controller(tmp_path):
    from aiforstudy.errors import ContractError
    from aiforstudy.pipeline.execution_control import ExecutionControl,ExecutionScope
    path=tmp_path/'execution.json';path.write_text('broken')
    ctl=ExecutionControl()
    with pytest.raises(ContractError):ctl.bind(path,run_id='run')
    with pytest.raises(ContractError):ctl.acquire_call(ExecutionScope('run'),fingerprint='f')
    assert path.read_text()=='broken'


@pytest.mark.parametrize('values',[{'parallelism':1.5},{'parallelism':True},
    {'attempt_limits':{'transport':1.5}},{'api_deadline_seconds':float('inf')},
    {'api_deadline_seconds':True},{'policy_version':1}])
def test_execution_policy_rejects_unbounded_or_ambiguous_limits(values):
    from aiforstudy.errors import ConfigError
    from aiforstudy.pipeline.execution_control import ExecutionPolicy
    with pytest.raises(ConfigError):ExecutionPolicy(**values)
