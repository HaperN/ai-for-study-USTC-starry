"""Progress all subprocess pipes while enforcing cancellation and a deadline.

Use binary PIPEs. The caller owns termination/reaping on exceptions and closes
the streams. No credentials reach argv/files and no background threads survive
a cancelled call. In particular, do not retry communicate(input=None) after a
partial timed-out write: CPython no longer registers that stdin for writing.
"""
from __future__ import annotations

import os
import selectors
import time


def communicate_bounded(process, payload: bytes, *, deadline: float | None,
                        checkpoint=None, poll_seconds: float = .05) -> tuple[bytes, bytes]:
    """Progress pipes; ``deadline=None`` waits until completion or cancellation."""
    output = bytearray()
    errors = bytearray()
    pending = memoryview(payload)
    offset = 0
    with selectors.DefaultSelector() as selector:
        if process.stdin is not None:
            if pending:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            else:
                process.stdin.close()
        for stream, destination in ((process.stdout, output), (process.stderr, errors)):
            if stream is not None:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, destination)
        while True:
            if checkpoint:
                checkpoint()
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError('Subprocess I/O absolute deadline exceeded; remote status unknown')
            if not selector.get_map() and process.poll() is not None:
                return bytes(output), bytes(errors)
            wait = poll_seconds if remaining is None else min(poll_seconds, remaining)
            for key, event in selector.select(wait):
                if event & selectors.EVENT_WRITE:
                    try:
                        offset += os.write(key.fd, pending[offset:offset + 65536])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        offset = len(pending)
                    if offset == len(pending):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()  # EOF lets json.load()/read() finish.
                else:
                    try:
                        chunk = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if chunk:
                        key.data.extend(chunk)
                    else:
                        selector.unregister(key.fileobj)
