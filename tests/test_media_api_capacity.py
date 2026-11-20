"""Resource concurrency must leave an actual API slot available for prose."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from aiforstudy.errors import Cancelled
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy, ExecutionScope
from aiforstudy.pipeline.services import CancelToken


def bound_control(tmp_path, parallelism=4):
    control = ExecutionControl(ExecutionPolicy(parallelism=parallelism))
    control.bind(tmp_path/'execution.json', run_id='media-concurrency')
    return control


def acquire(control, name, stage='figures', cancel=None):
    return control.acquire_call(ExecutionScope('media-concurrency', 'U', stage, name),
                                fingerprint=name, cancel=cancel)


def test_fourth_media_waits_without_occupying_the_prose_api_slot(tmp_path):
    control = bound_control(tmp_path)
    held = [acquire(control, str(i)) for i in range(3)]
    started = threading.Event()
    def queued_media():
        started.set()
        return acquire(control, 'fourth')
    with ThreadPoolExecutor(max_workers=2) as pool:
        fourth = pool.submit(queued_media)
        body = None
        try:
            assert started.wait(1)
            body = pool.submit(acquire, control, 'body', 'text_audit')
            lease = body.result(timeout=1)
            try:
                assert not fourth.done(), 'media occupied the slot reserved for prose'
                assert len(control.snapshot()['attempts']) == 4
            finally:
                lease.finish()
        finally:
            for lease in held:
                lease.finish()
            fourth.result(timeout=2).finish()
            if body is not None and body.done() and not body.exception():
                body.result().finish()


def test_cancelled_queued_media_consumes_neither_slot_nor_attempt(tmp_path):
    control = bound_control(tmp_path)
    held = [acquire(control, str(i)) for i in range(3)]
    token = CancelToken()
    ready = threading.Event()
    def queued():
        ready.set()
        return acquire(control, 'cancelled', cancel=token)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(queued)
        try:
            assert ready.wait(1)
            token.cancel('test cancellation')
            with pytest.raises(Cancelled):
                future.result(timeout=1)
            assert len(control.snapshot()['attempts']) == 3
            acquire(control, 'body', 'lesson').finish()
        finally:
            for lease in held:
                lease.finish()
            if future.done() and not future.exception():
                future.result().finish()
    fresh = [acquire(control, 'fresh-'+str(i)) for i in range(3)]
    for lease in fresh:
        lease.finish()


def test_media_persistence_failure_returns_both_slots(tmp_path, monkeypatch):
    control = bound_control(tmp_path)
    original = control._persist
    def fail():
        raise OSError('test persistence failure')
    monkeypatch.setattr(control, '_persist', fail)
    with pytest.raises(OSError):
        acquire(control, 'failed')
    monkeypatch.setattr(control, '_persist', original)
    with ThreadPoolExecutor(max_workers=1) as pool:
        held = []
        try:
            for i in range(3):
                held.append(pool.submit(acquire, control, 'fresh-'+str(i)).result(timeout=1))
            held.append(pool.submit(acquire, control, 'body', 'lesson').result(timeout=1))
        finally:
            for lease in held:
                lease.finish()


def test_single_api_slot_still_allows_media(tmp_path):
    control = bound_control(tmp_path, parallelism=1)
    acquire(control, 'single').finish()
    assert control.snapshot()['attempts'][0]['state'] == 'completed'
