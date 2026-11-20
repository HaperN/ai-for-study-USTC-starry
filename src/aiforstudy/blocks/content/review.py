"""Criterion-based review: a model may supply verdicts, never coverage authority.

The host freezes applicability and evidence identities before dispatch. Quotation
validation proves what was inspected; semantic correctness remains the named
reviewer's responsibility and is reported separately from mathematical checks.
"""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
import json

from .content_contracts import JsonRecord, as_dict, content_digest
from ...errors import ContractError


STATUSES = frozenset({"pass", "fail", "unknown", "not_applicable", "not_run"})


class ReviewReport(JsonRecord):
    """Immutable-by-copy host report retaining the actual individual verdicts."""


class ReviewProtocolError(ContractError):
    """Exact batch diagnostics survive the existing bounded reviewer ticket path."""
    def __init__(self, errors):
        self.errors = deepcopy(errors)
        super().__init__(json.dumps({'schema_version':'ContentReviewProtocolErrorsV1',
            'repair_scope':'review_response_only','errors':self.errors}, ensure_ascii=False))


def _row(value):
    return as_dict(value) if hasattr(value, "as_dict") else deepcopy(dict(value))


def build_evidence_catalog(content, reader_document, source_context, math_report=None):
    """Project exact current quotes and independent source obligations by ID.

    An author's absent claim cannot remove a source requirement. References to a
    claim which the author failed to define cannot manufacture reader coverage.
    """
    c, doc, context = _row(content), _row(reader_document), _row(source_context)
    h, unit = content_digest(c), "unit:" + c["unit_id"]
    claims = {r["id"]: r for r in c.get("teaching_plan", {}).get("claims", [])}
    goal_ids = {r['id'] for r in c.get('teaching_plan', {}).get('goals', [])}
    direct_sources = {cid: {ref["obligation_id"] for ref in row.get("source_refs", [])
                            if ref.get("obligation_id")}
                      for cid, row in claims.items()}
    claim_sources, claim_quotes = {}, {}

    def resolve(cid, visiting):
        if cid in visiting or cid not in claims:
            raise ContractError("unresolved/cyclic claim source dependency: " + cid)
        if cid in claim_sources:
            return claim_sources[cid], claim_quotes[cid]
        source_ids, quotes = set(direct_sources[cid]), set()
        for ref in claims[cid].get("source_refs", []):
            if ref.get("claim_id"):
                inherited, inherited_quotes = resolve(ref["claim_id"], visiting | {cid})
                source_ids.update(inherited)
                quotes.update(inherited_quotes)
            if ref.get("kind") in {"textbook", "source"} and ref.get("quote"):
                if ref["quote"] not in context["source_text"]:
                    # 用户决定（2026-10-11）：**引文逐字校验已删除**，不再是阻塞判据。
                    # 这里只做"这条引文能否当证据"的入目录筛选：不能在冻结原文里定位的引文
                    # 直接不进证据目录（后续判词会报缺证据，属可反馈的问题）。
                    # 原来抛 ContractError 会让**整个 text_audit 阶段**失败 →
                    # 媒体/审计/交互/渲染全部连锁阻塞，读者什么都拿不到
                    # （实测 run-20261012-083036）。
                    continue
                quotes.add(ref["quote"])
        claim_sources[cid], claim_quotes[cid] = source_ids, quotes
        return source_ids, quotes

    for cid in claims:
        resolve(cid, set())
    # An explicit visible anchor permits inspection of its whole structural
    # reader object. This is inspection scope only: `represented` below still
    # changes solely for the exact teaching_evidence pointer.
    from .reader_fields import _leaves, _registry_tree
    visible = {pointer for pointer, *_ in _leaves(c, _registry_tree())
               if not pointer.startswith('/exercise_regions/')}

    def owner(pointer):
        if pointer not in visible:
            return None
        parts = pointer.split('/')
        if len(parts) > 3 and isinstance(c.get(parts[1]), list):
            return '/' + '/'.join(parts[1:3]) + '/'
        return None

    owner_subjects = {}
    for entry in c.get('teaching_evidence', []):
        root = owner(entry.get('reader_pointer'))
        if root is None:
            continue
        subjects = owner_subjects.setdefault(root, set())
        subjects.update(set(entry.get('claim_refs', [])) & set(claims))
        if entry.get('goal_id') in goal_ids:
            subjects.add(entry['goal_id'])
        for cid in set(entry.get('claim_refs', [])) & set(claims):
            subjects.update(claim_sources[cid])
    catalog = {
        "source:" + unit: {"kind": "source", "content_hash": h,
                            "text": context["source_text"], "subject_ids": [unit]}
    }
    represented = set()
    for block in doc["blocks"]:
        pointer = block["json_pointer"]
        subjects = {unit}
        root = owner(pointer)
        if root is not None:
            subjects.update(owner_subjects.get(root, ()))
        if pointer.startswith('/exercise_regions/'):
            parts=pointer.split('/')
            try:
                subjects.add(c['exercise_regions'][int(parts[2])]['id'])
            except (KeyError, IndexError, ValueError) as exc:
                raise ContractError('exercise reader evidence has no current source region') from exc
        if pointer.startswith('/learning_checks/'):
            parts = pointer.split('/')
            try:
                index = int(parts[2])
                if index < 0 or str(index) != parts[2]: raise ValueError('noncanonical owner index')
                check = c['learning_checks'][index]
            except (KeyError, IndexError, ValueError) as exc:
                raise ContractError('learning-check reader evidence has no current owner') from exc
            # Current schema declares goals directly and claims via SourceRef.
            # This grants inspection scope, not teaching/source coverage.
            subjects.update(set(check.get('goal_refs', [])) & goal_ids)
            for ref in check.get('source_refs', []):
                cid = ref.get('claim_id')
                if ref.get('kind') in {'derived', 'content'} and cid in claims:
                    subjects.add(cid)
                    subjects.update(claim_sources[cid])
        for entry in c.get("teaching_evidence", []):
            if entry.get("reader_pointer") != pointer:
                continue
            # The link itself is not mathematical evidence; it only bounds what
            # the reviewer must inspect when judging this claim or learning goal.
            valid_claims = set(entry.get("claim_refs", [])) & set(claims)
            subjects.update(valid_claims)
            if entry.get("goal_id"):
                subjects.add(entry["goal_id"])
            for cid in valid_claims:
                subjects.update(claim_sources[cid])
                represented.update(direct_sources[cid])
        catalog["reader:" + block["block_id"]] = {
            "kind": "reader", "content_hash": h, "text": block["visible_text"],
            "subject_ids": sorted(subjects), "json_pointer": pointer,
            "block_id": block["block_id"],
        }
    source_text = context["source_text"]
    from .exercise_regions import bind_exercise_regions
    for region in bind_exercise_regions(c.get('exercise_regions',[]), context, stored=True):
        catalog['source:exercise:'+region['id']] = {
            'kind':'source', 'content_hash':h, 'text':region['raw_markdown'],
            'subject_ids':[unit,region['id']], 'source_id':region['id']}
    for cid, quotes in claim_quotes.items():
        goal_ids = {g["id"] for g in c.get("teaching_plan", {}).get("goals", [])
                    if cid in g.get("claim_refs", [])}
        for index, quote in enumerate(sorted(quotes)):
            catalog[f"source:claim:{cid}:{index}"] = {
                "kind": "source", "content_hash": h, "text": quote,
                "subject_ids": sorted({unit, cid} | goal_ids | claim_sources[cid]),
            }
    for obligation in context.get("source_obligations", []):
        oid, quote = obligation["id"], obligation["source_excerpt"]
        if not quote.strip() or quote not in source_text:
            raise ContractError("source obligation quote is absent from frozen source: " + oid)
        subject_claims = {cid for cid, refs in claim_sources.items() if oid in refs}
        goal_ids = {goal["id"] for goal in c.get("teaching_plan", {}).get("goals", [])
                    if set(goal.get("claim_refs", [])) & subject_claims}
        catalog["source:" + oid] = {
            "kind": "source", "content_hash": h, "text": quote,
            "subject_ids": sorted({unit, oid} | subject_claims | goal_ids),
            "source_id": oid,
        }
        if oid not in represented:
            catalog["absence:" + oid] = {
                "kind": "absence", "content_hash": h, "subject_ids": [oid],
                "source_id": oid,
                "parent_pointer": "/teaching_evidence" if subject_claims else "/teaching_plan/claims",
            }
    for row in (_row(math_report).get("checks", []) if math_report is not None else []):
        cid = row.get("claim_id")
        if cid not in claims:
            raise ContractError("mathematical check refers to an unknown content claim")
        if not row.get('relation_id'):
            raise ContractError('mathematical evidence lacks current relation identity')
        eid = "math:" + str(row.get("model_id", "")) + ":" + row['relation_id']
        if eid in catalog:
            raise ContractError("duplicate mathematical check evidence identity")
        catalog[eid] = {**deepcopy(row), "kind": "math_check", "content_hash": h,
                        "subject_ids": sorted({unit, cid} | claim_sources[cid])}
    return catalog


