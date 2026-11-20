"""Goal-based, approval-bound production media requirements (V2 only)."""
from __future__ import annotations
from copy import deepcopy
import re
from ...core.digest import digest_of
from ...errors import ContractError
from ...core.review_mode import reviews_frozen
from ..audit.evidence import resolve_json_pointer

KINDS = ('source_image', 'drawing', 'plot', 'geometry', 'diagram', 'animation')
POLICY_VERSION = 'media-policy-author-v1'
CRITERIA_VERSION = '1'


def content_hash(content):
    from ..content.content_contracts import content_digest
    return content_digest(content)


def require_approval(content, approval):
    if reviews_frozen(): return
    from ..content.content_contracts import parse_content, as_dict, LessonContentV4
    from ..content.content_approval import validate_content_approval
    c=as_dict(content); a=as_dict(approval)
    if not isinstance(parse_content(c), LessonContentV4):
        raise ContractError('生产媒体要求 LessonContentV4；历史 V1 需求不能回退生产')
    validate_content_approval(c,a)
    if a.get('content_revision') != c.get('revision'):
        raise ContractError('媒体内容批准 revision 已过期')


def require_content_binding(content, binding):
    """Accept immutable identity without granting semantic approval."""
    if reviews_frozen(): return
    if isinstance(binding,dict) and binding.get('schema_version')=='ContentIdentityV1':
        from ..content.review_outcome import validate_content_identity
        return validate_content_identity(content,binding)
    return require_approval(content,binding)


def visible_fields(content):
    """The reader owns traversal, field order and structural object identity."""
    from ..content.reader_fields import iter_teaching_fields
    return list(iter_teaching_fields(content))


def candidates(content):
    """One explicit decision per approved goal, with all its reader evidence."""
    if reviews_frozen():
        plan=content.get('teaching_plan') or {}
        goals=plan.get('goals',[]) if isinstance(plan,dict) else []
        return [{'target_id':goal.get('id'),'goal_id':goal.get('id'),'kind':'goal',
                 'level':goal.get('level'),'required':(goal.get('visual_requirement') or {}).get('required') is True
                    if isinstance(goal.get('visual_requirement') or {},dict) else False,
                 'visual_requirement':deepcopy(goal.get('visual_requirement') or {}),
                 'claim_refs':deepcopy(goal.get('claim_refs') or []),'reader_evidence':[],
                 'text':str(goal.get('observable_task') or '')}
                for goal in goals if isinstance(goal,dict)] if isinstance(goals,list) else []
    fields={r['pointer']:r for r in visible_fields(content)}
    plan=content.get('teaching_plan') or {}; rows=[]
    for goal in plan.get('goals') or []:
        evidence=[]
        for item in content.get('teaching_evidence') or []:
            if item.get('goal_id')!=goal['id']: continue
            pointer=item.get('reader_pointer')
            if pointer not in fields: raise ContractError('目标教学证据不是可见字段: '+str(pointer))
            evidence.append({**deepcopy(fields[pointer]),'purpose':item['purpose'],'claim_refs':item['claim_refs']})
        obligation=goal.get('visual_requirement') or {}
        rows.append({'target_id':goal['id'],'goal_id':goal['id'],'kind':'goal','level':goal['level'],
                     'required':obligation.get('required') is True,'visual_requirement':deepcopy(obligation),
                     'claim_refs':deepcopy(goal.get('claim_refs') or []),'reader_evidence':evidence,
                     'text':'\n'.join([goal['observable_task']]+[e['text'] for e in evidence])})
    ids=[r['target_id'] for r in rows]
    if not ids or len(ids)!=len(set(ids)): raise ContractError('媒体目标必须具有唯一稳定 ID 且不能为空')
    return rows


def goal_reader_context(content, candidate):
    """Keep explicit evidence distinct from the same structural owner's text.

    A statement is an anchor into one example, not a claim that its proof is
    absent. Never use a reused ID, the teaching plan, or another array row to
    enlarge that evidence. Root fields such as body_markdown stay root fields.
    """
    fields={row['pointer']:row for row in visible_fields(content)}
    explicit=deepcopy(candidate['reader_evidence'])
    pointers={row['pointer'] for row in explicit}
    owners=set()
    for pointer in pointers:
        parts=pointer.strip('/').split('/')
        if (len(parts)>2 and isinstance(content.get(parts[0]),list)
                and parts[1].isascii() and parts[1].isdecimal()):
            owners.add('/'+'/'.join(parts[:2])+'/')
    # The renderer traverses a whole row (including each step and its basis)
    # before the next row. Preserve that order in the review payload as well.
    ordered=[row for p,row in fields.items()
             if not explicit or p in pointers or any(p.startswith(root) for root in owners)]
    return {'explicit_reader_evidence':explicit,
            'supplementary_reader_context':[deepcopy(row) for row in ordered if row['pointer'] not in pointers],
            'repairable_reader_pointers':[row['pointer'] for row in ordered if author_reader_pointer(row['pointer'])],
            'reader_text':'\n'.join(row['text'] for row in ordered)}


