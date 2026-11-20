"""Graph-v5 production stages: approved content → requests → resources → view.

No stage reads a legacy lesson as fallback. Backend calls are strictly per request.
"""
from __future__ import annotations
from ..blocks.audit.contracts import ticket_verdict_content
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil
from ..contracts.artifact import ArtifactRef
from ..contracts.enums import QualityState
from ..contracts.stage import StageResult
from ..core.digest import digest_file,digest_of
from ..core.jsonio import read_json,read_json_soft,write_json_atomic
from ..core.review_mode import reviews_frozen, frozen_review, substantive_reviews_enabled, current_review_mode
from ..errors import CapabilityError,ContractError
from ..blocks.media.media_requirements import content_hash,validate_requirements
from ..blocks.media.media_planning import plan_media_requirements,MediaPlanningError
from ..blocks.media.media_manifest import validate_manifest,approve_media
from ..blocks.media.execution import execute_static
from ..blocks.media.dependencies import resource_dependencies
from ..blocks.media.evidence_digest import manifest_digest
from .services import reraise_control_error
from .graph import MEDIA_TICKET_STAGES
from ..blocks.content.content_contracts import CLAIM_REPAIR_FIELDS
from ..blocks.content.review_outcome import content_identity, outcome, inherit_outcome, aggregate_outcomes, media_outcome


def _root(ctx):
    return Path(ctx.services.store.work) if ctx.services.store else Path(ctx.workspace.root)/'.cache/release'


def _path(ctx,stage,unit=None): return _root(ctx)/'products'/stage/(str(unit or ctx.node.scope_key)+'.json')


def _load(ctx,stage,unit=None):
    path=_path(ctx,stage,unit)
    if not path.is_file(): raise ContractError('缺本次运行产物：'+str(path))
    payload=read_json(path)['payload']
    if stage=='media_plan' and not reviews_frozen():
        content,_=_load_content(ctx)
        current=_load_author_media(ctx,content)
        if payload.get('author_media_hash')!=digest_of(current):
            raise ContractError('媒体技术计划未绑定当前作者需求')
        from ..blocks.media.intent_review import validate_plan_intent
        validate_plan_intent(payload,content)
    return payload


def _load_content(ctx):
    from .content_stages import load_content, _frozen_context
    from ..blocks.content.content_policy import load_content_policy
    content,_=load_content(ctx)
    state=_content_outcome(ctx,content)
    # D1：这里**不再**拦截"确定有错误"的正文。原来 `state['status'] in {'known_error','missing'}`
    # 就整段拒绝媒体生成，那是与审核层重复的设计 —— 正文能不能用，text_audit 已经判过了；
    # 而"这一节还有未解决的问题"会由上游 outcome 继续传播
    # （`inherit_outcome(media_outcome(row), upstream)`），并在阅读页的 `problem_summary` 里列出。
    # 整段拒绝的实际后果是：正文都已经生成、审核意见都很具体了，读者却拿不到任何图 ——
    # 与 D1（全部保留 + 标明问题）相反。
    # 仍然保留后面两条真正必要的检查（来源一致、声称 verified 时必须能取到不可变已批准证据）。
    context=_frozen_context(ctx)
    if not reviews_frozen() and content.get('source_context_hash')!=context['source_context_hash']:
        raise ContractError('内容来源已变化')
    if not reviews_frozen() and state['status']=='verified':
        # Still require real immutable evidence when claiming verified content.
        load_content(ctx,approved=True)
    return content,content_identity(content,context['reference_catalog'],
        policy_digest=load_content_policy(context['learner_profile']).digest)


def _content_outcome(ctx,content):
    audit=(read_json_soft(_path(ctx,'text_audit')) or {}).get('payload') or {}
    if audit.get('content_hash')==content_hash(content) and audit.get('review_outcome'):
        return deepcopy(audit['review_outcome'])
    return outcome('unverified',['尚无当前正文审核结果'])


def _plan_outcome(ctx,content,approval,plan):
    if substantive_reviews_enabled(): return _content_outcome(ctx,content)
    if reviews_frozen(): return frozen_review()
    from ..blocks.media.intent_review import validate_plan_intent
    receipt=validate_plan_intent(plan,content)
    upstream=_content_outcome(ctx,content) if approval.get('schema_version')=='ContentIdentityV1' else outcome('verified')
    return inherit_outcome(receipt['review_outcome'],upstream)


def _load_author_media(ctx,content,*,allow_stale=False):
    from .content_stages import load_author_media
    return load_author_media(ctx,content,allow_stale=allow_stale)


def _client(ctx,role,bucket):
    from .stage_io import _gateway_client
    return _gateway_client(ctx,role=role,bucket=bucket)[0]


def _source_assets(ctx):
    """Expose actual indexed source files and the bounded curated library, never infer jobs."""
    parse=Path(getattr(ctx.context,'parse','') or '.')
    source=[]; seen=set()
    for index_name,field in [('images.json','images'),('figures.json','figures')]:
        payload=read_json_soft(parse/index_name) or {}
        for raw in payload.get(field) or []:
            identity=raw.get('id')
            if not identity or identity in seen: continue
            resource=raw.get('file') or raw.get('page_image')
            if not resource: continue
            file=(parse/str(resource)).resolve()
            if not file.is_relative_to(parse.resolve()) or not file.is_file() or file.is_symlink(): continue
            row={'asset_id':identity,'origin':'textbook','index':index_name,'metadata':deepcopy(raw),'file_hash':digest_file(file)}
            row['hash']=digest_of(row); source.append(row); seen.add(identity)
    # Legacy canned diagrams have no approved-object binding. Do not advertise
    # them as production-capable assets; historical outputs remain readable.
    return {'source_images':source,'library':[]}



def _source_context(ctx):
    parse=Path(getattr(ctx.context,'parse','') or '.')
    catalog=read_json_soft(parse/'chapters.json') or {}
    unit=next((r for r in catalog.get('lesson_units') or [] if str(r.get('unit_id'))==str(ctx.node.scope_key)),{})
    relative=unit.get('original_text')
    path=(parse/str(relative or '')).resolve()
    if not relative or not path.is_relative_to(parse.resolve()) or not path.is_file(): return {'source_text':'','source_hash':None}
    return {'source_text':path.read_text(encoding='utf-8'),'source_hash':digest_file(path)}


def _capabilities(ctx):
    from ..checks.offline import _browser_executable
    caps=getattr(ctx.services,'capabilities',None)
    raw=caps.identity_dict() if caps else {}
    kinds=['source_image']
    manim_available=bool(caps and 'manim' in caps.render_backends())
    if manim_available and _browser_executable(Path(ctx.workspace.root)): kinds+=['drawing']
    if manim_available: kinds.append('animation')
    from ..blocks.interaction.spec import interaction_capabilities
    from .interaction_runtime import interaction_runtime_inputs
    content,_ = _load_content(ctx)
    interaction=interaction_capabilities(content, **interaction_runtime_inputs(ctx))
    return {'kinds':kinds,'backends':raw,'interaction':interaction['available'],
        'interaction_targets':interaction['targets'],'interaction_reader_protocol':interaction['reader_protocol'],
        'code_generation':'GeneratedMediaCodeV1','static_renderer':'api-manim-still-python-v1',
        'animation_renderer':'api-manim-python-v1','source_assets':_source_assets(ctx),'source_context':_source_context(ctx)}


def _emit(ctx,stage,payload,kind,inputs,unit=None):
    from .stage_io import _emit as emit
    return emit(ctx,name=stage,kind=kind,file_type=kind,payload=payload,inputs=inputs,path=_path(ctx,stage,unit))


def _boundary(ctx,exc,outputs=()):
    reraise_control_error(exc)
    ref=_emit(ctx,ctx.node.stage,{'unit_id':str(ctx.node.scope_key),
        'review_outcome':outcome('missing',[f'{type(exc).__name__}: {exc}']),
        'missing':[{'object_id':str(ctx.node.scope_key),'reason':str(exc)}]},'stage.partial',{'error':str(exc)})
    outputs=(*outputs,ref)
    return StageResult.blocked(ctx.node.stage,f'{type(exc).__name__}: {exc}',outputs=outputs)


def _checkpoint(ctx):
    ctx.services.cancel.checkpoint(); ctx.services.control.check()


def _persisted_tickets(ctx,stages=('figures','audit')):
    """Read current-run authorities, rejecting conflicting frozen criteria."""
    known={}
    paths=[_path(ctx,'text_audit' if stage=='content' else stage) for stage in stages]
    for path in paths:
        payload=(read_json_soft(path) or {}).get('payload') or {}
        # Fulfilled rows retain resolved standards even when the active ticket
        # list is empty. Later regeneration must recheck those same standards.
        historical=[ticket for row in payload.get('requests') or []
                    for ticket in row.get('repair_tickets') or []]
        for ticket in historical+list(payload.get('tickets') or []):
            identity=ticket['ticket_id']
            if identity in known and known[identity]['criteria']!=ticket['criteria']:
                raise ContractError('cross-stage ticket criteria conflict: '+identity)
            known[identity]=ticket
    return known


def _media_evidence_basis(row):
    return digest_of({'spec_hash':row.get('spec_hash'),'render_profile':row.get('render_profile'),
        'resources':sorted((r.get('kind',''),r.get('relative_path',''),r.get('sha256',''))
                           for r in row.get('resources',[]))})


def _legacy_protocol_ticket(ticket):
    from ..blocks.audit.contracts import make_ticket_id,target_location_key
    code=(ticket.get('previous_attempt') or {}).get('issue_key','')
    target=ticket.get('target',{})
    if not (ticket.get('kind')=='evidence_format' and code.startswith('MEDIA_REVIEW_')
            and target.get('step_id')==code): return False
    return ticket['ticket_id']==make_ticket_id(rule=target['rule'],unit_id=ticket['unit_id'],
        artifact_id=target['artifact_id'],location=target_location_key(target)[0])


