"""Capacity exhaustion preserves the actual answer and never resets retry identity."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

import pytest

from aiforstudy.errors import Cancelled, ConfigError
from aiforstudy.pipeline.execution_control import ExecutionControl, AttemptLimitExceeded
from aiforstudy.pipeline.services import CancelToken
from aiforstudy.providers.gateway import Gateway, CallRequest
from aiforstudy.providers.transport import RealTransport


@contextmanager
def endpoint(answer):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            text, reason, ident = answer(request, len(requests))
            payload = {'id': ident, 'model': request['model'],
                'choices': [{'finish_reason': reason,
                             'message': {'content': text, 'reasoning_content': 'private reasoning'}}],
                'usage': {'total_tokens': 17}, 'not_for_persistence': 'whole HTTP envelope'}
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def transport(url):
    return RealTransport(model='offline', api_key='not-a-real-key', base_url=url)


def request(unit='U'):
    return CallRequest(role='source_inventory', messages=[{'role':'user','content':unit}],
                       stage='lesson', unit_key=unit, scope_kind='unit')


def gateway(root, wire, *, role=None, observer=None, cancel=None):
    control = ExecutionControl()
    control.bind(root/'execution.json', run_id='run-capacity')
    return Gateway(transport=wire, roles={'source_inventory': role or {
        'model':'offline','effort':'low','max_tokens':8}}, control=control,
        cancel=cancel, cache_dir=root/'work/model-cache', book_id='book', call_observer=observer)


def evidence(root):
    return [json.loads(p.read_text()) for p in sorted((root/'model-responses').glob('*.json'))]


def test_http_worker_keeps_per_response_metadata_and_two_tuple_compatibility():
    with endpoint(lambda r,n: ('{"ok":true}', 'length', 'response-1')) as (url, _):
        result = transport(url)(model='offline',effort='low',messages=[],images=[],max_tokens=8,timeout=5)
    assert result == ('{"ok":true}', {'total_tokens':17})
    text, usage = result
    assert text == '{"ok":true}' and usage == {'total_tokens':17}
    assert getattr(result, 'metadata', None) == {
        'finish_reason':'length','response_id':'response-1','model':'offline'}


def test_http_bad_json_is_saved_for_each_attempt_without_http_envelope_or_credentials(tmp_path):
    with endpoint(lambda r,n: ('{"items":[', 'stop', f'response-{n}')) as (url, sent):
        result = gateway(tmp_path, transport(url)).call(request())
    assert not result.ok and result.attempts == 2 and len(sent) == 2
    rows = evidence(tmp_path)
    assert len(rows) == 2
    assert {r['metadata']['response_id'] for r in rows} == {'response-1','response-2'}
    assert all(r['text'] == '{"items":[' and r['metadata']['finish_reason'] == 'stop' for r in rows)
    assert all(r['max_tokens'] == 8 and r['fingerprint'] == result.fingerprint for r in rows)
    assert str(tmp_path/'model-responses') in result.error
    saved = json.dumps(rows)
    for forbidden in ('not-a-real-key','Authorization','private reasoning','whole HTTP envelope','messages'):
        assert forbidden not in saved
    attempts = json.loads((tmp_path/'execution.json').read_text())['attempts']
    assert [r['state'] for r in attempts] == ['protocol_failed','protocol_failed']
    assert all('model-responses' in r['error'] for r in attempts)


def test_parseable_length_is_not_success_and_without_capacity_retry_stops_persistently(tmp_path):
    with endpoint(lambda r,n: ('{"partial":true}', 'length', f'response-{n}')) as (url, sent):
        first = gateway(tmp_path, transport(url)).call(request())
        assert not first.ok and first.attempts == 1
        assert 'capacity' in first.error and 'model-responses' in first.error
        resumed = gateway(tmp_path, transport(url)).call(request())
        assert not resumed.ok and resumed.attempts == 1 and len(sent) == 1
    assert evidence(tmp_path)[0]['text'] == '{"partial":true}'
    assert not list((tmp_path/'work/model-cache').glob('*.json'))


def test_explicit_capacity_retry_uses_same_durable_identity_and_records_actual_limits(tmp_path):
    events = []
    role = {'model':'offline','effort':'low','max_tokens':8,'length_retry_max_tokens':16}
    def answer(r,n):
        return ('{"partial":true}', 'length', 'short') if r['max_tokens'] == 8 else ('{"ok":true}', 'stop', 'complete')
    with endpoint(answer) as (url, sent):
        first = gateway(tmp_path, transport(url),role=role,observer=lambda e,m: events.append((e,m))).call(request())
        assert first.ok and first.payload == {'ok':True} and first.attempts == 2
        again = gateway(tmp_path, transport(url),role=role).call(request())
        assert again.cached and again.payload == {'ok':True}
        assert [r['max_tokens'] for r in sent] == [8,16]
        assert all(r['messages'] == sent[0]['messages'] and r['reasoning_effort'] == 'low' for r in sent)
    attempts = json.loads((tmp_path/'execution.json').read_text())['attempts']
    assert len({a['fingerprint'] for a in attempts}) == 1
    assert [m['max_tokens'] for e,m in events if e == 'started'] == [8,16]
    assert [m['max_tokens'] for e,m in events if e == 'response'] == [8,16]
    assert len(evidence(tmp_path)) == 1


def test_cancel_after_truncation_and_restart_preserve_expansion_and_attempt_count(tmp_path):
    token = CancelToken()
    def observe(event, metadata):
        if event == 'response': token.cancel('pause before second request')
    role = {'model':'offline','effort':'low','max_tokens':8,'length_retry_max_tokens':16}
    with endpoint(lambda r,n: ('{"partial":true}', 'length', str(n)) if n == 1
                  else ('{"ok":true}', 'stop', str(n))) as (url, sent):
        with pytest.raises(Cancelled):
            gateway(tmp_path, transport(url),role=role,observer=observe,cancel=token).call(request())
        result = gateway(tmp_path, transport(url),role=role).call(request())
        assert result.ok and result.attempts == 2
        assert [r['max_tokens'] for r in sent] == [8,16]
    assert len(json.loads((tmp_path/'execution.json').read_text())['attempts']) == 2


def test_exhausted_capacity_and_changed_retry_policy_cannot_bypass_persistent_limit(tmp_path):
    role = {'model':'offline','effort':'low','max_tokens':8,'length_retry_max_tokens':16}
    with endpoint(lambda r,n: ('{"partial":true}', 'length', str(n))) as (url, sent):
        first = gateway(tmp_path, transport(url),role=role).call(request())
        assert not first.ok and first.attempts == 2
        for retry in (16,32):
            with pytest.raises(AttemptLimitExceeded):
                gateway(tmp_path,transport(url),role={**role,'length_retry_max_tokens':retry}).call(request())
        assert [r['max_tokens'] for r in sent] == [8,16]


def test_cache_capacity_policy_change_is_not_silent_cache_hit(tmp_path):
    role = {'model':'offline','effort':'low','max_tokens':8,'length_retry_max_tokens':16}
    with endpoint(lambda r,n: ('{"n":'+str(n)+'}', 'stop', str(n))) as (url,sent):
        first = gateway(tmp_path,transport(url),role=role).call(request())
        second = gateway(tmp_path,transport(url),role={**role,'length_retry_max_tokens':32}).call(request())
        assert not second.cached and second.payload == {'n':2}
        assert first.fingerprint == second.fingerprint
        assert len(sent) == 2


def test_metadata_belongs_to_each_response_even_when_transport_is_shared():
    def answer(r,n):
        unit = r['messages'][0]['content']
        if unit == 'slow': time.sleep(.1)
        return json.dumps({'unit':unit}), 'length' if unit == 'slow' else 'stop', unit
    with endpoint(answer) as (url, _):
        shared = transport(url)
        def invoke(unit):
            return shared(model='offline',effort='low',messages=[{'role':'user','content':unit}],
                          images=[],max_tokens=8,timeout=5)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(invoke,['slow','fast']))
    assert [getattr(r,'metadata',None) for r in results] == [
        {'finish_reason':'length','response_id':'slow','model':'offline'},
        {'finish_reason':'stop','response_id':'fast','model':'offline'}]


def test_legacy_two_tuple_transport_keeps_success_and_finite_bad_json_retry(tmp_path):
    values = iter([('bad JSON', {}), ('{"ok":true}', {})])
    result = gateway(tmp_path, lambda **kwargs: next(values)).call(request())
    assert result.ok and result.attempts == 2
    rows = evidence(tmp_path)
    assert len(rows) == 1 and rows[0]['text'] == 'bad JSON' and rows[0]['metadata'] == {}


@pytest.mark.parametrize('role', [
    {'max_tokens':8,'length_retry_max_tokens':8},
    {'max_tokens':8,'length_retry_max_tokens':4},
    {'max_tokens':8,'length_retry_max_tokens':True},
])
def test_bad_capacity_configuration_is_rejected_before_dispatch(tmp_path, role):
    def never(**kwargs): pytest.fail('invalid capacity must not reach provider')
    with pytest.raises(ConfigError):
        gateway(tmp_path, never,role={'model':'offline','effort':'low',**role}).call(request())


def test_source_inventory_role_reaches_provider_without_changing_judge(tmp_path):
    from aiforstudy.core.workspace import Workspace
    from aiforstudy.providers.llm import load_config
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(Workspace(root))
    role = cfg.role('source_inventory')
    seen = []
    result = gateway(tmp_path,lambda **kw: (seen.append(kw) or ('{}',{})),role=role).call(request())
    assert result.ok and seen[0]['max_tokens'] == 64000 and seen[0]['effort'] == 'low'
    assert role.as_dict()['length_retry_max_tokens'] == 128000
    assert cfg.role('judge').max_tokens == 393_216 and cfg.role('judge').effort == 'high'


def configured_judge_gateway(root, wire):
    from aiforstudy.core.workspace import Workspace
    from aiforstudy.providers.llm import load_config
    role = load_config(Workspace(Path(__file__).resolve().parents[1])).role('judge')
    control = ExecutionControl()
    control.bind(root/'execution.json', run_id='run-judge-capacity')
    return Gateway(transport=wire, roles={'judge': role}, control=control,
                   cache_dir=root/'work/model-cache', book_id='book')


def judge_request():
    return CallRequest(role='judge', messages=[{'role':'user','content':'fixed review criteria'}],
                       stage='text_audit', unit_key='U', scope_kind='unit')


def test_configured_judge_uses_approved_393216_without_retrying_truncation(tmp_path):
    with endpoint(lambda req, number: ('', 'length', 'truncated')) as (url, sent):
        result = configured_judge_gateway(tmp_path, transport(url)).call(judge_request())
        assert not result.ok and result.attempts == 1
        assert result.failure_kind == 'truncation'
        assert [r['max_tokens'] for r in sent] == [393_216]
        assert sent[0]['reasoning_effort'] == 'high'
    assert evidence(tmp_path)[0]['max_tokens'] == 393_216


def test_configured_judge_persisted_truncation_cannot_send_another_call_after_resume(tmp_path):
    with endpoint(lambda req, number: ('', 'length', 'truncated')) as (url, sent):
        result = configured_judge_gateway(tmp_path, transport(url)).call(judge_request())
        for _ in range(2):
            resumed = configured_judge_gateway(tmp_path, transport(url)).call(judge_request())
            assert not resumed.ok and resumed.attempts == 1
            assert resumed.failure_kind == 'truncation'
        assert [r['max_tokens'] for r in sent] == [393_216]
    assert len(evidence(tmp_path)) == 1
    assert len(json.loads((tmp_path/'execution.json').read_text())['attempts']) == 1
