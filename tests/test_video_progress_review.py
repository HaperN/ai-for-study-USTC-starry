"""Independent-review counterexamples: cache identity, real retries, and partial input."""
import json
import wave
from pathlib import Path

import pytest

from test_video_forward_progress import session_at, lesson_section


def frames_for(session, count):
    rows = []
    for i in range(1, count + 1):
        (session.directory/f'{i}.jpg').write_bytes(f'image-{i}'.encode())
        rows.append({'id': f'frame-{i}', 'path': f'{i}.jpg', 'start': (i-1)*10, 'end': i*10, 'text': 'raw'})
    return rows


def test_each_failed_frame_keeps_its_gap_and_blocks_its_formula(tmp_path):
    from aiforstudy.core.video_progress import protect_deferred_dependencies
    s = session_at(tmp_path, lambda request: {'frames': []}); s.service.config['vision_batch_size'] = 2
    frames, _ = s.vision(frames_for(s, 2), {'segments': []}, 20)
    lesson = {'sections': [lesson_section('s',0,20,'frame-1','Unverified diagram')],
              'formulas': [{'latex': 'x=9', 'source_ids': ['frame-1'], 'uncertainty': ''}], 'uncertainties': []}
    result = protect_deferred_dependencies(lesson, frames, {'segments': []})
    assert {tuple(g['source_ids']) for g in result['deferred_work']} == {('frame-1',), ('frame-2',)}
    assert result['formulas'] == []
    assert result['sections'][0]['deferred_source_ids'] == ['frame-1']


def test_new_revision_metadata_does_not_retry_identical_global_review(tmp_path):
    from aiforstudy.core.video_lecture_reconciliation import reconcile_lecture, re_review_lecture
    lesson = {'sections': [lesson_section('old',0,10,'asr-1','Original')], 'formulas': [], 'uncertainties': []}
    transcript = {'segments': [{'id':'asr-1','start':0,'end':10,'text':'raw'}]}
    answer = {**lesson, 'covered_section_ids': ['old'], 'corrections': []}
    calls = []
    def respond(request):
        calls.append(request.role)
        return answer if request.role == 'lesson' else {'decision':'invalid'}
    for tag in ('revision-first','revision-second'):
        s = session_at(tmp_path, respond, tag)
        result = reconcile_lecture(s, lesson, [], transcript, 10, tag)
        review = re_review_lecture(s, result, [], transcript, 10, tag)
        assert review['state'] == 'deferred'
    assert calls == ['lesson', 'text_judge', 'text_judge']


def test_review_receipt_metadata_is_not_new_reconciliation_evidence(tmp_path):
    from aiforstudy.core.video_lecture_reconciliation import reconcile_lecture
    lesson = {'sections': [lesson_section('old',0,10,'asr-1','Original')], 'formulas': [], 'uncertainties': []}
    transcript = {'segments': [{'id':'asr-1','start':0,'end':10,'text':'raw'}]}
    calls = []
    def respond(request):
        calls.append(request.tag); return {'invalid': True}
    for i in range(2):
        review = {'state':'completed','decision':'issues_found','issues':[{'description':'same issue','source_ids':['asr-1']}],
                  'tag_prefix':f'revision-{i}','input_digest':str(i),'reviewed_lesson_digest':str(i)}
        reconcile_lecture(session_at(tmp_path, respond, f'rev{i}'), lesson, [], transcript, 10, f'rev{i}', review)
    assert len(calls) == 2
    review['issues'][0]['description'] = 'New source-based explanation'
    reconcile_lecture(session_at(tmp_path, respond, 'new-evidence'), lesson, [], transcript, 10, 'new-evidence', review)
    assert len(calls) == 4


def test_success_cache_cannot_bypass_an_existing_tag_producer_binding(tmp_path):
    a = session_at(tmp_path, lambda req: {'model': 'A'}, 'a', model='A')
    b = session_at(tmp_path, lambda req: {'model': 'B'}, 'b', model='B')
    assert a.call('lesson','same-tag','instructions',{}) == {'model':'A'}
    assert b.call('lesson','other-tag','instructions',{}) == {'model':'B'}
    relabel = session_at(tmp_path, lambda req: pytest.fail('no new call'), 'a', model='B')
    with pytest.raises(ValueError, match='identity|producer|生产身份'):
        relabel.call('lesson','same-tag','instructions',{})


