"""Code receipts require approved inputs and actual-pixel semantic review.

The reviewer below is an offline transport fixture, never semantic evidence for
production. Browser captures and (for animation) ffmpeg decoding remain real.
"""
from copy import deepcopy
import base64
from io import BytesIO
import json
from pathlib import Path
import subprocess

import pytest

from aiforstudy.blocks.audit.evidence import resolve_json_pointer
from aiforstudy.blocks.media.math_context import ApprovedMathContext
from aiforstudy.blocks.media.validation import (
    media_review_items, result_digest, validate_media, validate_media_report,
)
from aiforstudy.blocks.media.review_policy import media_review_policy
from aiforstudy.core.digest import digest_file, digest_of, digest_text
from aiforstudy.errors import ContractError
from test_semantic_static_media import context_case, vector


class Reviewer:
    def __init__(self, rejected=None):
        self.rejected = rejected
        self.payload = None

    def chat_json(self, messages, *, tag, images):
        self.payload = json.loads(messages[-1]['content'])
        assert images and all(Path(p).is_file() for p in images)
        rows = []
        for item in self.payload['review_items']:
            failed = item['criterion_id'] == self.rejected
            row = {'criterion_id': item['criterion_id'], 'version': item['version'],
                   'status': 'fail' if failed else 'pass',
                   'why': 'Offline response fixture for receipt validation, not a mathematical proof.',
                   'evidence_ids': list(self.payload['evidence_catalog'])}
            if failed:
                row.update(problem='向量端点与批准数值不符。', action='将向量端点改为批准的 (2,1)，重新渲染。',
                           expected=item['statement'])
            rows.append(row)
        return {'data': {'verdicts': rows, 'problems': []}}


def code_case(tmp_path, *, animation=False):
    _, content, approval, request = context_case(
        kind='animation' if animation else 'geometry', objects=[vector('a', [2, 1])])
    request['kind'] = 'animation' if animation else 'drawing'
    if animation:
        request['animation']['steps'] = ['显示向量起点。', '平移向量并比较。']
    context = ApprovedMathContext.from_approved_content(content, approval, request)
    steps = [{'step_id': 'S'+str(i+1), 'author_step_index': i, 'caption': text,
              'observation': '向量方向和长度保持。', 'expected_change': text}
             for i, text in enumerate(request['animation']['steps'])] if animation else []
    program = {'schema_version': 'GeneratedMediaCodeV1', 'request_id': request['request_id'],
               'backend': 'manim' if animation else 'matplotlib',
               'code': ('from manim import Scene\nfrom aifs_media_runtime import media_step\n'
                        'class GeneratedScene(Scene):\n    def construct(self):\n'
                        '        with media_step(self, "S1"):\n            self.wait(1)\n'
                        '        with media_step(self, "S2"):\n            self.wait(1)\n') if animation else
                       ('from matplotlib.figure import Figure\n'
                        'def build_figure(context):\n    figure = Figure()\n'
                        '    figure.add_subplot().arrow(0, 0, 2, 1)\n    return figure\n'),
               'object_bindings': [{'display_id': 'arrow', 'model_id': 'M', 'object_id': 'a'}],
               'steps': steps}
    row = {'request_id': request['request_id'], 'kind': request['kind'],
           'request_hash': digest_of(request), 'criteria_digest': request['criteria_digest'],
           'source_dir': str(tmp_path), 'candidate_spec': program, 'spec_hash': digest_of(program),
           'math_context_digest': context.digest,
           'approved_dependencies': [{**d, 'value': resolve_json_pointer(content, d['pointer'])[1]}
                                     for d in request['dependency_paths']],
           'resources': []}
    bind_program(row)
    if not animation:
        path = tmp_path/'figure.svg'
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500">'
                        '<rect width="800" height="500" fill="white"/>'
                        '<path d="M100 400 L600 150" stroke="blue" stroke-width="8"/>'
                        '<text x="150" y="100" font-size="40">a = (2, 1)</text></svg>')
        row['resources'] = [resource(path, 'svg')]
    return request, row


