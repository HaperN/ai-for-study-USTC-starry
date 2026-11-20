"""Author-owned media intent, host bindings, and ticket-scoped intent repair.

The author never invents hashes or offsets. Historical content remains readable,
but absence of author intent is never interpreted as a no-media decision.
"""
from copy import deepcopy

from ...core.digest import digest_of
from ...core.review_mode import reviews_frozen, frozen_review
from ...errors import ContractError
from ..content.content_contracts import AuthorMediaRequirementsV1, as_dict
from ..audit.evidence import resolve_json_pointer
from .media_requirements import (visible_fields, candidates, semantic_dependencies,
                                 resolve_bound_placement, validate_criteria, _validate_math)


def _unreviewed_artifact(requirements, content, response_hash):
    from ..content.json_only import normalize_content
    content = normalize_content(content)
    return {'schema_version': 'AuthorMediaArtifactV1', 'book_id': content['book_id'],
            'unit_id': content['unit_id'], 'content_revision': content['revision'],
            'content_hash': digest_of(content, check=False),
            'source_context_hash': content['source_context_hash'],
            'author_response_hash': response_hash, 'requirements': parse_author_requirements(requirements),
            'bindings': {}, 'goal_bindings': {}, 'errors': [], **frozen_review()}


def _project_unreviewed_requests(requirements, content):
    """Supply renderer/compiler fields without asserting any mathematical binding."""
    from ..content.json_only import normalize_content
    requirements = parse_author_requirements(requirements)
    content = normalize_content(content)
    authored = requirements.get('requests')
    authored = authored if isinstance(authored, list) else []
    rows = []
    for index, request in enumerate(authored):
        if not isinstance(request, dict):
            continue
        row = deepcopy(request)
        key = row['request_key']
        row.setdefault('request_id', 'MR-' + digest_of({'unit_id': content['unit_id'],
            'request_key': key, 'index': index})[7:27])
        row.setdefault('serves', deepcopy(row.get('goal_refs', [])))
        row.setdefault('math_model_refs', [])
        row.setdefault('constraints', {'mathematical_objects': row.get('mathematical_objects', ''),
                                        'conditions': deepcopy(row.get('conditions', []))})
        placement = deepcopy(row.get('placement')) if isinstance(row.get('placement'), dict) else {}
        if isinstance(row.get('placement'), str):
            # Keep prose intent visible to production/review without pretending
            # an unparsed instruction is an exact position in the current text.
            placement['author_intent'] = row['placement']
            placement['reason'] = '作者位置说明尚未精确定位；保留原意，暂按正文末尾展示。'
        placement.setdefault('target_pointer', '/body_markdown')
        placement.setdefault('owner_id', content['unit_id'])
        placement.setdefault('step_id', '')
        placement.setdefault('anchor_text', '')
        placement.setdefault('position', 'after')
        placement.setdefault('span', [0, 0])
        placement.setdefault('field_hash', '')
        row['placement'] = placement
        row.setdefault('dependency_paths', [])
        row.setdefault('source_refs', [])
        row.setdefault('criteria_digest', digest_of(row.get('acceptance_criteria', [])))
        row['review_outcome'] = frozen_review()
        rows.append(row)
    return requirements, rows


def parse_author_requirements(value):
    if reviews_frozen():
        from ..content.json_only import normalize_media_requirements
        return normalize_media_requirements(value)
    try:
        return AuthorMediaRequirementsV1.model_validate(value).as_dict()
    except (TypeError,ValueError) as exc:
        raise ContractError('AuthorMediaRequirementsV1: '+str(exc)) from exc