def test_image_bytes_bind_an_exchange_even_when_the_path_is_unchanged(tmp_path):
    calls = []
    def respond(request):
        calls.append(request.tag); return {'image': Path(request.images[0]).read_text()}
    s = session_at(tmp_path, respond); image = s.directory/'image.jpg'; image.write_text('old')
    assert s.call('source_repair','image','instructions',{},images=[image]) == {'image':'old'}
    image.write_text('new')
    with pytest.raises(ValueError, match='identity|changed'):
        s.call('source_repair','image','instructions',{},images=[image])
    assert s.call('source_repair','new-image','instructions',{},images=[image]) == {'image':'new'}
    assert len(calls) == 2


def test_real_visual_diagnostics_recognize_more_valid_frames_as_progress(tmp_path):
    calls = []
    def respond(request):
        calls.append(request.tag)
        return {'frames': [{'id':f'frame-{i}', 'visual_text':f'clear {i}' if i<=len(calls) else None,
                            'explanation':'', 'uncertainties':[]} for i in (1,2,3)]}
    s = session_at(tmp_path, respond); s.service.config['vision_batch_size'] = 3
    result, _ = s.vision(frames_for(s,3), {'segments':[]}, 30)
    assert [x['visual_text'] for x in result] == ['clear 1','clear 2','clear 3']
    assert len(calls) == 3


def test_visual_repairs_preserve_union_of_independently_valid_frames(tmp_path):
    calls = []
    def respond(request):
        calls.append(request.tag)
        good = 1 if len(calls)==1 else 2
        return {'frames': [{'id':f'frame-{i}','visual_text':f'clear {i}' if i==good else None,
                            'explanation':'','uncertainties':[]} for i in (1,2)]}
    s = session_at(tmp_path,respond); s.service.config['vision_batch_size'] = 2
    result, _ = s.vision(frames_for(s,2), {'segments':[]}, 20)
    assert [x['visual_text'] for x in result] == ['clear 1','clear 2']
    assert len(calls) == 2


def test_lesson_repairs_keep_disjoint_valid_neighbors_from_both_replies(tmp_path):
    calls=[]
    def respond(request):
        calls.append(request.tag)
        return {'sections':[
            lesson_section('first',0,10,'asr-1' if len(calls)==1 else 'invented','First explanation'),
            lesson_section('second',10,20,'invented' if len(calls)==1 else 'asr-2','Second explanation')],
            'formulas':[],'uncertainties':[]}
    s=session_at(tmp_path,respond);s.service.config['chapter_seconds']=20
    transcript={'segments':[{'id':f'asr-{i}','start':(i-1)*10,'end':i*10,'text':'raw'} for i in (1,2)]}
    result,_=s.lesson([],transcript,20)
    assert [x['markdown'] for x in result['sections']]==['First explanation [asr-1]','Second explanation [asr-2]']
    assert len(calls)==2


def test_dropping_an_unresolved_section_does_not_make_its_gap_disappear(tmp_path):
    calls=[]
    good=lesson_section('first',0,10,'asr-1','Useful')
    bad=lesson_section('second',10,20,'invented','Unbound')
    def respond(request):
        calls.append(request.tag)
        return {'sections':[good,bad] if len(calls)==1 else [good], 'formulas':[],'uncertainties':[]}
    s=session_at(tmp_path,respond);s.service.config['chapter_seconds']=20
    transcript={'segments':[{'id':f'asr-{i}','start':(i-1)*10,'end':i*10,'text':'raw'} for i in (1,2)]}
    result,_=s.lesson([],transcript,20)
    assert any(x.get('processing_status')=='deferred' and x['start']==10 and x['end']==20 for x in result['sections'])


