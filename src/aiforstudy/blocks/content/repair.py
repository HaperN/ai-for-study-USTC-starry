"""Host converts source-backed findings into stable tickets and repair hints."""
from copy import deepcopy
from dataclasses import replace
from .content_contracts import as_dict, content_digest, CLAIM_REPAIR_FIELDS
from ..audit.contracts import validate_ticket, make_ticket_id
from ..audit.evidence import locate_quote
from ...core.digest import digest_text
from ...errors import ContractError


def _subject_paths(c, subject, catalog):
    paths = {v['json_pointer'] for v in catalog.values()
             if v['kind']=='reader' and subject in v['subject_ids']
             and not v['json_pointer'].startswith('/reference_catalog/')}
    for i, claim in enumerate(c['teaching_plan']['claims']):
        if claim['id']==subject:
            paths.update(f'/teaching_plan/claims/{i}/{key}' for key in CLAIM_REPAIR_FIELDS)
    for i, model in enumerate(c['math_models']):
        if any(claim['claim_id']==subject for claim in model['claims']):
            paths.add(f'/math_models/{i}')
    return paths


def _missing_obligation(c, source, oid):
    obligation = next((o for o in source['source_obligations'] if o['id']==oid), None)
    if obligation is None:
        raise ContractError('absence ticket lacks independent source obligation')
    claims = c['teaching_plan']['claims']
    claim = next((k for k in claims if any(r.get('obligation_id')==oid for r in k['source_refs'])), None)
    cid = claim['id'] if claim else 'K-'+oid
    goal = next((g for g in c['teaching_plan']['goals'] if cid in g['claim_refs']), None)
    gid = goal['id'] if goal else 'G-'+oid
    additions = {}
    if not claim:
        additions['/teaching_plan/claims/-']=[{'id':cid,'kind':obligation['kind'],
            'source_refs':[{'kind':'source','obligation_id':oid,'quote':obligation['source_excerpt']}]}]
    if not goal:
        additions['/teaching_plan/goals/-']=[{'id':gid,'level':obligation['level'],'claim_refs':[cid]}]
    additions['/teaching_evidence/-']=[{'goal_id':gid,'claim_refs':[cid],
        'reader_pointer':'/body_markdown','purpose':purpose} for purpose in ('definition','understanding_check')]
    return additions, {'/body_markdown', *additions}


def _current_branch_diagnostic(content, source, row):
    """Recompute append candidates; persisted findings are never patch authority."""
    from .learning_checks import validate_learning_checks
    match = next((item for item in validate_learning_checks(content, source)
        if item.get('issue_key') == row.get('issue_key')
        and item.get('branch_id') == row.get('branch_id')
        and item['check_id'] == row.get('owner_id')
        and item['pointer'] == row.get('json_pointer')), None)
    if match is None:
        raise ContractError('missing current learning-check branch diagnostic')
    for field in ('branch_results','test_results','test_case_candidates','conditional_test_case'):
        if row.get(field) != match.get(field):
            raise ContractError('stale or forged learning-check branch diagnostic: '+field)
    return match