def author_binding_findings(requirements, content):
    """R3-1：把作者媒体绑定的判定**逐条列出可修位置**，而不是抛裸 ContractError。

    为什么：内层纠正轮取消后，裸抛一律走 `attempt.status='blocked'`，
    journal 里**没有 `merged_feedback`**，下一轮返修看不到"改哪里才算过"。
    这里按 request 逐项核对落点与对象绑定，产出与其它反馈同形的错误行；
    真正的语义绑定仍由 `project_author_requests` 完整校验（这里只负责**说清位置**）。
    """
    if reviews_frozen():
        return []
    findings=[]
    rows={r['pointer']:r for r in visible_fields(content)}
    models={m.get('model_id'):m for m in (content.get('math_models') or []) if isinstance(m,dict)}
    for index, authored in enumerate((requirements or {}).get('requests') or []):
        if not isinstance(authored,dict):
            continue
        root=f'/media_requirements/requests/{index}'
        placement=authored.get('placement') or {}
        pointer=placement.get('target_pointer'); row=rows.get(pointer)
        if row is None:
            findings.append(dict(path=root+'/placement/target_pointer', code='unknown_reader_pointer',
                value=pointer,
                fix='把 target_pointer 指向当前正文里真实存在的读者文本叶（数组用从0开始的数字索引）。',
                passing_criterion='target_pointer 精确命中一个可见读者字段；owner_id/step_id 与它一致。'))
            continue
        if row.get('owner_id')!=placement.get('owner_id') or row.get('step_id','')!=placement.get('step_id',''):
            findings.append(dict(path=root+'/placement/owner_id', code='media_placement_identity',
                candidates=[{'owner_id':row.get('owner_id'),'step_id':row.get('step_id','')}],
                fix='把 owner_id/step_id 改成该落点真实所属的对象与步骤；其余字段不变。',
                passing_criterion='值精确等于宿主候选且其余字段不变；完整响应仍须通过原有严格投影、来源和数学校验。'))
        # 目标映射 / 未知引用：也要给出可修位置，不能只丢一句"映射不一致"
        decisions={d.get('target_id'):d for d in ((requirements or {}).get('decisions') or [])
                   if isinstance(d,dict)}
        serves=set(authored.get('goal_refs') or [])
        mapped={gid for gid,d in decisions.items() if authored.get('request_key') in (d.get('request_ids') or [])}
        if serves and serves!=mapped:
            findings.append(dict(path=root+'/goal_refs', code='media_goal_mapping',
                value=sorted(serves), candidates=sorted(mapped),
                fix='让 goal_refs 与该 request_key 在 decisions[].request_ids 里的登记完全一致。',
                passing_criterion='每个请求都被恰好一个目标的 decisions 登记，且 goal_refs 与登记集合相等。'))
        claims={c.get('id') for c in ((content.get('teaching_plan') or {}).get('claims') or [])}
        unknown_claims=[c for c in (authored.get('claim_ids') or []) if c not in claims]
        if unknown_claims:
            findings.append(dict(path=root+'/claim_ids', code='unknown_claim',
                value=unknown_claims,
                fix='只引用当前 teaching_plan.claims 里真实存在的主张 ID。',
                passing_criterion='claim_ids 全部命中当前教学主张，且被目标所覆盖。'))
        unknown_models=[m for m in (authored.get('math_model_ids') or []) if m not in models]
        if unknown_models:
            findings.append(dict(path=root+'/math_model_ids', code='unknown_math_model',
                value=unknown_models,
                fix='只引用当前 content.math_models 里真实存在的模型 ID。',
                passing_criterion='math_model_ids 全部命中当前数学模型。'))
        for ref_index, ref in enumerate(authored.get('object_refs') or []):
            if not isinstance(ref,dict):
                continue
            model=models.get(ref.get('model_id'))
            ids={o.get('math_object_id') for o in ((model or {}).get('objects') or []) if isinstance(o,dict)}
            if model is None or ref.get('object_id') not in ids:
                findings.append(dict(path=f'{root}/object_refs/{ref_index}/object_id', code='unknown_math_object',
                    value=ref.get('object_id'),
                    fix='只引用同一数学模型里真实存在的 math_object_id，并保留模型身份与来源。',
                    passing_criterion='每个 object_ref 的 model_id/object_id 都命中冻结数学模型中的真实对象。'))
    return findings


def _placement(content, authored):
    rows=[r for r in visible_fields(content) if r['pointer']==authored['target_pointer']]
    if not rows: raise ContractError('作者落点不是当前可见字段')
    row=rows[0]
    if row['owner_id']!=authored['owner_id'] or row.get('step_id','')!=authored.get('step_id',''):
        raise ContractError('作者落点 owner_id/step_id 不匹配；必须明确重绑')
    text=row['text']; anchor=authored['anchor_text']; starts=[]; start=0
    while True:
        index=text.find(anchor,start)
        if index<0: break
        starts.append(index); start=index+1
    occurrence=authored.get('occurrence')
    if not starts: raise ContractError('作者锚句不在当前正文，须更新精确锚句')
    if occurrence is None and len(starts)!=1:
        raise ContractError('作者锚句重复，须明确 occurrence（从1起）')
    occurrence=occurrence or 1
    if occurrence>len(starts): raise ContractError('作者锚句 occurrence 越界')
    index=starts[occurrence-1]
    result={k:v for k,v in authored.items() if k!='occurrence'}
    result.update(span=[index,index+len(anchor)],field_hash=row['hash'])
    resolve_bound_placement(content,result)
    return result


