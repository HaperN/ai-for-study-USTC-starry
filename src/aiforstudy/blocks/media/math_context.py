"""The sole approval-bound, immutable mathematical authority for media executors."""
from __future__ import annotations
from dataclasses import dataclass
import json
from ...core.digest import canonical_json, digest_of
from ...errors import ContractError
from ...core.review_mode import reviews_frozen
from ..math import parse_math_model
from .media_requirements import require_approval, require_content_binding, validate_criteria, _validate_math, semantic_dependencies
from ..audit.evidence import resolve_json_pointer


@dataclass(frozen=True, init=False)
class ApprovedMathContext:
    _payload: str

    def __init__(self, *args, **kwargs):
        raise ContractError('ApprovedMathContext requires from_approved_content(content, approval, request)')

    @classmethod
    def from_approved_content(cls, content, approval, request):
        require_approval(content,approval)
        return cls.from_content_binding(content,approval,request)

    @classmethod
    def from_content_binding(cls, content, approval, request):
        """Immutable source identity; callers carry verification state separately."""
        from ..content.content_contracts import as_dict
        c,a=as_dict(content),as_dict(approval)
        if reviews_frozen():
            obj=object.__new__(cls)
            object.__setattr__(obj,'_payload',canonical_json({'content':c,'approval':a,'request':request}))
            return obj
        require_content_binding(c,a)
        if not isinstance(request,dict): raise ContractError('approved media request required')
        validate_criteria(request)
        if not request.get('request_id'): raise ContractError('media request_id required')
        goals={g['id']:g for g in c['teaching_plan']['goals']}
        claims={k['id']:k for k in c['teaching_plan']['claims']}
        if not isinstance(request.get('goal_refs'),list) or not request['goal_refs'] or not set(request['goal_refs'])<=set(goals):
            raise ContractError('media request references unknown teaching goals')
        if request.get('serves')!=request['goal_refs'] or not isinstance(request.get('claim_ids'),list) or not set(request['claim_ids'])<=set(claims):
            raise ContractError('media request references unknown teaching claims')
        _validate_math(request,c)
        deps=request.get('dependency_paths')
        if not isinstance(deps,list): raise ContractError('media dependency_paths required')
        actual={}
        for dep in deps:
            if not isinstance(dep,dict): raise ContractError('media dependency must be a pointer/hash')
            pointer=dep.get('pointer'); found,value=resolve_json_pointer(c,pointer or '')
            if not found or dep.get('hash')!=digest_of(value): raise ContractError('media dependency is stale')
            actual[pointer]=dep['hash']
        if any(actual.get(d['pointer'])!=d['hash'] for d in semantic_dependencies(request,c)):
            raise ContractError('media request omitted approved semantic dependencies')
        obj=object.__new__(cls)
        object.__setattr__(obj,'_payload',canonical_json({'content':c,'approval':a,'request':request}))
        return obj

    def _data(self): return json.loads(self._payload)
    @property
    def unit_id(self): return self._data()['content']['unit_id']
    @property
    def book_id(self): return self._data()['content']['book_id']
    @property
    def request(self): return self._data()['request']
    @property
    def content_digest(self): return digest_of(self._data()['content'],check=False)
    @property
    def approval_digest(self): return digest_of(self._data()['approval'])
    @property
    def digest(self): return digest_of({'content':self.content_digest,'approval':self.approval_digest,'request':self.request})

    def model_by_id(self, model_id):
        refs={r['model_id']:r['digest'] for r in self.request['math_model_refs']}
        if model_id not in refs: raise ContractError('mathematical model is outside approved request: '+str(model_id))
        raw=next(m for m in self._data()['content']['math_models'] if m['model_id']==model_id)
        model=parse_math_model(raw)
        if model.digest!=refs[model_id]: raise ContractError('approved mathematical model digest changed')
        return model

    def object_by_ref(self, ref):
        if not isinstance(ref,dict) or set(ref)!={'model_id','object_id'} or ref not in self.request['object_refs']:
            raise ContractError('object binding is outside approved request')
        return self.model_by_id(ref['model_id']).object_by_id(ref['object_id'])

    def goal_claim_ids(self, goal_id):
        if goal_id not in self.request['goal_refs']: raise ContractError('goal is outside approved request')
        return tuple(next(g for g in self._data()['content']['teaching_plan']['goals'] if g['id']==goal_id)['claim_refs'])

    def claim_model_ids(self, claim_id):
        if claim_id not in self.request['claim_ids']: raise ContractError('claim is outside approved request')
        return tuple(next(c for c in self._data()['content']['teaching_plan']['claims'] if c['id']==claim_id)['math_model_refs'])


def from_approved_content(content, approval, request):
    return ApprovedMathContext.from_approved_content(content,approval,request)
