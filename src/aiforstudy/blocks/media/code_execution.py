"""API-written media, isolated render, actual-pixel review, bounded revision.

The approved request is immutable. Python is never evaluated by this host.
Each attempt and both render profiles are retained for diagnosis.
"""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import time
import json

from ...core.digest import digest_file, digest_of, digest_text
from ...core.jsonio import write_json_atomic, read_json_soft
from ...core.review_mode import reviews_frozen, frozen_review, substantive_reviews_enabled, current_review_mode
from ...errors import Cancelled, CapabilityError, ContractError
from ..audit.evidence import resolve_json_pointer
from .math_context import ApprovedMathContext

MAX_CODE_ATTEMPTS = 3


def _bound_row(request, content, approval, context):
    deps=[]
    for dep in ([] if reviews_frozen() else request.get('dependency_paths',[])):
        found,value=resolve_json_pointer(content,dep['pointer'])
        if not reviews_frozen() and (not found or digest_of(value)!=dep['hash']):
            raise ContractError('approved media dependency changed')
        deps.append({**dep,'value':deepcopy(value)})
    return {'request_id':request['request_id'],'kind':request['kind'],
        'request':deepcopy(request),'request_hash':digest_of(request),
        'criteria_digest':request.get('criteria_digest'),'math_context_digest':context.digest,
        'approved_reader_document_hash':approval.get('reader_document_hash'),
        'approved_dependencies':deps,'resources':[],'state':'failed','reason':''}


OUTPUT_LIMIT_HINT = ('上一次请求把输出额度全部用于思考，没有输出任何代码。请缩短推理：'
    '不要逐一推演所有布局或复核每个像素，按冻结需求直接给出一个清楚的方案，'
    '并输出完整 JSON 与完整 Python 代码。')


