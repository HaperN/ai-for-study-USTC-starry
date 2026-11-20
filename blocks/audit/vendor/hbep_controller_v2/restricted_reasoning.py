"""Small, source-bound reasoning checker, not a natural-language proof oracle.

Only explicit givens, rational one-variable comparisons, exact arithmetic and
explicitly authorised conditional prerequisites are executable. Unsupported prose
remains unverified. Connectives identify proposed steps; they never grant rules.
"""
from __future__ import annotations

import ast
from fractions import Fraction
import re

from .numeric_reasoning import _eval_numeric

SCHEMA = "restricted-reasoning-v1"
_REL = re.compile(r"(<=|>=|!=|==|=|<|>)")
_GIVEN = re.compile(r"^(?:设|给定|已知|假定)\s*")
_CONCLUSION = re.compile(r"(?:因此|所以|从而|于是|故|显然|可得|得到|这说明|根据[^，,]*[，,]|由[^，,]*[，,])\s*")
_CONDITIONAL = re.compile(r"^(?:若|如果)\s*(.+?)[，,]\s*则\s*(.+)$")
_AUTH = re.compile(r"^(?:允许引用|允许使用|已允许引用)(?:的)?(?:前置)?(?:定理|结论|规则)\s*([^：:]+)[：:]\s*(.+)$")


def _normal(text):
    text = text.strip().strip('。；;，, ').replace('≤', '<=').replace('≥', '>=').replace('≠', '!=')
    for left, right in [('$$', '$$'), (r'\(', r'\)'), (r'\[', r'\]'), ('$', '$')]:
        if text.startswith(left) and text.endswith(right):
            text = text[len(left):-len(right)].strip()
    return text


def _linear(text):
    """Return coefficients and constant; reject nonlinear/implicit syntax."""
    text=text.strip()
    if len(text) > 300:
        raise ValueError('expression budget')
    tree = ast.parse(text.strip(), mode='eval')
    if sum(1 for _ in ast.walk(tree)) > 80:
        raise ValueError('expression budget')
    def walk(node):
        if isinstance(node, ast.Expression): return walk(node.body)
        if isinstance(node, ast.Name) and re.fullmatch(r'[A-Za-z][A-Za-z_0-9]*', node.id):
            return {node.id: Fraction(1)}, Fraction(0)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return {}, _eval_numeric(ast.get_source_segment(text, node))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            a,b=walk(node.operand); k=-1 if isinstance(node.op,ast.USub) else 1
            return {v:k*c for v,c in a.items()}, k*b
        if isinstance(node, ast.BinOp):
            a,b=walk(node.left); c,d=walk(node.right)
            if isinstance(node.op,(ast.Add,ast.Sub)):
                k=-1 if isinstance(node.op,ast.Sub) else 1
                coeff={v:a.get(v,Fraction(0))+k*c.get(v,Fraction(0)) for v in a.keys()|c.keys()}
                return {v:n for v,n in coeff.items() if n}, b+k*d
            if isinstance(node.op,ast.Mult) and not (a and c):
                return ({v:n*d for v,n in a.items()},b*d) if not c else ({v:n*b for v,n in c.items()},b*d)
            if isinstance(node.op,ast.Div) and not c and d:
                return {v:n/d for v,n in a.items()},b/d
        raise ValueError('unsupported expression')
    return walk(tree)


def _predicate(text):
    value=_normal(text)
    parts=_REL.split(value)
    if len(parts)!=3: return None
    try:
        a,b=_linear(parts[0]); c,d=_linear(parts[2])
        coeff={v:a.get(v,Fraction(0))-c.get(v,Fraction(0)) for v in a.keys()|c.keys()}
        coeff={v:n for v,n in coeff.items() if n}
        if len(coeff)>1: return None
        op='=' if parts[1]=='==' else parts[1]
        if not coeff: return ('',op,b-d)
        var,k=next(iter(coeff.items()))
        if k<0: op={'<':'>','>':'<','<=':'>=','>=':'<=','=':'=','!=':'!='}[op]
        return var,op,(d-b)/k
    except (ValueError,SyntaxError,ZeroDivisionError,OverflowError):
        return None