def validate_verdicts(items, response, evidence_catalog) -> ReviewReport:
    """Validate an exact batch of frozen criteria against current evidence.

    Missing/duplicate verdicts are protocol failures. Unknown or not-run checks
    remain explicit unresolved checks, rather than being silently omitted.
    """
    frozen = [_row(item) for item in items]
    if not frozen:
        raise ContractError("review requires host-applicable criteria")
    expected = {}
    for item in frozen:
        cid = item.get("criterion_id")
        if not isinstance(cid, str) or not cid or cid in expected:
            raise ContractError("missing/duplicate host criterion ID")
        if type(item.get("version")) is not int or item["version"] < 1:
            raise ContractError("host criterion requires positive integer version")
        if not item.get("content_hash") or not item.get("policy_digest"):
            raise ContractError("host criterion lacks current input/policy binding")
        expected[cid] = item
    hashes = {item["content_hash"] for item in frozen}
    policies = {item["policy_digest"] for item in frozen}
    if len(hashes) != 1 or len(policies) != 1:
        raise ContractError("review batch mixes content or policy versions")
    errors = []

    def issue(code, path, actual, item=None, *, ref=None, passing=None, **detail):
        error = {'code':code, 'path':path, 'actual':deepcopy(actual),
                 'criterion_id':item.get('criterion_id') if item else None,
                 'subject_id':item.get('subject_id') if item else None,
                 'passing_criterion':passing or 'Return the exact frozen criterion and valid current evidence; do not change content, applicability or verdict meaning.'}
        if ref is not None and item is not None:
            eligible = {eid:entry for eid,entry in evidence_catalog.items()
                        if entry.get('content_hash') == item['content_hash']}
            error['allowed_evidence_ids'] = list(eligible)
            quote = ref.get('quote')
            original = evidence_catalog.get(ref.get('evidence_id')) if isinstance(ref.get('evidence_id'), str) else None
            kind = original.get('kind') if original else None
            error['candidates'] = [dict(evidence_id=eid, quote=quote) for eid,entry in eligible.items()
                if entry.get('kind') in {'reader','source'} and (kind is None or entry['kind'] == kind)
                and isinstance(quote,str) and quote.strip() and quote in entry.get('text','')]
            eid = ref.get('evidence_id')
            if (code == 'invalid_evidence_quote' and not error['candidates']
                    and isinstance(eid,str) and eid in eligible
                    and kind in {'reader','source'} and eligible[eid].get('text','').strip()):
                # Offer the exact existing block, never a fuzzy rewrite. The
                # reviewer must still choose a relevant slice and judge meaning.
                error['candidates'] = [dict(evidence_id=eid, quote=eligible[eid]['text'])]
        error.update(detail)
        errors.append(error)

    if not isinstance(response, Mapping):
        issue('invalid_response', '', response)
        raise ReviewProtocolError(errors)
    if set(response) != {'verdicts'}:
        issue('invalid_response_fields', '', sorted(response))
    rows = response.get('verdicts')
    if not isinstance(rows, list):
        issue('invalid_verdicts', '/verdicts', rows)
        raise ReviewProtocolError(errors)
    if len(rows) != len(expected):
        issue('verdict_count', '/verdicts', len(rows), expected_count=len(expected))
    seen, validated = set(), []
    blocking, warnings = [], []
    allowed = {'criterion_id', 'version', 'status', 'reason', 'evidence_refs',
               'reason_code', 'repair_instructions'}
    for index, raw in enumerate(rows):
        path = f'/verdicts/{index}'
        if not isinstance(raw, dict):
            issue('invalid_verdict', path, raw)
            continue
        row = deepcopy(raw)
        cid = row.get('criterion_id')
        if not isinstance(cid,str) or cid not in expected:
            issue('unknown_criterion', path+'/criterion_id', cid,
                  allowed_criterion_ids=list(expected))
            continue
        item = expected[cid]
        if set(raw) - allowed:
            issue('untrusted_verdict_fields', path, sorted(set(raw)-allowed), item)
        if cid in seen:
            issue('duplicate_criterion', path+'/criterion_id', cid, item)
        seen.add(cid)
        if type(row.get('version')) is not int or row['version'] != item['version']:
            issue('changed_criterion_version', path+'/version', row.get('version'), item,
                  expected_version=item['version'])
        status = row.get('status')
        if not isinstance(status,str) or status not in STATUSES:
            issue('unknown_criterion_status', path+'/status', status, item)
        if not isinstance(row.get('reason'),str) or not row['reason'].strip():
            issue('missing_reason', path+'/reason', row.get('reason'), item)
        if status == 'not_applicable' and item.get('applicable',True):
            issue('changed_applicability', path+'/status', status, item)
        if item.get('applicable',True) is False and status != 'not_applicable':
            issue('changed_applicability', path+'/status', status, item)
        refs = row.get('evidence_refs')
        if not isinstance(refs,list):
            issue('invalid_evidence_refs', path+'/evidence_refs', refs, item)
            continue
        for ref_index, ref in enumerate(refs):
            ref_path = path+f'/evidence_refs/{ref_index}'
            if not isinstance(ref,dict):
                issue('invalid_evidence_reference', ref_path, ref, item)
                continue
            if set(ref) - {'evidence_id','quote'}:
                issue('untrusted_evidence_fields', ref_path, sorted(set(ref)-{'evidence_id','quote'}), item)
            eid = ref.get('evidence_id')
            if not isinstance(eid,str) or not eid:
                issue('invalid_evidence_id', ref_path+'/evidence_id', eid, item, ref=ref)
                continue
            if eid not in evidence_catalog:
                issue('unknown_evidence_id', ref_path+'/evidence_id', eid, item, ref=ref)
                continue
            entry = evidence_catalog[eid]
            kind = entry.get('kind')
            quote = ref.get('quote')
            if entry.get('content_hash') != item['content_hash']:
                issue('stale_evidence', ref_path+'/evidence_id', eid, item, ref=ref)
            # Subject links locate evidence; the reviewer judges its relevance.
            # Repeated optional references do not change the semantic verdict.
            if kind in {'reader','source'}:
                # The current evidence ID locates the frozen original. R8
                # removes verbatim matching; the review still owns fidelity.
                if 'quote' in ref and not isinstance(quote,str):
                    issue('invalid_evidence_quote', ref_path+'/quote', quote, item, ref=ref,
                          passing='Omit quote or provide a string; any excerpt or summary must retain the current evidence conditions and conclusion.')
            elif kind == 'absence':
                if not entry.get('parent_pointer') or not entry.get('source_id'):
                    issue('invalid_absence_evidence', ref_path+'/evidence_id', eid, item, ref=ref)
                # This catalog entry records an absent metadata binding, not
                # absent teaching text. The reviewer checks the full candidate.
            elif kind == 'math_check':
                if status == 'pass' and entry.get('status') == 'fail':
                    issue('math_evidence_cannot_pass', ref_path+'/evidence_id', eid, item, ref=ref)
            else:
                issue('unknown_evidence_kind', ref_path+'/evidence_id', eid, item, ref=ref)
        validated.append(row)
        if status not in ('pass','not_applicable'):
            finding = {**row, 'rule_id':item['rule_id'], 'subject_id':item['subject_id'],
                       'expected':item['expected'], 'repair_hint':item.get('repair_hint','')}
            (blocking if item.get('required',True) else warnings).append(finding)
    for cid in expected.keys()-seen:
        issue('missing_criterion', '/verdicts', None, expected[cid])
    if errors:
        raise ReviewProtocolError(errors)
    return ReviewReport(
        schema_version="ReviewReportV2", input_hash=next(iter(hashes)),
        policy_digest=next(iter(policies)), item_digest=content_digest({"items": frozen}),
        evidence_digest=content_digest(evidence_catalog),
        verdicts=validated, blocking_issues=blocking, warnings=warnings,
        decision="needs_review" if blocking else "accepted",
    )


