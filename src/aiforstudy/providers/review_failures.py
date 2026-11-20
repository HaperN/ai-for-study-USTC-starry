"""Classify review protocol errors separately from failed model calls.

Only ``protocol`` describes an invalid review action and can justify an evidence
repair. Transport, truncation and configuration failures contain no judgment.
"""
from __future__ import annotations

from ..errors import Cancelled, ConfigError, ContractError

FAILURE_KINDS = frozenset({'protocol', 'truncation', 'timeout', 'network',
                           'configuration', 'cancelled', 'internal',
                           # 修改计划 A2：空答复与无法解析的原文各自保留类别，不再被改写成 internal。
                           'empty_response', 'unparsed'})


class ReviewCallFailure(RuntimeError):
    """A failed call retains its machine-readable nature through client adapters."""
    def __init__(self, message, *, failure_kind='internal'):
        super().__init__(message)
        self.failure_kind = failure_kind if failure_kind in FAILURE_KINDS else 'internal'


def classify_review_failure(exc: BaseException) -> str:
    """Return an explicit category; unknown implementation failures stay internal."""
    explicit = getattr(exc, 'failure_kind', None)
    if explicit in FAILURE_KINDS:
        return explicit
    if isinstance(exc, Cancelled):
        return 'cancelled'
    if isinstance(exc, ConfigError):
        return 'configuration'
    if isinstance(exc, TimeoutError):
        return 'timeout'
    if isinstance(exc, (ConnectionError, OSError)):
        return 'network'
    if isinstance(exc, ContractError):
        return 'protocol'
    if exc.__cause__ is not None:
        return classify_review_failure(exc.__cause__)
    return 'internal'
