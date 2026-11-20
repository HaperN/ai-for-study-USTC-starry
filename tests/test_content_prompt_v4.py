from copy import deepcopy
import importlib
import json
import pytest
from aiforstudy.errors import ContractError
from aiforstudy.blocks.content.content_contracts import LessonContentV4
from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.generation import generate_content
from test_teaching_policy_v2 import teaching_case
from support.content_case import authoring


class Transport:
    def __init__(self,data): self.data=data; self.calls=[]
    def chat_json(self,messages,**kwargs):
        self.calls.append(deepcopy(messages)); return {'data':authoring(deepcopy(self.data))}


def test_author_transport_receives_valid_v4_schema_example_and_frozen_policy():
    c,s=teaching_case(); client=Transport(c); p=load_content_policy()
    result=generate_content(s,p,client)['content']
    assert isinstance(LessonContentV4.model_validate(result),LessonContentV4)
    payload=json.loads(client.calls[0][1]['content'])
    LessonContentV4.model_validate(payload['output_schema_example'].get('content',payload['output_schema_example']))
    assert payload['output_json_schema']['$defs']['ContentMetaV4']['properties']['schema_version']['const']=='4.0'
    assert p.digest in client.calls[0][0]['content']
    assert 'LessonContentV4' in client.calls[0][0]['content']


@pytest.mark.parametrize('version',[None,'3.0','5.0'])
def test_author_does_not_launder_wrong_or_missing_schema_version(version):
    c,s=teaching_case(); c['_meta']={} if version is None else {'schema_version':version}
    seen=[]
    with pytest.raises(ContractError): generate_content(s,load_content_policy(),Transport(c),response_sink=seen.append)
    assert seen[0]==authoring(c)


def test_host_rejects_wrong_provenance_instead_of_overwriting():
    c,s=teaching_case(); c['original_sha256']='different'
    with pytest.raises(ContractError,match='original_sha256'): generate_content(s,load_content_policy(),Transport(c))


def test_all_roles_use_same_schema_policy_and_teaching_examples_in_actual_messages():
    prompt=importlib.import_module('aiforstudy.blocks.content.prompt')
    c,s=teaching_case(); p=load_content_policy()
    messages=[prompt.build_content_messages(role,s,p,tickets=()) for role in ('author','reviewer','repair')]
    payloads=[json.loads(m[1]['content']) for m in messages]
    assert all(x['policy_digest']==p.digest for x in payloads)
    author_schema=payloads[0]['output_json_schema']
    assert author_schema['properties']['content']
    content_schema=author_schema['$defs']['LessonContentV4']
    for other in payloads[1:]:
        schema=other['content_json_schema']
        assert {key:value for key,value in schema.items() if key!='$defs'}==content_schema
        for name,definition in schema['$defs'].items():
            assert author_schema['$defs'][name]==definition
    assert {x['kind'] for x in payloads[0]['teaching_examples']}=={'definition','theorem','procedure','example','convention'}
    for payload in payloads:
        example=payload.get('output_schema_example',payload.get('content_schema_example'))
        LessonContentV4.model_validate(example.get('content',example))
    assert all('线性代数' not in json.dumps(m,ensure_ascii=False) for m in messages)


def test_repair_receives_full_content_contract_and_reviewer_only_verdicts():
    from aiforstudy.blocks.content.prompt import build_content_messages
    _, source=teaching_case(); policy=load_content_policy()
    patch={'schema_version':'ContentPatchV1','base_content_hash':'sha256:frozen','operations':[]}
    repair=json.loads(build_content_messages('repair',{'source_context':source,'patch_contract':patch},policy)[-1]['content'])
    review=json.loads(build_content_messages('reviewer',source,policy)[-1]['content'])
    assert repair['output_contract']['schema_version']=='ContentAuthoringV1'
    assert '完整' in repair['output_contract']['instruction']
    assert 'output_json_schema' not in repair and 'output_schema_example' not in repair
    assert 'output_json_schema' not in review and 'output_schema_example' not in review
    assert 'content_json_schema' in repair and 'content_json_schema' in review


def test_math_contract_and_example_reach_author_transport_and_parse():
    from aiforstudy.blocks.math.contracts import parse_math_model
    c,s=teaching_case(); client=Transport(c)
    generate_content(s,load_content_policy(),client)
    payload=json.loads(client.calls[0][1]['content'])
    assert payload['math_contract']
    assert parse_math_model(payload['math_schema_example']).model_id


def test_author_preserves_wrong_source_example_for_policy_instead_of_rewriting_it():
    from aiforstudy.blocks.content.content_policy import check_content
    c,s=teaching_case()
    original={'id':'E','label':'例1','statement':s['source_text'],'book_solution':'','source_line':1}
    s['source_examples']=[original]
    c['examples']=[{'id':'E','kind':'source','label':'例1','statement':'被作者篡改的条件','book_solution':'','source_line':1,
        'source_refs':[{'kind':'textbook','example_id':'E'}], 'approach':'读取约定','steps':[{'id':'S','markdown':'按约定判断','basis':'采用定义','basis_refs':['K']}],'conclusion':'可取零'}]
    result=generate_content(s,load_content_policy(),Transport(c))['content']
    assert result['examples'][0]['statement']=='被作者篡改的条件'
    assert any(x['issue_key']=='changed-source-example-E' for x in check_content(result,s,load_content_policy()).blocking_issues)
