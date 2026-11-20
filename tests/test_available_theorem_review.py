"""New review/repair requests carry names only; existing journals retain their input."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from aiforstudy.blocks.content.content_contracts import content_digest
from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.reader_document import project_reader
from aiforstudy.blocks.content.review import review_content
from aiforstudy.core.review_mode import review_mode
from aiforstudy.pipeline import content_stages as stages
from support.content_case import authoring, teaching_case
from test_available_theorem_generation import EXPLANATION, _install_available
from test_balanced_content_review import Client, verdicts
from test_content_generation_v3 import context

NAMES = [EXPLANATION['name']]


def stage_context(tmp_path):
    ctx=context(tmp_path)
    path=ctx.context.parse/'chapters.json'
    chapters=json.loads(path.read_text())
    chapters['lesson_units'][:0]=[{'unit_id':'U10'}, {'unit_id':'U2'}]
    path.write_text(json.dumps(chapters))
    return ctx


def requests(client, prefix):
    return [json.loads(messages[1]['content'])['frozen_context']
            for messages, meta in client.calls if meta['tag'].startswith(prefix)
            and not meta.get('syntax_repair')]


def assert_syntax_only(client):
    corrections = [messages for messages, meta in client.calls if meta.get('syntax_repair')]
    assert corrections
    for messages in corrections:
        payload = json.loads(messages[-1]['content'])
        assert 'frozen_context' not in payload
        assert 'available_theorem_names' not in payload
        assert 'available_theorem_explanations' not in payload
        assert EXPLANATION['markdown'] not in json.dumps(payload, ensure_ascii=False)


def forbid_scan(*_args, **_kwargs):
    pytest.fail('已有冻结输入时，不得重新扫描前序讲解')


def save_messages(tmp_path,client):
    (tmp_path/'actual-model-messages.json').write_text(json.dumps(client.calls,ensure_ascii=False,indent=2))


def test_actual_review_stage_reads_completed_material_once_and_replays_corrections(tmp_path,monkeypatch):
    ctx=stage_context(tmp_path);scans=[];material=[]
    def available(run,unit,*,unit_order):
        scans.append((run,unit,unit_order));return deepcopy(material)
    _install_available(monkeypatch,available)
    client=Client([authoring(teaching_case()[0]), '{"verdicts":', verdicts()])
    monkeypatch.setattr(stages._t(),'_gateway_client',lambda *_a,**_k:(client,'offline'))
    with review_mode('balanced'):
        assert stages.lesson_stage(ctx).execution_state.value=='succeeded'
        material[:]=[EXPLANATION,deepcopy(EXPLANATION)]  # Completed after author start; names need no duplicates.
        original=stages._frozen_context(ctx)
        ctx.node.stage='text_audit'
        result=stages.text_audit_stage(ctx)
        assert result.execution_state.value=='succeeded',result.error
        _install_available(monkeypatch,forbid_scan)
        again=stages.text_audit_stage(ctx)
        assert again.execution_state.value=='succeeded',again.error
        assert stages._frozen_context(ctx)==original
    assert scans==[(tmp_path,'U',['U10','U2','U'])]*2
    sent=requests(client,'content-review')
    assert len(sent)==1 and len(client.calls)==3
    assert_syntax_only(client)
    assert '不要求补交前文证明' in client.calls[1][0][0]['content']
    assert '后续 CONCEPT 复用已提供的同一定理讲解' not in client.calls[1][0][0]['content']
    for frozen in sent:
        assert frozen['available_theorem_names']==NAMES
        assert 'available_theorem_explanations' not in frozen
        assert EXPLANATION['markdown'] not in json.dumps(frozen,ensure_ascii=False)
        assert 'available_theorem_names' not in frozen['source_context']
        assert 'available_theorem_explanations' not in frozen['source_context']
        assert frozen['source_context']==original
    h=content_digest(sent[0]['candidate_content'])
    stored=json.loads((tmp_path/'work/products/content/U/review-journals'/
                       (h.split(':')[-1]+'.input.json')).read_text())
    assert stored['available_theorem_names']==NAMES
    assert 'available_theorem_explanations' not in stored
    save_messages(tmp_path,client)


def test_full_review_content_sends_the_same_optional_material_without_mutating_source():
    from test_feedback import review_case
    from test_rubric import ScopedReviewer
    content,source,_,criterion,_,_=review_case();before=deepcopy(source)
    client=ScopedReviewer()
    with review_mode('full'):
        report=review_content(content,project_reader(content,source['reference_catalog']),
            source,[criterion],None,client,available_theorem_names=NAMES)
    assert report.decision=='accepted'
    assert client.requests[0]['available_theorem_names']==NAMES
    assert 'available_theorem_explanations' not in client.requests[0]
    assert client.requests[0]['source_context']==before and source==before


def prepare_repair(tmp_path,monkeypatch,material):
    ctx=stage_context(tmp_path)
    _install_available(monkeypatch,lambda *_a,**_k:deepcopy(material))
    draft=authoring(teaching_case()[0]);fixed=deepcopy(draft)
    fixed['content']['body_markdown']+=' 当前应用先检查非负条件。'
    client=Client([draft,{'verdicts':[{'status':'fail',
        'reason':'当前例题直接套用前文结论，没有核对非负条件；请补明当前应用条件。'}]},
        '{"content":',fixed])
    monkeypatch.setattr(stages._t(),'_gateway_client',lambda *_a,**_k:(client,'offline'))
    assert stages.lesson_stage(ctx).execution_state.value=='succeeded'
    ctx.node.stage='text_audit';audit=stages.text_audit_stage(ctx)
    assert audit.execution_state.value=='succeeded',audit.error
    assert len(audit.repair_requests)==1
    ctx.node.stage='lesson'
    ctx.repair_batch=SimpleNamespace(batch_id='theorem-repair',round=1,
        jobs=[SimpleNamespace(owner='lesson',kind='text_content',artifact_id='lesson:U',
            ticket_ids=audit.repair_requests[0].ticket_ids)])
    content=stages.load_content(ctx)[0];h=content_digest(content)
    review_input=tmp_path/'work/products/content/U/review-journals'/(h.split(':')[-1]+'.input.json')
    return ctx,client,review_input


@pytest.mark.parametrize('material', [[EXPLANATION], []])
def test_repair_uses_its_draft_review_snapshot_even_when_empty_and_replays_after_interruption(tmp_path,monkeypatch,material):
    with review_mode('balanced'):
        ctx,client,review_input=prepare_repair(tmp_path,monkeypatch,material)
        _install_available(monkeypatch,forbid_scan)
        publish=stages._publish_revision
        monkeypatch.setattr(stages,'_publish_revision',lambda *_a,**_k:(_ for _ in ()).throw(OSError('offline interruption')))
        first=stages.lesson_stage(ctx)
        assert first.execution_state.value=='blocked'
        saved=json.loads(review_input.read_text())
        saved['available_theorem_names']=['后来更新，不应混入重放']
        review_input.write_text(json.dumps(saved))
        monkeypatch.setattr(stages,'_publish_revision',publish)
        replay=stages.lesson_stage(ctx)
        assert replay.execution_state.value=='succeeded',replay.error
    sent=requests(client,'content-repair')
    assert len(sent)==1 and len(client.calls)==4
    assert_syntax_only(client)
    for frozen in sent:
        assert frozen['available_theorem_names']==[row['name'] for row in material]
        assert 'available_theorem_explanations' not in frozen
        assert EXPLANATION['markdown'] not in json.dumps(frozen,ensure_ascii=False)
        assert frozen['content']['body_markdown']==teaching_case()[0]['body_markdown']
        assert 'available_theorem_explanations' not in frozen['source_context']
    saved=json.loads(stages._generation_journal(ctx,phase='repair').with_suffix('.input.json').read_text())
    assert saved['available_theorem_names']==[row['name'] for row in material]
    save_messages(tmp_path,client)


@pytest.mark.parametrize('old_input', ['missing','another-draft','legacy'])
def test_repair_without_a_matching_material_snapshot_reads_actual_prior_units_once(tmp_path,monkeypatch,old_input):
    with review_mode('balanced'):
        ctx,client,review_input=prepare_repair(tmp_path,monkeypatch,[EXPLANATION])
        if old_input=='missing':
            review_input.unlink()
        elif old_input=='another-draft':
            review_input.rename(review_input.with_name('different-content-hash.input.json'))
        else:
            old=json.loads(review_input.read_text());old.pop('available_theorem_names',None)
            review_input.write_text(json.dumps(old))
        fresh=[{**EXPLANATION,'name':'实际可用的前序定理'}];scans=[]
        def available(run,unit,*,unit_order):
            scans.append((run,unit,unit_order));return deepcopy(fresh)
        _install_available(monkeypatch,available)
        result=stages.lesson_stage(ctx)
        assert result.execution_state.value=='succeeded',result.error
    assert scans==[(tmp_path,'U',['U10','U2','U'])]
    assert all(frozen['available_theorem_names']==[fresh[0]['name']] for frozen in requests(client,'content-repair'))


@pytest.mark.parametrize('legacy_material', [None, [EXPLANATION]])
def test_old_review_input_and_journal_do_not_gain_new_message_fields(tmp_path,monkeypatch,legacy_material):
    ctx=stage_context(tmp_path)
    client=Client([authoring(teaching_case()[0]),verdicts()])
    monkeypatch.setattr(stages._t(),'_gateway_client',lambda *_a,**_k:(client,'offline'))
    with review_mode('balanced'):
        assert stages.lesson_stage(ctx).execution_state.value=='succeeded'
        content=stages.load_content(ctx)[0];h=content_digest(content)
        path=tmp_path/'work/products/content/U/review-journals'/(h.split(':')[-1]+'.input.json')
        path.parent.mkdir(parents=True,exist_ok=True)
        old={'previous_tickets':[], 'author_media':stages.load_author_media(ctx,content)['requirements']}
        if legacy_material is not None:
            old['available_theorem_explanations']=legacy_material
        path.write_text(json.dumps(old))
        _install_available(monkeypatch,forbid_scan)
        ctx.node.stage='text_audit'
        assert stages.text_audit_stage(ctx).execution_state.value=='succeeded'
        if legacy_material is None:
            path.unlink()  # Older runs may have the journal without its input file.
        again=stages.text_audit_stage(ctx)
        assert again.execution_state.value=='succeeded',again.error
    assert len(client.calls)==2
    sent=requests(client,'content-review')[0]
    assert 'available_theorem_names' not in sent
    assert sent.get('available_theorem_explanations')==legacy_material
    assert json.loads(path.read_text())==old


@pytest.mark.parametrize('legacy_material', [None, [EXPLANATION]])
def test_old_repair_input_and_journal_replay_without_new_material(tmp_path,monkeypatch,legacy_material):
    ctx=stage_context(tmp_path);ctx.repair_batch=SimpleNamespace(batch_id='old-repair',jobs=[])
    content,source=teaching_case();policy=load_content_policy(source['learner_profile'])
    journal=stages._generation_journal(ctx,phase='repair')
    journal.parent.mkdir(parents=True,exist_ok=True)
    old={'source_context':source,'content':content,'repair_tickets':[]}
    if legacy_material is not None:
        old['available_theorem_explanations']=legacy_material
    journal.with_suffix('.input.json').write_text(json.dumps(old))
    _install_available(monkeypatch,forbid_scan)
    client=Client([authoring(content)])
    with review_mode('balanced'):
        stages._repair_content(ctx,content,source,policy,client,[],None)
        stages._repair_content(ctx,content,source,policy,client,[],None)
    assert len(client.calls)==1
    sent=requests(client,'content-repair')[0]
    assert 'available_theorem_names' not in sent
    assert sent.get('available_theorem_explanations')==legacy_material
    assert json.loads(journal.with_suffix('.input.json').read_text())==old


@pytest.mark.parametrize('legacy_material', [[EXPLANATION], []])
def test_new_repair_extracts_only_names_from_its_old_review_input(tmp_path,monkeypatch,legacy_material):
    with review_mode('balanced'):
        ctx,client,review_input=prepare_repair(tmp_path,monkeypatch,[EXPLANATION])
        old=json.loads(review_input.read_text());old.pop('available_theorem_names',None)
        old['available_theorem_explanations']=legacy_material
        review_input.write_text(json.dumps(old))
        _install_available(monkeypatch,forbid_scan)
        result=stages.lesson_stage(ctx)
        assert result.execution_state.value=='succeeded',result.error
    for frozen in requests(client,'content-repair'):
        assert frozen['available_theorem_names']==[row['name'] for row in legacy_material]
        assert 'available_theorem_explanations' not in frozen
        assert EXPLANATION['markdown'] not in json.dumps(frozen,ensure_ascii=False)
    assert json.loads(review_input.read_text())==old


@pytest.mark.parametrize('initial,names', [
    ({'available_theorem_explanations':[EXPLANATION]}, NAMES),
    ({'available_theorem_explanations':[]}, []),
    ({'available_theorem_names':[], 'available_theorem_explanations':[EXPLANATION]}, []),
])
def test_new_no_draft_round_takes_only_names_from_legacy_author_input(tmp_path,monkeypatch,initial,names):
    ctx=stage_context(tmp_path)
    ctx.repair_batch=SimpleNamespace(batch_id='new-no-draft-round',round=1,jobs=[])
    old=tmp_path/'work/products/content/U/generation-journal.input.json'
    old.parent.mkdir(parents=True,exist_ok=True)
    old.write_text(json.dumps({'previous_rejection':None,**initial}))
    _install_available(monkeypatch,forbid_scan)
    client=Client([authoring(teaching_case()[0])])
    monkeypatch.setattr(stages._t(),'_gateway_client',lambda *_a,**_k:(client,'offline'))
    with review_mode('balanced'):
        result=stages.lesson_stage(ctx)
        assert result.execution_state.value=='succeeded',result.error
        old.write_text(json.dumps({'previous_rejection':None,'available_theorem_names':['后来的名称']}))
        replay=stages.lesson_stage(ctx)
        assert replay.execution_state.value=='succeeded',replay.error
    assert len(client.calls)==1
    frozen=requests(client,'content-author')[0]
    assert frozen['available_theorem_names']==names
    assert 'available_theorem_explanations' not in frozen
    assert EXPLANATION['markdown'] not in json.dumps(frozen,ensure_ascii=False)
    stored=json.loads(stages._generation_journal(ctx).with_suffix('.input.json').read_text())
    assert stored['available_theorem_names']==names
    assert 'available_theorem_explanations' not in stored
