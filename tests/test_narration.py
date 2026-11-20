"""Narration uses actual audio lengths and keeps the video, captions and steps together."""
from pathlib import Path
import importlib
import subprocess
import wave

import pytest


def narration():
    try:
        return importlib.import_module('aiforstudy.blocks.media.narration')
    except ModuleNotFoundError:
        pytest.fail('Host narration adapter is missing')


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


class RecordedVoice:
    source = {'backend': 'test-recording', 'voice': 'duration-fixture'}
    def __init__(self):
        self.calls = []
    def synthesize(self, text, destination):
        self.calls.append(text)
        subprocess.run([ffmpeg(), '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=1.8', '-ar', '24000', '-ac', '1', str(destination)], check=True)


def test_real_audio_retimes_steps_and_captions_without_cutting_speech(tmp_path):
    module = narration()
    source = tmp_path / 'source.mp4'
    subprocess.run([ffmpeg(), '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'testsrc=size=160x90:rate=10:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)], check=True)
    backend = RecordedVoice()
    steps = [{'step_id': 's1', 'caption': '先看复数', 'observation': '观察实部', 'start': 0, 'end': 1},
             {'step_id': 's2', 'caption': '再看变化', 'start': 1, 'end': 2}]
    result = module.narrate_animation(source, steps, tmp_path / 'spoken', backend=backend)
    assert steps[0]['end'] == 1, 'do not mutate the archived input'
    assert result['steps'][0]['end'] >= 2.05
    assert result['steps'][1]['start'] == result['steps'][0]['end']
    assert result['steps'][-1]['end'] == result['duration_seconds']
    assert result['steps'][0]['narration_duration'] == pytest.approx(1.8, abs=.01)
    assert '观察实部' in backend.calls[0]
    assert Path(result['video_path']).is_file()
    import av
    with av.open(result['video_path']) as container:
        stream=container.streams.video[0]
        packet_end=max(float(packet.pts*packet.time_base)+float((packet.duration or 0)*packet.time_base)
                       for packet in container.demux(stream) if packet.pts is not None)
    assert packet_end == pytest.approx(result['duration_seconds'],abs=.02), 'real H264 packets must reach retimed steps'
    decoded = subprocess.run([ffmpeg(), '-v', 'error', '-i', result['video_path'],
                              '-map', '0:a:0', '-f', 'null', '-'], capture_output=True)
    assert decoded.returncode == 0, decoded.stderr
    with wave.open(result['audio_path']) as audio:
        assert audio.getnframes()/audio.getframerate() == pytest.approx(result['duration_seconds'], abs=.02)
    assert '00:00:02.100' in Path(result['captions_path']).read_text()
    assert result['source']['backend'] == 'test-recording'
    again = module.narrate_animation(source, steps, tmp_path / 'spoken', backend=backend)
    assert again['cache_hit'] is True
    assert len(backend.calls) == 2
    import json
    receipt=tmp_path/'spoken/narrated.narration.json'
    damaged=json.loads(receipt.read_text());damaged['output_hashes']={}
    receipt.write_text(json.dumps(damaged))
    rebuilt=module.narrate_animation(source,steps,tmp_path/'spoken',backend=backend)
    assert rebuilt['cache_hit'] is False, 'missing output digests cannot authorize resume'
    assert len(backend.calls)==4