def bind_program(row):
    program = row['candidate_spec']
    row['spec_hash'] = digest_of(program)
    row['code_product'] = {'schema_version': 'GeneratedMediaProductV1',
        'program_hash': digest_of(program), 'code_hash': digest_text(program['code']),
        'context_digest': row['math_context_digest'], 'backend': program['backend'],
        'input_binding_status': 'pass', 'mathematical_status': 'requires_visual_review',
        'object_bindings': deepcopy(program['object_bindings']), 'steps': deepcopy(program['steps'])}


def resource(path, kind, **metadata):
    mimes = {'svg': 'image/svg+xml', 'mp4': 'video/mp4', 'poster': 'image/png',
             'keyframe': 'image/png', 'captions': 'text/vtt; charset=utf-8',
             'transcript': 'text/plain; charset=utf-8'}
    return {'kind': kind, 'relative_path': path.name, 'sha256': digest_file(path),
            'bytes': path.stat().st_size, 'mime': mimes[kind], **metadata}


def test_code_drawing_can_only_pass_after_actual_correspondence_review(tmp_path):
    request, row = code_case(tmp_path)
    pending = validate_media(request, row)
    assert [i['code'] for i in pending['issues']] == ['MEDIA_CALL_FAILED']
    assert pending['review_failure']['kind'] == 'internal'
    assert pending['math_report']['status'] == 'needs_review'
    reviewer = Reviewer()
    report = validate_media(request, row, client=reviewer)
    assert report['ok'], report['issues']
    assert report['math_report']['status'] == 'pass'
    assert report['math_report']['basis'] == 'approved_inputs_and_visual_review'
    assert row['code_product']['mathematical_status'] == 'requires_visual_review'
    criterion = next(i for i in report['review_items'] if i['criterion_id'] == 'host.code.mathematical-correspondence')
    assert {'rendered_frame', 'approved_dependencies', 'object_bindings'} <= set(criterion['required_evidence_kinds'])
    assert validate_media_report(request, row, report)


@pytest.mark.parametrize('damage', ['missing', 'outside', 'hash', 'context', 'approval', 'proof'])
def test_invalid_code_bindings_cannot_reach_semantic_approval(tmp_path, damage):
    request, row = code_case(tmp_path)
    if damage == 'missing': row['candidate_spec']['object_bindings'] = []
    elif damage == 'outside': row['candidate_spec']['object_bindings'][0]['object_id'] = 'unapproved'
    elif damage == 'hash': row['candidate_spec']['code'] += '\n# different source\n'
    elif damage == 'context': row['code_product']['context_digest'] = 'sha256:'+'f'*64
    elif damage == 'approval': row['approved_dependencies'][-1]['value'] = 'changed'
    elif damage == 'proof': row['code_product']['mathematical_status'] = 'pass'
    if damage in ('missing', 'outside'): bind_program(row)
    reviewer = Reviewer()
    report = validate_media(request, row, client=reviewer)
    assert not report['ok'] and reviewer.payload is None
    assert any(i['code'] in ('MEDIA_CODE_BINDING', 'MEDIA_APPROVED_CONTEXT') for i in report['issues'])
    assert report['math_report']['status'] != 'pass'


def test_empty_capture_cannot_approve_generated_picture(monkeypatch, tmp_path):
    request, row = code_case(tmp_path)
    monkeypatch.setattr('aiforstudy.blocks.media.validation.capture_display', lambda *a, **kw: [])
    reviewer = Reviewer()
    report = validate_media(request, row, client=reviewer)
    assert not report['ok'] and reviewer.payload is None
    assert any(i['code'] == 'MEDIA_FRAME_COVERAGE' for i in report['issues'])


