"""No network: persistent evidence recovery exercises the production Gateway."""
import json
from pathlib import Path

import pytest

from aiforstudy.errors import ContractError
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy, AttemptLimitExceeded
from aiforstudy.providers.gateway import CallRequest, Gateway


def case(tmp_path, responses, *, seconds=1000, clock=None):
    from aiforstudy.pipeline.media_review_recovery import MediaReviewRecovery
    now = clock or (lambda: 100.0)
    guard = ExecutionControl(ExecutionPolicy(parallelism=1,api_deadline_seconds=seconds),clock=now)
    guard.bind(tmp_path/'execution.json',run_id='same-run')
    calls = []
    def transport(**kwargs):
        calls.append(kwargs)
        value = responses.pop(0)
        if isinstance(value, BaseException): raise value
        return value, {'total_tokens': 23}
    gateway = Gateway(transport=transport, roles={'judge': {'model': 'offline', 'max_tokens': 100}},
        control=guard,
        run_id='same-run', clock=now)
    recovery = MediaReviewRecovery(tmp_path/'recovery.json', identity={
        'run_id': 'same-run', 'unit_id': 'U1', 'request_id': 'MR1',
        'request_hash': 'request', 'resource_hash': 'pixels', 'criteria_digest': 'criteria',
        'policy_digest': 'policy', 'evidence_basis': 'frames+captions'}, session_id='initial')
    gateway.media_review_recovery = recovery
    request = CallRequest(role='judge', unit_key='U1', resource_id='MR1', stage='figures',
        messages=[{'role': 'user', 'content': 'all frozen frames, captions, criteria and math objects'}])
    return gateway, recovery, request, calls


def test_exhausted_evidence_never_opens_a_second_batch_on_resume(tmp_path):
    gateway,recovery,request,calls=case(tmp_path,[TimeoutError(),TimeoutError()])
    assert not gateway.call(request).ok
    assert len(calls)==2 and recovery.snapshot()['state']=='transport_failed'
    from aiforstudy.pipeline.media_review_recovery import MediaReviewRecovery
    restored=MediaReviewRecovery(recovery.path,identity=recovery.identity,session_id='resume')
    assert not restored.authorize_resume('resume',gateway.control.policy.as_dict())
    gateway.media_review_recovery=restored
    with pytest.raises(AttemptLimitExceeded): gateway.call(request)
    assert len(calls)==2 and len(gateway.control.snapshot()['attempts'])==2
    assert not (tmp_path/'usage.jsonl').exists()


@pytest.mark.parametrize('status', ['pass', 'fail', 'needs_review'])
def test_valid_semantic_response_is_not_transport_recoverable(tmp_path, status):
    response = json.dumps({'verdicts': [{'criterion_id':'C1', 'version':'1', 'status':status,
        'why':'actual evidence', 'evidence_ids':['caption:S1']}]})
    gateway, recovery, request, calls = case(tmp_path, [response])
    result = gateway.call(request)
    assert result.payload['verdicts'][0]['status'] == status
    assert recovery.snapshot()['state'] == 'valid_review_received'
    assert not recovery.authorize_resume('resume', gateway.control.policy.as_dict())
    assert len(calls) == 1


@pytest.mark.parametrize('bad', ['{bad-json', ''])
def test_unparseable_response_preserves_attempts_without_resume_refill(tmp_path, bad):
    gateway, recovery, request, calls = case(tmp_path, [bad, bad])
    assert not gateway.call(request).ok
    assert len(gateway.control.snapshot()['attempts']) == 2
    assert recovery.snapshot()['state'] == 'transport_failed'
    assert not recovery.authorize_resume('resume', gateway.control.policy.as_dict())


def test_valid_json_with_invalid_review_protocol_cannot_get_transport_resume(tmp_path):
    gateway, recovery, request, _ = case(tmp_path, ['{"accepted":true}'])
    assert gateway.call(request).ok
    assert recovery.snapshot()['state'] == 'review_protocol_invalid'
    assert not recovery.authorize_resume('resume', gateway.control.policy.as_dict())


