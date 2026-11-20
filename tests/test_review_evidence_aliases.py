"""Short model IDs preserve exact canonical evidence and immutable responses."""
from copy import deepcopy
import json
import re

import pytest

from aiforstudy.blocks.content.reader_document import project_reader
from aiforstudy.blocks.content.review import review_content, validate_verdicts
from aiforstudy.errors import ContractError
from test_feedback import review_case
from test_rubric import ScopedReviewer
from test_teaching_review_evidence import evidence, item, verdict


def test_model_receives_short_aliases_but_validated_report_retains_canonical_ids():
    c, source, _, criterion, _, _ = review_case()
    client = ScopedReviewer(); raw = []
    result = review_content(c, project_reader(c,source['reference_catalog']), source,
                            [criterion], None, client, response_sink=raw.append)
    catalog = client.requests[0]['evidence_catalog']
    assert catalog and all(re.fullmatch(r'E[0-9]{4,}', key) for key in catalog)
    assert all(re.fullmatch(r'E[0-9]{4,}', ref['evidence_id'])
               for row in raw[0]['verdicts'] for ref in row['evidence_refs'])
    assert all(ref['evidence_id'].startswith(('reader:','source:'))
               for row in result.verdicts for ref in row['evidence_refs'])
    assert result.decision == 'accepted'


def test_raw_failed_response_and_bound_mapping_are_saved_before_rejection():
    c, source, _, criterion, _, _ = review_case()
    class WrongQuoteType(ScopedReviewer):
        def chat_json(self,*args,**kwargs):
            out = super().chat_json(*args,**kwargs)
            out['data']['verdicts'][0]['evidence_refs'][0]['quote'] = []
            self.raw = deepcopy(out['data'])
            return out
    client=WrongQuoteType(); saved=[]; exchanges=[]
    with pytest.raises(ContractError):
        review_content(c, project_reader(c,source['reference_catalog']),source,[criterion],None,
                       client,response_sink=saved.append,exchange_sink=exchanges.append)
    assert saved == [client.raw] and len(exchanges) == 1
    exchange=exchanges[0]
    assert exchange['response'] == client.raw
    assert exchange['criteria'] == [criterion]
    assert exchange['binding']['batch_index'] == 0
    assert exchange['binding']['encoding'] == 'EvidenceAliasesV1'
    assert set(exchange['evidence_aliases'].values()) == set(exchange['evidence_catalog'])


def test_alias_assignment_is_deterministic_and_response_decode_changes_only_ids():
    from aiforstudy.blocks.content.review_aliases import build_evidence_aliases, decode_evidence_response
    catalog=evidence()
    aliases=build_evidence_aliases(catalog)
    assert aliases == {'E0001':'reader:step','E0002':'source:T1'}
    assert build_evidence_aliases(dict(reversed(list(catalog.items())))) == aliases
    raw={'verdicts':[verdict(status='unknown', evidence_refs=[
        {'evidence_id':'E0001','quote':'For x != 0'}, {'evidence_id':'E0001','quote':'x/x = 1.'},
        {'evidence_id':'E0002','quote':'For a nonzero x'}])]}
    before=deepcopy(raw)
    normalized=decode_evidence_response(raw,aliases)
    assert raw == before
    assert normalized['verdicts'][0]['status'] == 'unknown'
    assert normalized['verdicts'][0]['reason'] == raw['verdicts'][0]['reason']
    assert validate_verdicts([item()],normalized,catalog).decision == 'needs_review'


@pytest.mark.parametrize('wrong',['E001','E0000','E9999','reader:step',None,1,{}])
def test_unknown_aliases_never_use_fuzzy_or_canonical_id_fallback(wrong):
    from aiforstudy.blocks.content.review_aliases import decode_evidence_response
    raw={'verdicts':[verdict(evidence_refs=[{'evidence_id':wrong,'quote':'For x != 0'}])]}
    with pytest.raises(ContractError):
        decode_evidence_response(raw,{'E0001':'reader:step'})