def _approved_draft_evidence_basis(ctx,row):
    """Recover a legacy ticket's earlier profile only from its exact host receipt."""
    if row.get('kind')!='animation' or row.get('render_profile')!='final': return None
    if not getattr(getattr(ctx,'services',None),'store',None): return None
    from ..blocks.media.code_execution import _render_row
    from ..blocks.media.validation import validate_media_report
    try:
        request=row['request'];program=row['candidate_spec'];identity=row['code_generation_identity']
        if identity!=digest_of({'request':request,'context':row['math_context_digest'],
                'generation_dependency_hash':row['generation_dependency_hash'],
                'contract':'GeneratedMediaCodeV1','attempt_limit':3}): return None
        count=len(row['code_attempts'])
        if not 1<=count<=3: return None
        root=_root(ctx).resolve()
        attempt=root/'media'/str(ctx.node.scope_key)/row['request_id']/('generation-'+identity[7:23])/f'attempt-{count:02d}'
        if not attempt.resolve().is_relative_to(root) or any(p.is_symlink() for p in (attempt,*attempt.parents) if p!=root and p.is_relative_to(root)):
            return None
        from ..core.resource_files import relocated_source_dir
        if relocated_source_dir(row['source_dir'],row.get('resources') or []).resolve()!=(attempt/'final').resolve() or not _intact_media_resources(row): return None
        paths=[attempt/name for name in ('program.json','draft-runtime.json','draft-revalidation.json')]
        if any(p.is_symlink() or not p.is_file() for p in paths): return None
        saved_program,runtime,report=[read_json(p) for p in paths]
        if saved_program!=program or digest_of(program)!=row['spec_hash']: return None
        if relocated_source_dir(runtime['source_dir'],runtime.get('resources') or []).resolve()!=(attempt/'draft').resolve(): return None
        draft=_render_row(row,program,runtime,'draft')
        if not _intact_media_resources(draft) or _media_evidence_basis(draft)==_media_evidence_basis(row): return None
        # Earlier buggy migration only appended this field to unscoped legacy
        # tickets. Try that exact reversal; never discard other frozen fields.
        originals=[draft]
        restored=deepcopy(draft)
        for ticket in restored.get('repair_tickets',[]):
            if _legacy_protocol_ticket(ticket): ticket['previous_attempt'].pop('media_evidence_basis',None)
        if restored!=draft: originals.append(restored)
        for original in originals:
            try:
                if validate_media_report(request,original,report): return _media_evidence_basis(original)
            except (ContractError,ValueError,TypeError,KeyError): pass
    except (OSError,ValueError,TypeError,KeyError): pass
    return None


def _tickets(ctx,content,rows,target='media_resource',*,evidence_origins=None):
    """Stable original criteria and narrow repair routes for every backend."""
    from .diagnostics import technical_ticket
    from ..blocks.audit.contracts import RepairDirective,validate_ticket,make_ticket_id,target_location_key
    from .repair import RepairRouteV1
    from ..blocks.audit.evidence import resolve_json_pointer
    from ..blocks.content.review_document import build_review_document
    from .content_stages import _frozen_context
    context=_frozen_context(ctx)
    blocks={b['json_pointer']:b for b in build_review_document(content,context['reference_catalog']).blocks}
    known=_persisted_tickets(ctx,('figures','audit','content'))
    tickets=[]; directives=[]; routes=[]; seen=set(); dispatched=set()
    def emit(data,artifact_kind,*,dispatch=True):
        ticket=validate_ticket(data)
        if ticket.ticket_id in seen:
            if not dispatch or ticket.ticket_id in dispatched: return
            ticket=validate_ticket(next(row for row in tickets if row['ticket_id']==ticket.ticket_id))
        else:
            seen.add(ticket.ticket_id); tickets.append(ticket.as_dict())
        if not dispatch or ticket.ticket_id in dispatched: return
        dispatched.add(ticket.ticket_id)
        from ..blocks.audit.contracts import normalize_verdict_content
        issue_key=(ticket.previous_attempt or {}).get('issue_key','')
        base_id=issue_key.removeprefix('criterion:')
        prefix='ticket:'+ticket.ticket_id+':'
        current_judgments=[{**verdict,'criterion_id':verdict['criterion_id'].removeprefix(prefix)}
            for verdict in report.get('verdicts',[])
            if verdict.get('criterion_id')==base_id or str(verdict.get('criterion_id','')).startswith(prefix)]
        current_criteria=[{**item,'criterion_id':item['criterion_id'].removeprefix(prefix)}
            for item in report.get('review_items',[])
            if item.get('criterion_id')==base_id or str(item.get('criterion_id','')).startswith(prefix)]
        current_issues=[issue for issue in report.get('issues',[]) if issue.get('code')==issue_key]
        current_discoveries=[discovery for discovery in (report.get('review_problems') or (row.get('review') or {}).get('problems') or [])
            if isinstance(discovery,dict) and discovery.get('issue_key') and issue_key.endswith('|'+discovery['issue_key'])]
        verdict_content={**ticket_verdict_content(ticket),**normalize_verdict_content({
            'current_judgments':current_judgments,'current_criteria':current_criteria,
            'current_issues':current_issues,'current_discoveries':current_discoveries})}
        basis=(ticket.previous_attempt or {}).get('media_evidence_basis')
        step=verdict_content['target'].get('step_id','')
        if basis and step.endswith('|'+basis):
            verdict_content['target']['step_id']=step.removesuffix('|'+basis)
        directives.append(RepairDirective(unit_id=str(ctx.node.scope_key),ticket_ids=(ticket.ticket_id,),
            owner=ticket.owner,kind=ticket.kind,reason=ticket.problem,
            artifact_hashes={ticket.target['artifact_id']:ticket.target['artifact_hash']},verdict_content=verdict_content))
        routes.append(RepairRouteV1(ticket.ticket_id,ctx.node.stage,
            'media_evidence' if artifact_kind=='evidence' else artifact_kind,ticket.target['artifact_id']).as_dict())
    def executable_route(data,row,artifact_kind):
        if data['owner']=='visual_renderer' and data['allowed_paths']==['/resources']:
            return 'media_resource'
        if data['kind'] not in ('visual_layout','visual_math'): return artifact_kind
        if row.get('code_product') or (row.get('candidate_spec') or {}).get('schema_version')=='GeneratedMediaCodeV1' or row['kind']=='drawing':
            data['allowed_paths']=['/spec/code','/spec/steps','/spec/object_bindings']
            data['repair_instructions']['action']='按具体运行错误或真实画面发现修复完整 Python 代码及分镜；保留原请求、作者步骤、批准数学依据、落点和原工单通过标准。不得重做正文或降低标准。'
            return 'media_resource'
        previous=data.setdefault('previous_attempt',{})
        if row['kind']=='source_image':
            # A copied/cropped source has no mutable layout spec. Replan this
            # request through the coordinator, retaining its original standards.
            data['allowed_paths']=['/requests']; previous['source_image_replan']=True
            data['repair_instructions']['action']='重规划当前源图片请求：可改用另一登记来源或可执行后端；保留原请求身份、目标、required义务、来源条件及冻结标准，再核验数学与实际画面。'
            return 'media_requirements'
        if previous.pop('source_image_replan',False):
            data['allowed_paths']=['/spec/layout/font_size','/spec/layout/label_offsets'] if data['kind']=='visual_layout' and row['kind']!='animation' else ['/spec']
            data['repair_instructions']['action']='修复当前后端的授权规格或布局字段；保留请求、批准数学对象与原票标准，重新核验真实画面。'
        return artifact_kind
    for row in rows:
        if row.get('state') in ('fulfilled','blocked'): continue
        rid=row['request_id']; report=row.get('validation') or {}
        items={r['criterion_id']:r for r in report.get('review_items',[])}
        verdicts={r['criterion_id']:r for r in report.get('verdicts',[])}
        call_failed = bool(report.get('review_failure')) and report['review_failure'].get('kind') != 'protocol'
        uncertain_ids = {cid for cid,v in verdicts.items() if v.get('status') == 'needs_review'}
        protocol_invalid=any(i['code'] in ('MEDIA_REVIEW_FORMAT','MEDIA_REVIEW_EVIDENCE',
            'MEDIA_REVIEW_COVERAGE','MEDIA_REVIEW_NOT_RUN','MEDIA_REVIEW_MISSING') for i in report.get('issues',[]))
        discoveries=report.get('review_problems') or (row.get('review') or {}).get('problems') or []
        code_product=bool(row.get('code_product') or
            (row.get('candidate_spec') or {}).get('schema_version')=='GeneratedMediaCodeV1' or row['kind']=='drawing')
        # Protocol repair belongs to these frozen pixels, not every later code
        # candidate for this request. Review wording/telemetry cannot renew it.
        evidence_basis=_media_evidence_basis(row) if code_product else None
        legacy_basis=(_approved_draft_evidence_basis(ctx,row) if code_product and
            any(_legacy_protocol_ticket(t) for t in row.get('repair_tickets',[])) else None)
        retained_protocol_codes=set()
        content_discovered=any(isinstance(p,dict) and p.get('target_artifact_kind')=='content' for p in discoveries)
        actionable_ids=set()
        if code_product and row.get('request'):
            from ..blocks.media.validation import actionable_media_failures,media_review_items,result_digest
            from ..blocks.media.review_policy import media_review_policy
            frozen_items=media_review_items(row['request'],row,media_review_policy(row['request']['kind']))
            items={item['criterion_id']:item for item in frozen_items}
            if report.get('result_hash')==result_digest(row) and not content_discovered:
                actionable_ids={v['criterion_id'] for v in actionable_media_failures(report,items=frozen_items)}
        def dispatch_ticket(data,default,criterion_id=None):
            if call_failed: return False
            if not code_product: return default
            if content_discovered and data['owner'] in ('visual_planner','visual_renderer'): return False
            if actionable_ids:
                # Preserve the protocol ticket, but fix the known visual defect
                # before purchasing another review of unchanged bad pixels.
                if data['kind']=='evidence_format': return False
                if protocol_invalid and data['kind'] in ('visual_math','visual_layout'):
                    return (criterion_id in actionable_ids if criterion_id else
                            any(cid.startswith('ticket:'+data['ticket_id']+':') for cid in actionable_ids))
            return default
        failures=[]
        for problem in discoveries:
            if isinstance(problem,dict) and problem.get('target_artifact_kind')=='content':
                failures.append(('POST_MEDIA_CONTENT',problem.get('problem',''),problem))
        for cid,v in verdicts.items():
            if v.get('status')=='fail' and not cid.startswith('ticket:'):
                failures.append(('criterion:'+cid,v.get('why') or '原冻结画面标准未通过',None))
        for issue in report.get('issues',[]):
            if issue['code']=='MEDIA_CALL_FAILED' or (issue['code']=='MEDIA_CRITERION_UNRESOLVED'
                    and issue.get('criterion_id') in uncertain_ids): continue
            if issue['code'] in ('MEDIA_CRITERION_UNRESOLVED','MEDIA_REVIEW_PROBLEMS') and failures: continue
            failures.append((issue['code'],issue['message'],None))
        # Persist original unresolved standards; passing a different/new criterion
        # can never erase an earlier accepted repair obligation.
        for original in row.get('repair_tickets',[]):
            required=[c for c in original['criteria'] if c.get('required',True)]
            previous_attempt=original.get('previous_attempt') or {}
            protocol_code=previous_attempt.get('issue_key','')
            scoped_protocol=code_product and original['kind']=='evidence_format' and protocol_code.startswith('MEDIA_REVIEW_')
            if scoped_protocol:
                old_basis=previous_attempt.get('media_evidence_basis')
                if _legacy_protocol_ticket(original):
                    old_basis=(evidence_origins or {}).get(original['ticket_id']) or legacy_basis or old_basis
                same_pixels=(old_basis==evidence_basis if old_basis else
                             original['target']['artifact_hash']==row.get('spec_hash'))
                if not same_pixels: continue
            if original['kind']=='evidence_format':
                unresolved=call_failed or bool(uncertain_ids) or any(i['code'].startswith('MEDIA_REVIEW_') for i in report.get('issues',[]))
            else:
                unresolved=protocol_invalid or not required or not all(
                    verdicts.get('ticket:'+original['ticket_id']+':'+c['criterion_id'],{}).get('status')=='pass'
                    and verdicts.get('ticket:'+original['ticket_id']+':'+c['criterion_id'],{}).get('version')==str(c['version'])
                    for c in required)
            if unresolved:
                current=deepcopy(original)
                if scoped_protocol:
                    current['previous_attempt']['media_evidence_basis']=evidence_basis
                    retained_protocol_codes.add(protocol_code)
                current['target']['artifact_hash']=row.get('spec_hash') or digest_of(row.get('visual_spec') or row.get('invalid_spec') or row.get('request',{}))
                artifact_kind='evidence' if current['kind']=='evidence_format' else ('visual_spec' if row['kind']=='animation' else target)
                # An invalid review cannot erase a content obligation or spend
                # another content batch. Keep it persisted while only the stable
                # evidence-format ticket goes back to the coordinator.
                emit(current,executable_route(current,row,artifact_kind),
                    dispatch=dispatch_ticket(current,
                        (protocol_invalid and current['kind']=='evidence_format') or
                        (not protocol_invalid and any(verdicts.get('ticket:'+original['ticket_id']+':'+c['criterion_id'],{}).get('status')=='fail'
                            for c in required))))
        if not failures and not row.get('repair_tickets') and not call_failed and not uncertain_ids:
            failures=[('MEDIA_SPEC_INVALID',row.get('reason') or '资源没有完成',None)]
        for code,problem,discovery in failures:
            if code in retained_protocol_codes: continue
            owner='visual_planner';kind='visual_math';artifact_kind='visual_spec' if row['kind']=='animation' else target
            artifact_id='media:'+rid;artifact_hash=row.get('spec_hash') or digest_of(row.get('visual_spec') or row.get('invalid_spec') or row.get('request',{}))
            paths=('/spec',);pointer='/spec';source=context['source_text'];rule='MEDIA_RESOURCE_INVALID:'+code
            action='根据具体错误修复当前请求的类型化规格；只能引用已批准的数学对象，不得修改数学真值、请求身份或验收标准。'
            expected='当前请求的真实资源、数学绑定和所有原冻结画面标准逐项通过。'
            item=items.get(code.removeprefix('criterion:'))
            if item: expected=item['statement']
            if code in ('MEDIA_LAYOUT','MEDIA_LABEL_READABILITY','criterion:host.media.readability'):
                kind='visual_layout';paths=('/spec/layout/font_size','/spec/layout/label_offsets') if row['kind']!='animation' else ('/spec',)
                action='调整授权布局字段，修复原工单指出的字号、遮挡或裁切；重新在1280与390宽度实际截图验收。'
            if code.startswith('MEDIA_REVIEW_'):
                owner='reviewer';kind='evidence_format';artifact_kind='evidence';paths=('/review',);pointer='/review'
                action='仅补齐当前冻结标准的实际截图证据和逐项判词，不重写或重画资源。'
                expected='原冻结标准身份和版本保持不变，每项包含全部要求的真实画面证据与明确判词。'
            if code=='RESOURCE_INVALID':
                owner='visual_renderer';artifact_kind='media_resource';paths=('/resources',);pointer='/resources'
                action='按当前已冻结请求、源图来源或规格重新生成损坏/缺失的资源；不得改来源身份、规格、数学绑定或原验收标准，再核验字节摘要与实际画面。'
                expected='全部登记资源存在且字节摘要有效，来源/规格和请求保持不变，原冻结标准及实际画面重新通过审核。'
            issue_key=code
            if discovery:
                pointer=discovery.get('pointer') or ''
                found,text=resolve_json_pointer(content,pointer)
                valid=(found and isinstance(text,str) and pointer in blocks
                    and not pointer.startswith('/reference_catalog/') and discovery.get('issue_key')
                    and discovery.get('evidence') and discovery['evidence'] in text
                    and discovery.get('action') and discovery.get('expected'))
                if valid:
                    owner='lesson';kind='text_content';artifact_kind='content';artifact_id='lesson:'+str(ctx.node.scope_key)
                    artifact_hash=content_hash(content);paths=(pointer,);source=text;rule='POST_MEDIA_CONTENT'
                    issue_key=blocks[pointer]['block_id']+'|'+discovery['issue_key']
                    action=discovery['action'];expected=discovery['expected']
                    # An actual mathematical contradiction may need the referenced
                    # approved model changed, followed by a fresh content approval.
                    bound=set((row.get('request') or {}).get('claim_ids',[]))
                    model_ids={r['model_id'] for r in (row.get('request') or {}).get('math_model_refs',[])}
                    paths+=tuple('/math_models/'+str(i) for i,m in enumerate(content['math_models'])
                                 if m['model_id'] in model_ids)
                    paths+=tuple('/teaching_plan/claims/'+str(i)+'/'+field
                                 for i,claim in enumerate(content['teaching_plan']['claims']) if claim['id'] in bound
                                 for field in CLAIM_REPAIR_FIELDS)
                else:
                    owner='reviewer';kind='evidence_format';artifact_kind='evidence';paths=('/review',);pointer='/review'
                    rule='MEDIA_RESOURCE_INVALID:CONTENT_DISCOVERY_PROTOCOL';issue_key='CONTENT_DISCOVERY_PROTOCOL'
                    problem='正文发现缺当前具体字段、原文、稳定问题身份、修法或通过标准'
                    action='补全正文发现协议并引用批准字段中的真实原文；不得直接改正文。'
            data=technical_ticket(unit_id=str(ctx.node.scope_key),artifact_id=artifact_id,artifact_hash=artifact_hash,
                problem=problem,source=source,owner=owner,kind=kind,allowed_paths=paths,rule=rule,
                action=action,expected=expected,evidence=artifact_kind not in ('content','evidence')).as_dict()
            if code=='RESOURCE_INVALID':
                data['preserve']=['/request','/spec','/acceptance_criteria','/source_provenance']
            data['target'].update(rule=rule,step_id=issue_key,anchor_kind='step')
            data['target'].pop('json_pointer',None);data['target'].pop('location',None)
            data['previous_attempt']={'json_pointer':pointer,'issue_key':issue_key,'request_id':rid}
            if code_product and code.startswith('MEDIA_REVIEW_'):
                data['target']['step_id']=issue_key+'|'+evidence_basis
                data['previous_attempt']['media_evidence_basis']=evidence_basis
            data['ticket_id']=make_ticket_id(rule=rule,unit_id=str(ctx.node.scope_key),artifact_id=artifact_id,
                location=target_location_key(data['target'])[0])
            original=known.get(data['ticket_id'])
            if original:
                data={**deepcopy(original),'target':data['target'],'allowed_paths':data['allowed_paths'],
                    'status':data['status'],'source_evidence':data['source_evidence']}
            emit(data,executable_route(data,row,artifact_kind),
                dispatch=dispatch_ticket(data,
                    not protocol_invalid or data['owner']=='visual_renderer' or data['kind'] not in ('visual_math','visual_layout'),
                    code.removeprefix('criterion:') if code.startswith('criterion:') else None))
    return tickets,tuple(directives),tuple(routes)


