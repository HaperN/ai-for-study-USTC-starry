"""Source contracts must actually be refreshed and checked by shared engine stages."""
import json
from dataclasses import replace

from test_dual_toc_pipeline import _ctx
from test_mineru_production_regressions import layout, write_json
from aiforstudy.core.digest import digest_file
from aiforstudy.contracts.enums import PreparePolicy
from aiforstudy.contracts.stage import StageResult
from aiforstudy.pipeline import preparation as p, preparation_checks as old


def test_pdf_existing_document_still_rebuilds_old_adapter(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.node = replace(ctx.node, node_id='mineru', stage='mineru')
    source = ctx.context.parse
    write_json(source / 'mineru/layout.json', layout('第一章 甲'))
    write_json(source / 'ingest.json', {'route': 'mineru', 'mineru': {
        'layout_sha256': digest_file(source / 'mineru/layout.json').removeprefix('sha256:')}})
    result = p.mineru_stage(ctx)
    assert result.execution_state.value == 'succeeded', result
    assert ctx.context.parse != source
    assert (ctx.context.parse / 'source.blocks.json').is_file()
    assert not (source / 'source.blocks.json').exists()
    assert any('source_blocks.py' in f for f in result.input_files)


def test_reuse_reports_stale_adapter_without_writing(tmp_path):
    ctx = _ctx(tmp_path, reuse=True)
    ctx.node = replace(ctx.node, node_id='mineru', stage='mineru')
    original = ctx.context.parse
    result = p.mineru_stage(ctx)
    assert result.execution_state.value == 'blocked'
    assert '来源' in str(result.error) or '适配' in str(result.error)
    assert ctx.context.parse == original
    assert not (ctx.services.store.work / 'prepared-parse').exists()


def test_export_does_not_invoke_retired_rule_extractor(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    ctx.node = replace(ctx.node, node_id='export', stage='export')
    calls = []
    monkeypatch.setattr(p, '_run', lambda ctx, module, *args: calls.append(module))
    monkeypatch.setattr(old, 'export_stage', lambda ctx: StageResult.ok('export'))
    p.export_stage(ctx)
    assert calls == []


def test_body_ignores_corrupt_retired_exercise_artifact(tmp_path):
    from aiforstudy.core.source_materials import write_source_materials
    ctx = _ctx(tmp_path, pdf=False)
    ctx.node = replace(ctx.node, node_id='body', stage='body')
    (ctx.context.parse / 'exercises.json').write_text('{invalid retired artifact')
    write_source_materials(ctx.context.parse)
    result = old.body_stage(ctx)
    assert result.quality_state.value == 'accepted', result
    assert not any(f['code'].startswith('exercise-') for f in result.findings)


def test_body_requires_fresh_source_materials_not_question_index(tmp_path):
    ctx = _ctx(tmp_path, pdf=False)
    ctx.node = replace(ctx.node, node_id='body', stage='body')
    (ctx.context.parse / 'source_materials.json').unlink(missing_ok=True)
    result = old.body_stage(ctx)
    assert result.execution_state.value == 'blocked'
    assert '原文资料' in str(result.error)
