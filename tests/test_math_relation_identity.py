"""A teaching claim may need several independent, non-overwriting CAS checks."""
from copy import deepcopy

import pytest

from aiforstudy.blocks.math.contracts import parse_math_model
from aiforstudy.blocks.math.verify import verify_math
from aiforstudy.errors import ContractError


def model_data(*, unknown=False):
    claims = [dict(claim_id='K', kind='identity', math_object_id='one',
                   lhs_object_id='one', rhs_object_id=right) for right in ('one', 'two')]
    if unknown:
        claims = [dict(claim_id='K', kind='abstract', math_object_id=name,
                       reason='This relation requires source-backed reasoning.') for name in ('one', 'two')]
    return dict(schema_version='1.0', model_id='M', symbols=[], conditions=[], source_refs=['SO'],
        objects=[dict(math_object_id=name, kind='scalar', expr={'op':'const','value':value})
                 for name,value in [('one',1),('two',2)]], claims=claims)


def test_same_claim_relations_have_stable_distinct_identity_without_changing_math():
    raw = model_data(); model = parse_math_model(raw)
    assert len(model.claims) == 2
    assert len({row['relation_id'] for row in model.claims}) == 2
    stripped = [{k:v for k,v in row.items() if k != 'relation_id'} for row in model.claims]
    assert stripped == [{**row, 'conditions':[]} for row in raw['claims']]
    reverse = deepcopy(raw); reverse['claims'].reverse()
    assert list(reversed(parse_math_model(reverse).claims)) == list(model.claims)
    assert parse_math_model(model.as_dict()).digest == model.digest


def test_claim_subset_checks_both_relations_and_cannot_hide_the_false_one():
    model = parse_math_model(model_data())
    report = verify_math(model, ['K'])
    assert len(report.checks) == 2 and report.status == 'fail'
    assert {row.status for row in report.checks} == {'pass','fail'}
    assert len({row.relation_id for row in report.checks}) == 2
    with pytest.raises(ContractError, match='ambiguous|multiple'):
        model.claim_by_id('K')
    with pytest.raises(ContractError, match='ambiguous|multiple'):
        report.by_claim('K')
    for relation in model.claims:
        chosen = verify_math(model, [{'claim_id':'K', 'relation_id':relation['relation_id']}])
        assert len(chosen.checks) == 1
        assert chosen.checks[0].relation_id == relation['relation_id']
    with pytest.raises(ContractError):
        verify_math(model, [{'claim_id':'OTHER', 'relation_id':model.claims[0]['relation_id']}])
    with pytest.raises(ContractError):
        verify_math(model, ['K', {'relation_id':model.claims[0]['relation_id']}])


@pytest.mark.parametrize('damage', ['same_relation_id','duplicate_implicit_relation','forged_id','explicit_null'])
def test_relation_identity_conflicts_and_malformed_ids_are_rejected(damage):
    raw = model_data()
    if damage == 'same_relation_id':
        for row in raw['claims']: row['relation_id'] = 'R-SAME'
    elif damage == 'duplicate_implicit_relation': raw['claims'][1] = deepcopy(raw['claims'][0])
    elif damage == 'explicit_null': raw['claims'][0]['relation_id'] = None
    else: raw['claims'][0]['relation_id'] = '../other'
    with pytest.raises(ContractError): parse_math_model(raw)


def test_old_single_relation_model_is_readable_but_verifier_reports_new_identity():
    raw=model_data();raw['claims'].pop()
    model=parse_math_model(raw); report=verify_math(model)
    assert model.claim_by_id('K')['relation_id']
    assert report.by_claim('K').status == 'pass'
    assert report.as_dict()['verifier_version'] == '2.0'
    assert report.as_dict()['schema_version'] == '2.0'


