#!/usr/bin/env python3
"""Bounded real excerpt verification; does not assert whole-book preparation."""
import argparse,json,sys,traceback
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from aiforstudy.core.workspace import open_workspace
from aiforstudy.core.jsonio import read_json,write_json_atomic
from aiforstudy.core.digest import digest_file
from aiforstudy.providers.factory import configured_providers
from aiforstudy.providers.llm import load_config
from aiforstudy.providers.gateway import Gateway
from aiforstudy.pipeline.gateway_client import GatewayClient
from aiforstudy.pipeline.execution_control import ExecutionControl,load_policy
from aiforstudy.core.review_mode import review_mode
from aiforstudy.blocks.content.source_context import source_context_from_frozen
from aiforstudy.blocks.content.generation import generate_content
from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.review import review_content

def check_frozen_sources(output, pdf, raw, images):
 """Read-only preflight: PDF drift must fail before any run/output write."""
 if (output/'provenance.json').exists():
  prior=read_json(output/'provenance.json')
  if prior.get('pdf_sha256')!=digest_file(pdf):
   raise ValueError('Frozen PDF changed; use a fresh output directory')
  if prior.get('images')!={i:digest_file(Path(i)) for i in images}:
   raise ValueError('Frozen source pages changed; use a fresh output directory')
 if (output/'raw-source.md').exists() and digest_file(output/'raw-source.md')!=digest_file(raw):
  raise ValueError('Frozen source changed; use a fresh output directory')


def validate_cached_artifacts(output, authored, source, report, policy_digest):
 """Bind authored text/media to the preserved response and its real review."""
 from aiforstudy.blocks.content.content_contracts import content_digest
 from aiforstudy.blocks.content.source_context import SourceContext
 from aiforstudy.blocks.content.generation import _validate_author_output
 def reject():
  raise ValueError('Frozen author/review binding changed or missing; use a fresh output directory')
 journal=output/'author-journal.json'
 if not journal.is_file():reject()
 attempts=read_json(journal).get('attempts',[])
 if not attempts or attempts[-1].get('status')!='accepted':reject()
 attempt=attempts[-1];raw=attempt.get('response')
 if attempt.get('response_hash')!=content_digest({'response':raw}):reject()
 preserved=False
 for path in (output/'exchanges').glob('*.input.json'):
  answer=path.with_name(path.name.replace('.input.json','.output.json'))
  if read_json(path).get('role')=='lesson' and answer.is_file():
   if read_json(answer).get('data')==raw:preserved=True
 if not preserved:reject()
 expected=_validate_author_output(raw,SourceContext.model_validate(source))
 if content_digest(expected)!=content_digest(authored):reject()
 if report is not None:
  expected_binding={'content_hash':content_digest(authored['content']),
   'source_context_hash':source['source_context_hash'],'policy_digest':policy_digest}
  exchanges=report.get('exchanges')
  if not isinstance(exchanges,list) or not exchanges:reject()
  for exchange in exchanges:
   binding=exchange.get('binding',{})
   if any(binding.get(k)!=v for k,v in expected_binding.items()):reject()


