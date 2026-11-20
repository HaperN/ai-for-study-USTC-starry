"""Unknown review is displayable, while confirmed errors never enter the reader."""
import json
from pathlib import Path

import pytest

from aiforstudy.core.digest import digest_file, digest_of
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.errors import ContractError


def _run(tmp_path):
    from test_diagnostic_preview_boundary import run_fixture
    return run_fixture(tmp_path)


def _payload(run, stage):
    return json.loads((run/'work/products'/stage/'U.json').read_text())['payload']


def _save(run, stage, payload):
    write_json_atomic(run/'work/products'/stage/'U.json', {'payload': payload})


def test_unknown_content_keeps_reader_without_fabricating_approval(tmp_path):
    from aiforstudy.blocks.render.diagnostics import build_diagnostic_preview
    run = _run(tmp_path)
    audit = _payload(run, 'text_audit')
    audit.update(approval={}, review_outcome={'status': 'unverified', 'reasons': ['review timed out']})
    _save(run, 'text_audit', audit)
    plan = {'content_hash': _payload(run, 'lesson')['content_hash'], 'requests': [], 'decisions': []}
    _save(run, 'media_plan', plan)
    _save(run, 'figures', {'content_hash': plan['content_hash'], 'requirements_hash': digest_of(plan), 'requests': []})
    result = build_diagnostic_preview(run, unit_ids=['U'], out_dir=tmp_path/'reader')
    data = (Path(result['output_dir'])/'data/data.js').read_text()
    assert 'ReaderDocumentV1' in data and 'review timed out' in data
    assert result['review_outcome']['status'] == 'unverified'
    assert result['production_accepted'] is False
    assert _payload(run, 'text_audit')['approval'] == {}


def test_confirmed_bad_content_is_missing_and_other_units_continue(tmp_path):
    from aiforstudy.blocks.render.diagnostics import build_diagnostic_preview
    run = _run(tmp_path)
    lesson = _payload(run, 'lesson')
    secret = lesson['content']['body_markdown']
    audit = _payload(run, 'text_audit')
    audit.update(approval={}, review_outcome={'status': 'known_error', 'reasons': ['math mismatch'],
                                             'attempts': {'content_repairs': 2}})
    _save(run, 'text_audit', audit)
    result = build_diagnostic_preview(run, unit_ids=['U', 'V'], out_dir=tmp_path/'reader')
    data = (Path(result['output_dir'])/'data/data.js').read_text()
    assert secret not in data
    assert 'math mismatch' in data and 'content_repairs' in data
    assert {row['unit_id'] for row in result['missing']} == {'U', 'V'}
    assert result['review_outcome']['status'] == 'missing'


def test_confirmed_bad_media_is_not_projected_even_with_intact_bytes(tmp_path):
    from aiforstudy.blocks.render.diagnostics import build_diagnostic_preview
    run = _run(tmp_path)
    file = run/'work/media/R/figure.svg'
    file.parent.mkdir(parents=True)
    file.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text>WRONG-MATH</text></svg>')
    manifest = _payload(run, 'figures')
    manifest['requests'][0].update(source_dir=str(file.parent),
        resources=[{'kind': 'svg', 'relative_path': file.name, 'sha256': digest_file(file)}],
        review_outcome={'status': 'known_error', 'reasons': ['incorrect formula']})
    _save(run, 'figures', manifest)
    report = build_diagnostic_preview(run, unit_ids=['U'], out_dir=tmp_path/'reader')
    data = (Path(report['output_dir'])/'data/data.js').read_text()
    assert 'WRONG-MATH' not in data
    assert 'incorrect formula' in data
    assert report['missing'][0]['request_id'] == 'R'


def test_identity_is_not_an_approval_and_is_bound_to_exact_reader():
    from test_content_v4_reader_contract import candidate, catalog
    from aiforstudy.blocks.content.review_outcome import content_identity, validate_content_identity
    from aiforstudy.blocks.media.media_requirements import require_approval
    content = candidate()
    identity = content_identity(content, catalog(), policy_digest='sha256:policy')
    assert 'decision' not in identity and 'approval' not in identity
    assert validate_content_identity(content, identity)
    with pytest.raises(ContractError): require_approval(content, identity)
    content['body_markdown'] += 'tampered'
    with pytest.raises(ContractError): validate_content_identity(content, identity)


