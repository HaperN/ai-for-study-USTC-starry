"""Contract-valid offline content data. No approval or check is mocked.

The fixed verdict fixture describes this tiny, hand-written convention only.
The host reconstructs SourceContext, local checks, math and reader evidence.
"""
from copy import deepcopy
import json
from aiforstudy.blocks.content.content_contracts import parse_content
from aiforstudy.blocks.content.content_policy import load_content_policy, check_content, applicable_items
from aiforstudy.blocks.content.source_context import SourceContext, LearnerProfileV1
from aiforstudy.blocks.content.review_document import build_review_document
from aiforstudy.blocks.content.review import build_evidence_catalog, validate_verdicts
from aiforstudy.blocks.content.content_approval import approve_content
from aiforstudy.blocks.content.math_checks import check_content_math

def teaching_case(kind='convention'):
    text = '本节用 n 表示非负整数，包括零。这个约定只限定本节中 n 的取值；看到 n=0 时也符合约定。'
    claim = dict(id='K', kind=kind, source_refs=[{'kind':'textbook','obligation_id':'SO','quote':text}], conditions=['n 是非负整数'], conclusion='n 可以取零', symbols=['n'])
    content = dict(_meta={'schema_version':'4.0'}, unit_id='U', book_id='B', title='记号约定', reader_intro='判断 n 的允许取值。', prerequisite_recaps=[], body_markdown=text, summary='n 可以为零。', annotations=[], explainables=[], examples=[], concepts=[], teaching_plan={'goals':[{'id':'G','observable_task':'判断 n=0 是否符合约定','claim_refs':['K'],'level':'core'}], 'prerequisites':[], 'claims':[claim]}, teaching_evidence=[{'goal_id':'G','purpose':'definition','reader_pointer':'/body_markdown','claim_refs':['K']},{'goal_id':'G','purpose':'understanding_check','reader_pointer':'/body_markdown','claim_refs':['K']}], math_models=[], learning_checks=[])
    source = dict(book_id='B',unit_id='U',source_text=text,original_text='U.md',original_sha256='sha',source_inputs={'original':'sha'},unit={},chapters={},concept_index={},reference_catalog={'entries':[]},learner_profile=LearnerProfileV1().as_dict(),source_examples=[],source_obligations=[{'id':'SO','kind':kind,'level':'core','concept_id':'','conditions':claim['conditions'],'conclusion':claim['conclusion'],'source_excerpt':text,'source_refs':[{'kind':'textbook','quote':text}],'extraction_method':'semantic'}],obligation_extraction_status='semantic_verified')
    return content, source

def approved_inputs():
    c, raw = teaching_case()
    ctx = SourceContext.model_validate(raw)
    source = ctx.as_dict(); source['source_context_hash'] = ctx.digest
    c.update(source_context_hash=ctx.digest, original_text=ctx.original_text,
             original_sha256=ctx.original_sha256, revision=1)
    c = parse_content(c).as_dict()
    policy = load_content_policy(ctx.learner_profile)
    doc = build_review_document(c, ctx.reference_catalog)
    items = applicable_items(c, source, policy)
    evidence = build_evidence_catalog(c, doc, source)
    verdicts = []
    for item in items:
        refs = []
        for kind in item['required_evidence_kinds'] or ['reader']:
            eid, entry = next((eid, x) for eid,x in evidence.items()
                             if x['kind']==kind and item['subject_id'] in x['subject_ids'])
            refs.append({'evidence_id':eid,'quote':entry['text']})
        verdicts.append({'criterion_id':item['criterion_id'],'version':item['version'],
                         'status':'pass','reason':'非负整数的条件和零的边界例完整保留。','evidence_refs':refs})
    review = validate_verdicts(items, {'verdicts':verdicts}, evidence).as_dict()
    review.update(review_items=list(items), evidence_catalog=evidence)
    checks = check_content(c, source, policy)
    math = check_content_math(c).as_dict()
    return c, source, policy, doc, [checks, review], math

def issue(inputs):
    c, source, policy, doc, reports, math = inputs
    return approve_content(c, doc, reports, policy, source_context=source, math_report=math)

def approved_content():
    inputs = approved_inputs()
    return inputs[0], issue(inputs).as_dict()


def reference_catalog():
    return SourceContext.model_validate(teaching_case()[1]).reference_catalog


def none_decision():
    return {'target_id':'G','goal_id':'G','requirement':'none',
        'reason':'零是否属于约定集合可以直接代入文字定义判断。',
        'benefit':'读者可以直接检查 n=0 满足非负条件。',
        'cost':'图示会增加不必要的坐标约定。',
        'text_alternative':'正文已解释零满足非负条件，并给出边界例。',
        'request_ids':[],'handled_by':'media'}