def _media_plan_repair_context(ctx,prior):
    """Only the coordinator's persisted current tickets may authorize replanning."""
    from ..blocks.audit.contracts import validate_ticket
    batch=getattr(ctx,'repair_batch',None)
    if batch is None: return None
    known=_persisted_tickets(ctx,MEDIA_TICKET_STAGES)
    rows={r['request_id']:r for r in ((read_json_soft(_path(ctx,'figures')) or {}).get('payload') or {}).get('requests',[])}
    selected={}; replanned=set(prior.get('replan_request_ids') or [])
    previous=prior.get('repair_basis') or prior.get('candidate') or prior
    for job in batch.jobs:
        for tid in job.ticket_ids:
            if tid not in known: raise ContractError('媒体重规划引用未持久化的工单：'+tid)
            data=known[tid]; ticket=validate_ticket(data); target=ticket.target
            if (ticket.status!='ready' or ticket.unit_id!=str(ctx.node.scope_key)
                    or ticket.owner!='visual_planner' or ticket.kind not in ('visual_math','visual_layout')
                    or job.artifact_id!=target['artifact_id'] or job.owner!=ticket.owner or job.kind!=ticket.kind):
                raise ContractError('媒体重规划工单权限、状态或作业绑定无效')
            if target.get('rule')=='MEDIA_REQUIREMENTS_INVALID' and target['artifact_id']=='requirements:'+str(ctx.node.scope_key):
                current_hash=digest_of(prior.get('candidate') or prior)
            elif (data.get('previous_attempt') or {}).get('source_image_replan'):
                rid=data['previous_attempt'].get('request_id'); row=rows.get(rid)
                request=next((r for r in previous.get('requests',[]) if r['request_id']==rid),None)
                if (not row or row.get('kind')!='source_image' or request is None
                        or target['artifact_id']!='media:'+rid or data['allowed_paths']!=['/requests']):
                    raise ContractError('源图片重规划缺当前请求或资源授权')
                current_hash=row.get('spec_hash'); replanned.add(rid)
            else: raise ContractError('该工单未授权媒体需求重规划')
            if not current_hash or target['artifact_hash']!=current_hash:
                raise ContractError('媒体重规划工单版本已过期')
            selected[tid]=data
    if not selected: raise ContractError('媒体重规划缺协调器选中工单')
    return {'tickets':list(selected.values()),'previous_requirements':previous,
            'replan_request_ids':sorted(replanned)}


def _freeze_replanned_obligations(plan,repair_context):
    """Replanning changes execution, never the failed request's teaching duty."""
    if not repair_context or not repair_context['replan_request_ids']: return
    previous=repair_context['previous_requirements']; selected=set(repair_context['replan_request_ids'])
    old={r['request_id']:r for r in previous['requests']}
    current={r['request_id']:r for r in plan['requests']}
    if set(old)!=set(current): raise ContractError('源图片重规划必须保留原请求身份')
    for rid,before in old.items():
        after=current[rid]
        if rid not in selected:
            if before!=after: raise ContractError('源图片重规划不得改未授权请求')
            continue
        for key in ('request_key','serves','goal_refs','learning_goal','observe','misconception','constraints','acceptance_criteria'):
            if before.get(key)!=after.get(key): raise ContractError('源图片重规划不得改变原目标、来源条件或冻结标准：'+key)
        for key in ('claim_ids','math_model_refs','object_refs'):
            if any(value not in after[key] for value in before[key]): raise ContractError('源图片重规划不得丢失批准数学绑定：'+key)
        if any(ref not in after['source_refs'] for ref in before['source_refs'] if ref.get('kind')!='asset'):
            raise ContractError('源图片重规划不得丢失原来源条件')
    decisions={d['target_id']:d for d in plan['decisions']}
    rank={'none':0,'recommended':1,'required':2}
    for before in previous['decisions']:
        after=decisions.get(before['target_id'])
        if (after is None or rank[after['requirement']]<rank[before['requirement']]
                or set(after['request_ids'])!=set(before['request_ids']) or after['handled_by']!=before['handled_by']):
            raise ContractError('源图片重规划不得降低 required 或丢失原目标请求映射')


