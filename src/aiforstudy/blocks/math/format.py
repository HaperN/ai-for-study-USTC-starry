"""Finite AST-to-display syntax; user text is never interpreted as TeX."""
from __future__ import annotations

from ...errors import ContractError
from .expression import ast_names, normalize_ast, symbol_names

FORMAT_VERSION = '1.0'


def format_expression(expr: dict, *, backend: str = 'mathtext', symbols=None) -> str:
    if backend not in {'mathtext', 'katex', 'mathtex'}:
        raise ContractError('format.backend: unsupported display backend')
    # Validate size/syntax before discovering any names; identifiers can only
    # become generated display tokens, never TeX commands or raw fragments.
    normalized, _, _ = normalize_ast(expr, allowed_names=None if symbols is None else symbol_names(symbols))
    def render(node):
        op = node['op']
        if op == 'const':
            value = node['value']
            if isinstance(value, int):
                return str(value)
            # Decimal expansion avoids invalid ungrouped scientific notation.
            text = format(value, '.12g')
            if 'e' in text:
                mantissa, power = text.split('e')
                return mantissa + r'\times 10^{' + str(int(power)) + '}'
            return text
        if op == 'var':
            name = node['name']
            if len(name) == 1:
                return name
            return r'\mathrm{' + name.replace('_', r'\_') + '}'
        if op == 'pow':
            return r'\left(' + render(node['base']) + r'\right)^{' + str(node['exponent']) + '}'
        if op == 'sqrt':
            return r'\sqrt{' + render(node['arg']) + '}'
        if op == 'abs':
            return r'\left|' + render(node['arg']) + r'\right|'
        if op in {'sin', 'cos', 'exp', 'log'}:
            return '\\' + op + r'\left(' + render(node['arg']) + r'\right)'
        left, right = render(node['left']), render(node['right'])
        if op == 'div':
            return r'\frac{' + left + '}{' + right + '}'
        operator = {'add': '+', 'sub': '-', 'mul': r'\cdot '}[op]
        return r'\left(' + left + operator + right + r'\right)'
    return render(normalized)
