"""Review reference corrections remain local, explicit and evidence-bound."""
from copy import deepcopy
import json

import pytest

from aiforstudy.blocks.content.review import build_evidence_catalog, validate_verdicts
from aiforstudy.errors import ContractError
from test_teaching_review_evidence import item, evidence, verdict, source_bound_inputs


def errors_for(rows, catalog=None, items=None):
    with pytest.raises(ContractError) as caught:
        validate_verdicts(items or [item()], {'verdicts':rows}, catalog or evidence())
    assert hasattr(caught.value, 'errors'), str(caught.value)
    errors=caught.value.errors
    assert json.loads(str(caught.value))['errors']==errors
    return errors


def test_same_reader_id_can_cite_distinct_exact_slices_without_changing_response():
    row=verdict()
    row['evidence_refs'][0]['quote']='For x != 0'
    row['evidence_refs'].append({'evidence_id':'reader:step','quote':'x/x = 1.'})
    before=deepcopy(row)
    report=validate_verdicts([item()],{'verdicts':[row]},evidence())
    assert report.decision=='accepted' and report.verdicts==[before] and row==before


@pytest.mark.parametrize('status', ['pass', 'fail', 'unknown'])
def test_current_bound_evidence_allows_equivalent_summary_without_replacing_original(status):
    row=verdict(status=status)
    row['evidence_refs'][0]['quote']='When x is nonzero, dividing x by itself gives 1.'
    original=evidence(); before=deepcopy(original)
    report=validate_verdicts([item()], {'verdicts':[row]}, original)
    assert report.verdicts[0]['status']==status
    assert original==before
    assert report.verdicts[0]['evidence_refs'][0]['quote']==row['evidence_refs'][0]['quote']
    foreign=deepcopy(original); foreign['source:T1']['subject_ids']=['OTHER']
    assert validate_verdicts([item()], {'verdicts':[row]}, foreign).verdicts == [row]


def test_identical_optional_id_quote_pair_preserves_the_response():
    row=verdict();row['evidence_refs'].append(deepcopy(row['evidence_refs'][0]))
    report=validate_verdicts([item()], {'verdicts':[row]}, evidence())
    assert report.decision=='accepted' and report.verdicts==[row]


@pytest.mark.parametrize('status', ['pass', 'fail', 'unknown'])
def test_reader_evidence_does_not_require_extra_source_id_even_for_saved_criteria(status):
    row=verdict(evidence_refs=[{'evidence_id':'reader:step','quote':'For x != 0'},
                               {'evidence_id':'reader:step','quote':'x/x = 1.'}], status=status)
    report=validate_verdicts([item()], {'verdicts':[row]}, evidence())
    assert report.verdicts[0]['status']==status
    assert report.decision==('accepted' if status=='pass' else 'needs_review')


def test_source_citation_can_locate_review_without_mandatory_reader_id():
    row=verdict(evidence_refs=[{'evidence_id':'source:T1','quote':'For a nonzero x'}])
    assert validate_verdicts([item()], {'verdicts':[row]}, evidence()).decision=='accepted'


def test_all_reference_errors_have_exact_paths_and_grounded_candidates_in_one_feedback():
    catalog=evidence()
    catalog['source:foreign']={**catalog['source:T1'],'subject_ids':['OTHER']}
    catalog['reader:stale']={**catalog['reader:step'],'content_hash':'sha256:old'}
    row=verdict(evidence_refs=[
        {'evidence_id':'reader:typo','quote':'For x != 0'},
        {'evidence_id':'source:foreign','quote':'For a nonzero x'},
        {'evidence_id':'source:T1','quote':[]},
        {'evidence_id':'reader:stale','quote':'For x != 0'},
    ])
    other=verdict(criterion_id='CR-second',version=999)
    errors=errors_for([row,other],catalog,[item(),item(criterion_id='CR-second')])
    by_path={e['path']:e for e in errors}
    prefix='/verdicts/0/evidence_refs/'
    for index,code in ((0,'unknown_evidence_id'),
                       (2,'invalid_evidence_quote'),(3,'stale_evidence')):
        assert any(e['path'].startswith(prefix+str(index)) and e['code']==code for e in errors)
    assert not any(e['path'].startswith(prefix+'1/') for e in errors)
    first=by_path[prefix+'0/evidence_id']
    assert first['actual']=='reader:typo'
    assert first['candidates']==[{'evidence_id':'reader:step','quote':'For x != 0'}]
    assert first['subject_id']=='T1' and first['criterion_id']=='CR-source-T1'
    assert by_path['/verdicts/1/version']['code']=='changed_criterion_version'
    assert all('passing_criterion' in e for e in errors)


