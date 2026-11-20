"""Decode JSON syntax without imposing an object or application schema."""
from __future__ import annotations

import json
import re


_STRINGS_OR_CONSTANTS = re.compile(r'"(?:[^"\\]|\\.)*"|(?P<constant>-?Infinity|NaN)')


def loads_json_syntax(text: str):
    """Reject Python's non-JSON constants and retain normal decoder locations."""
    def reject_constant(value):
        # The decoder reports a token but no offset for parse_constant. Skip
        # quoted strings so the diagnostic points at the actual invalid token.
        position = next(match.start() for match in _STRINGS_OR_CONSTANTS.finditer(text)
                        if match.group('constant') == value)
        raise json.JSONDecodeError('Invalid JSON constant: ' + value, text, position)

    return json.loads(text, parse_constant=reject_constant)
