"""Offline stage tests for concurrent, independently bound media resources."""
from collections import Counter
from copy import deepcopy
import json
from threading import Barrier, Event, Lock, get_ident
from types import SimpleNamespace

import pytest

from aiforstudy.core.review_mode import current_review_mode, review_mode
from aiforstudy.errors import BudgetExceeded, Cancelled, ContractError
from aiforstudy.pipeline.services import CancelToken
from test_media_code_contracts import approved_case
from test_media_code_execution import configure_stage


def stage_case(tmp_path, monkeypatch, ids, *, parallelism=3):
    content, approval, request, _ = approved_case()
    stages, context = configure_stage(tmp_path, monkeypatch, content, approval, request, {})
    plan = deepcopy(stages._load(context, 'media_plan'))
    plan['requests'] = [{**deepcopy(request), 'request_id': rid, 'request_key': rid} for rid in ids]
    monkeypatch.setattr(stages, '_load', lambda *_: plan)
    monkeypatch.setattr(stages, '_client', lambda *_, **__: SimpleNamespace(resource_id=''))
    context.services.control.policy = SimpleNamespace(media_resource_parallelism=parallelism)
    context.services.cancel = CancelToken()
    emits = []
    original_emit = stages._emit

    def emit(*args, **kwargs):
        emits.append(get_ident())
        return original_emit(*args, **kwargs)

    monkeypatch.setattr(stages, '_emit', emit)
    return stages, context, emits


def resource_row(request, out_dir):
    return {'request_id': request['request_id'], 'kind': request['kind'],
            'request': deepcopy(request), 'state': 'fulfilled', 'source_dir': str(out_dir),
            'resources': [{'kind': 'image', 'relative_path': 'figure.png', 'sha256': 'offline'}],
            'validation': {'ok': True, 'decision': 'accepted', 'issues': [], 'verdicts': []}}


def manifest(stages, context):
    return json.loads(stages._path(context, 'figures').read_text())['payload']


@pytest.mark.parametrize('mode', ['balanced', 'json_only'])
def test_three_resources_start_together_and_emit_once_in_plan_order(tmp_path, monkeypatch, mode):
    from aiforstudy.blocks.media import code_execution
    stages, context, emits = stage_case(tmp_path, monkeypatch, ['A', 'B', 'C'])
    barrier = Barrier(3, timeout=2)
    c_finished, b_finished = Event(), Event()
    observations = {}
    finished = []

    def execute(request, *, client, reviewer, out_dir, **kwargs):
        rid = request['request_id']
        observations[rid] = (current_review_mode(), client, reviewer, out_dir, kwargs['cancel'])
        barrier.wait()
        if rid == 'A':
            assert b_finished.wait(2)
        elif rid == 'B':
            assert c_finished.wait(2)
        finished.append(rid)
        if rid == 'C': c_finished.set()
        if rid == 'B': b_finished.set()
        return resource_row(request, out_dir)

    monkeypatch.setattr(code_execution, 'execute_code', execute)
    main_thread = get_ident()
    with review_mode(mode):
        result = stages.figures_stage(context)
    assert result.execution_state.value == 'succeeded', result.error
    assert finished == ['C', 'B', 'A']
    assert [row['request_id'] for row in manifest(stages, context)['requests']] == ['A', 'B', 'C']
    assert emits == [main_thread]
    assert len({id(item[1]) for item in observations.values()}) == 3
    for rid, (observed_mode, client, reviewer, out_dir, cancel) in observations.items():
        assert observed_mode == mode
        assert client.resource_id == rid
        assert (reviewer.resource_id == rid) if mode == 'balanced' else reviewer is None
        assert out_dir == tmp_path / 'media' / 'U' / rid
        assert cancel is context.services.cancel
    if mode == 'balanced':
        assert len({id(item[2]) for item in observations.values()}) == 3


def test_duplicate_resource_identity_executes_once(tmp_path, monkeypatch):
    from aiforstudy.blocks.media import code_execution
    stages, context, _ = stage_case(tmp_path, monkeypatch, ['A', 'B', 'A', 'C'])
    barrier = Barrier(3, timeout=2)
    calls = Counter()
    lock = Lock()

    def execute(request, *, out_dir, **kwargs):
        with lock: calls[request['request_id']] += 1
        barrier.wait()
        return resource_row(request, out_dir)

    monkeypatch.setattr(code_execution, 'execute_code', execute)
    with review_mode('balanced'):
        result = stages.figures_stage(context)
    assert result.execution_state.value == 'succeeded', result.error
    assert calls == Counter(A=1, B=1, C=1)
    assert [row['request_id'] for row in manifest(stages, context)['requests']] == ['A', 'B', 'A', 'C']


def test_repeated_identity_uses_the_last_plan_specification(tmp_path, monkeypatch):
    from aiforstudy.blocks.media import code_execution
    stages, context, _ = stage_case(tmp_path, monkeypatch, ['A', 'B', 'A'])
    requests = stages._load(context, 'media_plan')['requests']
    requests[0]['caption'] = 'earlier specification'
    requests[2]['caption'] = 'latest specification'
    executed = []

    def execute(request, *, out_dir, **kwargs):
        executed.append(deepcopy(request))
        return resource_row(request, out_dir)

    monkeypatch.setattr(code_execution, 'execute_code', execute)
    with review_mode('balanced'):
        result = stages.figures_stage(context)
    assert result.execution_state.value == 'succeeded', result.error
    assert [row['caption'] for row in executed if row['request_id'] == 'A'] == ['latest specification']
    assert all(row['request']['caption'] == 'latest specification'
               for row in manifest(stages, context)['requests'] if row['request_id'] == 'A')


