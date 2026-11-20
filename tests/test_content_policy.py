"""Production V4 policy cases and explicit rejection of historical V3 approval."""
import pytest
from aiforstudy.blocks.content import content_policy as policy
from aiforstudy.blocks.content.content_contracts import parse_content
from test_teaching_policy_v2 import teaching_case


def candidate():
    """Historical V3 reader fixture retained for read-only compatibility callers."""
    return {'_meta':{'schema_version':'3.0'},'book_id':'B','unit_id':'U','title':'约定',
        'body_markdown':r'设 $x\ne 0$。','annotations':[],'explainables':[],
        'examples':[],'concepts':[],'teaching_plan':{'claims':[],'steps':[]}}


def checked(c, source):
    return policy.check_content(c, source, policy.load_content_policy(source['learner_profile']))


def test_same_policy_for_all_roles_and_media_free_short_content():
    c, source=teaching_case();p=policy.load_content_policy(source['learner_profile'])
    for role in ('author','reviewer','repair'):
        prompt=policy.prompt_rules(p,role)
        for rule in p.rules: assert rule['rule_id'] in prompt and rule['severity'] in prompt
    assert not checked(c,source).blocking_issues
    assert not any('media' in r['rule_id'] or 'figure' in r['rule_id'] for r in p.rules)


def test_claim_longer_than_200_and_no_media_allowed():
    c,source=teaching_case()
    conclusion='n 可以取零。'+'非负整数的范围包括零且不含负数。'*16
    assert len(conclusion)>200
    c['body_markdown']=source['source_text']=conclusion
    c['teaching_plan']['claims'][0]['conclusion']=conclusion
    c['teaching_plan']['claims'][0]['source_refs'][0]['quote']=conclusion
    source['source_obligations'][0].update(conclusion=conclusion,source_excerpt=conclusion)
    source['source_obligations'][0]['source_refs'][0]['quote']=conclusion
    assert not checked(c,source).blocking_issues
    c['figure_requests']=[]
    assert any(r['rule_id']=='CONTENT-SCHEMA' for r in checked(c,source).blocking_issues)


def test_notation_explanation_has_explicit_structure_exception():
    c,source=teaching_case()
    c['explainables']=[{'id':'N','kind':'notation','name':'记号约定','what':'记号约定','why':'固定写法',
        'markdown':'n 表示非负整数','warmup':'','steps':[], 'not_applicable_reason':'纯记号，无推导步骤'}]
    assert not checked(c,source).blocking_issues


def test_introduced_definition_in_body_does_not_require_separate_explanation_card():
    c,source=teaching_case('definition')
    source['reference_catalog']['entries']=[dict(id='C',kind='CONCEPT',label='非负整数',
        version='1',unit_id='U',markdown=c['body_markdown'])]
    c['concepts']=[dict(id='C',role='introduced',explained_where='/body_markdown')]
    assert not c['explainables']
    assert not checked(c,source).blocking_issues
    items=policy.applicable_items(c,source,policy.load_content_policy())
    assert not any('source' in row['required_evidence_kinds'] for row in items)
    assert any(row['rule_id']=='PEDAGOGY-DEFINITION' for row in items)
    assert any(row['rule_id']=='CONCEPT-EXPLANATION-FIDELITY' for row in items)


def test_omitted_binding_keeps_source_review_without_mandatory_citation():
    c,source=teaching_case()
    c['teaching_plan']['claims']=[]
    c['teaching_plan']['goals']=[]
    c['teaching_evidence']=[]
    items=policy.applicable_items(c,source,policy.load_content_policy())
    fidelity=next(row for row in items if row['rule_id']=='SOURCE-FIDELITY')
    assert fidelity['required_evidence_kinds']==[]
    assert fidelity['required'] is True


def test_required_source_obligations_and_textbook_examples_cannot_disappear():
    c,source=teaching_case()
    c['teaching_plan']['claims']=[];c['teaching_plan']['goals']=[];c['teaching_evidence']=[]
    source['source_examples']=[{'id':'E-01','label':'例1','statement':'原题条件','book_solution':'原书解答','source_line':1}]
    report=checked(c,source)
    assert any(r['rule_id']=='CONTENT-COVERAGE' for r in report.warnings)
    assert any(r['issue_key']=='changed-source-example-E-01' for r in report.blocking_issues)


@pytest.mark.parametrize('damage',['v2','v3','claim-type','step-type'])
def test_content_schema_and_claim_step_object_types_are_strict(damage):
    c,source=teaching_case()
    if damage=='v2': c['_meta']['schema_version']='2.0'
    if damage=='v3': c=candidate()
    if damage=='claim-type': c['teaching_plan']['claims']=['wrong']
    if damage=='step-type': c['explainables']=[{'id':'X','kind':'procedure','name':'步骤','markdown':'说明','steps':['wrong']}]
    assert any(r['rule_id']=='CONTENT-SCHEMA' for r in checked(c,source).blocking_issues)


def test_historical_v3_is_readable_but_cannot_get_current_policy_pass():
    c=candidate();_,source=teaching_case()
    assert parse_content(c).as_dict()==c
    assert checked(c,source).decision=='needs_repair'


def test_prompts_use_current_policy_digest_and_style_rules():
    p=policy.load_content_policy()
    assert p.digest in policy.prompt_rules(p,'author')
    for word in policy.STYLE_WORDS:
        assert word in policy.prompt_rules(p,'author')


def test_present_source_chain_must_resolve_real_text_or_grounded_claim():
    c,source=teaching_case()
    c['examples']=[{'id':'X','kind':'derived_example','statement':'教学构造','approach':'用当前约定判断',
        'conclusion':'符合约定','source_refs':[{'kind':'textbook','quote':'不存在的教材条件'}],
        'steps':[{'id':'S','markdown':'推导','basis':'条件','basis_refs':['K']}]}]
    assert any(r['issue_key'].startswith('invalid-source:') for r in checked(c,source).blocking_issues)
    c['examples'][0]['source_refs']=[{'kind':'content','claim_id':'K'}]
    assert not checked(c,source).blocking_issues
