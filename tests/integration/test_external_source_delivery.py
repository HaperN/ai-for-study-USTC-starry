"""Reused preparation remains trusted by location and exact source dependencies."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil

import pytest
from PIL import Image

from aiforstudy.core.digest import digest_file
from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.core.source_materials import write_source_materials
from aiforstudy.errors import ContractError
from aiforstudy.pipeline.delivery_stage import delivery_stage, validate_reader_delivery
from aiforstudy.pipeline.graph import Node
from aiforstudy.pipeline.media_stages import render_stage
from aiforstudy.pipeline.services import StageContext
from support.runner_v5 import run_book, UNIT


def _add_material(parse):
    image = parse/'images/question.png'
    image.parent.mkdir(exist_ok=True)
    Image.new('RGB', (2, 2), 'red').save(image)
    lines = ['[第 1 页]', '第一章 甲', '甲之一节', '教学原文。', '第2章 综合习题',
             '1. 保留原题。 ![](images/question.png)']
    (parse/'document.md').write_text('\n'.join(lines)+'\n')
    entries = [dict(entry_id=eid, toc_id=toc_id, number=number, title=title,
                    level=level, order=order, category=kind, pdf_page=1,
                    printed_page='1', toc_page=99)
               for eid,toc_id,number,title,level,order,kind in [
                   ('T-0001','T-0001','第1章','第一章 甲',1,1,'teaching'),
                   ('T-0002','T-0002','1.1','甲之一节',2,2,'teaching'),
                   ('X1',None,'2','综合习题',1,3,'exercise')]]
    write_json_atomic(parse/'toc.excluded.json', {'schema_version':'1.0',
        'all_entries':entries, 'entries':[entries[-1]]})
    write_json_atomic(parse/'headings.json', {'headings':[
        {'line':2,'text':lines[1],'level':1,'page':1,'region':'body'},
        {'line':3,'text':lines[2],'level':2,'page':1,'region':'body'},
        {'line':5,'text':lines[4],'level':1,'page':1,'region':'exercise'}]})
    manifest=write_source_materials(parse)
    assert manifest['materials'][0]['state']=='recorded'
    return image


def test_real_render_and_delivery_accept_reused_parse_and_detect_changed_original(tmp_path):
    result,store,engine,transport=run_book(tmp_path,until='interaction')
    assert result.status=='succeeded',result.recovery_hint
    parse=tmp_path/'earlier-run/work/prepared-parse'
    shutil.copytree(engine.services.context.parse,parse)
    image=_add_material(parse)
    context=replace(engine.services.context,parse=parse)
    services=replace(engine.services,context=context)
    ctx=StageContext(node=Node('render:CH01','render','CH01'),request=engine.request,
        plan=engine.plan,workspace=engine.services.workspace,context=context,services=services)
    calls=len(transport.calls)
    rendered=render_stage(ctx)
    assert rendered.execution_state.value=='succeeded',rendered.error
    payload=read_json(store.work/'products/render/CH01.json')['payload']
    snapshot=payload['reader_snapshot']
    assert snapshot['source_hashes'][str(image)]==digest_file(image)
    exported=Path(payload['out_dir'])
    data=json.loads((exported/'data/data.js').read_text().split('window.__DATA__ = ',1)[1].rstrip().removesuffix(';'))
    assert data['source_materials']['material:X1']['raw_markdown'].endswith('1. 保留原题。 ![](images/question.png)\n')
    assert (exported/data['source_assets']['images/question.png']).read_bytes()==image.read_bytes()
    assert validate_reader_delivery(ctx)['CH01']['units']==[UNIT]
    delivered=delivery_stage(replace(ctx,node=Node('delivery','delivery','book')))
    assert delivered.execution_state.value=='succeeded',delivered.error
    assert len(transport.calls)==calls
    Image.new('RGB',(2,2),'blue').save(image)
    with pytest.raises(ContractError,match='reader source changed'):
        validate_reader_delivery(ctx)


@pytest.mark.parametrize('location',['outside','inside_parse','sibling_prefix','symlink'])
def test_source_validation_rejects_unlisted_absolute_files(tmp_path,location):
    from aiforstudy.blocks.render.snapshot_sources import validate_snapshot_sources
    parse=tmp_path/'parse'; parse.mkdir()
    run=tmp_path/'run'; run.mkdir()
    output=run/'snapshot'; (output/'data').mkdir(parents=True)
    (output/'data/data.js').write_text('window.__DATA__ = '+json.dumps({'source_materials':{},'units':{}})+';')
    extra=(parse/'secret.json' if location=='inside_parse' else
           tmp_path/'parse-other'/'document.md' if location=='sibling_prefix' else tmp_path/'private.txt')
    extra.parent.mkdir(exist_ok=True); extra.write_text('unrelated')
    if location=='symlink':
        (parse/'document.md').symlink_to(extra); extra=parse/'document.md'
    snapshot={'output_dir':str(output),'source_hashes':{str(extra):digest_file(extra)}}
    with pytest.raises(ContractError,match='reader source path is invalid'):
        validate_snapshot_sources(snapshot,run,parse)


def test_source_validation_accepts_only_referenced_inline_original_images(tmp_path):
    from aiforstudy.blocks.render.snapshot_sources import validate_snapshot_sources
    parse=tmp_path/'parse'; parse.mkdir()
    run=tmp_path/'run'; run.mkdir()
    output=run/'snapshot'; (output/'data').mkdir(parents=True)
    image=parse/'inline.png'; Image.new('RGB',(2,2),'green').save(image)
    data={'source_materials':{},'units':{'U':{'reader_document':{'blocks':[
        {'group':'exercise_regions','raw_text':'原题。 ![](inline.png)'}]}}}}
    (output/'data/data.js').write_text('window.__DATA__ = '+json.dumps(data)+';')
    snapshot={'output_dir':str(output),'source_hashes':{str(image):digest_file(image)}}
    assert validate_snapshot_sources(snapshot,run,parse)
    changed=deepcopy(snapshot); changed['source_hashes'][str(parse/'unreferenced.png')]='missing'
    with pytest.raises(ContractError,match='reader source path is invalid'):
        validate_snapshot_sources(changed,run,parse)


@pytest.mark.parametrize('invalid', ['missing_product', 'unrecorded_product', 'stale_source',
    'altered_display', 'symlink_product', 'path_escape', 'symlink_image'])
def test_formatted_image_allowlist_requires_current_run_product_and_safe_path(tmp_path, invalid):
    from aiforstudy.blocks.render.snapshot_sources import validate_snapshot_sources
    parse = tmp_path/'parse'; parse.mkdir()
    run = tmp_path/'run'; run.mkdir()
    output = run/'snapshot'; (output/'data').mkdir(parents=True)
    image = parse/'fixed.png'; Image.new('RGB', (2, 2), 'green').save(image)
    raw = '原文 ! [题图](fixed.png)'
    markdown = '原文 ![题图](fixed.png)'
    source_sha = 'sha256:' + hashlib.sha256(raw.encode()).hexdigest()
    if invalid == 'path_escape':
        image = tmp_path/'private.png'; Image.new('RGB', (2, 2), 'red').save(image)
        markdown = '![](../private.png)'
    elif invalid == 'symlink_image':
        private = tmp_path/'private.png'; image.rename(private); image.symlink_to(private)
    data = {'source_materials': {'material:X': {'raw_markdown': raw,
        'source_sha256': source_sha, 'display_markdown': markdown,
        'formatting': {'status': 'formatted', 'notes': []}}}, 'units': {}}
    (output/'data/data.js').write_text('window.__DATA__ = '+json.dumps(data)+';')
    relative = 'work/products/source_format/book.json'
    product = run/relative
    write_json_atomic(product, {'_meta': {}, 'payload': {'materials': {'material:X': {
        'source_sha256': 'sha256:old' if invalid == 'stale_source' else source_sha,
        'status': 'formatted', 'display_markdown': '另一份整理版' if invalid == 'altered_display' else markdown,
        'notes': []}}}})
    sources = {relative: digest_file(product), str(image): digest_file(image)}
    if invalid == 'missing_product':
        product.unlink(); sources[relative] = 'missing'
    elif invalid == 'unrecorded_product':
        del sources[relative]
    elif invalid == 'symlink_product':
        other = tmp_path/'other-book.json'; product.rename(other); product.symlink_to(other)
    snapshot = {'output_dir': str(output), 'source_hashes': sources}

    with pytest.raises(ContractError, match='reader source path is invalid'):
        validate_snapshot_sources(snapshot, run, parse)
