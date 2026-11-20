"""Revoice a previously narrated delivery using its CURRENT MP4 clock."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from aiforstudy.blocks.media.narration import narrate_animation, configured_backend, spoken_text
from aiforstudy.blocks.media.local_narration import digest
from tools.upgrade_study_reader import _narrate_row


def current_clock_steps(steps):
    """Reset active trim evidence; older generations remain historical only."""
    result=deepcopy(steps)
    for step in result:
        historical=('source_start','source_end','input_motion_duration',
                    'motion_lineage','prior_clock_lineage')
        step['prior_clock_lineage']={k:step.pop(k) for k in historical if k in step}
    return result


def proven_motion_steps(steps, video, receipt_path):
    receipt=json.loads(Path(receipt_path).read_text())
    hashes=[v for k,v in receipt.get('output_hashes',{}).items() if k.endswith('.mp4')]
    if (receipt.get('timing')!='original motion followed by final-frame hold; complete decoded voice'
            or receipt.get('producer_version') not in ('host-narration-1.2-neural','host-narration-1.3-provider-timing')
            or digest(video) not in hashes or len(receipt.get('steps',[]))!=len(steps)):
        raise ValueError('Prior receipt does not prove this current MP4 hold policy')
    result=current_clock_steps(steps)
    for source,prior,current in zip(steps,receipt['steps'],result):
        if any(source.get(k)!=prior.get(k) for k in ('step_id','start','end','source_start','source_end')):
            raise ValueError('Prior receipt clock does not match current manifest')
        duration=float(prior['source_end'])-float(prior['source_start'])
        if not 0<duration<=source['end']-source['start']+.04:raise ValueError('Invalid proven motion interval')
        current['input_motion_duration']=duration
        current['motion_lineage']={'receipt_sha256':digest(receipt_path),'current_video_sha256':digest(video),
            'current_input_window':[source['start'],source['end']],
            'current_motion_window':[source['start'],source['start']+duration],
            'basis':'Hash-matched prior compositor receipt: original motion then final-frame hold; only original interval LENGTH reused'}
    return result


def apply_script_overrides(row,uid,rid,changes):
    row=deepcopy(row)
    for change in changes:
        if (change['unit_id'],change['request_id'])!=(uid,rid):continue
        matching=[s for s in row['steps'] if s['step_id']==change['step_id']]
        if len(matching)!=1 or spoken_text(matching[0])!=change['before']:
            raise ValueError('Script override before text must match exactly')
        step=matching[0];step['narration_text']=change['after']
        if 'canonical_spoken_text' in step:raise ValueError('Canonical reviewed scripts require editorial review')
        if change['before'] not in step.get('display_markdown',''):raise ValueError('Display before text must match exactly')
        step['display_markdown']=step['display_markdown'].replace(change['before'],change['after'],1)
        step['script_revision']={k:change[k] for k in ('before','after')}
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','output','config','corpus'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--receipt-source',type=Path)
    p.add_argument('--overrides',type=Path)
    a=p.parse_args();source=a.source.resolve();output=a.output.resolve()
    if output==source or output.is_relative_to(source) or source.is_relative_to(output):raise ValueError('Separate output required')
    changes=json.loads(a.overrides.read_text())['changes'] if a.overrides else []
    backend=configured_backend(json.loads(a.config.read_text()))
    corpus=json.loads(a.corpus.read_text())['records']
    scripts={(r['unit_id'],r['request_id'],r['step_id']):r for r in corpus}
    manifest=json.loads((source/'neural-media.json').read_text())
    output.mkdir(parents=True,exist_ok=True);rows=[]
    for entry in manifest['animations']:
        row=deepcopy(entry['animation']);uid=entry['unit_id'];rid=entry['request_id']
        row=apply_script_overrides(row,uid,rid,changes)
        video=source/'book'/row['base']/row['resources']['mp4']['relative_path']
        receipt_source=(a.receipt_source or source)/'book'/row['base']/'narrated.narration.json'
        try:
            row['steps']=proven_motion_steps(row['steps'],video,receipt_source)
        except (FileNotFoundError,ValueError) as exc:
            row['steps']=current_clock_steps(row['steps'])
            for step in row['steps']:step['motion_lineage']={'basis':'No hash-matched prior receipt available; full current window retained','reason':str(exc)}
        for s in row['steps']:
            expected=scripts[(uid,rid,s['step_id'])]['comparison_spoken_text']
            if spoken_text(s)!=expected:raise ValueError('Frozen script mismatch: '+uid+'/'+s['step_id'])
        generated=_narrate_row(uid,0,row,source/'book',output/'book',lambda video,steps,folder:narrate_animation(video,steps,folder,backend=backend))
        for resource in generated['resource_manifest']:
            if resource.get('kind')=='keyframe' and 'source_timestamp' in resource:
                at=resource['source_timestamp']
                for step in generated['steps']:
                    if step['source_start']-.001<=at<=step['source_end']+.001:
                        resource['timestamp']=round(step['start']+min(max(0,at-step['source_start']),step.get('input_motion_duration',step['source_end']-step['source_start'])),6);break
        receipt_path=output/'book'/generated['base']/'narrated.narration.json'
        receipt=json.loads(receipt_path.read_text())
        generated['narration'].update(video_timing=receipt['video_timing'],audio_quality=receipt['audio_quality'],input_clock='current narrated MP4 step.start/end',previous_narration=entry['animation'].get('narration'))
        rows.append({**entry,'animation':generated})
        result={'producer_version':receipt['producer_version'],'source_manifest_sha256':digest(source/'neural-media.json'),'source_preserved':True,'animations':rows}
        pending=output/'neural-media.tmp';pending.write_text(json.dumps(result,ensure_ascii=False,indent=2));pending.replace(output/'neural-media.json')
        print(json.dumps({'finished':len(rows),'unit':uid,'duration':receipt['duration_seconds']},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
