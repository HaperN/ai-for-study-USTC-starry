"""Bounded, declarative local checks; student feedback is not a repair ticket.

No generated program, expression evaluator or natural-language proof grader is
accepted. Regression cases remain host-only and never enter reader projection.
"""
from __future__ import annotations
import math
import re
from copy import deepcopy
from functools import lru_cache
from .content_contracts import LearningCheckV1, as_dict, parse_content, content_digest

_NUMBER = re.compile(r'^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$')


def _normalize(check, value):
    kind = check.kind
    if kind == 'boolean':
        return (type(value) is bool, value)
    if kind == 'numeric':
        if type(value) in (int, float):
            try:
                return (math.isfinite(value), value)
            except OverflowError:
                return (False, None)
        if type(value) is not str or not _NUMBER.fullmatch(value.strip()):
            return (False, None)
        number = float(value.strip())
        return (math.isfinite(number), number)
    if type(value) is not str or not value.strip():
        return (False, None)
    if kind == 'choice':
        return (value in {x.id for x in check.input_spec.options}, value)
    if check.scoring.normalization != 'exact':
        value = value.strip()
    if check.scoring.normalization == 'casefold':
        value = value.casefold()
    return (True, value)


def _matches(check, value, expected, *, tolerance=False):
    valid, normalized = _normalize(check, expected)
    if not valid:
        return False
    if check.kind == 'numeric' and tolerance:
        limit = max(check.scoring.absolute_tolerance, check.scoring.relative_tolerance * abs(normalized))
        return abs(value - normalized) <= limit
    if check.kind == 'numeric':
        return value == normalized
    return type(value) is type(normalized) and value == normalized


def grade_learning_check(check: LearningCheckV1 | dict, value) -> dict:
    """Returns status/feedback_id/feedback only, never a solution or private case."""
    check = LearningCheckV1.model_validate(as_dict(check))
    valid, normalized = _normalize(check, value)
    if not valid:
        return dict(status='invalid', feedback_id='', feedback='请先输入有效答案。')
    if check.kind == 'self_explanation':
        return dict(status='manual', feedback_id='', feedback='请对照解法核对自己的理由；此题由你自评。')
    if any(_matches(check, normalized, expected, tolerance=True) for expected in check.scoring.accepted_inputs):
        return dict(status='correct', feedback_id='', feedback='回答正确。请再核对使用条件。')
    for misconception in check.misconception_feedback:
        if any(_matches(check, normalized, expected) for expected in misconception.inputs):
            return dict(status='incorrect', feedback_id=misconception.id, feedback=misconception.feedback)
    return dict(status='incorrect', feedback_id='', feedback='结果尚未匹配。检查条件与关键一步，可逐级查看提示。')


def _branch_test_id(check, branch, **identity_parts):
    base = 'T-BRANCH-' + content_digest(dict(check_id=check.id, branch_id=branch.id,
                                           **identity_parts)).split(':')[-1][:16]
    identity, suffix = base, 1
    used = {case.id for case in check.test_cases}
    while identity in used:
        suffix += 1
        identity = base + '-' + str(suffix)
    return identity