@pytest.mark.parametrize('failure', [TimeoutError('review deadline'), ContractError('broken review JSON')])
def test_text_audit_keeps_candidate_on_review_failure(tmp_path, monkeypatch, failure):
    from aiforstudy.pipeline import content_stages as C, stage_io
    from test_media_plan_stage import ctx
    from support.content_case import approved_inputs
    c, source, *_ = approved_inputs()
    context = ctx(tmp_path, 'text_audit')
    monkeypatch.setattr(C, 'load_content', lambda *_a, **_k: (c, {}))
    monkeypatch.setattr(C, '_frozen_context', lambda *_: source)
    monkeypatch.setattr(C, '_load_tickets', lambda *_: ([], {}))
    class Reviewer:
        def chat_json(self, *args, **kwargs): raise failure
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *_a, **_k: (Reviewer(), 'controlled'))
    result = C.text_audit_stage(context)
    assert result.execution_state.value == 'succeeded', result.error
    payload = json.loads((tmp_path/'products/text_audit/U.json').read_text())['payload']
    assert payload['review_outcome']['status'] == 'unverified'
    assert payload['content_identity']['schema_version'] == 'ContentIdentityV1'
    assert not payload['approval']
    assert all(r['target_artifact_kind'] == 'evidence' for r in result.repair_routes)


def test_media_plan_accepts_identity_without_claiming_upstream_approval():
    from test_media_decisions_v2 import math_request_case
    from support.content_case import author_media, Planner, teaching_case
    from aiforstudy.blocks.content.review_outcome import content_identity
    from aiforstudy.blocks.media.media_planning import plan_media_requirements
    from aiforstudy.blocks.media.media_requirements import validate_requirements
    content, approval, raw, caps = math_request_case()
    identity = content_identity(content, teaching_case()[1]['reference_catalog'], policy_digest=approval['policy_digest'])
    plan = plan_media_requirements(content, identity, author_requirements=author_media(content, raw),
                                   capabilities=caps, client=Planner(raw))
    assert plan['schema_version'] == 'MediaRequirementsV3'
    assert plan['content_identity_hash'] == digest_of(identity)
    assert 'approval_ref' not in plan
    assert validate_requirements(plan, content, identity, capabilities=caps)['ok']


def test_media_generation_prompt_never_calls_identity_an_approval():
    from test_media_code_contracts import approved_case
    from support.content_case import teaching_case
    from aiforstudy.blocks.content.review_outcome import content_identity
    from aiforstudy.blocks.media.code_generation import media_code_prompt
    content, approval, request, _ = approved_case()
    identity = content_identity(content, teaching_case()[1]['reference_catalog'],
                                policy_digest=approval['policy_digest'])
    messages = media_code_prompt(request, content=content, approval=identity)
    assert '仅绑定冻结输入，不代表正文已经核实' in messages[0]['content']
    payload = json.loads(messages[1]['content'])
    assert payload['approval']['schema_version'] == 'ContentIdentityV1'
    assert 'decision' not in payload['approval']


def test_verified_state_requires_replayable_approval_and_unknown_propagates(tmp_path):
    from support.content_case import approved_inputs, issue
    from aiforstudy.blocks.content.review_outcome import inherit_outcome, outcome
    from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
    inputs = approved_inputs()
    content, source, policy, doc, reports, math = inputs
    run = _run(tmp_path)
    _save(run,'lesson',{'content':content,'content_hash':digest_of(content)})
    audit={'content_hash':digest_of(content),'source_context':source,'review_document':doc.as_dict(),
        'check_reports':[r.as_dict() if hasattr(r,'as_dict') else r for r in reports],
        'math_report':math,'approval':issue(inputs).as_dict(),'review_outcome':outcome('verified')}
    _save(run,'text_audit',audit)
    plan={'content_hash':digest_of(content),'requests':[],'decisions':[]}
    _save(run,'media_plan',plan)
    _save(run,'figures',{'content_hash':digest_of(content),'requirements_hash':digest_of(plan),'requests':[]})
    verified=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader')
    assert verified['review_outcome']['status']=='verified'
    audit['check_reports']=[]
    _save(run,'text_audit',audit)
    unknown=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader')
    assert unknown['review_outcome']['status']=='unverified'
    assert inherit_outcome(outcome('verified'),outcome('unverified',['source uncertainty']))['status']=='unverified'


def test_bad_media_plan_preserves_current_unverified_body(tmp_path):
    from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
    run=_run(tmp_path)
    _save(run,'media_plan',{'review_outcome':{'status':'missing','reasons':['compiler unavailable']}})
    report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader')
    assert 'ReaderDocumentV1' in (Path(report['output_dir'])/'data/data.js').read_text()
    # In full mode this unreviewed lesson has not authorized media production.
    # Its plan problem is recorded without treating unmade media as lost bytes.
    assert any(row['kind']=='media_plan' for row in report['problems'])
    assert report['missing']==[] and report['partial'] is False
    assert report['problem_summary'][0]['status']=='unverified'
    assert report['production_accepted'] is False


