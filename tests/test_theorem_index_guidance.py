"""Existing content calls carry theorem reuse guidance; no live model is used."""
from copy import deepcopy
import json

import pytest

from aiforstudy.blocks.content.content_contracts import ExampleV4, ExplainableV4
from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.prompt import build_content_messages
from aiforstudy.blocks.content.review import review_content
from aiforstudy.blocks.content.source_context import FrozenReferenceCatalog
from aiforstudy.core.review_mode import review_mode
from support.content_case import teaching_case
from test_balanced_content_review import Client


@pytest.mark.parametrize('mode', ['balanced', 'full', 'json_only'])
@pytest.mark.parametrize('role', ['author', 'repair', 'reviewer'])
def test_actual_messages_carry_theorem_mainline_and_reuse_rules(mode, role):
    content, source = teaching_case()
    previous = [{'concept_id': 'C-PREVIOUS', 'unit_id': 'PREVIOUS',
                 'name': '非零复数的倒数',
                 'what': '若 z 是非零复数，则 z 存在唯一乘法逆元。',
                 'why': '非零条件保证除法有意义。', 'markdown': '此前已给出的完整证明。',
                 'steps': [], 'source_label': '性质 1', 'review_outcome': {'status': 'verified'}}]
    request = source if role == 'author' else {'source_context': source, 'content': content}
    request['available_theorem_names'] = [row['name'] for row in previous]
    before = deepcopy(request)
    with review_mode(mode):
        messages = build_content_messages(role, request, load_content_policy())
    payload = json.loads(messages[1]['content'])
    frozen = payload['frozen_context']
    assert frozen['available_theorem_names'] == [row['name'] for row in previous]
    assert request == before, '提示词组装不能改写已有完整解释或来源身份'
    system = messages[0]['content']
    for required in ('kind=theorem', 'concept_id', '完整条件与结论', '完整证明',
                     '单独一段', '[[EXPLAIN:本定理解释ID|定理名称]]',
                     '完整内嵌', '所有证明默认折叠', '点击展开',
                     '[[CONCEPT:C-ID|定理名称]]', '原地浮窗',
                     'available_theorem_names', '不得假称读过',
                     '同名或同编号', '描述性名称', '原编号',
                     '不按卡数、名称字数', '保留已有解释 ID'):
        assert required in system, (mode, role, required)
    assert '保留实际编号和适用条件' not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize('mode', ['balanced', 'full', 'json_only'])
@pytest.mark.parametrize('role', ['author', 'repair'])
def test_actual_author_and_repair_payloads_show_one_existing_theorem_object(mode, role):
    _, source = teaching_case()
    source['available_theorem_names'] = []
    with review_mode(mode):
        payload = json.loads(build_content_messages(role, source, load_content_policy())[1]['content'])
    example = payload['theorem_index_example']
    content = example['content']
    theorem = ExplainableV4.model_validate(content['explainables'][0])
    catalog = FrozenReferenceCatalog.model_validate(example['reference_catalog'])
    assert {entry.id for entry in catalog.entries} == {theorem.concept_id}
    assert catalog.entries[0].label == '性质 1（示例原编号）'
    ExampleV4.model_validate(content['examples'][0])
    assert theorem.kind == 'theorem'
    assert theorem.id == 'DEMO-INVERSE-THEOREM'
    assert theorem.concept_id == 'C-DEMO-COMPLEX-INVERSE'
    assert theorem.name == '非零复数的倒数'
    assert theorem.what == r'若 $z=x+iy$ 是非零复数（$x,y$ 为实数），则它有唯一乘法逆元 $z^{-1}=\frac{x-iy}{x^2+y^2}$。'
    assert '\n\n[[EXPLAIN:DEMO-INVERSE-THEOREM|非零复数的倒数]]\n\n' in content['body_markdown']
    assert '[[CONCEPT:C-DEMO-COMPLEX-INVERSE|非零复数的倒数]]' in content['body_markdown']
    assert '[[CONCEPT:C-DEMO-COMPLEX-INVERSE|非零复数的倒数]]' in content['examples'][0]['steps'][0]['basis']
    proof = '\n'.join(step.markdown + step.basis for step in theorem.steps)
    for mathematical_fact in (r'x^2+y^2>0', r'(x+iy)(x-iy)=x^2-ixy+ixy-i^2y^2=x^2+y^2', r'u=u(zv)=(uz)v=v'):
        assert mathematical_fact in proof
    assert theorem.what not in content['body_markdown']
    assert all(step.markdown not in content['body_markdown'] for step in theorem.steps)
    assert len(content['explainables']) == 1


@pytest.mark.parametrize('status', ['pass', 'fail', 'unknown'])
def test_theorem_review_uses_existing_criteria_call_and_verdict_authority(status):
    content, source = teaching_case()
    content['body_markdown'] = '[[EXPLAIN:THM|测试定理]]'
    content['explainables'] = [{'id': 'THM', 'kind': 'theorem', 'concept_id': 'C-TEST',
        'name': '测试定理', 'what': '当前条件与结论。', 'why': '当前用途。',
        'markdown': '当前证明，故意不提供模型语义真值。'}]
    reason = '离线固定判词：这里只验证既有路由，不代表真实定理审阅。'
    answer = {'verdicts': [{'status': status, 'reason': reason}]}
    client = Client([answer])
    with review_mode('balanced'):
        report = review_content(content, {}, source, (), None, client).as_dict()
    assert len(client.calls) == 1
    frozen = json.loads(client.calls[0][0][1]['content'])['frozen_context']
    assert frozen['candidate_content']['explainables'] == content['explainables']
    criteria = {row['criterion_id']: row['expected'] for row in frozen['criteria']}
    assert set(criteria) == {'CONTENT-KNOWLEDGE', 'CONTENT-STEPS', 'CONTENT-SELF-CHECKS'}
    assert '来源' in criteria['CONTENT-KNOWLEDGE'] and '定理名称' in criteria['CONTENT-KNOWLEDGE']
    assert '完整内嵌' in criteria['CONTENT-STEPS'] and '所有证明默认折叠' in criteria['CONTENT-STEPS']
    assert report['verdicts'] == answer['verdicts']
    assert (report['decision'] == 'accepted') is (status == 'pass')
