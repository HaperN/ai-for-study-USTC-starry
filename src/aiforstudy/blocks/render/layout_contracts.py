"""Versioned, read-only records for the derived reader protocol."""
from ..content.content_contracts import JsonRecord


class ReaderMountV1(JsonRecord):
    """Source-bound mount; requested_span_cp uses approved Unicode code points."""


class ReaderLayoutV1(JsonRecord):
    """Syntax-block projection independent of ReaderDocument approval identity."""