def test_finished_resource_fills_a_slot_before_earlier_resource_finishes(tmp_path, monkeypatch):
    from aiforstudy.blocks.media import code_execution
    stages, context, _ = stage_case(tmp_path, monkeypatch, ['A', 'B', 'C', 'D'], parallelism=2)
    first_pair = Barrier(2, timeout=2)
    c_started = Event()
    lock = Lock()
    active = peak = 0

    def execute(request, *, out_dir, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            rid = request['request_id']
            if rid in ('A', 'B'): first_pair.wait()
            if rid == 'A': assert c_started.wait(2)
            if rid == 'C': c_started.set()
            return resource_row(request, out_dir)
        finally:
            with lock: active -= 1

    monkeypatch.setattr(code_execution, 'execute_code', execute)
    with review_mode('balanced'):
        result = stages.figures_stage(context)
    assert result.execution_state.value == 'succeeded', result.error
    assert peak == 2
    assert c_started.is_set()


@pytest.mark.parametrize('error_type', [Cancelled, BudgetExceeded])
def test_control_error_stops_pending_resources_without_emitting_manifest(tmp_path, monkeypatch, error_type):
    from aiforstudy.blocks.media import code_execution
    stages, context, emits = stage_case(tmp_path, monkeypatch, ['A', 'B', 'C', 'D', 'E'])
    barrier = Barrier(3, timeout=2)
    started = set()
    lock = Lock()

    def execute(request, *, out_dir, **kwargs):
        with lock: started.add(request['request_id'])
        barrier.wait()
        raise error_type('stop media work')

    monkeypatch.setattr(code_execution, 'execute_code', execute)
    with review_mode('balanced'), pytest.raises(error_type, match='stop media work'):
        stages.figures_stage(context)
    assert started == {'A', 'B', 'C'}
    assert emits == []
    assert not stages._path(context, 'figures').exists()


def test_resource_error_keeps_other_resources_and_original_plan_order(tmp_path, monkeypatch):
    from aiforstudy.blocks.media import code_execution
    stages, context, emits = stage_case(tmp_path, monkeypatch, ['A', 'B', 'C'])
    barrier = Barrier(3, timeout=2)

    def execute(request, *, out_dir, **kwargs):
        barrier.wait()
        if request['request_id'] == 'B': raise RuntimeError('offline renderer failed')
        return resource_row(request, out_dir)

    monkeypatch.setattr(code_execution, 'execute_code', execute)
    with review_mode('balanced'):
        result = stages.figures_stage(context)
    # 用户 2026-10-20 定放宽：个别媒体失败只记为缺失，媒体阶段不再整体判阻塞。
    assert result.execution_state.value == 'succeeded' and result.quality_state.value == 'needs_review'
    assert [f['request_id'] for f in result.findings if f.get('code') == 'MEDIA_MISSING'] == ['B']
    rows = manifest(stages, context)['requests']
    assert [row['request_id'] for row in rows] == ['A', 'B', 'C']
    assert [row['state'] for row in rows] == ['fulfilled', 'needs_review', 'fulfilled']
    assert 'offline renderer failed' in rows[1]['reason']
    assert emits == [get_ident()]


def test_parallel_resources_retain_separate_three_attempt_budgets_on_resume(tmp_path, monkeypatch):
    from aiforstudy.blocks.media import code_generation, code_runtime
    stages, context, _ = stage_case(tmp_path, monkeypatch, ['A', 'B', 'C'])
    first_attempts = Barrier(3, timeout=2)
    calls = Counter()
    lock = Lock()

    def unusable(request, *, client, **kwargs):
        rid = request['request_id']
        assert client.resource_id == rid
        with lock:
            calls[rid] += 1
            attempt = calls[rid]
        if attempt == 1: first_attempts.wait()
        raise ContractError('unusable offline candidate')

    monkeypatch.setattr(code_generation, 'generate_media_code', unusable)
    monkeypatch.setattr(code_runtime, 'preflight_generated_code', lambda *_, **__: {'ok': True})
    with review_mode('balanced'):
        initial = stages.figures_stage(context)
        resumed = stages.figures_stage(context)
    assert initial.execution_state.value == resumed.execution_state.value == 'succeeded'  # 放宽：只记缺失
    assert calls == Counter(A=3, B=3, C=3)
    for rid in calls:
        saved = json.loads((tmp_path / 'media' / 'U' / rid / 'result.json').read_text())
        assert saved['request_id'] == rid
        assert [attempt['index'] for attempt in saved['code_attempts']] == [1, 2, 3]
    assert all(len(row['code_attempts']) == 3 for row in manifest(stages, context)['requests'])


def test_parallel_prose_findings_use_one_existing_lesson_repair_route(tmp_path, monkeypatch):
    from aiforstudy.blocks.media import code_execution
    stages, context, emits = stage_case(tmp_path, monkeypatch, ['A', 'B', 'C'])
    barrier = Barrier(3, timeout=2)

    def execute(request, *, out_dir, **kwargs):
        barrier.wait()
        row = resource_row(request, out_dir)
        row['validation']['review_problems'] = [{'target_artifact_kind': 'content',
            'status': 'fail', 'reason': '请补充正文条件：'+request['request_id']}]
        return row

    monkeypatch.setattr(code_execution, 'execute_code', execute)
    with review_mode('balanced'):
        result = stages.figures_stage(context)
    assert result.execution_state.value == 'succeeded', result.error
    assert len(result.repair_requests) == len(result.repair_routes) == 1
    assert result.repair_requests[0].owner == 'lesson'
    payload = manifest(stages, context)
    assert len(payload['tickets']) == 1
    assert payload['tickets'][0]['problem'] == '；'.join('请补充正文条件：'+rid for rid in ['A', 'B', 'C'])
    assert all(row['review_outcome']['status'] == 'verified' for row in payload['requests'])
    assert emits == [get_ident()]
