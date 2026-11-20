import hashlib,json
from pathlib import Path
import pytest
from aiforstudy.practice.catalog import content_digest,validate_review_files,select_edition
from aiforstudy.practice.assembly import validate_papers
from aiforstudy.practice.render import build_page
from test_practice_catalog import approved

def bound(tmp_path, updates=None):
 q=approved();q.update(updates or {});q['review']['content_sha256']=content_digest(q);blind={'id':q['id'],'status':'solved','answer':q['answer'],'solution':q['solution'],'issues':[]};q['review']['blind_solution_sha256']=hashlib.sha256(json.dumps(blind,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
 records=[('blind.json',[blind]),('comparison.json',[{'id':q['id'],'status':'accepted','author_content_sha256':content_digest(q),'issues':[]}])];ev=[]
 for name,record in records:
  p=tmp_path/name;p.write_text(json.dumps(record));ev.append({'path':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
 q['review']['evidence_files']=ev;return q

def test_rehashing_wrong_answer_does_not_reuse_old_comparison(tmp_path):
 q=bound(tmp_path);validate_review_files([q],tmp_path);q['solution']='WRONG';q['review']['content_sha256']=content_digest(q)
 with pytest.raises(ValueError,match='comparison'):validate_review_files([q],tmp_path)

def test_question_only_uses_minimal_fields():
 q=approved();q['necessary_conditions']=['SECRET_HINT'];q['methods']=['SECRET_METHOD'];q['source']['solution_note']='SECRET_SOURCE';h=build_page('卷',[q],question_only=True,paper={'id':'x','answer':'SECRET_PAPER'})
 for x in ['SECRET_HINT','SECRET_METHOD','SECRET_SOURCE','SECRET_PAPER']:assert x not in h

def test_paper_path_escape_rejected():
 p={'id':'../../escape','kind':'coverage','title':'x','purpose':'x','items':[{'question_id':'A-1','points':10}]}
 with pytest.raises(ValueError,match='identity'):validate_papers([approved()],[p])

def test_teacher_profile_typo_rejected():
 with pytest.raises(ValueError,match='unknown profile'):select_edition([approved()],'custom',{'include_id':['x']})
