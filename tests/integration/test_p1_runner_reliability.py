"""Real orchestration regressions; no network or model transport is used."""
import json
import threading
import time
from pathlib import Path

import pytest

from aiforstudy.contracts.artifact import ArtifactRef
from aiforstudy.contracts.enums import Goal, QualityState
from aiforstudy.contracts.stage import StageResult
from aiforstudy.pipeline.engine import build_engine, execute, resume_request
from aiforstudy.pipeline.stubs import smoke_registry
from test_m2_engine import make_workspace, request_for


def test_slow_node_is_dispatched_once_with_free_worker(tmp_path):
    workspace, book = make_workspace(tmp_path)
    registry = smoke_registry()
    original = registry.get("mineru")
    calls = []

    def slow(ctx):
        calls.append(ctx.node.node_id)
        time.sleep(0.45)  # exceeds runner's result-poll interval
        return original(ctx)

    registry.register("mineru", slow)
    engine = build_engine(request_for(book.book_id, parallel=2), workspace,
                          stage_registry=registry, until="mineru")
    result, store = execute(engine)
    assert result.status == "succeeded"
    assert calls == ["mineru"]
    assert store.node_states()["mineru"].attempt == 1


@pytest.mark.parametrize("change", ["source", "config", "implementation"])
def test_resume_invalidates_current_inputs(tmp_path, change):
    workspace, book = make_workspace(tmp_path)
    registry = smoke_registry()
    request = request_for(book.book_id, goal=Goal.PREPARE)
    _, store = execute(build_engine(request, workspace, stage_registry=registry))
    if change == "source":
        (book.parse / "document.md").write_text("new source text", encoding="utf-8")
    elif change == "config":
        (workspace.config / "models.json").write_text('{"changed": true}', encoding="utf-8")
    else:
        original = registry.get("mineru")

        def revised(ctx):
            return original(ctx)

        registry.register("mineru", revised)
    result, store, _ = resume_request(request, store.run_id, workspace, stage_registry=registry)
    assert result.status == "succeeded"
    assert store.node_states()["mineru"].execution_state.value == "succeeded"


def test_changed_upstream_output_invalidates_dependent_even_with_unchanged_source(tmp_path):
    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id, goal=Goal.PREPARE)
    registry = smoke_registry()
    version = ["one"]

    def produce(ctx):
        path = ctx.services.store.work / "source-output.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(version[0], encoding="utf-8")
        ref = ArtifactRef(book_id=book.book_id, kind="parse.mineru", logical_key="mineru",
                          path=path, producer_stage="mineru").with_content()
        return StageResult.ok("mineru", outputs=(ref,), quality_state=QualityState.ACCEPTED)

    registry.register("mineru", produce)
    _, store = execute(build_engine(request, workspace, stage_registry=registry))
    first_toc_digest = store.node_states()["toc"].inputs_digest
    Path(store.node_states()["mineru"].outputs[0]["path"]).unlink()
    version[0] = "two"
    _, store, _ = resume_request(request, store.run_id, workspace, stage_registry=registry)
    assert store.node_states()["toc"].execution_state.value == "succeeded"
    assert store.node_states()["toc"].inputs_digest != first_toc_digest





def test_cancel_retains_completed_inflight_artifact_and_stops_later_nodes(tmp_path):
    workspace, book = make_workspace(tmp_path)
    registry = smoke_registry(); original = registry.get('mineru')
    gate = threading.Event(); entered = threading.Event(); box = {}
    def produce(ctx):
        result = original(ctx)
        entered.set(); assert gate.wait(timeout=5)
        return result
    registry.register('mineru', produce)
    engine = build_engine(request_for(book.book_id), workspace, stage_registry=registry, until='toc')
    worker = threading.Thread(target=lambda: box.update(zip(('result','store'), execute(engine))))
    worker.start()
    try:
        assert entered.wait(timeout=5)
        engine.services.cancel.cancel('offline stop after artifact completed')
        assert not box
    finally:
        gate.set(); worker.join(timeout=10)
    assert not worker.is_alive()
    assert box['result'].status == 'cancelled'
    assert box['store'].node_states()['mineru'].outputs
    assert box['store'].node_states()['toc'].execution_state.value == 'cancelled'












