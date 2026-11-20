"""M6 current contracts: durable bounded attempts, cache, cancellation and identity.

Historical fee aggregation assertions belong to explicit UsageLedger tests;
production recovery never reads billing records or cumulative reservations.
"""
from pathlib import Path
import json
import pytest
from aiforstudy.errors import Cancelled, CapabilityError, ContractError
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy, AttemptLimitExceeded
from aiforstudy.pipeline.services import CancelToken
from aiforstudy.providers.gateway import MAX_ATTEMPTS, CallRequest, Gateway, replay_transport


def make_gateway(tmp_path, transport, *, policy=None, cancel=None, run_id="run-1", cache=True):
    control = ExecutionControl(policy or ExecutionPolicy())
    control.bind(tmp_path / 'execution.json', run_id=run_id)
    return Gateway(transport=transport, roles={"lesson": Role()}, control=control, cancel=cancel,
                   cache_dir=tmp_path / "cache" if cache else None, run_id=run_id, book_id="b1")


class Role:
    def __init__(self, model="m1", effort="high", max_tokens=1000):
        self.model, self.effort, self.max_tokens = model, effort, max_tokens


def req(**kw) -> CallRequest:
    base = dict(role="lesson", messages=[{"role": "user", "content": "hi"}],
                bucket="chapter", unit_key="CH01-S01", stage="lesson")
    base.update(kw)
    return CallRequest(**base)


def test_fingerprint_ignores_run_id_and_paths_but_tracks_content(tmp_path):
    """§11：指纹不含 run_id/时间/绝对路径；**必须**含实际消费内容与逻辑身份。"""
    def transport(**kw):
        return '{"ok": 1}', {"total_tokens": 1}

    a = make_gateway(tmp_path / "a", transport, run_id="run-A")
    b = make_gateway(tmp_path / "b", transport, run_id="run-B")
    assert a.call(req()).fingerprint == b.call(req()).fingerprint, "换 run_id 不该换指纹"
    assert a.call(req(messages=[{"role": "user", "content": "别的"}])).fingerprint != \
        a.call(req()).fingerprint, "内容变了指纹必须变"
    assert a.call(req(prompt_version="v2")).fingerprint != a.call(req()).fingerprint
    assert a.call(req(unit_key="CH09-S09")).fingerprint != a.call(req()).fingerprint, \
        "跨书/跨单元不许串缓存归属"
    assert a.call(req(role="lesson")).fingerprint == a.call(req(role="lesson")).fingerprint


def test_replay_transport_refuses_instead_of_falling_back(tmp_path):
    """§4：replay 缺缓存**立即阻断**，禁止回退真实服务。"""
    transport = replay_transport({})
    gateway = make_gateway(tmp_path, transport)
    with pytest.raises(CapabilityError, match="立即阻断"):
        gateway.call(req())


def test_image_cache_tracks_bytes_and_does_not_reuse_same_path_with_new_content(tmp_path):
    image = tmp_path / 'page.png'
    image.write_bytes(b'first-image-content')
    calls = []

    def transport(**kwargs):
        calls.append(Path(kwargs['images'][0]).read_bytes())
        return '{"accepted":true}', {"total_tokens": 10}

    gateway = make_gateway(tmp_path, transport)
    request = req(images=(str(image),))
    first = gateway.call(request)
    cached = gateway.call(request)
    image.write_bytes(b'changed-image-content')
    changed = gateway.call(request)
    assert first.ok and cached.cached
    assert changed.ok and not changed.cached
    assert changed.fingerprint != first.fingerprint
    assert calls == [b'first-image-content', b'changed-image-content']


def test_image_fingerprint_ignores_location_but_rejects_missing_input(tmp_path):
    first = tmp_path / 'first.png'
    second = tmp_path / 'renamed.png'
    first.write_bytes(b'same-image')
    second.write_bytes(b'same-image')
    assert req(images=(str(first),)).fingerprint(model='m', effort='high') == \
        req(images=(str(second),)).fingerprint(model='m', effort='high')
    first.unlink()
    with pytest.raises(ContractError, match='图像|图片'):
        req(images=(str(first),)).fingerprint(model='m', effort='high')


