"""D5/D13/D14: failures stay distinct, deadlines and repeats have one owner."""
from dataclasses import replace
import json
from pathlib import Path
import socket

import pytest

from aiforstudy.errors import ConfigError, ContractError
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy, ExecutionScope
from aiforstudy.providers.gateway import Gateway, CallRequest
from aiforstudy.blocks.audit.contracts import RepairDirective
from aiforstudy.pipeline.repair import RepairCoordinator, UnitRepairState, RepairBatch, StopDecision


def test_release_review_capacity_and_single_call_policy():
    root = Path(__file__).resolve().parents[1]
    roles = json.loads((root / 'config/models.json').read_text())['roles']
    for name in ('judge', 'interaction', 'toc_review'):
        assert roles[name]['max_tokens'] == 393_216
        assert not roles[name].get('length_retry_max_tokens')
    cfg = json.loads((root / 'config/execution.json').read_text())
    assert cfg['call_timeout_seconds'] == 900
    assert cfg['profiles']['release']['api_deadline_seconds'] is None


def test_unlimited_run_still_caps_each_call_at_900(tmp_path):
    now = [0.0]
    control = ExecutionControl(ExecutionPolicy(api_deadline_seconds=None), clock=lambda: now[0])
    control.bind(tmp_path / 'execution.json', run_id='run')
    now[0] = 5000
    first = control.acquire_call(ExecutionScope('run'), fingerprint='f')
    assert first.timeout_seconds() == 900
    now[0] += 700
    assert first.timeout_seconds() == 200
    first.finish()
    second = control.acquire_call(ExecutionScope('run'), fingerprint='f')
    assert second.timeout_seconds() == 900
    second.finish()


def test_explicit_total_deadline_is_not_restarted_for_second_call(tmp_path):
    now = [0.0]
    control = ExecutionControl(ExecutionPolicy(api_deadline_seconds=1000), clock=lambda: now[0])
    control.bind(tmp_path / 'execution.json', run_id='run')
    first = control.acquire_call(ExecutionScope('run'), fingerprint='f')
    assert first.timeout_seconds() == 900
    now[0] += 700
    first.finish()
    second = control.acquire_call(ExecutionScope('run'), fingerprint='f')
    assert second.timeout_seconds() == 300
    now[0] += 10
    assert second.timeout_seconds() == 290
    second.finish()


@pytest.mark.parametrize('exc,kind', [(ContractError('missing reviewer span'), 'protocol'),
    (TimeoutError('connect timeout'), 'timeout'), (ConnectionRefusedError('refused'), 'network'),
    (ConfigError('HTTP 400 max_tokens rejected'), 'configuration'), (RuntimeError('bug'), 'internal')])
def test_review_failure_has_explicit_classification(exc, kind):
    import aiforstudy.providers as providers
    from importlib import import_module
    assert hasattr(providers, '__path__')
    try:
        module = import_module('aiforstudy.providers.review_failures')
    except ImportError:
        pytest.fail('shared review failure classifier is missing')
    assert module.classify_review_failure(exc) == kind


@pytest.mark.parametrize('exc,kind', [(TimeoutError('connect'), 'timeout'),
    (ConnectionRefusedError('DNS/connect'), 'network')])
def test_gateway_client_preserves_transport_failure_kind(tmp_path, exc, kind):
    from aiforstudy.pipeline.gateway_client import GatewayClient, ModelCallFailed
    def wire(**kwargs):
        raise exc
    control = ExecutionControl()
    control.bind(tmp_path / 'execution.json', run_id='run')
    gateway = Gateway(transport=wire, roles={'judge': {'model': 'offline', 'max_tokens': 5_000_000}}, control=control)
    client = GatewayClient(gateway, role='judge', model='offline', effort='high', stage='text_audit', unit_key='U', bucket='audit')
    with pytest.raises(ModelCallFailed) as caught:
        client.chat_json([])
    assert getattr(caught.value, 'failure_kind', None) == kind
    assert type(exc).__name__ in str(caught.value)


def test_parameter_rejection_never_retries_or_reduces_capacity(tmp_path):
    sent = []
    def wire(**kwargs):
        sent.append(kwargs['max_tokens'])
        raise ConfigError('HTTP 400: max_tokens rejected')
    control = ExecutionControl()
    control.bind(tmp_path / 'execution.json', run_id='run')
    gateway = Gateway(transport=wire, roles={'judge': {'model':'offline', 'max_tokens':5_000_000}}, control=control)
    with pytest.raises(ConfigError, match='max_tokens rejected'):
        gateway.call(CallRequest(role='judge', messages=[]))
    assert sent == [5_000_000]
    assert len(control.snapshot()['attempts']) == 1


def test_real_network_refusal_remains_network_failure():
    from aiforstudy.providers.transport import RealTransport
    reserved = socket.socket()
    reserved.bind(('127.0.0.1', 0))
    port = reserved.getsockname()[1]
    try:
        wire = RealTransport(model='offline', api_key='fixture', base_url=f'http://127.0.0.1:{port}')
        with pytest.raises(ConnectionError, match='ConnectionRefusedError'):
            wire(model='offline', effort='off', messages=[], images=[], max_tokens=1, timeout=2)
    finally:
        reserved.close()


def test_dead_ticket_kinds_owners_and_states_are_rejected():
    from aiforstudy.blocks.audit.contracts import KINDS, OWNERS, STATUSES
    assert 'source_conflict' not in KINDS
    assert 'source_review' not in OWNERS
    assert set(STATUSES) == {'ready', 'needs_evidence', 'closed'}
    with pytest.raises(ContractError):
        RepairDirective(unit_id='U', ticket_ids=('t',), owner='lesson', reason='r', kind='source_conflict')