def test_failed_resume_does_not_retain_old_outputs(tmp_path):
    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id)
    registry = smoke_registry()
    _, store = execute(build_engine(request, workspace, stage_registry=registry, until="mineru"))
    Path(store.node_states()["mineru"].outputs[0]["path"]).unlink()

    def fail(ctx):
        raise RuntimeError("new attempt failed")

    registry.register("mineru", fail)
    result, store, _ = resume_request(request, store.run_id, workspace,
                                      stage_registry=registry, until="mineru")
    assert result.status == "failed"
    assert store.node_states()["mineru"].outputs == []


def test_completed_prefix_is_not_dispatched_again_but_keeps_quality_gate(tmp_path):
    from aiforstudy.pipeline.runner import Runner
    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id, goal=Goal.PREPARE)
    prefix_engine = build_engine(request, workspace, until="body")
    _, store = execute(prefix_engine)
    completed = set(store.node_states())
    # Input changed after preparation: the just-completed prefix is explicitly retained.
    (book.parse / "document.md").write_text("prepared source", encoding="utf-8")
    engine = build_engine(request, workspace)
    result = Runner(engine.services, engine.graph, store, resume=True,
                    initial_completed=completed).run()
    assert result.status == "succeeded"
    assert result.execution_counts == {"succeeded": 6}
    assert all(state.attempt == 1 for state in store.node_states().values())


def test_cache_requires_output_digest_and_origin(tmp_path):
    from aiforstudy.pipeline.state import RunStore
    workspace, book = make_workspace(tmp_path)
    engine = build_engine(request_for(book.book_id), workspace, until="mineru")
    _, store = execute(engine)
    node = store.node_states()["mineru"]
    node.outputs[0]["content_digest"] = None
    assert not store.reusable(node, inputs_digest=node.inputs_digest)
    node = store.node_states()["mineru"]
    node.outputs[0]["producer_stage"] = "unrelated-stage"
    assert not store.reusable(node, inputs_digest=node.inputs_digest)


def test_runner_resume_preserves_transport_attempts_without_counting_cached_nodes(tmp_path):
    from aiforstudy.providers.gateway import Gateway, CallRequest
    workspace, book = make_workspace(tmp_path)
    registry = smoke_registry(); original = registry.get('mineru'); seen = []
    def transport(**kw): seen.append(1); return '{}', {'total_tokens':'ignored'}
    def consume(ctx):
        gateway = Gateway(transport=transport, roles={'lesson':{'model':'offline','effort':'off'}},
                          control=ctx.services.control, cancel=ctx.services.cancel)
        assert gateway.call(CallRequest(role='lesson', messages=[], stage='mineru')).ok
        return original(ctx)
    registry.register('mineru', consume)
    request = request_for(book.book_id)
    _, store = execute(build_engine(request, workspace, stage_registry=registry, until='mineru'))
    cached, store, _ = resume_request(request, store.run_id, workspace, stage_registry=registry, until='mineru')
    assert cached.execution_counts == {'cached':1} and seen == [1]
    Path(store.node_states()['mineru'].outputs[0]['path']).unlink()
    second, store, _ = resume_request(request, store.run_id, workspace, stage_registry=registry, until='mineru')
    assert second.status == 'succeeded' and seen == [1,1]
    Path(store.node_states()['mineru'].outputs[0]['path']).unlink()
    exhausted, store, _ = resume_request(request, store.run_id, workspace, stage_registry=registry, until='mineru')
    assert exhausted.status == 'failed' and seen == [1,1]
    assert [a['attempt'] for a in json.loads((store.root/'execution.json').read_text())['attempts']] == [1,2]



def test_same_control_two_phase_run_keeps_completed_prefix_and_attempts(tmp_path):
    from aiforstudy.pipeline.runner import Runner
    from aiforstudy.pipeline.execution_control import ExecutionScope
    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id, goal=Goal.PREPARE)
    engine = build_engine(request, workspace, stage_registry=smoke_registry(), until='body')
    _, store = execute(engine)
    lease = engine.services.control.acquire_call(ExecutionScope(store.run_id, 'book', 'toc'), fingerprint='offline')
    lease.finish('response_received')
    before = engine.services.control.snapshot()['attempts']
    full = build_engine(request, workspace, stage_registry=engine.services.stages)
    result = Runner(engine.services, full.graph, store, resume=True,
                    initial_completed=set(store.node_states())).run()
    assert result.status == 'succeeded'
    assert engine.services.control.snapshot()['attempts'] == before
    assert all(state.attempt == 1 for state in store.node_states().values())