def _holds(pred,value=Fraction(0)):
    var,op,bound=pred
    left,right=(value,bound) if var else (bound,Fraction(0))
    return {'<':left<right,'>':left>right,'<=':left<=right,'>=':left>=right,'=':left==right,'!=':left!=right}[op]


def _cells(var, predicates):
    bounds=sorted({p[2] for p in predicates if p[0]==var})
    if not bounds:return [Fraction(0)]
    return [bounds[0]-1,*bounds,bounds[-1]+1,*((a+b)/2 for a,b in zip(bounds,bounds[1:]))]


def _countermodel(pred, assumptions):
    """Exact cell decomposition on rational order, not sampled heuristics.

    All supported predicates are constant on each open interval between their
    bounds. Check every boundary and one rational representative of every cell.
    """
    var=pred[0]
    if not var: return {} if not _holds(pred) else None
    for point in _cells(var,[pred,*assumptions]):
        if all(_holds(p,point) for p in assumptions) and not _holds(pred,point):
            return {var:str(point)}
    return None


def check_text(text, *, allowed_prerequisites=()):
    checked=[]; blocked=[]; unknown=[]; givens=[]; rules=[]; prerequisites=[]
    declarations=[]; segments=[]; step=0
    def span(start,end):
        return {'start':start,'end':end,'quote':text[start:end]}
    def unknown_span(start,end,reason):
        unknown.append({'start':start,'end':end,'reason':reason})
    def add_rule(statement, evidence, name, origin):
        match=_CONDITIONAL.match(_normal(statement))
        item={'name':name,'statement':statement,'origin':origin,'evidence':evidence,
              'machine_replay_available':bool(match)}
        if match:
            premises=[_normal(p) for p in re.split(r'[；;]|且',match.group(1))]
            conclusion=_normal(match.group(2))
            item.update(conditions=premises,conclusion=conclusion)
            rules.append((premises,conclusion,item))
        prerequisites.append(item)
    for record in allowed_prerequisites:
        if not isinstance(record,dict):
            prerequisites.append({'origin':'approved_external','record':record,'machine_replay_available':False})
            continue
        statement=record.get('statement','')
        # Only a fully supplied conditional can be replayed; a title is not it.
        add_rule(statement,dict(record),record.get('title',record.get('source_atom_id','')),'approved_external')

    offset=0; in_code=False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith('```'):
            in_code=not in_code
            segments.append(('scope',offset,offset+len(line)))
        elif in_code:
            segments.append(('unparsed_code',offset,offset+len(line)))
        elif line.lstrip().startswith('#'):
            segments.append(('scope',offset,offset+len(line)))
        else:
            for m in re.finditer(r'[^。；;\n]+[。；;]?',line):
                start=offset+m.start(); end=offset+m.end()
                while start<end and text[start].isspace():start+=1
                while end>start and text[end-1].isspace():end-=1
                if start<end:segments.append(('text',start,end))
        offset+=len(line)
    for kind,start,end in segments:
        if kind=='unparsed_code':
            unknown_span(start,end,'code_or_quoted_material_requires_semantic_context');continue
        if kind=='scope':
            givens=[]
            unknown_span(start,end,'scope_marker_not_a_verified_mathematical_statement')
            # Inline assumptions/rules from another example are not inherited.
            rules=[r for r in rules if r[2]['origin']=='approved_external']
            continue
        raw=text[start:end]; body=_normal(raw)
        auth=_AUTH.match(body)
        if auth:
            add_rule(auth.group(2),span(start,end),auth.group(1),'text_authorization')
            declarations.append(span(start,end));continue
        if _CONDITIONAL.match(body):
            unknown_span(start,end,'conditional_statement_not_an_initial_fact');continue
        given=_GIVEN.match(body)
        if given:
            value=_normal(body[given.end():]); pred=_predicate(value)
            if pred is not None:
                givens.append((value,pred,span(start,end)))
            else:unknown_span(start,end,'unsupported_given_or_scope')
            continue
        # Nested hypothetical/reductio/example-error contexts require semantic reading.
        if any(marker in body for marker in ('反设','反证','错误示例','错误地','并非','假如','假设','例如若')):
            unknown_span(start,end,'qualified_or_hypothetical_context');givens=[];continue
        cues=list(_CONCLUSION.finditer(body))
        claim=_normal(body[cues[-1].end():]) if cues else body
        pred=_predicate(claim)
        if not cues and pred is None:
            unknown_span(start,end,'unparsed_prose_not_granted_as_fact');continue
        step+=1
        item={'id':f'RS-{step:05d}',**span(start,end),'evidence':[],'rule':'unverified'}
        matching_rule=next((r for r in rules if r[1]==claim and
                            (r[2]['name'] in body or any(w in body for w in ('由此','因此','所以')))),None)
        if matching_rule:
            premises,_,rule=matching_rule
            available={v:ev for v,p,ev in givens}
            item['evidence']=[rule['evidence'],*[available[p] for p in premises if p in available]]
            item['rule']='declared-prerequisite'
            missing=[p for p in premises if p not in available]
            if not missing:
                item['status']='checked_clear';checked.append(item)
                if pred is not None:givens.append((claim,pred,span(start,end)))
            else:
                item.update(status='unresolved',reason='prerequisite_conditions_not_established',missing_conditions=missing)
                blocked.append(item)
            continue
        if pred is None:
            # No supported proposition was extracted: there is no failed
            # inference to adjudicate individually. Keep the exact range for
            # the existing whole-scope semantic review, without manufacturing
            # one paid rebuttal obligation per rhetorical connective.
            unknown_span(start,end,'conclusion_or_inference_outside_supported_grammar');continue
        relevant=[(v,p,ev) for v,p,ev in givens if p[0] in ('',pred[0])]
        item['evidence']=[ev for _,_,ev in relevant]
        if not pred[0]:
            item['rule']='exact-rational-arithmetic'
            if _holds(pred):item['status']='checked_clear';checked.append(item)
            else:
                item.update(status='confirmed_issue',reason='false_exact_numeric_relation',counterexample={})
                blocked.append(item)
            continue
        if not relevant:
            unknown_span(start,end,'no_established_local_conditions');continue
        assumptions=[p for _,p,_ in relevant]
        # Inconsistent givens are not grounds for either endorsement or accusation.
        if not any(all(_holds(p,point) for p in assumptions) for point in _cells(pred[0],assumptions)):
            item.update(status='unresolved',reason='inconsistent_or_unestablished_conditions');blocked.append(item);continue
        counter=_countermodel(pred,assumptions)
        if counter is not None:
            item.update(status='confirmed_issue',reason='counterexample_satisfies_explicit_conditions',
                        counterexample=counter,rule='rational-order-counterexample')
            blocked.append(item)
        elif any(p==pred for _,p,_ in relevant):
            item.update(status='checked_clear',rule='exact-restatement');checked.append(item)
        else:
            item.update(status='unresolved',reason='mathematical_entailment_holds_but_reader_bridge_not_verified',
                        mathematical_relation='entailed_in_rational_linear_scope')
            blocked.append(item)
    # All unverified material remains in the same request body; these coordinates
    # index it instead of appending a duplicate copy of the chapter.
    combined=[]
    for item in unknown:
        if combined and not text[combined[-1]['end']:item['start']].strip():
            prior=combined[-1]
            prior['end']=max(prior['end'],item['end'])
            prior['reasons']=list(dict.fromkeys([*prior['reasons'],item['reason']]))
        else:
            combined.append({'start':item['start'],'end':item['end'],'reasons':[item['reason']]})
    return {'schema':SCHEMA,'checked_span':{'start':0,'end':len(text)},
            'checked_steps':checked,'blocked_steps':blocked,'unverified_spans':combined,
            'allowed_prerequisites':prerequisites,'declarations':declarations,
            'complete':bool(checked) and not blocked and not unknown,
            'scope':'explicit givens; rational arithmetic/order; declared conditional prerequisites',
            'quality_pass':False}
