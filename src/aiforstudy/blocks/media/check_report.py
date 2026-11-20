"""Machine-readable media checks with compatibility fields for current consumers."""
from ...core.digest import digest_of


def check_report(*,inputs,rules,issues=(),expected=(),checked=(),**compat):
    issues=list(issues)
    return {'schema_version':'CheckReportV1','input_hash':digest_of(inputs),'rule_results':[{'rule_id':rule,'status':'fail' if any(i.get('rule_id')==rule for i in issues) else 'pass'} for rule in rules],'blocking_issues':issues,'warnings':[],'coverage':{'expected':list(expected),'checked':list(checked),'missing':sorted(set(expected)-set(checked))},'ok':not issues,'problems':[i['message'] for i in issues],**compat}
