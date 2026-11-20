"""Small public API for generated scenes, loaded only inside the OS sandbox.

The generation response supplies step identities and captions, never timestamps.
The trusted renderer records the Manim renderer clock at context boundaries.
The host independently checks those intervals against the encoded video stream.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import math
import hashlib
from pathlib import Path

_context = None
_expected = ()
_records = []
_active = None
_scene = None
_narration = {}


def _configure(context, steps, narration=None):
    global _context, _expected, _records, _active, _scene, _narration
    _narration = {row["step_id"]:deepcopy(row) for row in (narration or {}).get("steps",[])}
    _context = deepcopy(context)
    _expected = () if _context.get('review_mode') in ('json_only','balanced') and not _narration else tuple(row["step_id"] for row in steps)
    _records = []
    _active = None
    _scene = None


def load_context():
    """Return a fresh copy of the frozen request and approved mathematical inputs."""
    if _context is None:
        raise RuntimeError("load_context is only available inside the media worker")
    return deepcopy(_context)


def _clock(scene):
    value = float(scene.renderer.time)
    if not math.isfinite(value) or value < 0:
        raise ValueError("media_step renderer clock is not finite and nonnegative")
    return value


@contextmanager
def media_step(scene, step_id):
    """Wrap each declared step's animations/waits in the actual rendered order."""
    global _active, _scene
    if _context is None:
        raise RuntimeError("media_step is only available inside the media worker")
    voice = _narration.get(step_id)
    if voice:
        path = Path(voice['path'])
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=voice['sha256']:
            raise ValueError('narration audio hash is corrupt')
        duration = float(voice['duration'])
        if not math.isfinite(duration) or not 0 < duration <= 1800:
            raise ValueError('narration duration is invalid')
    if _context.get('review_mode') in ('json_only','balanced') and not _narration:
        start=_clock(scene)
        yield
        _records.append({'step_id':step_id,'start':start,'end':_clock(scene)})
        return
    if _active is not None:
        raise ValueError("media_step contexts cannot be nested")
    if len(_records) >= len(_expected) or step_id != _expected[len(_records)]:
        raise ValueError(f"media_step order/identity differs from the declared steps: {step_id!r}")
    if _scene is not None and scene is not _scene:
        raise ValueError("media_step must use the same GeneratedScene instance")
    _scene = scene
    start = _clock(scene)
    previous = _records[-1]["end"] if _records else 0.0
    if abs(start - previous) > .00001:
        raise ValueError("media_step: rendered time outside a declared step is not covered")
    _active = step_id
    try:
        if voice: scene.add_sound(voice['path'])
        yield
        if voice and _clock(scene)-start < duration+.25:
            scene.wait(duration+.25-(_clock(scene)-start))
        end = _clock(scene)
        if end <= start:
            raise ValueError(f"media_step {step_id!r} rendered no frames")
        _records.append({"step_id": step_id, "start": start, "end": end})
    finally:
        _active = None


def _finish(scene):
    if _context and _context.get('review_mode') in ('json_only','balanced') and not _narration: return deepcopy(_records)
    if _active is not None or tuple(row["step_id"] for row in _records) != _expected:
        raise ValueError("media_step did not cover every declared step")
    if not _records or abs(_records[-1]["end"] - _clock(scene)) > .00001:
        raise ValueError("media_step does not cover the complete rendered timeline")
    return deepcopy(_records)
