from copy import deepcopy
import pytest

from aiforstudy.blocks.content.content_contracts import parse_content
from aiforstudy.blocks.content.content_policy import check_content, load_content_policy, applicable_items
from aiforstudy.blocks.content.review import build_evidence_catalog, validate_verdicts
from aiforstudy.blocks.content.review_document import build_review_document
from aiforstudy.blocks.content.repair import finding_ticket, reconcile_tickets
from test_teaching_policy_v2 import teaching_case


def case():
    c, s = teaching_case()
    c['teaching_plan']['claims'][0]['conditions'] = ['n 是大于或等于零的整数']
    c['teaching_plan']['claims'][0]['conclusion'] = '本约定允许 n 等于 0。'
    c = parse_content(c).as_dict()
    p = load_content_policy(s['learner_profile'])
    return c, s, p


def test_equivalent_wording_is_not_a_proven_local_error_but_requires_fidelity_review():
    c, s, p = case()
    local = check_content(c, s, p)
    assert not [r for r in local.blocking_issues if r['issue_key'].startswith('changed-obligation')]
    item = next(r for r in applicable_items(c, s, p) if r['rule_id'] == 'SOURCE-FIDELITY')
    assert item['required'] and item['required_evidence_kinds'] == []
    doc = build_review_document(c, s['reference_catalog'])
    catalog = build_evidence_catalog(c, doc, s)
    unknown = dict(criterion_id=item['criterion_id'], version=item['version'], status='unknown',
                   reason='还未对照判断是否等价。', evidence_refs=[])
    report = validate_verdicts([item], {'verdicts':[unknown]}, catalog)
    assert report['decision'] != 'accepted'


@pytest.mark.parametrize('other_id', ['OTHER', 'SO'])
def test_fidelity_failure_can_repair_its_claim_without_rebinding_source_or_another_claim(other_id):
    c, s, p = case()
    other = deepcopy(c['teaching_plan']['claims'][0]); other['id'] = other_id
    other['source_refs'] = []
    c['teaching_plan']['claims'].append(other)
    c['math_models'] = [dict(schema_version='1.0',model_id='M',symbols=[dict(name='n',domain='real',assumptions=[])],
        objects=[dict(math_object_id='n',kind='scalar',expr={'op':'var','name':'n'})],conditions=[],
        claims=[dict(claim_id=cid,kind='abstract',math_object_id='n',reason='来源语义核验') for cid in ('K',other_id)],
        source_refs=['SO'])]
    catalog = build_evidence_catalog(c, build_review_document(c, s['reference_catalog']), s)
    source = next(k for k,v in catalog.items() if v['kind']=='source' and 'SO' in v['subject_ids'])
    reader = next(k for k,v in catalog.items() if v['kind']=='reader' and 'SO' in v['subject_ids'])
    row = dict(rule_id='SOURCE-FIDELITY', criterion_id='SOURCE-FIDELITY:SO', version=2,
        subject_id='SO', reason='主张与读者结论遗漏必要限制。', status='fail',
        evidence_refs=[{'evidence_id':key,'quote':catalog[key]['text']} for key in (source, reader)])
    ticket = finding_ticket(c, s, p, row, catalog)
    assert {'/teaching_plan/claims/0/conditions', '/teaching_plan/claims/0/conclusion'} <= set(ticket.allowed_paths)
    assert not any('/claims/1/' in path or 'source_refs' in path or path.startswith('/math_models')
                   for path in ticket.allowed_paths)
    active, closed = reconcile_tickets([], [ticket], [dict(criterion_id='SOURCE-FIDELITY:SO',
        version=2,status='unknown')], content_hash='new', executed_checks={'rule:CONTENT-COVERAGE'})
    assert active and not closed


@pytest.mark.parametrize('field,value', [('conditions', []), ('conditions', [' ']), ('conclusion', ' ')])
def test_missing_canonical_fields_are_still_local_errors(field, value):
    c, s, p = case(); c['teaching_plan']['claims'][0][field] = value
    assert any(r['issue_key'].startswith('changed-obligation') for r in check_content(c,s,p).blocking_issues)