#: 冻结字段名（原 `content_patches._PROTECTED`）：作者散文叶不得落在这些字段上。
_PROTECTED = frozenset({'unit_id','book_id','original_text','original_sha256','source_context_hash',
    'source_refs','source_ref','exercise_regions','id','example_id','step_id','claim_id','relation_id',
    'criterion_id','model_id','math_object_id','request_id','request_key','book_solution'})


def author_reader_pointer(pointer):
    """Narrow author prose leaves; source records and scoring never join a repair."""
    if not isinstance(pointer,str) or any(part in _PROTECTED for part in pointer.split('/')):
        return False
    return bool(re.fullmatch(
        r'/(?:body_markdown|reader_intro|one_line|summary|'
        r'prerequisite_recaps/[0-9]+/markdown|annotations/[0-9]+/markdown|'
        r'explainables/[0-9]+/(?:what|why|markdown|warmup|steps/[0-9]+/(?:markdown|basis)|(?:key_points|pitfalls)/[0-9]+)|'
        r'examples/[0-9]+/(?:approach|warmup|basis|conclusion|steps/[0-9]+/(?:markdown|basis)|(?:key_points|pitfalls)/[0-9]+)|'
        r'learning_checks/[0-9]+/(?:hints/[0-9]+|answer|misconception_feedback/[0-9]+/feedback))',pointer))


def resolve_bound_placement(content, placement, *, legacy=False):
    if reviews_frozen():
        placement=deepcopy(placement) if isinstance(placement,dict) else {}
        span=placement.get('span')
        offset=(span[0] if placement.get('position')=='before' else span[1]) if isinstance(span,list) and len(span)==2 else 0
        return {**placement,'status':'resolved','target':placement.get('target_pointer','/body_markdown'),
                'offset':offset,'anchor_id':'media-'+digest_of(placement)[7:27],'reason':'审核已冻结（json_only）'}
    if not isinstance(placement,dict): raise ContractError('placement 必须为对象')
    pointer=placement.get('target_pointer')
    row=next((r for r in visible_fields(content) if r['pointer']==pointer),None)
    if row is None: raise ContractError('placement.target_pointer 不属于当前可见文本字段')
    text=row['text']
    if placement.get('owner_id')!=row['owner_id']: raise ContractError('placement owner_id 不匹配；数组移动后须重新绑定')
    if row.get('step_id') and placement.get('step_id')!=row['step_id']: raise ContractError('placement step_id 不匹配；步骤移动后须重新绑定')
    if placement.get('field_hash')!=row['hash']: raise ContractError('placement field_hash 已过期')
    span=placement.get('span'); anchor=placement.get('anchor_text')
    if not isinstance(span,list) or len(span)!=2 or any(type(n)!=int for n in span) or not 0<=span[0]<span[1]<=len(text) or text[span[0]:span[1]]!=anchor:
        raise ContractError('placement span/anchor_text 与当前字段不对应；重复句必须指定精确 occurrence')
    if placement.get('position') not in ('before','after'): raise ContractError('placement.position 必须为 before/after')
    left,right=span
    if legacy:
        # Explicit V1 reader only. Current composition delegates syntax-block
        # snapping to ReaderLayout and keeps this author span unchanged.
        left=text.rfind('\n\n',0,span[0]); left=left+2 if left>=0 else 0
        right=text.find('\n\n',span[1]); right=len(text) if right<0 else right
    return {**deepcopy(placement),'status':'resolved','target':pointer,'offset':left if placement['position']=='before' else right,'anchor_id':'media-'+digest_of(placement)[7:27],'reason':''}


def _strings(value, field, *, nonempty=False):
    if not isinstance(value,list) or any(not isinstance(x,str) or not x.strip() for x in value) or len(value)!=len(set(value)) or (nonempty and not value):
        raise ContractError(field+' 必须为唯一非空字符串数组')
    return set(value)