@pytest.mark.parametrize('total,expected',[(1000,[900,1]),(None,[900,900])])
def test_every_review_retry_obeys_single_cap_and_explicit_remaining_total(tmp_path,total,expected):
    clock=[100.0]
    gateway,recovery,request,calls=case(tmp_path,[],seconds=total,clock=lambda:clock[0])
    def transport(**kw):
        calls.append(kw); clock[0]+=999
        raise TimeoutError('offline disconnect')
    gateway.transport=transport
    assert not gateway.call(request).ok
    assert [row['timeout'] for row in calls]==expected
    assert recovery.snapshot()['state']=='transport_failed'


def test_unrelated_gateway_request_keeps_existing_two_attempt_limit(tmp_path):
    gateway, _, request, calls = case(tmp_path, [TimeoutError(), TimeoutError()])
    gateway.media_review_recovery = None
    assert not gateway.call(request).ok
    with pytest.raises(AttemptLimitExceeded): gateway.call(request)
    assert len(calls) == 2


def test_real_runner_resume_keeps_candidate_and_finite_attempts(tmp_path,monkeypatch):
    _runner_transport_resume_case(tmp_path,monkeypatch,budget_stop=False,legacy=False)


@pytest.mark.parametrize('kind', ['drawing', 'animation'])
@pytest.mark.parametrize('phase', ['code', 'review'])
def test_runner_prompt_upgrade_cannot_remake_pending_evidence_or_reset_transport(tmp_path, monkeypatch, kind, phase):
    _runner_transport_resume_case(tmp_path, monkeypatch, budget_stop=False, legacy=False,
        prompt_upgrade=f'{phase}-'+('animation' if kind=='animation' else 'static')+'.md', kind=kind)


