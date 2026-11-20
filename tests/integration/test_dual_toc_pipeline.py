"""Directory authority wiring: temporary books, no hosted calls or old artifact writes."""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiforstudy.contracts.enums import PreparePolicy
from aiforstudy.core.digest import digest_file
from aiforstudy.errors import BudgetExceeded, Cancelled
from aiforstudy.pipeline import preparation as P
from aiforstudy.pipeline import preparation_checks as old
from aiforstudy.pipeline.engine import build_engine
from aiforstudy.pipeline.graph import Node
from aiforstudy.pipeline.services import Providers, StageContext
from aiforstudy.pipeline.state import RunStore
from support.preparation_book import _mini_book, _request, _tree, _write_json
from aiforstudy.core.source_materials import is_current as materials_current


def _ctx(tmp_path, *, pdf=True, reuse=False):
    ws = _mini_book(tmp_path)
    source = ws.root / 'source.pdf'
    if pdf:
        import pymupdf
        doc = pymupdf.open()
        doc.new_page().insert_text((40, 40), 'CONTENTS')
        doc.new_page().insert_text((40, 40), 'CHAPTER 1')
        doc.save(source)
        doc.close()
        registry_path = ws.root / 'config/books.json'
        registry = json.loads(registry_path.read_text())
        registry['books']['mini-book']['sources'] = [
            {'kind': 'pdf', 'path': str(source), 'sha256': digest_file(source)}]
        _write_json(registry_path, registry)
    req = _request('mini-book')
    if reuse:
        req = replace(req, prepare_policy=PreparePolicy.REUSE)
    engine = build_engine(req, ws, until='toc', providers=Providers())
    services = engine.services
    services.store = RunStore.create(ws.root / 'runs', req, engine.plan, control=engine.services.control)
    parse = services.context.parse
    (parse / 'document.md').write_text(
        '[第 1 页]\nCONTENTS\n[第 2 页]\n第一章 甲\n甲导言\n1.1 甲之一节\n甲节正文\n', encoding='utf-8')
    _write_json(parse / 'headings.json', {'headings': [
        {'text': '第一章 甲', 'line': 4, 'page': 2},
        {'text': '1.1 甲之一节', 'line': 6, 'page': 2}]})
    return StageContext(node=Node('toc', 'toc', 'book'), request=req, plan=engine.plan,
                        workspace=ws, context=services.context, services=services)


def _directory_double(monkeypatch, *, status='accepted', fail=None):
    """Replace the independent model module; real preparation/alignment still run."""
    def current(parse_dir, pdf):
        path = Path(parse_dir) / 'toc.dual-source.json'
        if not path.is_file():
            return False
        data = json.loads(path.read_text())
        return (data.get('status') == 'accepted' and
                data.get('pdf') == digest_file(pdf) and
                data.get('document') == digest_file(Path(parse_dir) / 'document.md'))

    def generate(parse_dir, pdf, client, log=None):
        if fail:
            raise fail('boundary stopped')
        path = Path(parse_dir)
        toc = json.loads((path / 'toc.v2.json').read_text())
        for index, node in enumerate(toc['nodes'], 1):
            node['order'] = index
            node['page'] = {'printed': '1', 'pdf': 2}
            node['evidence'] = {'from': ['printed-toc'], 'agree': True}
        toc['nodes'][0]['title'] = '甲'
        _write_json(path / 'toc.v2.json', toc)
        _write_json(path / 'toc.excluded.json', {'schema_version': '1.0', 'entries': [], 'all_entries': [
            {'entry_id': 'F-1', 'toc_id': 'T-0001', 'title': '甲', 'number': '第1章',
             'level': 1, 'order': 1, 'category': 'teaching', 'printed_page': '1',
             'pdf_page': 2, 'toc_page': 1, 'reason': ''},
            {'entry_id': 'F-2', 'toc_id': 'T-0002', 'title': '甲之一节', 'number': '1.1',
             'level': 2, 'order': 2, 'category': 'teaching', 'printed_page': '1',
             'pdf_page': 2, 'toc_page': 1, 'reason': ''}]})
        report = {'status': status, 'problems': ([] if status == 'accepted' else ['目录看不清']),
                  'pdf': digest_file(pdf), 'document': digest_file(path / 'document.md')}
        _write_json(path / 'toc.dual-source.json', report)
        return report

    module = SimpleNamespace(generate_directory=generate, is_current=current)
    from blocks.parse.toc import dual_source
    monkeypatch.setattr(dual_source, 'is_current', current)
    original_legacy = old._legacy
    monkeypatch.setattr(old, '_legacy', lambda name: module if name ==
                        'blocks.parse.toc.dual_source' else original_legacy(name))
    # The pipeline gateway owns hosted calls. The directory algorithm is independently
    # tested; this boundary double exercises integration without spending a token.
    def gateway(*args, **kwargs):
        assert kwargs['role'] == 'toc_review'
        return SimpleNamespace(), 'injected'
    monkeypatch.setattr(old, '_gateway_client', gateway)
    return module


