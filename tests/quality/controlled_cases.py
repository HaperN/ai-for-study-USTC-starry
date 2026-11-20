"""Small, hand-authored behavioral controls, not model-quality evaluations.

Every result comes from a current production function. Fixed review responses
exercise receipt validation only; they are never evidence of independent review.
No real-unit or holdout text is read by this module.
"""
from copy import deepcopy
from support.content_case import author_media, technical_plan
from hashlib import sha256

from aiforstudy.errors import ContractError
from aiforstudy.core.digest import digest_of
from aiforstudy.blocks.content.content_contracts import parse_content, content_digest
from aiforstudy.blocks.content.content_policy import check_content, load_content_policy, applicable_items
from aiforstudy.blocks.content.source_context import SourceContext, LearnerProfileV1
from aiforstudy.blocks.content.reader_document import project_reader
from aiforstudy.blocks.content.review_document import build_review_document
from aiforstudy.blocks.content.review import build_evidence_catalog, validate_verdicts
from aiforstudy.blocks.content.content_approval import approve_content
from aiforstudy.blocks.content.math_checks import check_content_math, math_review_items
from aiforstudy.blocks.math import parse_math_model, verify_math


def convention():
    text = '本节用 n 表示非负整数，包括零。看到 n=0 时也符合约定。请判断 n=0 是否符合这个约定并说出理由。'
    claim = dict(id='K', kind='convention', source_refs=[{'kind':'textbook','obligation_id':'SO','quote':text}],
                 conditions=['n 是非负整数'], conclusion='n 可以取零', symbols=['n'])
    c = dict(_meta={'schema_version':'4.0'}, unit_id='U', book_id='B', title='记号约定',
        reader_intro='判断 n 的允许取值。', prerequisite_recaps=[], body_markdown=text,
        summary='n 可以为零。', annotations=[], explainables=[], examples=[], concepts=[],
        teaching_plan={'goals':[{'id':'G','observable_task':'判断 n=0 是否符合约定','claim_refs':['K'],'level':'core'}],
                      'prerequisites':[], 'claims':[claim]},
        teaching_evidence=[{'goal_id':'G','purpose':purpose,'reader_pointer':'/body_markdown','claim_refs':['K']}
                           for purpose in ('definition','understanding_check')], math_models=[], learning_checks=[])
    source_hash = sha256(text.encode()).hexdigest()
    s = dict(book_id='B', unit_id='U', source_text=text, original_text='controlled-U.md', original_sha256=source_hash,
        source_inputs={'original':source_hash}, unit={}, chapters={}, concept_index={}, reference_catalog={'entries':[]},
        learner_profile=LearnerProfileV1().as_dict(), source_examples=[], source_obligations=[{
            'id':'SO','kind':'convention','level':'core','concept_id':'','conditions':claim['conditions'],
            'conclusion':claim['conclusion'],'source_excerpt':text,
            'source_refs':[{'kind':'textbook','quote':text}],'extraction_method':'semantic'}],
        obligation_extraction_status='semantic_verified')
    return c, s


def bundle(c=None, s=None, *, omit_math_review=False):
    """Issue genuine host receipts from explicit fixed transport data.

    The semantic oracle is deliberately fixed for tiny controlled fixtures. It
    does not run an LLM or establish educational adequacy of arbitrary content.
    """
    if c is None:
        c, s = convention()
    ctx = SourceContext.model_validate(s)
    source = ctx.as_dict(); source['source_context_hash'] = ctx.digest
    c = deepcopy(c)
    c.update(source_context_hash=ctx.digest, original_text=ctx.original_text,
             original_sha256=ctx.original_sha256, revision=1)
    c = parse_content(c).as_dict()
    policy = load_content_policy(ctx.learner_profile)
    doc = build_review_document(c, ctx.reference_catalog)
    local = check_content(c, source, policy)
    math = check_content_math(c).as_dict()
    items = applicable_items(c, source, policy)
    if not omit_math_review:
        items += math_review_items(c, policy, math)
    evidence = build_evidence_catalog(c, doc, source, math)
    verdicts = []
    for item in items:
        refs = []
        for kind in item['required_evidence_kinds']:
            eid, entry = next((eid, e) for eid, e in evidence.items()
                             if e['kind'] == kind and item['subject_id'] in e['subject_ids'])
            refs.append({'evidence_id':eid, 'quote':entry['text']})
        verdicts.append({'criterion_id':item['criterion_id'], 'version':item['version'], 'status':'pass',
            'reason':'固定对照的来源与正文使用同一条件和结论；此数据只验证审核接口，不是模型或教师评阅。',
            'evidence_refs':refs})
    review = validate_verdicts(items, {'verdicts':verdicts}, evidence).as_dict()
    review.update(review_items=list(items), evidence_catalog=evidence)
    return c, source, policy, doc, [local, review], math