def test_wrong_quote_type_offers_its_current_full_text_without_guessing():
    row=verdict();row['evidence_refs'][0]['quote']=[]
    errors=errors_for([row])
    error=next(e for e in errors if e['code']=='invalid_evidence_quote')
    assert error['candidates']==[{'evidence_id':'reader:step','quote':evidence()['reader:step']['text']}]
    catalog=evidence();catalog['reader:step']['subject_ids']=['OTHER']
    error=next(e for e in errors_for([row],catalog) if e['code']=='invalid_evidence_quote')
    assert error['candidates']==[{'evidence_id':'reader:step','quote':catalog['reader:step']['text']}]


@pytest.mark.parametrize('bad_id',[None,{},[],42])
def test_dirty_reference_ids_are_diagnostics_not_program_type_errors(bad_id):
    row=verdict();row['evidence_refs'][0]['evidence_id']=bad_id
    assert any(e['code']=='invalid_evidence_id' for e in errors_for([row]))


@pytest.mark.parametrize('status',['unknown','not_run','fail'])
def test_valid_multiple_quotes_never_upgrade_uncertainty_or_failure(status):
    row=verdict(status=status)
    row['evidence_refs'].append({'evidence_id':'reader:step','quote':'For x != 0'})
    report=validate_verdicts([item()],{'verdicts':[row]},evidence())
    assert report.decision=='needs_review' and report.blocking_issues[0]['status']==status


def learning_catalog():
    c,doc,source=source_bound_inputs()
    c['teaching_plan']['goals'] += [{'id':'SECOND','claim_refs':['K']}, {'id':'OTHER','claim_refs':[]}]
    c['learning_checks']=[{'id':'CHECK','goal_refs':['G','SECOND'],
        'source_refs':[{'kind':'derived','claim_id':'K'}],
        'statement':'For x != 0, choose x/x.', 'answer':'The result is 1.'},
        {'id':'SIBLING','goal_refs':['OTHER'],'source_refs':[],'statement':'Unrelated task'}]
    for index,field in ((0,'statement'),(0,'answer'),(1,'statement')):
        text=c['learning_checks'][index][field]
        doc['blocks'].append({'block_id':f'check-{index}-{field}',
            'json_pointer':f'/learning_checks/{index}/{field}','visible_text':text,'raw_text':text})
    return c,doc,source


def test_learning_owner_declared_goals_and_claims_reach_its_visible_fields_only():
    c,doc,source=learning_catalog();catalog=build_evidence_catalog(c,doc,source)
    for field in ('statement','answer'):
        assert {'G','SECOND','K','SO1'}<=set(catalog['reader:check-0-'+field]['subject_ids'])
        assert 'OTHER' not in catalog['reader:check-0-'+field]['subject_ids']
    assert set(catalog['reader:check-1-statement']['subject_ids'])=={'OTHER','unit:U'}
    assert 'SECOND' not in catalog['reader:B']['subject_ids']