def test_alias_decoding_preserves_cross_subject_refs_and_does_not_fill_missing_quotes():
    from aiforstudy.blocks.content.review_aliases import decode_evidence_response
    catalog=evidence(); catalog['source:foreign']={**catalog['source:T1'],'subject_ids':['OTHER']}
    raw={'verdicts':[verdict(evidence_refs=[{'evidence_id':'E0001','quote':''},
        {'evidence_id':'E0002','quote':'x/x = 1.'}])]}
    normalized=decode_evidence_response(raw,{'E0001':'reader:step','E0002':'source:foreign'})
    report = validate_verdicts([item()],normalized,catalog)
    assert report.decision == 'accepted'
    assert normalized['verdicts'][0]['evidence_refs'][0] == {'evidence_id':'reader:step','quote':''}
    assert normalized['verdicts'][0]['evidence_refs'][1] == {'evidence_id':'source:foreign','quote':'x/x = 1.'}


def test_alias_feedback_projects_only_ids_not_quotes_or_persisted_tickets():
    from aiforstudy.blocks.content.review_aliases import project_repair_context
    errors=[{'path':'/verdicts/0/evidence_refs/0/evidence_id','actual':'reader:step',
             'allowed_evidence_ids':['reader:step','reader:old'],
             'candidates':[{'evidence_id':'reader:step','quote':'reader:step'},
                           {'evidence_id':'reader:old','quote':'older text'}]}]
    problem='ReviewProtocolError: '+json.dumps({'schema_version':'ContentReviewProtocolErrorsV1',
        'repair_scope':'review_response_only','errors':errors})
    context={'previous_tickets':[{'problem':problem,'previous_attempt':{'closure_reason':problem}}]}
    before=deepcopy(context)
    projected=project_repair_context(context,{'reader:step':'E0001'})
    assert context == before
    data=json.loads(projected['previous_tickets'][0]['problem'].split(': ',1)[1])
    assert data['errors'][0]['actual'] == 'E0001'
    assert data['errors'][0]['allowed_evidence_ids'] == ['E0001']
    assert data['errors'][0]['candidates'] == [{'evidence_id':'E0001','quote':'reader:step'}]


def test_each_batch_keeps_the_full_catalog_assignment_and_distinct_bound_exchange():
    c, source, _, criterion, _, _=review_case()
    items=[{**criterion,'criterion_id':criterion['criterion_id']+':'+str(i)} for i in range(13)]
    client=ScopedReviewer(); exchanges=[]
    report=review_content(c,project_reader(c,source['reference_catalog']),source,items,None,
                          client,exchange_sink=exchanges.append)
    assert len(exchanges)==2 and report.exchanges == exchanges
    assert exchanges[0]['evidence_aliases']==exchanges[1]['evidence_aliases']
    assert exchanges[0]['binding']['items_digest'] != exchanges[1]['binding']['items_digest']
    assert [x['binding']['batch_index'] for x in exchanges] == [0,1]


def test_later_batch_protocol_error_preserves_inspectable_earlier_verdicts_without_approval():
    c, source, _, criterion, _, _=review_case()
    items=[{**criterion,'criterion_id':criterion['criterion_id']+':'+str(i)} for i in range(13)]
    class SecondBatchFails(ScopedReviewer):
        def chat_json(self,*args,**kwargs):
            result=super().chat_json(*args,**kwargs)
            if len(self.requests)==2:
                result['data']['verdicts'][0]['evidence_refs'][0]['evidence_id']='E9999'
            return result
    client=SecondBatchFails(); exchanges=[]
    with pytest.raises(ContractError) as caught:
        review_content(c,project_reader(c,source['reference_catalog']),source,items,None,
                       client,exchange_sink=exchanges.append)
    partial=caught.value.partial_report
    assert partial['schema_version']=='PartialReviewReportV1'
    assert partial['decision']=='needs_review' and partial['complete'] is False
    assert [r['status'] for r in partial['verdicts']] == ['pass']*12+['unknown']
    assert partial['exchanges']==exchanges and len(exchanges)==2


def test_cancellation_is_not_converted_into_partial_semantic_verdicts():
    from aiforstudy.errors import Cancelled
    c, source, _, criterion, _, _=review_case()
    class CancelReviewer:
        def chat_json(self,*args,**kwargs):
            raise Cancelled('stop')
    with pytest.raises(Cancelled) as caught:
        review_content(c,project_reader(c,source['reference_catalog']),source,[criterion],None,
                       CancelReviewer())
    assert not hasattr(caught.value,'partial_report')