@pytest.mark.parametrize('kind',['empty_response','truncation','unparsed'])
def test_unusable_repair_retains_last_useful_candidate(tmp_path, kind):
    from aiforstudy.providers.gateway import CallResult
    good = lesson_section('good',0,10,'asr-1','Useful original neighbor')
    first = {'sections':[good], 'formulas':[{'latex':'bad','source_ids':['imaginary']}], 'uncertainties':[]}
    s = session_at(tmp_path, lambda req: first); calls=[]
    def call(request):
        calls.append(request.tag)
        if len(calls)==1:
            return CallResult(ok=True,payload=first,text=json.dumps(first))
        return CallResult(ok=False,payload=None,text='invalid',failure_kind=kind)
    s.gateway.call=call
    result,_=s.lesson([],{'segments':[{'id':'asr-1','start':0,'end':10,'text':'raw'}]},10)
    assert any(x['markdown']==good['markdown'] for x in result['sections'])
    assert result['formulas']==[]


@pytest.mark.parametrize('text',['','not JSON'])
def test_real_gateway_does_not_repeat_identical_malformed_requests(tmp_path,text):
    from aiforstudy.providers.gateway import Gateway
    from aiforstudy.pipeline.execution_control import ExecutionControl,ExecutionPolicy
    from aiforstudy.pipeline.services import CancelToken
    s=session_at(tmp_path,lambda req:None);messages=[]
    def transport(**kwargs):
        messages.append(kwargs['messages']); return text,{}
    control=ExecutionControl(ExecutionPolicy());control.bind(s.directory/'execution.json',run_id='fixture')
    s.gateway=Gateway(transport=transport,roles={'lesson':{'model':'fixture','effort':'low','max_tokens':1024}},control=control,cancel=CancelToken(),run_id='fixture',book_id='fixture')
    with pytest.raises(ValueError):
        s.call('lesson','bad-json','instructions',{'source':'same'},validate=lambda x:None)
    assert len(messages)==len({json.dumps(x,sort_keys=True) for x in messages}), 'the gateway must return format failure to the owner, not blindly resend'


def test_truncated_audio_is_fatal_not_an_ordinary_recognition_gap(tmp_path,monkeypatch):
    from aiforstudy.core import video_recognition_worker as worker
    wav=tmp_path/'audio.wav'
    with wave.open(str(wav),'wb') as f:
        f.setparams((1,2,16000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*60*16000)
    wav.write_bytes(wav.read_bytes()[:44+40*32000])
    model=tmp_path/'model';model.mkdir();(model/'config.json').write_text('{}');(model/'model.safetensors').write_bytes(b'model')
    monkeypatch.setattr(worker,'_load_asr_backend',lambda model:lambda *args:{'text':'recognized','generation_tokens':1})
    with pytest.raises((ValueError,worker.IdentityMismatch),match='truncated|integrity|截断'):
        worker.run_asr(wav,tmp_path/'result.json',model)


def test_ocr_source_read_error_is_fatal(tmp_path,monkeypatch):
    from aiforstudy.core import video_recognition_worker as worker
    executable=tmp_path/'vision';executable.write_bytes(b'fixture');monkeypatch.setenv('AIFORSTUDY_VIDEO_OCR_EXECUTABLE',str(executable))
    frames=[]
    for i in range(2):
        p=tmp_path/f'{i}.jpg';p.write_bytes(b'image');frames.append({'id':f'f{i}','path':str(p),'start':i,'end':i+1})
    manifest=tmp_path/'frames.json';manifest.write_text(json.dumps({'frames':frames}))
    def recognize(executable,path):
        if Path(path).name=='1.jpg':raise FileNotFoundError('source image unavailable')
        return {'success':True,'observations':[]}
    monkeypatch.setattr(worker,'_recognize_frame',recognize)
    with pytest.raises(FileNotFoundError):worker.run_ocr(manifest,tmp_path/'ocr.json')


def test_markdown_names_the_unfinished_operation():
    from aiforstudy.core.video_analysis import VideoAnalysisService
    result={'sections':[lesson_section('s',0,10,'asr-1','Readable')],'formulas':[],'uncertainties':[],
            'deferred_work':[{'id':'gap','stage':'review','state':'deferred','start':0,'end':10,'source_ids':['asr-1'],'message':'局部内容未完成'}]}
    text=VideoAnalysisService._markdown('title',result,'source.mp4')
    assert '全片审核' in text and '待核' in text