def test_reader_location_does_not_invent_subject_links_or_prejudge_semantics():
    c,doc,source=learning_catalog()
    c['learning_checks'][0]['goal_refs'].append('INVENTED')
    c['learning_checks'][0]['source_refs'].append({'kind':'derived','claim_id':'INVENTED'})
    catalog=build_evidence_catalog(c,doc,source)
    assert 'INVENTED' not in catalog['reader:check-0-statement']['subject_ids']
    row=verdict(status='fail', reason='This task does not teach SECOND; inspect the goal and add its actual task.',
                evidence_refs=[{'evidence_id':'reader:check-1-statement','quote':'Unrelated task'}])
    criterion=item(subject_id='SECOND',content_hash=catalog['reader:B']['content_hash'],required_evidence_kinds=['reader'])
    report=validate_verdicts([criterion],{'verdicts':[row]},catalog)
    assert report.decision=='needs_review' and report.verdicts[0]['status']=='fail'


def test_valid_references_cannot_override_a_computed_counterexample():
    status = 'fail'
    catalog={'math:M:R':{'kind':'math_check','content_hash':'sha256:current',
        'subject_ids':['T1'],'status':status}}
    row=verdict(evidence_refs=[{'evidence_id':'math:M:R'}])
    errors=errors_for([row],catalog,[item(required_evidence_kinds=['math_check'])])
    assert any(e['code']=='math_evidence_cannot_pass' for e in errors)


def test_reviewer_can_accept_independently_reasoned_cas_unknown():
    catalog={'math:M:R':{'kind':'math_check','content_hash':'sha256:current',
        'subject_ids':['T1'],'status':'unknown'}}
    row=verdict(evidence_refs=[{'evidence_id':'math:M:R'}])
    report=validate_verdicts([item(required_evidence_kinds=['math_check'])],{'verdicts':[row]},catalog)
    assert report.decision=='accepted'


def test_repeated_nontext_evidence_with_quotes_preserves_the_response():
    catalog={'math:M:R':{'kind':'math_check','content_hash':'sha256:current',
        'subject_ids':['T1'],'status':'pass'}}
    row=verdict(evidence_refs=[{'evidence_id':'math:M:R','quote':'first'},
                              {'evidence_id':'math:M:R','quote':'second'}])
    report=validate_verdicts([item(required_evidence_kinds=['math_check'])],{'verdicts':[row]},catalog)
    assert report.decision=='accepted' and report.verdicts==[row]


def test_unknown_criterion_shape_and_bad_quote_are_aggregated_without_partial_report():
    rows=[verdict(criterion_id=[]),verdict(evidence_refs=[{'evidence_id':'reader:step','quote':[]}])]
    errors=errors_for(rows)
    assert {'unknown_criterion','invalid_evidence_quote','verdict_count'}<=set(e['code'] for e in errors)


def test_reviewer_receives_current_ids_and_retains_failed_batch_and_feedback():
    from aiforstudy.blocks.content.review import review_content
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.blocks.content.prompt import build_content_messages
    from test_feedback import review_case
    from test_rubric import ScopedReviewer
    c,source,policy,criterion,_,_=review_case()
    class BadReference(ScopedReviewer):
        def chat_json(self,messages,**kwargs):
            result=super().chat_json(messages,**kwargs)
            self.returned=deepcopy(result['data'])
            self.returned['verdicts'][0]['evidence_refs'][0]['evidence_id']='reader:typo'
            return {'data':self.returned}
    client=BadReference();saved=[]
    with pytest.raises(ContractError) as caught:
        review_content(c,project_reader(c,source['reference_catalog']),source,[criterion],None,
            client,response_sink=saved.append)
    assert len(client.requests)==1 and saved==[client.returned]
    request=client.requests[0]
    assert request['criterion_evidence_ids'][criterion['criterion_id']]==list(request['evidence_catalog'])
    feedback=str(caught.value)
    messages=build_content_messages('reviewer',{'source_context':source,
        'repair_context':{'previous_tickets':[{'problem':feedback}]}},policy)
    assert json.loads(messages[1]['content'])['frozen_context']['repair_context']['previous_tickets'][0]['problem']==feedback
    assert json.loads(feedback)['errors'][0]['candidates']
