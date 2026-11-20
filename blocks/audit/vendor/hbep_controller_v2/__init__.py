"""HBEP undergraduate STEM controller-v2 protocol package.

This package exposes implementation primitives only.  It deliberately does
not expose a locally constructible formal controller or any hidden-evaluation
material.
"""

from .errors import ControllerV2Error

__all__ = ("ControllerV2Error",)
__version__ = "0.4.1"
