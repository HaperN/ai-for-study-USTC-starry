from copy import deepcopy

import pytest

from aiforstudy.blocks.audit.contracts import validate_ticket, make_ticket_id
from aiforstudy.blocks.content.content_contracts import content_digest, parse_content
from aiforstudy.blocks.content.content_policy import check_content, load_content_policy
from aiforstudy.blocks.content.repair import finding_ticket, reconcile_tickets
from aiforstudy.blocks.content.review import build_evidence_catalog
from aiforstudy.blocks.content.review_document import build_review_document
from test_teaching_policy_v2 import teaching_case


def ticket(*, mathematical=False):
    c,s=teaching_case();c['teaching_plan']['claims'][0]['conditions']=[]
    c=parse_content(c).as_dict();p=load_content_policy(s['learner_profile'])
    catalog=build_evidence_catalog(c,build_review_document(c,s['reference_catalog']),s)
    row=next(r for r in check_content(c,s,p).blocking_issues if r['issue_key']=='changed-obligation-SO')
    if mathematical:
        row=dict(rule_id='MATH-REPRESENTATION',subject_id='K',issue_key='math:M:K',
            problem='Current mathematical claim failed.',expected='Reexecute this claim.')
    return finding_ticket(c,s,p,row,catalog,deterministic=True)


def test_deterministic_ticket_stays_open_when_the_current_checker_did_not_execute():
    prior=ticket()
    remaining,closed=reconcile_tickets([], [prior], [], content_hash='current')
    assert closed==[]
    assert remaining[0].ticket_id==prior.ticket_id
    assert remaining[0].target['artifact_hash']=='current'


def test_executed_local_check_can_close_only_its_missing_original_issue():
    prior=ticket()
    remaining,closed=reconcile_tickets([], [prior], [], content_hash='current',
        executed_checks={'rule:CONTENT-COVERAGE'})
    assert remaining==[] and closed==[prior.ticket_id]
    remaining,closed=reconcile_tickets([prior], [prior], [], content_hash='current',
        executed_checks={'rule:CONTENT-COVERAGE'})
    assert [t.ticket_id for t in remaining]==[prior.ticket_id] and closed==[]


@pytest.mark.parametrize('executed', [set(), {'rule:MATH-REPRESENTATION'}, {'math:M:OTHER'}])
def test_mathematical_ticket_requires_its_own_current_claim_execution(executed):
    prior=ticket(mathematical=True)
    remaining,closed=reconcile_tickets([], [prior], [], content_hash='current',executed_checks=executed)
    assert [t.ticket_id for t in remaining]==[prior.ticket_id] and closed==[]


def test_current_mathematical_pass_closes_the_same_frozen_claim():
    prior=ticket(mathematical=True)
    remaining,closed=reconcile_tickets([], [prior], [], content_hash='current',executed_checks={'math:M:K'})
    assert remaining==[] and closed==[prior.ticket_id]


def external_ticket():
    raw=ticket().as_dict()
    raw['target']['rule']='POST_MEDIA_CONTENT'
    raw['ticket_id']=make_ticket_id(rule='POST_MEDIA_CONTENT',unit_id=raw['unit_id'],
        artifact_id=raw['target']['artifact_id'],location='s:'+raw['target']['step_id'])
    raw['previous_attempt']={'criterion_id':'old-criterion'}
    raw['criteria']=[dict(criterion_id='original-A',version=3,required=True,expected='Original A',method='reviewer',evidence_type='text'),
                     dict(criterion_id='original-B',version=7,required=True,expected='Original B',method='reviewer',evidence_type='text')]
    return validate_ticket(raw)


@pytest.mark.parametrize('damage', ['missing','version','unknown','old_criterion'])
def test_media_ticket_needs_every_original_external_criterion_pass(damage):
    prior=external_ticket()
    rows=[dict(criterion_id=f'external:{prior.ticket_id}:original-A',version=3,status='pass'),
          dict(criterion_id=f'external:{prior.ticket_id}:original-B',version=7,status='pass')]
    if damage=='missing':rows.pop()
    if damage=='version':rows[1]['version']=8
    if damage=='unknown':rows[1]['status']='unknown'
    if damage=='old_criterion':rows=[dict(criterion_id='old-criterion',version=3,status='pass')]
    remaining,closed=reconcile_tickets([], [prior], rows, content_hash='current')
    assert [t.ticket_id for t in remaining]==[prior.ticket_id] and closed==[]


def test_media_ticket_closes_with_all_original_versions_and_keeps_criteria_unchanged():
    prior=external_ticket();before=deepcopy(prior.as_dict())
    rows=[dict(criterion_id=f'external:{prior.ticket_id}:original-A',version=3,status='pass'),
          dict(criterion_id=f'external:{prior.ticket_id}:original-B',version=7,status='pass')]
    remaining,closed=reconcile_tickets([], [prior], rows, content_hash='current')
    assert remaining==[] and closed==[prior.ticket_id]
    assert prior.as_dict()==before