def project_author_requests(requirements,content):
    """Validate intent against current content and compute deterministic bindings."""
    if reviews_frozen():
        return _project_unreviewed_requests(requirements, content)
    requirements=parse_author_requirements(requirements); content=as_dict(content)
    # Missing pedagogical goals are a content-audit finding; they must not be
    # hidden by a structural media failure before that audit can issue a patch.
    goals={g['target_id']:g for g in candidates(content)} if content['teaching_plan']['goals'] else {}
    decisions=requirements['decisions']; authored=requirements['requests']
    ids=[r['request_key'] for r in authored]
    if len(ids)!=len(set(ids)): raise ContractError('作者 request_key 重复')
    if len(decisions)!=len(goals) or {d['target_id'] for d in decisions}!=set(goals):
        raise ContractError('作者 decisions 必须完整且唯一覆盖教学目标')
    from ..math.contracts import parse_math_model
    models={m.model_id:m for m in map(parse_math_model,content['math_models'])}
    claims={c['id']:c for c in content['teaching_plan']['claims']}
    used=set(); mapped={key:set() for key in ids}
    for decision in decisions:
        gid=decision['target_id']; refs=decision['request_ids']
        if decision['goal_id']!=gid or len(refs)!=len(set(refs)) or not set(refs)<=set(ids):
            raise ContractError('作者 decision 目标或请求引用无效')
        if goals[gid]['required'] and decision['requirement']!='required':
            raise ContractError('已批准 required 可视化目标不能撤销')
        if decision['handled_by']=='interaction':
            if refs or decision['requirement']=='none': raise ContractError('作者交互委派必须显式且不挂媒体请求')
        elif decision['requirement']=='required' and not refs:
            raise ContractError('作者 required 目标缺请求')
        elif decision['requirement']=='recommended' and not refs and not decision['not_produced_reason'].strip():
            raise ContractError('作者 recommended 不制作须解释原因')
        if decision['requirement']=='none' and refs: raise ContractError('作者 none 不得挂请求')
        used.update(refs)
        for key in refs: mapped[key].add(gid)
    if used!=set(ids): raise ContractError('作者存在未登记到目标的请求')
    projected=[]
    for request in authored:
        key=request['request_key']; serves=set(request['goal_refs'])
        if serves!=mapped[key]: raise ContractError('作者请求与目标映射不一致：'+key)
        allowed_claims=set().union(*(set(goals[g]['claim_refs']) for g in serves))
        if not set(request['claim_ids'])<=allowed_claims or not set(request['claim_ids'])<=set(claims):
            raise ContractError('作者请求引用未知主张：'+key)
        if not set(request['math_model_ids'])<=set(models): raise ContractError('作者请求引用未知数学模型：'+key)
        for ref in request['object_refs']:
            if ref['model_id'] not in request['math_model_ids']: raise ContractError('作者对象未绑定数学模型：'+key)
            models[ref['model_id']].object_by_id(ref['object_id'])
        placement=_placement(content,request['placement'])
        row={k:deepcopy(v) for k,v in request.items() if k not in ('kind','math_model_ids','placement')}
        row.update(request_id='MR-'+digest_of({'unit_id':content['unit_id'],'request_key':key})[7:27],
                   kind=request['kind'],serves=request['goal_refs'],placement=placement,
                   math_model_refs=[{'model_id':mid,'digest':models[mid].digest} for mid in request['math_model_ids']],
                   constraints={'mathematical_objects':request['mathematical_objects'],'conditions':request['conditions']},
                   criteria_digest=digest_of(request['acceptance_criteria']))
        _validate_math(row,content)
        # Generic image intent may compile to a source image, so backend-specific
        # evidence support is checked after compilation, not guessed here.
        validate_criteria(row,backend=False)
        dep={'pointer':placement['target_pointer'],'hash':placement['field_hash']}
        deps={d['pointer']:d for d in [dep,*semantic_dependencies(row,content)]}
        row['dependency_paths']=list(deps.values())
        row['source_refs']=[{'kind':'approved_content',**dep}]
        projected.append(row)
    return requirements,projected


def _goal_bindings(content):
    claims={c['id']:c for c in content['teaching_plan']['claims']}
    return {g['id']:digest_of({'goal':g,'claims':[claims.get(cid,{'missing_claim_id':cid}) for cid in g['claim_refs']],
        'source_context_hash':content.get('source_context_hash')}) for g in content['teaching_plan']['goals']}


