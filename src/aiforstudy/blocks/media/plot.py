"""Matplotlib curves sampled only from approved ASTs on certified domain pieces."""
from __future__ import annotations
import math
from fractions import Fraction
import numpy as np
from matplotlib.figure import Figure
from ...errors import ContractError, CapabilityError
from ..math import evaluate_expression,format_expression
from ..math.expression import ast_names,normalize_ast
from .static_common import fields,layout_spec,binding,product,svg_figure,context_required


def _linear(node,variable):
    op=node['op']
    if op=='const': return Fraction(0),Fraction(str(node['value']))
    if op=='var' and node['name']==variable: return Fraction(1),Fraction(0)
    if op=='pow' and node['exponent'] in (0,1): return (Fraction(0),Fraction(1)) if node['exponent']==0 else _linear(node['base'],variable)
    if op in ('add','sub','mul','div'):
        a,b=_linear(node['left'],variable); c,d=_linear(node['right'],variable)
        if op=='add': return a+c,b+d
        if op=='sub': return a-c,b-d
        if op=='mul' and not a: return c*b,d*b
        if op=='mul' and not c: return a*d,b*d
        if op=='div' and not c and d: return a/d,b/d
    raise CapabilityError('unknown plot domain: guard is not affine; no unchecked cross-domain curve is drawn')


def _guards(node):
    op=node['op']; guards=[]
    if op in ('const','var'): return guards
    if op=='pow':
        if node['exponent']<0: guards.append(('ne',node['base']))
        return guards+_guards(node['base'])
    if op in ('sin','cos','exp','log','sqrt','abs'):
        if op in ('log','sqrt'): guards.append(('gt' if op=='log' else 'ge',node['arg']))
        return guards+_guards(node['arg'])
    if op=='div': guards.append(('ne',node['right']))
    return guards+_guards(node['left'])+_guards(node['right'])


def sample_series(context,object_ref,variable,domain,*,samples=257):
    if type(samples)!=int or not 2<=samples<=512: raise ContractError("samples must be an integer in 2..512")
    context_required(context); obj=context.object_by_ref(object_ref); model=context.model_by_id(object_ref['model_id'])
    if obj['kind']!='scalar': raise ContractError('plot requires a scalar object')
    symbols={s['name']:s for s in model.symbols}
    if variable not in symbols or symbols[variable]['domain']!='real' or ast_names(obj['expr'])-{variable}: raise CapabilityError('plot supports one real independent variable')
    normalize_ast(obj['expr'],allowed_names=set(symbols))
    if not isinstance(domain,list) or len(domain)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) or abs(x)>1e6 for x in domain) or not domain[0]<domain[1]: raise ContractError('finite increasing bounded plot domain required')
    lo,hi=map(float,domain); guards=_guards(obj['expr'])
    for condition in model.conditions:
        guards.extend(_guards(condition['left'])+_guards(condition['right']))
        guards.append((condition['op'],{'op':'sub','left':condition['left'],'right':condition['right']}))
    for assumption in symbols[variable]['assumptions']:
        guards.append(({'positive':'gt','nonnegative':'ge','negative':'lt','nonpositive':'le','nonzero':'ne'}[assumption],{'op':'var','name':variable}))
    normalized=[]; cuts={Fraction(str(lo)),Fraction(str(hi))}
    for op,expr in guards:
        a,b=_linear(expr,variable); normalized.append((op,a,b))
        if a:
            root=-b/a
            if lo<=root<=hi: cuts.add(root)
    def allowed(x):
        xx=x if isinstance(x,Fraction) else Fraction(str(x))
        for op,a,b in normalized:
            value=a*xx+b
            if not {'ne':value!=0,'eq':value==0,'gt':value>0,'ge':value>=0,'lt':value<0,'le':value<=0}[op]: return False
        return True
    cuts=sorted(cuts); segments=[]; excluded=[float(x) for x in cuts if not allowed(x)]
    rejected=[]
    for left,right in zip(cuts,cuts[1:]):
        if not allowed((left+right)/2): rejected.append([float(left),float(right)]); continue
        inset=(right-left)/512
        start=left if allowed(left) else left+inset; end=right if allowed(right) else right-inset
        xs=np.linspace(float(start),float(end),samples)
        ys=np.asarray(evaluate_expression(obj,model.symbols,{variable:xs}),dtype=float)
        if ys.ndim==0: ys=np.full_like(xs,float(ys))
        if ys.shape!=xs.shape or not np.all(np.isfinite(ys)): raise ContractError('content: invalid approved plotting expression')
        segments.append([[float(x),float(y)] for x,y in zip(xs,ys)])
    if not segments: raise ContractError('content: approved conditions leave no drawable interval')
    return {'object_ref':object_ref,'domain':[lo,hi],'variable':variable,'segments':segments,
            'excluded_points':excluded,'excluded_intervals':rejected,'domain_method':'original_ast_affine_domain_guards_v1'}