def finding_ticket(content, source_context, policy, row, catalog=None, *, deterministic=False):
    c, source = as_dict(content), as_dict(source_context)
    catalog = catalog or {}
    h, unit = content_digest(c), c['unit_id']
    rule = row['rule_id']
    definition = next((r for r in policy.rules if r['rule_id']==rule), None)
    if definition is None:
        raise ContractError('finding refers to unknown policy rule '+rule)
    subject = str(row.get('subject_id') or row.get('source_id') or row.get('owner_id') or row.get('json_pointer'))
    issue = row.get('criterion_id') or rule+':'+subject+':'+str(row.get('issue_key',''))
    paths, additions = set(), {}
    quote_diagnostic = None
    branch_diagnostic = None
    branch_codes = ('missing_branch_regression:', 'shadowed_misconception:', 'unreachable_misconception:')
    trigger_codes = ('misconception_matches_correct:', 'invalid_misconception_trigger:')
    trigger_diagnostic = None
    if deterministic and rule == 'CONTENT-LEARNING-CHECKS' and str(row.get('issue_key','')).startswith(branch_codes):
        branch_diagnostic = _current_branch_diagnostic(c, source, row)
    elif deterministic and rule == 'CONTENT-LEARNING-CHECKS' and str(row.get('issue_key','')).startswith(trigger_codes):
        trigger_diagnostic = _current_branch_diagnostic(c, source, row)
    missing_branch_case = branch_diagnostic is not None and branch_diagnostic['code'] == 'missing_branch_regression'
    # 引文诊断（source_quote_not_verbatim）已随逐字引文校验一起删除。
    source_quote = ''
    evidence_refs = row.get('evidence_refs', [])
    for ref in evidence_refs:
        entry = catalog[ref['evidence_id']]
        if entry['kind']=='source' and not source_quote:
            source_quote=entry['text']
        elif entry['kind']=='absence':
            new, targets = _missing_obligation(c, source, entry['source_id'])
            additions.update(new); paths.update(targets)
    if row.get('evidence_kind')=='absence':
        new, targets = _missing_obligation(c, source, row['source_id'])
        additions.update(new); paths.update(targets)
    if branch_diagnostic is not None:
        # Suggest a regression for the diagnosed check. Related feedback or
        # reader edits remain allowed and must pass full revalidation.
        index = next(i for i, check in enumerate(c['learning_checks']) if check['id']==branch_diagnostic['check_id'])
        path = f'/learning_checks/{index}/test_cases/-'
        additions = {path:deepcopy(branch_diagnostic['test_case_candidates']) if missing_branch_case
                     else [deepcopy(branch_diagnostic['conditional_test_case'])]}
        paths = {path}
        if not missing_branch_case:
            paths.add(branch_diagnostic['pointer']+'/inputs')
    elif trigger_diagnostic is not None:
        paths = {trigger_diagnostic['pointer']+'/inputs'}
    if rule=='MATH-REPRESENTATION':
        # Suggest grounded model IDs/objects for omitted calculations. These
        # examples guide repair; they do not restrict additional valid objects.
        for i, claim in enumerate(c['teaching_plan']['claims']):
            if subject not in ('unit:'+c['unit_id'],claim['id']):
                continue
            # The unit criterion inspects every current mathematical binding,
            # including models whose arithmetic passed but whose meaning did
            # not match the source. Suggest locations for the actual host claim IDs.
            paths.update(_subject_paths(c,claim['id'],catalog))
            if claim.get('math_model_refs'):
                continue
            mid='M-'+claim['id']
            additions.setdefault('/math_models/-',[]).append({'model_id':mid,'source_refs':[claim['id']],
                '$identity_allowlist':{'claim_id':[claim['id']],
                    'math_object_id':[mid+'-O'+str(j) for j in range(8)]}})
            paths.update({'/math_models/-',f'/teaching_plan/claims/{i}/math_model_refs'})
    if rule=='CONTENT-LEARNING-CHECKS' and row.get('issue_key')=='uncovered_core_goal':
        goal=next((g for g in c['teaching_plan']['goals'] if g['id']==row.get('owner_id')),None)
        if goal:
            additions.setdefault('/teaching_evidence/-',[]).append({'goal_id':goal['id'],
                'claim_refs':goal['claim_refs'],'reader_pointer':'/body_markdown','purpose':'understanding_check'})
            paths.update({'/body_markdown','/teaching_evidence/-'})
    if branch_diagnostic is not None or trigger_diagnostic is not None or quote_diagnostic is not None:
        pass
    elif rule != 'SOURCE-FIDELITY':
        paths.update(_subject_paths(c,subject,catalog))
    else:
        # The criterion is scoped to a source obligation, while editable claims
        # have different IDs. Suggest claims actually bound to that source.
        paths.update(entry['json_pointer'] for entry in catalog.values()
                     if entry['kind'] == 'reader' and subject in entry['subject_ids']
                     and not entry['json_pointer'].startswith('/reference_catalog/'))
        for index, claim in enumerate(c['teaching_plan']['claims']):
            if any(ref.get('obligation_id') == subject for ref in claim['source_refs']):
                paths.update(f'/teaching_plan/claims/{index}/{field}'
                             for field in CLAIM_REPAIR_FIELDS)
                paths.update(entry['json_pointer'] for entry in catalog.values()
                             if entry['kind'] == 'reader' and claim['id'] in entry['subject_ids']
                             and not entry['json_pointer'].startswith('/reference_catalog/'))
    changed_claim = next((claim for claim in c['teaching_plan']['claims']
                          if claim['id']==row.get('owner_id')),None)
    if (deterministic and rule=='CONTENT-COVERAGE' and changed_claim is not None
            and str(row.get('issue_key','')).startswith('changed-obligation-')):
        i=c['teaching_plan']['claims'].index(changed_claim)
        paths.update(f'/teaching_plan/claims/{i}/{key}'
                     for key in ('kind', *CLAIM_REPAIR_FIELDS))
    elif deterministic and row.get('json_pointer') and not additions and trigger_diagnostic is None:
        paths.add(row['json_pointer'])
    if not source_quote:
        source_quote = next((o['source_excerpt'] for o in source['source_obligations'] if o['id']==subject),source['source_text'])
    located = locate_quote(source_quote,source['source_text'],artifact_id='source:'+unit,
                           artifact_hash=digest_text(source['source_text']),json_pointer='/text')
    if located.status!='valid':
        located=locate_quote(source['source_text'],source['source_text'],artifact_id='source:'+unit,
                            artifact_hash=digest_text(source['source_text']),json_pointer='/text')
    if located.status!='valid':
        raise ContractError('finding lacks actionable current source/reader scope: '+issue)
    paths = {p for p in paths if not p.startswith('/reference_catalog/')}
    if not paths:
        paths.add('/body_markdown')  # A location hint cannot block a valid finding.
    problem = row.get('problem') or row.get('reason') or 'required criterion unresolved'
    action = row.get('repair_hint') or definition['repair_guidance']
    if isinstance(row.get('repair_instructions'), dict):
        action = row['repair_instructions'].get('action') or action
    expected = row.get('expected') or definition['pass_criteria']
    if branch_diagnostic is not None:
        from .content_policy import _failure_guidance
        action = _failure_guidance(branch_diagnostic['repair_hint'], '', definition['repair_guidance'])
        expected = _failure_guidance(branch_diagnostic['expected'], '', definition['pass_criteria'])
    data = dict(schema_version='2.0', ticket_id=make_ticket_id(rule=rule,unit_id=unit,
        artifact_id='lesson:'+unit,location='s:'+issue),unit_id=unit,kind='text_content',
        severity='P2' if definition['severity']=='warning' else 'P1',owner='lesson',status='ready',
        target={'artifact_id':'lesson:'+unit,'artifact_hash':h,'rule':rule,'step_id':issue,'anchor_kind':'step'},
        problem=problem,learner_obstacle='该内容尚不能支持冻结学习目标：'+problem,
        source_evidence=[s.as_dict() for s in located.spans],allowed_paths=sorted(paths),
        preserve=[],  # preserve 护栏已删除（用户决策）：工单不再携带冻结路径清单
        repair_instructions={'locate':', '.join(sorted(paths)),'action':action,'rationale':problem,'expected_change':expected},
        criteria=[{'criterion_id':issue,'version':int(row.get('version') or definition['version']),
                   'required':True,'expected':expected,'method':'rule' if deterministic else 'reviewer','evidence_type':'text'}],
        previous_attempt={'criterion_id':row.get('criterion_id'),'subject_id':subject,
            'issue_key':row.get('issue_key'),'deterministic':deterministic,
            'policy_digest':policy.digest,'allowed_additions':additions,'discovery':deepcopy(row)})
    return validate_ticket(data)