def bind_author_requirements(requirements,content,*,author_response_hash,previous=None,allow_stale=False):
    """Rebinding may move offsets, but never acknowledges changed mathematical intent."""
    if reviews_frozen():
        return _unreviewed_artifact(requirements, content, author_response_hash)
    content=as_dict(content); requirements=parse_author_requirements(requirements)
    errors=deepcopy((previous or {}).get('errors',[])); bindings={}
    goal_bindings=_goal_bindings(content)
    if previous is not None:
        old_goals={d['goal_id'] for d in previous['requirements']['decisions']}
        for gid,value in goal_bindings.items():
            if gid in old_goals and (previous.get('goal_bindings') or {}).get(gid)!=value:
                errors.append({'request_key':'goal:'+gid,'code':'AUTHOR_MEDIA_DECISION_STALE',
                    'goal_ids':[gid],'message':'目标 '+gid+' 的任务或关联主张已变化（或缺旧绑定）；作者须重新核对媒体决定与理由'})
    semantic_by_key={}
    for request in requirements['requests']:
        reference={**request,'math_model_refs':[{'model_id':mid} for mid in request['math_model_ids']]}
        semantic=[d for d in semantic_dependencies(reference,content)
                  if d['pointer'].startswith(('/teaching_plan/','/math_models/'))]
        key=request['request_key'];semantic_by_key[key]=semantic
        old=(previous or {}).get('bindings',{}).get(key)
        if old and old['semantic_dependencies']!=semantic:
            errors.append({'request_key':key,'code':'AUTHOR_MEDIA_SEMANTICS_CHANGED',
                'message':'正文返修改变目标、主张或数学模型；须核对该请求的对象条件与绑定'})
    try:
        _,rows=project_author_requests(requirements,content)
        for row in rows:
            key=row['request_key']
            semantic=semantic_by_key[key]
            bindings[key]={'placement':row['placement'],'semantic_dependencies':semantic}
    except ContractError as exc:
        current_goals={g['id'] for g in content['teaching_plan']['goals']}
        decided={d['target_id'] for d in requirements['decisions']}
        if current_goals-decided and decided<=current_goals:
            errors.append({'request_key':'','code':'AUTHOR_MEDIA_NEW_GOALS','goal_ids':sorted(current_goals-decided),
                'message':'正文修复新增教学目标；原作者须明确补充这些目标的媒体决定，不能自动补 none'})
        # Isolate broken anchors without granting authority over other requests.
        for request in requirements['requests']:
            try: _placement(content,request['placement'])
            except ContractError as issue:
                errors.append({'request_key':request['request_key'],'code':'AUTHOR_MEDIA_BINDING','message':str(issue)})
        if not errors: errors=[{'request_key':'','code':'AUTHOR_MEDIA_BINDING','message':str(exc)}]
    errors=list({(e['request_key'],e['code']):e for e in errors}.values())
    if errors:
        for key,value in (previous or {}).get('bindings',{}).items():
            if any(e['request_key']==key for e in errors): bindings[key]=deepcopy(value)
        for error in errors:
            if error['code']=='AUTHOR_MEDIA_DECISION_STALE':
                for gid in error['goal_ids']:
                    goal_bindings[gid]=(previous or {}).get('goal_bindings',{}).get(gid)
    if errors and not allow_stale: raise ContractError('作者媒体需求绑定失败：'+'；'.join(e['message'] for e in errors))
    return {'schema_version':'AuthorMediaArtifactV1','book_id':content['book_id'],'unit_id':content['unit_id'],
            'content_revision':content['revision'],'content_hash':digest_of(content,check=False),
            'source_context_hash':content['source_context_hash'],'author_response_hash':author_response_hash,
            'requirements':requirements,'bindings':bindings,'goal_bindings':goal_bindings,
            'status':'stale' if errors else 'bound','errors':errors}


def validate_author_artifact(record,content,*,allow_stale=False):
    if reviews_frozen():
        before = record if isinstance(record, dict) else {}
        return {**deepcopy(before), **_unreviewed_artifact(
            before.get('requirements', before), content, before.get('author_response_hash', ''))}
    if not isinstance(record,dict) or record.get('schema_version')!='AuthorMediaArtifactV1':
        raise ContractError('缺作者同期媒体需求 AuthorMediaArtifactV1；不能自动补 none')
    content=as_dict(content)
    for key,value in {'content_hash':digest_of(content,check=False),'content_revision':content['revision'],
                      'book_id':content['book_id'],'unit_id':content['unit_id'],
                      'source_context_hash':content['source_context_hash']}.items():
        if record.get(key)!=value: raise ContractError('作者媒体需求版本绑定失效：'+key)
    if not record.get('author_response_hash'): raise ContractError('作者媒体需求缺原始响应来源')
    if record.get('status')=='stale' and allow_stale: return deepcopy(record)
    if record.get('status')!='bound': raise ContractError('作者媒体需求需要具体返修：'+str(record.get('errors')))
    fresh=bind_author_requirements(record['requirements'],content,author_response_hash=record['author_response_hash'])
    if record.get('goal_bindings')!=fresh['goal_bindings']: raise ContractError('作者媒体目标语义绑定缺失或已过期')
    if record.get('bindings')!=fresh['bindings']: raise ContractError('作者媒体需求落点/语义摘要已过期')
    return deepcopy(record)


def validate_author_projection(plan,content):
    if reviews_frozen():
        return validate_author_artifact((plan or {}).get('author_media'), content)
    record=validate_author_artifact(plan.get('author_media'),content)
    if plan.get('author_media_hash')!=digest_of(record): raise ContractError('作者媒体需求摘要失效')
    _,rows=project_author_requests(record['requirements'],content)
    actual={r['request_key']:r for r in plan['requests']}
    if len(actual)!=len(plan['requests']) or set(actual)!={r['request_key'] for r in rows}:
        raise ContractError('技术编译不得丢失或新增作者请求')
    for before in rows:
        after=actual[before['request_key']]
        allowed={'animation'} if before['kind']=='animation' else {'drawing','source_image','plot','geometry','diagram'}
        if after['kind'] not in allowed: raise ContractError('技术编译改变作者媒体类型')
        for key,value in before.items():
            if key in ('kind','source_refs'): continue
            if after.get(key)!=value: raise ContractError('技术编译改变作者图意或绑定：'+key)
    mapping={r['request_key']:r['request_id'] for r in rows}
    expected=[{**d,'request_ids':[mapping[k] for k in d['request_ids']]} for d in record['requirements']['decisions']]
    if plan['decisions']!=expected: raise ContractError('技术编译改变作者 decisions')
    return record


