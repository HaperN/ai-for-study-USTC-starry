"""Regressions for stale review evidence and runtime availability at the reader boundary."""
from copy import deepcopy
from pathlib import Path
import shutil

import pytest

from aiforstudy.blocks.content.review_outcome import outcome
from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
from aiforstudy.blocks.render.readiness import reader_problem_summary
from aiforstudy.pipeline.stage_io import _legacy
from test_diagnostic_preview_boundary import run_fixture
from test_diagnostic_snapshot_integrity import canvas_run, data_at
from test_reader_admission import intact_drawing, product, save


@pytest.mark.parametrize('damage',['hash','identity','unreadable','non_object'])
def test_bad_audit_keeps_independently_bound_current_text(tmp_path,damage):
    run=run_fixture(tmp_path);intact_drawing(run)
    lesson=product(run,'lesson');audit=product(run,'text_audit')
    lesson['content_identity']=deepcopy(audit['content_identity']);save(run,'lesson',lesson)
    audit['review_outcome']=outcome('verified')
    if damage=='hash': audit['content_hash']='sha256:'+'0'*64
    elif damage=='identity': audit['content_identity']['content_hash']='sha256:'+'0'*64
    elif damage=='non_object': audit=['invalid audit']
    save(run,'text_audit',audit)
    if damage=='unreadable': (run/'work/products/text_audit/U.json').write_text('{broken')
    report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader',require_complete_units=True)
    data=data_at(report)
    assert report['rendered_units']==['U']
    assert data['units']['U']['reader_document']['blocks']
    assert report['problem_summary'][0]['status']=='unverified'
    assert report['problem_summary'][0]['reasons']
    assert not report['production_accepted']
    assert report['problem_summary'][0]['unit_id']=='U'
    assert data['problem_summary']==report['problem_summary']
    assert 'U — 未核实' not in (Path(report['output_dir'])/'index.html').read_text()


def test_audit_failure_does_not_bypass_bad_lesson_source_identity(tmp_path):
    run=run_fixture(tmp_path);intact_drawing(run)
    lesson=product(run,'lesson');lesson['content_identity']=deepcopy(product(run,'text_audit')['content_identity'])
    lesson['content_identity']['source_context_hash']='sha256:'+'0'*64
    save(run,'lesson',lesson)
    (run/'work/products/text_audit/U.json').write_text('{broken')
    report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader',require_complete_units=True)
    assert report['rendered_units']==[]
    assert 'identity' in report['problem_summary'][0]['reasons'][0]


def test_recorded_environment_failure_cannot_hide_behind_verified_status():
    reason='交互运行资源缺失：assets/mathbox/mathbox.min.js'
    row={'reader_document':{'schema_version':'ReaderDocumentV1'},'review_outcome':outcome('verified'),
        'diagnostic_problems':[{'unit_id':'U','kind':'interaction','reason':reason}]}
    assert reader_problem_summary({'units':{'U':row}},['U'])==[
        {'unit_id':'U','status':'unverified','label':'未核实','reasons':[reason]}]


def test_missing_canvas_runtime_updates_unit_and_overall_outcomes(tmp_path,monkeypatch):
    renderer=_legacy('build');vendor=tmp_path/'vendor';shutil.copytree(renderer.VENDOR,vendor)
    (vendor/'mathbox/mathbox.min.js').unlink();monkeypatch.setattr(renderer,'VENDOR',vendor)
    report=build_reader_snapshot(canvas_run(tmp_path),unit_ids=['U'],out_dir=tmp_path/'reader')
    data=data_at(report)
    assert any('mathbox' in reason for reason in data['units']['U']['review_outcome']['reasons'])
    assert any('mathbox' in reason for reason in report['review_outcome']['reasons'])
    assert any('mathbox' in reason for reason in report['problem_summary'][0]['reasons'])