def test_media_review_timeout_preserves_unknown_and_resources(tmp_path):
    from test_media_code_validation import code_case
    from aiforstudy.blocks.media.validation import validate_media
    from aiforstudy.blocks.content.review_outcome import media_outcome
    request, row = code_case(tmp_path)
    class Reviewer:
        def chat_json(self, messages, *, tag, images): raise TimeoutError('review deadline')
    row['validation'] = validate_media(request, row, client=Reviewer())
    assert media_outcome(row)['status'] == 'unverified'
    assert row['validation']['issues'][0]['code'] == 'MEDIA_CALL_FAILED'
    assert row['validation']['review_failure']['kind'] == 'timeout'
    assert (tmp_path/'figure.svg').is_file()


def test_media_small_label_is_warning_but_receipt_still_checks_evidence(tmp_path):
    from test_media_code_validation import code_case, Reviewer
    from aiforstudy.blocks.media.validation import validate_media, validate_media_report
    request, row = code_case(tmp_path)
    file = tmp_path/'figure.svg'
    file.write_text(file.read_text().replace('font-size="40"', 'font-size="24"'))
    row['resources'][0]['sha256'] = digest_file(file)
    report = validate_media(request, row, client=Reviewer())
    assert report['ok'], report['issues']
    assert report['warnings']
    assert validate_media_report(request, row, report)
    report['evidence_catalog'] = {}
    with pytest.raises(ContractError): validate_media_report(request, row, report)


def test_production_render_and_delivery_keep_unknown_with_missing_unit(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from dataclasses import make_dataclass
    from aiforstudy.pipeline import media_stages, delivery_stage
    from aiforstudy.pipeline.graph import Node
    from aiforstudy.checks import offline
    from aiforstudy.checks.offline import OfflineCheck
    run = _run(tmp_path)
    from test_reader_admission import intact_drawing
    intact_drawing(run)
    parse = tmp_path/'parse'
    write_json_atomic(parse/'chapters.json', {'lesson_units':[
        {'unit_id':'U','chapter':'C'}, {'unit_id':'V','chapter':'C'}]})
    audit = _payload(run, 'text_audit')
    audit.update(approval={}, review_outcome={'status':'unverified','reasons':['review deadline']})
    _save(run, 'text_audit', audit)
    Context = make_dataclass('Context', ['node','context','workspace','services','plan'])
    context = Context(node=Node('render:C','render','C'),
        context=SimpleNamespace(book_id='test-book',revision='source',parse=parse,title='Partial'),
        workspace=SimpleNamespace(root=Path(__file__).resolve().parents[2]),
        services=SimpleNamespace(store=SimpleNamespace(work=run/'work',run_id=run.name),
            cancel=SimpleNamespace(checkpoint=lambda:None),control=SimpleNamespace(check=lambda:None)),
        plan=SimpleNamespace(resolved_scope=('U','V')))
    monkeypatch.setattr(offline,'check_browser_execution',lambda *_a,**_k: OfflineCheck(
        'browser-execution','blocked','browser unavailable',{}))
    result = media_stages.render_stage(context)
    assert result.execution_state.value == 'succeeded', result.error
    assert result.quality_state.value == 'needs_review'
    payload = json.loads((run/'work/products/render/C.json').read_text())['payload']
    assert payload['reader_snapshot']['review_outcome']['status'] == 'missing'
    assert 'review deadline' in (run/'work/legacy/render/C/data/data.js').read_text()
    assert '页面检查未完成' not in (run/'work/legacy/render/C/index.html').read_text()
    assert 'reader-check-status' not in (run/'work/legacy/render/C/index.html').read_text()
    from aiforstudy.blocks.render.diagnostics import build_diagnostic_preview
    diagnostic = build_diagnostic_preview(run,unit_ids=['U','V'],out_dir=tmp_path/'diagnostic')
    assert diagnostic['snapshot_hash'] != payload['reader_snapshot']['snapshot_hash']
    assert payload['reader_snapshot']['rendered_units'] == ['U']
    context.node=SimpleNamespace(stage='delivery',scope_key='scope',node_id='delivery:scope')
    result = delivery_stage.delivery_stage(context)
    assert result.execution_state.value == 'succeeded', result.error
    assert result.quality_state.value == 'needs_review'
    # A separately requested diagnostic preview cannot replace teaching output.
    payload['reader_snapshot']=diagnostic
    write_json_atomic(run/'work/products/render/C.json',{'payload':payload})
    with pytest.raises(ContractError,match='诊断快照不能充当教学产物'):
        delivery_stage.validate_reader_delivery(context)