def _author_media_binding_tickets(ctx,content,approval,record,*,errors=None,intent_review=None,capabilities=None):
    """Route changed author anchors/semantic bindings to the original author."""
    from .diagnostics import technical_ticket
    from .repair import RepairRouteV1
    from ..blocks.audit.contracts import RepairDirective, make_ticket_id, target_location_key
    old=_persisted_tickets(ctx,('media_plan',)); tickets=[]
    reader_goals={}
    if intent_review is not None:
        from ..blocks.media.intent_review import intent_context,validate_intent_review
        validate_intent_review(intent_review,content,record,capabilities)
        reader_goals={g['goal_id']:g for g in intent_context(content,record,capabilities)['goals']}
    requirements=record.get('requirements')
    requests=requirements.get('requests',[]) if isinstance(requirements,dict) else []
    requests=[r for r in requests if isinstance(r,dict)] if isinstance(requests,list) else []
    errors=record['errors'] if errors is None else errors
    for error in errors:
        key=error['request_key']
        index=next((i for i,r in enumerate(requests) if r.get('request_key')==key),None)
        new_goals=error['code']=='AUTHOR_MEDIA_NEW_GOALS'
        decision_error=error['code'] in {'AUTHOR_MEDIA_DECISION_STALE','AUTHOR_MEDIA_INTENT'}
        reader_error=error['code']=='AUTHOR_READER_CONTENT'
        whole_requirements=index is None and not (new_goals or decision_error or reader_error)
        if reader_error:
            gid=error['goal_ids'][0];goal=reader_goals[gid]
            verdict=next(v for v in intent_review['verdicts'] if v['goal_id']==gid)
            if verdict['status']!='fail' or verdict.get('failure_kind')!='reader_content':
                raise ContractError('正文返修缺当前明确的读者内容失败判词')
            pointer=verdict['reader_pointer']
            field=next(r for r in goal['explicit_reader_evidence']+goal['supplementary_reader_context'] if r['pointer']==pointer)
            paths=[pointer] if pointer in goal['repairable_reader_pointers'] else []
            key='goal:'+gid
        elif whole_requirements:
            paths=['/']; key='requirements'
        elif new_goals:
            paths=['/decisions','/requests']; key='new-goals:'+','.join(error['goal_ids'])
        elif decision_error:
            gid=error['goal_ids'][0]
            decision_index=next(i for i,d in enumerate(record['requirements']['decisions']) if d['goal_id']==gid)
            paths=[f'/decisions/{decision_index}','/requests']; key='goal:'+gid
        else:
            fields=['placement']
            if error['code']=='AUTHOR_MEDIA_SEMANTICS_CHANGED':
                fields+=['math_model_ids','object_refs','mathematical_objects','conditions']
                if requests[index]['kind']=='animation': fields.append('animation')
            paths=[f'/requests/{index}/{field}' for field in fields]
        rule=('POST_MEDIA_CONTENT' if reader_error else
              'AUTHOR_MEDIA_INTENT' if error['code']=='AUTHOR_MEDIA_INTENT' else 'AUTHOR_MEDIA_BINDING')
        artifact_id=('lesson:' if reader_error else 'author-media:')+content['unit_id']
        data=technical_ticket(unit_id=str(ctx.node.scope_key),artifact_id=artifact_id,
            artifact_hash=content_hash(content) if reader_error else digest_of(record),problem=error['message'],
            source=(field['text'] if paths else '') if reader_error else content['body_markdown'],
            owner='lesson',kind='text_content',allowed_paths=paths,rule=rule,
            action=('建议从 '+pointer+' 作者文字字段补足判词指出的论证：'+error['message']+
                ' 可同时修复关联教学字段；原题、原书解答、来源真切片与既有身份不变，修后执行完整正文检查与独立复核。' if reader_error and paths else
                '该发现仅定位到不可改写的来源字段，当前无合法作者文字返修范围；保留未决，不改原题，不改媒体理由掩盖。' if reader_error else
                '以保留的原始媒体需求和已通过正文为依据，补齐或修正完整作者需求；保留已有教学目的和验收标准，不修改正文。' if whole_requirements else
                '只修本工单目标的媒体决定与理由；可由 none 提升为 required 并追加真实需求。既有请求、身份及全部验收标准保持，已有 required 不降级；其他目标不动。' if decision_error else
                '只为工单新增目标补作者 decisions 和必要 requests；已有决定、请求及标准必须逐项保持。' if new_goals else
                '只修本请求授权的精确锚句/落点或批准数学对象条件；保持 request_key、类型、目标、教学目的和原验收标准。'),
            expected=('当前实际读者内容足以完成：'+goal['goal']['observable_task']+'；明确解决：'+error['message']+
                      '；来源、身份及原教学标准保持，当前正文检查和独立正文复核通过。' if reader_error else
                      '原作者请求在当前批准正文唯一定位，数学对象/条件一致；全部原请求及验收标准保留。')).as_dict()
        if reader_error:
            from ..blocks.audit.evidence import locate_quote
            # The validated pointer already identifies the frozen evidence.
            # A reviewer's summary is not a replacement for that original.
            proof=locate_quote(field['text'],field['text'],artifact_id=artifact_id,
                artifact_hash=content_hash(content),json_pointer=pointer)
            data['source_evidence']=[s.as_dict() for s in proof.spans]
            data['status']='ready' if paths and proof.status=='valid' else 'needs_evidence'
            # preserve 护栏已删除：工单不再携带冻结路径清单。
        data['target'].update(rule=rule,step_id=key+'|'+error['code'],anchor_kind='step')
        data['target'].pop('json_pointer',None);data['target'].pop('location',None)
        data['ticket_id']=make_ticket_id(rule=rule,unit_id=content['unit_id'],
            artifact_id=data['target']['artifact_id'],location=target_location_key(data['target'])[0])
        data['previous_attempt']={'request_key':key,'binding_error':error}
        if intent_review is not None:
            data['previous_attempt'].update(intent_review=deepcopy(intent_review),
                evidence_hash=digest_of(intent_review),capabilities=deepcopy(capabilities))
        if data['ticket_id'] in old:
            original=old[data['ticket_id']]
            data={**deepcopy(original),'target':data['target'],'allowed_paths':paths,'source_evidence':data['source_evidence'],
                  'previous_attempt':data['previous_attempt'],'status':data['status'],'preserve':data.get('preserve',[])}
        tickets.append(data)
    # Multiple changed bindings of one request share one concrete authority.
    tickets=list({t['ticket_id']:t for t in tickets}.values())
    # lesson_stage deliberately prohibits a single call from modifying both
    # artifacts. Recheck author intent after the body transaction before it can
    # dispatch the persisted media-only issues against the resulting revision.
    body_first=any(t['target']['artifact_id']=='lesson:'+content['unit_id'] for t in tickets)
    dispatch=[t for t in tickets if t['status']=='ready'
              and (not body_first or t['target']['artifact_id']=='lesson:'+content['unit_id'])]
    payload={'author_media_hash':digest_of(record),'author_media':record,'tickets':tickets,
        'deferred_ticket_ids':[t['ticket_id'] for t in tickets if body_first and t['target']['artifact_id']=='author-media:'+content['unit_id']],
        'requirements_check':{'ok':False,'problems':errors},
        'intent_review':deepcopy(intent_review),
        'review_outcome':outcome('known_error',[e['message'] for e in errors])}
    ref=_emit(ctx,'media_plan',payload,'media.requirements',{'content_hash':content_hash(content),'author_media_hash':digest_of(record)})
    directives=tuple(RepairDirective(unit_id=content['unit_id'],ticket_ids=(t['ticket_id'],),owner='lesson',
        kind='text_content',reason=t['problem'],artifact_hashes={t['target']['artifact_id']:t['target']['artifact_hash']},verdict_content=ticket_verdict_content(t)) for t in dispatch)
    routes=tuple(RepairRouteV1(t['ticket_id'],ctx.node.stage,
        'content' if t['target']['artifact_id']=='lesson:'+content['unit_id'] else 'author_media',
        t['target']['artifact_id']).as_dict() for t in dispatch)
    return StageResult.ok(ctx.node.stage,outputs=(ref,),quality_state=QualityState.NEEDS_REVIEW,
        repair_requests=directives,repair_routes=routes)



def _content_not_passed(ctx):
    """R1：正文第一道审核的当前结论 —— **没过**就返回原因，过了返回空串。

    算"没过"的三种：`known_error`（确定错误）、`unverified`（未核实）、
    `missing`（审核没能给出结论，例如协议错误导致只能给未决）。
    这三种都不该放进媒体 —— 媒体要建立在**已通过质量审核**的正文上。
    """
    audit=(read_json_soft(_path(ctx,'text_audit')) or {}).get('payload') or {}
    outcome=audit.get('review_outcome') or {}
    status=str(outcome.get('status') or '')
    if status in ('known_error','unverified','missing'):
        reasons='；'.join(str(r) for r in (outcome.get('reasons') or [])[:3])
        return f'正文第一道审核未通过（{status}）：{reasons}'
    return ''


