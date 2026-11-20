"""The reader retains available teaching text and reports every unresolved unit."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
from aiforstudy.contracts.request import RunRequest
from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.errors import ContractError
from test_diagnostic_animation_preview import data_at
from test_diagnostic_preview_boundary import run_fixture
from test_reader_admission import intact_drawing, product, save
from test_reader_delivery_composition import rendered


@pytest.mark.parametrize('status', ['known_error', 'unverified'])
def test_current_text_survives_unresolved_review_and_missing_media(tmp_path, status):
    run=run_fixture(tmp_path); intact_drawing(run)
    audit=product(run,'text_audit')
    audit['review_outcome']={'status':status,'reasons':['division denominator is unresolved']}
    save(run,'text_audit',audit)
    (run/'work/products/media_plan/U.json').unlink()
    report=build_reader_snapshot(run,unit_ids=['U','V'],out_dir=tmp_path/'reader',require_complete_units=True)
    data=data_at(report)
    assert report['rendered_units']==['U']
    assert data['units']['U']['reader_document']
    html=(Path(report['output_dir'])/'index.html').read_text()
    summary={row['unit_id']:row for row in report['problem_summary']}
    assert summary['U']['status']==status
    assert 'division denominator is unresolved' in summary['U']['reasons']
    assert summary['V']['status']=='missing'
    assert data['problem_summary']==report['problem_summary']
    assert 'reader-problem-summary' not in html and 'division denominator is unresolved' not in html
    assert report['preview_only'] is False and report['production_accepted'] is False


def test_all_missing_units_still_have_incomplete_chapter_page(tmp_path, monkeypatch):
    from aiforstudy.pipeline.media_stages import render_stage
    from aiforstudy.pipeline.delivery_stage import validate_reader_delivery
    ctx,run=rendered(tmp_path,monkeypatch)
    (run/'work/products/lesson/U.json').unlink()
    result=render_stage(ctx)
    assert result.execution_state.value=='succeeded',result.error
    row=read_json(run/'work/products/render/C.json')['payload']
    assert row['rendered_units']==[] and row['partial']
    html=(Path(row['out_dir'])/'index.html').read_text()
    assert row['reader_snapshot']['problem_summary'][0]['unit_id']=='U'
    assert row['reader_snapshot']['problem_summary'][0]['status']=='missing'
    assert 'reader-problem-summary' not in html
    assert validate_reader_delivery(ctx)['C']['coverage_complete'] is False


def test_teaching_animation_has_production_assembly_without_false_approval(tmp_path, monkeypatch):
    ctx,run=rendered(tmp_path,monkeypatch,animation=True)
    row=read_json(run/'work/products/render/C.json')['payload']
    snapshot=row['reader_snapshot']; data=data_at(snapshot)
    animation=data['animations']['U'][0]
    assert snapshot['preview_only'] is False and data['preview_only'] is False
    assert animation['schema_version']=='ReaderAnimationV1'
    assert animation['preview_only'] is False and animation['production_accepted'] is False
    assert animation['missing']==[] and animation['status']=='needs_review'
    assert animation['resources']['mp4']['relative_path']=='video.mp4'


@pytest.mark.parametrize('format',['scope-html','zip','single-html'])
def test_unimplemented_delivery_format_is_rejected(format):
    with pytest.raises(ContractError):
        RunRequest.from_dict({'book_id':'B','selectors':{'kind':'all','values':[]},'delivery_formats':[format]})


def test_render_terminal_dependencies_follow_graph_changes(monkeypatch):
    from aiforstudy.pipeline import graph
    from aiforstudy.pipeline.runner import _satisfies
    from aiforstudy.contracts.enums import ExecutionState
    monkeypatch.setitem(graph.STAGE_BY_NAME,'render',replace(graph.stage('render'),requires=('media_plan',)))
    assert _satisfies(graph.Node('media_plan:U','media_plan','U'),
        SimpleNamespace(execution_state=ExecutionState.BLOCKED),consumer=graph.Node('render:C','render','C'))


def test_delivery_refuses_teaching_snapshot_marked_preview(tmp_path,monkeypatch):
    from aiforstudy.pipeline.delivery_stage import validate_reader_delivery
    ctx,run=rendered(tmp_path,monkeypatch)
    path=run/'work/products/render/C.json';wrapper=read_json(path)
    assert wrapper['payload']['reader_snapshot']['preview_only'] is False
    wrapper['payload']['reader_snapshot']['preview_only']=True
    write_json_atomic(path,wrapper)
    with pytest.raises(ContractError,match='诊断|preview'):
        validate_reader_delivery(ctx)


def test_delivery_checks_frozen_members_without_rederiving_layout(tmp_path,monkeypatch):
    from aiforstudy.pipeline.delivery_stage import validate_reader_delivery
    from test_reader_delivery_composition import rebound_output
    ctx,run=rendered(tmp_path,monkeypatch)
    # D8 accepts this risk: coherent assembly errors are not independently rebuilt.
    rebound_output(run,'layout')
    assert validate_reader_delivery(ctx)['C']['units']==['U']


def test_unverified_unit_is_listed_even_without_missing_resources(tmp_path):
    run=run_fixture(tmp_path);intact_drawing(run)
    audit=product(run,'text_audit');audit['review_outcome']={'status':'unverified','reasons':['review connection failed']}
    save(run,'text_audit',audit)
    report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader',require_complete_units=True)
    assert report['partial'] is False
    html=(Path(report['output_dir'])/'index.html').read_text()
    summary=report['problem_summary'][0]
    assert summary['unit_id']=='U' and summary['status']=='unverified'
    assert 'review connection failed' in summary['reasons']
    assert data_at(report)['units']['U']['reader_document']
    assert report['production_accepted'] is False
    assert 'reader-problem-summary' not in html and 'review connection failed' not in html


def test_production_animation_cannot_accept_a_forged_verified_label(tmp_path):
    from aiforstudy.blocks.render.diagnostics import _attach_production_animation
    from test_diagnostic_animation_preview import animation_fixture
    from test_reader_admission import current_plan
    run,source,_=animation_fixture(tmp_path);current_plan(run,'animation')
    request=product(run,'media_plan')['requests'][0]
    row=product(run,'figures')['requests'][0]
    row.update(state='fulfilled',render_profile='final')
    row['animation']['status']='fulfilled'
    with pytest.raises(ContractError):
        _attach_production_animation(tmp_path/'out',unit='U',book_id='test-book',
            animation=row['animation'],sources={r['relative_path']:source/r['relative_path'] for r in row['resources']},
            profile='final',request=request,row=row,review={'status':'verified'})
    assert not list((tmp_path/'out').rglob('*.mp4'))


@pytest.mark.parametrize('stage',['media_plan','figures','audit'])
def test_unreadable_media_product_never_hides_current_body(tmp_path,stage):
    run=run_fixture(tmp_path);intact_drawing(run)
    path=run/'work/products'/stage/'U.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('{incomplete JSON')
    report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader',require_complete_units=True)
    assert report['rendered_units']==['U']
    assert data_at(report)['units']['U']['reader_document']
    assert report['partial'] and report['production_accepted'] is False
    assert report['problem_summary'][0]['status']=='unverified'


def test_empty_chapter_summary_runs_in_real_browser(tmp_path):
    import shutil
    from aiforstudy.checks.offline import check_browser_execution
    run=run_fixture(tmp_path)
    (run/'work/products/lesson/U.json').unlink()
    state=read_json(run/'state.json')
    state['nodes']={'lesson:U':{'stage':'lesson','scope_key':'U','execution_state':'blocked',
        'error':{'reason':'source inventory structural_span: frozen source span changed'}}}
    write_json_atomic(run/'state.json',state)
    report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader',require_complete_units=True)
    html=(Path(report['output_dir'])/'index.html').read_text()
    summary=report['problem_summary'][0]
    assert summary['unit_id']=='U' and summary['status']=='missing'
    assert summary['reasons'][0]=='source inventory structural_span: frozen source span changed'
    assert report['rendered_units']==[] and report['partial']
    assert report['production_accepted'] is False
    assert 'reader-problem-summary' not in html and 'source inventory structural_span' not in html
    browser=check_browser_execution(Path(report['output_dir']),node=shutil.which('node'),
        workspace_root=Path(__file__).resolve().parents[2])
    assert browser.ok,browser.as_dict()


@pytest.mark.parametrize('root_reason',[
    'content author/patch blocked: source inventory structural_span: original span changed',
    'content author/patch blocked: source inventory scaffold_refinement: missing before/after evidence',
])
def test_ungenerated_unit_summary_uses_lesson_root_cause_without_downstream_duplicates(tmp_path,root_reason):
    run=run_fixture(tmp_path)
    (run/'work/products/lesson/U.json').unlink()
    state=read_json(run/'state.json')
    state['nodes']={stage+':U':{'stage':stage,'scope_key':'U','execution_state':'blocked',
        'error':{'reason':root_reason if stage=='lesson' else '上游 lesson:U 不满足（'+root_reason+'）'}}
        for stage in ('lesson','text_audit','media_plan','figures','audit','interaction')}
    write_json_atomic(run/'state.json',state)
    report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader',require_complete_units=True)
    data=data_at(report);row=report['problem_summary'][0]
    assert row['unit_id']=='U' and row['label']=='未生成' and row['status']=='missing'
    assert row['reasons'][0]==root_reason
    assert any('缺文件' in reason for reason in row['reasons'])
    assert sum(root_reason in reason for reason in row['reasons'])==1
    assert not any('上游 lesson' in reason for reason in row['reasons'])
    assert data['problem_summary']==report['problem_summary']
    assert data['unavailable_units'][0]['problems'][0]['reason']==root_reason


def test_changed_lesson_failure_invalidates_delivery_and_produces_new_snapshot(tmp_path,monkeypatch):
    from aiforstudy.pipeline.media_stages import render_stage
    from aiforstudy.pipeline.delivery_stage import validate_reader_delivery
    ctx,run=rendered(tmp_path,monkeypatch)
    (run/'work/products/lesson/U.json').unlink()
    state=read_json(run/'state.json')
    state['nodes']={'lesson:U':{'stage':'lesson','scope_key':'U','execution_state':'blocked',
        'error':{'reason':'source inventory structural_span: first cause'}}}
    write_json_atomic(run/'state.json',state)
    assert render_stage(ctx).execution_state.value=='succeeded'
    first=read_json(run/'work/products/render/C.json')['payload']['reader_snapshot']
    assert validate_reader_delivery(ctx)['C']['missing_units']==['U']
    original_html=(Path(first['output_dir'])/'index.html').read_bytes()
    state['nodes']['lesson:U']['error']['reason']='source inventory scaffold_refinement: changed cause'
    write_json_atomic(run/'state.json',state)
    with pytest.raises(ContractError,match='processing record changed'):
        validate_reader_delivery(ctx)
    assert render_stage(ctx).execution_state.value=='succeeded'
    second=read_json(run/'work/products/render/C.json')['payload']['reader_snapshot']
    assert first['snapshot_hash']!=second['snapshot_hash']
    assert (Path(first['output_dir'])/'index.html').read_bytes()==original_html
    assert second['problem_summary'][0]['reasons'][0].endswith('changed cause')
    assert validate_reader_delivery(ctx)['C']['missing_units']==['U']
