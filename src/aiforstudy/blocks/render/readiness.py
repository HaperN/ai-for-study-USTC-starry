"""Reader availability: current text survives unresolved review or missing media."""
from copy import deepcopy

from ...core.digest import digest_of
from ...errors import ContractError
from ...core.review_mode import reviews_frozen, substantive_reviews_enabled


#: 修改计划 G1/A5：页面与报告统一使用的失败类别名称。
FAILURE_CATEGORY_LABELS = {
    'network': '网络', 'timeout': '网络', 'empty_response': '空答复', 'syntax': '语法', 'unparsed': '语法',
    'protocol': '形状', 'shape': '形状', 'truncation': '截断', 'capture': '截图环境',
    'internal': '技术异常', 'configuration': '技术异常', 'math': '数学失败', 'cancelled': '取消',
    'interrupted_unknown': '未知中断',
}

#: 修改计划 D3：媒体 verified 的含义。
MEDIA_VERIFIED_SCOPE = '媒体“已核实”只表示完成状态画面审核通过，不包含连续运动、音画同步和浏览器实播。'


def failure_category(kind):
    return FAILURE_CATEGORY_LABELS.get(str(kind or ''), '未知中断' if not kind else str(kind))


def media_doubt_tier(row):
    """修改计划 D2：fail → 确定错误；有 action 的 needs_review → 可修疑点；其余 needs_review → 未确认（仅记录）。

    单纯看不清不触发重做；这里只决定页面与报告的措辞。
    """
    report = (row or {}).get('validation') or {}
    verdicts = [v for v in (report.get('verdicts') or report.get('raw_verdicts') or []) if isinstance(v, dict)]
    if any(v.get('status') == 'fail' for v in verdicts):
        return '确定错误'
    doubts = [v for v in verdicts if v.get('status') == 'needs_review']
    if any(str(v.get('action') or '').strip() for v in doubts):
        return '可修疑点'
    if doubts:
        return '未确认（仅记录）'
    return ''


def media_dimensions(row):
    """修改计划 G1：媒体状态按 生成 / 审核 / 挂载 / 实播 分维度记录，失败类别用统一名称。"""
    row = row or {}
    attempts = row.get('code_attempts') or []
    last = attempts[-1] if attempts else {}
    report = row.get('validation') or {}
    review_failure = report.get('review_failure') or {}
    generated = bool(row.get('resources'))
    failure = (last.get('failure_kind') or ('interrupted_unknown' if last.get('state') == 'interrupted_unknown' else '')
               if not generated else review_failure.get('kind', ''))
    return {
        'generation': 'done' if generated else (last.get('state') or 'not_started'),
        'review': ('capture_failed' if report.get('capture_failed') else
                   (row.get('review_outcome') or {}).get('status') or ('not_run' if not report else 'incomplete')),
        'mount': 'pending_check',
        'playback': 'not_checked',
        **({'failure_category': failure_category(failure)} if failure else {}),
    }


def lesson_failure_records(state, unit):
    """The author's terminal cause, without repeated downstream blocked errors."""
    records=[]; seen=set()
    for _,node in sorted(((state or {}).get('nodes') or {}).items()):
        if (node.get('scope_key')!=unit or node.get('stage')!='lesson'
                or node.get('execution_state') not in {'failed','blocked'}):
            continue
        error=node.get('error')
        reason=error.get('reason') if isinstance(error,dict) else error
        if isinstance(reason,str) and reason.strip() and reason not in seen:
            records.append({'stage':'lesson','reason':reason})
            seen.add(reason)
    return records


