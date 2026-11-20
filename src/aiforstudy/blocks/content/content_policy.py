"""Single production teaching policy: executable local receipts and frozen review items.

Local checks establish structural evidence only. Semantic teaching and source fidelity
remain reviewer items, never reported as passed by a code-only inspection.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from .content_contracts import ContentPolicy, ContentCheckReport, LessonContentV4, as_dict, content_digest, parse_content
from .source_context import LearnerProfileV1, SourceContext, FrozenReferenceCatalog
from .reader_document import project_reader
from ...errors import ContractError

MAX_CONTENT_BYTES = 200 * 1024
# 1MB 是早期的过度设计：整份重写的纠正轮请求 = 完整提示词 + 冻结底稿 + 上一次答复 + 诊断，
# 真实复变函数单元（正文 JSON 约 40KB、单次 preserve 约 500 条路径）一算就超。
# 历史回归案例：SS01/SS02 的正文返修都死在这条上限上，
# 于是 D16-1「输出不合格 → 可纠正错误 + 轮次」在真实体量下永远走不到。
# 现在给足余量；超限仍按原口径**保留响应、绝不截断证据**，不是静默裁掉上下文。
MAX_REQUEST_BYTES = 8 * 1024 * 1024
STYLE_WORDS = ('综上所述','总而言之','一言以蔽之','值得注意的是','需要指出的是','众所周知','显而易见','不难发现','不难想到','毋庸置疑','某种意义上','由此可见','如前所述','首先，其次','换句话说，')

# Author, repair and review share the reader's requested organization. This is
# prose guidance for existing content objects, not a new schema or local gate.
READER_SUPPORT_RULES = r'''连续讲课以教材章节顺序和先修依赖为骨架：先交代前节已得到什么、这里仍缺什么，再解释新对象为何出现、符号和条件是什么意思、怎样使用，最后自然接到下一问题。跨章指代须核对真实章节主题；不把尚未解释的内容当作已经掌握，不把定义—性质的小块或卡片拼接当作正文，也不靠照抄原文再加首尾套话充当重写。核心直观解释与当前必需的先修含义留在正文，读者不打开补充仍能跟随因果主线；正式证明与完整例题可选，提供时显式标记角色、保存完整并默认折叠。
教材结论逐项保留：对照本节原文保留全部定义、定理、推论、性质、恒等式、判别法、运算和变换公式，并写出完整适用条件。初学者友好通过解释含义、连接前后知识来实现，不能通过删减结论、只留定理名称或把多条性质概述成一句话实现。只折叠证明，不折叠结论；完整条件与结论在正文相关位置默认可见，不把原文折叠区、查阅链接或结论总表当成正文已讲。提供的完整例题整体折叠，其可迁移的方法和结论仍在主线可见。
每个图片或动画围绕明确教学目标：前文提出观察任务，后文解释观察支持的判断，再接后续问题；不机械凑图，不按标题关键词套无关函数或场景。caption和observation是可直接给学生读的中文与LaTeX解释，不用制图命令作讲课稿；中文口语与可视 LaTeX分开表达，沿用既有字段，不新增字段、API调用、审核项目或轮次。
正文与补充的阅读组织：正文对应位置的补充小链接打开独立可拖动浮窗；首次定理的独立 EXPLAIN 段完整内嵌，条件、结论和直观解释可见，所有证明默认折叠、点击展开。作者只写既有内容对象与标记，交互由阅读器负责，不输出额外 HTML、脚本或字段。
概念、定理、性质及运算法则的链接放在公式结束符外的相邻正文，不嵌入 $…$、$$…$$、\(…\)、\[…\] 内。例如先写 $z_1+z_2=z_2+z_1$，再写对应的交换律链接。公式里的数学变量、符号和条件完整保留，旁边再给解释入口。旧内容中误放的链接由渲染兼容移到公式旁；仅此显示问题不要求重写整节，链接的知识含义和目标仍需正确。
首次引入概念时，在正文对应位置用普通 Markdown 引用块给出名称、核心含义、符号和必要条件，短而完整。例如：
> **概念·共轭复数**
> 若 $z=x+iy$，其中 $x,y$ 为实数，则 $\bar z=x-iy$ 称为 $z$ 的共轭复数。
这里是写法示例，不复制无关概念或示例 ID。已有明确命名且准确完整的定义小块可沿用。前文已学概念只需在当前用法处简短回顾并链接，不为每次 CONCEPT 引用再复制一块，也不为定义额外强加证明。
进一步的含义解释、用途、对象对应或边界比较放现有 explainables，用 what/why/markdown 等已有字段讲清，在正文相关词句写 [[EXPLAIN:解释ID|短显示文字]]；复用冻结目录中真实概念用 [[CONCEPT:真实ID|概念名]]，没有合适目录对象时写本节 explainables，不编造目录 ID。
易错和直觉分别是现有 annotations 中的独立对象，kind 用 pitfall 或 intuition；其他补充可用 note 或 derive。每个对象围绕一个明确问题，多个独立易错、直觉或说明不混成一张大卡。在读者需要的正文或例题步骤处用 [[NOTE:注释ID|短显示文字]] 指向相应对象，显示文字说明所点内容，不能只在文末列一堆无上下文链接。卡内解释与链接所在词句、对象和条件要对应。
必要条件和主线必要方法在正文可见；提供的完整推导保存在正文或例题；首次定理通过独立 EXPLAIN 段完整内嵌，证明默认折叠、可展开，仍属于完整教学内容，无须在 body_markdown 重复一份。首次依赖所必需的知识也要就地讲清；读者不打开浮窗仍能跟完主线。补充 derive 只能展开非必需的另一种推导或回顾，不能把定理、性质或例题应讲的步骤藏入浮窗。
避免重复是迁移和复用，不是省掉呈现：已有补充说明可迁入相应 explainables/annotations，正文保留核心内容、必要过渡和短链接；同一补充只保存一份，通过链接复用，不能以正文已有为由取消用户要求的独立补充对象和就地入口。返修只处理明确问题及必要关联，保留未涉及的正确内容、身份和链接，不借迁移删条件、改结论或扩写无关部分。
所有证明（包括首次定理及正文夹带证明）默认收起，条件、结论和直观解释可见；所有例题的题干、思路、步骤、依据与答案整体默认收起，只留标题入口，点击后完整读取。折叠只改变显示，审核仍读取全部内容，不降低步骤完整性标准。正文证明用独立“证明：”段与“证毕。”标记边界，后续解释或概念另起段。
不设固定字数、卡片数、链接数，不要求每节凑齐所有 kind；没有实际补充需要时不编空卡。以上是生成、返修和现有内容审核的阅读规则，不新增字段、数量或格式的机器拦截，也不增加审核轮次。
现有审核须完整检查卡内内容及其实际链接上下文，核对显示文字、目标对象、数学条件和解释的语义对应，不能只因卡片存在或 ID 可解析就宣布通过。知识或算式错误、链接到另一概念或误区、必要知识被藏起、缺少所需说明或入口而影响当前理解，都应在现有 reason 中指出具体词句、目标或应出现的位置，说明影响及应改什么；不要只报数组为空或链接数不足。额外类比、纯审美或不影响理解的格式偏好仅作建议，不据此判 fail/unknown。'''

THEOREM_INDEX_RULES = r'''定理讲解与复用：复用现有 ExplainableV4，在 content.explainables 中用 kind=theorem；concept_id 指向冻结来源目录中准确对应的现有 C-ID，不编造 ID，也不把不同数学结论绑定到同一条目。
name 优先用已知的正式定理名称；没有公认名称时，根据真实条件与结论用准确的描述性名称，不冒充公认名称。原编号保留在来源目录与原书资料中供核对，不作主要教学标签，也不篡改原书编号；不新加命名调用或字段。
what 写完整条件与结论，why 简短说明含义或用途，如提供证明，在 steps 或 markdown 给出完整证明，每一步说明变换与依据。首次主线在适当位置单独一段写 [[EXPLAIN:本定理解释ID|定理名称]]；阅读器把该既有对象完整内嵌，条件、结论和直观解释可见，所有证明默认折叠、点击展开，无需再在 body_markdown 复制整段条件、结论和证明，也不另造一张概念核心块重复此定理。
之后正文和生成例题用 [[CONCEPT:C-ID|定理名称]] 原地浮窗复用同一定理，窗内证明默认折叠、可以展开，不要求跳节；引用处仍交代当前适用的条件和使用理由，不用链接替代当前例题应展示的推导。
frozen_context.available_theorem_names 只提供前文已完成定理的名称列表，可为空，不附前文条件、结论、解释或证明。依据定理名称、自身数学知识及当前教材和正文核对当前用法，引用身份仍从已有来源目录选取，不凭名称编造 C-ID。沿用准确名称，不重复生成同一定理的完整讲解；完整前文供阅读器复用，不再加入模型请求。仅凭名称不得假称读过或已核验前文证明；主线必要的方法和依据仍须完整；所提供的证明和完整例题须逐步完整。
同一数学定理只保存并复用一份完整解释，不能仅因同名或同编号合并；核对准确来源 C-ID、条件和结论，遇到具体矛盾如实指出，不把相关但不同的定理当成同一个。
定向返修保留已有解释 ID、正确 concept_id、名称及正确链接，仅修改被指出的错误及必要关联；若来源确实错绑，依据当前真实目录修正对应关系，不通过换 ID、删证明或复制整段来消除问题。
现有知识与步骤审核完整阅读本次正文中首次内嵌定理的 what、why、steps/markdown 及当前引用上下文；前文只给名称，根据数学知识检查当前应用的条件和推导，不要求补交前文证明。不要因为证明存于 explainables 而要求 body_markdown 再抄一份，也不能把首次定理当成可选补充漏审。实际知识错误、必要条件遗漏、证明缺步或来源错绑按既有判词在 reason 中写清位置、原句或公式、影响及修改要求。不按卡数、名称字数、原编号是否出现在教学标题或字段外观拒收，不新增 schema、机器硬门、审核项目、调用或轮次。'''

CONVENTION_RULES = '''教材约定与勘误：教材明确约定了本书符号、对象或运算的含义与适用范围，且当前使用自洽时，按该约定判断。不同于模型常用约定本身不是错误，不得把另一套惯例强加给本书。要求修改必须指出具体矛盾、反例或推导问题，并交代在本书条件下为何成立；确实无法确认时记录具体疑点，不擅自改正文。约定不能掩盖同一适用范围内的自相矛盾或错误推导，明确的数学错误仍需修正。原文 LaTeX、排版、错别字和识别错误可依据数学知识纠正，但要区分纠正误识别与改变原书约定。
记号写法与教材不同，但正文已明确说明含义且前后自洽时，不判 fail，只可作为润色建议（例如算术根是否加圆括号）。
多值函数的分支选择会影响结果或符号时，必须明确写出所用分支及其取值范围；未写明而影响结论的，按缺少必要条件处理。'''

SHARED_CONCEPT_RULES = '''公共概念勘误：发现冻结目录中的公共概念条目有明确知识、公式或条件错误时，除修复本节受影响内容，还在现有 content.corrections 中提出公共修正版；不能只删除 CONCEPT 链接或换成本地 EXPLAIN 来绕过错误卡片。每条使用现有 id、original、corrected、reason、source_refs；source_refs 用 kind="concept"、真实 concept_id 和 json_pointer="/formal" 或 "/definition" 定位公共字段，original 为该字段完整当前值，corrected 为完整修正版，reason 说明具体错误与依据。不编造 ID，不借勘误改其他概念。审核应对照公共原值、修正版、本节用法及教材约定核对这一修正；当前既有正文审核确认后，程序才在同一次运行内同步公共条目。未核实的提案不当成已确认事实，历史审核记录不改写。
frozen_context/source_context 的 concept_index 显示的是叠加修正之后的值；审核材料 shared_concept_corrections 逐条给出公共原值 before、现值 after、来源单元和状态（已发布、本节提案、本节保留）。不能因为“显示值已经等于修正版”就要求删除本节的修正提案：删除后公共原值会恢复旧错。旧正文若实际写入错误原句或公式，只按已有定向返修处理受影响内容；仅引用卡片时复用修正后的卡片，不重写整节。'''

REVIEW_STABILITY_RULES = '''审核与复审：首次审核尽量集中指出所有已确认的实质问题，分别说明位置、原句或公式、原因及修改要求；没有证据的猜测或可选表达润色不作为失败。复审先核对 repair_context.previous_tickets 中的问题是否已修好，再指出新发现的明确错误。repair_context.previous_verdicts 是上一轮原始判词（可含已通过或可选建议），previous_content_hash 标记它所针对的旧正文，只作历史依据，不能替代当前内容。已修好的问题不重复退回；改变上一轮判断时，在现有 reason 说明具体理由，例如此前漏检了什么条件、发现了什么反例或本次出现什么新错误。此前作为可选建议的内容，不能只因这次偏好不同就升级为必改。新发现的明确错误仍应报告，不受旧 pass 限制；不要为了维持一致而隐瞒错误。定向返修只改明确问题及必要关联，自行对照上一份内容排除无关改动，不新增审核字段或轮次。
source_obligations 等处的 source_excerpt 是教材原文，可能含 OCR 旧字或原书旧写法，不是公共卡片的现值；引用或评判卡片内容一律以 concept_index 当前值为准，并说明所依据的是哪一个。
复审时对上一轮每条问题逐条写明“已修 / 仍在 / 撤回（附理由）”，并区分本轮新出现的错误与此前漏检、本轮才发现的旧错误。不能因为返修轮次将尽就放过明确错误。
candidate_content.ticket_responses 是作者对上一轮工单的说明（decision=fixed 或 kept，basis 为依据或反例）。对其中每条 kept 必须逐条回应：依据成立就撤回原意见并在 reason 说明；不成立就给出具体反驳（指出依据的错误、反例或推导问题），不能只重复原意见。'''

#: 修改计划 B3/B7：返修时公共卡必须改正式字段；审核建议本身有误时可保留原文并说明依据。
REPAIR_RESPONSE_RULES = '''问题出在公共概念卡片（concept_index 中的条目）时，必须在 content.corrections 中给出该卡片字段的完整修正版：corrected 是改正后的完整值，且与 original 不同；只在正文里解释或绕开错卡不算修复。
返修前先核对每条审核建议本身是否成立。建议成立就按建议修改；建议本身有误（例如所要求的条件过强、与反例冲突，或与教材约定矛盾）时保留原文，不照改。无论哪种，都可在可选字段 content.ticket_responses 中逐条写明：[{"ticket_id": 工单 ID, "decision": "fixed" 或 "kept", "basis": 修改说明或保留的具体依据/反例}]。该字段不给读者看，只交给下一轮审核；它不增加返修轮次，也不能代替真实修改。'''

PROFILE_PATH = Path(__file__).resolve().parents[4] / 'config' / 'teaching_profiles.json'
if not PROFILE_PATH.is_file():
    from importlib.resources import files
    PROFILE_PATH = Path(str(files('aiforstudy').joinpath('resources/teaching_profiles.json')))


class ContentPolicyV2(ContentPolicy):
    """Copy-on-read policy including the complete frozen learner profile."""
    def __getattr__(self, key):
        return self.as_dict()[key] if key in self else super().__getattr__(key)

    def __getitem__(self, key):
        return self.as_dict()[key]


def load_learner_profile(profile_id=None, *, path=None):
    data=json.loads(Path(path or PROFILE_PATH).read_text(encoding='utf-8'))
    if data.get('schema_version')!='TeachingProfilesV1': raise ContractError('unsupported teaching profiles config')
    target=profile_id or data['default_profile_id']
    matches=[p for p in data['profiles'] if p['profile_id']==target]
    if len(matches)!=1: raise ContractError('missing/duplicate learner profile '+target)
    return LearnerProfileV1.model_validate(matches[0])


def _rule(identity, requirement, *, method='code', severity='blocking', applies='content', counterexample='', repair=None, evidence_kinds=None, version=2):
    return dict(rule_id=identity,version=version,phase='content',severity=severity,applies_to=applies,
        requirement=requirement,check_method=method,executor=identity if method=='code' else 'criterion_reviewer',
        pass_criteria=requirement,counterexample=counterexample,required_evidence_kinds=evidence_kinds or [],
        repair_guidance=repair or '指出具体缺失或错误及当前证据；以上一份内容为底稿，仅修复该问题及必要关联，未涉及内容保持原样；保留来源事实与完整条件，输出前对照底稿排除无关改动，再返回完整内容。')


# Pedagogical distinctions are policy data, shared by author/reviewer/repair/spec.
PEDAGOGY = {
    'definition': ('定义和概念准确给出名称、含义和必要条件，不为定义强加证明、额外例子或反例；首次概念核心和细解释按读者补充组织规则呈现。不能按卡数或格式判错；知识错误、错误链接或实质阅读缺口须说明具体依据。', '把“非负”说成“正”属于知识性错误；准确陈述定义但未给证明不构成错误。', 'n≥0 包含零；n>0 不包含零。可用 n=0 区分两个条件。'),
    'theorem': ('保留全部假设及结论，正式证明可选，提供时完整展开并写清每一步变换与依据；核对条件和适用范围，不能跳过中间步骤或只说“显然”“可得”，类比不能代替证明或削弱条件。首次 theorem 解释经独立 EXPLAIN 段完整内嵌，条件、结论和直观解释可见，所有证明默认折叠、点击展开，后续按准确来源 C-ID 用 CONCEPT 复用；完整内嵌证明无须在 body_markdown 重复。名称准确，来源、条件和结论对应，不能仅按同名同编号合并。', '已提供的证明略去关键等式变换。', '证明按假设、逐步推导、结论展开；每一步指出使用的定义、已知定理或运算规则。'),
    'procedure': ('按目标说明输入与适用条件，关键步骤有可追溯依据，并解释结果意味着什么。', '跳过必要中间步，只写“计算可得”。', '先确认总数不为零，再用部分数除以总数；所得比值表示部分占整体的比例。'),
    'example': ('完整例题可选；提供时保留原题和条件，解释选法，完整展开每一步推导、计算或作图并说明依据，不能省略中间步骤或只给结论；回看答案，教学构造须标明来源链。', '替换原题以便套公式，答案只有数字；或用“计算可得”跳过计算过程。', '原题要求比例；先识别部分与整体，再检查分母不为零，逐步代入并计算，最后说明比值的含义。'),
    'convention': ('明确符号或约定的含义、范围与边界；短而完整即可，不强制预热、推导或固定例子数。', '只说“按惯例记为 n”，不交代取值范围。', '本节 n 表示非负整数，包括零；这一约定只在本节使用。'),
}


def load_content_policy(profile=None):
    profile=load_learner_profile() if profile is None else LearnerProfileV1.model_validate(as_dict(profile))
    rules=[
        _rule('CONTENT-SCHEMA','仅接受严格 LessonContentV4；身份唯一，规范字段完整，禁止媒体字段和标记。',counterexample='把 v3 的 thought/text 原样当作 v4。'),
        _rule('CONTENT-SOURCE-READY','来源上下文与读者配置绑定当前版本，独立义务提取须 semantic_verified 方可正式批准。',counterexample='把作者漏列的主张当作教材没有要求。'),
        _rule('CONTENT-COVERAGE','逐条对照冻结来源义务检查完整 candidate_content 的实际教学内容；claim、goal、teaching_evidence 的缺少绑定只作提示，不能据此判正文缺内容、fail 或 unknown。已有 claim 的类型及条件、结论字段仍须有效（原义务无条件时允许 conditions=[]）。是否漏讲、遗漏必要条件或定理例题推导缺步，由本项及 SOURCE-FIDELITY 阅读正文判断；不能用绑定存在或文字逐字相同冒充数学判断。',counterexample='正文确实未讲才是覆盖缺失；正文已讲但漏绑，不是内容错误。',version=4),
        _rule('CONTENT-REFERENCES','已有来源链、概念、主张、目标、数学模型、原例题和读者引用都可解析；teaching_evidence 缺少 claim_refs 只作定位提示，不能直接判正文缺内容。教学 claim_id 与模型数学关系须双向绑定，同一教学主张可含多关系，每条 relation_id 独立且全部接受验证与留证，不能只核第一条；原题及来源字段保留。',counterexample='主张 D 引用模型 M，但 M 只检查 K；或同主张两条关系只用其中一条的通过结果覆盖另一条。',version=4),
        _rule('CONTENT-EXERCISE-SOURCES','习题与原书答案区必须逐字绑定当前冻结来源范围和摘要；不得覆盖例题或教学义务，也不得作为教学、自测、数学或媒体证据。'),
        _rule('EXERCISE-SOURCE-BOUNDARY','只核对题区所属单元、范围及原文完整性：属于原书习题或答案资料，未吞并理论/例题，未改写、拆题或新增解答；不判断题目或原书答案的数学正确性，不要求求解、讲解或配图。',method='reviewer',applies='exercise_region',evidence_kinds=[]),
        _rule('CONTENT-PREREQUISITES','assumed 依赖仅来自冻结读者配置；recap 指向真实回顾；new_here 的内容须在实际正文中讲清。new_here 缺少教学证据绑定只作提示，不能直接判正文未讲；阅读完整正文核对，实际缺必要先修解释才判失败。',counterexample='作者自行宣称读者已学会未配置知识；正文已有解释但漏绑不是内容错误。',version=3),
        _rule('CONTENT-BASIS','所提供的定理证明和完整例题每一步须有真实可读的推导依据，可写在步骤正文、basis 或相关讲解中。basis 或 basis_refs 缺项只作定位提示，不等于没有依据；结合完整正文判断是否实际跳步或依据错误。已给引用须真实可解析，前步引用不得指向当前或后续步骤。',counterexample='推导实际跳步、只说“根据定义”却未说明所用内容，或引用不存在/后续步骤；正文已解释而漏绑不是错误。',version=3),
        _rule('CONTENT-STRUCTURE','导语、小结与可观察目标真实存在；例题有思路、步骤和结果。自测按需要提供，不要求每个目标出题；已有自动评分自测含提示、答案、错因与测试样例，随文观察或自我解释不强制自动评分结构。',counterexample='只有长篇空泛文字，没有目标或原例题解答；缺少额外自测本身不是错误。',version=3),
        _rule('CONTENT-LEARNING-CHECKS','理解目标可以通过准确正文、完整推导、配图或动画及相应讲解帮助读者达成，不要求每个核心目标额外出题、作答或绑定 understanding_check。仅审核实际提供的自测或作答任务是否只考已教内容；没有自测或作答任务时本项可通过，不能因缺题或缺绑定判 fail/unknown。已有自动评分须实际区分正确、错误、边界输入，所有错因分支可触发；自我解释明确由读者自评。配图与动画需求及理解收益由后续既有媒体审核检查，成品效果在制作后审核；不得以尚未制作媒体为由卡正文或要求补题，媒体也不能代替已提供证明和完整例题应有的完整推导。',counterexample='已有自测把错误输入判正确；没有额外作答任务、没有理解任务绑定或媒体等待制作本身不是内容错误。',version=4),
        _rule('CONTENT-CAPACITY',f'内容最多 {MAX_CONTENT_BYTES} bytes；请求最多 {MAX_REQUEST_BYTES} bytes，超限保留响应而非截断。'),
        _rule('PEDAGOGY-EXAMPLE-MODEL','逐例题核对：例题里凡能写成 math_models 关系的对象/条件/结论，都必须真的建立数学模型并在讲解里展示过程（推导、计算或作图），不能只给文字结论。判定"能写"的最低线是：该例题含等量关系、函数/表达式、方程或不等式、向量/矩阵、坐标或几何量之一。确实无法写成 AST 的纯叙述性例题不在此列。',method='reviewer',applies='example',counterexample='例题只写"由定义可得结论为 1"，没有数学模型、没有过程。'),
        _rule('CONCEPT-EXPLANATION-FIDELITY','逐条核对 introduced 概念在读者正文中的名称、含义和必要条件，首次核心小块、补充解释与就地链接按读者组织规则核对；不为定义额外强加证明或重复讲解，不按卡数、字数或单纯格式偏好判错。已有解释须数学正确，允许等价重述和正确的教学例子、几何直觉。formal 可能含识别、排版或印刷错误，允许依据数学知识纠正，不能仅因与错误原文不同判错。所提供的定理证明和完整例题仍须每一步完整推导。',method='reviewer',applies='unit',counterexample='定义漏掉必要的非零条件属于知识性错误；未额外证明定义不是错误。',version=5),
        _rule('SOURCE-FIDELITY','对照冻结 source_obligations、教学 claim 和真实读者文字，核验正确的含义、必要条件、量词、结论和原题。允许等价重述；定义和概念只核知识正确性，不因省略原文的辅助论证或额外解释而拒收，所提供的定理证明和完整例题则逐步核对完整推导。原文或 formal 的 LaTeX、格式、错别字、识别或印刷错误，允许依据数学知识纠正；涉及数学含义的勘误可用现有 corrections 简述原式、改式及理由，无须另找原始来源证明。不能把错误原文当正确知识，也不能编造已核实原书页面；只有数学含义确实无法判断时才返回 unknown。',method='reviewer',applies='source_obligation',counterexample='漏掉真实必要条件属于错误；正确修复漏符号或不为概念补证明不构成来源失真。',version=4),
        _rule('GOAL-SUFFICIENCY','结合 observable_task 核对教学内容：定义和概念准确陈述含义和必要条件，不强加定义证明或无关拓展；实际理解支持和阅读组织按统一规则核对；即使目标用了“解释为什么”，也不能据此强加概念证明。所提供的定理证明和完整例题须完整展示每一步推导及依据，不能用结果或笼统描述代替过程；涉及知识性错误、必要条件遗漏或定理例题跳步时指出具体位置。',method='reviewer',applies='goal',counterexample='主线必要的方法缺失，或已提供的证明例题跳步；准确陈述概念但未证明不构成不足。',version=4),
        _rule('ASSESSMENT-ALIGNMENT','通读全节，仅核对实际提供的自测或作答任务是否只考已教内容，问题、提示、答案和具体错因是否一致。自动评分题的回归输入须真实覆盖常见误解及边界；随文观察与自我解释不要求自动评分或测试样例，自我解释不得伪装自动证明评分。不要求每个目标额外出题；没有自测或作答任务时可通过，不因缺题、缺绑定或媒体等待制作而判 fail/unknown。配图、动画的理解收益由既有媒体环节审核。',method='reviewer',applies='unit',counterexample='已有题目答案错误或超出本节所教范围；未安排额外题目不构成失败。',version=3),
        _rule('READER-COHERENCE','按教材章节顺序和先修依赖检查连续讲课的因果主线：承接前节的问题、新符号和条件的含义、用途与下节联系须真实，不以定义—性质卡片拼接或照抄代替解释。结合冻结读者起点及完整正文和独立补充检查术语、顺序与跨块一致性；首次概念核心就地可读，细解释、易错和直觉通过相关词句短链接访问，完整核对卡内内容与链接显示文字、上下文和目标的语义对应。必要条件须可读，提供的证明完整保留并默认折叠；首次定理内嵌的 theorem 解释保留可见核心；提供证明时保留展开入口，后续引用可原地回看同一讲解。核对定理名称、来源 C-ID、条件和结论，原编号保留在来源资料，不按同名或同编号合并；不重复保存补充，不按数量、名称字数或格式硬判。实际错误或入口缺失影响理解时须说明具体位置、影响与修改要求。',method='reviewer',applies='unit',counterexample='定义尚未说明，后文就把它用作依据；易错链接打开另一概念的卡片。',version=4),
        _rule('MATH-REPRESENTATION',
            '对照冻结来源、当前读者文字与 claims/conditions/math_models，核对每个数学表征的主张身份、完整条件、定义域及关系。'
            '识别当前数学契约支持的可计算主张；不能通过省略 math_models 或把可计算关系标作抽象来绕过 CAS。'
            '对真正抽象或不可计算的主张不强制编造 AST；结合来源上下文和数学知识判断，定义和概念不要求额外证明或解释，所提供的证明和完整例题仍须完整逐步推导。'
            'exercise_regions 的原书题目与答案仅是保留资料，不是教学主张，不求解、不检查其数学正确性、不要求 AST。'
            '已有 CAS fail 不能被本项或任何模型 pass 覆盖；CAS unknown/not_run 原结果必须保留，不能宣称已经计算验证。',
            method='reviewer',applies='unit',evidence_kinds=[],version=3,
            counterexample='正文写出可计算的等式，却省略数学模型；或用模型 K 的正确计算替主张 D 背书。',
            repair='定位遗漏或错配的主张 ID 和读者片段，为受支持计算补齐同 ID 数学关系与原条件并重跑 CAS；抽象主张依据数学知识核对，定义和概念不强制解释，已提供的证明和完整例题补齐每步推导，保留已有 CAS 状态。'),
    ]
    for kind,(good,bad,_) in PEDAGOGY.items():
        rules.append(_rule('PEDAGOGY-'+kind.upper(),good,method='reviewer',applies=kind,counterexample=bad,
                           version=5 if kind=='definition' else 4 if kind=='theorem' else 3 if kind=='example' else 2))
    rules.extend([
        _rule('STYLE-SENTENCE','剔除公式后单句超过 90 字提醒；40 字为偏好，不是硬门。',severity='warning'),
        _rule('STYLE-COVERAGE','读者正文低于 max(250 字, 原文×0.6) 只报覆盖异常提醒；充分性另有证据审核。',severity='warning'),
        _rule('STYLE-TEMPLATE','模板套话只提醒：'+'、'.join(STYLE_WORDS),severity='warning'),
        _rule('STYLE-LABEL','“依据：”“理由：”等填空式措辞只提醒。',severity='warning'),
        _rule('STYLE-METAPHOR','同组比喻超过每千字三次只提醒；不可据此判定数学错误。',severity='warning'),
        _rule('STYLE-PERSONIFICATION','数学对象拟人化表述只提醒；错误须另举条件或推理证据。',severity='warning'),
    ])
    return ContentPolicyV2(schema_version='ContentPolicyV2',version=2,learner_profile=profile.as_dict(),
        learner_profile_digest=content_digest(profile),rules=rules,
        allowed_semantic_fallbacks=['source_semantic_review'],
        status_values=['pass','fail','unknown','not_applicable','not_run'])


def prompt_rules(policy, audience):
    if audience not in ('author','reviewer','repair'): raise ContractError('invalid policy audience')
    return json.dumps({**policy.as_dict(),'policy_digest':policy.digest},ensure_ascii=False)


_DEFAULT_FAILURE_EXPECTED = '修复当前规则的具体失败，并重新运行检查。'
_DEFAULT_REPAIR_HINT = '以上一份内容为底稿，仅修复明确问题及必要关联，未涉及内容保持原样；保留原题与来源事实，输出前对照底稿，再返回完整内容。'


def _failure_guidance(specific, default, general):
    if not specific or specific in (default, general):
        return general
    return specific + '\n规则总体要求：' + general


def _issue(rule,path,key,problem,**extra):
    return dict(rule_id=rule,json_pointer=path,owner_id=extra.pop('owner_id',path),issue_key=key,
        problem=problem,severity=extra.pop('severity','P1'),expected=extra.pop('expected',_DEFAULT_FAILURE_EXPECTED),
        repair_hint=extra.pop('repair_hint',_DEFAULT_REPAIR_HINT),**extra)


def _walk(value,path=''):
    if isinstance(value,dict):
        yield path,value
        for key,child in value.items(): yield from _walk(child,path+'/'+key)
    elif isinstance(value,list):
        for i,child in enumerate(value): yield from _walk(child,path+'/'+str(i))


def _prepare(c,s,p):
    if p.schema_version!='ContentPolicyV2': raise ContractError('production requires ContentPolicyV2')
    parsed=parse_content(c)
    if not isinstance(parsed,LessonContentV4): raise ContractError('production check requires LessonContentV4')
    source=SourceContext.model_validate(s)
    doc=project_reader(parsed,source.reference_catalog)
    return dict(c=parsed.as_dict(),s=source.as_dict(),p=p,doc=doc,
        reader={b['json_pointer']:b for b in doc.blocks},
        claims={x.id:x.as_dict() for x in parsed.teaching_plan.claims},
        goals={x.id:x.as_dict() for x in parsed.teaching_plan.goals})


def _linked_claims(state, obligation):
    return [c for c in state['claims'].values() if any(r.get('obligation_id')==obligation['id'] for r in c['source_refs'])]


def _bound_evidence(state, claim_id):
    return [e for e in state['c']['teaching_evidence'] if claim_id in e['claim_refs'] and e['reader_pointer'] in state['reader'] and e['goal_id'] in state['goals']]


def _source_ready(st):
    s,p=st['s'],st['p']; out=[]; rule='CONTENT-SOURCE-READY'
    if s['obligation_extraction_status']!='semantic_verified':
        out.append(_issue(rule,'/','semantic-extraction-not-run','来源仅有结构清单；独立语义义务提取尚未验证。',reason_code='not_run',owner_stage='lesson'))
    if content_digest(s['learner_profile'])!=p.learner_profile_digest:
        out.append(_issue(rule,'/','profile-mismatch','来源读者配置与冻结政策不一致。'))
    source=SourceContext.model_validate(s)
    if s['source_context_hash'] and s['source_context_hash']!=source.digest:
        out.append(_issue(rule,'/','source-digest','来源上下文冻结摘要不匹配。'))
    for key in ('unit_id','book_id','original_sha256','original_text','source_context_hash'):
        if st['c'].get(key) and st['c'][key] != (source.digest if key=='source_context_hash' else s[key]):
            out.append(_issue(rule,'/'+key,'identity-'+key,'候选与冻结来源的 '+key+' 不一致。'))
    for o in s['source_obligations']:
        if not o['source_excerpt'].strip() or o['source_excerpt'] not in s['source_text']:
            out.append(_issue(rule,'/','obligation-source-'+o['id'],'义务 '+o['id']+' 缺少真实来源片段。'))
    return out


def _coverage(st):
    out=[]; rule='CONTENT-COVERAGE'
    for o in st['s']['source_obligations']:
        claims=_linked_claims(st,o)
        if not claims:
            out.append(_issue(rule,'/teaching_plan/claims','missing-obligation-'+o['id'],'冻结来源义务 '+o['id']+' 未绑定 claim；仅为绑定提示，是否已讲由审核核对完整正文。',severity='warning',source_id=o['id'],owner_id=o['id'],evidence_kind='absence',parent_pointer='/teaching_plan/claims'))
        for claim in claims:
            if (claim['kind']!=o['kind'] or (o['conditions'] and not claim['conditions'])
                    or any(not value.strip() for value in claim['conditions']) or not claim['conclusion'].strip()):
                out.append(_issue(rule,'/teaching_plan/claims','changed-obligation-'+o['id'],'主张 '+claim['id']+' 未保留义务 '+o['id']+' 的类型，或缺少非空条件/结论字段。语义完整性还须 SOURCE-FIDELITY 核验。',owner_id=claim['id'],source_id=o['id']))
            evidence=_bound_evidence(st,claim['id'])
            if not evidence:
                out.append(_issue(rule,'/teaching_evidence','missing-reader-'+claim['id'],'主张 '+claim['id']+' 对应义务 '+o['id']+' 缺少读者证据绑定；不代表正文缺内容，须审核完整正文。',severity='warning',source_id=o['id'],owner_id=claim['id'],evidence_kind='absence',parent_pointer='/teaching_evidence'))
            if o['level']=='core' and not any(g['level']=='core' and claim['id'] in g['claim_refs'] for g in st['goals'].values()):
                out.append(_issue(rule,'/teaching_plan/goals','downgraded-core-'+o['id'],'核心义务 '+o['id']+' 缺少核心目标绑定；实际覆盖仍按冻结义务审核完整正文。',severity='warning',source_id=o['id'],owner_id=o['id']))
    for goal in st['goals'].values():
        if not any(e['goal_id']==goal['id'] and e['reader_pointer'] in st['reader'] for e in st['c']['teaching_evidence']):
            out.append(_issue(rule,'/teaching_evidence','unexplained-goal-'+goal['id'],'目标 '+goal['id']+' 缺少读者证据绑定；是否已讲须审核完整正文。',severity='warning',owner_id=goal['id']))
    return out


def _exercise_sources(st):
    from .exercise_regions import bind_exercise_regions, is_exercise_pointer
    out=[]; rule='CONTENT-EXERCISE-SOURCES'; bound=[]
    try:
        bound=bind_exercise_regions(st['c']['exercise_regions'], st['s'], stored=True)
        if bound!=st['c']['exercise_regions']:
            out.append(_issue(rule,'/exercise_regions','source-order','习题区必须按原文顺序保存。'))
    except ContractError as exc:
        out.append(_issue(rule,'/exercise_regions','source-binding',str(exc)))
    for i,row in enumerate(st['c']['teaching_evidence']):
        if is_exercise_pointer(row['reader_pointer']):
            out.append(_issue(rule,f'/teaching_evidence/{i}','exercise-teaching-evidence','原书习题资料不能充当教学证据或理解任务。'))
    lines=st['s']['source_text'].splitlines(keepends=True)
    offsets=[0]
    for line in lines: offsets.append(offsets[-1]+len(line))
    spans=[(offsets[row['source_lines'][0]-1],offsets[row['source_lines'][1]]) for row in bound]
    for path,row in _walk(st['c']):
        if is_exercise_pointer(path): continue
        for ref in row.get('source_refs',[]):
            if not isinstance(ref,dict): continue
            points_to_region=is_exercise_pointer(ref.get('json_pointer'))
            quote=ref.get('quote','')
            if quote and ref.get('kind') in ('textbook','source'):
                hits=[match.start() for match in re.finditer(re.escape(quote),st['s']['source_text'])]
                points_to_region=points_to_region or bool(hits and all(
                    any(first<=hit and hit+len(quote)<=last for first,last in spans) for hit in hits))
            if points_to_region:
                out.append(_issue(rule,path+'/source_refs','exercise-source-as-teaching','原书习题/答案来源不能转成待讲解的主张、例题、自测或数学任务。'))
    return out


def _references(st):
    c,s=st['c'],st['s']; out=[]; rule='CONTENT-REFERENCES'
    # 用户 2026-10-11 决定删除：逐字引文校验（source_reference_errors）。
    repairable_refs=set()
    concept_entries={x['id']:x for x in s['reference_catalog']['entries'] if x['kind']=='CONCEPT'}
    concepts=set(concept_entries)
    concept_sources={x['id']:x for x in s.get('concept_index',{}).get('concepts',[])
                     if isinstance(x,dict) and isinstance(x.get('id'),str)}
    obligations={x['id'] for x in s['source_obligations']}
    models={x['model_id'] for x in c['math_models']}
    source_examples={x['id']:x for x in s['source_examples']}
    if len(models)!=len(c['math_models']):
        out.append(_issue(rule,'/math_models','duplicate-model','数学模型 ID 必须唯一。'))
    relations_by_model={model['model_id']:{claim['claim_id'] for claim in model['claims']} for model in c['math_models']}
    for i,claim in enumerate(c['teaching_plan']['claims']):
        for j,model_id in enumerate(claim['math_model_refs']):
            if model_id in relations_by_model and claim['id'] not in relations_by_model[model_id]:
                out.append(_issue(rule,f'/teaching_plan/claims/{i}/math_model_refs/{j}',
                    'missing-math-claim-'+claim['id']+'-'+model_id,
                    '教学主张 '+claim['id']+' 引用模型 '+model_id+'，但该模型没有同 ID 的数学关系。',owner_id=claim['id']))
    for i,model in enumerate(c['math_models']):
        if not model['source_refs']:
            out.append(_issue(rule,f'/math_models/{i}/source_refs','missing-math-source','数学模型需要冻结来源 ID。'))
        for j,claim in enumerate(model['claims']):
            target=st['claims'].get(claim['claim_id'])
            if target is None or model['model_id'] not in target['math_model_refs']:
                out.append(_issue(rule,f'/math_models/{i}/claims/{j}','unknown-math-claim','数学主张 '+claim['claim_id']+' 须绑定引用该模型的教学主张。'))
    def concept_grounded(ref):
        identity=ref.get('concept_id') or ref.get('id')
        entry=concept_entries.get(identity)
        if not entry or not entry.get('markdown','').strip() or not entry.get('unit_id','').strip():
            return False
        if ref.get('id') and ref['id']!=identity: return False
        if ref.get('unit_id') and ref['unit_id']!=entry['unit_id']: return False
        if any(ref.get(key) for key in ('obligation_id','example_id','claim_id')): return False
        # A catalog identity is sufficient; optional file provenance must also
        # resolve to one frozen source record, never to the current unit by fiat.
        fields=('path','sha256','json_pointer','start','end','source_line')
        supplied={key:ref[key] for key in fields if ref.get(key) not in (None,'')}
        if supplied:
            sources=concept_sources.get(identity,{}).get('source_refs') or []
            if not isinstance(sources,list): return False
            if not any(isinstance(row,dict) and all(row.get(key)==value for key,value in supplied.items())
                       and (not row.get('unit_id') or row['unit_id']==entry['unit_id']) for row in sources):
                return False
        return True

    def grounded(ref,seen):
        # MathModelV1 deliberately uses stable string references, while reader
        # teaching objects use SourceRefV1 records. Both resolve frozen sources.
        if isinstance(ref,str):
            if ref in obligations: return True
            if ref in st['claims'] and ref not in seen:
                refs=st['claims'][ref]['source_refs']
                return bool(refs) and all(grounded(r,seen|{ref}) for r in refs)
            return False
        if ref['kind']=='concept': return concept_grounded(ref)
        if ref['kind']=='video_observation':
            fields=('kind','id','unit_id','path','json_pointer','sha256')
            if any(ref.get(k) for k in ('concept_id','example_id','claim_id','obligation_id','start','end','source_line')): return False
            return any(all(ref.get(k)==row.get('source_ref',{}).get(k) for k in fields)
                       for row in s.get('video_evidence',[]))
        if ref.get('obligation_id') and ref['obligation_id'] not in obligations: return False
        if ref.get('unit_id') and ref['unit_id']!=s['unit_id']: return False
        if ref.get('sha256') and ref['sha256']!=s['original_sha256']: return False
        if ref['kind'] in ('textbook','source'):
            if ref.get('example_id'):
                if ref['example_id'] not in source_examples: return False
            quote=ref.get('quote','')
            # A frozen source ID locates the original independently of author
            # wording. Fidelity belongs to review, not a verbatim-quote gate.
            return bool(ref.get('obligation_id') in obligations
                        or ref.get('example_id') in source_examples
                        or quote and quote in s['source_text'])
        if ref['kind'] in ('content','derived','approved_content'):
            ident=ref.get('claim_id','')
            if ident not in st['claims'] or ident in seen: return False
            refs=st['claims'][ident]['source_refs']
            return bool(refs) and all(grounded(r,seen|{ident}) for r in refs)
        return False
    for path,row in _walk(c):
        for field,known in [('claim_refs',set(st['claims'])),('concept_refs',concepts),('goal_refs',set(st['goals'])),('math_model_refs',models)]:
            for ident in row.get(field,[]):
                if ident not in known: out.append(_issue(rule,path+'/'+field,'unknown-'+ident,'不存在的 '+field+' 引用 '+ident))
        for index,ref in enumerate(row.get('source_refs',[])):
            ref_path=path+'/source_refs/'+str(index)
            if ref_path not in repairable_refs and not grounded(ref,{row.get('id','')}):
                out.append(_issue(rule,ref_path,'invalid-source:'+ref_path,'来源引用没有解析到冻结来源，或有循环依赖。'))
    for i,e in enumerate(c['teaching_evidence']):
        if e['goal_id'] not in st['goals']:
            out.append(_issue(rule,f'/teaching_evidence/{i}','invalid-reader-evidence','教学证据引用了不存在的目标。'))
        if not e['claim_refs']:
            out.append(_issue(rule,f'/teaching_evidence/{i}','unbound-reader-claims',
                '教学证据未绑定主张；仅作定位提示，实际教学覆盖须核对完整正文。',severity='warning'))
    # 用户 2026-10-11 决定删除：读者锚点合法性校验（reader_reference_errors）。
    for claim in st['claims'].values():
        if not claim['source_refs']: out.append(_issue(rule,'/teaching_plan/claims','ungrounded-'+claim['id'],'主张 '+claim['id']+' 缺少来源链。'))
    for ex in c['examples']:
        if not ex['source_refs']: out.append(_issue(rule,'/examples','example-source-'+ex['id'],'例题 '+ex['id']+' 缺少来源链。'))
        if ex['kind']=='source' and ex['id'] not in source_examples: out.append(_issue(rule,'/examples','false-source-'+ex['id'],'候选伪称原书例题 '+ex['id']))
    by_id={x['id']:x for x in c['examples']}
    for ident,original in source_examples.items():
        current=by_id.get(ident)
        if not current or current['kind']!='source' or any(current[k]!=original[k] for k in ('statement','label','book_solution','source_line')):
            out.append(_issue(rule,'/examples','changed-source-example-'+ident,'原书例题 '+ident+' 的题干、解答或来源身份缺失/被改变。',owner_id=ident))
    return out


def _prerequisites(st):
    out=[]; c=st['c']; assumed=set(st['p'].learner_profile['assumed_prerequisite_ids'])
    recaps={x['id']:x for x in c['prerequisite_recaps']}
    for i,p in enumerate(c['teaching_plan']['prerequisites']):
        valid=False
        if p['treatment']=='assumed': valid=p['dependency_id'] in assumed
        if p['treatment']=='recap': valid=p['recap_id'] in recaps and bool(recaps[p['recap_id']]['markdown'].strip())
        if p['treatment']=='new_here':
            dependency=p['dependency_id']
            claim_ids={dependency} if dependency in st['claims'] else set()
            for goal in st['goals'].values():
                if dependency in goal['concept_refs']: claim_ids.update(goal['claim_refs'])
            valid=any(_bound_evidence(st,cid) for cid in claim_ids)
            if not valid:
                out.append(_issue('CONTENT-PREREQUISITES',f'/teaching_plan/prerequisites/{i}',
                    'unbound-prerequisite-'+p['id'],'本节新讲的先修 '+dependency+' 缺少教学证据绑定；请核对完整正文中的实际解释，不能据此判正文未讲。',
                    owner_id=p['id'],severity='warning'))
            continue
        if not valid: out.append(_issue('CONTENT-PREREQUISITES',f'/teaching_plan/prerequisites/{i}','unsupported-prerequisite-'+p['id'],'先修 '+p['dependency_id']+' 的 '+p['treatment']+' 处理缺少冻结配置或读者解释。',owner_id=p['id']))
    return out


def _basis(st):
    out=[]; rule='CONTENT-BASIS'; c=st['c']
    known=set(st['claims']) | {e['id'] for e in st['s']['reference_catalog']['entries'] if e['kind']=='CONCEPT'}
    explanation_ids={x['id'] for x in c['explainables']+c['annotations']+c['prerequisite_recaps']+c['corrections']}
    for group in ('examples','explainables'):
        for i,row in enumerate(c[group]):
            previous=set()
            for j,step in enumerate(row['steps']):
                pointer=f'/{group}/{i}/steps/{j}'
                refs=step['basis_refs']; valid=True
                if not step['basis'].strip() or not refs:
                    out.append(_issue(rule,pointer,'unbound-step-basis-'+step['id'],
                        '步骤 '+step['id']+' 缺少单列依据或依据引用；须结合步骤正文和相关讲解判断实际依据是否完整，缺绑定不等于推导缺步。',
                        owner_id=step['id'],severity='warning'))
                for ref in refs:
                    allowed=ref in known or ref in previous or ref in explanation_ids-{row['id']}
                    if ref.startswith('/'):
                        allowed=ref in st['reader'] and not ref.startswith(f'/{group}/{i}/steps/')
                        if ref.startswith(f'/{group}/{i}/steps/'):
                            allowed=any(ref==f'/{group}/{i}/steps/{k}/markdown' or ref==f'/{group}/{i}/steps/{k}/basis' for k in range(j)) and ref in st['reader']
                    valid=valid and allowed
                if not valid: out.append(_issue(rule,pointer,'unresolved-step-basis-'+step['id'],'步骤 '+step['id']+' 的已有依据引用无法解析，或指向自身/后续步骤。',owner_id=step['id']))
                for ref in step['explanation_refs']:
                    if ref not in explanation_ids and ref not in st['reader']:
                        out.append(_issue(rule,pointer+'/explanation_refs','unknown-explanation-'+ref,'步骤解释引用无法解析：'+ref))
                previous.add(step['id'])
            for field,allowed in [('basis_refs',known|explanation_ids),('explanation_refs',explanation_ids)]:
                for ref in row.get(field,[]):
                    if ref not in allowed and ref not in st['reader']: out.append(_issue(rule,f'/{group}/{i}/{field}','unknown-'+ref,'引用无法解析：'+ref))
    return out


def _structure(st):
    c=st['c']; out=[]; rule='CONTENT-STRUCTURE'
    for field in ('reader_intro','summary'):
        if not c[field].strip(): out.append(_issue(rule,'/'+field,'missing-'+field,field+' 需要面向读者的内容。'))
    if not c['teaching_plan']['goals']: out.append(_issue(rule,'/teaching_plan/goals','missing-goals','需要可观察的学习目标。'))
    for group in ('examples',):
        for i,row in enumerate(c[group]):
            fields=('approach','steps','conclusion') if group=='examples' else ('goal_refs','source_refs','statement','hints','answer','misconception_feedback','test_cases')
            for field in fields:
                value=row[field]
                if not value or isinstance(value,str) and not value.strip(): out.append(_issue(rule,f'/{group}/{i}/{field}','missing-'+field,'缺少 '+field+'，需要可用的教学内容。',owner_id=row['id']))
    return out


def _learning_checks(st):
    from .learning_checks import validate_learning_checks
    return [_issue('CONTENT-LEARNING-CHECKS',r['pointer'],r.get('issue_key',r['code']),r['reason'],
            owner_id=r['check_id'] or r['pointer'], check_diagnostic=r,
            **{k:v for k,v in r.items() if k not in ('pointer','code','reason','check_id','issue_key')})
            for r in validate_learning_checks(st['c'],st['s'])]


def _capacity(st):
    size=len(json.dumps(st['c'],ensure_ascii=False).encode())
    return [_issue('CONTENT-CAPACITY','/','content_bytes',f'内容 {size} bytes 超过 {MAX_CONTENT_BYTES} bytes。')] if size>MAX_CONTENT_BYTES else []


def _plain(text):
    return re.sub(r'\$\$.*?\$\$|\$[^$]*\$|\\\(.*?\\\)|\\\[.*?\\\]','',text,flags=re.S)


def _style(st,rule):
    findings=[]
    for b in st['doc'].blocks:
        text=_plain(b['visible_text']); hits=[]
        if rule=='STYLE-SENTENCE': hits=[s for s in re.split(r'[。！？；\n]',text) if len(re.sub(r'\s','',s))>90]
        if rule=='STYLE-TEMPLATE': hits=[w for w in STYLE_WORDS if w in text]
        if rule=='STYLE-LABEL': hits=re.findall(r'(?:依据|理由|这一步在干什么)[:：]',text)
        if rule=='STYLE-PERSONIFICATION': hits=re.findall(r'[a-zA-Z][^。；，]{0,6}(?:管着|站在|负责|盯着|守着)',text)
        if hits: findings.append(_issue(rule,b['json_pointer'],'style', '编辑提醒：'+ '；'.join(hits[:3]),severity_level='warning',hits=hits))
    text='\n'.join(_plain(b['visible_text']) for b in st['doc'].blocks if b['group']!='references')
    if rule=='STYLE-METAPHOR':
        hits=re.findall(r'[刀削砍劈]',text)
        if len(hits)/max(1,len(text)/1000)>3: findings.append(_issue(rule,'/','metaphor-frequency','同组比喻每千字超过三次。',hits=hits))
    if rule=='STYLE-COVERAGE':
        size=len(re.sub(r'\s','',text)); threshold=max(250,round(len(_plain(st['s']['source_text']))*.6))
        if size<threshold: findings.append(_issue(rule,'/','coverage-length',f'读者内容 {size} 字，低于覆盖提醒阈值 {threshold}；这不是充分性判词。',measured=size,threshold=threshold))
    return findings


CHECKERS = {
    'CONTENT-SOURCE-READY':_source_ready,'CONTENT-COVERAGE':_coverage,'CONTENT-REFERENCES':_references,
    'CONTENT-PREREQUISITES':_prerequisites,'CONTENT-BASIS':_basis,'CONTENT-STRUCTURE':_structure,'CONTENT-CAPACITY':_capacity,
    'CONTENT-LEARNING-CHECKS':_learning_checks,
    'CONTENT-EXERCISE-SOURCES':_exercise_sources,
}


def _schema(st):
    # Keep an executable schema checker in the registry as well as preparatory
    # parsing: removing or replacing this executor cannot manufacture a receipt.
    parsed=parse_content(st['c'])
    if not isinstance(parsed,LessonContentV4): raise ContractError('production requires LessonContentV4')
    project_reader(parsed,FrozenReferenceCatalog.model_validate(st['s']['reference_catalog']))
    return []


CHECKERS['CONTENT-SCHEMA']=_schema
for _style_id in ('STYLE-SENTENCE','STYLE-COVERAGE','STYLE-TEMPLATE','STYLE-LABEL','STYLE-METAPHOR','STYLE-PERSONIFICATION'):
    CHECKERS[_style_id]=lambda st,rule=_style_id:_style(st,rule)


def check_content(content, source_context, policy):
    c,s=as_dict(content),as_dict(source_context); issues=[]; warnings=[]; results=[]; state=None
    try: state=_prepare(c,s,policy)
    except (ContractError,ValueError,TypeError,KeyError) as exc:
        issues.append(_issue('CONTENT-SCHEMA','/','schema',str(exc)))
    for rule in policy.rules:
        identity=rule['rule_id']; method=rule['check_method']; executed=False; findings=[]
        if method=='reviewer':
            applies=rule['applies_to']
            applicable=state is None or applies=='unit'
            if state is not None:
                if applies=='source_obligation': applicable=bool(state['s']['source_obligations'])
                elif applies=='exercise_region': applicable=bool(state['c']['exercise_regions'])
                elif applies=='goal': applicable=bool(state['goals'])
                elif applies in PEDAGOGY: applicable=any(c['kind']==applies for c in state['claims'].values())
            status='not_run' if applicable else 'not_applicable'
        elif identity not in CHECKERS:
            status='not_run'; findings=[_issue(identity,'/','checker-not-run','本地 checker 未注册，不能标为通过。',reason_code='not_run')]
        elif state is None:
            executed=identity=='CONTENT-SCHEMA'; status='fail' if executed else 'not_run'
        else:
            try:
                findings=CHECKERS[identity](state); executed=True
                status='fail' if any(f.get('severity')!='warning' for f in findings) else 'pass'
            except Exception as exc:
                status='unknown'; executed=True
                findings=[_issue(identity,'/','checker-error',f'检查器失败：{type(exc).__name__}: {exc}',reason_code='checker_error')]
        for finding in findings:
            finding['expected']=_failure_guidance(finding.get('expected'),_DEFAULT_FAILURE_EXPECTED,rule['pass_criteria'])
            finding['repair_hint']=_failure_guidance(finding.get('repair_hint'),_DEFAULT_REPAIR_HINT,rule['repair_guidance'])
            if rule['severity']=='warning' or finding.get('severity')=='warning':
                finding['severity']='warning'; warnings.append(finding)
            else: issues.append(finding)
        results.append(dict(rule_id=identity,version=rule['version'],method=method,check_method=method,
            required=rule['severity']=='blocking',status=status,executed=executed,findings=findings))
    return ContentCheckReport(schema_version='ContentCheckReportV2',input_hash=content_digest(c),policy_digest=policy.digest,
        source_digest=SourceContext.model_validate(s).digest if state else content_digest(s),
        reader_digest=state['doc'].digest if state else '',rule_results=results,blocking_issues=issues,warnings=warnings,
        decision='needs_repair' if issues else 'needs_review',coverage=dict(block_ids=state['doc'].required_block_ids if state else [],
        source_obligation_ids=[o['id'] for o in s.get('source_obligations',[])],rule_ids=[r['rule_id'] for r in results if r['executed']]))


def applicable_items(content, source_context, policy):
    c,s=as_dict(content),as_dict(source_context); st=_prepare(c,s,policy); items=[]
    def add(rule,subject):
        items.append(dict(criterion_id=rule['rule_id']+':'+subject,version=int(rule['version']),rule_id=rule['rule_id'],
            method='reviewer',required=rule['severity']=='blocking',applicable=True,subject_id=subject,
            expected=rule['pass_criteria'],repair_hint=rule['repair_guidance'],required_evidence_kinds=[],
            content_hash=content_digest(c),policy_digest=policy.digest))
    for rule in policy.rules:
        # R2-2（用户："机器没判过的模型也要判一次"）：**不再按 check_method 过滤**。
        # 原来只把 `method='reviewer'` 的 11 条发给模型，于是结构/覆盖/依据/自测完整性
        # 这些机器判据模型从不复核。现在 required 判据都发；warning（样式提醒）不发。
        if rule['severity']!='blocking': continue
        applies=rule['applies_to']
        if applies in ('content','unit'):
            # R2-2：整篇级判据（结构/覆盖/依据/自测完整性/引用完整性/容量/来源就绪/习题来源）
            # 以单元为主体发给模型复核**一次**。不这样做它们一条都发不出去 ——
            # 旧实现只发 method='reviewer'，而这些是 method='code'，模型从不复核它们。
            add(rule,'unit:'+c['unit_id'])
        elif applies=='source_obligation':
            for o in st['s']['source_obligations']:
                add(rule,o['id'])
        elif applies=='goal':
            for g in st['goals'].values(): add(rule,g['id'])
        elif applies=='exercise_region':
            for region in c.get('exercise_regions',[]): add(rule,region['id'])
        else:
            for claim in st['claims'].values():
                if claim['kind']==applies: add(rule,claim['id'])
    return tuple(items)




# Historical schema-2 helpers only; never production V4 approval criteria.
@dataclass(frozen=True)
class LegacyStyleRule:
    key: str
    what: str  # 一句人话：这条在防什么
    level: str = "soft"  # hard / soft
    words: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    limit: float | None = None
    unit: str = ""  # 阈值的单位（说明用）
    prompt: str = ""  # 提示词里怎么写这一条
    detail: str = ""  # 为什么有这条（实测来源）


# 篇幅地板的常量（`RULES` 里那句提示词要用它们拼 → 必须在前面）
FLOOR_ABS = 250
FLOOR_RATIO = 0.6


# ── 规范本体 ────────────────────────────────────────────────────────────────
LEGACY_STYLE_RULES: tuple[LegacyStyleRule, ...] = (
    LegacyStyleRule(
        key="套话",
        what="模板腔的套话（读起来就是 AI 写的）",
        level="soft",
        words=STYLE_WORDS,
        prompt="**套话黑名单（出现即不合格）**：综上所述、总而言之、一言以蔽之、"
        "值得注意的是、需要指出的是、众所周知、显而易见、不难发现、不难想到、"
        "毋庸置疑、某种意义上、由此可见、如前所述。",
        detail="用户 2026-09-24：「AI 味太浓」。这是最容易被读者一眼看出的那一类。",
    ),
    LegacyStyleRule(
        key="填空腔",
        what="栏目标签式的句子（「依据：…」这种）",
        level="soft",
        words=(
            "依据：",
            "依据:",
            "这一步在干什么：",
            "这一步在干什么:",
            "理由：",
            "理由:",
        ),
        prompt="**不要写成填空腔**：不许出现「依据：…」「理由：…」这种栏目标签式的句子，"
        "把原因**融进句子里**（✗「依据：等式两边同乘非零常数」→ "
        "✓「两边同乘一个不为零的数，等式照样成立」）。",
        detail="我们自己早期提示词带出来的毛病；页面上会变成「填空作业」的样子。",
    ),
    LegacyStyleRule(
        key="单句太长",
        what="一句话装了两三件事，读者要回头读",
        level="soft",
        limit=90,
        unit="字",
        prompt="**每段只讲一件事，句子短**：单句 90 字以内（理想 40 字以内）；"
        "连着两句不要用同一个句式开头。",
        detail="实测长句是最常见的「卡住读者」的原因；90 字是 v2 正文的硬线，"
        "体检里更严（60 字）只作提醒。",
    ),
    # ⚠️ 级别从 soft 升到 hard（2026-09-26）：这是用户点名的毛病（P-043「第一刀的 AI 味太浓」），
    # 判据是"同一套比喻的词频"，客观可数；只在提醒里挂着就永远没人改（实测：没人管）。
    LegacyStyleRule(
        key="比喻太满",
        what="同一套比喻在很短的一段里反复出现（像 AI 在凑字数）",
        level="soft",
        limit=3,
        unit="次/千字",
        patterns=(r"[刀削砍劈]",),
        prompt="**比喻不许用满**：一套比喻（比如「刀」「削」「砍」）在同一节里点到为止，"
        "不要每隔两句就来一次 —— 读者会先注意到比喻，而不是数学。",
        detail="用户 2026-09-25 第二批第 18 条：「第一刀的 AI 味太浓」（P-043）。",
    ),
    # 同理升为 hard：用户点名过（P-029「语言有些不通」），判据是句式，客观可数。
    LegacyStyleRule(
        key="拟人化",
        what="把数学对象说成有意志的人（「a 管着…」「b 站在…」）",
        level="soft",
        patterns=(
            r"[a-zA-Z\$][^。；，]{0,6}(管着|站在|负责|盯着|守着)",
            r"(管着|站在|负责|盯着|守着)[^。；，]{0,6}[a-zA-Z\$]",
        ),
        prompt="**数学对象不许拟人**：不要写「$a$ 管着变量前面的数」「$b$ 站在等号右边」"
        "这类说法，写成「$a$ 是 $x_{1}$ 的系数」这种人话。",
        detail="用户 2026-09-25 第二批第 3 条（P-029）：「语言有些不通」。",
    ),
    # 地板的**唯一实现**是下面的 `floor_of()`；这里的 limit 只是给人看的"绝对下限"。
    # 为什么单独立一个函数：这条公式原来在四个文件里各写了一遍
    # （validate / quality_report / style_lint / prompt），改一处不会带动其它三处 ——
    # 于是"同一节被两个工具判出相反结论"的历史反复发生（见问题清单 §18.5 的 A-4）。
    LegacyStyleRule(
        key="篇幅地板",
        what="讲得太短（说明推理和术语没补上）",
        level="hard",
        limit=250,
        unit="字（或原文×0.6，取大者）",
        # 提示词里那句由常量拼出来（不在字符串里手写 250 / 0.6 —— 那样公式一改这句就成假话）
        prompt=f"正文**至少** max({FLOOR_ABS} 字, 原文 × {FLOOR_RATIO:g})；"
        f"写长没关系，**没有上限**。",
        detail="用户 2026-09-25 定案：取消上限、只留地板（见问题清单第六节 6.3）。",
    ),
    LegacyStyleRule(
        key="段落数",
        what="段落太少（一大坨）或太多（碎成渣）",
        level="soft",
        limit=5,
        unit="段（v1 老产物 3~5 段）",
        prompt="按「一段一件事」分段；一段 2~4 句。",
        detail="P-004 的排版节奏；只对 v1（补充式）老产物生效，v2 由正文自然成段。",
    ),
)
