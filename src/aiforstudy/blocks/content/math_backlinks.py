"""Derive only the redundant reverse index of exact mathematical claim IDs.

This is an author-boundary projection, not a historical content parser or a
mathematical judgement. Explicit contradictory edges never get removed.
"""
from copy import deepcopy

from ...core.digest import digest_of
from ...errors import ContractError

VERSION = 1


def _index(rows, field, path):
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
        return None  # Leave schema/type failures on their original correction path.
    result={}
    for i,row in enumerate(rows):
        identity=row.get(field)
        if not isinstance(identity,str) or not identity.strip():return None
        if identity in result:
            raise ContractError(f'{path}/{i}/{field}: duplicate mathematical backlink identity')
        result[identity]=row
    return result


def derive_math_backlinks(content):
    """Copy and append exact model memberships, preserving existing list order."""
    result=deepcopy(content)
    def finish(changes=()):
        report={'schema_version':'MathBacklinksDerivationV1','version':VERSION,
            'input_hash':digest_of(content),'output_hash':digest_of(result),'changes':list(changes)}
        return result,report
    plan=result.get('teaching_plan') if isinstance(result,dict) else None
    if not isinstance(plan,dict):return finish()
    claims=_index(plan.get('claims'),'id','/teaching_plan/claims')
    models=_index(result.get('math_models'),'model_id','/math_models')
    if claims is None or models is None:return finish()
    memberships={cid:[] for cid in claims}
    for i,(mid,model) in enumerate(models.items()):
        relations=model.get('claims')
        if not isinstance(relations,list) or any(not isinstance(row,dict) for row in relations):
            return finish()
        related=set()
        for j,relation in enumerate(relations):
            cid=relation.get('claim_id')
            if not isinstance(cid,str) or not cid.strip():
                return finish()
            if cid not in claims:
                raise ContractError(f'/math_models/{i}/claims/{j}/claim_id: unknown teaching claim_id {cid!r}; no backlink inferred')
            if cid not in related:memberships[cid].append(mid)
            related.add(cid)
    changes=[]
    for i,(cid,claim) in enumerate(claims.items()):
        path=f'/teaching_plan/claims/{i}/math_model_refs'
        before=claim.get('math_model_refs',[])
        if not isinstance(before,list) or any(not isinstance(mid,str) for mid in before):
            # Ordinary schema diagnostics can authorize correction of a type,
            # but a malformed value is never silently treated as an empty list.
            continue
        if len(before)!=len(set(before)) or not set(before)<=set(memberships[cid]):
            raise ContractError(path+': duplicate, unknown or contradictory explicit mathematical backlink')
        added=[mid for mid in memberships[cid] if mid not in before]
        if not added:continue
        after=[*before,*added]
        changes.append({'path':path,'present_before':'math_model_refs' in claim,
            'before':deepcopy(claim.get('math_model_refs')),'after':after,'added':added})
        claim['math_model_refs']=after
    return finish(changes)
