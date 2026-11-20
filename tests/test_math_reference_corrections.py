"""Repair invalid references, never redefine mathematical meaning to pass schema."""
from copy import deepcopy
import json

import pytest

from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.generation import generate_content
from aiforstudy.blocks.content.generation_corrections import LocalCorrectionRequired
from aiforstudy.blocks.content.math_corrections import validate_author_math
from aiforstudy.blocks.math.contracts import parse_math_model
from aiforstudy.errors import ContractError
from support.content_case import teaching_case, authoring
from test_generation_corrections import SequenceClient


def const(x): return {'op':'const','value':x}
def var(name): return {'op':'var','name':name}


def alias_case():
    content, source = teaching_case()
    expression = {'op':'add','left':{'op':'pow','base':var('n'),'exponent':2},'right':const(1)}
    condition = {'op':'gt','left':var('denom'),'right':const(0)}
    model = dict(schema_version='1.0', model_id='M',
        symbols=[{'name':'n','domain':'real'}], conditions=[condition], source_refs=['SO'],
        objects=[{'math_object_id':'denom','kind':'scalar','expr':expression},
                 {'math_object_id':'quotient','kind':'scalar','expr':{'op':'div','left':const(1),'right':var('denom')}}],
        claims=[{'claim_id':'K','relation_id':'R','kind':'identity','math_object_id':'quotient',
                 'lhs_object_id':'quotient','rhs_object_id':'quotient','conditions':[deepcopy(condition)]}])
    content['math_models']=[model]
    content['teaching_plan']['claims'][0]['math_model_refs']=['M']
    bad = authoring(content)
    good = deepcopy(bad)
    target=good['content']['math_models'][0]
    target['conditions'][0]['left']=deepcopy(expression)
    target['objects'][1]['expr']['right']=deepcopy(expression)
    target['claims'][0]['conditions'][0]['left']=deepcopy(expression)
    return source,bad,good


def test_scalar_alias_reports_every_object_global_and_relation_condition_at_once():
    source,bad,_=alias_case()
    with pytest.raises(LocalCorrectionRequired) as found:
        validate_author_math(bad['content'],source=source)
    error=found.value
    expected={'/content/math_models/0/conditions/0/left',
        '/content/math_models/0/objects/1/expr/right',
        '/content/math_models/0/claims/0/conditions/0/left'}
    assert set(error.allowed_paths)==expected
    expression=bad['content']['math_models'][0]['objects'][0]['expr']
    assert len(error.errors)==3
    for row in error.errors:
        assert row['candidates']==[expression]
        assert row['fix'] and row['passing_criterion']


def test_model_expands_only_frozen_alias_candidates_in_bounded_generation(tmp_path):
    """R3-1：内层取消 —— 一次调用；三条候选错误全部写进 journal 反馈。"""
    source,bad,good=alias_case()
    journal=tmp_path/'corrections.json';client=SequenceClient(bad)
    with pytest.raises(ContractError,match='逐条修正'):
        generate_content(source,load_content_policy(),client,correction_journal=journal)
    assert len(client.calls)==1
    feedback=json.loads(journal.read_text())['merged_feedback']
    # 信封解析不能覆盖已收集的三条精确反馈。
    assert {row['path'] for row in feedback['errors']} == {
        '/content/math_models/0/conditions/0/left',
        '/content/math_models/0/objects/1/expr/right',
        '/content/math_models/0/claims/0/conditions/0/left'}
    assert all(row['code'] == 'object_used_as_symbol' and row['candidates']
               for row in feedback['errors'])
    # 合法答复仍然一次通过（对照）
    result=generate_content(source,load_content_policy(),SequenceClient(good))
    assert result['content']['math_models'][0]==parse_math_model(good['content']['math_models'][0]).as_dict()





def test_undeclared_safe_symbol_needs_both_teaching_declaration_and_frozen_source():
    source,bad,good=alias_case()
    for envelope in (bad,good):
        model=envelope['content']['math_models'][0]
        model['conditions']=[];model['claims'][0]['conditions']=[]
        model['objects']= [{'math_object_id':'quotient','kind':'scalar','expr':var('n')}]
        model['symbols']=[]
    good['content']['math_models'][0]['symbols']=[{'name':'n','domain':'integer','assumptions':['nonnegative']}]
    result=generate_content(source,load_content_policy(),SequenceClient(good))
    assert result['content']['math_models'][0]['symbols'][0]['name']=='n'
    foreign=deepcopy(source);foreign['source_text']='没有对应的数学符号。'
    with pytest.raises(LocalCorrectionRequired) as found:
        validate_author_math(bad['content'],source=foreign)
    assert not found.value.append_only


