import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from aiforstudy.errors import ContractError
from aiforstudy.pipeline.execution_control import AttemptLimitExceeded
from aiforstudy.practice import execution, model
from aiforstudy.practice.model import question_input
from test_practice_catalog import question


def test_blind_input_only_contains_question_and_applicable_context():
 q={'id':'q1','stem':'真实题面','answer':'AUTHOR_SECRET','solution':'AUTHOR_SOLUTION','source':{'course':'复变函数A','common_header':'路径正向','source_quote':'HIDDEN_SOURCE_ANSWER','pdf_path':'/private/LOCAL_PATH','year':'YEAR_HINT','answer':'SOURCE_SECRET'}}
 payload=question_input(q,'blind');s=str(payload)
 assert payload['stem']=='真实题面'
 assert payload['source']=={'course':'复变函数A','common_header':'路径正向'}
 for secret in ['AUTHOR_SECRET','AUTHOR_SOLUTION','HIDDEN_SOURCE_ANSWER','LOCAL_PATH','YEAR_HINT','SOURCE_SECRET']:assert secret not in s

def test_comparison_receives_all_published_solution_support():
 q={'id':'q','title':'标题','stem':'题面','answer':'答案','solution':'推导','pitfalls':['必须检查的易错说明'],'methods':['方法标签'],'topic':'主题','unit_id':'CH01','source':{'course':'复变函数A'},'independent':{'answer':'独立解'}}
 p=question_input(q,'compare')
 assert p['pitfalls']==q['pitfalls'] and p['methods']==q['methods'] and p['title']==q['title']


@pytest.fixture
def invalid_offline_model(monkeypatch):
    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        return '{invalid fixture JSON', {}

    ws = SimpleNamespace(config_json=lambda name: {
        'roles': {'text_judge': {'model': 'offline-fixture'}},
    })
    monkeypatch.setattr(model, 'open_workspace', lambda path: ws)
    monkeypatch.setattr(model, 'configured_providers', lambda *args: SimpleNamespace(llm=transport))
    return calls


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'cleared_attempts', 'truncated_attempts'])
def test_resume_cannot_reset_a_lost_or_damaged_attempt_ledger(tmp_path, invalid_offline_model, damage):
    output = tmp_path / 'calls'
    with pytest.raises(RuntimeError):
        model.call_batch(tmp_path, [question()], output)
    with pytest.raises(AttemptLimitExceeded):
        model.call_batch(tmp_path, [question()], output)
    ledger = output / 'execution.json'
    saved = json.loads(ledger.read_text())
    assert len(saved['attempts']) == len(invalid_offline_model) == 2
    assert (output / 'request.json').is_file()
    assert (output / 'response.json').is_file()
    assert any((output / 'model-responses').rglob('*.json'))

    if damage == 'missing':
        ledger.unlink()
    elif damage == 'corrupt':
        ledger.write_text('{broken')
    else:
        attempts = saved['attempts'][:1] if damage == 'truncated_attempts' else []
        ledger.write_text(json.dumps({**saved, 'attempts': attempts}))
    before = ledger.read_bytes() if ledger.exists() else None
    with pytest.raises(ContractError, match='执行'):
        model.call_batch(tmp_path, [question()], output)
    assert len(invalid_offline_model) == 2
    assert (ledger.read_bytes() if ledger.exists() else None) == before


def test_direct_batch_rejects_images_before_configuring_transport(tmp_path, monkeypatch):
    q = question()
    q['source']['common_header'] = '见图 ![曲线]'
    monkeypatch.setattr(model, 'configured_providers', lambda *args: pytest.fail('provider requested'))
    monkeypatch.setattr(model, 'open_workspace', lambda *args: pytest.fail('workspace requested'))
    with pytest.raises(ValueError, match='image'):
        model.call_batch(tmp_path, [q], tmp_path / 'out')
    assert not (tmp_path / 'out').exists()



def test_concurrent_batch_cannot_rebind_an_active_ledger(tmp_path, monkeypatch):
    output = tmp_path / 'calls'
    started, release = Event(), Event()
    calls = []

    def offline_transport(**kwargs):
        calls.append(kwargs)
        started.set()
        assert release.wait(timeout=5)
        return json.dumps({'questions': [{
            'id': 'A-1', 'status': 'solved', 'answer': '答案', 'solution': '过程', 'issues': [],
        }]}), {}

    workspace = SimpleNamespace(config_json=lambda name: {
        'roles': {'text_judge': {'model': 'offline-fixture'}},
    })
    monkeypatch.setattr(model, 'open_workspace', lambda path: workspace)
    monkeypatch.setattr(model, 'configured_providers', lambda *args: SimpleNamespace(llm=offline_transport))
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(model.call_batch, tmp_path, [question()], output)
        try:
            assert started.wait(timeout=5)
            with pytest.raises(ContractError, match='正在使用'):
                model.call_batch(tmp_path, [question()], output)
        finally:
            release.set()
        first = pending.result(timeout=5)

    resumed = model.call_batch(tmp_path, [question()], output)
    assert resumed['fingerprint'] == first['fingerprint']
    assert len(calls) == 1
    assert len(json.loads((output / 'execution.json').read_text())['attempts']) == 1


