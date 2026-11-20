from __future__ import annotations

import ast
from fractions import Fraction
import io
import re
import tokenize


_MAX_AST_NODES = 128
_MAX_SOURCE_CHARS = 4096
_MAX_INTEGER_BITS = 4096
_MAX_EXPONENT = 32
_TRAILING_PUNCTUATION = "。；;，,"
_QUANTITY_WITH_UNIT = re.compile(
    r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)\s*"
    r"(?:%|[fpnumkMGT]?(?:m|s|g|A|V|W|J|Hz|Pa|N|C|K|eV|rad|mol|L))\b"
)
_QUANTITATIVE_RESULT = re.compile(
    r"(?:为|等于|结果|得到|计算得|约为|达到|增加到|减少到)\s*"
    r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
)


def _strip_math_delimiters(value: str) -> str:
    text = value.strip().rstrip(_TRAILING_PUNCTUATION).strip()
    pairs = (("$$", "$$"), (r"\(", r"\)"), (r"\[", r"\]"), ("$", "$"))
    for left, right in pairs:
        if text.startswith(left) and text.endswith(right) and len(text) >= len(left) + len(right):
            return text[len(left) : len(text) - len(right)].strip()
    return text


def _numeric_latex(value: str) -> str:
    text = _strip_math_delimiters(value)
    text = text.replace(r"\cdot", "*").replace(r"\times", "*").replace(r"\div", "/")
    text = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    text = re.sub(r"(?<=[0-9)])\s*(?=\()", "*", text)
    text = re.sub(r"(?<=\))\s*(?=[0-9(])", "*", text)
    text = re.sub(r"(?<=[0-9)])\^(?=[+-]?\d)", "**", text)
    return text


def _fraction_from_literal(token: str) -> Fraction:
    if any(character in token.lower() for character in ("j", "n")):
        raise ValueError("unsupported numeric literal")
    return Fraction(token.replace("_", ""))


def _bounded(value: Fraction) -> Fraction:
    if value.numerator.bit_length() > _MAX_INTEGER_BITS or value.denominator.bit_length() > _MAX_INTEGER_BITS:
        raise ValueError("numeric result exceeds the exact arithmetic budget")
    return value


def _eval_numeric(value: str) -> Fraction:
    if not isinstance(value, str) or not value or len(value) > _MAX_SOURCE_CHARS:
        raise ValueError("numeric expression is unavailable")
    expression = _numeric_latex(value)
    if not expression:
        raise ValueError("numeric expression is empty")
    try:
        tokens = tokenize.generate_tokens(io.StringIO(expression).readline)
        rewritten: list[tuple[int, str]] = []
        for token in tokens:
            if token.type == tokenize.NUMBER:
                rewritten.append((tokenize.NAME, f'F("{token.string}")'))
            elif token.type in {
                tokenize.ENDMARKER,
                tokenize.NEWLINE,
                tokenize.NL,
                tokenize.ENCODING,
            }:
                continue
            else:
                rewritten.append((token.type, token.string))
        tree = ast.parse(tokenize.untokenize(rewritten), mode="eval")
    except (SyntaxError, tokenize.TokenError) as exc:
        raise ValueError("unsupported numeric expression") from exc
    if sum(1 for _ in ast.walk(tree)) > _MAX_AST_NODES:
        raise ValueError("numeric expression is too complex")

    def evaluate(node: ast.AST) -> Fraction:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "F"
            and len(node.args) == 1
            and not node.keywords
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return _bounded(_fraction_from_literal(node.args[0].value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = evaluate(node.operand)
            return operand if isinstance(node.op, ast.UAdd) else -operand
        if isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return _bounded(left + right)
            if isinstance(node.op, ast.Sub):
                return _bounded(left - right)
            if isinstance(node.op, ast.Mult):
                return _bounded(left * right)
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise ZeroDivisionError("division by zero")
                return _bounded(left / right)
            if isinstance(node.op, ast.Pow):
                if right.denominator != 1 or abs(right.numerator) > _MAX_EXPONENT:
                    raise ValueError("unsupported exponent")
                exponent = right.numerator
                if exponent < 0 and left == 0:
                    raise ZeroDivisionError("zero to a negative power")
                return _bounded(left**exponent)
        raise ValueError("unsupported numeric expression")

    return evaluate(tree)


def _numeric_equality_status(value: str) -> str:
    expression = _numeric_latex(value)
    if expression.count("=") != 1:
        return "NOT_EQUALITY"
    left, right = (item.strip() for item in expression.split("=", 1))
    if not left or not right:
        return "UNSUPPORTED"
    try:
        return "TRUE" if _eval_numeric(left) == _eval_numeric(right) else "FALSE"
    except ZeroDivisionError:
        return "UNDEFINED"
    except ValueError:
        return "UNSUPPORTED"


def _numeric_rule_fact_status(value: str) -> str:
    expression = _numeric_latex(value)
    if "=" not in expression or not any(character.isdigit() for character in expression):
        return "NOT_NUMERIC"
    if "\\" in expression:
        return "UNSUPPORTED"
    if re.search(r"[A-Za-z_]", expression):
        return "NOT_NUMERIC"
    return _numeric_equality_status(expression)


def _contains_quantitative_claim(value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    text = _strip_math_delimiters(value)
    return bool(
        _QUANTITY_WITH_UNIT.search(text) or _QUANTITATIVE_RESULT.search(text)
    )


__all__ = [
    "_contains_quantitative_claim",
    "_eval_numeric",
    "_numeric_equality_status",
    "_numeric_latex",
    "_numeric_rule_fact_status",
]
