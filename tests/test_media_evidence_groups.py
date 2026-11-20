"""Explicit host-owned groups abbreviate citations without granting approval."""
from copy import deepcopy
import json

import pytest

from aiforstudy.blocks.media import validation
from aiforstudy.blocks.media.evidence_digest import validation_digest
from aiforstudy.core.digest import digest_of
from aiforstudy.errors import ContractError
from test_media_code_validation import code_case


class GroupReviewer:
    def __init__(self, *, status='pass', extra_ref=None):
        self.status = status
        self.extra_ref = extra_ref
        self.payload = None
        self.response = None

    def chat_json(self, messages, *, tag, images):
        self.payload = json.loads(messages[-1]['content'])
        assert 'evidence_groups' in self.payload, 'The host must declare groups in this review input.'
        refs = list(self.payload['evidence_groups'])
        refs += [eid for eid, entry in self.payload['evidence_catalog'].items()
                 if entry['kind'] not in ('rendered_frame', 'captions')]
        if self.extra_ref:
            refs.append(self.extra_ref)
        self.response = {'verdicts': [
            {'criterion_id': item['criterion_id'], 'version': item['version'], 'status': self.status,
             'evidence_ids': refs, 'why': '两个尺寸的向量端点与批准值逐一对应。',
             'problem': '向量端点标签重叠。' if self.status != 'pass' else '',
             'action': '分开端点标签并重新渲染。' if self.status != 'pass' else '',
             'expected': item['statement'] if self.status != 'pass' else ''}
            for item in self.payload['review_items']], 'problems': []}
        return {'data': deepcopy(self.response)}


def test_explicit_group_review_preserves_raw_citations_and_reloads_actual_evidence(tmp_path):
    request, result = code_case(tmp_path)
    reviewer = GroupReviewer()
    report = validation.validate_media(request, result, client=reviewer)
    assert report['ok'], report['issues']
    assert report['raw_verdicts'] == reviewer.response['verdicts']
    assert report['evidence_groups'] == reviewer.payload['evidence_groups']
    expected_ids = set(report['evidence_catalog'])
    assert all(set(row['evidence_ids']) == expected_ids for row in report['verdicts'])
    assert set(report['math_report']['evidence_ids']) == expected_ids
    assert validation.validate_media_report(request, result, report)
    assert validation_digest(report).startswith('sha256:')


def group_case():
    items = [{'criterion_id': 'host.code.author-step-coverage', 'version': '1',
              'statement': '全部真实帧与字幕对应批准步骤。',
              'required_evidence_kinds': ['rendered_frame', 'captions', 'steps']}]
    catalog = {'wide': {'kind': 'rendered_frame', 'sha256': 'sha256:wide', 'step_id': 'S1'},
               'narrow': {'kind': 'rendered_frame', 'sha256': 'sha256:narrow', 'step_id': 'S1'},
               'vtt': {'kind': 'captions', 'sha256': 'sha256:vtt'},
               'transcript': {'kind': 'captions', 'sha256': 'sha256:text', 'caption_form': 'transcript'},
               'steps': {'kind': 'steps', 'data': [{'step_id': 'S1', 'caption': '观察端点。'}]}}
    context = {'request_hash': digest_of('request'), 'result_hash': digest_of('draft'),
               'criteria_digest': digest_of(items), 'policy_digest': digest_of('policy'),
               'display_profiles_hash': digest_of(['wide', 'narrow']), 'review_items': items}
    return items, catalog, context


def response_for(items, refs, *, status='pass'):
    return {'verdicts': [{'criterion_id': items[0]['criterion_id'], 'version': '1', 'status': status,
                         'evidence_ids': refs, 'why': '窄屏步骤 S1 中端点文字被覆盖。',
                         'problem': '端点文字被覆盖。' if status != 'pass' else '',
                         'action': '移开标签并复核所有尺寸。' if status != 'pass' else '',
                         'expected': items[0]['statement'] if status != 'pass' else ''}], 'problems': []}