def issue(b):
    c, s, p, doc, reports, math = b
    return approve_content(c, doc, reports, p, source_context=s, math_report=math)


def result(observed, description, **evidence):
    return {'observed':observed, 'description':description, 'evidence':evidence}


def guarded(description, fn, *, inputs=None, rejected_types=(ContractError,)):
    try:
        value = fn()
    except rejected_types as exc:
        return result('reject', description, inputs=inputs,
                      exception={'type':type(exc).__name__, 'message':str(exc)})
    decision=value.get('decision') if isinstance(value,dict) else None
    observed='reject' if decision in {'needs_review','rejected','blocked'} else 'accept'
    return result(observed, description, inputs=inputs, output=value)


def local_result(description, c, s):
    report = check_content(c, s, load_content_policy(s['learner_profile'])).as_dict()
    return result('reject' if report['blocking_issues'] else 'accept', description,
                  content=c, source=s, report=report,
                  scope='local structural/policy gate; semantic reviewer remains not_run')


def concise_convention(reject):
    c, s = convention()
    if reject:
        c['teaching_plan']['claims'][0]['conditions'] = []
    return local_result('简短而完整的约定不受硬字数门槛阻拦；移除来源的非负条件必须阻断。', c, s)


def prerequisite_access(reject):
    c, s = convention()
    c['prerequisite_recaps'] = [{'id':'P-RECAP','title':'先修回顾','markdown':'非负整数指大于等于零的整数。'}]
    c['teaching_plan']['prerequisites'] = [{'id':'P','dependency_id':'nonnegative-integer',
        'treatment':'recap','recap_id':'absent' if reject else 'P-RECAP'}]
    return local_result('先修处理必须能解析到实际读者可见回顾；缺失回顾不可仅声称已解释。', c, s)


def const(x): return {'op':'const','value':x}
def var(): return {'op':'var','name':'x'}
def binary(op, a, b): return {'op':op,'left':a,'right':b}


def identity(lhs, rhs, *, conditions=(), assumptions=()):
    return {'schema_version':'1.0','model_id':'M',
        'symbols':[{'name':'x','domain':'real','assumptions':list(assumptions)}],
        'objects':[{'math_object_id':'f','kind':'scalar','expr':lhs},
                   {'math_object_id':'g','kind':'scalar','expr':rhs}],
        'conditions':list(conditions), 'source_refs':['SO'],
        'claims':[{'claim_id':'K','kind':'identity','math_object_id':'f',
                   'lhs_object_id':'f','rhs_object_id':'g'}]}


def math_result(description, model):
    report = verify_math(parse_math_model(model)).as_dict()
    # unknown/resource failure is not counted as successful rejection of a
    # known mathematical counterexample.
    observed = {'pass':'accept', 'fail':'reject'}.get(report['status'], 'unresolved')
    return result(observed, description, model=model, report=report,
                  scope='real isolated CAS worker; independent of media evaluator')


def division_conditions(reject):
    model = identity(binary('div',var(),var()), const(1),
                     conditions=[] if reject else [binary('ne',var(),const(0))])
    return math_result('x/x=1 必须保留 x≠0；缺少条件时检查器应给出 x=0 的定义域反例。', model)


def square_root_sign(reject):
    model = identity({'op':'sqrt','arg':{'op':'pow','base':var(),'exponent':2}}, var(),
                     assumptions=[] if reject else ['nonnegative'])
    return math_result('sqrt(x²)=x 仅在非负假设下成立；任意实数域应找到负数反例。', model)


def matrix_rank_degeneracy(reject):
    model = {'schema_version':'1.0','model_id':'M','symbols':[{'name':'x','domain':'real'}],
        'objects':[{'math_object_id':'A','kind':'matrix','rows':[[var(),const(0)],[const(0),const(1)]]}],
        'conditions':[] if reject else [binary('ne',var(),const(0))], 'source_refs':['SO'],
        'claims':[{'claim_id':'K','kind':'matrix_rank','math_object_id':'A','expected':2}]}
    return math_result('diag(x,1) 秩为 2 需要 x≠0；不得把一般位置秩当作所有参数值的秩。', model)


