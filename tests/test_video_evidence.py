import hashlib
import json
from pathlib import Path
import pytest
from aiforstudy.core.video_evidence import validate_video_evidence, import_video_evidence


def fixture(tmp_path):
    import subprocess
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=size=100x80:rate=1:duration=10','-c:v','mpeg4','-y',str(tmp_path/'clip.mp4')], check=True)
    probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(tmp_path/'clip.mp4')]))
    tb = next(s['time_base'] for s in probe['streams'] if s['codec_type']=='video')
    m = {'schema_version':'1.0', 'course_id':'physics', 'media_id':'video', 'duration_seconds':10, 'width':100, 'height':80, 'time_base':tb, 'files':[{'id':'video', 'path':'clip.mp4', 'sha256':hashlib.sha256((tmp_path/'clip.mp4').read_bytes()).hexdigest()}], 'observations':[{'id':'o1', 'source_id':'video', 'start':1, 'end':2, 'bbox':[0,0,100,80], 'model_status':'verified'}]}
    p = tmp_path / 'manifest.json'; p.write_text(json.dumps(m)); return p, m


def test_import_and_model_verification(tmp_path):
    p, m = fixture(tmp_path)
    assert validate_video_evidence(p)['ok']
    out = import_video_evidence(p, tmp_path / 'out')
    stored = json.loads((Path(out['bundle']) / 'manifest.json').read_text())
    assert stored['observations'][0].get('host_verified') is not True
    assert (Path(out['bundle']) / 'clip.mp4').read_bytes() == (tmp_path / 'clip.mp4').read_bytes()


@pytest.mark.parametrize('change', ['escape','hash','negative','overflow','timebase','bbox','duplicate','reference'])
def test_reject_invalid(tmp_path, change):
    p, m = fixture(tmp_path)
    if change == 'escape': m['files'][0]['path'] = '../clip.mp4'
    if change == 'hash': m['files'][0]['sha256'] = '0'*64
    if change == 'negative': m['observations'][0]['start'] = -1
    if change == 'overflow': m['observations'][0]['end'] = 11
    if change == 'timebase': m['time_base'] = '0/0'
    if change == 'bbox': m['observations'][0]['bbox'] = [0,0,101,80]
    if change == 'duplicate': m['observations'].append(m['observations'][0])
    if change == 'reference': m['observations'][0]['source_id'] = 'missing'
    p.write_text(json.dumps(m))
    assert not validate_video_evidence(p)['ok']
    with pytest.raises(ValueError): import_video_evidence(p, tmp_path / 'out')


def test_projection_and_course_guard(tmp_path):
    from aiforstudy.core.video_evidence import project_video_evidence
    p, _ = fixture(tmp_path)
    bundle = Path(import_video_evidence(p, tmp_path / 'out')['bundle'])
    with pytest.raises(ValueError): project_video_evidence(bundle, tmp_path / 'view', course_id='complex-analysis')
    result = project_video_evidence(bundle, tmp_path / 'view', course_id='physics')
    assert 'currentTime' in (Path(result['index'])).read_text()
    assert (tmp_path / 'view' / 'clip.mp4').read_bytes() == (tmp_path / 'clip.mp4').read_bytes()


def test_reject_missing_evidence_and_external_symlink(tmp_path):
    p, m = fixture(tmp_path)
    m['observations'][0]['evidence_ids'] = ['absent']
    p.write_text(json.dumps(m))
    assert not validate_video_evidence(p)['ok']
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.write_bytes(b'video')
    (tmp_path / 'clip.mp4').unlink()
    (tmp_path / 'clip.mp4').symlink_to(outside)
    m['observations'][0]['evidence_ids'] = []
    p.write_text(json.dumps(m))
    assert not validate_video_evidence(p)['ok']


