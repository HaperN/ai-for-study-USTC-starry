"""Shared safe mathematical syntax, independent of visual rendering."""
import copy
import importlib
import math

import pytest
from aiforstudy.errors import ContractError


def api():
    try:
        return importlib.import_module('aiforstudy.blocks.math')
    except ModuleNotFoundError:
        pytest.fail('shared math package has not been implemented')


def c(value):
    return {'op': 'const', 'value': value}


def v(name='x'):
    return {'op': 'var', 'name': name}


def binary(op, left, right):
    return {'op': op, 'left': left, 'right': right}


def model_data(expr=None):
    return {'schema_version': '1.0', 'model_id': 'model-test',
            'symbols': [{'name': 'x', 'domain': 'real', 'assumptions': []}],
            'objects': [{'math_object_id': 'f', 'kind': 'scalar', 'expr': expr or v()}],
            'conditions': [], 'claims': [], 'source_refs': ['source-1']}


def test_model_digest_tracks_objects_conditions_and_cannot_be_mutated_by_input():
    data = model_data()
    model = api().parse_math_model(data)
    original = model.digest
    data['objects'][0]['expr']['name'] = 'untrusted'
    assert model.object_by_id('f')['expr'] == v()
    assert model.digest == original
    dumped = model.as_dict()
    dumped['objects'][0]['expr']['name'] = 'untrusted'
    assert model.digest == original
    altered = model_data()
    altered['conditions'] = [binary('ne', v(), c(0))]
    assert api().parse_math_model(altered).digest != original


@pytest.mark.parametrize('expr', ["__import__('os').system('touch /tmp/math-bad')",
                                  {'op': 'call', 'name': 'open', 'args': []},
                                  {'op': 'const', 'value': True},
                                  {'op': 'const', 'value': float('nan')},
                                  {'op': 'var', 'name': 'undeclared'},
                                  {'op': 'const', 'value': 1, 'python': 'x'},
                                  {'op': 'pow', 'base': c(2), 'exponent': 0.5}])
def test_unsafe_ast_is_rejected_before_worker_execution(expr):
    with pytest.raises(ContractError):
        api().parse_math_model(model_data(expr))


def test_matrix_shape_and_claim_reference_are_validated():
    data = model_data()
    data['objects'].append({'math_object_id': 'A', 'kind': 'matrix',
                            'rows': [[c(1), c(2)], [c(3)]]})
    with pytest.raises(ContractError, match='rows'):
        api().parse_math_model(data)
    data = model_data()
    data['claims'] = [{'claim_id': 'missing', 'kind': 'matrix_rank',
                       'math_object_id': 'does-not-exist', 'expected': 1}]
    with pytest.raises(ContractError, match='math_object_id'):
        api().parse_math_model(data)


def test_trusted_numeric_interpreter_covers_all_finite_operators_and_arrays():
    evaluate = api().evaluate_expression
    symbols = model_data()['symbols']
    cases = [('sin', math.sin(2)), ('cos', math.cos(2)), ('exp', math.exp(2)),
             ('log', math.log(2)), ('sqrt', math.sqrt(2)), ('abs', 2)]
    for op, want in cases:
        assert evaluate({'op': op, 'arg': v()}, symbols, {'x': 2}) == pytest.approx(want)
    assert evaluate({'op': 'pow', 'base': v(), 'exponent': -2}, symbols, {'x': 2}) == .25
    assert evaluate(binary('div', v(), c(2)), symbols, {'x': 2}) == 1
    matrix = {'kind': 'matrix', 'rows': [[v(), c(2)], [c(3), c(4)]]}
    assert evaluate(matrix, symbols, {'x': 1}).tolist() == [[1, 2], [3, 4]]
    with pytest.raises(ContractError):
        evaluate(binary('div', c(1), c(0)), [], {})


def test_formatter_output_is_renderable_and_only_uses_closed_ast():
    from matplotlib.mathtext import MathTextParser
    expr = binary('div', {'op': 'sqrt', 'arg': binary('add', v(), c(1))},
                  {'op': 'pow', 'base': v(), 'exponent': 2})
    text = api().format_expression(expr, symbols=['x'])
    MathTextParser('path').parse('$' + text + '$')
    assert '\\frac' in text and '\\sqrt' in text
    with pytest.raises(ContractError):
        api().format_expression({'op': 'raw_tex', 'value': '\\input{secret}'})