@pytest.mark.parametrize('evidence',['valid','changed_lesson','changed_figures','wrong_binding',
    'incomplete_figures','missing_checks','changed_audit','old_content_version','old_media_version'])
def test_current_delivery_requires_current_complete_evidence_despite_legacy_certificate(tmp_path,evidence):
    from support.runner_v5 import run_book,UNIT
    from aiforstudy.pipeline.delivery_stage import validate_reader_delivery
    from aiforstudy.pipeline.services import StageContext
    from aiforstudy.pipeline.graph import Node
    from aiforstudy.core.jsonio import read_json,write_json_atomic
    from aiforstudy.errors import ContractError
    result,store,engine,_=run_book(tmp_path,until='delivery')
    assert result.status=='succeeded',result.recovery_hint
    ctx=StageContext(node=Node('delivery','delivery','book'),request=engine.request,
        plan=engine.plan,workspace=engine.services.workspace,context=engine.services.context,services=engine.services)
    assert UNIT in validate_reader_delivery(ctx)['CH01']['units']
    root=store.work/'products'
    content_path=root/f'content/{UNIT}/revisions/000001.json'
    text_path=root/f'text_audit/{UNIT}.json'
    figures_path=root/f'figures/{UNIT}.json'
    audit_path=root/f'audit/{UNIT}.json'
    # The historical terminal certificate cannot upgrade a damaged current receipt.
    legacy=dict(final_quality='accepted',supersedes_nodes=['lesson:'+UNIT,'figures:'+UNIT],
        final_checks={'lesson':{'problems':[]},'figures':{'quality_state':'accepted','requests_complete':True},
                      'audit':{'quality_state':'accepted','blocking_open':0,'review_errors':[]}})
    write_json_atomic(store.work/'legacy-terminal-certificate.json',legacy)
    def modify(path,change):
        value=read_json(path);change(value);write_json_atomic(path,value)
    if evidence=='changed_lesson':
        modify(content_path,lambda r:r.update(body_markdown=r['body_markdown']+' 被篡改的条件。'))
    elif evidence=='changed_figures':
        modify(figures_path,lambda r:r['payload'].update(content_hash='sha256:changed'))
    elif evidence=='wrong_binding':
        modify(audit_path,lambda r:r['payload']['approval'].update(content_hash='sha256:wrong-content'))
    elif evidence=='incomplete_figures':
        modify(figures_path,lambda r:r['payload']['requests'].append({'request_id':'unplanned','kind':'geometry','state':'failed'}))
    elif evidence=='missing_checks':
        modify(text_path,lambda r:r['payload'].pop('check_reports'))
    elif evidence=='changed_audit':
        modify(audit_path,lambda r:r['payload'].update(approval={}))
    elif evidence=='old_content_version':
        modify(text_path,lambda r:r['payload']['approval'].update(schema_version='ContentApprovalV1'))
    elif evidence=='old_media_version':
        modify(audit_path,lambda r:r['payload']['approval'].update(schema_version='MediaApprovalV1'))
    before={str(p):p.read_bytes() for p in store.work.rglob('*') if p.is_file()}
    if evidence=='valid':
        assert UNIT in validate_reader_delivery(ctx)['CH01']['units']
    else:
        with pytest.raises(ContractError):validate_reader_delivery(ctx)
    assert before=={str(p):p.read_bytes() for p in store.work.rglob('*') if p.is_file()}