def media_plan_stage(ctx):
    repair_context=None
    try:
        _checkpoint(ctx)
        # R1（用户选 A）：**正文第一道审核没过就不做媒体**。
        # 判定用 `text_audit` 的当前结果：known_error / unverified 都算"没过"。
        # 正文会继续走返修（最多 6 圈）；轮次用尽仍未过则本单元不做媒体，
        # 但正文照旧渲染（R9）。原实现刻意不拦（D1），与用户口径相反，已按 R1 改回。
        blocking='' if reviews_frozen() else _content_not_passed(ctx)
        if blocking:
            # 必须是 **blocked**（而不是 ok）：本阶段不产出 `media_plan/<unit>.json`，
            # 下游（figures 等）会按"缺本次运行产物"处理。若这里报 ok，
            # 下游就会把"合法跳过"误判成"产物丢失"并各自 blocked ——
            # 实测 run-20261012-080849：figures 因此报缺 media_plan 产物。
            # blocked 让失败正确地**归因到上游**（审核没过），而不是怪下游缺文件。
            return StageResult.blocked(ctx.node.stage, blocking,
                quality_state=QualityState.NEEDS_REVIEW,
                findings=({'code':'MEDIA_BLOCKED_BY_CONTENT','why':blocking},))
        content,approval=_load_content(ctx); caps=_capabilities(ctx)
        author_media=_load_author_media(ctx,content,allow_stale=True)
        if not reviews_frozen() and author_media['status']=='stale': return _author_media_binding_tickets(ctx,content,approval,author_media)
        prior=(read_json_soft(_path(ctx,'media_plan')) or {}).get('payload') or {}
        repair_context=None if reviews_frozen() else _media_plan_repair_context(ctx,prior)
        client=None if caps.get('code_generation') else (_client(ctx,'figures','media_plan') if author_media['requirements']['requests'] else None)
        plan=plan_media_requirements(content,approval,author_requirements=author_media,book_id=ctx.context.book_id,capabilities=caps,client=client,revision=int(prior.get('revision') or 0)+1,repair_context=repair_context)
        if reviews_frozen():
            plan.update(review_mode=current_review_mode(), review_status='not_run', review_outcome=frozen_review())
            ref=_emit(ctx,'media_plan',plan,'media.requirements',{'content_hash':content_hash(content),'review_mode':current_review_mode()})
            return StageResult.ok(ctx.node.stage,outputs=(ref,),quality_state=QualityState.NOT_CHECKED)
        try: _freeze_replanned_obligations(plan,repair_context)
        except ContractError as exc: raise MediaPlanningError(str(exc),plan) from exc
        report=validate_requirements(plan,content,approval,capabilities=caps)
        # All deterministic capability checks above run before a paid judge call.
        from ..blocks.media.intent_review import review_author_intent,retain_intent_findings,intent_review_key
        review_key=intent_review_key(content,author_media,caps)
        receipt=review_author_intent(content,author_media,caps,_client(ctx,'judge','media_plan'),
            journal=_root(ctx)/'products/author_intent'/content['unit_id']/(review_key+'.json'))
        unresolved=retain_intent_findings(_root(ctx)/'products/author_intent'/content['unit_id']/'history.json',
                                         content['unit_id'],receipt)
        failures=[{'request_key':'goal:'+row['goal_id'],
                   'code':'AUTHOR_READER_CONTENT' if row['failure_kind']=='reader_content' else 'AUTHOR_MEDIA_INTENT',
                   'goal_ids':[row['goal_id']],'message':row['reason']} for row in receipt['verdicts'] if row['status']=='fail']
        if failures:
            return _author_media_binding_tickets(ctx,content,approval,author_media,errors=failures,
                                                 intent_review=receipt,capabilities=caps)
        # A fresh unknown cannot close a previously established goal deficiency.
        if unresolved:
            ref=_emit(ctx,'media_plan',{'author_media':author_media,'author_media_hash':digest_of(author_media),
                'intent_review':receipt,'unresolved_intent':unresolved,'tickets':[],'review_outcome':outcome('known_error',
                    ['原媒体意图缺口尚无当前通过判词；未知不能消除原失败'])},'media.requirements',
                {'content_hash':content_hash(content),'intent_review_hash':digest_of(receipt)})
            return StageResult.blocked(ctx.node.stage,'媒体意图原缺口尚未复核通过',outputs=(ref,))
        plan['intent_review']=receipt
        state=_content_outcome(ctx,content) if approval.get('schema_version')=='ContentIdentityV1' else outcome('verified')
        plan['review_outcome']=inherit_outcome(receipt['review_outcome'],state)
        ref=_emit(ctx,'media_plan',plan,'media.requirements',{'content_hash':content_hash(content),'author_media_hash':digest_of(author_media),'approval_hash':digest_of(approval),'capability_digest':digest_of(caps)})
        return StageResult.ok(ctx.node.stage,outputs=(ref,),quality_state=QualityState.ACCEPTED if plan['review_outcome']['status']=='verified' else QualityState.NEEDS_REVIEW,input_files=(str(_path(ctx,'text_audit')),))
    except MediaPlanningError as exc:
        from .diagnostics import technical_ticket
        from .repair import RepairRouteV1
        from ..blocks.audit.contracts import RepairDirective
        candidate=exc.candidate
        if not candidate.get('requests'):
            # No technical request exists to repair; the compiler cannot change decisions.
            return _boundary(ctx,exc)
        technical_fields=('kind','source_image_id','source_refs','spec','library_id')
        paths=tuple(f'/requests/{index}/{field}' for index in range(len(candidate['requests']))
                    for field in technical_fields)
        ticket=technical_ticket(unit_id=str(ctx.node.scope_key),
            artifact_id='requirements:'+str(ctx.node.scope_key),artifact_hash=digest_of(candidate),
            problem=str(exc),source=str(content.get('body_markdown') or ''),
            owner='visual_planner',kind='visual_math',allowed_paths=paths,rule='MEDIA_REQUIREMENTS_INVALID',
            action='只修作者媒体需求的技术编译：'+str(exc)+'；只返回 requests 技术项，字段限 request_key、kind、source_image_id、source_refs、spec、library_id。'
                '不得修改 decisions、作者需求、正文、数学绑定、位置、图意或验收标准；request_key 只能原样回显，不得新增或遗漏请求。'
                'image 只能选已支持静态后端，animation 必须仍为 animation；source_refs 只能补登记资源来源，不得改正文来源绑定。',
            expected='每个作者 request_key 恰有一个技术项；请求身份、作者语义和原验收标准不变，技术来源、后端与类型化规格通过本地 MediaRequirementsV2 核验。')
        ticket=replace(ticket,preserve=('/book_id','/unit_id','/content_hash','/approval_ref',
            '/decisions','/author_media','/author_media_hash')+tuple(
                f'/requests/{index}/{field}' for index,request in enumerate(candidate['requests'])
                for field in request if field not in technical_fields))
        payload={'candidate':candidate,'tickets':[ticket.as_dict()],'revision':candidate['revision'],'content_hash':content_hash(content),'requirements_check':{'ok':False,'problems':[str(exc)]}}
        if repair_context and repair_context['replan_request_ids']:
            payload.update(repair_basis=repair_context['previous_requirements'],replan_request_ids=repair_context['replan_request_ids'])
        ref=_emit(ctx,'media_plan',payload,'media.requirements',{'content_hash':content_hash(content),'approval_hash':digest_of(approval)})
        directive=RepairDirective(unit_id=str(ctx.node.scope_key),ticket_ids=(ticket.ticket_id,),owner=ticket.owner,kind=ticket.kind,reason=ticket.problem,artifact_hashes={ticket.target['artifact_id']:ticket.target['artifact_hash']},verdict_content=ticket_verdict_content(ticket))
        return StageResult.ok(ctx.node.stage,outputs=(ref,),quality_state=QualityState.NEEDS_REVIEW,repair_requests=(directive,),repair_routes=(RepairRouteV1(ticket.ticket_id,ctx.node.stage,'media_requirements',ticket.target['artifact_id']).as_dict(),))
    except Exception as exc: return _boundary(ctx,exc)


def _source_image(ctx,request,target):
    from .stage_io import _legacy
    image_id=request.get('source_image_id')
    asset=next((r for r in _source_assets(ctx)['source_images'] if r['asset_id']==image_id),None)
    source=asset['metadata'] if asset else None
    if source is None: raise ContractError('source_image_id 不在教材图片索引')
    base=Path(ctx.context.parse).resolve()
    for key in ('file','page_image'):
        if source.get(key):
            path=base/str(source[key])
            if not path.resolve().is_relative_to(base) or path.is_symlink(): raise ContractError('教材图资源越界或符号链接')
    if not _legacy('build').crop_figure(base,source,target): raise CapabilityError('教材图片缺文件/裁图区域')
    from PIL import Image
    with Image.open(target) as image: normalized=image.convert('RGB')
    normalized.save(target,format='PNG')
    return {'asset_id':asset['asset_id'],'asset_hash':asset['hash'],'source_file_hash':asset['file_hash'],
            'output_hash':digest_file(target),'origin':'textbook','transform':'normalize'}


def _intact_media_resources(row):
    """Cache reuse rechecks the same path, declared bytes and hash contract."""
    from ..core.resource_files import relocated_source_dir, verified_resource_file
    resources=row.get('resources') or []
    if not row.get('source_dir') or not resources: return False
    base=relocated_source_dir(row['source_dir'],resources)  # 修改计划 F1
    try:
        for resource in resources:
            verified_resource_file(base,resource)
    except (ContractError,OSError,ValueError,TypeError):
        return False
    return True


def _pending_protocol_review(row):
    """A current host report may need bounded citation repair, not new code."""
    from ..blocks.media.validation import result_digest
    report=row.get('validation') or {}
    issues=report.get('issues');verdicts=report.get('verdicts')
    return (report.get('ok') is False and report.get('result_hash')==result_digest(row)
        and isinstance(issues,list) and bool(issues)
        and all(isinstance(i,dict) and (str(i.get('code','')).startswith('MEDIA_REVIEW_')
            or i.get('code')=='MEDIA_CALL_FAILED') for i in issues)
        and isinstance(verdicts,list) and all(isinstance(v,dict) and v.get('status')=='pass' for v in verdicts)
        and not report.get('review_problems') and not (row.get('review') or {}).get('problems'))



def _media_plan_was_skipped(ctx):
    """R1：本单元的媒体是按"正文未过审"合法跳过的吗？

    这时 `media_plan/<unit>.json` 不存在，但**不是产物丢失**：上游 blocked。
    下游必须据此返回 not_applicable，而不是报"缺本次运行产物"
    （实测 run-20261012-080849：figures 因此 blocked，把合法跳过误判成缺文件）。
    """
    try:
        state=ctx.services.store.load_state()
    except Exception:                                        # noqa: BLE001
        return False
    node=state.get('nodes',{}).get('media_plan:'+str(ctx.node.scope_key))
    if not isinstance(node,dict):
        return False
    if node.get('execution_state')!='blocked':
        return False
    return 'MEDIA_BLOCKED_BY_CONTENT' in json.dumps(node,ensure_ascii=False)


def _limited_media_request(ctx, request, content, approval, capabilities, previous):
    """Keep one resource's generation, review and bounded repair in one worker."""
    _checkpoint(ctx)
    rid=request['request_id']
    client=_client(ctx,'figures','figures')
    reviewer=_client(ctx,'judge','figures') if substantive_reviews_enabled() else None
    for bound_client in (client,reviewer):
        if hasattr(bound_client,'resource_id'):
            bound_client.resource_id=rid
    dependencies=resource_dependencies(ctx,request,capabilities)
    resource_dir=_root(ctx)/'media'/str(ctx.node.scope_key)/rid
    if request['kind'] in ('drawing','animation'):
        from ..blocks.media.code_execution import execute_code
        row=execute_code(request,content=content,approval=approval,out_dir=resource_dir,
            client=client,reviewer=reviewer,workspace_root=ctx.workspace.root,
            cancel=ctx.services.cancel,previous=previous,dependencies=dependencies)
    else:
        row=execute_static(request,content=content,approval=approval,
            source_assets=capabilities['source_assets'],out_dir=resource_dir,
            client=client,reviewer=reviewer,workspace_root=ctx.workspace.root,
            source_image=lambda req,path:_source_image(ctx,req,path))
    if substantive_reviews_enabled():
        if row.get('resources') and not row.get('validation'):
            from ..blocks.media.validation import validate_media
            from ..blocks.media.code_execution import _apply_review
            _apply_review(row,validate_media(request,row,client=reviewer,
                workspace_root=ctx.workspace.root,content=content))
        if not row.get('review_outcome'): row['review_outcome']=media_outcome(row)
        row.update(review_mode='balanced')
    else:
        row.update(review_outcome=frozen_review(),review_mode=current_review_mode(),review_status='not_run')
    return row


