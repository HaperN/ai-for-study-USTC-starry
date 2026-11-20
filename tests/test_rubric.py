"""Frozen per-criterion evidence replaces retired rubric scores and ID guessing."""
import json
import sys
from pathlib import Path

import pytest

from aiforstudy.blocks.content.review import validate_verdicts, review_content
from aiforstudy.blocks.content.reader_document import project_reader
from aiforstudy.blocks.content.content_policy import applicable_items
from aiforstudy.blocks.content.prompt import build_content_messages
from aiforstudy.errors import ContractError
from test_feedback import review_case
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from model import _loads_lenient


def test_one_required_failure_beats_every_other_pass_without_score_threshold():
    _, _, _, item, catalog, row = review_case()
    items, rows = [], []
    for i in range(8):
        cid = item['criterion_id'] + ':' + str(i)
        items.append({**item,'criterion_id':cid})
        rows.append({**row,'criterion_id':cid,'status':'fail' if i==0 else 'pass'})
    result = validate_verdicts(items,{'verdicts':rows},catalog)
    assert result.decision == 'needs_review'
    assert [x['criterion_id'] for x in result.blocking_issues] == [items[0]['criterion_id']]


@pytest.mark.parametrize('damage', ['missing-item','missing-reason','wrong-id','old-version'])
def test_unanswered_or_untrusted_judgment_is_protocol_error_not_fabrication(damage):
    _, _, _, criterion, catalog, verdict = review_case()
    rows=[verdict]
    if damage=='missing-item': rows=[]
    if damage=='missing-reason': verdict['reason']=''
    if damage=='wrong-id': verdict['criterion_id']='L1'
    if damage=='old-version': verdict['version']=1
    with pytest.raises(ContractError): validate_verdicts([criterion],{'verdicts':rows},catalog)


@pytest.mark.parametrize('status',['unknown','not_run'])
def test_unanswered_required_check_never_becomes_a_pass(status):
    _, _, _, criterion, catalog, verdict = review_case()
    verdict.update(status=status,evidence_refs=[],reason='review unavailable')
    result=validate_verdicts([criterion],{'verdicts':[verdict]},catalog)
    assert result.decision=='needs_review'
    assert result.blocking_issues[0]['status']==status
    assert 'fabrication' not in result.blocking_issues[0]['reason']


class ScopedReviewer:
    def __init__(self, fail=False):
        self.requests=[]
        self.fail=fail

    def chat_json(self,messages,**kw):
        from support.prompt_context import expand_prompt_material
        payload=json.loads(messages[1]['content'])
        request=expand_prompt_material(payload['frozen_context'],payload)
        self.requests.append(request)
        if self.fail: return {'data':{'accepted':True}}
        rows=[]
        for item in request['criteria']:
            refs=[]
            # This fixture deliberately supplies an optional reader citation so
            # alias/error-retention tests exercise the reference path as well.
            for kind in item['required_evidence_kinds'] or ['reader']:
                eid,entry=next((eid,x) for eid,x in request['evidence_catalog'].items()
                    if x['kind']==kind and item['subject_id'] in x['subject_ids'])
                refs.append({'evidence_id':eid,'quote':entry['text']})
            rows.append({'criterion_id':item['criterion_id'],'version':item['version'],'status':'pass',
                'reason':'当前来源、范围和读者证据一致。','evidence_refs':refs})
        return {'data':{'verdicts':rows}}


def test_reviewer_receives_full_source_beyond_the_selected_quote():
    c, source, policy, _, _, _=review_case()
    source['source_text'] += '摘录以外的整节原文，仍然是合法出处，不能误报编造。'
    doc=project_reader(c,source['reference_catalog'])
    items=applicable_items(c,source,policy)
    client=ScopedReviewer()
    result=review_content(c,doc,source,items,None,client)
    assert result.decision=='accepted'
    assert client.requests[0]['source_context']['source_text']==source['source_text']
    assert '摘录以外' in client.requests[0]['source_context']['source_text']


def test_review_batches_only_given_frozen_criteria_and_keeps_complete_evidence():
    c, source, policy, criterion, _, _=review_case()
    items=[{**criterion,'criterion_id':criterion['criterion_id']+':'+str(i)} for i in range(25)]
    client=ScopedReviewer()
    result=review_content(c,project_reader(c,source['reference_catalog']),source,items,None,client)
    assert [len(req['criteria']) for req in client.requests]==[12,12,1]
    assert [v['criterion_id'] for v in result.verdicts]==[x['criterion_id'] for x in items]
    assert all(req['source_context']['source_text']==source['source_text'] for req in client.requests)


def test_bad_review_retains_raw_response_and_never_returns_partial_approval():
    c, source, policy, criterion, _, _=review_case()
    client=ScopedReviewer(fail=True); raw=[]
    with pytest.raises(ContractError):
        review_content(c,project_reader(c,source['reference_catalog']),source,[criterion],None,client,response_sink=raw.append)
    assert raw==[{'accepted':True}]
    assert len(client.requests)==1


def test_reviewer_prompt_has_frozen_policy_and_no_legacy_item_id_guessing():
    _, source, policy, _, _, _=review_case()
    messages=build_content_messages('reviewer',source,policy)
    assert 'L1' not in messages[0]['content']
    assert '宿主原 ID' in messages[0]['content']
    assert json.loads(messages[1]['content'])['policy_digest']==policy.digest


def test_lenient_json_repairs_invalid_escapes():
    broken='{"items": [{"quote": "$\\lambda+\\mu$"}]}'
    with pytest.raises(ValueError): json.loads(broken)
    data,repaired=_loads_lenient(broken)
    assert repaired is True and data['items'][0]['quote']==r'$\lambda+\mu$'


def test_valid_json_control_escape_cannot_be_repaired_after_parsing():
    data,repaired=_loads_lenient('{"quote": "$\\boldsymbol{a}$"}')
    assert repaired is False and data['quote']=='$\x08oldsymbol{a}$'


def test_lenient_json_keeps_valid_json_untouched():
    data,repaired=_loads_lenient('{"a": "b\\nc"}')
    assert repaired is False and data=={'a':'b\nc'}
