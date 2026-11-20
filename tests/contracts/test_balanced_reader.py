"""A review finding must survive delivery without hiding the current lesson/media."""
from pathlib import Path

import pytest

from aiforstudy.core.review_mode import review_mode
from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
from aiforstudy.blocks.media.composition import reader_projection_status
from test_diagnostic_preview_boundary import run_fixture
from test_diagnostic_animation_preview import data_at
from test_reader_admission import intact_drawing, product, save


@pytest.mark.parametrize('status', ['known_error', 'unverified', 'verified'])
def test_balanced_reader_keeps_real_picture_and_actual_review(tmp_path, status):
    run=run_fixture(tmp_path)
    intact_drawing(run)
    audit=product(run,'text_audit')
    audit['review_outcome']={'status':status,'reasons':[] if status=='verified' else ['例2第三步缺少非零条件']}
    save(run,'text_audit',audit)
    manifest=product(run,'figures')
    manifest['requests'][0].update(state='needs_review' if status!='verified' else 'fulfilled',
        review_outcome={'status':status,'reasons':[] if status=='verified' else ['图中分母没有注明非零']})
    save(run,'figures',manifest)
    with review_mode('balanced'):
        report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader',require_complete_units=True)
    data=data_at(report)
    assert data['units']['U']['reader_document']['blocks']
    assert data['figures'] and 'circle' in (Path(report['output_dir'])/'data/data.js').read_text()
    assert data['units']['U']['reader_media'][0]['review_outcome']['status']==status
    assert report['review_mode']=='balanced'
    assert report['review_status']==('passed' if status=='verified' else 'needs_review')
    assert report['production_accepted'] is False
    assert report['partial'] is False
    assert report['review_outcome']['status']==status
    assert data['units']['U']['review_outcome']['status']==status
    if status!='verified':
        assert '例2第三步缺少非零条件' in report['review_outcome']['reasons']
        assert report['problem_summary']
    else:
        assert report['review_outcome']['status']=='verified'
        assert not report['problem_summary']


@pytest.mark.parametrize('browser_status', ['accepted','failed','blocked'])
def test_balanced_browser_result_cannot_overwrite_unresolved_content(browser_status):
    snapshot={'missing':[], 'review_outcome':{'status':'known_error','reasons':['例2计算错误']}}
    with review_mode('balanced'):
        result,partial=reader_projection_status(snapshot,{'status':browser_status,'detail':'实际页面检查'})
    assert result['status']!='verified'
    assert '例2计算错误' in result['reasons']
    assert partial is (browser_status=='failed')


def test_balanced_reader_does_not_apply_a_previous_versions_pass_to_new_text(tmp_path):
    run=run_fixture(tmp_path);intact_drawing(run)
    audit=product(run,'text_audit')
    audit['content_hash']='sha256:old-content'
    audit['review_outcome']={'status':'verified','reasons':[]}
    save(run,'text_audit',audit)
    with review_mode('balanced'):
        report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader',require_complete_units=True)
    data=data_at(report)
    assert data['units']['U']['reader_document']['blocks']
    assert any('正文审核' in p['reason'] for p in report['problems'])
    assert report['review_status']=='needs_review'