def _parallel_media_results(ctx, requests, produce):
    # The same resource directory and persisted attempt budget must have one owner.
    # Keep the existing final-manifest rule: the last row for an identity wins.
    unique={request['request_id']:request for request in requests}
    if not unique: return
    policy=getattr(ctx.services.control,'policy',None)
    limit=min(len(unique),max(1,int(getattr(policy,'media_resource_parallelism',3))))
    remaining=iter(unique.values())
    pool=ThreadPoolExecutor(max_workers=limit,thread_name_prefix='media-resource')
    pending={}
    try:
        while True:
            while len(pending)<limit:
                _checkpoint(ctx)
                request=next(remaining,None)
                if request is None: break
                future=pool.submit(copy_context().run,produce,request)
                pending[future]=request
            if not pending: break
            done,_=wait(pending,return_when=FIRST_COMPLETED)
            # Consume this batch before adding work, so control errors propagate first.
            for future in done:
                yield pending.pop(future),future
    finally:
        for future in pending: future.cancel()
        pool.shutdown(wait=True,cancel_futures=True)


def figures_stage(ctx):
    resource_results=None
    try:
        _checkpoint(ctx)
        if not substantive_reviews_enabled() and _media_plan_was_skipped(ctx):
            return StageResult.not_applicable(ctx.node.stage, 'content-not-verified',
                'R1：正文第一道审核未通过，本单元不做媒体')
        content,approval=_load_content(ctx); plan=_load(ctx,'media_plan')
        current_capabilities=_capabilities(ctx)
        validate_requirements(plan,content,approval,capabilities=current_capabilities)
        previous=(read_json_soft(_path(ctx,'figures')) or {}).get('payload') or {}
        prior_rows={r['request_id']:r for r in previous.get('requests',[])}
        requests=plan['requests']; rows=[]; animations={}; animation_tickets=[]; animation_directives=[]
        deferred_protocol=set(); evidence_origins={}
        client=reviewer=None
        if requests and not reviews_frozen():
            client=_client(ctx,'figures','figures')
            reviewer=_client(ctx,'judge','figures')
        batch=getattr(ctx,'repair_batch',None)
        affected={getattr(j,'artifact_id','') for j in getattr(batch,'jobs',())}
        wanted={tid for j in getattr(batch,'jobs',()) for tid in j.ticket_ids}
        persisted=_persisted_tickets(ctx)
        static_tickets=[t for tid,t in persisted.items() if tid in wanted]
        if wanted-set(t['ticket_id'] for t in static_tickets): raise ContractError('媒体返修引用未持久化的工单')
        # Animation backend consumes the same trusted selected-ticket pool.
        previous={**previous,'tickets':list(persisted.values())}
        animation_pairs=[]
        if reviews_frozen():
            resource_results=_parallel_media_results(ctx,requests,
                lambda request:_limited_media_request(ctx,request,content,approval,
                    current_capabilities,prior_rows.get(request['request_id'])))
        results=resource_results if resource_results is not None else ((request,None) for request in requests)
        for request,future in results:
            resource_failure=False
            try:
                _checkpoint(ctx); rid=request['request_id']; old=prior_rows.get(rid)
                code_backend=current_capabilities.get('code_generation')=='GeneratedMediaCodeV1'
                if reviews_frozen():
                    rows.append(future.result())
                    continue
                same_context=True
                if code_backend and old:
                    from ..blocks.media.math_context import ApprovedMathContext
                    same_context=old.get('math_context_digest')==ApprovedMathContext.from_content_binding(content,approval,request).digest
                dependencies=resource_dependencies(ctx,request,current_capabilities) if code_backend or request['kind']!='animation' else {}
                review_only=False
                same_code_generation=bool(code_backend and request['kind'] in ('drawing','animation') and old
                    and same_context and old.get('generation_dependency_hash')==dependencies['generation_dependency_hash']
                    and old.get('request_hash')==digest_of(request))
                pending=False
                if old and request['kind'] in ('drawing','animation') and (code_backend or old.get('code_product')):
                    from ..blocks.media.math_context import ApprovedMathContext
                    from .media_review_recovery import require_compatible_pending_evidence
                    pending=require_compatible_pending_evidence(request,old,dependencies=dependencies,
                        math_context_digest=ApprovedMathContext.from_content_binding(content,approval,request).digest)
                    if pending and not _intact_media_resources(old):
                        resource_failure=True
                        raise CapabilityError('冻结媒体资源缺失或字节损坏；不能补审或重置代码候选。')
                if same_code_generation and old.get('render_profile')=='draft' and _intact_media_resources(old):
                    # Capture legacy ownership before a review can promote this
                    # frozen program to different final bytes in the same call.
                    for ticket in persisted.values():
                        if (_legacy_protocol_ticket(ticket) and ticket['target']['artifact_id'] in ('media:'+rid,'visual:'+rid)
                                and ticket['target']['artifact_hash']==old.get('spec_hash')):
                            evidence_origins[ticket['ticket_id']]=_media_evidence_basis(old)
                if pending and 'media:'+rid not in affected and 'visual:'+rid not in affected:
                    rows.append({**deepcopy(old),'state':'needs_review'})
                    deferred_protocol.add(rid)
                    continue
                if (same_code_generation and old.get('review_dependency_hash')!=dependencies['review_dependency_hash']
                        and 'media:'+rid not in affected and 'visual:'+rid not in affected):
                    if not _intact_media_resources(old):
                        resource_failure=True
                        raise CapabilityError('审核依赖已变化，但冻结媒体资源缺失或字节损坏；不能纯重审或重置代码候选。')
                    review_only=True
                if not review_only and old and same_context and (code_backend or request['kind']!='animation') and 'media:'+rid not in affected and 'visual:'+rid not in affected:
                    single_plan={**plan,'requests':[request]}
                    single_manifest={**previous,'content_hash':plan['content_hash'],'requirements_hash':digest_of(single_plan),'requests':[old]}
                    if validate_manifest(single_plan,single_manifest)['ok']:
                        if old.get('generation_dependency_hash')==dependencies['generation_dependency_hash']:
                            if old.get('review_dependency_hash')==dependencies['review_dependency_hash']:
                                rows.append({**old,'cache_hit':True}); continue
                            review_only=True
                if code_backend and request['kind'] in ('drawing','animation'):
                    from ..blocks.media.code_execution import execute_code
                    repair=[t for t in persisted.values() if t.get('target',{}).get('artifact_id') in ('media:'+rid,'visual:'+rid)]
                    selected=[t for t in static_tickets if t.get('target',{}).get('artifact_id') in ('media:'+rid,'visual:'+rid)]
                    evidence_requested=bool(selected) and all(t['kind']=='evidence_format' for t in selected)
                    same_generation=bool(old) and same_context and old.get('generation_dependency_hash')==dependencies['generation_dependency_hash'] and old.get('request_hash')==digest_of(request)
                    request_reviewer=reviewer
                    if evidence_requested and same_generation:
                        from .media_review_recovery import evidence_reviewer
                        request_reviewer=evidence_reviewer(ctx,reviewer,request,old)
                    row=execute_code(request,content=content,approval=approval,
                        out_dir=_root(ctx)/'media'/str(ctx.node.scope_key)/rid,
                        client=client,reviewer=request_reviewer,workspace_root=ctx.workspace.root,
                        cancel=ctx.services.cancel,previous=old,repair_context=repair,
                        evidence_only=review_only or (evidence_requested and same_generation),dependencies=dependencies)
                    rows.append(row);continue
                if request['kind']=='animation':
                    from ..blocks.figures.animation import route_request
                    animation_pairs.append(('lesson',route_request(request,unit_id=content['unit_id'],scope='lesson'),request)); continue
                resource_dir=_root(ctx)/'media'/str(ctx.node.scope_key)/rid
                repair=[t for t in static_tickets if t.get('target',{}).get('artifact_id')=='media:'+rid]
                selected_jobs=[job for job in getattr(batch,'jobs',()) if any(t['ticket_id'] in job.ticket_ids for t in repair)]
                evidence_requested=bool(repair) and all(getattr(job,'kind','evidence_format' if all(t.get('kind')=='evidence_format' for t in repair) else '')=='evidence_format' for job in selected_jobs)
                same_generation=bool(old) and old.get('generation_dependency_hash')==dependencies['generation_dependency_hash'] and old.get('request_hash')==digest_of(request)
                row=execute_static(request,content=content,approval=approval,source_assets=current_capabilities['source_assets'],out_dir=resource_dir,client=client,reviewer=reviewer,workspace_root=ctx.workspace.root,source_image=lambda req,path:_source_image(ctx,req,path),repair_context=repair,previous=old,evidence_only=review_only or (evidence_requested and same_generation))
                row.update(dependencies)
                rows.append(row)
            except Exception as exc:
                reraise_control_error(exc)
                from ..providers.review_failures import classify_review_failure
                failure_kind=classify_review_failure(exc)
                rid=request['request_id']
                if isinstance(exc,CapabilityError) and prior_rows.get(rid) and _pending_protocol_review(prior_rows[rid]):
                    # A mismatched recovery/environment cannot rewrite the
                    # frozen candidate or its receipt as a new failed review.
                    retained=ArtifactRef(book_id=ctx.context.book_id,kind='figure.svg',
                        logical_key='unit:'+str(ctx.node.scope_key),path=_path(ctx,'figures'),
                        producer_stage=ctx.node.stage).with_content()
                    return StageResult.blocked(ctx.node.stage,type(exc).__name__+': '+str(exc),outputs=(retained,))
                row=deepcopy(prior_rows.get(rid) or {})
                row.update(request_id=rid,kind=request['kind'],request=deepcopy(request),
                    request_hash=digest_of(request),criteria_digest=request['criteria_digest'],
                    state='needs_review',reason=type(exc).__name__+': '+str(exc))
                row.setdefault('resources',[])
                code=('MEDIA_RESOURCE_INVALID' if resource_failure else
                      ('MEDIA_REVIEW_FORMAT' if row['resources'] and _intact_media_resources(row) else 'MEDIA_RESOURCE_INVALID')
                      if failure_kind=='protocol' else 'MEDIA_CALL_FAILED')
                row['validation']={'ok':False,'decision':'needs_review','issues':[{'code':code,
                    'message':row['reason']}],'verdicts':[]}
                if not resource_failure:
                    row['validation']['review_failure']={'kind':failure_kind,'reason':row['reason']}
                from ..blocks.media.validation import result_digest
                row['validation']['result_hash']=result_digest(row)
                rows.append(row)
                deferred_protocol.add(rid)
        if animation_pairs:
            from .media import fulfill
            caps=ctx.services.capabilities
            generated,animations,animation_tickets,animation_directives=fulfill(ctx,animation_pairs,content,previous,caps,content_approval=approval)
            for row in generated:
                original=next(r for r in requests if r['request_id']==row['request_id'])
                row['request_hash']=digest_of(original); row['request']=original
            rows+=generated
        # Preserve plan order, including reused animation rows.
        by_id={r['request_id']:r for r in rows}; rows=[by_id[r['request_id']] for r in requests]
        from ..blocks.media.validation import validate_media,validate_media_report
        from ..blocks.audit.evidence import resolve_json_pointer
        for row in rows:
            if reviews_frozen(): continue
            # Preserve the exact failed receipt until Runner authorizes a bounded
            # evidence batch; rebinding tickets here would trigger an extra review.
            if row['request_id'] in deferred_protocol: continue
            request=next(r for r in requests if r['request_id']==row['request_id'])
            row['request']=deepcopy(request)
            row['criteria_digest']=request['criteria_digest']
            row['approved_reader_document_hash']=approval['reader_document_hash']
            dependencies=[]
            for dep in request['dependency_paths']:
                found,value=resolve_json_pointer(content,dep['pointer'])
                if not found or digest_of(value)!=dep['hash']:
                    raise ContractError('media dependency no longer matches approved content')
                dependencies.append({**dep,'value':deepcopy(value)})
            row['approved_dependencies']=dependencies
            row['repair_tickets']=[t for t in persisted.values()
                if t.get('target',{}).get('artifact_id') in ('media:'+row['request_id'],'visual:'+row['request_id'])]
            if row.get('resources'):
                try:
                    valid=bool(row.get('validation')) and validate_media_report(request,row,row['validation'])
                except ContractError:
                    valid=False
                from ..blocks.media.validation import result_digest
                from ..blocks.media.validation import media_review_items
                from ..blocks.media.review_policy import media_review_policy
                current_code_review=bool(row.get('validation')
                    and row['validation'].get('result_hash')==result_digest(row)
                    and row['validation'].get('policy_digest')==digest_of(media_review_policy(request['kind']))
                    and row['validation'].get('review_items')==media_review_items(request,row,media_review_policy(request['kind'])))
                if not valid and not current_code_review:
                    row['validation']=validate_media(request,row,client=reviewer,workspace_root=ctx.workspace.root)
                report=row['validation']
                if row.get('code_product') and row['kind']=='animation' and row.get('render_profile')!='final':
                    row.update(state='needs_review',reason='动画尚未完成 final 渲染和审核；draft 仅作制作反馈。')
                    continue
                if row['kind']=='animation' and report['ok']:
                    row['animation']['status']='fulfilled'
                    if row.get('code_product'): row['animation']['math_report']=report['math_report']
                    from ..blocks.media.evidence_digest import validation_digest
                    row['animation']['frame_report']={'status':'pass','validation_hash':validation_digest(report)}
                row.update(state='fulfilled' if report['ok'] else report['decision'],
                    reason='；'.join(i['message'] for i in report['issues']),
                    review={'ok':report['ok'],'problems':report.get('review_problems',[])})
        upstream=_plan_outcome(ctx,content,approval,plan)
        for row in rows:
            if not substantive_reviews_enabled(): row.pop('review_outcome',None)
            row['review_outcome']=(deepcopy(row.get('review_outcome') or media_outcome(row))
                if substantive_reviews_enabled() else frozen_review() if reviews_frozen()
                else inherit_outcome(media_outcome(row),upstream))
        tickets,directives,routes=([],(),()) if reviews_frozen() else _tickets(ctx,content,rows,evidence_origins=evidence_origins)
        if substantive_reviews_enabled():
            # Confirmed discoveries about the text use the existing lesson
            # repair route and its unit budget, never a second media loop.
            from .content_stages import balanced_media_content_repairs
            from ..blocks.media.validation import _verdict_reason
            problems=[]
            for row in rows:
                report=row.get('validation') or {}
                for problem in [*report.get('review_problems',[]), *report.get('verdicts',[])]:
                    if (isinstance(problem,dict) and problem.get('target_artifact_kind')=='content'
                            and problem.get('status')!='pass'):
                        problems.append({**deepcopy(problem), 'reason':_verdict_reason(problem) or '正文仍有待核实问题。'})
            tickets,directives,routes=balanced_media_content_repairs(ctx,content,problems)
        from .repair import RepairRouteV1
        routes+=tuple(RepairRouteV1(t['ticket_id'],ctx.node.stage,'media_evidence' if t['kind']=='evidence_format' else 'visual_spec',t['target']['artifact_id']).as_dict() for t in animation_tickets)
        tickets+=animation_tickets; directives+=tuple(animation_directives)
        payload={'schema_version':'MediaManifestV2','unit_id':content['unit_id'],'content_hash':content_hash(content),'requirements_hash':digest_of(plan),'requests':rows,'review_outcome':aggregate_outcomes([r['review_outcome'] for r in rows]) if rows else upstream,'tickets':tickets,'policy':'media-plan-explicit-v1','evidence':{'decision_count':len(plan['decisions']),'why':'所有登记决策已评估；空请求不触发任何资源后端'} if not requests else {},'animation_payload':{content['unit_id']:[{**r['animation'],'source_dir':r['source_dir']} for r in rows if r['kind']=='animation' and (r['state']=='fulfilled' or substantive_reviews_enabled() and r.get('resources') and r.get('animation'))]}}
        if substantive_reviews_enabled():
            payload['review_mode']='balanced'
            # Keep media and prose conclusions separate at resource level;
            # the lesson still reports the discovered error and its repair.
            overall=[payload['review_outcome'], upstream]
            if problems:
                overall.append(outcome('known_error' if any(p.get('status')=='fail' for p in problems) else 'unverified',
                    [p['reason'] for p in problems]))
            payload['review_outcome']=aggregate_outcomes(overall)
        ref=_emit(ctx,'figures',payload,'figure.svg',{'content_hash':content_hash(content),'requirements_hash':digest_of(plan)})
        files=tuple(str(Path(r['source_dir'])/s['relative_path']) for r in rows if r.get('source_dir') for s in r.get('resources',[]))
        if reviews_frozen():
            failures=[r for r in rows if r.get('state')!='fulfilled'
                      and not (substantive_reviews_enabled() and r.get('resources') and r.get('validation'))]
            # 用户 2026-10-20 定：技术检查放宽。个别媒体没做成只记为缺失（清单里 review_outcome=missing），
            # 不再把整个媒体阶段判阻塞——否则本节的审核与交互会被依赖关系一并拦下。
            quality=(QualityState.ACCEPTED if payload['review_outcome']['status']=='verified' and not failures
                     else QualityState.NEEDS_REVIEW) if substantive_reviews_enabled() else QualityState.NOT_CHECKED
            return StageResult.ok(ctx.node.stage,outputs=(ref,),quality_state=quality,
                repair_requests=directives,repair_routes=routes,
                findings=[{'code':'MEDIA_MISSING','request_id':r.get('request_id'),'why':str(r.get('reason') or '媒体制作未完成')}
                          for r in failures],
                input_files=files+(str(_path(ctx,'media_plan')),))
        if not requests: return StageResult.not_applicable(ctx.node.stage,policy=payload['policy'],evidence=json.dumps(payload['evidence'],ensure_ascii=False),outputs=(ref,),input_files=(str(_path(ctx,'media_plan')),))
        failures=[r for r in rows if r['review_outcome']['status']!='verified']
        return StageResult.ok(ctx.node.stage,outputs=(ref,),quality_state=QualityState.NEEDS_REVIEW if failures else QualityState.ACCEPTED,repair_requests=directives,repair_routes=routes,input_files=files+(str(_path(ctx,'media_plan')),))
    except Exception as exc: return _boundary(ctx,exc)
    finally:
        if resource_results is not None: resource_results.close()