def reader_problem_summary(data, selected, state=None):
    """One visible entry per unresolved unit, including absent reader documents."""
    def states(review):
        return [review.get('status'), *(status for parent in review.get('upstream', []) for status in states(parent))]
    records=[]
    for unit in selected:
        row=data['units'].get(unit) or {}
        review=row.get('review_outcome') or {}
        if ((reviews_frozen() and not substantive_reviews_enabled()) or review.get('status')=='verified') and row.get('reader_document') and not row.get('diagnostic_problems'):
            continue
        available=bool(row.get('reader_document'))
        status='missing' if not available else 'known_error' if 'known_error' in states(review) else 'unverified'
        root_reasons=[] if available else [p['reason'] for p in lesson_failure_records(state,unit)]
        reasons=list(dict.fromkeys([*root_reasons,*review.get('reasons',[]),
            *((f"【{p['tier']}】" if p.get('tier') else '')+str(p['reason'])
              for p in row.get('diagnostic_problems',[]) if p.get('reason'))]))
        records.append({'unit_id':unit,'status':status,
            'label':{'missing':'未生成','known_error':'确定错误','unverified':'未核实'}[status],
            'reasons':reasons or ['正文尚未生成' if not available else '尚无完整审核证据']})
    return records


def validate_media_bindings(content, plan, manifest):
    if not plan or plan.get('content_hash') != digest_of(content) or not isinstance(plan.get('requests'), list):
        raise ContractError('媒体计划缺失或未绑定当前正文')
    if plan.get('schema_version') not in {'MediaRequirementsV2', 'MediaRequirementsV3'}:
        raise ContractError('教学组合需要当前媒体需求契约，不能把旧或缺失需求当作无需媒体')
    from ..media.author_requirements import validate_author_projection
    validate_author_projection(plan, content)
    from ..media.intent_review import validate_plan_intent
    validate_plan_intent(plan, content)
    requests = plan['requests']
    ids = [r.get('request_id') for r in requests]
    if any(not isinstance(rid, str) or not rid for rid in ids) or len(ids) != len(set(ids)):
        raise ContractError('媒体需求 request_id 缺失或重复')
    # An explicit no-media decision needs no fabricated generation result.
    if not requests:
        return
    if (manifest.get('content_hash') != plan['content_hash']
            or manifest.get('requirements_hash') != digest_of(plan)):
        raise ContractError('媒体清单未绑定当前正文和需求')
    rows = manifest.get('requests') or []
    actual = [r.get('request_id') for r in rows]
    if len(actual) != len(set(actual)) or set(actual) != set(ids):
        raise ContractError('媒体清单请求遗漏、重复或额外未登记')
    by_id = {r['request_id']: r for r in requests}
    for row in rows:
        request = by_id[row['request_id']]
        if row.get('kind') != request['kind'] or row.get('request_hash') != digest_of(request):
            raise ContractError('媒体请求规格绑定失效：' + row['request_id'])
        if row.get('kind') == 'animation' and row.get('render_profile') != 'final':
            raise ContractError('动画尚未完成 final 渲染：' + row['request_id'])


def retain_complete_units(data, missing, state):
    """Retain every current reader document; an empty chapter still has a report."""
    unavailable = set()
    selected = list(data['scope'])
    for unit in selected:
        if not (data['units'].get(unit) or {}).get('reader_document'):
            unavailable.add(unit)
    records = []
    for unit in selected:
        if unit not in unavailable:
            continue
        failures = lesson_failure_records(state,unit)
        failures.extend(deepcopy(row) for row in missing if row.get('unit_id') == unit)
        records.append({'unit_id': unit, 'problems': failures,
                        'automatic_processing': (data['units'].get(unit) or {}).get('automatic_processing', {})})
    complete = [unit for unit in selected if unit not in unavailable]
    data['unavailable_units'] = records
    data['scope'] = complete
    data['units'] = {unit: data['units'][unit] for unit in complete}
    data['tree'] = [row for row in data['tree'] if row['id'] in complete]
    data['models'] = {key: row for key, row in data['models'].items() if row.get('unit_id') in complete}
    used = {row['figure_id'] for unit in data['units'].values() for row in unit.get('reader_media', [])
            if row.get('figure_id')}
    data['figures'] = {key: row for key, row in data['figures'].items() if key in used}
    return complete
