"""Pure question contracts. A review is valid only for exactly its reviewed content."""
from copy import deepcopy
import hashlib
import json
import re

PRIORITIES = ('必会', '可能考', '罕见')
CONTENT_FIELDS = ('id','title','stem','answer','solution','topic','unit_id','source','methods','pitfalls')

def content_digest(question):
    payload = {key: question.get(key) for key in CONTENT_FIELDS}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def validate_catalog(questions):
    if not isinstance(questions, list) or not questions:
        raise ValueError('catalog must contain questions')
    seen = set()
    for q in questions:
        if not isinstance(q, dict):
            raise ValueError('question must be an object')
        ident = q.get('id')
        if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]+', ident):
            raise ValueError('invalid question identity')
        if ident in seen: raise ValueError('duplicate question: '+ident)
        seen.add(ident)
        for field in ('title','stem','answer','solution','topic','unit_id'):
            if not isinstance(q.get(field),str) or not q[field].strip():
                raise ValueError(ident+': missing '+field)
        visible=[q.get(key,'') for key in ('title','stem','answer','solution','topic','priority_reason')]
        visible.extend(q.get('methods',[]));visible.extend(q.get('pitfalls',[]))
        if any(isinstance(text,str) and re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',text) for text in visible):
            raise ValueError(ident+': control character in published mathematics')
        for text in visible:
            if not isinstance(text,str):continue
            math_spans=re.findall(r'(?s)(?<!\\)\${1,2}.*?(?<!\\)\${1,2}|\\\(.*?\\\)|\\\[.*?\\\]',text)
            if any(re.search(r'\t(?:heta|au|imes|ext(?:bf|it)?|anh?|ilde)\b|\r(?:ho|ight|angle|ceil|floor)\b',span) for span in math_spans):
                raise ValueError(ident+': escaped TeX command became a control character')
        if q.get('priority') not in PRIORITIES or not q.get('priority_reason'):
            raise ValueError(ident+': priority requires a supported evidence reason')
        if type(q.get('difficulty')) is not int or not 1 <= q['difficulty'] <= 5:
            raise ValueError(ident+': difficulty must be 1-5')
        source = q.get('source')
        if not isinstance(source,dict) or source.get('kind') not in ('past_exam','textbook_exercise','generated_variant','teacher_material') or not source.get('course'):
            raise ValueError(ident+': missing source provenance')
        if source['kind'] in ('past_exam','textbook_exercise') and (type(source.get('pdf_page')) is not int or source['pdf_page'] < 1 or not source.get('question_number') or not source.get('source_quote')):
            raise ValueError(ident+': original source page/question/quote missing')
        review=q.get('review',{})
        if not isinstance(review,dict) or review.get('status')!='accepted' or review.get('issues') != []:
            raise ValueError(ident+': independent review is not accepted')
        if review.get('content_sha256') != content_digest(q):
            raise ValueError(ident+': reviewed content digest mismatch')
        if not review.get('author') or not review.get('blind_solver') or review['author']==review['blind_solver']:
            raise ValueError(ident+': independent solver identity required')
        if not re.fullmatch('[0-9a-f]{64}',str(review.get('blind_solution_sha256',''))) or not review.get('comparison'):
            raise ValueError(ident+': blind review evidence missing')
    return questions

def select_edition(questions, edition, profile=None, additions=None):
    validate_catalog(questions)
    if edition not in ('basic','full','custom'): raise ValueError('unknown edition')
    profile=profile or {}
    allowed={'include_ids','exclude_ids','topics','priority_overrides','title','teacher','evidence_note'}
    if set(profile)-allowed: raise ValueError('unknown profile keys')
    extra=additions or []
    if extra: validate_catalog(extra)
    if edition!='custom' and (profile or extra): raise ValueError('teacher configuration requires custom edition')
    combined=questions+extra
    validate_catalog(combined)
    byid={q['id']:q for q in combined}
    include=set(profile.get('include_ids',[]));exclude=set(profile.get('exclude_ids',[]))
    if (include|exclude)-set(byid): raise ValueError('unknown teacher question reference')
    if include&exclude: raise ValueError('teacher includes and excludes the same question')
    if edition=='basic': chosen=[q for q in questions if q.get('basic') is True]
    elif edition=='full': chosen=questions
    else:
        topics=set(profile.get('topics',[]))
        chosen=[q for q in combined if q['id'] not in exclude and (q.get('basic') is True or q['id'] in include or q['topic'] in topics)]
    result=deepcopy(chosen)
    overrides=profile.get('priority_overrides',{})
    if set(overrides)-set(byid): raise ValueError('unknown priority override')
    for q in result:
        if q['id'] in overrides:
            o=overrides[q['id']]
            if o.get('priority') not in PRIORITIES or not o.get('reason'): raise ValueError('teacher priority override requires reason')
            q['priority']=o['priority'];q['priority_reason']=o['reason']
    return sorted(result,key=lambda q:(q['unit_id'],PRIORITIES.index(q['priority']),q['difficulty'],q['id']))

def validate_review_files(questions, root):
    from pathlib import Path
    base=Path(root).resolve();cache={}
    def rows(data):
        if isinstance(data,list):return [r for r in data if isinstance(r,dict)]
        if not isinstance(data,dict):return []
        return rows(data.get('bound_comparisons',[]))+rows(data.get('questions',[]))+rows(data.get('payload'))
    for q in questions:
        files=q.get('review',{}).get('evidence_files')
        if not isinstance(files,list) or not files:raise ValueError(q['id']+': review evidence files missing')
        records=[]
        for row in files:
            path=(base/row['path']).resolve()
            if not path.is_relative_to(base):raise ValueError('evidence path escape')
            key=(str(path),row['sha256'])
            if key not in cache:
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
                    raise ValueError(q['id']+': evidence hash mismatch')
                try:cache[key]=rows(json.loads(path.read_text(encoding='utf-8')))
                except ValueError:raise ValueError(q['id']+': evidence must be structured review records')
            records.extend(cache[key])
        matches=[r for r in records if r.get('id')==q['id']]
        if not any(r.get('status')=='accepted' and r.get('issues')==[] and r.get('author_content_sha256')==content_digest(q) for r in matches):
            raise ValueError(q['id']+': accepted comparison does not bind current content')
        if not any(r.get('status')=='solved' and r.get('answer') and r.get('solution') and hashlib.sha256(json.dumps(r,ensure_ascii=False,sort_keys=True).encode()).hexdigest()==q['review'].get('blind_solution_sha256') for r in matches):
            raise ValueError(q['id']+': independent blind solution evidence mismatch')
    return True