def external_review_items(content, policy, tickets=()):
    """Additional frozen standards from persisted media discoveries.

    They add to the mandatory teaching policy; neither a model nor a media
    report can remove a base criterion or declare its own discovery resolved.
    """
    from ..audit.contracts import validate_ticket
    c = _row(content)
    result, seen = [], set()
    for raw in tickets:
        ticket = validate_ticket(_row(raw))
        if (ticket.target.get('rule') != 'POST_MEDIA_CONTENT'
                or ticket.owner != 'lesson' or ticket.kind != 'text_content'
                or ticket.unit_id != c['unit_id']
                or ticket.target['artifact_id'] != 'lesson:' + c['unit_id']):
            raise ContractError('invalid external content repair authority')
        if ticket.ticket_id in seen:
            raise ContractError('duplicate external content repair authority')
        seen.add(ticket.ticket_id)
        for criterion in ticket.criteria:
            if not criterion.required:
                continue
            result.append({'criterion_id': 'external:' + ticket.ticket_id + ':' + criterion.criterion_id,
                'version': criterion.version, 'rule_id': 'POST_MEDIA_CONTENT',
                'subject_id': 'unit:' + c['unit_id'], 'content_hash': content_digest(c),
                'policy_digest': policy.digest, 'required': True, 'applicable': True,
                'expected': criterion.expected, 'repair_hint': ticket.repair_instructions['action'],
                'required_evidence_kinds': [],
                'ticket_id': ticket.ticket_id})
    return tuple(result)