def test_local_failure_can_resume_without_invented_transport_attempts(tmp_path):
    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id)
    registry = smoke_registry()
    original = registry.get("mineru")
    broken = [True]

    def local_stage(ctx):
        if broken[0]:
            raise ValueError("local parse failure before any external call")
        return original(ctx)

    registry.register("mineru", local_stage)
    first, store = execute(build_engine(request, workspace, stage_registry=registry, until="mineru"))
    assert first.status == "failed"
    broken[0] = False
    second, _, _ = resume_request(request, store.run_id, workspace,
                                  stage_registry=registry, until="mineru")
    assert second.status == "succeeded"
    assert json.loads((store.root / 'execution.json').read_text())['attempts'] == []





def test_cached_stage_does_not_dispatch_another_stage_call(tmp_path):
    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id)
    registry = smoke_registry(); original = registry.get('mineru'); seen = []
    def consume(ctx):
        seen.append(ctx.node.node_id)
        return original(ctx)
    registry.register('mineru', consume)
    _, store = execute(build_engine(request, workspace, stage_registry=registry, until='mineru'))
    result, _, _ = resume_request(request, store.run_id, workspace, stage_registry=registry, until='mineru')
    assert result.status == 'succeeded'
    assert result.execution_counts == {'cached':1}
    assert seen == ['mineru']



# --------------------------------------------------------------------------- #
# 阶段级：取消后 runner 的调用数不再增加，并以 cancelled 收场
# --------------------------------------------------------------------------- #
def test_runner_cancel_stops_dispatch_and_marks_cancelled(tmp_path):
    """取消发生在第一个节点执行中：后面的节点**一个都不许再派发**。"""
    from aiforstudy.pipeline.engine import execute
    from aiforstudy.pipeline.stubs import smoke_registry
    from test_m2_engine import make_workspace, request_for

    workspace, book = make_workspace(tmp_path)
    registry = smoke_registry()
    original = registry.get("mineru")
    seen: list[str] = []

    def cancel_on_first(ctx):
        seen.append(ctx.node.node_id)
        if len(seen) == 1:
            ctx.services.cancel.cancel("跑到第一个节点就取消")
        return original(ctx)

    registry.register("mineru", cancel_on_first)
    engine = build_engine(request_for(book.book_id), workspace, stage_registry=registry,
                          until="toc")
    result, _store = execute(engine)
    assert result.status == "cancelled"
    assert result.exit_code == 130
    assert seen == ["mineru"], f"取消之后仍在派发新节点：{seen}"


def test_resume_restores_consumed_scope_attempt_after_dispatch_crash(tmp_path):
    from aiforstudy.pipeline.execution_control import ExecutionScope, AttemptLimitExceeded
    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id, goal=Goal.PREPARE)
    registry = smoke_registry()
    engine = build_engine(request, workspace, stage_registry=registry)
    _, store = execute(engine)
    scope = ExecutionScope(store.run_id, 'CH01', 'audit', 'figure-1')
    engine.services.control.acquire_call(scope, fingerprint='frozen-evidence')  # crash before finish
    result, _, resumed = resume_request(request, store.run_id, workspace, stage_registry=registry)
    assert result.status == 'succeeded'
    control = resumed.services.control
    assert control.snapshot()['attempts'][0]['state'] == 'interrupted'
    assert control.transport_attempts_used(scope, 'frozen-evidence') == 1
    control.acquire_call(scope, fingerprint='frozen-evidence').finish('transport_failed')
    with pytest.raises(AttemptLimitExceeded): control.acquire_call(scope, fingerprint='frozen-evidence')
    other = control.acquire_call(ExecutionScope(store.run_id, 'CH01', 'audit', 'figure-2'),
                                 fingerprint='other-evidence')
    assert other.transport_attempt == 1
    other.finish('completed')



def test_cancel_exception_during_repair_scheduling_finishes_run(tmp_path, monkeypatch):
    from aiforstudy.pipeline.runner import Runner
    from aiforstudy.errors import Cancelled
    workspace, book = make_workspace(tmp_path)
    engine = build_engine(request_for(book.book_id), workspace, stage_registry=smoke_registry(), until='mineru')
    def stopped(*args): raise Cancelled('offline cancellation during repair scheduling')
    monkeypatch.setattr(Runner, '_handle_repair_requests', stopped)
    result, store = execute(engine)
    assert result.status == 'cancelled' and result.exit_code == 130
    assert json.loads(store.state_path.read_text())['status'] != 'running'
