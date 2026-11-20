"""The production snapshot must carry authored roles through the actual DOM."""
import json
from pathlib import Path
import subprocess

import pytest
from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
from aiforstudy.core.digest import digest_file, digest_of
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.core.review_mode import review_mode
from test_diagnostic_preview_boundary import run_fixture


def authored_run(tmp_path):
    run = run_fixture(tmp_path)
    path = run/'work/products/lesson/U.json'
    lesson = json.loads(path.read_text())['payload']
    c = lesson['content']
    c['body_markdown'] = '结论及适用条件。\n\n逐项推导得到结果。\n\n取数值代入求解。\n\n一般结论仍适用于所有满足条件的对象。'
    c['teaching_evidence'] = [dict(goal_id='G', purpose='definition', reader_pointer='/body_markdown', claim_refs=['K'], quote=quote, reader_role=role) for quote, role in [
        ('结论及适用条件。','main'),('逐项推导得到结果。','proof'),('取数值代入求解。','worked_example'),('一般结论仍适用于所有满足条件的对象。','main')]]
    lesson['content_hash'] = digest_of(c)
    write_json_atomic(path, {'payload':lesson})
    path = run/'work/products/media_plan/U.json'
    plan = json.loads(path.read_text())['payload']; plan['content_hash'] = digest_of(c)
    start = c['body_markdown'].index('逐项推导'); quote = '逐项推导得到结果。'
    plan['requests'][0]['placement'].update(anchor_text=quote,span=[start,start+len(quote)],field_hash=digest_of(c['body_markdown']))
    write_json_atomic(path, {'payload':plan})
    path = run/'work/products/figures/U.json'
    manifest = json.loads(path.read_text())['payload']; manifest.update(content_hash=digest_of(c),requirements_hash=digest_of(plan))
    write_json_atomic(path, {'payload':manifest})
    return run


def snapshot(run,tmp_path,mode):
    before = {str(p.relative_to(run)):digest_file(p) for p in run.rglob('*') if p.is_file()}
    with review_mode(mode):
        report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader')
    assert before == {str(p.relative_to(run)):digest_file(p) for p in run.rglob('*') if p.is_file()}
    data=json.loads((Path(report['output_dir'])/'data/data.js').read_text().split('=',1)[1].strip().removesuffix(';'))
    assert report['production_accepted'] is False
    assert data['units']['U']['review_outcome']['status'] != 'verified'
    return report,data['units']['U']


@pytest.mark.parametrize('mode',['balanced','json_only','full'])
def test_snapshot_roles_reach_dom_and_keep_proof_media_owned(tmp_path,mode):
    run=authored_run(tmp_path)
    report,unit=snapshot(run,tmp_path,mode)
    assert unit['teaching_presentation'] is not None
    assert list(unit['teaching_presentation']['roles'].values()) == ['main','proof','worked_example','main']
    assert unit['teaching_presentation']['reader_document_hash']==digest_of(unit['reader_document'])
    root=Path(__file__).resolve().parents[2]
    result=subprocess.run(['node',str(root/'tests/ui/snapshot_teaching_dom.cjs')],input=json.dumps(unit,ensure_ascii=False),text=True,capture_output=True,cwd=root)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('mode',['balanced','json_only'])
@pytest.mark.parametrize('damage',['legacy','partial_quote','missing_claims'])
def test_limited_snapshot_keeps_legacy_or_invalid_draft_readable(tmp_path,mode,damage):
    run=authored_run(tmp_path);path=run/'work/products/lesson/U.json'
    lesson=json.loads(path.read_text())['payload'];c=lesson['content']
    if damage=='legacy':
        c.pop('teaching_plan');c.pop('teaching_evidence')
    elif damage=='missing_claims': c.pop('teaching_plan')
    else: c['teaching_evidence'][1]['quote']='逐项推导'
    write_json_atomic(path,{'payload':lesson})
    report,unit=snapshot(run,tmp_path,mode)
    assert '逐项推导得到结果。' in str(unit['reader_document'])
    assert unit['teaching_presentation'] is None
    if damage!='legacy':
        assert any(p.get('kind')=='teaching_presentation' for p in report['problems'])
        assert any(row['status']=='known_error' and any('教学展示声明无效' in reason for reason in row['reasons']) for row in unit['review_outcome']['upstream'])