BALANCED_CRITERIA = (
    ('CONTENT-KNOWLEDGE', '对照本节实际教材范围保留主要知识、原例题及完整题设、必要条件、量词和结论，不漏讲或擅改；允许正确纠正原文识别及排版错误。知识、定义、公式和必要条件正确；含义、符号、适用条件及理解所需讲解清楚，不能只列公式；正文、批注、解释、例题、自测和图注同用此标准。直觉和类比须说明对象对应及适用范围，不能把特例说成一般情况；具体误区说明须保留正确条件和算式。按读者起点在首次依赖处补足真正必需的先修解释和当前用法，核对正文、解析、例题的顺序、术语与结论连贯一致。缺失实质阻碍学习时须定位并说明当前讲解为何不足及应补什么。定义和概念不要求额外证明；首次概念核心留正文，细解释及易错、直觉按独立补充与就地短链接组织。完整核对卡内内容、显示文字与目标的语义对应；错绑、必要内容藏入浮窗或补充入口缺失影响理解时，指出实际词句、对应对象、影响和修复要求。不能以正文已有为由免除补充组织，也不要求复制讲解或凑卡数。定理名称采用正式名或准确描述性名称，核对其来源 C-ID、条件与结论及当前引用适用性，不能仅按同名同编号合并；原编号留在来源资料供核对，不按教学标题中的编号或名称字数判错。额外类比及不影响理解的审美、格式偏好仅在reason标为建议。逐概念、定理、例题检查有明确教学收益的图片或动画是否已具体申请，缺口须指出实际位置、难点和可观察收益，不按数量或审美偏好判失败。'),
    ('CONTENT-STEPS', '定理以及需要推导的性质、推论、例题的必要步骤完整、每步有依据，并解释结论含义、方法选择理由及可迁移的方法回顾；实际展示推导链，不能仅报所用法则或说可验证，不循环使用待证结论。关键变化缺解释导致读者不能复现或区分易混对象时，指出具体缺口、当前文字为何不足及应补步骤。首次定理经独立 EXPLAIN 段完整内嵌的 what、why、steps/markdown 属于完整教学内容，条件、结论与直观解释可见，所有证明默认折叠、点击展开，须完整审核核心与证明，无需 body_markdown 重复；后续 CONCEPT 按名称引用前文定理，依据数学知识检查当前适用条件，不要求补交前文证明；当前例题仍须保留应有步骤。不按标题将性质豁免为概念，不以留给读者替代应讲推导，不要求为定义补证明；直觉和动画不能替代必要证明。按题目实际难点考虑具体错法、错因、正确区别或简短反例，不要求列举所有可能错法；认为易错支持确有缺口时须说明会混淆什么、现有讲解为何不足、补哪一句或哪个反例能解决。必要方法回顾保留主线；补充易错提醒用独立annotation并在相关步骤短链接，key_points或pitfalls可链接复用，不复制整段说明。'),
    ('CONTENT-SELF-CHECKS', '已有自测只考已教内容，题意、提示、答案和具体错因反馈一致且正确；自动评分题的评分输入与答案一致，开放自评题有可对照的解释且不假称自动判断；不要求每目标新增题。'),
)

