"""Package current graph output only after the complete receipt chain validates."""
from __future__ import annotations
from pathlib import Path
from ..contracts.artifact import ArtifactRef
from ..contracts.enums import QualityState
from ..contracts.stage import StageResult
from ..errors import ContractError
from ..core.jsonio import read_json_soft
from ..core.digest import digest_file, digest_of
from ..core.review_mode import reviews_frozen, frozen_review, substantive_reviews_enabled
from ..blocks.content.review_outcome import aggregate_outcomes, outcome
from .services import StageContext, reraise_control_error
from .stage_io import _store_root,_emit,_file_digest
def _rendered_chapters(ctx: StageContext) -> tuple[Path, ...]:
    root = _store_root(ctx) / "legacy" / "render"
    selected = ctx.plan.resolved_scope or ()
    catalog = read_json_soft(Path(ctx.context.parse) / "chapters.json") or {}
    by_unit = {str(u.get("unit_id")): str(u.get("chapter") or "")
               for u in catalog.get("lesson_units") or []}
    names = list(dict.fromkeys(by_unit.get(unit, "") for unit in selected))
    return tuple(root / name for name in names if name)


def reader_dependency_problems(export_dir):
    """修改计划 F2：页面数据用到的播放器依赖必须随入口一起交付（依赖闭包）。

    Animations need assets/animations.js (loaded on demand by app.js); canvas
    models need the MathBox runtime (loaded on demand by models.js).
    """
    import json
    export_dir=Path(export_dir)
    data_path=export_dir/'data'/'data.js'
    if not data_path.is_file():
        return ['缺页面数据 data/data.js']
    text=data_path.read_text(encoding='utf-8')
    try:
        data=json.loads(text[text.index('=')+1:].strip().rstrip(';'))
    except (ValueError, IndexError) as exc:
        return [f'页面数据无法解析：{exc}']
    problems=[]
    if any(data.get('animations') or {}) and not (export_dir/'assets'/'animations.js').is_file():
        problems.append('页面数据含动画，但缺 assets/animations.js（播放器脚本）')
    canvas=any(isinstance(m,dict) and m.get('kind')=='canvas' for m in (data.get('models') or {}).values())
    if canvas:
        for name in ('mathbox.min.js','three.min.js'):
            if not (export_dir/'assets'/'mathbox'/name).is_file():
                problems.append(f'页面数据含画布模型，但缺 assets/mathbox/{name}')
    return problems


def validate_reader_delivery(ctx, *, chapter_key=None):
    """Verify the actual immutable projection and exported bytes, not fake approvals."""
    from ..blocks.render.diagnostics import _existing_snapshot, validate_snapshot_dependencies, processing_record
    from ..blocks.render.snapshot_sources import validate_snapshot_sources
    work=_store_root(ctx); run=work.parent
    evidence={}
    for chapter in _rendered_chapters(ctx):
        if chapter_key is not None and chapter.name!=str(chapter_key): continue
        row=(read_json_soft(work/'products/render'/(chapter.name+'.json')) or {}).get('payload') or {}
        if row.get('schema_version')!='ReaderProjectionV1' or row.get('out_dir')!=str(chapter):
            raise ContractError('缺当前 ReaderProjectionV1：'+chapter.name)
        snapshot=row.get('reader_snapshot') or {}; source=Path(snapshot.get('output_dir') or '')
        rendered=snapshot.get('rendered_units') or []
        if (snapshot.get('render_mode')!='teaching' or snapshot.get('preview_only') is not False
                or row.get('rendered_units')!=rendered or row.get('units')!=snapshot.get('units')
                or len(rendered)!=len(set(rendered)) or not set(rendered)<=set(row.get('units') or [])):
            raise ContractError('交付须使用教学快照；诊断快照不能充当教学产物')
        if not source.resolve().is_relative_to((work/'reader-snapshots').resolve()):
            raise ContractError('reader snapshot outside current run')
        if _existing_snapshot(source,snapshot.get('snapshot_hash'))!=snapshot:
            raise ContractError('reader snapshot report changed')
        validate_snapshot_dependencies(snapshot)
        state=read_json_soft(run/'state.json') or {}
        if snapshot.get('processing_state_hashes')!={unit:digest_of(processing_record(state,unit)) for unit in snapshot['units']}:
            raise ContractError('reader automatic processing record changed')
        validate_snapshot_sources(snapshot, run, ctx.context.parse)
        # 修改计划 F2：缺播放器依赖只记警告、不拒绝交付（不是正文显示问题；用户 2026-10-20 定：技术检查放宽）。
        dependency_warnings=reader_dependency_problems(chapter)
        actual={str(p.relative_to(chapter)):digest_file(p) for p in chapter.rglob('*') if p.is_file()}
        if actual!=row.get('members') or digest_of({'snapshot_hash':snapshot['snapshot_hash'],'members':actual})!=row.get('reader_projection_hash'):
            raise ContractError('reader export bytes changed: '+chapter.name)
        from ..blocks.media.composition import validate_reader_export, reader_projection_status
        # D8: only frozen hashes/member bytes, never independent layout replay.
        validate_reader_export(snapshot,row.get('composition'),exported=chapter,
            browser_report=row.get('browser_report') or {})
        review,partial=reader_projection_status(snapshot,row.get('browser_report') or {})
        if row.get('review_outcome')!=review or row.get('partial') is not partial:
            raise ContractError('reader projection status differs from current evidence')
        missing=[unit for unit in row['units'] if unit not in rendered]
        if missing:
            review=aggregate_outcomes([review,outcome('missing',['单元未渲染：'+', '.join(missing)])])
        evidence[chapter.name]={'reader_projection_hash':row['reader_projection_hash'],
            'snapshot_hash':snapshot['snapshot_hash'],'review_outcome':review,
            'partial':bool(partial or missing),'units':rendered,
            'requested_units':row['units'],'missing_units':missing,'coverage_complete':not missing,
            'composition':row['composition'],'dependency_warnings':dependency_warnings}
    if not evidence: raise ContractError('交付范围没有当前阅读器产物')
    covered=[unit for row in evidence.values() for unit in row['units']]
    expected=list(ctx.plan.resolved_scope or ())
    if chapter_key is not None:
        catalog=read_json_soft(Path(ctx.context.parse)/'chapters.json') or {}
        members={str(unit['unit_id']) for unit in catalog.get('lesson_units',[]) if str(unit.get('chapter'))==str(chapter_key)}
        expected=[unit for unit in expected if unit in members]
    requested=[unit for row in evidence.values() for unit in row['requested_units']]
    if (sorted(requested)!=sorted(expected) or len(covered)!=len(set(covered))
            or not set(covered)<=set(expected)):
        raise ContractError('reader delivery omits or duplicates selected units')
    return evidence


