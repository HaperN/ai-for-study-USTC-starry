"""Completed teaching states, desktop evidence, and labelled repair input."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from aiforstudy.blocks.media import validation, code_runtime
from aiforstudy.core.digest import digest_file
from aiforstudy.core.review_mode import review_mode
from aiforstudy.providers.transport import RealTransport


@pytest.fixture
def video(tmp_path, monkeypatch):
    path = tmp_path/'video.mp4'
    path.write_bytes(b'local video fixture; decoding isolated below')
    monkeypatch.setattr(code_runtime, 'default_services', lambda **kw: SimpleNamespace(ffmpeg='unused'))
    monkeypatch.setattr(code_runtime, '_video', lambda *a, **kw:
        ({'streams': [{'codec_type': 'video', 'r_frame_rate': '30/1'}]}, 9.9, 297))
    def extract(path, destination, at, ffmpeg):
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (40, 30), (int(at*20), 0, 0)).save(destination)
    monkeypatch.setattr(code_runtime, '_frame', extract)
    return {'source_dir': str(tmp_path), 'resources': [
        {'kind': 'mp4', 'relative_path': path.name, 'sha256': digest_file(path)}],
        'animation': {'steps': [
            {'step_id': 'S1', 'start': 0, 'end': 2, 'caption': '原分式', 'observation': '保留分母非零条件'},
            {'step_id': 'S2', 'start': 2, 'end': 6, 'caption': '乘共轭后的完整展开', 'observation': '四项尚未合并'},
            {'step_id': 'S3', 'start': 6, 'end': 9.90000000000009, 'caption': '合并后的结果', 'observation': '分母是模平方'},
        ]}}


def test_ends_include_full_expansion_and_float_tail_without_transition_samples(video, tmp_path):
    targets, checks = validation._substantive_targets({'kind': 'animation'}, video, tmp_path, tmp_path)
    # Hand-computed last-frame timestamps at 30 fps, sought from prior ms.
    assert [r['timestamp'] for r, p in targets] == [1.966, 5.966, 9.866]
    assert [r['step_id'] for r, p in targets] == ['S1', 'S2', 'S3']
    assert [r['caption'] for r, p in targets] == ['原分式', '乘共轭后的完整展开', '合并后的结果']
    assert all(p.is_file() for r, p in targets)
    assert checks[0]['evidence']['sample_times_seconds'] == [1.966, 5.966, 9.866]


@pytest.mark.parametrize('steps', [[], [None, {'start': 0, 'end': float('nan')},
    {'start': 0, 'end': float('inf')}, {'start': 8, 'end': 50}]])
def test_missing_step_times_fall_back_to_one_final_image_with_limited_scope(video, tmp_path, steps):
    video['animation']['steps'] = steps
    targets, checks = validation._substantive_targets({'kind': 'animation'}, video, tmp_path, tmp_path)
    assert [r['timestamp'] for r, p in targets] == [9.866]
    assert not targets[0][0].get('step_id')
    assert '无可用分步时点' in checks[0]['detail']


def test_real_decoded_images_remain_inside_their_teaching_step(tmp_path, monkeypatch):
    import shutil
    import subprocess
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg or not shutil.which('ffprobe'):
        pytest.skip('local ffmpeg and ffprobe required')
    path = tmp_path/'states.mp4'
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    raw = b''.join(bytes(color) * (64*48*9) for color in colors)
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
        '-s', '64x48', '-r', '30', '-i', 'pipe:0', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-threads', '1', '-movflags', '+faststart', str(path)], input=raw, check=True, timeout=20)
    monkeypatch.setattr(code_runtime, 'default_services', lambda **kw: SimpleNamespace(ffmpeg=ffmpeg))
    row = {'source_dir': str(tmp_path), 'resources': [{'kind': 'mp4', 'relative_path': path.name,
        'sha256': digest_file(path)}], 'animation': {'steps': [
            {'step_id': 'S1', 'start': 0, 'end': .3},
            {'step_id': 'S2', 'start': .3, 'end': .6},
            {'step_id': 'S3', 'start': .6, 'end': .90000000000001}]}}
    with review_mode('balanced'):
        targets, checks = validation._substantive_targets({'kind': 'animation'}, row, tmp_path, tmp_path)
    assert [r['timestamp'] for r, p in targets] == [.266, .566, .866]
    for (_, path), expected in zip(targets, colors):
        with Image.open(path) as img:
            pixel = img.convert('RGB').getpixel((32, 24))
        assert all(abs(actual-want) < 8 for actual, want in zip(pixel, expected)), pixel
    assert checks[0]['evidence']['decoded_frames'] == 27


@pytest.fixture
def desktop_capture(monkeypatch):
    captures = []
    def capture(path, *, profiles, **kw):
        captures.append(deepcopy(profiles))
        return [{'profile_id': p['profile_id'], 'viewport_width': p['width'],
                 'content_width': p['content_width'], 'image_path': str(path),
                 'image_hash': digest_file(path)} for p in profiles]
    monkeypatch.setattr(validation, 'capture_display', capture)
    return captures


def wire_reviewer(tmp_path):
    from test_syntax_repair_policy import client_for
    client, calls = client_for(tmp_path, ['{"verdicts":', json.dumps({
        'verdicts': [{'status': 'pass', 'reason': '所给状态公式和条件正确。'}]}, ensure_ascii=False)])
    class Reviewer:
        def chat_json(self, messages, *, images, **kw):
            return client.chat_json(messages, images=images, **kw)
        @property
        def bodies(self):
            return [RealTransport(model='offline', api_key='offline').request_body(
                messages=call['messages'], images=call['images']) for call in calls]
    return Reviewer()


def test_static_review_has_labelled_image_and_syntax_repair_is_text_only(tmp_path, desktop_capture):
    path = tmp_path/'actual.png'
    Image.new('RGB', (780, 500), 'navy').save(path)
    row = {'source_dir': str(tmp_path), 'resources': [
        {'kind': 'image', 'relative_path': path.name, 'sha256': digest_file(path)}]}
    reviewer = wire_reviewer(tmp_path)
    with review_mode('balanced'):
        report = validation.validate_media({'request_id': 'R', 'kind': 'drawing', 'caption': '复平面'},
            row, client=reviewer, content={}, workspace_root=tmp_path)
    assert report['ok'], report
    assert [[p['width'] for p in profiles] for profiles in desktop_capture] == [[1280]]
    assert len(reviewer.bodies) == 2
    parts = reviewer.bodies[0]['messages'][-1]['content']
    assert [p['type'] for p in parts] == ['text', 'text', 'image_url']
    assert '复平面' in parts[1]['text'] and '电脑' in parts[1]['text']
    assert isinstance(reviewer.bodies[1]['messages'][-1]['content'], str)
    assert json.loads(reviewer.bodies[1]['messages'][-1]['content'])['original_response'] == '{"verdicts":'


def test_review_and_repair_keep_image_time_step_and_meaning_adjacent(video, tmp_path, desktop_capture):
    from aiforstudy.blocks.media.code_execution import _feedback
    reviewer = wire_reviewer(tmp_path)
    with review_mode('balanced'):
        report = validation.validate_media({'request_id': 'A', 'kind': 'animation'}, video,
            client=reviewer, content={}, workspace_root=tmp_path)
        feedback = _feedback({'request': {}, 'validation': report})
    assert report['ok'], report
    assert len(reviewer.bodies) == 2
    parts = reviewer.bodies[0]['messages'][-1]['content']
    assert [p['type'] for p in parts] == ['text', 'text', 'image_url', 'text', 'image_url', 'text', 'image_url']
    labels = [parts[i]['text'] for i in (1, 3, 5)]
    for label, expected in zip(labels, [
        ('S1', '1.966', '原分式', '保留分母非零条件'),
        ('S2', '5.966', '乘共轭后的完整展开', '四项尚未合并'),
        ('S3', '9.866', '合并后的结果', '分母是模平方'),
    ]):
        assert all(value in label for value in expected), label
    assert not any('image_labels' in message for body in reviewer.bodies for message in body['messages'])
    assert isinstance(reviewer.bodies[1]['messages'][-1]['content'], str)
    assert feedback['image_labels'] == labels
    assert feedback['images'] == [e['path'] for e in report['evidence_catalog'].values()
                                  if e['kind'] == 'rendered_frame']


@pytest.mark.parametrize('statuses,count,outcome', [
    (['fail', 'pass'], 2, 'verified'), (['fail'], 3, 'known_error'),
    (['needs_review'], 1, 'unverified'),
])
def test_only_confirmed_errors_revise_with_original_program_and_labelled_image(
        tmp_path, monkeypatch, desktop_capture, statuses, count, outcome):
    from aiforstudy.blocks.media.code_execution import execute_code
    monkeypatch.setattr(code_runtime, 'preflight_generated_code', lambda *a, **kw: {'ok': True})
    def render(program, *, out_dir, **kw):
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir/'actual.png'
        Image.new('RGB', (780, 500), 'navy').save(path)
        return {'status': 'rendered', 'source_dir': str(out_dir), 'resources': [
            {'kind': 'image', 'relative_path': path.name, 'sha256': digest_file(path)}]}
    monkeypatch.setattr(code_runtime, 'render_generated_code', render)
    generated = []
    maker_bodies = []
    review_calls = []
    class Maker:
        def chat_json(self, messages, *, images=None, **kw):
            maker_bodies.append(RealTransport(model='offline', api_key='offline').request_body(
                messages=messages, images=images))
            program = {'code': f'# Original candidate {len(generated) + 1}\nfrom manim import Scene\nclass GeneratedScene(Scene):\n    pass\n'}
            generated.append(program)
            return {'data': program}
    class Reviewer:
        def chat_json(self, messages, *, images, **kw):
            status = statuses[min(len(review_calls), len(statuses)-1)]
            review_calls.append(status)
            return {'data': {'verdicts': [{'status': status, 'reason':
                '蓝色端点(1,2)与正文(2,1)相反，请交换坐标。' if status == 'fail' else
                '未看到连续运动，无法确定全程同步。' if status == 'needs_review' else '所见端点正确。'}]}}
    with review_mode('balanced'):
        kwargs = dict(content={'body_markdown': '端点(2,1)'}, approval={}, out_dir=tmp_path,
                      client=Maker(), reviewer=Reviewer(), workspace_root=tmp_path)
        row = execute_code({'request_id': 'R', 'kind': 'drawing', 'caption': '坐标'}, **kwargs)
        resumed = execute_code({'request_id': 'R', 'kind': 'drawing', 'caption': '坐标'}, previous=row, **kwargs)
    assert row['review_outcome']['status'] == outcome
    assert len(generated) == len(review_calls) == len(row['code_attempts']) == count
    assert len(resumed['code_attempts']) == count
    for index, body in enumerate(maker_bodies[1:], 1):
        parts = body['messages'][-1]['content']
        assert [part['type'] for part in parts] == ['text', 'text', 'image_url']
        repair = json.loads(parts[0]['text'])['repair']
        assert repair['original_code'] == generated[index-1]['code']
        assert repair['previous_program']['code'] == generated[index-1]['code']
        assert '蓝色端点' in json.dumps(repair['feedback'], ensure_ascii=False)
        assert '电脑' in parts[1]['text'] and '坐标' in parts[1]['text']