def test_pdf_with_old_review_but_no_transport_cannot_pass_as_dual_directory(tmp_path):
    ctx = _ctx(tmp_path)
    result = P.toc_stage(ctx)
    assert result.execution_state.value == 'blocked'
    assert '目录' in str(result.error)
    assert not (ctx.services.store.work / 'toc.review.suggested.json').exists()


def test_declared_missing_pdf_never_falls_back_to_legacy_review(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.context.sources[0].path.unlink()
    result = P.toc_stage(ctx)
    assert result.execution_state.value == 'blocked'
    assert 'PDF' in str(result.error)


def test_stale_directory_reuse_blocks_before_mutation(tmp_path):
    ctx = _ctx(tmp_path, reuse=True)
    before = _tree(ctx.context.parse)
    result = P.toc_stage(ctx)
    assert result.execution_state.value == 'blocked'
    assert _tree(ctx.context.parse) == before
    assert not (ctx.services.store.work / 'prepared-parse').exists()


def test_success_updates_authority_and_invalidates_only_this_run_derivatives(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    source = ctx.context.parse
    before = _tree(source)
    _directory_double(monkeypatch)
    result = P.toc_stage(ctx)
    assert result.quality_state.value == 'accepted'
    assert ctx.context.parse == ctx.services.store.work / 'prepared-parse'
    assert _tree(source) == before
    decisions = json.loads((ctx.context.parse / 'toc.decisions.json').read_text())
    assert [(d['line'], d['title']) for d in decisions['decisions']] == [
        (4, '甲'), (6, '甲之一节')]
    for name in ('body.json', 'chapters.json', 'concepts.json', 'concepts.parts', 'original_text'):
        assert not (ctx.context.parse / name).exists(), name
    assert (ctx.context.parse / 'toc.authority.json').is_file()
    assert not (ctx.services.store.work / 'toc.review.suggested.json').exists()


def test_unresolved_directory_does_not_make_structure_authority(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    _directory_double(monkeypatch, status='needs_review')
    result = P.toc_stage(ctx)
    assert result.execution_state.value == 'blocked'
    assert '目录看不清' in str(result.error)
    assert not (ctx.context.parse / 'toc.decisions.json').exists()


@pytest.mark.parametrize('error', [Cancelled, BudgetExceeded])
def test_model_control_stop_propagates_without_becoming_ordinary_review_problem(tmp_path, monkeypatch, error):
    ctx = _ctx(tmp_path)
    _directory_double(monkeypatch, fail=error)
    with pytest.raises(error, match='boundary stopped'):
        P.toc_stage(ctx)


def test_accepted_current_directory_reuses_without_client_or_mutation(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    _directory_double(monkeypatch)
    assert P.toc_stage(ctx).quality_state.value == 'accepted'
    before = _tree(ctx.context.parse)
    monkeypatch.setattr(old, '_gateway_client', lambda *a, **kw: pytest.fail('fresh cache called a model'))
    ctx.request = replace(ctx.request, prepare_policy=PreparePolicy.REUSE)
    assert P.toc_stage(ctx).quality_state.value == 'accepted'
    assert _tree(ctx.context.parse) == before


def test_changed_heading_input_invalidates_stale_authority_under_reuse(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    _directory_double(monkeypatch)
    assert P.toc_stage(ctx).quality_state.value == 'accepted'
    (ctx.context.parse / 'headings.json').write_text('{"headings": []}')
    ctx.request = replace(ctx.request, prepare_policy=PreparePolicy.REUSE)
    result = P.toc_stage(ctx)
    assert result.execution_state.value == 'blocked'


def test_non_pdf_import_retains_explicit_legacy_compatibility(tmp_path):
    ctx = _ctx(tmp_path, pdf=False)
    result = P.toc_stage(ctx)
    assert result.quality_state.value == 'accepted'
    events = ctx.services.store.events.path.read_text()
    assert '非 PDF' in events or '非PDF' in events


def test_default_stage_registry_consumes_new_authority_through_export(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    source = ctx.context.parse
    before = _tree(source)
    _directory_double(monkeypatch)
    assert ctx.services.stages.get('toc')(ctx).quality_state.value == 'accepted'
    for stage in ('structure', 'export'):
        ctx.node = Node(stage, stage, 'book')
        result = ctx.services.stages.get(stage)(ctx)
        assert result.execution_state.value == 'succeeded', result.error
        assert result.quality_state.value == 'accepted', result.findings
    parse = ctx.context.parse
    chapters = json.loads((parse / 'chapters.json').read_text())
    assert [row['unit_id'] for row in chapters['lesson_units']] == ['CH01', 'CH01-S01']
    units = {row['unit_id']: row for row in chapters['lesson_units']}
    assert '甲导言' in (parse / units['CH01']['original_text']).read_text()
    assert '甲节正文' in (parse / units['CH01-S01']['original_text']).read_text()
    assert materials_current(parse)
    assert _tree(source) == before


@pytest.mark.parametrize(('cached_body', 'reuse'), [(False, False), (True, False), (True, True)])
def test_structure_blocks_unlocated_independent_material_even_with_current_cached_body(
        tmp_path, monkeypatch, cached_body, reuse):
    from aiforstudy.core.source_materials import write_source_materials
    ctx = _ctx(tmp_path)
    _directory_double(monkeypatch)
    assert P.toc_stage(ctx).quality_state.value == 'accepted'
    if cached_body:
        for stage in ('structure', 'export', 'body'):
            ctx.node=Node(stage,stage,'book')
            assert ctx.services.stages.get(stage)(ctx).execution_state.value=='succeeded'
    parse=ctx.context.parse
    excluded=json.loads((parse/'toc.excluded.json').read_text())
    row={'entry_id':'F-unlocated','toc_id':None,'title':'未定位独立习题资料',
         'number':'2','level':1,'order':3,'category':'exercise','pdf_page':2,
         'printed_page':'1','toc_page':1,'reason':'目录有但正文标题不能唯一定位'}
    excluded['entries'].append(row)
    excluded['all_entries'].append(row)
    _write_json(parse/'toc.excluded.json',excluded)
    # A current manifest alone must not bless cuts built without this boundary.
    write_source_materials(parse)
    assert materials_current(parse)
    raw=(parse/'document.md').read_bytes()
    if cached_body:
        # Otherwise-fresh metadata must not let a new run skip the guard stage.
        monkeypatch.setattr(P,'_adaptation_needed',lambda context:False)
        monkeypatch.setattr(P,'_directory_freshness',lambda *args:(True,True))
        freshness=P.preparation_freshness(ctx.context)
        assert freshness['invalidates_catalog']
        assert '独立资料来源定位失败' in freshness['required']['structure']
    if reuse: ctx.request=replace(ctx.request,prepare_policy=PreparePolicy.REUSE)
    ctx.node=Node('structure','structure','book')
    result=P.structure_stage(ctx)
    assert result.execution_state.value=='blocked'
    assert '独立资料来源定位失败' in str(result.error)
    assert 'F-unlocated' in str(result.error)
    assert (parse/'document.md').read_bytes()==raw
    assert json.loads((parse/'source_materials.json').read_text())['materials'][0]['state']=='unresolved'


@pytest.mark.parametrize(('listed', 'marked'), [(True, True), (False, True), (False, False)])
def test_only_independent_exercise_chapter_leaves_lessons_and_all_source_is_preserved(tmp_path, monkeypatch, listed, marked):
    ctx = _ctx(tmp_path)
    parse = ctx.context.parse
    exercise_heading = '第二章 综合习题' if listed else ('习题 1.1' if marked else '习题一')
    (parse / 'document.md').write_text(
        '[第 1 页]\nCONTENTS\n[第 2 页]\n第一章 甲\n甲导言\n1.1 甲之一节\n甲节正文\n'
        f'{exercise_heading}\n1. 计算向量和。\n第三章 乙\n乙导言\n', encoding='utf-8')
    _write_json(parse / 'headings.json', {'headings': [
        {'text': text, 'line': line, 'page': 2, 'region': 'exercise' if line == 8 else 'body'}
        for line, text in [(4, '第一章 甲'), (6, '1.1 甲之一节'), (8, exercise_heading), (10, '第三章 乙')]
        if marked or line != 8]})
    module = _directory_double(monkeypatch)
    original = module.generate_directory

    def generate(*args, **kwargs):
        report = original(*args, **kwargs)
        target = Path(args[0])
        toc = json.loads((target / 'toc.v2.json').read_text())
        toc['nodes'].append({'toc_id': 'T-0003', 'number': '第3章', 'title': '乙', 'level': 1,
                             'parent': None, 'kind': 'chapter', 'confirmed': True, 'order': 3,
                             'page': {'printed': '1', 'pdf': 2},
                             'evidence': {'from': ['printed-toc'], 'agree': True}})
        _write_json(target / 'toc.v2.json', toc)
        excluded = json.loads((target / 'toc.excluded.json').read_text())
        exercise = {'entry_id': 'F-3', 'toc_id': None, 'title': '综合习题', 'number': '第2章',
                    'level': 1, 'order': 3, 'category': 'exercise', 'printed_page': '1',
                    'pdf_page': 2, 'toc_page': 1, 'reason': '本章只有练习题'}
        excluded['entries'] = [exercise] if listed else []
        excluded['all_entries'] += ([exercise] if listed else []) + [
            {'entry_id': 'F-4', 'toc_id': 'T-0003', 'title': '乙', 'number': '第3章',
             'level': 1, 'order': 4, 'category': 'teaching', 'printed_page': '1',
             'pdf_page': 2, 'toc_page': 1, 'reason': ''}]
        _write_json(target / 'toc.excluded.json', excluded)
        return report

    module.generate_directory = generate
    for stage in ('toc', 'structure', 'export', 'body'):
        ctx.node = Node(stage, stage, 'book')
        result = ctx.services.stages.get(stage)(ctx)
        assert result.execution_state.value == 'succeeded', result.error
        assert result.quality_state.value == 'accepted', result.findings
    parse = ctx.context.parse
    chapters = json.loads((parse / 'chapters.json').read_text())
    assert [row['unit_id'] for row in chapters['lesson_units']] == ['CH01', 'CH01-S01', 'CH03']
    all_text = '\n'.join((parse / row['original_text']).read_text() for row in chapters['lesson_units'])
    assert ('计算向量和' not in all_text) if listed else ('计算向量和' in all_text)
    assert '乙导言' in all_text
    materials = json.loads((parse / 'source_materials.json').read_text())
    assert [r['entry_id'] for r in materials['materials']] == (['F-3'] if listed else [])
    if listed:
        row=materials['materials'][0]
        assert row['owner_toc_id'] is None and row['order']==3
        assert row['source_lines']==[8,9]
        assert row['raw_markdown']=='第二章 综合习题\n1. 计算向量和。\n'
        assert row['state']=='recorded'
    assert materials_current(parse)
    assert not (ctx.services.store.work/'exercise-check').exists()


def test_catalog_reports_verified_dual_source_without_fabricating_legacy_review(tmp_path, monkeypatch):
    from aiforstudy.blocks.parse.catalog import build_catalog
    ctx = _ctx(tmp_path)
    legacy = (ctx.context.parse / 'toc.review.json').read_bytes()
    _directory_double(monkeypatch)
    for stage in ('toc', 'structure', 'export'):
        ctx.node = Node(stage, stage, 'book')
        assert ctx.services.stages.get(stage)(ctx).execution_state.value == 'succeeded'
    catalog, details = build_catalog(ctx.context.parse, book_id='mini-book', revision='parse-v2')
    assert details['review']['workflow'] == 'dual-source'
    assert details['review']['stale'] is False
    assert not [n for n in catalog.needs_review if not n.resolved]
    assert (ctx.context.parse / 'toc.review.json').read_bytes() == legacy


def test_catalog_refuses_failed_dual_source_even_with_valid_old_review(tmp_path):
    from aiforstudy.blocks.parse.catalog import build_catalog
    from aiforstudy.blocks.parse.review import expected_inputs_digest
    ctx = _ctx(tmp_path)
    parse = ctx.context.parse
    _write_json(parse / 'toc.review.json', {'inputs_digest': expected_inputs_digest(parse),
                                           'decisions': []})
    _write_json(parse / 'toc.dual-source.json', {'status': 'needs_review', 'problems': ['缺图']})
    catalog, details = build_catalog(parse, book_id='mini-book', revision='parse-v2')
    assert details['review']['workflow'] == 'dual-source'
    assert details['review']['stale'] is True
    assert any(n.kind == 'directory-dual-source' and not n.resolved for n in catalog.needs_review)


def test_missing_imported_pdf_pointer_never_claims_non_pdf_compatibility(tmp_path):
    ctx = _ctx(tmp_path, pdf=False)
    (ctx.context.parse / 'pdf.path').write_text(str(tmp_path / 'missing-book.pdf'))
    result = P.toc_stage(ctx)
    assert result.execution_state.value == 'blocked'


def test_actual_dual_source_gateway_and_default_parse_stages_share_one_engine(tmp_path):
    """Only the hosted transport is replaced; PNG rendering and all stages are real."""
    from blocks.parse.toc import cli
    ctx = _ctx(tmp_path)
    source = ctx.context.parse
    _write_json(source / 'toc.json', {'book_title': '离线接线教材', 'printed_entries': [
        {'text': '第一章 甲', 'toc_page': 1, 'from_page': 1, 'level': 1},
        {'text': '1.1 甲之一节', 'toc_page': 1, 'from_page': 1, 'level': 2}]})
    # Real inline source proves that the preparation chain preserves exercises
    # without invoking another model role or splitting any question.
    with (source / 'document.md').open('a', encoding='utf-8') as output:
        output.write('习题\n1. 求甲。\n')
    headings = json.loads((source / 'headings.json').read_text())
    headings['headings'].append({'text': '习题', 'line': 8, 'page': 2, 'region': 'exercise'})
    _write_json(source / 'headings.json', headings)
    before = _tree(source)
    class Transport:
        calls = 0
        phases = []
        def __call__(self, *, model, effort, messages, images, max_tokens, timeout):
            self.calls += 1
            data = json.loads(messages[-1]['content'])
            assert 'phase' not in data, 'preparation must not scan or extract exercises'
            if set(data) == {'pdf_pages'}:
                self.phases.append('toc-image')
                assert set(data) == {'pdf_pages'}  # Independent image extraction.
                assert len(images) == 1
                assert Path(images[0]).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
                result = {'schema_version': '1.0', 'complete': True,
                          'pages': [{'pdf_page': 1, 'status': 'toc'}], 'entries': [
                    {'title': '甲', 'number': '第一章', 'level': 1, 'printed_page': '1',
                     'toc_page': 1, 'category': 'teaching', 'reason': '受控目录图章行'},
                    {'title': '甲之一节', 'number': '1.1', 'level': 2, 'printed_page': '1',
                     'toc_page': 1, 'category': 'teaching', 'reason': '受控目录图节行'}]}
            else:
                self.phases.append('toc-compare')
                assert not images
                result = {'schema_version': '1.0', 'matches': [
                    {'mineru_id': 'M0001', 'visual_id': 'V0001', 'status': 'same'},
                    {'mineru_id': 'M0002', 'visual_id': 'V0002', 'status': 'same'}]}
            return json.dumps(result, ensure_ascii=False), {'total_tokens': 17}
    transport = Transport()
    ctx.services.providers = Providers(llm=transport, is_stub=True)
    for stage in ('toc', 'structure', 'export', 'body'):
        ctx.node = Node(stage, stage, 'book')
        result = ctx.services.stages.get(stage)(ctx)
        assert result.execution_state.value == 'succeeded', result.error
        assert result.quality_state.value == 'accepted', result.findings
    assert transport.phases == ['toc-image', 'toc-compare']
    assert transport.calls == 2
    assert materials_current(ctx.context.parse)
    chapter_data=json.loads((ctx.context.parse/'chapters.json').read_text())
    texts='\n'.join((ctx.context.parse/row['original_text']).read_text() for row in chapter_data['lesson_units'])
    assert '1. 求甲。' in texts
    records = json.loads((ctx.services.store.root / 'execution.json').read_text())['attempts']
    assert len(records) == transport.calls
    assert [row['scope']['stage'] for row in records].count('toc') == 2
    assert {row['scope']['stage'] for row in records} == {'toc'}
    assert all(row['attempt'] == 1 and row['state'] == 'response_received' for row in records)
    assert not (ctx.services.store.root / 'usage.jsonl').exists()
    ctx.node = Node('toc', 'toc', 'book')
    assert P.toc_stage(ctx).quality_state.value == 'accepted'
    assert transport.calls == 2
    assert _tree(source) == before
    parse = ctx.context.parse
    assert cli.main(['authority', str(parse)]) == 0
    (parse / 'document.md').write_text('输入已变化')
    stale_before = _tree(parse)
    assert cli.main(['authority', str(parse)]) == 2
    assert _tree(parse) == stale_before
