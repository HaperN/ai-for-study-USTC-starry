"""Deterministic paper contracts: accepted questions, explicit purposes and scope."""
from copy import deepcopy
import re
from .catalog import validate_catalog

def _validate_supplied_hashes(paper, ids, current):
    """Absent legacy bindings may be created; explicit bindings are immutable."""
    if 'question_hashes' in paper and (not isinstance(paper['question_hashes'],dict) or paper['question_hashes']!=current):
        raise ValueError('paper question hashes do not match selected reviewed content')
    selected=set(ids)
    grouped=any(item.get('slot_id') is not None for item in paper['items'])
    for slot in paper.get('task_slots') or []:
        evidence=slot.get('selection_evidence') or {}
        if 'question_hashes' not in evidence:continue
        actual=[item['question_id'] for item in paper['items'] if item.get('slot_id')==slot.get('id')] if grouped else None
        refs=slot.get('question_ids',evidence.get('question_ids',actual))
        if (not isinstance(refs,(list,tuple)) or not refs or any(not isinstance(i,str) for i in refs)
                or len(set(refs))!=len(refs) or not set(refs)<=selected
                or (grouped and set(refs)!=set(actual))):
            raise ValueError('selection hashes refer to inconsistent selected slot IDs')
        declared=evidence.get('question_ids',refs)
        if (not isinstance(declared,(list,tuple)) or any(not isinstance(i,str) for i in declared)
                or len(set(declared))!=len(declared) or set(declared)!=set(refs)
                or ('question_id' in evidence and (len(refs)!=1 or evidence['question_id']!=refs[0]))):
            raise ValueError('selection hashes refer to inconsistent selected question IDs')
        expected={i:current[i] for i in refs}
        if not isinstance(evidence['question_hashes'],dict) or evidence['question_hashes']!=expected:
            raise ValueError('selection question hashes do not match selected reviewed content')

def validate_papers(questions, papers):
    validate_catalog(questions)
    byid={q['id']:q for q in questions};seen=set();result=[]
    for original in papers:
        p=deepcopy(original)
        if not isinstance(p.get('id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]+',p['id']): raise ValueError('invalid paper identity')
        if p.get('id') in seen:raise ValueError('duplicate paper')
        seen.add(p.get('id'))
        if p.get('kind') not in ('coverage','fitted','pressure'):raise ValueError('unknown paper class')
        if not p.get('title') or not p.get('purpose') or not p.get('items'):raise ValueError('paper needs a training purpose and items')
        ids=[x.get('question_id') for x in p['items']]
        if len(set(ids))!=len(ids):raise ValueError('duplicate question in paper')
        if set(ids)-set(byid):raise ValueError('unknown paper question')
        current_hashes={i:byid[i]['review']['content_sha256'] for i in ids}
        _validate_supplied_hashes(p,ids,current_hashes)
        for item in p['items']:
            if type(item.get('points')) not in (int,float) or not 0 < item['points'] <= 100:
                raise ValueError('invalid training points')
        if p.get('edition')=='basic' and any(byid[i].get('basic') is not True for i in ids):raise ValueError('basic paper contains non-basic questions')
        covered={byid[i]['topic'] for i in ids}
        if set(p.get('coverage_topics',[]))-covered:raise ValueError('coverage claim is unsupported by selected questions')
        if p['kind']=='fitted' and not p.get('reference'):raise ValueError('fitted paper requires reference')
        if p['kind']=='pressure' and not p.get('obstacle'):raise ValueError('pressure paper requires a genuine obstacle')
        p['total_points']=sum(i['points'] for i in p['items'])
        p['covered_topics']=sorted(covered)
        if 'question_hashes' not in p:p['question_hashes']=current_hashes
        result.append(p)
    return result