BALANCED_REVIEW_ID = 'CONTENT-SUBSTANTIVE'


def _review_balanced(content, source_context, client, *, response_sink=None,
                     exchange_sink=None, repair_context=None, correction_journal=None,
                     author_media=None, available_theorem_names=None, available_theorem_explanations=None):
    """One complete-unit review, with only the answer envelope corrected locally."""
    from pathlib import Path
    from ...core.jsonio import read_json
    from .content_policy import load_content_policy
    from .generation_corrections import LocalCorrectionRequired, run_local_generation
    from .prompt import build_content_messages

    c, context = _row(content), _row(source_context)
    policy = load_content_policy(context.get('learner_profile'))
    criteria = [dict(criterion_id=key, version=1, rule_id=key,
        subject_id='unit:'+context['unit_id'], expected=expected, required=True,
        applicable=True) for key, expected in BALANCED_CRITERIA]
    request = {'material_notes':{
                   # 修改计划 B2：教材原文片段与卡片现值的区别写进审核材料本身。
                   'source_excerpt':'教材原文，可能含 OCR 旧字，不是卡片现值；卡片以 concept_index 当前值为准。',
                   'ticket_responses':'candidate_content.ticket_responses 是作者对上一轮工单的说明，须逐条回应。'},
               'source_context':context, 'candidate_content':c, 'criteria':criteria,
               'author_media':deepcopy(author_media),
               'repair_context':deepcopy(repair_context or {})}
    if available_theorem_names is not None:
        request['available_theorem_names'] = deepcopy(available_theorem_names)
    # Retain only for replay of requests frozen before names-only inputs.
    if available_theorem_explanations is not None:
        request['available_theorem_explanations'] = deepcopy(available_theorem_explanations)
    messages = build_content_messages('reviewer', request, policy)
    responses, exchanges, valid, warnings = [], [], [], []

    def record(raw, *, fresh=True):
        responses.append(deepcopy(raw))
        exchange = {'schema_version':'ContentReviewExchangeV1',
            'binding':{'content_hash':content_digest(c),
                'source_context_hash':context.get('source_context_hash') or content_digest(context),
                'policy_digest':policy.digest, 'batch_index':0},
            'criteria':deepcopy(criteria), 'response':deepcopy(raw)}
        exchanges.append(exchange)
        if fresh and response_sink is not None:
            response_sink(deepcopy(raw))
        if fresh and exchange_sink is not None:
            exchange_sink(deepcopy(exchange))

    if correction_journal is not None and Path(correction_journal).is_file():
        for attempt in read_json(correction_journal).get('attempts', []):
            if 'response' in attempt:
                record(attempt['response'], fresh=False)

    def readable(row):
        return (isinstance(row, dict) and isinstance(row.get('status'), str) and row['status'] in STATUSES
                and isinstance(row.get('reason'), str) and bool(row['reason'].strip()))

    # 修改计划 A6：第一份可读答复的判词是基准。格式纠正只补被指出的坏行；
    # 改变已可读判词的状态或新增判词记为 format_correction_drift 留证，不生效。
    baseline, drift = [], []

    def validate(response):
        errors = []
        if isinstance(response, list) and response and all(readable(row) for row in response):
            response = {'verdicts': response}      # 裸判词数组：本地包装，不调模型
        if not isinstance(response, dict) or not isinstance(response.get('verdicts'), list):
            errors.append({'path':'/verdicts', 'code':'review_format',
                'fix':'返回 JSON 对象，其中 verdicts 为判词数组；不要改正文。',
                'passing_criterion':'可读取的 verdicts 数组；无法判断用 unknown。'})
        else:
            rows = response['verdicts']
            first = not baseline
            pending = set(range(len(rows))) if first else {i for i, row in enumerate(baseline) if row is None}
            if first:
                baseline.extend([None] * len(rows))
            for index, row in enumerate(rows):
                if index in pending:
                    if readable(row):
                        baseline[index] = deepcopy(row)
                    continue
                if not readable(row):
                    continue                        # 纠正时弄坏的已读判词不影响基准
                if index >= len(baseline) or baseline[index] is False:
                    drift.append({'index':index, 'kind':'added', 'after':deepcopy(row)})
                elif row['status'] != baseline[index]['status']:
                    drift.append({'index':index, 'kind':'status_changed',
                                  'before':deepcopy(baseline[index]), 'after':deepcopy(row)})
            for index in sorted(i for i in pending if baseline[i] is None and i >= len(rows)):
                baseline[index] = False             # 纠正答复删去了原坏行：它本无可读判词，视为撤回
            for index in sorted(i for i in pending if baseline[i] is None):
                errors.append({'path':f'/verdicts/{index}', 'code':'review_format',
                    'fix':'保留原有实质判断，填写 status 与 reason；无法判断用 unknown，不修改正文。',
                    'passing_criterion':'status 为 pass/fail/unknown/not_applicable/not_run，reason 为非空文本。'})
        if errors:
            raise LocalCorrectionRequired('正文审核答复格式无法读取', errors=errors,
                                          allowed_paths=['/verdicts'], kind='fields')
        return response

    failure = {}
    try:
        run_local_generation(messages, client, validate,
            tag=f"content-review:{context['unit_id']}:0",
            correction_tag=f"content-review-correction:{context['unit_id']}",
            response_sink=record, correction_journal=correction_journal,
            max_corrections=2, correction_guard=False)
    except ContractError as exc:
        # Only exhausted local syntax/answer corrections become an unverified
        # review. Transport, journal and file failures retain their real errors.
        if not isinstance(exc.__cause__, LocalCorrectionRequired):
            raise
        failure = {'kind':'protocol', 'reason':str(exc)}
    # IDs and optional evidence are not admission rules; repeated rows collapse.
    for row in baseline:
        if row and row not in valid:
            valid.append(row)
    if drift:
        warnings.append(f'格式纠正改变或新增了 {len(drift)} 条判词（格式纠正漂移），已留证但不生效。')
    verdicts = valid or [{'status':'unknown', 'reason':'本次未获得可用的实质审核判词。'}]
    failures = [row for row in verdicts if row['status'] == 'fail']
    accepted = not failure and all(row['status'] in ('pass', 'not_applicable') for row in verdicts)
    return ReviewReport({'schema_version':'ReviewReportV2', 'review_mode':'balanced',
        'review_items':criteria, 'verdicts':verdicts, 'blocking_issues':failures,
        'warnings':list(dict.fromkeys(warnings)), 'responses':responses, 'exchanges':exchanges,
        'evidence_catalog':{}, 'review_failure':failure, 'format_correction_drift':drift,
        'decision':'accepted' if accepted else 'needs_review'})


