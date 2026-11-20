"""A stale catalog may never silently retarget a requested teaching unit."""
import json
from dataclasses import replace
from pathlib import Path
import pytest

from aiforstudy.contracts.enums import SelectorKind, PreparePolicy
from aiforstudy.core.registry import load_registry
from aiforstudy.core.digest import digest_file
from aiforstudy.pipeline.engine import build_engine
from aiforstudy.pipeline.planner import plan
from aiforstudy.pipeline.services import Providers
from aiforstudy.errors import ContractError
from support.preparation_book import _mini_book, _request, _write_json, _tree


def stale_pdf_book(tmp_path):
    ws=_mini_book(tmp_path)
    pdf=ws.root/'book/source.pdf'; pdf.write_bytes(b'%PDF-1.4\ncontrolled old source identity\n%%EOF')
    registry=json.loads(ws.registry_path.read_text())
    registry['books']['mini-book']['sources']=[{'kind':'pdf','path':'book/source.pdf','sha256':digest_file(pdf)}]
    _write_json(ws.registry_path,registry)
    parse=ws.root/'book/parse'
    chapters=json.loads((parse/'chapters.json').read_text())
    chapters['lesson_units'][0]['anchor']='1'
    chapters['lesson_units'][1]['anchor']='1.1'
    _write_json(parse/'chapters.json',chapters)
    ingest=json.loads((parse/'ingest.json').read_text()); ingest['source_sha256']=digest_file(pdf)
    _write_json(parse/'ingest.json',ingest)
    return ws,parse,_request('mini-book',kind=SelectorKind.UNITS,values=('CH01-S01',))


def test_existing_catalog_cannot_hide_required_source_and_directory_rebuild(tmp_path):
    ws,parse,request=stale_pdf_book(tmp_path); before=_tree(ws.root)
    p=plan(request,load_registry(ws))
    assert p.requires_preparation is True
    assert p.resolved_scope is None, 'old catalog will be deleted, so it cannot fix the new scope'
    assert {t.stage for t in p.preparation}=={'mineru','toc','structure','export','body','concepts'}
    assert _tree(ws.root)==before, 'plan must not prepare or update files'


def test_production_engine_defers_all_paid_unit_nodes_until_new_scope_exists(tmp_path):
    ws,parse,request=stale_pdf_book(tmp_path)
    e=build_engine(request,ws,providers=Providers())
    assert e.plan.resolved_scope is None
    assert e.graph.order()==('mineru','toc','structure','export','body')
    assert not (ws.root/'data/books').exists()


def test_read_only_plan_freshness_works_from_outside_repository(tmp_path):
    import os
    import subprocess
    import sys
    ws,_,_=stale_pdf_book(tmp_path/'workspace')
    cwd=tmp_path/'outside'; cwd.mkdir()
    before=_tree(ws.root)
    proc=subprocess.run([sys.executable,'-m','aiforstudy','--workspace',str(ws.root),
        'plan','--book','mini-book','--units','CH01-S01','--json'],cwd=cwd,
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src')},
        capture_output=True,text=True,timeout=30)
    assert proc.returncode==0,proc.stdout+proc.stderr
    result=json.loads(proc.stdout)
    assert result['resolved_scope'] is None and result['requires_preparation']
    assert _tree(ws.root)==before


def test_refresh_does_not_use_an_existing_catalog_to_pick_unit_nodes(tmp_path):
    ws=_mini_book(tmp_path)
    request=replace(_request('mini-book',kind=SelectorKind.UNITS,values=('CH01-S01',)),
                    prepare_policy=PreparePolicy.REFRESH)
    p=plan(request,load_registry(ws))
    assert p.resolved_scope is None and p.requires_preparation


def test_invalid_chapter_syntax_is_rejected_before_catalog_rebuild(tmp_path):
    from aiforstudy.errors import InputError
    ws,_,request=stale_pdf_book(tmp_path)
    request=replace(request,selectors=replace(request.selectors,
        kind=SelectorKind.CHAPTERS,values=('CH04-CH02',)))
    with pytest.raises(InputError,match='反向区间'):
        plan(request,load_registry(ws))


@pytest.mark.parametrize('change',['title','anchor','content','ambiguous','missing'])
def test_scope_rebuild_cannot_reuse_an_id_for_another_source(tmp_path,change):
    from aiforstudy.pipeline.engine import validate_scope_identity
    ws,parse,request=stale_pdf_book(tmp_path)
    engine=build_engine(request,ws,providers=Providers())
    chapters=json.loads((parse/'chapters.json').read_text())
    unit=chapters['lesson_units'][1]
    if change=='title': unit['title']='另一节'
    if change=='anchor': unit['anchor']='1.2'
    if change=='content': (parse/unit['original_text']).write_text('另一节内容')
    if change=='missing': chapters['lesson_units'].pop()
    if change=='ambiguous': chapters['lesson_units'].append(dict(unit,unit_id='CH01-S99'))
    _write_json(parse/'chapters.json',chapters)
    with pytest.raises(ContractError,match='来源身份|锚点|歧义|消失'):
        validate_scope_identity(engine.scope_identity,engine.services.context)


