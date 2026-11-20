"""Evidence recovery keeps exact content, frozen ticket identity and criteria."""
from copy import deepcopy
from dataclasses import replace

import pytest

from aiforstudy.blocks.audit.evidence import locate_quote
from aiforstudy.blocks.content.reader_document import project_reader
from aiforstudy.blocks.content.review import validate_verdicts
from aiforstudy.blocks.content.repair import reconcile_tickets
from aiforstudy.blocks.content.content_contracts import content_digest
from aiforstudy.errors import ContractError
from test_feedback import review_case, ticket_case

KW = dict(artifact_id='lesson:U', artifact_hash='sha256:'+'a'*64, json_pointer='/body_markdown')


def test_visible_concept_projection_preserves_original_token_and_source_map():
    c, source, _, _, _, _ = review_case()
    raw = '开头。某一个能用其余的[[CONCEPT:C-01|线性表示]]，就是拼出来。结尾。'
    c['body_markdown'] = raw
    c['concepts'] = [{'id':'C-01','role':'introduced'}]
    source['reference_catalog']['entries'] = [{'id':'C-01','kind':'CONCEPT','label':'线性表示','version':'1','markdown':'概念定义'}]
    block = next(b for b in project_reader(c,source['reference_catalog']).blocks if b['json_pointer']=='/body_markdown')
    assert block['raw_text'] == raw
    assert block['visible_text'] == '开头。某一个能用其余的线性表示，就是拼出来。结尾。'
    marker = block['marker_spans'][0]
    assert raw[marker['raw_start']:marker['raw_end']] == '[[CONCEPT:C-01|线性表示]]'
    assert all(raw[index] == char for char,index in zip(block['visible_text'],block['source_map']))


@pytest.mark.parametrize('raw,quote,status', [
    ('线性表示；线性表示', '线性表示', 'ambiguous'),
    ('若 $x=1$ 则线性表示', '若 $x=2$ 则线性表示', 'invalid'),
    ('若 a b 则线性表示', '若 ab 则线性表示', 'invalid'),
])
def test_quote_locator_never_loosens_mathematical_evidence(raw, quote, status):
    assert locate_quote(quote, raw, **KW).status == status


def test_quoted_ellipsis_marker_produces_real_ordered_spans():
    raw = '秩属于原向量组。这里有中间解释。不同极大无关组的个数相等。'
    result = locate_quote('秩属于原向量组。『……』不同极大无关组的个数相等。', raw, **KW)
    assert result.status == 'valid' and len(result.spans) == 2
    assert all(s.quote == raw[s.start:s.end] for s in result.spans)


def test_evidence_rebinding_without_review_does_not_close_or_change_criteria():
    c, _, _, ticket = ticket_case()
    old = replace(ticket,status='needs_evidence',allowed_paths=())
    c['body_markdown'] += '补充文字。'
    updated, closed = reconcile_tickets([], [old], [], content_hash=content_digest(c))
    assert not closed and len(updated) == 1
    assert updated[0].ticket_id == old.ticket_id and updated[0].criteria == old.criteria
    assert updated[0].status == 'needs_evidence'


@pytest.mark.parametrize('damage', ['missing', 'foreign', 'duplicate', 'weakened-standard'])
def test_bad_evidence_supplement_cannot_replace_frozen_review(damage):
    _, _, _, criterion, catalog, verdict = review_case()
    rows = [verdict]
    if damage == 'missing': rows = []
    if damage == 'foreign': verdict['criterion_id'] = 'other'
    if damage == 'duplicate': rows.append(deepcopy(verdict))
    if damage == 'weakened-standard': verdict['expected'] = '随便说修好了即可'
    with pytest.raises(ContractError):
        validate_verdicts([criterion],{'verdicts':rows},catalog)


def test_recovery_of_one_ticket_keeps_every_other_open_ticket():
    c, _, _, ticket = ticket_case()
    other = replace(ticket,ticket_id='independent-ticket',previous_attempt={'criterion_id':'other'})
    row = {'criterion_id':ticket.criteria[0].criterion_id,'version':ticket.criteria[0].version,'status':'pass'}
    pending, closed = reconcile_tickets([], [ticket, other], [row], content_hash=content_digest(c))
    assert closed == [ticket.ticket_id]
    assert [t.ticket_id for t in pending] == [other.ticket_id]
    assert pending[0].criteria == other.criteria
