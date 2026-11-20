"""Existing bounded templates, with explicit delegation and observable sampling."""
from copy import deepcopy
import math


def verify_template_response(model):
    template=model.get('template'); p=deepcopy(model.get('params') or {})
    before=after=None; control=''
    try:
        if template=='vector-combination':
            vectors=p['vectors']; coefficients=p.get('coefficients') or [1]*len(vectors)
            def evaluate(c): return [sum(float(v[d] if d<len(v) else 0)*float(c[i]) for i,v in enumerate(vectors)) for d in range(max(map(len,vectors)))]
            before=evaluate(coefficients); moved=list(coefficients)
            i=next(i for i,v in enumerate(vectors) if any(float(x)!=0 for x in v)); moved[i]+=0.5
            after=evaluate(moved); control='coefficients/'+str(i)
        elif template=='row-operation':
            matrix=p['matrix']; before=deepcopy(matrix); after=deepcopy(matrix)
            # Scale a nonzero row with a legal nonzero coefficient.
            i=next(i for i,row in enumerate(matrix) if any(float(x)!=0 for x in row))
            after[i]=[float(x)*1.5 for x in matrix[i]]; control='scale-row/'+str(i)
        elif template=='cross-product-3d':
            a,b=p['a'],p['b']
            def cross(a,b): return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
            before=cross(a,b)
            for i in range(3):
                moved=list(a); moved[i]+=0.5; after=cross(moved,b)
                if after!=before: control='a/'+str(i); break
            if after==before:
                for i in range(3):
                    moved=list(b); moved[i]+=0.5; after=cross(a,moved)
                    if after!=before: control='b/'+str(i); break
        elif template=='harmonic-contours':
            x,y=p['initial_point']; extent=p['extent']
            def evaluate(x,y): return {'u':x*x-y*y,'v':2*x*y,'grad_u':[2*x,-2*y],'grad_v':[2*y,2*x]}
            before=evaluate(x,y); moved=x-0.25 if x+0.25>extent else x+0.25; after=evaluate(moved,y); control='initial_point/x'
        elif template=='elimination-steps':
            steps=p['steps']; before=steps[0]['rows']; after=next(s['rows'] for s in steps[1:] if s['rows']!=before); control='step-index'
        else: return {'ok':False,'reason':'模板没有已实现的状态采样器'}
        def finite(value):
            if isinstance(value,(float,int)): return math.isfinite(value)
            if isinstance(value,dict): return all(finite(v) for v in value.values())
            if isinstance(value,list): return all(finite(v) for v in value)
            return isinstance(value,str) and bool(value.strip())
        ok=before!=after and bool(control) and finite(before) and finite(after)
        return {'ok':ok,'template':template,'control':control,'before':before,'after':after,'method':'bounded-template-observable-sampling-v1','reason':'' if ok else '参数变化没有改变有限可观测状态'}
    except (KeyError,TypeError,ValueError,IndexError,StopIteration): return {'ok':False,'reason':'参数/步骤不足以验证真实状态变化','template':template}
