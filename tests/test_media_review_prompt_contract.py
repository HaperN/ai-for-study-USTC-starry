"""The model's copyable response examples must satisfy the real review protocol."""
from copy import deepcopy
import json
import re

import pytest

from aiforstudy.blocks.media.review_policy import build_media_review_prompt, code_review_criteria
from aiforstudy.blocks.media.validation import validate_verdicts


def examples(kind='drawing'):
    rows = [json.loads(text) for text in re.findall(r"```json\s*([\s\S]*?)\s*```", build_media_review_prompt(kind).text)]
    assert len(rows) >= 2, 'The initial shape and a failing response must both be copyable JSON.'
    return rows


def review(response, kind='drawing'):
    items = code_review_criteria(animation=kind == 'animation')
    for item in items:
        item['required_evidence_kinds'] = item.pop('evidence_required')
    catalog = {'frame:wide': {'kind': 'rendered_frame'},
               'frame:narrow': {'kind': 'rendered_frame'},
               'approved:objects': {'kind': 'approved_dependencies'},
               'bindings': {'kind': 'object_bindings'},
               'math-report': {'kind': 'mathematical_report'},
               'request': {'kind': 'request'}, 'steps': {'kind': 'steps'},
               'captions': {'kind': 'captions'}}
    return validate_verdicts(items, response, catalog)


@pytest.mark.parametrize('kind', ['drawing', 'animation'])
def test_initial_complete_response_example_is_protocol_valid(kind):
    initial = examples(kind)[0]
    assert review(initial, kind)['ok']
    assert set(initial['verdicts'][0]) == {
        'criterion_id', 'version', 'status', 'evidence_ids', 'why', 'problem', 'action', 'expected'}


@pytest.mark.parametrize('status', ['fail', 'needs_review'])
def test_failed_example_produces_actionable_revision_without_protocol_failure(status):
    response = deepcopy(next(row for row in examples() if row['verdicts'][0]['status'] == 'fail'))
    verdict = response['verdicts'][0]
    verdict['status'] = status
    report = review(response)
    assert not report['ok']
    assert [issue['code'] for issue in report['issues']] == ['MEDIA_CRITERION_UNRESOLVED']
    assert all(isinstance(verdict[key], str) and verdict[key].strip()
               for key in ('problem', 'action', 'expected'))
    assert verdict['expected'] == code_review_criteria()[0]['statement']
    assert report['issues'][0]['expected'] == code_review_criteria()[0]['statement']


def test_type_specific_review_prompt_keeps_animation_evidence_out_of_static():
    from aiforstudy.blocks.media import review_policy
    build = getattr(review_policy, 'build_media_review_prompt', None)
    assert callable(build), 'review prompt must select common and kind-specific assets'
    static = build('drawing').text
    animation = build('animation').text
    assert '字幕' not in static and '作者原始步骤' not in static
    assert '字幕' in animation and '作者原始步骤' in animation
    assert static != animation
