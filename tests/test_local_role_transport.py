import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import pytest
from aiforstudy.errors import ConfigError
from aiforstudy.providers.gateway import CallRequest


def local(**kwargs):
    return importlib.import_module('aiforstudy.providers.local_compatible').LocalCompatibleTransport(model='fixture', **kwargs)


def test_local_no_key_wire_and_image(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((dict(self.headers), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':17,'completion_tokens':4,'total_tokens':21}}).encode())
        def log_message(self, *args): pass
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        client = local(base_url=f'http://127.0.0.1:{server.server_port}/v1')
        image = tmp_path/'x.png'; image.write_bytes(b'image-fixture')
        text, usage = client(model='fixture', effort='max', messages=[{'role':'system','content':'sys'}, {'role':'user','content':'inspect','image_labels':['source']}], images=[str(image)], max_tokens=256, timeout=5)
        assert text == '{"ok":true}' and usage['total_tokens'] == 21
        headers, body = received[0]
        assert 'Authorization' not in headers
        assert not {'thinking','reasoning_effort','response_format'} & body.keys()
        assert body['messages'][-1]['content'][-1]['image_url']['url'].startswith('data:image/png;base64,')
        assert client.calls == 1 and client.image_calls == 1
    finally:
        server.shutdown(); server.server_close(); thread.join()


@pytest.mark.parametrize('url', ['http://example.org/v1', 'http://user:secret@localhost/v1','http://localhost/v1?key=secret','file:///tmp/model'])
def test_local_rejects_unsafe_endpoint(url):
    with pytest.raises(ConfigError): local(base_url=url)


def test_role_routing_and_identity():
    router_cls = importlib.import_module('aiforstudy.providers.routing').RoleTransportRouter
    a = local(base_url='http://127.0.0.1:8001/v1')
    b = local(base_url='http://127.0.0.1:8002/v1')
    router = router_cls({'lesson':a,'media':b})
    assert router.for_role('lesson') is a and router.for_role('media') is b
    with pytest.raises(ConfigError): router.for_role('missing')
    request = CallRequest(role='lesson', messages=[{'role':'user','content':'same'}])
    assert request.fingerprint(model='same',effort='high',transport_identity=a.cache_identity) != request.fingerprint(model='same',effort='high',transport_identity=b.cache_identity)


def test_gateway_routes_and_does_not_reuse_other_endpoint_cache(tmp_path):
    from aiforstudy.providers.routing import RoleTransportRouter
    from aiforstudy.providers.gateway import Gateway
    from aiforstudy.pipeline.execution_control import ExecutionControl
    class Stub:
        def __init__(self, endpoint): self.cache_identity={'provider':'fixture','endpoint':endpoint}; self.calls=0
        def __call__(self, **kwargs): self.calls+=1; return '{"ok":true}',{}
    a,b=Stub('one'),Stub('two')
    control=ExecutionControl();control.bind(tmp_path/'execution.json',run_id='routes')
    roles={'lesson':{'model':'same'},'media':{'model':'same'}}
    gateway=Gateway(transport=RoleTransportRouter({'lesson':a,'media':b}), roles=roles,control=control,cache_dir=tmp_path/'cache')
    req=CallRequest(role='lesson',messages=[{'role':'user','content':'x'}])
    assert gateway.call(req).ok
    assert gateway.call(req).cached
    gateway.transport=RoleTransportRouter({'lesson':b,'media':a})
    assert not gateway.call(req).cached
    assert a.calls==b.calls==1


def test_factory_constructs_mixed_roles_without_cloud_key(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from aiforstudy.providers.factory import configured_providers
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    ws=SimpleNamespace(root=tmp_path,config_json=lambda _: {'provider':'deepseek-account','roles':{'lesson':{'model':'deepseek-flash'},'media':{'provider':'local-compatible','model':'vision','base_url':'http://127.0.0.1:8000/v1'}}})
    providers=configured_providers(SimpleNamespace(profile=SimpleNamespace(value='release')),ws)
    assert providers.llm.for_role('lesson').cache_identity['provider']=='deepseek-account'
    assert providers.llm.for_role('media').cache_identity['provider']=='local-compatible'


def test_local_timeout_reaps_worker():
    import time
    from aiforstudy.pipeline.services import CancelToken
    from aiforstudy.errors import Cancelled
    started=threading.Event();release=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            started.set(); release.wait(5)
        def log_message(self,*args): pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    token=CancelToken()
    client=local(base_url=f'http://127.0.0.1:{server.server_port}',cancel=token,cancel_grace_seconds=.1)
    timer=threading.Thread(target=lambda:(started.wait(3),token.cancel()),daemon=True);timer.start()
    try:
        before=time.monotonic()
        with pytest.raises(Cancelled):
            client(model='x',effort='high',messages=[{'role':'user','content':'x'}],images=None,max_tokens=20,timeout=4)
        assert time.monotonic()-before < 2
        assert client.last_remote_unconfirmed
        assert client.calls==0
    finally:
        release.set();server.shutdown();server.server_close();thread.join();timer.join()


def factory_for(config, tmp_path):
    from types import SimpleNamespace
    from aiforstudy.providers.factory import configured_providers
    workspace = SimpleNamespace(root=tmp_path, config_json=lambda _: config)
    request = SimpleNamespace(profile=SimpleNamespace(value='release'))
    return configured_providers(request, workspace).llm


@pytest.mark.parametrize('provider', ['local-compatible', 'deepseek'])
def test_factory_retains_distinct_role_credentials_at_same_endpoint(tmp_path, monkeypatch, provider):
    monkeypatch.setenv('AIFS_TEST_LESSON_KEY', 'lesson-sentinel')
    monkeypatch.setenv('AIFS_TEST_MEDIA_KEY', 'media-sentinel')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    client = factory_for({'provider': provider, 'base_url': 'http://127.0.0.1:8000/v1',
        'roles': {'lesson': {'model': 'same', 'api_key_ref': 'env:AIFS_TEST_LESSON_KEY'},
                  'media': {'model': 'same', 'api_key_ref': 'env:AIFS_TEST_MEDIA_KEY'}}}, tmp_path)
    from aiforstudy.providers.routing import RoleTransportRouter
    assert isinstance(client, RoleTransportRouter)
    assert client.for_role('lesson').api_key == 'lesson-sentinel'
    assert client.for_role('media').api_key == 'media-sentinel'
    assert 'sentinel' not in json.dumps(client.for_role('lesson').cache_identity)


@pytest.mark.parametrize('at_role', [True, False])
@pytest.mark.parametrize('legacy_present', [True, False])
def test_cloud_explicit_reference_overrides_legacy_key(tmp_path, monkeypatch, at_role, legacy_present):
    monkeypatch.setenv('AIFS_TEST_CLOUD_KEY', 'explicit-sentinel')
    if legacy_present:
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'legacy-sentinel')
    else:
        monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    config = {'provider': 'deepseek', 'roles': {'lesson': {'model': 'same'}}}
    target = config['roles']['lesson'] if at_role else config
    target['api_key_ref'] = 'env:AIFS_TEST_CLOUD_KEY'
    client = factory_for(config, tmp_path)
    assert client is not None
    assert client.api_key == 'explicit-sentinel'


@pytest.mark.parametrize('reference', ['env:AIFS_TEST_MISSING_KEY', 'env:', 'file:secrets', None, ''])
def test_cloud_missing_or_invalid_explicit_reference_never_uses_legacy(tmp_path, monkeypatch, reference):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'legacy-sentinel')
    monkeypatch.delenv('AIFS_TEST_MISSING_KEY', raising=False)
    config = {'provider': 'deepseek', 'api_key_ref': reference,
              'roles': {'lesson': {'model': 'same'}}}
    with pytest.raises(ConfigError):
        factory_for(config, tmp_path)


def test_cloud_without_explicit_reference_keeps_legacy_resolver(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'legacy-sentinel')
    config = {'provider': 'deepseek', 'roles': {'lesson': {'model': 'same'}, 'media': {'model': 'same'}}}
    client = factory_for(config, tmp_path)
    assert client.api_key == 'legacy-sentinel'
    assert client.network_wait is True


def test_missing_legacy_role_does_not_bypass_later_explicit_ref_validation(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('AIFS_TEST_MISSING_KEY', raising=False)
    config = {'provider': 'deepseek', 'roles': {
        'lesson': {'model': 'same'},
        'media': {'model': 'same', 'api_key_ref': 'env:AIFS_TEST_MISSING_KEY'}}}
    with pytest.raises(ConfigError, match='Configured credential is missing'):
        factory_for(config, tmp_path)


def test_cloud_role_reference_overrides_top_level_reference(tmp_path, monkeypatch):
    monkeypatch.setenv('AIFS_TEST_TOP_KEY', 'top-sentinel')
    monkeypatch.setenv('AIFS_TEST_ROLE_KEY', 'role-sentinel')
    config = {'provider': 'deepseek', 'api_key_ref': 'env:AIFS_TEST_TOP_KEY',
        'roles': {'lesson': {'model': 'same', 'api_key_ref': 'env:AIFS_TEST_ROLE_KEY'}}}
    assert factory_for(config, tmp_path).api_key == 'role-sentinel'


@pytest.mark.parametrize('env_present', [False, True])
def test_original_model_config_preserves_standard_secret_file_and_environment_priority(tmp_path, monkeypatch, env_present):
    from pathlib import Path
    config = json.loads((Path(__file__).parent/'fixtures/legacy-models-http.json').read_text())
    assert config['api_key_ref'] == 'env:DEEPSEEK_API_KEY'
    secrets = tmp_path/'.secrets'
    secrets.mkdir()
    (secrets/'deepseek.env').write_text('DEEPSEEK_API_KEY=file-sentinel\n')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    if env_present:
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'environment-sentinel')
    client = factory_for(config, tmp_path)
    route = client.for_role('lesson') if hasattr(client, 'for_role') else client
    assert route.api_key == ('environment-sentinel' if env_present else 'file-sentinel')
    assert route.report()['key_ref'] == ('env:DEEPSEEK_API_KEY' if env_present else
                                         'file:.secrets/deepseek.env#DEEPSEEK_API_KEY')
    assert 'sentinel' not in json.dumps(route.report())


def test_custom_reference_never_uses_standard_secret_file(tmp_path, monkeypatch):
    secrets = tmp_path/'.secrets'
    secrets.mkdir()
    (secrets/'deepseek.env').write_text('DEEPSEEK_API_KEY=file-sentinel\n')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'environment-sentinel')
    monkeypatch.delenv('AIFS_TEST_MISSING_KEY', raising=False)
    with pytest.raises(ConfigError, match='Configured credential is missing'):
        factory_for({'provider': 'deepseek', 'api_key_ref': 'env:AIFS_TEST_MISSING_KEY',
                     'roles': {'lesson': {'model': 'same'}}}, tmp_path)


@pytest.mark.parametrize('provider', ['deepseek', 'local-compatible'])
def test_custom_reference_keeps_safe_provenance(tmp_path, monkeypatch, provider):
    monkeypatch.setenv('AIFS_TEST_CLOUD_KEY', 'custom-sentinel')
    client = factory_for({'provider': provider, 'base_url': 'http://127.0.0.1:8000/v1',
                          'api_key_ref': 'env:AIFS_TEST_CLOUD_KEY',
                          'roles': {'lesson': {'model': 'same'}}}, tmp_path)
    assert client.report()['key_ref'] == 'env:AIFS_TEST_CLOUD_KEY'
    assert 'custom-sentinel' not in json.dumps(client.report())