def compile_plot(spec,context):
    context_required(context); fields(spec,('schema_version','series','layout'))
    if spec['schema_version']!='MediaPlotV2': raise ContractError('expected MediaPlotV2')
    rows=spec['series']
    if not isinstance(rows,list) or not 1<=len(rows)<=4: raise ContractError('plot requires 1..4 series')
    ids=[]; data=[]; bindings=[]
    for row in rows:
        fields(row,('id','object_ref','variable','domain'),'','plot series')
        if not isinstance(row['id'],str) or not row['id'] or row['id'] in ids: raise ContractError('unique series ID required')
        ids.append(row['id']); data.append(sample_series(context,row['object_ref'],row['variable'],row['domain']))
        bindings.append(binding(row['id'],row['object_ref'],context))
    layout=layout_spec(spec['layout'],ids)
    fig=Figure(figsize=(8,5),dpi=100); ax=fig.subplots(); ax.set_facecolor('#f8fafc'); colors=['#2563eb','#d97706','#0f766e','#a21caf']
    all_y=[]
    for index,(row,series) in enumerate(zip(rows,data)):
        obj=context.object_by_ref(row['object_ref']); model=context.model_by_id(row['object_ref']['model_id'])
        label=row['object_ref']['object_id']+' = $'+format_expression(obj['expr'],symbols=model.symbols)+'$'
        for part,segment in enumerate(series['segments']):
            a=np.array(segment); all_y.extend(a[:,1]); ax.plot(a[:,0],a[:,1],color=colors[index],lw=2.4,label=label if part==0 else None)
        for point in series['excluded_points']: ax.axvline(point,ls=':',lw=1,color='#64748b')
    low,high=np.quantile(all_y,[.02,.98]); span=max(high-low,1)
    ax.set_ylim(low-.15*span,high+.15*span)
    ax.axhline(0,lw=.8,color='#94a3b8'); ax.axvline(0,lw=.8,color='#94a3b8'); ax.grid(alpha=.2)
    ax.set_xlabel(rows[0]['variable'],fontsize=24); ax.set_ylabel('value',fontsize=24); ax.legend(fontsize=layout['font_size']*1.5,loc='best')
    ax.set_title('Approved function and domain',fontsize=26,pad=16)
    ax.tick_params(labelsize=24)
    from matplotlib.ticker import MaxNLocator
    ax.xaxis.set_major_locator(MaxNLocator(nbins=4)); ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    fig.subplots_adjust(left=.16,right=.95,bottom=.19,top=.85)
    layout={**layout,'y_viewport':[float(low-.15*span),float(high+.15*span)],'clipped_to_viewport':True}
    relations=[{'rule_id':'plot.domain_segments.v1','status':'pass','method':'original_ast_domain_guards','sample_count':sum(len(s) for d in data for s in d['segments'])}]
    return product('plot',spec,context,{'series':data},bindings,relations,layout,svg_figure(fig))
