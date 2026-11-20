"""Narrow author corrections for dependencies of already selected claims.

Diagnostics grant no authority over intent, objects or mathematical definitions.
The ordinary projection and mathematical validators still decide acceptance.
"""
from copy import deepcopy
from typing import get_args


def _ids(value):
    return (isinstance(value, list)
            and all(isinstance(item, str) and item.strip() for item in value)
            and len(value) == len(set(value)))


def _index(rows, key):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return None
    ids = [row.get(key) for row in rows]
    return dict(zip(ids, rows)) if _ids(ids) else None


def author_dependency_errors(envelope):
    """Suggest only missing, existing model IDs in deterministic source order.

    Malformed or unknown identities belong to strict validation, not inference.
    Preserve the author's current list order and append its selected claims'
    missing dependencies; never remove a claim to make its dependencies vanish.
    """
    if not isinstance(envelope, dict):
        return []
    content, media = envelope.get('content'), envelope.get('media_requirements')
    if not isinstance(content, dict) or not isinstance(media, dict):
        return []
    plan, requests = content.get('teaching_plan'), media.get('requests')
    if not isinstance(plan, dict) or not isinstance(requests, list):
        return []
    models, claims = _index(content.get('math_models'), 'model_id'), _index(plan.get('claims'), 'id')
    if models is None or claims is None:
        return []
    errors = []
    for i, request in enumerate(requests):
        if not isinstance(request, dict):
            continue
        selected, declared = request.get('claim_ids'), request.get('math_model_ids')
        if not _ids(selected) or not _ids(declared) or not set(selected) <= claims.keys() or not set(declared) <= models.keys():
            continue
        dependencies = [claims[cid].get('math_model_refs') for cid in selected]
        if any(not _ids(refs) or not set(refs) <= models.keys() for refs in dependencies):
            continue
        required = set().union(*map(set, dependencies))
        if not set(declared) <= required:
            continue  # Unrelated existing models also need strict validation.
        missing = required - set(declared)
        if not missing:
            continue
        candidate = [*declared, *(mid for mid in models if mid in missing)]
        errors.append(dict(path=f'/media_requirements/requests/{i}/math_model_ids',
            code='missing_claim_math_models', value=deepcopy(declared), candidates=[candidate],
            fix='只在 math_model_ids 原列表末尾追加候选中已绑定主张依赖的现有模型；保持原顺序、claim_ids、object_refs、媒体意图和验收标准。',
            passing_criterion='math_model_ids 精确等于唯一候选，覆盖已绑定主张的全部模型；其余字段不变，最终仍须通过原数学与媒体校验。'))
    return errors


