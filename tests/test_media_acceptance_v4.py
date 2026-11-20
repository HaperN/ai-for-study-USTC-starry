import pytest
from aiforstudy.core.digest import digest_of


def test_reviewer_does_not_reread_entire_content(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media_stages as M
    from test_media_plan_stage import ctx
    from support.content_case import approved_content as fixture, Planner, author_media
    content,approval=fixture()
    monkeypatch.setattr(M,'_load_author_media',lambda *a,**k:author_media(content))
    monkeypatch.setattr(M,'_load_content',lambda c:(content,approval)); monkeypatch.setattr(M,'_capabilities',lambda c:{'kinds':[]}); monkeypatch.setattr(M,'_client',lambda *a,**k:Planner())
    M.media_plan_stage(ctx(tmp_path,'media_plan')); M.figures_stage(ctx(tmp_path,'figures'))
    def forbidden(*a,**k): raise AssertionError('audit must not call model discover or re-generate')
    monkeypatch.setattr(M,'_client',forbidden)
    assert M.audit_stage(ctx(tmp_path,'audit')).quality_state.value=='accepted'


def test_existing_templates_verify_real_observable_changes():
    from aiforstudy.blocks.media.interaction_adapter import verify_template_response
    spec={'template':'vector-combination','params':{'vectors':[[1,0],[0,1]],'coefficients':[1,1]}}
    report=verify_template_response(spec)
    assert report['ok'] and report['before']!=report['after']
    spec['params']['vectors']=[[0,0]]; spec['params']['coefficients']=[1]
    assert not verify_template_response(spec)['ok']


def test_corrupt_resource_audit_dispatches_resource_repair(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media_stages as M
    from aiforstudy.core.jsonio import read_json
    from aiforstudy.core.digest import digest_file
    from pathlib import Path
    from test_source_image_replan_v5 import failed_source
    context,content,approval,raw,caps,plan,_,_,reviewer=failed_source(tmp_path,monkeypatch)
    reviewer.fail=None
    accepted=M.figures_stage(context)
    assert accepted.quality_state.value=='accepted',accepted.error
    manifest=read_json(M._path(context,'figures'))['payload']
    row=manifest['requests'][0];resource=row['resources'][0]
    assert row['repair_tickets'], 'exercise corruption after an earlier repair has passed'
    original_standards={('ticket:'+t['ticket_id']+':'+c['criterion_id'],str(c['version']))
        for t in row['repair_tickets'] for c in t['criteria']}
    path=Path(row['source_dir'])/resource['relative_path']
    assert digest_file(path)==resource['sha256']
    path.write_bytes(b'corrupt generated image bytes')
    context.node.stage='audit'
    outcome=M.audit_stage(context)
    assert outcome.quality_state.value=='needs_review',outcome.error
    assert any('字节摘要无效' in item['why'] for item in outcome.findings)
    assert outcome.repair_requests
    assert outcome.repair_routes[0]['target_artifact_kind']=='media_resource'
    audit=read_json(M._path(context,'audit'))['payload']
    ticket=next(t for t in audit['tickets'] if t['owner']=='visual_renderer')
    assert ticket['allowed_paths']==['/resources']
    assert {'/request','/spec','/acceptance_criteria'}<=set(ticket['preserve'])
    assert all(r['target_artifact_kind']=='media_resource' for r in outcome.repair_routes)

    # Repair consumes the persisted authority, restores from the same registered
    # source, and reviews both the original standards and the new byte obligation.
    from types import SimpleNamespace
    context.repair_batch=SimpleNamespace(jobs=[SimpleNamespace(ticket_ids=[ticket['ticket_id']],
        artifact_id=ticket['target']['artifact_id'],owner=ticket['owner'],kind=ticket['kind'])])
    restored=[];source_image=M._source_image
    def restore(c,request,target):
        restored.append(request['source_image_id'])
        return source_image(c,request,target)
    class NoSpecPlanning:
        def chat_json(self,*args,**kwargs): raise AssertionError('byte repair must keep the source request')
    monkeypatch.setattr(M,'_source_image',restore)
    monkeypatch.setattr(M,'_client',lambda c,role,bucket:reviewer if role=='judge' else NoSpecPlanning())
    context.node.stage='figures'
    repaired=M.figures_stage(context)
    assert repaired.quality_state.value=='accepted',repaired.error
    assert restored==['IMG1']
    current=read_json(M._path(context,'figures'))['payload']
    assert current['requests'][0]['request']==row['request']
    assert digest_file(path)==resource['sha256']
    assert any(item.get('ticket_id')==ticket['ticket_id'] for item in reviewer.seen[-1]['review_items'])
    assert original_standards<={(item['criterion_id'],str(item['version']))
        for item in reviewer.seen[-1]['review_items']}
    assert read_json(M._path(context,'media_plan'))['payload']==plan
    context.node.stage='audit';context.repair_batch=None
    closed=M.audit_stage(context)
    assert closed.quality_state.value=='accepted',closed.error
    assert read_json(M._path(context,'audit'))['payload']['tickets']==[]