def test_media_stream_metadata_is_verified(tmp_path):
    import subprocess
    p, m = fixture(tmp_path)
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=size=100x80:rate=10:duration=1','-c:v','mpeg4','-y',str(tmp_path/'clip.mp4')], check=True)
    probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(tmp_path/'clip.mp4')]))
    v = next(s for s in probe['streams'] if s['codec_type']=='video')
    m.update(duration_seconds=float(probe['format']['duration']), time_base=v['time_base'], media_id='video')
    m['files'][0]['sha256'] = hashlib.sha256((tmp_path/'clip.mp4').read_bytes()).hexdigest()
    m['observations'][0].update(start=0, end=1)
    p.write_text(json.dumps(m))
    assert validate_video_evidence(p)['ok']
    for key, wrong in [('width',101), ('time_base','1/9999'), ('duration_seconds',30)]:
        bad = dict(m); bad[key] = wrong; p.write_text(json.dumps(bad))
        assert not validate_video_evidence(p)['ok'], key
    p.write_text(json.dumps(m)); (tmp_path/'clip.mp4').write_bytes(b'video')
    m['files'][0]['sha256'] = hashlib.sha256(b'video').hexdigest(); p.write_text(json.dumps(m))
    assert not validate_video_evidence(p)['ok']


def test_explicit_attachment_and_frozen_identity(tmp_path):
    from aiforstudy.core.video_evidence import attach_video_evidence, frozen_video_evidence
    p, m = fixture(tmp_path)
    with pytest.raises(ValueError, match='course mismatch'):
        attach_video_evidence(p, tmp_path/'parse', book_id='complex-analysis', unit_id='U')
    attach_video_evidence(p, tmp_path/'parse', book_id='physics', unit_id='U')
    rows = frozen_video_evidence(tmp_path/'parse', book_id='physics', unit_id='U')
    assert len(rows) == 1
    assert rows[0]['semantic_status'] == 'unverified'
    assert rows[0]['source_ref']['json_pointer'] == '/observations/0'
    assert rows[0]['source_ref']['sha256'] == rows[0]['fingerprint']
    assert frozen_video_evidence(tmp_path/'parse', book_id='physics', unit_id='OTHER') == []
    with pytest.raises(ValueError, match='course mismatch'):
        frozen_video_evidence(tmp_path/'parse', book_id='complex-analysis', unit_id='U')


def test_video_sources_are_frozen_and_exactly_grounded(tmp_path):
    from aiforstudy.core.video_evidence import attach_video_evidence
    from aiforstudy.pipeline.content_stages import _collect_source
    from aiforstudy.blocks.content.source_context import source_context_from_frozen
    from aiforstudy.blocks.content.content_policy import check_content, load_content_policy
    from test_content_generation_v3 import context
    from support.content_case import teaching_case
    ctx = context(tmp_path); p,m = fixture(tmp_path); m['course_id'] = 'B'; p.write_text(json.dumps(m))
    before = source_context_from_frozen(_collect_source(ctx)).digest
    attach_video_evidence(p, ctx.context.parse, book_id='B', unit_id='U')
    source = source_context_from_frozen(_collect_source(ctx))
    assert source.digest != before
    from copy import deepcopy
    ref = deepcopy(source.video_evidence[0]['source_ref'])
    content, _ = teaching_case()
    content['teaching_plan']['claims'][0]['source_refs'] = [ref]
    def errors(src):
        return [x for x in check_content(content, src, load_content_policy(src['learner_profile'])).blocking_issues if x['issue_key'].startswith('invalid-source:')]
    assert not errors(source.as_dict())
    for field, value in [('json_pointer','/observations/42'), ('id','unknown'), ('path','other.json'), ('sha256','0'*64), ('unit_id','OTHER')]:
        candidate=deepcopy(ref); candidate[field]=value
        content['teaching_plan']['claims'][0]['source_refs'] = [candidate]
        assert errors(source.as_dict()), field


