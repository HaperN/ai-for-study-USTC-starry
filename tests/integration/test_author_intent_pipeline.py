"""Actual Runner routes goal intent failures to bounded author-only repair."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from aiforstudy.core.jsonio import read_json
from support.book_factory import UNIT
from support.runner_v5 import TeachingTransport,run_book


class IntentTransport(TeachingTransport):
    def __init__(self,mode='repair'):
        super().__init__();self.mode=mode;self.intent_calls=0;self.intent_patch_calls=0;self.intent_repaired=False

    def _answer(self,system,user):
        data=json.JSONDecoder().raw_decode(user)[0]; task=data.get('role_task')
        if task=='author_media_intent_review':
            self.intent_calls+=1
            if self.mode=='malformed':return json.dumps({'verdicts':[]})
            status=('pass' if self.intent_repaired and self.mode=='repair' else
                    'unknown' if self.intent_repaired and self.mode=='unknown' else 'fail')
            return json.dumps({'verdicts':[{'goal_id':g['goal_id'],'status':status,
                'reason':'离线审核样本：需补充可观察请求。' if status=='fail' else '离线复核样本。',
                'reader_quote':g['explicit_reader_evidence'][0]['text'],'decision_quote':g['decision']['reason'],
                **({'failure_kind':'media_decision','reader_pointer':g['explicit_reader_evidence'][0]['pointer']} if status=='fail' else {})}
                for g in data['goals']]},ensure_ascii=False)
        if task=='author_media_repair':
            self.intent_patch_calls+=1;self.intent_repaired=True
            from test_author_media_requirements import math_case
            frozen=data['frozen_context']
            request=deepcopy(math_case()[2]['requirements']['requests'][0])
            c=frozen['content'];request['placement'].update(owner_id=c['unit_id'],anchor_text=c['body_markdown'])
            # D16-3：整份重写 —— 交回完整 AuthorMediaRequirementsV1。
            revised=deepcopy(frozen['author_media']['requirements'])
            revised['decisions'][0]={**revised['decisions'][0],'requirement':'required',
                'request_ids':[request['request_key']],
                'reason':'补充图示观察零这个边界。'+str(self.intent_patch_calls)}
            revised['requests']=revised['requests'] or [request]
            return json.dumps(revised,ensure_ascii=False)
        response=super()._answer(system,user)
        if task=='author':
            from test_author_media_requirements import math_case
            envelope=json.loads(response);c=envelope['content'];sample=math_case()[0]
            c['math_models']=deepcopy(sample['math_models'])
            c['math_models'][0]['source_refs']=[c['teaching_plan']['claims'][0]['source_refs'][0]['obligation_id']]
            c['teaching_plan']['claims'][0]['math_model_refs']=['M']
            response=json.dumps(envelope,ensure_ascii=False)
        return response


def test_engine_none_failure_adds_required_author_request_then_rechecks_current_revision(tmp_path):
    transport=IntentTransport()
    result,store,engine,_=run_book(tmp_path,transport=transport,until='media_plan')
    work=Path(store.work);plan=read_json(work/f'products/media_plan/{UNIT}.json')['payload']
    assert plan.get('schema_version')=='MediaRequirementsV3',result.as_dict()
    assert transport.author_calls==1 and transport.intent_patch_calls==1 and transport.intent_calls==2
    assert plan['decisions'][0]['requirement']=='required' and len(plan['requests'])==1
    assert plan['intent_review']['review_outcome']['status']=='verified'
    original=read_json(work/f'products/content/{UNIT}/revisions/000001.json')
    head=read_json(work/f'products/content/{UNIT}/current.json')
    current=read_json(head['content_ref']['path'])
    for field in ('body_markdown','math_models','teaching_plan','source_context_hash'):
        assert current[field]==original[field],field
    assert read_json(work/f'products/author_intent/{UNIT}/history.json')['unresolved']=={}


# Full-review fixtures share the current two-round default execution budget.
# Restoring a frozen legacy six-round run is covered separately.
@pytest.mark.parametrize('mode,expected_patches',[('always_fail',2),('unknown',1)])
def test_engine_failed_intent_has_finite_author_rounds_and_unknown_never_closes_it(tmp_path,mode,expected_patches):
    transport=IntentTransport(mode)
    result,store,engine,_=run_book(tmp_path,transport=transport,until='media_plan')
    work=Path(store.work)
    assert transport.intent_patch_calls==expected_patches,result.as_dict()
    assert transport.intent_calls==expected_patches+1
    assert not result.scope_accepted
    plan=read_json(work/f'products/media_plan/{UNIT}.json')['payload']
    assert plan.get('schema_version')!='MediaRequirementsV3'
    assert read_json(work/f'products/author_intent/{UNIT}/history.json')['unresolved']
    from aiforstudy.pipeline.engine import resume_request
    from aiforstudy.pipeline.services import Providers
    counts=(transport.intent_calls,transport.intent_patch_calls)
    resume_request(engine.request,store.run_id,engine.services.workspace,
        providers=Providers(llm=transport),until='media_plan')
    assert (transport.intent_calls,transport.intent_patch_calls)==counts


def test_engine_malformed_intent_is_unverified_and_rebuild_does_not_reset_retry_count(tmp_path):
    from aiforstudy.pipeline.engine import resume_request
    from aiforstudy.pipeline.services import Providers
    transport=IntentTransport('malformed')
    result,store,engine,_=run_book(tmp_path,transport=transport,until='media_plan')
    path=Path(store.work)/f'products/media_plan/{UNIT}.json'
    plan=read_json(path)['payload']
    assert plan['intent_review']['review_outcome']['status']=='unverified',result.as_dict()
    # 内层纠正已取消：坏判词一次即记为 unverified，不新增模型调用。
    assert transport.intent_calls==1 and transport.intent_patch_calls==0
    call_count=len(transport.calls)
    path.unlink()
    again,store,_=resume_request(engine.request,store.run_id,engine.services.workspace,
        providers=Providers(llm=transport),until='media_plan')
    assert read_json(path)['payload']['intent_review']==plan['intent_review'],again.as_dict()
    assert transport.intent_calls==1 and len(transport.calls)==call_count