def _book_reader(ctx, chapters):
    """The optional unified view cannot discard already delivered chapter pages."""
    if len(chapters)<2:
        return None, (), None
    from .media_stages import _render_reader_projection
    import shutil
    work=_store_root(ctx)
    receipt_path=work/'products/render/book.json'
    out=work/'legacy/render/book'
    def clear_export():
        # Only the current run's replaceable export/receipt; immutable snapshots
        # and all per-chapter pages remain untouched, even when rebuilding fails.
        receipt_path.unlink(missing_ok=True)
        if out.is_symlink() or out.is_file(): out.unlink()
        elif out.exists(): shutil.rmtree(out)
    try:
        clear_export()
        catalog_path=Path(ctx.context.parse)/'chapters.json'
        catalog=read_json_soft(catalog_path) or {}
        units=list(ctx.plan.resolved_scope or ())
        known=[str(row['unit_id']) for row in catalog.get('lesson_units',[])]
        if not units or set(units)-set(known):
            raise ContractError('全书阅读入口须使用冻结且仍在目录中的完整选择范围')
        row=_render_reader_projection(ctx,key='book',units=units,chapter_units=known)
        row=dict(row,dependency_warnings=reader_dependency_problems(out))  # 修改计划 F2：只记警告
        ref=_emit(ctx,name='render',kind='render.book',file_type='render.book',payload=row,
            inputs={'reader_projection_hash':row['reader_projection_hash'],
                    'chapters':digest_file(catalog_path)},path=receipt_path)
        resources=tuple(ArtifactRef(book_id=ctx.context.book_id,
            kind='render.html' if path.suffix=='.html' else 'render.resource',
            logical_key='render:book/'+path.relative_to(out).as_posix(),path=path,
            producer_stage=ctx.node.stage,producer_version='reader-projection-v1',
            dependency_digest=row['reader_projection_hash']).with_content()
            for path in sorted(out.rglob('*')) if path.is_file())
        return row,(ref,*resources),None
    except Exception as exc:
        reraise_control_error(exc)
        clear_export()
        return None,(),f'{type(exc).__name__}: {exc}'