def test_reader_snapshot_publishes_attached_evidence(tmp_path):
    from aiforstudy.core.video_evidence import attach_video_evidence
    from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
    from aiforstudy.blocks.render.snapshot_sources import validate_snapshot_sources
    from test_diagnostic_preview_boundary import run_fixture
    p,m=fixture(tmp_path); m['course_id']='test-book'; p.write_text(json.dumps(m))
    parse=tmp_path/'parse'; parse.mkdir()
    attached=attach_video_evidence(p,parse,book_id='test-book',unit_id='U')
    run=run_fixture(tmp_path)
    report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'preview',parse_root=parse)
    output=Path(report['output_dir']); data=json.loads((output/'data/data.js').read_text().split('window.__DATA__ = ',1)[1].rstrip().removesuffix(';'))
    assert data['video_evidence'][0]['unit_id']=='U'
    viewer=output/data['video_evidence'][0]['url']
    assert viewer.is_file() and (viewer.parent/'clip.mp4').is_file()
    assert 'video-evidence.js' in (output/'index.html').read_text()
    assert validate_snapshot_sources(report,run,parse)
    (Path(attached['bundle'])/'clip.mp4').chmod(0o644)
    (Path(attached['bundle'])/'clip.mp4').write_bytes(b'changed')
    with pytest.raises(Exception): validate_snapshot_sources(report,run,parse)


def test_unlocated_claim_is_preserved_without_fabricated_timestamp(tmp_path):
    from aiforstudy.core.video_evidence import project_video_evidence
    p,m=fixture(tmp_path); m['observations'][0].update(start=None,end=None,text='无定位候选')
    p.write_text(json.dumps(m))
    bundle=Path(import_video_evidence(p,tmp_path/'bundles')['bundle'])
    view=Path(project_video_evidence(bundle,tmp_path/'view')['index']).read_text()
    assert '无定位候选' in view and '无时间定位' in view
    assert 'data-time="None"' not in view


def test_recoverable_window_extraction_records_source_and_never_overwrites(tmp_path):
    from aiforstudy.core.video_evidence import extract_video_window
    p,m = fixture(tmp_path)
    out=tmp_path/'extracted'
    result=extract_video_window(tmp_path/'clip.mp4', out, start=2, end=4)
    meta=json.loads((out/'extraction.json').read_text())
    assert result['ok'] and meta['requested_source_interval_seconds']==[2,4]
    assert meta['source_sha256']==m['files'][0]['sha256']
    assert (out/'frame.png').is_file() and (out/'window.mp4').is_file()
    with pytest.raises(ValueError): extract_video_window(tmp_path/'clip.mp4', out, start=2, end=4)
    with pytest.raises(ValueError): extract_video_window(tmp_path/'clip.mp4', tmp_path/'bad', start=-1, end=4)


def test_declared_streams_cannot_misstate_probe(tmp_path):
    import subprocess
    p,m=fixture(tmp_path)
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(tmp_path/'clip.mp4')]))
    stream=probe['streams'][0]
    m['streams']=[{k:stream[k] for k in ('index','codec_type','codec_name','time_base')}]
    p.write_text(json.dumps(m)); assert validate_video_evidence(p)['ok']
    m['streams'][0]['codec_name']='h264'; p.write_text(json.dumps(m))
    assert not validate_video_evidence(p)['ok']


def test_observation_timing_cannot_point_to_non_video_or_invalid_confirmation(tmp_path):
    p,m=fixture(tmp_path)
    (tmp_path/'notes.txt').write_text('note')
    m['files'].append({'id':'notes','path':'notes.txt','sha256':hashlib.sha256(b'note').hexdigest()})
    m['observations'][0]['source_id']='notes'; p.write_text(json.dumps(m)); assert not validate_video_evidence(p)['ok']
    m['observations'][0]['source_id']='video'; m['observations'][0]['confirmation_pts_s']=11
    p.write_text(json.dumps(m)); assert not validate_video_evidence(p)['ok']


