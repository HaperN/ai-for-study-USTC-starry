"""Offline proof that optional observation never changes real model routing."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy
from aiforstudy.providers.gateway import CallRequest, Gateway
from aiforstudy.providers.transport import RealTransport


@pytest.fixture
def observer_type():
    path = Path(__file__).resolve().parents[1] / 'tools/real_run_observer.py'
    spec = importlib.util.spec_from_file_location('real_run_observer_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LocalRunObserver


def roles():
    return json.loads((Path(__file__).resolve().parents[1] / 'config/models.json').read_text())['roles']


def gateway(tmp_path, transport, observer=None, *, run_id='run-1', cache=False, parallel=2):
    control = ExecutionControl(ExecutionPolicy(parallelism=parallel))
    control.bind(tmp_path / 'execution.json', run_id=run_id)
    return Gateway(transport=transport, roles=roles(), control=control, book_id='book-1',
                   cache_dir=tmp_path / 'cache' if cache else None,
                   call_observer=observer)


def request(role='lesson', unit='U1'):
    return CallRequest(role=role, messages=[{'role':'user', 'content':'PRIVATE-BOOK-CONTENT'}],
                       stage='figures' if role == 'judge' else role,
                       unit_key=unit, resource_id='media-1' if role == 'judge' else '')


def rows(path):
    return [json.loads(line) for line in (path / 'model-observations.jsonl').read_text().splitlines()]


def test_constructing_observer_is_read_only_and_never_constructs_transport(observer_type, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('observer must not construct a service')
    monkeypatch.setattr(RealTransport, '__post_init__', forbidden)
    output = tmp_path / 'not-created'
    observer = observer_type(output, book_id='book-1')
    assert not output.exists()
    assert observer.summary()['transport_attempts'] == 0


def test_all_production_roles_forward_to_the_same_real_transport(observer_type, tmp_path, monkeypatch):
    observer = observer_type(tmp_path / 'observations', book_id='book-1')
    seen = []
    def exchange(self, **kwargs):
        seen.append(kwargs)
        return '{"actual_transport_response":true}', {'prompt_tokens':11, 'completion_tokens':7, 'total_tokens':18}
    monkeypatch.setattr(RealTransport, '__call__', exchange)
    real = RealTransport(model='configured', api_key='PRIVATE-KEY')
    g = gateway(tmp_path, real, observer)
    for role, spec in roles().items():
        answer = g.call(request(role))
        assert answer.payload == {'actual_transport_response':True}
        assert seen[-1]['model'] == spec['model']
        assert seen[-1]['messages'] == request(role).messages
        assert seen[-1]['max_tokens'] == spec['max_tokens']
    events = rows(tmp_path / 'observations')
    responses = [row for row in events if row['event'] == 'response']
    assert {row['role'] for row in responses} == set(roles())
    judge = next(row for row in responses if row['role'] == 'judge')
    assert (judge['stage'], judge['unit_id'], judge['resource_id']) == ('figures', 'U1', 'media-1')
    assert len(seen) == len(roles()) == observer.summary()['transport_attempts']
    assert observer.summary()['known_reported_token_totals']['total_tokens'] == 18 * len(roles())
    assert 'PRIVATE-' not in json.dumps(events)


def test_retry_keeps_provider_usage_even_when_model_json_is_bad(observer_type, tmp_path):
    answers = iter([('broken json', {'total_tokens':13}), ('{"ok":true}', {'total_tokens':17})])
    observer = observer_type(tmp_path / 'observations', book_id='book-1')
    result = gateway(tmp_path, lambda **kwargs:next(answers), observer).call(request())
    assert result.ok and result.attempts == 2
    events = rows(tmp_path / 'observations')
    responses = [row for row in events if row['event'] == 'response']
    assert [row['attempt'] for row in responses] == [1, 2]
    assert len({row['attempt_id'] for row in responses}) == 2
    assert observer.summary()['known_reported_token_totals']['total_tokens'] == 30


@pytest.mark.parametrize('usage', [None, {}, {'total_tokens':'invalid'}, {'total_tokens':True}, []])
def test_missing_or_invalid_usage_is_unknown_not_zero(observer_type, tmp_path, usage):
    observer = observer_type(tmp_path / 'observations', book_id='book-1')
    assert gateway(tmp_path, lambda **kwargs:('{"ok":true}', usage), observer).call(request()).ok
    summary = observer.summary()
    assert summary['unknown_usage_attempts'] == 1
    assert summary['known_reported_token_totals'] == {}
    assert summary['token_accounting_complete'] is False


def test_failures_are_unknown_cache_hits_do_not_invent_usage(observer_type, tmp_path):
    observer = observer_type(tmp_path / 'observations', book_id='book-1')
    calls = []
    def exchange(**kwargs):
        calls.append(1)
        if len(calls) == 1: raise TimeoutError('PRIVATE-KEY and PRIVATE-BOOK-CONTENT')
        return '{"ok":true}', {'total_tokens':9, 'prompt_tokens_details':{'cached_tokens':4, 'secret':'PRIVATE-KEY'}, 'raw':'PRIVATE-BOOK-CONTENT'}
    g = gateway(tmp_path, exchange, observer, cache=True)
    assert g.call(request()).ok
    assert g.call(request()).cached
    assert len(calls) == 2
    summary = observer.summary()
    assert summary['transport_attempts'] == 2 and summary['cache_hits'] == 1
    assert summary['failed_attempts'] == 1 and summary['unknown_usage_attempts'] == 1
    assert summary['known_reported_token_totals']['total_tokens'] == 9
    assert summary['token_accounting_complete'] is False
    assert 'PRIVATE-' not in (tmp_path / 'observations/model-observations.jsonl').read_text()


def test_broken_callback_never_discards_response_or_adds_retry(tmp_path):
    calls = []
    def exchange(**kwargs):
        calls.append(1)
        return '{"ok":true}', {'total_tokens':1}
    def broken_observer(event, metadata):
        raise OSError('observer disk is full')
    result = gateway(tmp_path, exchange, broken_observer).call(request())
    assert result.ok and len(calls) == 1


def test_disk_error_is_visible_in_summary_without_harming_response(observer_type, tmp_path):
    output = tmp_path / 'observations'
    observer = observer_type(output, book_id='book-1')
    output.write_text('directory became unavailable')
    assert gateway(tmp_path, lambda **kwargs:('{"ok":true}', {'total_tokens':5}), observer).call(request()).ok
    assert observer.summary()['write_errors'] == 2
    assert observer.summary()['known_reported_token_totals']['total_tokens'] == 5


def test_concurrency_preserves_scope_attempt_and_complete_jsonl(observer_type, tmp_path):
    observer = observer_type(tmp_path / 'observations', book_id='book-1')
    barrier = threading.Barrier(2)
    def exchange(**kwargs):
        barrier.wait(timeout=5)
        return '{"ok":true}', {'total_tokens':3}
    g = gateway(tmp_path, exchange, observer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda unit:g.call(request(unit=unit)), ['U1', 'U2']))
    assert all(row.ok for row in results)
    events = rows(tmp_path / 'observations')
    for unit in ('U1', 'U2'):
        pairs = [row for row in events if row['unit_id'] == unit]
        assert {row['event'] for row in pairs} == {'started', 'response'}
        assert len({row['attempt_id'] for row in pairs}) == 1
    assert observer.summary()['known_reported_token_totals']['total_tokens'] == 6


def test_resume_appends_new_session_without_refunding_interrupted_attempt(observer_type, tmp_path):
    output = tmp_path / 'observations'
    first = observer_type(output, book_id='book-1')
    def interrupt(**kwargs): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): gateway(tmp_path, interrupt, first).call(request())
    second = observer_type(output, book_id='book-1', resume_run_id='run-1')
    result = gateway(tmp_path, lambda **kwargs:('{"ok":true}', {'total_tokens':8}), second).call(request())
    assert result.ok and result.attempts == 2
    events = rows(output)
    assert len({row['session_id'] for row in events}) == 2
    assert second.summary()['transport_attempts'] == 2
    assert second.summary()['unknown_usage_attempts'] == 1
    assert second.summary()['known_reported_token_totals']['total_tokens'] == 8
    with pytest.raises(ValueError): observer_type(output, book_id='book-1')
    with pytest.raises(ValueError): observer_type(output, book_id='other', resume_run_id='run-1')
    with pytest.raises(ValueError): observer_type(output, book_id='book-1', resume_run_id='other-run')


def test_corrupt_observation_log_is_rejected_without_rewriting(observer_type, tmp_path):
    output = tmp_path / 'observations'
    output.mkdir()
    path = output / 'model-observations.jsonl'
    path.write_text('{"schema_version":"obsolete"}\n')
    before = path.read_bytes()
    with pytest.raises(ValueError): observer_type(output, book_id='book-1', resume_run_id='run-1')
    assert path.read_bytes() == before


def test_actual_gateway_client_wires_observer_from_providers(observer_type, tmp_path):
    from aiforstudy.pipeline.gateway_client import _gateway_client
    from aiforstudy.pipeline.services import CancelToken, Providers
    observer = observer_type(tmp_path / 'observations', book_id='book-1')
    ctx = SimpleNamespace(
        workspace=SimpleNamespace(config_json=lambda name:{'roles':roles()}),
        context=SimpleNamespace(book_id='book-1'),
        node=SimpleNamespace(stage='figures', scope_key='U1'),
        services=SimpleNamespace(
            store=SimpleNamespace(root=tmp_path, work=tmp_path / 'work', run_id='run-1'),
            control=ExecutionControl(), cancel=CancelToken(),
            providers=Providers(llm=lambda **kwargs:('{"ok":true}', {'total_tokens':4}), call_observer=observer)),
    )
    client, source = _gateway_client(ctx, role='judge', bucket='figures')
    client.resource_id = 'media-1'
    assert client.chat_json(request('judge').messages)['data'] == {'ok':True}
    assert source == 'injected'
    response = next(row for row in rows(tmp_path / 'observations') if row['event'] == 'response')
    assert (response['role'], response['stage'], response['resource_id']) == ('judge', 'figures', 'media-1')


def test_resumed_summary_includes_attempts_missing_from_observer_log(observer_type, tmp_path):
    def interrupt(**kwargs): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): gateway(tmp_path, interrupt).call(request())
    observer = observer_type(tmp_path / 'observations', book_id='book-1', resume_run_id='run-1')
    assert gateway(tmp_path, lambda **kwargs:('{"ok":true}', {'total_tokens':4}), observer).call(request()).ok
    summary = observer.summary(execution_path=tmp_path / 'execution.json')
    assert summary['transport_attempts'] == 2
    assert summary['unobserved_attempts'] == 1
    assert summary['unknown_usage_attempts'] == 1
    assert summary['token_accounting_complete'] is False


def test_observer_callback_cannot_mutate_provider_usage(tmp_path):
    usage = {'total_tokens':4}
    def mutate(event, metadata):
        if event == 'response': metadata['usage']['total_tokens'] = 999
    assert gateway(tmp_path, lambda **kwargs:('{"ok":true}', usage), mutate).call(request()).ok
    assert usage == {'total_tokens':4}


@pytest.mark.parametrize('damage', ['run', 'fingerprint', 'malformed'])
def test_journal_reconciliation_failure_is_reported_without_throwing(observer_type, tmp_path, damage):
    observer = observer_type(tmp_path / 'observations', book_id='book-1')
    assert gateway(tmp_path, lambda **kwargs:('{"ok":true}', {'total_tokens':4}), observer).call(request()).ok
    path = tmp_path / 'execution.json'
    payload = json.loads(path.read_text())
    if damage == 'run': payload['run_id'] = 'another-run'
    if damage == 'fingerprint': payload['attempts'][0]['fingerprint'] = 'wrong'
    path.write_text('{' if damage == 'malformed' else json.dumps(payload))
    summary = observer.summary(execution_path=path)
    assert summary['execution_reconciliation'] == 'failed'
    assert summary['token_accounting_complete'] is False


def test_two_new_observers_cannot_mix_different_runs_in_one_output(observer_type, tmp_path):
    output = tmp_path / 'observations'
    first = observer_type(output, book_id='book-1')
    second = observer_type(output, book_id='book-1')
    assert gateway(tmp_path / 'one', lambda **kwargs:('{"ok":true}', {'total_tokens':4}), first).call(request()).ok
    assert gateway(tmp_path / 'two', lambda **kwargs:('{"ok":true}', {'total_tokens':5}), second, run_id='run-2').call(request()).ok
    assert {row['run_id'] for row in rows(output)} == {'run-1'}
    assert second.summary()['write_errors'] == 2
