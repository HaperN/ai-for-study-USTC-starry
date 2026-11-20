"""Per-call response metadata; legacy transports still return a plain two-tuple."""


def response_metadata(value):
    """Only terminal status/identity cross the worker boundary, never HTTP data."""
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in ('finish_reason', 'response_id', 'model')
            if isinstance(value.get(key), str) and 0 < len(value[key]) <= 1024}


class TransportResponse(tuple):
    """Unpacks and compares as ``(text, usage)`` with metadata owned by this call."""

    def __new__(cls, text, usage, *, metadata=None):
        result = super().__new__(cls, (text, usage))
        result.metadata = response_metadata(metadata)
        return result