def _runner_transport_resume_case(tmp_path, monkeypatch, *, budget_stop, legacy, prompt_upgrade=None, kind='drawing'):
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from aiforstudy.core.digest import digest_of
    from aiforstudy.pipeline import media_stages, content_stages
    from aiforstudy.pipeline.repair import UnitRepairState
    from content_case import teaching_case, author_media, reviewed_plan
    from test_media_decisions_v2 import math_request_case
    from aiforstudy.blocks.media.media_planning import plan_media_requirements
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
    content, approval, raw, _ = math_request_case('animation' if kind == 'animation' else 'geometry')
    _, source = teaching_case()
    content['unit_id'] = UNIT
    from aiforstudy.blocks.content.content_contracts import content_digest
    approval.update(unit_id=UNIT, content_hash=content_digest(content))
    raw['requests'][0]['placement']['owner_id'] = UNIT
    caps = {'code_generation':'GeneratedMediaCodeV1', 'kinds':[kind]}
    plan = reviewed_plan(content, plan_media_requirements(content, approval,
        author_requirements=author_media(content, raw), capabilities=caps))
    request = plan['requests'][0]
    approved_context = ApprovedMathContext.from_content_binding(content, approval, request)
    dependencies = {'generation_dependency_hash': 'same-generation', 'review_dependency_hash': 'same-review'}
    dependency_provider = lambda *args: dependencies
    if prompt_upgrade:
        # Exercise real component selection/hashing with editable temporary prompt
        # assets. Model transport and native render discovery stay offline.
        import shutil
        from aiforstudy.blocks.media import dependencies as dependency_module
        from aiforstudy.checks import offline
        from aiforstudy.providers import capabilities
        from aiforstudy.core import prompt_assets
        package = Path(dependency_module.__file__).resolve().parents[2]
        shadow = tmp_path/'package'
        prompt_dir = shadow/'blocks/media/prompts'
        shutil.copytree(package/'blocks/media/prompts', prompt_dir)
        original_identity = dependency_module.file_identity
        frozen_runtime = {}
        def file_identity(path):
            candidate = Path(path) if path else None
            if candidate and candidate.is_relative_to(package):
                copied = shadow/candidate.relative_to(package)
                if copied.is_file(): return original_identity(copied)
            # Other developers can edit unrelated files while this fixture runs;
            # this case upgrades only its temporary prompt, not the whole runtime.
            key = str(path)
            if key not in frozen_runtime: frozen_runtime[key] = original_identity(path)
            return frozen_runtime[key]
        monkeypatch.setattr(dependency_module, 'file_identity', file_identity)
        monkeypatch.setattr(prompt_assets, 'files', lambda _: shadow)
        monkeypatch.setattr(offline, '_browser_executable', lambda _: None)
        monkeypatch.setattr(dependency_module.shutil, 'which', lambda _: None)
        monkeypatch.setattr(capabilities, 'resolve_manim_interpreter', lambda _: None)
        dependency_provider = dependency_module.resource_dependencies
        dependencies = dependency_provider(SimpleNamespace(workspace=workspace, services=engine.services),
            request, caps)
    resource_dir = store.work / 'frozen-media'
    resource_dir.mkdir()
    image = resource_dir / 'figure.png'
    image.write_bytes(b'frozen actual resource bytes')
    program = {'schema_version': 'GeneratedMediaCodeV1', 'request_id': request['request_id'],
        'backend': 'manim' if kind=='animation' else 'matplotlib', 'code': 'def build_figure(context):\n    return None\n', 'object_bindings': [], 'steps': []}
    old = {'request_id': request['request_id'], 'kind': kind, 'request': request,
        'request_hash': digest_of(request), 'criteria_digest': request['criteria_digest'],
        'math_context_digest': approved_context.digest, 'approved_reader_document_hash': approval['reader_document_hash'],
        'approved_dependencies': deepcopy(request['dependency_paths']), 'state': 'needs_review', 'reason': '缺完整真实帧引用',
        'candidate_spec': program, 'spec_hash': digest_of(program), 'render_profile': 'final',
        'code_product': {'schema_version': 'GeneratedMediaProductV1'}, 'repair_tickets': [],
        'code_attempts': [{'index': 1}], 'source_dir': str(resource_dir),
        'resources': [{'kind': 'image', 'relative_path': image.name, 'sha256': digest_file(image)}],
        **dependencies}
    old['code_generation_identity'] = digest_of({'request': request, 'context': approved_context.digest,
        'generation_dependency_hash': dependencies['generation_dependency_hash'],
        'contract': 'GeneratedMediaCodeV1', 'attempt_limit': 3})

    def incomplete_report(row):
        items = validation.media_review_items(request, row, media_review_policy(kind))
        return {'ok': False, 'decision': 'needs_review', 'result_hash': validation.result_digest(row),
            'issues': [{'code': 'MEDIA_REVIEW_EVIDENCE', 'message': '缺完整真实帧引用'}],
            'review_items': items, 'review_problems': [], 'evidence_catalog': {},
            'verdicts': [{'criterion_id': i['criterion_id'], 'version': i['version'], 'status': 'pass'} for i in items]}

    old['validation'] = incomplete_report(old)
    monkeypatch.setattr(media_stages, '_load_content', lambda ctx: (content, approval))
    monkeypatch.setattr(media_stages, '_load', lambda *args: plan)
    monkeypatch.setattr(content_stages, '_frozen_context', lambda ctx: source)
    monkeypatch.setattr(media_stages, '_capabilities', lambda ctx: caps)
    from aiforstudy.pipeline.gateway_client import GatewayClient
    network_calls = []
    def offline_transport(**kwargs):
        network_calls.append(kwargs)
        raise TimeoutError('offline transport failure; never contacted network')
    def client(ctx, role, bucket):
        if role != 'judge': return object()
        gateway = Gateway(transport=offline_transport, roles={'judge': {'model':'offline', 'max_tokens':100}},
            control=ctx.services.control,
            run_id=store.run_id, book_id=ctx.context.book_id)
        return GatewayClient(gateway, role='judge', model='offline', effort='high',
            stage='figures', unit_key=UNIT, bucket='figures')
    monkeypatch.setattr(media_stages, '_client', client)
    monkeypatch.setattr(media_stages, 'resource_dependencies', dependency_provider)
    monkeypatch.setattr(validation, 'display_runtime', lambda *args: None)

    def generate(*args, **kwargs):
        calls['generation'] += 1
        raise ContractError('unexpected generation on protocol-only resume')

    def review(request, row, **kwargs):
        calls['judge'] += 1
        kwargs['client'].chat_json([{'role':'user', 'content':json.dumps({'request':request,
            'review_items':['all frozen criteria'], 'evidence_catalog':{'caption:S1':'must cite real subtitle'}})}],
            tag='media-validation:'+request['request_id'])
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
    if prompt_upgrade:
        write_json_atomic(store.work/'media'/UNIT/request['request_id']/'result.json', old)
    state = store.load_state()
    state['repair_states'] = {UNIT: UnitRepairState(UNIT,
        evidence_attempts_by_verdict={}).as_dict()}
    store.save_state(state)
    result, resumed, _ = resume_request(engine.request, store.run_id, workspace,
        stage_registry=registry, until='figures')
    assert not result.release_ready
    assert first_stage_calls[0] == ({'generation': 0, 'judge': 0}, {'generation': 0, 'judge': 0})
    saved = resumed.load_state()['repair_states'][UNIT]
    assert saved['evidence_attempts_by_verdict'][tickets[0]['ticket_id']] == 1
    assert all(n == 1 for n in saved['evidence_attempts_by_verdict'].values())
    assert saved['active_batch_id'] is None
    assert calls['generation'] == 0
    assert len(network_calls) == 2, json.loads(media_stages._path(ctx,'figures').read_text())['payload']['requests'][0].get('reason')
    records = list((store.work/'media-review-recovery').glob('*.json'))
    assert len(records) == 1
    recovery = json.loads(records[0].read_text())
    assert recovery['state'] == 'transport_failed'
    if prompt_upgrade:
        manifest_path = media_stages._path(ctx, 'figures')
        result_path = store.work/'media'/UNIT/request['request_id']/'result.json'
        protected = [manifest_path, result_path, records[0], image]
        before_bytes = {path:path.read_bytes() for path in protected}
        before_execution = (store.root/'execution.json').read_bytes()
        before_repair = deepcopy(resumed.load_state()['repair_states'][UNIT])
        before_calls = deepcopy(calls)
        upgrade = prompt_dir/prompt_upgrade
        original_prompt = upgrade.read_text()
        upgrade.write_text(original_prompt+'\n升级本角色提示；不得重置旧补审任务。\n')
        upgraded_dependencies = dependency_provider(SimpleNamespace(workspace=workspace, services=engine.services),
            request, caps)
        key = 'generation_dependency_hash' if prompt_upgrade.startswith('code') else 'review_dependency_hash'
        assert upgraded_dependencies[key] != dependencies[key]
        for _ in range(2):
            result, resumed, _ = resume_request(engine.request, store.run_id, workspace,
                stage_registry=registry, until='figures')
            assert calls == before_calls, 'prompt upgrade must not dispatch generation or a fresh review batch'
            assert len(network_calls) == 2
            resumed_node=resumed.node_states()['figures:'+UNIT]
            assert resumed_node.execution_state.value in ('cached','blocked')
            assert resumed_node.quality_state.value != 'accepted'
            assert not result.release_ready
            if resumed_node.execution_state.value=='blocked':
                assert '待补审' in resumed_node.error['reason']
            assert before_bytes == {path:path.read_bytes() for path in protected}
            assert (store.root/'execution.json').read_bytes() == before_execution
            assert resumed.load_state()['repair_states'][UNIT] == before_repair
            assert list((store.work/'media-review-recovery').glob('*.json')) == records
        # Restoring the exact frozen prompt may continue the same bounded batch.
        upgrade.write_text(original_prompt)
    for _ in range(2):
        result,resumed,_=resume_request(engine.request,store.run_id,workspace,
            stage_registry=registry,until='figures')
        assert len(network_calls)==2, 'resume cannot grant another transport batch'
        assert calls['generation']==0
    assert not store.usage_path.exists()
    assert len(json.loads(media_stages._path(ctx,'figures').read_text())['payload']['requests'][0]['code_attempts'])==1