def note_reference(reject):
    c, _ = convention()
    c['annotations'] = [{'id':'N','title':'边界提示','markdown':'零不是正数，但属于非负整数。'}]
    c['body_markdown'] += '[[NOTE:missing]]' if reject else '[[NOTE:N]]'
    def run():
        parsed = parse_content(c)
        doc = project_reader(parsed, {'entries':[]})
        notes = [b for b in doc.blocks if b['json_pointer'].startswith('/annotations/')]
        if not notes:
            raise AssertionError('valid NOTE did not enter the shared reader document')
        return {'reader_blocks':list(doc.blocks), 'reader_hash':doc.digest}
    return guarded('批注引用需解析到实际共享读者文档；未知 NOTE 标记不得通过。', run, inputs=c)


def reader_field_fidelity(reject):
    b = list(bundle())
    doc = b[3].as_dict()
    original = deepcopy(doc)
    if reject:
        row = next(x for x in doc['blocks'] if x['json_pointer']=='/summary')
        row['raw_text'] = row['visible_text'] = '审核视图中伪造的小结，读者正文没有此句。'
    b[3] = doc
    def run():
        receipt = issue(b).as_dict()
        reader = project_reader(b[0], b[1]['reference_catalog'])
        if list(reader.blocks) != original['blocks']:
            raise AssertionError('reader/reviewer fields diverged before injected mutation')
        return {'receipt':receipt, 'reader_fields':[r['json_pointer'] for r in reader.blocks],
                'reader_blocks':list(reader.blocks)}
    return guarded('批准器重新构建共享 ReaderDocument；篡改仅审核视图的小结应被阻断。', run,
                   inputs={'content':b[0], 'submitted_review_document':doc})


def frozen_review_inputs():
    criterion = {'criterion_id':'CR-K','version':2,'rule_id':'SOURCE-FIDELITY','method':'reviewer',
        'required':True,'subject_id':'K','expected':'保留非零条件','repair_hint':'明确 x≠0',
        'required_evidence_kinds':['reader','source'], 'content_hash':'sha256:controlled-current',
        'policy_digest':'sha256:controlled-policy'}
    catalog = {
        'reader:step':{'kind':'reader','content_hash':criterion['content_hash'],
            'text':'对于 x≠0，x/x=1。','subject_ids':['K'],'json_pointer':'/body_markdown'},
        'source:K':{'kind':'source','content_hash':criterion['content_hash'],
            'text':'对非零实数 x，商 x/x 等于 1。','subject_ids':['K']}}
    verdict = {'criterion_id':'CR-K','version':2,'status':'pass','reason':'正文和来源均保留非零条件。',
        'evidence_refs':[{'evidence_id':'reader:step','quote':'对于 x≠0'},
                         {'evidence_id':'source:K','quote':'对非零实数 x'}]}
    return criterion, catalog, verdict


def review_evidence(quote_mismatch):
    item, catalog, verdict = frozen_review_inputs()
    if quote_mismatch: verdict['evidence_refs'][0]['quote'] = '对于包括零在内的任何实数'
    return guarded('quote 仅辅助定位；摘录与正文不一致不再由逐字匹配直接拒收。'
        '此控制只验证宿主保留判词，不证明摘录正确，也不替代模型对实际正文的实质审核。',
        lambda:validate_verdicts([item], {'verdicts':[verdict]}, catalog).as_dict(),
        inputs={'item':item,'catalog':catalog,'response':verdict})


class FixedPlanner:
    """Offline transport only; the production planner/validator runs normally."""
    def __init__(self, response): self.response=deepcopy(response); self.calls=0
    def chat_json(self, *args, **kwargs):
        self.calls += 1
        return {'data':deepcopy(self.response)}


def none_decision():
    return {'target_id':'G','goal_id':'G','requirement':'none',
        'reason':'不讨论三维曲面；此处只需凭非负整数定义判断零。',
        'benefit':'文字边界例已经足以完成判断。','cost':'新增图示会引入无关坐标。',
        'text_alternative':'直接将 n=0 代入正文定义并解释。','request_ids':[],'handled_by':'media'}


