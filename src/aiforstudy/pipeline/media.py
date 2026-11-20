"""V2 animation planning and isolated rendering; unified media validation owns review."""
from pathlib import Path
import json

from ..blocks.figures import animation
from ..blocks.visual.spec import parse_visual_spec
from ..blocks.audit.contracts import RepairDirective
from ..core.digest import digest_file, digest_of
from ..core.jsonio import write_json_atomic
from ..errors import ContractError, CapabilityError
from .services import reraise_control_error, BoundaryCancellation




def specification_prompt(request, lesson, source, unit_id, request_id):
    from ..blocks.visual.planner_contract import visual_planner_contract, visual_planner_instructions
    return [
        {'role':'system','content':visual_planner_instructions()},
        {'role':'user','content':json.dumps({'request':request,'unit_id':unit_id,'request_id':request_id,
          'source_text':source,'approved_content':lesson,'visual_contract':visual_planner_contract()},ensure_ascii=False)}]


def animation_component_dependencies(worker):
    """Actual component files, never truncated source-string cache identities."""
    blocks=Path(animation.__file__).parents[1]
    groups={
      'schema': [blocks/'visual/spec.py'],
      'prompt': [blocks/'visual/planner_contract.py'],
      'mathematics': [blocks/'math'/name for name in ('__init__.py','contracts.py','expression.py','format.py','verify.py')],
      'presentation': [blocks/'media/presentation_policy.py'],
      'compiler': [blocks/'visual/compiler.py',blocks/'media/math_context.py',blocks/'media/plot.py'],
      'backend': [blocks/'visual/manim_adapter.py',blocks/'visual/worker.py',Path(animation.__file__)],
      'worker': sorted(Path(worker).glob('*.py')),
    }
    return {key:{p.name:digest_file(p) for p in paths} for key,paths in groups.items()}


def cache_identity(ctx, request, lesson, source, caps=None, *, math_context=None, runtime_identity=None):
    from ..blocks.visual.worker import resolve_worker_dir
    worker=resolve_worker_dir(workspace_root=ctx.workspace.root)
    return digest_of({'request':request,'approved_math':math_context.digest if math_context else None,
      'components':animation_component_dependencies(worker),'font':_font_fingerprint(worker),
      'runtime':runtime_identity or _animation_runtime_identity(ctx)})


def _animation_runtime_identity(ctx):
    """Probe before any outer row reuse, using the renderer's actual services."""
    from ..blocks.visual.manim_adapter import default_services,limits_for_profile
    from ..blocks.visual.worker import probe_isolation,environment_hash
    from ..blocks.media.dependencies import file_identity
    import shutil
    services=default_services(workspace_root=ctx.workspace.root)
    probe=probe_isolation(services=services.worker_services(),
        limits=limits_for_profile(services.worker_dir,'draft'),collect_environment=True,
        cancel=BoundaryCancellation(ctx.services.cancel,ctx.services.control))
    isolation=probe.get('isolation') or {}
    if not isolation.get('safe'):
        raise CapabilityError('动画当前隔离探针未通过，不能复用历史资源')
    typography=(probe.get('environment') or {}).get('math_tex') or {}
    if not typography.get('ok') or not typography.get('frame_hash'):
        raise CapabilityError('动画分数/上下标/中文混排实帧预检未通过：'+str(typography.get('detail')))
    return {'environment':environment_hash(probe,profile='draft'),
            'interpreter':file_identity(services.interpreter),
            'ffmpeg':file_identity(services.ffmpeg),'ffprobe':file_identity(shutil.which('ffprobe'))}


def review_identity(ctx,request):
    # Only a dependency descriptor. The unified MediaValidationService owns review.
    from ..blocks.media.dependencies import model_role_identity, component_identity, file_identity
    from .runtime_dependencies import STATIC_REVIEW_COMPONENTS,media_prompt_components
    from .stage_io import _repo_root
    return digest_of({'judge':model_role_identity(ctx,'judge'),
                      'criteria':request.get('acceptance_criteria'),
                      'components':component_identity(STATIC_REVIEW_COMPONENTS+media_prompt_components(request.get('kind','animation'),'review')),
                      'reader_surface':file_identity(_repo_root()/'blocks/render/assets/media-surface.css'),
                      'frame_checker':digest_file(Path(animation.__file__).parents[1]/'visual/frame_check.py')})


def _font_fingerprint(worker):
    from ..blocks.visual.worker import load_asset
    policy = load_asset(worker, 'policy')
    path = Path(policy.FONT_FILE)
    return {'family':policy.FONT_FAMILY, 'file':str(path),
            'sha256':digest_file(path) if path.is_file() else 'missing'}


