"""Rebuild the full reader from authored lessons and verified local media."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.revise_textbook_reader import revise
from tools.upgrade_study_reader import load_reader_data


def build(source,previous,output,*,course_library=None):
    source,previous,output=map(lambda p:Path(p).resolve(),(source,previous,output))
    if output in (source,previous) or output/'book'==source:raise ValueError('Use an independent delivery directory')
    from aiforstudy.core.course_library import load_library
    bundle = Path(course_library).resolve() if course_library else ROOT/'data/parse/complex-analysis-v2/course-library'
    if course_library or (bundle/'current.json').exists():
        load_library(bundle)  # Fail before touching a delivery if its explicit source is invalid.
    else:
        bundle = None
    output.mkdir(parents=True,exist_ok=True)
    media=deepcopy(json.loads((previous/'neural-media.json').read_text()))
    title_data=json.loads((ROOT/'data/study-revision-20261029/wholebook/media-step-titles.json').read_text())
    titles={(r['uid'],r['rid'],r['step']):r['title'] for r in title_data['titles']}
    title_matches=set()
    for row in media['animations']:
        for step in row['animation']['steps']:
            key=(row['unit_id'],row['request_id'],step['step_id'])
            if key in titles:step['display_title']=titles[key];title_matches.add(key)
            if row['unit_id']=='CH05-S01-SS02' and '曲面' in step.get('caption',''):step['caption']=step['caption'].replace('曲面','误差曲线')
    if title_matches!=set(titles):raise ValueError('Step title identities do not match current media')
    overrides={}
    for patch_path in sorted((output/'media-corrections').glob('**/resource-metadata-patch.json')):
        patch=json.loads(patch_path.read_text());identity=patch['match']
        matches=[row for row in media['animations'] if row['unit_id']==identity['unit_id'] and row['request_id']==identity['request_id']]
        if len(matches)!=1:raise ValueError('Ambiguous corrected media identity')
        animation=matches[0]['animation']
        if animation['base']!=patch['target_base']:raise ValueError('Corrected media base changed')
        for kind,resource in patch['resource_updates'].items():
            animation['resources'][kind]=resource
            animation['resource_manifest']=[resource if r['kind']==kind else r for r in animation['resource_manifest']]
            overrides[(animation['base'],kind)]=patch_path.parent/resource['relative_path']
        animation['pixel_correction']=patch.get('pixel_correction',{})
    resources=[]
    for row in media['animations']:
        animation=row['animation']
        for kind,resource in animation['resources'].items():
            rel=Path(animation['base'])/resource['relative_path']
            if rel.is_absolute() or '..' in rel.parts:raise ValueError('Media path escaped delivery')
            original=overrides.get((animation['base'],kind),previous/'book'/rel)
            content=original.read_bytes();digest=hashlib.sha256(content).hexdigest()
            if digest!=resource['sha256'].removeprefix('sha256:') or len(content)!=resource['bytes']:raise ValueError('Media signature mismatch: '+str(rel))
            target=output/'book'/rel;target.parent.mkdir(parents=True,exist_ok=True)
            if original!=target:shutil.copy2(original,target)
            resources.append({'path':str(rel),'bytes':len(content),'sha256':digest,'corrected':(animation['base'],kind) in overrides})
    neural=output/'neural-media.json';neural.write_text(json.dumps(media,ensure_ascii=False,indent=2)+'\n')
    revision=ROOT/'data/study-revision-20261029'
    manifests=[revision/'wholebook'/('lessons-'+group+'.json') for group in ('CH01-03','CH04-06','CH07-09')]
    report=revise(source,output/'book',manifests,parse_root=ROOT/'data/parse/complex-analysis-v2',neural_results=neural,course_library=bundle)
    source_assets=[]
    for relative in set(load_reader_data(source).get('source_assets',{}).values()):
        rel=Path(relative)
        if rel.is_absolute() or '..' in rel.parts:raise ValueError('Source image path escaped book')
        original=source/rel
        if not original.is_file():raise FileNotFoundError('Source image is missing: '+str(original))
        target=output/'book'/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,target)
        source_assets.append(str(rel))
    report.update(media_resources=resources,source_image_assets=source_assets,corrected_media_resources=len(overrides),manifest_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in manifests})
    (output/'build-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('units','rewritten_body_characters','planned_visuals','planned_animations','visible_textbook_conclusions','visible_reusable_conclusions','corrected_media_resources')},ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--previous-delivery',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--course-library',type=Path,help='Imported local course bundle; defaults to the textbook parse directory')
    args=parser.parse_args();build(args.source,args.previous_delivery,args.output,course_library=args.course_library)
