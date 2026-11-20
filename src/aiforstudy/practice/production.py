"""Sequential author → answer-hidden solve → comparison → publication receipt."""
from copy import deepcopy
from pathlib import Path
import hashlib,json,shutil
from .model import call_batch, question_input
from .catalog import content_digest,validate_catalog,validate_review_files,PRIORITIES

def _digest(data):return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def _write(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def produce_batch(workspace, questions, output, *, caller=None):
    caller=caller or call_batch;root=Path(output);drafts=deepcopy(questions)
    ids=[]
    for q in drafts:
        for k in ('id','title','stem','topic','unit_id','priority_reason'):
            if not isinstance(q.get(k),str) or not q[k].strip():raise ValueError('missing draft metadata: '+k)
        if q.get('priority') not in PRIORITIES or type(q.get('difficulty')) is not int or not 1<=q['difficulty']<=5:raise ValueError('invalid draft priority/difficulty')
        if not isinstance(q.get('source'),dict) or q['source'].get('kind') not in ('past_exam','textbook_exercise','generated_variant','teacher_material') or not q['source'].get('course'):raise ValueError('missing draft source')
        if q['source']['kind'] in ('past_exam','textbook_exercise') and (type(q['source'].get('pdf_page')) is not int or q['source']['pdf_page']<1 or not q['source'].get('question_number') or not q['source'].get('source_quote')):raise ValueError('missing source page/question/quote')
        question_input(q, 'author')
        for k in ('answer','solution','review','pitfalls'):q.pop(k,None)
        ids.append(q['id'])
    if not ids or len(ids)!=len(set(ids)):raise ValueError('empty or duplicate draft IDs')
    identity=_digest(drafts);root.mkdir(parents=True,exist_ok=True);manifest=root/'production-input.json'
    if manifest.exists() and json.loads(manifest.read_text())['sha256']!=identity:raise ValueError('production output belongs to different inputs')
    _write(manifest,{'sha256':identity,'questions':drafts})
    author=caller(workspace,drafts,root/'author',mode='author')
    blind=caller(workspace,drafts,root/'blind',mode='blind')
    authored={q['id']:q for q in author['payload']['questions']};solved={q['id']:q for q in blind['payload']['questions']}
    if set(authored)!=set(ids) or set(solved)!=set(ids):raise ValueError('author/solver omitted requested IDs')
    ready=[];blocked=[]
    for q in drafts:
        a,b=authored[q['id']],solved[q['id']]
        if a.get('status')!='solved' or b.get('status')!='solved' or a.get('issues') or b.get('issues'):blocked.append({'id':q['id'],'reason':'author or independent solver unable','author':a,'blind':b});continue
        q.update(answer=a['answer'],solution=a['solution'],pitfalls=a.get('pitfalls',[]));q.setdefault('methods',[]);q['independent']=b;ready.append(q)
    accepted=[]
    if ready:
        for q in ready:
            question_input(q, 'compare')
        comp=caller(workspace,ready,root/'comparison',mode='compare')
        judged={q['id']:q for q in comp.get('bound_comparisons',[])}
        if set(judged)!={q['id'] for q in ready}:raise ValueError('comparison lacks host-bound requested IDs')
        ev=[];(root/'evidence').mkdir(exist_ok=True)
        for stage in ('author','blind','comparison'):
            p=root/stage/'response.json';digest=hashlib.sha256(p.read_bytes()).hexdigest();dest=root/'evidence'/(digest[:16]+'-'+stage+'.json');shutil.copy2(p,dest);ev.append({'path':str(dest.relative_to(root)),'sha256':digest})
        for q in ready:
            c=judged[q['id']];b=q.pop('independent')
            if c.get('status')!='accepted' or c.get('issues') or c.get('corrected_answer') or c.get('author_content_sha256')!=content_digest(q):blocked.append({'id':q['id'],'reason':'final comparison requires revision','comparison':c});continue
            q['review']={'status':'accepted','content_sha256':content_digest(q),'author':'author:'+author['request_sha256'],'blind_solver':'blind:'+blind['request_sha256'],'blind_solution_sha256':_digest(b),'comparison':c.get('reason',''),'issues':[],'evidence_files':ev};accepted.append(q)
    if accepted:validate_catalog(accepted);validate_review_files(accepted,root)
    _write(root/'catalog.json',accepted);_write(root/'blocked.json',blocked)
    report={'input_count':len(drafts),'accepted':len(accepted),'blocked':len(blocked),'status':'accepted' if not blocked else 'needs_revision','author_model':author.get('model'),'solver_model':blind.get('model'),'input_sha256':identity}
    _write(root/'production-report.json',report);return report
