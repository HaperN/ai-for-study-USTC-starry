"""Diagnostic bytes can play without manufacturing a fulfilled AnimationResult."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from aiforstudy.core.digest import digest_file, digest_of
from aiforstudy.core.jsonio import write_json_atomic
from test_diagnostic_preview_boundary import run_fixture


def animation_fixture(tmp_path):
    from PIL import Image
    from aiforstudy.delivery.resources import mime_for
    run = run_fixture(tmp_path)
    root = run/'work/media/R'; root.mkdir(parents=True)
    video = root/'video.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=640x360:r=15',
        '-t', '1', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)],
        check=True, capture_output=True, timeout=30)
    for name, color in [('poster.png','blue'), ('keyframes/a.png','red'), ('keyframes/b.png','green')]:
        path=root/name;path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB',(64,36),color).save(path)
    (root/'captions.vtt').write_text('WEBVTT\n\nS1\n00:00:00.000 --> 00:00:01.000\n真实字幕\n', encoding='utf-8')
    (root/'transcript.txt').write_text('真实字幕\n', encoding='utf-8')
    resources=[]
    for kind,relative in [('mp4','video.mp4'),('poster','poster.png'),('keyframe','keyframes/a.png'),
        ('keyframe','keyframes/b.png'),('captions','captions.vtt'),('transcript','transcript.txt')]:
        path=root/relative
        resources.append({'kind':kind,'relative_path':relative,'sha256':digest_file(path),
            'bytes':path.stat().st_size,'mime':mime_for(relative)})
    req_path=run/'work/products/media_plan/U.json'
    req=json.loads(req_path.read_text());req['payload']['requests'][0]['kind']='animation'
    write_json_atomic(req_path,req)
    path=run/'work/products/figures/U.json';manifest=json.loads(path.read_text())
    row=manifest['payload']['requests'][0]
    row.update(kind='animation',source_dir=str(root),resources=resources,spec_hash=digest_of('frozen program'), render_profile='draft',
        animation={'schema_version':'1.0','request_id':'R','status':'needs_review','spec_hash':digest_of('frozen program'),
            'compiler_hash':digest_of('compiler'),'environment_hash':digest_of('environment'),'resources':deepcopy(resources),
            'steps':[{'step_id':'S1','caption':'真实字幕','start':0,'end':1}],
            'math_report':{'status':'needs_review'},'frame_report':None,'technical_report':{}})
    manifest['payload']['requirements_hash']=digest_of(req['payload'])
    write_json_atomic(path,manifest)
    return run,root,path


def data_at(report):
    source=(Path(report['output_dir'])/'data/data.js').read_text()
    return json.loads(source[source.index('=')+1:].strip().removesuffix(';'))


def test_unreviewed_animation_plays_as_independent_projection_and_keeps_all_keyframes(tmp_path):
    from aiforstudy.blocks.render.diagnostics import build_diagnostic_preview
    from aiforstudy.pipeline.stage_io import _legacy
    run,source,manifest=animation_fixture(tmp_path)
    before={str(p.relative_to(run)):digest_file(p) for p in run.rglob('*') if p.is_file()}
    report=build_diagnostic_preview(run,unit_ids=['U'],out_dir=tmp_path/'preview')
    payload=data_at(report)['animations']['U'][0]
    assert payload['schema_version']=='DiagnosticAnimationV1'
    from aiforstudy.blocks.visual.result import parse_animation_result
    from aiforstudy.errors import ContractError
    with pytest.raises(ContractError): parse_animation_result(payload)
    assert payload['preview_only'] is True and payload['production_accepted'] is False
    assert payload['status']=='needs_review' and payload['missing']==[]
    assert report['missing']==[] and report['problems']
    assert payload['resources']['mp4']['relative_path']=='video.mp4'
    assert payload['captions_text']==(source/'captions.vtt').read_text()
    assert payload['transcript_text']==(source/'transcript.txt').read_text()
    assert {r['relative_path'] for r in payload['resource_manifest'] if r['kind']=='keyframe'}=={'keyframes/a.png','keyframes/b.png'}
    target=Path(report['output_dir'])/payload['base']
    assert digest_file(target/'keyframes/a.png')==digest_file(source/'keyframes/a.png')
    assert digest_file(target/'keyframes/b.png')==digest_file(source/'keyframes/b.png')
    plan=json.loads((Path(report['output_dir'])/'media-placement-plan.json').read_text())
    assert len([r for r in plan['placements'][0]['resources'] if r['kind']=='keyframe'])==2
    assert before=={str(p.relative_to(run)):digest_file(p) for p in run.rglob('*') if p.is_file()}



@pytest.mark.parametrize('damage',['video','hash','different_resource_list','duplicate_primary'])
def test_invalid_diagnostic_animation_stays_visible_as_missing_without_promotion(tmp_path,damage):
    from aiforstudy.blocks.render.diagnostics import build_diagnostic_preview
    run,source,path=animation_fixture(tmp_path)
    manifest=json.loads(path.read_text());row=manifest['payload']['requests'][0]
    if damage in ('video','hash'):
        (source/'video.mp4').write_bytes(b'not a video')
        if damage=='video':
            for resources in (row['resources'],row['animation']['resources']):
                resources[0].update(sha256=digest_file(source/'video.mp4'),bytes=(source/'video.mp4').stat().st_size)
    elif damage=='different_resource_list': row['animation']['resources']=row['animation']['resources'][:-1]
    else:
        duplicate=deepcopy(row['resources'][0]);duplicate['relative_path']='another.mp4'
        (source/'another.mp4').write_bytes((source/'video.mp4').read_bytes())
        row['resources'].append(duplicate);row['animation']['resources'].append(deepcopy(duplicate))
    write_json_atomic(path,manifest)
    report=build_diagnostic_preview(run,unit_ids=['U'],out_dir=tmp_path/'preview')
    assert report['missing'] and report['problems'] and report['production_accepted'] is False
    for payload in data_at(report)['animations'].get('U',[]):
        assert payload['status']=='needs_review' and 'mp4' not in payload['resources']
    assert json.loads(path.read_text())['payload']['requests'][0]['animation']['status']=='needs_review'
