"""A non-blocking OS lease shared by CLI and UI for one canonical book root.

The lock file is deliberately never unlinked: deleting a held lock would allow
another process to lock a different inode at the same path. Metadata is only
for diagnosis; liveness comes from the kernel lock, which also handles crashes.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..errors import ContractError

LOCK_NAME = ".run.lock"


class BookBusyError(ContractError):
    """Another worker owns this book; no run state has been changed."""


def _lock(fd: int) -> bool:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno not in (13, 11, 36):
                raise
            return False
    else:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    return True


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


class BookLease:
    lock_name = LOCK_NAME

    def __init__(self, book_root: str | Path) -> None:
        self.book_root = Path(book_root).resolve()
        self.path = self.book_root / self.lock_name
        self._fd: int | None = None

    def __enter__(self) -> "BookLease":
        if self._fd is not None:
            raise BookBusyError(f"书籍 lease 已持有：{self.book_root}")
        self.book_root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if not _lock(fd):
                raise BookBusyError(f"书籍正在运行：{self.book_root.name}；请等待当前运行结束")
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps({"pid": os.getpid(), "acquired_at": time.time()}).encode())
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                _unlock(fd)
            finally:
                os.close(fd)


class ReleasePointerLease(BookLease):
    """Serialize pointer writers even when the caller already holds the book lease."""

    lock_name = ".current.lock"


def is_book_active(book_root: str | Path) -> bool:
    """Probe an existing lease without creating directories or changing metadata."""
    path = Path(book_root).resolve() / LOCK_NAME
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        if not _lock(fd):
            return True
        _unlock(fd)
        return False
    finally:
        os.close(fd)


__all__ = ["BookLease", "BookBusyError", "is_book_active"]
