from copy import deepcopy
import pytest

from aiforstudy.errors import ContractError
from aiforstudy.blocks.content.content_contracts import parse_content, content_digest
from aiforstudy.blocks.content.content_policy import load_content_policy, check_content, applicable_items
from aiforstudy.blocks.content.source_context import SourceContext
from aiforstudy.blocks.content.review_document import build_review_document
from aiforstudy.blocks.content.review import build_evidence_catalog, validate_verdicts
from aiforstudy.blocks.content.content_approval import approve_content, validate_content_approval
from support.content_case import teaching_case, approved_inputs, issue








def test_v2_approval_binds_independent_source_math_reader_and_real_verdicts():
    inputs = approved_inputs()
    receipt = issue(inputs)
    assert receipt.schema_version == 'ContentApprovalV2'
    assert receipt.math_report_hash == content_digest(inputs[-1])
    assert receipt.source_context_hash == inputs[1]['source_context_hash']
    assert receipt.review_items_hash
    assert validate_content_approval(inputs[0], receipt, inputs[2]).decision == 'accepted'


def test_historical_content_cannot_receive_a_new_approval():
    inputs=list(approved_inputs());content=deepcopy(inputs[0])
    content['_meta']['schema_version']='3.0'
    with pytest.raises(ContractError,match='historical content is read-only'):
        approve_content(content,inputs[3],inputs[4],inputs[2])


@pytest.mark.parametrize('damage', ['omit_criterion','fake_evidence','local_not_run','math_fail','stale_math'])
def test_v2_cannot_issue_from_fake_coverage_or_failed_required_checks(damage):
    inputs = list(approved_inputs())
    if damage=='omit_criterion': inputs[4][1]['verdicts'].pop()
    if damage=='fake_evidence': inputs[4][1]['evidence_catalog']['source:SO']['text']='False source'
    if damage=='local_not_run':
        local=inputs[4][0].as_dict()
        local['rule_results'][0].update(status='not_run',executed=False)
        inputs[4][0]=local
    if damage=='math_fail': inputs[-1].update(status='fail',checks=[{'claim_id':'K','status':'fail'}])
    if damage=='stale_math': inputs[-1]['input_hash']='old'
    with pytest.raises(ContractError): issue(inputs)


def test_v2_receipt_rejects_changed_content_and_missing_math_binding():
    inputs = approved_inputs(); receipt=issue(inputs).as_dict()
    c=deepcopy(inputs[0]); c['body_markdown']='n 必须为正，不含零。'
    with pytest.raises(ContractError): validate_content_approval(c,receipt,inputs[2])
    receipt.pop('math_report_hash')
    with pytest.raises(ContractError): validate_content_approval(inputs[0],receipt,inputs[2])
