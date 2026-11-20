"""Independent matrix-process and approved-claim derivation diagrams."""
from __future__ import annotations
from ...errors import ContractError, CapabilityError
from ..math import verify_math,format_expression
from .static_common import fields,layout_spec,numeric,exact_object,binding,product,svg_text,svg_document,context_required,CONSTRUCTION_SPEC_FIELDS


def compile_diagram(spec,context):
    context_required(context); fields(spec,CONSTRUCTION_SPEC_FIELDS)
    if spec['schema_version']!='MediaDiagramV2': raise ContractError('expected MediaDiagramV2')
    c=spec['construction']; fields(c,('kind',),('before','after','operation','model_id','claim_ids'))
    layout=layout_spec(spec['layout'],('before','after','operation'))
    if c['kind']=='row_operation':
        fields(c,('kind','before','after','operation')); op=c['operation']
        fields(op,('kind','source','target'),('factor',))
        before=numeric(context,c['before'],'matrix'); after=numeric(context,c['after'],'matrix')
        if len(before)>6 or len(before[0])>6: raise CapabilityError('readable matrix diagram supports at most 6x6')
        expected=exact_object(context,c['before']); wanted=exact_object(context,c['after'])
        source,target=op['source'],op['target']
        if any(type(x)!=int or not 0<=x<len(expected) for x in (source,target)) or source==target: raise ContractError('distinct valid row indices required')
        bindings=[binding(k,c[k],context) for k in ('before','after')]
        if op['kind']=='swap':
            fields(op,('kind','source','target')); expected[source],expected[target]=expected[target],expected[source]
            caption=f'R{source+1} ↔ R{target+1}'
        elif op['kind']=='add_multiple':
            fields(op,('kind','source','target','factor')); factor=numeric(context,op['factor'],'scalar'); exact=exact_object(context,op['factor'])
            expected[target]=[a+exact*b for a,b in zip(expected[target],expected[source])]
            caption=f'R{target+1} ← R{target+1} + ({factor:g}) R{source+1}'; bindings.append(binding('factor',op['factor'],context))
        else: raise ContractError('unsupported elementary row operation')
        if expected!=wanted: raise ContractError('content: approved matrix row operation is false; return mathematical truth to content')
        data={'before':before,'after':after,'operation':op}; parts=[svg_text('Elementary row operation',400,55,34),svg_text(caption,400,115,32)]
        positions={}
        for ident,values,cx in [('before',before,205),('after',after,595)]:
            rows,cols=len(values),len(values[0]); width=min(280,45*cols+30); height=40*rows+20; top=270-height/2
            parts.append(f'<path d="M {cx-width/2+10} {top} h -10 v {height} h 10 M {cx+width/2-10} {top} h 10 v {height} h -10" fill="none" stroke="#334155" stroke-width="2"/>')
            for i,row in enumerate(values):
                for j,value in enumerate(row): parts.append(svg_text(f'{value:g}',cx+(j-(cols-1)/2)*45,top+40*(i+1),layout['font_size']*4/3))
            offset=layout['label_offsets'].get(ident,[0,0]); parts.append(svg_text(c[ident]['object_id'],cx+offset[0],top+height+35+offset[1],32)); positions[ident]={'center':[cx,270],'width':width,'height':height}
        parts.append(svg_text('→',400,280,32))
        relations=[{'rule_id':'matrix.row_operation.v1','status':'pass','method':'exact_rational_calculation','operation':op}]
        layout={**layout,'objects':positions}
    elif c['kind']=='derivation':
        fields(c,('kind','model_id','claim_ids')); model=context.model_by_id(c['model_id'])
        if not isinstance(c['claim_ids'],list) or not 1<=len(c['claim_ids'])<=5: raise ContractError('derivation needs 1..5 frozen claims')
        report=verify_math(model,c['claim_ids'])
        if report.status!='pass': raise ContractError('content: derivation claims are not proven: '+report.status)
        parts=[svg_text('Approved mathematical derivation',400,50,32)]; bindings=[]; steps=[]
        relations=[claim for cid in c['claim_ids'] for claim in model.relations_for_claim(cid)]
        if len(relations)>5: raise CapabilityError('readable derivation supports at most 5 mathematical relations')
        for index,claim in enumerate(relations):
            cid=claim['claim_id'];rid=claim['relation_id']
            if claim['kind']!='identity': raise CapabilityError('derivation display currently supports frozen identities')
            left={'model_id':model.model_id,'object_id':claim['lhs_object_id']}; right={'model_id':model.model_id,'object_id':claim['rhs_object_id']}
            for side,ref in [('left',left),('right',right)]: bindings.append(binding(rid+'-'+side,ref,context))
            # Raw labels are host-built object IDs; no free TeX is accepted.
            text=f"{left['object_id']} = {right['object_id']}"; parts.append(svg_text(text,400,120+75*index,32)); steps.append({'claim_id':cid,'relation_id':rid,'left':context.object_by_ref(left),'right':context.object_by_ref(right)})
        data={'steps':steps}; relations=[check.as_dict() for check in report.checks]
    else: raise ContractError('unsupported diagram construction')
    return product('diagram',spec,context,data,bindings,relations,layout,svg_document(parts))
