"""Fixed mathematical-coordinate geometry, never guessed screen coordinates."""
from __future__ import annotations
import numpy as np
from matplotlib.figure import Figure
from ...errors import ContractError
from .static_common import fields,layout_spec,numeric,exact_object,binding,product,svg_figure,context_required,CONSTRUCTION_SPEC_FIELDS


def compile_geometry(spec,context):
    context_required(context); fields(spec,CONSTRUCTION_SPEC_FIELDS)
    if spec['schema_version']!='MediaGeometryV2': raise ContractError('expected MediaGeometryV2')
    c=spec['construction']; fields(c,('kind',),('a','b','result','normal','offset','classification'))
    kind=c['kind']; fig=Figure(figsize=(8,5),dpi=100); ax=fig.subplots()
    ax.set_facecolor('#f8fafc'); ax.grid(alpha=.2); bindings=[]
    if kind=='vector_sum':
        fields(c,('kind','a','b','result'))
        layout=layout_spec(spec['layout'],('a','b','result'))
        vals={key:numeric(context,c[key],'vector') for key in ('a','b','result')}
        if any(len(v)!=2 for v in vals.values()): raise ContractError('vector_sum requires two-dimensional vectors')
        a,b,r=(exact_object(context,c[key]) for key in ('a','b','result'))
        if [x+y for x,y in zip(a,b)]!=r: raise ContractError('content: approved vector result does not equal a+b; return mathematical truth to content')
        data={**vals,'origin':[0,0],'translated_b_start':vals['a']}
        colors={'a':'#2563eb','b':'#d97706','result':'#0f766e'}
        starts={'a':[0,0],'b':vals['a'],'result':[0,0]}
        for key in ('a','b','result'):
            start=np.array(starts[key]); end=start+np.array(vals[key]); bindings.append(binding(key,c[key],context))
            ax.annotate('',xy=end,xytext=start,arrowprops={'arrowstyle':'->','color':colors[key],'lw':2.6,'mutation_scale':18})
            off=layout['label_offsets'].get(key,[10,12])
            label=c[key]['object_id']
            ax.annotate(label,xy=(start+end)/2,xytext=off,textcoords='offset pixels',fontsize=layout['font_size'],color=colors[key])
        points=np.array([[0,0],vals['a'],vals['result']]); span=max(float(np.ptp(points,axis=0).max()),1)
        ax.set_xlim(points[:,0].min()-.25*span,points[:,0].max()+.45*span)
        ax.set_ylim(points[:,1].min()-.25*span,points[:,1].max()+.35*span); ax.set_aspect('equal')
        relation={'rule_id':'vector.sum.v1','status':'pass','method':'exact_rational_calculation','object_ids':[c[k]['object_id'] for k in ('a','b','result')]}
        ax.set_title('Vector addition',fontsize=26,pad=16)
        detail='   '.join(c[key]['object_id']+'=('+','.join(f'{v:g}' for v in vals[key])+')' for key in ('a','b','result'))
        fig.text(.5,.025,detail,ha='center',fontsize=24)
    elif kind=='plane':
        fields(c,('kind','normal','offset','classification'))
        layout=layout_spec(spec['layout'],('plane',))
        n=np.array(numeric(context,c['normal'],'vector'),dtype=float); d=numeric(context,c['offset'],'scalar')
        if len(n)!=3 or not np.linalg.norm(n): raise ContractError('plane requires a nonzero three-dimensional normal')
        contains=exact_object(context,c['offset'])==0
        if c['classification'] not in ('affine','subspace'): raise ContractError('plane classification must be affine/subspace')
        if c['classification']=='subspace' and not contains: raise ContractError('content: affine plane not through the origin is not a subspace')
        center=n*d/np.dot(n,n); seed=np.eye(3)[int(np.argmin(abs(n)))]; u=np.cross(n,seed); u=u/np.linalg.norm(u); v=np.cross(n,u); v=v/np.linalg.norm(v)
        vertices=[(center+s*2*u+t*2*v).tolist() for s,t in ((-1,-1),(1,-1),(1,1),(-1,1))]
        data={'normal':n.tolist(),'offset':d,'contains_origin':bool(contains),'vertices':vertices,'projection':'fixed-isometric-orthographic'}
        def project(x): return [x[0]-.65*x[1],x[2]+.4*x[0]+.4*x[1]]
        from matplotlib.patches import Polygon
        points=np.array([project(p) for p in vertices]); ax.add_patch(Polygon(points,facecolor='#93c5fd',edgecolor='#2563eb',alpha=.7))
        for i,name in enumerate(('x','y','z')):
            end=np.array(project(np.eye(3)[i]*4)); ax.annotate(name,end,fontsize=24); ax.plot([0,end[0]],[0,end[1]],color='#94a3b8',lw=1)
        ax.scatter([0],[0],color='#dc2626',s=35); ax.annotate('O',(0,0),xytext=(-18,-15),textcoords='offset pixels',fontsize=24)
        label=' + '.join(f'{n[i]:g}{name}' for i,name in enumerate(('x','y','z')) if n[i])+' = '+f'{d:g}'
        ax.annotate(label,project(center),xytext=layout['label_offsets'].get('plane',[0,18]),textcoords='offset pixels',fontsize=layout['font_size'],ha='center')
        ax.autoscale_view(); ax.margins(.25); ax.set_aspect('equal'); ax.set_title('Plane through O' if contains else 'Affine plane: O is excluded',fontsize=26)
        bindings=[binding('normal',c['normal'],context),binding('offset',c['offset'],context)]
        relation={'rule_id':'plane.origin.v1','status':'pass','contains_origin':bool(contains),'method':'exact_rational_calculation'}
    else: raise ContractError('unsupported fixed geometry construction')
    ax.set_xlabel('x' if kind=='vector_sum' else 'fixed projection',fontsize=24); ax.set_ylabel('y' if kind=='vector_sum' else '',fontsize=24)
    ax.tick_params(labelsize=24)
    from matplotlib.ticker import MaxNLocator
    ax.xaxis.set_major_locator(MaxNLocator(nbins=4)); ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    fig.subplots_adjust(left=.14,right=.94,bottom=.22,top=.86)
    return product('geometry',spec,context,data,bindings,[relation],layout,svg_figure(fig))
