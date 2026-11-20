"""Production V5 stage functions, genuine files, host tickets and review receipts."""
import json
from copy import deepcopy
from types import SimpleNamespace
import pytest
from test_content_generation_v3 import context as stage_context
from test_teaching_policy_v2 import teaching_case
from test_source_inventory import response as inventory_response
from aiforstudy.blocks.content.source_context import SourceContext
from aiforstudy.blocks.content.content_contracts import parse_content
from aiforstudy.pipeline import content_stages as stages, stage_io as T


def _restore_omitted_obligations(content, source):
    """替身交回完整正文时，把底稿里被整条漏掉的来源义务按宿主模板补回去。

    这是**测试替身**的职责，不是生产代码：模型在整份重写里必须自己补全
    （判据来自 `source_inventory` 与教学策略），替身只是复现那个答复。
    补的对象沿用 `_missing_obligation` 的模板，与旧补丁路径给出的
    `allowed_additions` 同一形状，免得测试自己发明一套字段。
    """
    plan=content['teaching_plan']
    for obligation in source.source_obligations:
        oid=obligation.id
        if any(any(ref.get('obligation_id')==oid for ref in claim['source_refs'])
               for claim in plan['claims']):
            continue
        cid='K-'+oid; gid='G-'+oid
        plan['claims'].append({'id':cid,'kind':obligation.kind,
            'source_refs':[{'kind':'source','obligation_id':oid,'quote':obligation.source_excerpt}],
            'conditions':list(obligation.conditions),'conclusion':obligation.conclusion,'symbols':['n']})
        plan['goals'].append({'id':gid,'level':obligation.level,'claim_refs':[cid],
            'observable_task':'判断给定取值是否满足该约定'})
        content['teaching_evidence'] += [{'goal_id':gid,'claim_refs':[cid],
            'reader_pointer':'/body_markdown','purpose':purpose}
            for purpose in ('definition','understanding_check')]


class Client:
    def __init__(self, candidate, source, *, protocol_error=False, reject=True):
        self.candidate=candidate;self.source=source;self.calls=[];self.repaired=False
        self.protocol_error=protocol_error;self.reject=reject
    def chat_json(self,messages,**kw):
        tag=kw.get('tag',''); self.calls.append(tag)
        if tag.startswith('content-source-inventory:'):
            return {'data':inventory_response(self.source,extra=False)}
        payload=json.loads(messages[1]['content'])['frozen_context']
        if tag.startswith('content-author:'):
            from support.content_case import authoring
            return {'data':authoring(deepcopy(self.candidate))}
        if tag.startswith('content-repair:'):
            # D16-5：返修交出的是**完整正文**（ContentAuthoringV1），不是 ContentPatchV1 补丁。
            # 底稿在 frozen_context.content；模型要以上一次正文为基础整份重写。
            from support.content_case import authoring
            self.repaired=True
            content=deepcopy(payload['content'])
            content['body_markdown']=self.source.source_text+' 请判断 n=0 是否允许，并说明理由。'
            _restore_omitted_obligations(content,self.source)
            return {'data':authoring(content)}
        if tag.startswith('content-review:'):
            if self.protocol_error:
                self.protocol_error=False
                return {'data':{'accepted':True}}
            rows=[]
            for item in payload['criteria']:
                refs=[]
                for kind in item['required_evidence_kinds']:
                    eid,ev=next((eid,ev) for eid,ev in payload['evidence_catalog'].items()
                        if ev['kind']==kind and item['subject_id'] in ev['subject_ids'])
                    refs.append({'evidence_id':eid,'quote':ev['text']})
                bad=self.reject and not self.repaired and item['criterion_id']=='SOURCE-FIDELITY:SO'
                rows.append({'criterion_id':item['criterion_id'],'version':item['version'],
                    'status':'fail' if bad else 'pass',
                    'reason':'正文把非负说成正，漏掉零；改为非负且包括零。' if bad else '来源条件和当前对应读者文字相符。',
                    'evidence_refs':refs})
            return {'data':{'verdicts':rows}}
        raise AssertionError(tag)


def setup(tmp_path,monkeypatch,**kw):
    c,s=teaching_case(); c['body_markdown']='n 表示正整数，不含零。'
    source=SourceContext.model_validate(s).model_copy(update={'obligation_extraction_status':'structural_only'})
    source=source.model_copy(update={'source_context_hash':source.digest})
    monkeypatch.setattr(stages,'_structural_context',lambda ctx:source)
    client=Client(c,source,**kw)
    monkeypatch.setattr(T,'_gateway_client',lambda *a,**k:(client,'scripted-offline'))
    return client


def audit_payload(ctx):
    return json.loads((stages._root(ctx)/'text_audit/U.json').read_text())['payload']


def test_real_stage_author_review_patch_closes_original_criterion(tmp_path,monkeypatch):
    client=setup(tmp_path,monkeypatch)
    ctx=stage_context(tmp_path)
    result=stages.lesson_stage(ctx)
    assert result.execution_state.value=='succeeded', result.error
    assert len(client.calls)==2
    ctx.node.stage='text_audit'
    first=stages.text_audit_stage(ctx)
    assert first.execution_state.value=='succeeded', first.error
    payload=audit_payload(ctx);tickets=payload['tickets']
    assert len(tickets)==1 and tickets[0]['status']=='ready'
    assert tickets[0]['criteria'][0]['criterion_id']=='SOURCE-FIDELITY:SO'
    batch=SimpleNamespace(jobs=[SimpleNamespace(owner='lesson',kind='text_content',ticket_ids=[tickets[0]['ticket_id']])])
    ctx.repair_batch=batch;ctx.node.stage='lesson'
    patched=stages.lesson_stage(ctx)
    assert patched.execution_state.value=='succeeded',patched.error
    ctx.node.stage='text_audit'
    second=stages.text_audit_stage(ctx)
    assert second.quality_state.value=='accepted', second.error
    payload=audit_payload(ctx)
    assert tickets[0]['ticket_id'] in payload['closed_ticket_ids']
    content,approval=stages.load_content(ctx,approved=True)
    assert content['revision']==2 and approval['schema_version']=='ContentApprovalV2'
    assert client.calls.count('content-source-inventory:U')==1
    assert payload['frozen_tickets'][0]['criteria']==tickets[0]['criteria']


