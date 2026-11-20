"""Revoice manifest-selected textbook animations from their ORIGINAL video clock.

Only animations and generated resources change. Existing lesson prose, reader
layout and JavaScript are not rewritten. Outputs are resumable and fully decoded.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.upgrade_study_reader import load_reader_data, _narrate_row, _digest
from aiforstudy.blocks.media.narration import configured_backend, narrate_animation, VERSION


def load_selections(paths):
    """Read one or more authored lesson manifests, without silently merging IDs."""
    selections={}
    for path in paths:
        document=json.loads(Path(path).read_text(encoding='utf-8'))
        units=document.get('units')
        if not isinstance(units,dict):
            raise ValueError('lesson manifest must contain units')
        for uid,unit in units.items():
            if uid in selections:
                raise ValueError('duplicate unit in lesson manifests: '+uid)
            media=unit.get('media',[])
            if isinstance(media,(dict,str)): media=[media]
            if not isinstance(media,list): raise ValueError('media must be a list: '+uid)
            rows=[];seen=set()
            for entry in media:
                entry={'request_id':entry} if isinstance(entry,str) else deepcopy(entry)
                identity=entry.get('request_id') if isinstance(entry,dict) else None
                if not identity or identity in seen:
                    raise ValueError('missing or duplicate media request_id: '+uid)
                seen.add(identity);rows.append(entry)
            selections[uid]=rows
    return selections


def _write_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    pending=path.with_suffix(path.suffix+'.tmp')
    pending.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    pending.replace(path)


def revoice(source,output,selections,*,workers=3,backend=None,limit=None,assets_only=False,results_file=None):
    source=Path(source).resolve();output=Path(output).resolve()
    if source==output or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('narration output must be independent from the source reader')
    original=load_reader_data(source)
    output.mkdir(parents=True,exist_ok=True)
    # Asset-only batches do not read or write the shared lesson document.
    if not assets_only:
        load_reader_data(output)
    backend=backend or configured_backend(json.loads((ROOT/'config/narration.json').read_text()))
    jobs=[]
    for uid,entries in selections.items():
        by_id={row['request_id']:row for row in original.get('animations',{}).get(uid,[])}
        for entry in entries:
            identity=entry['request_id']
            if identity not in by_id:
                raise ValueError(f'original animation does not exist: {uid}/{identity}')
            row=deepcopy(by_id[identity])
            if row.get('narration') or any('source_start' in step for step in row.get('steps',[])):
                raise ValueError('source contains a previously narrated clock; use the original silent reader')
            overrides=entry.get('narration_steps') or []
            if isinstance(overrides,dict):
                overrides=[{'step_id':key,**value} for key,value in overrides.items()]
            by_step={step['step_id']:step for step in row['steps']}
            seen=set()
            for authored in overrides:
                sid=authored.get('step_id')
                if sid not in by_step or sid in seen:
                    raise ValueError(f'unknown or duplicate authored step: {uid}/{identity}/{sid}')
                seen.add(sid)
                # Authored prose cannot overwrite the original render clock.
                for key in ('narration_text','caption_tex','display_markdown'):
                    if authored.get(key): by_step[sid][key]=authored[key]
            jobs.append((uid,identity,row))
    if limit is not None: jobs=jobs[:limit]
    report={'schema_version':'NaturalLessonNarrationV1','producer_version':VERSION,
        'source':str(source),'source_data_sha256':_digest(source/'data/data.js'),
        'source_preserved':True,'source_voice':backend.source,'requested':len(jobs),
        'finished':0,'errors':[],'complete':False,'animations':[]}
    report_path=output/'natural-narration-report.json'
    results_file=Path(results_file) if results_file else output/'natural-narration-patch.json'
    cumulative={}
    if assets_only and results_file.is_file():
        saved=json.loads(results_file.read_text())
        for entry in saved.get('animations',[]):
            cumulative[(entry['unit_id'],entry.get('request_id') or entry['animation']['request_id'])]=entry
    _write_json(report_path,report)
    completed={}
    def run(uid,identity,row):
        def narrator(video,steps,folder):
            return narrate_animation(video,steps,folder,backend=backend)
        result=_narrate_row(uid,0,row,source,output,narrator)
        receipt=json.loads((output/result['base']/'narrated.narration.json').read_text())
        result['narration'].update(video_timing=receipt['video_timing'],audio_quality=receipt['audio_quality'])
        result['technical_report']['narration'].update(audio_quality=receipt['audio_quality'])
        return result
    with ThreadPoolExecutor(max_workers=max(1,min(int(workers),6))) as pool:
        futures={pool.submit(run,*job):(job[0],job[1]) for job in jobs}
        for future in as_completed(futures):
            uid,identity=futures[future]
            try:
                row=future.result();completed[(uid,identity)]=row
                cumulative[(uid,identity)]={'unit_id':uid,'request_id':identity,'animation':row}
                if assets_only:
                    _write_json(results_file,{'producer_version':VERSION,'animations':list(cumulative.values())})
                report['animations'].append({'unit_id':uid,'request_id':identity,
                    'seconds':row['narration']['duration_seconds'],'base':row['base'],
                    'video_timing':row['narration']['video_timing']})
            except Exception as exc:
                report['errors'].append({'unit_id':uid,'request_id':identity,'error':str(exc)})
                cumulative.pop((uid,identity),None)
                if assets_only:
                    _write_json(results_file,{'producer_version':VERSION,'animations':list(cumulative.values())})
            report['finished']+=1
            _write_json(report_path,report)
            print(f"{report['finished']}/{len(jobs)} {uid}/{identity}",flush=True)
    if assets_only:
        _write_json(results_file,{'producer_version':VERSION,'animations':list(cumulative.values())})
        report.update(complete=not report['errors'],assets_only=True,results_file=str(results_file.resolve()))
        _write_json(report_path,report)
        return report
    # Re-read immediately before the narrow patch so lesson edits made while
    # synthesis was running are retained. Callers serialize this short save.
    current=load_reader_data(output)
    patches=[]
    for uid,entries in selections.items():
        prior={row['request_id']:row for row in current.get('animations',{}).get(uid,[])}
        selected=[]
        for entry in entries:
            identity=entry['request_id'];key=(uid,identity)
            if key not in completed:
                if limit is not None and key not in {(a,b) for a,b,_ in jobs}:
                    existing=prior.get(identity)
                    if existing and existing.get('narration',{}).get('source',{}).get('neural'):
                        selected.append(existing)
                continue
            generated=completed[key]
            merged=deepcopy(prior.get(identity,generated))
            for field in ('base','resources','resource_manifest','steps','captions_text',
                          'transcript_text','narration','technical_report'):
                merged[field]=generated[field]
            selected.append(merged);patches.append({'unit_id':uid,'request_id':identity,'animation':generated})
        current.setdefault('animations',{})[uid]=selected
    _write_json(results_file,{'producer_version':VERSION,'animations':patches})
    data_file=output/'data/data.js';pending=data_file.with_suffix('.js.tmp')
    pending.write_text('window.__DATA__ = '+json.dumps(current,ensure_ascii=False)+';\n',encoding='utf-8')
    pending.replace(data_file)
    report['complete']=not report['errors']
    _write_json(report_path,report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--manifest',required=True,nargs='+',type=Path)
    parser.add_argument('--workers',default=3,type=int)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--assets-only',action='store_true',help='generate assets and JSON rows without reading/writing data.js')
    parser.add_argument('--results-file',type=Path,help='atomic cumulative rows JSON for the root merge')
    args=parser.parse_args()
    result=revoice(args.source,args.output,load_selections(args.manifest),workers=args.workers,limit=args.limit,
                   assets_only=args.assets_only,results_file=args.results_file)
    print(json.dumps({key:result[key] for key in ('complete','requested','finished','errors')},ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['complete'] else 1)
