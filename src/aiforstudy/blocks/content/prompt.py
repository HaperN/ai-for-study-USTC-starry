"""Role instructions around one frozen policy and the actual strict V4 schema."""
from __future__ import annotations
import json
from .content_contracts import LessonContentV4, ContentAuthoringV1, LearningCheckV1, as_dict, content_digest
from .content_policy import (PEDAGOGY, MAX_REQUEST_BYTES, READER_SUPPORT_RULES,
    THEOREM_INDEX_RULES, CONVENTION_RULES, SHARED_CONCEPT_RULES, REVIEW_STABILITY_RULES, prompt_rules,
    REPAIR_RESPONSE_RULES)
from ..math import describe_math_contract, math_schema_example
from ...errors import ContractError
from ...core.prompt_assets import load_prompt_asset


def content_schema_example():
    """A minimal complete convention, validated by the same model as production."""
    value={
        '_meta':{'schema_version':'4.0','file_type':'LessonContentV4'},
        'unit_id':'EXAMPLE-UNIT','title':'记号约定',
        'reader_intro':'读完后能判断零是否属于本节 n 的取值范围。',
        'prerequisite_recaps':[],
        'body_markdown':'本节用 n 表示非负整数，包括零。例如 n=0 和 n=2 都符合约定，n=-1 不符合。请判断 n=0 是否允许，并用本节约定说明理由。',
        'summary':'这里的 n 可以为零，但不能为负数。',
        'annotations':[], 'explainables':[], 'examples':[], 'concepts':[],
        'teaching_plan':{
            'goals':[{'id':'G-CONVENTION','observable_task':'判断给定整数是否可作为 n','claim_refs':['K-CONVENTION'],'level':'core'}],
            'prerequisites':[],
            'claims':[{'id':'K-CONVENTION','kind':'convention','source_refs':[{'kind':'textbook','obligation_id':'SO-CONVENTION','quote':'本节用 n 表示非负整数，包括零。'}], 'conditions':['n 是非负整数'],'conclusion':'n 可以取零','symbols':['n']}]},
        'teaching_evidence':[{'goal_id':'G-CONVENTION','purpose':'definition','reader_pointer':'/body_markdown','claim_refs':['K-CONVENTION']},{'goal_id':'G-CONVENTION','purpose':'understanding_check','reader_pointer':'/body_markdown','claim_refs':['K-CONVENTION']}],
        'math_models':[], 'learning_checks':[],
    }
    return LessonContentV4.model_validate(value).model_dump(mode='json',by_alias=True,exclude_defaults=True)


def reader_presentation_example():
    """Contract-valid, self-contained convention; no invented frozen source IDs."""
    content = content_schema_example()
    main = '本节用 n 表示非负整数，即大于或等于零的整数。判断时先与零比较；零符合约定，负整数不符合。'
    proof = '### 证明：零符合约定\n\n零是整数，且 0≥0，因此零满足非负整数的定义。证毕。'
    worked = '### 例题：判断取值\n\n判断 n=2 和 n=-1 是否符合约定。先检查整数条件，再与零比较：2 和 -1 都是整数；2>0，所以 2 符合；-1<0，所以 -1 不符合。'
    takeaway = '### 例题的方法回顾\n\n可迁移的方法是先检查对象是否为整数，再检查它是否大于或等于零；不要把非负误解为严格为正。'
    content['body_markdown'] = '\n\n'.join((main, proof, worked, takeaway))
    content['teaching_plan']['claims'][0]['source_refs'] = []
    content['teaching_evidence'] = [
        {'goal_id': 'G-CONVENTION', 'purpose': purpose,
         'reader_pointer': '/body_markdown', 'claim_refs': ['K-CONVENTION'],
         'quote': quote, 'reader_role': role}
        for purpose, quote, role in [('definition', main, 'main'),
            ('derivation', proof, 'proof'), ('example', worked, 'worked_example'),
            ('contrast', takeaway, 'main')]]
    return LessonContentV4.model_validate(content).model_dump(mode='json', by_alias=True, exclude_defaults=True)


REVIEW_TASK = '你是内容审核者。仅对宿主给出的适用 criteria 各返回一次 verdict；不修改内容，不自行撤销适用项。candidate_content 是本轮完整正文；repair_context.machine_findings 与 math_findings 是宿主检查证据。逐项独立审核所有必需标准，即使机器发现问题也要审核；意见冲突时具体解释依据，宿主会合并保留实质性结果。JSON 类型、容量、评分执行和当前版本等机器事实可结合完整候选与机器记录核实，不要因读者文字未显示内部字段就判 unknown。\n只输出 JSON 对象，唯一根字段 "verdicts" 为数组。每行包含 criterion_id（宿主原 ID）、version（对应 criteria 项的原整数版本，逐项复制）、status（pass|fail|unknown|not_applicable|not_run）、reason（具体理由）及 evidence_refs（对象数组，允许为空）。判 fail 时，在现有 reason 内写清具体位置、错误原句或公式、错误原因和修改要求；不增加字段。实际缺少内容时指出应出现的位置及前后原句或公式，说明缺哪一步和应补什么，不编造不存在的引文。\n必须检查完整 candidate_content 的实际正文、例题步骤与自测。claim、goal、teaching_evidence 缺少绑定只是定位提示，不能据此判正文缺失、fail 或 unknown。absence 只表示缺少绑定，不证明正文没讲；即使有 absence，若实际正文满足标准仍可 pass。绑定或 ID 齐全也不能代替内容审核。\n引用信息仅辅助定位，不强制引用 reader/source/absence 类型 ID；没有合适 ID 时用 evidence_refs=[]，在 reason 中直接说明实际位置及原文依据。若提供引用，每项给当前 evidence_id；quote 仅辅助定位，可省略或为空，提供时应忠实摘录或概述并保留条件与结论。reason 已写清依据时无须重复摘录。criterion_evidence_ids 是可选定位列表。evidence_id 复制当前 evidence_catalog 的键；各类型都可引用当前目录中与判词实际相关的证据，不要求 subject_ids 与当前审核对象相同；例如证明使用逆元时可引用逆元的当前数学检查。相关性须在 reason 中解释，不能用无关检查替代当前主张验证。不要编造 ID 或引用旧版本；重复引用只属冗余，不改变判词，也不因重复要求重交。\n定义和概念不要求额外证明；正文概念核心、独立补充及就地短链接按统一阅读组织规则核对；所提供的证明和完整例题每一步都要完整推导并有依据。允许作者依据数学知识修正原文或 formal 的 LaTeX、格式、错别字及明确识别错误，不能仅因改正后与原文不同而拒收。知识错误、必要条件遗漏、定理例题跳步、自测评分错误仍判 fail。\npass 须依据当前完整候选说明理由，不能只凭内部计划、ID 或 CAS 状态宣布正文正确。CAS fail 不得覆盖；CAS unknown 可依据来源、数学知识和明确推理独立论证 pass，但不得宣称已计算验证。仍无法判断或存在来源争议时用 unknown，并写明 reason_code=unsupported/source_dispute/timeout。\n若 repair_context 含 ContentReviewProtocolErrorsV1，按每项 path、criterion_id、actual 和 candidates 纠正当前审核响应；旧记录中仅缺 reader/source/absence 类型、证据未绑定同一 subject 或重复引用不再是错误。candidates 仅为定位线索，不代表语义通过。不得修改正文、冻结标准、适用性或为消除格式错把实质 unknown/fail 升级为 pass。'