def test_unknown_variable_can_only_choose_declared_names_and_unsafe_gets_actionable_feedback(tmp_path):
    """R3-1：内层纠正轮已取消 —— 不合格的答复**只花一次调用**，
    但必须把"哪里错、怎么算过"写进 journal 供外层返修轮使用。

    保证没变：不安全的名字永远不会被接受。
    """
    source,bad,_=alias_case()
    model=bad['content']['math_models'][0]
    model['conditions']=[];model['claims'][0]['conditions']=[]
    model['objects']= [{'math_object_id':'quotient','kind':'scalar','expr':var('__import__')}]
    journal=tmp_path/'math.json'
    client=SequenceClient(bad)
    with pytest.raises(ContractError,match='逐条修正'):
        generate_content(source,load_content_policy(),client,correction_journal=journal)
    assert len(client.calls)==1, '内层不再重交'
    feedback=json.loads(journal.read_text())['merged_feedback']
    assert feedback['errors'], feedback
    assert all('path' in row for row in feedback['errors'])
    assert any(row.get('passing_criterion') or row.get('fix') for row in feedback['errors'])
    # 合法的声明变量仍然可以通过（对照）
    source2,_,good=alias_case()
    model=good['content']['math_models'][0]
    model['conditions']=[];model['claims'][0]['conditions']=[]
    model['objects']= [{'math_object_id':'quotient','kind':'scalar','expr':var('n')}]
    result=generate_content(source2,load_content_policy(),SequenceClient(good))
    assert result['content']['math_models'][0]['objects'][0]['expr']==var('n')


def test_rejected_answer_is_durable_and_replays_without_a_call(tmp_path):
    """R3-1：内层取消后，"被拒"也要**持久**：重放同一个 journal 不再调用模型。

    旧行为是"1 原始 + 2 纠正"才判死；现在一次即判，反馈留在 journal 里给外层返修轮用。
    """
    source,bad,_=alias_case();journal=tmp_path/'state.json'
    client=SequenceClient(bad)
    with pytest.raises(ContractError,match='逐条修正'):
        generate_content(source,load_content_policy(),client,correction_journal=journal)
    assert len(client.calls)==1
    replay=SequenceClient()
    with pytest.raises(ContractError,match='逐条修正'):
        generate_content(source,load_content_policy(),replay,correction_journal=journal)
    assert replay.calls==[], 'journal 已定稿，重放不得再花一次调用'


def test_diagnostic_aggregates_missing_objects_aliases_and_relation_variable():
    source,bad,_=alias_case()
    model=bad['content']['math_models'][0]
    model['claims'].append({'claim_id':'K','relation_id':'R2','kind':'derivative',
        'math_object_id':'quotient','expected_object_id':'missing','variable':'typo','order':1})
    with pytest.raises(LocalCorrectionRequired) as caught:
        validate_author_math(bad['content'],source=source)
    errors=caught.value.errors
    assert len(errors)==5
    assert {row['code'] for row in errors}=={'unknown_math_object_id','object_used_as_symbol','undeclared_math_symbol'}
    item=next(row for row in errors if row['path'].endswith('/variable'))
    assert item['candidates']==['n']
    assert caught.value.append_only['/content/math_models/0/objects']['object_ids']==['missing']


def test_missing_object_append_and_exact_alias_nodes_can_be_repaired_together():
    source,bad,good=alias_case()
    for envelope in (bad,good):
        envelope['content']['math_models'][0]['claims'][0]['rhs_object_id']='missing'
    good['content']['math_models'][0]['objects'].append({
        'math_object_id':'missing','kind':'scalar','expr':var('n')})
    result=generate_content(source,load_content_policy(),SequenceClient(good))
    assert len(result['content']['math_models'][0]['objects'])==3
    # Structural acceptance does not claim that the modified identity is true;
    # ordinary CAS will examine the appended actual object afterward.


def test_recursive_scalar_alias_candidates_are_frozen_and_cycles_do_not_invent_values():
    source,bad,_=alias_case()
    model=bad['content']['math_models'][0]
    actual=deepcopy(model['objects'][0]['expr'])
    model['objects'].append({'math_object_id':'inner','kind':'scalar','expr':actual})
    model['objects'][0]['expr']=var('inner')
    with pytest.raises(LocalCorrectionRequired) as caught:
        validate_author_math(bad['content'],source=source)
    assert len(caught.value.errors)==4
    assert all(row['candidates']==[actual] for row in caught.value.errors)
    model['objects'][-1]['expr']=var('denom')
    with pytest.raises(LocalCorrectionRequired) as caught:
        validate_author_math(bad['content'],source=source)
    assert all(row['candidates']==[] for row in caught.value.errors)
    assert not caught.value.append_only


@pytest.mark.parametrize('damage',['unlisted_symbol','unused_symbol','rename_old_symbol','false_teaching_declaration'])
def test_symbol_changes_are_validated_without_a_field_whitelist(damage):
    source,bad,_=alias_case()
    model=bad['content']['math_models'][0]
    model['conditions']=[];model['claims'][0]['conditions']=[]
    model['objects']=[{'math_object_id':'quotient','kind':'scalar','expr':var('n')}]
    model['symbols']=[{'name':'m','domain':'real'}]
    good=deepcopy(bad);changed=good['content']['math_models'][0]
    changed['symbols'].append({'name':'n','domain':'real'})
    if damage=='unlisted_symbol': changed['symbols'].append({'name':'extra','domain':'real'})
    if damage=='unused_symbol': changed['objects'][0]['expr']=var('m')
    if damage=='rename_old_symbol': changed['symbols'][0]['name']='x'
    if damage=='false_teaching_declaration':
        bad['content']['teaching_plan']['claims'][0]['symbols']=[]
        good['content']['teaching_plan']['claims'][0]['symbols']=[]
    result = generate_content(source,load_content_policy(),SequenceClient(good))
    assert result['content']['math_models'][0] == parse_math_model(changed).as_dict()
    assert 'approval' not in result
