"""Local authority checks; semantic reviewer test doubles are not production review."""
from copy import deepcopy
from pathlib import Path
import pytest
from aiforstudy.core.digest import digest_of
from aiforstudy.errors import ContractError
from aiforstudy.blocks.media.validation import validate_verdicts, media_review_items
from aiforstudy.blocks.media.review_policy import media_review_policy


def request():
    rows=[{'criterion_id':'observe-vector','version':'1','kind':'pedagogical',
           'statement':'两个方向可分别观察','evidence_required':['rendered_frame']}]
    return {'request_id':'R','kind':'geometry','acceptance_criteria':rows,'criteria_digest':digest_of(rows)}


def catalog():
    return {'frame:wide':{'kind':'rendered_frame','sha256':'sha256:wide','profile_id':'wide'},
            'frame:narrow':{'kind':'rendered_frame','sha256':'sha256:narrow','profile_id':'narrow'},
            'objects':{'kind':'object_bindings','data':[]}}


def answer(items):
    return {'verdicts':[{'criterion_id':i['criterion_id'],'version':i['version'],'status':'pass',
        'why':'左右方向分别可见。','evidence_ids':list(catalog())} for i in items]}


def test_bool_and_prose_never_close_initial_review():
    items=media_review_items(request(),{},media_review_policy())
    report=validate_verdicts(items,{'accepted':True,'evidence':'看起来正常'},catalog())
    assert not report['ok']


def test_every_frozen_criterion_and_current_citation_identity_is_required():
    items=media_review_items(request(),{},media_review_policy())
    verdict=answer(items)
    assert validate_verdicts(items,verdict,catalog())['ok']
    for damage in ('omitted','wrong_version','fake_evidence'):
        broken=deepcopy(verdict)
        if damage=='omitted': broken['verdicts'].pop()
        elif damage=='wrong_version': broken['verdicts'][0]['version']='2'
        else: broken['verdicts'][0]['evidence_ids'].append('invented')
        assert not validate_verdicts(items,broken,catalog())['ok'],damage


@pytest.mark.parametrize('status', ['pass', 'fail'])
@pytest.mark.parametrize('refs', [[], ['frame:narrow'], ['frame:narrow', 'frame:narrow'], ['objects']])
def test_current_citations_locate_verdict_without_repeating_all_supplied_evidence(status, refs):
    items=[{'criterion_id':'host.code.mathematical-correspondence','version':'1',
            'statement':'实际端点与批准对象一致。',
            'required_evidence_kinds':['rendered_frame','object_bindings']}]
    response={'verdicts':[{'criterion_id':items[0]['criterion_id'],'version':'1','status':status,
        'why':'核对两种显示尺寸、批准对象及实际端点。','evidence_ids':refs,
        'problem':'窄屏端点标成错误数值。' if status=='fail' else '',
        'action':'改正端点标签并复核全部尺寸。' if status=='fail' else '',
        'expected':items[0]['statement'] if status=='fail' else ''}]}
    report=validate_verdicts(items,response,catalog())
    assert [i['code'] for i in report['issues']]==(['MEDIA_CRITERION_UNRESOLVED'] if status=='fail' else [])
    assert report['verdicts'][0]['status']==status
    assert report['verdicts'][0]['evidence_ids']==list(dict.fromkeys(refs))


def test_initial_and_repair_keep_identical_request_standards():
    r=request(); before=deepcopy(r)
    original=media_review_items(r,{},media_review_policy())
    repaired=media_review_items(r,{'repair_tickets':[]},media_review_policy())
    assert original==repaired and r==before
    r['acceptance_criteria'][0]['statement']='换过标准'
    with pytest.raises(ContractError): media_review_items(r,{},media_review_policy())


def static_row(tmp_path,size=40):
    from aiforstudy.core.digest import digest_file
    path=tmp_path/'figure.svg'
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500"><rect width="800" height="500" fill="white"/><text x="100" y="100" font-size="{size}">x = 0</text></svg>')
    r=request()
    product={'schema_version':'StaticMathProductV2','data':{},'data_hash':digest_of({}),
        'object_bindings':[],'relations':[],'technical_report':{'mathematical_status':'pass'}}
    row={'request_id':'R','kind':'geometry','request_hash':digest_of(r),'criteria_digest':r['criteria_digest'],
        'source_dir':str(tmp_path),'static_product':product,
        'resources':[{'kind':'svg','relative_path':path.name,'sha256':digest_file(path)}]}
    return r,row


def test_actual_browser_catches_16px_svg_scaled_below_14px(tmp_path):
    from aiforstudy.blocks.media.validation import validate_media
    r,row=static_row(tmp_path,16)
    report=validate_media(r,row)
    assert not report['ok']
    assert any(i['code']=='MEDIA_LABEL_READABILITY' for i in report['warnings'])
    assert any(i['code']=='MEDIA_CALL_FAILED' for i in report['issues'])
    assert report['review_failure']['kind']=='internal'
    frames=[e for e in report['evidence_catalog'].values() if e['kind']=='rendered_frame']
    assert {f['viewport_width'] for f in frames}=={1280}
    desktop=frames[0]
    assert desktop['media_width']==450
    assert desktop['labels'][0]['font_px']==pytest.approx(16*450/800)
    assert Path(desktop['path']).is_file()


