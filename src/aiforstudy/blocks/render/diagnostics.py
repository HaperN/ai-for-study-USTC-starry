"""Immutable teaching and diagnostic reader snapshots with honest review status."""
from __future__ import annotations
from copy import deepcopy
from html import escape
import json
from pathlib import Path
import re
import tempfile

from ...core.digest import digest_of, digest_file
from ...core.jsonio import read_json, write_json_atomic
from ...core.review_mode import reviews_frozen, frozen_review, substantive_reviews_enabled, current_review_mode
from ...errors import ContractError, ConfigError
from ..content.reader_document import project_reader
from ..media.media_requirements import content_hash, resolve_bound_placement
from .layout import layout_from_reader_media
from ..content.review_outcome import outcome, inherit_outcome, aggregate_outcomes, media_outcome


def processing_record(state, unit):
    from .readiness import lesson_failure_records
    repair=(state.get('repair_states') or {}).get(unit) or {}
    record={key:deepcopy(value) for key,value in repair.items()
            if key in {'content_rounds_used','content_rounds_by_artifact','content_attempts_by_artifact',
                       'evidence_attempts_by_verdict','history','stopped_reason'}}
    failures=lesson_failure_records(state,unit)
    if failures:
        # These causes are displayed in the page, so delivery/resume must also
        # invalidate their old snapshot when the unit's actual cause changes.
        record['lesson_failures']=failures
    return record


def _input(run: Path, relative: str, sources: dict, *, optional=False) -> dict:
    path = run / relative
    if not path.resolve().is_relative_to(run) or any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(run)):
        sources[relative] = 'rejected-path'
        raise ContractError('diagnostic input escapes run')
    if optional and not path.exists():
        sources[relative] = 'missing'
        return {}
    # Invalid JSON is still a distinct frozen input; record bytes before parsing.
    sources[relative] = digest_file(path) if path.is_file() else 'missing'
    return read_json(path)


def _reader_structure_tree(tree: list[dict], structure: dict) -> list[dict]:
    """Add existing textbook ancestors without adding unselected lessons."""
    records = {row['unit_key']: row for row in structure.get('units', [])
               if isinstance(row, dict) and row.get('unit_key')}
    if not records:
        return tree
    selected = {row['id']: row for row in tree}
    order = {key: index for index, key in enumerate(records)}
    nodes = {}; roots = []
    for unit in sorted(selected, key=lambda key: order.get(key, len(order))):
        path = []; seen = set(); key = unit
        while key in records and key not in seen:
            seen.add(key); path.append(records[key]); key = records[key].get('parent_unit_key')
        if not path:
            roots.append(deepcopy(selected[unit]))
            continue
        parent = None
        for row in reversed(path):
            key = row['unit_key']
            if key not in nodes:
                original = selected.get(key, {})
                node = {'id': key, 'title': row.get('title') or original.get('title') or key,
                        'unit': bool(original.get('unit')), 'children': [],
                        'anchor': row.get('display_anchor') or '',
                        'level': {'chapter': 1, 'section': 2, 'subsection': 3}.get(row.get('level'))}
                nodes[key] = node
                (parent['children'] if parent is not None else roots).append(node)
            parent = nodes[key]
    return roots


def _resource(run: Path, row: dict, resource: dict, sources: dict) -> Path:
    from ...core.resource_files import relocated_source_dir
    root = relocated_source_dir(row.get('source_dir'), row.get('resources') or [], run_root=run)  # 修改计划 F1
    if not root.is_absolute(): root = run / root
    relative = Path(resource.get('relative_path') or '')
    path = root / relative
    if relative.is_absolute() or '..' in relative.parts or not path.resolve().is_relative_to(run):
        raise ContractError('diagnostic media path escapes run')
    if any(p.is_symlink() for p in [path,*path.parents] if p.is_relative_to(run)):
        raise ContractError('diagnostic media missing or symlink')
    # Rejected/missing bytes affect the preview too, even if the manifest is unchanged.
    sources[str(path.relative_to(run))] = digest_file(path) if path.is_file() else 'missing'
    if not path.is_file(): raise ContractError('diagnostic media missing or symlink')
    if not reviews_frozen() and resource.get('sha256') != sources[str(path.relative_to(run))]: raise ContractError('diagnostic media hash mismatch')
    return path


def _implementation_inputs(renderer) -> dict:
    """Conservative local closure of projection, validation and resource readers.

    These source trees cover transitive schema/validator imports. This deliberately
    invalidates more often than a hand-maintained list of individual functions.
    Runner/cache identities are a separate production boundary.
    """
    package = Path(__file__).resolve().parents[2]
    roots = ('blocks', 'contracts', 'core', 'checks', 'delivery')
    files = {str(p.relative_to(package)): p for root in roots for p in (package/root).rglob('*')
             if p.is_file() and p.suffix in ('.py', '.cjs')}
    files.update({'errors.py':package/'errors.py', 'pipeline/stage_io.py':package/'pipeline/stage_io.py',
                  'legacy/build.py':Path(renderer.__file__)})
    from ...pipeline.stage_io import _legacy
    model_root = Path(_legacy('model_generate').__file__).parent
    for path in model_root.glob('*.py'):
        files['legacy/model/'+path.name] = path
    files['legacy/lib/files.py'] = Path(renderer.__file__).resolve().parents[2]/'lib/files.py'
    # The Markdown projector can resolve a different installed asset root.
    from .layout import _assets
    root = _assets()
    for relative in ('assets/reader-markdown.js','vendor/marked.min.js'):
        files['layout/'+relative] = root/relative
    return {name:digest_file(path) for name,path in sorted(files.items())}


