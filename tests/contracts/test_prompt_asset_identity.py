"""Offline contracts for production prompts and their transport identity."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import importlib
from importlib.resources import files
import json

import pytest

from aiforstudy.blocks.media.code_generation import media_code_prompt
from aiforstudy.core.digest import digest_of, digest_text
from aiforstudy.pipeline.gateway_client import GatewayClient
from aiforstudy.providers.gateway import CallResult
from test_media_code_contracts import approved_case


class RecordingGateway:
    def __init__(self): self.requests = []
    def call(self, request):
        self.requests.append(request)
        return CallResult(ok=True, payload={}, fingerprint=request.fingerprint(model='offline', effort=''))


def client(gateway):
    return GatewayClient(gateway, role='figures', model='offline', effort='', stage='figures',
                         unit_key='U', bucket='media')


@pytest.mark.parametrize('kind', ['drawing', 'animation'])
def test_generator_gets_frozen_quality_without_judge_output_protocol(kind):
    from aiforstudy.blocks.media.review_policy import media_review_policy
    from aiforstudy.blocks.media.validation import media_review_items
    content, approval, req, _ = approved_case(kind)
    first = media_code_prompt(req, content=content, approval=approval)
    repair = media_code_prompt(req, content=content, approval=approval, feedback={'problem':'cropped'})
    payload = json.loads(first[-1]['content'])
    assert 'quality_contract' in payload
    assert 'verdicts' not in json.dumps(payload['quality_contract'])
    assert 'prompt' not in payload['quality_contract']
    assert payload['quality_contract'] == json.loads(repair[-1]['content'])['quality_contract']
    assert payload['acceptance_criteria'] == req['acceptance_criteria']
    assert payload['output_json_schema']['properties']['schema_version']['const'] == 'GeneratedMediaCodeV1'
    actual = media_review_items(req, {'code_product':{'schema_version':'GeneratedMediaProductV1'}},
                                media_review_policy(kind))
    host = {item['criterion_id']:item for item in actual if item['criterion_id'].startswith('host.')}
    for criterion in payload['quality_contract']['criteria']:
        assert host[criterion['criterion_id']]['statement'] == criterion['statement']
        assert host[criterion['criterion_id']]['required_evidence_kinds'] == criterion['evidence_required']


def test_gateway_propagates_explicit_versions_instead_of_swallowing_kwargs():
    gateway = RecordingGateway()
    client(gateway).chat_json([{'role':'system','content':'offline'}],
                              prompt_version='sha256:prompt', rules_version='sha256:rules')
    assert gateway.requests[0].prompt_version == 'sha256:prompt'
    assert gateway.requests[0].rules_version == 'sha256:rules'


def test_gateway_versions_track_actual_messages_and_frozen_rules():
    content, approval, req, _ = approved_case()
    messages = media_code_prompt(req, content=content, approval=approval)
    gateway = RecordingGateway(); adapter = client(gateway)
    adapter.chat_json(messages)
    original = gateway.requests[-1]
    assert original.prompt_version == digest_of(messages)
    assert original.rules_version
    changed = deepcopy(messages); changed[0]['content'] += ' Changed instruction.'
    adapter.chat_json(changed)
    assert gateway.requests[-1].prompt_version != original.prompt_version
    assert gateway.requests[-1].rules_version == original.rules_version
    assert gateway.requests[-1].fingerprint(model='offline', effort='') != original.fingerprint(model='offline', effort='')


def test_packaged_prompt_loads_away_from_cwd_and_is_immutable(tmp_path, monkeypatch):
    assert files('aiforstudy').joinpath('core/prompt_assets.py').is_file()
    assets = importlib.import_module('aiforstudy.core.prompt_assets')
    monkeypatch.chdir(tmp_path)
    asset = assets.load_prompt_asset('media/code-static.md')
    assert asset.sha256 == digest_text(asset.text)
    with pytest.raises(FrozenInstanceError): asset.text = 'mutated'
    with pytest.raises(ValueError): assets.load_prompt_asset('../secrets.env')


def test_changed_asset_byte_reaches_real_gateway_request(monkeypatch):
    assert files('aiforstudy').joinpath('core/prompt_assets.py').is_file()
    assets = importlib.import_module('aiforstudy.core.prompt_assets')
    content, approval, req, _ = approved_case()
    gateway = RecordingGateway(); adapter = client(gateway)
    adapter.chat_json(media_code_prompt(req, content=content, approval=approval))
    first = gateway.requests[-1]
    original = assets.files
    class Resource:
        def __init__(self, resource): self.resource = resource
        def joinpath(self, *parts): return Resource(self.resource.joinpath(*parts))
        def read_text(self, **kwargs):
            text = self.resource.read_text(**kwargs)
            return text + ' ' if self.resource.name == 'code-static.md' else text
    monkeypatch.setattr(assets, 'files', lambda name: Resource(original(name)))
    adapter.chat_json(media_code_prompt(req, content=content, approval=approval))
    assert gateway.requests[-1].prompt_version != first.prompt_version
    assert gateway.requests[-1].fingerprint(model='offline', effort='') != first.fingerprint(model='offline', effort='')


@pytest.mark.parametrize('kind', ['drawing', 'animation'])
def test_code_examples_in_final_messages_pass_real_code_validator(kind):
    from aiforstudy.blocks.media.code_contracts import validate_media_code
    content, approval, req, _ = approved_case(kind)
    messages = media_code_prompt(req, content=content, approval=approval)
    example = json.loads(messages[-1]['content'])['example']['program']
    synthetic = {'request_id':'EXAMPLE-ONLY', 'kind':kind,
        'object_refs':[{'model_id':'EXAMPLE-M','object_id':'a'}],
        'animation':{'steps':['显示向量', '向左平移']}}
    assert validate_media_code(example, synthetic)['backend'] == ('matplotlib' if kind == 'drawing' else 'manim')


def test_author_prompt_provenance_reaches_production_gateway_after_repair_append():
    from aiforstudy.blocks.content.prompt import build_content_messages
    from aiforstudy.blocks.content.content_policy import load_content_policy
    from test_teaching_policy_v2 import teaching_case
    _, source = teaching_case()
    messages = build_content_messages('author', source, load_content_policy())
    gateway = RecordingGateway(); adapter = client(gateway)
    adapter.chat_json(messages)
    before = gateway.requests[-1]
    adapter.chat_json([*messages, {'role':'user','content':json.dumps({'role_task':'author_structure_repair','schema_errors':['missing field']})}])
    after = gateway.requests[-1]
    assert before.prompt_version == digest_of(messages)
    assert after.prompt_version != before.prompt_version
    assert after.rules_version == before.rules_version


def test_static_review_final_transport_examples_satisfy_real_verdict_validator(tmp_path):
    import re
    from aiforstudy.blocks.media.validation import validate_media
    from test_media_code_validation import code_case
    from test_media_review_prompt_contract import review
    class ReviewGateway(RecordingGateway):
        def call(self, request):
            result = super().call(request)
            payload = json.loads(request.messages[-1]['content'])
            result.payload = {'problems':[], 'verdicts':[
                {'criterion_id':item['criterion_id'], 'version':item['version'], 'status':'pass',
                 'why':'Offline transport fixture', 'problem':'', 'action':'', 'expected':'',
                 'evidence_ids':list(payload['evidence_catalog'])} for item in payload['review_items']]}
            return result
    req, product = code_case(tmp_path)
    gateway = ReviewGateway()
    report = validate_media(req, product, client=client(gateway))
    assert report['ok'], report['issues']
    actual = gateway.requests[-1]
    assert actual.prompt_version == digest_of(actual.messages)
    assert actual.rules_version
    system = actual.messages[0]['content']
    assert '字幕' not in system
    samples = [json.loads(raw) for raw in re.findall(r'```json\s*([\s\S]*?)\s*```', system)]
    assert len(samples) == 2
    assert review(samples[0])['ok']
    assert {issue['code'] for issue in review(samples[1])['issues']} == {'MEDIA_CRITERION_UNRESOLVED'}
