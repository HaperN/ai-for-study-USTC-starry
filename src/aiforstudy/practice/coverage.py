"""Check evidenced knowledge coverage against the actual published statements."""
from pathlib import Path
import hashlib,json

def stem_digest(question):
    return hashlib.sha256(question['stem'].encode()).hexdigest()

def statement_digest(question):
    source=question.get('source',{})
    statement={'id':question['id'],'stem':question['stem'],
               'source':{k:source[k] for k in ('kind','course','year','question_number','common_header','adaptation_note') if k in source}}
    return hashlib.sha256(json.dumps(statement,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def read_native_coverage_review(result, request, statements):
    """Keep a native review as native evidence, binding its actual input snapshot.

    Host-added statement hashes are a projection of the saved materials, never
    a synthetic API response or a substitute for the reviewer's findings.
    """
    result,request,statements=map(Path,(result,request,statements))
    review=json.loads(result.read_text());job=json.loads(request.read_text())
    bank=json.loads(statements.read_text())
    digest=hashlib.sha256(json.dumps({k:v for k,v in job.items() if k!='request_sha256'},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    if review.get('schema_version')!='NativeKnowledgeCoverageV1' or review.get('engine')!='codex-native-agent' or not review.get('reviewer'):
        raise ValueError('native coverage identity missing')
    if job.get('request_sha256')!=digest or review.get('chapter_request_sha256')!=digest:
        raise ValueError('native coverage request mismatch')
    if job.get('global_question_index')!=statements.name:
        raise ValueError('native coverage statement bank mismatch')
    byid={q['id']:q for q in bank}
    if len(byid)!=len(bank):raise ValueError('duplicate coverage statement')
    for q in bank:
        if q.get('statement_sha256')!=statement_digest(q):raise ValueError('coverage statement changed')
    for q in job.get('questions',[]):
        if q!=byid.get(q['id']):raise ValueError('chapter statement differs from global bank')
    # The chapter request pins its own questions. Cross-chapter statements need
    # the hash actually saved by the reviewer, not a newly hashed mutable bank.
    bound={q['id']:q['statement_sha256'] for q in job.get('questions',[])}
    seen=set()
    for reference in review.get('references',[]):
        ident=reference.get('question_id');expected=reference.get('statement_sha256')
        if ident in seen:raise ValueError('duplicate native coverage reference')
        seen.add(ident)
        if ident not in byid or not expected or expected!=byid[ident]['statement_sha256']:
            raise ValueError('native coverage reference statement changed')
        bound[ident]=expected
    return {**review,'question_statement_hashes':bound,
            'native_evidence':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (result,request,statements)}}

def read_coverage_review(folder):
    """Require the audit to equal the saved real model response, plus host hashes."""
    folder=Path(folder)
    review=json.loads((folder/'coverage.json').read_text())
    response=json.loads((folder/'response.json').read_text())
    request=json.loads((folder/'request.json').read_text())
    data={k:v for k,v in request.items() if k!='sha256'}
    digest=hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    if not response.get('ok') or response.get('request_sha256')!=digest or request.get('sha256')!=digest or review.get('request_sha256')!=digest:
        raise ValueError('coverage audit request evidence mismatch')
    payload={k:v for k,v in review.items() if k not in ('question_stem_hashes','request_sha256')}
    if payload!=response.get('payload'):raise ValueError('coverage audit differs from actual model response')
    expected={q['id']:stem_digest(q) for q in request['payload']['questions']}
    if review.get('question_stem_hashes')!=expected:raise ValueError('coverage statement binding mismatch')
    return review

def evaluate_coverage(questions, obligations, reviews):
    """Reviews are ordered oldest to newest; a later gap cannot inherit old pass."""
    byid={q['id']:q for q in questions};latest={}
    for review in reviews:latest[review['unit_id']]=review
    records=[]
    for obligation in obligations:
        review=latest.get(obligation['primary_unit_id'],{})
        matches=[r for r in review.get('obligations',[]) if r.get('id')==obligation['id']]
        problems=[];used=set()
        if len(matches)!=1:
            records.append({'id':obligation['id'],'unit_id':obligation['primary_unit_id'],'status':'missing','issues':['没有唯一的逐项覆盖审查'],'question_ids':[]});continue
        row=matches[0]
        if row.get('status')!='covered':problems.append('独立覆盖审查尚未通过')
        hashes=review.get('question_stem_hashes',{})
        def bound(task):
            ids=task.get('question_ids',[])
            if not ids or not task.get('evidence'):return False
            for ident in ids:
                if ident not in byid:return False
                if 'question_statement_hashes' in review:
                    if review['question_statement_hashes'].get(ident)!=statement_digest(byid[ident]):return False
                elif hashes.get(ident)!=stem_digest(byid[ident]):return False
            used.update(ids);return True
        targets=[t for t in row.get('targets',[]) if t.get('goal') and bound(t)]
        if len({t['goal'].strip() for t in targets})<2 or len({i for t in targets for i in t['question_ids']})<2:
            problems.append('不足两道有效题承担两种不同训练目标')
        facets={f['id']:f for f in row.get('facets',[])}
        for required in obligation.get('facets',[]):
            if not required.get('exercise_required'):continue
            facet=facets.get(required['id'],{})
            if facet.get('status')!='covered' or not bound(facet):problems.append('子目标未闭合：'+required['id'])
        records.append({'id':obligation['id'],'unit_id':obligation['primary_unit_id'],'status':'covered' if not problems else 'partial','issues':problems,'question_ids':sorted(used)})
    covered=sum(r['status']=='covered' for r in records)
    return {'schema_version':'PracticeCoverageV1','complete':bool(records) and covered==len(records),'obligations':len(records),'covered':covered,'remaining':len(records)-covered,'records':records}
