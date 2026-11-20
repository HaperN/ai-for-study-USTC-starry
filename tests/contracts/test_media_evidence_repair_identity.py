"""Evidence repair is bounded per rendered candidate, not for all future code."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from aiforstudy.blocks.audit.contracts import make_ticket_id, target_location_key
from aiforstudy.core.digest import digest_of
from aiforstudy.pipeline import content_stages, media_stages
from aiforstudy.pipeline.repair import RepairBatch, RepairCoordinator, StopDecision, UnitRepairState
from content_case import teaching_case


def reviewed_media_case(kind, unit_id='U'):
    """Current author intent with an explicit offline semantic verdict."""
    from aiforstudy.blocks.content.content_policy import load_content_policy
    from aiforstudy.blocks.content.review_outcome import content_identity
    from aiforstudy.blocks.media.media_planning import plan_media_requirements
    from content_case import author_media, reviewed_plan
    from test_media_decisions_v2 import math_request_case

    content, _, raw, _ = math_request_case('animation' if kind == 'animation' else 'geometry')
    _, source = teaching_case()
    content['unit_id'] = source['unit_id'] = unit_id
    raw['requests'][0]['placement']['owner_id'] = unit_id
    approval = content_identity(content, source['reference_catalog'],
        policy_digest=load_content_policy(source['learner_profile']).digest)
    capabilities = {'code_generation':'GeneratedMediaCodeV1', 'kinds':['drawing','animation']}
    plan = plan_media_requirements(content, approval, capabilities=capabilities,
        author_requirements=author_media(content, raw))
    return content, source, approval, reviewed_plan(content, plan), capabilities


@pytest.fixture
def routing(monkeypatch):
    content, source = teaching_case()
    ctx = SimpleNamespace(node=SimpleNamespace(scope_key=content['unit_id'], stage='figures'))
    monkeypatch.setattr(content_stages, '_frozen_context', lambda ctx: source)
    known = {}
    monkeypatch.setattr(media_stages, '_persisted_tickets', lambda *args, **kwargs: known)
    row = {'request_id': 'MR-animation', 'kind': 'animation', 'state': 'needs_review',
           'candidate_spec': {'schema_version': 'GeneratedMediaCodeV1', 'code': 'candidate one'},
           'spec_hash': digest_of('candidate one'), 'render_profile': 'draft',
           'resources': [{'kind': 'mp4', 'relative_path': 'clip.mp4', 'sha256': digest_of('draft one')}],
           'validation': {'issues': [{'code': 'MEDIA_REVIEW_EVIDENCE', 'message': '缺 narrow 真实帧'}],
                          'verdicts': [{'criterion_id': 'host.media.readability', 'version': '1', 'status': 'pass'}]}}

    def tickets(candidate):
        emitted, directives, routes = media_stages._tickets(ctx, content, [candidate])
        known.update({t['ticket_id']: t for t in emitted})
        return emitted, directives, routes

    return row, tickets, RepairCoordinator(SimpleNamespace()), UnitRepairState(content['unit_id'])


@pytest.mark.parametrize('change', ['new_code', 'final_pixels'])
def test_new_real_media_candidate_can_repair_evidence_without_resetting_prior_budget(routing, change):
    row, route, coordinator, state = routing
    old, _, _ = route(row)
    old_id = old[0]['ticket_id']
    state.evidence_attempts_by_verdict[old_id] = 1
    revised = deepcopy(row)
    revised['repair_tickets'] = old
    if change == 'new_code':
        revised['candidate_spec']['code'] = 'candidate two'
        revised['spec_hash'] = digest_of('candidate two')
    else:
        revised['render_profile'] = 'final'
    revised['resources'][0]['sha256'] = digest_of(change)
    current, directives, routes = route(revised)
    batch = coordinator.plan(list(directives), state)
    assert isinstance(batch, RepairBatch), 'new real candidate must not inherit the old verdict repair exhaustion'
    assert all(job.kind == 'evidence_format' for job in batch.jobs)
    assert all(r['target_artifact_kind'] == 'media_evidence' for r in routes)
    assert old_id not in {t for job in batch.jobs for t in job.ticket_ids}
    assert current[0]['criteria'] == old[0]['criteria']
    assert state.evidence_attempts_by_verdict == {old_id: 1}

    new_id = current[0]['ticket_id']
    state.evidence_attempts_by_verdict[new_id] = 1
    revised['repair_tickets'] += current
    repeated, directives, _ = route(revised)
    stopped = coordinator.plan(list(directives), state)
    assert isinstance(stopped, StopDecision) and stopped.stopped_by == 'evidence_attempts_exhausted'
    assert {t['ticket_id'] for t in repeated} == {new_id}
    assert state.evidence_attempts_by_verdict == {old_id: 1, new_id: 1}


def test_changed_review_wording_or_telemetry_cannot_buy_another_repair(routing):
    row, route, coordinator, state = routing
    old, _, _ = route(row)
    state.evidence_attempts_by_verdict[old[0]['ticket_id']] = 1
    row['repair_tickets'] = old
    row['validation']['issues'][0]['message'] = '同一画面的引用换了措辞'
    row['source_dir'] = '/moved/archive'
    row['code_attempts'] = [{'wall_seconds': 99, 'directory': '/new/telemetry/path'}]
    current, directives, _ = route(row)
    assert {t['ticket_id'] for t in current} == {old[0]['ticket_id']}
    assert coordinator.plan(list(directives), state).stopped_by == 'evidence_attempts_exhausted'


@pytest.mark.parametrize('change', ['stale_hash', 'semantic_fail', 'needs_review', 'content_problem'])
def test_deferred_protocol_resume_requires_current_receipt_and_no_semantic_failure(routing, change):
    from aiforstudy.blocks.media.validation import result_digest
    row, _, _, _ = routing
    row['validation'].update(ok=False, result_hash=result_digest(row))
    assert media_stages._pending_protocol_review(row)
    if change == 'stale_hash': row['validation']['result_hash'] = 'sha256:stale'
    elif change == 'content_problem': row['validation']['review_problems'] = [{'target_artifact_kind': 'content'}]
    else: row['validation']['verdicts'][0]['status'] = 'fail' if change == 'semantic_fail' else 'needs_review'
    assert not media_stages._pending_protocol_review(row)


@pytest.mark.parametrize('new_code', [False, True])
def test_legacy_ticket_budget_is_preserved_for_same_code_but_not_transferred_to_new_code(routing, new_code):
    row, route, coordinator, state = routing
    tickets, _, _ = route(row)
    legacy = deepcopy(tickets[0])
    legacy['target']['step_id'] = 'MEDIA_REVIEW_EVIDENCE'
    legacy['previous_attempt'].pop('media_evidence_basis', None)
    legacy['ticket_id'] = make_ticket_id(rule=legacy['target']['rule'], unit_id=legacy['unit_id'],
        artifact_id=legacy['target']['artifact_id'], location=target_location_key(legacy['target'])[0])
    state.evidence_attempts_by_verdict[legacy['ticket_id']] = 1
    row['repair_tickets'] = [legacy]
    if new_code:
        row['spec_hash'] = digest_of('actual revised source')
        row['candidate_spec']['code'] = 'actual revised source'
        row['resources'][0]['sha256'] = digest_of('actual revised pixels')
    _, directives, _ = route(row)
    result = coordinator.plan(list(directives), state)
    assert isinstance(result, RepairBatch if new_code else StopDecision)
    assert state.evidence_attempts_by_verdict == {legacy['ticket_id']: 1}


@pytest.mark.parametrize(('kind', 'state', 'profile'), [('drawing', 'fulfilled', 'final'), ('animation', 'needs_review', 'draft')])
@pytest.mark.parametrize('damage', ['none', 'missing', 'corrupt', 'unchanged_policy'])
def test_review_dependency_change_reuses_intact_media_without_trusting_old_acceptance(tmp_path, monkeypatch, kind, state, profile, damage):
    import json
    from aiforstudy.blocks.media import code_execution, validation
    from aiforstudy.blocks.media.math_context import ApprovedMathContext
    from aiforstudy.core.digest import digest_file
    from aiforstudy.core.jsonio import write_json_atomic
    from aiforstudy.errors import Cancelled

    ctx = SimpleNamespace(node=SimpleNamespace(stage='figures', scope_key='U', node_id='figures:U'),
        context=SimpleNamespace(book_id='B',revision='source'), workspace=SimpleNamespace(root=tmp_path),
        services=SimpleNamespace(store=SimpleNamespace(work=tmp_path),
            cancel=SimpleNamespace(checkpoint=lambda: None), control=SimpleNamespace(check=lambda: None)),
        repair_batch=None)
    content, source, approval, plan, capabilities = reviewed_media_case(kind)
    request = plan['requests'][0]
    math_context = ApprovedMathContext.from_content_binding(content, approval, request)
    resource = tmp_path / 'frozen.png'
    resource.write_bytes(b'actual frozen bytes')
    old = {'request_id': request['request_id'], 'kind': kind, 'state': state,
        'request_hash': digest_of(request), 'math_context_digest': math_context.digest,
        'generation_dependency_hash': 'same-generation', 'review_dependency_hash': 'old-review',
        'render_profile': profile, 'source_dir': str(tmp_path),
        'resources': [{'kind': 'image', 'relative_path': resource.name, 'sha256': digest_file(resource)}],
        'validation': {'ok': state == 'fulfilled'}}
    write_json_atomic(media_stages._path(ctx, 'figures'), {'payload': {'requests': [old]}})
    if damage == 'missing': resource.unlink()
    elif damage == 'corrupt': resource.write_bytes(b'changed after acceptance')
    monkeypatch.setattr(media_stages, '_load_content', lambda ctx: (content, approval))
    monkeypatch.setattr(media_stages, '_load', lambda *args: plan)
    monkeypatch.setattr(media_stages, '_capabilities', lambda ctx: capabilities)
    monkeypatch.setattr(media_stages, '_client', lambda *args: object())
    monkeypatch.setattr(media_stages, '_persisted_tickets', lambda *args: {})
    monkeypatch.setattr(content_stages, '_frozen_context', lambda ctx: source)
    monkeypatch.setattr(validation, 'display_runtime', lambda *args: None)
    monkeypatch.setattr(media_stages, 'resource_dependencies', lambda *args: {
        'generation_dependency_hash': 'same-generation',
        'review_dependency_hash': 'old-review' if damage == 'unchanged_policy' else 'new-review'})
    # A changed review policy invalidates even an old accepted receipt.
    monkeypatch.setattr(media_stages, 'validate_manifest', lambda *args: {'ok': False})
    selected = []

    def stop_before_model_or_renderer(request, **kwargs):
        selected.append(kwargs)
        raise Cancelled('resume dispatch captured offline')

    monkeypatch.setattr(code_execution, 'execute_code', stop_before_model_or_renderer)
    if damage in ('none', 'unchanged_policy'):
        with pytest.raises(Cancelled, match='resume dispatch captured offline'):
            media_stages.figures_stage(ctx)
        assert len(selected) == 1 and selected[0]['evidence_only'] is (damage == 'none')
        assert selected[0]['previous'] == old
    else:
        outcome = media_stages.figures_stage(ctx)
        assert outcome.execution_state.value == 'succeeded' and selected == []
        assert outcome.quality_state.value == 'needs_review'
        current=json.loads(media_stages._path(ctx, 'figures').read_text())['payload']['requests'][0]
        assert current['review_outcome']['status']=='missing'
        assert '冻结媒体资源缺失或字节损坏' in current['reason']
        assert current['resources']==old['resources'] and current['source_dir']==old['source_dir']
        assert current['generation_dependency_hash']==old['generation_dependency_hash']
    if damage in ('none', 'unchanged_policy'):
        assert json.loads(media_stages._path(ctx, 'figures').read_text())['payload']['requests'] == [old]


@pytest.mark.parametrize('spent_evidence_attempt', [False, True])
def test_real_runner_resume_routes_unchanged_protocol_failure_before_any_media_call(tmp_path, monkeypatch, spent_evidence_attempt):
    import json
    from aiforstudy.blocks.media import code_generation, code_runtime, validation
    from aiforstudy.blocks.media.math_context import ApprovedMathContext
    from aiforstudy.blocks.media.review_policy import media_review_policy
    from aiforstudy.core.digest import digest_file
    from aiforstudy.core.jsonio import write_json_atomic
    from aiforstudy.errors import ContractError
    from aiforstudy.pipeline.engine import run_request, resume_request
    from aiforstudy.pipeline.stubs import smoke_registry
    from runner_v5 import setup_book, _request, UNIT

    workspace = setup_book(tmp_path)
    registry = smoke_registry()
    first_stage_calls = []
    calls = {'generation': 0, 'judge': 0}

    def figures(ctx):
        before = deepcopy(calls)
        result = media_stages.figures_stage(ctx)
        if ctx.repair_batch is None: first_stage_calls.append((before, deepcopy(calls)))
        return result

    registry.register('figures', figures)
    _, store, engine = run_request(_request(), workspace, stage_registry=registry, until='media_plan')
    content, source, approval, plan, capabilities = reviewed_media_case('drawing', UNIT)
    request = plan['requests'][0]
    math_context = ApprovedMathContext.from_content_binding(content, approval, request)
    dependencies = {'generation_dependency_hash': 'same-generation', 'review_dependency_hash': 'same-review'}
    resource_dir = store.work / 'frozen-media'
    resource_dir.mkdir()
    image = resource_dir / 'figure.png'
    image.write_bytes(b'frozen actual resource bytes')
    program = {'schema_version': 'GeneratedMediaCodeV1', 'request_id': request['request_id'],
        'backend': 'matplotlib', 'code': 'def build_figure(context):\n    return None\n', 'object_bindings': [], 'steps': []}
    from aiforstudy.blocks.media.code_execution import _bound_row
    old = {**_bound_row(request, content, approval, math_context),
        'request_id': request['request_id'], 'kind': 'drawing', 'request': request,
        'request_hash': digest_of(request), 'criteria_digest': request['criteria_digest'],
        'math_context_digest': math_context.digest, 'approved_reader_document_hash': approval['reader_document_hash'],
        'state': 'needs_review', 'reason': '缺完整真实帧引用',
        'candidate_spec': program, 'spec_hash': digest_of(program), 'render_profile': 'final',
        'code_product': {'schema_version': 'GeneratedMediaProductV1'}, 'repair_tickets': [],
        'code_attempts': [{'index': 1}], 'source_dir': str(resource_dir),
        'resources': [{'kind': 'image', 'relative_path': image.name, 'sha256': digest_file(image)}],
        **dependencies}
    old['code_generation_identity'] = digest_of({'request': request, 'context': math_context.digest,
        'generation_dependency_hash': dependencies['generation_dependency_hash'],
        'contract': 'GeneratedMediaCodeV1', 'attempt_limit': 3})

    def incomplete_report(row):
        items = validation.media_review_items(request, row, media_review_policy())
        return {'ok': False, 'decision': 'needs_review', 'result_hash': validation.result_digest(row),
            'policy_digest':digest_of(media_review_policy()),
            'issues': [{'code': 'MEDIA_REVIEW_EVIDENCE', 'message': '缺完整真实帧引用'}],
            'review_items': items, 'review_problems': [], 'evidence_catalog': {},
            'verdicts': [{'criterion_id': i['criterion_id'], 'version': i['version'], 'status': 'pass'} for i in items]}

    old['validation'] = incomplete_report(old)
    monkeypatch.setattr(media_stages, '_load_content', lambda ctx: (content, approval))
    monkeypatch.setattr(media_stages, '_load', lambda *args: plan)
    monkeypatch.setattr(content_stages, '_frozen_context', lambda ctx: source)
    monkeypatch.setattr(media_stages, '_capabilities', lambda ctx: capabilities)
    monkeypatch.setattr(media_stages, '_client', lambda *args: object())
    monkeypatch.setattr(media_stages, 'resource_dependencies', lambda *args: dependencies)
    monkeypatch.setattr(validation, 'display_runtime', lambda *args: None)

    def generate(*args, **kwargs):
        calls['generation'] += 1
        raise ContractError('unexpected generation on protocol-only resume')

    def review(request, row, **kwargs):
        calls['judge'] += 1
        return incomplete_report(row)

    monkeypatch.setattr(code_generation, 'generate_media_code', generate)
    monkeypatch.setattr(code_runtime, 'preflight_generated_code', lambda *args, **kwargs: {'ok': True})
    monkeypatch.setattr(validation, 'validate_media', review)
    ctx = SimpleNamespace(node=SimpleNamespace(scope_key=UNIT, stage='figures'),
        services=SimpleNamespace(store=store), workspace=workspace)
    tickets, _, _ = media_stages._tickets(ctx, content, [old])
    old['repair_tickets'] = tickets
    old['validation'] = incomplete_report(old)
    write_json_atomic(media_stages._path(ctx, 'figures'), {'payload': {'requests': [old], 'tickets': tickets}})
    state = store.load_state()
    state['repair_states'] = {UNIT: UnitRepairState(UNIT,
        evidence_attempts_by_verdict={tickets[0]['ticket_id']: 1} if spent_evidence_attempt else {}).as_dict()}
    store.save_state(state)
    result, resumed, _ = resume_request(engine.request, store.run_id, workspace,
        stage_registry=registry, until='figures')
    assert result.status == 'needs_review'
    assert first_stage_calls == [({'generation': 0, 'judge': 0}, {'generation': 0, 'judge': 0})]
    assert calls == {'generation': 0, 'judge': 0 if spent_evidence_attempt else 1}
    assert resumed.load_state()['repair_states'][UNIT]['evidence_attempts_by_verdict'] == {tickets[0]['ticket_id']: 1}
    assert len(json.loads(media_stages._path(ctx, 'figures').read_text())['payload']['requests'][0]['code_attempts']) == 1


@pytest.fixture
def legacy_final_history(tmp_path, monkeypatch):
    """Real receipt/file verification; only the semantic reviewer is offline."""
    import shutil
    import subprocess
    from aiforstudy.blocks.media import code_execution, validation
    from aiforstudy.core.digest import digest_file
    from aiforstudy.core.jsonio import write_json_atomic
    from test_media_code_validation import code_case, resource, Reviewer

    content, source = teaching_case()
    ctx = SimpleNamespace(node=SimpleNamespace(scope_key=content['unit_id'], stage='figures'),
        services=SimpleNamespace(store=SimpleNamespace(work=tmp_path)))
    monkeypatch.setattr(content_stages, '_frozen_context', lambda ctx: source)
    request, base = code_case(tmp_path, animation=True)
    base.update(request=request, generation_dependency_hash=digest_of('same generator'), code_attempts=[{'index': 1}])
    base['code_generation_identity'] = digest_of({'request': request, 'context': base['math_context_digest'],
        'generation_dependency_hash': base['generation_dependency_hash'], 'contract': 'GeneratedMediaCodeV1', 'attempt_limit': 3})
    attempt = tmp_path/'media'/content['unit_id']/request['request_id']/('generation-'+base['code_generation_identity'][7:23])/'attempt-01'
    draft_dir = attempt/'draft'
    draft_dir.mkdir(parents=True)
    video = draft_dir/'video.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=white:s=640x360:r=15',
        '-t', '2', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True, capture_output=True, timeout=30)
    steps = [{**s, 'kind': 'change', 'start': i, 'end': i+1} for i,s in enumerate(base['candidate_spec']['steps'])]
    captions = draft_dir/'captions.vtt'
    captions.write_text('WEBVTT\n\n'+'\n\n'.join(f"{s['step_id']}\n00:00:0{s['start']}.000 --> 00:00:0{s['end']}.000\n{s['caption']}" for s in steps)+'\n')
    transcript = draft_dir/'transcript.txt'
    transcript.write_text('\n'.join(s['caption'] for s in steps))
    resources = [resource(video, 'mp4'), resource(captions, 'captions'), resource(transcript, 'transcript')]
    for i,step in enumerate(steps):
        for phase,at in [('before',i+.1),('observe',i+.5),('after',i+.9)]:
            frame = draft_dir/f"{step['step_id']}-{phase}.png"
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-threads', '1', '-filter_threads', '1', '-ss', str(at),
                '-i', str(video), '-frames:v', '1', '-update', '1', str(frame)], check=True, capture_output=True, timeout=30)
            resources.append(resource(frame, 'keyframe', step_id=step['step_id'], phase=phase, timestamp=at))
    resources.append(resource(draft_dir/'S1-before.png','poster'))
    runtime = {'source_dir': str(draft_dir), 'resources': resources, 'steps': steps,
        'compiler_hash': digest_of('compiler'), 'environment_hash': digest_of('environment'), 'technical_report': {}}
    draft = code_execution._render_row(base, base['candidate_spec'], runtime, 'draft')
    draft.update(state='needs_review', validation={'issues':[{'code':'MEDIA_REVIEW_EVIDENCE','message':'draft missing citations'}]})
    monkeypatch.setattr(media_stages, '_persisted_tickets', lambda *args: {})
    tickets, _, _ = media_stages._tickets(ctx, content, [draft])
    legacy = deepcopy(tickets[0])
    legacy['target']['step_id'] = 'MEDIA_REVIEW_EVIDENCE'
    legacy['previous_attempt'].pop('media_evidence_basis')
    legacy['ticket_id'] = make_ticket_id(rule=legacy['target']['rule'], unit_id=legacy['unit_id'],
        artifact_id=legacy['target']['artifact_id'], location=target_location_key(legacy['target'])[0])
    draft['repair_tickets'] = [legacy]
    report = validation.validate_media(request, draft, client=Reviewer())
    assert validation.validate_media_report(request, draft, report)
    write_json_atomic(attempt/'program.json', base['candidate_spec'])
    write_json_atomic(attempt/'draft-runtime.json', runtime)
    write_json_atomic(attempt/'draft-revalidation.json', report)
    final_dir = attempt/'final'
    shutil.copytree(draft_dir, final_dir)
    with (final_dir/'video.mp4').open('ab') as stream: stream.write(b'final profile bytes')
    final_runtime = deepcopy(runtime)
    final_runtime['source_dir'] = str(final_dir)
    for r in final_runtime['resources']:
        path=final_dir/r['relative_path']
        r.update(sha256=digest_file(path),bytes=path.stat().st_size)
    final = code_execution._render_row(draft, base['candidate_spec'], final_runtime, 'final')
    final.update(state='needs_review', validation={'ok':False, 'issues':[{'code':'MEDIA_REVIEW_EVIDENCE','message':'final missing transcript'}],
        'verdicts':[], 'review_problems':[]})
    final['validation']['result_hash'] = validation.result_digest(final)
    wrong = deepcopy(legacy)
    wrong['previous_attempt']['media_evidence_basis'] = digest_of({'spec_hash': final['spec_hash'],
        'render_profile':'final', 'resources':sorted((r['kind'],r['relative_path'],r['sha256']) for r in final['resources'])})
    monkeypatch.setattr(media_stages, '_persisted_tickets', lambda *args: {legacy['ticket_id']:wrong})
    return ctx, content, draft, final, wrong, attempt


@pytest.mark.parametrize('polluted_row', [False, True])
def test_legacy_final_migration_uses_approved_draft_history_and_keeps_both_budgets(legacy_final_history, polluted_row):
    ctx, content, draft, final, legacy, attempt = legacy_final_history
    if polluted_row: final['repair_tickets'] = [deepcopy(legacy)]
    state = UnitRepairState(content['unit_id'], evidence_attempts_by_verdict={legacy['ticket_id']:1})
    tickets, directives, _ = media_stages._tickets(ctx, content, [final])
    batch = RepairCoordinator(SimpleNamespace()).plan(list(directives), state)
    assert isinstance(batch, RepairBatch), 'approved prior draft must not spend the distinct final evidence budget'
    assert legacy['ticket_id'] not in {t['ticket_id'] for t in tickets}
    assert state.evidence_attempts_by_verdict == {legacy['ticket_id']:1}
    assert tickets[0]['criteria'] == legacy['criteria']
    final_id = tickets[0]['ticket_id']
    state.evidence_attempts_by_verdict[final_id] = 1
    final['repair_tickets'] += tickets
    _, directives, _ = media_stages._tickets(ctx, content, [final])
    assert RepairCoordinator(SimpleNamespace()).plan(list(directives), state).stopped_by == 'evidence_attempts_exhausted'
    assert state.evidence_attempts_by_verdict == {legacy['ticket_id']:1, final_id:1}


@pytest.mark.parametrize('damage', ['missing_receipt', 'wrong_result', 'missing_frame', 'changed_program'])
def test_legacy_final_migration_never_infers_fresh_budget_without_complete_history(legacy_final_history, damage):
    import json
    ctx, content, draft, final, legacy, attempt = legacy_final_history
    final['repair_tickets'] = [deepcopy(legacy)]
    receipt = attempt/'draft-revalidation.json'
    if damage == 'missing_receipt': receipt.unlink()
    elif damage == 'wrong_result':
        report = json.loads(receipt.read_text()); report['result_hash'] = digest_of('unrelated row')
        receipt.write_text(json.dumps(report))
    elif damage == 'missing_frame': (attempt/'draft/S1-before.png').unlink()
    else: (attempt/'program.json').write_text('{}')
    tickets, directives, _ = media_stages._tickets(ctx, content, [final])
    state = UnitRepairState(content['unit_id'], evidence_attempts_by_verdict={legacy['ticket_id']:1})
    assert {t['ticket_id'] for t in tickets} == {legacy['ticket_id']}
    assert RepairCoordinator(SimpleNamespace()).plan(list(directives), state).stopped_by == 'evidence_attempts_exhausted'
