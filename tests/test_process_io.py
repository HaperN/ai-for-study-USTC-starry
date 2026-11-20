"""Bounded pipe communication must progress in both directions."""
import subprocess
import sys
import time

import pytest


@pytest.mark.parametrize('source', [
    "import sys; sys.stdout.buffer.write(b'o'*2097152); sys.stdout.flush(); "
    "sys.stderr.buffer.write(b'e'*1048576); sys.stderr.flush(); "
    "data=sys.stdin.buffer.read(); print(len(data))",
    "import sys,time; time.sleep(.15); data=sys.stdin.buffer.read(); "
    "sys.stdout.buffer.write(b'o'*2097152); print(len(data)); sys.stderr.buffer.write(b'e'*1048576)",
], ids=['output-before-input', 'slow-input'])
def test_full_duplex_with_large_pipes(source):
    from aiforstudy.core.process_io import communicate_bounded
    proc = subprocess.Popen([sys.executable, '-c', source], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = communicate_bounded(proc, b'x'*2097152, deadline=time.monotonic()+3)
        assert out == b'o'*2097152 + b'2097152\n'
        assert err == b'e'*1048576
        assert proc.returncode == 0
        assert proc.stdin.closed
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(1)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()


def test_child_exit_during_write_is_reaped():
    from aiforstudy.core.process_io import communicate_bounded
    proc = subprocess.Popen([sys.executable, '-c', 'raise SystemExit(7)'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        out, err = communicate_bounded(proc, b'x'*2097152, deadline=time.monotonic()+2)
        assert (out, err) == (b'', b'')
        assert proc.returncode == 7
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(1)
        proc.stdin.close()
        proc.stdout.close()