@pytest.mark.parametrize('content_status,browser_status,expected', [
    ('verified','blocked','unverified'),
    ('known_error','accepted','known_error'),
])
def test_balanced_render_runs_browser_and_delivery_retains_its_actual_result(
        tmp_path, monkeypatch, content_status, browser_status, expected):
    from types import SimpleNamespace
    from aiforstudy.core.jsonio import read_json, write_json_atomic
    from aiforstudy.pipeline.graph import Node
    from aiforstudy.pipeline.delivery_stage import delivery_stage
    from aiforstudy.pipeline.media_stages import render_stage
    from aiforstudy.checks import offline
    run=run_fixture(tmp_path);intact_drawing(run)
    audit=product(run,'text_audit')
    audit['review_outcome']={'status':content_status,'reasons':[] if content_status=='verified' else ['例2计算错误']}
    save(run,'text_audit',audit)
    parse=tmp_path/'parse'
    write_json_atomic(parse/'chapters.json',{'lesson_units':[{'unit_id':'U','chapter':'C'}]})
    ctx=SimpleNamespace(node=Node('render:C','render','C'),
        context=SimpleNamespace(book_id='test-book',revision='source',parse=parse,title='Reader'),
        workspace=SimpleNamespace(root=Path(__file__).resolve().parents[2]),
        services=SimpleNamespace(store=SimpleNamespace(work=run/'work',run_id=run.name),
            cancel=SimpleNamespace(checkpoint=lambda:None),control=SimpleNamespace(check=lambda:None)),
        plan=SimpleNamespace(resolved_scope=('U',)))
    monkeypatch.setattr(offline,'check_browser_execution',lambda *_a,**_k:
        offline.OfflineCheck('browser-execution',browser_status,'explicit offline test',{}))
    with review_mode('balanced'):
        result=render_stage(ctx)
        assert result.execution_state.value=='succeeded',result.error
        render=read_json(run/'work/products/render/C.json')['payload']
        assert render['browser_report']['status']==browser_status
        assert 'explicit offline test' in render['browser_report']['detail']
        ctx.node=Node('delivery','delivery','book')
        result=delivery_stage(ctx)
    assert result.execution_state.value=='succeeded'
    assert result.quality_state.value=='needs_review'
    delivery=read_json(result.outputs[0].path)['payload']
    assert delivery['preview_available'] is True
    assert delivery['review_mode']=='balanced'
    assert delivery['review_status']=='needs_review'
    assert delivery['offline_acceptance']['checks'][0]['status']==browser_status
    assert delivery['review_outcome']['status']==expected
    assert delivery['formally_published'] is False


@pytest.mark.parametrize('statuses,expected', [
    (['verified','known_error'],'known_error'),
    (['known_error','unverified'],'known_error'),
    (['known_error','missing'],'missing'),
    (['verified','unverified'],'unverified'),
])
def test_balanced_summary_distinguishes_bad_existing_work_from_missing_work(statuses, expected):
    from aiforstudy.blocks.content.review_outcome import aggregate_outcomes
    with review_mode('balanced'):
        actual=aggregate_outcomes({'status':status,'reasons':[status]} for status in statuses)
    assert actual['status']==expected
    assert actual['reasons']==statuses
    with review_mode('full'):
        assert aggregate_outcomes([{'status':'known_error'}])['status']=='missing'


@pytest.mark.parametrize('mode', ['balanced','json_only'])
@pytest.mark.parametrize('locator', ['span', 'anchor_only', 'unusable', 'missing_field'])
def test_limited_snapshot_preserves_usable_placement_or_falls_back(tmp_path, mode, locator):
    run=run_fixture(tmp_path);intact_drawing(run)
    lesson=product(run,'lesson');lesson['content']['body_markdown']='首段。\n\n尾段。'
    save(run,'lesson',lesson)
    plan=product(run,'media_plan')
    usable=locator in ('span','anchor_only')
    plan['requests'][0]['placement'].update(target_pointer='/body_markdown',
        span=[0,3] if usable else [50,60],anchor_text='首段。' if usable else '旧版锚句',
        owner_id='stale-owner',field_hash='stale-hash',position='after')
    if locator=='anchor_only':
        # The author supplies an anchor; host projection leaves an unresolved
        # span placeholder until the current reader field is available.
        plan['requests'][0]['placement'].update(span=[0,0],occurrence=1,field_hash='')
    if locator=='missing_field':
        plan['requests'][0]['placement'].update(target_pointer='/missing',span=[0,3])
        plan['requests'][0]['placement'].pop('anchor_text')
    save(run,'media_plan',plan)
    with review_mode(mode):
        report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader')
    view=data_at(report)['units']['U']
    expected=[0,3] if usable else [0,8]
    assert view['reader_media'][0]['placement']['span']==expected
    mounted=view['reader_layout']['mounts'][0]
    assert mounted['requested_span_cp']==expected
    block=next(b for b in view['reader_layout']['blocks'] if b['block_id']==mounted['resolved_block_id'])
    assert block['source_start_cp']==(0 if usable else 5)
    assert view['reader_media'][0]['figure_id']
