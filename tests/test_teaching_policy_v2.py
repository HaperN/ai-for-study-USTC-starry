from copy import deepcopy
import pytest
from aiforstudy.blocks.content import content_policy as policy_module
from aiforstudy.blocks.content.source_context import LearnerProfileV1


from support.content_case import teaching_case


def checked(c=None, s=None):
    original_c, original_s = teaching_case()
    return policy_module.check_content(c or original_c, s or original_s, policy_module.load_content_policy(LearnerProfileV1()))


def test_short_complete_convention_has_no_local_hard_issue_and_no_fake_review_pass():
    report = checked()
    assert not report.blocking_issues
    assert report.warnings, 'short content signals an editorial warning, not a hard floor'
    assert all(x['status'] in {'pass','fail','unknown','not_run','not_applicable'} for x in report.rule_results)
    assert all(x['status'] in {'not_run','not_applicable'} and not x['executed'] for x in report.rule_results if x['method']=='reviewer')
    theorem=next(x for x in report.rule_results if x['rule_id']=='PEDAGOGY-THEOREM')
    assert theorem['status']=='not_applicable'


def test_missing_checker_never_emits_pass(monkeypatch):
    assert hasattr(policy_module, 'CHECKERS'), 'no executable checker registry'
    monkeypatch.delitem(policy_module.CHECKERS,'CONTENT-COVERAGE')
    report=checked()
    receipt=next(x for x in report.rule_results if x['rule_id']=='CONTENT-COVERAGE')
    assert receipt['status']=='not_run' and receipt['executed'] is False
    assert any(x['rule_id']=='CONTENT-COVERAGE' for x in report.blocking_issues)


def test_omitted_claim_cannot_erase_independent_source_obligation():
    c,s=teaching_case(); c['teaching_plan']['claims']=[]; c['teaching_plan']['goals']=[]; c['teaching_evidence']=[]
    report=checked(c,s)
    assert any(x['rule_id']=='CONTENT-COVERAGE' and 'SO' in x['problem'] for x in report.warnings)
    items=policy_module.applicable_items(c,s,policy_module.load_content_policy())
    assert any(x['subject_id']=='SO' for x in items)


@pytest.mark.parametrize('mutation', ['condition','profile','recap','basis','structure'])
def test_real_coverage_prerequisite_and_reference_breaks_are_hard(mutation):
    c,s=teaching_case()
    if mutation=='condition': c['teaching_plan']['claims'][0]['conditions']=[]
    if mutation=='profile': c['teaching_plan']['prerequisites']=[{'id':'P','dependency_id':'unknown','treatment':'assumed'}]
    if mutation=='recap': c['teaching_plan']['prerequisites']=[{'id':'P','dependency_id':'unknown','treatment':'recap','recap_id':'absent'}]
    if mutation=='basis': c['explainables']=[{'id':'X','kind':'procedure','name':'步骤','markdown':'求结果','steps':[{'id':'S','markdown':'得到结果','basis':'根据定义','basis_refs':['missing']}]}]
    if mutation=='structure': s['obligation_extraction_status']='structural_only'
    assert checked(c,s).blocking_issues


def test_all_five_teaching_kinds_get_specific_applicable_rules():
    policy=policy_module.load_content_policy()
    for kind in ('definition','theorem','procedure','example','convention'):
        c,s=teaching_case(kind)
        items=policy_module.applicable_items(c,s,policy)
        assert any(x['subject_id']=='K' and x['rule_id']=='PEDAGOGY-'+kind.upper() for x in items)
        assert len({x['criterion_id'] for x in items})==len(items)
    c,s=teaching_case()
    assert not any(x['rule_id']=='PEDAGOGY-THEOREM' for x in policy_module.applicable_items(c,s,policy))


