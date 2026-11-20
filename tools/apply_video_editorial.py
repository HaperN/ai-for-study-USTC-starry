"""Publish explicit source-checked edits without rewriting model evidence/history."""
from __future__ import annotations
import argparse,hashlib,json,re,sys,time,uuid
from copy import deepcopy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from aiforstudy.core.video_analysis import VideoAnalysisService,validate_lesson
from aiforstudy.core.jsonio import write_json_atomic

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def apply_edits(base,edits):
    result=deepcopy(base);seen=set()
    ids={x['id'] for x in base.get('frames',[])}|{x['id'] for x in base.get('transcript',{}).get('segments',[])}
    for edit in edits:
        pointer=edit['json_pointer']
        if pointer in seen or not re.fullmatch(r'/(?:sections/\d+/(?:markdown|title)|formulas/\d+/(?:latex|note|uncertainty)|corrections/\d+(?:/(?:before|after|reason))?|provenance)',pointer):raise ValueError('Unsupported or duplicate editorial field')
        seen.add(pointer)
        if not edit.get('reason') or not isinstance(edit.get('source_ids'),list) or not edit['source_ids'] or any(i not in ids for i in edit['source_ids']):raise ValueError('Editorial edit lacks valid source references')
        parts=pointer.strip('/').split('/');target=result
        try:
            for part in parts[:-1]:target=target[int(part)] if isinstance(target,list) else target[part]
            key=int(parts[-1]) if isinstance(target,list) else parts[-1]
            if target[key]!=edit['before']:raise ValueError('Editorial before value does not match')
        except (IndexError,KeyError,TypeError) as exc:raise ValueError('Editorial field does not exist') from exc
        after=edit['after']
        if pointer=='/provenance':
            if not isinstance(after,dict) or any(after.get(k)!=v for k,v in edit['before'].items()):raise ValueError('Original model provenance cannot be rewritten')
            if set(after)-set(edit['before'])!={'editorial_revision'}:raise ValueError('Only explicit editorial provenance may be added')
        elif isinstance(target[key],str) and not isinstance(after,str):raise ValueError('Editorial text must remain text')
        elif isinstance(target[key],dict):
            if not isinstance(after,dict) or any(i not in ids for i in after.get('source_ids',[])):raise ValueError('Invalid revised correction record')
        target[key]=deepcopy(after)
    return result

def publish(workspace,job_id,patch_path):
    service=VideoAnalysisService(Path(workspace));directory=service._jobdir(job_id);patch_path=Path(patch_path);patch=json.loads(patch_path.read_text())
    with service._lock(directory/'worker.lock',False):
        row=json.loads((directory/'job.json').read_text())
        if row['state']!='completed':raise ValueError('Only a completed published result can receive editorial edits')
        parent=row.get('active_result','result.json');source=service.artifact(job_id,parent)
        if source is None or sha(source)!=patch['base_sha256']:raise ValueError('Published parent differs from reviewed patch')
        if Path(patch['base_path']).resolve()!=source.resolve():raise ValueError('Patch targets a different published result')
        base=json.loads(source.read_text());result=apply_edits(base,patch['edits'])
        ids={x['id'] for x in base['frames']}|{x['id'] for x in base['transcript']['segments']}
        validate_lesson(result,ids,base['duration'])
        for key in ['source','transcript','frames','coverage']:
            if result[key]!=base[key]:raise ValueError('Original recognition layer changed')
        identity=uuid.uuid4().hex;output=directory/'revisions'/identity;output.mkdir(parents=True)
        relative=output.relative_to(directory).as_posix()
        result['previous_review']={'reviewed_result_sha256':patch['base_sha256'],'review':deepcopy(base['review'])}
        result['review']={'state':'source_checked_edits','decision':'limited_source_check','automatic_approval':False,'issues':[],
            'limitations':['本次仅核对并修正编辑记录中的原片、识别来源及数学记法问题；没有逐句逐帧核实全文。','先前模型的“未发现问题”只针对编辑前版本，保留为历史记录，不能作为当前全文审核通过结论。']}
        result['editorial_revision']={'id':identity,'method':'independent_source_checked_editorial_revision','parent_result':parent,
            'parent_result_sha256':patch['base_sha256'],'patch_sha256':sha(patch_path),'editor_sha256':sha(__file__),
            'editor':'Codex independent source inspection','edits':len(patch['edits']),'model_response_modified':False,'human_mathematical_approval':False}
        result['provenance']['active_editorial_producer']=deepcopy(result['editorial_revision'])
        result['production_accepted']=False;result['quality_state']='candidate'
        result['exports']={'json':relative+'/result.json','markdown':relative+'/lesson.md','corrections':relative+'/edits.json',
            'original_json':'result.json','original_markdown':'lesson.md','parent_json':parent}
        write_json_atomic(output/'result.json',result);write_json_atomic(output/'edits.json',patch);write_json_atomic(output/'review.json',result['review'])
        write_json_atomic(output/'source-check.json',{'kind':'independent_source_checked_editorial_revision','source_ids':sorted({i for e in patch['edits'] for i in e['source_ids']}),
            'input_files':{n:sha(directory/n) for n in ['asr.json','ocr.json','visual.json']},'raw_sources_modified':False,'semantic_completeness_verified':False})
        (output/'lesson.md').write_text(service._markdown(result['title'],result,'../../'+base['source']['path']))
        files={name:sha(output/name) for name in ['result.json','lesson.md','edits.json','review.json','source-check.json']}
        receipt={'id':identity,'state':'completed','stage':'completed','kind':'source_checked_editorial_revision','parent_result':parent,
            'parent_result_sha256':patch['base_sha256'],'files':files,'producer':result['editorial_revision'],'created_at':time.time()}
        write_json_atomic(output/'revision.json',receipt)
        latest=json.loads((directory/'job.json').read_text())
        if latest.get('active_result','result.json')!=parent or sha(source)!=patch['base_sha256']:raise ValueError('Parent changed during editorial publication')
        service._register_artifacts(job_id,[relative+'/'+name for name in files])
        service._update(job_id,active_result=relative+'/result.json',active_result_sha256=files['result.json'],exports=result['exports'],revision={k:v for k,v in receipt.items() if k!='files'},state='completed',stage='completed',progress=1,error=None)
        service._event(job_id,'独立来源核对后的编辑修订已发布；模型原稿与原始识别保留',revision_id=identity)
        return {'job_id':job_id,'revision_id':identity,'result':str(output/'result.json'),'edits':len(patch['edits'])}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',type=Path,required=True);p.add_argument('--job',required=True);p.add_argument('--patch',type=Path,required=True);a=p.parse_args();print(json.dumps(publish(a.workspace,a.job,a.patch),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
