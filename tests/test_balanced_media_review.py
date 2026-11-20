"""Limited media review uses real pixels and preserves unresolved findings."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from PIL import Image

from aiforstudy.core.review_mode import review_mode
from aiforstudy.core.digest import digest_file

ROOT = Path(__file__).parents[1]
CONTENT = {'unit_id': 'U', 'body_markdown': '向量终点应为 (2, 1)。'}
REQUEST = {'request_id': 'R', 'kind': 'drawing', 'caption': '观察向量', 'acceptance_criteria': []}
PROGRAM = {'code': 'from manim import Scene, Arrow, ORIGIN, RIGHT, UP\nclass GeneratedScene(Scene):\n    def construct(self):\n        self.add(Arrow(ORIGIN, 2*RIGHT+UP, buff=0))\n'}


class Reviewer:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def chat_json(self, messages, *, images, **kwargs):
        assert images and all(Path(p).is_file() for p in images)
        self.calls.append((deepcopy(messages), deepcopy(kwargs), list(images)))
        response = self.responses[min(len(self.calls)-1, len(self.responses)-1)]
        if isinstance(response, Exception):
            raise response
        return deepcopy(response) if isinstance(response, str) else {'data': deepcopy(response)}


class Maker:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or [PROGRAM]

    def chat_json(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        response = self.responses[min(len(self.calls)-1, len(self.responses)-1)]
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else {'data': deepcopy(response)}


def verdict(status):
    return {'verdicts': [{'status': status, 'reason': '箭头终点画成 (1,2)，与正文 (2,1) 不符，请交换坐标。'
                         if status == 'fail' else '实际画面与正文对应。' if status == 'pass' else '无法确定端点刻度。'}]}


def picture(tmp_path):
    path = tmp_path/'actual.png'
    Image.new('RGB', (300, 200), 'navy').save(path)
    return {'source_dir': str(tmp_path), 'resources': [{'kind': 'image', 'relative_path': path.name,
                                                      'sha256': digest_file(path)}]}


def test_balanced_reviews_real_picture_with_content_without_metadata_gates(tmp_path):
    from aiforstudy.blocks.media.validation import validate_media
    reviewer = Reviewer([verdict('fail')])
    with review_mode('balanced'):
        report = validate_media(REQUEST, picture(tmp_path), client=reviewer, content=CONTENT, workspace_root=ROOT)
    assert report['ok'] is False
    assert report['verdicts'] == verdict('fail')['verdicts']
    assert report['review_outcome']['status'] == 'known_error'
    assert len(reviewer.calls) == 1
    assert CONTENT['body_markdown'] in reviewer.calls[0][0][-1]['content']


@pytest.mark.parametrize('statuses,count,outcome', [(['fail', 'pass'], 2, 'verified'),
                                                  (['fail'], 3, 'known_error'),
                                                  (['needs_review'], 1, 'unverified')])
def test_balanced_real_code_revision_is_bounded_and_persisted(tmp_path, statuses, count, outcome):
    from aiforstudy.blocks.media.code_execution import execute_code
    maker, reviewer = Maker(), Reviewer([verdict(s) for s in statuses])
    kwargs = dict(content=CONTENT, approval={}, out_dir=tmp_path, client=maker,
                  reviewer=reviewer, workspace_root=ROOT)
    with review_mode('balanced'):
        row = execute_code(REQUEST, **kwargs)
        resumed = execute_code(REQUEST, previous=row, **kwargs)
    assert row['review_outcome']['status'] == outcome, row
    assert row['state'] == ('fulfilled' if outcome == 'verified' else 'needs_review')
    assert row['validation']['verdicts']
    assert len(maker.calls) == count and len(reviewer.calls) == count
    assert len(row['code_attempts']) == count == len(resumed['code_attempts'])
    assert all((Path(row['source_dir'])/r['relative_path']).is_file() for r in row['resources'])
    if count > 1:
        assert '箭头终点画成' in maker.calls[1][-1]['content']
    saved = json.loads((tmp_path/'result.json').read_text())
    assert saved['validation']['verdicts'] == row['validation']['verdicts']


def test_balanced_review_syntax_uses_five_corrections_then_keeps_unverified(tmp_path):
    from aiforstudy.blocks.media.validation import validate_media
    reviewer = Reviewer(['{"verdicts": ['])
    row = picture(tmp_path)
    with review_mode('balanced'):
        report = validate_media(REQUEST, row, client=reviewer, content=CONTENT, workspace_root=ROOT)
        replayed = validate_media(REQUEST, row, client=reviewer, content=CONTENT, workspace_root=ROOT)
    assert report['review_outcome']['status'] == 'unverified'
    assert len(reviewer.calls) == 6
    assert replayed['review_outcome']['status'] == 'unverified'
    assert '原' in reviewer.calls[1][0][-1]['content']
    assert list(tmp_path.rglob('*corrections*.json'))


def test_balanced_animation_reviews_decoded_final_video_without_step_declarations(tmp_path):
    from aiforstudy.blocks.media.code_runtime import render_generated_code
    from aiforstudy.blocks.media.validation import validate_media
    request = {**REQUEST, 'kind': 'animation'}
    code = 'from manim import *\nclass GeneratedScene(Scene):\n    def construct(self):\n        d=Dot()\n        self.add(d)\n        self.play(d.animate.shift(RIGHT),run_time=.4)\n'
    reviewer = Reviewer([verdict('pass')])
    with review_mode('balanced'):
        row = render_generated_code({'code': code}, context={'request': request}, out_dir=tmp_path,
                                    workspace_root=ROOT, profile='final')
        assert row['status'] == 'rendered', row
        assert row['review_mode'] == 'balanced'
        report = validate_media(request, row, client=reviewer, content=CONTENT, workspace_root=ROOT)
    assert report['ok'] is True, report
    frames = [e for e in report['evidence_catalog'].values() if e['kind'] == 'rendered_frame']
    times = {f['source_time_seconds'] for f in frames}
    assert len(times) == 1 and min(times) > .2
    assert all('poster' not in f['source_path'] for f in frames)
    assert len(reviewer.calls) == 1
    assert report['technical_checks'][0]['evidence']['sample_times_seconds']
    assert '无可用分步时点' in report['technical_checks'][0]['detail']
    assert len(reviewer.calls[0][0][-1]['image_labels']) == 1


@pytest.mark.parametrize('step_location', ['animation', 'root'])
def test_balanced_animation_review_receives_produced_steps_and_actual_caption_text(tmp_path, monkeypatch, step_location):
    from aiforstudy.blocks.media import validation
    row = picture(tmp_path)
    steps = [{'step_id': 'S1', 'start': 0, 'end': 2, 'caption': '制作分镜中的字幕',
              'observation': '观察终点', 'expected_change': '由左向右移动'}]
    if step_location == 'animation':
        row['animation'] = {'steps': deepcopy(steps)}
    else:
        row['steps'] = deepcopy(steps)
    texts = {'captions': 'WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n实际字幕：点由右向左移动。\n',
             'transcript': 'S1  00:00:00.000 — 00:00:02.000\n实际讲稿与分镜不同，须由审核判断。\n'}
    for kind, text in texts.items():
        path = tmp_path / ('captions.vtt' if kind == 'captions' else 'transcript.txt')
        path.write_text(text, encoding='utf-8')
        row['resources'].append({'kind': kind, 'relative_path': path.name, 'sha256': digest_file(path)})
    original = deepcopy(row)
    monkeypatch.setattr(validation, '_substantive_targets',
                        lambda *args: ([(row['resources'][0], tmp_path/'actual.png')], []))
    reviewer = Reviewer([verdict('needs_review')])
    with review_mode('balanced'):
        report = validation.validate_media({**REQUEST, 'kind': 'animation'}, row,
            client=reviewer, content=CONTENT, workspace_root=ROOT)
    assert len(reviewer.calls) == 1
    payload = json.loads(reviewer.calls[0][0][-1]['content'])
    assert payload['animation']['steps'] == steps
    captions = [e for e in payload['evidence_catalog'].values() if e['kind'] == 'captions']
    assert {e['caption_form']: e['text'] for e in captions} == texts
    assert all(e['sha256'] == digest_file(Path(e['path'])) for e in captions)
    assert report['review_outcome']['status'] == 'unverified'
    assert row == original


def test_balanced_animation_missing_optional_steps_and_text_still_reaches_reviewer(tmp_path, monkeypatch):
    from aiforstudy.blocks.media import validation
    row = picture(tmp_path)
    monkeypatch.setattr(validation, '_substantive_targets',
                        lambda *args: ([(row['resources'][0], tmp_path/'actual.png')], []))
    reviewer = Reviewer([verdict('needs_review')])
    with review_mode('balanced'):
        report = validation.validate_media({**REQUEST, 'kind': 'animation'}, row,
            client=reviewer, content=CONTENT, workspace_root=ROOT)
    assert len(reviewer.calls) == 1
    payload = json.loads(reviewer.calls[0][0][-1]['content'])
    assert 'animation' not in payload
    assert not any(e['kind'] == 'captions' for e in payload['evidence_catalog'].values())
    assert report['review_outcome']['status'] == 'unverified'
    assert report['review_failure'] == {}


@pytest.mark.parametrize('response', [RuntimeError('review provider unavailable'),
    {'verdicts': [], 'problems': [{'target_artifact_kind': 'content', 'problem': '正文数值自相矛盾，需核对正文。'}]}])
def test_unknown_transport_or_content_problem_does_not_rebuy_code(tmp_path, response):
    from aiforstudy.blocks.media.code_execution import execute_code
    maker, reviewer = Maker(), Reviewer([response])
    with review_mode('balanced'):
        row = execute_code(REQUEST, content=CONTENT, approval={}, out_dir=tmp_path,
                           client=maker, reviewer=reviewer, workspace_root=ROOT)
    assert row['resources'] and row['state'] == 'needs_review'
    assert len(maker.calls) == 1 and len(reviewer.calls) == 1
    if isinstance(response, Exception):
        assert 'review provider unavailable' in row['reason']
    else:
        assert row['validation']['review_problems'] == response['problems']


@pytest.mark.parametrize('exhausted', [False, True])
def test_code_json_corrections_do_not_multiply_candidate_attempts(tmp_path, exhausted):
    from aiforstudy.blocks.media.code_execution import execute_code
    maker = Maker(['{"code":', '{"code":', '{"code":' if exhausted else PROGRAM])
    reviewer = Reviewer([verdict('pass')])
    with review_mode('balanced'):
        row = execute_code(REQUEST, content=CONTENT, approval={}, out_dir=tmp_path,
                           client=maker, reviewer=reviewer, workspace_root=ROOT)
        if exhausted:
            resumed = execute_code(REQUEST, content=CONTENT, approval={}, out_dir=tmp_path,
                                   client=maker, reviewer=reviewer, workspace_root=ROOT, previous=row)
            assert len(resumed['code_attempts']) == 1
    assert len(maker.calls) == (6 if exhausted else 3) and len(row['code_attempts']) == 1, row
    assert len(reviewer.calls) == (0 if exhausted else 1)
    assert row['state'] == ('failed' if exhausted else 'fulfilled')


def test_failed_later_generation_preserves_last_real_media_and_original_verdict(tmp_path):
    from aiforstudy.blocks.media.code_execution import execute_code
    maker, reviewer = Maker([PROGRAM, RuntimeError('generation provider unavailable')]), Reviewer([verdict('fail')])
    with review_mode('balanced'):
        row = execute_code(REQUEST, content=CONTENT, approval={}, out_dir=tmp_path,
                           client=maker, reviewer=reviewer, workspace_root=ROOT)
    assert len(maker.calls) == 2 and len(reviewer.calls) == 1
    assert row['resources'] and row['state'] == 'needs_review'
    assert row['review_outcome']['status'] == 'known_error'
    assert row['validation']['verdicts'] == verdict('fail')['verdicts']
    assert 'generation provider unavailable' in row['reason']


def test_balanced_unknown_content_problem_is_not_declared_a_known_error(tmp_path):
    from aiforstudy.blocks.media.validation import validate_media
    reviewer = Reviewer([{'verdicts': [{'status': 'needs_review', 'reason': '无法判断正文是否正确。'}],
                          'problems': [{'target_artifact_kind': 'content', 'status': 'needs_review', 'reason': '无法判断。'}]}])
    with review_mode('balanced'):
        report = validate_media(REQUEST, picture(tmp_path), client=reviewer, content=CONTENT, workspace_root=ROOT)
    assert report['review_outcome']['status'] == 'unverified'


def test_valid_json_nonprogram_is_feedback_for_execution_not_a_metadata_gate(tmp_path):
    from aiforstudy.blocks.media.code_execution import execute_code
    maker, reviewer = Maker([42, PROGRAM]), Reviewer([verdict('pass')])
    with review_mode('balanced'):
        row = execute_code(REQUEST, content=CONTENT, approval={}, out_dir=tmp_path,
                           client=maker, reviewer=reviewer, workspace_root=ROOT)
    assert row['state'] == 'fulfilled', row
    assert len(maker.calls) == 2
    assert '42' in maker.calls[1][-1]['content']
