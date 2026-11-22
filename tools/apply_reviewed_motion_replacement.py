"""Project a separately reviewed Manim replacement into a new reader copy."""
from pathlib import Path
from copy import deepcopy
import argparse,base64,hashlib,json,re,shutil,sys
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from aiforstudy.practice.render import js_data

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def digest(x):return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def apply(reader,packet,review,output):
    reader,packet,review,output=map(lambda p:Path(p).resolve(),(reader,packet,review,output));root=packet.parent
    if output==reader or output.is_relative_to(reader) or reader.is_relative_to(output):raise ValueError('Reader roots overlap')
    if output.exists() and any(output.iterdir()):raise ValueError('Use a new empty reader output')
    spec=read(packet);audit=read(review)
    if (audit.get('status')!='accepted' or audit.get('issues') or audit.get('packet',{}).get('sha256')!=sha(packet)
            or not audit.get('actor',{}).get('independent_from_candidate_author')):raise ValueError('Independent motion review missing or mismatched')
    def asset(row):
        p=(root/row['path']).resolve()
        if not p.is_relative_to(root) or not p.is_file() or sha(p)!=row['sha256'].removeprefix('sha256:'):raise ValueError('Motion asset changed')
        return p
    for key,row in spec['assets_and_evidence'].items():
        asset(row)
        if audit['assets'].get(key,{}).get('sha256')!=row['sha256']:raise ValueError('Review asset mismatch '+key)
    proposal=read(asset(spec['assets_and_evidence']['binding_proposal']));target=proposal['target'];uid,rid=target['unit_id'],target['request_id']
    raw=(reader/'data/data.js').read_text();data=json.loads(raw[raw.index('{'):raw.rfind('}')+1]);original=deepcopy(data)
    unit=data['units'][uid]
    if digest(unit['study_lesson'])!=target['study_lesson_sha256']:raise ValueError('Reviewed lesson changed')
    found=[x for x in data['animations'][uid] if x.get('request_id')==rid]
    if len(found)!=1:raise ValueError('Logical animation is not unique')
    old=deepcopy(found[0]);keep=proposal['keep_logical_binding']
    if old.get('base')!=target['old_base'] or old.get('placement')!=keep['placement']:raise ValueError('Old binding mismatch')
    old_movie=reader/old['base']/old['resources']['mp4']['relative_path']
    if sha(old_movie)!=target['old_video_sha256'].removeprefix('sha256:'):raise ValueError('Old video changed')
    updates=deepcopy(proposal['animation_field_updates']);base=updates['base']
    if not re.fullmatch(r'assets/narrated/CH\d+(?:-[A-Z]+\d+)*/MR-[0-9a-f]+-motion-\d{8}/',base):raise ValueError('Unsafe replacement base')
    if [(s['step_id'],s['start'],s['end']) for s in old['steps']]!=[(s['step_id'],s['start'],s['end']) for s in updates['steps']]:raise ValueError('Narration timeline changed')
    if sha(root/'candidate/voice.mp3')!=hashlib.sha256(base64.b64decode(updates['playback']['audio']['data_base64'],validate=True)).hexdigest():raise ValueError('Inline audio mismatch')
    svg=spec['assets_and_evidence']['svg'];updates['resources']['svg']={'kind':'svg','relative_path':Path(svg['path']).name,'sha256':'sha256:'+svg['sha256'],'bytes':asset(svg).stat().st_size,'mime':'image/svg+xml'}
    updates['resource_manifest'].append(updates['resources']['svg'])
    found[0].update(updates)
    found[0]['svg_alt']='圆弧域、角域与归一化水平条带的同参数静态对照'
    found[0]['static_caption']='示意参数 a=h=1，α=π/6，β=2π/3；边界不属于开域。动态点对应与归一化过程见上方播放器。'
    if data['units']!=original['units']:raise ValueError('Motion replacement changed textbook content')
    for name in data['animations']:
        if name!=uid and data['animations'][name]!=original['animations'][name]:raise ValueError('Unrelated animation changed')
    shutil.copytree(reader,output,dirs_exist_ok=True);dest=output/base;dest.mkdir(parents=True,exist_ok=True)
    public_names={r['relative_path'] for r in updates['resources'].values()}|{'picture.mp4','voice.mp3'}
    for name in public_names:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',name):raise ValueError('Unsafe media filename')
        source=root/'candidate'/name
        if not source.is_file():raise ValueError('Missing public media '+name)
        shutil.copy2(source,dest/name)
    for resource in updates['resources'].values():
        if sha(dest/resource['relative_path'])!=resource['sha256'].removeprefix('sha256:'):raise ValueError('Published resource differs')
    (output/'data/data.js').write_text('window.__DATA__='+js_data(data)+';\n')
    result={'schema_version':'ReviewedMotionReaderProjectionV1','unit_id':uid,'request_id':rid,'input_data_sha256':sha(reader/'data/data.js'),'output_data_sha256':sha(output/'data/data.js'),'packet_sha256':sha(packet),'independent_review_sha256':sha(review),'old_base':old['base'],'new_base':base,'old_video_sha256':sha(old_movie),'new_video_sha256':sha(dest/'narrated.mp4'),'textbook_and_other_animations_unchanged':True,'scope':'Only this media replacement is approved by the linked audit; no whole-unit review promotion.'}
    (output/'reader-motion-replacement-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('reader','packet','review','out'):p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args();print(json.dumps(apply(a.reader,a.packet,a.review,a.out),ensure_ascii=False))