def test_two_unknown_relations_get_distinct_review_items_and_evidence():
    from support.content_case import approved_inputs
    from aiforstudy.blocks.content.content_contracts import parse_content
    from aiforstudy.blocks.content.math_checks import check_content_math, math_review_items
    from aiforstudy.blocks.content.review import build_evidence_catalog
    from aiforstudy.blocks.content.review_document import build_review_document
    c,s,p,_,_,_=approved_inputs()
    c['math_models']=[model_data(unknown=True)]
    c['teaching_plan']['claims'][0]['math_model_refs']=['M']
    c=parse_content(c).as_dict()
    report=check_content_math(c).as_dict()
    items=math_review_items(c,p,report)
    assert len(items)==len({row['criterion_id'] for row in items})==2
    assert {row['subject_id'] for row in items}=={'K'}
    evidence=build_evidence_catalog(c,build_review_document(c,s['reference_catalog']),s,report)
    assert len([key for key in evidence if key.startswith('math:')])==2
    from aiforstudy.blocks.content.content_approval import _math_binding
    _math_binding(c,report)
    broken=deepcopy(report);broken['checks'].pop()
    with pytest.raises(ContractError): _math_binding(c,broken)
    broken=deepcopy(report);broken['schema_version']='ContentMathReportV1'
    with pytest.raises(ContractError): _math_binding(c,broken)


def relation_approval_inputs():
    """Explicit semantic verdicts for this artificial unknown-math fixture only."""
    from support.content_case import approved_inputs
    from aiforstudy.blocks.content.content_contracts import parse_content
    from aiforstudy.blocks.content.content_policy import check_content, applicable_items
    from aiforstudy.blocks.content.math_checks import check_content_math, math_review_items
    from aiforstudy.blocks.content.review import build_evidence_catalog, validate_verdicts
    from aiforstudy.blocks.content.review_document import build_review_document
    c,s,p,_,_,_=approved_inputs()
    c['math_models']=[model_data(unknown=True)]
    c['teaching_plan']['claims'][0]['math_model_refs']=['M']
    c=parse_content(c).as_dict()
    doc=build_review_document(c,s['reference_catalog'])
    math=check_content_math(c).as_dict()
    items=applicable_items(c,s,p)+math_review_items(c,p,math)
    evidence=build_evidence_catalog(c,doc,s,math)
    verdicts=[]
    for item in items:
        refs=[]
        for kind in item['required_evidence_kinds']:
            eid,row=next((eid,row) for eid,row in evidence.items()
                         if row['kind']==kind and item['subject_id'] in row['subject_ids'])
            refs.append({'evidence_id':eid,'quote':row['text']})
        verdicts.append(dict(criterion_id=item['criterion_id'],version=item['version'],
            status='pass',reason='Explicit offline fixture verdict; both relation criteria are present.',evidence_refs=refs))
    review=validate_verdicts(items,{'verdicts':verdicts},evidence).as_dict()
    review.update(review_items=list(items),evidence_catalog=evidence)
    return [c,s,p,doc,[check_content(c,s,p),review],math]


def test_approval_requires_both_unknown_relation_reviews_and_current_report():
    from support.content_case import issue
    inputs=relation_approval_inputs()
    assert issue(inputs).decision=='accepted'
    math_verdicts=[row for row in inputs[4][1]['verdicts'] if row['criterion_id'].startswith('MATH-SEMANTIC:')]
    assert len(math_verdicts)==2
    for damage in ('missing_review','unknown_review','old_report','missing_relation','wrong_claim'):
        changed=list(inputs)
        changed[4]=[inputs[4][0],deepcopy(inputs[4][1])]
        changed[-1]=deepcopy(inputs[-1])
        review=changed[4][1]
        if damage=='missing_review': review['verdicts'].remove(next(row for row in review['verdicts'] if row['criterion_id']==math_verdicts[-1]['criterion_id']))
        if damage=='unknown_review': next(row for row in review['verdicts'] if row['criterion_id']==math_verdicts[-1]['criterion_id'])['status']='unknown'
        if damage=='old_report': changed[-1]['schema_version']='ContentMathReportV1'
        if damage=='missing_relation': changed[-1]['checks'].pop()
        if damage=='wrong_claim': changed[-1]['checks'][1]['claim_id']='OTHER'
        with pytest.raises(ContractError): issue(changed)


