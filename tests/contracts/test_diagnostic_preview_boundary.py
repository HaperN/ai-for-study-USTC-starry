"""Diagnostic rendering never grants approval or changes the source run."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from aiforstudy.core.digest import digest_of, digest_file
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.errors import ContractError


def run_fixture(tmp_path):
    from test_content_v4_reader_contract import candidate, catalog
    from aiforstudy.blocks.content.content_contracts import parse_content
    from aiforstudy.blocks.content.reader_document import project_reader
    c = parse_content(candidate()).as_dict()
    c['book_id'] = 'test-book'
    doc = project_reader(c, catalog()).as_dict()
    run = tmp_path/'run-test'
    write_json_atomic(run/'state.json', {'book_id':'test-book','run_id':'run-test','status':'needs_review','scope_accepted':False})
    products = run/'work/products'
    def save(stage, payload): write_json_atomic(products/stage/'U.json', {'payload':payload})
    save('lesson', {'content':c,'content_hash':digest_of(c)})
    save('text_audit', {'approval':{'decision':'accepted','content_hash':digest_of(c),'reader_document_hash':digest_of(doc)},'review_document':doc})
    text=c['body_markdown']
    request={'request_id':'R','kind':'drawing','caption':'可见图注','alt_text':'替代文本','serves':['G'],
        'placement':{'target_pointer':'/body_markdown','owner_id':'U','anchor_text':text,'span':[0,len(text)],'field_hash':digest_of(text),'position':'after'}}
    req={'content_hash':digest_of(c),'requests':[request],'decisions':[]}
    save('media_plan',req)
    save('figures', {'content_hash':digest_of(c),'requirements_hash':digest_of(req),
        'requests':[{'request_id':'R','kind':'drawing','state':'needs_review','reason':'缺审核证据','resources':[]}]})
    return run


def test_missing_media_creates_shared_reader_preview_without_mutating_run(tmp_path):
    from aiforstudy.blocks.render.diagnostics import build_diagnostic_preview
    run=run_fixture(tmp_path)
    before={str(p.relative_to(run)):digest_file(p) for p in run.rglob('*') if p.is_file()}
    result=build_diagnostic_preview(run,unit_ids=['U'],out_dir=tmp_path/'preview')
    assert result['preview_only'] is True and result['production_accepted'] is False
    assert result['missing'] and result['problems']
    out=Path(result['output_dir'])
    assert result['problem_summary']
    assert '诊断预览，尚非验收交付' not in (out/'index.html').read_text()
    assert 'assets/render-core.js' in (out/'index.html').read_text()
    assert 'media-surface.css' in (out/'index.html').read_text()
    assert (out/'media-placement-plan.json').is_file()
    assert before=={str(p.relative_to(run)):digest_file(p) for p in run.rglob('*') if p.is_file()}


def test_diagnostic_refuses_stale_content_and_output_over_source(tmp_path):
    from aiforstudy.blocks.render.diagnostics import build_diagnostic_preview
    run=run_fixture(tmp_path)
    with pytest.raises(ContractError): build_diagnostic_preview(run,unit_ids=['U'],out_dir=run/'work/products')
    p=run/'work/products/lesson/U.json'; data=json.loads(p.read_text());data['payload']['content']['summary']='篡改'
    write_json_atomic(p,data)
    report=build_diagnostic_preview(run,unit_ids=['U'],out_dir=tmp_path/'preview')
    assert report['missing'] and 'content' in report['missing'][0]['reason']
    assert '篡改' not in (Path(report['output_dir'])/'data/data.js').read_text()


def test_multiselect_keeps_good_unit_when_another_has_no_approved_content(tmp_path):
    from aiforstudy.blocks.render.diagnostics import build_diagnostic_preview
    run=run_fixture(tmp_path)
    write_json_atomic(run/'work/products/lesson/V.json', {'payload':{'content':{'unit_id':'V'}}})
    report=build_diagnostic_preview(run,unit_ids=['U','V'],out_dir=tmp_path/'preview')
    assert report['units']==['U','V']
    assert any(p['unit_id']=='V' and p['kind']=='content' for p in report['problems'])
    data=(Path(report['output_dir'])/'data/data.js').read_text()
    assert 'ReaderDocumentV1' in data and '本单元无法生成诊断正文' in data


def test_diagnostic_unreviewed_svg_is_technical_preview_not_fake_approval(tmp_path):
    from aiforstudy.blocks.render.diagnostics import build_diagnostic_preview
    run=run_fixture(tmp_path); source=run/'work/media/R';source.mkdir(parents=True)
    file=source/'figure.svg';file.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="20"/></svg>')
    p=run/'work/products/figures/U.json'; manifest=json.loads(p.read_text());row=manifest['payload']['requests'][0]
    row.update(source_dir=str(source),resources=[{'kind':'svg','relative_path':'figure.svg','sha256':digest_file(file)}])
    write_json_atomic(p,manifest)
    result=build_diagnostic_preview(run,unit_ids=['U'],out_dir=tmp_path/'preview')
    assert not result['missing'] and result['production_accepted'] is False
    assert 'circle' in (Path(result['output_dir'])/'data/data.js').read_text()
    assert json.loads(p.read_text())['payload']['requests'][0]['state']=='needs_review'


def test_cli_and_control_preview_share_builder_without_changing_run(tmp_path, capsys):
    from aiforstudy.core.workspace import Workspace
    from aiforstudy.cli import main
    from aiforstudy.ui.server import ControlServer
    import shutil
    workspace=Workspace(tmp_path/'workspace')
    write_json_atomic(workspace.root/'config/books.json', {'books':{}})
    source=run_fixture(tmp_path)
    run=workspace.root/'data/books/test-book/runs/run-test'
    shutil.copytree(source,run)
    state_before=(run/'state.json').read_bytes()
    assert main(['preview','--workspace',str(workspace.root),'--run','run-test','--units','U','--json'])==0
    cli=json.loads(capsys.readouterr().out)
    control=ControlServer(workspace,port=0)
    status, payload=control.dispatch('POST','/api/runs/run-test/preview',{}, {'units':['U']})
    assert status==200 and payload['snapshot_hash']==cli['snapshot_hash']
    snapshot=Path(payload['output_dir']).name
    index=control._diagnostic_file('test-book','run-test',snapshot,'index.html')
    assert index and index.name=='index.html'
    assert control._diagnostic_file('test-book','run-test',snapshot,'../../../state.json') is None
    assert control._diagnostic_file('test-book','run-test',snapshot,'diagnostic-preview.json') is None
    index.write_text('tampered')
    assert control._diagnostic_file('test-book','run-test',snapshot,'index.html') is None
    assert state_before==(run/'state.json').read_bytes()
