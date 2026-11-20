"""Delivery checks frozen snapshots and member bytes; it does not rebuild layouts."""
from copy import deepcopy
from dataclasses import make_dataclass
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiforstudy.core.digest import digest_file, digest_of
from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.errors import ContractError
from aiforstudy.pipeline.delivery_stage import validate_reader_delivery, delivery_stage
from aiforstudy.pipeline.graph import Node
from aiforstudy.pipeline.media_stages import render_stage
from test_diagnostic_preview_boundary import run_fixture
from test_reader_admission import intact_drawing


def rendered(tmp_path, monkeypatch, *, partial=False, animation=False):
    from aiforstudy.checks import offline
    if animation:
        from test_diagnostic_animation_preview import animation_fixture
        from test_reader_admission import current_plan, product, save
        run,_,_=animation_fixture(tmp_path); current_plan(run,'animation')
        manifest=product(run,'figures')
        manifest['requests'][0].update(request_hash=digest_of(product(run,'media_plan')['requests'][0]),render_profile='final')
        save(run,'figures',manifest)
    else:
        run=run_fixture(tmp_path); intact_drawing(run)
    parse=tmp_path/'parse'
    units=['U','V'] if partial else ['U']
    write_json_atomic(parse/'chapters.json',{'lesson_units':[{'unit_id':u,'chapter':'C'} for u in units]})
    Context=make_dataclass('Context',['node','context','workspace','services','plan'])
    ctx=Context(Node('render:C','render','C'),
        SimpleNamespace(book_id='test-book',revision='source',parse=parse,title='Reader'),
        SimpleNamespace(root=Path(__file__).resolve().parents[2]),
        SimpleNamespace(store=SimpleNamespace(work=run/'work',run_id=run.name),
            cancel=SimpleNamespace(checkpoint=lambda:None),control=SimpleNamespace(check=lambda:None)),
        SimpleNamespace(resolved_scope=tuple(units)))
    monkeypatch.setattr(offline,'check_browser_execution',lambda *_a,**_k:
        offline.OfflineCheck('browser-execution','blocked','explicit offline test without browser',{}))
    result=render_stage(ctx)
    assert result.execution_state.value=='succeeded',result.error
    return ctx,run


def rebound_output(run, damage):
    path=run/'work/products/render/C.json'; wrapper=read_json(path); row=wrapper['payload']
    snapshot=row['reader_snapshot']; source=Path(snapshot['output_dir']); exported=Path(row['out_dir'])
    def data_at(root):
        return json.loads((root/'data/data.js').read_text().split('window.__DATA__ = ',1)[1].rsplit(';',1)[0])
    data=data_at(source); placements=read_json(source/'media-placement-plan.json')
    unit=data['units']['U']
    if damage=='reader': unit['reader_document']['blocks'][0]['raw_text']='changed source meaning'
    elif damage=='layout': unit['reader_layout']['blocks']=[]
    elif damage=='media': unit['reader_media'][0]['caption']='changed teaching requirement'
    elif damage=='placements': placements['placements'][0]['placement']['offset']=0
    elif damage=='missing_unit': data['units']={}
    else: raise AssertionError(damage)
    for root in (source,exported):
        (root/'data/data.js').write_text('window.__DATA__ = '+json.dumps(data,ensure_ascii=False)+';')
        write_json_atomic(root/'media-placement-plan.json',placements)
    snapshot['resources']=[{'path':str(p.relative_to(source)),'sha256':digest_file(p)}
        for p in sorted(source.rglob('*')) if p.is_file() and p.name!='diagnostic-preview.json']
    for root in (source,exported):write_json_atomic(root/'diagnostic-preview.json',snapshot)
    # D8 accepts coherent assembly errors; only byte/binding consistency remains.
    from aiforstudy.blocks.media import composition
    if hasattr(composition,'reader_projection_binding'):
        row['composition']=composition.reader_projection_binding(snapshot)
    members={str(p.relative_to(exported)):digest_file(p) for p in exported.rglob('*') if p.is_file()}
    row.update(members=members,reader_projection_hash=digest_of({'snapshot_hash':snapshot['snapshot_hash'],'members':members}))
    write_json_atomic(path,wrapper)


@pytest.mark.parametrize('damage',['reader','layout','media','placements','missing_unit'])
def test_delivery_accepts_coherent_projection_without_independent_layout_replay(tmp_path,monkeypatch,damage):
    ctx,run=rendered(tmp_path,monkeypatch)
    assert validate_reader_delivery(ctx)['C']['units']==['U']
    rebound_output(run,damage)
    before={str(p):p.read_bytes() for p in run.rglob('*') if p.is_file()}
    assert validate_reader_delivery(ctx)['C']['units']==['U']
    assert before=={str(p):p.read_bytes() for p in run.rglob('*') if p.is_file()}


