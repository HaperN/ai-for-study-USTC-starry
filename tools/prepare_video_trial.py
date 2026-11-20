#!/usr/bin/env python3
"""Prepare the existing BV1ki4y1A7Mh trial as an explicit evidence manifest.

Reads existing artifacts only; it neither downloads nor calls recognition models.
The output must reside at the trial root so all source paths are relative.
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('trial_root',type=Path)
a=p.parse_args(); root=a.trial_root.resolve()
claims=json.loads((root/'curated-lecture/claims-evidence.json').read_text(encoding='utf-8'))
timing=json.loads((root/'gemini-review/temporal-check/results.json').read_text(encoding='utf-8'))
source=json.loads((root/'source-final-manifest.json').read_text(encoding='utf-8'))
video=root/'media/BV1ki4y1A7Mh_p1.mp4'
expected='d721c5732732a2fc665718a63ced658e9b10b3234421708c234e06ffde36166b'

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def asset(identity,path,expected_hash=None):
    path=Path(path)
    if not path.is_absolute(): path=root/path
    path=path.resolve(); rel=path.relative_to(root).as_posix(); actual=digest(path)
    if expected_hash and actual!=expected_hash: raise ValueError('original hash mismatch: '+identity)
    return {'id':identity,'path':rel,'sha256':actual}

files=[asset('video',video,expected),asset('audio','media/p1-audio-clean.m4a',source['files'][2]['sha256']),
       asset('claims','curated-lecture/claims-evidence.json'),asset('source-manifest','source-final-manifest.json'),
       asset('trial-status','trial-manifest.json'),asset('decode-status','video-full-validation.json'),
       asset('curated-page','curated-lecture/index.html'),
       asset('independent-review-text','gemini-review/independent-review.txt')]
for path in sorted((root/'report-assets').rglob('*')):
    if path.is_file(): files.append(asset('report-asset:' + path.relative_to(root/'report-assets').as_posix(),path))
for key,e in claims['evidence'].items(): files.append(asset(key,e['path'],e.get('sha256')))
observations=[]
for claim in claims['claims']:
    start,end=claim['approx_source_interval_seconds'] or (None,None)
    observations.append({'id':claim['id'],'source_id':'video','start':start,'end':end,
        'text':claim['statement'],'category':claim['status'],'evidence_ids':claim['evidence_ids'],
        'time_kind':'approximate_source_interval' if start is not None else 'unlocated'})
for i,event in enumerate(timing['events']):
    refs=[]
    for side in ('before','after'):
        key=f'transition-{i}-{side}'; refs.append(key)
        files.append(asset(key,event[side],event[side+'_sha256']))
    observations.append({'id':f'transition-{i}','source_id':'video','start':event['before_pts_s'],
        'end':event['first_threshold_crossing_pts_s'],'bbox':event['bbox_xyxy'],'text':event['roi'],
        'category':'manual_region_transition','time_kind':'recorded_pts', 'evidence_ids':refs,
        'confirmation_pts_s':event['confirmation_pts_s'],'method_note':event['manual_validation']})
# Regression observations preserve failure modes as candidates, with original artifacts.
regression_files = {
    'ocr-660':'vision/docling-nonchina-formula-output/frame-00660.md',
    'ocr-720':'vision/docling-nonchina-formula-output/frame-00720.md',
    'asr-notes':'asr/README.md',
    'asr-710':'asr/qwen-mlx-0.6b-4bit-20s-from710s-sample.txt',
    'frame-613':'gemini-review/frame-0613.png',
    'frame-626':'gemini-review/frame-0626.png',
}
files.extend(asset(key, value) for key,value in regression_files.items())
regressions = [
    ('minus', 660, 720, '质子质量指数负号可能漏识；对照原帧和 OCR 原稿。', ['ocr-660','ocr-720','F720','trial-status']),
    ('repeat', 0, 12, '提示词转写出现重复课程名；保留原输出并回听。', ['ASR-W','asr-notes']),
    ('intermediate', 612.542, 626, '题干与图像分时出现；保留原帧及局部时序。', ['frame-613','frame-626','TIMING','REVIEW']),
    ('offset', 628.167, 628.208, '模型时间锚与首次出现 PTS 有偏差；时间锚不能替代原帧。', ['TIMING','REVIEW','ARGON']),
    ('oral-qualification', 710, 730, '不考虑万有引力与不考虑重力的口述区分需要保留；识别稿仍需回听。', ['asr-710','asr-notes','ASR-Q']),
]
for identity,start,end,text,refs in regressions:
    observations.append({'id':'regression-'+identity,'source_id':'video','start':start,'end':end,
        'text':text,'category':'candidate_regression','time_kind':'review_window','evidence_ids':refs})
probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)]))
stream=next(s for s in probe['streams'] if s['codec_type']=='video')
manifest={'schema_version':'1.0','course_id':'electromagnetism-experiment','media_id':'video',
    'duration_seconds':float(probe['format']['duration']),'width':stream['width'],'height':stream['height'],
    'time_base':stream['time_base'], 'streams':[{k:s[k] for k in ('index','codec_type','codec_name','time_base')} for s in probe['streams']],
    'files':files,'observations':observations,'scope':claims['scope'],
    'limitations':claims['uncertainties'],'status_note':'Original model outputs and reviews are candidates, not host semantic verification; trial-status preserves failed and blocked runs.'}
out=root/'video-evidence-manifest.json'
out.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'manifest':str(out),'files':len(files),'observations':len(observations)},ensure_ascii=False))