def test_optional_citations_still_require_complete_current_actual_frames(tmp_path):
    class UncitedReviewer(Reviewer):
        def chat_json(self, messages, *, tag, images):
            response = super().chat_json(messages, tag=tag, images=images)
            for verdict in response['data']['verdicts']:
                verdict['evidence_ids'] = []
            return response

    request, row = code_case(tmp_path)
    report = validate_media(request, row, client=UncitedReviewer())
    assert report['ok'], report['issues']
    assert validate_media_report(request, row, report)
    report['evidence_catalog'] = {eid:entry for eid,entry in report['evidence_catalog'].items()
        if entry.get('profile_id') != 'wide'}
    with pytest.raises(ContractError, match='display profiles|viewport/actual-frame'):
        validate_media_report(request, row, report)


def test_failed_math_review_preserves_concrete_repair_and_frozen_standard(tmp_path):
    request, row = code_case(tmp_path)
    report = validate_media(request, row, client=Reviewer('host.code.mathematical-correspondence'))
    assert not report['ok'] and report['math_report']['status'] == 'needs_review'
    issue = next(i for i in report['issues'] if i['code'] == 'MEDIA_CRITERION_UNRESOLVED')
    assert issue['problem'] == '向量端点与批准数值不符。'
    assert '(2,1)' in issue['action']
    item = next(i for i in report['review_items'] if i['criterion_id'] == issue['criterion_id'])
    assert issue['expected'] == item['statement']


@pytest.mark.parametrize('kind', ['approved_dependencies', 'request', 'mathematical_report'])
def test_code_report_rejects_rewritten_approved_evidence(tmp_path, kind):
    request, row = code_case(tmp_path)
    report = validate_media(request, row, client=Reviewer())
    assert report['ok'], report['issues']
    for entry in report['evidence_catalog'].values():
        if entry['kind'] == kind: entry['data'] = []
    with pytest.raises(ContractError, match='approved|批准|code'):
        validate_media_report(request, row, report)


@pytest.mark.parametrize('profile',['draft','final'])
def test_code_animation_stages_preserve_author_coverage_and_real_times(tmp_path,profile):
    request, row = code_case(tmp_path, animation=True)
    video = tmp_path/'video.mp4'
    video_format='640x360:r=15' if profile=='draft' else '1280x720:r=30'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'color=c=white:s='+video_format, '-t', '2', '-an', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', str(video)], check=True, capture_output=True, timeout=30)
    steps = [{**s, 'kind': 'change', 'start': i, 'end': i+1}
             for i, s in enumerate(row['candidate_spec']['steps'])]
    captions = tmp_path/'captions.vtt'
    captions.write_text('WEBVTT\n\n'+'\n\n'.join(
        f"{s['step_id']}\n00:00:0{s['start']}.000 --> 00:00:0{s['end']}.000\n{s['caption']}" for s in steps)+'\n')
    transcript = tmp_path/'transcript.txt'
    transcript.write_text('\n'.join(s['caption'] for s in steps))
    resources = [resource(video, 'mp4'), resource(captions, 'captions'), resource(transcript, 'transcript')]
    for i, step in enumerate(steps):
        for phase, at in [('before', i+.1), ('observe', i+.5), ('after', i+.9)]:
            frame = tmp_path/f"{step['step_id']}-{phase}.png"
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-threads', '1', '-filter_threads', '1',
                            '-ss', str(at), '-i', str(video), '-frames:v', '1', '-update', '1', str(frame)],
                           check=True, capture_output=True, timeout=30)
            resources.append(resource(frame, 'keyframe', step_id=step['step_id'], phase=phase, timestamp=at))
    poster=tmp_path/'poster.png'
    poster.write_bytes((tmp_path/'S1-before.png').read_bytes())
    resources.append(resource(poster, 'poster'))
    row.update(resources=resources, render_profile=profile, animation={
        'schema_version': '1.0', 'request_id': request['request_id'], 'status': 'needs_review',
        'spec_hash': row['spec_hash'], 'compiler_hash': 'sha256:'+'a'*64, 'environment_hash': 'sha256:'+'b'*64,
        'resources': deepcopy(resources), 'steps': steps, 'source_binding': {},
        'math_report': {'status': 'needs_review'}, 'frame_report': None,
        'technical_report': {'ffmpeg_decode_ok': True}})
    report = validate_media(request, row, client=Reviewer())
    assert report['ok'], report['issues']
    assert any(i['criterion_id'] == 'host.code.author-step-coverage' for i in report['review_items'])
    before = result_digest(row)
    row['animation']['math_report'] = report['math_report']
    row['animation']['status'] = 'fulfilled'
    assert result_digest(row) == before
    assert validate_media_report(request, row, report)
    if profile=='final':
        from aiforstudy.blocks.render.diagnostics import _attach_production_animation
        row.update(state='fulfilled',validation=report)
        payload=_attach_production_animation(tmp_path/'reader',unit='U',book_id='B',
            animation=row['animation'],sources={r['relative_path']:tmp_path/r['relative_path'] for r in resources},
            profile=profile,request=request,row=row,review={'status':'verified'})
        assert payload['production_accepted'] is True and payload['preview_only'] is False
        assert payload['missing']==[] and payload['technical_checks']['decode_ok']
        from aiforstudy.blocks.media.evidence_digest import validation_digest
        assert payload['validation_hash']==validation_digest(report)
    without_frames=deepcopy(row)
    without_frames['resources']=[r for r in without_frames['resources'] if r.get('phase')!='after']
    without_frames['animation']['resources']=deepcopy(without_frames['resources'])
    rejected=validate_media(request,without_frames,client=Reviewer())
    assert not rejected['ok'] and any(i['code']=='MEDIA_FRAME_COVERAGE' for i in rejected['issues'])
    row['animation']['steps'][1]['author_step_index'] = 0
    rejected = validate_media(request, row, client=Reviewer())
    assert any(i['code'] == 'MEDIA_CODE_BINDING' for i in rejected['issues'])


