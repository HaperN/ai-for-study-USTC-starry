"""Deterministic paper contracts: accepted questions, explicit purposes and scope."""
from copy import deepcopy
import re
from .catalog import validate_catalog

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
        p['question_hashes']={i:byid[i]['review']['content_sha256'] for i in ids}
        result.append(p)
    return result
