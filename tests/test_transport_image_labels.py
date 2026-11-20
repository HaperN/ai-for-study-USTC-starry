"""Image evidence labels reach the wire beside their matching image bytes."""
from copy import deepcopy
import base64
import json

import pytest

from aiforstudy.blocks.content.generation_corrections import run_local_generation
from aiforstudy.errors import ConfigError
from aiforstudy.pipeline.execution_control import ExecutionControl
from aiforstudy.pipeline.gateway_client import GatewayClient
from aiforstudy.providers.gateway import CallRequest, Gateway
from aiforstudy.providers.transport import RealTransport


@pytest.fixture
def images(tmp_path):
    from PIL import Image
    paths = [tmp_path / 'first.png', tmp_path / 'second.png']
    for path, color in zip(paths, ('red', 'blue')):
        Image.new('RGB', (1, 1), color).save(path)
    return [str(path) for path in paths]


def wire_transport():
    return RealTransport(model='offline', api_key='offline-placeholder')


def decoded_images(parts):
    return [base64.b64decode(part['image_url']['url'].split(',', 1)[1])
            for part in parts if part['type'] == 'image_url']


@pytest.mark.parametrize('content', ['review', [{'type': 'text', 'text': 'review'}]])
def test_unlabelled_calls_keep_existing_text_and_image_order(images, content):
    from pathlib import Path
    messages = [{'role': 'user', 'content': content}]
    before = deepcopy(messages)
    body = wire_transport().request_body(messages=messages, images=images)
    parts = body['messages'][0]['content']
    assert [part['type'] for part in parts] == ['text', 'image_url', 'image_url']
    assert parts[0] == {'type': 'text', 'text': 'review'}
    assert decoded_images(parts) == [Path(path).read_bytes() for path in images]
    assert messages == before


@pytest.mark.parametrize('content', ['review', [{'type': 'text', 'text': 'review'}]])
def test_each_label_immediately_precedes_its_image_without_mutating_messages(images, content):
    from pathlib import Path
    labels = ['S1；共轭初态；2.4 s', 'S2；分母实数化结果；8.1 s']
    messages = [{'role': 'system', 'content': 'judge'},
                {'role': 'user', 'content': content, 'image_labels': labels}]
    before = deepcopy(messages)
    transport = wire_transport()
    body = transport.request_body(messages=messages, images=images)
    parts = body['messages'][1]['content']
    assert [part['type'] for part in parts] == ['text', 'text', 'image_url', 'text', 'image_url']
    assert parts[1] == {'type': 'text', 'text': labels[0]}
    assert parts[3] == {'type': 'text', 'text': labels[1]}
    assert decoded_images(parts) == [Path(path).read_bytes() for path in images]
    assert all('image_labels' not in message for message in body['messages'])
    assert messages == before
    assert transport.calls == 0


@pytest.mark.parametrize('labels', [None, 'S1', ['only one'], ['S1', ''], ['S1', '  '], ['S1', 2]])
def test_invalid_label_batches_are_rejected_instead_of_misbinding_images(images, labels):
    with pytest.raises(ConfigError, match='image_labels'):
        wire_transport().request_body(messages=[{'role': 'user', 'content': 'review',
                                                 'image_labels': labels}], images=images)


def test_internal_empty_labels_are_removed_from_text_only_wire_messages():
    messages = [{'role': 'user', 'content': 'review', 'image_labels': []}]
    body = wire_transport().request_body(messages=messages)
    assert body['messages'] == [{'role': 'user', 'content': 'review'}]
    assert messages[0]['image_labels'] == []


def test_changed_image_labels_change_the_existing_gateway_fingerprint(images):
    first = CallRequest(role='reviewer', images=tuple(images),
        messages=[{'role': 'user', 'content': 'review', 'image_labels': ['S1 at 1 s', 'S2 at 2 s']}])
    second = deepcopy(first)
    second.messages[0]['image_labels'][0] = 'S1 at 3 s'
    assert first.fingerprint(model='offline', effort='high') != second.fingerprint(model='offline', effort='high')


def test_protocol_correction_keeps_labels_and_images_on_the_last_user_message(tmp_path, images):
    from pathlib import Path
    bodies = []
    replies = iter(['{"verdict":', '{"verdict":"pass"}'])
    transport = wire_transport()

    def local_reply(**request):
        bodies.append(transport.request_body(messages=request['messages'], images=request['images'],
                                              model=request['model'], effort=request['effort'],
                                              max_tokens=request['max_tokens']))
        return next(replies), {}

    control = ExecutionControl()
    control.bind(tmp_path / 'execution.json', run_id='label-correction')
    gateway = Gateway(transport=local_reply, roles={'reviewer': {'model': 'offline'}}, control=control)
    client = GatewayClient(gateway, role='reviewer', model='offline', effort='high',
                           stage='media', unit_key='U', bucket='media')

    class ImageClient:
        def chat_json(self, messages, **kwargs):
            return client.chat_json(messages, images=images, **kwargs)

    labels = ['S1；共轭初态；2.4 s', 'S2；分母实数化结果；8.1 s']
    messages = [{'role': 'user', 'content': 'review', 'image_labels': labels}]
    result = run_local_generation(messages, ImageClient(), lambda result: result,
        tag='media-review', correction_tag='media-review-format', max_corrections=1,
        correction_journal=tmp_path / 'corrections.json', correction_guard=False)

    assert result == {'verdict': 'pass'}
    assert len(bodies) == 2
    for body in bodies:
        parts = body['messages'][-1]['content']
        assert [part['type'] for part in parts] == ['text', 'text', 'image_url', 'text', 'image_url']
        assert parts[1] == {'type': 'text', 'text': labels[0]}
        assert parts[3] == {'type': 'text', 'text': labels[1]}
        assert decoded_images(parts) == [Path(path).read_bytes() for path in images]
        assert all('image_labels' not in message for message in body['messages'])
    assert bodies[1]['messages'][0] == {'role': 'user', 'content': 'review'}
    feedback = json.loads(bodies[1]['messages'][-1]['content'][0]['text'])
    assert feedback['role_task'] == 'local_generation_correction'
    assert feedback['errors'][0]['code'] == 'json_syntax'
    assert messages[0]['image_labels'] == labels
    assert transport.calls == 0