def _existing_snapshot(output: Path, identity: str) -> dict:
    """Never heal or overwrite an already published URL, including corruption."""
    try:
        if output.is_symlink(): raise ContractError('diagnostic snapshot is a symlink')
        report = read_json(output/'diagnostic-preview.json')
        mode=report.get('render_mode')
        expected_preview=mode=='diagnostic'
        expected_accepted=(mode=='teaching' and report.get('review_mode')!='balanced'
            and report.get('review_outcome',{}).get('status')=='verified' and not report.get('missing'))
        if (mode not in {'diagnostic','teaching'} or report.get('snapshot_hash') != identity
                or report.get('preview_only') is not expected_preview
                or report.get('production_accepted') is not expected_accepted or report.get('output_dir') != str(output)):
            raise ContractError('diagnostic snapshot identity mismatch')
        expected = {'diagnostic-preview.json'}
        for row in report.get('resources') or []:
            relative = Path(row['path']); path = output/relative
            if relative.is_absolute() or '..' in relative.parts or str(relative) in expected:
                raise ContractError('diagnostic snapshot resource path invalid')
            expected.add(str(relative))
            if (any(p.is_symlink() for p in [path,*path.parents] if p.is_relative_to(output))
                    or not path.is_file() or digest_file(path) != row.get('sha256')):
                raise ContractError('diagnostic snapshot resource integrity mismatch')
        actual = {str(p.relative_to(output)) for p in output.rglob('*') if p.is_file()}
        if actual != expected or 'index.html' not in expected or 'data/data.js' not in expected:
            raise ContractError('diagnostic snapshot inventory mismatch')
        return report
    except (OSError,ValueError,KeyError,ConfigError) as exc:
        raise ContractError('diagnostic snapshot cannot be reused: '+str(exc)) from exc


def validate_snapshot_dependencies(snapshot: dict) -> bool:
    """A cached chapter also depends on current renderer code and packaged assets."""
    from ...pipeline.stage_io import _legacy
    renderer=_legacy('build')
    path=Path(snapshot['output_dir'])/'data/data.js'
    try:
        data=json.loads(path.read_text(encoding='utf-8').split('window.__DATA__ = ',1)[1].rsplit(';',1)[0])
    except (OSError,ValueError,IndexError) as exc:
        raise ContractError('reader data envelope invalid') from exc
    files=renderer.reader_site_inputs(data)
    actual={'reader_inputs':{name:digest_file(path) if path.is_file() else 'missing' for name,path in sorted(files.items())},
            'implementation':_implementation_inputs(renderer)}
    if actual!=snapshot.get('dependencies'):
        raise ContractError('reader renderer or packaged asset dependencies changed')
    return True



def _check_animation_bytes(animation, sources, *, book_id, unit, profile):
    """Validate actual playable resources without creating any output files."""
    from ...checks.offline import check_animation_video, check_webvtt
    from ...delivery.resources import ANIMATION_REQUIRED, AnimationManifest, mime_for
    from PIL import Image
    manifest=AnimationManifest.from_result(animation,sources=sources,book_id=book_id,unit_id=unit)
    by_kind={}
    paths=set()
    for entry in manifest.entries:
        if entry.relative_path in paths:
            raise ContractError('diagnostic animation contains duplicate resource paths')
        paths.add(entry.relative_path)
        by_kind.setdefault(entry.kind,[]).append(entry)
        if entry.mime!=mime_for(entry.relative_path):
            raise ContractError('diagnostic animation resource MIME mismatch')
    for kind in ANIMATION_REQUIRED:
        if len(by_kind.get(kind,[]))!=1:
            raise ContractError('diagnostic animation requires exactly one '+kind)
    for kind in ('poster','keyframe'):
        for entry in by_kind.get(kind,[]):
            with Image.open(sources[entry.relative_path]) as image:
                if image.format!='PNG': raise ContractError('diagnostic animation image must be PNG')
                image.verify()
    video=check_animation_video(sources[by_kind['mp4'][0].relative_path],profile=profile)
    if not video.evidence.get('decode_ok') or (video.evidence.get('video') or {}).get('codec_name')!='h264':
        raise ContractError('diagnostic animation cannot decode safe H.264 video: '+video.detail)
    captions=sources[by_kind['captions'][0].relative_path]
    check=check_webvtt(captions)
    if not check.ok: raise ContractError('diagnostic captions invalid: '+check.detail)
    captions_text=captions.read_text(encoding='utf-8')
    transcript=sources[by_kind['transcript'][0].relative_path].read_text(encoding='utf-8')
    return captions_text, transcript, video


def _animation_resources(output: Path, *, unit: str, book_id: str,
                         animation: dict, sources: dict, profile: str, diagnostic: bool) -> dict:
    """Technical assembly shared by two callers; this never grants approval."""
    from ...delivery.resources import ANIMATION_REQUIRED, collect_animation_resources
    from ...errors import CapabilityError
    base='assets/animations/'+('diagnostic-' if diagnostic else 'teaching-')+digest_of({'book':book_id,'unit':unit,
        'request':animation.get('request_id'),'spec':animation.get('spec_hash'),
        'resources':animation.get('resources')},check=False)[7:31]+'/'
    payload={'schema_version':'DiagnosticAnimationV1' if diagnostic else 'ReaderAnimationV1',
        'preview_only':diagnostic,'production_accepted':False,
        'unit_id':unit,'book_id':book_id,'request_id':animation.get('request_id'),
        'status':animation.get('status'),'base':base,'resources':{},'resource_manifest':[],
        'missing':list(ANIMATION_REQUIRED),'notes':['诊断资源投影；未授予数学或画面批准。'] if diagnostic else [],
        'steps':deepcopy(animation.get('steps') or []),'transcript_text':''}
    if reviews_frozen():
        # Assemble available files without granting technical or quality approval.
        import shutil
        from ...delivery.resources import check_relative_path, mime_for
        for resource in animation.get('resources') or []:
            relative=check_relative_path(resource['relative_path'])
            origin=sources.get(relative)
            if origin is None or not Path(origin).is_file(): continue
            destination=output/base/relative
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(origin,destination)
            entry={**resource,'relative_path':relative,'sha256':digest_file(destination),
                   'bytes':destination.stat().st_size,'mime':mime_for(relative)}
            payload['resource_manifest'].append(entry)
            payload['resources'].setdefault(entry['kind'],entry)
            if entry['kind']=='transcript':
                payload['transcript_text']=destination.read_text(encoding='utf-8',errors='replace')
            elif entry['kind']=='captions' and destination.stat().st_size<=64*1024:
                payload['captions_text']=destination.read_text(encoding='utf-8',errors='replace')
        payload['missing']=[kind for kind in ANIMATION_REQUIRED if kind not in payload['resources']]
        payload['technical_checks']={'status':'not_run','review_mode':current_review_mode()}
        payload['review_outcome']=frozen_review()
        return payload
    try:
        captions_text, transcript, video = _check_animation_bytes(
            animation, sources, book_id=book_id, unit=unit, profile=profile)
        collected=collect_animation_resources(results=[animation],target_root=output/base,
            sources=sources,book_id=book_id,unit_id=unit)
        payload['resource_manifest']=[entry.as_dict() for entry in collected.entries]
        for entry in collected.entries:
            payload['resources'].setdefault(entry.kind,entry.as_dict())
        payload.update(missing=[],transcript_text=transcript,
            technical_checks={'decode_ok':True,'captions_valid':True,
                'profile_problems':video.evidence.get('profile_problems') or []})
        if len(captions_text.encode('utf-8'))<=64*1024:
            payload['captions_text']=captions_text
    except (ContractError,CapabilityError,OSError,ValueError,KeyError) as exc:
        payload['notes'].append(str(exc))
    return payload


