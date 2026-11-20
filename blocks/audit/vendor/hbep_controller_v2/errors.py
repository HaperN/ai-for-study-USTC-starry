from __future__ import annotations


class ControllerV2Error(Exception):
    """Stable fail-closed error returned by controller-v2 protocol code.

    ``poisoned`` is deliberately explicit.  Callers must not infer it from an
    exception class or message: a failure before handoff is normally a clean
    rejection, while the same validation failure after handoff is poison.
    """

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        poisoned: bool = False,
    ) -> None:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("controller-v2 error code must be non-empty")
        if not isinstance(message, str):
            raise TypeError("controller-v2 error message must be a string")
        self.code = code
        self.poisoned = bool(poisoned)
        self.message = message
        super().__init__(f"{code}: {message}" if message else code)


__all__ = ["ControllerV2Error"]
