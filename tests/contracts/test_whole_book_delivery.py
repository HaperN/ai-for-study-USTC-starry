"""Cross-chapter delivery uses the same current reader without losing teaching data."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiforstudy.core.digest import digest_file, digest_of
from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.core.review_mode import review_mode
from aiforstudy.errors import Cancelled, ContractError
from aiforstudy.pipeline.graph import Node
from aiforstudy.pipeline.media_stages import render_stage
from aiforstudy.pipeline.delivery_stage import delivery_stage
from test_diagnostic_animation_preview import animation_fixture
from test_theorem_index import row as theorem_row, save as save_theorem


def _context(tmp_path, monkeypatch, *, units=('U', 'V'), status='known_error'):
    from aiforstudy.checks import offline
    from aiforstudy.core.source_materials import write_source_materials
    from PIL import Image
    run, video_root, _ = animation_fixture(tmp_path)
    # The first chapter has an actual video; the later chapter references its
    # own named theorem. Both are immutable current-run authored products.
    save_theorem(run, theorem_row('V', 'C-V', '实虚部分别收敛判据'), audit_status=status)
    audit = read_json(run/'work/products/text_audit/U.json')['payload']
    lesson = read_json(run/'work/products/lesson/U.json')['payload']
    audit.update(content_hash=lesson['content_hash'], review_outcome={'status':'verified','reasons':[]})
    write_json_atomic(run/'work/products/text_audit/U.json', {'payload':audit})
    parse = tmp_path/'prepared-parse'
    parse.mkdir()
    lines=['[第 1 页]', '第一章 甲', '第一章原文。', '第二章 乙', '第二章原文。', '参考答案', '1. 原答案 ![](images/answer.png)']
    (parse/'document.md').write_text('\n'.join(lines)+'\n')
    entries=[{'entry_id':eid,'toc_id':eid if kind=='teaching' else None,'number':number,'title':title,
              'level':1,'order':order,'category':kind,'pdf_page':1,'printed_page':'1','toc_page':99}
             for eid,number,title,order,kind in [('T1','1','第一章 甲',1,'teaching'),
               ('T2','2','第二章 乙',2,'teaching'),('A1','','参考答案',3,'answers')]]
    write_json_atomic(parse/'toc.v2.json', {'nodes':[{'toc_id':e['entry_id'],'number':e['number'],
        'title':e['title'],'level':1,'kind':'chapter','page':{'pdf':1},'order':e['order']}
        for e in entries if e['category']=='teaching']})
    write_json_atomic(parse/'toc.excluded.json', {'schema_version':'1.0','all_entries':entries,'entries':[entries[-1]]})
    write_json_atomic(parse/'headings.json', {'headings':[
        {'line':i,'text':lines[i-1],'level':1,'page':1,'region':region} for i,region in [(2,'body'),(4,'body'),(6,'answer')]]})
    (parse/'images').mkdir()
    Image.new('RGB',(8,8),'red').save(parse/'images/answer.png')
    material=write_source_materials(parse)['materials'][0]
    write_json_atomic(run/'work/products/source_format/book.json', {'payload':{'materials':{
        material['id']:{'source_sha256':material['source_sha256'],'status':'formatted',
                        'display_markdown':'## 参考答案\n\n1. 整理后的答案 $x=1$ ![](images/answer.png)','notes':[]}}}})
    write_json_atomic(parse/'chapters.json', {'lesson_units':[
        {'unit_id':'U','chapter':'CH01'},{'unit_id':'V','chapter':'CH02'}]})
    ctx=SimpleNamespace(node=Node('render:CH01','render','CH01'),
        context=SimpleNamespace(book_id='test-book',revision='source',parse=parse,title='Reader'),
        workspace=SimpleNamespace(root=Path(__file__).resolve().parents[2]),
        services=SimpleNamespace(store=SimpleNamespace(work=run/'work',run_id=run.name),
            cancel=SimpleNamespace(checkpoint=lambda:None),control=SimpleNamespace(check=lambda:None)),
        plan=SimpleNamespace(resolved_scope=units))
    checked=[]
    def check(path, **kwargs):
        checked.append(Path(path))
        return offline.OfflineCheck('browser-execution','blocked','offline fixture: '+Path(path).name,{})
    monkeypatch.setattr(offline, 'check_browser_execution', check)
    with review_mode('balanced'):
        for chapter in dict.fromkeys('CH01' if unit=='U' else 'CH02' for unit in units):
            ctx.node=Node('render:'+chapter,'render',chapter)
            result=render_stage(ctx)
            assert result.execution_state.value=='succeeded', result.error
    ctx.node=Node('delivery','delivery','book')
    return ctx, run, video_root, checked


def _data(out):
    raw=(out/'data/data.js').read_text()
    return json.loads(raw.split('window.__DATA__ = ',1)[1].rstrip().removesuffix(';'))


def test_cross_chapter_delivery_preserves_animation_theorems_and_single_formatted_material(tmp_path, monkeypatch):
    ctx,run,video_root,checked=_context(tmp_path,monkeypatch)
    chapters={str(p):digest_file(p) for c in ('CH01','CH02') for p in (run/'work/legacy/render'/c).rglob('*') if p.is_file()}
    with review_mode('balanced'):
        result=delivery_stage(ctx)
    payload=read_json(result.outputs[0].path)['payload']
    book=run/'work/legacy/render/book'
    assert payload.get('entrypoint')==str(book/'index.html')
    assert (book/'index.html').is_file()
    receipt=read_json(run/'work/products/render/book.json')['payload']
    assert receipt['schema_version']=='ReaderProjectionV1'
    assert receipt['units']==['U','V'] and receipt['rendered_units']==['U','V']
    assert receipt['reader_snapshot']['preview_only'] is False
    assert receipt['reader_snapshot']['render_mode']=='teaching'
    assert checked==[run/'work/legacy/render/CH01',run/'work/legacy/render/CH02',book]
    assert receipt['browser_report']['detail']=='offline fixture: book'
    assert payload['review_outcome']['status']=='known_error'
    assert payload['offline_acceptance']['passed'] is False
    assert payload['book_reader']['reader_projection_hash']==receipt['reader_projection_hash']
    assert payload['members']['book/index.html']==digest_file(book/'index.html')
    assert {ref.path for ref in result.outputs} >= {book/'index.html',book/'data/data.js'}
    assert chapters=={name:digest_file(Path(name)) for name in chapters}
    data=_data(book)
    assert data['scope']==['U','V']
    assert data['theorem_index']['by_unit']['V']['C-V']['name']=='实虚部分别收敛判据'
    animation=data['animations']['U'][0]
    assert digest_file(book/animation['base']/'video.mp4')==digest_file(video_root/'video.mp4')
    assert animation['steps'][0]['caption']=='真实字幕'
    material=data['source_materials']['material:A1']
    assert '原答案' in material['raw_markdown'] and '整理后的答案' in material['display_markdown']
    assert material['formatting']['status']=='formatted'
    assert len(data['source_materials'])==1
    def flatten(rows):
        return [item for r in rows for item in [r,*flatten(r.get('children',[]))]]
    assert sum(r['id']=='material:A1' for r in flatten(data['tree']))==1
    assert digest_file(book/data['source_assets']['images/answer.png'])==digest_file(ctx.context.parse/'images/answer.png')


def test_selected_plan_order_is_the_book_scope_order(tmp_path, monkeypatch):
    ctx,run,_,_=_context(tmp_path,monkeypatch,units=('V','U'))
    with review_mode('balanced'):
        result=delivery_stage(ctx)
    assert result.execution_state.value=='succeeded'
    assert _data(run/'work/legacy/render/book')['scope']==['V','U']


def test_one_chapter_keeps_its_existing_page_and_has_no_extra_render(tmp_path, monkeypatch):
    ctx,run,_,checked=_context(tmp_path,monkeypatch,units=('U',))
    before=list(checked)
    with review_mode('balanced'):
        result=delivery_stage(ctx)
    payload=read_json(result.outputs[0].path)['payload']
    assert payload.get('entrypoint')==str(run/'work/legacy/render/CH01/index.html')
    assert checked==before
    assert not (run/'work/products/render/book.json').exists()


def test_book_failure_preserves_chapters_and_records_unfinished_entry(tmp_path,monkeypatch):
    ctx,run,_,_=_context(tmp_path,monkeypatch)
    from aiforstudy.blocks.render import diagnostics
    def failed(*args,**kwargs):
        raise ContractError('全书输出测试失败')
    monkeypatch.setattr(diagnostics,'build_reader_snapshot',failed)
    with review_mode('balanced'):
        result=delivery_stage(ctx)
    payload=read_json(result.outputs[0].path)['payload']
    assert result.execution_state.value=='succeeded'
    assert result.quality_state.value=='needs_review'
    assert payload.get('book_reader_error')=='ContractError: 全书输出测试失败'
    assert payload['partial'] is True
    assert payload['entrypoint']==str(run/'work/legacy/render/CH01/index.html')
    assert all((run/'work/legacy/render'/name/'index.html').is_file() for name in payload['chapters'])
    assert not any(name.startswith('book/') for name in payload['members'])


def test_book_cancellation_is_not_downgraded_to_optional_failure(tmp_path,monkeypatch):
    ctx,_,_,_=_context(tmp_path,monkeypatch)
    from aiforstudy.blocks.render import diagnostics
    def cancelled(*args,**kwargs):
        raise Cancelled('用户停止')
    monkeypatch.setattr(diagnostics,'build_reader_snapshot',cancelled)
    with review_mode('balanced'), pytest.raises(Cancelled,match='用户停止'):
        delivery_stage(ctx)


def test_failed_rebuild_does_not_reuse_old_book_export_or_receipt(tmp_path,monkeypatch):
    ctx,run,_,_=_context(tmp_path,monkeypatch)
    with review_mode('balanced'):
        first=delivery_stage(ctx)
    assert read_json(first.outputs[0].path)['payload']['book_reader']
    from aiforstudy.blocks.render import diagnostics
    def fail(*args,**kwargs):
        raise ContractError('当前重建失败')
    monkeypatch.setattr(diagnostics,'build_reader_snapshot',fail)
    with review_mode('balanced'):
        result=delivery_stage(ctx)
    payload=read_json(result.outputs[0].path)['payload']
    assert payload['book_reader'] is None
    assert not (run/'work/products/render/book.json').exists()
    assert not (run/'work/legacy/render/book').exists()
    assert not any(name.startswith('book/') for name in payload['members'])


def test_json_only_book_keeps_audits_not_run_and_does_not_call_browser(tmp_path,monkeypatch):
    ctx,run,_,checked=_context(tmp_path,monkeypatch)
    before=list(checked)
    with review_mode('json_only'):
        result=delivery_stage(ctx)
    payload=read_json(result.outputs[0].path)['payload']
    assert payload['entrypoint']==str(run/'work/legacy/render/book/index.html')
    assert checked==before
    assert result.quality_state.value=='not_checked'
    assert payload['review_status']=='not_run'
    assert payload['book_reader']['browser_report']['status']=='not_run'
    assert payload['book_reader']['review_outcome']['status']=='unverified'