def checked_group_case(*, status='pass', extra_refs=()):
    items, catalog, context = group_case()
    groups = validation.media_evidence_groups(catalog, review_context=context)
    response = response_for(items, [*groups, 'steps', *extra_refs], status=status)
    checked = validation.validate_verdicts(items, response, catalog,
        evidence_groups=groups, review_context=context)
    return {**context, **checked, 'evidence_groups': groups, 'evidence_catalog': catalog,
            'review_problems': []}, items


def test_groups_expand_to_all_frames_and_both_caption_forms_with_specific_frame_overlap():
    report, _ = checked_group_case(extra_refs=['narrow'])
    assert report['ok'], report['issues']
    assert set(report['verdicts'][0]['evidence_ids']) == {'wide', 'narrow', 'vtt', 'transcript', 'steps'}
    assert len(report['verdicts'][0]['evidence_ids']) == 5
    assert set(group['name'] for group in report['evidence_groups'].values()) == {
        'all_rendered_frames', 'all_captions'}


def test_group_fail_and_needs_review_are_never_approval_and_only_fail_authorizes_revision():
    for status in ('fail', 'needs_review'):
        report, items = checked_group_case(status=status)
        assert not report['ok']
        assert [issue['code'] for issue in report['issues']] == ['MEDIA_CRITERION_UNRESOLVED']
        failures = validation.actionable_media_failures(report, items=items)
        assert len(failures) == (1 if status == 'fail' else 0)
        if failures:
            assert set(failures[0]['evidence_ids']) == {'wide', 'narrow', 'vtt', 'transcript', 'steps'}


@pytest.mark.parametrize('refs', [[], ['narrow']])
def test_concrete_failure_remains_actionable_without_all_group_citations(refs):
    items, catalog, context = group_case()
    groups = validation.media_evidence_groups(catalog, review_context=context)
    response = response_for(items, refs, status='fail')
    checked = validation.validate_verdicts(items, response, catalog,
        evidence_groups=groups, review_context=context)
    assert not checked['ok']
    assert checked['verdicts'][0]['evidence_ids'] == refs
    assert [issue['code'] for issue in checked['issues']] == ['MEDIA_CRITERION_UNRESOLVED']
    report = {**context, **checked, 'evidence_groups': groups, 'evidence_catalog': catalog}
    failures = validation.actionable_media_failures(report, items=items)
    assert len(failures) == 1
    assert failures[0]['evidence_ids'] == refs


def test_unknown_reference_is_not_dropped_to_create_an_actionable_failure():
    report, items = checked_group_case(status='fail', extra_refs=['invented'])
    assert not report['ok']
    assert report['verdicts'] == report['raw_verdicts']
    assert validation.actionable_media_failures(report, items=items) == []


@pytest.mark.parametrize('damage', ['missing', 'extra', 'wrong_kind', 'duplicate', 'empty', 'name', 'unknown'])
def test_modified_group_members_or_identity_cannot_pass_or_authorize_revision(damage):
    items, catalog, context = group_case()
    groups = validation.media_evidence_groups(catalog, review_context=context)
    original_ids = list(groups)
    frame_id = next(eid for eid, group in groups.items() if group['name'] == 'all_rendered_frames')
    group = groups[frame_id]
    if damage == 'missing': group['evidence_ids'].remove('narrow')
    elif damage == 'extra': group['evidence_ids'].append('invented')
    elif damage == 'wrong_kind': group['evidence_ids'].append('vtt')
    elif damage == 'duplicate': group['evidence_ids'].append('wide')
    elif damage == 'empty': group['evidence_ids'] = []
    elif damage == 'name': group['name'] = 'all_objects'
    else: groups['group:invented'] = groups.pop(frame_id)
    checked = validation.validate_verdicts(items, response_for(items, [*original_ids, 'steps'], status='fail'),
        catalog, evidence_groups=groups, review_context=context)
    report = {**context, **checked, 'evidence_groups': groups, 'evidence_catalog': catalog}
    assert not checked['ok']
    assert validation.actionable_media_failures(report, items=items) == []


