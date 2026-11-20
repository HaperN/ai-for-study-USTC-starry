"""Real graph-5 engine with a fixed, inspectable offline model transport.

Only external model I/O is replaced. Source inventory, content checking, review
validation, approval, budget accounting, cache and Runner are production code.
"""
from copy import deepcopy
import json
from aiforstudy.contracts.enums import Profile
from aiforstudy.pipeline.engine import run_request, resume_request, build_engine, execute
from aiforstudy.pipeline.services import Providers
from aiforstudy.core.jsonio import read_json, write_json_atomic
from support.book_factory import _mini_book, _request, BOOK, UNIT, ScriptedTransport
from support.content_case import teaching_case, none_decision, authoring, repair_answer, IntentReviewer
from support.prompt_context import expand_prompt_material


class TeachingTransport(ScriptedTransport):
    def __init__(self, *, reject_first=False, refuse_repair=False, fail_protocol=False):
        super().__init__()
        self.reject_first=reject_first; self.refuse_repair=refuse_repair
        self.fail_protocol=fail_protocol; self.repaired=False
        self.author_calls=0; self.review_calls=0; self.patch_calls=0; self.content_repairs=0
        self.repair_bodies=[]
        self.intent_review_calls=0; self.intent_reviewer=IntentReviewer()

    def _answer(self, system, user):
        raw=json.loads(user)
        data=expand_prompt_material(raw,raw)
        if data.get('role_task') == 'source_inventory':
            text=data['source_text']
            obligations=[{k:v for k,v in row.items() if k in ('id','kind','level','concept_id','conditions','conclusion','source_excerpt')} for row in data['structural_obligations']]
            if not obligations:
                obligations=[{'id':'NEW-convention','kind':'convention','level':'core','concept_id':'',
                    'conditions':['n 是非负整数'],'conclusion':'n 可以取零','source_excerpt':text}]
            return json.dumps({'schema_version':'SourceInventoryV1',**data['source_identity'],
                'obligations':obligations,'coverage':[{'block_id':b['id'],
                    'obligation_ids':[o['id'] for o in obligations],
                    'reason':'本节只规定 n 为含零的非负整数，本段完整给出该约定。'} for b in data['source_blocks']],
                'unresolved':[]},ensure_ascii=False)
        task=data.get('role_task'); frozen=data.get('frozen_context',{})
        if task == 'author_media_intent_review':
            response=self.intent_reviewer.chat_json([{'role':'user','content':user}],
                tag='author-media-intent:offline')
            self.intent_review_calls+=1
            return json.dumps(response['data'],ensure_ascii=False)
        if task == 'author':
            self.author_calls+=1
            c,_=teaching_case();c.update(book_id=frozen['book_id'],unit_id=frozen['unit_id'])
            c['body_markdown']=frozen['source_text']
            obligation=frozen['source_obligations'][0]
            c['teaching_plan']['claims'][0].update(kind=obligation['kind'],
                conditions=obligation['conditions'],conclusion=obligation['conclusion'],
                source_refs=[{'kind':'textbook','obligation_id':obligation['id'],'quote':obligation['source_excerpt']}])
            if self.reject_first: c['body_markdown']='n 表示正整数，不含零。'
            return json.dumps(authoring(c),ensure_ascii=False)
        if task == 'repair':
            # D16-5：返修不再是补丁，而是「以上一次正文为底稿整份重写」。
            # 交付物必须是与首次生成同一格式的 ContentAuthoringV1 完整信封。
            self.patch_calls+=1; self.content_repairs+=1; self.repaired=True
            # 记下提示词里给的底稿：它必须**是上一次正文**，不是空手重生成。
            self.repair_bodies.append(frozen['content']['body_markdown'])
            return json.dumps(repair_answer(frozen,
                frozen['source_context']['source_text']+' 请判断 n=0 是否符合约定，并说明理由。'),
                ensure_ascii=False)
        if task == 'reviewer':
            self.review_calls+=1
            if self.fail_protocol:
                self.fail_protocol=False
                return json.dumps({'accepted':True})
            rows=[]
            for item in frozen['criteria']:
                refs=[]
                for kind in item['required_evidence_kinds']:
                    eid,ev=next((eid,ev) for eid,ev in frozen['evidence_catalog'].items()
                        if ev['kind']==kind and item['subject_id'] in ev['subject_ids'])
                    refs.append({'evidence_id':eid,'quote':ev['text']})
                bad=self.reject_first and (not self.repaired or self.refuse_repair) and item['rule_id']=='SOURCE-FIDELITY'
                rows.append({'criterion_id':item['criterion_id'],'version':item['version'],
                    'status':'fail' if bad else 'pass',
                    'reason':'正文将非负改为正，需恢复含零条件。' if bad else '固定约定的条件、零的边界和对应读者证据一致。',
                    'evidence_refs':refs})
            return json.dumps({'verdicts':rows},ensure_ascii=False)
        if 'candidates' in data:
            decisions=[]
            for target in data['candidates']:
                row=none_decision();row.update(target_id=target['target_id'],goal_id=target['target_id']);decisions.append(row)
            return json.dumps({'decisions':decisions,'requests':[]},ensure_ascii=False)
        raise AssertionError(f'unrecognized current protocol: {task!r}, {system[:70]!r}')


def setup_book(tmp_path):
    workspace=_mini_book(tmp_path)
    parse=workspace.root/'book/parse'
    text=teaching_case()[1]['source_text']
    chapters=read_json(parse/'chapters.json')
    for unit in chapters['lesson_units']:
        (parse/unit['original_text']).write_text(text,encoding='utf-8')
    write_json_atomic(parse/'concepts.json',{'_meta':{'schema_version':'1.0','file_type':'concepts'},'concepts':[]})
    for path in (parse/'concepts.parts').glob('*.json'):
        row=read_json(path);row['concepts']=[]
        from aiforstudy.core.digest import digest_file
        source=next(u for u in chapters['lesson_units'] if u['unit_id']==row['unit_id'])
        row['_meta']['text_sha256']=digest_file(parse/source['original_text'])
        write_json_atomic(path,row)
    return workspace


def run_book(tmp_path, *, transport=None, until='text_audit'):
    workspace=setup_book(tmp_path);transport=transport or TeachingTransport()
    request=_request()
    result,store,engine=run_request(request,workspace,providers=Providers(llm=transport),until=until)
    return result,store,engine,transport