def _text(value, field):
    if not isinstance(value,str) or not value.strip(): raise ContractError(field+' 需要明确教学依据')


def validate_criteria(request, *, backend=True):
    if reviews_frozen(): return
    from typing import get_args
    from ..content.content_contracts import AuthorMediaEvidence
    criteria=request.get('acceptance_criteria')
    if not isinstance(criteria,list) or not criteria: raise ContractError('acceptance_criteria 需要结构化冻结标准')
    ids=[]
    for row in criteria:
        if not isinstance(row,dict) or set(row)!={'criterion_id','version','kind','statement','evidence_required'}:
            raise ContractError('媒体标准需要 criterion_id/version/kind/statement/evidence_required')
        _text(row['criterion_id'],'criterion_id'); ids.append(row['criterion_id'])
        if row['version']!=CRITERIA_VERSION or row['kind'] not in ('mathematical','pedagogical','visual','source'):
            raise ContractError('媒体标准 version/kind 不支持')
        _text(row['statement'],'criterion statement'); _strings(row['evidence_required'],'evidence_required',nonempty=True)
        # The author schema owns the evidence vocabulary. Backend selection can
        # restrict that vocabulary, never invent another author-facing protocol.
        supported=set(get_args(AuthorMediaEvidence))
        if backend:
            supported-=({'object_bindings','mathematical_report'} if request.get('kind')=='source_image'
                        else {'source_provenance'})
            if request.get('kind')!='animation': supported-={'captions','steps'}
        if set(row['evidence_required'])-supported:
            raise ContractError('该制作类型无法提供要求的证据：'+str(sorted(set(row['evidence_required'])-supported)))
    if len(ids)!=len(set(ids)): raise ContractError('criterion_id 重复')
    if request.get('criteria_digest')!=digest_of(criteria): raise ContractError('冻结媒体标准摘要已过期')


def _validate_sources(request,content,capabilities,deps):
    refs=request.get('source_refs')
    if not isinstance(refs,list) or not refs: raise ContractError('媒体来源为空')
    for ref in refs:
        if not isinstance(ref,dict): raise ContractError('媒体来源必须为对象')
        if ref.get('kind')=='approved_content':
            found,value=resolve_json_pointer(content,ref.get('pointer',''))
            if not found or ref.get('hash')!=digest_of(value) or ref.get('pointer') not in deps: raise ContractError('已审核教学构造来源链无效')
        elif ref.get('kind')=='asset':
            assets=capabilities.get('source_assets') or {}
            items=(assets.get('source_images') or [])+(assets.get('library') or [])
            if not any(ref.get('asset_id')==a.get('asset_id') and ref.get('hash')==a.get('hash') for a in items): raise ContractError('媒体资源来源 ID/hash 不在真实索引')
        elif ref.get('kind')=='source':
            source=capabilities.get('source_context') or {}
            if not ref.get('quote') or not source.get('source_hash') or ref.get('source_hash')!=source['source_hash'] or ref['quote'] not in source.get('source_text',''): raise ContractError('教材逐字引文/来源摘要无效')
        else: raise ContractError('source_refs kind 必须为 source/approved_content/asset')
    if request['kind']=='source_image' and not any(s.get('kind')=='asset' and s.get('asset_id')==request.get('source_image_id') for s in refs): raise ContractError('教材图必须引用所请求 asset 来源')


def _validate_math(request,content):
    if reviews_frozen(): return
    from ..math.contracts import parse_math_model
    models={m.model_id:m for m in map(parse_math_model,content['math_models'])}
    refs=request.get('math_model_refs'); objects=request.get('object_refs')
    if not isinstance(refs,list) or not isinstance(objects,list): raise ContractError('math_model_refs/object_refs 必须显式数组')
    bound={}
    for ref in refs:
        if not isinstance(ref,dict) or set(ref)!={'model_id','digest'}: raise ContractError('数学模型引用需要 model_id/digest')
        _text(ref['model_id'],'model_id'); _text(ref['digest'],'model digest')
        mid=ref['model_id']; model=models.get(mid)
        if model is None or ref['digest']!=model.digest or mid in bound: raise ContractError('数学模型引用未知、重复或已过期')
        bound[mid]=model
    seen=set()
    for ref in objects:
        if not isinstance(ref,dict) or set(ref)!={'model_id','object_id'}: raise ContractError('对象引用需要 model_id/object_id')
        _text(ref['model_id'],'object model_id'); _text(ref['object_id'],'object_id')
        key=(ref['model_id'],ref['object_id'])
        if key in seen or key[0] not in bound: raise ContractError('对象引用重复或未绑定模型')
        bound[key[0]].object_by_id(key[1]); seen.add(key)
    if request['kind'] in ('plot','geometry','diagram','animation') and (not bound or not objects): raise ContractError('数学媒体需要批准的模型与对象引用；缺失须返修内容')
    claims={c['id']:c for c in content['teaching_plan']['claims']}
    for cid in request['claim_ids']:
        for mid in claims[cid].get('math_model_refs') or []:
            if mid not in bound: raise ContractError('请求遗漏主张所依赖的数学模型')
    for mid,model in bound.items():
        if not any(mid in claims[cid].get('math_model_refs',[]) for cid in request['claim_ids']): raise ContractError('数学模型与请求主张没有关联')


