"""The live acceptance harness must preserve returned usage and cancellation."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest


@pytest.fixture
def tool():
    path = Path(__file__).resolve().parents[1]/'tools/verify_code_media_e2e.py'
    spec = importlib.util.spec_from_file_location('code_media_acceptance_under_test',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def live_transport(tool,tmp_path):
    transport = tool.LiveMediaTransport(model='offline-model',approved_budget_tokens=100_000,
                                        api_key='offline-test-key',base_url='https://example.invalid')
    transport.evidence_out = tmp_path
    return transport


def call_kwargs():
    return {'model':'offline-model','effort':'high','max_tokens':32000,'timeout':300,
            'messages':[{'role':'system','content':'GeneratedMediaCodeV1'},
                        {'role':'user','content':json.dumps({'request':{'request_id':'R'}})}],
            'images':None}


@pytest.mark.parametrize('raw',['{"code":"unfinished', '', '{"code":"complete"}'])
def test_received_text_and_usage_survive_format_errors_for_gateway(tool,tmp_path,monkeypatch,capsys,raw):
    usage = {'prompt_tokens':100,'completion_tokens':300,'total_tokens':400,
             'completion_tokens_details':{'reasoning_tokens':200}}
    monkeypatch.setattr(tool.RealTransport,'__call__',lambda self,**kwargs:(raw,deepcopy(usage)))
    transport = live_transport(tool,tmp_path)
    assert transport(**call_kwargs()) == (raw,usage)
    rows = json.loads((tmp_path/'live-exchanges.json').read_text())
    assert rows[0]['response_text'] == raw
    assert rows[0]['usage'] == usage
    assert bool(rows[0]['response_json_error']) == (raw != '{"code":"complete"}')
    assert rows[0]['response'] == ({'code':'complete'} if not rows[0]['response_json_error'] else None)
    events = [json.loads(line) for line in (tmp_path/'live-call-events.jsonl').read_text().splitlines()]
    assert [row['event'] for row in events] == ['api-start','api-finish']
    assert events[0]['max_tokens'] == 32000 and events[0]['effort'] == 'high'
    assert events[-1]['usage'] == usage and events[-1]['response_chars'] == len(raw)
    assert 'offline-test-key' not in (tmp_path/'live-call-events.jsonl').read_text()+capsys.readouterr().out


def test_gateway_records_known_usage_before_retrying_invalid_model_json(tool,tmp_path,monkeypatch):
    from aiforstudy.providers.gateway import Gateway, CallRequest
    from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy
    from aiforstudy.core.usage import UsageLedger
    answers = iter([('{"code":"unfinished',{'prompt_tokens':11,'completion_tokens':7,'total_tokens':18}),
                    ('{"ok":true}',{'prompt_tokens':11,'completion_tokens':8,'total_tokens':19})])
    monkeypatch.setattr(tool.RealTransport,'__call__',lambda self,**kwargs:next(answers))
    transport = live_transport(tool,tmp_path)
    control=ExecutionControl(ExecutionPolicy(parallelism=1));control.bind(tmp_path/'execution.json',run_id='tool-test')
    gateway = Gateway(transport=transport,roles={'figures':{'model':'offline-model','effort':'high','max_tokens':32}},
                      control=control,run_id='tool-test')
    result = gateway.call(CallRequest(role='figures',messages=call_kwargs()['messages'],
                                      tag='tool-test',stage='figures',unit_key='U',bucket='figures'))
    assert result.ok and result.attempts == 2
    assert [row['attempt'] for row in control.snapshot()['attempts']] == [1,2]
    assert [row['usage']['total_tokens'] for row in transport.exchanges] == [18,19]
    assert not (tmp_path/'usage.jsonl').exists()


def test_call_evidence_records_actual_wire_effort_without_credentials(tool,tmp_path,monkeypatch):
    def reply(self,**kwargs):
        self.request_body(messages=kwargs['messages'],images=kwargs['images'],
                          model=kwargs['model'],effort=kwargs['effort'],max_tokens=kwargs['max_tokens'])
        return '{}',{'total_tokens':10}
    monkeypatch.setattr(tool.RealTransport,'__call__',reply)
    transport=live_transport(tool,tmp_path)
    transport(**{**call_kwargs(),'effort':'low','max_tokens':64000})
    row=json.loads((tmp_path/'live-exchanges.json').read_text())[0]
    assert row['wire_options']['reasoning_effort']=='low'
    assert row['wire_options']['thinking']=={'type':'enabled'}
    assert set(row['wire_options'])=={'model','max_tokens','thinking','reasoning_effort'}
    assert 'offline-test-key' not in json.dumps(row)


@pytest.mark.parametrize('failure',[TimeoutError('bounded timeout'),RuntimeError('worker failure')])
def test_transport_failures_have_started_and_failed_events_without_fake_usage(tool,tmp_path,monkeypatch,failure):
    def fail(self,**kwargs): raise failure
    monkeypatch.setattr(tool.RealTransport,'__call__',fail)
    transport = live_transport(tool,tmp_path)
    with pytest.raises(type(failure)):
        transport(**call_kwargs())
    events = [json.loads(line) for line in (tmp_path/'live-call-events.jsonl').read_text().splitlines()]
    assert [row['event'] for row in events] == ['api-start','api-failure']
    assert events[-1]['error_type'] == type(failure).__name__
    assert events[-1]['usage'] is None
    assert json.loads((tmp_path/'live-exchanges.json').read_text())[0]['error_type'] == type(failure).__name__


def acceptance_setup(tool,tmp_path,monkeypatch,signal_to_send=None):
    source = tmp_path/'source'
    (source/'config').mkdir(parents=True)
    models = {'base_url':'https://example.invalid','roles':{
        'figures':{'model':'offline-model','effort':'high','max_tokens':32000},
        'judge':{'model':'offline-model','effort':'high','max_tokens':32000}}}
    (source/'config/models.json').write_text(json.dumps(models))
    monkeypatch.setattr(tool,'ROOT',source)
    observed = {}

    def setup(output):
        workspace = SimpleNamespace(root=output/'ws',config=output/'ws/config')
        workspace.config.mkdir(parents=True)
        (workspace.root/'book/parse/original_text').mkdir(parents=True)
        (workspace.root/'book/parse/concepts.parts').mkdir(parents=True)
        (workspace.config/'capabilities.json').write_text(json.dumps({'render_environment':{},'browser_acceptance':{}}))
        (workspace.config/'models.json').write_text(json.dumps(models))
        return workspace

    def fake_transport(**kwargs):
        observed['transport_kwargs'] = kwargs
        return SimpleNamespace(exchanges=[],calls=0,live_calls_started=0,spent_tokens=0)

    def run(request,workspace,*,providers,until,cancel):
        observed.update(request=request,cancel=cancel,models=json.loads((workspace.config/'models.json').read_text()))
        if signal_to_send is not None:
            handler = signal.getsignal(signal_to_send)
            assert callable(handler)
            handler(signal_to_send,None)
            assert cancel.cancelled
        exit_code = 130 if cancel.cancelled else 0
        result = SimpleNamespace(exit_code=exit_code,as_dict=lambda:{'exit_code':exit_code})
        engine=SimpleNamespace(services=SimpleNamespace(context=SimpleNamespace(parse=workspace.root/'book/parse')))
        return result,SimpleNamespace(root=workspace.root/'run',work=workspace.root/'work'),engine

    monkeypatch.setattr(tool,'setup_book',setup)
    monkeypatch.setattr(tool,'default_services',lambda **kwargs:SimpleNamespace(worker_dir=tmp_path/'worker',interpreter='/usr/bin/python3'))
    monkeypatch.setattr(tool,'display_runtime',lambda *args:(tmp_path/'browser',tmp_path/'node',tmp_path/'playwright'))
    monkeypatch.setattr(tool,'LiveMediaTransport',fake_transport)
    monkeypatch.setattr(tool,'run_request',run)
    return observed,models


@pytest.mark.parametrize('override',[False,True])
def test_budget_and_figure_only_overrides_reach_engine_and_report(tool,tmp_path,monkeypatch,override):
    observed,models = acceptance_setup(tool,tmp_path,monkeypatch)
    options = {'media_max_tokens':8192,'media_effort':'none'} if override else {}
    report = tool.run_acceptance(tmp_path/'evidence',live_media=True,budget_tokens=123456,**options)
    # This transport fixture returns exit 0 but creates no accepted products.
    assert not report['ok']
    assert 'budget' not in observed['request'].as_dict()
    assert observed['transport_kwargs']['approved_budget_tokens'] == 123456
    assert observed['transport_kwargs']['cancel'] is observed['cancel']
    expected = {**models['roles']['figures'],**({'max_tokens':8192,'effort':'none'} if override else {})}
    assert observed['models']['roles']['figures'] == expected
    assert observed['models']['roles']['judge'] == models['roles']['judge']
    assert report['media_roles']['figures'] == expected
    assert report['media_overrides'] == ({'figures':{'max_tokens':8192,'effort':'none'}} if override else {})
    assert report['budget_tokens'] == 123456
    assert json.loads((tool.ROOT/'config/models.json').read_text()) == models


def test_zero_exit_without_media_or_html_cannot_claim_acceptance(tool,tmp_path,monkeypatch):
    acceptance_setup(tool,tmp_path,monkeypatch)
    report=tool.run_acceptance(tmp_path/'evidence',live_media=True)
    assert report['exit_code']==0 and not report['ok']
    assert any('HTML' in error for error in report['acceptance_errors'])
    assert any('一图一动画' in error for error in report['acceptance_errors'])
    assert json.loads((tmp_path/'evidence/acceptance.json').read_text())==report


def test_reader_contract_failure_writes_complete_report_instead_of_escaping(tool,tmp_path,monkeypatch):
    acceptance_setup(tool,tmp_path,monkeypatch)
    run=tool.run_request
    def html_without_receipt(*args,**kwargs):
        result,store,extra=run(*args,**kwargs)
        page=store.work/'legacy/render/CH01/index.html'
        page.parent.mkdir(parents=True);page.write_text('<html></html>')
        return result,store,extra
    monkeypatch.setattr(tool,'run_request',html_without_receipt)
    report=tool.run_acceptance(tmp_path/'evidence')
    assert not report['ok'] and report['external_api_calls']==0
    assert any('ReaderProjection' in error for error in report['acceptance_errors'])
    assert json.loads((tmp_path/'evidence/acceptance.json').read_text())==report


def test_engine_exception_keeps_report_and_controlled_exchange_evidence(tool,tmp_path,monkeypatch):
    acceptance_setup(tool,tmp_path,monkeypatch)
    def fail(*args,**kwargs): raise RuntimeError('controlled stage failure')
    monkeypatch.setattr(tool,'run_request',fail)
    report=tool.run_acceptance(tmp_path/'evidence')
    assert not report['ok'] and report['exit_code']==1
    assert report['exception']['type']=='RuntimeError'
    assert 'controlled stage failure' in report['exception']['message']
    assert report['external_api_calls']==0 and not report.get('browser')
    assert (tmp_path/'evidence/model-exchanges.json').is_file()
    assert json.loads((tmp_path/'evidence/acceptance.json').read_text())==report


def test_draft_animation_cannot_claim_finished_html_acceptance(tool,tmp_path,monkeypatch):
    acceptance_setup(tool,tmp_path,monkeypatch)
    run=tool.run_request
    def with_draft(*args,**kwargs):
        result,store,extra=run(*args,**kwargs)
        rows=[{'request_id':'I','kind':'drawing','state':'fulfilled','request':{},'validation':{},'render_profile':'final'},
              {'request_id':'A','kind':'animation','state':'fulfilled','request':{},'validation':{},'render_profile':'draft'}]
        tool.write_json_atomic(store.work/'products/figures'/(tool.UNIT+'.json'),{'payload':{'requests':rows}})
        html=store.work/'legacy/render/CH01/index.html'
        html.parent.mkdir(parents=True);html.write_text('<html></html>')
        return result,store,extra
    monkeypatch.setattr(tool,'run_request',with_draft)
    monkeypatch.setattr(tool,'validate_media_report',lambda *a,**k:True,raising=False)
    monkeypatch.setattr(tool,'browser_check',lambda *a,**k:pytest.fail('draft reached final browser acceptance'))
    report=tool.run_acceptance(tmp_path/'evidence',live_media=True)
    assert not report['ok']
    assert any('final' in error for error in report['acceptance_errors'])


@pytest.mark.parametrize('signum',[signal.SIGINT,signal.SIGTERM])
def test_signal_cancellation_reaches_runner_and_restores_handlers(tool,tmp_path,monkeypatch,signum):
    previous = {number:signal.getsignal(number) for number in (signal.SIGINT,signal.SIGTERM)}
    observed,_ = acceptance_setup(tool,tmp_path,monkeypatch,signal_to_send=signum)
    report = tool.run_acceptance(tmp_path/'evidence',live_media=True)
    assert observed['cancel'].cancelled
    assert report['exit_code'] == 130 and report['cancelled'] is True and not report['ok']
    assert all(signal.getsignal(number) == handler for number,handler in previous.items())
    assert (tmp_path/'evidence/model-exchanges.json').is_file()
    assert (tmp_path/'evidence/acceptance.json').is_file()


def test_cli_forwards_optional_figure_settings_and_budget(tool,tmp_path,monkeypatch):
    observed = {}
    def fake(output,**kwargs):
        observed.update(output=output,**kwargs)
        return {'ok':False,'exit_code':130}
    monkeypatch.setattr(tool,'run_acceptance',fake)
    assert tool.main(['--out',str(tmp_path),'--live-media','--budget-tokens','44444',
                      '--media-max-tokens','8192','--media-effort','none']) == 130
    assert observed == {'output':tmp_path,'live_media':True,'budget_tokens':44444,
                        'media_max_tokens':8192,'media_effort':'none'}


def resume_fixture(tool,tmp_path,monkeypatch):
    observed,models=acceptance_setup(tool,tmp_path,monkeypatch)
    prior=tmp_path/'prior';workspace=tool.setup_book(prior)
    run=workspace.root/'data/books/mini-book/runs/run-frozen'
    run.mkdir(parents=True)
    tool.write_json_atomic(run/'request.json',tool._request().as_dict())
    tool.write_json_atomic(prior/'acceptance.json',{
        'actual_media_api':True,'scope':'fixed approved teaching fixture; production media and composition stages',
        'run_directory':str(run),'reported_tokens':1234})
    monkeypatch.setattr(tool,'open_workspace',lambda path:workspace,raising=False)
    def resume(request,run_id,ws,**kwargs):
        observed['resume_run']=run_id
        return tool.run_request(request,ws,**kwargs)
    monkeypatch.setattr(tool,'resume_request',resume,raising=False)
    monkeypatch.setattr(tool,'setup_book',lambda *a:pytest.fail('resume must preserve the existing fixture'))
    return prior,workspace,observed


def test_resume_reuses_workspace_request_and_cumulative_budget(tool,tmp_path,monkeypatch):
    prior,ws,observed=resume_fixture(tool,tmp_path,monkeypatch)
    saved=(ws.config/'models.json').read_bytes()
    report=tool.run_acceptance(tmp_path/'continued',live_media=True,budget_tokens=50000,resume_from=prior)
    assert observed['resume_run']=='run-frozen'
    assert 'budget' not in observed['request'].as_dict()
    assert (ws.config/'models.json').read_bytes()==saved
    assert report['resumed_from']==str(prior)
    assert report['workspace_root']==str(ws.root)
    assert (tmp_path/'continued/prior-acceptance.json').is_file()
    assert not report['ok']  # Fake engine still has no HTML.


@pytest.mark.parametrize('resume',[False,True])
def test_reader_uses_executed_preparation_context_instead_of_original_parse(tool,tmp_path,monkeypatch,resume):
    if resume:
        prior,workspace,observed=resume_fixture(tool,tmp_path,monkeypatch)
    else:
        observed,_=acceptance_setup(tool,tmp_path,monkeypatch)
    run=tool.run_request
    execution={}
    def prepared_run(request,workspace,**kwargs):
        result,store,engine=run(request,workspace,**kwargs)
        prepared=store.work/'prepared-parse';prepared.mkdir(parents=True)
        assert (workspace.root/'book/parse').is_dir()
        engine.services.context=SimpleNamespace(parse=prepared)
        execution['context']=engine.services.context
        page=store.work/'legacy/render/CH01/index.html'
        page.parent.mkdir(parents=True);page.write_text('<html></html>')
        return result,store,engine
    def inspect_projection(store,context,rows):
        execution['projection_context']=context
        return {}
    monkeypatch.setattr(tool,'run_request',prepared_run)
    monkeypatch.setattr(tool,'_reader_projection',inspect_projection)
    report=tool.run_acceptance(tmp_path/'continued' if resume else tmp_path/'evidence',
        live_media=True,**({'resume_from':prior} if resume else {}))
    assert not report['ok']  # No media are fabricated by this context-routing test.
    assert execution['projection_context'] is execution['context']


@pytest.mark.parametrize('options',[{'live_media':False},{'live_media':True,'media_effort':'none'}])
def test_resume_rejects_provider_switch_or_generation_override(tool,tmp_path,monkeypatch,options):
    prior,ws,observed=resume_fixture(tool,tmp_path,monkeypatch)
    before=(ws.config/'models.json').read_bytes()
    with pytest.raises(ValueError):
        tool.run_acceptance(tmp_path/'continued',resume_from=prior,**options)
    assert 'transport_kwargs' not in observed
    assert (ws.config/'models.json').read_bytes()==before
