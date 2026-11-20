"""Account transport preserves message/image evidence and fails closed locally."""
import base64
from copy import deepcopy
import importlib

import pytest
import offline_guard

from aiforstudy.errors import ConfigError


def transport(**kwargs):
    module = importlib.import_module('aiforstudy.providers.dsh_account')
    return module.DshAccountTransport(model='deepseek-flash', **kwargs)


def test_request_keeps_roles_order_and_real_image_bytes(tmp_path):
    image = tmp_path / 'sample.png'
    image.write_bytes(b'controlled-image-bytes')
    messages = [{'role': 'system', 'content': 'Return JSON.'},
                {'role': 'assistant', 'content': '{"prior":true}'},
                {'role': 'user', 'content': 'inspect', 'image_labels': ['frame 1']}]
    before = deepcopy(messages)
    client = transport()
    request = client.request_body(messages=messages, images=[str(image)],
                                  model='deepseek-flash', effort='max', max_tokens=256)
    assert [m['role'] for m in request['messages']] == ['system', 'assistant', 'user']
    parts = request['messages'][-1]['content']
    assert parts[:2] == [{'type': 'text', 'text': 'inspect'}, {'type': 'text', 'text': 'frame 1'}]
    assert base64.b64decode(parts[2]['image_url']['url'].split(',', 1)[1]) == b'controlled-image-bytes'
    assert request['reasoning_effort'] == 'max'
    assert request['max_tokens'] == 256
    assert 'response_format' not in request
    assert messages == before
    assert client.calls == 0


@pytest.mark.parametrize('message', [
    {'role': 'tool', 'content': 'tool output'},
    {'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'https://example.org/a.png'}}]},
    {'role': 'user', 'content': [{'type': 'audio', 'data': 'abc'}]},
    {'role': 'assistant', 'content': '', 'tool_calls': [{'name': 'execute'}]},
])
def test_unsupported_inputs_are_refused_before_credentials_or_network(message):
    with pytest.raises(ConfigError):
        transport().request_body(messages=[message])


def test_missing_images_and_label_mismatch_do_not_degrade_to_text(tmp_path):
    client = transport()
    with pytest.raises(ConfigError):
        client.request_body(messages=[{'role': 'user', 'content': 'inspect'}],
                            images=[str(tmp_path / 'missing.png')])
    image = tmp_path / 'sample.png'
    image.write_bytes(b'image')
    with pytest.raises(ConfigError, match='image_labels'):
        client.request_body(messages=[{'role': 'user', 'content': 'inspect', 'image_labels': []}],
                            images=[str(image)])


def test_error_mapping_does_not_expose_provider_messages():
    module = importlib.import_module('aiforstudy.providers.dsh_account')
    with pytest.raises(ConfigError) as caught:
        module.raise_bridge_error({'code': 'ACCOUNT_SIGN_IN_REQUIRED', 'message': 'secret-fixture'})
    assert 'ACCOUNT_SIGN_IN_REQUIRED' in str(caught.value)
    assert 'secret-fixture' not in str(caught.value)


def test_usage_normalization_uses_supplied_provider_counters_only():
    module = importlib.import_module('aiforstudy.providers.dsh_account')
    assert module.normalize_usage({'inputTokens': 23, 'outputTokens': 7,
                                   'cacheReadTokens': 3}) == {
        'prompt_tokens': 26, 'completion_tokens': 7, 'total_tokens': 33,
        'prompt_cache_hit_tokens': 3, 'prompt_cache_miss_tokens': 23,
    }
    assert module.normalize_usage({}) == {}


def test_usage_cache_write_is_additional_input_not_double_counted():
    from aiforstudy.providers.dsh_account import normalize_usage
    result=normalize_usage({'inputTokens':10,'cacheReadTokens':3,'cacheWriteTokens':5,'outputTokens':2,'totalTokens':999})
    assert result['prompt_tokens']==18 and result['total_tokens']==20
    assert result['prompt_cache_write_tokens']==5
    assert normalize_usage({'inputTokens':True,'outputTokens':-2})=={}


def test_account_pre_cancel_never_starts_bridge(monkeypatch):
    from aiforstudy.pipeline.services import CancelToken
    from aiforstudy.errors import Cancelled
    token=CancelToken();token.cancel()
    client=transport(cancel=token)
    monkeypatch.setattr('subprocess.Popen',lambda *a,**k:pytest.fail('cancelled request started process'))
    with pytest.raises(Cancelled):
        client(model='deepseek-flash',effort='high',messages=[],images=None,max_tokens=100,timeout=1)
    assert client.calls==0


@pytest.mark.skipif(offline_guard.is_installed(), reason='Installed Harness SDK launch is disabled in offline tests')
def test_installed_sdk_initializes_before_reading_missing_account(tmp_path):
    from pathlib import Path
    if not Path('/Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness').is_file():
        pytest.skip('installed Harness SDK required for composition contract')
    client=transport(account_home=tmp_path)
    with pytest.raises(ConfigError,match='ACCOUNT_SIGN_IN_REQUIRED'):
        client(model='deepseek-flash',effort='high',messages=[{'role':'user','content':'x'}],images=None,max_tokens=100,timeout=5)
    assert client.calls==0


@pytest.mark.skipif(offline_guard.is_installed(), reason='Installed Harness SDK launch is disabled in offline tests')
def test_installed_sdk_invalid_image_precedes_missing_account(tmp_path):
    from pathlib import Path
    if not Path('/Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness').is_file():
        pytest.skip('installed Harness SDK required for image admission contract')
    image=tmp_path/'invalid.png';image.write_bytes(b'not a PNG')
    client=transport(account_home=tmp_path)
    with pytest.raises(ConfigError) as caught:
        client(model='deepseek-flash',effort='high',messages=[{'role':'user','content':'x'}],images=[str(image)],max_tokens=100,timeout=5)
    assert 'ACCOUNT_SIGN_IN_REQUIRED' not in str(caught.value)
    assert 'NO_ADAPTER' not in str(caught.value)
    assert client.calls==0
