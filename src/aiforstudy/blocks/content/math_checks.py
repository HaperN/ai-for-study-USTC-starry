"""Execute content mathematics and expose honest, claim-specific fallback review."""
from .content_contracts import as_dict, content_digest, JsonRecord
from ..math.contracts import parse_math_model
from ..math.verify import verify_math
from ...errors import ContractError

# An interrupted/resource-failed check has not supplied a mathematical result.
# Only an intrinsic capability/decidability limit may request source review.
NO_FALLBACK = frozenset({'cancelled', 'timeout', 'budget_exhausted', 'cpu_limit',
                         'resource_failure', 'worker_protocol_error', 'memory_limit', 'io_denied', 'cas_failure'})
MATH_REPORT_VERSION = 'ContentMathReportV2'


def check_content_math(content, *, limits=None, cancel=None):
    c = as_dict(content)
    models, checks = {}, []
    for raw in c.get('math_models', []):
        model = parse_math_model(raw)
        models[model.model_id] = model.digest
        result = verify_math(model, limits=limits, cancel=cancel).as_dict()
        checks.extend(dict(row, model_id=model.model_id, model_digest=model.digest)
                      for row in result['checks'])
    statuses = {row['status'] for row in checks}
    status = ('not_applicable' if not models else 'fail' if 'fail' in statuses else
              'unknown' if statuses != {'pass'} else 'pass')
    return JsonRecord(schema_version=MATH_REPORT_VERSION, input_hash=content_digest(c),
                      models=models, checks=checks, status=status)


def math_review_items(content, policy, math_report):
    if math_report is None:
        return ()
    c, report = as_dict(content), as_dict(math_report)
    items = []
    for row in report.get('checks', []):
        if not row.get('relation_id'):
            raise ContractError('mathematical review requires current relation identity')
        if row['status'] != 'unknown' or row.get('reason_code') in NO_FALLBACK:
            continue
        items.append(dict(
            criterion_id=f"MATH-SEMANTIC:{row['model_id']}:{row['relation_id']}",
            version=6, rule_id='MATH-REPRESENTATION', method='reviewer', required=True,
            applicable=True, subject_id=row['claim_id'],
            relation_id=row['relation_id'], model_id=row['model_id'],
            expected='依据来源条件、读者正文与数学知识核对主张；定义和概念只核知识正确性，不强制推导或额外解释，'
                     '定理和例题须逐步核对完整推导及依据。CAS 保持 unknown，'
                     '不得宣称已计算证明。当前关系 '+row['relation_id']+'，未知原因：'+row.get('reason', ''),
            repair_hint='依据数学知识纠正具体错误；定义和概念不因未证明而拒收，定理和例题补齐每一步推导。不得宣称已计算验证，无法确定知识正确性则如实返回 unknown。',
            required_evidence_kinds=[],
            content_hash=content_digest(c), policy_digest=policy.digest))
    return tuple(items)