def _branch_diagnostic(check, branch, pointer, test_results):
    """Report missing execution separately from a trigger the grader cannot reach."""
    branch_results = [dict(input=value, actual=grade_learning_check(check, value)) for value in branch.inputs]
    reachable = [row['input'] for row in branch_results
                 if row['actual']['status'] == 'incorrect' and row['actual']['feedback_id'] == branch.id]
    candidates, conditional = [], None
    if reachable:
        code = 'missing_branch_regression'
        value = reachable[0]
        candidates = [dict(id=_branch_test_id(check, branch, input=value), input=value,
                           expected='incorrect', feedback=branch.id, coverage='wrong')]
        reason = '错因 '+branch.id+' 已有可触发的错误输入，但 test_cases 缺少实际命中此分支的回归。'
        hint = '仅向该题 test_cases 末尾追加宿主给出的完整候选；保留原测试、错因分支、题干、来源和评分，不能靠改触发值或删除分支过关。'
        expected = '新增用例实际评分为 incorrect 且 feedback_id 精确等于 '+branch.id+'；原有用例和错因分支均保留并重新执行，所有评分与反馈断言通过。'
    else:
        shadowed = any(row['actual']['status'] == 'incorrect' and row['actual']['feedback_id']
                       and row['actual']['feedback_id'] != branch.id for row in branch_results)
        code = 'shadowed_misconception' if shadowed else 'unreachable_misconception'
        reason = '错因 '+branch.id+(' 的触发输入被更早的错因分支截取。' if shadowed else ' 没有能实际命中自身的有效错误输入。')
        conditional = dict(id=_branch_test_id(check, branch, mode='repair-trigger'),
            expected='incorrect', feedback=branch.id, coverage='wrong',
            **{'$branch_input':dict(check_id=check.id, branch_id=branch.id)})
        hint = '同一补丁只修本错因 inputs，并向该题 test_cases 末尾追加宿主条件模板。新增 input 必须选自修改后的本分支 inputs，且实际评分必须命中本分支；模板身份、预期结果、反馈 ID 和 coverage 不可改。保留原测试、评分、错因解释和全部分支，不可删除或伪造预期。'
        expected = '保留错因 '+branch.id+' 的身份和解释，修改后的有效错误输入实际返回此 feedback_id；同轮追加的固定身份测试必须使用其中一个输入并实际评分为 incorrect、feedback_id 为 '+branch.id+'。原测试全部通过；没有可达输入时仍未修复。'
    return dict(code=code, pointer=pointer, reason=reason, check_id=check.id,
        branch_id=branch.id, issue_key=code+':'+branch.id,
        branch_results=branch_results, test_results=deepcopy(test_results),
        test_case_candidates=candidates, conditional_test_case=conditional,
        repair_hint=hint, expected=expected)


