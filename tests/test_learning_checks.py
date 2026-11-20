"""Local scoring must distinguish actual errors and exercise every feedback branch."""
from copy import deepcopy
import importlib
import pytest


def check_fixture(kind='choice'):
    values = {
        'choice': ('a', 'b', '', ['a'], [{'id':'a','label':'2'},{'id':'b','label':'4'}]),
        'boolean': (True, False, 'true', [True], []),
        'numeric': (2, 4, '', [2], []),
        'key_step': ('divide by 2', 'multiply by 2', '', ['divide by 2'], []),
        'self_explanation': ('我的理由', '', '', [], []),
    }
    right, wrong, boundary, accepted, options = values[kind]
    feedback = [] if kind == 'self_explanation' else [{'id':'M','inputs':[wrong],'feedback':'你把操作方向弄反了；请检查运算依据。'}]
    cases = [] if kind == 'self_explanation' else [
        {'id':'right','input':right,'expected':'correct','coverage':'correct'},
        {'id':'wrong','input':wrong,'expected':'incorrect','coverage':'wrong','feedback':'M'},
        {'id':'boundary','input':boundary,'expected':'invalid','coverage':'boundary'},
    ]
    return dict(id='L',kind=kind,source_kind='derived',goal_refs=['G'],source_refs=[{'kind':'content','claim_id':'K'}],
        statement='根据本节方法，给出关键一步。',input_spec={'options':options},
        scoring={'accepted_inputs':accepted},hints=['先辨认操作方向。','两边做同一个运算。'],
        answer='先除以 2，再核对条件。',misconception_feedback=feedback,test_cases=cases)


def api():
    module=importlib.import_module('aiforstudy.blocks.content.learning_checks')
    return module


def content_fixture(kind='choice'):
    from test_content_v4_reader_contract import candidate
    c=candidate(); c['learning_checks']=[check_fixture(kind)]
    return c


@pytest.mark.parametrize('kind', ['choice','boolean','numeric','key_step'])
def test_supported_checks_correct_wrong_boundary_and_misconception(kind):
    from aiforstudy.blocks.content.content_contracts import LearningCheckV1
    check=LearningCheckV1.model_validate(check_fixture(kind))
    for case in check.test_cases:
        result=api().grade_learning_check(check,case.input)
        assert result['status']==case.expected
        assert result['feedback_id']==case.feedback
    assert api().validate_learning_checks(content_fixture(kind))==[]


def test_numeric_tolerance_is_inclusive_and_nonfinite_and_coercions_invalid():
    row=check_fixture('numeric'); row['scoring']['absolute_tolerance']=0.25
    from aiforstudy.blocks.content.content_contracts import LearningCheckV1
    check=LearningCheckV1.model_validate(row)
    for value, expected in [(' 2.25 ','correct'),('1.75','correct'),('2.250001','incorrect'),('NaN','invalid'),('Infinity','invalid'),('2abc','invalid'),(True,'invalid'),('','invalid')]:
        assert api().grade_learning_check(check,value)['status']==expected


def test_key_step_casefold_is_explicit_and_free_explanation_is_manual():
    from aiforstudy.blocks.content.content_contracts import LearningCheckV1
    row=check_fixture('key_step'); row['scoring']['normalization']='casefold'
    assert api().grade_learning_check(LearningCheckV1.model_validate(row),' DIVIDE BY 2 ')['status']=='correct'
    manual=LearningCheckV1.model_validate(check_fixture('self_explanation'))
    assert api().grade_learning_check(manual,'这不是一个机器能判对的证明')['status']=='manual'
    assert api().grade_learning_check(manual,'')['status']=='invalid'


@pytest.mark.parametrize('mutation,code', [
    ('all_pass','regression_mismatch'),('missing_boundary','missing_regression_coverage'),
    ('dead_feedback','shadowed_misconception'),('correct_trigger','misconception_matches_correct'),
    ('untaught','goal_not_taught'),
    ('unknown_goal','unknown_goal'),('missing_source','missing_source'),
])
def test_rejects_bad_assessments(mutation,code):
    c=content_fixture(); row=c['learning_checks'][0]
    if mutation=='all_pass': row['scoring']['accepted_inputs'].append('b')
    elif mutation=='missing_boundary': row['test_cases'].pop()
    elif mutation=='dead_feedback': row['misconception_feedback'].append({'id':'dead','inputs':['b'],'feedback':'另一种错因'})
    elif mutation=='correct_trigger': row['misconception_feedback'][0]['inputs']=['a']
    elif mutation=='untaught': c['teaching_evidence']=[]
    elif mutation=='unknown_goal': row['goal_refs']=['missing']
    elif mutation=='missing_source': row['source_refs']=[]
    assert code in {x['code'] for x in api().validate_learning_checks(c)}


