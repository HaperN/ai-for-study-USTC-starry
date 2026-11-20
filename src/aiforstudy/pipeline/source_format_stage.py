"""Format independent directory materials once per run, outside lesson authoring."""
from pathlib import Path

from ..blocks.content import material_formatting, generation_corrections
from ..contracts.enums import QualityState
from ..contracts.stage import StageResult
from ..core.digest import digest_bytes, digest_file, digest_of
from ..core.jsonio import read_json_soft, write_json_atomic
from .gateway_client import _gateway_client
from .services import reraise_control_error
from .stage_io import _emit, _store_root


def source_format_stage(ctx):
    """The existing material boundary selects input; no source discovery or review."""
    parse = Path(ctx.context.parse)
    source = parse / 'source_materials.json'
    catalog_path = parse / 'chapters.json'
    materials = (read_json_soft(source) or {}).get('materials', [])
    catalog = read_json_soft(catalog_path) or {}
    inputs = [source, catalog_path]
    lesson_texts = []
    for unit in catalog.get('lesson_units', []):
        relative = unit.get('original_text')
        if not relative:
            continue
        path = (parse / relative).resolve()
        if path.is_relative_to(parse.resolve()) and path.is_file():
            lesson_texts.append(path.read_text(encoding='utf-8-sig'))
            inputs.append(path)
    # The authoritative region boundary already excludes embedded lesson material.
    # Whole-text containment also protects old imports. Source line coordinates
    # here and in normalized lesson exports must not be compared numerically.
    config = getattr(ctx.workspace, 'config_json', None)
    models = config('models.json') if callable(config) else {}
    implementation = {str(Path(module.__file__).name): digest_file(Path(module.__file__))
                      for module in (material_formatting, generation_corrections)}
    root = _store_root(ctx) / 'products/source_format'
    formatted, findings = {}, []
    client = None
    for row in materials:
        ctx.services.cancel.checkpoint()
        identity = row['id']
        raw = row.get('raw_markdown') or ''
        original = {'source_sha256': row.get('source_sha256'), 'status': 'original',
                    'display_markdown': '', 'notes': []}
        if row.get('state') != 'recorded' or not raw.strip():
            formatted[identity] = {**original, 'reason': 'unresolved_source'}
            continue
        if any(raw.strip() in text for text in lesson_texts):
            formatted[identity] = {**original, 'reason': 'already_in_lesson'}
            continue
        if digest_bytes(raw.encode('utf-8')) != row.get('source_sha256'):
            formatted[identity] = {**original, 'reason': 'source_hash_mismatch'}
            findings.append({'material_id': identity, 'reason': 'source_hash_mismatch'})
            continue
        fingerprint = digest_of({'material': row, 'implementation': implementation,
                                 'models': models})
        folder = root / 'items' / fingerprint.split(':')[-1]
        saved_path = folder / 'result.json'
        saved = read_json_soft(saved_path)
        if isinstance(saved, dict) and saved.get('input_hash') == fingerprint:
            formatted[identity] = saved['result']
            continue
        try:
            if client is None:
                client, _ = _gateway_client(ctx, role='lesson', bucket='source_format')
            if client is None:
                raise RuntimeError('未配置资料格式整理模型，保留原文')
            client.scope_kind = 'run'
            client.resource_id = identity
            result = material_formatting.format_material(row, client=client,
                correction_journal=folder / 'generation-journal.json')
            output = {**original, **result, 'status': 'formatted'}
        except Exception as exc:
            reraise_control_error(exc)
            reason = type(exc).__name__ + ': ' + str(exc)
            output = {**original, 'reason': reason}
            findings.append({'material_id': identity, 'reason': reason})
            ctx.log('独立资料格式整理未完成，展示原文', material_id=identity, reason=reason)
        write_json_atomic(saved_path, {'input_hash': fingerprint, 'result': output})
        formatted[identity] = output
    ref = _emit(ctx, name='source_format', kind='source.formatted',
        file_type='source_material_formatting', path=root / 'book.json',
        payload={'materials': formatted, 'review_status': 'not_run',
                 'note': '仅整理未进入正文生成单元的独立资料格式；原文保留，不作答案正确性认证'},
        inputs={str(p): digest_file(p) if p.is_file() else None for p in dict.fromkeys(inputs)},
        findings=findings)
    return StageResult.ok(ctx.node.stage, outputs=(ref,), quality_state=QualityState.NOT_CHECKED,
        findings=findings, input_files=tuple(str(p) for p in dict.fromkeys(inputs)))