def _attach_diagnostic_animation(output: Path, *, unit: str, book_id: str,
                                 animation: dict, sources: dict, profile: str) -> dict:
    return _animation_resources(output,unit=unit,book_id=book_id,animation=animation,
                                sources=sources,profile=profile,diagnostic=True)


def _attach_production_animation(output: Path, *, unit: str, book_id: str,
                                animation: dict, sources: dict, profile: str,
                                request: dict, row: dict, review: dict) -> dict:
    """Consume a current final result and real review evidence, without promotion.

    Unverified, technically usable bytes remain visible with no approval. Failed
    and missing media never enter this assembler. A verified label alone cannot
    grant acceptance: the request-bound validation receipt is checked again here.
    """
    if reviews_frozen():
        payload=_animation_resources(output,unit=unit,book_id=book_id,animation=animation,
            sources=sources,profile=profile,diagnostic=False)
        if substantive_reviews_enabled():
            payload['review_outcome']=deepcopy(review)
        return payload
    if profile!='final' or row.get('request_hash')!=digest_of(request):
        raise ContractError('生产动画需要当前请求的 final 资源')
    if review.get('status') in {'known_error','missing'}:
        raise ContractError('确定错误或缺失动画不能进入生产装配')
    accepted=False
    if review.get('status')=='verified':
        from ..media.validation import validate_media_report
        validate_media_report(request,row,row.get('validation'))
        accepted=row.get('state')=='fulfilled' and animation.get('status')=='fulfilled'
    payload=_animation_resources(output,unit=unit,book_id=book_id,animation=animation,
        sources=sources,profile=profile,diagnostic=False)
    payload['review_outcome']=deepcopy(review)
    # Approval evidence includes physical video duration/sample timestamps;
    # preserve these exact values instead of treating them as runtime clocks.
    payload['validation_hash']=digest_of(row.get('validation') or {},check=False)
    payload['production_accepted']=bool(accepted and not payload['missing'])
    return payload


