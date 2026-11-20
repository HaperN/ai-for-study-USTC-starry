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