def authoring_schema_example():
    content=content_schema_example()
    return ContentAuthoringV1.model_validate({'schema_version':'ContentAuthoringV1','content':content,
        'media_requirements':{'schema_version':'AuthorMediaRequirementsV1','requests':[],
        'decisions':[{'goal_id':'G-CONVENTION','target_id':'G-CONVENTION','requirement':'none',
        'reason':'直接对照非负定义即可判断零。','benefit':'明确零是允许的边界。','cost':'图示会增加无须使用的坐标约定。',
        'text_alternative':'正文已有零与负数的正反例和可见判断任务。','request_ids':[],'handled_by':'media'}]}}).model_dump(mode='json',by_alias=True,exclude_defaults=True)


def learning_check_examples():
    base={'id':'CHECK-CONVENTION','kind':'boolean','source_kind':'derived','goal_refs':['G-CONVENTION'],
        'source_refs':[],'statement':'本节允许 n=0。','input_spec':{'options':[]},
        'scoring':{'accepted_inputs':[True]},'hints':['检查非负是否包含零。'],'answer':'允许，零属于非负整数。',
        'misconception_feedback':[{'id':'M-ZERO','inputs':[False],'feedback':'非负包含零，正数才排除零。'}],
        'test_cases':[{'id':'T-YES','input':True,'expected':'correct','coverage':'correct'},
            {'id':'T-NO','input':False,'expected':'incorrect','coverage':'wrong'},
            {'id':'T-BOUND','input':'true','expected':'invalid','coverage':'boundary'}]}
    return [LearningCheckV1.model_validate(base).as_dict()]


def balanced_authoring_example():
    """Small, executable writing example; not an admission schema or quota."""
    return {'content': {
        'title':'非负整数约定（仅演示输出组织，勿照抄到教材）',
        'body_markdown':('> **概念·非负整数**\n> 非负整数是大于或等于零的整数，包括零和正整数。'
            '\n\n本节用 n 表示非负整数。区分非负与正，可看[[EXPLAIN:DEMO-ZERO|零这一边界]]；'
            '判断 n=0 时注意[[NOTE:DEMO-ZERO-PITFALL|非负不是正数]]。'
            '\n\n数轴上，零和右侧的整数点符合约定，负整数不符合。'),
        'explainables':[{'id':'DEMO-ZERO', 'name':'非负与正的区别',
            'what':'非负指大于或等于零；正指严格大于零。',
            'why':'检查边界零，可避免把这两个条件混淆。'}],
        'annotations':[{'id':'DEMO-ZERO-PITFALL', 'kind':'pitfall', 'title':'非负不是正数',
            'markdown':'把非负误读成正数会排除 n=0。代入边界可区分：0≥0 成立，但 0>0 不成立。'}],
        'examples':[{'id':'DEMO-EXAMPLE', 'label':'判断约定的使用范围',
            'statement':'n=0 与 n=-1 哪个符合非负整数约定？',
            'approach':'分别检查它们与零的大小关系。',
            'steps':[{'id':'DEMO-STEP', 'markdown':'0等于零，满足非负；-1小于零，不满足非负。',
                'basis':'非负指大于或等于零。'}],
            'conclusion':'n=0符合，n=-1不符合。'}],
        'learning_checks':learning_check_examples(),
    }, 'media_requirements':{'requests':[{
        'request_key':'DEMO-NUMBER-LINE', 'kind':'image',
        'learning_goal':'区分包含零与排除零的边界条件。',
        'mathematical_objects':'数轴上的整数点 -2,-1,0,1,2。',
        'observe':'把零和右侧整数点同色标出，负整数用另一颜色；突出零归入非负一侧。',
        'caption':'非负整数包括零。', 'alt_text':'数轴上零和正整数被标为非负，负整数在另一侧。',
        'placement':{'target_pointer':'/body_markdown',
            'anchor_text':'数轴上，零和右侧的整数点符合约定，负整数不符合。', 'position':'after'},
    }], 'decisions':[{'target_id':'DEMO-ZERO', 'requirement':'recommended',
        'reason':'用边界点和两侧对比帮助区分非负与正，无须连续变化。',
        'request_ids':['DEMO-NUMBER-LINE']}]}}