@pytest.mark.parametrize('status', ['missing_caption', 'fail', 'needs_review', 'pass'])
def test_resumed_payload_is_validated_without_filling_missing_subtitle_ids(tmp_path, status):
    from aiforstudy.blocks.media.validation import validate_verdicts
    items = [{'criterion_id':'host.code.step-coverage', 'version':'1', 'statement':'实际步骤与字幕一致',
        'required_evidence_kinds':['rendered_frame', 'captions']}]
    catalog = {'frame:wide:S1':{'kind':'rendered_frame'}, 'frame:narrow:S1':{'kind':'rendered_frame'},
        'captions:vtt':{'kind':'captions'}, 'captions:transcript':{'kind':'captions', 'caption_form':'transcript'}}
    refs = list(catalog)
    if status == 'missing_caption': refs.remove('captions:transcript')
    response = {'verdicts':[{'criterion_id':items[0]['criterion_id'], 'version':'1',
        'status':'pass' if status == 'missing_caption' else status, 'why':'查看冻结实际帧和字幕',
        'evidence_ids':refs, 'problem':'某步骤未展现变化', 'action':'按冻结步骤修复', 'expected':items[0]['statement']}]}
    gateway,recovery,request,calls=case(tmp_path,[json.dumps(response)])
    received=gateway.call(request)
    result = validate_verdicts(items, received.payload, catalog)
    assert result['ok'] is (status in ('pass','missing_caption'))
    assert received.payload == response
    if status == 'missing_caption':
        assert result['issues']==[]
        assert 'captions:transcript' not in received.payload['verdicts'][0]['evidence_ids']
    assert recovery.snapshot()['state'] == 'valid_review_received'
    assert not recovery.authorize_resume('another-resume', gateway.control.policy.as_dict())