def repair_author_requirements(record,patch,tickets,content):
    """Apply only current, coordinator-selected fields; identities/criteria stay frozen."""
    from ..audit.contracts import validate_ticket
    validate_author_artifact(record,content,allow_stale=True)
    authority={t['ticket_id']:validate_ticket(t) for t in tickets}
    if (not isinstance(patch,dict) or set(patch)!={'schema_version','base_author_media_hash','ticket_refs','operations'}
            or patch['schema_version']!='AuthorMediaPatchV1' or patch['base_author_media_hash']!=digest_of(record)
            or set(patch['ticket_refs'])!=set(authority) or len(patch['ticket_refs'])!=len(authority)):
        raise ContractError('作者需求返修必须绑定当前工单与需求版本')
    allowed=set(); existing_request_paths=set()
    new_goals=set(); reviewed_goals=set()
    covered_errors=set()
    for ticket in authority.values():
        if (ticket.status!='ready' or ticket.owner!='lesson' or ticket.kind!='text_content'
                or ticket.target['artifact_id']!='author-media:'+content['unit_id']
                or ticket.target['artifact_hash']!=digest_of(record)):
            raise ContractError('作者需求返修工单无效或已过期')
        allowed.update(ticket.allowed_paths)
        error=ticket.as_dict().get('previous_attempt',{}).get('binding_error',{})
        covered_errors.add((error.get('request_key'),error.get('code')))
        if error.get('code')=='AUTHOR_MEDIA_NEW_GOALS': new_goals.update(error.get('goal_ids',[]))
        elif error.get('code') in {'AUTHOR_MEDIA_DECISION_STALE','AUTHOR_MEDIA_INTENT'}:
            reviewed_goals.update(error.get('goal_ids',[]))
            if error['code']=='AUTHOR_MEDIA_INTENT':
                from .intent_review import validate_intent_review
                info=ticket.as_dict().get('previous_attempt',{})
                receipt=info.get('intent_review')
                validate_intent_review(receipt,content,record,info.get('capabilities',{}))
                if info.get('evidence_hash')!=digest_of(receipt): raise ContractError('作者意图返修证据摘要无效')
                failed={v['goal_id'] for v in receipt['verdicts'] if v['status']=='fail'}
                if not set(error['goal_ids'])<=failed: raise ContractError('作者意图返修缺当前失败证据')
        else: existing_request_paths.update(ticket.allowed_paths)
    if {(e['request_key'],e['code']) for e in record.get('errors',[])}-covered_errors:
        raise ContractError('作者需求返修未覆盖仍待处理的授权问题；不得清除未选数学/锚点工单')
    updated=deepcopy(record['requirements']); seen=set()
    if not isinstance(patch['operations'],list) or not patch['operations']: raise ContractError('作者需求返修缺具体操作')
    for op in patch['operations']:
        path=op.get('path')
        if set(op)!={'op','path','value'} or op['op']!='replace' or path not in allowed or path in seen:
            raise ContractError('作者需求返修越过授权字段')
        seen.add(path); parts=path.strip('/').split('/'); target=updated
        for part in parts[:-1]: target=target[int(part)] if isinstance(target,list) else target[part]
        target[int(parts[-1]) if isinstance(target,list) else parts[-1]]=deepcopy(op['value'])
    for ticket in authority.values():
        if not set(ticket.allowed_paths)&seen: raise ContractError('作者需求返修未覆盖每张授权工单')
        error=ticket.as_dict().get('previous_attempt',{}).get('binding_error',{})
        if error.get('code')=='AUTHOR_MEDIA_SEMANTICS_CHANGED':
            semantic_paths={p for p in ticket.allowed_paths if not p.endswith('/placement')}
            if not semantic_paths&seen: raise ContractError('作者需求返修未核对已变化的数学语义；只改锚句不能关闭该工单')
    updated=parse_author_requirements(updated)
    # A repair can rebind a request to corrected content, never change why the
    # request exists, remove it, or weaken its original observable standards.
    old_by_key={r['request_key']:r for r in record['requirements']['requests']}
    new_by_key={r['request_key']:r for r in updated['requests']}
    if not set(old_by_key)<=set(new_by_key): raise ContractError('作者需求返修不得删除原请求')
    for index,old in enumerate(record['requirements']['requests']):
        new=new_by_key[old['request_key']]
        for field in ('request_key','kind','goal_refs','claim_ids','learning_goal','observe','misconception','acceptance_criteria'):
            if new[field]!=old[field]: raise ContractError('作者需求返修改变冻结语义：'+field)
        # NEW_GOALS grants a bulk /requests write only to add requests. Changes
        # to an old request still need that request's separately selected ticket,
        # even when both kinds of repair are applied in one atomic patch.
        changed={field for field in old.keys()|new.keys() if old.get(field)!=new.get(field)}
        if any(f'/requests/{index}/{field}' not in existing_request_paths for field in changed):
            raise ContractError('作者需求返修改变已有请求的未授权字段')
    for key in set(new_by_key)-set(old_by_key):
        if not set(new_by_key[key]['goal_refs']) or not set(new_by_key[key]['goal_refs'])<=new_goals|reviewed_goals:
            raise ContractError('新增请求只能服务本工单授权目标')
    old_decisions={d['target_id']:d for d in record['requirements']['decisions']}
    new_decisions={d['target_id']:d for d in updated['decisions']}
    for key,value in old_decisions.items():
        after=new_decisions.get(key)
        if key not in reviewed_goals:
            if after!=value: raise ContractError('作者需求返修不得改变未授权 decisions')
            continue
        if (not after or after.get('goal_id')!=value['goal_id'] or after.get('target_id')!=value['target_id']
                or not set(value['request_ids'])<=set(after['request_ids'])
                or {'none':0,'recommended':1,'required':2}[after['requirement']]<{'none':0,'recommended':1,'required':2}[value['requirement']]
                or (value['requirement']=='required' and after['handled_by']!=value['handled_by'])):
            raise ContractError('作者意图返修不得删除旧请求或降低既有需求')
    if set(new_decisions)-set(old_decisions)!=new_goals: raise ContractError('作者需求返修新增目标超出授权')
    result=bind_author_requirements(updated,content,author_response_hash=record['author_response_hash'])
    result['repair_provenance']={'previous_hash':digest_of(record),'patch_hash':digest_of(patch),'ticket_refs':patch['ticket_refs']}
    return result


