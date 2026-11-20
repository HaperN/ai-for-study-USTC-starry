import json
from copy import deepcopy
from pathlib import Path
import pytest
from aiforstudy.blocks.media.local_narration import LocalCacheBackend, cache_key
from tools.revoice_wholebook import current_clock_steps


def test_cache_identity_tracks_effective_settings():
    source={'model_revision':'a','weights_sha256':'b','voice':'serena','seed':1,'instruction':'clear'}
    keys={cache_key('甲',source)}
    for field in source:
        other={**source,field:str(source[field])+'changed'}
        assert cache_key('甲',other) not in keys
    assert cache_key('乙',source) not in keys


def test_missing_or_corrupt_cache_fails_without_fallback(tmp_path):
    backend=LocalCacheBackend({'identity':{'voice':'serena'},'cache_dir':str(tmp_path)})
    with pytest.raises(ValueError,match='cache'):
        backend.synthesize('甲',tmp_path/'out.wav')
    folder=tmp_path/cache_key('甲',backend.source);folder.mkdir()
    (folder/'audio.wav').write_bytes(b'broken')
    (folder/'result.json').write_text(json.dumps({'audio_sha256':'wrong'}))
    with pytest.raises(ValueError,match='cache'):
        backend.synthesize('甲',tmp_path/'out.wav')


def test_second_retime_keeps_current_input_clock_and_old_lineage():
    original=[{'step_id':'S1','start':0,'end':15,'source_start':0,'source_end':3,'narration_text':'甲。'},
              {'step_id':'S2','start':15,'end':30,'source_start':3,'source_end':6,'narration_text':'乙。'}]
    frozen=deepcopy(original)
    steps=current_clock_steps(original)
    assert [(s['start'],s['end']) for s in steps]==[(0,15),(15,30)]
    assert steps[1]['prior_clock_lineage']['source_start']==3
    assert 'source_start' not in steps[1]
    assert original==frozen