@pytest.mark.parametrize('field', ['request_hash', 'result_hash', 'criteria_digest', 'policy_digest',
                                  'display_profiles_hash', 'review_items', 'pixels', 'captions', 'step'])
def test_previous_review_groups_cannot_be_reused_after_any_bound_evidence_changes(field):
    items, catalog, context = group_case()
    groups = validation.media_evidence_groups(catalog, review_context=context)
    if field == 'pixels': catalog['narrow']['sha256'] = 'sha256:new-pixels'
    elif field == 'captions': catalog['transcript']['sha256'] = 'sha256:new-text'
    elif field == 'step': catalog['wide']['step_id'] = 'S2'
    elif field == 'review_items': context[field] = []
    else: context[field] = digest_of('changed')
    checked = validation.validate_verdicts(items, response_for(items, [*groups, 'steps']), catalog,
        evidence_groups=groups, review_context=context)
    assert not checked['ok']


def test_group_without_actual_frames_and_group_not_declared_by_host_are_rejected():
    items, catalog, context = group_case()
    groups = validation.media_evidence_groups(catalog, review_context=context)
    response = response_for(items, [*groups, 'steps'])
    assert not validation.validate_verdicts(items, response, catalog)['ok']
    catalog = {key: value for key, value in catalog.items() if value['kind'] != 'rendered_frame'}
    assert validation.media_evidence_groups(catalog, review_context=context) == {}
    assert not validation.validate_verdicts(items, response, catalog,
        evidence_groups=groups, review_context=context)['ok']


@pytest.mark.parametrize('damage', ['raw', 'expanded', 'group', 'missing_raw', 'missing_groups', 'missing_both'])
def test_saved_group_report_cannot_reload_after_citations_or_members_are_tampered(tmp_path, damage):
    request, result = code_case(tmp_path)
    report = validation.validate_media(request, result, client=GroupReviewer())
    assert report['ok'], report['issues']
    original_digest = validation_digest(report)
    if damage == 'raw': report['raw_verdicts'][0]['evidence_ids'].append('invented')
    elif damage == 'expanded': report['verdicts'][0]['why'] = '改写模型的判断。'
    elif damage == 'group': next(iter(report['evidence_groups'].values()))['evidence_ids'].pop()
    elif damage == 'missing_raw': report.pop('raw_verdicts')
    elif damage == 'missing_groups': report.pop('evidence_groups')
    else:
        report.pop('evidence_groups')
        report.pop('raw_verdicts')
    assert validation_digest(report) != original_digest
    with pytest.raises(ContractError):
        validation.validate_media_report(request, result, report)


def test_legacy_explicit_evidence_id_protocol_still_passes_without_groups():
    items, catalog, _ = group_case()
    checked = validation.validate_verdicts(items, response_for(items, list(catalog)), catalog)
    assert checked['ok'], checked['issues']


def test_stripping_both_group_fields_from_current_report_cannot_authorize_revision(tmp_path):
    request, result = code_case(tmp_path)
    report = validation.validate_media(request, result, client=GroupReviewer(status='fail'))
    assert validation.actionable_media_failures(report, items=report['review_items'])
    report.pop('raw_verdicts')
    report.pop('evidence_groups')
    assert validation.actionable_media_failures(report, items=report['review_items']) == []


def test_bad_group_citation_in_a_sibling_does_not_hide_independently_valid_failure():
    items, catalog, context = group_case()
    items.append({**deepcopy(items[0]), 'criterion_id': 'host.animation.step-caption'})
    groups = validation.media_evidence_groups(catalog, review_context=context)
    response = response_for(items, [*groups, 'steps'], status='fail')
    response['verdicts'].append({**deepcopy(response['verdicts'][0]),
        'criterion_id': items[1]['criterion_id'], 'evidence_ids': ['group:invented', 'steps']})
    checked = validation.validate_verdicts(items, response, catalog,
        evidence_groups=groups, review_context=context)
    report = {**context, **checked, 'evidence_groups': groups, 'evidence_catalog': catalog}
    assert [row['criterion_id'] for row in validation.actionable_media_failures(report, items=items)] == [
        'host.code.author-step-coverage']