def test_partial_delivery_records_only_rendered_coverage_and_remains_a_preview(tmp_path,monkeypatch):
    ctx,run=rendered(tmp_path,monkeypatch,partial=True)
    evidence=validate_reader_delivery(ctx)['C']
    assert evidence['units']==['U']
    assert evidence['requested_units']==['U','V']
    assert evidence['missing_units']==['V'] and evidence['coverage_complete'] is False
    ctx.node=Node('delivery','delivery','book')
    result=delivery_stage(ctx)
    assert result.execution_state.value=='succeeded' and result.quality_state.value=='needs_review'
    payload=read_json(result.outputs[0].path)['payload']
    assert payload['partial'] and payload['preview_available'] and not payload['formally_published']
    assert payload['stage_receipts']['C']['units']==['U']


def test_delivery_consistency_check_keeps_source_run_immutable(tmp_path,monkeypatch):
    ctx,run=rendered(tmp_path,monkeypatch)
    before={str(p):p.read_bytes() for p in run.rglob('*') if p.is_file()}
    evidence=validate_reader_delivery(ctx)
    assert evidence['C']['composition']['schema_version']=='ReaderCompositionV1'
    assert evidence['C']['composition']['units']['U']['reader_layout_hash']
    assert before=={str(p):p.read_bytes() for p in run.rglob('*') if p.is_file()}


def test_animation_sample_times_and_resources_survive_export_consistency_check(tmp_path,monkeypatch):
    ctx,run=rendered(tmp_path,monkeypatch,animation=True)
    before={str(p):digest_file(p) for p in run.rglob('*') if p.is_file()}
    evidence=validate_reader_delivery(ctx)
    assert evidence['C']['units']==['U'] and evidence['C']['review_outcome']['status']=='unverified'
    assert before=={str(p):digest_file(p) for p in run.rglob('*') if p.is_file()}


def test_rehashed_exported_html_cannot_override_the_frozen_snapshot(tmp_path,monkeypatch):
    ctx,run=rendered(tmp_path,monkeypatch)
    path=run/'work/products/render/C.json';wrapper=read_json(path);row=wrapper['payload']
    out=Path(row['out_dir']);html=out/'index.html'
    original=html.read_text()
    modified=original.replace('<body class="book-page">','<body class="book-page"><p>unapproved replacement</p>')
    assert modified != original, 'tampering fixture must actually change the exported page'
    html.write_text(modified)
    members={str(p.relative_to(out)):digest_file(p) for p in out.rglob('*') if p.is_file()}
    row.update(members=members,reader_projection_hash=digest_of({'snapshot_hash':row['reader_snapshot']['snapshot_hash'],'members':members}))
    write_json_atomic(path,wrapper)
    with pytest.raises(ContractError,match='exported HTML'):validate_reader_delivery(ctx)


@pytest.mark.parametrize('include_snapshot',[False,True])
def test_outer_status_or_snapshot_label_cannot_upgrade_current_unknown(tmp_path,monkeypatch,include_snapshot):
    from aiforstudy.blocks.content.review_outcome import outcome
    ctx,run=rendered(tmp_path,monkeypatch)
    path=run/'work/products/render/C.json';wrapper=read_json(path);row=wrapper['payload']
    assert validate_reader_delivery(ctx)['C']['review_outcome']['status']=='unverified'
    row['review_outcome']=outcome('verified');row['partial']=False
    if include_snapshot:
        row['reader_snapshot']['review_outcome']=outcome('verified')
        for root in (Path(row['reader_snapshot']['output_dir']),Path(row['out_dir'])):
            write_json_atomic(root/'diagnostic-preview.json',row['reader_snapshot'])
        out=Path(row['out_dir'])
        members={str(p.relative_to(out)):digest_file(p) for p in out.rglob('*') if p.is_file()}
        row.update(members=members,reader_projection_hash=digest_of({'snapshot_hash':row['reader_snapshot']['snapshot_hash'],'members':members}))
    write_json_atomic(path,wrapper)
    with pytest.raises(ContractError):validate_reader_delivery(ctx)


@pytest.mark.parametrize('receipt',['current','old_v2','missing_while_verified'])
def test_render_checks_current_media_receipts_before_claiming_verified(tmp_path,receipt):
    from aiforstudy.pipeline.services import StageContext
    from support.runner_v5 import run_book, UNIT
    result,store,engine,transport=run_book(tmp_path,until='interaction')
    assert result.status=='succeeded'
    path=store.work/'products/audit'/f'{UNIT}.json'
    wrapper=read_json(path);audit=wrapper['payload']
    assert audit['approval']['schema_version']=='MediaApprovalV3'
    if receipt=='old_v2': audit['approval']['schema_version']='MediaApprovalV2'
    elif receipt=='missing_while_verified': audit['approval']={}
    write_json_atomic(path,wrapper)
    ctx=StageContext(node=Node('render:CH01','render','CH01'),request=engine.request,
        plan=engine.plan,workspace=engine.services.workspace,context=engine.services.context,services=engine.services)
    calls=len(transport.calls)
    rendered_result=render_stage(ctx)
    assert rendered_result.execution_state.value=='succeeded',rendered_result.error
    if receipt=='current':
        assert validate_reader_delivery(ctx)['CH01']['coverage_complete']
    else:
        evidence=validate_reader_delivery(ctx)['CH01']
        assert evidence['review_outcome']['status']!='verified'
        assert read_json(rendered_result.outputs[0].path)['payload']['reader_snapshot']['production_accepted'] is False
    assert len(transport.calls)==calls