def test_timeout_attempts_survive_truncation_without_per_attempt_responses(tmp_path, monkeypatch):
    output = tmp_path / 'calls'
    calls = []

    def timed_out(**kwargs):
        calls.append(kwargs)
        proof = json.loads((output / 'dispatch.json').read_text())
        ledger = json.loads((output / 'execution.json').read_text())
        assert len(proof['attempts']) == len(ledger['attempts']) == len(calls)
        assert proof['scope'] == ledger['attempts'][-1]['scope']
        assert proof['request_sha256'] == json.loads((output / 'request.json').read_text())['sha256']
        for field in ('attempt_id', 'attempt', 'fingerprint'):
            assert proof['attempts'][-1][field] == ledger['attempts'][-1][field]
        raise TimeoutError('offline transport timed out before any reply')

    workspace = SimpleNamespace(config_json=lambda name: {
        'roles': {'text_judge': {'model': 'offline-fixture'}},
    })
    monkeypatch.setattr(model, 'open_workspace', lambda path: workspace)
    monkeypatch.setattr(model, 'configured_providers', lambda *args: SimpleNamespace(llm=timed_out))
    monkeypatch.setattr(model.Gateway, '_wait_network_retry', lambda self: None)
    with pytest.raises(RuntimeError, match='offline transport timed out'):
        model.call_batch(tmp_path, [question()], output)
    assert len(calls) == 2
    assert (output / 'response.json').is_file()
    assert not list((output / 'model-responses').glob('*.json'))

    ledger = output / 'execution.json'
    saved = json.loads(ledger.read_text())
    saved['attempts'].pop()
    ledger.write_text(json.dumps(saved))
    before = ledger.read_bytes()
    with pytest.raises(ContractError, match='执行'):
        model.call_batch(tmp_path, [question()], output)
    assert len(calls) == 2
    assert ledger.read_bytes() == before


@pytest.mark.parametrize('damage', ['ledger_stage', 'ledger_fingerprint', 'response_stage'])
def test_retry_rejects_mismatched_transport_identity(tmp_path, invalid_offline_model, damage):
    output = tmp_path / 'calls'
    with pytest.raises(RuntimeError):
        model.call_batch(tmp_path, [question()], output)
    assert len(invalid_offline_model) == 2
    if damage == 'response_stage':
        path = next((output / 'model-responses').glob('*.json'))
        record = json.loads(path.read_text())
        record['scope']['stage'] = 'other-stage'
    else:
        path = output / 'execution.json'
        record = json.loads(path.read_text())
        for attempt in record['attempts']:
            if damage == 'ledger_stage':
                attempt['scope']['stage'] = 'other-stage'
            else:
                attempt['fingerprint'] = 'sha256:' + 'e' * 64
    path.write_text(json.dumps(record))
    with pytest.raises(ContractError, match='执行'):
        model.call_batch(tmp_path, [question()], output)
    assert len(invalid_offline_model) == 2


@pytest.mark.parametrize('field', ['answer', 'solution', 'independent', 'pitfalls'])
def test_direct_compare_rejects_images_in_forwarded_stage_material(tmp_path, monkeypatch, field):
    q = question()
    q['independent'] = {'solution': '独立推导'}
    image = '推导用图 ![示意][diagram]\n\n[diagram]: assets/diagram.svg'
    q[field] = {'conditions': [image]} if field == 'independent' else [image] if field == 'pitfalls' else image
    monkeypatch.setattr(model, 'configured_providers', lambda *args: pytest.fail('provider requested'))
    monkeypatch.setattr(model, 'open_workspace', lambda *args: pytest.fail('workspace requested'))
    with pytest.raises(ValueError, match='image'):
        model.call_batch(tmp_path, [q], tmp_path / 'out', mode='compare')
    assert not (tmp_path / 'out').exists()


def test_blind_input_ignores_image_markup_in_omitted_fields():
    q = question()
    q['answer'] = '作者先给的图 ![图](assets/image.svg)'
    q['source']['source_quote'] = '私有原文 ![图](assets/image.svg)'
    assert question_input(q, 'blind')['stem'] == q['stem']



def test_dispatch_accounting_write_failure_blocks_transport_and_resume(tmp_path, monkeypatch, invalid_offline_model):
    output = tmp_path / 'calls'
    write = execution.write_json_atomic

    def fail_on_dispatch(path, payload):
        if payload['attempts']:
            raise OSError('offline test could not persist the dispatch')
        return write(path, payload)

    with monkeypatch.context() as overrides:
        overrides.setattr(execution, 'write_json_atomic', fail_on_dispatch)
        with pytest.raises(ContractError, match='无法持久化'):
            model.call_batch(tmp_path, [question()], output)
    assert not invalid_offline_model
    assert len(json.loads((output / 'execution.json').read_text())['attempts']) == 1
    accounting = output / 'dispatch.json'
    before = accounting.read_bytes()
    assert json.loads(before)['attempts'] == []

    with pytest.raises(ContractError, match='独立派发记录不一致'):
        model.call_batch(tmp_path, [question()], output)
    assert not invalid_offline_model
    assert accounting.read_bytes() == before
