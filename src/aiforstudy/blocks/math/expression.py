"""Closed mathematical AST and trusted numerical interpretation.

The CAS verifier never calls this numerical evaluator. Resource capability limits
are distinct from syntax safety: a safe model exceeding a backend limit remains
inspectable, and verification reports unknown instead of changing the problem.
"""
from __future__ import annotations

import math
import re
from numbers import Real
from typing import Any, Mapping

from ...errors import ContractError

AST_VERSION = '1.0'
MAX_AST_DEPTH = 12
MAX_AST_NODES = 256
MAX_MATRIX_SIZE = 8
MIN_INTEGER_POWER = -8
MAX_INTEGER_POWER = 8
BINARY_OPS = frozenset({'add', 'sub', 'mul', 'div'})
UNARY_OPS = frozenset({'sin', 'cos', 'exp', 'log', 'sqrt', 'abs'})
OPERATORS = frozenset({'const', 'var', 'pow'}) | BINARY_OPS | UNARY_OPS
AST_FIELDS = {'const': frozenset({'op', 'value'}), 'var': frozenset({'op', 'name'}),
              'pow': frozenset({'op', 'base', 'exponent'}),
              **{op: frozenset({'op', 'left', 'right'}) for op in BINARY_OPS},
              **{op: frozenset({'op', 'arg'}) for op in UNARY_OPS}}
BACKEND_CAPABILITIES = {
    'numpy': {'operators': sorted(OPERATORS), 'max_depth': 12, 'max_nodes': 256,
              'integer_power': [-8, 8], 'matrix_size': [8, 8]},
    'sympy': {'operators': sorted(OPERATORS), 'max_depth': 12, 'max_nodes': 256,
              'integer_power': [-8, 8], 'matrix_size': [8, 8],
              'claims': ['identity', 'equation_equivalent', 'solution_set',
                         'matrix_rank', 'matrix_inverse', 'derivative']},
    'mathtext': {'operators': sorted(OPERATORS)},
    'katex': {'operators': sorted(OPERATORS)},
    'mathtex': {'operators': sorted(OPERATORS)},
}
NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')


class MathCapabilityError(ContractError):
    """Safe syntax outside the declared computational capability."""


def symbol_names(symbols: Any) -> set[str]:
    if isinstance(symbols, Mapping):
        return set(symbols)
    return {item if isinstance(item, str) else item['name'] for item in symbols}


def _number(value: Any, path: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f'{path}: finite numeric constant required')
    # Bound input magnitude, before conversion can overflow or allocate huge CAS integers.
    if isinstance(value, int) and value.bit_length() > 1024:
        raise ContractError(f'{path}: constant integer exceeds safe input size')
    if not math.isfinite(value):
        raise ContractError(f'{path}: finite numeric constant required')
    return value


def normalize_ast(node: Any, path: str = 'expr', *, allowed_names: set[str] | None,
                  enforce_limits: bool = True) -> tuple[dict, int, int]:
    count = 0
    def visit(item: Any, where: str, depth: int) -> tuple[dict, int]:
        nonlocal count
        count += 1
        # Defensive parsing bounds protect the parent; normal capability is checked separately.
        if depth > 64 or count > 4096:
            raise MathCapabilityError(f'{where}: AST exceeds safe parsing size')
        if not isinstance(item, dict):
            raise ContractError(f'{where}: expression must be a closed AST object, never text')
        op = item.get('op')
        expected = AST_FIELDS.get(op) if isinstance(op, str) else None
        if expected is None or set(item) != expected:
            raise ContractError(f'{where}: unsupported operator or fields {op!r}')
        if op == 'const':
            return {'op': op, 'value': _number(item['value'], where + '.value')}, depth
        if op == 'var':
            name = item['name']
            if not isinstance(name, str) or not NAME_RE.fullmatch(name) or (allowed_names is not None and name not in allowed_names):
                raise ContractError(f'{where}.name: undeclared or unsafe symbol {name!r}')
            return {'op': op, 'name': name}, depth
        if op == 'pow':
            power = item['exponent']
            if isinstance(power, bool) or not isinstance(power, int) or abs(power) > 1000000:
                raise ContractError(f'{where}.exponent: bounded integer required')
            if enforce_limits and not MIN_INTEGER_POWER <= power <= MAX_INTEGER_POWER:
                raise MathCapabilityError(f'{where}.exponent: supported integer powers are -8..8')
            base, deepest = visit(item['base'], where + '.base', depth + 1)
            return {'op': op, 'base': base, 'exponent': power}, deepest
        if op in UNARY_OPS:
            arg, deepest = visit(item['arg'], where + '.arg', depth + 1)
            return {'op': op, 'arg': arg}, deepest
        left, ld = visit(item['left'], where + '.left', depth + 1)
        right, rd = visit(item['right'], where + '.right', depth + 1)
        return {'op': op, 'left': left, 'right': right}, max(ld, rd)
    result, depth = visit(node, path, 1)
    if enforce_limits and (depth > MAX_AST_DEPTH or count > MAX_AST_NODES):
        raise MathCapabilityError(f'{path}: AST capability exceeded (depth={depth}, nodes={count})')
    return result, depth, count


