"""Approval must bind actual V4 source, reader, math, policy and criterion evidence."""
from copy import deepcopy
import pytest
from aiforstudy.errors import ContractError
from aiforstudy.blocks.content.content_approval import approve_content, validate_content_approval
from aiforstudy.blocks.content.content_contracts import content_digest
from test_content_approval_v2 import approved_inputs, issue


@pytest.mark.parametrize('fake', [{'findings':[]}, {'decision':'accepted'},
    {'schema_version':'ContentCheckReportV1','findings':[],'coverage':{'cross_block_checked':True}}])
def test_empty_findings_or_legacy_coverage_cannot_approve(fake):
    inputs=list(approved_inputs())
    inputs[4]=[inputs[4][0],fake]
    with pytest.raises(ContractError): issue(inputs)


def test_receipt_binds_content_policy_source_and_document():
    inputs=approved_inputs();c,source,p,doc,_,math=inputs
    receipt=issue(inputs)
    assert receipt.schema_version=='ContentApprovalV2'
    assert receipt.source_context_hash==source['source_context_hash']
    assert receipt.review_document_hash==content_digest(doc)
    assert receipt.math_report_hash==content_digest(math)
    assert validate_content_approval(c,receipt,p,source_context_hash=source['source_context_hash'],review_document=doc).decision=='accepted'
    changed=deepcopy(c);changed['body_markdown']='changed'
    with pytest.raises(ContractError): validate_content_approval(changed,receipt,p)
    with pytest.raises(ContractError): validate_content_approval(c,receipt,p,source_context_hash='other')
    with pytest.raises(ContractError): validate_content_approval(c,receipt,p,review_document={'fake':'document'})


@pytest.mark.parametrize('damage',['missing-item','wrong-subject','wrong-document'])
def test_partial_or_unrelated_criterion_evidence_is_rejected(damage):
    inputs=list(approved_inputs())
    if damage=='missing-item': inputs[4][1]['verdicts'].pop()
    if damage=='wrong-subject': inputs[4][1]['review_items'][0]['subject_id']='foreign'
    if damage=='wrong-document': inputs[3]=dict(inputs[3].as_dict(), blocks=[])
    with pytest.raises(ContractError): issue(inputs)


def test_source_citation_type_is_optional_when_substantive_verdict_is_present():
    inputs=list(approved_inputs())
    verdict=next(v for v in inputs[4][1]['verdicts'] if v['criterion_id'].startswith('SOURCE-FIDELITY:'))
    verdict['evidence_refs']=[r for r in verdict['evidence_refs'] if not r['evidence_id'].startswith('source:')]
    # Evidence types locate the source; omitting one is not a content failure.
    assert verdict['status']=='pass' and verdict['reason']
    assert issue(inputs).decision=='accepted'