def test_all_declared_local_rules_have_real_executors_and_style_is_soft():
    p=policy_module.load_content_policy(LearnerProfileV1(discipline='历史'))
    assert p.schema_version=='ContentPolicyV2'
    assert all(callable(policy_module.CHECKERS.get(r['rule_id'])) for r in p.rules if r['check_method']=='code')
    c,s=teaching_case(); c['body_markdown']='值得注意的是，'+('泛泛而谈，'*160)+'。'
    r=checked(c,s)
    assert {'STYLE-TEMPLATE','STYLE-SENTENCE'} <= {x['rule_id'] for x in r.warnings}
    assert not any(x['rule_id'].startswith('STYLE-') for x in r.blocking_issues)
    assert any(x['rule_id']=='PEDAGOGY-CONVENTION' and x['status']=='not_run' for x in r.rule_results)


def test_policy_freezes_profile_and_profiles_change_digest():
    profile=LearnerProfileV1(assumed_prerequisite_ids=['integer'])
    p=policy_module.load_content_policy(profile)
    profile.assumed_prerequisite_ids.append('calculus')
    assert p.learner_profile['assumed_prerequisite_ids']==['integer']
    assert p.digest!=policy_module.load_content_policy(profile).digest


def test_math_model_ids_and_string_source_refs_use_actual_math_contract():
    c,s=teaching_case()
    c['math_models']=[{'schema_version':'1.0','model_id':'M','symbols':[],
        'objects':[{'math_object_id':'X','kind':'scalar','expr':{'op':'const','value':0}}],
        'conditions':[], 'claims':[{'claim_id':'K','kind':'abstract','math_object_id':'X'}], 'source_refs':['SO']}]
    c['teaching_plan']['claims'][0]['math_model_refs']=['M']
    report=checked(c,s)
    assert not report.blocking_issues
    c['math_models'][0]['source_refs']=['made-up']
    assert any(i['rule_id']=='CONTENT-REFERENCES' for i in checked(c,s).blocking_issues)


@pytest.mark.parametrize('model_claim_ids', [[], ['K']])
def test_declared_math_model_reference_requires_same_teaching_claim_in_model(model_claim_ids):
    c,s=teaching_case()
    c['math_models']=[{'schema_version':'1.0','model_id':'M','symbols':[],
        'objects':[{'math_object_id':'X','kind':'scalar','expr':{'op':'const','value':0}}],
        'conditions':[], 'claims':[{'claim_id':cid,'kind':'identity','math_object_id':'X','lhs_object_id':'X','rhs_object_id':'X'} for cid in model_claim_ids],
        'source_refs':['SO']}]
    c['teaching_plan']['claims'][0]['math_model_refs']=['M']
    derived=deepcopy(c['teaching_plan']['claims'][0]); derived.update(id='D',source_refs=[{'kind':'derived','claim_id':'K'}])
    c['teaching_plan']['claims'].append(derived)
    report=checked(c,s)
    assert any(x['rule_id']=='CONTENT-REFERENCES' and x['owner_id']=='D' and x['issue_key']=='missing-math-claim-D-M' for x in report.blocking_issues)
    # Adding exactly the missing mathematical relationships closes this gate.
    c['math_models'][0]['claims']=[{'claim_id':cid,'kind':'identity','math_object_id':'X','lhs_object_id':'X','rhs_object_id':'X'} for cid in ('K','D')]
    assert not checked(c,s).blocking_issues


def test_omitted_models_still_require_source_backed_representation_review():
    c,s=teaching_case(); p=policy_module.load_content_policy()
    items=policy_module.applicable_items(c,s,p)
    representation=[x for x in items if x['rule_id']=='MATH-REPRESENTATION']
    assert len(representation)==1
    item=representation[0]
    assert item['subject_id']=='unit:U' and item['required'] is True
    assert item['required_evidence_kinds']==[]
    receipt=next(x for x in checked(c,s).rule_results if x['rule_id']=='MATH-REPRESENTATION')
    assert receipt['status']=='not_run' and not receipt['executed']
    assert not checked(c,s).blocking_issues, 'abstract/noncomputable content is evaluated semantically, not forced into AST'
    assert 'math_models' in item['expected'] and 'CAS' in item['expected']
    assert '来源' in item['expected'] and '抽象' in item['expected']