def audit_stage(ctx):
    try:
        _checkpoint(ctx); content,content_approval=_load_content(ctx); requirements=_load(ctx,'media_plan'); manifest=_load(ctx,'figures')
        if reviews_frozen():
            state=deepcopy(manifest.get('review_outcome') or _content_outcome(ctx,content)) if substantive_reviews_enabled() else frozen_review()
            ref=_emit(ctx,'audit',{'approval':None,'review_mode':current_review_mode(),'review_status':'reused' if substantive_reviews_enabled() else 'not_run',
                'review_outcome':state,'content_hash':content_hash(content),'tickets':[],
                'requirements_hash':digest_of(requirements),'manifest_hash':manifest_digest(manifest)},
                'audit.unit',{'content':content_hash(content),'review_mode':current_review_mode()})
            quality=(QualityState.ACCEPTED if state['status']=='verified' else QualityState.NEEDS_REVIEW) if substantive_reviews_enabled() else QualityState.NOT_CHECKED
            return StageResult.ok(ctx.node.stage,outputs=(ref,),quality_state=quality)
        validate_requirements(requirements,content,content_approval,capabilities=_capabilities(ctx))
        report=validate_manifest(requirements,manifest)
        approval=None
        if report['ok']: approval,report=approve_media(requirements,manifest,content_approval)
        failures=[]
        if not report['ok']:
            for row in manifest['requests']:
                request=next((r for r in requirements['requests'] if r['request_id']==row['request_id']),None)
                if not request: continue
                if row.get('state')!='fulfilled':
                    failures.append(row)
                    continue
                single_plan={**requirements,'requests':[request]}
                single_manifest={**manifest,'requirements_hash':digest_of(single_plan),'requests':[row]}
                check=validate_manifest(single_plan,single_manifest)
                if not check['ok']:
                    problems=check['problems']
                    failure={**row,'state':'needs_review','reason':'；'.join(problems)}
                    resource_issues=[issue for issue in check['blocking_issues'] if issue['code']=='RESOURCE_INVALID']
                    evidence_issues=[issue for issue in check['blocking_issues']
                        if issue['code'] in ('IMAGE_REVIEW_EVIDENCE','MEDIA_VALIDATION_V2')]
                    if resource_issues:
                        failure['validation']=deepcopy(row.get('validation') or {})
                        failure['validation'].setdefault('issues',[]).append({
                            'code':'RESOURCE_INVALID','message':'；'.join(i['message'] for i in resource_issues)})
                    elif evidence_issues:
                        failure['validation']=deepcopy(row.get('validation') or {})
                        failure['validation'].setdefault('issues',[]).append({
                            'code':'MEDIA_REVIEW_EVIDENCE','message':'；'.join(i['message'] for i in evidence_issues)})
                    failures.append(failure)
        tickets,directives,routes=_tickets(ctx,content,failures)
        upstream=_plan_outcome(ctx,content,content_approval,requirements)
        state=inherit_outcome(outcome('verified') if approval else aggregate_outcomes(media_outcome(row) for row in failures),upstream)
        # This is evidence validation only; no discover and no hidden regeneration.
        ref=_emit(ctx,'audit',{'approval':approval,'review_outcome':state,'report':report,'content_hash':content_hash(content),'requirements_hash':digest_of(requirements),'manifest_hash':manifest_digest(manifest),'tickets':tickets or manifest.get('tickets') or []},'audit.unit',{'content_binding':digest_of(content_approval),'requirements':digest_of(requirements),'manifest':manifest_digest(manifest)})
        return StageResult.ok(ctx.node.stage,outputs=(ref,),quality_state=QualityState.ACCEPTED if state['status']=='verified' else QualityState.NEEDS_REVIEW,repair_requests=directives,repair_routes=routes,findings=[{'code':'media-acceptance','why':p} for p in report['problems']],input_files=(str(_path(ctx,'media_plan')),str(_path(ctx,'figures')))+tuple(str(Path(r['source_dir'])/s['relative_path']) for r in manifest['requests'] if r.get('source_dir') for s in r.get('resources',[])))
    except Exception as exc: return _boundary(ctx,exc)


