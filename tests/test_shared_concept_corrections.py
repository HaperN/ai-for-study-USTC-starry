"""Shared corrections reuse a real body review and its existing repair budget."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.core.review_mode import review_mode
from aiforstudy.pipeline import content_stages as stages
from test_content_generation_v3 import context
from test_balanced_content_review import Client

OLD = r'$zz=|z|^2$'
NEW = r'$z\bar z=|z|^2$'


def correction(cid='CA', old=OLD, new=NEW):
    return {'id':'fix-'+cid, 'original':old, 'corrected':new,
            'reason':'模平方应与共轭相乘。',
            'source_refs':[{'kind':'concept', 'concept_id':cid, 'json_pointer':'/formal'}]}


def setup(tmp_path):
    ctx = context(tmp_path)
    write_json_atomic(ctx.context.parse/'concepts.json', {'concepts':[
        {'id':'CA', 'name':'模平方', 'formal':OLD, 'ownership_unit':'U'},
        {'id':'CB', 'name':'另一概念', 'formal':'保持原值', 'ownership_unit':'V'}]})
    return ctx


def test_review_sees_corrected_public_entry_before_it_can_become_shared(tmp_path, monkeypatch):
    ctx = setup(tmp_path)
    client = Client([{'body_markdown':'使用修正后的模平方关系。', 'corrections':[correction()]},
                     {'verdicts':[{'status':'pass', 'reason':'已核对修正后的公共定义和正文。'}]}])
    monkeypatch.setattr(stages._t(), '_gateway_client', lambda *_a, **_k:(client,'offline'))
    with review_mode('balanced'):
        assert stages.lesson_stage(ctx).execution_state.value == 'succeeded'
        before = stages._frozen_context(ctx)
        assert next(c for c in before['concept_index']['concepts'] if c['id']=='CA')['formal'] == OLD
        result = stages.text_audit_stage(context(tmp_path, 'text_audit'))
        assert result.quality_state.value == 'accepted', result.error
    request = json.loads(client.calls[1][0][1]['content'])['frozen_context']
    source = request['source_context']
    assert next(c for c in source['concept_index']['concepts'] if c['id']=='CA')['formal'] == NEW
    assert next(c for c in source['reference_catalog']['entries'] if c['id']=='CA')['markdown'] == NEW


def test_shared_correction_does_not_expand_prior_theorem_proof_context(tmp_path,monkeypatch):
    ctx=setup(tmp_path)
    index=read_json(ctx.context.parse/'concepts.json')
    index['concepts'][0].update(type='theorem',ownership_unit='PRIOR',source_parts=[{'text':'PRIOR_PROOF_MUST_STAY_HIDDEN'}])
    write_json_atomic(ctx.context.parse/'concepts.json',index)
    client=Client([{'body_markdown':'使用修正式。','corrections':[correction()]},
                   {'verdicts':[{'status':'pass','reason':'已检查明确勘误。'}]}])
    monkeypatch.setattr(stages._t(),'_gateway_client',lambda *_a,**_k:(client,'offline'))
    with review_mode('balanced'):
        stages.lesson_stage(ctx); stages.text_audit_stage(context(tmp_path,'text_audit'))
    assert 'PRIOR_PROOF_MUST_STAY_HIDDEN' not in client.calls[1][0][1]['content']


def test_next_unit_uses_confirmed_correction_but_existing_source_stays_frozen(tmp_path, monkeypatch):
    ctx = setup(tmp_path)
    (ctx.context.parse/'source.md').write_text('本节复习模平方。', encoding='utf-8')
    client = Client([{'body_markdown':'已修正。', 'corrections':[correction()]},
                     {'verdicts':[{'status':'pass','reason':'修正成立。'}]},
                     {'body_markdown':'下一小节。'}])
    monkeypatch.setattr(stages._t(), '_gateway_client', lambda *_a, **_k:(client,'offline'))
    with review_mode('balanced'):
        stages.lesson_stage(ctx)
        before = stages._frozen_context(ctx)
        revision = (tmp_path/'work/products/content/U/revisions/000001.json').read_bytes()
        audit_ctx = deepcopy(ctx); audit_ctx.node.stage='text_audit'
        stages.text_audit_stage(audit_ctx)
        assert stages._frozen_context(ctx) == before
        next_ctx = deepcopy(ctx)
        next_ctx.node.scope_key='V'; next_ctx.node.node_id='lesson:V'
        chapters = read_json(next_ctx.context.parse/'chapters.json')
        chapters['lesson_units'].append({'unit_id':'V','title':'下一节','original_text':'source.md'})
        write_json_atomic(next_ctx.context.parse/'chapters.json',chapters)
        result = stages.lesson_stage(next_ctx)
        assert result.execution_state.value == 'succeeded', result.error
    request=json.loads(client.calls[-1][0][1]['content'])['frozen_context']
    assert next(c for c in request['concept_index']['concepts'] if c['id']=='CA')['formal'] == NEW
    assert next(c for c in stages._frozen_context(next_ctx)['concept_index']['concepts'] if c['id']=='CB')['formal'] == '保持原值'
    assert (tmp_path/'work/products/content/U/revisions/000001.json').read_bytes() == revision


def test_unaccepted_proposal_never_updates_shared_catalog(tmp_path, monkeypatch):
    ctx = setup(tmp_path)
    client = Client([{'body_markdown':'仍需核对。','corrections':[correction()]},
                     {'verdicts':[{'status':'fail','reason':'修正缺少条件。'}]}])
    monkeypatch.setattr(stages._t(),'_gateway_client',lambda *_a,**_k:(client,'offline'))
    with review_mode('balanced'):
        stages.lesson_stage(ctx); stages.text_audit_stage(context(tmp_path,'text_audit'))
    assert not (tmp_path/'work/products/concepts/corrections.json').exists()


def test_published_source_snapshot_keeps_real_profile_changes_visible(tmp_path,monkeypatch):
    from aiforstudy.blocks.content import content_policy
    from aiforstudy.blocks.content.source_context import LearnerProfileV1
    ctx=setup(tmp_path)
    monkeypatch.setattr(stages._t(),'_gateway_client',lambda *_a,**_k:(Client([{'body_markdown':'正文'}]),'offline'))
    with review_mode('balanced'):
        stages.lesson_stage(ctx)
        old=stages._frozen_context(ctx)
        monkeypatch.setattr(content_policy,'load_learner_profile',lambda:LearnerProfileV1(profile_id='changed-profile'))
        current=stages._frozen_context(ctx)
    assert current['learner_profile']['profile_id']=='changed-profile'
    assert current['source_context_hash']!=old['source_context_hash']


def test_concurrent_different_entries_merge_and_stale_same_entry_is_preserved(tmp_path):
    from aiforstudy.blocks.content.concept_corrections import proposed_corrections, publish_corrections, load_corrections
    ctx=setup(tmp_path); source=stages._frozen_context(ctx)
    def publish(cid, old, new):
        content={'unit_id':'U','corrections':[correction(cid,old,new)]}
        proposals,_=proposed_corrections(content,source)
        return publish_corrections(tmp_path/'work/products',proposals,content=content,
            reviewed_context=source,report={'decision':'accepted','verdicts':[{'status':'pass','reason':'已核对'}]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda args:publish(*args),[('CA',OLD,NEW),('CB','保持原值','更新后的另一概念')]))
    rows=load_corrections(tmp_path/'work/products')
    assert {r['concept_id']:r['after'] for r in rows} == {'CA':NEW,'CB':'更新后的另一概念'}
    assert all(r['review_report']['decision']=='accepted' for r in rows)
    added, conflicts=publish('CA',OLD,'另一个并发修正版')
    assert not added and len(conflicts)==1
    assert load_corrections(tmp_path/'work/products') == rows


def test_same_concept_different_field_updates_do_not_restore_old_formal(tmp_path):
    from aiforstudy.blocks.content.concept_corrections import proposed_corrections, publish_corrections, load_corrections, overlay_catalog
    ctx=setup(tmp_path); source=stages._frozen_context(ctx)
    source['concept_index']['concepts'][0]['definition']='旧定义文字'
    first={'unit_id':'U','corrections':[correction()]}
    second={'unit_id':'V','corrections':[correction('CA','旧定义文字','新定义文字')]}
    second['corrections'][0]['source_refs'][0]['json_pointer']='/definition'
    for content in (first,second):
        proposals,_=proposed_corrections(content,source)
        publish_corrections(tmp_path/'work/products',proposals,content=content,reviewed_context=source,
            report={'decision':'accepted','verdicts':[{'status':'pass','reason':'已复核'}]})
    revisions=load_corrections(tmp_path/'work/products')
    projected=overlay_catalog(source['reference_catalog'],revisions)
    assert next(r for r in projected['entries'] if r['id']=='CA')['markdown']==NEW
    assert revisions[-1]['entry']['formal']==NEW
    assert revisions[-1]['entry']['definition']=='新定义文字'


def test_only_explicitly_linked_old_error_in_body_needs_repair():
    from aiforstudy.blocks.content.concept_corrections import affected_body_paths
    revision={'concept_id':'CA','before':OLD}
    content={'unit_id':'V','body_markdown':'参看 [[CONCEPT:CA|模平方]]。'}
    assert affected_body_paths(content,revision)==[]
    content['body_markdown']+='因此 '+OLD+'。'
    assert affected_body_paths(content,revision)==['/body_markdown']
    content['body_markdown']='无关概念恰好含 '+OLD
    assert affected_body_paths(content,revision)==[]
    content['corrections']=[correction()]
    assert affected_body_paths(content,revision)==[]


def test_old_formula_inside_long_public_definition_is_located_in_linked_body():
    from aiforstudy.blocks.content.concept_corrections import affected_body_paths
    revision={'concept_id':'CA','before':'复数的模满足 '+OLD+'，从而可以计算模平方。',
              'after':'复数的模满足 '+NEW+'，从而可以计算模平方。'}
    content={'unit_id':'V','body_markdown':'依据 [[CONCEPT:CA|模平方]]，因此 '+OLD+'。'}
    assert affected_body_paths(content,revision)==['/body_markdown']


def test_final_corrected_text_containing_old_words_is_not_an_obsolete_assertion():
    from aiforstudy.blocks.content.concept_corrections import affected_body_paths
    revision={'concept_id':'CA','before':'正数','after':'正数或零'}
    content={'unit_id':'V','body_markdown':'依照 [[CONCEPT:CA|非负数]]，其取值为正数或零。'}
    assert affected_body_paths(content,revision)==[]


def test_old_formula_as_explicit_counterexample_is_not_host_confirmed_error():
    from aiforstudy.blocks.content.concept_corrections import catalog_review_outcome
    from test_theorem_index import row
    value=row('V','CA')
    value['content']['body_markdown']='依据 [[CONCEPT:CA|模平方]]，错误写法是 '+OLD+'，正确写法是 '+NEW+'。'
    value['reference_catalog']['entries'][0]['markdown']=OLD
    current=deepcopy(value['reference_catalog'])
    current['entries'][0].update(markdown=NEW,version='corrected')
    audit={'content_identity':{'reference_catalog':value['reference_catalog']}}
    with review_mode('balanced'):
        state=catalog_review_outcome(value['content'],current,audit,{'status':'verified','reasons':[]})
    assert state['status']=='unverified'


def test_counterexample_may_stay_unchanged_but_new_public_card_needs_current_review(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from aiforstudy.blocks.content.concept_corrections import proposed_corrections, publish_corrections
    from aiforstudy.pipeline.concept_repairs import collect_body_repairs
    ctx=setup(tmp_path)
    body='依据 [[CONCEPT:CA|模平方]]，错误写法是 '+OLD+'，正确写法是 '+NEW+'。'
    client=Client([{'body_markdown':body}, {'verdicts':[{'status':'pass','reason':'旧审核记录。'}]}])
    monkeypatch.setattr(stages._t(),'_gateway_client',lambda *_a,**_k:(client,'offline'))
    with review_mode('balanced'):
        stages.lesson_stage(ctx); stages.text_audit_stage(context(tmp_path,'text_audit'))
        source=stages._frozen_context(ctx)
        products=tmp_path/'work/products'
        before=read_json(products/'lesson/U.json')['payload']['content']
        client.answers=iter([{'schema_version':'ContentAuthoringV1',
            'content':before},
            {'verdicts':[{'status':'pass','reason':'已按新公共条目核实，旧式仅用作错误反例，无需修改正文。'}]}])
        draft={'unit_id':'OTHER','corrections':[correction()]}
        proposals,_=proposed_corrections(draft,source)
        publish_corrections(products,proposals,content=draft,reviewed_context=source,
            report={'decision':'accepted','verdicts':[{'status':'pass','reason':'修正成立。'}]})
        pending=collect_body_repairs(products)
        ids=pending['U']['result'].repair_requests[0].ticket_ids
        ctx.repair_batch=SimpleNamespace(batch_id='shared-context-check',round=1,
            jobs=[SimpleNamespace(owner='lesson',kind='text_content',ticket_ids=ids,artifact_id='lesson:U')])
        assert stages.lesson_stage(ctx).execution_state.value=='succeeded'
        result=stages.text_audit_stage(context(tmp_path,'text_audit'))
        assert result.quality_state.value=='accepted'
        assert collect_body_repairs(products)=={}
    assert len(client.calls)==4
    audit=read_json(products/'text_audit/U.json')['payload']
    after=read_json(products/'lesson/U.json')['payload']['content']
    assert {k:v for k,v in after.items() if k not in ('revision','source_context_hash')}=={
        k:v for k,v in before.items() if k not in ('revision','source_context_hash')}
    assert audit['review_outcome']['status']=='verified'
    assert set(ids)<=set(audit['closed_ticket_ids'])
    assert next(r for r in audit['content_identity']['reference_catalog']['entries'] if r['id']=='CA')['markdown']==NEW


def test_reader_projection_refreshes_old_references_without_rewriting_or_reusing_old_pass(tmp_path):
    from test_theorem_index import row, save
    from aiforstudy.blocks.content.theorem_index import load_theorem_rows
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.blocks.content.concept_corrections import proposed_corrections, publish_corrections
    ctx=setup(tmp_path); source=stages._frozen_context(ctx)
    old=row('V','CV')
    old['content']['body_markdown']='参看 [[CONCEPT:CA|模平方]]。'
    old['reference_catalog']['entries'].append({'id':'CA','kind':'CONCEPT','label':'模平方',
        'unit_id':'U','markdown':OLD,'version':'old'})
    with review_mode('balanced'):
        old['reader_document']=project_reader(old['content'],old['reference_catalog']).as_dict()
        save(tmp_path,old)
        before=(tmp_path/'work/products/lesson/V.json').read_bytes()
        draft={'unit_id':'U','corrections':[correction()]}
        proposals,_=proposed_corrections(draft,source)
        publish_corrections(tmp_path/'work/products',proposals,content=draft,reviewed_context=source,
            report={'decision':'accepted','verdicts':[{'status':'pass','reason':'已复核'}]})
        rows=load_theorem_rows(tmp_path)
    assert len(rows)==1
    assert rows[0]['review_outcome']['status']=='unverified'
    texts=[b['raw_text'] for b in rows[0]['reader_document']['blocks'] if b.get('owner_id')=='CA']
    assert NEW in texts and OLD not in texts
    assert (tmp_path/'work/products/lesson/V.json').read_bytes()==before
    from aiforstudy.blocks.render.diagnostics import build_diagnostic_preview
    write_json_atomic(tmp_path/'state.json',{'book_id':'B'})
    with review_mode('balanced'):
        report=build_diagnostic_preview(tmp_path,unit_ids=['V'],out_dir=tmp_path.parent/(tmp_path.name+'-preview'))
    from pathlib import Path
    data=json.loads((Path(report['output_dir'])/'data/data.js').read_text().split('=',1)[1].strip().removesuffix(';'))
    blocks=data['units']['V']['reader_document']['blocks']
    assert NEW in [b['raw_text'] for b in blocks if b['owner_id']=='CA']
    assert data['units']['V']['review_outcome']['status']=='unverified'
    assert report['source_hashes']['work/products/concepts/corrections.json']


def test_confirmed_body_impact_dispatches_only_affected_unit_with_existing_two_round_cap(tmp_path):
    from aiforstudy.blocks.content.concept_corrections import proposed_corrections, publish_corrections
    from aiforstudy.pipeline.concept_repairs import collect_body_repairs
    from aiforstudy.pipeline.repair import RepairCoordinator, UnitRepairState, StopDecision
    from aiforstudy.pipeline.execution_control import ExecutionPolicy
    from test_theorem_index import row, save
    ctx=setup(tmp_path); source=stages._frozen_context(ctx)
    with review_mode('balanced'):
        for unit, body in [('V','依据 [[CONCEPT:CA|模平方]] 得 '+OLD),
                           ('W','仅回看 [[CONCEPT:CA|模平方]]。'),('X','无关的正文')]:
            old=row(unit,'C'+unit); old['content']['body_markdown']=body
            old['reference_catalog']['entries'].append({'id':'CA','kind':'CONCEPT','label':'模平方',
                'unit_id':'U','markdown':OLD,'version':'old'})
            save(tmp_path,old)
        content={'unit_id':'U','corrections':[correction()]}
        proposals,_=proposed_corrections(content,source)
        publish_corrections(tmp_path/'work/products',proposals,content=content,reviewed_context=source,
            report={'decision':'accepted','verdicts':[{'status':'pass','reason':'已复核'}]})
        pending=collect_body_repairs(tmp_path/'work/products')
    assert set(pending)=={'V'}
    result=pending['V']['result']
    evidence=pending['V']['notice']['tickets'][0]['previous_attempt']
    assert evidence['context_check_required'] is True
    assert 'confirmed_content_hash' not in evidence
    assert len(result.repair_requests)==1
    assert result.repair_requests[0].artifact_hashes.keys()=={'lesson:V'}
    coordinator=RepairCoordinator(ExecutionPolicy())
    state=UnitRepairState('V', content_rounds_by_artifact={'lesson:V':2})
    decision=coordinator.plan(list(result.repair_requests),state)
    assert isinstance(decision,StopDecision)
    assert decision.stopped_by=='content_rounds_exhausted'


def test_two_confirmed_revisions_repair_first_old_body_to_final_value(tmp_path):
    from aiforstudy.blocks.content.concept_corrections import proposed_corrections, publish_corrections, overlay_context, load_corrections
    from aiforstudy.pipeline.concept_repairs import collect_body_repairs
    from test_theorem_index import row, save
    ctx=setup(tmp_path); source=stages._frozen_context(ctx)
    final=r'$|z|^2=z\bar z$'
    with review_mode('balanced'):
        old=row('V','CV'); old['content']['body_markdown']='依据 [[CONCEPT:CA|模平方]] 得 '+OLD
        old['reference_catalog']['entries'].append({'id':'CA','kind':'CONCEPT','label':'模平方',
            'unit_id':'U','markdown':OLD,'version':'old'})
        save(tmp_path,old)
        for before,after in ((OLD,NEW),(NEW,final)):
            content={'unit_id':'U','corrections':[correction('CA',before,after)]}
            proposals,_=proposed_corrections(content,source)
            publish_corrections(tmp_path/'work/products',proposals,content=content,reviewed_context=source,
                report={'decision':'accepted','verdicts':[{'status':'pass','reason':'已复核'}]})
            source=overlay_context(source,load_corrections(tmp_path/'work/products'))
        pending=collect_body_repairs(tmp_path/'work/products')
    assert set(pending)=={'V'}
    assert OLD in pending['V']['notice']['tickets'][0]['problem']
    assert final in pending['V']['notice']['tickets'][0]['problem']
    assert pending['V']['notice']['correction_versions']==[load_corrections(tmp_path/'work/products')[-1]['version']]


@pytest.mark.parametrize('exhausted',[False,True])
def test_runner_refreshes_completed_reference_pages_and_repairs_only_old_body(tmp_path,exhausted):
    from collections import Counter
    from aiforstudy.pipeline.graph import ExecutionGraph, Node
    from aiforstudy.pipeline.runner import Runner
    from aiforstudy.pipeline.services import StageRegistry
    from aiforstudy.contracts.stage import StageResult
    from aiforstudy.contracts.enums import QualityState
    from aiforstudy.contracts.artifact import ArtifactRef
    from aiforstudy.blocks.content.concept_corrections import proposed_corrections, publish_corrections, project_current_catalog
    from test_balanced_mode import _runner
    from test_theorem_index import row, save
    base,_ = _runner(tmp_path,QualityState.ACCEPTED)
    counts=Counter()
    def stage(ctx):
        unit=ctx.node.scope_key; name=ctx.node.stage
        counts[(unit,name)]+=1
        products=ctx.services.store.work/'products'
        if name=='lesson':
            value=row(unit,'C'+unit)
            value['reference_catalog']['entries'].append({'id':'CA','kind':'CONCEPT','label':'模平方',
                'unit_id':'U','markdown':OLD,'version':'old'})
            value['reference_catalog']=project_current_catalog(products,value['reference_catalog'])
            value['content']['body_markdown']=('依据 [[CONCEPT:CA|模平方]] 得 '+
                (NEW if ctx.repair_batch else OLD) if unit=='V' else
                '仅回看 [[CONCEPT:CA|模平方]]。' if unit=='W' else '独立正文。')
            save(ctx.services.store.root,value)
            if unit=='V' and exhausted:
                audit_path=products/'text_audit/V.json'
                record=read_json(audit_path)
                record['payload']['review_outcome']={'status':'known_error','reasons':['既有模型审核已确认V把旧式作为正确计算依据。']}
                write_json_atomic(audit_path,record)
        elif name=='text_audit' and unit=='U':
            source={'concept_index':{'concepts':[{'id':'CA','name':'模平方','formal':OLD}]},
                    'source_context_hash':'reviewed-source'}
            content={'unit_id':'U','corrections':[correction()]}
            proposals,_=proposed_corrections(content,source)
            publish_corrections(products,proposals,content=content,reviewed_context=source,
                report={'decision':'accepted','verdicts':[{'status':'pass','reason':'已核对'}]})
        elif name=='render':
            lesson=read_json(products/'lesson'/f'{unit}.json')['payload']
            catalog=project_current_catalog(products,lesson['content_identity']['reference_catalog'])
            write_json_atomic(products/'render'/f'{unit}.json',{'catalog':catalog,'body':lesson['content']['body_markdown']})
        path=products/name/f'{unit}.json'
        ref=ArtifactRef(book_id=ctx.context.book_id,kind=name+'.unit',logical_key=unit,
                        path=path,producer_stage=name).with_content()
        return StageResult.ok(name,quality_state=QualityState.ACCEPTED,outputs=(ref,))
    nodes=[]
    for unit in ('V','W','X','U'):
        dependencies=tuple('render:'+u for u in ('V','W','X')) if unit=='U' else ()
        nodes.extend([Node('lesson:'+unit,'lesson',unit,requires=dependencies),
                      Node('text_audit:'+unit,'text_audit',unit,requires=('lesson:'+unit,)),
                      Node('render:'+unit,'render',unit,requires=('text_audit:'+unit,))])
    base.services.stages=StageRegistry({name:stage for name in ('lesson','text_audit','render')})
    runner=Runner(base.services,ExecutionGraph(tuple(nodes),goal='deliver',profile='release'),base.store)
    if exhausted:
        runner._repair_state('V').content_rounds_by_artifact={'lesson:V':2}
    runner.run()
    assert counts[('V','lesson')]==(1 if exhausted else 2)
    # 修改计划 C3（2026-10-20）：W 审核已通过且引用了被修正的卡 → 自动定向重审一次（不返修正文）。
    assert counts[('W','lesson')]==1 and counts[('W','text_audit')]==2
    assert counts[('W','render')]==2
    assert counts[('X','render')]==1 and counts[('X','lesson')]==1
    assert runner._repair_state('V').content_rounds_by_artifact=={'lesson:V':2 if exhausted else 1}
    rendered=read_json(base.store.work/'products/render/W.json')
    assert next(r for r in rendered['catalog']['entries'] if r['id']=='CA')['markdown']==NEW
    if exhausted:
        from aiforstudy.blocks.render.diagnostics import build_diagnostic_preview
        with review_mode('balanced'):
            report=build_diagnostic_preview(base.store.root,unit_ids=['V'],out_dir=tmp_path/'preview-exhausted')
        from pathlib import Path
        data=json.loads((Path(report['output_dir'])/'data/data.js').read_text().split('=',1)[1].strip().removesuffix(';'))
        assert data['units']['V']['review_outcome']['status']=='known_error'
        assert any(OLD in b['raw_text'] for b in data['units']['V']['reader_document']['blocks']
                   if b['json_pointer']=='/body_markdown')
        notices=list((base.store.work/'products/concepts/body-repairs/V').glob('*.json'))
        assert len(notices)==1 and read_json(notices[0])['dispatched']
