"""Current graph protections for execution attempts, cache, cancellation and repair."""
import json
import pytest
from aiforstudy.pipeline.engine import resume_request, run_request, build_engine, execute
from aiforstudy.pipeline.services import Providers, CancelToken
from aiforstudy.errors import ContractError
from support.runner_v5 import TeachingTransport, run_book, setup_book, _request, UNIT


def test_current_runner_persists_every_actual_model_attempt_without_usage_ledger(tmp_path):
    result,store,engine,transport=run_book(tmp_path)
    assert result.status=='succeeded',result.recovery_hint
    rows=json.loads((store.root/'execution.json').read_text())['attempts']
    assert len(rows)==len(transport.calls)
    assert all(r['scope']['run_id']==store.run_id and r['scope']['unit_id']==UNIT for r in rows)
    assert {r['scope']['stage'] for r in rows}=={'lesson','text_audit'}
    assert all(r['attempt']==1 and r['state']=='response_received' for r in rows)
    assert not (store.root/'usage.jsonl').exists()
    receipt=json.loads((store.work/f'products/text_audit/{UNIT}.json').read_text())['payload']['approval']
    assert receipt['schema_version']=='ContentApprovalV2'


def test_current_runner_resume_reuses_unchanged_candidate_and_review(tmp_path):
    result,store,engine,transport=run_book(tmp_path)
    assert result.status=='succeeded'
    before=len(transport.calls)
    current=(store.work/f'products/content/{UNIT}/current.json').read_bytes()
    again,store,_=resume_request(engine.request,store.run_id,engine.services.workspace,
        providers=Providers(llm=transport),until='text_audit')
    assert again.status=='succeeded',again.recovery_hint
    assert len(transport.calls)==before
    assert (store.work/f'products/content/{UNIT}/current.json').read_bytes()==current


@pytest.mark.parametrize('refuse',[False,True])
def test_current_runner_repairs_original_ticket_with_persistent_object_rounds(tmp_path,refuse):
    transport=TeachingTransport(reject_first=True,refuse_repair=refuse)
    result,store,engine,transport=run_book(tmp_path,transport=transport)
    audit=json.loads((store.work/f'products/text_audit/{UNIT}.json').read_text())['payload']
    repairs=store.load_state()['repair_states'][UNIT]
    assert transport.author_calls==1
    assert 1<=transport.patch_calls<=2
    assert repairs['content_rounds_by_artifact']['lesson:'+UNIT]==transport.patch_calls
    if refuse:
        assert not audit.get('approval')
        assert result.status!='succeeded' or result.quality_counts.get('needs_review',0)>0
        assert audit['tickets']
    else:
        assert result.status=='succeeded',result.recovery_hint
        assert audit['approval']['schema_version']=='ContentApprovalV2'
        assert audit['closed_ticket_ids']


def test_current_runner_protocol_repair_does_not_reauthor(tmp_path):
    result,store,_,transport=run_book(tmp_path,transport=TeachingTransport(fail_protocol=True))
    assert result.status=='succeeded',result.recovery_hint
    assert transport.author_calls==1 and transport.patch_calls==0
    audit=json.loads((store.work/f'products/text_audit/{UNIT}.json').read_text())['payload']
    assert audit['approval']['schema_version']=='ContentApprovalV2'
    assert audit['closed_ticket_ids']


def test_current_runner_cancelled_run_cannot_call_author(tmp_path):
    ws=setup_book(tmp_path);transport=TeachingTransport();cancel=CancelToken();cancel.cancel()
    engine=build_engine(_request(),ws,providers=Providers(llm=transport),until='text_audit',cancel=cancel)
    result,store=execute(engine)
    assert result.status=='cancelled'
    assert transport.calls==[]


@pytest.mark.parametrize('case',['missing','duplicate','orphan'])
def test_runner_requires_an_exact_unique_route_for_each_repair_ticket(case):
    from types import SimpleNamespace
    from aiforstudy.blocks.audit.contracts import RepairDirective
    from aiforstudy.contracts.stage import StageResult
    from aiforstudy.pipeline.runner import Runner
    runner=object.__new__(Runner);runner.graph=SimpleNamespace(graph_version='7.0');runner._repair_routes={}
    node=SimpleNamespace(stage='text_audit')
    route={'schema_version':'RepairRouteV1','ticket_id':'T','origin_stage':'text_audit','target_artifact_kind':'content','target_artifact_id':'lesson:U'}
    directive=RepairDirective(ticket_ids=('T',),owner='lesson',unit_id='U',reason='正文条件返修',kind='text_content',artifact_hashes={'lesson:U':'sha256:'+'a'*64})
    with pytest.raises(ContractError,match='RepairRouteV1|repair_routes'):
        result=StageResult.ok('text_audit',repair_requests=() if case=='orphan' else (directive,),repair_routes=() if case=='missing' else ((route,route) if case=='duplicate' else (route,)))
        runner._handle_repair_requests(node,result,{})


def test_resume_refuses_silent_attempt_policy_increase(tmp_path):
    workspace=setup_book(tmp_path);transport=TeachingTransport()
    path=workspace.root/'config/execution.json'
    policy=json.loads(path.read_text())
    policy['attempt_limits']['transport']=1
    path.write_text(json.dumps(policy))
    result,store,engine=run_request(_request(),workspace,providers=Providers(llm=transport),until='text_audit')
    assert result.status=='succeeded',result.recovery_hint
    policy['attempt_limits']['transport']=2
    path.write_text(json.dumps(policy))
    before=len(transport.calls)
    with pytest.raises(ContractError,match='策略'):
        resume_request(engine.request,store.run_id,engine.services.workspace,providers=Providers(llm=transport),until='text_audit')
    assert len(transport.calls)==before


def test_current_single_unit_cli_plan_is_read_only_and_requires_no_transport(tmp_path,monkeypatch,capsys):
    from aiforstudy.cli import main
    from aiforstudy.providers.gateway import Gateway
    workspace=setup_book(tmp_path)
    before={str(p.relative_to(workspace.root)):p.read_bytes() for p in workspace.root.rglob('*') if p.is_file()}
    monkeypatch.setattr(Gateway,'call',lambda *a,**k:(_ for _ in ()).throw(AssertionError('dry plan must not call a model')))
    assert main(['--workspace',str(workspace.root),'plan','--book','mini-book','--units',UNIT,'--json'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['resolved_scope']==[UNIT]
    assert before=={str(p.relative_to(workspace.root)):p.read_bytes() for p in workspace.root.rglob('*') if p.is_file()}