def test_regression_records_preserve_distinct_candidates_and_source_times(tmp_path):
    from aiforstudy.core.video_evidence import project_video_evidence
    p,m=fixture(tmp_path)
    m['observations']=[
        {'id':'minus','source_id':'video','start':1,'end':2,'category':'ocr_candidate','text':'指数 -27 的负号可能漏识','model_status':'verified'},
        {'id':'repeat','source_id':'video','start':2,'end':3,'category':'asr_candidate','text':'电磁科学 电磁科学'},
        {'id':'before','source_id':'video','start':4,'end':4,'category':'frame','text':'题干出现，图未出现'},
        {'id':'after','source_id':'video','start':5,'end':5,'category':'frame','text':'图出现'},
        {'id':'offset','source_id':'video','start':6.1,'end':6.2,'category':'timing','text':'模型时间与首次出现时间不同'},
        {'id':'qualification','source_id':'video','start':7,'end':8,'category':'asr_candidate','text':'不需要考虑万有引力，但不是说不考虑重力'},
    ]
    p.write_text(json.dumps(m,ensure_ascii=False)); result=import_video_evidence(p,tmp_path/'bundles')
    saved=json.loads((Path(result['bundle'])/'manifest.json').read_text())
    assert saved['observations']==m['observations'] and 'host_verified' not in saved['observations'][0]
    view=Path(project_video_evidence(Path(result['bundle']),tmp_path/'view')['index']).read_text()
    for snippet in ('-27','电磁科学 电磁科学','题干出现，图未出现','图出现','6.1','但不是说不考虑重力'):
        assert snippet in view


def test_view_separates_candidate_records_and_raw_assets(tmp_path):
    from aiforstudy.core.video_evidence import project_video_evidence
    p,m=fixture(tmp_path); m['observations'][0].update(category='asr_candidate',text='候选文本')
    p.write_text(json.dumps(m)); bundle=Path(import_video_evidence(p,tmp_path/'bundles')['bundle'])
    view=Path(project_video_evidence(bundle,tmp_path/'view')['index']).read_text()
    assert 'asr_candidate' in view and '原始文件' in view
    assert '语义核验' in view and m['files'][0]['sha256'] in view


def test_runtime_cache_identity_includes_video_implementation():
    from aiforstudy.pipeline.runtime_dependencies import STAGE_PACKAGE_PATHS
    for stage in ('lesson','render','delivery'):
        assert 'core/video_evidence.py' in STAGE_PACKAGE_PATHS[stage]
        assert 'core/course_library.py' in STAGE_PACKAGE_PATHS[stage]


def test_model_verified_is_labeled_as_model_claim(tmp_path):
    from aiforstudy.core.video_evidence import project_video_evidence
    p,_=fixture(tmp_path); bundle=Path(import_video_evidence(p,tmp_path/'bundles')['bundle'])
    page=Path(project_video_evidence(bundle,tmp_path/'view')['index']).read_text()
    assert '模型自报：verified' in page


def test_imported_bundle_is_read_only_and_revalidated(tmp_path):
    import stat
    p,_=fixture(tmp_path); bundle=Path(import_video_evidence(p,tmp_path/'out')['bundle'])
    assert not bundle.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert not (bundle/'clip.mp4').stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert validate_video_evidence(bundle/'manifest.json')['ok']