def test_valid_pcm_cache_preserves_exact_script_and_metadata(tmp_path):
    import wave
    from aiforstudy.blocks.media.local_narration import digest
    backend=LocalCacheBackend({'identity':{'voice':'serena'},'cache_dir':str(tmp_path)})
    text='负一，乘以派。';key=cache_key(text,backend.source);folder=tmp_path/key;folder.mkdir()
    with wave.open(str(folder/'audio.wav'),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\x01\x00'*24000)
    (folder/'result.json').write_text(json.dumps({'cache_key':key,'identity':backend.source,'text':text,'audio_sha256':digest(folder/'audio.wav')}))
    result=backend.synthesize(text,tmp_path/'out.wav')
    assert result['sample_count']==24000 and result['duration_seconds']==1
    assert digest(tmp_path/'out.wav')==digest(folder/'audio.wav')
    with pytest.raises(ValueError,match='cache'):
        backend.synthesize('一，乘以派。',tmp_path/'other.wav')


def test_proven_hold_uses_current_start_and_rejects_unmatched_receipt(tmp_path):
    from tools.revoice_wholebook import proven_motion_steps
    from aiforstudy.blocks.media.local_narration import digest
    video=tmp_path/'input.mp4';video.write_bytes(b'current-movie')
    steps=[{'step_id':'S1','start':0,'end':15,'source_start':0,'source_end':3},
           {'step_id':'S2','start':15,'end':30,'source_start':3,'source_end':8}]
    receipt={'timing':'original motion followed by final-frame hold; complete decoded voice','producer_version':'host-narration-1.2-neural','output_hashes':{'old/narrated.mp4':digest(video)},'steps':steps}
    path=tmp_path/'receipt.json';path.write_text(json.dumps(receipt))
    result=proven_motion_steps(steps,video,path)
    assert result[1]['input_motion_duration']==5
    assert result[1]['motion_lineage']['current_motion_window']==[15,20]
    video.write_bytes(b'changed')
    with pytest.raises(ValueError,match='receipt'):proven_motion_steps(steps,video,path)


def test_actual_second_retime_short_and_long_pcm_preserves_motion_and_boundaries(tmp_path):
    import subprocess,wave,math,struct
    import av,numpy as np
    from aiforstudy.blocks.media import narration as n
    from tools.verify_revoiced_delivery import frame_at
    video=tmp_path/'old.mp4'
    subprocess.run([n.resolve_ffmpeg(),'-v','error','-y','-f','lavfi','-i','testsrc=size=160x90:rate=10:duration=6','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True)
    class PCM:
        source={'backend':'test-pcm'}
        def synthesize(self,text,destination):
            seconds=.5 if '短' in text else 2
            with wave.open(str(destination),'wb') as w:
                w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000)
                w.writeframes(b''.join(struct.pack('<h',int(5000*math.sin(i/10))) for i in range(int(seconds*24000))))
    steps=[{'step_id':'S1','start':0,'end':3,'input_motion_duration':1,'narration_text':'短。'},
           {'step_id':'S2','start':3,'end':6,'input_motion_duration':1,'narration_text':'长。'}]
    result=n.narrate_animation(video,steps,tmp_path/'new',backend=PCM())
    assert result['duration_seconds']==pytest.approx(3.3)
    assert [(s['start'],s['end']) for s in result['steps']]==[(0,1),(1,3.3)]
    assert result['steps'][1]['source_start']==3
    with av.open(str(video)) as before,av.open(result['video_path']) as after:
        for source_at,new_at in [(.9,.9),(3.9,1.9),(3.9,3.1)]:
            x=frame_at(before,source_at);y=frame_at(after,new_at)
            assert np.abs(x.astype(float)-y.astype(float)).mean()<5
    assert result['video_timing']['video_packets_or_frames']==33


@pytest.mark.parametrize('proof', ['missing', 'mismatched', 'fresh'])
def test_repeated_revoice_discards_inherited_trim_until_fresh_proof(tmp_path, proof):
    from tools.revoice_wholebook import proven_motion_steps
    from aiforstudy.blocks.media.local_narration import digest
    video=tmp_path/'current.mp4';video.write_bytes(b'current-video')
    inherited={'step_id':'S1','start':0,'end':15,'source_start':0,'source_end':5,
               'input_motion_duration':3,'motion_lineage':{'basis':'older generation proof'},
               'prior_clock_lineage':{'source_start':9,'source_end':12}}
    original=deepcopy(inherited)
    receipt_path=tmp_path/'prior.json'
    if proof!='missing':
        receipt_path.write_text(json.dumps({
            'timing':'original motion followed by final-frame hold; complete decoded voice',
            'producer_version':'host-narration-1.2-neural',
            'output_hashes':{'prior.mp4':digest(video) if proof=='fresh' else 'different-video'},
            'steps':[inherited]}))
    try:
        actual=proven_motion_steps([inherited],video,receipt_path)[0]
    except (FileNotFoundError,ValueError):
        actual=current_clock_steps([inherited])[0]
    # Only a freshly checked receipt may reactivate trimming for this input.
    if proof=='fresh':
        assert actual['input_motion_duration']==5
        assert actual['motion_lineage']['current_motion_window']==[0,5]
    else:
        assert 'input_motion_duration' not in actual
        assert 'motion_lineage' not in actual
        assert actual.get('input_motion_duration',actual['end']-actual['start'])==15
    assert actual['prior_clock_lineage']['input_motion_duration']==3
    assert actual['prior_clock_lineage']['motion_lineage']==original['motion_lineage']
    assert actual['prior_clock_lineage']['prior_clock_lineage']==original['prior_clock_lineage']
    assert inherited==original


def test_script_override_requires_exact_before_and_preserves_formula():
    from tools.revoice_wholebook import apply_script_overrides
    row={'steps':[{'step_id':'S1','narration_text':'半径零点九时。','display_markdown':'半径零点九时。\n$r=0.9$'}]}
    change=[{'unit_id':'U','request_id':'R','step_id':'S1','before':'半径零点九时。','after':'当半径等于零点九的时候。'}]
    result=apply_script_overrides(row,'U','R',change)
    assert result['steps'][0]['display_markdown']=='当半径等于零点九的时候。\n$r=0.9$'
    assert row['steps'][0]['narration_text']=='半径零点九时。'
    with pytest.raises(ValueError,match='before'):apply_script_overrides(result,'U','R',change)