def negated_media_decision(reject):
    from aiforstudy.blocks.media.media_planning import plan_media_requirements
    from aiforstudy.blocks.media.media_requirements import candidates
    c, s = convention(); c['body_markdown'] += '不讨论三维曲面；此处只需判断整数约定。'
    if reject:
        c['teaching_plan']['goals'][0]['visual_requirement'] = {
            'required':True,'reason':'这是宿主冻结的受控必需图示条件。'}
    b = bundle(c,s); content=b[0]; approval=issue(b).as_dict()
    client = FixedPlanner({'decisions':[none_decision()],'requests':[]})
    return guarded('否定词中的三维术语不产生硬配图义务；已冻结 required=true 却返回 none 必须拒绝。',
        lambda:plan_media_requirements(content,approval,capabilities={'kinds':[]},client=client,author_requirements=author_media(content,client.response)),
        inputs={'content':content, 'candidates':candidates(content),'fixed_response':client.response})


def approved_plot_context():
    from aiforstudy.blocks.media.media_planning import plan_media_requirements
    from aiforstudy.blocks.media.math_context import ApprovedMathContext
    c, s = convention()
    text='对实数 x≠0，定义 f(x)=1/x。x=0 处没有定义。读图时零左右分开观察，不可跨零连接；请指出哪个 x 被排除并说明原因。'
    c.update(title='倒数函数的定义域', reader_intro='判断零为什么不在定义域中。',
             body_markdown=text, summary='x=0 被排除，曲线左右分开。')
    claim=c['teaching_plan']['claims'][0]
    claim.update(kind='definition', source_refs=[{'kind':'textbook','obligation_id':'SO','quote':text}],
                 conditions=['x 是非零实数'], conclusion='f(x)=1/x，零被排除', symbols=['x'])
    c['teaching_plan']['goals'][0]['observable_task']='从曲线和定义判断 x=0 为什么被排除'
    sh=sha256(text.encode()).hexdigest()
    s.update(source_text=text, original_sha256=sh, source_inputs={'original':sh})
    s['source_obligations'][0].update(kind='definition',conditions=claim['conditions'],
        conclusion=claim['conclusion'],source_excerpt=text,source_refs=deepcopy(claim['source_refs']))
    expr = binary('div',const(1),var())
    model = identity(expr,expr,conditions=[binary('ne',var(),const(0))])
    c['math_models'] = [model]; c['teaching_plan']['claims'][0]['math_model_refs'] = ['M']
    b = bundle(c,s); c=b[0]; a=issue(b).as_dict(); model=parse_math_model(c['math_models'][0])
    field_hash=digest_of(c['body_markdown'])
    request={'request_key':'reciprocal-domain','kind':'plot','serves':['G'],'goal_refs':['G'],'claim_ids':['K'],
        'math_model_refs':[{'model_id':'M','digest':model.digest}],
        'object_refs':[{'model_id':'M','object_id':'f'}],
        'learning_goal':'观察定义域断点','observe':'在零左右分别读取曲线','misconception':'误连断点两侧',
        'source_refs':[{'kind':'approved_content','pointer':'/body_markdown','hash':field_hash}],
        'constraints':{},'placement':{'target_pointer':'/body_markdown','owner_id':'U',
            'anchor_text':c['body_markdown'],'span':[0,len(c['body_markdown'])],
            'field_hash':field_hash,'position':'after'},
        'dependency_paths':[{'pointer':'/body_markdown','hash':field_hash}],
        'acceptance_criteria':[{'criterion_id':'no-zero-crossing','version':'1','kind':'mathematical',
            'statement':'不得跨越 x=0 连接曲线','evidence_required':['object_bindings','rendered_frame']}],
        'caption':'零处无定义','alt_text':'两侧分离的曲线'}
    decision=none_decision(); decision.update(requirement='recommended',request_ids=['reciprocal-domain'])
    plan=plan_media_requirements(c,a,capabilities={'kinds':['plot']},
                               client=FixedPlanner(technical_plan({'decisions':[decision],'requests':[request]})),
                               author_requirements=author_media(c,{'decisions':[decision],'requests':[request]}))
    return ApprovedMathContext.from_approved_content(c,a,plan['requests'][0])