@pytest.mark.parametrize('field,value', [('source_kind','source'),('kind','proof'),('grader_code','return true'),('scoring',{'accepted_inputs':[2],'absolute_tolerance':float('inf')})])
def test_schema_rejects_original_exercise_claim_and_executable_or_nonfinite_grading(field,value):
    from aiforstudy.blocks.content.content_contracts import LearningCheckV1
    row=check_fixture('numeric'); row[field]=value
    with pytest.raises(ValueError): LearningCheckV1.model_validate(row)


def test_reader_projects_options_and_feedback_but_never_private_cases():
    from aiforstudy.blocks.content.content_contracts import parse_content
    from aiforstudy.blocks.content.reader_document import project_reader
    from test_content_v4_reader_contract import catalog
    row=content_fixture(); row['learning_checks'][0]['test_cases'][0]['id']='private-secret-id'
    doc=project_reader(parse_content(row),catalog()).as_dict()
    pointers={b['json_pointer'] for b in doc['blocks']}
    assert '/learning_checks/0/input_spec/options/0/label' in pointers
    assert '/learning_checks/0/misconception_feedback/0/feedback' in pointers
    assert 'private-secret-id' not in str(doc)
    assert 'test_cases' not in str(doc)
    assert doc['learning_checks'][0]['source_kind']=='derived'


def test_short_convention_can_use_a_visible_understanding_task_without_auto_grader():
    c=content_fixture();c['learning_checks']=[]
    c['annotations'].append({'id':'TASK','markdown':'请用自己的话说明符号代表什么，并与本节定义对照。'})
    c['teaching_evidence'].append({'goal_id':'G','purpose':'understanding_check','reader_pointer':'/annotations/1/markdown','claim_refs':['K']})
    assert api().validate_learning_checks(c)==[]
    c['teaching_evidence'][-1]['reader_pointer']='/annotations/9/markdown'
    assert api().validate_learning_checks(c)==[]


def test_rejects_overflowed_numeric_tolerance_and_unicode_scoring_uses_same_folding():
    from aiforstudy.blocks.content.content_contracts import LearningCheckV1
    row=check_fixture('numeric');row['scoring']={'accepted_inputs':[1e308],'relative_tolerance':1e308}
    with pytest.raises(ValueError,match='finite'): LearningCheckV1.model_validate(row)
    row=check_fixture('key_step'); row['scoring']={'accepted_inputs':['fl'],'normalization':'casefold'}
    check=LearningCheckV1.model_validate(row)
    assert api().grade_learning_check(check,'ﬂ')['status']=='correct'


@pytest.mark.parametrize('target',['statement','answer','option','feedback'])
def test_required_display_text_cannot_be_whitespace(target):
    from aiforstudy.blocks.content.content_contracts import LearningCheckV1
    row=check_fixture()
    if target in ('statement','answer'): row[target]='  '
    elif target=='option': row['input_spec']['options'][0]['label']='  '
    else: row['misconception_feedback'][0]['feedback']='  '
    with pytest.raises(ValueError): LearningCheckV1.model_validate(row)


def test_invalid_misconception_trigger_and_fake_manual_correctness_reject():
    row=content_fixture();row['learning_checks'][0]['misconception_feedback'][0]['inputs'].append('missing')
    assert 'invalid_misconception_trigger' in {x['code'] for x in api().validate_learning_checks(row)}
    from aiforstudy.blocks.content.content_contracts import LearningCheckV1
    manual=check_fixture('self_explanation');manual['test_cases']=[{'id':'fake','input':'proof','expected':'correct','coverage':'correct'}]
    with pytest.raises(ValueError): LearningCheckV1.model_validate(manual)


def test_numeric_boundary_excludes_unicode_digit_coercion():
    from aiforstudy.blocks.content.content_contracts import LearningCheckV1
    check=LearningCheckV1.model_validate(check_fixture('numeric'))
    assert api().grade_learning_check(check,'٢')['status']=='invalid'