def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--workspace',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
 p.add_argument('--pages',type=Path,required=True);p.add_argument('--source-only',action='store_true');p.add_argument('--media',action='store_true')
 a=p.parse_args();o=a.out.resolve()
 w=open_workspace(a.workspace);cfg=load_config(w)
 parse=w.root/'data/parse/complex-analysis-v2'
 raw=parse/'original_text/ch01_s01_ss02.md';pdf=w.root/'data/raw/复变函数.pdf'
 images=[str(a.pages/f'physical-{i:03}.png') for i in (12,13,14)]
 check_frozen_sources(o,pdf,raw,images)
 o.mkdir(parents=True,exist_ok=True)
 control=ExecutionControl(load_policy(w,'release'));control.bind(o/'execution.json',run_id=o.name)
 transport=configured_providers(SimpleNamespace(profile=SimpleNamespace(value='release')),w).llm
 def observe(event,data):
  with (o/'calls.jsonl').open('a') as f:f.write(json.dumps({'event':event,**data},ensure_ascii=False)+'\n')
  print(event,data.get('role',''),flush=True)
 gateway=Gateway(transport=transport,roles=cfg.roles,control=control,cache_dir=o/'cache',book_id='complex-analysis-v2',call_observer=observe)
 class Client(GatewayClient):
  def chat_json(self,messages,*,images=None,**kw):
   if images is not None:kw['images']=images
   n=len(list((o/'exchanges').glob('*.input.json')))+1;(o/'exchanges').mkdir(exist_ok=True)
   write_json_atomic(o/'exchanges'/f'{n:03}.input.json',{'role':self.role,'messages':messages,'images':kw.get('images',[])})
   r=super().chat_json(messages,**kw);write_json_atomic(o/'exchanges'/f'{n:03}.output.json',r);return r
 def client(role):
  r=cfg.role(role);return Client(gateway,role=role,model=r.model,effort=r.effort,stage=role,unit_key='CH01-S01-SS02',bucket='content')
 if not (o/'raw-source.md').exists():(o/'raw-source.md').write_bytes(raw.read_bytes())
 if not (o/'provenance.json').exists():
  write_json_atomic(o/'provenance.json',{'pdf_path':str(w.root/'data/raw/复变函数.pdf'),'pdf_sha256':digest_file(w.root/'data/raw/复变函数.pdf'),'scope':'bounded real textbook excerpt, not whole-book preparation','source_path':str(raw),'source_sha256':digest_file(raw),'physical_pages':[12,13,14],'images':{i:digest_file(Path(i)) for i in images},'wholebook_preparation':False})
 repaired=o/'source-repair.json'
 if not repaired.exists():
  r=client('source_repair').chat_json([{'role':'system','content':'你负责忠实教材校勘。只输出JSON。以实际页图为依据转录1.1.2共轭复数完整小节（性质1至例5，不含后续小节）。保留原书印刷错误，不静默改正确；在issues数组逐条区分印刷差异与OCR错误，给出正确数学及依据。输出 {"transcription": "完整Markdown", "issues": [{"source_text":"", "kind":"print_difference或ocr_error", "correction":"", "reason":""}]}。尤其物理13页例2 y≠±i 与例3虚部分母可能是书上印刷差异。'}, {'role':'user','content':raw.read_text()}],images=images,tag='excerpt-page-transcription')
  write_json_atomic(repaired,r['data'])
 if a.source_only:return 0
 repair=read_json(repaired);text=repair['transcription'];(o/'source-transcription.md').write_text(text)
 part=read_json(parse/'concepts.parts/CH01-S01-SS02.json');unit=part['unit']
 frozen={'book_id':'complex-analysis-v2','unit_id':unit['unit_id'],'source_text':text,'original_text':'source-transcription.md','original_sha256':digest_file(o/'source-transcription.md'),'source_inputs':{'raw':digest_file(raw),'page_transcription':digest_file(repaired)},'unit':unit,'chapters':{'lesson_units':[unit]},'concept_index':{'concepts':[]}}
 with review_mode('balanced'):
  source=source_context_from_frozen(frozen).as_dict()
  # Source notes are evidence, not a replacement authored lesson or a preparation receipt.
  source['unit']['source_notes']=repair['issues']
  source=source_context_from_frozen({**frozen,'unit':source['unit']}).as_dict()
  if (o/'source-context.json').exists() and read_json(o/'source-context.json')!=source:raise ValueError('Frozen context changed; use a fresh output directory')
  write_json_atomic(o/'source-context.json',source)
  authored=read_json(o/'authored.json') if (o/'authored.json').exists() else generate_content(source,load_content_policy(),client('lesson'),correction_journal=o/'author-journal.json')
  validate_cached_artifacts(o,authored,source,None,load_content_policy().digest)
  if not (o/'authored.json').exists():write_json_atomic(o/'authored.json',authored)
  result=read_json(o/'review.json') if (o/'review.json').exists() else review_content(authored['content'],None,source,[],None,client('text_judge'),author_media=authored.get('media_requirements'),correction_journal=o/'review-journal.json')
  report=result.as_dict() if hasattr(result,'as_dict') else result
  validate_cached_artifacts(o,authored,source,report,load_content_policy().digest)
  if not (o/'review.json').exists():write_json_atomic(o/'review.json',report)
  from aiforstudy.blocks.content.reader_document import project_reader
  from aiforstudy.blocks.content.teaching_projection import compile_teaching_presentation
  from aiforstudy.blocks.render.layout import layout_from_reader_media
  from aiforstudy.pipeline.stage_io import _legacy
  content=authored['content'];reader=project_reader(content,source['reference_catalog']).as_dict()
  presentation=compile_teaching_presentation(content,reader)
  data={'book':'真实教材节选：共轭复数','scope':[unit['unit_id']],'tree':[{'id':unit['unit_id'],'title':content['title'],'unit':True,'children':[]}],
   'units':{unit['unit_id']:{'unit_id':unit['unit_id'],'title':content['title'],'reader_document':reader,'reader_layout':layout_from_reader_media(reader,[]),'teaching_presentation':presentation,'body_markdown':content['body_markdown'],'reader_media':[],'figures':[],'concepts':[],'crumb':'真实模型节选验证；非全书发布'}},
   'figures':{},'concepts':{},'diagrams':{},'models':{},'toc_gaps':[],'concept_figures':{},'number_refs':{},'counts':{'canvas_models':0},'animations':{},'preview_only':True,'production_accepted':False}
  media_states=[]
  if a.media:
   from aiforstudy.blocks.media.code_execution import execute_code
   from aiforstudy.blocks.content.review_outcome import content_identity,media_outcome
   from aiforstudy.core.digest import digest_of
   from aiforstudy.blocks.render.diagnostics import _attach_diagnostic_animation
   from aiforstudy.blocks.render.layout import usable_location_span
   from aiforstudy.blocks.audit.evidence import resolve_json_pointer
   identity=content_identity(content,source['reference_catalog'],policy_digest=load_content_policy().digest)
   from aiforstudy.blocks.media.media_planning import plan_media_requirements
   from aiforstudy.blocks.media.author_requirements import bind_author_requirements
   record=bind_author_requirements(authored['media_requirements'],content,author_response_hash=digest_of(authored))
   plan=plan_media_requirements(content,identity,author_requirements=record,capabilities={'code_generation':'GeneratedMediaCodeV1'})
   requests=plan['requests']
   write_json_atomic(o/'media-plan.json',plan)
   write_json_atomic(o/'media-requests.json',requests)
   entries=[]
   for request in requests:
    if request['kind'] not in ('drawing','animation'):raise ValueError('Unsupported excerpt media kind '+request['kind'])
    rid=request['request_id'];directory=o/'media'/rid
    row=execute_code(request,content=content,approval=identity,out_dir=directory,client=client('figures'),reviewer=client('judge'),workspace_root=w.root)
    if row.get('resources') and any(i.get('code')=='MEDIA_CALL_FAILED' for i in row.get('validation',{}).get('issues',[])):
     from aiforstudy.blocks.media.validation import validate_media
     from aiforstudy.blocks.media.code_execution import _apply_review
     write_json_atomic(directory/'prior-review-failure.json',row)
     _apply_review(row,validate_media(request,row,client=client('judge'),workspace_root=w.root,content=content))
     write_json_atomic(directory/'result.json',row)
    write_json_atomic(directory/'verified-result.json',row)
    media_states.append({'request_id':rid,'kind':row['kind'],'state':row['state'],'render_profile':row.get('render_profile'),'review_decision':row.get('validation',{}).get('decision')})
    placement=dict(request['placement']);found,raw=resolve_json_pointer(content,placement['target_pointer'])
    if not found or not isinstance(raw,str):raise ValueError('Media placement not in authored content')
    span=usable_location_span(raw,placement.get('span'),placement.get('anchor_text'))
    placement.update(span=span,anchor_text=raw[span[0]:span[1]],field_hash=digest_of(raw),anchor_id=rid)
    entry={'request_id':rid,'kind':request['kind'],'figure_id':None,'caption':request.get('caption',''),'alt_text':request.get('alt_text',''),'placement':placement,'source_hash':digest_of(row),'review_outcome':media_outcome(row)}
    paths={r['relative_path']:Path(row['source_dir'])/r['relative_path'] for r in row.get('resources',[])}
    if row.get('animation'):
     animation=_attach_diagnostic_animation(o/'reader',unit=unit['unit_id'],book_id=content['book_id'],animation=row['animation'],sources=paths,profile=row.get('render_profile','final'))
     animation['placement']=placement;data['animations'].setdefault(unit['unit_id'],[]).append(animation)
    else:
     svg=next((paths[r['relative_path']] for r in row.get('resources',[]) if r['kind']=='svg'),None)
     if svg:
      data['figures'][rid]={'id':rid,'source':'generated','svg':_legacy('build').inline_svg(svg.read_text(),rid),'caption':entry['caption']};entry['figure_id']=rid
     else:
      import base64
      raster=next((paths[r['relative_path']] for r in row.get('resources',[]) if r['kind'] in ('image','png')),None)
      if raster:
       data['figures'][rid]={'id':rid,'file':'data:image/png;base64,'+base64.b64encode(raster.read_bytes()).decode(),'caption':entry['caption']};entry['figure_id']=rid
    entries.append(entry)
   data['units'][unit['unit_id']]['reader_media']=entries
   data['units'][unit['unit_id']]['reader_layout']=layout_from_reader_media(reader,entries)
  _legacy('build').write_reader_site(data,o/'reader',book=data['book'])
  write_json_atomic(o/'reader-document.json',reader);write_json_atomic(o/'teaching-presentation.json',presentation)
  verified=report['decision']=='accepted' and all(r['state']=='fulfilled' and r['review_decision']=='accepted' for r in media_states)
  write_json_atomic(o/'verification.json',{'scope':'real excerpt; not whole-book preparation or CLI publication','preview_only':True,'production_accepted':False,'text_review':report['decision'],'media_requested':bool(a.media),'media':media_states,'verified_requested_stages':verified,'reader':str(o/'reader/index.html')})
 return 0 if verified else 1
if __name__=='__main__':
 try:sys.exit(main())
 except Exception:traceback.print_exc();sys.exit(1)
