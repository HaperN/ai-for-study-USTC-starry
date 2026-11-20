"""The real-run entry and release policy must agree on actual model capacity."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
import time

import pytest

from aiforstudy.pipeline.engine import policy_from
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionScope
from support.preparation_book import _mini_book
from tools.real_run import build_parser, _request


def delivery_request(workspace, *extra):
    args = build_parser().parse_args([
        '--book', 'mini-book', '--units', 'CH01-S01', *extra])
    return _request(args, workspace)[0]


@pytest.mark.parametrize('arguments,requested,effective', [
    ([], 32, 18), (['--parallel', '1'], 1, 1),
    (['--parallel', '4'], 4, 4), (['--parallel', '32'], 32, 18),
])
def test_new_delivery_request_and_release_capacity_agree(tmp_path, arguments, requested, effective):
    workspace = _mini_book(tmp_path)
    request = delivery_request(workspace, *arguments)
    policy = policy_from(request, workspace)
    assert request.parallelism == requested
    assert policy.parallelism == effective
    assert policy.render['max_concurrent_workers'] == 6
    assert policy.media_resource_parallelism == 4
    assert policy.content_repair_rounds_per_object == 2
    # The text/media split never exceeds the requested total.
    assert policy.content_slots == min(6, effective)
    assert policy.media_api_slots == min(12, effective)


def test_prepare_default_and_saved_resume_request_keep_their_parallelism(tmp_path):
    workspace = _mini_book(tmp_path)
    args = build_parser().parse_args(['--book', 'mini-book', '--phase', 'prepare'])
    assert _request(args, workspace)[0].parallelism == 1
    saved = delivery_request(workspace, '--parallel', '2').as_dict()
    before = deepcopy(saved)
    root = workspace.root/'data/books/mini-book/runs/run-existing'
    root.mkdir(parents=True)
    (root/'request.json').write_text(json.dumps(saved))
    args = build_parser().parse_args([
        '--book', 'mini-book', '--phase', 'resume', '--run-id', 'run-existing'])
    resumed, _ = _request(args, workspace)
    assert resumed.parallelism == 2
    assert json.loads((root/'request.json').read_text()) == before


def _admit(control, stage, admitted):
    """Start admitted+1 calls on one stage; return (peak, entered-before-release)."""
    lock = threading.Lock()
    full, extra_waiting, release = threading.Event(), threading.Event(), threading.Event()
    entered, state = [], {'active': 0, 'peak': 0}

    def call(index):
        if index == admitted:
            extra_waiting.set()
        attempt = control.acquire_call(ExecutionScope('run-split', f'U{index}', stage),
                                       fingerprint=f'{stage}-{index}')
        try:
            with lock:
                entered.append(index)
                state['active'] += 1
                state['peak'] = max(state['peak'], state['active'])
                if state['active'] == admitted:
                    full.set()
            assert release.wait(5), 'test did not release model calls'
        finally:
            with lock:
                state['active'] -= 1
            attempt.finish()

    with ThreadPoolExecutor(max_workers=admitted + 1) as pool:
        futures = [pool.submit(call, index) for index in range(admitted)]
        try:
            assert full.wait(2), f'{stage} capacity below {admitted}'
            futures.append(pool.submit(call, admitted))
            assert extra_waiting.wait(2)
            time.sleep(.2)
            before = len(entered)
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)
    return state['peak'], before


@pytest.mark.parametrize('stage,admitted', [('lesson', 6), ('text_audit', 6), ('figures', 12)])
def test_release_control_splits_text_and_media_lanes(tmp_path, stage, admitted):
    workspace = _mini_book(tmp_path)
    control = ExecutionControl(policy_from(delivery_request(workspace), workspace))
    control.bind(tmp_path/'execution.json', run_id='run-split')
    peak, before_release = _admit(control, stage, admitted)
    assert peak == admitted and before_release == admitted


def test_unset_split_keeps_old_policy_digest_and_slots():
    from aiforstudy.pipeline.execution_control import ExecutionPolicy
    policy = ExecutionPolicy(parallelism=4)
    assert 'content_parallelism' not in policy.as_dict()
    assert 'media_api_parallelism' not in policy.as_dict()
    assert (policy.content_slots, policy.media_api_slots) == (4, 3)
