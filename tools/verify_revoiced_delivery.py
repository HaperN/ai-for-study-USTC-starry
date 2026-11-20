"""Verify delivered PCM, hashes, captions, seeking and second-generation motion."""
import argparse
import json
from pathlib import Path
import sys
import wave
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.revoice_wholebook import apply_script_overrides
from aiforstudy.blocks.media.local_narration import digest, cache_key
from aiforstudy.blocks.media.narration import visual_latex, _stamp


def frame_at(container,seconds):
    stream=container.streams.video[0]
    container.seek(int(seconds/stream.time_base),stream=stream,backward=True)
    for frame in container.decode(stream):
        if float(frame.pts*frame.time_base)>=seconds-.015:
            return frame.to_ndarray(format='rgb24')
    raise ValueError('Seek produced no frame')


def main():
    import av
    import numpy as np
    p=argparse.ArgumentParser()
    for name in ('source','output','config','corpus'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--overrides',type=Path)
    a=p.parse_args();cfg=json.loads(a.config.read_text());original=json.loads((a.source/'neural-media.json').read_text())
    changes=json.loads(a.overrides.read_text())['changes'] if a.overrides else []
    old={(r['unit_id'],r['request_id']):apply_script_overrides(r['animation'],r['unit_id'],r['request_id'],changes) for r in original['animations']}
    corpus=json.loads(a.corpus.read_text())['records'];scripts={(r['unit_id'],r['request_id'],r['step_id']):r for r in corpus}
    new=json.loads((a.output/'neural-media.json').read_text());checks=[]
    for entry in new['animations']:
        uid=entry['unit_id'];rid=entry['request_id'];row=entry['animation'];prior=old[(uid,rid)];base=a.output/'book'/row['base']
        for resource in row['resource_manifest']:
            path=base/resource['relative_path'];assert path.stat().st_size==resource['bytes'] and digest(path)==resource['sha256'].removeprefix('sha256:')
        receipt=json.loads((base/'narrated.narration.json').read_text())
        for path,h in receipt['output_hashes'].items():assert digest(path)==h
        video=base/row['resources']['mp4']['relative_path'];source=a.source/'book'/prior['base']/prior['resources']['mp4']['relative_path']
        assert digest(source)==row['narration']['source_video_sha256'].removeprefix('sha256:')
        vtt=(base/row['resources']['captions']['relative_path']).read_text();transcript=(base/row['resources']['transcript']['relative_path']).read_text()
        assert vtt.count(' --> ')==len(row['steps'])
        with wave.open(str(base/'narrated.wav'),'rb') as w:
            assert w.getframerate()==24000;combined=w.readframes(w.getnframes());assert abs(len(combined)/48000-receipt['duration_seconds'])<.001
        with av.open(str(source)) as cin,av.open(str(video)) as cout:
            for before,step in zip(prior['steps'],row['steps']):
                assert (step['source_start'],step['source_end'])==(before['start'],before['end'])
                assert step['prior_clock_lineage']=={k:before[k] for k in ('source_start','source_end') if k in before}
                text=scripts[(uid,rid,step['step_id'])]['comparison_spoken_text'];assert step['narration_text']==text
                folder=Path(cfg['cache_dir'])/cache_key(text,cfg['identity'])
                with wave.open(str(folder/'audio.wav'),'rb') as w:
                    assert w.getframerate()==24000;pcm=w.readframes(w.getnframes())
                offset=round(step['start']*24000)*2
                assert combined[offset:offset+len(pcm)]==pcm
                assert abs(len(pcm)/48000-step['narration_duration'])<.000001
                assert step['end']-step['start']>=step['narration_duration']
                assert f"{_stamp(step['start'])} --> {_stamp(step['end'])}" in vtt
                assert step['display_markdown'] in transcript
                assert step['display_markdown']==visual_latex(before.get('display_markdown') or before.get('caption_tex') or before.get('observation') or before.get('caption') or text)
                # Seek both clips at the same position within this CURRENT input step.
                delta=min(1.,(before['end']-before['start'])/2)
                first=frame_at(cin,before['start']+delta);second=frame_at(cout,step['start']+delta)
                error=float(np.mean(np.abs(first.astype(float)-second.astype(float))))
                assert error<5,('Motion frame mismatch',uid,step['step_id'],error)
                fps=float(receipt['video_timing']['fps'])
                motion=float(step.get('input_motion_duration',before['end']-before['start']))
                last=max(0,motion-1/fps)
                source_last=frame_at(cin,before['start']+last)
                output_last=frame_at(cout,step['start']+last)
                tail=frame_at(cout,step['end']-1/fps)
                motion_end_error=float(np.abs(source_last.astype(float)-output_last.astype(float)).mean())
                hold_error=float(np.abs(source_last.astype(float)-tail.astype(float)).mean())
                assert motion_end_error<5 and hold_error<5,('Last motion/hold mismatch',uid,step['step_id'],motion_end_error,hold_error)
                checks.append({'motion_end_frame_error':motion_end_error,'hold_end_frame_error':hold_error,'unit_id':uid,'step_id':step['step_id'],'pcm_exact':True,'script_exact':True,'latex_preserved':True,'current_input_window':[before['start'],before['end']],'output_window':[step['start'],step['end']],'seek_frame_mean_absolute_error':error})
    assert len(new['animations'])==12 and len(checks)==57
    result={'status':'passed','animations':12,'steps':57,'checks':checks,'checks_scope':'All resource hashes, original input SHA, exact decoded PCM placement, script/LaTeX preservation, VTT clocks, 171 current-clock decoded frame comparisons (seek, last motion, final hold); not a human pronunciation or naturalness review.'}
    (a.output/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'status':'passed','animations':12,'steps':57}))

if __name__=='__main__':main()