def test_model_output_limit_configuration_is_part_of_effective_cache_identity(tmp_path):
    calls = []

    def transport(**kwargs):
        calls.append(kwargs['max_tokens'])
        return '{"accepted":true}', {"total_tokens": 10}

    gateway = make_gateway(tmp_path, transport)
    first = gateway.call(req())
    gateway.roles['lesson'].max_tokens = 2000
    changed = gateway.call(req())
    assert first.ok and changed.ok and not changed.cached
    assert calls == [1000, 2000]



def test_bad_json_consumes_durable_attempt_before_parse(tmp_path):
    dispatched = []
    def transport(**kw):
        rows = json.loads((tmp_path / 'execution.json').read_text())['attempts']
        dispatched.append(rows[-1]['state'])
        return '{invalid', {'total_tokens':77}
    gateway = make_gateway(tmp_path, transport)
    result = gateway.call(req())
    assert not result.ok and result.attempts == MAX_ATTEMPTS == 2
    assert dispatched == ['dispatched', 'dispatched']
    assert [r['state'] for r in gateway.control.snapshot()['attempts']] == ['protocol_failed'] * 2
    assert not (tmp_path / 'usage.jsonl').exists()


def test_timeout_and_bad_json_share_persistent_two_attempt_limit(tmp_path):
    seen = []
    def transport(**kw):
        seen.append(1)
        if len(seen) == 1: raise TimeoutError('offline timeout')
        return '{invalid', {'total_tokens': 'not-an-execution-input'}
    gateway = make_gateway(tmp_path, transport)
    result = gateway.call(req())
    assert not result.ok and result.attempts == 2
    restored = make_gateway(tmp_path, transport)
    with pytest.raises(AttemptLimitExceeded): restored.call(req())
    assert len(seen) == 2
    assert [r['state'] for r in restored.control.snapshot()['attempts']] == ['transport_failed', 'protocol_failed']


def test_cache_hit_does_not_dispatch_or_consume_another_attempt(tmp_path):
    seen = []
    def transport(**kw):
        seen.append(1)
        return '{"answer":1}', {'total_tokens':None}
    policy = ExecutionPolicy(attempt_limits={'transport':1})
    gateway = make_gateway(tmp_path, transport, policy=policy)
    assert gateway.call(req()).ok
    before = (tmp_path / 'execution.json').read_bytes()
    assert gateway.call(req()).cached
    restored = make_gateway(tmp_path, transport, policy=policy)
    assert restored.call(req()).cached
    assert seen == [1]
    assert (tmp_path / 'execution.json').read_bytes() == before


def test_cancel_before_request_never_dispatches_or_consumes_attempt(tmp_path):
    cancel = CancelToken(); cancel.cancel('offline stop')
    seen = []
    gateway = make_gateway(tmp_path, lambda **kw: seen.append(1), cancel=cancel)
    with pytest.raises(Cancelled): gateway.call(req())
    assert seen == [] and gateway.control.snapshot()['attempts'] == []


def test_cancellation_after_first_failure_stops_retry_and_survives_resume(tmp_path):
    cancel = CancelToken(); seen = []
    def first(**kw):
        seen.append(1); cancel.cancel('offline stop')
        raise TimeoutError('offline timeout')
    gateway = make_gateway(tmp_path, first, cancel=cancel)
    with pytest.raises(Cancelled): gateway.call(req())
    assert len(gateway.control.snapshot()['attempts']) == 1
    restored = make_gateway(tmp_path, lambda **kw: ('{"answer":2}', {}))
    result = restored.call(req())
    assert result.ok and result.attempts == 2
    assert [r['attempt'] for r in restored.control.snapshot()['attempts']] == [1,2]


def test_full_attempt_limit_cannot_be_reset_by_ignoring_usage_file(tmp_path):
    (tmp_path / 'usage.jsonl').write_text('{historical malformed data')
    def timeout(**kw): raise TimeoutError('offline')
    assert not make_gateway(tmp_path, timeout).call(req()).ok
    (tmp_path / 'usage.jsonl').unlink()
    restored = make_gateway(tmp_path, timeout)
    with pytest.raises(AttemptLimitExceeded): restored.call(req())
    assert len(restored.control.snapshot()['attempts']) == 2
