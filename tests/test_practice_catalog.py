import copy
import pytest
from aiforstudy.practice.catalog import content_digest, validate_catalog, select_edition

def question():
 return {'id':'A-1','title':'根','stem':'求 z 的三次方等于1的全部根。','answer':'三个单位根','solution':'由极形式得到三个根。','topic':'复数','unit_id':'CH01-S01-SS04','priority':'必会','priority_reason':'核心方法，非未来必考保证','difficulty':2,'source':{'kind':'past_exam','course':'复变函数A','pdf_page':4,'question_number':'1','source_quote':'原题'},'methods':['开方'],'pitfalls':['漏根'],'basic':True}

def approved():
 q=question();q['review']={'status':'accepted','content_sha256':content_digest(q),'blind_solver':'independent-1','author':'author-1','blind_solution_sha256':'b'*64,'comparison':'条件、三个根和推导逐项一致','issues':[]};return q

def test_changed_solution_cannot_keep_approval():
 q=approved();q['solution']='错误的新解答'
 with pytest.raises(ValueError,match='digest'):validate_catalog([q])

def test_author_cannot_certify_itself():
 q=approved();q['review']['blind_solver']='author-1'
 with pytest.raises(ValueError,match='independent'):validate_catalog([q])

def test_duplicate_and_unreviewed_rejected():
 q=approved()
 with pytest.raises(ValueError,match='duplicate'):validate_catalog([q,q])
 with pytest.raises(ValueError,match='review'):validate_catalog([question()])

def test_basic_subset_full_and_teacher_can_extend():
 a=approved();b=copy.deepcopy(a);b.update(id='A-2',basic=False,priority='可能考');b['review']['content_sha256']=content_digest(b)
 c=copy.deepcopy(a);c.update(id='TEACHER-1',basic=False,topic='教师补充');c['review']['content_sha256']=content_digest(c)
 assert [q['id'] for q in select_edition([a,b],'basic')]==['A-1']
 assert len(select_edition([a,b],'full'))==2
 assert [q['id'] for q in select_edition([a,b],'custom',{'include_ids':['TEACHER-1'],'exclude_ids':['A-2']},[c])]==['A-1','TEACHER-1']

def test_unknown_teacher_reference_is_error():
 with pytest.raises(ValueError,match='unknown'):select_edition([approved()],'custom',{'include_ids':['missing']})

def test_unknown_priority_and_missing_source_rejected():
 q=approved();q['priority']='百分百必考';q['review']['content_sha256']=content_digest(q)
 with pytest.raises(ValueError,match='priority'):validate_catalog([q])
 q=approved();q['source']={};q['review']['content_sha256']=content_digest(q)
 with pytest.raises(ValueError,match='source'):validate_catalog([q])
