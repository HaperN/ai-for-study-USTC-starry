"""Killable HTTP exchange for MinerU; headers/credentials only cross stdin."""
import json
import sys
import subprocess
import time


def exchange(url, *, method='GET', body=None, headers=None, timeout=60, checkpoint=None):
    """``timeout=None`` allows unlimited response waiting, with cancellation."""
    from aiforstudy.core.process_io import communicate_bounded
    from urllib.parse import urlsplit
    if urlsplit(url).scheme not in ('http', 'https'):
        raise ValueError('HTTP(S) URL required')
    sys.audit('urllib.Request', url, None, {}, method)
    if checkpoint:
        checkpoint()
    deadline = None if timeout is None else time.monotonic() + timeout
    header = json.dumps(dict(url=url, method=method, headers=headers or {},
                             timeout=15 if timeout is None else min(timeout, 15),
                             read_timeout=timeout, body_present=body is not None)).encode() + b'\n'
    process = subprocess.Popen([sys.executable, __file__], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        output, _ = communicate_bounded(process, header + (body or b''),
            deadline=deadline, checkpoint=checkpoint)
        if process.returncode:
            raise OSError('HTTP worker exited; remote status unknown')
        status, raw = output.split(b'\n', 1)
        status = int(status)
        if status == 0:
            raise OSError('HTTP connection or body read failed; remote status unknown')
        return status, raw
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=1)
        for stream in (process.stdin, process.stdout):
            if stream:
                stream.close()


def main():
    import http.client
    from urllib.parse import urlsplit
    request = json.loads(sys.stdin.buffer.readline())
    body = sys.stdin.buffer.read()
    parts = urlsplit(request['url'])
    connection = (http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection)(
        parts.hostname, parts.port, timeout=request['timeout'])
    try:
        connection.connect()
        connection.sock.settimeout(request['read_timeout'])
        path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
        connection.request(request['method'], path, body=body if request['body_present'] else None,
                           headers=request['headers'])
        response = connection.getresponse()
        raw = response.read()
        sys.stdout.buffer.write(str(response.status).encode() + b'\n' + raw)
    except Exception:
        sys.stdout.buffer.write(b'0\n')
    finally:
        connection.close()


if __name__ == '__main__':
    main()