def plot_discontinuity(reject):
    from aiforstudy.blocks.media.plot import compile_plot
    ctx = approved_plot_context()
    spec={'schema_version':'MediaPlotV2','series':[{'id':'curve',
        'object_ref':{'model_id':'M','object_id':'f'},'variable':'x','domain':[-2,2]}],'layout':{}}
    if reject:
        # The mathematical model is unchanged. A planner tries to inject a
        # hand-picked line crossing the singularity instead of using the AST.
        spec['series'][0]['points'] = [[-1,-1],[1,1]]
    def run():
        product=compile_plot(spec,ctx)
        data=product['data']['series'][0]
        crossings=[segment for segment in data['segments']
                   if min(p[0] for p in segment)<0<max(p[0] for p in segment)]
        if data['excluded_points'] != [0.0] or len(data['segments']) != 2 or crossings:
            raise AssertionError('production plot connected across the known singularity')
        return {'data':data,'relations':product['relations'],'object_bindings':product['object_bindings'],
                'svg_sha256':sha256(product['svg'].encode()).hexdigest(),
                'crossing_segments':len(crossings)}
    return guarded('真实 1/x 绘图按批准 AST 分成两段；规划器注入跨断点坐标线段会被严格契约拒绝。',
                   run, inputs={'spec':spec,'approved_model':ctx.model_by_id('M').as_dict()})


def visual_spec():
    return {'schema_version':'2.0','request_id':'A','unit_id':'U','goal_ids':['G'],'claim_ids':['K'],
        'math_model_refs':[{'model_id':'M','digest':'sha256:'+'a'*64}],
        'objects':[{'object_id':'formula','kind':'formula','model_id':'M','math_object_id':'f'}],
        'steps':[{'step_id':'S','kind':'change','goal_id':'G','claim_ids':['K'],
            'math_claim_refs':[{'model_id':'M','claim_id':'K'}],'object_ids':['formula'],
            'observation':'观察平方表达式的指数。','expected_change':'公式从隐藏变为可见。',
            'caption':'读出右上角的平方指数。','caption_claim_ids':['K'],'start':0,'end':8,
            'actions':[{'action':'reveal','object_id':'formula'}],'checkpoints':[{'at':4,'note':'读出指数'}]}]}


def animation_action(reject):
    from aiforstudy.blocks.visual.spec import parse_visual_spec
    spec=visual_spec()
    if reject: spec['steps'][0]['actions']=[]
    return guarded('change 步骤须包含真正改变可见状态的动作；只有字幕的空动画不可通过。',
                   lambda:parse_visual_spec(spec).as_dict(), inputs=spec)


def version_binding(reject):
    item,catalog,verdict=frozen_review_inputs()
    if reject: verdict['version']=1
    return guarded('相同 criterion_id 也必须绑定当前冻结 version；旧版本 pass 不可复用。',
        lambda:validate_verdicts([item],{'verdicts':[verdict]},catalog).as_dict(),
        inputs={'item':item,'catalog':catalog,'response':verdict})


def source_omission(reject):
    c,s=convention()
    if reject:
        c['teaching_plan']['claims']=[]; c['teaching_plan']['goals']=[]; c['teaching_evidence']=[]
    report=local_result('独立来源义务 SO 必须仍被覆盖；作者删除目标与主张不能消灭来源义务。',c,s)
    doc=project_reader(parse_content(c),s['reference_catalog'])
    catalog=build_evidence_catalog(c,doc,s)
    report['evidence']['catalog']=catalog
    return report


def unknown_resolution(reject):
    c,s=convention()
    c['math_models']=[{'schema_version':'1.0','model_id':'M','symbols':[],
        'objects':[{'math_object_id':'zero','kind':'scalar','expr':const(0)}],
        'conditions':[],'claims':[{'claim_id':'K','kind':'abstract','math_object_id':'zero',
            'reason':'符号约定由原文设定，CAS 无法替代来源对该约定的解释。'}], 'source_refs':['SO']}]
    c['teaching_plan']['claims'][0]['math_model_refs']=['M']
    b=bundle(c,s,omit_math_review=reject)
    if b[-1]['status']!='unknown':
        raise AssertionError('the actual abstract CAS result must remain unknown')
    def run():
        receipt=issue(b).as_dict()
        return {'receipt':receipt,'math_report_after_approval':b[-1],
                'review_criteria':[i['criterion_id'] for i in b[4][1]['review_items']]}
    return guarded('CAS 的 unknown 保持原值；只在逐主张独立来源复核凭据完整时允许批准，缺失则拒绝。',
        run, inputs={'content':b[0],'math_report':b[-1],
                     'review_report':b[4][1], 'oracle':'fixed controlled transport, not independent human/model review'})


CASES={fn.__name__:fn for fn in (
    concise_convention, prerequisite_access, division_conditions, square_root_sign,
    matrix_rank_degeneracy, note_reference, reader_field_fidelity, review_evidence,
    negated_media_decision, plot_discontinuity, animation_action, version_binding,
    source_omission, unknown_resolution)}