def test_changed_recovery_identity_fails_before_new_io(tmp_path):
    from aiforstudy.pipeline.media_review_recovery import MediaReviewRecovery
    gateway,recovery,request,calls=case(tmp_path,[])
    recovery.save()
    with pytest.raises(ContractError,match='身份'):
        MediaReviewRecovery(recovery.path,identity={**recovery.identity,'resource_hash':'changed'},session_id='resume')
    assert not calls


def test_recovered_draft_does_not_spend_its_transport_quota_on_new_final_pixels(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from aiforstudy.blocks.media import validation
    from aiforstudy.blocks.media.code_execution import execute_code
    from test_media_code_execution import mock_runtime
    from test_media_code_contracts import approved_case
    content, approval, request, program = approved_case('animation')
    calls = mock_runtime(monkeypatch, program)
    def missing(request, row, **kwargs):
        return {'ok':False, 'decision':'needs_review', 'result_hash':validation.result_digest(row),
            'issues':[{'code':'MEDIA_REVIEW_EVIDENCE','message':'缺逐字稿'}],
            'verdicts':[], 'review_problems':[]}
    monkeypatch.setattr(validation, 'validate_media', missing)
    previous = execute_code(request, content=content, approval=approval, out_dir=tmp_path,
        client=object(), reviewer=object())
    assert previous['render_profile'] == 'draft'
    seen = []
    fresh_final_client = object()
    evidence_client = SimpleNamespace(after_evidence_client=fresh_final_client)
    def accepted(request, row, **kwargs):
        seen.append((row['render_profile'], kwargs['client']))
        return {'ok':True, 'decision':'accepted', 'issues':[], 'review_problems':[],
            'result_hash':validation.result_digest(row), 'math_report':{'status':'pass'}}
    monkeypatch.setattr(validation, 'validate_media', accepted)
    result = execute_code(request, content=content, approval=approval, out_dir=tmp_path,
        client=object(), reviewer=evidence_client, previous=previous, evidence_only=True)
    assert result['state'] == 'fulfilled' and result['render_profile'] == 'final'
    assert seen == [('draft', evidence_client), ('final', fresh_final_client)]
    assert calls['generate'] == 1 and len(result['code_attempts']) == 1


def test_recovery_never_widens_a_stricter_transport_limit(tmp_path):
    gateway,recovery,request,calls=case(tmp_path,[TimeoutError()])
    gateway.control.policy.attempt_limits['transport']=1
    assert not gateway.call(request).ok
    assert len(calls)==1
    assert not recovery.authorize_resume('resume',gateway.control.policy.as_dict())
    with pytest.raises(AttemptLimitExceeded): gateway.call(request)
    assert len(calls)==1


def test_frozen_evidence_client_rejects_another_persisted_resource_before_transport(tmp_path):
    from types import SimpleNamespace
    from aiforstudy.pipeline.media_review_recovery import EvidenceReviewClient
    gateway, recovery, request, calls = case(tmp_path, [])
    client = EvidenceReviewClient(SimpleNamespace(gateway=gateway), recovery)
    with pytest.raises(ContractError, match='冻结媒体证据'):
        client.validate_frozen_evidence({'request_id':'MR1'}, {'spec_hash':'another-program', 'resources':[]})
    assert calls == []