def review_content(content, reader_document, source_context, items, math_report, client,
                   *, response_sink=None, exchange_sink=None, repair_context=None,
                   correction_journal=None, author_media=None, available_theorem_names=None,
                   available_theorem_explanations=None):
    """Dispatch bounded criterion batches through the shared production prompt.

    Evidence remains complete within each batch. A capacity error is explicit;
    neither text nor criteria are truncated to squeeze a request through.
    """
    from ...core.review_mode import substantive_reviews_enabled
    if substantive_reviews_enabled():
        return _review_balanced(content, source_context, client, response_sink=response_sink,
            exchange_sink=exchange_sink, repair_context=repair_context,
            correction_journal=correction_journal, author_media=author_media,
            available_theorem_names=available_theorem_names,
            available_theorem_explanations=available_theorem_explanations)
    from .content_policy import load_content_policy
    from .prompt import build_content_messages
    from .math_checks import math_review_items
    from .review_aliases import (EVIDENCE_ENCODING, EVIDENCE_ID_CONTRACT,
        build_evidence_aliases, decode_evidence_response, map_protocol_errors,
        project_repair_context)

    context = _row(source_context)
    policy = load_content_policy(context["learner_profile"])
    frozen = [_row(item) for item in items] + list(math_review_items(content, policy, math_report))
    catalog = build_evidence_catalog(content, reader_document, context, math_report)
    aliases = build_evidence_aliases(catalog)
    responses, verdicts, exchanges, protocol_errors = [], [], [], []
    try:
        for offset in range(0, len(frozen), 12):
            batch = frozen[offset:offset + 12]
            if any(item["policy_digest"] != policy.digest for item in batch):
                raise ContractError("review items do not match the frozen teaching policy")
            relevant = catalog
            batch_aliases = {alias:eid for alias,eid in aliases.items() if eid in relevant}
            model_catalog = {alias:deepcopy(relevant[eid]) for alias,eid in batch_aliases.items()}
            request = {"source_context": context, "criteria": batch,
                       "candidate_content": _row(content),
                       "evidence_catalog": model_catalog,
                       "evidence_encoding": EVIDENCE_ENCODING,
                       "evidence_id_contract": EVIDENCE_ID_CONTRACT,
                       "criterion_evidence_ids": {item['criterion_id']:[eid for eid,entry in model_catalog.items()
                           if entry['content_hash']==item['content_hash']]
                           for item in batch},
                       "repair_context": project_repair_context(repair_context or {},
                           {eid:alias for alias,eid in batch_aliases.items()}),
                       "teaching_plan": _row(content).get("teaching_plan", {}),
                       "math_models": _row(content).get("math_models", []),
                       "learning_checks": _row(content).get("learning_checks", []),
                       "math_report": _row(math_report) if math_report is not None else None}
            if available_theorem_names is not None:
                request['available_theorem_names'] = deepcopy(available_theorem_names)
            if available_theorem_explanations is not None:
                request['available_theorem_explanations'] = deepcopy(available_theorem_explanations)
            messages = build_content_messages("reviewer", request, policy)
            response = client.chat_json(messages, tag=f"content-review:{context['unit_id']}:{offset // 12}").get("data")
            if response_sink is not None:
                response_sink(deepcopy(response))
            exchange = {'schema_version':'ContentReviewExchangeV1',
                'binding':{'encoding':EVIDENCE_ENCODING, 'content_hash':content_digest(content),
                    'source_context_hash':context.get('source_context_hash') or content_digest(context),
                    'policy_digest':policy.digest, 'items_digest':content_digest({'items':batch}),
                    'catalog_digest':content_digest(relevant), 'batch_index':offset // 12},
                'criteria':deepcopy(batch), 'evidence_catalog':deepcopy(relevant),
                'evidence_aliases':deepcopy(batch_aliases), 'response':deepcopy(response)}
            exchanges.append(exchange)
            if exchange_sink is not None:
                exchange_sink(deepcopy(exchange))
            try:
                validate_verdicts(batch, response, model_catalog)
            except ReviewProtocolError as exc:
                # Durable findings use canonical IDs, never stale aliases from a
                # different request. The next prompt projects candidates afresh.
                protocol_errors.extend(map_protocol_errors(exc.errors, batch_aliases))
                continue
            canonical = decode_evidence_response(response, batch_aliases)
            checked = validate_verdicts(batch, canonical, relevant)
            responses.append(deepcopy(response))
            verdicts.extend(checked["verdicts"])
        if protocol_errors:
            raise ReviewProtocolError(protocol_errors)
        final = validate_verdicts(frozen, {"verdicts": verdicts}, catalog).as_dict()
        final.update(review_items=frozen, evidence_catalog=catalog, responses=responses, exchanges=exchanges)
        return ReviewReport(final)
    except Exception as exc:
        from ...pipeline.services import reraise_control_error
        reraise_control_error(exc)
        from .review_partial import build_partial_review_report
        exc.partial_report = build_partial_review_report(
            content, context, frozen, catalog, exchanges, exc,
            decode_response=decode_evidence_response)
        raise
