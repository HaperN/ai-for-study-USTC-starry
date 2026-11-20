import hashlib
import json
from pathlib import Path
from tools.refresh_lesson_transcripts import refresh
from aiforstudy.checks.offline import parse_webvtt_cues


def test_full_written_explanation_keeps_existing_voice_and_video(tmp_path):
    book=tmp_path/'book';base=book/'assets/clip';base.mkdir(parents=True)
    (base/'voice.mp4').write_bytes(b'unchanged video and voice')
    resources={k:{'kind':k,'relative_path':p} for k,p in
               [('captions','narrated.vtt'),('transcript','narrated.txt'),('mp4','voice.mp4')]}
    step={'step_id':'S1','start':0,'end':8,'narration_text':'圆周的角度是派。','caption_tex':r'$\pi$'}
    results=tmp_path/'results.json'
    results.write_text(json.dumps({'animations':[{'unit_id':'U','request_id':'M','animation':
        {'base':'assets/clip','resources':resources,'resource_manifest':list(resources.values()),'steps':[step]}}]}))
    manifest=tmp_path/'lessons.json'
    display='圆周的角度是π。\n'+r'弧度 $\pi$。'
    manifest.write_text(json.dumps({'units':{'U':{'media':[{'request_id':'M','narration_steps':[
        {'step_id':'S1','narration_text':'圆周的角度是π。','display_markdown':display}]}]}}}))
    report=refresh(book,results,[manifest])
    assert report['complete_written_steps']==1
    assert (base/'voice.mp4').read_bytes()==b'unchanged video and voice'
    row=json.loads(results.read_text())['animations'][0]['animation']
    assert '圆周的角度' in row['steps'][0]['display_markdown']
    assert r'$\pi$' in row['transcript_text']
    assert parse_webvtt_cues(row['captions_text'])[0]['text']==row['steps'][0]['display_markdown']
    r=row['resources']['transcript'];assert r['sha256']=='sha256:'+hashlib.sha256((base/r['relative_path']).read_bytes()).hexdigest()