def ast_names(node: Mapping[str, Any]) -> set[str]:
    if node['op'] == 'const':
        return set()
    if node['op'] == 'var':
        return {node['name']}
    if node['op'] == 'pow':
        return ast_names(node['base'])
    if node['op'] in UNARY_OPS:
        return ast_names(node['arg'])
    return ast_names(node['left']) | ast_names(node['right'])


def evaluate_expression(expr: Any, symbols: Any, values: Mapping[str, Any]) -> Any:
    """Evaluate validated ASTs with trusted NumPy operations; no text evaluator.

    Scalar results are Python scalars. Vector/matrix object dictionaries return
    NumPy arrays and accept the same syntax and declared numerical capability.
    Domain/nonfinite failures raise ContractError; they cannot be silently drawn.
    """
    import numpy as np
    names = symbol_names(symbols)
    def scalar(node: Any) -> Any:
        normalized, _, _ = normalize_ast(node, allowed_names=names)
        def run(item: dict) -> Any:
            op = item['op']
            if op == 'const':
                return item['value']
            if op == 'var':
                if item['name'] not in values:
                    raise ContractError(f"values: missing symbol {item['name']}")
                value = values[item['name']]
                array = np.asarray(value)
                if array.dtype.kind not in 'iufc' or not np.all(np.isfinite(array)):
                    raise ContractError('values: finite numeric values required')
                return array
            if op == 'pow':
                base = np.asarray(run(item['base']))
                # Integer bases need promotion for negative powers; complex
                # bases must retain their imaginary component during promotion.
                dtype = complex if np.iscomplexobj(base) else float
                return np.power(np.asarray(base, dtype=dtype), item['exponent'])
            if op in UNARY_OPS:
                operations = {'sin': np.sin, 'cos': np.cos, 'exp': np.exp,
                              'log': np.log, 'sqrt': np.sqrt, 'abs': np.abs}
                return operations[op](run(item['arg']))
            left, right = run(item['left']), run(item['right'])
            return {'add': np.add, 'sub': np.subtract, 'mul': np.multiply,
                    'div': np.divide}[op](left, right)
        try:
            with np.errstate(all='raise'):
                result = run(normalized)
            if not np.all(np.isfinite(result)):
                raise ContractError('expr: numerical result is not finite')
            return result.item() if isinstance(result, np.ndarray) and result.ndim == 0 else result
        except (FloatingPointError, ZeroDivisionError, OverflowError, ValueError) as exc:
            raise ContractError(f'expr: numerical domain failure ({exc})') from exc
    if isinstance(expr, dict) and expr.get('kind') in {'scalar', 'vector', 'matrix'}:
        if expr['kind'] == 'scalar':
            return scalar(expr['expr'])
        rows = expr['rows'] if expr['kind'] == 'matrix' else [expr['entries']]
        if not rows or not rows[0] or len(rows) > 8 or len(rows[0]) > 8:
            raise MathCapabilityError('rows: matrix/vector capability exceeded')
        if any(len(row) != len(rows[0]) for row in rows):
            raise ContractError('rows: rectangular matrix required')
        result = np.asarray([[scalar(item) for item in row] for row in rows])
        return result if expr['kind'] == 'matrix' else result[0]
    return scalar(expr)