def test_code_animation_requires_host_step_criterion(tmp_path):
    request, row = code_case(tmp_path, animation=True)
    items = media_review_items(request, row, media_review_policy())
    assert any(i['criterion_id'] == 'host.code.author-step-coverage' for i in items)


def test_matplotlib_embedded_raster_is_reviewed_in_the_actual_svg(tmp_path):
    from PIL import Image
    request, row = code_case(tmp_path)
    pixels=BytesIO()
    Image.new('RGB',(8,8),'blue').save(pixels,format='PNG')
    inline=base64.b64encode(pixels.getvalue()).decode('ascii')
    svg=tmp_path/'figure.svg'
    # Matplotlib wraps base64 data with whitespace in its standalone SVG.
    svg.write_text(svg.read_text().replace('</svg>',
        f'<image x="200" y="150" width="100" height="100" href="data:image/png;base64,\n{inline}"/></svg>'))
    row['resources']=[resource(svg,'svg')]
    report=validate_media(request,row,client=Reviewer())
    assert report['ok'],report['issues']
    frames=[e for e in report['evidence_catalog'].values() if e['kind']=='rendered_frame']
    assert {f['source_path'] for f in frames}=={'figure.svg'}
    assert validate_media_report(request,row,report)


@pytest.mark.parametrize('href',[
    'https://example.invalid/remote.png',
    'data:image/svg+xml;base64,PHN2Zy8+',
    'data:image/png;base64,bm90LWFuLWltYWdl',
    'data:image/png;base64,invalid&payload',
])
def test_embedded_svg_image_cannot_hide_external_or_nonraster_content(tmp_path,href):
    from aiforstudy.blocks.media.validation import _safe_svg
    path=tmp_path/'image.svg'
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg"><image href="{href}"/></svg>')
    with pytest.raises(ContractError): _safe_svg(path)


def test_embedded_raster_dimension_limit_is_enforced(tmp_path):
    from PIL import Image
    from aiforstudy.blocks.media.validation import _safe_svg
    pixels=BytesIO()
    Image.new('RGB',(8193,1),'blue').save(pixels,format='PNG')
    inline=base64.b64encode(pixels.getvalue()).decode('ascii')
    path=tmp_path/'wide.svg'
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,{inline}"/></svg>')
    with pytest.raises(ContractError,match='dimensions'): _safe_svg(path)