@pytest.mark.parametrize('mutate', [
    lambda data: data['symbols'][0].update(name='x-y'),
    lambda data: data['symbols'][0].update(assumptions=[{}]),
    lambda data: data.update(conditions=[binary([], v(), c(0))]),
    lambda data: data.update(claims=[{'claim_id': 'bad', 'kind': 'matrix_rank',
                                      'math_object_id': [], 'expected': 1}]),
])
def test_malformed_typed_contract_fields_raise_contract_error(mutate):
    data = model_data()
    mutate(data)
    with pytest.raises(ContractError):
        api().parse_math_model(data)


def test_scalar_variables_can_be_vectorized_without_changing_negative_power_values():
    evaluate = api().evaluate_expression
    result = evaluate({'op': 'pow', 'base': v(), 'exponent': -1}, ['x'], {'x': [1, 2, 4]})
    assert result.tolist() == [1, .5, .25]


def test_complex_sqrt_is_supported_on_numeric_route():
    result = api().evaluate_expression({'op': 'sqrt', 'arg': v()}, ['x'], {'x': -1 + 0j})
    assert result == 1j


def test_formatter_rejects_deep_untrusted_ast_without_recursing_first():
    expr = v()
    for _ in range(1000):
        expr = {'op': 'sin', 'arg': expr}
    with pytest.raises(ContractError):
        api().format_expression(expr)


def test_parse_deep_ast_rejects_before_copying_nested_input():
    expr = v()
    for _ in range(1000):
        expr = {'op': 'sin', 'arg': expr}
    with pytest.raises(ContractError):
        api().parse_math_model(model_data(expr))


def test_authoritative_schema_and_example_are_json_serializable_and_parser_valid():
    import json
    contract = api().describe_math_contract()
    example = api().math_schema_example()
    encoded = json.dumps(contract, ensure_ascii=False, allow_nan=False)
    assert json.loads(encoded)['title'] == 'MathModelV1'
    assert contract['additionalProperties'] is False
    assert set(contract['required']) == {'schema_version', 'model_id', 'symbols', 'objects',
                                         'conditions', 'claims', 'source_refs'}
    model = api().parse_math_model(example)
    assert model.as_dict() == json.loads(json.dumps(example, allow_nan=False))
    assert api().verify_math(model).status == 'pass'
    op_branches = contract['$defs']['expression']['oneOf']
    # Numeric rendering operations available today must all have usable schema
    # branches; generation cannot be steered back to a weaker AST subset.
    operations = {branch['properties']['op']['const'] for branch in op_branches}
    assert operations == {'const', 'var', 'add', 'sub', 'mul', 'div', 'pow',
                          'sin', 'cos', 'exp', 'log', 'sqrt', 'abs'}
    claim_branches = contract['properties']['claims']['items']['oneOf']
    assert {branch['properties']['kind']['const'] for branch in claim_branches} == {
        'identity', 'equation_equivalent', 'solution_set', 'matrix_rank',
        'matrix_inverse', 'derivative', 'abstract'}


@pytest.mark.parametrize('power,expected', [(2, -1 + 0j), (-2, -1 + 0j), (3, -1j)])
def test_complex_integer_powers_preserve_imaginary_component(power, expected):
    result = api().evaluate_expression({'op': 'pow', 'base': v(), 'exponent': power},
                                      [{'name': 'x', 'domain': 'complex', 'assumptions': []}], {'x': 1j})
    assert result == pytest.approx(expected)


def test_complex_vectorized_negative_power_preserves_values():
    result = api().evaluate_expression({'op': 'pow', 'base': v(), 'exponent': -1},
                                      [{'name': 'x', 'domain': 'complex', 'assumptions': []}],
                                      {'x': [1j, 1 + 1j]})
    assert result.tolist() == pytest.approx([-1j, .5 - .5j])