def test_actual_media_entry_rejects_approval_under_previous_relation_policy():
    from support.content_case import approved_inputs, issue, Planner, author_media
    from aiforstudy.blocks.content.content_policy import ContentPolicyV2
    from aiforstudy.blocks.media.media_planning import plan_media_requirements
    c,s,p,*_=approved_inputs()
    receipt=issue(approved_inputs()).as_dict()
    old=p.as_dict()
    next(rule for rule in old['rules'] if rule['rule_id']=='CONTENT-REFERENCES')['version']=2
    receipt['policy_digest']=ContentPolicyV2(old).digest
    client=Planner()
    with pytest.raises(ContractError,match='policy_digest'):
        plan_media_requirements(c,receipt,capabilities={'kinds':[]},client=client,author_requirements=author_media(c))
    assert client.calls==client.intent_reviewer.calls==0


def media_context(*, kind, both_true=False):
    """Bind unverified content identity: the real compiler must prove the math."""
    from test_media_decisions_v2 import math_request_case, Planner
    from support.content_case import author_media
    from aiforstudy.blocks.media.media_planning import plan_media_requirements
    from aiforstudy.blocks.media.math_context import ApprovedMathContext
    from aiforstudy.blocks.content.content_contracts import parse_content
    from aiforstudy.blocks.content.review_outcome import content_identity
    c,a,raw,caps=math_request_case(kind)
    data=model_data()
    if both_true: data['objects'][1]['expr']['value']=1
    model=parse_math_model(data)
    c['math_models']=[model.as_dict()]
    c=parse_content(c).as_dict()
    from support.content_case import approved_inputs
    inputs=approved_inputs()
    identity=content_identity(c,inputs[1]['reference_catalog'],policy_digest=inputs[2].digest)
    req=raw['requests'][0]
    req['math_model_refs']=[{'model_id':'M','digest':model.digest}]
    req['object_refs']=[{'model_id':'M','object_id':name} for name in ('one','two')]
    plan=plan_media_requirements(c,identity,capabilities=caps,client=Planner(raw),author_requirements=author_media(c,raw))
    return ApprovedMathContext.from_content_binding(c,identity,plan['requests'][0])


def test_derivation_diagram_renders_every_relation_and_rejects_any_false_one():
    from aiforstudy.blocks.media.diagram import compile_diagram
    spec={'schema_version':'MediaDiagramV2','construction':{'kind':'derivation','model_id':'M','claim_ids':['K']},'layout':{}}
    with pytest.raises(ContractError,match='not proven'): compile_diagram(spec,media_context(kind='diagram'))
    product=compile_diagram(spec,media_context(kind='diagram',both_true=True))
    assert len(product['data']['steps'])==len(product['relations'])==2
    assert len({row['relation_id'] for row in product['data']['steps']})==2


def test_animation_claim_selects_all_relations_but_explicit_relation_is_exact():
    from test_visual_spec_v2 import visual_spec
    from aiforstudy.blocks.visual.compiler import compile_scene
    from aiforstudy.blocks.visual.manim_adapter import load_trusted_scene
    ctx=media_context(kind='animation')
    spec=visual_spec()
    spec.update(request_id=ctx.request['request_id'],unit_id='U',goal_ids=['G'],claim_ids=['K'],math_model_refs=ctx.request['math_model_refs'])
    spec['objects']=[{'object_id':name,'kind':'formula','model_id':'M','math_object_id':name} for name in ('one','two')]
    step=spec['steps'][0]
    step.update(goal_id='G',claim_ids=['K'],caption_claim_ids=['K'],object_ids=['one','two'],
        math_claim_refs=[{'model_id':'M','claim_id':'K'}],actions=[{'action':'reveal','object_id':name} for name in ('one','two')])
    with pytest.raises(ContractError,match='数学检查 fail'): compile_scene(spec,'draft',math_context=ctx)
    selected=ctx.model_by_id('M').claims[0]['relation_id']
    step['math_claim_refs'][0]['relation_id']=selected
    spec['objects'].pop();step.update(object_ids=['one'],actions=[{'action':'reveal','object_id':'one'}])
    scene=compile_scene(spec,'draft',math_context=ctx)
    report=load_trusted_scene(scene.source)['math_binding']['reports'][0]
    assert len(report['checks'])==1 and report['checks'][0]['relation_id']==selected
    step['math_claim_refs'][0]['claim_id']='FORGED'
    with pytest.raises(ContractError,match='claim_id'): compile_scene(spec,'draft',math_context=ctx)
