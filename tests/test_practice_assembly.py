import pytest
from aiforstudy.practice.assembly import validate_papers
from test_practice_catalog import approved

def test_papers_require_real_questions_and_nonduplicate_selection():
 q=approved();p={'id':'coverage-1','kind':'coverage','title':'覆盖卷','purpose':'核对开方','items':[{'question_id':'A-1','points':10}],'coverage_topics':['复数'],'training_minutes':20}
 assert validate_papers([q],[p])[0]['total_points']==10
 with pytest.raises(ValueError,match='unknown'):validate_papers([q],[{**p,'items':[{'question_id':'absent','points':10}]}])
 with pytest.raises(ValueError,match='duplicate'):validate_papers([q],[{**p,'items':p['items']*2}])

def test_coverage_cannot_claim_untrained_topic():
 p={'id':'coverage-1','kind':'coverage','title':'覆盖卷','purpose':'覆盖','items':[{'question_id':'A-1','points':10}],'coverage_topics':['没有训练到的方法']}
 with pytest.raises(ValueError,match='coverage'):validate_papers([approved()],[p])

def test_fitted_requires_explicit_reference_and_pressure_needs_obstacle():
 p={'id':'p','kind':'fitted','title':'拟合','purpose':'结构','items':[{'question_id':'A-1','points':10}]}
 with pytest.raises(ValueError,match='reference'):validate_papers([approved()],[p])
 p['kind']='pressure'
 with pytest.raises(ValueError,match='obstacle'):validate_papers([approved()],[p])


def pressure_record(q):
 return {'id':'pressure-hash','kind':'pressure','title':'压力卷','purpose':'条件检查','obstacle':'先检查条件',
         'items':[{'question_id':q['id'],'points':10,'slot_id':'s1'}],
         'task_slots':[{'id':'s1','question_ids':[q['id']],'selection_evidence':{
             'question_ids':[q['id']],'question_hashes':{q['id']:q['review']['content_sha256']}}}]}


@pytest.mark.parametrize('hashes',[{'A-1':'0'*64},{},{'A-1':'0'*64,'OTHER':'0'*64},None])
def test_explicit_paper_hashes_must_be_complete_and_current(hashes):
 q=approved();p=pressure_record(q);p['question_hashes']=hashes
 with pytest.raises(ValueError,match='hash'):validate_papers([q],[p])
 assert p['question_hashes']==hashes


@pytest.mark.parametrize('hashes',[{'A-1':'0'*64},{},{'OTHER':'0'*64},None])
def test_explicit_selection_hashes_cannot_be_refreshed_or_ignored(hashes):
 q=approved();p=pressure_record(q);p['question_hashes']={q['id']:q['review']['content_sha256']}
 p['task_slots'][0]['selection_evidence']['question_hashes']=hashes
 with pytest.raises(ValueError,match='hash'):validate_papers([q],[p])


def test_selection_ids_must_match_the_actual_slot_and_paper():
 from copy import deepcopy
 from aiforstudy.practice.catalog import content_digest
 a=approved();b=deepcopy(a);b['id']='A-2';b['stem']='另一个问题';b['review']['content_sha256']=content_digest(b)
 p=pressure_record(a);p['items'].append({'question_id':'A-2','points':10,'slot_id':'s2'})
 p['task_slots'][0]['question_ids']=['A-2']
 p['task_slots'][0]['selection_evidence']={'question_ids':['A-2'],'question_hashes':{'A-2':b['review']['content_sha256']}}
 with pytest.raises(ValueError,match='selection'):validate_papers([a,b],[p])


def test_legacy_hashless_papers_remain_valid_without_mutating_input():
 q=approved();p=pressure_record(q);del p['task_slots'][0]['selection_evidence']['question_hashes']
 result=validate_papers([q],[p])[0]
 assert result['question_hashes']=={q['id']:q['review']['content_sha256']}
 assert 'question_hashes' not in p
 assert 'question_hashes' not in result['task_slots'][0]['selection_evidence']


def test_matching_explicit_paper_and_selection_hashes_are_preserved():
 q=approved();p=pressure_record(q);p['question_hashes']={q['id']:q['review']['content_sha256']}
 result=validate_papers([q],[p])[0]
 assert result['question_hashes']==p['question_hashes']
 assert result['task_slots']==p['task_slots']