def test_timeline_requires_full_ordered_video_coverage(tmp_path):
    module = narration()
    source = tmp_path / 'source.mp4'
    subprocess.run([ffmpeg(), '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'color=size=160x90:rate=10:duration=2', '-c:v', 'libx264', str(source)], check=True)
    with pytest.raises(ValueError, match='cover|contiguous'):
        module.narrate_animation(source, [{'caption':'空隙', 'start':1, 'end':2}], tmp_path/'bad', backend=RecordedVoice())


def test_speech_text_removes_tex_markup_but_keeps_mathematical_meaning():
    text = narration().spoken_text({'caption': r'观察 $z_1+z_2=\frac{1}{2}$。', 'observation':'实部相加'})
    assert '\\' not in text and '$' not in text
    assert '二分之一' in text or '2分之1' in text
    assert '实部相加' in text


def test_audio_policy_accepts_one_aac_voice_but_rejects_unbounded_or_multiple_tracks():
    module = narration()
    voice = {'codec_name':'aac','channels':1,'sample_rate':'24000','duration':'4.1'}
    assert module.audio_stream_problems([voice], video_seconds=4.1) == []
    assert module.audio_stream_problems([], video_seconds=4.1) == []
    assert module.audio_stream_problems([voice,voice], video_seconds=4.1)
    assert module.audio_stream_problems([{**voice,'codec_name':'mp3'}], video_seconds=4.1)
    assert module.audio_stream_problems([{**voice,'duration':'10'}], video_seconds=4.1)
    assert module.audio_stream_problems([{**voice,'sample_rate':'999999'}], video_seconds=4.1)


def test_worker_step_plays_verified_host_audio_and_waits_until_voice_finishes(tmp_path):
    import importlib.util
    from types import SimpleNamespace
    import hashlib
    asset = Path(__file__).resolve().parents[1]/'tools/manim_worker/aifs_media_runtime.py'
    spec = importlib.util.spec_from_file_location('worker_narration_test', asset)
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    audio = tmp_path/'voice.wav'
    with wave.open(str(audio),'wb') as sound:
        sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(24000)
        sound.writeframes(b'\0' * 24000 * 2 * 3)
    receipt = {'steps':[{'step_id':'s1','path':str(audio),'duration':3,
                        'sha256':hashlib.sha256(audio.read_bytes()).hexdigest()}]}
    class Scene:
        def __init__(self):
            self.renderer=SimpleNamespace(time=0.0); self.sounds=[]
        def add_sound(self,path): self.sounds.append((path,self.renderer.time))
        def wait(self,seconds): self.renderer.time+=seconds
    worker._configure({'request':{'kind':'animation'}}, [{'step_id':'s1'}], narration=receipt)
    scene=Scene()
    with worker.media_step(scene,'s1'):
        scene.wait(.5)
    assert scene.sounds == [(str(audio),0)]
    assert scene.renderer.time >= 3.25
    assert worker._finish(scene)[0]['end'] == scene.renderer.time
    audio.write_bytes(b'changed')
    worker._configure({'request':{'kind':'animation'}}, [{'step_id':'s1'}], narration=receipt)
    with pytest.raises(ValueError,match='hash|corrupt'):
        with worker.media_step(Scene(),'s1'): pass


def test_optional_voice_resources_keep_explicit_mime_and_path_contract():
    from aiforstudy.blocks.visual.result import AnimationResource
    audio = AnimationResource(kind='audio',relative_path='voice.wav',sha256='sha256:'+'a'*64,
                              bytes=100,mime='audio/wav')
    receipt = AnimationResource(kind='narration',relative_path='voice.json',sha256='sha256:'+'b'*64,
                                bytes=100,mime='application/json; charset=utf-8')
    assert audio.as_dict()['kind'] == 'audio'
    assert receipt.as_dict()['kind'] == 'narration'


def test_prepare_step_audio_creates_fixed_host_inputs_with_decoded_duration(tmp_path):
    result = narration().prepare_step_audio([{'step_id':'s1','caption':'观察复数'}],tmp_path,
                                            backend=RecordedVoice())
    assert result['source']['backend'] == 'test-recording'
    step = result['steps'][0]
    assert Path(step['path']).is_file()
    assert step['duration'] == pytest.approx(1.8)
    assert len(step['sha256']) == 64


def test_offline_manifest_keeps_silent_legacy_resources_optional(tmp_path,monkeypatch):
    from aiforstudy.checks import offline
    from aiforstudy.blocks.visual.worker import file_resource
    rows=[]
    for kind,name in [('mp4','video.mp4'),('poster','poster.png'),('keyframe','key.png'),
                       ('captions','captions.vtt'),('transcript','transcript.txt')]:
        path=tmp_path/name;path.write_bytes(b'fixture')
        rows.append(file_resource(path,kind=kind))
    monkeypatch.setattr(offline,'check_animation_video',lambda *a,**kw:
                        offline.OfflineCheck('animation.video','accepted','fixture verification'))
    result=offline.check_animation_resources({'resources':rows},tmp_path)
    assert result.ok,result.detail


def test_enabled_production_hook_prepares_audio_before_isolated_dispatch(tmp_path,monkeypatch):
    """Capture the job at the sandbox boundary; no generated code runs on host."""
    from types import SimpleNamespace
    from aiforstudy.blocks.media import code_runtime as runtime
    from aiforstudy.blocks.visual import worker
    import json
    import shutil
    worker_dir=Path(__file__).resolve().parents[1]/'tools/manim_worker'
    services=SimpleNamespace(worker_dir=worker_dir,scratch_root=tmp_path/'scratch',ffmpeg=ffmpeg(),
                             worker_services=lambda:None)
    monkeypatch.setattr(runtime,'_validate_program',lambda program,context:program)
    monkeypatch.setattr(runtime,'default_services',lambda **kw:services)
    monkeypatch.setattr(runtime,'limits_for_profile',lambda *a:SimpleNamespace(timeout_seconds=120,
                        cpu_cores=1,memory_bytes=1024**3,output_bytes=128*1024**2))
    monkeypatch.setattr(runtime,'probe_isolation',lambda **kw:{'outcome':{'status':'ok'},
                        'isolation':{'safe':True,'applied':{'landlock':{'backend':'test'}}}})
    monkeypatch.setattr(runtime,'environment_hash',lambda *a,**kw:'test-environment')
    captured=[]
    def dispatch(**kwargs):
        job=kwargs['job'];captured.append(job)
        assert Path(job['narration']['steps'][0]['path']).is_file()
        assert 'endpoint' not in json.dumps(job)
        return {'status':'blocked','reason':'test boundary reached'}
    monkeypatch.setattr(runtime,'run_worker_job',dispatch)
    monkeypatch.setattr(narration(),'configured_backend',lambda config:RecordedVoice())
    program={'backend':'manim','code':'raise RuntimeError("must not execute on host")',
             'steps':[{'step_id':'s1','caption':'观察复数'}]}
    result=runtime.render_generated_code(program,context={'request':{'kind':'animation'}},
        out_dir=tmp_path/'out',narration={'enabled':True,'backend':'test'})
    assert result['status']=='blocked'
    assert len(captured)==1
    assert result['technical_report']['narration']['source']['backend']=='test-recording'
    assert (tmp_path/'out/source/narration/step-001.wav').is_file()


def test_offline_resources_reject_corrupt_optional_wav(tmp_path,monkeypatch):
    from aiforstudy.checks import offline
    from aiforstudy.blocks.visual.worker import file_resource
    rows=[]
    for kind,name in [('mp4','video.mp4'),('poster','poster.png'),('keyframe','key.png'),
                       ('captions','captions.vtt'),('transcript','transcript.txt'),('audio','voice.wav')]:
        path=tmp_path/name;path.write_bytes(b'not audio')
        rows.append(file_resource(path,kind=kind))
    monkeypatch.setattr(offline,'check_animation_video',lambda *a,**kw:
                        offline.OfflineCheck('animation.video','accepted','fixture verification'))
    result=offline.check_animation_resources({'resources':rows},tmp_path)
    assert not result.ok and ('WAV' in result.detail or '音频' in result.detail),result.detail


def test_resource_collection_registers_optional_voice_and_provenance(tmp_path):
    from aiforstudy.blocks.visual.result import collect_resources
    (tmp_path/'narrated.wav').write_bytes(b'voice')
    (tmp_path/'narrated.narration.json').write_text('{"source":{"backend":"recorded"},"steps":[{}]}')
    resources=collect_resources(tmp_path)
    assert {row.kind for row in resources} == {'audio','narration'}


def test_narration_policy_rejects_truncated_voice_but_allows_long_final_hold():
    module=narration()
    receipt={'source':{'backend':'recorded'},'steps':[{'step_id':'s1','duration':1.5},
                                                   {'step_id':'s2','duration':1.8}]}
    steps=[{'step_id':'s1','start':0,'end':2},{'step_id':'s2','start':2,'end':8}]
    voice={'codec_name':'aac','channels':1,'sample_rate':'24000','duration':'.1'}
    assert module.audio_stream_problems([voice],video_seconds=8,narration=receipt,steps=steps)
    assert module.audio_stream_problems([{**voice,'duration':'3.8'}],video_seconds=8,
                                       narration=receipt,steps=steps)==[]
    assert module.audio_stream_problems([{**voice,'duration':'3.5'}],video_seconds=8,
                                       narration=receipt,steps=steps)
    assert module.audio_stream_problems([],video_seconds=8,narration=receipt,steps=steps)
    assert module.audio_stream_problems([voice],video_seconds=8)==[], 'legacy audio has no invented speech endpoint'


def test_generated_video_verification_rejects_truncated_narration(tmp_path,monkeypatch):
    from aiforstudy.blocks.media import code_runtime as runtime
    from types import SimpleNamespace
    import json
    info={'streams':[{'codec_type':'video','codec_name':'h264','pix_fmt':'yuv420p','width':160,'height':90,
                      'r_frame_rate':'30/1','duration':'4','nb_read_frames':'120'},
                     {'codec_type':'audio','codec_name':'aac','channels':1,'sample_rate':'24000','duration':'.1'}],
          'format':{'duration':'4'}}
    monkeypatch.setattr(runtime.shutil,'which',lambda name:'ffprobe')
    monkeypatch.setattr(runtime,'_run',lambda args:json.dumps(info).encode() if '-show_streams' in args else b'')
    monkeypatch.setattr(runtime,'faststart_ok',lambda path:True)
    with pytest.raises(ValueError,match='配音|旁白|narration'):
        runtime._video(tmp_path/'voice.mp4',services=SimpleNamespace(ffmpeg=ffmpeg()),
            quality={'pixel_width':160,'pixel_height':90,'frame_rate':30},
            narration={'source':{'backend':'recorded'},'steps':[{'step_id':'s1','start':0,'duration':3.8}]})


def test_offline_acceptance_checks_actual_speech_endpoint_not_final_hold(tmp_path,monkeypatch):
    from aiforstudy.checks import offline
    from types import SimpleNamespace
    video=tmp_path/'voice.mp4';video.write_bytes(b'probe-fixture')
    voice={'codec_type':'audio','codec_name':'aac','channels':1,'sample_rate':'24000','duration':'.1'}
    info={'streams':[{'codec_type':'video','codec_name':'h264','pix_fmt':'yuv420p','width':1280,
                      'height':720,'r_frame_rate':'30/1'},voice],'format':{'duration':'8'}}
    monkeypatch.setattr(offline,'_ffprobe_streams',lambda path:(info,''))
    monkeypatch.setattr(offline.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stderr=b''))
    receipt={'source':{'backend':'recorded'},'steps':[{'step_id':'s1','duration':1.5},
                                                   {'step_id':'s2','duration':1.8}]}
    steps=[{'step_id':'s1','start':0,'end':2},{'step_id':'s2','start':2,'end':8}]
    bad=offline.check_animation_video(video,narration=receipt,steps=steps,narrated_duration=8)
    assert not bad.ok and '旁白' in bad.detail,bad.detail
    voice['duration']='3.8'
    valid=offline.check_animation_video(video,narration=receipt,steps=steps,narrated_duration=8)
    assert valid.ok,valid.detail


def test_offline_ffprobe_requests_the_audio_fields_required_by_acceptance(tmp_path,monkeypatch):
    from aiforstudy.checks import offline
    from types import SimpleNamespace
    import json
    seen=[]
    def probe(argv,**kwargs):
        seen.append(argv)
        return SimpleNamespace(returncode=0,stderr=b'',stdout=json.dumps({'streams':[]}).encode())
    monkeypatch.setattr(offline.subprocess,'run',probe)
    offline._ffprobe_streams(tmp_path/'spoken.mp4')
    argv=seen[0];fields=set()
    for index,arg in enumerate(argv):
        if arg=='-show_entries' and argv[index+1].startswith('stream='):
            fields.update(argv[index+1].partition('=')[2].split(','))
    required={'codec_type','codec_name','channels','sample_rate','duration','start_time'}
    assert required<=fields, 'ffprobe must actually return audio acceptance inputs: '+str(required-fields)


def test_video_only_rebuild_reuses_existing_wave_and_steps_and_keeps_each_hold(tmp_path,monkeypatch):
    import av
    import hashlib
    module=narration()
    source=tmp_path/'source.mp4'
    subprocess.run([ffmpeg(),'-v','error','-y','-f','lavfi','-i','testsrc=size=160x90:rate=10:duration=2',
                    '-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
    wave_path=tmp_path/'already-spoken.wav'
    subprocess.run([ffmpeg(),'-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=6',
                    '-ar','24000','-ac','1',str(wave_path)],check=True)
    before=hashlib.sha256(wave_path.read_bytes()).hexdigest()
    steps=[{'step_id':'s1','source_start':0,'source_end':1,'start':0,'end':3},
           {'step_id':'s2','source_start':1,'source_end':2,'start':3,'end':6}]
    result=module.rebuild_narrated_video(source,steps,wave_path,tmp_path/'repaired.mp4')
    with av.open(result['video_path']) as container:
        frames=list(container.decode(video=0))
        end=float(frames[-1].pts*frames[-1].time_base)+.1
        assert end==pytest.approx(6,abs=.01)
        assert len(frames)==60
        import numpy as np
        # Holding the first step must not show the second step's animation early.
        for first,last in [(10,28),(40,58)]:
            assert np.mean(np.abs(frames[first].to_ndarray(format='rgb24').astype(float)-
                                  frames[last].to_ndarray(format='rgb24').astype(float)))<1.5
    assert hashlib.sha256(wave_path.read_bytes()).hexdigest()==before
    assert result['video_timing']['video_seconds']==pytest.approx(6,abs=.01)
    import sys
    monkeypatch.setitem(sys.modules,'av',None)
    fallback=module._verify_video_timeline(result['video_path'],expected=6,fps=10,ffmpeg=ffmpeg())
    assert fallback['video_seconds']==pytest.approx(6,abs=.01)
    assert 'video-only' in fallback['method']
    with pytest.raises(ValueError,match='video stream ends'):
        module._verify_video_timeline(source,expected=6,fps=10,ffmpeg=ffmpeg())