def author_grammar_errors(envelope):
    """Offer exact grammar candidates without choosing a new identity or intent.

    Duplicate references retain their valid set and first-occurrence order. A
    mistyped placement identity is recoverable only when the unchanged visible
    pointer, selected goal's reader evidence and exact anchor agree. Conflicting
    existing owners, unknown references and ambiguous anchors remain strict.
    """
    from ..content.content_contracts import AuthorMediaEvidence
    from .media_requirements import visible_fields
    from .author_requirements import _placement
    from ...errors import ContractError

    if not isinstance(envelope, dict): return []
    content, media = envelope.get('content'), envelope.get('media_requirements')
    if not isinstance(content, dict) or not isinstance(media, dict): return []
    plan, requests = content.get('teaching_plan'), media.get('requests')
    if not isinstance(plan, dict) or not isinstance(requests, list): return []
    goals, claims = _index(plan.get('goals'), 'id'), _index(plan.get('claims'), 'id')
    models, request_index = _index(content.get('math_models'), 'model_id'), _index(requests, 'request_key')
    if any(index is None for index in (goals, claims, models, request_index)): return []
    errors = []

    def candidate(path, value, proposed, code, fix):
        if value != proposed:
            errors.append(dict(path=path, code=code, value=deepcopy(value), candidates=[deepcopy(proposed)],
                fix=fix+' 仅将该叶字段设为唯一候选；其他请求、身份、落点、媒体意图、数学对象及验收标准不变。',
                passing_criterion='值精确等于宿主候选且其余字段不变；完整响应仍须通过原有严格投影、来源和数学校验。'))

    def deduplicate(values, allowed, path):
        if (not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values)
                or not set(values) <= set(allowed)): return
        candidate(path, values, list(dict.fromkeys(values)), 'duplicate_media_references',
                  '只去除重复引用，保留全部现有有效引用及其首次出现的顺序，不得删实际依赖或调整次序。')

    try: fields = visible_fields(content)
    except (KeyError, TypeError, ValueError): fields = []
    fields = [row for row in fields if isinstance(row.get('owner_id'), str)
              and isinstance(row.get('step_id', ''), str)]
    owners = {row['owner_id'] for row in fields}
    steps = {row['step_id'] for row in fields if row.get('step_id')}
    evidence = content.get('teaching_evidence')
    evidence = evidence if isinstance(evidence, list) else []
    for i, request in enumerate(requests):
        base = f'/media_requirements/requests/{i}'
        for key, allowed in (('goal_refs', goals), ('claim_ids', claims), ('math_model_ids', models)):
            deduplicate(request.get(key), allowed, base+'/'+key)
        criteria = request.get('acceptance_criteria')
        for j, criterion in enumerate(criteria if isinstance(criteria, list) else []):
            if isinstance(criterion, dict):
                deduplicate(criterion.get('evidence_required'), get_args(AuthorMediaEvidence),
                            f'{base}/acceptance_criteria/{j}/evidence_required')
        refs, declared = request.get('object_refs'), request.get('math_model_ids')
        if (isinstance(refs, list) and isinstance(declared, list)
                and all(isinstance(mid, str) and mid in models for mid in declared)):
            valid = True
            for ref in refs:
                if not isinstance(ref, dict) or set(ref) != {'model_id', 'object_id'}:
                    valid = False; break
                mid, oid = ref['model_id'], ref['object_id']
                if not isinstance(mid, str) or mid not in declared or not isinstance(oid, str):
                    valid = False; break
                objects = _index(models[mid].get('objects'), 'math_object_id')
                if objects is None or oid not in objects:
                    valid = False; break
            if valid:
                unique = []
                for ref in refs:
                    if ref not in unique: unique.append(ref)
                candidate(base+'/object_refs', refs, unique, 'duplicate_media_references',
                          '仅去除完全相同的对象引用，保留所有模型和对象的绑定及首次出现顺序。')
        placement, selected = request.get('placement'), request.get('goal_refs')
        if (not isinstance(placement, dict) or not isinstance(selected, list)
                or any(not isinstance(gid, str) or gid not in goals for gid in selected)): continue
        rows = [row for row in fields if row['pointer'] == placement.get('target_pointer')]
        if len(rows) != 1: continue
        row = rows[0]
        if not any(isinstance(e, dict) and e.get('goal_id') in selected
                   and e.get('reader_pointer') == row['pointer'] for e in evidence): continue
        wanted = {'owner_id':row['owner_id'], 'step_id':row.get('step_id', '')}
        changed = {key:value for key,value in wanted.items() if placement.get(key, '') != value}
        if not changed or any(key not in placement or not isinstance(placement[key], str)
                              for key in changed): continue
        if any(placement[key] in (owners if key == 'owner_id' else steps) for key in changed): continue
        try: _placement(content, {**placement, **changed})
        except (ContractError, KeyError, TypeError, ValueError): continue
        for key, value in changed.items():
            candidate(base+'/placement/'+key, placement[key], value, 'media_placement_identity',
                      '保持原可见字段指针、精确锚句和目标读者证据；仅修正该字段所属对象的误写身份。')
    decisions = media.get('decisions')
    for i, decision in enumerate(decisions if isinstance(decisions, list) else []):
        if (isinstance(decision, dict) and isinstance(decision.get('target_id'), str)
                and decision['target_id'] in goals and decision.get('goal_id') == decision['target_id']):
            deduplicate(decision.get('request_ids'), request_index,
                        f'/media_requirements/decisions/{i}/request_ids')
    return errors


def author_decision_errors(envelope):
    """Diagnose missing goals without choosing the author's media decisions."""
    if not isinstance(envelope, dict): return []
    content, media = envelope.get('content'), envelope.get('media_requirements')
    if not isinstance(content, dict) or not isinstance(media, dict): return []
    plan = content.get('teaching_plan')
    if not isinstance(plan, dict): return []
    goals, claims = _index(plan.get('goals'), 'id'), _index(plan.get('claims'), 'id')
    decisions, requests = media.get('decisions', []), media.get('requests', [])
    decided, requested = _index(decisions, 'target_id'), _index(requests, 'request_key')
    if any(index is None for index in (goals, claims, decided, requested)): return []
    if (not set(decided) <= set(goals) or any(d.get('goal_id') != gid for gid,d in decided.items())
            or any(not _ids(g.get('claim_refs')) or not set(g['claim_refs']) <= set(claims) for g in goals.values())):
        return []
    missing = [dict(goal_id=gid, target_id=gid, claim_ids=deepcopy(goal['claim_refs']),
                    required=isinstance(goal.get('visual_requirement'),dict)
                             and goal['visual_requirement'].get('required') is True)
               for gid,goal in goals.items() if gid not in decided]
    if not missing: return []
    common = dict(goal_candidates=missing,
        fix='补齐列出的缺失 goal 决策，由作者根据目标选择 requirement、理由和 request_ids；宿主不默认 none。可同步修改关联决策或新增请求，既有身份和来源真切片须保持。每条请求必须服务其声明目标，claim_ids 对应目标主张，decision/request 双向映射完整；全部数学、落点与媒体意图仍须复验。',
        passing_criterion='所有目标各有一个明确有效 decision；既有身份与来源不变，完整 schema、严格投影与语义复验通过。')
    return [dict(common, path='/media_requirements/decisions', code='missing_media_decisions', value=deepcopy(decisions)),
            dict(common, path='/media_requirements/requests', code='missing_goal_media_requests', value=deepcopy(requests))]
