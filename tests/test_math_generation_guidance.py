"""Generation examples must demonstrate useful, falsifiable mathematical checks."""
from copy import deepcopy

import pytest

from aiforstudy.blocks.content.prompt import math_representation_examples
from aiforstudy.blocks.math import (
    evaluate_expression,
    math_schema_example,
    parse_math_model,
    verify_math,
)


def quotient_model():
    examples = math_representation_examples()
    assert 'complex_quotient' in examples, 'A worked complex quotient model is required.'
    return parse_math_model(examples['complex_quotient']['model'])


def test_quotient_example_checks_three_real_relations_instead_of_self_equalities():
    model = quotient_model()
    assert {(row['name'], row['domain']) for row in model.symbols} == {
        ('x', 'real'), ('y', 'real'),
    }
    assert not model.conditions
    relations = {row['relation_id']: row for row in model.claims}
    assert set(relations) == {'R-QUOT-REAL', 'R-QUOT-IMAG', 'R-QUOT-MODULUS'}

    # Hand-calculated at x=0, y=2: D=5, N=-3. These are the three
    # cross-multiplied real-part, imaginary-part, and modulus identities.
    for relation_id, expected in [
        ('R-QUOT-REAL', 5), ('R-QUOT-IMAG', 10), ('R-QUOT-MODULUS', 25),
    ]:
        relation = relations[relation_id]
        assert relation['kind'] == 'identity'
        assert relation['lhs_object_id'] != relation['rhs_object_id']
        left = model.object_by_id(relation['lhs_object_id'])['expr']
        right = model.object_by_id(relation['rhs_object_id'])['expr']
        assert left != right, 'Renaming the same expression is still a self-equality.'
        for expression in (left, right):
            assert evaluate_expression(expression, model.symbols, {'x': 0, 'y': 2}) == expected

        # The quotient's excluded point belongs to this relation's domain,
        # even though the cleared polynomial identity itself holds there.
        assert len(relation['conditions']) == 1
        condition = relation['conditions'][0]
        assert condition['op'] == 'ne'
        for values, denominator in [({'x': 1, 'y': 0}, 0), ({'x': 0, 'y': 2}, 5)]:
            assert evaluate_expression(condition['left'], model.symbols, values) == denominator
            assert evaluate_expression(condition['right'], model.symbols, values) == 0

    report = verify_math(model)
    assert report.status == 'pass', report.as_dict()
    assert len(report.checks) == 3
    assert all(row.status == 'pass' for row in report.checks)


@pytest.mark.parametrize('relation_id,damage', [
    ('R-QUOT-REAL', 'omit_term'),
    ('R-QUOT-IMAG', 'reverse_sign'),
    ('R-QUOT-MODULUS', 'omit_term'),
])
def test_quotient_example_exposes_wrong_sign_or_missing_term(relation_id, damage):
    raw = deepcopy(quotient_model().as_dict())
    relation = next(row for row in raw['claims'] if row['relation_id'] == relation_id)
    left = next(row for row in raw['objects'] if row['math_object_id'] == relation['lhs_object_id'])
    if damage == 'reverse_sign':
        # 2y(1-x) - Ny becomes 2y(1-x) + Ny.
        assert left['expr']['op'] == 'sub'
        left['expr']['op'] = 'add'
    else:
        # Drop +2y² from the real relation or +4y² from the modulus relation.
        assert left['expr']['op'] == 'add'
        left['expr'] = left['expr']['left']

    report = verify_math(parse_math_model(raw))
    statuses = {row.relation_id: row.status for row in report.checks}
    assert statuses[relation_id] == 'fail', report.as_dict()
    assert all(status == 'pass' for key, status in statuses.items() if key != relation_id)


def test_ratio_example_cannot_cancel_a_denominator_without_its_nonzero_condition():
    raw = math_schema_example()
    assert verify_math(parse_math_model(raw)).status == 'pass'
    raw['conditions'] = []
    report = verify_math(parse_math_model(raw))
    assert report.status == 'fail'
    assert report.checks[0].reason_code == 'domain_violation'
    assert report.checks[0].evidence['counterexample'] == {'x': 0}