def semantic_dependencies(request,content):
    """Host computes semantic dependencies; the model cannot omit their cache binding."""
    goals=_strings(request.get('goal_refs'),'goal_refs'); claims=_strings(request.get('claim_ids'),'claim_ids')
    refs=request.get('math_model_refs')
    if not isinstance(refs,list) or any(not isinstance(r,dict) for r in refs): raise ContractError('math_model_refs 必须为对象数组')
    models=_strings([r.get('model_id') for r in refs],'math_model_refs.model_id')
    rows=[]
    for collection,ident,selected in (('goals','id',goals),('claims','id',claims)):
        for index,row in enumerate(content['teaching_plan'][collection]):
            if row[ident] in selected: rows.append({'pointer':f'/teaching_plan/{collection}/{index}','hash':digest_of(row)})
    for index,row in enumerate(content['math_models']):
        if row['model_id'] in models: rows.append({'pointer':f'/math_models/{index}','hash':digest_of(row)})
    for index,row in enumerate(content['teaching_evidence']):
        if row['goal_id'] in goals:
            rows.append({'pointer':f'/teaching_evidence/{index}','hash':digest_of(row)})
            found,value=resolve_json_pointer(content,row['reader_pointer'])
            if found: rows.append({'pointer':row['reader_pointer'],'hash':digest_of(value)})
    return rows