class IntentReviewer:
    """Explicit offline semantic verdicts; production validation still runs."""
    def __init__(self):
        self.calls = 0

    def chat_json(self, messages, **kwargs):
        assert kwargs.get('tag', '').startswith('author-media-intent:')
        payload = json.loads(messages[-1]['content'])
        verdicts = []
        for row in payload['goals']:
            assert isinstance(row['reader_text'], str) and row['reader_text'].strip()
            assert row['decision']['reason'].strip()
            verdicts.append({'goal_id':row['goal_id'], 'status':'pass',
                'reason':'离线固定样本：已逐项核对文字任务或显式媒体请求，不代表真实教材判断。',
                'reader_quote':row['reader_text'], 'decision_quote':row['decision']['reason']})
        self.calls += 1
        return {'data':{'verdicts':verdicts}}


class Planner:
    def __init__(self, raw=None):
        self.raw = raw if raw is not None else {'decisions':[none_decision()],'requests':[]}
        self.calls = 0
        self.intent_reviewer = IntentReviewer()
    def chat_json(self, *args, **kwargs):
        if kwargs.get('tag', '').startswith('author-media-intent:'):
            return self.intent_reviewer.chat_json(*args, **kwargs)
        self.calls += 1
        return {'data':technical_plan(self.raw)}


def technical_plan(raw):
    """Only explicit backend choices belong to the post-author test transport."""
    return {'requests':[{k:deepcopy(v) for k,v in r.items()
        if k in ('request_key','kind','source_image_id','source_refs','spec','library_id')}
        for r in raw['requests']]}


def authoring(content,raw=None):
    """Explicit fixture migration, never a production inference or fallback."""
    raw=deepcopy(raw if raw is not None else {'decisions':[
        {**none_decision(),'goal_id':goal['id'],'target_id':goal['id']}
        for goal in content['teaching_plan']['goals']], 'requests':[]})
    rows=[]
    for r in raw['requests']:
        placement={k:v for k,v in r['placement'].items() if k not in ('span','field_hash')}
        if 'span' in r['placement']:
            from aiforstudy.blocks.audit.evidence import resolve_json_pointer
            _,text=resolve_json_pointer(content,placement['target_pointer'])
            placement['occurrence']=sum(1 for i in range(r['placement']['span'][0]+1) if text.startswith(placement['anchor_text'],i))
        rows.append({k:deepcopy(r[k]) for k in ('request_key','goal_refs','claim_ids','object_refs',
            'learning_goal','observe','misconception','acceptance_criteria','caption','alt_text')})
        rows[-1].update(kind='animation' if r['kind']=='animation' else 'image',placement=placement,
            math_model_ids=[m['model_id'] for m in r['math_model_refs']],
            mathematical_objects=r.get('mathematical_objects',r['observe']),
            conditions=r.get('conditions',r.get('constraints',{}).get('conditions',[])),
            animation=r.get('animation') or ({'initial_state':'显示批准对象','steps':['按请求展示关系'],
                'key_pauses':['在关键关系处停顿'],'final_state':'保留最终关系'} if r['kind']=='animation' else None))
    return {'schema_version':'ContentAuthoringV1','content':deepcopy(content),
        'media_requirements':{'schema_version':'AuthorMediaRequirementsV1','decisions':raw['decisions'],'requests':rows}}


def repair_answer(frozen, body_markdown):
    """D16-5 离线返修答复：与首次生成同一格式的**完整正文**，不是补丁。

    测试替身统一走这里，免得每个替身各写一份「底稿 + 改正文」的拼装逻辑，
    也就不会有人再退回已退役的 ContentPatchV1。
    """
    content = deepcopy(frozen['content'])
    content['body_markdown'] = body_markdown
    return authoring(content)


def author_media(content,raw=None):
    from aiforstudy.blocks.media.author_requirements import bind_author_requirements
    from aiforstudy.core.digest import digest_of
    envelope=authoring(content,raw)
    return bind_author_requirements(envelope['media_requirements'],content,author_response_hash=digest_of(envelope))


def no_media_requirements(content, approval, *, capabilities=None, decision=None):
    from aiforstudy.blocks.media.media_planning import plan_media_requirements
    plan = plan_media_requirements(content, approval, capabilities=capabilities or {'kinds':[]},
        author_requirements=author_media(content,{'decisions':[decision or none_decision()],'requests':[]}),
        client=Planner({'decisions':[decision or none_decision()],'requests':[]}))
    return reviewed_plan(content, plan)


def reviewed_plan(content, plan):
    """Attach an explicitly simulated review using the production verifier."""
    from aiforstudy.blocks.media.intent_review import review_author_intent
    plan['intent_review'] = review_author_intent(content, plan['author_media'],
        plan['capabilities'], IntentReviewer())
    assert plan['intent_review']['review_outcome']['status'] == 'verified'
    return plan