def delivery_stage(ctx: StageContext) -> StageResult:
    """`delivery`：把已渲染的章装成范围交付，并跑**离线验收**（§12 / A23 / A24）。

    只构建与验收，**不切发布指针**（理由见本节开头第 3 条）。产物里记下待发布的
    release 标识与验收结论，供最终切换阶段使用。
    """
    ctx.services.cancel.checkpoint()
    chapters=_rendered_chapters(ctx)
    if not chapters or any(not (chapter/'index.html').is_file() for chapter in chapters):
        return StageResult.blocked(ctx.node.stage,'尚无实际生成的章节 HTML。')
    book_reader,book_outputs,book_error=_book_reader(ctx,chapters)
    render_dirs=(*chapters, *((Path(book_reader['out_dir']),) if book_reader else ()))
    entrypoint=str((Path(book_reader['out_dir']) if book_reader else chapters[0])/'index.html')
    book_fields={'entrypoint':entrypoint,'book_reader':book_reader,'book_reader_error':book_error}
    book_findings=[{'code':'book-reader','why':book_error}] if book_error else []
    if substantive_reviews_enabled():
        # Rendering already ran the browser against these exported files. Carry
        # that result, rather than re-running every old delivery gate or browser.
        receipts={chapter.name:(read_json_soft(_store_root(ctx)/'products/render'/(chapter.name+'.json')) or {}).get('payload') or {}
                  for chapter in chapters}
        checks=[row.get('browser_report') or {'status':'not_run','ok':None,'detail':'缺页面实际检查结果'}
                for row in receipts.values()]
        reviews=[row.get('review_outcome') or outcome('unverified',['缺章节审核结果']) for row in receipts.values()]
        if book_reader:
            checks.append(book_reader['browser_report'])
            reviews.append(book_reader['review_outcome'])
        if book_error:
            checks.append({'status':'failed','ok':False,'detail':book_error})
            reviews.append(outcome('missing',['全书阅读入口未生成：'+book_error]))
        for check in checks:
            if check.get('status')!='accepted':
                reviews.append(outcome('missing' if check.get('status')=='failed' else 'unverified',
                    [check.get('detail') or '页面尚未核实']))
        review=aggregate_outcomes(reviews)
        tree=_store_root(ctx)/'legacy/render'
        members={page.relative_to(tree).as_posix():digest_file(page)
                 for chapter in render_dirs for page in sorted(chapter.rglob('*')) if page.is_file()}
        partial=bool(book_error or (book_reader or {}).get('partial') or any(row.get('partial',False) for row in receipts.values()))
        ref=_emit(ctx,name='delivery',kind='delivery.scope',file_type='delivery.scope',
            payload={**book_fields,'scope_kind':str(ctx.node.scope_key),'chapters':[c.name for c in chapters],
                'members':members,'stage_receipts':receipts,'preview_available':True,
                'formally_published':False,'partial':partial,'review_mode':'balanced',
                'review_status':'passed' if review['status']=='verified' else 'needs_review',
                'review_outcome':review,'offline_acceptance':{
                    'passed':all(c.get('status')=='accepted' for c in checks),
                    'blocked':any(c.get('status') in ('blocked','not_run') for c in checks),
                    'checks':checks,'review_mode':'balanced'}},
            inputs={'chapters':[c.name for c in chapters],'member_digests':members},findings=book_findings)
        return StageResult.ok(ctx.node.stage,outputs=(ref,*book_outputs),findings=tuple(book_findings),
            quality_state=QualityState.ACCEPTED if review['status']=='verified' else QualityState.NEEDS_REVIEW)
    if reviews_frozen():
        tree = _store_root(ctx)/'legacy/render'
        members = {page.relative_to(tree).as_posix(): digest_file(page)
                   for chapter in render_dirs for page in sorted(chapter.rglob('*')) if page.is_file()}
        ref = _emit(ctx, name='delivery', kind='delivery.scope', file_type='delivery.scope',
            payload={**book_fields,'scope_kind': str(ctx.node.scope_key), 'chapters': [c.name for c in chapters],
                'members': members, 'stage_receipts': {}, 'preview_available': True,
                'formally_published': False, 'partial': bool(book_error or (book_reader or {}).get('partial')), 'review_mode': 'json_only',
                'review_status': 'not_run', 'review_outcome': frozen_review(),
                'offline_acceptance': {'status': 'not_run', 'passed': None, 'blocked': False,
                                       'checks': [], 'review_mode': 'json_only'}},
            inputs={'chapters': [c.name for c in chapters], 'member_digests': members},findings=book_findings)
        return StageResult.ok(ctx.node.stage, outputs=(ref,*book_outputs), findings=tuple(book_findings),quality_state=QualityState.NOT_CHECKED)
    from ..checks.offline import run_offline_acceptance
    from ..delivery.resources import ResourceManifest

    try:
        receipt_evidence = validate_reader_delivery(ctx)
    except ContractError as exc:
        return StageResult.blocked(ctx.node.stage, f"分阶段交付证据不完整：{exc}")
    chapters = _rendered_chapters(ctx)
    if not chapters or any(not (c / "index.html").is_file() for c in chapters):
        return StageResult.blocked(
            ctx.node.stage, "没有任何已渲染的章：交付必须建立在**本次运行的**渲染产物上。",
            outputs=())
    if any(p.is_symlink() for c in render_dirs for p in c.rglob("*")):
        return StageResult.failed(ctx.node.stage, "交付资源不能包含符号链接", outputs=())
    manifest = ResourceManifest(book_id=ctx.context.book_id)
    members: dict[str, str] = {}
    tree = _store_root(ctx) / "legacy" / "render"
    for page in sorted(p for c in render_dirs for p in c.rglob("*") if p.is_file()):
        rel = page.relative_to(tree).as_posix()
        manifest.add_file(logical_name=rel, kind=_kind_of(page), source=page,
                          book_id=ctx.context.book_id, scope_key=ctx.node.scope_key)
        members[rel] = _file_digest(page) or ""
    verify = manifest.verify(tree)
    if not verify["ok"]:
        return StageResult.failed(
            ctx.node.stage, f"资源清单自检不过：{verify['problems'][:3]}", outputs=())

    # 离线验收：逐项给出 accepted/failed/blocked，**不用 skipped 冒充通过**（§15 A26）。
    # 环境缺能力时它就是 blocked，于是这里如实 blocked —— 不把"没验"当"验过了"。
    tree = _store_root(ctx) / "legacy" / "render"
    # Runtime discovery belongs to the user's workspace, including installed wheels.
    # jsdom directory existence is never used as evidence of browser execution.
    import shutil as _shutil
    report = run_offline_acceptance(tree_root=tree, node=_shutil.which("node"),
                                    workspace_root=Path(ctx.workspace.root))
    findings = [*book_findings, *[{"code": f"offline-{c.name}", "why": c.detail}
                for c in report.checks if not c.ok]]
    ref = _emit(ctx, name="delivery", kind="delivery.scope", file_type="delivery.scope",
                # ⚠️ 用 `scope_key`，不是 `node.grain` —— `Node` 上没有 `grain`
                #    （粒度挂在 `graph.StageSpec` 上）。第一版这么写直接 AttributeError，
                #    而它被 `except` 之外的路径暴露成 failed（不是静默通过，这点是对的）。
                payload={**book_fields,"scope_kind": str(ctx.node.scope_key), "stage_receipts": receipt_evidence, "chapters":
                         [c.name for c in chapters],
                         "resources": manifest.as_dict(), "members": members,
                         "preview_available": True, "formally_published": False,
                         "review_outcome": aggregate_outcomes([row['review_outcome'] for row in receipt_evidence.values()]+
                             ([book_reader['review_outcome']] if book_reader else [])+
                             ([outcome('missing',['全书阅读入口未生成：'+book_error])] if book_error else [])+
                             ([] if report.passed else [outcome('unverified' if report.blocked else 'missing',[c.detail for c in report.checks if not c.ok])])),
                         "partial": bool(book_error or (book_reader or {}).get('partial') or any(row['partial'] for row in receipt_evidence.values())),
                         "offline_acceptance": report.as_dict(),
                         # ⚠️ run_id 只能从 `store` 拿：`Services` 上**没有** `run_id`
                         #    （第一版这么写又是 AttributeError）。直接调阶段时 store 可能
                         #    是 None，所以给一个明确的"未知"而不是崩。
                         "publish_pending": {
                             # `store.run_id` 本身已经是 `run-…`，再拼一次会成 `run-run-…`
                             "release_id": (str(ctx.services.store.run_id)
                                            if ctx.services.store is not None
                                            else "（直接调阶段，无 run 作用域）"),
                                             "note": "§14：M10 通过前不自动切换发布指针"},
                         "notes": list(manifest.conflicts)},
                inputs={"chapters": [c.name for c in chapters],
                        "member_digests": members},
                findings=findings)
    if book_error or (book_reader and book_reader['review_outcome']['status']!='verified') or not report.passed or any(row['review_outcome']['status']!='verified' or not row['coverage_complete']
                               for row in receipt_evidence.values()):
        return StageResult.ok(ctx.node.stage, outputs=(ref,*book_outputs),
                              quality_state=QualityState.NEEDS_REVIEW,
                              findings=tuple(findings))
    return StageResult.ok(ctx.node.stage, outputs=(ref,*book_outputs),
                          quality_state=QualityState.ACCEPTED)


def _kind_of(path: Path) -> str:
    """按扩展名判资源种类。认不出的当 `data`（**不丢**，也不假装它是图）。"""
    return {".svg": "svg", ".png": "png", ".jpg": "png", ".jpeg": "png", ".glb": "glb",
            ".css": "css", ".js": "js", ".woff": "font", ".woff2": "font",
            ".ttf": "font"}.get(path.suffix.lower(), "data")