def validate_learning_checks(content, source_context=None) -> list[dict]:
    """Host policy checks. Frozen source resolution remains in content_policy.

    This establishes exercised scoring for supplied checks, not a requirement
    to add checks for every goal. Teaching sufficiency remains semantic review.
    """
    c = parse_content(as_dict(content)).as_dict()
    issues = []
    def issue(code, pointer, reason, check_id='', **details):
        issues.append(dict(code=code, pointer=pointer, reason=reason, check_id=check_id, **details))
    goals = {g['id']: g for g in c['teaching_plan']['goals']}
    from .reader_document import project_reader
    from .source_context import FrozenReferenceCatalog
    if source_context is not None:
        context = as_dict(source_context)
        catalog = context['reference_catalog']
    else:
        # No invented source truth: only enumerate reader-owned pointers when no
        # frozen source context was supplied by the policy host.
        catalog = None
    if catalog is not None:
        reader_pointers = {b['json_pointer'] for b in project_reader(parse_content(c), FrozenReferenceCatalog.model_validate(catalog)).blocks}
    else:
        from .reader_document import _leaves, _registry_tree
        reader_pointers = {pointer for pointer, *_ in _leaves(c, _registry_tree())}
    taught = {e['goal_id'] for e in c['teaching_evidence']
        if e['purpose'] != 'understanding_check' and e['reader_pointer'] in reader_pointers
        and not e['reader_pointer'].startswith('/learning_checks/')}
    for i, row in enumerate(c['learning_checks']):
        p = f'/learning_checks/{i}'
        check = LearningCheckV1.model_validate(row)
        if not check.goal_refs:
            issue('missing_goal', p+'/goal_refs', '自测缺少目标绑定；实际是否考已教内容由审核核对完整正文。', check.id, severity='warning')
        for goal in check.goal_refs:
            if goal not in goals:
                issue('unknown_goal', p+'/goal_refs', '自测引用不存在的目标 '+goal, check.id)
            elif goal not in taught:
                issue('goal_not_taught', p+'/goal_refs', '自测目标 '+goal+' 缺少自测之外的教学证据绑定；不代表正文未教，须审核完整正文。', check.id, severity='warning')
        if not check.source_refs:
            issue('missing_source', p+'/source_refs', '派生自测需要可解析的来源引用。', check.id)
        if not check.hints or any(not x.strip() for x in check.hints):
            issue('missing_hint', p+'/hints', '自测需要非空的逐级提示。', check.id)
        if check.kind == 'self_explanation':
            continue
        cases = check.test_cases
        required = {'correct', 'wrong', 'boundary'}
        coverage = {case.coverage for case in cases}
        if not required <= coverage:
            issue('missing_regression_coverage', p+'/test_cases', '需有正确、错误和边界输入回归。', check.id)
        triggered = set()
        statuses = set()
        test_results = []
        for j, case in enumerate(cases):
            result = grade_learning_check(check, case.input)
            test_results.append(dict(case_id=case.id, input=case.input,
                expected=dict(status=case.expected, feedback_id=case.feedback or None), actual=result))
            statuses.add(result['status'])
            if result['status'] != case.expected or (case.feedback and result['feedback_id'] != case.feedback):
                issue('regression_mismatch', p+f'/test_cases/{j}',
                      f'回归 {case.id} 实际评分为 {result["status"]}、反馈为 {result["feedback_id"]!r}；'
                      f'声明评分为 {case.expected}、反馈为 {case.feedback!r}（空反馈表示不限制分支）。', check.id,
                      issue_key='regression_mismatch:'+case.id, test_case_id=case.id,
                      test_result=test_results[-1],
                      repair_hint='仅修本回归用例：若输入就是要测试的情形，使声明结果与实际评分一致；'
                          '若想覆盖 invalid 边界，改为真正无效的输入并保持相应声明。数字字符串可被数值题正常解析，不能仅因它是字符串就断言 invalid。'
                          '保留该用例 ID、评分器、其他用例及错因分支，不靠放宽正确答案过关。',
                      expected='用当前评分器执行该用例，实际 status 与 expected 相同；'
                          '非空 feedback 须等于实际分支 ID。正确、错误和边界覆盖及已有分支回归仍成立。')
            if case.coverage == 'correct' and case.expected != 'correct' or case.coverage == 'wrong' and case.expected != 'incorrect':
                issue('invalid_regression_role', p+f'/test_cases/{j}', '正确/错误覆盖样例必须具有相应评分。', check.id,
                      issue_key='invalid_regression_role:'+case.id, test_case_id=case.id)
            if result['status'] == 'incorrect' and result['feedback_id']:
                triggered.add(result['feedback_id'])
        if not {'correct', 'incorrect'} <= statuses:
            issue('non_discriminating_grader', p+'/scoring', '评分器必须实际区分正确与错误输入。', check.id)
        if not check.misconception_feedback:
            issue('missing_misconception', p+'/misconception_feedback', '自动检查需要至少一个具体错因与触发样例。', check.id)
        for j, branch in enumerate(check.misconception_feedback):
            if branch.id not in triggered:
                issues.append(_branch_diagnostic(check, branch, p+f'/misconception_feedback/{j}', test_results))
            if any(grade_learning_check(check, value)['status'] == 'correct' for value in branch.inputs):
                issue('misconception_matches_correct', p+f'/misconception_feedback/{j}', '错因触发值包含正确答案。', check.id,
                      branch_id=branch.id, issue_key='misconception_matches_correct:'+branch.id)
            if any(grade_learning_check(check, value)['status'] == 'invalid' for value in branch.inputs):
                issue('invalid_misconception_trigger', p+f'/misconception_feedback/{j}', '错因触发值不属于此题的有效输入。', check.id,
                      branch_id=branch.id, issue_key='invalid_misconception_trigger:'+branch.id)
    return issues


@lru_cache(maxsize=1)
def _casefold_map():
    # A frozen Unicode table makes host and browser normalization identical,
    # including ligatures/Greek sigma. This is data, not an author program.
    return {chr(code): chr(code).casefold() for code in range(0x110000)
        if chr(code).casefold() != chr(code)}


def project_learning_check_runtime(check: LearningCheckV1 | dict) -> dict:
    """Only declarative interaction metadata; prose is supplied by reader blocks."""
    check = LearningCheckV1.model_validate(as_dict(check))
    runtime = dict(id=check.id, kind=check.kind, source_kind=check.source_kind,
        input_spec=dict(options=[{'id':x.id} for x in check.input_spec.options]),
        scoring=check.scoring.as_dict(),
        misconception_feedback=[{'id':m.id, 'inputs':m.inputs} for m in check.misconception_feedback])
    if check.scoring.normalization == 'casefold':
        runtime['casefold_map'] = dict(_casefold_map())
    return runtime