def _correction(message, path, fix, *, passing=None):
    """把"模型的答复不合格"报成**可纠正错误**，而不是判死整节（D16-1）。

    与 `pipeline/content_stages._repair_content` 同一条口径：判据一个字都不放松，
    变的只是处置方式 —— 说清哪条路径不对、怎么改、什么算通过，然后退回一轮。
    """
    from ..content.generation_corrections import LocalCorrectionRequired
    raise LocalCorrectionRequired(
        message,
        errors=[{'path': path, 'code': 'author_media_requirements', 'fix': fix,
                 'passing_criterion': passing or
                 'AuthorMediaRequirementsV1 完整通过校验；请求身份、媒体类型、教学目的、目标/主张引用与'
                 '原验收标准逐项保持，工单授权范围内的目标已按要求修正。'}],
        allowed_paths=[path], kind='author_media')


def _authority(record, tickets, content):
    """校验工单权威，返回 (authority, allowed, existing_paths, new_goals, reviewed_goals)。"""
    from ..audit.contracts import validate_ticket
    authority={t['ticket_id']:validate_ticket(t) for t in tickets}
    allowed=set(); existing_paths=set(); new_goals=set(); reviewed_goals=set()
    covered=set()
    for ticket in authority.values():
        if (ticket.status!='ready' or ticket.owner!='lesson' or ticket.kind!='text_content'
                or ticket.target['artifact_id']!='author-media:'+content['unit_id']
                or ticket.target['artifact_hash']!=digest_of(record)):
            _correction('作者需求返修工单无效或已过期（状态/归属/版本必须对应当前作者需求）', '/',
                        '不要改动需求；这是宿主侧工单问题，请原样返回当前需求')
        allowed.update(ticket.allowed_paths)
        info=ticket.as_dict().get('previous_attempt',{})
        error=info.get('binding_error',{})
        covered.add((error.get('request_key'),error.get('code')))
        if error.get('code')=='AUTHOR_MEDIA_NEW_GOALS':
            new_goals.update(error.get('goal_ids',[]) or [g for g in info.get('goal_ids',[])])
        elif error.get('code') in {'AUTHOR_MEDIA_DECISION_STALE','AUTHOR_MEDIA_INTENT'}:
            reviewed_goals.update(error.get('goal_ids',[]) or [g for g in info.get('goal_ids',[])])
            if error['code']=='AUTHOR_MEDIA_INTENT':
                from .intent_review import validate_intent_review
                receipt=info.get('intent_review')
                try:
                    validate_intent_review(receipt,content,record,info.get('capabilities',{}))
                except ContractError as exc:
                    _correction('作者意图返修证据无效：'+str(exc), '/decisions',
                                '不要改动需求；宿主证据问题不该由作者答复掩盖')
                if info.get('evidence_hash')!=digest_of(receipt):
                    _correction('作者意图返修证据摘要无效', '/decisions', '同上报，勿改需求')
                failed={v['goal_id'] for v in receipt['verdicts'] if v['status']=='fail'}
                if not set(error.get('goal_ids',[]))<=failed:
                    _correction('作者意图返修缺当前失败证据', '/decisions', '同上报，勿改需求')
        else:
            existing_paths.update(ticket.allowed_paths)
    if {(e['request_key'],e['code']) for e in record.get('errors',[])}-covered:
        _correction('作者需求返修未覆盖仍待处理的授权问题；不得清除未选数学/锚点工单', '/',
                    '只按工单授权修正，不要顺带处理未授权问题')
    return authority, allowed, existing_paths, new_goals, reviewed_goals