def balanced_animation_example():
    """A teaching example, not an animation quota or a new output contract."""
    return {
        'purpose':'演示完整教学讲解如何与动画对应；只参考写法，不是新工单或媒体配额，不照搬示例ID、题目或无关媒体。',
        'content':{'title':'复数和的模平方（教学写法示例）',
            'reader_intro':'理解交叉项为何相加为两倍实部，并能避免把共轭项当成相同项。',
            'body_markdown':('> **概念·共轭复数**\n'
                r'> 若 $z=x+iy$，其中 $x,y$ 为实数，则 $\bar z=x-iy$ 称为 $z$ 的共轭复数。'
                '\n\n计算复数和的模平方时，直接套实数平方公式容易混淆交叉项。'
                '先把模平方转成与共轭相乘，才能看清每一项的来源。'
                r'对任意 $v=a+ib$（$a,b$ 为实数），$v\bar v=(a+ib)(a-ib)=a^2-(ib)^2=a^2+b^2=|v|^2$，其中用到 $i^2=-1$。'
                '可对照[[EXPLAIN:DEMO-MOD-SQUARE|乘积形式的用途]]，再看[[EXAMPLE:DEMO-CROSS-TERMS|一般推导]]。'
                '\n\n两个交叉项为什么相加为两倍实部？可看[[NOTE:DEMO-CONJ-INTUITION|平面上的共轭对称]]。'
                '推导仍在例题中完整展开，示意帮助理解对应关系。'),
            'explainables':[{'id':'DEMO-MOD-SQUARE', 'name':'模平方与共轭',
                'what':'这是表达方式的转换；对象仍是同一个复数的模平方。',
                'why':'把模平方化为乘积以后，可以用分配律展开，再辨认每个平方项与交叉项。',
                'markdown':'先写两个因子，再跟踪每个乘积项来自哪一对因子，能避免把两个不同来源的交叉项误当成相同项。正文和例题保留完整等式链，这里只补充选法的观察角度。'}],
            'annotations':[{'id':'DEMO-CONJ-INTUITION', 'kind':'intuition', 'title':'共轭对称与虚部抵消',
                'markdown':r'把 $w=a+ib$ 与 $\bar w=a-ib$ 看成平面中的点 $(a,b)$ 与 $(a,-b)$：实部相同，虚部相反。相加时虚部抵消，留下 $2a=2\operatorname{Re}w$。这是一般共轭关系的直观解释；动画中的数值示意不能替代例题的一般证明。'},
                {'id':'DEMO-CONJ-PITFALL', 'kind':'pitfall', 'title':'共轭项通常不是相同项',
                 'markdown':r'不能把两交叉项直接写成 $2w$。例如 $w=1+i$ 时，$w+\bar w=(1+i)+(1-i)=2$，而 $2w=2+2i$。只有 $w$ 为实数时，$w+\bar w=2w$ 才成立。'}],
            'examples':[{'id':'DEMO-CROSS-TERMS', 'label':'模平方和的交叉项',
            'statement':r'设 $z_1,z_2$ 为任意复数，说明 $|z_1+z_2|^2=|z_1|^2+|z_2|^2+2\operatorname{Re}(z_1\bar z_2)$。',
            'approach':'题目含和的模平方，先改写为和与其共轭相乘；展开后识别交叉项的关系，再决定怎样合并。此方法不要求两复数是实数。',
            'steps':[{'id':'EXPAND', 'markdown':r'$|z_1+z_2|^2=(z_1+z_2)(\bar z_1+\bar z_2)=z_1\bar z_1+z_1\bar z_2+z_2\bar z_1+z_2\bar z_2$。',
                       'basis':'模平方等于自身与共轭的乘积，共轭保持加法，再用分配律。'},
                     {'id':'PAIR', 'markdown':r'令 $w=z_1\bar z_2$，则 $\bar w=\bar z_1z_2=z_2\bar z_1$，所以两个交叉项互为共轭。',
                       'basis':'共轭保持乘法、双重共轭还原及乘法交换律。'},
                     {'id':'COMBINE', 'markdown':r'由 $w+\bar w=2\operatorname{Re}w$，两交叉项合并为 $2\operatorname{Re}(z_1\bar z_2)$，两平方项分别为 $|z_1|^2$ 与 $|z_2|^2$。这里取的是实部；[[NOTE:DEMO-CONJ-PITFALL|为何不是两倍原项]]。',
                       'basis':r'写 $w=a+ib$（$a,b$ 为实数），则 $w+\bar w=(a+ib)+(a-ib)=2a=2\operatorname{Re}w$。'}],
            'conclusion':r'因此对任意复数 $z_1,z_2$，$|z_1+z_2|^2=|z_1|^2+|z_2|^2+2\operatorname{Re}(z_1\bar z_2)$。',
            'key_points':['遇到复数和的模平方，先转成与共轭相乘，再用分配律追踪项的来源。',
                '合并交叉项前先确认它们互为共轭；共轭对相加得到两倍实部，而不是默认两项相同。'],
            'pitfalls':['不要直接套实数的交叉项写法；具体错因和反例见[[NOTE:DEMO-CONJ-PITFALL|为什么不能合成2w]]。']}],
            'learning_checks':[{'id':'DEMO-CONJ-CHECK', 'kind':'boolean', 'source_kind':'derived',
                'goal_refs':[], 'source_refs':[], 'statement':r'当 $w=1+i$ 时，$w+\bar w=2$。',
                'input_spec':{'options':[]}, 'scoring':{'accepted_inputs':[True]},
                'hints':['先写出共轭，再相加。'], 'answer':r'正确，$(1+i)+(1-i)=2$，虚部抵消。',
                'misconception_feedback':[{'id':'DEMO-CONJ-WRONG', 'inputs':[False],
                    'feedback':'共轭会改变虚部符号，两项相加为两倍实部；不要把它们当成两个1+i。'}],
                'test_cases':[{'id':'DEMO-CONJ-YES', 'input':True, 'expected':'correct', 'coverage':'correct'},
                    {'id':'DEMO-CONJ-NO', 'input':False, 'expected':'incorrect', 'coverage':'wrong'},
                    {'id':'DEMO-CONJ-BOUND', 'input':'true', 'expected':'invalid', 'coverage':'boundary'}]}]},
        'media_requirements':{'requests':[{
            'request_key':'DEMO-CROSS-TERMS-MOTION', 'kind':'animation',
            'learning_goal':'跟随交叉项从哪里来、为什么互为共轭以及怎样合并成两倍实部。',
            'mathematical_objects':r'$z_1,z_2,z_1\bar z_2,z_2\bar z_1$ 与完整模平方恒等式。',
            'observe':'同色项从两个因子对应到四项；交叉项配成共轭；虚部抵消后回到一般恒等式。',
            'animation':{
                'initial_state':'显示待说明的模平方式与两个因子，固定公式区和观察区。',
                'steps':['依次对应分配律得到的四项，保留来源，突出两个交叉项。',
                         '令 w 为第一交叉项，把另一项对应到共轭 w；用标为示意的 a+bi 与 a-bi 显示虚部抵消，参照轴保持固定。',
                         '回到一般公式，交叉项合并为两倍实部，保留两模平方项与完整结论。'],
                'key_pauses':['停在四项与来源对应处。','停在共轭配对和虚部抵消处。','停留最终一般恒等式。'],
                'final_state':'完整恒等式保持可见，读者能对应每一项的来源；数值示意不冒充一般证明。'},
            'placement':{'target_pointer':'/examples/0/steps/1/markdown',
                'anchor_text':r'令 $w=z_1\bar z_2$，则 $\bar w=\bar z_1z_2=z_2\bar z_1$，所以两个交叉项互为共轭。', 'position':'after'},
            'caption':'从交叉项的来源到共轭配对，再到两倍实部。',
            'alt_text':'逐步对应两个交叉项，展示它们互为共轭，虚部抵消后相加为两倍实部。'}],
            'decisions':[{'target_id':'DEMO-CROSS-TERMS', 'requirement':'recommended',
                'reason':'读者需连续追踪项的来源、共轭对应与抵消；依次变换比把全部公式并排放在一张图里更容易跟随。',
                'request_ids':['DEMO-CROSS-TERMS-MOTION']}]}}


