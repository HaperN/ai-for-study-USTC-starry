"""A bad sibling citation cannot suppress a separately verified visual failure."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from aiforstudy.blocks.media import code_execution, code_generation, validation
from aiforstudy.blocks.media.review_policy import code_review_criteria
from aiforstudy.core.digest import digest_of
from test_media_code_contracts import approved_case
from test_media_code_execution import mock_runtime


def report_for(row, *, semantic_fail=True, content_error=False, repaired=False):
    items = [
        {'criterion_id': 'host.code.mathematical-correspondence', 'version': '1',
         'statement': '实际向量端点必须对应批准对象 (3,3)。',
         'required_evidence_kinds': ['rendered_frame', 'object_bindings']},
        {'criterion_id': 'host.animation.step-caption', 'version': '1',
         'statement': '每步字幕必须对应真实变化。',
         'required_evidence_kinds': ['rendered_frame', 'captions', 'steps']},
    ]
    catalog = {'wide': {'kind': 'rendered_frame'}, 'narrow': {'kind': 'rendered_frame'},
               'objects': {'kind': 'object_bindings'}, 'steps': {'kind': 'steps'},
               'approved': {'kind': 'approved_dependencies'}, 'math': {'kind': 'mathematical_report'},
               'vtt': {'kind': 'captions'},
               'transcript': {'kind': 'captions', 'caption_form': 'transcript'}}
    verdicts = []
    for i, item in enumerate(items):
        status = 'pass' if repaired or (i == 0 and not semantic_fail) else 'fail'
        refs = list(catalog) if repaired or i == 0 else [*catalog, 'transcript:invented']
        verdicts.append({'criterion_id': item['criterion_id'], 'version': '1', 'status': status,
                         'evidence_ids': refs, 'why': '实际帧中端点标签重叠。',
                         'problem': '端点标签重叠，不能辨认批准数值。',
                         'action': '移开标签后重渲染，核对批准端点。',
                         'expected': '模型自写的标准不能替代冻结标准。'})
    problems = ([{'target_artifact_kind': 'content', 'pointer': '/body_markdown',
                  'issue_key': 'wrong-math', 'problem': '批准正文数值存在矛盾。',
                  'evidence': '正文', 'action': '先修复正文并重新批准。', 'expected': '正文数学正确。'}]
                if content_error else [])
    checked = validation.validate_verdicts(items, {'verdicts': verdicts, 'problems': problems}, catalog)
    return {**checked, 'decision': 'accepted' if checked['ok'] else 'needs_review',
            'result_hash': validation.result_digest(row), 'review_items': items,
            'evidence_catalog': catalog, 'review_problems': problems,
            'math_report': {'status': 'pass' if checked['ok'] else 'needs_review'}}


def prepare(monkeypatch, *, mode):
    content, approval, request, program = approved_case('animation')
    calls = mock_runtime(monkeypatch, program)
    feedback = []

    def generate(*args, **kwargs):
        calls['generate'] += 1
        feedback.append(deepcopy(kwargs.get('feedback')))
        return deepcopy(program)

    def review(request, row, **kwargs):
        calls['review'] += 1
        return report_for(row, semantic_fail=mode != 'protocol', content_error=mode == 'content',
                          repaired=mode == 'mixed' and calls['generate'] >= 2)

    monkeypatch.setattr(code_generation, 'generate_media_code', generate)
    monkeypatch.setattr(validation, 'validate_media', review)
    return content, approval, request, calls, feedback


def test_mixed_review_revises_code_before_another_review_and_retains_frozen_standard(tmp_path, monkeypatch):
    content, approval, request, calls, feedback = prepare(monkeypatch, mode='mixed')
    row = code_execution.execute_code(request, content=content, approval=approval, out_dir=tmp_path,
                                      client=object(), reviewer=object())
    assert row['state'] == 'fulfilled' and row['render_profile'] == 'final'
    assert calls == {'generate': 2, 'render': ['draft', 'draft', 'final'], 'review': 3}
    assert len(row['code_attempts']) == 2
    assert [f['criterion_id'] for f in feedback[1]['failures']] == ['host.code.mathematical-correspondence']
    assert feedback[1]['failures'][0]['expected'] == code_review_criteria()[0]['statement']


def test_protocol_only_failure_rechecks_evidence_without_generating_more_code(tmp_path, monkeypatch):
    content, approval, request, calls, _ = prepare(monkeypatch, mode='protocol')
    kwargs = dict(content=content, approval=approval, out_dir=tmp_path, client=object(), reviewer=object())
    row = code_execution.execute_code(request, **kwargs)
    renewed = code_execution.execute_code(request, **kwargs, previous=row, evidence_only=True)
    assert renewed['state'] == 'needs_review'
    assert calls == {'generate': 1, 'render': ['draft'], 'review': 2}
    assert len(renewed['code_attempts']) == 1 and renewed['resources'] == row['resources']


def test_content_discovery_blocks_media_revision_even_with_a_valid_visual_failure(tmp_path, monkeypatch):
    content, approval, request, calls, _ = prepare(monkeypatch, mode='content')
    row = code_execution.execute_code(request, content=content, approval=approval, out_dir=tmp_path,
                                      client=object(), reviewer=object())
    assert row['state'] == 'needs_review'
    assert calls == {'generate': 1, 'render': ['draft'], 'review': 1}
    assert row['validation']['review_problems'][0]['target_artifact_kind'] == 'content'


def test_mixed_failures_still_stop_after_three_persisted_candidates(tmp_path, monkeypatch):
    content, approval, request, calls, _ = prepare(monkeypatch, mode='persistent')
    kwargs = dict(content=content, approval=approval, out_dir=tmp_path, client=object(), reviewer=object())
    row = code_execution.execute_code(request, **kwargs)
    resumed = code_execution.execute_code(request, **kwargs, previous=row)
    assert resumed['state'] == 'needs_review'
    assert calls == {'generate': 3, 'render': ['draft'] * 3, 'review': 3}
    assert len(resumed['code_attempts']) == 3


def test_evidence_only_replay_persists_new_failure_feedback_at_the_same_attempt_count(tmp_path, monkeypatch):
    content, approval, request, calls, feedback = prepare(monkeypatch, mode='protocol')

    def review(request, row, **kwargs):
        calls['review'] += 1
        return report_for(row, semantic_fail=calls['review'] >= 2, repaired=calls['generate'] >= 2)

    monkeypatch.setattr(validation, 'validate_media', review)
    kwargs = dict(content=content, approval=approval, out_dir=tmp_path, client=object(), reviewer=object())
    original = code_execution.execute_code(request, **kwargs)
    renewed = code_execution.execute_code(request, **kwargs, previous=original, evidence_only=True)
    persisted = json.loads((tmp_path / 'result.json').read_text())
    assert len(original['code_attempts']) == len(renewed['code_attempts']) == 1
    assert persisted['validation'] == renewed['validation']
    resumed = code_execution.execute_code(request, **kwargs, previous=renewed)
    assert resumed['state'] == 'fulfilled' and calls['generate'] == 2
    assert [f['criterion_id'] for f in feedback[1]['failures']] == ['host.code.mathematical-correspondence']


@pytest.mark.parametrize('final_outcome', ['pass', 'blocked', 'semantic_fail', 'exception', 'cancelled'])
def test_repaired_draft_protocol_promotes_frozen_code_to_final_before_returning(tmp_path, monkeypatch, final_outcome):
    from aiforstudy.blocks.media import code_runtime
    from aiforstudy.errors import Cancelled
    from aiforstudy.pipeline import content_stages, media_stages
    from test_teaching_policy_v2 import teaching_case
    content, approval, request, program = approved_case('animation')
    teaching_content, source = teaching_case()
    ctx = SimpleNamespace(node=SimpleNamespace(scope_key=teaching_content['unit_id'], stage='figures'))
    monkeypatch.setattr(content_stages, '_frozen_context', lambda ctx: source)
    monkeypatch.setattr(media_stages, '_persisted_tickets', lambda *a, **kw: {})
    calls = mock_runtime(monkeypatch, program, final_blocked=final_outcome == 'blocked')
    render = code_runtime.render_generated_code

    def render_final(candidate, *, profile, **kwargs):
        if profile == 'final' and final_outcome == 'cancelled':
            calls['render'].append(profile)
            return {'status': 'cancelled', 'reason': 'final worker cancelled', 'resources': []}
        if profile == 'final' and final_outcome == 'exception':
            calls['render'].append(profile)
            raise RuntimeError('final renderer interrupted')
        return render(candidate, profile=profile, **kwargs)

    def review(request, row, **kwargs):
        calls['review'] += 1
        report = report_for(row, repaired=not (row['render_profile'] == 'final' and final_outcome == 'semantic_fail'))
        if calls['review'] == 1:
            # Every semantic verdict passes; one citation is not current evidence.
            report['verdicts'][1]['evidence_ids'].append('transcript:invented')
            checked = validation.validate_verdicts(report['review_items'],
                {'verdicts': report['verdicts'], 'problems': []}, report['evidence_catalog'])
            report.update(checked, decision='needs_review')
        return report

    monkeypatch.setattr(code_runtime, 'render_generated_code', render_final)
    monkeypatch.setattr(validation, 'validate_media', review)
    kwargs = dict(content=content, approval=approval, out_dir=tmp_path, client=object(), reviewer=object())
    original = code_execution.execute_code(request, **kwargs)
    assert original['state'] == 'needs_review' and original['render_profile'] == 'draft'
    tickets, directives, _ = media_stages._tickets(ctx, teaching_content, [original])
    assert tickets and all(d.owner == 'reviewer' for d in directives)

    def no_generation_probe(*args, **kwargs):
        raise AssertionError('review-only promotion must rely on the real renderer environment check')

    monkeypatch.setattr(code_runtime, 'preflight_generated_code', no_generation_probe)
    if final_outcome == 'cancelled':
        with pytest.raises(Cancelled, match='final worker cancelled'):
            code_execution.execute_code(request, **kwargs, previous=original, evidence_only=True, repair_context=tickets)
        persisted = json.loads((tmp_path / 'result.json').read_text())
        assert calls == {'generate': 1, 'render': ['draft', 'final'], 'review': 2}
        assert len(persisted['code_attempts']) == 1 and persisted['resources'] == []
        assert persisted['state'] != 'fulfilled' and not persisted.get('animation')
        assert not persisted.get('validation', {}).get('ok')
        return
    renewed = code_execution.execute_code(request, **kwargs, previous=original, evidence_only=True, repair_context=tickets)
    persisted = json.loads((tmp_path / 'result.json').read_text())
    assert calls['generate'] == 1 and calls['render'] == ['draft', 'final']
    assert len(renewed['code_attempts']) == 1 and renewed['candidate_spec'] == original['candidate_spec']
    assert persisted == renewed
    if final_outcome in ('blocked', 'exception'):
        assert renewed['state'] == ('blocked' if final_outcome == 'blocked' else 'failed')
        assert calls['review'] == 2 and renewed['resources'] == []
        assert not renewed.get('validation', {}).get('ok') and not renewed.get('animation')
    else:
        assert renewed['render_profile'] == 'final' and calls['review'] == 3
        assert renewed['source_dir'] != original['source_dir']
        assert renewed['validation']['result_hash'] == validation.result_digest(renewed)
        assert renewed['state'] == ('fulfilled' if final_outcome == 'pass' else 'needs_review')
        if final_outcome == 'semantic_fail':
            assert code_execution._feedback(renewed)['failures']
            assert renewed['validation']['ok'] is False
    if final_outcome in ('semantic_fail', 'exception'):
        _, directives, routes = media_stages._tickets(ctx, teaching_content, [renewed])
        assert directives and any(r['target_artifact_kind'] == 'media_resource' for r in routes)


def test_unknown_caption_citation_remains_a_protocol_error():
    report = report_for({})
    issue = next(i for i in report['issues'] if i['code'] == 'MEDIA_REVIEW_EVIDENCE')
    assert '证据引用无效' in issue['message']


@pytest.mark.parametrize('damage', ['unknown_frame', 'version', 'empty_action', 'duplicate', 'needs_review', 'unknown_criterion'])
def test_only_independently_valid_failures_authorize_revision(damage):
    report = report_for({})
    frozen_items = deepcopy(report['review_items'])
    verdict = report['verdicts'][0]
    if damage == 'unknown_frame': verdict['evidence_ids'].append('invented')
    elif damage == 'version': verdict['version'] = '999'
    elif damage == 'empty_action': verdict['action'] = ' '
    elif damage == 'duplicate': report['verdicts'].append(deepcopy(verdict))
    elif damage == 'needs_review': verdict['status'] = 'needs_review'
    else:
        verdict['criterion_id'] = 'invented'
        report['review_items'][0]['criterion_id'] = 'invented'
        report['ok'] = True
        report['issues'] = []
    assert validation.actionable_media_failures(report, items=frozen_items) == []


@pytest.mark.parametrize('mode', ['mixed', 'protocol', 'content'])
def test_coordinator_keeps_evidence_tickets_but_prioritizes_valid_semantic_failures(monkeypatch, mode):
    from test_teaching_policy_v2 import teaching_case
    from aiforstudy.pipeline import media_stages, content_stages
    content, source = teaching_case()
    ctx = SimpleNamespace(node=SimpleNamespace(scope_key=content['unit_id'], stage='figures'))
    monkeypatch.setattr(content_stages, '_frozen_context', lambda ctx: source)
    monkeypatch.setattr(media_stages, '_persisted_tickets', lambda *a, **kw: {})
    _, _, request, program = approved_case('animation')
    row = {'request_id': request['request_id'], 'kind': 'animation', 'state': 'needs_review',
           'request': request, 'candidate_spec': program, 'spec_hash': digest_of(program),
           'code_product': {'schema_version': 'GeneratedMediaProductV1'},
           'code_attempts': [{'index': i} for i in range(1, 4)]}
    row['validation'] = report_for(row, semantic_fail=mode != 'protocol', content_error=mode == 'content')
    row['review'] = {'problems': row['validation']['review_problems']}
    tickets, directives, routes = media_stages._tickets(ctx, content, [row])
    assert any(t['kind'] == 'evidence_format' for t in tickets)
    if mode == 'mixed':
        assert directives and all(d.owner == 'visual_planner' for d in directives)
        assert all(r['target_artifact_kind'] == 'media_resource' for r in routes)
        selected = {d.ticket_ids[0] for d in directives}
        chosen = next(t for t in tickets if t['ticket_id'] in selected)
        assert chosen['criteria'][0]['expected'] == code_review_criteria()[0]['statement']
    else:
        assert directives and all(d.owner not in ('visual_planner', 'visual_renderer') for d in directives)
        if mode == 'protocol': assert all(d.owner == 'reviewer' for d in directives)
