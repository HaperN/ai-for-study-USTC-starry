"""R26: intent review must inspect the same bounded text the reader can see."""
from copy import deepcopy
import json

import pytest

from aiforstudy.blocks.content.content_contracts import parse_content
from aiforstudy.blocks.media import intent_review as review
from aiforstudy.blocks.media.author_requirements import bind_author_requirements
from aiforstudy.core.digest import digest_of
from aiforstudy.errors import ContractError
from support.content_case import approved_content, author_media
from test_author_media_intent import Reviewer


def example_case():
    content, _ = approved_content()
    content['examples'] = [dict(id='E', kind='derived_example', label='例1',
        statement='证明零乘积性质。', approach='先证充分性，再证必要性。',
        steps=[dict(id='S1', markdown='若首因子为零，乘积为零。', basis='乘法定义。'),
               dict(id='S2', markdown='乘积为零且次因子非零时，用其逆元消去，首因子为零。', basis='逆元存在与结合律。')],
        conclusion='两个因子中至少一个为零。')]
    content['teaching_evidence'] = [dict(goal_id='G', purpose='example',
        reader_pointer='/examples/0/statement', claim_refs=['K']),
        dict(goal_id='G', purpose='understanding_check', reader_pointer='/examples/0/conclusion',claim_refs=['K'])]
    content = parse_content(content).as_dict()
    return content, author_media(content)


def test_review_receives_all_steps_and_basis_of_exact_example_owner():
    content, record = example_case()
    context = review.intent_context(content, record, {})
    goal = context['goals'][0]
    assert all(step['markdown'] in goal['reader_text'] and step['basis'] in goal['reader_text']
               for step in content['examples'][0]['steps'])
    assert [r['pointer'] for r in goal['explicit_reader_evidence']] == ['/examples/0/statement','/examples/0/conclusion']
    rows = {r['pointer']: r for r in goal['supplementary_reader_context']}
    for index, step in enumerate(content['examples'][0]['steps']):
        row = rows[f'/examples/0/steps/{index}/markdown']
        assert row['owner_id'] == 'E' and row['step_id'] == step['id']
        assert row['hash'] == digest_of(step['markdown']) and row['text'] == step['markdown']
    assert goal['reader_text'].index('乘法定义。') < goal['reader_text'].index('乘积为零且次因子非零时')


def test_context_never_borrows_other_example_or_same_id_in_another_group():
    content, _ = example_case()
    other = deepcopy(content['examples'][0]); other.update(id='E2', statement='另例题不能代替证明。')
    other['steps'] = [dict(id='S3', markdown='别例完整证明不能借用。',basis='别例依据。')]
    content['examples'].append(other)
    content['annotations'] = [dict(id='E',title='其他组同名',markdown='跨组同ID不能混入。')]
    content['teaching_plan']['goals'][0]['observable_task']='计划自述不能当正文。'
    content['original_text']='原始教材路径不能当读者正文。'
    record = author_media(content)
    goal = review.intent_context(content, record, {})['goals'][0]
    assert content['examples'][0]['steps'][0]['markdown'] in goal['reader_text']
    assert not any(text in goal['reader_text'] for text in ('另例题','别例完整','跨组同ID','计划自述','原始教材路径'))
    assert all(row['pointer'].startswith('/examples/0/') for row in goal['supplementary_reader_context'])


def test_body_evidence_does_not_expand_to_every_array_owner():
    content, _ = example_case()
    content['teaching_evidence'][0]['reader_pointer']='/body_markdown'
    content['teaching_evidence']=content['teaching_evidence'][:1]
    record=author_media(content)
    goal=review.intent_context(content,record,{})['goals'][0]
    assert goal['supplementary_reader_context']==[]
    assert goal['reader_text']==content['body_markdown']


def test_step_quote_is_valid_but_missing_step_is_not_invented():
    content, record=example_case()
    class Steps(Reviewer):
        def chat_json(self,messages,**kwargs):
            data=json.loads(messages[1]['content']);self.calls.append(data)
            return {'data':{'verdicts':[dict(goal_id='G',status='pass',reason='核对步骤。',
                reader_quote=content['examples'][0]['steps'][1]['markdown'],
                decision_quote=data['goals'][0]['decision']['reason'])]}}
    receipt=review.review_author_intent(content,record,{},Steps())
    assert receipt['review_outcome']['status']=='verified'
    content['examples'][0]['steps']=content['examples'][0]['steps'][:1]
    context=review.intent_context(content,author_media(content),{})
    assert '用其逆元消去' not in context['goals'][0]['reader_text']


def test_projection_identity_rejects_old_receipt_and_changes_journal_key():
    content, record=example_case()
    receipt=review.review_author_intent(content,record,{},Reviewer())
    legacy=deepcopy(receipt); legacy.pop('context_version',None)
    with pytest.raises(ContractError,match='receipt|过期'):
        review.validate_intent_review(legacy,content,record,{})
    old_key=digest_of({'content':digest_of(content),'author':digest_of(record),'capabilities':{},'rules':review.INTENT_RULES}).split(':')[-1]
    assert review.intent_review_key(content,record,{}) != old_key


