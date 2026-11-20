"""A prose finding must remain actionable without hiding correct media."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from aiforstudy.blocks.media.validation import _substantive_verdicts
from aiforstudy.core.review_mode import review_mode

PROBLEM = {'target_artifact_kind': 'content', 'status': 'fail',
           'reason': '正文上半圆的辐角表达漏了 |z|<1，请补上模的限制。'}


@pytest.mark.parametrize('location', ['problems', 'verdicts'])
def test_correct_media_does_not_inherit_prose_error(location):
    response = {'verdicts': [{'status': 'pass', 'reason': '图中上半单位圆盘与条件一致。'}]}
    response.setdefault(location, []).append(deepcopy(PROBLEM))
    report = _substantive_verdicts(response)
    assert report['ok'] is True
    assert report['review_outcome']['status'] == 'verified'
    assert (report['review_problems'] if location == 'problems' else report['verdicts'])[-1] == PROBLEM


def test_body_only_review_does_not_certify_unexamined_media():
    report = _substantive_verdicts({'verdicts': [], 'problems': [deepcopy(PROBLEM)]})
    assert report['ok'] is False
    assert report['review_outcome']['status'] == 'unverified'


def test_visible_media_error_still_fails_alongside_body_error():
    report = _substantive_verdicts({'verdicts': [{'status': 'fail', 'reason': '图中漏画内圆。'}],
                                  'problems': [deepcopy(PROBLEM)]})
    assert report['review_outcome']['status'] == 'known_error'
    assert report['review_outcome']['reasons'] == ['图中漏画内圆。']


@pytest.mark.parametrize('status,reason_field,expected', [
    ('fail', 'reason', 'known_error'), ('fail', 'why', 'known_error'),
    ('needs_review', 'reason', 'unverified'), (None, 'reason', 'unverified')])
def test_figures_stage_keeps_correct_picture_and_preserves_body_finding(tmp_path, monkeypatch, status, reason_field, expected):
    from test_content_generation_v3 import context
    from aiforstudy.pipeline import media_stages as stage
    from aiforstudy.blocks.media import code_execution

    ctx = context(tmp_path, 'figures')
    ctx.services.control = SimpleNamespace(check=lambda: None)
    content = {'unit_id': 'U', 'book_id': 'B', 'body_markdown': '上半圆也可表为 0<arg z<pi。'}
    request = {'request_id': 'R', 'kind': 'drawing'}
    problem = {'target_artifact_kind': 'content', reason_field: PROBLEM['reason']}
    if status is not None:
        problem['status'] = status
    report = _substantive_verdicts({'verdicts': [{'status': 'pass', 'reason': '实际图片正确。'}],
                                  'problems': [problem]})
    picture = tmp_path / 'picture.png'
    picture.write_bytes(b'fixture: rendering is outside this routing test')
    row = {'request_id': 'R', 'kind': 'drawing', 'source_dir': str(tmp_path),
           'resources': [{'kind': 'image', 'relative_path': picture.name}],
           'state': 'fulfilled', 'validation': report, 'review_outcome': report['review_outcome']}
    monkeypatch.setattr(stage, '_load_content', lambda *_: (content, {}))
    monkeypatch.setattr(stage, '_load', lambda *_: {'requests': [request], 'decisions': []})
    monkeypatch.setattr(stage, '_capabilities', lambda *_: {'code_generation': 'GeneratedMediaCodeV1'})
    monkeypatch.setattr(stage, 'validate_requirements', lambda *a, **k: None)
    monkeypatch.setattr(stage, '_client', lambda *a: None)
    monkeypatch.setattr(stage, 'resource_dependencies', lambda *a: {})
    monkeypatch.setattr(stage, '_plan_outcome', lambda *a: {'status': 'verified', 'reasons': []})
    monkeypatch.setattr(code_execution, 'execute_code', lambda *a, **k: deepcopy(row))
    with review_mode('balanced'):
        result = stage.figures_stage(ctx)
    assert result.execution_state.value == 'succeeded', result.error
    assert result.quality_state.value == 'needs_review'
    payload = json.loads((tmp_path/'work/products/figures/U.json').read_text())['payload']
    assert payload['requests'][0]['review_outcome']['status'] == 'verified'
    assert payload['review_outcome']['status'] == expected
    assert PROBLEM['reason'] in payload['review_outcome']['reasons']
    if status == 'fail':
        assert len(result.repair_requests) == 1
        assert result.repair_requests[0].owner == 'lesson'
        assert result.repair_requests[0].artifact_hashes.keys() == {'lesson:U'}
        assert payload['tickets'][0]['problem'] == PROBLEM['reason']
    else:
        assert not result.repair_requests