def directive(hash_='a', why='积分步骤缺失', expected='给出积分计算', tickets=('t',)):
    row = RepairDirective(unit_id='U', ticket_ids=tickets, owner='lesson', kind='text_content',
                          artifact_hashes={'lesson:U': hash_}, reason=why)
    # Supply actual judgment, independent of the current artifact and ticket ID.
    return replace(row, verdict_content={'why':why, 'criteria':[{'expected':expected, 'method':'reviewer'}]})


def test_same_verdict_stops_after_changed_artifact_and_ticket_identity():
    coordinator = RepairCoordinator(ExecutionPolicy())
    try:
        first = directive()
    except TypeError:
        pytest.fail('RepairDirective lacks actual cross-version judgment content')
    state = UnitRepairState(unit_id='U', content_rounds_by_artifact={'lesson:U':1})
    state.last_candidate_signature = coordinator.signature([first], {'lesson:U':'a'})
    again = directive(hash_='b', why='  积分步骤缺失  ', tickets=('new-version-ticket',))
    stopped = coordinator.plan([again], state)
    assert isinstance(stopped, StopDecision)
    assert stopped.stopped_by == 'no_progress'
    assert '同一判词内容重复出现' in stopped.reason


def test_new_verdict_information_does_not_trigger_early_stop():
    coordinator = RepairCoordinator(ExecutionPolicy())
    try:
        first = directive()
    except TypeError:
        pytest.fail('RepairDirective lacks actual cross-version judgment content')
    state = UnitRepairState(unit_id='U', content_rounds_by_artifact={'lesson:U':1})
    state.last_candidate_signature = coordinator.signature([first], {'lesson:U':'a'})
    changed = directive(hash_='b', expected='还须给出上下限和定积分值')
    assert isinstance(coordinator.plan([changed], state), RepairBatch)
    assert state.content_rounds_by_artifact['lesson:U'] == 1


def test_distinct_frozen_criteria_with_same_wording_continue():
    from aiforstudy.blocks.audit.contracts import normalize_verdict_content
    one = {'criterion_id':'SOURCE-FAITHFUL', 'version':1, 'why':'缺少依据'}
    other = {'criterion_id':'DOMAIN-COMPLETE', 'version':1, 'why':'缺少依据'}
    assert normalize_verdict_content(one) != normalize_verdict_content(other)
    assert normalize_verdict_content({'why':'X 与 x 不同'}) != normalize_verdict_content({'why':'x 与 x 不同'})


def test_total_deadline_survives_resume_without_refund(tmp_path, monkeypatch):
    from aiforstudy.pipeline import execution_control as module
    wall = [100.0]
    monkeypatch.setattr(module.time, 'time', lambda: wall[0])
    policy = ExecutionPolicy(api_deadline_seconds=1000)
    control = ExecutionControl(policy, clock=lambda: 0.0)
    control.bind(tmp_path/'execution.json', run_id='run')
    wall[0] += 700
    restored = ExecutionControl(policy, clock=lambda: 0.0)
    restored.bind(tmp_path/'execution.json', run_id='run', require_existing=True)
    lease = restored.acquire_call(ExecutionScope('run'), fingerprint='f')
    assert lease.timeout_seconds() == 300
    lease.finish()


def test_expired_run_does_not_consume_new_attempt(tmp_path):
    now = [0.0]
    control = ExecutionControl(ExecutionPolicy(api_deadline_seconds=1), clock=lambda:now[0])
    control.bind(tmp_path/'execution.json', run_id='run')
    now[0] = 2
    with pytest.raises(TimeoutError, match='整轮'):
        control.acquire_call(ExecutionScope('run'), fingerprint='f')
    assert control.snapshot()['attempts'] == []


def test_directive_roundtrip_retains_new_judgment_content():
    from aiforstudy.blocks.audit.contracts import RepairDirective
    original = directive()
    restored = RepairDirective.from_dict(json.loads(json.dumps(original.as_dict())))
    assert restored.kind == original.kind
    assert restored.verdict_content == original.verdict_content


def test_content_directive_uses_full_current_judgment_not_only_problem():
    from dataclasses import replace
    from test_repair_contract_v2 import _full_ticket_data
    from aiforstudy.blocks.audit.contracts import validate_ticket
    from aiforstudy.pipeline.stage_io import _ticket_directives
    ticket = validate_ticket(_full_ticket_data())
    first = _ticket_directives([ticket], ticket.unit_id, 'a')[0]
    changed = replace(ticket, repair_instructions={**ticket.repair_instructions, 'action':'给出新的积分路线并核对端点'})
    second = _ticket_directives([changed], ticket.unit_id, 'b')[0]
    coordinator = RepairCoordinator(ExecutionPolicy())
    assert coordinator.signature([first]) != coordinator.signature([second])
    moved = _ticket_directives([replace(ticket,target={**ticket.target,'artifact_hash':'b'})], ticket.unit_id, 'b')[0]
    assert coordinator.signature([first]) == coordinator.signature([moved])


def test_reconciliation_bookkeeping_is_not_new_judgment_information():
    from dataclasses import replace
    from test_repair_contract_v2 import _full_ticket_data
    from aiforstudy.blocks.audit.contracts import validate_ticket
    from aiforstudy.pipeline.stage_io import _ticket_directives
    ticket = validate_ticket(_full_ticket_data())
    repeated = replace(ticket, previous_attempt={'closure_reason':ticket.problem})
    coordinator = RepairCoordinator(ExecutionPolicy())
    assert coordinator.signature(_ticket_directives([ticket],ticket.unit_id,'a')) == coordinator.signature(
        _ticket_directives([repeated],ticket.unit_id,'b'))
