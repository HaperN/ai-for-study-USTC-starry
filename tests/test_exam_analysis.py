import copy
import json
from pathlib import Path
import pytest
from aiforstudy.core.exam_analysis import analyze, select, validate_annotations, load_annotations
from aiforstudy.core.course_library import load_library
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def corpus():
 c,_=load_library(ROOT/'data/parse/complex-analysis-v2/course-library')
 return c,load_annotations()
def test_real_folds_and_fixed_budget(corpus):
 c,a=corpus; r=analyze(c,a)
 assert len(r['folds'])==6
 assert [sum(f['course']==k for f in r['folds']) for k in ['复变函数A','复变函数B']]==[4,2]
 for f in r['folds']:
  for p in f['predictions'].values():
   assert len(p['cards'])==6
   assert len({q['template_id'] for q in p['cards']})==6
  assert f['metrics']['frequency']['score_coverage'] is None
 assert r['strict_asof']['metrics'] is None

def test_target_mutation_and_course_isolation(corpus):
 c,a=corpus; f=analyze(c,a)['folds'][0]; other=copy.deepcopy(a)
 ids=set(f['train_session_ids'])
 for q in other['questions']:
  if q['session_id'] not in ids:q['annotation']['template_id']='pending';q['candidate_transcription']='future answer'
 s={s['id']:s for s in c['exam_sessions']}
 assert select(a,s,ids,'frequency')==select(other,s,ids,'frequency')
 assert all(s[x]['course']==f['course'] for x in ids)
 assert all(int(s[x]['academic_year'][:4])<f['target_ordinal'] for x in ids)

def test_identity_and_page_validation(corpus):
 c,a=corpus; b=copy.deepcopy(a);b['questions'][0]['pdf_page']=23
 with pytest.raises(ValueError):validate_annotations(c,b)
 b=copy.deepcopy(a);b['questions'].append(b['questions'][0])
 with pytest.raises(ValueError):validate_annotations(c,b)

def test_duplicate_does_not_inflate(corpus):
 c,a=corpus;s={x['id']:x for x in c['exam_sessions']};ids={x['id'] for x in s.values() if x['course']=='复变函数A'}
 b=copy.deepcopy(a);b['questions']+=b['questions']
 assert select(a,s,ids,'frequency')==select(b,s,ids,'frequency')

def test_unknowns_and_conflicts(corpus):
 c,a=corpus;r=analyze(c,a)
 assert len(a['questions'])==238
 assert all(q['solution'] is None and not q['automatic_grading'] for q in a['questions'])
 assert any(q['pdf_page']==15 and q['printed_label']=='3(1)' and q['annotation']['status']=='pending' for q in a['questions'])
 assert all(q['assignment_status']=='unknown' for q in r['textbook_pool'])

def test_reference_calculations():
 s=pytest.importorskip("sympy")
 p,x,y,a,b,c,d,z=s.symbols('p x y a b c d z')
 u=a*x**3+b*x*x*y+c*x*y*y+d*y**3
 assert s.expand(s.diff(u,x,2)+s.diff(u,y,2)-(6*a+2*c)*x-(2*b+6*d)*y)==0
 assert s.simplify(1/(2*p)-1/(p-1)+1/(2*(p-2))-1/(p*(p-1)*(p-2)))==0
 assert s.residue(s.cos(z)/(z-s.I),z,s.I)==s.cosh(1)
 assert s.series(1/(1-z)+s.exp(z),z,0,6).removeO()==sum((1+1/s.factorial(n))*z**n for n in range(6))

def test_foundations_and_repeat_sensitivity(corpus):
 c,a=corpus;r=analyze(c,a)
 for f in r['folds']:
  assert all([x['template_id'] for x in p['cards'][:2]]==['conjugate','taylor'] for p in f['predictions'].values())
  assert all(p['estimated_minutes']==120 for p in f['predictions'].values())
 assert r['folds'][-1]['metrics']['frequency']['verified_repeat_items']==1
 assert all(v['new_family_coverage'] is None for f in r['folds'] for v in f['metrics'].values())

def test_every_fold_is_immune_to_future_and_target_changes(corpus):
 c,a=corpus;s={x['id']:x for x in c['exam_sessions']}
 for f in analyze(c,a)['folds']:
  ids=set(f['train_session_ids']);changed=copy.deepcopy(a)
  for q in changed['questions']:
   if q['session_id'] not in ids:
    q['annotation']['template_id']='power';q['points']=100;q['family_id']='spoof';q['solution']={'answer':'target answer'}
  for method,p in f['predictions'].items():assert select(changed,s,ids,method)==p

def test_reviewed_argument_condition_conflict_is_quarantined(corpus):
 c,a=corpus
 q=next(q for q in a['questions'] if q['id']=='general-A-2021-2022-final:3(2)')
 assert q['pdf_page']==9
 assert q['annotation']['status']=='pending'
 assert q['annotation']['issue_type']=='condition_conflict'
 assert q['annotation']['template_id']=='pending'
 assert not q['automatic_grading']
 assert q['solution'] is None and q['candidate_transcription'] is None
 assert sum(q['annotation']['status']=='pending' for q in a['questions'])==11

def test_analysis_edit_invalidates_only_declared_reader_consumers(tmp_path,monkeypatch):
 from aiforstudy.pipeline.runner import Runner
 from aiforstudy.pipeline import stage_io
 from aiforstudy.core.digest import digest_of
 from support.runtime_tree import copy_runtime
 package,adapters=tmp_path/'package',tmp_path/'adapters'
 copy_runtime(package,adapters)
 runner=object.__new__(Runner)
 monkeypatch.setattr(runner,'_runtime_package_root',lambda:package)
 monkeypatch.setattr(stage_io,'_repo_root',lambda:adapters)
 monkeypatch.setattr(runner,'_runtime_identity',lambda:{'python':{},'dependencies':{}})
 before={s:runner._stage_runtime_identity(s) for s in ['render','delivery','lesson']}
 p=package/'core/exam_analysis.py'
 p.write_text(p.read_text().replace("'recency_decay': 0.75", "'recency_decay': 0.5"))
 after={s:runner._stage_runtime_identity(s) for s in before}
 for stage in ['render','delivery']:
  assert digest_of(after[stage])!=digest_of(before[stage])
  assert {k for k,v in before[stage]['package'].items() if after[stage]['package'][k]!=v}=={'core/exam_analysis.py'}
 assert after['lesson']==before['lesson']
