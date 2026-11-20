"""Package independently reviewed Manim outputs; never approve a render itself."""
from pathlib import Path
import argparse,base64,hashlib,json,shutil,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from aiforstudy.practice.catalog import content_digest
from aiforstudy.practice.media import media_spec_digest,validate_media_manifest

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,data):Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def package(work,catalog,out):
    work,out=Path(work).resolve(),Path(out).resolve()
    if out.exists() and any(out.iterdir()):raise ValueError('use an empty media output folder')
    questions=read(catalog);byid={q['id']:q for q in questions}
    review_path=work/'math-review/final-media-review.json'
    reviews={r['id']:r for r in read(review_path)['items']}
    technical={r['id']:r for r in read(work/'media-technical-verification.json')}
    specs=read(work/'scene-specs.json');items=[];records=[]
    out.mkdir(parents=True,exist_ok=True)
    for spec in specs:
        ident=spec['id'];q=byid[spec['question_id']];digest=content_digest(q)
        audited=reviews[ident];tech=technical[ident]
        if audited['status']!='accepted' or audited.get('issues') or audited['question_content_sha256']!=digest:raise ValueError(ident+': actual media review is not accepted')
        source=Path(__file__).resolve().parents[1]/'examples/practice_media'/(ident+'_scene.py')
        if source.read_text()!=spec['program']['code']:raise ValueError('scene changed after rendering')
        result_path=work/'renders/final'/ident/'result.json';result=read(result_path)
        folder=result_path.parent;dest=out/ident;dest.mkdir()
        svg=(Path(__file__).resolve().parents[1]/'src/aiforstudy/resources/practice/practice-para23-15.svg') if ident=='mapping' else work/'statics'/(ident+'.svg')
        sources={'mp4':folder/'video.mp4','picture':folder/'picture.mp4','audio':folder/'voice.mp3','poster':folder/'poster.png','captions':folder/'captions.vtt','transcript':folder/'transcript.txt','svg':svg,'source':source,'render_receipt':result_path,'math_review':review_path,'technical_review':work/'media-technical-verification.json'}
        for kind in ('mp4','picture','audio','svg','source'):
            if audited['asset_hashes'][kind]!=sha(sources[kind]):raise ValueError(ident+': reviewed '+kind+' changed')
        if tech['video_sha256']!=sha(sources['mp4']) or not tech['video_stream_copy_identical'] or tech['motion_frames_unique']<2 or tech['audio_rms']<=0:raise ValueError(ident+': motion/audio evidence missing')
        files={}
        for kind,path in sources.items():
            target=dest/path.name;shutil.copy2(path,target);files[kind]={'path':str(target.relative_to(out)),'sha256':sha(target)}
        write(dest/'program.json',spec);files['program']={'path':ident+'/program.json','sha256':sha(dest/'program.json')}
        item={'id':ident,'origin':'practice','kind':'animation','engine':'manim','question_id':q['id'],'question_content_sha256':digest,'unit_id':q.get('mount_unit_id',q['unit_id']),'request_id':spec['program']['request_id'],'title':spec['title'],'purpose':spec['purpose_markdown'],'files':files,'steps':result['steps'],'duration':tech['duration'],'render_receipt_sha256':files['render_receipt']['sha256'],'captions_text':sources['captions'].read_text(),'transcript_text':sources['transcript'].read_text(),'static_existing_in_solution':ident=='mapping','svg_alt':spec['title']+'的条件与对应关系','static_caption':'静态 SVG 保留关键条件和结论；动态过程请在电子版中分步观察。','playback':{'video_src':'practice-media/'+ident+'/picture.mp4','audio':{'src':'practice-media/'+ident+'/voice.mp3','data_base64':base64.b64encode(sources['audio'].read_bytes()).decode(),'mime':'audio/mpeg'}}}
        titles={s['step_id']:s.get('display_title',s['step_id']) for s in spec['steps']}
        item['steps']=[{**s,'display_title':titles[s['step_id']]} for s in item['steps']]
        item['review']={'status':'accepted','question_content_sha256':digest,'asset_hashes':{k:v['sha256'] for k,v in files.items()},'math_checks':audited['math_checks'],'visual_checks':audited['visual_checks'],'motion_verified':True,'actual_audio_listened':False,'scope':'Independent script and actual frame/SVG review; audio decoded and non-silent, no human listening assessment.'}
        records.append({'id':ident,'spec_sha256':media_spec_digest(item),'review':item['review']});items.append(item)
    evidence=out/'publication-review.json';write(evidence,{'schema_version':'PracticeMediaReviewV1','items':records})
    for item in items:item['review_evidence']={'path':evidence.name,'sha256':sha(evidence)}
    manifest={'schema_version':'PracticeMediaV1','items':items};validate_media_manifest(manifest,questions,out);write(out/'manifest.json',manifest)
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('work','catalog','out'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();result=package(a.work,a.catalog,a.out);print(json.dumps({'animations':len(result['items']),'ids':[r['question_id'] for r in result['items']]},ensure_ascii=False))