def rewrite_author_requirements(record, revised, tickets, content):
    """D16-3：作者媒体返修 = **基于上一次 requirements 整份重写**，不再是补丁。

    与补丁路径（`repair_author_requirements`）判据完全一致，只改交付方式：
    模型交回完整 `AuthorMediaRequirementsV1`，宿主逐条核对"什么被改了、改动是否被授权"。
    任何一条不合格都报成**可纠正错误 + 精确路径**，交给 `run_local_generation` 给轮次。
    """
    if reviews_frozen():
        return _unreviewed_artifact(revised, content, (record or {}).get('author_response_hash', ''))
    validate_author_artifact(record,content,allow_stale=True)
    authority, allowed, existing_paths, new_goals, reviewed_goals = _authority(record, tickets, content)
    if not isinstance(revised, dict) or set(revised)!={'schema_version','decisions','requests'} \
            or revised.get('schema_version')!='AuthorMediaRequirementsV1':
        _correction('作者需求返修必须整份返回 AuthorMediaRequirementsV1（顶层只有 schema_version/decisions/requests）',
                    '/', '删掉补丁用的 operations/base_author_media_hash/ticket_refs，整份交回需求本身')
    try:
        updated=parse_author_requirements(revised)
    except ContractError as exc:
        _correction('作者需求结构不合格：'+str(exc), '/', '按 AuthorMediaRequirementsV1 修正字段与类型后整份重交')

    if '/' in allowed:
        # Whole-object binding feedback may concern malformed fields or unknown
        # target IDs. Neither can freeze the invalid old identity into the repair.
        # Existing content and the independent media review remain authoritative.
        try:
            result=bind_author_requirements(updated,content,author_response_hash=record['author_response_hash'])
        except ContractError as exc:
            _correction(str(exc), '/', '按已通过正文中的目标、数学对象和读者位置修正媒体需求')
        result['repair_provenance']={'previous_hash':digest_of(record),'revised_hash':digest_of(updated),
            'ticket_refs':sorted(authority)}
        return result

    old_requests=record['requirements']['requests']
    new_requests=updated['requests']
    old_by_key={r['request_key']:r for r in old_requests}
    new_by_key={r['request_key']:r for r in new_requests}
    if len(new_by_key)!=len(new_requests):
        _correction('作者需求返修不得重复 request_key', '/requests', '每个 request_key 只保留一条请求')
    if not set(old_by_key)<=set(new_by_key):
        _correction('作者需求返修不得删除原请求：'+', '.join(sorted(set(old_by_key)-set(new_by_key))),
                    '/requests', '把被删掉的请求按原样写回，只能修改或追加')
    new_index={r['request_key']:i for i,r in enumerate(new_requests)}

    # 1) 已有请求：冻结语义逐项保持；被改的字段必须落在该请求的授权路径或整表授权上。
    for old in old_requests:
        new=new_by_key[old['request_key']]
        index=new_index[old['request_key']]
        for field in ('request_key','kind','goal_refs','claim_ids','learning_goal','observe',
                      'misconception','acceptance_criteria'):
            if new.get(field)!=old.get(field):
                _correction(f'作者需求返修改变冻结语义：{field}（请求 {old["request_key"]}）',
                            f'/requests/{index}/{field}',
                            '把该字段恢复为该请求原来的值；只有工单明确授权的字段才能改')
        for field in sorted(set(old)|set(new)):
            if old.get(field)==new.get(field):
                continue
            here=f'/requests/{index}/{field}'
            if not _authorized(here, existing_paths):
                _correction(f'作者需求返修改变已有请求的未授权字段：{here}', here,
                            '把该字段恢复为原值；未被工单授权的字段不能改')

    # 2) 新增请求：只能服务本工单授权目标，且新增本身要有授权（本工单或整表的 /requests）。
    for key in sorted(set(new_by_key)-set(old_by_key)):
        row=new_by_key[key]
        if not any(_authorized(f'/requests/{new_index[key]}/{field}', allowed)
                   for field in row):
            _correction(f'新增请求 {key} 没有被工单授权的写入范围', '/requests',
                        f'去掉这条新请求；只有明确授权新增目标的工单才能追加（当前授权：{sorted(allowed)}）')
        if not set(row['goal_refs']) or not set(row['goal_refs'])<=new_goals|reviewed_goals:
            _correction(f'新增请求只能服务本工单授权目标：{key}', f'/requests/{new_index[key]}/goal_refs',
                        f'把 goal_refs 改成授权目标之一；当前授权：{sorted(new_goals|reviewed_goals)}')

    # 3) decisions：未授权目标必须原样；已授权目标不得删请求或降级。
    old_decisions={d['target_id']:d for d in record['requirements']['decisions']}
    new_decisions={d['target_id']:d for d in updated['decisions']}
    if set(new_decisions)-set(old_decisions)!=new_goals:
        _correction('作者需求返修新增目标超出授权：'+', '.join(sorted(set(new_decisions)-set(old_decisions)-new_goals)),
                    '/decisions', f'只允许为本工单授权的新增目标补 decision；当前授权：{sorted(new_goals)}')
    for key,value in old_decisions.items():
        after=new_decisions.get(key)
        index=next(i for i,d in enumerate(updated['decisions']) if d['target_id']==key)
        if key not in reviewed_goals:
            if after!=value:
                _correction(f'作者需求返修不得改变未授权 decisions：{key}', f'/decisions/{index}',
                            '把该目标的决定整条恢复为原值')
            continue
        if (not after or after.get('goal_id')!=value['goal_id'] or after.get('target_id')!=value['target_id']
                or not set(value['request_ids'])<=set(after['request_ids'])
                or {'none':0,'recommended':1,'required':2}[after['requirement']]
                   <{'none':0,'recommended':1,'required':2}[value['requirement']]
                or (value['requirement']=='required' and after['handled_by']!=value['handled_by'])):
            _correction(f'作者意图返修不得删除旧请求或降低既有需求：{key}', f'/decisions/{index}',
                        '保留原有 request_ids 与 required 级别；只能提升或补足理由')

    # 4) 每张工单都要有实际改动；语义变更类工单必须核对语义字段（只改锚句不算）。
    for ticket in authority.values():
        changed=_changed_paths(old_requests,new_requests,record,updated)
        if not any(_authorized(p, ticket.allowed_paths) for p in changed):
            _correction(f'作者需求返修未覆盖这张授权工单：{ticket.problem if hasattr(ticket,"problem") else ticket.ticket_id}',
                        (ticket.allowed_paths or ['/'])[0],
                        '按该工单要求改动它授权的字段；原样交回等于没修')
        error=ticket.as_dict().get('previous_attempt',{}).get('binding_error',{})
        if error.get('code')=='AUTHOR_MEDIA_SEMANTICS_CHANGED':
            semantic={p for p in ticket.allowed_paths if not p.endswith('/placement')}
            if not any(_authorized(p, semantic) for p in changed):
                _correction('作者需求返修未核对已变化的数学语义；只改锚句不能关闭该工单',
                            sorted(semantic)[0] if semantic else '/requests',
                            '按工单定位到的语义字段修正，不能只挪锚句')

    result=bind_author_requirements(updated,content,author_response_hash=record['author_response_hash'])
    result['repair_provenance']={'previous_hash':digest_of(record),'revised_hash':digest_of(updated),
        'ticket_refs':sorted(authority)}
    return result


