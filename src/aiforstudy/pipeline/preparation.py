"""Production preparation: fill missing MinerU-derived products inside this run.

Legacy parsing algorithms are reused as explicit adapters. Only deterministic CLI
commands run in subprocesses; model work always receives the shared Gateway client.
Original import directories are never overwritten.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

from ..contracts.enums import QualityState
from ..contracts.stage import StageResult
from ..core.digest import digest_file, digest_of
from ..core.jsonio import read_json, read_json_soft, write_json_atomic
from ..errors import CapabilityError
from ..providers import mineru
from .graph import GRAPH_VERSION, PREPARATION_STAGES


def _tree_hashes(root):
    return {p.relative_to(root).as_posix(): digest_file(p)
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def source_fingerprint(context):
    return digest_of({'revision': context.revision,
                      'sources': {str(s.path): digest_file(s.path) if s.path.is_file() else None
                                  for s in context.sources},
                      'parse': _tree_hashes(context.parse)})


def prepared_context(context):
    """Read-only cache resolution; reuse requires unchanged original inputs and outputs."""
    pointer = context.workspace / 'data/books' / context.book_id / 'prepared/current.json'
    data = read_json_soft(pointer) or {}
    # Graph 8 adds delivery-only formatting; graph 7's preparation is unchanged.
    # Reuse still requires every original and prepared file hash plus boundaries.
    if (not data or data.get('graph_version') not in (GRAPH_VERSION, '7.0')
            or data.get('source_digest') != source_fingerprint(context)):
        return context
    path = Path(data.get('parse', ''))
    allowed = context.workspace / 'data/books' / context.book_id / 'runs'
    if not path.is_dir() or not path.resolve().is_relative_to(allowed.resolve()):
        return context
    if data.get('files') != _tree_hashes(path):
        return context
    from ..core.source_materials import is_current
    if not is_current(path):
        return context
    return replace(context, parse=path)


def _mutable(ctx):
    if ctx.request.prepare_policy.value == 'reuse':
        raise CapabilityError('prepare=reuse 要求已有完整且新鲜的基础产物；请使用 ensure 补齐')
    services = ctx.services
    target = services.store.work / 'prepared-parse'
    if ctx.context.parse == target:
        return target
    if not target.exists():
        original = services.registry.resolve(ctx.context.book_id)
        services._preparation_source_digest = source_fingerprint(original)
        if ctx.context.parse.is_dir():
            shutil.copytree(ctx.context.parse, target)
        else:
            target.mkdir(parents=True)
    services.context = replace(ctx.context, parse=target)
    ctx.context = services.context
    write_json_atomic(services.store.work / 'preparation-context.json',
                      {'parse': str(target), 'source_digest': getattr(
                          services, '_preparation_source_digest', '')})
    return target


def _cache(ctx):
    target = ctx.services.store.work / 'prepared-parse'
    if ctx.context.parse != target:
        return
    original = ctx.services.registry.resolve(ctx.context.book_id)
    write_json_atomic(original.workspace / 'data/books' / original.book_id /
                      'prepared/current.json', {
                          'parse': str(target),
                          'source_digest': source_fingerprint(original),
                          'files': _tree_hashes(target),
                          'run_id': ctx.services.store.run_id,
                          'graph_version': GRAPH_VERSION,
                      })


def _run(ctx, module, *args):
    from .stage_io import _repo_root
    ctx.services.cancel.checkpoint()
    command = [sys.executable, '-m', module]
    result = ctx.services.run_subprocess([*command, *map(str, args)],
                                         cwd=_repo_root())
    output = result.stdout.decode('utf-8', 'replace') if isinstance(result.stdout, bytes) else str(result.stdout)
    ctx.log(f'基础准备 {module}', output=output[-1600:])
    if result.returncode:
        raise CapabilityError(f'{module} 失败（{result.returncode}）：{output[-2400:]}')


def _pdf(ctx):
    return next((s.path for s in ctx.context.sources if s.kind == 'pdf' and s.path.is_file()), None)


def _directory_source(context):
    """The same PDF/legacy decision is used by planning and TOC execution."""
    from .stage_io import _legacy
    declared = [s for s in context.sources if s.kind == 'pdf']
    pdf = next((s.path for s in declared if s.path.is_file()), None)
    if declared and pdf is None:
        return None, '注册的 PDF 不可读，不能退回旧目录复核'
    cli = _legacy('blocks.parse.toc.cli')
    if pdf is None:
        pdf = cli._pdf_for(context.parse)
    if pdf is None:
        imported_pdf = (any((context.parse / name).is_file() for name in ('pdf.path', 'toc.pdf.json'))
            or bool((read_json_soft(context.parse / 'ingest.json') or {}).get('pdf'))
            or context.parse.name in cli.SLUG_PDF)
        if imported_pdf:
            return None, '导入目录声明了 PDF 但源文件不可读；请恢复 PDF，不能当作非 PDF 历史兼容数据'
    return pdf, None


def _requires_source_contract(ctx):
    # Historical non-PDF imports remain readable. All production PDF books and
    # versioned imports must prove that the current source-preserving adapter ran.
    return _source_contract_required(ctx.context)


def _source_contract_required(context):
    return (any(s.kind == 'pdf' for s in context.sources)
            or (context.parse / 'source.blocks.json').is_file())


def _adaptation_needed(context):
    """One freshness predicate shared by read-only planning and MinerU execution."""
    parse = context.parse
    missing = (not (parse / 'document.md').is_file() or
               (not (parse / 'headings.json').is_file() and
                not (parse / 'chapters.json').is_file()))
    if _source_contract_required(context):
        from .stage_io import _legacy
        source_blocks = _legacy('blocks.parse.mineru.source_blocks')
        return missing or not source_blocks.adaptation_is_current(parse, mineru_dir=parse / 'mineru')
    return missing


def _directory_freshness(parse, pdf):
    from .stage_io import _legacy
    dual = _legacy('blocks.parse.toc.dual_source')
    cli = _legacy('blocks.parse.toc.cli')
    return dual.is_current(parse, pdf), cli.authority_is_current(parse)


def _concept_preparation(context):
    from . import preparation_checks as old
    catalog, _ = old.build_catalog(context.parse, book_id=context.book_id,
                                   revision=context.revision)
    index = old.build_index(context.parse, catalog, book_id=context.book_id)
    missing = {p.unit_key for p in index.blocking_parts()}
    for unit in read_json(context.parse / 'chapters.json').get('lesson_units', []):
        part = read_json_soft(context.parse / 'concepts.parts' / f"{unit['unit_id']}.json") or {}
        meta = part.get('_meta') or {}
        saved = str(meta.get('source_sha256') or meta.get('text_sha256') or '')
        source = context.parse / str(unit.get('original_text') or '')
        if saved and source.is_file() and saved.removeprefix('sha256:') != digest_file(source).removeprefix('sha256:'):
            missing.add(unit['unit_id'])
    return catalog, index, missing


def preparation_freshness(context, *, refresh=False):
    """Read-only stage requirements, using the execution predicates above.

    `invalidates_catalog` means selectors must stay symbolic until body/export
    completes. An existing file alone never proves a production PDF is prepared.
    """
    stages = PREPARATION_STAGES
    if refresh:
        return {'invalidates_catalog':True, 'required':{
            stage:'显式 refresh 将重建全书基础产物并重新核实范围' for stage in stages}}
    reasons = {}
    parse = context.parse
    if not (parse / 'chapters.json').is_file():
        return {'invalidates_catalog':True, 'required':{
            stage:'尚无可解析选择器的章节目录，须先完成全书基础准备' for stage in stages}}
    invalidates = False
    if _adaptation_needed(context):
        reasons['mineru'] = 'MinerU 适配/来源契约缺失或过期，执行时会失效旧目录及全书衍生产物'
        invalidates = True
    pdf, source_problem = _directory_source(context)
    if source_problem:
        reasons['toc'] = source_problem
        invalidates = True
    elif pdf is not None:
        current, authority_current = _directory_freshness(parse, pdf)
        if not current or not authority_current:
            reasons['toc'] = ('双源视觉目录缺失或过期，需要模型生成/对比并重建正文权威'
                              if not current else '正文目录权威过期，须重建权威及其衍生产物')
            invalidates = True
    units = (read_json_soft(parse / 'chapters.json') or {}).get('lesson_units', [])
    if any(u.get('original_text') and not (parse / u['original_text']).is_file() for u in units):
        reasons['export'] = '单元原文缺失，重导出可能改变目录及单元边界'
        invalidates = True
    from ..core.source_materials import is_current
    if not is_current(parse):
        reasons['structure'] = '目录原文资料契约缺失或过期，须从完整原文重建结构及导出'
        invalidates = True
    else:
        from .stage_io import _legacy
        regions = _legacy('blocks.parse.toc.regions')
        try:
            regions.require_teaching_boundaries(regions.build_regions(parse))
        except ValueError as exc:
            reasons['structure'] = str(exc)
            invalidates = True
    if invalidates:
        first = min(stages.index(stage) for stage in reasons)
        for stage in stages[first:]:
            reasons.setdefault(stage, '上游基础契约重建会使该全书衍生产物失效')
        return {'invalidates_catalog':True, 'required':reasons}
    if not (parse / 'body.json').is_file():
        for stage in ('structure', 'export', 'body'):
            reasons[stage] = '正文归属缺失，需要根据目录权威重新生成并检查'
    if not (parse / 'concepts.json').is_file():
        reasons['concepts'] = '缺少完整全书概念索引'
    else:
        _, _, missing = _concept_preparation(context)
        if missing:
            reasons['concepts'] = '概念覆盖缺失或原文指纹过期：' + ', '.join(sorted(missing))
    return {'invalidates_catalog':False, 'required':reasons}


def _parse_dependencies(ctx, *modules):
    """MinerU owns source products, not later directory material exports."""
    from .stage_io import _repo_root
    paths = [ctx.context.parse / name for name in (
        'source.blocks.json', 'document.md', 'headings.json', 'figures.json')]
    for module in modules:
        paths.extend(sorted((_repo_root() / 'blocks/parse' / module).glob('*.py')))
    return tuple(str(path) for path in paths)


def _mineru_token(workspace):
    return mineru.resolve_api_token(secret_paths=(workspace.root / '.secrets/mineru.env',))[0]


def mineru_stage(ctx):
    from . import preparation_checks as old
    # A frozen registry identity must agree with readable bytes before reuse or upload.
    # Refresh authorizes new parsing, not silently rebinding the book to another PDF.
    for source in ctx.context.sources:
        if source.kind == 'pdf' and source.sha256 and source.path.is_file():
            if (str(source.sha256).removeprefix('sha256:') !=
                    digest_file(source.path).removeprefix('sha256:')):
                return StageResult.blocked('mineru',
                    'PDF 内容指纹与注册表冻结指纹不一致；请核对源文件和书籍身份后新建运行')
    source_hash, _ = old._declared_source_hash(ctx)
    refresh = ctx.request.prepare_policy.value == 'refresh'
    decision = mineru.decide(ctx.workspace, ctx.context.parse,
                             source_hash=source_hash, refresh=refresh,
                             allow_api=ctx.services.control.policy.allow_real_services)
    if decision.blocked and not (ctx.services.providers.mineru and _pdf(ctx)):
        return StageResult.blocked('mineru', decision.reason)
    if decision.route == mineru.ROUTE_REUSE:
        verification = old._verify_local_mineru(ctx.context.parse)
        if not verification["ok"]:
            return StageResult.blocked('mineru', verification["reason"])
    needs_adapt = _adaptation_needed(ctx.context)
    if needs_adapt and ctx.request.prepare_policy.value == 'reuse':
        return StageResult.blocked('mineru', 'MinerU 适配/来源契约已过期；请使用 ensure 在运行副本中重建')
    if decision.route == mineru.ROUTE_API or (decision.blocked and ctx.services.providers.mineru):
        pdf = _pdf(ctx)
        if pdf is None:
            return StageResult.blocked('mineru', 'MinerU API 获取需要注册表中可读取的 PDF')
        parse = _mutable(ctx)
        ctx.services.cancel.checkpoint()
        fetcher = ctx.services.providers.mineru or old._legacy('blocks.parse.mineru.fetch').fetch
        def checkpoint():
            ctx.services.cancel.checkpoint()
            ctx.services.control.check()
        checkpoint()
        timeout = ctx.services.control.policy.api_deadline_seconds
        record = fetcher(pdf, parse, checkpoint=checkpoint, token=_mineru_token(ctx.workspace), refetch=refresh,
                         timeout_s=None if timeout is None else max(1, int(timeout)), log=ctx.log)
        write_json_atomic(ctx.services.store.work / 'mineru-fetch.json', {
            'service': 'mineru', 'source_hash': source_hash,
            'record': record,
        })
        ctx.services.cancel.checkpoint()
        needs_adapt = True
        # A new MinerU response invalidates all derived parse products, only in this run's copy.
        for name in ('toc.v2.json', 'toc.review.json', 'toc.decisions.json', 'body.json',
                     'chapters.json', 'concepts.json', 'structure.json', 'concepts.parts',
                     'original_text', '_norm', 'source_materials.json'):
            path = parse / name
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
    if needs_adapt:
        replay_adapter = old._legacy('blocks.parse.toc.replay')
        pdf = _pdf(ctx)
        cached_directory = replay_adapter.capture(ctx.context.parse, pdf) if pdf else None
        parse = _mutable(ctx)
        adapter = old._legacy('blocks.parse.mineru.adapt')
        # Remove stale cuts even if adaptation fails. Original imported products
        # are untouched; directory model evidence is retained for safe local replay.
        removed = old._legacy('blocks.parse.toc.cli').invalidate_derived(parse)
        ctx.log('MinerU 适配契约变化，重建来源层并失效旧切分', removed=removed)
        result = adapter.adapt(parse / 'mineru', parse, book_title=ctx.context.title, pdf=_pdf(ctx))
        problems = (result.audit.get('checks') or {}).get('hard_rules') or []
        if problems:
            return StageResult.failed('mineru', 'MinerU 适配硬规则失败：' + str(problems))
        layout = adapter.find_layout(parse / 'mineru')
        write_json_atomic(parse / 'ingest.json', {'route': 'mineru',
            'source_sha256': source_hash, 'mineru': {
                'layout_sha256': digest_file(layout).removeprefix('sha256:')}})
        if cached_directory:
            receipt = replay_adapter.replay(parse, pdf, cached_directory, log=ctx.log)
            ctx.log('检查已复核目录证据能否离线回放',
                    status=(receipt or {}).get('status', 'inputs_changed'))
    result = old.mineru_stage(ctx)
    return replace(result, input_files=(*result.input_files,
        *_parse_dependencies(ctx, 'mineru', 'toc')))


def toc_stage(ctx):
    from . import preparation_checks as old
    ctx.services.cancel.checkpoint()
    pdf, source_problem = _directory_source(ctx.context)
    cli = old._legacy('blocks.parse.toc.cli')
    if source_problem:
        return StageResult.blocked('toc', source_problem)
    if pdf is None:
        # Explicit compatibility for imported non-PDF datasets and offline fixtures.
        # A declared PDF, even missing, never enters this path.
        ctx.log('非 PDF 历史导入兼容：读取已有目录契约，不声称完成双源视觉目录')
        if not (ctx.context.parse / 'toc.v2.json').is_file():
            return StageResult.blocked('toc', '非 PDF 导入缺少已有目录，无法生成视觉目录')
        return old.toc_stage(ctx)

    dual = old._legacy('blocks.parse.toc.dual_source')
    parse = ctx.context.parse
    current, authority_current = _directory_freshness(parse, pdf)
    if ctx.request.prepare_policy.value == 'reuse' and not (current and authority_current):
        return StageResult.blocked('toc', 'prepare=reuse 缺完整且新鲜的双源目录及正文权威；'
                                   '请使用 ensure 补齐，旧复核建议稿不能替代')
    if not current:
        client, _ = old._gateway_client(ctx, role='toc_review', bucket='prepare')
        if client is None:
            return StageResult.blocked('toc', '目录双源生成需要可用模型传输及完整 PDF 目录页；'
                                       '旧目录复核不能替代，请配置模型或提供有效双源缓存')
        client.scope_kind = 'book'
        parse = _mutable(ctx)
        cli._remember_pdf(parse, pdf)
        ctx.services.control.check()
        ctx.services.cancel.checkpoint()
        # Before a changed authority can fail, remove stale downstream products from
        # this run's copy. A failed review must never leave an apparently usable cut.
        removed = cli.invalidate_derived(parse)
        ctx.log('双源目录变化，失效本次运行的旧正文及概念产物', removed=removed)
        report = dual.generate_directory(parse, pdf, client, log=ctx.log)
        if report.get('status') != 'accepted':
            return StageResult.blocked('toc', '双源目录尚未完成：' +
                                       str(report.get('problems') or report.get('status')))
        authority_current = False
    if not authority_current:
        parse = _mutable(ctx)
        cli._remember_pdf(parse, pdf)
        cli.invalidate_derived(parse)
        ctx.services.cancel.checkpoint()
        authority = cli.write_authority(parse, read_json(parse / 'toc.v2.json'))
        if authority.get('status') != 'accepted':
            return StageResult.blocked('toc', '目录正文权威尚未完成：' +
                                       str(authority.get('problems') or []))
    toc = read_json(parse / 'toc.v2.json')
    nodes, _ = old.build_toc_nodes(toc)
    manifest = read_json(parse / 'toc.dual-source.json')
    ref = old._emit(ctx, name='toc', kind=old.KIND_BY_STAGE['toc'], file_type='parse_toc',
                    payload={'toc_nodes': [n.as_dict() for n in nodes],
                             'toc_nodes_count': len(nodes), 'dual_source': manifest,
                             'authority': read_json(parse / 'toc.authority.json'),
                             'pending': [], 'disagreements': [], 'unresolved': [],
                             'blocked_for_release': False},
                    inputs={'book_id': ctx.context.book_id, 'revision': ctx.context.revision,
                            'dual_source': digest_file(parse / 'toc.dual-source.json'),
                            'authority': digest_file(parse / 'toc.authority.json')})
    watched = ['toc.json', 'toc.pdf.json', 'document.md', 'headings.json', 'toc.dual-source.json',
               'toc.visual.json', 'toc.comparison.json', 'toc.arbitration.json',
               'toc.v2.json', 'toc.excluded.json', 'toc.authority.json',
               'toc.decisions.json', 'toc.alignment.json', 'toc.regions.json']
    ctx.log(f'完整双源目录 {len(nodes)} 个教学节点，正文及习题边界权威已生成',
            reused=current and authority_current)
    exclusions = (read_json_soft(parse/'toc.authority.json') or {}).get('unresolved_exclusions') or []
    findings = [{'code': 'toc-exclusion-unresolved', 'detail': row} for row in exclusions]
    return StageResult.ok('toc', outputs=(ref,), findings=findings,
                          quality_state=QualityState.NEEDS_REVIEW if findings else QualityState.ACCEPTED,
                          input_files=(str(pdf), *(str(parse / name) for name in watched)))


def structure_stage(ctx):
    from . import preparation_checks as old
    from ..core.source_materials import is_current, write_source_materials

    parse = ctx.context.parse
    if ctx.request.prepare_policy.value != 'reuse':
        parse = _mutable(ctx)
    current = is_current(parse)
    if not current:
        if ctx.request.prepare_policy.value == 'reuse':
            return StageResult.blocked('structure',
                '目录原文资料契约缺失或过期；请使用 ensure 从完整原文重建')
        parse = _mutable(ctx)
        write_source_materials(parse)
        # Old exercise projections may have removed source text. Rebuild every
        # cut in this run copy; never let a newly written manifest bless old cuts.
        for name in ('document.selected.md', 'source.projection.json',
                     'structure.json', 'structured.anchor3.md', 'chapters.json',
                     'body.json', 'original_text', '_norm'):
            path = parse / name
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
    regions = old._legacy('blocks.parse.toc.regions')
    try:
        regions.require_teaching_boundaries(regions.build_regions(parse))
    except ValueError as exc:
        return StageResult.blocked('structure', str(exc))
    if (parse / 'body.json').is_file():
        result = old.structure_stage(ctx)
        return replace(result, input_files=(*result.input_files,
            *old.source_material_input_files(ctx)))
    parse = _mutable(ctx)
    decisions = parse / 'toc.decisions.json'
    if not decisions.is_file():
        return StageResult.blocked('structure', '缺目录权威 toc.decisions.json；不回退正则层级')
    _run(ctx, 'blocks.parse.structure.cli', 'structure', parse / 'document.md', '--out', parse,
         '--authority', decisions)
    structure = read_json(parse / 'structure.json')
    # Rebuilding structure always invalidates downstream cuts, irrespective of
    # whether this is an old imported preparation or a current run's refresh.
    (parse / 'chapters.json').unlink(missing_ok=True)
    (parse / 'body.json').unlink(missing_ok=True)
    ref = old._emit(ctx, name='structure', kind=old.KIND_BY_STAGE['structure'],
                    file_type='parse_structure', payload=structure,
                    inputs={'authority': digest_file(decisions),
                            'document': digest_file(parse / 'document.md'),
                            'source_materials': digest_file(parse / 'source_materials.json')})
    return StageResult.ok('structure', outputs=(ref,), quality_state=QualityState.ACCEPTED,
                          input_files=(*old.source_material_input_files(ctx),
                              *old.parse_input_files(ctx, 'toc.decisions.json',
                                  'structure.json', 'structured.anchor3.md')))


def export_stage(ctx):
    from . import preparation_checks as old
    catalog = read_json_soft(ctx.context.parse / 'chapters.json') or {}
    missing_text = [u.get('original_text') for u in catalog.get('lesson_units', [])
                    if u.get('original_text') and
                    not (ctx.context.parse / u['original_text']).is_file()]
    if not catalog or missing_text:
        parse = _mutable(ctx)
        if not (parse / 'structured.anchor3.md').is_file():
            if not (parse / 'toc.decisions.json').is_file():
                return StageResult.blocked('export', '缺原文且无权威结构可重导出；请补 MinerU/目录准备输入')
            _run(ctx, 'blocks.parse.structure.cli', 'structure', parse / 'document.md',
                 '--out', parse, '--authority', parse / 'toc.decisions.json')
        from ..providers.normalizer import normalize
        ctx.services.cancel.checkpoint()
        report = normalize(parse / 'structured.anchor3.md', parse / '_norm')
        ctx.log('正文归一化完成', result=report)
        _run(ctx, 'blocks.parse.export.cli', parse / '_norm/sources/normalized-source.jsonl',
             '--out', parse, '--title', ctx.context.title)
        (parse / 'body.json').unlink(missing_ok=True)
    if not (ctx.context.parse / 'body.json').is_file():
        parse = _mutable(ctx)
        _run(ctx, 'blocks.parse.toc.cli', 'body', parse)
    result = old.export_stage(ctx)
    return replace(result, input_files=(*result.input_files,
        *old.source_material_input_files(ctx),
        *old.parse_input_files(ctx, unit_sources=True)))


CONCEPT_REEXTRACT_ROUNDS = 2


class _RetaggedClient:
    """Same client, distinct request tags: a re-extraction is a new logical request."""

    def __init__(self, client, suffix):
        self._client, self._suffix = client, suffix

    def __getattr__(self, name):
        return getattr(self._client, name)

    def __setattr__(self, name, value):
        if name in ('_client', '_suffix'):
            object.__setattr__(self, name, value)
        else:
            setattr(self._client, name, value)

    def chat_json(self, messages, *, tag='', **kwargs):
        return self._client.chat_json(messages, tag=f'{tag}:{self._suffix}', **kwargs)


def concepts_stage(ctx):
    from . import preparation_checks as old
    catalog, index, missing = _concept_preparation(ctx.context)
    refresh = ctx.request.prepare_policy.value == 'refresh'
    if missing or refresh:
        client, _ = old._gateway_client(ctx, role='concepts', bucket='prepare')
        if client is None:
            result = old.concepts_stage(ctx)
            if missing and not index.blocking_parts():
                return StageResult.blocked('concepts', '概念分片原文指纹已变化，且未配置模型服务，不能复用旧片',
                                           outputs=result.outputs)
            return result
        parse = _mutable(ctx)
        extractor = old._legacy('blocks.parse.concepts.extract_concepts')
        def unit_progress(_done, _total, unit_id):
            ctx.services.cancel.checkpoint()
            # Serial extraction: charge each actual unit, including its retries,
            # instead of exhausting one fictitious 'book' unit's attempt limit.
            client.unit_key = unit_id
        extraction = extractor.extract_concepts(
            parse, client=client, only_units=None if refresh else sorted(missing),
            force=True, parallel=1, retries=1, prefer_cleaned=False,
            progress=unit_progress, log=ctx.log)
        # 用户 2026-10-20 定：抽取失败加重新抽取。失败单元再抽至多 CONCEPT_REEXTRACT_ROUNDS 轮；
        # 每轮换请求标签（新的逻辑请求、各自的运输额度），不会撞上同一请求的次数上限。
        for round_no in range(1, CONCEPT_REEXTRACT_ROUNDS + 1):
            failed = sorted({row['unit_id'] for row in extraction.get('failed') or []})
            if not failed:
                break
            ctx.services.cancel.checkpoint()
            ctx.log(f'概念抽取失败 {len(failed)} 个单元，第 {round_no} 轮重新抽取：{failed[:5]}')
            extraction = extractor.extract_concepts(
                parse, client=_RetaggedClient(client, f're{round_no}'), only_units=failed,
                force=True, parallel=1, retries=1, prefer_cleaned=False,
                progress=unit_progress, log=ctx.log)
        ctx.log('全书概念准备完成', result=extraction)
    result = old.concepts_stage(ctx)
    if result.quality_state is QualityState.ACCEPTED:
        _cache(ctx)
    return result


def preparation_stages():
    return {'mineru': mineru_stage, 'toc': toc_stage, 'structure': structure_stage,
            'export': export_stage, 'concepts': concepts_stage}