def _output_limit_failure(exc):
    """True when the model call ended at finish_reason=length with no program."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if getattr(exc, 'failure_kind', None) == 'truncation':
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _output_limit_feedback(row):
    # Rule 2026-10-16: one hinted retry after a capacity failure, inside the
    # existing three-attempt budget. No candidate exists, so nothing else to show.
    return {'problem': row.get('reason'), 'output_limit': True, 'action': OUTPUT_LIMIT_HINT,
            'expected': row['request'].get('acceptance_criteria', [])}


def _feedback(row, include_needs_review=False):
    report=row.get('validation') or {}
    failures=_actionable_failures(row, include_needs_review)
    evidence=[entry for entry in (report.get('evidence_catalog') or {}).values()
        if entry.get('kind')=='rendered_frame' and entry.get('path')]
    frames=[entry['path'] for entry in evidence]
    return {'problem':row.get('reason'),'issues':report.get('issues',[]),
        'invalid_candidate_json':row.get('invalid_candidate_json'),
        'failures':failures,'images':frames,'review_problems':report.get('review_problems',[]),
        **({'image_labels':[entry['image_label'] for entry in evidence]}
           if evidence and all(entry.get('image_label') for entry in evidence) else {}),
        'action':'修改完整 Python 程序，解决运行错误或实际画面问题；不得删请求、改正文、改落点或降低原通过标准。',
        'expected':row['request'].get('acceptance_criteria',[])}


def _actionable_failures(row, include_needs_review=False):
    if substantive_reviews_enabled():
        from .validation import actionable_media_failures
        return actionable_media_failures(row.get('validation') or {}, items=[],
                                         include_needs_review=include_needs_review)
    if reviews_frozen(): return []
    from .review_policy import media_review_policy
    from .validation import actionable_media_failures, media_review_items, result_digest
    report=row.get('validation') or {}
    if report.get('result_hash')!=result_digest(row): return []
    return actionable_media_failures(report,items=media_review_items(row['request'],row,media_review_policy()))


def _unverified_review_without_revision(row):
    report=row.get('validation') or {}
    if substantive_reviews_enabled():
        return bool(report) and not report.get('ok') and not _actionable_failures(row)
    unresolved=(bool(report.get('review_failure')) or any(
        issue.get('code')=='MEDIA_CALL_FAILED' or str(issue.get('code','')).startswith('MEDIA_REVIEW_')
        for issue in report.get('issues',[])) or any(
        verdict.get('status')=='needs_review' for verdict in report.get('verdicts',[])))
    return unresolved and not _actionable_failures(row)


def _failure_kind(exc):
    """The first explicit category along the cause chain (A5), else the generic class."""
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        kind = getattr(current, 'failure_kind', None)
        if isinstance(kind, str) and kind:
            return kind
        current = current.__cause__ or current.__context__
    from ...providers.review_failures import classify_review_failure
    return classify_review_failure(exc)


#: 修改计划 E1/D5：这些审核失败不含任何画面判断，恢复时只补截图与审核，不占新候选。
INCOMPLETE_REVIEW_KINDS = frozenset({'capture', 'network', 'timeout', 'empty_response', 'cancelled'})


def _review_incomplete(row):
    report = row.get('validation')
    if not row.get('resources'):
        return False
    if not report:
        return True
    return bool(report.get('capture_failed')) or (
        (report.get('review_failure') or {}).get('kind') in INCOMPLETE_REVIEW_KINDS)


def _doubt_repair_due(row, history):
    """修改计划 B1：有明确 action 的疑点按原剩余候选返修一次（每项媒体至多 1 次）。

    `needs_review_repair` on an attempt reserves the repair for the next one; a
    reservation on any earlier attempt means the repair was already made.
    """
    return (substantive_reviews_enabled() and bool(history) and len(history) < MAX_CODE_ATTEMPTS
            and not any(a.get('needs_review_repair') for a in history[:-1])
            and bool(_actionable_failures(row, include_needs_review=True)))


def _technical_render_failure(rendered):
    """The worker ran the program and it raised before producing media (D6)."""
    return (rendered.get('status') == 'failed' and not rendered.get('resources')
            and (rendered.get('technical_report') or {}).get('phase') == 'render')


def _technical_error_text(rendered):
    worker = (rendered.get('technical_report') or {}).get('worker') or {}
    parts = [str(rendered.get('reason') or '')]
    for key in ('stderr_tail', 'stderr', 'traceback', 'error'):
        if isinstance(worker.get(key), str) and worker[key].strip():
            parts.append(worker[key])
    return '\n'.join(parts)


def _render_row(base, program, rendered, profile):
    row=deepcopy(base)
    row.update(candidate_spec=deepcopy(program),spec_hash=digest_of(program),
        source_dir=rendered.get('source_dir',''),resources=rendered.get('resources',[]),
        render_profile=profile,state='rendered',reason=rendered.get('reason',''))
    row['code_product']={'schema_version':'GeneratedMediaProductV1',
        'program_hash':digest_of(program),'code_hash':digest_text(program['code']),
        'context_digest':row['math_context_digest'],'backend':program.get('backend','manim'),
        'input_binding_status':'not_run' if reviews_frozen() else 'pass',
        'mathematical_status':'not_run' if reviews_frozen() else 'requires_visual_review',
        'object_bindings':deepcopy(program.get('object_bindings',[])),'steps':deepcopy(program.get('steps',[]))}
    if row['kind']=='animation':
        row['animation']={'schema_version':'1.0','request_id':row['request_id'],
            'status':'needs_review','spec_hash':row['spec_hash'],
            'compiler_hash':rendered['compiler_hash'],'environment_hash':rendered['environment_hash'],
            'resources':deepcopy(row['resources']),'steps':rendered['steps'],
            'math_report':{'status':'needs_review','basis':'approved_inputs_only'},
            'frame_report':None,'technical_report':rendered['technical_report'],
            'source_binding':{'context_digest':row['math_context_digest'],
                'object_bindings':deepcopy(program.get('object_bindings',[]))}}
    else:
        primary=next((r for r in row['resources'] if r['kind']=='svg'),None)
        raster=next((r for r in row['resources'] if r['kind']=='image'),None)
        if not raster: raise ContractError('代码绘图缺实际 PNG')
        identity='FIG-'+row['request_id']
        row.update(figure_id=identity,name=identity,svg=primary['relative_path'] if primary else None,
            screenshot=raster['relative_path'],image=raster['relative_path'] if not primary else None)
        row['entry']={'id':identity,'name':identity,'title':row['request'].get('caption',''),
            'svg':'figures/'+primary['relative_path'] if primary else None,
            'file':'figures/'+raster['relative_path'] if not primary else None,
            'request_id':row['request_id'],'concepts':[],'checks':{'code_rendered':True}}
    return row


def _complete_unreviewed(row):
    """A completed render is usable without claiming a review passed."""
    row.update(state='fulfilled',reason='审核已冻结（json_only）',review_outcome=frozen_review())
    for name in ('validation','review','media_review'):
        row.pop(name,None)
    if row.get('animation'):
        row['animation'].update(status='fulfilled',math_report=None,frame_report=None,
                                review_outcome=frozen_review())
    return row


def _apply_review(row, report):
    row['validation']=report
    row.update(state='fulfilled' if report['ok'] else report['decision'],
        reason='；'.join(issue['message'] for issue in report['issues']),
        review={'ok':report['ok'],'problems':report.get('review_problems',[])})
    if substantive_reviews_enabled():
        row.update(review_outcome=deepcopy(report['review_outcome']), review_mode='balanced',
                   review_status=report['review_outcome'].get('review_status', 'completed'))
        if row.get('animation'):
            row['animation'].update(status=row['state'], math_report=None, frame_report=None,
                                    review_outcome=deepcopy(row['review_outcome']))
        return row
    if row['kind']=='animation' and report['ok']:
        from .evidence_digest import validation_digest
        row['animation'].update(status='fulfilled',math_report=report['math_report'],
            frame_report={'status':'pass','validation_hash':validation_digest(report)})
    return row


def _complete_review(previous, *, request, base, history, content, reviewer, workspace_root,
                     cancel, runtime_context, out_dir, identity):
    """修改计划 D5/E1：已有完整成品而截图或审核未完成时，只补截图和审核，不占新候选。

    A draft animation that now passes continues to its final render with the same
    program, exactly as the first run would have; no model call is made here.
    """
    from .code_runtime import render_generated_code
    from .validation import validate_media, validate_media_report
    row=deepcopy(previous);row['code_attempts']=history
    _apply_review(row,validate_media(request,row,client=reviewer,workspace_root=workspace_root,content=content))
    attempt=history[-1] if history else None
    attempt_dir=Path(attempt['directory']) if attempt and attempt.get('directory') else (
        out_dir/('generation-'+identity[7:23])/f'attempt-{len(history):02d}')
    write_json_atomic(attempt_dir/'resumed-validation.json',row['validation'])
    if row['state']=='fulfilled' and row['kind']=='animation' and row.get('render_profile')=='draft':
        program=row['candidate_spec']
        rendered=render_generated_code(program,context=runtime_context,out_dir=attempt_dir/'final',
            workspace_root=workspace_root,profile='final',cancel=cancel)
        write_json_atomic(attempt_dir/'final-runtime.json',rendered)
        if rendered.get('status')=='cancelled': raise Cancelled(rendered.get('reason') or 'final code render cancelled')
        if rendered.get('status') not in ('ok','rendered','needs_review','fulfilled') or not rendered.get('resources'):
            row.update(state='blocked' if rendered.get('status')=='blocked' else 'failed',
                       reason=rendered.get('reason') or '正式渲染没有可验证媒体资源')
        else:
            row=_render_row({**base,'code_attempts':history},program,rendered,'final')
            _apply_review(row,validate_media(request,row,client=reviewer,workspace_root=workspace_root,content=content))
            write_json_atomic(attempt_dir/'final-validation.json',row['validation'])
    if row['state']=='fulfilled':
        validate_media_report(request,row,row['validation'])
    if attempt is not None:
        attempt.update(state=row['state'],reason=row.get('reason',''),review_resumed=True,
                       render_profile=row.get('render_profile'))
        write_json_atomic(attempt_dir/'attempt.json',attempt)
    row['code_attempts']=deepcopy(history)
    write_json_atomic(out_dir/'result.json',row)
    return row


def execute_code(request, *, content, approval, out_dir, client, reviewer,
                 workspace_root=None, cancel=None, previous=None,
                 repair_context=None, evidence_only=False, dependencies=None):
    """At most three model-written candidates per frozen generation identity.

    Draft pixels are reviewed before a final animation render. Final pixels are
    reviewed again; passing the draft alone never issues a fulfilled result.
    """
    from .code_generation import MAX_TECHNICAL_FIXES, fix_media_code, generate_media_code
    from .code_contracts import BACKENDS, validate_media_code
    from .code_runtime import preflight_generated_code, render_generated_code
    from .validation import validate_media, validate_media_report
    from ...pipeline.services import reraise_control_error
    frozen=reviews_frozen()
    substantive=substantive_reviews_enabled()
    context=ApprovedMathContext.from_content_binding(content,approval,request)
    base=_bound_row(request,content,approval,context)
    base.update(deepcopy(dependencies or {}),repair_tickets=deepcopy(repair_context or []))
    out_dir=Path(out_dir).resolve();out_dir.mkdir(parents=True,exist_ok=True)
    runtime_context={'request':deepcopy(request),'approved_content':deepcopy(content),
        'approved_models':deepcopy(content.get('math_models',[])) if frozen else
            [context.model_by_id(ref['model_id']).as_dict() for ref in request['math_model_refs']]}
    if frozen: runtime_context['review_mode']=current_review_mode()
    identity=digest_of({'request':request,'context':context.digest,
        'generation_dependency_hash':(dependencies or {}).get('generation_dependency_hash'),
        'contract':'GeneratedMediaCodeV1','attempt_limit':MAX_CODE_ATTEMPTS})
    persisted=read_json_soft(out_dir/'result.json')
    from ...pipeline.media_review_recovery import require_compatible_pending_evidence
    # A crash may leave result.json newer than the published stage manifest.
    # Check both before selecting by the new generation identity or writing state.
    if not frozen:
        for prior in (previous,persisted):
            require_compatible_pending_evidence(request,prior,dependencies=dependencies,
                math_context_digest=context.digest)
    if (persisted and persisted.get('code_generation_identity')==identity
            and (not previous or previous.get('code_generation_identity')!=identity
                 or len(persisted.get('code_attempts',[]))>=len(previous.get('code_attempts',[])))):
        previous=persisted
    if previous and previous.get('source_dir'):
        # 修改计划 F1：复制过的运行里，旧绝对路径按 /work/ 之后的部分重新挂载（sha256 一致才接受）。
        from ...core.resource_files import relocated_source_dir
        previous={**previous,'source_dir':str(relocated_source_dir(previous['source_dir'],previous.get('resources') or []))}
    same=bool(previous and previous.get('code_generation_identity')==identity)
    history=deepcopy(previous.get('code_attempts',[])) if same else []
    if history and history[-1].get('state')=='network_paused':
        # Plan N: a network pause is not a candidate. Reuse the same slot and
        # directory; its correction journal resends the unanswered request.
        history.pop()
    if history and history[-1].get('state')=='started':
        # 修改计划 E1：只有预约、没有结果的未知中断。不自动重发同一请求；它计入原候选并在报告中列出。
        history[-1].update(state='interrupted_unknown',
                           reason='上次制作在没有结果时中断（未知中断）；不自动重发同一请求，计入原候选。')
    regenerate_slot=False
    if same and history and history[-1].get('generation_stopped'):
        last=history[-1]
        from .code_generation import TRANSPORT_FAILURE_KINDS, TRANSPORT_MARKERS
        transport_stop=(last.get('failure_kind') in TRANSPORT_FAILURE_KINDS or (not last.get('failure_kind')
            and any(marker in str(last.get('reason') or '') for marker in TRANSPORT_MARKERS)))
        if (last.get('failure_kind')=='empty_response' or transport_stop) and not last.get('resumed_regeneration'):
            # 修改计划 E1：因空答复或网络断流停下的生成（含旧记录：原因里有 IncompleteRead 等）在原槽位续生成一次；
            # 质量或语法用尽仍保持停止。
            journal=Path(last.get('directory') or '')/'code-corrections.json'
            if last.get('directory') and journal.is_file():
                journal.replace(journal.with_name('code-corrections.empty-response.json'))
            history.pop()
            regenerate_slot=True
        else:
            return deepcopy(previous)
    base.update(code_generation_identity=identity,code_attempts=history)
    old_program=deepcopy(previous.get('candidate_spec')) if same else None
    feedback=_feedback(previous) if same else None
    if (same and history and history[-1].get('output_limit') and not previous.get('resources')
            and not any(a.get('output_limit') for a in history[:-1])):
        # 修改计划 E1：上次是长度截断，恢复后仍带提示重试，仍只 1 次、仍计入 3 个候选。
        feedback=_output_limit_feedback(previous)
    if substantive and same and previous.get('resources') and _review_incomplete(previous):
        from ...core.resource_files import relocated_source_dir, verified_resource_file
        try:
            for resource in previous['resources']:
                verified_resource_file(relocated_source_dir(previous['source_dir'], previous['resources']), resource)
        except (ContractError, OSError):
            pass  # Lost bytes are restored below from the frozen program.
        else:
            previous=_complete_review(previous, request=request, base=base, history=history,
                content=content, reviewer=reviewer, workspace_root=workspace_root, cancel=cancel,
                runtime_context=runtime_context, out_dir=out_dir, identity=identity)
            if previous.get('state')=='fulfilled':
                return deepcopy(previous)
            feedback=_feedback(previous)
    doubt_due=same and bool(previous.get('resources')) and _doubt_repair_due(previous, history)
    if doubt_due:
        history[-1]['needs_review_repair']=True
        feedback=_feedback(previous, include_needs_review=True)
    if substantive and same and previous.get('resources') and previous.get('validation'):
        from ...core.resource_files import relocated_source_dir, verified_resource_file
        try:
            for resource in previous['resources']:
                verified_resource_file(relocated_source_dir(previous['source_dir'], previous['resources']), resource)
        except (ContractError, OSError):
            pass  # Restore actual lost bytes below; never reuse a missing file.
        else:
            if (previous.get('state')=='fulfilled' or len(history)>=MAX_CODE_ATTEMPTS
                    or (_unverified_review_without_revision(previous) and not doubt_due)
                    or any(isinstance(p,dict) and p.get('target_artifact_kind')=='content'
                           for p in previous['validation'].get('review_problems',[]))):
                return deepcopy(previous)
    if evidence_only and not frozen:
        if not same or not previous.get('resources'): raise ContractError('evidence-only code reuse has stale authority')
        row=deepcopy(previous)
        for resource in row['resources']:
            path=Path(row['source_dir'])/resource['relative_path']
            if path.is_symlink() or not path.resolve().is_relative_to(Path(row['source_dir']).resolve()) or not path.is_file() or digest_file(path)!=resource['sha256']:
                raise ContractError('evidence-only resource missing or corrupt')
        row.update(base,resources=previous['resources'])
        verify_frozen=getattr(reviewer,'validate_frozen_evidence',None)
        if callable(verify_frozen): verify_frozen(request,row)
        _apply_review(row,validate_media(request,row,client=reviewer,workspace_root=workspace_root))
        if row['state']=='fulfilled' and row['kind']=='animation' and row.get('render_profile')=='draft':
            # Repaired draft evidence authorizes the final render, never release
            # of draft bytes. This continues the same paid code candidate.
            attempt_dir=out_dir/('generation-'+identity[7:23])/f'attempt-{len(history):02d}'
            write_json_atomic(attempt_dir/'draft-revalidation.json',row['validation'])
            row={**deepcopy(base),'render_profile':'final'}
            program=None
            start=time.monotonic()
            try:
                program=validate_media_code(old_program,request,content)
                row.update(candidate_spec=program,spec_hash=digest_of(program))
                if cancel is not None: cancel.checkpoint()
                rendered=render_generated_code(program,context=runtime_context,
                    out_dir=attempt_dir/'final',workspace_root=workspace_root,profile='final',cancel=cancel)
                write_json_atomic(attempt_dir/'final-runtime.json',rendered)
                if rendered.get('status')=='cancelled': raise Cancelled(rendered.get('reason') or 'final code render cancelled')
                if rendered.get('status') not in ('ok','rendered','needs_review','fulfilled') or not rendered.get('resources'):
                    if rendered.get('status')=='blocked': raise CapabilityError(rendered.get('reason') or 'code worker unavailable')
                    raise ContractError(rendered.get('reason') or '代码运行没有可验证媒体资源')
                row=_render_row(base,program,rendered,'final')
                # These final pixels are a new review, outside the recovered
                # frozen draft evidence job and its transport allowance.
                final_reviewer=getattr(reviewer,'after_evidence_client',reviewer)
                report=validate_media(request,row,client=final_reviewer,workspace_root=workspace_root)
                _apply_review(row,report)
                write_json_atomic(attempt_dir/'final-validation.json',report)
                if report['ok']: validate_media_report(request,row,report)
            except Exception as exc:
                # A blocked or interrupted final cannot retain draft authority.
                row={**deepcopy(base),'render_profile':'final',
                    'state':'blocked' if isinstance(exc,CapabilityError) else 'failed',
                    'reason':f'{type(exc).__name__}: {exc}'}
                if program is not None: row.update(candidate_spec=program,spec_hash=digest_of(program))
                row['validation']={'ok':False,'decision':row['state'],'review_problems':[],
                    'issues':[{'code':'MEDIA_RENDER_FAILED','message':row['reason']}]}
                reraise_control_error(exc)
            finally:
                if history:
                    history[-1].update(state=row['state'],reason=row['reason'],render_profile='final',
                        wall_seconds=round(history[-1].get('wall_seconds',0)+time.monotonic()-start,3))
                    write_json_atomic(attempt_dir/'attempt.json',history[-1])
                row['code_attempts']=deepcopy(history)
                write_json_atomic(out_dir/'result.json',row)
            return row
        write_json_atomic(out_dir/'result.json',row)
        return row
    if not frozen and same and previous.get('resources') and _unverified_review_without_revision(previous) and not doubt_due:
        # A repeated entry cannot turn unknown/call failure into another paid
        # program. Evidence-only repair above retains its existing authority.
        return deepcopy(previous)
    row=base
    last_rendered=deepcopy(previous) if same and previous.get('resources') else None
    # A lost/corrupt resource is restored from its frozen program, without another
    # code model call. Keep this separate from semantic code revision budgets.
    restoring=bool(same and previous.get('state')=='fulfilled' and old_program)
    if restoring:
        validate_media_code(old_program,request,content)
        restored=out_dir/('generation-'+identity[7:23])/'restored'
        rendered=render_generated_code(old_program,context=runtime_context,out_dir=restored,
            workspace_root=workspace_root,profile='final',cancel=cancel)
        if rendered.get('status') not in ('ok','rendered','needs_review','fulfilled') or not rendered.get('resources'):
            row.update(candidate_spec=old_program,spec_hash=digest_of(old_program),state='failed',
                reason=rendered.get('reason') or '冻结代码资源重建失败')
        else:
            row=_render_row(base,old_program,rendered,'final')
            if frozen and not substantive: _complete_unreviewed(row)
            else: _apply_review(row,validate_media(request,row,client=reviewer,workspace_root=workspace_root,content=content))
        write_json_atomic(out_dir/'result.json',row);return row
    # These are generation attempts, not worker subprocess attempts. An exhausted
    # failure survives coordinator re-entry, avoiding hidden infinite paid loops.
    if len(history)>=MAX_CODE_ATTEMPTS:
        if substantive:
            return deepcopy(previous)
        row=deepcopy(previous);row.update(state='needs_review',reason='本请求已达到三次代码制作上限；保留全部失败与源码，需显式重启制作。')
        return row
    # Test the installed backend with host-owned code before buying a candidate.
    # A replayed review needs no renderer; an exhausted request buys nothing.
    try:
        preflight=preflight_generated_code(BACKENDS[request['kind']],kind=request['kind'],
            workspace_root=workspace_root,cancel=cancel)
        write_json_atomic(out_dir/'preflight.json',preflight)
    except CapabilityError as exc:
        write_json_atomic(out_dir/'preflight.json',{**deepcopy(getattr(exc,'report',{})),'ok':False,
            'backend':BACKENDS[request['kind']],'reason':str(exc)})
        row=deepcopy(previous) if same else deepcopy(base)
        row.update(state='blocked',reason='媒体运行环境预检失败：'+str(exc))
        write_json_atomic(out_dir/'result.json',row)
        return row
    while len(history)<MAX_CODE_ATTEMPTS:
        if cancel is not None: cancel.checkpoint()
        index=len(history)+1;attempt_dir=out_dir/('generation-'+identity[7:23])/f'attempt-{index:02d}'
        attempt_dir.mkdir(parents=True,exist_ok=True);start=time.monotonic()
        attempt={'index':index,'state':'started','directory':str(attempt_dir)}
        if regenerate_slot:
            attempt['resumed_regeneration']=True;regenerate_slot=False
        history.append(attempt)
        row=deepcopy(base);row['code_attempts']=history
        write_json_atomic(out_dir/'result.json',row)
        try:
            program=generate_media_code(request,content=content,approval=approval,client=client,
                feedback=feedback,previous=old_program,
                correction_journal=attempt_dir/'code-corrections.json')
            old_program=program
            write_json_atomic(attempt_dir/'program.json',program)
            (attempt_dir/'generated.py').write_text(program['code'],encoding='utf-8')
            write_json_atomic(attempt_dir/'context.json',runtime_context)
            row.update(candidate_spec=program,spec_hash=digest_of(program))
            # A static Figure is inexpensive and uses the final display directly.
            profiles=('draft','final') if request['kind']=='animation' and not frozen else ('final',)
            for profile in profiles:
                if cancel is not None: cancel.checkpoint()
                rendered=render_generated_code(program,context=runtime_context,
                    out_dir=attempt_dir/profile,workspace_root=workspace_root,profile=profile,cancel=cancel)
                write_json_atomic(attempt_dir/(profile+'-runtime.json'),rendered)
                while (_technical_render_failure(rendered)
                       and sum(int(a.get('technical_fixes',0)) for a in history)<MAX_TECHNICAL_FIXES):
                    # 修改计划 D6：出图前的技术异常先做轻量修复，不新开候选；每项媒体合计至多 2 次。
                    fix_index=int(attempt.get('technical_fixes',0))+1
                    attempt['technical_fixes']=fix_index
                    error_text=_technical_error_text(rendered)
                    attempt.setdefault('technical_fix_log',[]).append({'index':fix_index,'profile':profile,
                        'error_tail':error_text[-500:]})
                    write_json_atomic(attempt_dir/f'program-before-fix-{fix_index}.json',program)
                    try:
                        fixed=fix_media_code(request,content=content,approval=approval,client=client,
                            program=program,error=error_text,
                            correction_journal=attempt_dir/f'technical-fix-{fix_index}.json')
                    except Exception as fix_exc:
                        reraise_control_error(fix_exc)
                        attempt['technical_fix_log'][-1]['fix_failed']=f'{type(fix_exc).__name__}: {fix_exc}'
                        break
                    program=old_program=fixed
                    write_json_atomic(attempt_dir/'program.json',program)
                    (attempt_dir/'generated.py').write_text(program['code'],encoding='utf-8')
                    row.update(candidate_spec=program,spec_hash=digest_of(program))
                    if cancel is not None: cancel.checkpoint()
                    rendered=render_generated_code(program,context=runtime_context,
                        out_dir=attempt_dir/f'{profile}-fix-{fix_index}',workspace_root=workspace_root,
                        profile=profile,cancel=cancel)
                    write_json_atomic(attempt_dir/f'{profile}-fix-{fix_index}-runtime.json',rendered)
                if rendered.get('status') not in ('ok','rendered','needs_review','fulfilled') or not rendered.get('resources'):
                    if rendered.get('status')=='blocked': raise CapabilityError(rendered.get('reason') or 'code worker unavailable')
                    raise ContractError(rendered.get('reason') or '代码运行没有可验证媒体资源')
                row=_render_row({**base,'code_attempts':history},program,rendered,profile)
                if frozen and not substantive:
                    _complete_unreviewed(row)
                    continue
                report=validate_media(request,row,client=reviewer,workspace_root=workspace_root,content=content)
                _apply_review(row,report)
                if substantive: last_rendered=deepcopy(row)
                write_json_atomic(attempt_dir/(profile+'-validation.json'),report)
                if not report['ok']: break
            attempt.update(state=row['state'],reason=row['reason'],wall_seconds=round(time.monotonic()-start,3),
                program_hash=digest_of(program),render_profile=row.get('render_profile'))
            # History is audit metadata and is frozen before the last validation
            # receipt is issued, not changed underneath its result hash.
            row['code_attempts']=deepcopy(history)
            if row['state']=='fulfilled':
                # Attempt telemetry is excluded from pixel authority; see validator.
                if not frozen: validate_media_report(request,row,row['validation'])
                write_json_atomic(out_dir/'result.json',row);return row
            if row['state']=='blocked' or any(p.get('target_artifact_kind')=='content' for p in (row.get('validation') or {}).get('review_problems',[]) if isinstance(p,dict)):
                break
            if _unverified_review_without_revision(row):
                # Unknown and failed calls contain no program defect. A valid
                # visual failure can still use the remaining candidate budget.
                # A needs_review verdict with a concrete fix gets one repair per
                # resource, inside the same three-attempt budget.
                if (substantive and len(history)<MAX_CODE_ATTEMPTS
                        and not any(a.get('needs_review_repair') for a in history)
                        and _actionable_failures(row, include_needs_review=True)):
                    attempt['needs_review_repair']=True
                    feedback=_feedback(row, include_needs_review=True)
                    continue
                break
            feedback=_feedback(row)
        except Exception as exc:
            from ...pipeline.network_gate import NetworkPaused
            if isinstance(exc,NetworkPaused):
                attempt.update(state='network_paused',reason=str(exc))
            reraise_control_error(exc)
            candidate=getattr(exc,'candidate',None)
            if candidate is not None:
                write_json_atomic(attempt_dir/'invalid-program.json',candidate)
                old_program=candidate
            # Never let a failed final render retain an accepted draft receipt.
            row={**deepcopy(base),'code_attempts':history}
            if old_program is not None:
                try:
                    validated=validate_media_code(old_program,request,content)
                    row.update(candidate_spec=validated,spec_hash=digest_of(validated))
                except ContractError:
                    # Invalid model metadata cannot enter host receipt fields
                    # (for example fabricated timestamp/status keys).
                    raw=json.dumps(old_program,ensure_ascii=False,sort_keys=True)
                    row.update(invalid_candidate_json=raw,spec_hash=digest_text(raw))
                    if not isinstance(old_program,dict): old_program=None
            row.update(state='blocked' if isinstance(exc,CapabilityError) else 'failed',
                reason=f'{type(exc).__name__}: {exc}')
            attempt.update(state=row['state'],reason=row['reason'],wall_seconds=round(time.monotonic()-start,3))
            if (_output_limit_failure(exc) and len(history)<MAX_CODE_ATTEMPTS
                    and not any(a.get('output_limit') for a in history[:-1])):
                attempt['output_limit']=True
                feedback=_output_limit_feedback(row)
                continue
            if _output_limit_failure(exc):
                attempt['output_limit']=True
            if getattr(exc,'generation_stopped',False):
                attempt['generation_stopped']=True
                # 修改计划 E1：持久化停止原因，恢复时据此决定是否在原槽位续生成。
                attempt['failure_kind']=_failure_kind(exc)
            feedback=_feedback(row)
            if isinstance(exc,CapabilityError) or getattr(exc,'generation_stopped',False): break
        finally:
            row['code_attempts']=deepcopy(history)
            write_json_atomic(attempt_dir/'attempt.json',attempt)
            write_json_atomic(out_dir/'result.json',row)
    if substantive and not row.get('resources') and last_rendered:
        failure=row.get('reason','')
        row=deepcopy(last_rendered)
        row.update(state='needs_review',code_attempts=deepcopy(history),
                   reason=row.get('reason','')+'；后续制作未完成：'+failure)
        row['review_outcome'].setdefault('reasons',[]).append('后续制作未完成：'+failure)
        write_json_atomic(out_dir/'result.json',row)
    return row
