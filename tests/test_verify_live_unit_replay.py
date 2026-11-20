"""Offline integrity tests for the bounded live entry; never call a model/GPU."""
import importlib.util
from pathlib import Path
import pytest
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.core.digest import digest_file
from aiforstudy.core.review_mode import review_mode
from aiforstudy.blocks.content.content_contracts import content_digest
from aiforstudy.blocks.content.source_context import source_context_from_frozen, SourceContext
from aiforstudy.blocks.content.generation import _validate_author_output
from aiforstudy.blocks.content.content_policy import load_content_policy

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('live_entry',ROOT/'tools/verify_live_unit.py')
entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)

@pytest.fixture
def saved(tmp_path):
 with review_mode('balanced'):
  source=source_context_from_frozen(dict(book_id='B',unit_id='U',source_text='原文',original_text='U.md',original_sha256='original',source_inputs={},unit={'title':'教材'},chapters={},concept_index={})).as_dict()
  raw={'content':{'title':'教材','body_markdown':'定义与条件保持可见。'},'media_requirements':{'requests':[],'decisions':[]}}
  authored=_validate_author_output(raw,SourceContext.model_validate(source))
  write_json_atomic(tmp_path/'author-journal.json',{'attempts':[{'status':'accepted','response':raw,'response_hash':content_digest({'response':raw})}]})
  write_json_atomic(tmp_path/'exchanges/001.input.json',{'role':'lesson'})
  write_json_atomic(tmp_path/'exchanges/001.output.json',{'data':raw})
  policy=load_content_policy().digest
  report={'decision':'accepted','exchanges':[{'binding':{'content_hash':content_digest(authored['content']),'source_context_hash':source['source_context_hash'],'policy_digest':policy,'batch_index':0}}]}
 return tmp_path,source,authored,report,policy

@pytest.mark.parametrize('change',['content','media','policy','source','missing_exchange'])
def test_rejects_unreviewed_cached_changes(saved,change):
 out,source,authored,report,policy=saved
 if change=='content':authored['content']['reader_intro']='被改写且未审核'
 if change=='media':authored['media_requirements']['requests']=[{'request_key':'unreviewed','kind':'image'}]
 if change=='policy':policy='changed-policy'
 if change=='source':source['source_context_hash']='changed-source'
 if change=='missing_exchange':report['exchanges']=[]
 with review_mode('balanced'),pytest.raises(ValueError,match='fresh output directory'):
  entry.validate_cached_artifacts(out,authored,source,report,policy)


def test_valid_replay_allows_renderer_only_changes_without_rewriting_evidence(saved):
 out,source,authored,report,policy=saved
 before={p:p.read_bytes() for p in out.rglob('*') if p.is_file()}
 (out/'reader').mkdir();(out/'reader/old.js').write_text('old renderer')
 with review_mode('balanced'):
  entry.validate_cached_artifacts(out,authored,source,report,policy)
  (out/'reader/old.js').write_text('new renderer')
  entry.validate_cached_artifacts(out,authored,source,report,policy)
 assert all(p.read_bytes()==value for p,value in before.items())


def test_changed_pdf_rejected_before_any_output_write(tmp_path):
 pdf=tmp_path/'source.pdf';pdf.write_bytes(b'old PDF')
 raw=tmp_path/'raw.md';raw.write_text('same OCR')
 page=tmp_path/'page.png';page.write_bytes(b'same page')
 out=tmp_path/'out';out.mkdir()
 write_json_atomic(out/'provenance.json',{'pdf_sha256':digest_file(pdf),'images':{str(page):digest_file(page)}})
 before=(out/'provenance.json').read_bytes();pdf.write_bytes(b'new PDF')
 with pytest.raises(ValueError,match='PDF.*fresh output directory'):
  entry.check_frozen_sources(out,pdf,raw,[str(page)])
 assert (out/'provenance.json').read_bytes()==before
 assert list(out.iterdir())==[out/'provenance.json']


def test_tampered_raw_response_is_rejected(saved):
 out,source,authored,report,policy=saved
 journal=entry.read_json(out/'author-journal.json')
 journal['attempts'][0]['response']['content']['body_markdown']='篡改原响应'
 write_json_atomic(out/'author-journal.json',journal)
 with review_mode('balanced'),pytest.raises(ValueError,match='fresh output directory'):
  entry.validate_cached_artifacts(out,authored,source,report,policy)


def test_main_pdf_preflight_precedes_execution_and_all_output_writes(tmp_path,monkeypatch):
 from types import SimpleNamespace
 import sys
 w=tmp_path/'workspace';pdf=w/'data/raw/复变函数.pdf';pdf.parent.mkdir(parents=True);pdf.write_bytes(b'old')
 raw=w/'data/parse/complex-analysis-v2/original_text/ch01_s01_ss02.md';raw.parent.mkdir(parents=True);raw.write_text('original')
 pages=tmp_path/'pages';pages.mkdir()
 images=[]
 for i in (12,13,14):
  p=pages/f'physical-{i:03}.png';p.write_bytes(b'page');images.append(str(p))
 out=tmp_path/'out';out.mkdir()
 write_json_atomic(out/'provenance.json',{'pdf_sha256':digest_file(pdf),'images':{p:digest_file(Path(p)) for p in images}})
 before=(out/'provenance.json').read_bytes();pdf.write_bytes(b'new')
 monkeypatch.setattr(entry,'open_workspace',lambda *_:SimpleNamespace(root=w))
 monkeypatch.setattr(entry,'load_config',lambda *_:None)
 def no_execution(*_):raise AssertionError('Execution must not start before PDF identity check')
 monkeypatch.setattr(entry,'ExecutionControl',no_execution)
 monkeypatch.setattr(sys,'argv',['verify','--workspace',str(w),'--out',str(out),'--pages',str(pages)])
 with pytest.raises(ValueError,match='PDF.*fresh output directory'):entry.main()
 assert (out/'provenance.json').read_bytes()==before
 assert list(out.iterdir())==[out/'provenance.json']
