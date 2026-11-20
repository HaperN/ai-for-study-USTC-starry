"""Bounded host-owned display primitives; all mathematical values come from context."""
from __future__ import annotations
from fractions import Fraction
from html import escape
from io import StringIO
import math
import re
from ...core.digest import digest_of
from ...errors import ContractError, CapabilityError
from ..math import evaluate_expression
from .math_context import ApprovedMathContext

CONSTRUCTION_SPEC_FIELDS = ('schema_version', 'construction', 'layout')


def fields(value, required, optional=(), label='spec'):
    if not isinstance(value,dict) or set(value)-set(required)-set(optional) or set(required)-set(value):
        raise ContractError(label+': unsupported or missing fields')


def layout_spec(raw, ids):
    fields(raw,(),('font_size','label_offsets'), 'layout')
    size=raw.get('font_size',24)
    if type(size) not in (int,float) or not 14<=size<=36: raise ContractError('layout.font_size must be 14..36')
    offsets=raw.get('label_offsets',{})
    if not isinstance(offsets,dict) or set(offsets)-set(ids): raise ContractError('layout labels must bind existing object IDs')
    for value in offsets.values():
        if not isinstance(value,list) or len(value)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) or abs(x)>50 for x in value):
            raise ContractError('layout label offsets must be finite within 50 pixels')
    return {'font_size':size,'label_offsets':offsets}


def context_required(context):
    if not isinstance(context,ApprovedMathContext): raise ContractError('approved mathematical context required')


def numeric(context, ref, kind=None):
    context_required(context)
    obj=context.object_by_ref(ref); model=context.model_by_id(ref['model_id'])
    if kind and obj['kind']!=kind: raise ContractError('mathematical object kind mismatch')
    if model.symbols or model.conditions: raise CapabilityError('constant geometry/diagram requires approved symbol-free objects and conditions')
    value=evaluate_expression(obj,model.symbols,{})
    import numpy as np
    if np.iscomplexobj(value): raise CapabilityError('real geometric values required')
    return value.tolist() if hasattr(value,'tolist') else value


def exact_scalar(expr):
    op=expr['op']
    if op=='const': return Fraction(str(expr['value']))
    if op in ('add','sub','mul','div'):
        a,b=exact_scalar(expr['left']),exact_scalar(expr['right'])
        return {'add':lambda:a+b,'sub':lambda:a-b,'mul':lambda:a*b,'div':lambda:a/b}[op]()
    if op=='pow': return exact_scalar(expr['base'])**expr['exponent']
    raise CapabilityError('exact relation needs rational constant AST; unsupported relation remains unknown')


def exact_object(context, ref):
    obj=context.object_by_ref(ref)
    if obj['kind']=='scalar': return exact_scalar(obj['expr'])
    if obj['kind']=='vector': return [exact_scalar(x) for x in obj['entries']]
    return [[exact_scalar(x) for x in row] for row in obj['rows']]


def binding(ident,ref,context):
    return {'id':ident,'object_ref':ref,'model_digest':context.model_by_id(ref['model_id']).digest,
            'object_digest':digest_of(context.object_by_ref(ref)),'label':ref['object_id']}


def svg_figure(fig):
    from matplotlib import rc_context
    stream=StringIO()
    with rc_context({'svg.hashsalt':'aifs-semantic-static-v2','text.usetex':False,'svg.fonttype':'none'}):
        fig.savefig(stream,format='svg',metadata={'Date':None})
    return re.sub(r"<!DOCTYPE[^>]*>", "", stream.getvalue())


def svg_text(text,x,y,size=16):
    return f'<text x="{x}" y="{y}" font-size="{size}" font-family="DejaVu Sans, sans-serif" text-anchor="middle" fill="#172b4d">{escape(str(text))}</text>'


def svg_document(parts):
    return '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500" viewBox="0 0 800 500"><rect width="800" height="500" fill="white"/>'+''.join(parts)+'</svg>'


def product(kind,spec,context,data,bindings,relations,layout,svg):
    return {'schema_version':'StaticMathProductV2','kind':kind,'math_context_digest':context.digest,
            'spec_hash':digest_of(spec),'data':data,'data_hash':digest_of(data),'object_bindings':bindings,
            'relations':relations,'layout':layout,'svg':svg,'technical_report':{'ok':True,'mathematical_status':'pass','relations':relations}}