def theorem_index_example():
    """One existing explainable supplies the first proof and later references."""
    return {
        'purpose': '定理组织示例，不是本节来源或新输出契约；示例 C-ID、解释 ID 与题目须替换为冻结目录和当前真实内容。描述性名称不冒充公认名称。',
        'reference_catalog': {'entries': [{'id': 'C-DEMO-COMPLEX-INVERSE',
            'label': '性质 1（示例原编号）', 'kind': 'CONCEPT', 'version': 'example',
            'markdown': '每个非零复数存在唯一乘法逆元。'}]},
        'content': {
            'body_markdown': '除以一个复数以前，先确认它非零，并说明为什么存在且只有一个倒数。'
                '\n\n[[EXPLAIN:DEMO-INVERSE-THEOREM|非零复数的倒数]]\n\n'
                '以后遇到除法，可由[[CONCEPT:C-DEMO-COMPLEX-INVERSE|非零复数的倒数]]回看完整结论和证明；每次仍须检查除数非零。',
            'explainables': [{'id': 'DEMO-INVERSE-THEOREM', 'kind': 'theorem',
                'concept_id': 'C-DEMO-COMPLEX-INVERSE', 'name': '非零复数的倒数',
                'what': r'若 $z=x+iy$ 是非零复数（$x,y$ 为实数），则它有唯一乘法逆元 $z^{-1}=\frac{x-iy}{x^2+y^2}$。',
                'why': '非零复数可以作除数；这个结论给出倒数的具体表达式，并保证结果唯一。',
                'markdown': '',
                'steps': [
                    {'id': 'DEMO-INVERSE-POSITIVE',
                     'markdown': r'由 $z\ne0$，$x,y$ 不同时为零。实数平方非负，至少一个严格为正，因此 $x^2+y^2>0$。令 $w=\frac{x-iy}{x^2+y^2}$，分母非零，所以 $w$ 有定义。',
                     'basis': '复数为零当且仅当实部、虚部都为零；非零实数的平方为正。'},
                    {'id': 'DEMO-INVERSE-PRODUCT',
                     'markdown': r'展开得 $(x+iy)(x-iy)=x^2-ixy+ixy-i^2y^2=x^2+y^2$。于是 $zw=\frac{(x+iy)(x-iy)}{x^2+y^2}=1$；交换因子也有 $wz=1$，故 $w$ 是逆元。',
                     'basis': r'使用复数乘法分配律、交换律及 $i^2=-1$；分母非零，才能约去 $x^2+y^2$。'},
                    {'id': 'DEMO-INVERSE-UNIQUE',
                     'markdown': r'若 $u,v$ 都是 $z$ 的逆元，则 $uz=zv=1$。由结合律，$u=u(zv)=(uz)v=v$，所以逆元唯一。结合上面的构造，$z^{-1}=w=\frac{x-iy}{x^2+y^2}$。',
                     'basis': '单位元与已知的乘法结合律；唯一性由任意两个逆元相等得到，不预先假设唯一。'}]}],
            'examples': [{'id': 'DEMO-INVERSE-USE', 'kind': 'derived_example',
                'label': '计算一个倒数', 'statement': r'求 $1+i$ 的倒数。',
                'approach': '先确认非零，再把实部、虚部代入已证明的倒数公式。',
                'steps': [{'id': 'DEMO-INVERSE-SUBSTITUTE',
                    'markdown': r'$1+i\ne0$，代入 $x=y=1$，得 $(1+i)^{-1}=\frac{1-i}{1^2+1^2}=\frac{1-i}{2}$。',
                    'basis': '[[CONCEPT:C-DEMO-COMPLEX-INVERSE|非零复数的倒数]]适用于已确认非零的复数。'}],
                'conclusion': r'倒数是 $\frac{1-i}{2}$。'}]}}


