"""Runtime facts used by capability promises and by their cache identity.

Inspect availability only: never construct a paid transport or dispatch a call.
The path to a browser is not an author input; its availability is.
"""
from pathlib import Path


def interaction_runtime_inputs(ctx) -> dict:
    from ..checks.offline import _browser_executable
    from .gateway_client import _transport_for
    providers = getattr(ctx.services, 'providers', None)
    return {
        'transport_available': providers is not None and _transport_for(ctx)[0] is not None,
        'reader_available': bool(_browser_executable(Path(ctx.workspace.root))),
    }