class ClassifiedReviewer(Reviewer):
    def __init__(self, kind='reader_content', pointer='/body_markdown', *, mixed=False):
        super().__init__('fail');self.kind=kind;self.pointer=pointer;self.mixed=mixed
    def chat_json(self,messages,**kwargs):
        payload=json.loads(messages[1]['content']);self.calls.append(payload)
        rows=[]
        for index, goal in enumerate(payload['goals']):
            pointer=self.pointer if index==0 else '/body_markdown'
            fields=goal.get('explicit_reader_evidence',[])+goal.get('supplementary_reader_context',[])
            field=next((f for f in fields if f['pointer']==pointer),None)
            rows.append(dict(goal_id=goal['goal_id'],status='fail',reason='应补足当前字段的论证，使读者可复述依据。',
                reader_quote=field['text'] if field else goal['reader_text'],decision_quote=goal['decision']['reason'],
                failure_kind='media_decision' if self.mixed and index else self.kind,reader_pointer=pointer))
        return {'data':{'verdicts':rows}}


def stage_case(tmp_path,monkeypatch,content=None,reviewer=None):
    from aiforstudy.pipeline import media_stages as stages
    from aiforstudy.blocks.content.review_outcome import content_identity
    from support.content_case import reference_catalog
    from test_media_plan_stage import ctx
    base,approval=approved_content();content=content or base
    identity=content_identity(content,reference_catalog(),policy_digest=approval['policy_digest'])
    record=author_media(content)
    monkeypatch.setattr(stages,'_load_content',lambda *_:(content,identity))
    monkeypatch.setattr(stages,'_load_author_media',lambda *a,**k:record)
    monkeypatch.setattr(stages,'_capabilities',lambda *_:{'kinds':[]})
    monkeypatch.setattr(stages,'_client',lambda *a,**k:reviewer or ClassifiedReviewer())
    context=ctx(tmp_path,'media_plan');result=stages.media_plan_stage(context)
    return result,json.loads((tmp_path/'products/media_plan/U.json').read_text())['payload'],content,record


def test_media_summary_uses_bound_original_for_ready_body_feedback(tmp_path,monkeypatch):
    content,_=example_case()
    class Summary(ClassifiedReviewer):
        def chat_json(self,*args,**kwargs):
            response=super().chat_json(*args,**kwargs)
            response['data']['verdicts'][0]['reader_quote']='如果第一个因子等于零，那么乘积等于零。'
            return response
    result,payload,_,_=stage_case(tmp_path,monkeypatch,content,
        Summary(pointer='/examples/0/steps/0/markdown'))
    ticket=payload['tickets'][0]
    assert ticket['status']=='ready' and result.repair_routes
    assert ticket['source_evidence'][0]['quote']==content['examples'][0]['steps'][0]['markdown']
    assert ticket['previous_attempt']['intent_review']['verdicts'][0]['reader_quote']=='如果第一个因子等于零，那么乘积等于零。'


@pytest.mark.parametrize('pointer',['/examples/0/statement','/examples/0/book_solution'])
def test_frozen_source_fields_are_evidence_but_never_patch_authority(tmp_path,monkeypatch,pointer):
    content,_=example_case();content['examples'][0].update(kind='source',book_solution='原书解答不能改写。')
    result,payload,_,_=stage_case(tmp_path,monkeypatch,content,ClassifiedReviewer(pointer=pointer))
    assert payload['review_outcome']['status']=='known_error'
    assert payload['tickets'] and not result.repair_requests and not result.repair_routes
    assert payload['tickets'][0]['allowed_paths']==[]
    assert payload['tickets'][0]['status']!='ready'


@pytest.mark.parametrize('pointer',['/teaching_plan/goals/0/observable_task','/examples/1/steps/0/markdown'])
def test_reviewer_cannot_authorize_hidden_or_foreign_owner_pointer(pointer):
    content,record=example_case();client=ClassifiedReviewer(pointer=pointer)
    receipt=review.review_author_intent(content,record,{},client)
    # R3-1：内层纠正轮取消 —— 协议坏答复一次即判为 unverified。
    assert receipt['review_outcome']['status']=='unverified' and len(client.calls)==1


def test_unclassified_failure_never_guesses_media_only_authority():
    class LegacyFailure(Reviewer):
        def chat_json(self,*args,**kwargs):
            result=super().chat_json(*args,**kwargs)
            for row in result['data']['verdicts']:
                row.pop('failure_kind',None);row.pop('reader_pointer',None)
            return result
    content,record=example_case();client=LegacyFailure('fail')
    receipt=review.review_author_intent(content,record,{},client)
    assert receipt['review_outcome']['status']=='unverified' and len(client.calls)==1


def test_mixed_body_and_media_failures_queue_body_only_without_losing_media_ticket(tmp_path,monkeypatch):
    from aiforstudy.pipeline.repair import RepairCoordinator,UnitRepairState
    content,_=approved_content()
    content['teaching_plan']['goals'].append({**deepcopy(content['teaching_plan']['goals'][0]),'id':'G2'})
    content['teaching_evidence'].append({**deepcopy(content['teaching_evidence'][0]),'goal_id':'G2'})
    result,payload,_,_=stage_case(tmp_path,monkeypatch,content,ClassifiedReviewer(mixed=True))
    assert len(payload['tickets'])==2
    assert {r['target_artifact_kind'] for r in result.repair_routes}=={'content'}
    batch=RepairCoordinator(None).plan(list(result.repair_requests),UnitRepairState(unit_id='U'))
    assert {job.artifact_id for job in batch.jobs}=={'lesson:U'}
    assert payload['deferred_ticket_ids']==[t['ticket_id'] for t in payload['tickets'] if t['target']['artifact_id']=='author-media:U']