def validate_requirements(plan,content,approval,*,capabilities):
    if reviews_frozen(): return
    require_content_binding(content,approval)
    current=approval.get('schema_version')=='ContentIdentityV1'
    binding_field='content_identity_hash' if current else 'approval_ref'
    required={'schema_version','book_id','unit_id','content_hash',binding_field,'policy_digest','capability_digest','revision','decisions','requests','media_policy_version'}
    if not isinstance(plan,dict) or required-set(plan): raise ContractError('MediaRequirementsV2 缺根字段')
    if plan['schema_version']!=('MediaRequirementsV3' if current else 'MediaRequirementsV2') or type(plan['revision'])!=int or plan['revision']<1 or plan['media_policy_version']!=POLICY_VERSION: raise ContractError('媒体需求版本/revision与输入身份不一致')
    if plan['book_id']!=content['book_id'] or plan['content_hash']!=content_hash(content) or plan['unit_id']!=content['unit_id'] or plan['policy_digest']!=approval['policy_digest'] or plan[binding_field]!=digest_of(approval): raise ContractError('需求 content/policy/identity 绑定失效')
    if plan['capability_digest']!=digest_of(capabilities): raise ContractError('需求 capability_digest 已过期')
    from .author_requirements import validate_author_projection
    validate_author_projection(plan,content)
    expected={r['target_id']:r for r in candidates(content)}
    decisions=plan['decisions']; requests=plan['requests']
    if not isinstance(decisions,list) or not isinstance(requests,list) or any(not isinstance(x,dict) for x in decisions+requests): raise ContractError('decisions/requests 必须为对象数组')
    if _strings([d.get('target_id') for d in decisions],'decisions.target_id')!=set(expected): raise ContractError('decisions 必须完整且唯一覆盖所有教学目标')
    ids=_strings([r.get('request_id') for r in requests],'request_id')
    claim_ids={c['id'] for c in content['teaching_plan']['claims']}
    for r in requests:
        fields={'request_id','kind','serves','goal_refs','claim_ids','math_model_refs','object_refs','learning_goal','observe','misconception','source_refs','constraints','placement','dependency_paths','acceptance_criteria','criteria_digest','caption','alt_text'}
        if fields-set(r): raise ContractError('媒体请求缺字段 '+str(sorted(fields-set(r))))
        if r['kind'] not in KINDS or r['kind'] not in capabilities.get('kinds',[]): raise ContractError('媒体后端不支持 kind='+str(r['kind']))
        goals=_strings(r['serves'],'serves',nonempty=True)
        if not goals<=set(expected) or _strings(r['goal_refs'],'goal_refs',nonempty=True)!=goals: raise ContractError('goal_refs/serves 指向未知或不一致目标')
        claims=_strings(r['claim_ids'],'claim_ids')
        if not claims<=claim_ids or not claims<=set().union(*(set(expected[g]['claim_refs']) for g in goals)): raise ContractError('claim_ids 指向未知目标主张')
        for name in ('learning_goal','observe','alt_text'): _text(r[name],name)
        if not isinstance(r['constraints'],dict): raise ContractError('constraints 必须为对象')
        validate_criteria(r); resolve_bound_placement(content,r['placement']); _validate_math(r,content)
        deps=r['dependency_paths']
        if not isinstance(deps,list) or not deps or any(not isinstance(x,dict) for x in deps): raise ContractError('dependency_paths 为空')
        pointers=[]
        for dep in deps:
            found,value=resolve_json_pointer(content,dep.get('pointer',''))
            if not found or dep.get('hash')!=digest_of(value): raise ContractError('媒体依赖字段不存在或摘要过期')
            pointers.append(dep['pointer'])
        if len(pointers)!=len(set(pointers)) or r['placement']['target_pointer'] not in pointers: raise ContractError('placement 字段必须唯一登记实际依赖')
        if any(dep not in deps for dep in semantic_dependencies(r,content)): raise ContractError('请求遗漏目标、主张或数学模型的实际依赖')
        _validate_sources(r,content,capabilities,set(pointers))
    used=set()
    for d in decisions:
        if d.get('goal_id')!=d['target_id']: raise ContractError('goal_id/target_id 必须对应同一目标')
        if d.get('requirement') not in ('required','recommended','none'): raise ContractError('媒体决策 requirement 无效')
        for field in ('reason','benefit','cost','text_alternative'): _text(d.get(field),'decision.'+field)
        refs=_strings(d.get('request_ids'),'decision.request_ids')
        if not refs<=ids: raise ContractError('decision request_ids 未登记')
        if d.get('handled_by') not in ('media','interaction'): raise ContractError('未知 handled_by')
        if d.get('status')=='fulfilled' or d.get('state')=='fulfilled': raise ContractError('规划不能声称 fulfilled；必须等待制作与验收')
        if expected[d['target_id']]['required'] and d['requirement']!='required': raise ContractError('已批准 required 可视化目标不能撤销；能力缺口须重规划或返修内容')
        if d['handled_by']=='interaction':
            if refs or d['requirement']=='none': raise ContractError('交互委派必须显式目标且不伪装媒体请求')
            if capabilities.get('interaction') is not True: raise ContractError('交互能力未声明，不能伪装完成')
            if 'interaction_targets' in capabilities and not capabilities['interaction_targets'].get(d['target_id'],{}).get('templates'):
                raise ContractError('当前目标没有可用交互模板或阅读器落点：'+d['target_id'])
        elif d['requirement']=='required' and not refs: raise ContractError('required 目标没有请求')
        elif d['requirement']=='recommended' and not refs: _text(d.get('not_produced_reason'),'not_produced_reason')
        if d['requirement']=='none' and refs: raise ContractError('none 决策不能挂请求')
        for rid in refs:
            if d['target_id'] not in next(r['serves'] for r in requests if r['request_id']==rid): raise ContractError('decision/request serves 不一致')
        used.update(refs)
    if used!=ids: raise ContractError('存在不服务登记决策的额外媒体请求')
    for r in requests:
        if set(r['serves'])!={d['target_id'] for d in decisions if r['request_id'] in d['request_ids']}: raise ContractError('共享请求没有完整对应目标决策')
    from .check_report import check_report
    return check_report(inputs={'content':content,'approval':approval,'requirements':plan},rules=('approval','coverage','source','dependencies','placement','capabilities','identity','criteria','math_binding'),expected=list(expected),checked=[d['target_id'] for d in decisions],content_hash=content_hash(content),requirements_hash=digest_of(plan),checks=['approval','coverage','source','dependencies','placement','capabilities','identity','criteria','math_binding'])