def reconcile_tickets(current, previous, verdicts, *, content_hash, executed_checks=()):
    """Retain frozen criteria; absence closes only a currently executed check.

    The host supplies rule:<rule_id> for successfully executed local checks
    (pass or fail, excluding unknown/not_run), and math:<model_id>:<claim_id>
    only for current mathematical passes. An empty set never proves execution.
    """
    executed_checks=set(executed_checks)
    old = {t.ticket_id:t for t in previous}
    fresh = {}
    for ticket in current:
        prior=old.get(ticket.ticket_id)
        if prior:
            ticket=replace(ticket,criteria=prior.criteria,severity=prior.severity,history=prior.history,
                previous_attempt={**(prior.previous_attempt or {}),**(ticket.previous_attempt or {}),
                                  'closure_reason':ticket.problem})
        fresh[ticket.ticket_id]=ticket
    closed=[]
    for ticket in previous:
        if ticket.ticket_id in fresh or not ticket.is_blocking:
            continue
        info=ticket.previous_attempt or {}
        cid=info.get('criterion_id')
        if ticket.target.get('rule')=='POST_MEDIA_CONTENT':
            required=[criterion for criterion in ticket.criteria if criterion.required]
            passed=bool(required) and all(any(
                v['criterion_id']==f'external:{ticket.ticket_id}:{criterion.criterion_id}'
                and v['status']=='pass' and v['version']==criterion.version for v in verdicts)
                for criterion in required)
        elif info.get('deterministic'):
            issue=str(info.get('issue_key') or '')
            identity=issue if issue.startswith('math:') else 'rule:'+ticket.target['rule']
            passed=identity in executed_checks
        else:
            passed=any(v['criterion_id']==cid and v['status']=='pass'
                       and v['version']==ticket.criteria[0].version for v in verdicts)
        if passed:
            closed.append(ticket.ticket_id)
        else:
            fresh[ticket.ticket_id]=replace(ticket,target={**ticket.target,'artifact_hash':content_hash},
                previous_attempt={**info,'closure_reason':'原冻结标准没有当前通过判词，不能关闭。'})
    return list(fresh.values()),closed