def _authorized(path, granted):
    """路径是否落在某个授权路径之下（授权 `/requests` 覆盖 `/requests/3/anchor_text`）。"""
    return any(path==g or path.startswith(g.rstrip('/')+'/') for g in granted)


def build_author_media_repair_messages(content, record, tickets, policy, *, previous_rejection=None):
    """D16-3 的作者媒体返修提示：交**完整** requirements，与正文返修同一形状。

    与 `blocks/content/prompt.build_content_messages` 一样，把宿主规则放进可哈希的
    JSON 载荷（`message_versions` 会读 `output_contract`），并且不做任何截断。
    """
    import json as _json
    from ...core.prompt_assets import load_prompt_asset
    task=load_prompt_asset('media/author_media_repair.md').text
    common=load_prompt_asset('content/common.md').text
    payload={
        'role_task': 'author_media_repair',
        'frozen_context': {'content': content, 'author_media': record,
                           'repair_tickets': [t.as_dict() if hasattr(t, 'as_dict') else t for t in tickets]},
        'author_requirements_schema': AuthorMediaRequirementsV1.model_json_schema(),
        'output_contract': {'schema_version': 'AuthorMediaRequirementsV1',
            'instruction': '只交回顶层 {schema_version, decisions, requests} 这一份完整需求；'
                           '不要交补丁、operations 或任何正文内容。'},
        'policy_digest': policy.digest,
    }
    if previous_rejection:
        payload['frozen_context']['previous_rejection']=deepcopy(previous_rejection)
    messages=[{'role': 'system', 'content': task+'\n'+common},
              {'role': 'user', 'content': _json.dumps(payload, ensure_ascii=False)}]
    return messages


def _changed_paths(old_requests,new_requests,old_record,new_requirements):
    """整份重写相对上一次到底改了哪些字段路径（用于"每张工单都要有改动"的核对）。"""
    changed=[]
    old_by_key={r['request_key']:r for r in old_requests}
    new_by_key={r['request_key']:r for r in new_requests}
    for key,old in old_by_key.items():
        new=new_by_key.get(key,{})
        index=[r['request_key'] for r in new_requests].index(key) if key in new_by_key else None
        for field in sorted(set(old)|set(new)):
            if old.get(field)!=new.get(field) and index is not None:
                changed.append(f'/requests/{index}/{field}')
    for key in set(new_by_key)-set(old_by_key):
        index=[r['request_key'] for r in new_requests].index(key)
        changed.extend(f'/requests/{index}/{field}' for field in new_by_key[key])
    old_decisions={d['target_id']:d for d in old_record['requirements']['decisions']}
    for index,row in enumerate(new_requirements['decisions']):
        before=old_decisions.get(row['target_id'])
        if before is None:
            changed.append(f'/decisions/{index}')
            continue
        for field in sorted(set(before)|set(row)):
            if before.get(field)!=row.get(field):
                changed.append(f'/decisions/{index}/{field}')
    return changed