def test_profile_override_cannot_omit_occupying_scrollbar_coverage():
    from aiforstudy.blocks.media.validation import _profiles
    from aiforstudy.blocks.media.review_policy import DEFAULT_DISPLAY_PROFILES
    profiles=deepcopy(list(DEFAULT_DISPLAY_PROFILES))
    next(p for p in profiles if p['profile_id']=='wide')['content_width']=458
    with pytest.raises(ContractError,match='scrollbar'):
        _profiles(profiles,media_review_policy())


def test_actual_readable_pixels_are_not_semantic_approval(tmp_path):
    from aiforstudy.blocks.media.validation import validate_media
    r,row=static_row(tmp_path,40)
    report=validate_media(r,row)
    assert not report['ok'] and report['decision']=='needs_review'
    assert [i['code'] for i in report['issues']]==['MEDIA_CALL_FAILED']
    assert report['review_failure']['kind']=='internal'


def test_actual_math_failure_cannot_be_overridden_by_optional_citations(tmp_path):
    from aiforstudy.blocks.media.validation import validate_media
    r,row=static_row(tmp_path,40)
    row['static_product']['technical_report']['mathematical_status']='fail'
    report=validate_media(r,row)
    assert not report['ok']
    assert any(i['code']=='MEDIA_MATHEMATICS' for i in report['issues'])


@pytest.mark.parametrize('text',[
    'WEBVTT\n\n00:00:02.000 --> 00:00:01.000\n反向时段\n',
    'WEBVTT\n\na\n00:00:00.000 --> 00:00:02.000\n甲\n\nb\n00:00:01.000 --> 00:00:03.000\n乙\n',
    'WEBVTT\n\n00:99:00.000 --> 00:99:01.000\n越界\n',
    'WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n',
])
def test_malformed_subtitles_are_not_passed_by_a_timing_regex(tmp_path,text):
    from aiforstudy.checks.offline import check_webvtt
    p=tmp_path/'captions.vtt';p.write_text(text)
    assert not check_webvtt(p).ok


def test_real_decode_and_wrong_profile_report_different_facts(tmp_path):
    import subprocess
    from aiforstudy.checks.offline import check_animation_video
    p=tmp_path/'video.mp4'
    proc=subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=white:s=640x360:r=15',
        '-t','1','-an','-c:v','libx264','-pix_fmt','yuv420p',str(p)],capture_output=True,timeout=30)
    assert proc.returncode==0,proc.stderr
    good=check_animation_video(p,profile='draft')
    wrong_size=check_animation_video(p,profile='final')
    assert good.ok and good.evidence['decode_ok']
    assert not wrong_size.ok and wrong_size.evidence['decode_ok'] and not wrong_size.evidence['profile_ok']
    p.write_bytes(p.read_bytes()[:100])
    corrupt=check_animation_video(p,profile='draft')
    assert not corrupt.ok and not corrupt.evidence['decode_ok']


def test_v2_manifest_cannot_inherit_bool_image_review(tmp_path):
    from aiforstudy.blocks.media.media_manifest import validate_manifest,approve_media
    r,row=static_row(tmp_path)
    row.update(state='fulfilled',review={'accepted':True,'evidence':'看起来正常'})
    plan={'schema_version':'MediaRequirementsV2','content_hash':'c','requests':[r]}
    manifest={'schema_version':'MediaManifestV2','content_hash':'c','requirements_hash':digest_of(plan),'requests':[row]}
    assert not validate_manifest(plan,manifest)['ok']
    with pytest.raises(ContractError): approve_media(plan,manifest,{'decision':'accepted','content_hash':'c'})


def test_validation_digest_only_exempts_real_video_seconds():
    from aiforstudy.blocks.media.evidence_digest import validation_digest
    from aiforstudy.core.digest import DigestError
    report={'schema_version':'MediaValidationV2','technical_checks':[{'name':'animation.resources','evidence':{
        'video':{'name':'animation.video','evidence':{'duration':1.0,'video':{'codec_name':'h264','width':1280}}}}}]}
    original=validation_digest(report)
    report['technical_checks'][0]['evidence']['video']['evidence']['duration']=1.1
    assert validation_digest(report)!=original
    for value in ('2026-10-08',-1,float('nan'),True):
        report['technical_checks'][0]['evidence']['video']['evidence']['duration']=value
        with pytest.raises(DigestError): validation_digest(report)
    with pytest.raises(DigestError):validation_digest({'technical_checks':[{'name':'runtime','evidence':{'duration':1}}]})
    with pytest.raises(DigestError):validation_digest({'timestamp':1})


def test_malformed_reviewer_identity_is_an_evidence_issue_not_a_crash():
    items=media_review_items(request(),{},media_review_policy())
    result=validate_verdicts(items,{'verdicts':[{'criterion_id':{'invented':'object'}}]},catalog())
    assert not result['ok'] and any(i['code']=='MEDIA_REVIEW_FORMAT' for i in result['issues'])
