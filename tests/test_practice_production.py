import json
from pathlib import Path

import pytest

from aiforstudy.practice.production import produce_batch
from aiforstudy.practice.catalog import content_digest,validate_review_files
from test_practice_catalog import question

def fake_call(ws,questions,out,mode,**kw):
 out=Path(out);out.mkdir(parents=True,exist_ok=True)
 if mode in ('author','blind'):
  rows=[{'id':q['id'],'status':'solved','answer':'三个单位根','solution':'由极形式得到三个根。','pitfalls':['漏根'],'issues':[]} for q in questions]
  d={'ok':True,'mode':mode,'model':'fixture','request_sha256':mode,'payload':{'questions':rows}}
 else:
  rows=[{'id':q['id'],'status':'accepted','issues':[],'reason':'逐问核对','author_content_sha256':content_digest(q)} for q in questions]
  d={'ok':True,'mode':mode,'model':'fixture','request_sha256':mode,'payload':{'questions':rows},'bound_comparisons':rows}
 (out/'response.json').write_text(json.dumps(d,ensure_ascii=False));return d

def test_complete_pipeline_writes_evidence_bound_catalog(tmp_path):
 q=question();q.pop('answer');q.pop('solution')
 r=produce_batch(tmp_path,[q],tmp_path/'run',caller=fake_call)
 assert r['accepted']==1 and r['blocked']==0
 data=json.loads((tmp_path/'run/catalog.json').read_text());validate_review_files(data,tmp_path/'run')

def test_rejected_comparison_cannot_publish(tmp_path):
 q=question()
 def disagree(ws,questions,out,mode,**kw):
  d=fake_call(ws,questions,out,mode,**kw)
  if mode=='compare':
   d['bound_comparisons'][0]['status']='needs_revision';d['bound_comparisons'][0]['issues']=['条件缺失']
  return d
 r=produce_batch(tmp_path,[q],tmp_path/'run',caller=disagree)
 assert r['accepted']==0 and r['blocked']==1
 assert json.loads((tmp_path/'run/catalog.json').read_text())==[]

def test_unsupported_diagram_input_stops_before_any_model_call(tmp_path):
 q=question();q['images']=['required-figure.png']
 def forbidden(*args,**kwargs):raise AssertionError('must not call a model without its required figure')
 with pytest.raises(ValueError,match='image'):produce_batch(tmp_path,[q],tmp_path/'run',caller=forbidden)

def test_unresolved_solver_issues_are_not_published(tmp_path):
 q=question()
 def unresolved(ws,questions,out,mode,**kw):
  d=fake_call(ws,questions,out,mode,**kw)
  if mode=='author':d['payload']['questions'][0]['issues']=['缺少条件']
  return d
 r=produce_batch(tmp_path,[q],tmp_path/'run',caller=unresolved)
 assert r['accepted']==0 and r['blocked']==1

def test_accepted_with_correction_is_not_published(tmp_path):
 q=question()
 def contradictory(ws,questions,out,mode,**kw):
  d=fake_call(ws,questions,out,mode,**kw)
  if mode=='compare':d['bound_comparisons'][0]['corrected_answer']='应改成其他答案'
  return d
 r=produce_batch(tmp_path,[q],tmp_path/'run',caller=contradictory)
 assert r['accepted']==0 and r['blocked']==1


@pytest.mark.parametrize(('field', 'markup'), [
    ('stem', '见图 ![必要题图][diagram]\n\n[diagram]: assets/diagram.svg'),
    ('stem', '见图 <IMG src="assets/diagram.svg">'),
    ('common_header', '共用图 ![曲线](assets/curve.svg)'),
    ('common_header', '共用图 ![曲线][]\n\n[曲线]: assets/curve.svg'),
])
def test_image_markup_is_rejected_before_any_stage(tmp_path, field, markup):
    q = question()
    if field == 'common_header':
        q['source'][field] = markup
    else:
        q[field] = markup

    def offline_call(*args, **kwargs):
        pytest.fail('text-only production dispatched an image-dependent question')

    output = tmp_path / 'run'
    with pytest.raises(ValueError, match='image'):
        produce_batch(tmp_path, [q], output, caller=offline_call)
    assert not output.exists()


@pytest.mark.parametrize('stage', ['author', 'blind'])
def test_generated_image_material_stops_before_comparison(tmp_path, stage):
    calls = []

    def offline_call(workspace, rows, path, mode, **kwargs):
        calls.append(mode)
        if mode == 'compare':
            pytest.fail('text-only comparison dispatched image-dependent material')
        result = fake_call(workspace, rows, path, mode, **kwargs)
        if mode == stage:
            result['payload']['questions'][0]['solution'] = '必要推导 <IMG src="assets/plot.svg">'
        return result

    output = tmp_path / 'run'
    with pytest.raises(ValueError, match='image'):
        produce_batch(tmp_path, [question()], output, caller=offline_call)
    assert calls == ['author', 'blind']
    assert not (output / 'catalog.json').exists()