def test_new_here_and_previous_step_basis_have_real_resolution():
    c,s=teaching_case()
    c['teaching_plan']['prerequisites']=[{'id':'P','dependency_id':'K','treatment':'new_here'}]
    c['explainables']=[{'id':'X','kind':'procedure','name':'判别','markdown':'逐步判别','steps':[
        {'id':'S1','markdown':'读取 n 的约定','basis':'本节约定允许 n=0','basis_refs':['K']},
        {'id':'S2','markdown':'代入零符合约定','basis':'前一步已确认取值范围','basis_refs':['S1']}]}]
    assert not checked(c,s).blocking_issues
    c['explainables'][0]['steps'][0]['basis_refs']=['X']
    assert any(i['rule_id']=='CONTENT-BASIS' for i in checked(c,s).blocking_issues)


def test_editorial_measures_do_not_approve_empty_teaching_or_weakened_analogy():
    from aiforstudy.blocks.content.review import validate_verdicts,build_evidence_catalog
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.blocks.content.source_context import FrozenReferenceCatalog
    from aiforstudy.blocks.content.content_contracts import parse_content
    c,s=teaching_case('theorem')
    c['body_markdown']='就像随手挑一个数一样，n 也可以是负数。'+'这能帮助我们理解内容。'*80
    p=policy_module.load_content_policy()
    local=policy_module.check_content(c,s,p)
    assert not local.blocking_issues, 'code cannot decide the semantic claim from word count'
    criterion=next(x for x in policy_module.applicable_items(c,s,p) if x['rule_id']=='SOURCE-FIDELITY')
    doc=project_reader(parse_content(c),FrozenReferenceCatalog())
    evidence=build_evidence_catalog(c,doc,s)
    reader=next(k for k,v in evidence.items() if v.get('json_pointer')=='/body_markdown')
    verdict={'criterion_id':criterion['criterion_id'],'version':criterion['version'],'status':'fail',
        'reason':'类比把冻结的非负条件改为允许负数。应明确 n≥0；负数只能作为反例。',
        'evidence_refs':[{'evidence_id':reader,'quote':'n 也可以是负数'},{'evidence_id':'source:SO','quote':'非负整数'}]}
    result=validate_verdicts([criterion],{'verdicts':[verdict]},evidence)
    assert result.decision=='needs_review' and result.blocking_issues


def test_mathematical_claim_must_reference_teaching_claim_identity():
    c,s=teaching_case()
    c['math_models']=[{'schema_version':'1.0','model_id':'M','symbols':[],
        'objects':[{'math_object_id':'X','kind':'scalar','expr':{'op':'const','value':0}}],
        'conditions':[], 'claims':[{'claim_id':'not-a-teaching-claim','kind':'abstract','math_object_id':'X'}], 'source_refs':['SO']}]
    assert any(x['rule_id']=='CONTENT-REFERENCES' for x in checked(c,s).blocking_issues)


def test_current_prompt_does_not_append_retired_hard_word_floor():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    assert not (root/'lib/style_rules.py').exists()
    assert '正文**至少**' not in policy_module.prompt_rules(policy_module.load_content_policy(), 'author')
    assert policy_module.prompt_rules(policy_module.load_content_policy(), 'author')


def test_learning_check_checker_is_part_of_production_policy():
    c,s=teaching_case()
    c['teaching_evidence']=[e for e in c['teaching_evidence'] if e['purpose']!='understanding_check']
    report=checked(c,s)
    result=next(i for i in report.rule_results if i['rule_id']=='CONTENT-LEARNING-CHECKS')
    assert result['status']=='pass' and result['executed']
    assert not any(i['rule_id']=='CONTENT-LEARNING-CHECKS' for i in report.warnings)