def test_scope_rebuild_allows_proven_formatting_change_but_not_math_sign_change(tmp_path):
    from aiforstudy.pipeline.engine import validate_scope_identity
    ws,parse,request=stale_pdf_book(tmp_path)
    path=parse/'original_text/CH01-S01.md'
    path.write_text('### 1.1 甲之一节\n对 x > 0，得到 x + 1。\n')
    engine=build_engine(request,ws,providers=Providers())
    path.write_text('## 1.1 甲之一节\n\n对 x>0，得到 x+1。\n')
    evidence=validate_scope_identity(engine.scope_identity,engine.services.context)
    assert evidence['CH01-S01']['match']=='exact_source_tokens'
    path.write_text('## 1.1 甲之一节\n\n对 x<0，得到 x+1。\n')
    with pytest.raises(ContractError,match='来源身份'):
        validate_scope_identity(engine.scope_identity,engine.services.context)


def test_scope_word_boundaries_are_not_erased_as_whitespace(tmp_path):
    from aiforstudy.pipeline.engine import validate_scope_identity
    ws,parse,request=stale_pdf_book(tmp_path)
    source=parse/'original_text/CH01-S01.md'; source.write_text('变量 a b 与变量 ab 不同。')
    engine=build_engine(request,ws,providers=Providers())
    source.write_text('变量 ab 与变量 a b 不同。')
    with pytest.raises(ContractError,match='来源身份'):
        validate_scope_identity(engine.scope_identity,engine.services.context)


def test_current_legacy_catalog_only_rebuilds_stale_concept_part(tmp_path):
    ws=_mini_book(tmp_path); parse=ws.root/'book/parse'
    request=_request('mini-book',kind=SelectorKind.UNITS,values=('CH01-S01',))
    assert plan(request,load_registry(ws)).requires_preparation is False
    source=parse/'original_text/CH01-S01.md'; source.write_text(source.read_text()+'新增原文条件')
    p=plan(request,load_registry(ws))
    assert p.resolved_scope==('CH01-S01',)
    assert {t.stage for t in p.preparation}=={'concepts'}


def test_imported_pdf_declaration_uses_same_blocking_readiness_as_execution(tmp_path):
    ws=_mini_book(tmp_path); parse=ws.root/'book/parse'
    (parse/'pdf.path').write_text(str(tmp_path/'missing.pdf'))
    request=_request('mini-book',kind=SelectorKind.UNITS,values=('CH01-S01',))
    p=plan(request,load_registry(ws))
    assert p.resolved_scope is None and p.requires_preparation
    assert '导入目录声明了 PDF' in next(t.reason for t in p.preparation if t.stage=='toc')


def test_smoke_does_not_claim_or_dispatch_pdf_preparation(tmp_path):
    from aiforstudy.contracts.enums import Profile
    ws,_,request=stale_pdf_book(tmp_path)
    e=build_engine(replace(request,profile=Profile.SMOKE),ws,providers=Providers())
    assert e.plan.resolved_scope==('CH01-S01',)
    assert not e.plan.requires_preparation


@pytest.mark.parametrize('changed_source',[False,True])
def test_real_preparation_prefix_checks_identity_before_concepts_dispatch(tmp_path,changed_source):
    """Exercise real stage implementations and engine continuation, no stage stubs.

    The independent freshness tests above establish when this deferred plan is
    produced. Here a small imported non-PDF book avoids a hosted TOC call while
    exercising the exact same engine boundary after its real preparation prefix.
    """
    from aiforstudy.pipeline.engine import execute,_capture_scope_identity,_graph_for_plan
    ws=_mini_book(tmp_path); parse=ws.root/'book/parse'
    request=_request('mini-book',kind=SelectorKind.UNITS,values=('CH01-S01',))
    engine=build_engine(request,ws,providers=Providers(),until='concepts')
    engine.scope_identity=_capture_scope_identity(request,engine.services.context)
    engine.plan=replace(engine.plan,resolved_scope=None,requires_preparation=True)
    engine.graph=_graph_for_plan(engine.plan,engine.until)
    if changed_source:
        (parse/'original_text/CH01-S01.md').write_text('编号相同但内容已变为另一节。')
    before=_tree(parse)
    result,store=execute(engine)
    assert _tree(parse)==before
    assert all(store.node_states()[s].execution_state.value=='succeeded'
               for s in ('mineru','toc','structure','export','body'))
    assert store.load_state()['requested_unit_identity']['units']['CH01-S01']['title']=='甲之一节'
    if changed_source:
        assert result.status=='blocked' and result.failure_stage=='scope'
        assert '来源身份' in result.recovery_hint
        assert 'concepts' not in store.node_states()
        assert not any(k.startswith('lesson:') for k in store.node_states())
    else:
        assert result.status=='succeeded',result.recovery_hint
        assert store.node_states()['concepts'].execution_state.value=='succeeded'
        assert store.load_state()['scope_identity_evidence']['CH01-S01']['match']=='exact_source_bytes'