def test_protocol_failure_dispatches_reviewer_without_reauthoring(tmp_path,monkeypatch):
    client=setup(tmp_path,monkeypatch,protocol_error=True,reject=False)
    ctx=stage_context(tmp_path)
    assert stages.lesson_stage(ctx).execution_state.value=='succeeded'
    ctx.node.stage='text_audit';first=stages.text_audit_stage(ctx)
    assert first.execution_state.value=='succeeded',first.error
    ticket=audit_payload(ctx)['tickets'][0]
    assert ticket['owner']=='reviewer' and ticket['kind']=='evidence_format'
    ctx.repair_batch=SimpleNamespace(jobs=[SimpleNamespace(owner='reviewer',kind='evidence_format',ticket_ids=[ticket['ticket_id']])])
    second=stages.text_audit_stage(ctx)
    assert second.quality_state.value=='accepted',second.error
    assert client.calls.count('content-author:U')==1
    assert ticket['ticket_id'] in audit_payload(ctx)['closed_ticket_ids']


def test_omitted_whole_source_obligation_is_added_with_host_ids_and_reviewed(tmp_path,monkeypatch):
    client=setup(tmp_path,monkeypatch,reject=False)
    client.candidate['teaching_plan']['claims']=[]
    client.candidate['teaching_plan']['goals']=[]
    client.candidate['teaching_evidence']=[]
    ctx=stage_context(tmp_path)
    assert stages.lesson_stage(ctx).execution_state.value=='succeeded'
    ctx.node.stage='text_audit';result=stages.text_audit_stage(ctx)
    assert result.execution_state.value=='succeeded',result.error
    tickets=audit_payload(ctx)['tickets']
    assert tickets and all(t['status']=='ready' for t in tickets)
    assert any(t.startswith('content-review:') for t in client.calls)
    ctx.node.stage='lesson';ctx.repair_batch=SimpleNamespace(jobs=[SimpleNamespace(owner='lesson',kind='text_content',ticket_ids=[t['ticket_id'] for t in tickets])])
    result=stages.lesson_stage(ctx)
    assert result.execution_state.value=='succeeded',result.error
    ctx.node.stage='text_audit';result=stages.text_audit_stage(ctx)
    assert result.quality_state.value=='accepted',result.error
    content,approval=stages.load_content(ctx,approved=True)
    assert content['teaching_plan']['claims'][0]['id']=='K-SO'
    assert set(t['ticket_id'] for t in tickets)<=set(approval['closed_ticket_ids'])


def test_post_media_content_ticket_requires_its_original_standard_and_reissues_receipt(tmp_path,monkeypatch):
    from aiforstudy.pipeline.diagnostics import technical_ticket
    from aiforstudy.core.digest import digest_of
    from aiforstudy.core.jsonio import write_json_atomic
    from aiforstudy.errors import ContractError
    client=setup(tmp_path,monkeypatch,reject=False)
    ctx=stage_context(tmp_path)
    assert stages.lesson_stage(ctx).execution_state.value=='succeeded'
    ctx.node.stage='text_audit'
    assert stages.text_audit_stage(ctx).quality_state.value=='accepted'
    content,old_approval=stages.load_content(ctx,approved=True)
    ticket=technical_ticket(unit_id='U',artifact_id='lesson:U',artifact_hash=digest_of(content),
        problem='图示包含零，正文却排除零',source=content['body_markdown'],owner='lesson',kind='text_content',
        allowed_paths=('/body_markdown',),rule='POST_MEDIA_CONTENT',action='恢复包含零的条件并核对图文。',
        expected='正文明确包含零，和批准图示以及教材条件一致。').as_dict()
    write_json_atomic(stages._root(ctx)/'figures/U.json',{'payload':{'tickets':[ticket]}})
    with pytest.raises(ContractError,match='revoked'): stages.load_content(ctx,approved=True)
    ctx.node.stage='lesson';ctx.repair_batch=SimpleNamespace(jobs=[SimpleNamespace(owner='lesson',kind='text_content',ticket_ids=[ticket['ticket_id']])])
    result=stages.lesson_stage(ctx)
    assert result.execution_state.value=='succeeded',result.error
    # Recheck without carrying the scheduler batch: the persisted authority must survive.
    ctx.repair_batch=None;ctx.node.stage='text_audit'
    result=stages.text_audit_stage(ctx)
    assert result.quality_state.value=='accepted',result.error
    current,receipt=stages.load_content(ctx,approved=True)
    assert receipt!=old_approval and ticket['ticket_id'] in receipt['closed_ticket_ids']
    payload=audit_payload(ctx)
    assert payload['repair_authorities'][0]['criteria']==ticket['criteria']
    reviewed=payload['check_reports'][1]['verdicts']
    assert any(r['criterion_id'].startswith('external:'+ticket['ticket_id']) and r['status']=='pass' for r in reviewed)
    payload['repair_authorities'][0]['criteria'][0]['expected']='随意即可'
    write_json_atomic(stages._root(ctx)/'text_audit/U.json',{'payload':payload})
    with pytest.raises(ContractError): stages.load_content(ctx,approved=True)