def math_representation_examples():
    """Executable contract examples, without adding operators or fake objects."""
    from ..math.contracts import parse_math_model
    var = lambda name: {'op':'var', 'name':name}
    mul = lambda a,b: {'op':'mul', 'left':var(a), 'right':var(b)}
    def product(x, y, a, b):
        return [{'op':'sub', 'left':mul(x,a), 'right':mul(y,b)},
                {'op':'add', 'left':mul(x,b), 'right':mul(y,a)}]
    components = dict(schema_version='1.0', model_id='complex-components',
        symbols=[dict(name=n,domain='real',assumptions=[]) for n in ('x','y','a','b')],
        objects=[dict(math_object_id='zw',kind='vector',entries=product('x','y','a','b')),
                 dict(math_object_id='wz',kind='vector',entries=product('a','b','x','y'))],
        conditions=[], claims=[dict(claim_id='K-PRODUCT',kind='identity',math_object_id='zw',
            lhs_object_id='zw',rhs_object_id='wz')],source_refs=['source-obligation-product'])
    abstract = dict(schema_version='1.0',model_id='complex-order-abstract',
        symbols=[dict(name=n,domain='real',assumptions=[]) for n in ('x','y')],
        objects=[dict(math_object_id='complex_coordinates',kind='vector',entries=[var('x'),var('y')])],
        conditions=[],claims=[dict(claim_id='K-NO-COMPLEX-ORDER',kind='abstract',
            math_object_id='complex_coordinates',reason='复数的真实对象以实虚部坐标表示；不存在与域运算相容的全序涉及序公理与全称命题，作为概念说明只核知识正确性，不强制证明，不能由坐标代入宣称计算证明。')],
        source_refs=['source-obligation-order'])
    # Check the proposed quotient by multiplying back, using real components.
    # N and D below are Python aliases only; the JSON AST inlines their values.
    const = lambda value: {'op':'const', 'value':value}
    binary = lambda op,left,right: {'op':op, 'left':left, 'right':right}
    square = lambda base: {'op':'pow', 'base':base, 'exponent':2}
    x, y = var('x'), var('y')
    one_minus_x = binary('sub', const(1), x)
    one_plus_x = binary('add', const(1), x)
    denominator = binary('add', square(one_minus_x), square(y))
    numerator = binary('sub', binary('sub', const(1), square(x)), square(y))
    modulus_numerator = binary('add', square(one_plus_x), square(y))
    pairs = [
        ('REAL', binary('add', binary('mul', numerator, one_minus_x),
                       binary('mul', const(2), square(y))),
         binary('mul', one_plus_x, denominator)),
        ('IMAG', binary('sub', binary('mul', binary('mul', const(2), y), one_minus_x),
                       binary('mul', numerator, y)),
         binary('mul', y, denominator)),
        ('MODULUS', binary('add', square(numerator), binary('mul', const(4), square(y))),
         binary('mul', modulus_numerator, denominator)),
    ]
    quotient = dict(schema_version='1.0', model_id='complex-quotient',
        symbols=[dict(name=n, domain='real', assumptions=[]) for n in ('x','y')],
        objects=[dict(math_object_id=ident, kind='scalar', expr=expr) for ident,expr in (
            ('f_real', binary('div', numerator, denominator)),
            ('f_imag', binary('div', binary('mul', const(2), y), denominator)),
            ('f_mod_sq', binary('div', modulus_numerator, denominator)))],
        conditions=[], claims=[], source_refs=['source-obligation-quotient'])
    for component,left,right in pairs:
        name = component.lower()
        quotient['objects'].extend([
            dict(math_object_id=name+'_lhs', kind='scalar', expr=left),
            dict(math_object_id=name+'_rhs', kind='scalar', expr=right)])
        quotient['claims'].append(dict(claim_id='K-QUOTIENT', relation_id='R-QUOT-'+component,
            kind='identity', math_object_id='f_mod_sq' if component == 'MODULUS' else 'f_'+name,
            lhs_object_id=name+'_lhs', rhs_object_id=name+'_rhs',
            conditions=[binary('ne', denominator, const(0))]))
    return {
        'complex_components': {'instruction':'z=x+iy、w=a+ib 的运算可用真实实虚部分量的 scalar/vector AST 表示；本例核对乘法交换律。替换成当前主张与来源ID，不因缺少 Re/Im/conjugate 算子就把可计算分量关系一律改成 abstract。',
                               'model':parse_math_model(components).as_dict()},
        'abstract_source_review': {'instruction':'abstract 仍须引用实际存在、忠实表示论证对象的 math_object_id；未知是需要来源语义审核，不是通过。不得用常量零或无关对象补缺，也不能借 abstract 绕开已有可计算关系。',
                                  'model':parse_math_model(abstract).as_dict()},
        'complex_quotient': {
            'instruction': (
                '生成示例，不是新增验收格式；按当前正文选用，替换主张与来源 ID，不要求其他题照搬本题模型。'
                '正文要表达：z=x+iy、z≠1 时，w=(1+z)/(1-z) 的实部、虚部和模平方。'
                '记 D=(1-x)²+y²、N=1-x²-y²、Q=(1+x)²+y²，候选结果为 u=N/D、v=2y/D、|w|²=Q/D；'
                'D、N、Q 仅是这里的说明简写，实际 AST 全部内联，symbols 仅声明 x,y。'
                '正确核对：从原式得到 (u+iv)(1-x-iy)=1+x+iy，代入候选实虚部，再保留 D≠0 清分母，'
                '分别核对 N(1-x)+2y²=(1+x)D、2y(1-x)-Ny=yD；'
                '结合 u²+v² 核对 N²+4y²=QD，得到模平方。这样把原运算与候选结果联系起来。'
                '三条关系分别使用局部 D≠0，忠实保留 z≠1；不能改成 x≠1 或 y≠0，也不约束无关性质。'
                '反例（不要照抄）：lhs_object_id=f_real、rhs_object_id=f_real，只验证了自己等于自己；'
                '复制同一候选表达式再改名 f_real_copy 也没有核对原式。CAS 即使 pass，也不能据此宣称推导正确。'
                '返修时正文若已正确，只修有问题的对象、关系或条件及必要关联，保留其余内容。'),
            'model':parse_math_model(quotient).as_dict()},
    }


# Provider prompt caching only reuses an identical prefix. Same-for-every-unit
# material goes first, then book-wide context, then this unit (rule 2026-10-16:
# reorder only, content unchanged).
_FIXED_FIRST = ('role_task', 'learner_profile', 'reader_reference_contract', 'input_context_notes',
                'output_schema_example', 'animation_requirement_example', 'theorem_index_example')
_BOOK_WIDE_FIRST = ('chapters', 'concept_index', 'reference_catalog')


def _book_wide_first(context):
    if not isinstance(context, dict):
        return context
    if isinstance(context.get('source_context'), dict):
        context = {**context, 'source_context': _book_wide_first(context['source_context'])}
        return {'source_context': context['source_context'],
                **{k: v for k, v in context.items() if k != 'source_context'}}
    return {**{k: context[k] for k in _BOOK_WIDE_FIRST if k in context},
            **{k: v for k, v in context.items() if k not in _BOOK_WIDE_FIRST}}


def _cache_friendly_order(payload):
    ordered = {k: payload[k] for k in _FIXED_FIRST if k in payload}
    ordered.update((k, v) for k, v in payload.items() if k not in ordered and k != 'frozen_context')
    if 'frozen_context' in payload:
        ordered['frozen_context'] = _book_wide_first(payload['frozen_context'])
    return ordered