def test_failed_extraction_is_recorded(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from aiforstudy.core.video_evidence import extract_video_window
    import aiforstudy.core.video_evidence as module
    fixture(tmp_path)
    original_run = module.subprocess.run
    monkeypatch.setattr(module.subprocess, 'run', lambda *a,**k: SimpleNamespace(returncode=1, stderr='decode failed') if a[0][0]=='ffmpeg' else original_run(*a,**k))
    result=extract_video_window(tmp_path/'clip.mp4',tmp_path/'failed',start=1,end=2)
    assert not result['ok']
    assert json.loads((tmp_path/'failed/extraction.json').read_text())['results'][0]['stderr']=='decode failed'


def test_extraction_detects_source_change(tmp_path, monkeypatch):
    from aiforstudy.core.video_evidence import extract_video_window
    import aiforstudy.core.video_evidence as module
    fixture(tmp_path)
    original_run=module.subprocess.run; changed=[]
    def run(*args, **kwargs):
        result=original_run(*args,**kwargs)
        if args[0][0]=='ffmpeg' and not changed:
            (tmp_path/'clip.mp4').write_bytes(b'changed'); changed.append(True)
        return result
    monkeypatch.setattr(module.subprocess,'run',run)
    result=extract_video_window(tmp_path/'clip.mp4',tmp_path/'window',start=1,end=2)
    assert not result['ok'] and result['source_changed'] is True


def test_lesson_watches_absent_video_pointer_for_later_attachment(tmp_path, monkeypatch):
    from aiforstudy.pipeline import content_stages as stages
    from test_content_generation_v3 import context, scripted_client
    ctx=context(tmp_path)
    monkeypatch.setattr(stages._t(), '_gateway_client', lambda *_a,**_k: (scripted_client(),'offline'))
    result=stages.lesson_stage(ctx)
    assert result.execution_state.value == 'succeeded', result.error
    assert str(ctx.context.parse/'video-evidence/current.json') in result.input_files


def test_view_exposes_exact_manifest_for_observation_references(tmp_path):
    from aiforstudy.core.video_evidence import project_video_evidence
    p,_=fixture(tmp_path); bundle=Path(import_video_evidence(p,tmp_path/'bundles')['bundle'])
    view=Path(project_video_evidence(bundle,tmp_path/'view')['index'])
    assert (view.parent/'manifest.json').read_bytes()==(bundle/'manifest.json').read_bytes()
    assert 'href="manifest.json"' in view.read_text()


def test_reader_snapshot_detects_new_video_attachment(tmp_path):
    from aiforstudy.core.video_evidence import attach_video_evidence
    from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
    from aiforstudy.blocks.render.snapshot_sources import validate_snapshot_sources
    from test_diagnostic_preview_boundary import run_fixture
    p,m=fixture(tmp_path); m['course_id']='test-book'; p.write_text(json.dumps(m))
    parse=tmp_path/'parse'; parse.mkdir(); run=run_fixture(tmp_path)
    report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'preview',parse_root=parse)
    attach_video_evidence(p,parse,book_id='test-book',unit_id='U')
    with pytest.raises(Exception): validate_snapshot_sources(report,run,parse)


@pytest.mark.parametrize('name', ['./index.html', './manifest.json', 'clip.mp4/../index.html'])
def test_reject_noncanonical_reserved_alias(tmp_path, name):
    p,m=fixture(tmp_path)
    m['files'].append({'id':'alias','path':name,'sha256':hashlib.sha256(b'asset').hexdigest()})
    (tmp_path/'index.html').write_bytes(b'asset')
    p.write_text(json.dumps(m))
    assert not validate_video_evidence(p)['ok']


def test_reject_normalized_duplicate_alias(tmp_path):
    p,m=fixture(tmp_path)
    m['files'].append({'id':'alias','path':'./clip.mp4','sha256':m['files'][0]['sha256']})
    p.write_text(json.dumps(m))
    assert not validate_video_evidence(p)['ok']


def test_projection_revalidates_completed_asset_bytes(tmp_path, monkeypatch):
    from aiforstudy.core.video_evidence import project_video_evidence
    p,_=fixture(tmp_path); bundle=Path(import_video_evidence(p,tmp_path/'bundles')['bundle'])
    original=Path.write_text
    def corrupt_after_viewer(path, *args, **kwargs):
        result=original(path,*args,**kwargs)
        if path.name == 'index.html': (path.parent/'clip.mp4').write_bytes(b'corrupt')
        return result
    monkeypatch.setattr(Path,'write_text',corrupt_after_viewer)
    with pytest.raises(ValueError, match='projection'):
        project_video_evidence(bundle,tmp_path/'view')