def build_reader_snapshot(run_dir: Path, *, unit_ids: list[str], out_dir: Path, parse_root: Path | None = None,
                          require_complete_units: bool = False) -> dict:
    """The single production/preview projection, preserving verification uncertainty."""
    frozen=reviews_frozen()
    substantive=substantive_reviews_enabled()
    run = Path(run_dir).resolve(); destination = Path(out_dir).resolve()
    if destination == run or (destination.is_relative_to(run) and not any(
            destination.is_relative_to(p) for p in (run/'diagnostics', run/'work/reader-snapshots'))):
        raise ContractError('diagnostic output must not overwrite source run')
    if not unit_ids or len(set(unit_ids)) != len(unit_ids) or any(not re.fullmatch(r'[A-Za-z0-9_-]+',u) for u in unit_ids):
        raise ContractError('diagnostic unit selection invalid')
    sources = {}; state = _input(run,'state.json',sources)
    # Execution state changes after rendering; only immutable product identities
    # and actual resource bytes form the reader's content dependency closure.
    sources.pop('state.json', None)
    from ...pipeline.stage_io import _legacy
    renderer = _legacy('build')
    data = {'book':str(state.get('book_id') or '教材'), 'scope':list(unit_ids), 'units':{},'figures':{},
            'concepts':{},'diagrams':{},'models':{},'tree':[],'toc_gaps':[],'concept_figures':{},
            'number_refs':{},'animations':{},'counts':{'canvas_models':0},'preview_only':not require_complete_units,'production_accepted':False}
    missing=[]; problems=[]; placements=[]; animations=[]; outcomes=[]; theorem_rows=[]
    for unit in unit_ids:
        def product(stage, optional=False):
            return _input(run,f'work/products/{stage}/{unit}.json',sources,optional=optional).get('payload') or {}
        media_input_outcomes=[]
        def media_product(stage):
            # Broken media envelopes must not turn intact text into "missing".
            try:
                payload=product(stage,True)
                if not isinstance(payload,dict): raise ContractError('媒体产物必须是对象')
                return payload
            except (ContractError,ConfigError,OSError,ValueError,AttributeError) as exc:
                reason='媒体产物无法读取：'+str(exc)
                problem={'unit_id':unit,'kind':stage,'reason':reason}
                problems.append(problem);missing.append(deepcopy(problem))
                media_input_outcomes.append(outcome('missing',[reason]))
                return {}
        content_state = outcome('missing', ['尚无当前正文'])
        try:
            lesson=product('lesson'); content=lesson.get('content') or {}
            ch=content_hash(content)
            if not frozen and (content.get('unit_id')!=unit or lesson.get('content_hash')!=ch):
                raise ContractError('reader content is not the recorded current snapshot')
            if lesson.get('content_ref') and not frozen:
                from ...contracts.artifact import ArtifactRef
                ref=ArtifactRef.from_dict(lesson['content_ref'])
                if not Path(ref.path).resolve().is_relative_to(run):
                    raise ContractError('reader content revision outside current run')
                ref.verify()
                if _input(run,str(Path(ref.path).relative_to(run)),sources)!=content:
                    raise ContractError('reader lesson differs from immutable content revision')
            from ..content.review_outcome import validate_content_identity
            current_identity=lesson.get('content_identity')
            if current_identity is not None and not frozen:
                validate_content_identity(content,current_identity)
            # Review evidence can be absent, stale or damaged independently of
            # an intact current lesson. Never reuse its approval or source catalog.
            try:
                audit=product('text_audit',True)
                if not isinstance(audit,dict): raise ContractError('正文审核产物必须是对象')
                if substantive and audit.get('content_hash')!=ch:
                    raise ContractError('正文审核记录对应旧版或未记录版本；当前正文尚未核实')
                if not frozen and audit.get('content_hash') not in (None,ch):
                    raise ContractError('reader audit content binding mismatch')
                if audit.get('content_identity') and not frozen:
                    validate_content_identity(content,audit['content_identity'])
                reviewed=audit.get('review_document') or {}
                if not isinstance(reviewed,dict): raise ContractError('正文审核文档必须是对象')
                content_state=deepcopy(audit.get('review_outcome') or outcome('unverified', ['尚无完整正文审核证据']))
                if not isinstance(content_state,dict) or content_state.get('status') not in {'verified','unverified','known_error','missing'}:
                    raise ContractError('正文审核状态无效')
            except (ContractError,ConfigError,OSError,ValueError,TypeError,KeyError,AttributeError) as exc:
                reason='正文审核证据不可用：'+str(exc)
                problems.append({'unit_id':unit,'kind':'text_audit','reason':reason})
                content_state=outcome('unverified',[reason])
                audit={};reviewed={}
            if substantive and audit.get('content_hash') not in (None,ch):
                # 修改计划 B5：审核针对旧版本正文时，不把它当作当前结论；旧结论作为历史保留。
                stale=content_state
                content_state=outcome('unverified',['当前版本正文尚未审核（已有审核针对旧版本）'])
                content_state['history']=[stale]
            body=content.get('body_markdown')
            if substantive and (not isinstance(body,str) or not body.strip()):
                content_state=outcome('missing',['正文为空'])
            current_identity=current_identity or audit.get('content_identity') or {}
            if frozen and not substantive: content_state=frozen_review()
            local_failures=[] if frozen else (lesson.get('checks') or {}).get('blocking_issues') or []
            if local_failures and content_state['status']!='verified':
                content_state=outcome('known_error',[str(row.get('problem') or row.get('message') or row) for row in local_failures])
            manifest=media_product('figures')
            media_audit=media_product('audit')
            from ..audit.contracts import validate_ticket
            for raw in ([] if frozen else [*(manifest.get('tickets') or []),*(media_audit.get('tickets') or [])]):
                try:
                    ticket=validate_ticket(raw)
                except ContractError:
                    continue
                if (ticket.is_blocking and ticket.target.get('rule')=='POST_MEDIA_CONTENT'
                        and ticket.target.get('artifact_hash')==ch):
                    content_state=outcome('known_error',[ticket.problem],upstream=[content_state])
            catalog=current_identity.get('reference_catalog') or reviewed.get('reference_catalog') or {}
            from ..content.concept_corrections import project_current_catalog, catalog_review_outcome
            current_catalog = project_current_catalog(run/'work/products', catalog,
                read=lambda relative:_input(run,'work/products/'+relative,sources,optional=True))
            if current_catalog != catalog:
                content_state = catalog_review_outcome(content,current_catalog,audit,content_state)
            catalog = current_catalog
            reader=project_reader(content,catalog).as_dict()
            if content_state.get('status')=='verified' and not substantive:
                from ..content.review_outcome import verify_content_audit
                try:
                    verify_content_audit(content,audit)
                except (ContractError,TypeError,KeyError) as exc:
                    content_state=outcome('unverified', ['批准证据无效：'+str(exc)])
            reqs=media_product('media_plan')
            if require_complete_units:
                if lesson.get('content_ref') and not frozen:
                    head=_input(run,f'work/products/content/{unit}/current.json',sources)
                    if head.get('content_ref')!=lesson['content_ref'] or head.get('content_hash')!=ch:
                        raise ContractError('正文产物不是当前内容版本')
        except (ContractError,ConfigError,OSError,ValueError,TypeError,KeyError,AttributeError) as exc:
            problem={'unit_id':unit,'kind':'content','reason':str(exc)};problems.append(problem)
            missing.append(deepcopy(problem))
            content_state=outcome('missing',[str(exc)],upstream=[content_state],attempts=content_state.get('attempts'))
            outcomes.append(content_state)
            data['units'][unit]={'unit_id':unit,'title':unit,'body_markdown':'本单元无法生成诊断正文，请查看输入问题。',
                                'review_outcome':content_state,'diagnostic_problems':[problem]}
            data['tree'].append({'id':unit,'title':unit+'（输入异常）','unit':True,'children':[]})
            continue
        theorem_rows.append({'content':content,'reader_document':reader,
                             'reference_catalog':catalog,'review_outcome':content_state})
        media=[]; unit_outcomes=[content_state,*media_input_outcomes]
        media_review_state=media_audit.get('review_outcome') or outcome('unverified',['尚无完整媒体验收证据'])
        if require_complete_units and not frozen:
            # Media admission is independent of text availability. Stale or
            # missing media is hidden while the current reader document remains.
            try:
                from .readiness import validate_media_bindings
                validate_media_bindings(content, reqs, manifest)
                if lesson.get('content_ref') and not frozen:
                    author_ref=ArtifactRef.from_dict(head.get('author_media_ref') or {})
                    author_path=Path(author_ref.path)
                    if not author_path.resolve().is_relative_to(run):
                        raise ContractError('作者媒体需求版本不在当前运行')
                    author=_input(run,str(author_path.relative_to(run)),sources)
                    author_ref.verify()
                    if head.get('author_media_hash')!=digest_of(author) or reqs.get('author_media_hash')!=digest_of(author):
                        raise ContractError('媒体计划不是当前作者媒体需求版本')
                unit_outcomes.append(reqs['intent_review']['review_outcome'])
                if media_review_state.get('status')=='verified':
                    from ..media.media_manifest import approve_media
                    expected,_=approve_media(reqs,manifest,current_identity)
                    if media_audit.get('approval')!=expected or expected.get('schema_version')!='MediaApprovalV3':
                        raise ContractError('媒体验收缺当前 MediaApprovalV3 或与实际证据不一致')
                unit_outcomes.append(media_review_state)
            except (ContractError,ConfigError,OSError,ValueError,KeyError) as exc:
                # R1/R9：正文第一道审核没过时**本来就不做媒体**。此时"没有媒体计划"
                # 是正常状态，不是缺产物 —— 记成缺产物会把整章 `partial` 拉成 True、
                # 生产验收被否，而读者拿到的是"正文照常展示 + 明确未核实"（R9 要的）。
                if content_state.get('status')=='verified':
                    problem={'unit_id':unit,'kind':'media_plan','reason':str(exc)}
                    problems.append(problem); missing.append(deepcopy(problem))
                else:
                    problems.append({'unit_id':unit,'kind':'media_plan',
                        'reason':'正文未通过第一道审核，按 R1 不做媒体（正文照常展示）'})
                media_review_state=outcome('unverified',[str(exc)])
                unit_outcomes.append(media_review_state)
                reqs={}; manifest={}
        if not reqs or (not frozen and reqs.get('content_hash')!=ch) or not isinstance(reqs.get('requests'),list):
            # R1/R9：正文第一道审核没过时**本来就不做媒体** —— 这是正常状态，
            # 不该记成"缺产物"，否则整章 partial=True、生产验收被否，
            # 而读者其实拿到的是"已渲染的正文 + 明确未核实"（正是 R9 要的）。
            # 只有"正文已过审却没有媒体计划"才算缺产物。
            content_passed = content_state.get('status')=='verified'
            if content_passed:
                reason='媒体计划缺失或未绑定当前正文；相关媒体和交互不可用'
                problem={'unit_id':unit,'kind':'media_plan','reason':reason}
                problems.append(problem);missing.append(deepcopy(problem));unit_outcomes.append(outcome('missing',[reason]))
            elif not frozen:
                problems.append({'unit_id':unit,'kind':'media_plan',
                    'reason':'正文未通过第一道审核，按 R1 不做媒体（正文照常展示）'})
            reqs={};manifest={}
        elif not frozen and manifest and (manifest.get('content_hash')!=ch or manifest.get('requirements_hash')!=digest_of(reqs)):
            problems.append({'unit_id':unit,'kind':'media','reason':'媒体清单与当前请求不匹配，资源暂不展示'})
            manifest={}
        rows={r['request_id']:r for r in manifest.get('requests') or [] if isinstance(r,dict) and r.get('request_id')}
        if not frozen and content_state.get('status')!='verified':
            problems.append({'unit_id':unit,'kind':'content','reason':'；'.join(content_state.get('reasons',[]))})
        for request in reqs.get('requests') or []:
            rid=request.get('request_id','unknown'); row=rows.get(rid) or {}
            try:
                if frozen:
                    from ..audit.evidence import resolve_json_pointer
                    from .layout import usable_location_span
                    p=deepcopy(request.get('placement') or {})
                    pointer=p.get('target_pointer','/body_markdown')
                    found,text=resolve_json_pointer(content,pointer)
                    if not found or not isinstance(text,str) or not text:
                        pointer='/body_markdown';text=content.get('body_markdown','')
                    old_span=p.get('span') if pointer==p.get('target_pointer','/body_markdown') else None
                    span=usable_location_span(text,old_span,p.get('anchor_text'))
                    p.update(target_pointer=pointer,owner_id=p.get('owner_id') or unit,
                        anchor_text=text[span[0]:span[1]],span=span,field_hash=digest_of(text),
                        position='before' if p.get('position')=='before' else 'after')
                else:
                    p=resolve_bound_placement(content,request['placement'])
            except (ContractError,KeyError,TypeError) as exc:
                problem={'unit_id':unit,'request_id':rid,'kind':'placement','reason':str(exc)}
                problems.append(problem);missing.append(deepcopy(problem));unit_outcomes.append(outcome('missing',[str(exc)]))
                continue
            own_media_review=(row.get('review_outcome') or media_outcome(row)) if substantive else media_outcome(row)
            media_state=frozen_review() if frozen and not substantive else inherit_outcome(own_media_review,content_state)
            if require_complete_units and not frozen:
                media_state=inherit_outcome(media_state,media_review_state)
            if media_state['status']=='verified' and not substantive:
                from ..media.validation import validate_media_report
                try:
                    validate_media_report(request,row,row.get('validation'))
                except (ContractError,KeyError,ValueError) as exc:
                    media_state=inherit_outcome(outcome('unverified',['媒体审核证据无效：'+str(exc)]),content_state)
            p['anchor_id']='media-'+digest_of({'request_id':rid,'placement':request['placement']})[7:27]
            if substantive and media_state.get('status')=='verified':
                from .readiness import MEDIA_VERIFIED_SCOPE
                media_state={**media_state,'warnings':list(dict.fromkeys([*media_state.get('warnings',[]),MEDIA_VERIFIED_SCOPE]))}
            entry={'request_id':rid,'kind':request['kind'],'figure_id':None,'caption':request.get('caption',''),
                   'alt_text':request.get('alt_text',''),'placement':p,
                   'source_hash':digest_of({'manifest':sources.get(f'work/products/figures/{unit}.json'), 'request_id':rid}),
                   'review_outcome':media_state,
                   'diagnostic_status':media_state['status'],'diagnostic_reason':'；'.join(media_state.get('reasons',[]))}
            resource_paths={}; invalid=[]
            display_resources = [] if not substantive and media_state['status'] in {'known_error','missing'} else row.get('resources') or []
            for resource in display_resources:
                try:
                    path=_resource(run,row,resource,sources)
                    relative=resource['relative_path']
                    if relative in resource_paths: raise ContractError('diagnostic duplicate resource path')
                    resource_paths[relative]=path
                    sources[str(path.relative_to(run))]=digest_file(path)
                except (ContractError,OSError) as exc: invalid.append(str(exc))
            if request['kind']=='animation' and resource_paths and not invalid:
                animation=deepcopy(row.get('animation') or {})
                # A diagnostic projection consumes the exact frozen row. Neither
                # copied execution status nor host-made pass reports grant review.
                if not frozen and (animation.get('request_id')!=rid or animation.get('spec_hash')!=row.get('spec_hash')
                        or animation.get('resources')!=row.get('resources')):
                    invalid.append('diagnostic animation/resource binding mismatch')
                else:
                    profile=row.get('render_profile') or 'final'
                    if require_complete_units and not frozen:
                        from ...errors import CapabilityError
                        try:
                            _check_animation_bytes(animation,resource_paths,book_id=state.get('book_id',''),
                                unit=unit,profile=profile)
                        except (ContractError,CapabilityError,OSError,ValueError,KeyError) as exc:
                            invalid.append('动画资源不可用：'+str(exc))
                    if not invalid:
                        animations.append((unit,animation,resource_paths,p,profile,request,row,media_state))
            elif request['kind']!='animation' and resource_paths and not invalid:
                kind_paths={r['kind']:resource_paths[r['relative_path']] for r in row.get('resources') or []}
                svg=kind_paths.get('svg'); raster=kind_paths.get('image') or kind_paths.get('png')
                fid='diagnostic-'+digest_of([unit,rid])[7:27]
                from PIL import Image
                try:
                    if svg:
                        from ..media.validation import _safe_svg
                        art=renderer.inline_svg(_safe_svg(svg),fid)
                        data['figures'][fid]={'id':fid,'source':'generated','svg':art,'caption':entry['caption']}
                        entry['figure_id']=fid
                    elif raster:
                        import base64
                        with Image.open(raster) as image:
                            expected={'.png':'PNG','.jpg':'JPEG','.jpeg':'JPEG'}.get(raster.suffix.lower())
                            if (image.format!=expected or not expected or max(image.size)>8192
                                    or image.width*image.height>16_777_216):
                                raise ContractError('diagnostic raster must be bounded PNG/JPEG with matching extension')
                            image.verify()
                        encoded=base64.b64encode(raster.read_bytes()).decode('ascii')
                        mime='image/png' if expected=='PNG' else 'image/jpeg'
                        data['figures'][fid]={'id':fid,'file':f'data:{mime};base64,{encoded}','caption':entry['caption']}
                        entry['figure_id']=fid
                except (ContractError,OSError,ValueError,SyntaxError,Image.DecompressionBombError) as exc:
                    invalid.append('静态媒体安全校验失败：'+str(exc))
            has_media=bool(entry['figure_id']) or any(u==unit and a.get('request_id')==rid for u,a,*_ in animations)
            if not has_media or invalid:
                reason='; '.join(invalid) or entry['diagnostic_reason'] or '没有可安全展示的媒体文件'
                missing.append({'unit_id':unit,'request_id':rid,'reason':reason})
                entry['review_outcome']=outcome('missing',[reason],upstream=[media_state])
                entry['diagnostic_status']='missing'
            if invalid:
                entry['diagnostic_reason'] += '；'+'; '.join(invalid)
            if (substantive and media_state['status']!='verified') or (not frozen and row.get('state')!='fulfilled') or invalid:
                from .readiness import media_dimensions, media_doubt_tier
                problems.append({'unit_id':unit,'request_id':rid,'state':entry['diagnostic_status'],'reason':entry['diagnostic_reason'],'resource_errors':invalid,
                                 'tier':media_doubt_tier(row),'dimensions':media_dimensions(row)})
            media.append(entry)
            unit_outcomes.append(entry['review_outcome'])
            placements.append({'unit_id':unit,'request_id':rid,'kind':request['kind'],'placement':p,'state':entry['diagnostic_status'],
                               'resources':[{'kind':resource['kind'],'path':str(resource_paths[resource['relative_path']].relative_to(run)),
                                   'sha256':resource['sha256']} for resource in row.get('resources') or []
                                   if resource['relative_path'] in resource_paths]})
        interaction_mounts=[]
        if any(d.get('handled_by')=='interaction' for d in reqs.get('decisions') or []):
            try:
                interaction=product('interaction',True)
                interaction_state=frozen_review() if frozen else inherit_outcome(interaction.get('review_outcome') or outcome('unverified',['交互尚未核实']),content_state)
                if interaction_state['status'] in {'missing','known_error'}:
                    raise ContractError('；'.join(interaction_state.get('reasons') or ['交互不可用']))
                if frozen:
                    # Placement approval is frozen; retain generated models at a readable landing.
                    for model in interaction.get('models') or []:
                        interaction_mounts.append({'unit_id':unit,'owner_id':unit,
                            'target_pointer':'/body_markdown','source_kind':'interaction',
                            'source_id':unit+'.'+model['id'],'source_hash':digest_of(model),
                            'requested_span_cp':[0,len(content.get('body_markdown',''))],
                            'edge':'after','field_hash':digest_of(content.get('body_markdown','')),
                            'anchor_text':content.get('body_markdown','')})
                else:
                    from ..interaction.placements import bind_interaction_placements
                    interaction_mounts=bind_interaction_placements(content,reqs,interaction)
                unit_outcomes.append(interaction_state)
                for model in interaction.get('models') or []:
                    identity=unit+'.'+model['id']; data['models'][identity]={**model,'id':identity,'unit_id':unit}
            except (ImportError,ContractError,ConfigError,OSError,ValueError) as exc:
                problems.append({'unit_id':unit,'kind':'interaction','reason':str(exc)})
                missing.append({'unit_id':unit,'kind':'interaction','reason':str(exc)})
                unit_outcomes.append(outcome('missing',[str(exc)]))
        layout=layout_from_reader_media(reader,media,interaction_mounts=interaction_mounts)
        presentation = None
        declarations = content.get('teaching_evidence') or []
        explicit_roles = isinstance(declarations,list) and any(
            isinstance(row,dict) and 'reader_role' in row for row in declarations)
        # Authored presentation is independent of the legacy review gates.
        # Limited-mode legacy drafts need no invented teaching plan; current
        # declarations must either bind to this Reader or report their error.
        if not frozen or explicit_roles:
            from ..content.teaching_projection import compile_teaching_presentation
            try:
                presentation = compile_teaching_presentation(content, reader)
            except (ContractError,KeyError,TypeError,ValueError,AttributeError) as exc:
                if not frozen:
                    raise
                reason='教学展示声明无效：'+str(exc)
                problems.append({'unit_id':unit,'kind':'teaching_presentation','reason':reason})
                unit_outcomes.append(outcome('known_error',[reason]))
        data['units'][unit]={'unit_id':unit,'title':content['title'],'reader_document':reader,'reader_layout':layout,
                            'reader_media':media,'teaching_presentation':presentation,'concepts':[],'figures':[],'crumb':'教学正文' if require_complete_units else '诊断预览',
                            'review_outcome':aggregate_outcomes(unit_outcomes),
                            'diagnostic_problems':[p for p in problems if p.get('unit_id')==unit]}
        outcomes.extend(unit_outcomes)
        data['tree'].append({'id':unit,'title':content['title'],'unit':True,'children':[]})
    for unit in unit_ids:
        data['units'][unit]['automatic_processing']=processing_record(state,unit)
    # Preparation can be reused from another run. Follow its recorded source
    # location; never infer it from a mutable global "current" pointer.
    body_product=_input(run,'work/products/body/body.json',sources,optional=True)
    recorded_parse=((body_product.get('payload') or {}).get('source_materials') or {}).get('source_dir')
    source_root=Path(parse_root or recorded_parse or run/'work/prepared-parse').resolve()
    from .source_materials import project_source_materials, copy_source_assets
    from ...core.source_materials import INPUTS
    data['counts']['canvas_models'] = sum(model.get('kind')=='canvas' for model in data['models'].values())
    site_data = {**data, 'animations': {unit:True for unit,*_ in animations}}
    site_inputs = renderer.reader_site_inputs(site_data)
    missing_canvas = [name for name,path in site_inputs.items()
                      if name.startswith('assets/mathbox/') and not path.is_file()]
    if missing_canvas:
        reason = '交互运行资源缺失：'+', '.join(missing_canvas)
        for model_id,model in data['models'].items():
            if model.get('kind')!='canvas': continue
            model['diagnostic_unavailable'] = reason
            problem = {'unit_id':model['unit_id'],'model_id':model_id,'kind':'interaction','reason':reason}
            missing.append(deepcopy(problem)); problems.append(problem)
            unit_row=data['units'][model['unit_id']]
            unit_row['diagnostic_problems'].append(problem)
            runtime_outcome=outcome('missing',[reason])
            unit_row['review_outcome']=aggregate_outcomes([unit_row['review_outcome'],runtime_outcome])
            outcomes.append(runtime_outcome)
    from .readiness import reader_problem_summary
    data['problem_summary']=reader_problem_summary(data,unit_ids,state)
    rendered_units=list(unit_ids)
    if require_complete_units:
        from .readiness import retain_complete_units
        rendered_units=retain_complete_units(data,missing,state)
        animations=[row for row in animations if row[0] in rendered_units]
        placements=[row for row in placements if row['unit_id'] in rendered_units]
        data['counts']['canvas_models']=sum(model.get('kind')=='canvas' for model in data['models'].values())
        site_inputs=renderer.reader_site_inputs({**data,'animations':{unit:True for unit,*_ in animations}})
    structure = _input(run, 'work/products/structure/structure.json', sources, optional=True)
    data['tree'] = _reader_structure_tree(data['tree'], structure.get('payload') or {})
    # A chapter can review an earlier theorem without carrying that chapter's
    # page or changing the current route. Reuse actual authored objects only.
    from ..content.theorem_index import build_theorem_index, load_theorem_rows
    other_units={path.stem for path in (run/'work/products/lesson').glob('*.json')}-set(unit_ids)
    theorem_rows.extend(load_theorem_rows(run,unit_ids=other_units,
        read=lambda relative:_input(run,relative,sources,optional=True)))
    data['theorem_index']=build_theorem_index(theorem_rows)
    for unit,row in data['units'].items():
        row['theorem_refs']=deepcopy(data['theorem_index']['refs_by_unit'].get(unit,{}))
    try:
        source_format = _input(run, 'work/products/source_format/book.json', sources, optional=True)
        formatted_payload = source_format.get('payload') if isinstance(source_format, dict) else None
    except (ContractError, ConfigError, OSError, ValueError):
        # Optional formatting failures must not block the original materials.
        # _input still records missing, rejected or malformed bytes in identity.
        formatted_payload = None
    source_paths=project_source_materials(data,source_root,formatted_payload=formatted_payload)
    material_inputs = [source_root/name for name in (*INPUTS,'source_materials.json','body.json')]
    from ...core.course_library import library_input_files
    material_inputs.extend(library_input_files(source_root/'course-library'))
    from ...core.video_evidence import load_attached_videos, video_input_files, project_video_evidence
    video_bundles = load_attached_videos(source_root, book_id=data['book'])
    material_inputs.extend(video_input_files(source_root, book_id=data['book']))
    data['video_evidence'] = [{'unit_id': unit, 'fingerprint': bundle.name,
        'url': 'video/' + bundle.name + '/index.html'}
        for unit, bundle, _ in video_bundles if unit in unit_ids]
    if not any(path.is_file() for path in material_inputs):
        # A later explicit attachment must invalidate even an initially empty source root.
        material_inputs=[source_root/'video-evidence/current.json']
    for path in [*material_inputs, *source_paths.values()]:
        key=str(path.relative_to(run)) if path.is_relative_to(run) else str(path)
        sources[key]=digest_file(path) if path.is_file() else 'missing'
    render_mode='teaching' if require_complete_units else 'diagnostic'
    from .display_policy import reader_display_policy
    overall=frozen_review() if frozen and not substantive else aggregate_outcomes(outcomes)
    data['review_outcome']=overall
    data['production_accepted']=bool(not substantive and require_complete_units and overall['status']=='verified' and not missing)
    dependencies = {'reader_inputs':{name:digest_file(path) if path.is_file() else 'missing'
                        for name,path in sorted(site_inputs.items())},
                    'implementation':_implementation_inputs(renderer)}
    identity=digest_of({'render_mode':render_mode,'source_hashes':sources,'units':unit_ids,'policy':reader_display_policy(),
        'projection':data,'placements':placements,'missing':missing,'problems':problems,
        'dependencies':dependencies})
    output=destination/identity[7:27]
    if output.exists(): return _existing_snapshot(output,identity)
    destination.mkdir(parents=True,exist_ok=True)
    # Assemble privately, then publish once; never rewrite an existing snapshot URL.
    with tempfile.TemporaryDirectory(prefix='.diagnostic-',dir=destination) as directory:
        pending=Path(directory)
        copy_source_assets(source_paths,data['source_assets'],pending)
        projected_videos = set()
        for unit, bundle, _ in video_bundles:
            if unit in unit_ids and bundle.name not in projected_videos:
                project_video_evidence(bundle, pending/'video'/bundle.name, course_id=data['book'])
                projected_videos.add(bundle.name)
        for unit,animation,paths,placement,profile,request,row,media_state in animations:
            if require_complete_units:
                payload=_attach_production_animation(pending,unit=unit,book_id=state.get('book_id',''),
                    animation=animation,sources=paths,profile=profile,request=request,row=row,review=media_state)
            else:
                payload=_attach_diagnostic_animation(pending,unit=unit,book_id=state.get('book_id',''),
                    animation=animation,sources=paths,profile=profile)
            payload['placement']=placement
            data['animations'].setdefault(unit,[]).append(payload)
            if payload.get('missing'):
                if require_complete_units and not frozen:
                    raise ContractError('动画组合期间资源变化：'+str(payload['notes']))
                missing.append({'unit_id':unit,'request_id':payload['request_id'],'reason':'; '.join(payload['notes'])})
        notes=[]
        renderer.write_reader_site(data,pending,book=data['book'],notes=notes)
        html=pending/'index.html'; text=html.read_text(encoding='utf-8')
        banner='<div class="diagnostic-banner" role="status">诊断预览，尚非验收交付。媒体状态及问题见位置清单；本页不改变原运行的批准状态。</div>'
        # R7（用户："不要放了"）：读者页**不再输出问题清单**与运行状态横幅。
        # 原因：原先把 `review_outcome.reasons` 原样拼进页面，含原始协议 JSON、
        # 上百条 `reader:sha256:…` 与整段 LaTeX，首屏全被占满、正文被推到很下面。
        # 诊断不丢：`problem_summary` / `review_outcome` / `check_reports` 仍在
        # `text_audit/*.json`、`render/*.json` 与 CLI `--json` 返回里。
        html.write_text(text,encoding='utf-8')
        report={'schema_version':'DiagnosticPreviewV1','preview_only':not require_complete_units,
                'production_accepted':data['production_accepted'],
                'review_mode':current_review_mode(),
                'review_status':('passed' if overall['status']=='verified' else 'needs_review') if substantive else 'not_run' if frozen else 'full',
                'render_mode':render_mode,'rendered_units':rendered_units,
                'source_run_id':state.get('run_id') or run.name,'source_hashes':sources,'units':list(unit_ids),
                'snapshot_hash':identity,'dependencies':dependencies,'missing':missing,'problems':problems,
                'review_outcome':overall,'partial':bool(missing),'problem_summary':data['problem_summary'],
                'processing_state_hashes':{unit:digest_of(processing_record(state,unit)) for unit in unit_ids},
                'renderer_notes':notes,'output_dir':str(output)}
        write_json_atomic(pending/'media-placement-plan.json',{'schema_version':'DiagnosticPlacementPlanV1','placements':placements,'preview_only':not require_complete_units})
        (pending/'media-placement-plan.md').write_text(('# 教学媒体位置\n\n' if require_complete_units else '# 诊断媒体位置\n\n')+''.join(f"- {p['unit_id']} / {p['request_id']}：{p['placement']['target_pointer']}；{p['state']}\n" for p in placements),encoding='utf-8')
        report['resources']=[{'path':str(p.relative_to(pending)),'sha256':digest_file(p)} for p in sorted(pending.rglob('*')) if p.is_file() and p.name!='diagnostic-preview.json']
        write_json_atomic(pending/'diagnostic-preview.json',report)
        try:
            pending.rename(output)
        except OSError:
            if output.exists(): return _existing_snapshot(output,identity)
            raise
    return report


def build_diagnostic_preview(run_dir: Path, *, unit_ids: list[str], out_dir: Path) -> dict:
    """Read-only CLI/UI facade over the exact production reader projection."""
    return build_reader_snapshot(run_dir,unit_ids=unit_ids,out_dir=out_dir)


def reader_check_html(text: str, report: dict) -> str:
    """Keep browser findings in the run report, outside the teaching page."""
    return re.sub(r'<!--reader-check-start-->.*?<!--reader-check-end-->','',text,flags=re.S)


def annotate_reader_check(output: Path, report: dict) -> None:
    path=Path(output)/'index.html'
    path.write_text(reader_check_html(path.read_text(encoding='utf-8'),report),encoding='utf-8')