def build_content_messages(role, content_context, policy, *, tickets=()):
    from ...core.review_mode import reviews_frozen, substantive_reviews_enabled
    from .prompt_context import CONTEXT_NOTES, compact_content_context
    if role not in ('author','reviewer','repair'): raise ContractError('unknown content role '+str(role))
    context, compacted = compact_content_context(as_dict(content_context))
    presentation_rules, separator, full_rules = load_prompt_asset('content/common.md').text.partition('\n<!-- mode-specific-content -->\n')
    if not separator:
        raise ContractError('content common prompt missing mode-specific boundary')
    shared_rules = '\n'.join((presentation_rules, READER_SUPPORT_RULES, THEOREM_INDEX_RULES,
                              CONVENTION_RULES, SHARED_CONCEPT_RULES))
    if role == 'reviewer':
        shared_rules += '\n' + REVIEW_STABILITY_RULES
    if substantive_reviews_enabled():
        common = ('面向学习者保留完整正文。知识、公式和条件必须正确；定义和概念不要求推导；'
            '但应把含义、符号和适用条件讲清楚；逐个主要概念、性质、定理和例题考虑读者会在哪里难理解，'
            '主动补准确直觉、边界对比、具体例子和易混点，“按需”不能理解为默认省略，'
            '不能把简化审核理解成只列公式、删掉必要讲解。不为定义强加证明或编造无用补充，实际补充按统一阅读组织规则呈现。'
            '定理以及需要推导的性质、推论应讲清结论含义、适用条件和作用，主线必要的方法及依据讲清，正式证明可选，提供时完整展开每一步；'
            '正式证明未提供不构成缺步；不能用“不难证明，留给读者”替代主线必要的方法、条件与依据说明。'
            '按内容的教学作用区分：背景概念说明不额外变成证明任务，例如“复数不能像实数比较大小”作为概念说明不强制证明；'
            '本节用于计算或证明的运算性质须讲清含义、条件、用法及所用依据；不强制逐条另附正式证明。'
            '对于实际提供的证明或演算，引用已知法则可以说明某一步为什么成立，但仅报法则名称、说“可验证”不能代替缺失的关键步骤；'
            '例如若选择提供复数乘法结合律的证明，应分别展开两边、整理并比较实部和虚部，或给出同样完整的其他证明。'
            '可以合并已说明依据的重复计算，但不能把待证性质自身当作依据。若引用前文完整证明，应明确对应的结论与条件。'
            '实质性例题按读题与条件、选方法及理由、逐步计算或证明与依据、结论、方法回顾和具体易错提醒来讲。'
            '回顾提炼可迁移的方法、选择条件与关键转折，不只重复答案；提醒指出具体错误想法或写法、错因、正确区别及条件，必要时给简短反例。'
            '按实际难点安排这些支持，核心讲解留在主线，补充迁入独立对象并在原位置短链接复用；无真实误区时允许省略，不编造错法凑数。'
            '正文、批注、解释、例题、自测和图注使用相同数学与条件标准；类比须说明对象对应关系和适用范围，'
            '公式正确不能抵消文字把运算对象、步骤或比较关系说错，也不能以直觉或动画替代关键证明。'
            '已在前文解释清楚的概念可简短回顾并链接，不堆砌重复文字或无关拓展。'
            '对照本节实际教材范围检查主要知识、原例题、完整题设、必要条件、量词和结论是否保留，不能靠少列计划或改题逃避内容。'
            '按给定读者起点在首次依赖处补足后文真正需要而尚未学过的先修知识；已讲过的简短回顾并链接，'
            '说明眼前为何需要此概念、公式或方法、帮助解决什么困难；核对正文、解释、例题之间的术语、记号、顺序与结论连贯一致。'
            '允许等价讲解和正确勘误，不要求逐字复述来源；实际来源片段不足时如实说明，不能编造已核对原书。'
            '已有自测的题意、答案和反馈须一致且正确；自动评分题的 scoring.accepted_inputs、'
            '误答反馈与 test_cases 期望须相互一致并能正确评分。开放自评题须给可对照的答案和解释，不宣称能自动判断。不要强制每目标加题或配图。'
            '原文的 LaTeX 公式代码、格式或错别字可能有误，应结合数学知识识别并改正。'
            '逐句区分已知事实、假设与待证结论，前后范围和语气须一致；反证中的假设必须明确是假设，'
            '不能先把假设写成已成立的事实，再在后面证明它不存在。'
            '缺内部证据 ID、AST、绑定、计划字段或模型不代表教学内容错误，不要求补齐这些内部表示；读者短链接的语义对应仍须核对。'
            '保留内容原意与全部有效教学内容；不要截断、按同名删题或猜来源。只输出合法 JSON。')
        if role == 'reviewer':
            task = ('你是正文实质审核者，一次阅读完整 candidate_content、实际 source_context、读者起点和 author_media。'
                '只输出现有 verdicts 数组，按实际发现可有多条判词；每条只须 status（pass|fail|unknown|not_applicable|not_run）与 reason。'
                'criteria 提示应覆盖的质量范围，不是固定输出表；不要求 criterion_id、version、evidence_refs，也不为凑分类逐项盖章。'
                '完整核对知识、内容遗漏、先修、连贯性、理解所需讲解、定理/性质/例题必要步骤、已有自测答案与评分及具体媒体需求。'
                '没有发现问题时说明实际检查范围与依据；不能只看机器状态。不能要求未提供的自测。'
                '不能只看公式是否正确，还要看读者能否理解其含义、条件及推导依据。缺讲解导致含义或用法不清时在知识项指出具体缺口；'
                '类比把特例说成一般情形、误区提示算错或漏必要条件，应判 fail 并指出实际原句或公式及错误条件。'
                '方法选择、必要先修或用法说明缺失而实质阻碍本节学习时，定位首次依赖处，解释当前正文为何不足及应补什么。'
                '关键变化只给结果，导致读者不能复现或区分容易混淆的对象，应指出具体缺步及其影响。'
                '不要求所有正确推导另列所有可能错法；认为易错支持确有缺口时，必须说明会混淆什么、当前讲解为何不足、补哪一句或哪个反例能解决。'
                '只有实际提供的证明或演算缺步，才按推导完整性指出；未提供可选正式证明不算缺步。必要方法、条件或依据缺失仍按实际教学缺口指出。单纯风格偏好和可选拓展不构成失败；独立补充、就地入口及其语义对应按统一阅读组织规则核对。'
                '必要条件、结论与直观解释留在可见主线；所有证明默认折叠、点击展开，包括首次完整内嵌的定理证明；补充易错、直觉应独立组织并就地链接，不能把避免重复解释成取消相应对象或入口。'
                '只是再加一种类比、换排版或重说已有内容，在 reason 标明建议，不作为实质返修要求。'
                '先查看实际内容，再判断是否没有适用的推导或自测；没有时可说明不适用，不强制返回对应行。'
                '逐概念、定理、例题考虑媒体能否明确降低理解难度；若遗漏有明确收益的动画，应指出具体段落或步骤、难跟随的关系、'
                '需要怎样的可见变化以及比现有讲解更易理解之处，在现有 reason 中提出补申请要求。'
                '查看 author_media 中是否已有相应请求，不以缺内部绑定、没有图数配额、个人审美或未制作的成品判失败。'
                '已申请只证明有制作计划，不能宣称成品效果已通过；实际效果由后续现有媒体审核检查。'
                'repair_context.previous_tickets 包含此前正文或媒体审阅指出的正文问题；对照当前完整正文逐一确认是否已修复，'
                '当前仍存在的问题分别判 fail，已解决的说明实际依据，无法确认用 unknown；不能把旧版原句当当前错误重复要求改写。'
                '确认修复实际提供的证明或演算中的缺步时，要找到新增的推导链；只补法则名不算解决。旧工单若只是强迫添加可选正式证明，不继续强求，说明当前必要方法和条件是否已讲清。'
                '审核严格程度不随剩余返修次数改变；返修耗尽由程序保留问题继续交付，不能据此把 fail/unknown 改为 pass。'
                '判 fail 时在 reason 写清位置、错误原句或公式、原因、应如何修改；缺步骤时指出应补位置及所缺步骤。'
                '多个独立问题分别写清，不能用一句笼统判词掩盖；非必要改进标为建议，不作为 fail。'
                '宿主没有做数学或评分验证，请独立判断；不能因机器未建模或未提供引用而判 fail。'
                '判断实质问题确需额外原文或画面而当前材料不足时用 unknown，说明具体限制；不能仅因没有来源ID或摘录判无法审核。'
                '无法判断用 unknown，如实说明原因，不改正文，不把不确定改成 pass。')
        else:
            task = ('你是教材内容作者。返回完整 JSON 对象：content 是对象，content.body_markdown 保存连贯的阅读正文，'
                '其余教学内容也放在 content 内，例如 explainables、annotations、examples、learning_checks、concepts。'
                '正文先讲清主要内容并在概念首次出现处给出名称与核心含义的小块；细解释放 explainables，易错和直觉分别放 annotations，正文相应位置用短链接打开；复用冻结目录概念时写 CONCEPT 标记。'
                '按实际难点提供独立补充和就地链接，不为凑数量拆卡，也不把冻结目录的一句定义当成本节必要讲解的替代品。'
                '用现有字段承载讲解：explainables.what/markdown 说清对象、符号和含义，why 说明眼前用途；'
                'annotations.kind 用 note（说明）、derive（补充推导）、pitfall（易错）、intuition（直觉），这些是用途而非每节必齐的栏目。'
                'examples.approach 说明选法理由，steps.markdown/basis 展示变化及合法依据，key_points 写解后可迁移的回顾，pitfalls 用短提示链接 annotations 中对应的独立易错说明，不复制详情。'
                '首次依赖所必需的先修在正文就地接上；补充回顾可复用 prerequisite_recaps 或解释，主线必要的方法仍在正文中连续可读，所提供的证明和完整例题保留并默认折叠。'
                '首次生成同时给出 media_requirements.requests 与 decisions，按理解任务决定媒体，'
                '写作时逐概念、定理和例题检查抽象对象、空间关系、变量变化、变换过程、推导联系及易混情况能否从可视化获益。'
                '有明确教学收益就提出需求，不必等文字讲不清；“文字足够”“都是公式”“没有必须动画的过程”不能单独作为不配媒体的理由。'
                '一张图适合并置关系与边界对比；动画适合连续变化、逐步变换或对象如何对应，不限于物理运动或几何移动。'
                '例题中的分配项来源、换元对应、变号、抵消、合并或前后关系若用动作更容易跟随，就应提出具体动画需求。'
                '只把整行公式依次淡入不够；应显示项的对应和变化并保留依据，正文仍给完整推导。静态关系适合图片，过程追踪按收益选择动画。'
                '例如正文涉及共轭时，可观察点与共轭点随虚部变化始终关于实轴对称；涉及复数加减时，可观察向量平移如何对应结果。'
                '这些只是按实际内容选择的机会，不是指定必做项目；不得引入本节未准备的知识。'
                '说明展示什么、让读者观察什么、具体帮助理解哪一点；没有明确收益或仅装饰/重复排字时可不配，并给具体理由，不设图数或动画数。'
                '请求 kind 用 image 或 animation；动画在 animation 内说明 initial_state、steps、key_pauses、final_state。'
                '位置用 placement 对象：target_pointer 指 content 内实际文字字段（如 /body_markdown），anchor_text 复制该字段中的唯一原句，position 用 after；'
                '不编造哈希或字符偏移。示例仅说明可用的组织方式，按本节需要取舍；这些字段不构成额外结构审核。'
                '若提供自测，优先沿示例使用现有 kind、statement、input_spec、scoring 和 answer，选择题用 kind=choice、选项 {id,label}，'
                'accepted_inputs 填正确选项 ID；不自造无法执行的评分方法。开放解释题用 self_explanation 并由读者对照答案自评。'
                '输出前在同一次生成内检查：读者能否理解含义、为何选此方法、必要先修是否接上、推导是否连续、'
                '最易误解处是否解释、解后能带走什么方法、图或动画是否有理解收益；必要内容留正文，补充迁移为独立对象并检查短链接，不复制讲解；只输出已有内容结构，不新增自查报告。')
            if role == 'repair':
                task += ('以 frozen_context.content 为底稿，仅修复 repair_tickets 与 previous_rejection 的明确问题及必要关联，'
                    '保留未涉及的正确直觉、误区、关键点、链接和媒体；补新讲解时自行与上一稿比较。'
                    '其余内容保持；输出前自行逐项对比上一版，确认问题已改好且没有无关改动，'
                    '不另交核查报告。返回完整 content。frozen_context.author_media 是上一版媒体需求；'
                    'animation_requirement_example 只供参考教学写法，不是新工单；不得照搬示例ID、题目或无关媒体，也不能为模仿示例改写无关正确内容。'
                    '工单明确要求补媒体或修正受正文改动影响的需求时，同次返回 media_requirements，保留原请求的 request_key、顺序和未涉及内容，'
                    '新请求追加在末尾。未涉及媒体时省略 media_requirements，由宿主沿用；不能删图或改成 none 来消除问题。')
                task += '\n' + REPAIR_RESPONSE_RULES
        payload = {'role_task':role, 'reader_presentation_example':reader_presentation_example(), 'frozen_context':context,
                   'learner_profile':policy.learner_profile}
        if compacted:
            payload['input_context_notes'] = CONTEXT_NOTES
        if role in ('author', 'repair'):
            payload['reader_reference_contract'] = {'rules':
                '在正文需要理解支持的位置使用 [[EXPLAIN:解释ID|显示文字]]、[[NOTE:注释ID|显示文字]]、'
                '[[EXAMPLE:例题ID|显示文字]]，对应 content.explainables/annotations/examples 中已有对象 id。'
                '引用已有概念用 [[CONCEPT:目录中的真实ID|显示文字]]，ID 从 frozen_context 中 source_context.reference_catalog'
                '（作者上下文直接为 reference_catalog）选取，concepts 可登记 {id,role}。'
                '批注统一用 NOTE 标记，一个独立说明对应一个对象；在相关正文词句处给短链接，避免把多项补充混为大卡。引用前文性质、定理或定义时保留适用条件，教学显示使用准确名称，原编号保留在来源资料；'
                '首次定理用独立 EXPLAIN 段完整内嵌其 theorem 对象，之后用 CONCEPT 原地复用完整讲解，不编造名称或无关目标。'
                '只链接实际相关且存在的目标；没有合适目录条目时自行写解释并用 EXPLAIN，不编造目录ID。'
                '路径相对于 content，数组用数字位置，如 /examples/0/steps/0/markdown。'
                '链接和位置说明帮助阅读，不新增必引ID、精确绑定或覆盖数量的拒收规则。'}
        if role == 'author':
            payload['output_schema_example'] = balanced_authoring_example()
        if role in ('author', 'repair'):
            payload['animation_requirement_example'] = balanced_animation_example()
            payload['theorem_index_example'] = theorem_index_example()
        if role == 'repair':
            payload['tickets'] = [as_dict(t) for t in tickets]
        return [{'role':'system','content':task+'\n'+common+'\n'+shared_rules},
                {'role':'user','content':json.dumps(_cache_friendly_order(payload),ensure_ascii=False)}]
    # Review/repair may carry SourceContext inside a richer request envelope.
    source=context.get('source_context',context)
    if (not reviews_frozen() and source.get('learner_profile')
            and content_digest(source['learner_profile'])!=policy.learner_profile_digest):
        raise ContractError('prompt learner profile differs from frozen policy')
    task = REVIEW_TASK if role == 'reviewer' else load_prompt_asset('content/'+role+'.md').text
    if role == 'repair':
        task += '\n' + REPAIR_RESPONSE_RULES
    system=task+'\n'+full_rules+'\n'+shared_rules+'\n'+prompt_rules(policy,role)
    payload={'role_task':role,'reader_presentation_example':reader_presentation_example(),'frozen_context':context,'policy_digest':policy.digest,
        'learner_profile':policy.learner_profile,
        'learning_check_examples':learning_check_examples(),
        'math_contract':describe_math_contract(),
        'math_schema_example':math_schema_example(),
        'math_representation_examples':math_representation_examples(),
        'teaching_examples':[dict(kind=kind,requirement=req,bad=bad,good=good) for kind,(req,bad,good) in PEDAGOGY.items()]}
    from .reader_references import reader_reference_contract, teaching_text_fields
    payload['reader_reference_contract'] = reader_reference_contract()
    system += '\n' + payload['reader_reference_contract']['rules']
    if isinstance(context.get('content'), dict):
        payload['reader_text_fields'] = teaching_text_fields(context['content'])
    if 'source_text' in source:
        from .exercise_regions import numbered_source_lines
        payload['source_lines'] = numbered_source_lines(source['source_text'])
        source['source_text'] = {'$source_lines': '/source_lines', 'join': ''}
        payload['input_context_notes'] = CONTEXT_NOTES
    elif compacted:
        payload['input_context_notes'] = CONTEXT_NOTES
    if role == 'author':
        payload['output_json_schema'] = ContentAuthoringV1.model_json_schema(by_alias=True)
        payload['output_schema_example'] = authoring_schema_example()
    else:
        payload['content_json_schema'] = LessonContentV4.model_json_schema(by_alias=True)
        payload['content_schema_example'] = content_schema_example()
        if role == 'repair':
            payload['tickets'] = [as_dict(t) for t in tickets]
            payload['output_contract'] = (context.get('output_contract')
                or {'schema_version':'ContentAuthoringV1', 'instruction':'返回完整正文 content（LessonContentV4）；媒体由宿主沿用。'})
    if role in ('author','repair'):
        payload['theorem_index_example'] = theorem_index_example()
        # Show layered math examples without expanding the full source and schema.
        fields = [json.dumps(key)+': '+json.dumps(value,ensure_ascii=False,
            indent=2 if key in ('math_schema_example','math_representation_examples') else None)
            for key,value in payload.items()]
        payload_json = '{\n'+',\n'.join(fields)+'\n}'
    else:
        payload_json = json.dumps(payload,ensure_ascii=False)
    messages=[{'role':'system','content':system},{'role':'user','content':payload_json}]
    # Generation checks the same bound inside its durable journal so an oversized
    # author/repair request receives outer-loop feedback before any API call.
    if (not reviews_frozen() and role == 'reviewer'
            and len(json.dumps(messages,ensure_ascii=False).encode())>MAX_REQUEST_BYTES):
        raise ContractError(f'{role} request exceeds {MAX_REQUEST_BYTES} bytes; batch context without truncating evidence')
    return messages
