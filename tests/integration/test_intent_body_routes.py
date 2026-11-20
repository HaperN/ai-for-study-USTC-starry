"""R26: real Runner separates body and author-media repair transactions."""
from copy import deepcopy
import json
from pathlib import Path

from aiforstudy.core.jsonio import read_json
from support.book_factory import UNIT
from support.runner_v5 import TeachingTransport,run_book


class MixedIntentTransport(TeachingTransport):
    def __init__(self):
        super().__init__();self.intent_calls=0;self.media_patch_calls=0;self.patch_log=[];self.media_repaired=False
    def _answer(self,system,user):
        data=json.JSONDecoder().raw_decode(user)[0];task=data.get('role_task')
        if task=='author_media_intent_review':
            self.intent_calls+=1;rows=[]
            for index,goal in enumerate(data['goals']):
                # D16-5：正文返修已是整份重写，会走 author 通道；"正文改过没有"要看
                # 那批正文返修记录，而不是已退役的 content-patch 计数。
                body_repaired=bool(self.repair_bodies)
                # 媒体那一侧看的是"返修答复是否已经交过"这个事实标志：用计数器会在
                # "答复已发出但审核还没重跑"的时间窗里误判（返修调用自己就把计数加了）。
                fail=(not body_repaired) if index==0 else not self.media_repaired
                fields=goal['explicit_reader_evidence'];field=next(f for f in fields if f['pointer']=='/body_markdown')
                row=dict(goal_id=goal['goal_id'],status='fail' if fail else 'pass',
                    reason='补当前正文的判断任务。' if index==0 else '媒体理由需说明已有文字任务的作用。',
                    reader_quote=field['text'],decision_quote=goal['decision']['reason'])
                if fail:row.update(failure_kind='reader_content' if index==0 else 'media_decision',reader_pointer=field['pointer'])
                rows.append(row)
            return json.dumps({'verdicts':rows},ensure_ascii=False)
        if task=='author_media_repair':
            self.media_patch_calls+=1;self.media_repaired=True
            self.patch_log.append(('author_media',[t['allowed_paths'] for t in data['frozen_context']['repair_tickets']]))
            frozen=data['frozen_context']
            tickets=frozen['repair_tickets']
            assert len(tickets)==1 and tickets[0]['target']['artifact_id']=='author-media:'+UNIT
            # D16-3：整份重写 —— 交回完整 AuthorMediaRequirementsV1，不再是 patch operations。
            revised=deepcopy(frozen['author_media']['requirements'])
            revised['decisions'][1]={**revised['decisions'][1],
                'reason':revised['decisions'][1]['reason']+'已有文字任务足以完成目标。'}
            return json.dumps(revised,ensure_ascii=False)
        response=super()._answer(system,user)
        if task=='author':
            envelope=json.loads(response);content=envelope['content']
            content['teaching_plan']['goals'].append({**deepcopy(content['teaching_plan']['goals'][0]),'id':'G2'})
            content['teaching_evidence'] += [{**deepcopy(e),'goal_id':'G2'} for e in list(content['teaching_evidence'])]
            media=envelope['media_requirements'];media['decisions'].append({**deepcopy(media['decisions'][0]),'goal_id':'G2','target_id':'G2'})
            return json.dumps(envelope,ensure_ascii=False)
        return response


def test_real_engine_repairs_body_then_media_and_closes_current_criteria(tmp_path):
    transport=MixedIntentTransport()
    result,store,engine,_=run_book(tmp_path,transport=transport,until='media_plan')
    work=Path(store.work);plan=read_json(work/f'products/media_plan/{UNIT}.json')['payload']
    assert plan.get('schema_version')=='MediaRequirementsV3',result.as_dict()
    assert transport.author_calls==1, '整份重写走 author 通道，但那是同一批返修调用'
    assert transport.content_repairs==1, '正文只返修一次'
    assert transport.patch_calls==1 and transport.media_patch_calls==1
    assert [kind for kind,_ in transport.patch_log]==["author_media"], (
        '正文返修已整份重写（走 author 通道），作者媒体那一笔仍是补丁事务')
    # D16-5：返修的提示词里必须放**上一次正文**做底稿，而不是让模型空手重生成。
    from support.content_case import teaching_case
    source_text=teaching_case()[1]['source_text']
    assert transport.repair_bodies==[source_text], transport.repair_bodies
    assert transport.intent_calls==3
    assert plan['intent_review']['review_outcome']['status']=='verified'
    assert read_json(work/f'products/author_intent/{UNIT}/history.json')['unresolved']=={}
    audit=read_json(work/f'products/text_audit/{UNIT}.json')['payload']
    assert audit['closed_ticket_ids'], 'body defect needs explicit current external-criterion closure'
    original=read_json(work/f'products/content/{UNIT}/revisions/000001.json')
    current=read_json(read_json(work/f'products/content/{UNIT}/current.json')['content_ref']['path'])
    assert '请判断 n=0' in current['body_markdown']
    assert current['teaching_plan']==original['teaching_plan']
    assert current['original_sha256']==original['original_sha256']