def valid_cached(row):
    from ..core.resource_files import relocated_source_dir
    result = row.get('animation') or {}
    base = relocated_source_dir(row.get('source_dir'), result.get('resources') or [])  # 修改计划 F1
    resources = result.get('resources') or []
    return (row.get('state') in ('fulfilled','needs_review') and
            (result.get('math_report') or {}).get('status')=='pass' and
            (result.get('technical_report') or {}).get('ffmpeg_decode_ok') is True and resources and
            all((base / r['relative_path']).is_file() and
                digest_file(base / r['relative_path']) == r['sha256'] for r in resources))


def fulfill(ctx, pairs, lesson, previous, caps, *, content_approval):
    """V2 production adapter. Q09 owns the sole semantic media review call."""
    from . import stage_io as T
    from .execution_control import ExecutionScope
    from ..blocks.media.math_context import ApprovedMathContext
    from ..blocks.visual.spec import VisualSpecV2
    from ..blocks.visual.worker import resolve_worker_dir
    from ..blocks.media.dependencies import model_role_identity
    rows,payloads,tickets,directives=[],{},[],[]
    root=T._store_root(ctx);unit_id=lesson['unit_id'];seen=set()
    environment=animation.render_environment(caps)
    runtime_identity=None
    batch=getattr(ctx,'repair_batch',None)
    wanted={t for job in getattr(batch,'jobs',()) for t in job.ticket_ids}
    for scope,route,request in pairs:
        ctx.services.cancel.checkpoint();ctx.services.control.check()
        rid=route.request_id
        row={'request_id':rid,'request_hash':digest_of(request),'kind':'animation','scope':scope,
             'request':request,'state':'blocked','reason':'','resources':[]}
        rows.append(row)
        try:
            if rid in seen: raise ContractError('重复动画请求身份：'+rid)
            seen.add(rid)
            context=ApprovedMathContext.from_content_binding(lesson,content_approval,request)
            from ..blocks.audit.evidence import resolve_json_pointer
            row['criteria_digest']=request['criteria_digest']
            row['approved_dependencies']=[{**dep,'value':resolve_json_pointer(lesson,dep['pointer'])[1]} for dep in request['dependency_paths']]
            if not environment.available: raise CapabilityError(environment.detail)
            # Expensive environment checks occur before any paid planning call.
            if runtime_identity is None: runtime_identity=_animation_runtime_identity(ctx)
            worker=resolve_worker_dir(workspace_root=ctx.workspace.root)
            components=animation_component_dependencies(worker)
            planning=digest_of({'request':request,'math_context':context.digest,
                               'prompt':components['prompt'],'schema':components['schema'],
                               'planner_role':model_role_identity(ctx,'figures')})
            identity=cache_identity(ctx,request,lesson,Path('.'),caps,math_context=context,runtime_identity=runtime_identity)
            row.update(planning_dependency_hash=planning,generation_dependency_hash=identity,resource_digest=identity)
            row['review_dependency_hash']=review_identity(ctx,request)
            prior=next((r for r in previous.get('requests',[]) if r.get('request_id')==rid),{})
            repair=[t for t in previous.get('tickets',[]) if t.get('ticket_id') in wanted and
                    t.get('target',{}).get('artifact_id') in ('visual:'+rid,'media:'+rid)]
            from ..blocks.media.execution import resource_regeneration_tickets
            regeneration=resource_regeneration_tickets(repair,request,prior)
            spec_repair=[t for t in repair if t not in regeneration and t.get('kind')!='evidence_format']
            evidence_only=bool(repair) and all(t.get('kind')=='evidence_format' for t in repair)
            if prior.get('resource_digest')==identity and (not repair or evidence_only) and valid_cached(prior):
                # Render evidence is reusable; current review is always revalidated by Q09.
                current_review=row['review_dependency_hash']
                row.update(prior,request=request,request_hash=digest_of(request),cache_hit=True,state='needs_review',
                           review_dependency_hash=current_review)
                if prior.get('review_dependency_hash')!=current_review:
                    row.pop('validation',None)
                continue
            spec=request.get('spec')
            if regeneration and not spec_repair:
                if prior.get('planning_dependency_hash')!=planning or not prior.get('visual_spec'):
                    raise ContractError('resource regeneration lacks current frozen animation spec')
                spec=prior['visual_spec']
            elif spec is None and prior.get('planning_dependency_hash')==planning and (not repair or evidence_only):
                spec=prior.get('visual_spec')
            if spec is None or spec_repair:
                planner,_=T._gateway_client(ctx,role='figures',bucket='figures')
                if planner is None: raise CapabilityError('动画缺 figures 规划角色')
                planner.resource_id=rid
                messages=specification_prompt(request,lesson,lesson.get('original_text',''),unit_id,rid)
                if repair:
                    messages.append({'role':'user','content':json.dumps({'previous_spec':prior.get('visual_spec'),
                        'repair_tickets':repair,'constraints':'只修复工单指定呈现问题；批准数学、目标、主张、冻结标准不能改变。'},ensure_ascii=False)})
                spec=planner.chat_json(messages,tag=f'visual-plan:{rid}')['data']
            row['invalid_spec']=spec;parsed=parse_visual_spec(spec)
            if not isinstance(parsed,VisualSpecV2): raise ContractError('生产动画只接受 VisualSpecV2')
            if parsed.request_id!=rid or parsed.unit_id!=unit_id: raise ContractError('动画规格身份与批准请求不一致')
            row.pop('invalid_spec',None);row['visual_spec']=parsed.as_dict()
            write_json_atomic(root/'visual-specs'/rid/(parsed.spec_hash.removeprefix('sha256:')+'.json'),parsed.as_dict())
            renderer=animation.manim_renderer(workspace_root=ctx.workspace.root,output_root=root/'animation',
                cancel=BoundaryCancellation(ctx.services.cancel,ctx.services.control),math_context=context)
            profile_results={};bases={};last=None
            for profile in ('draft','final'):
                ctx.services.cancel.checkpoint();ctx.services.control.check()
                with ctx.services.control.work_scope(ExecutionScope(ctx.services.store.run_id,unit_id,ctx.node.stage,rid)):
                    raw=renderer(profile,parsed.as_dict())
                result,directory=animation._parse_render_output(raw,request_id=rid)
                profile_results[profile]=result.as_dict();bases[profile]=directory;last=result
                if result.status not in ('needs_review','fulfilled'):
                    row.update(profile_results=profile_results,profile_dirs=bases,source_dir=directory,
                               animation=result.as_dict(),resources=[r.as_dict() for r in result.resources])
                    raise CapabilityError('动画 '+profile+' 渲染失败：'+str(result.technical_report))
            row.update(state='needs_review',reason='等待统一 MediaValidationService 核对冻结标准与实际画面',
                       animation=last.as_dict(),resources=[r.as_dict() for r in last.resources],source_dir=bases['final'],
                       profile_dirs=bases,profile_results=profile_results,math_context_digest=context.digest)
        except Exception as exc:
            reraise_control_error(exc)
            row['reason']=f'{type(exc).__name__}: {exc}'
            row['repair_instructions']='按具体字段/数学/环境错误修复；保留批准请求身份、数学模型摘要与冻结标准。'
            row['acceptance_criteria']=request.get('acceptance_criteria',[])
            from ..blocks.visual.spec import UnsupportedVisualSpec
            if isinstance(exc,UnsupportedVisualSpec):
                # 能力边界，**不是**字段错误（spec.py:149-158：模型说"这个场景我表达不了"，
                # 调用方应把它变成 unsupported／blocked）。以前这里也走下面的工单分支，
                # 于是程序命令它"按字段错误修复规格"接着交 —— 它重交十遍还是画不出来。
                # 记 blocked：媒体审核会跳过该行（media_stages.py:298），因此不会再派工单，
                # 但行上保留真实原因，验收能看见"这张图没做出来、为什么"。
                row.update(state='blocked',reason=str(exc))
            elif isinstance(exc,ContractError):
                from .diagnostics import technical_ticket
                ticket=technical_ticket(unit_id=unit_id,artifact_id='visual:'+rid,
                    artifact_hash=digest_of(row.get('invalid_spec') or row.get('visual_spec') or request),
                    problem=row['reason'],source=lesson.get('original_text',''),owner='visual_planner',kind='visual_math',
                    allowed_paths=('/spec',),rule='VISUAL_SPEC_INVALID',
                    action='按字段错误修复VisualSpecV2；不得新增或改变批准数学模型、教学目标与冻结标准。',
                    expected='V2目标/主张/对象关系与共享数学检查通过，draft/final实际资源交由统一媒体标准审核。')
                tickets.append(ticket.as_dict())
                directives.append(RepairDirective(unit_id=unit_id,ticket_ids=(ticket.ticket_id,),owner=ticket.owner,
                    kind=ticket.kind,reason=ticket.problem,artifact_hashes={'visual:'+rid:ticket.target['artifact_hash']}))
    return rows,payloads,tickets,directives
