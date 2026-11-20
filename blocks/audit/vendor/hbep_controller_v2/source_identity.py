from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

from .path_safety import assert_no_link_components, is_link_like


MAX_SOURCE_FILES = 4096
MAX_SOURCE_FILE_BYTES = 256 * 1024 * 1024
MAX_SOURCE_TREE_BYTES = 256 * 1024 * 1024
PRODUCTION_CONTROLLER_FILES = (
    "__init__.py",
    "diagram_observer.py",
    "courseforge_publication.py",
    "direct_document_audit.py",
    "direct_document_audit_cli.py",
    "errors.py",
    "numeric_reasoning.py",
    "path_safety.py",
    "producer_handoff.py",
    "production_contract.py",
    "production_review.py",
    "restricted_reasoning.py",
    "semantic_audit.py",
    "source_identity.py",
    "unified_beginner_auditor.py",
)


@dataclass(frozen=True, slots=True)
class TreeIdentity:
    files: int
    bytes: int
    sha256: str

    def as_dict(self, root: Path) -> dict[str, object]:
        return {
            "path": str(root.resolve()),
            "files": self.files,
            "bytes": self.bytes,
            "sha256": self.sha256,
        }


def tree_identity(root: str | Path) -> TreeIdentity:
    safe_root = assert_no_link_components(root, label="controller source")
    if is_link_like(safe_root) or not safe_root.is_dir():
        raise ValueError("controller source must be a regular directory")
    digest = hashlib.sha256(b"HBEP-CONTROLLER-SOURCE/1\0")
    count = 0
    total = 0
    for relative_path in PRODUCTION_CONTROLLER_FILES:
        path = safe_root / relative_path
        assert_no_link_components(path, label="controller source file")
        if is_link_like(path):
            raise ValueError("controller source contains a link or junction")
        if not path.is_file():
            raise ValueError("controller source is missing a required Python file")
        count += 1
        if count > MAX_SOURCE_FILES:
            raise ValueError("controller source contains too many files")
        remaining = MAX_SOURCE_TREE_BYTES - total
        content = read_regular_bytes(
            path,
            min(MAX_SOURCE_FILE_BYTES, remaining),
            label="controller source file",
        )
        total += len(content)
        if total > MAX_SOURCE_TREE_BYTES:
            raise ValueError("controller source is too large")
        relative = relative_path.encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return TreeIdentity(count, total, digest.hexdigest())


def read_regular_bytes(path: Path, limit: int, *, label: str) -> bytes:
    if limit < 1:
        raise ValueError(f"{label} is too large")
    candidate = assert_no_link_components(path, label=label)
    before = candidate.stat(follow_symlinks=False)
    if (
        is_link_like(candidate)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size > limit
    ):
        raise ValueError(f"{label} is unsafe or too large")
    descriptor = os.open(
        candidate,
        os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size != before.st_size
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ValueError(f"{label} changed before opening")
        content = bytearray()
        while True:
            chunk = os.read(descriptor, min(65536, limit + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > limit:
                raise ValueError(f"{label} is too large")
        after = os.fstat(descriptor)
        if after.st_size != opened.st_size or len(content) != after.st_size:
            raise ValueError(f"{label} changed while reading")
    finally:
        os.close(descriptor)
    return bytes(content)


__all__ = [
    "PRODUCTION_CONTROLLER_FILES",
    "TreeIdentity",
    "read_regular_bytes",
    "tree_identity",
]