def interaction_stage(ctx):
    try:
        _checkpoint(ctx); content,approval=_load_content(ctx); requirements=_load(ctx,'media_plan')
        validate_requirements(requirements,content,approval,capabilities=_capabilities(ctx))
        assignments=[d for d in requirements['decisions'] if d.get('handled_by')=='interaction']
        payload={'schema_version':'InteractionResultV2','content_hash':content_hash(content),'requirements_hash':digest_of(requirements),'models':[],'results':[],'policy':'media-plan-delegation-v1','evidence':'媒体规划没有委派交互目标' if not assignments else ''}
        if assignments:
            from .stage_io import _legacy
            generator=_legacy('model_generate'); client=_client(ctx,'interaction','interaction')
            if client is None: raise CapabilityError('委派交互目标缺 interaction 传输')
            previous=(read_json_soft(_path(ctx,'interaction')) or {}).get('payload') or {}
            wanted={tid for job in getattr(getattr(ctx,'repair_batch',None),'jobs',()) for tid in job.ticket_ids}
            persisted=_persisted_tickets(ctx,('interaction',))
            if wanted-set(persisted): raise ContractError('交互返修引用未持久化工单')
            repair=[persisted[tid] for tid in sorted(wanted)]
            generated=generator.generate_assigned_models(content,assignments=assignments,client=client,unit_id=content['unit_id'],parse_dir=Path(ctx.context.parse),requirements_hash=digest_of(requirements),repair_context=repair or None)
            payload.update(generated)
        from ..blocks.interaction.spec import validate_assignment_coverage
        if reviews_frozen():
            payload.update(review_mode=current_review_mode(),review_status='not_run',review_outcome=frozen_review())
            ref=_emit(ctx,'interaction',payload,'interaction.unit',{'content_hash':content_hash(content),'requirements_hash':digest_of(requirements)})
            return StageResult.ok(ctx.node.stage,outputs=(ref,),quality_state=QualityState.NOT_CHECKED)
        report=validate_assignment_coverage(requirements,payload,content_hash(content)); payload['report']=report
        if report['ok']:
            from ..blocks.interaction.placements import bind_interaction_placements
            payload['mounts']=bind_interaction_placements(content,requirements,payload)
        directives=();routes=()
        if not report['ok']:
            from .diagnostics import technical_ticket
            from .repair import RepairRouteV1
            from ..blocks.audit.contracts import RepairDirective
            ticket=technical_ticket(unit_id=str(ctx.node.scope_key),artifact_id='interaction:'+str(ctx.node.scope_key),artifact_hash=digest_of(payload),problem='；'.join(report['problems']),source=str(content.get('body_markdown') or ''),owner='interaction',kind='text_content',allowed_paths=('/models','/results'),rule='INTERACTION_ASSIGNMENT_INVALID',action='只修本次委派交互规格与结果：逐目标返回合法模板、当前正文锚点、正确binds_to和实际状态采样；补齐缺失目标、移除未登记目标。不能修改批准内容或把必要目标改不适用。',expected='当前content_hash/requirements_hash匹配；所有委派目标恰好一个完整规格及状态变化检查通过；必要目标不得not_applicable')
            from ..blocks.audit.contracts import validate_ticket
            original=_persisted_tickets(ctx,('interaction',)).get(ticket.ticket_id)
            if original: ticket=validate_ticket({**deepcopy(original),'target':ticket.target})
            payload['tickets']=[ticket.as_dict()]
            directives=(RepairDirective(unit_id=str(ctx.node.scope_key),ticket_ids=(ticket.ticket_id,),owner=ticket.owner,kind=ticket.kind,reason=ticket.problem,artifact_hashes={ticket.target['artifact_id']:ticket.target['artifact_hash']},verdict_content=ticket_verdict_content(ticket)),)
            routes=(RepairRouteV1(ticket.ticket_id,ctx.node.stage,'interaction_spec',ticket.target['artifact_id']).as_dict(),)
        upstream=_plan_outcome(ctx,content,approval,requirements)
        payload['review_outcome']=inherit_outcome(outcome('verified' if report['ok'] else 'missing',report['problems']),upstream)
        ref=_emit(ctx,'interaction',payload,'interaction.unit',{'content_hash':content_hash(content),'requirements_hash':digest_of(requirements)})
        return StageResult.ok(ctx.node.stage,outputs=(ref,),quality_state=QualityState.ACCEPTED if payload['review_outcome']['status']=='verified' else QualityState.NEEDS_REVIEW,repair_requests=directives,repair_routes=routes,findings=[{'code':'interaction-assignment','why':p} for p in report['problems']],input_files=(str(_path(ctx,'media_plan')),str(_path(ctx,'text_audit'))))
    except Exception as exc: return _boundary(ctx,exc)


def _copy_static_resources(row,unit_id,figures_dir):
    """Every unit/request has a namespace; verify bytes before and after copy."""
    if any('/' in identity or identity in ('.','..') for identity in (str(unit_id),str(row['request_id']))): raise ContractError('媒体命名空间身份不能包含路径')
    destination=Path(figures_dir)/str(unit_id)/row['request_id']; destination.mkdir(parents=True,exist_ok=True)
    paths={}
    for resource in row['resources']:
        from ..core.resource_files import relocated_source_dir
        rel=resource['relative_path']; source=relocated_source_dir(row['source_dir'],row['resources'])/rel  # 修改计划 F1
        if Path(rel).is_absolute() or '..' in Path(rel).parts or not source.is_file() or source.is_symlink() or digest_file(source)!=resource['sha256']: raise ContractError('组合复制前资源字节/路径不一致')
        target=destination/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        if digest_file(target)!=resource['sha256']: raise ContractError('组合复制后资源字节不一致')
        paths[rel]=str(target)
    entry=deepcopy(row['entry']);field='svg' if row.get('svg') else 'file'
    if row.get('code_product') and not reviews_frozen():
        from ..blocks.media.validation import validate_media_report
        from ..blocks.media.evidence_digest import validation_digest
        validate_media_report(row['request'],row,row.get('validation'))
        if row.get('state')!='fulfilled': raise ContractError('代码图片尚未通过实际画面审核')
        entry['checks']={'ok':True,'basis':'actual_media_validation',
            'validation_hash':validation_digest(row['validation'])}
    entry[field]='figures/'+str(unit_id)+'/'+row['request_id']+'/'+str(row.get('svg') or row.get('image'))
    return entry,paths


def _render_reader_projection(ctx, *, key, units, chapter_units):
    """Export one current reader scope; chapters and the book use identical code."""
    _checkpoint(ctx)
    from ..blocks.render.diagnostics import build_reader_snapshot, annotate_reader_check
    from ..checks.offline import check_browser_execution
    check_page=not reviews_frozen() or substantive_reviews_enabled()
    run=_root(ctx).parent
    snapshot=build_reader_snapshot(run,unit_ids=list(units),out_dir=_root(ctx)/'reader-snapshots'/key,
        parse_root=Path(ctx.context.parse),require_complete_units=True)
    source=Path(snapshot['output_dir'])
    out=_root(ctx)/'legacy/render'/key
    if out.is_symlink(): raise ContractError('reader output must not be symlink')
    if out.exists(): shutil.rmtree(out)
    shutil.copytree(source,out)
    browser_report={'status':'not_run','ok':None,'detail':'审核已冻结（json_only）','review_mode':'json_only'}
    if check_page:
        browser_report=check_browser_execution(out,node=shutil.which('node'),workspace_root=Path(ctx.workspace.root)).as_dict()
        annotate_reader_check(out,browser_report)
    write_json_atomic(out/'browser-report.json',browser_report)
    from ..blocks.media.composition import reader_projection_binding, reader_projection_status
    state,partial=reader_projection_status(snapshot,browser_report)
    members={str(p.relative_to(out)):digest_file(p) for p in sorted(out.rglob('*')) if p.is_file()}
    projection=digest_of({'snapshot_hash':snapshot['snapshot_hash'],'members':members})
    return {'schema_version':'ReaderProjectionV1','chapter':key,'units':list(units),
        'rendered_units':snapshot['rendered_units'],
        'not_in_scope':[u for u in chapter_units if u not in units],
        'pages':[name for name in members if name.endswith('.html')],'out_dir':str(out),
        'reader_snapshot':snapshot,'reader_projection_hash':projection,'members':members,
        'composition':reader_projection_binding(snapshot),
        'review_outcome':state,'partial':partial,
        'browser_report':browser_report,'report':{'problems':snapshot['problems']}}


def render_stage(ctx):
    """Gather terminal local products through the same immutable reader as preview."""
    try:
        _checkpoint(ctx)
        check_page=not reviews_frozen() or substantive_reviews_enabled()
        catalog_path=Path(ctx.context.parse)/'chapters.json'
        catalog=read_json(catalog_path)
        selected=ctx.plan.resolved_scope
        if selected is None: raise ContractError('render 必须冻结 resolved_scope')
        known={str(row['unit_id']) for row in catalog.get('lesson_units',[])}
        if set(selected)-known: raise ContractError('所选单元已从当前目录消失，不能缩小交付范围')
        chapter=str(ctx.node.scope_key)
        chapter_units=[str(r['unit_id']) for r in catalog.get('lesson_units',[]) if str(r.get('chapter'))==chapter]
        units=[u for u in selected if u in chapter_units]
        if not units: raise ContractError('当前章没有所选单元')
        payload=_render_reader_projection(ctx,key=chapter,units=units,chapter_units=chapter_units)
        run=_root(ctx).parent
        out=Path(payload['out_dir'])
        snapshot=payload['reader_snapshot']
        browser_report=payload['browser_report']
        projection=payload['reader_projection_hash']
        state=payload['review_outcome']
        ref=_emit(ctx,'render',payload,'render.chapter',{'reader_projection_hash':projection,
            'chapters':digest_file(catalog_path)},chapter)
        outputs=(ref,)+tuple(ArtifactRef(book_id=ctx.context.book_id,
            kind='render.html' if p.suffix=='.html' else 'render.resource',
            logical_key=f'render:{chapter}/{p.relative_to(out)}',path=p,producer_stage='render',
            producer_version='reader-projection-v1',dependency_digest=projection).with_content()
            for p in out.rglob('*') if p.is_file())
        findings=[{'code':'reader-partial','why':str(p)} for p in snapshot['problems']]
        if check_page and not browser_report['ok']: findings.append({'code':'browser','why':browser_report.get('detail','')})
        return StageResult.ok(ctx.node.stage,outputs=outputs,
            quality_state=QualityState.NOT_CHECKED if not check_page else QualityState.ACCEPTED if state['status']=='verified' and browser_report['ok'] else QualityState.NEEDS_REVIEW,
            findings=findings,input_files=(str(catalog_path),)+tuple(str(run/name) for name in snapshot['source_hashes']))
    except Exception as exc: return _boundary(ctx,exc)
