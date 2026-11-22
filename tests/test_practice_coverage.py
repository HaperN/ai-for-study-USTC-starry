import hashlib,json
import pytest
from aiforstudy.practice.coverage import evaluate_coverage

def case():
    questions=[{'id':'Q1','stem':'判断连续并说明原因','unit_id':'CH01'}, {'id':'Q2','stem':'构造去掉条件后的反例','unit_id':'CH01'}]
    obligations=[{'id':'K1','primary_unit_id':'CH01','facets':[{'id':'F1','exercise_required':True}]}]
    review={'unit_id':'CH01','question_stem_hashes':{q['id']:hashlib.sha256(q['stem'].encode()).hexdigest() for q in questions},'obligations':[{'id':'K1','status':'covered','targets':[{'goal':'基础应用','question_ids':['Q1'],'evidence':'判断连续'},{'goal':'条件边界','question_ids':['Q2'],'evidence':'去掉条件构造反例'}],'facets':[{'id':'F1','status':'covered','question_ids':['Q2'],'evidence':'反例检查条件'}]}]}
    return questions,obligations,review

def test_coverage_requires_current_task_and_every_facet():
    q,o,r=case();assert evaluate_coverage(q,o,[r])['complete']
    q[1]['stem']='换了一道无关题';assert not evaluate_coverage(q,o,[r])['complete']
    q,o,r=case();r['obligations'][0]['facets']=[]
    assert not evaluate_coverage(q,o,[r])['complete']

def test_one_question_and_unpublished_questions_cannot_close_two_goals():
    q,o,r=case();r['obligations'][0]['targets'][1]['question_ids']=['Q1']
    assert not evaluate_coverage(q,o,[r])['complete']
    q,o,r=case();assert not evaluate_coverage(q[:1],o,[r])['complete']

def test_later_review_with_gaps_supersedes_prior_pass():
    q,o,r=case();new=json.loads(json.dumps(r));new['obligations'][0]['status']='partial'
    assert not evaluate_coverage(q,o,[r,new])['complete']

def test_native_coverage_binds_common_conditions_and_preserves_native_identity(tmp_path):
    from aiforstudy.practice.coverage import read_native_coverage_review,statement_digest
    q,o,r=case()
    for item in q:item['source']={'course':'复变函数A','kind':'generated_variant','common_header':'参数a为正实数。'}
    bank=[{**item,'statement_sha256':statement_digest(item)} for item in q]
    job={'questions':bank,'global_question_index':'bank.json'}
    digest=hashlib.sha256(json.dumps(job,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    job['request_sha256']=digest
    r.update(schema_version='NativeKnowledgeCoverageV1',engine='codex-native-agent',reviewer='/root/reviewer',chapter_request_sha256=digest)
    for name,value in [('bank.json',bank),('request.json',job),('result.json',r)]:
        (tmp_path/name).write_text(json.dumps(value,ensure_ascii=False))
    native=read_native_coverage_review(tmp_path/'result.json',tmp_path/'request.json',tmp_path/'bank.json')
    assert native['engine']=='codex-native-agent'
    assert evaluate_coverage(q,o,[native])['complete']
    q[1]['source']['common_header']='参数a可以为零。'
    assert not evaluate_coverage(q,o,[native])['complete']

def test_native_cross_chapter_coverage_uses_saved_reviewer_hash(tmp_path):
    from aiforstudy.practice.coverage import read_native_coverage_review,statement_digest
    q,o,r=case()
    q[1]['unit_id']='CH02'
    bank=[{**item,'statement_sha256':statement_digest(item)} for item in q]
    job={'questions':bank[:1],'global_question_index':'bank.json'}
    digest=hashlib.sha256(json.dumps(job,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    job['request_sha256']=digest
    r.update(schema_version='NativeKnowledgeCoverageV1',engine='codex-native-agent',reviewer='/root/reviewer',chapter_request_sha256=digest,
             references=[{'question_id':'Q2','statement_sha256':bank[1]['statement_sha256']}])
    for name,value in [('bank.json',bank),('request.json',job),('result.json',r)]:
        (tmp_path/name).write_text(json.dumps(value,ensure_ascii=False))
    load=lambda:read_native_coverage_review(tmp_path/'result.json',tmp_path/'request.json',tmp_path/'bank.json')
    assert evaluate_coverage(q,o,[load()])['complete']
    bank[1]['stem']='不同章节后来更换成无关题'
    bank[1]['statement_sha256']=statement_digest(bank[1])
    (tmp_path/'bank.json').write_text(json.dumps(bank,ensure_ascii=False))
    with pytest.raises(ValueError,match='reference statement changed'):
        load()

def test_unpinned_cross_chapter_question_cannot_inherit_a_bank_hash(tmp_path):
    from aiforstudy.practice.coverage import read_native_coverage_review,statement_digest
    q,o,r=case()
    bank=[{**item,'statement_sha256':statement_digest(item)} for item in q]
    job={'questions':bank[:1],'global_question_index':'bank.json'}
    digest=hashlib.sha256(json.dumps(job,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    job['request_sha256']=digest
    r.update(schema_version='NativeKnowledgeCoverageV1',engine='codex-native-agent',reviewer='/root/reviewer',chapter_request_sha256=digest)
    for name,value in [('bank.json',bank),('request.json',job),('result.json',r)]:
        (tmp_path/name).write_text(json.dumps(value,ensure_ascii=False))
    native=read_native_coverage_review(tmp_path/'result.json',tmp_path/'request.json',tmp_path/'bank.json')
    assert 'Q2' not in native['question_statement_hashes']
    assert not evaluate_coverage(q,o,[native])['complete']
