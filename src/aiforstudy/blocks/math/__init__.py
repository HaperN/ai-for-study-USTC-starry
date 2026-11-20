"""Shared closed mathematical contracts, evaluators, and independent checking."""
from .contracts import (MathModelV1, MathCheck, MathCheckReport, VerificationLimits,
                        parse_math_model, describe_math_contract, math_schema_example)
from .expression import evaluate_expression, MathCapabilityError, BACKEND_CAPABILITIES
from .format import format_expression


def verify_math(model, claims=None, *, limits=None, cancel=None):
    # Keep worker module out of eager imports, allowing clean python -m startup.
    from .verify import verify_math as verify
    return verify(model, claims, limits=limits, cancel=cancel)


__all__ = ['MathModelV1', 'MathCheck', 'MathCheckReport', 'VerificationLimits', 'parse_math_model',
           'evaluate_expression', 'MathCapabilityError', 'BACKEND_CAPABILITIES',
           'format_expression', 'verify_math', 'describe_math_contract', 'math_schema_example']
