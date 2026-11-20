"""Refresh written lesson captions while preserving verified audio/video bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from aiforstudy.blocks.media.narration import _stamp,visual_latex,spoken_text
from aiforstudy.checks.offline import webvtt_cue_text


def refresh(book,results,manifests):
    book=Path(book).resolve();results=Path(results)
    authored={}
    for manifest in manifests:
        for uid,unit in json.loads(Path(manifest).read_text())['units'].items():
            for media in unit.get('media',[]):
                authored[(uid,media['request_id'])]={s['step_id']:s for s in media['narration_steps']}
    data=json.loads(results.read_text());count=0
    for item in data['animations']:
        row=item['animation'];steps=authored[(item['unit_id'],item['request_id'])]
        base=(book/row['base']).resolve()
        if not base.is_relative_to(book):raise ValueError('caption folder escapes book')
        vtt=['WEBVTT',''];words=[]
        for step in row['steps']:
            planned=steps[step['step_id']]
            if step['narration_text']!=spoken_text(planned):
                raise ValueError('changed spoken script requires new audio')
            display=visual_latex(planned['display_markdown'])
            step['display_markdown']=display
            label=step['step_id'];start,end=_stamp(step['start']),_stamp(step['end'])
            vtt.extend([label,f'{start} --> {end}',webvtt_cue_text(display),''])
            words.append(f'{label}  {start} — {end}\n{display}\n')
            count+=1
        texts={'captions':'\n'.join(vtt)+'\n','transcript':'\n'.join(words)}
        for kind,text in texts.items():
            resource=row['resources'][kind];path=(base/resource['relative_path']).resolve()
            if not path.is_relative_to(base):raise ValueError('caption file escapes folder')
            path.write_text(text,encoding='utf-8')
            resource.update(sha256='sha256:'+hashlib.sha256(path.read_bytes()).hexdigest(),bytes=path.stat().st_size)
            row[kind+'_text' if kind=='captions' else 'transcript_text']=text
        for resource in row['resource_manifest']:
            if resource['kind'] in texts:resource.update(row['resources'][resource['kind']])
        receipt=base/'narrated.narration.json'
        if receipt.is_file():
            saved=json.loads(receipt.read_text());saved['steps']=row['steps']
            for name in saved.get('output_hashes',{}):
                if Path(name).suffix in ('.vtt','.txt'):
                    saved['output_hashes'][name]=hashlib.sha256(Path(name).read_bytes()).hexdigest()
            saved['written_script_refreshed']=True
            receipt.write_text(json.dumps(saved,ensure_ascii=False,indent=2)+'\n')
    results.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    return {'animations':len(data['animations']),'complete_written_steps':count,'audio_video_unchanged':True}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--book',required=True);p.add_argument('--results',required=True)
    p.add_argument('--manifest',nargs='+',required=True);a=p.parse_args()
    print(json.dumps(refresh(a.book,a.results,a.manifest),ensure_ascii=False))
