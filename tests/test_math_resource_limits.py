"""Real host/worker probes; disabling the RSS checkpoint must break these."""
import subprocess
import sys

import pytest
from test_math_verification import identity, v, verify, api


def test_real_identity_can_be_verified_on_host():
    check = verify(identity(v(), v())).checks[0]
    assert check.status == 'pass', (check.reason_code, check.reason, check.evidence)


def test_allocating_worker_is_stopped_and_reaped(monkeypatch):
    original = subprocess.Popen
    children = []
    code = '''
import json, sys, time
from aiforstudy.blocks.math import verify as module
def allocate(self):
    chunks=[]
    for _ in range(80):
        chunks.append(bytearray(8*1024*1024))
        time.sleep(.01)
    time.sleep(5)
    return {'status':'pass','reason_code':'bad','reason':'','method':'none','evidence':{}}
module._CAS.run=allocate
print(json.dumps(module._worker(json.loads(sys.stdin.read()))))
'''
    def spawn(args, **kwargs):
        if args[-1] == '--worker':
            child = original([sys.executable, '-c', code], **kwargs)
            children.append(child)
            return child
        return original(args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    check = verify(identity(v(), v()), limits=api().VerificationLimits(memory_mib=128)).checks[0]
    assert (check.status, check.reason_code) == ('unknown', 'memory_limit'), check.evidence
    assert children and all(child.poll() is not None for child in children)
    assert all(stream.closed for child in children for stream in (child.stdin, child.stdout, child.stderr))
    if sys.platform == 'darwin':
        assert check.evidence['peak_rss_bytes'] > 128*1024*1024


@pytest.mark.skipif(sys.platform != 'darwin', reason='Darwin host RSS enforcement')
def test_small_budget_stops_real_cas_import():
    check = verify(identity(v(), v()), limits=api().VerificationLimits(memory_mib=32)).checks[0]
    assert (check.status, check.reason_code) == ('unknown', 'memory_limit'), check.evidence


@pytest.mark.skipif(sys.platform != 'darwin', reason='Darwin host RSS enforcement')
@pytest.mark.parametrize('failure', ['timeout', 'invalid_output', 'missing'])
def test_monitor_failure_is_unknown_and_reaps_worker(monkeypatch, failure):
    original = subprocess.Popen
    children = []
    def spawn(args, **kwargs):
        child = original(args, **kwargs)
        if args[-1] == '--worker':
            children.append(child)
        return child
    def failed_census(*args, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('ps', .2)
        if failure == 'missing':
            raise FileNotFoundError('ps')
        return subprocess.CompletedProcess(args[0], 0, stdout='invalid census')
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    monkeypatch.setattr(subprocess, 'run', failed_census)
    check = verify(identity(v(), v())).checks[0]
    assert (check.status, check.reason_code) == ('unknown', 'resource_monitor_failure')
    assert children and all(child.poll() is not None for child in children)
    assert all(stream.closed for child in children for stream in (child.stdin, child.stdout, child.stderr))
