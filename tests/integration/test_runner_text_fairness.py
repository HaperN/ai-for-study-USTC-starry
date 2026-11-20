"""Real graph/Runner scheduling with controlled local stage durations; no API."""
import json
import threading

import pytest

from aiforstudy.pipeline.engine import build_engine, execute, resume_request
from aiforstudy.pipeline.stubs import smoke_registry
from test_m2_engine import make_workspace, request_for


def _engine(tmp_path, units, parallel, registry):
    workspace, book = make_workspace(tmp_path, units=units)
    path = workspace.config / 'execution.json'
    policy = json.loads(path.read_text())
    policy['profiles']['smoke']['parallelism'] = parallel
    path.write_text(json.dumps(policy))
    request = request_for(book.book_id, units, parallel=parallel)
    return build_engine(request, workspace, stage_registry=registry), request, workspace


def _started(store):
    return [row.node_id for row in store.events_after()
            if row.kind == 'stage.started']


def test_ready_review_precedes_next_draft_and_media_has_no_bookwide_barrier(tmp_path):
    units = ('CH01-S01', 'CH01-S02', 'CH02-S01')
    engine, _, _ = _engine(tmp_path, units, 1, smoke_registry())
    result, store = execute(engine)
    assert result.status == 'succeeded'
    starts = _started(store)
    assert starts.index('text_audit:CH01-S01') < starts.index('lesson:CH01-S02')
    assert starts.index('figures:CH01-S01') < starts.index('lesson:CH02-S01')
    assert starts.index('render:CH01') < starts.index('lesson:CH02-S01')


@pytest.mark.parametrize('parallel', [2, 4])
def test_slow_media_cannot_fill_every_slot_while_later_text_needs_review(tmp_path, parallel):
    units = tuple(f'CH01-S{n:02}' for n in range(1, parallel + 3))
    registry = smoke_registry()
    media_impl, review_impl = registry.get('figures'), registry.get('text_audit')
    media_entered, last_reviewed, release_media = (threading.Event() for _ in range(3))
    all_media_slots_busy = threading.Event()
    active_lock, active_media = threading.Lock(), 0
    box = {}

    def slow_media(ctx):
        nonlocal active_media
        with active_lock:
            active_media += 1
            media_entered.set()
            if active_media == parallel:
                all_media_slots_busy.set()
        try:
            assert release_media.wait(15), 'test cleanup must release the media workers'
            return media_impl(ctx)
        finally:
            with active_lock:
                active_media -= 1

    def review(ctx):
        result = review_impl(ctx)
        if ctx.node.scope_key == units[-1]:
            last_reviewed.set()
        return result

    registry.register('figures', slow_media)
    registry.register('text_audit', review)
    engine, _, _ = _engine(tmp_path, units, parallel, registry)
    worker = threading.Thread(target=lambda: box.update(zip(('result', 'store'), execute(engine))))
    worker.start()
    try:
        assert media_entered.wait(8)
        assert last_reviewed.wait(8), 'later text audit waited for long-running media'
        assert all_media_slots_busy.wait(5), 'finished text must return its slot to media'
        assert not release_media.is_set()
    finally:
        release_media.set()
        worker.join(15)
    assert not worker.is_alive()
    assert box['result'].status == 'succeeded'
    states = box['store'].node_states()
    assert all(states[f'figures:{unit}'].attempt == 1 for unit in units)
    assert all(states[f'text_audit:{unit}'].attempt == 1 for unit in units)


def test_first_chapter_renders_while_next_chapter_draft_is_still_running(tmp_path):
    units = ('CH01-S01', 'CH02-S01')
    registry = smoke_registry()
    lesson_impl, render_impl = registry.get('lesson'), registry.get('render')
    later_started, first_rendered, release_later = (threading.Event() for _ in range(3))
    box = {}

    def lesson(ctx):
        if ctx.node.scope_key == 'CH02-S01':
            later_started.set()
            assert release_later.wait(15)
        return lesson_impl(ctx)

    def render(ctx):
        result = render_impl(ctx)
        if ctx.node.scope_key == 'CH01':
            first_rendered.set()
        return result

    registry.register('lesson', lesson)
    registry.register('render', render)
    engine, _, _ = _engine(tmp_path, units, 2, registry)
    worker = threading.Thread(target=lambda: box.update(zip(('result', 'store'), execute(engine))))
    worker.start()
    try:
        assert later_started.wait(8)
        assert first_rendered.wait(8), 'first chapter was held behind later chapter text'
    finally:
        release_later.set()
        worker.join(15)
    assert not worker.is_alive()
    assert box['result'].status == 'succeeded'
    starts = _started(box['store'])
    assert starts.index('render:CH01') < starts.index('text_audit:CH02-S01')


def test_cancel_reserved_text_lane_then_resume_reuses_finished_text(tmp_path):
    units = ('CH01-S01', 'CH01-S02', 'CH02-S01')
    registry = smoke_registry()
    media_impl, review_impl = registry.get('figures'), registry.get('text_audit')
    entered, reviewed, release = (threading.Event() for _ in range(3))
    box = {}

    def media(ctx):
        entered.set()
        assert release.wait(15)
        return media_impl(ctx)

    def review(ctx):
        result = review_impl(ctx)
        if ctx.node.scope_key == units[-1]:
            reviewed.set()
        return result

    registry.register('figures', media)
    registry.register('text_audit', review)
    engine, request, workspace = _engine(tmp_path, units, 2, registry)
    worker = threading.Thread(target=lambda: box.update(zip(('result', 'store'), execute(engine))))
    worker.start()
    try:
        assert entered.wait(8)
        assert reviewed.wait(8)
        engine.services.cancel.cancel('cancel after text, before media completion')
    finally:
        release.set()
        worker.join(15)
    assert not worker.is_alive()
    assert box['result'].status == 'cancelled'
    result, store, _ = resume_request(request, box['store'].run_id, workspace,
                                     stage_registry=registry)
    assert result.status == 'succeeded'
    for unit in units:
        state = store.node_states()[f'text_audit:{unit}']
        assert state.execution_state.value == 'cached'
        assert state.attempt == 1
    assert all(row.execution_state.value in {'succeeded', 'cached'}
               for row in store.node_states().values())
