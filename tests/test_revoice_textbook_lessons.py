"""Selected lessons use original video clocks and keep unrelated lesson edits."""
import importlib.util
import json
from pathlib import Path
import subprocess
import pytest


def tool():
    path=Path(__file__).resolve().parents[1]/'tools/revoice_textbook_lessons.py'
    assert path.is_file(), 'a resumable manifest-driven revoice tool is required'
    spec=importlib.util.spec_from_file_location('revoice_textbook_lessons',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_media_selection_keeps_authored_speech_and_rejects_duplicate_ids(tmp_path):
    m=tmp_path/'lessons.json'
    m.write_text(json.dumps({'units':{'CH01':{'media':[{'request_id':'good',
        'narration_steps':[{'step_id':'S1','narration_text':'这是内点。','caption_tex':'内点 $a$。'}]}]}}}))
    selected=tool().load_selections([m])
    assert selected['CH01'][0]['narration_steps'][0]['narration_text']=='这是内点。'
    with pytest.raises(ValueError,match='重复|duplicate'):
        tool().load_selections([m,m])


def test_batch_uses_original_clock_and_patches_only_selected_animation(tmp_path):
    from test_natural_narration import Tone
    import imageio_ffmpeg
    original=tmp_path/'original';output=tmp_path/'output'
    for root in (original,output): (root/'data').mkdir(parents=True)
    (original/'assets/a').mkdir(parents=True)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i',
        'testsrc=size=160x90:rate=10:duration=1','-c:v','libx264','-pix_fmt','yuv420p',
        str(original/'assets/a/video.mp4')],check=True)
    animation={'request_id':'chosen','base':'assets/a/','resources':{'mp4':{'relative_path':'video.mp4'}},
        'resource_manifest':[], 'steps':[{'step_id':'S1','caption':'旧字幕','start':0,'end':1}]}
    src={'units':{'U':{'title':'来源'}},'animations':{'U':[animation]}}
    current={'units':{'U':{'title':'正文的新标题'}},'animations':{'U':[{**animation,
        'steps':[{'step_id':'S1','start':0,'end':99}]},{'request_id':'unselected','base':'old-mechanical/'}]}}
    for root,data in [(original,src),(output,current)]:
        (root/'data/data.js').write_text('window.__DATA__ = '+json.dumps(data)+';')
    selection={'U':[{'request_id':'chosen','narration_steps':[{'step_id':'S1',
        'narration_text':'留意这个点，它始终没有离开单位圆。','caption_tex':'单位圆 $|z|=1$。',
        'display_markdown':'留意这个点，它始终没有离开单位圆 $|z|=1$。'}]}]}
    result=tool().revoice(original,output,selection,workers=1,backend=Tone())
    assert result['complete'] is True
    from tools.upgrade_study_reader import load_reader_data
    updated=load_reader_data(output)
    assert updated['units']['U']['title']=='正文的新标题'
    assert len(updated['animations']['U'])==1
    step=updated['animations']['U'][0]['steps'][0]
    assert step['source_end']==1 and step['end']==pytest.approx(2.1)
    assert step['display_markdown']=='留意这个点，它始终没有离开单位圆 $|z|=1$。'
    assert step['caption_tex']=='单位圆 $|z|=1$。'
    assert '单位圆' in step['narration_text']


def test_assets_only_works_without_a_shared_reader_data_file(tmp_path):
    from test_natural_narration import Tone
    import imageio_ffmpeg
    source=tmp_path/'source';(source/'data').mkdir(parents=True);(source/'assets').mkdir()
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i',
        'color=size=160x90:rate=10:duration=1','-c:v','libx264',str(source/'assets/video.mp4')],check=True)
    original={'units':{'U':{}},'animations':{'U':[{'request_id':'a','base':'assets/',
        'resources':{'mp4':{'relative_path':'video.mp4'}},'resource_manifest':[],
        'steps':[{'step_id':'S1','caption':'单位圆','start':0,'end':1}]}]}}
    (source/'data/data.js').write_text('window.__DATA__ = '+json.dumps(original)+';')
    output=tmp_path/'new-book';results=tmp_path/'neural-media.json'
    report=tool().revoice(source,output,{'U':[{'request_id':'a'}]},backend=Tone(),
        assets_only=True,results_file=results)
    assert report['complete'] is True
    assert not (output/'data/data.js').exists()
    generated=json.loads(results.read_text())['animations'][0]
    assert generated['unit_id']=='U' and generated['request_id']=='a'
    assert (output/generated['animation']['base']/'narrated.mp4').is_file()
    class UnavailableVoice:
        source={'backend':'unavailable-test','voice':'none'}
        def synthesize(self,text,destination): raise RuntimeError('network refused')
    failed=tool().revoice(source,output,{'U':[{'request_id':'a'}]},backend=UnavailableVoice(),
        assets_only=True,results_file=results)
    assert failed['complete'] is False and failed['errors']
    assert json.loads(results.read_text())['animations']==[], 'a failed replacement cannot publish stale rows'
