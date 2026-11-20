from __future__ import annotations

import os
from pathlib import Path


def is_link_like(path: str | Path) -> bool:
    candidate = Path(path)
    if candidate.is_symlink():
        return True
    is_junction = getattr(candidate, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def assert_no_link_components(path: str | Path, *, label: str) -> Path:
    absolute = Path(os.path.abspath(Path(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if is_link_like(current):
            raise ValueError(f"{label} must not pass through a link or junction: {current}")
    return absolute


def assert_managed_path(path: str | Path, root: str | Path, *, label: str) -> Path:
    safe_root = assert_no_link_components(root, label=label)
    safe_path = assert_no_link_components(path, label=label)
    try:
        safe_path.relative_to(safe_root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside its managed root") from exc
    return safe_path


__all__ = ["assert_managed_path", "assert_no_link_components", "is_link_like"]
