from copy import deepcopy
from pathlib import Path
import json
import subprocess
import pytest
from aiforstudy.core.digest import digest_of
from aiforstudy.errors import ContractError
from aiforstudy.core.review_mode import review_mode
from test_content_v4_reader_contract import candidate, catalog

ROOT = Path(__file__).resolve().parents[2]

def api():
    assert (ROOT/'src/aiforstudy/blocks/render/layout.py').is_file(), 'ReaderLayout implementation missing'
    from aiforstudy.blocks.render.layout import build_reader_layout
    return build_reader_layout

def document(raw):
    from aiforstudy.blocks.content.reader_document import project_reader
    c = candidate(); c['body_markdown'] = raw
    return project_reader(c,catalog()).as_dict()

def mount(doc, raw, anchor, source='M', kind='media'):
    start = raw.index(anchor)
    return {'unit_id':'U','owner_id':'U','target_pointer':'/body_markdown','source_kind':kind,'source_id':source,'source_hash':digest_of(source),'anchor_id':'anchor-'+source,'requested_span_cp':[start,start+len(anchor)],'edge':'after','field_hash':digest_of(raw),'anchor_text':anchor}

def test_layout_codepoint_mapping_and_immutable_reader():
    build = api()
    raw = '中文😀𝒜[[CONCEPT:C]]，\\(x=1\\)。\r\n\r\n- 第一项\r\n\r\n  含有$$y=2$$的第二段\r\n\r\n- 最后一项'
    doc = document(raw); before = deepcopy(doc)
    row = mount(doc,raw,'第二段')
    layout = build(doc,[row],workspace_root=ROOT,display_policy={'version':'test'})
    chosen = next(b for b in layout['blocks'] if b['block_id']==layout['mounts'][0]['resolved_block_id'])
    assert chosen['token_type']=='list'
    assert raw[chosen['source_start_cp']:chosen['source_end_cp']].endswith('最后一项')
    assert layout['mounts'][0]['requested_span_cp']==row['requested_span_cp']
    assert doc==before and layout['reader_document_hash']==digest_of(doc)

def test_layout_stable_order_and_tamper_rejection():
    build = api(); raw='锚句。\n\n尾段'; doc=document(raw)
    rows=[mount(doc,raw,'锚句。',source=x) for x in ('image1','image2','animation')]
    layout=build(doc,rows,workspace_root=ROOT,display_policy={})
    assert [r['source_id'] for r in layout['mounts']]==['image1','image2','animation']
    assert [r['order_at_anchor'] for r in layout['mounts']]==[0,1,2]
    assert build(doc,rows,workspace_root=ROOT,display_policy={})==layout
    for field,value in [('field_hash','stale'),('owner_id','other')]:
        changed=deepcopy(rows);changed[0][field]=value
        with pytest.raises(ContractError,match='/body_markdown.*image1'): build(doc,changed,workspace_root=ROOT,display_policy={})

def test_layout_rejects_ambiguous_anchor_without_span():
    build=api();raw='重复。重复。';doc=document(raw);row=mount(doc,raw,'重复。');row.pop('requested_span_cp')
    with pytest.raises(ContractError,match='occurrence|span'): build(doc,[row],workspace_root=ROOT,display_policy={})


@pytest.mark.parametrize('kind', ['media', 'interaction'])
@pytest.mark.parametrize('target', ['title', 'raw_markdown', 'raw_source'])
def test_layout_rejects_mounts_on_read_only_source_material(kind, target):
    from aiforstudy.blocks.content.reader_document import project_reader
    build = api()
    raw = '习题\n1. 证明原书命题。\n'
    content = candidate()
    content['exercise_regions'] = [dict(id='ER-1', title='习题', kind='exercise',
        source_lines=[1, 2], raw_markdown=raw, source_sha256=digest_of(raw))]
    doc = project_reader(content, catalog()).as_dict()
    pointer = '/body_markdown' if target == 'raw_source' else '/exercise_regions/0/' + target
    field = next(b for b in doc['blocks'] if b['json_pointer'] == pointer)
    if target == 'raw_source':
        field['raw_source'] = True
    text = field['raw_text']
    row = mount(doc, text, text[:2], kind=kind)
    row.update(target_pointer=pointer, owner_id=field['owner_id'])
    # Source prose stays readable with a normal layout. A mount must fail at
    # the host boundary instead of being accepted and then hidden by the UI.
    assert build(doc, [], workspace_root=ROOT, display_policy={})['mounts'] == []
    with pytest.raises(ContractError, match='read-only source material'):
        build(doc, [row], workspace_root=ROOT, display_policy={})


def test_codepoint_layout_reaches_real_renderer_without_losing_visible_text():
    build=api()
    raw='中文😀𝒜[[CONCEPT:C|概念𝒜]]，\\(x=1\\)。\r\n\r\n- 第一项\r\n\r\n  含有$$y=2$$的第二段\r\n\r\n- 最后一项\r\n\r\n尾段[链接][target]\r\n\r\n[target]: https://example.org'
    doc=document(raw);layout=build(doc,[mount(doc,raw,'第二段')],workspace_root=ROOT,display_policy={})
    script="""const fs=require('fs'),input=JSON.parse(fs.readFileSync(0,'utf8'));
      const marked=require('./blocks/render/vendor/marked.min.js'),katex=require('./blocks/render/vendor/katex.min.js');
      const r=require('./blocks/render/assets/render-core.js').createRenderer({marked,katex});
      process.stdout.write(r.readerDocumentHtml(input.doc,'body',[{request_id:'M',kind:'animation'}],input.layout));"""
    result=subprocess.run(['node','-e',script],cwd=ROOT,input=json.dumps({'doc':doc,'layout':layout}),text=True,capture_output=True,check=True)
    html=result.stdout
    assert '中文😀𝒜' in html and '概念𝒜' in html and '第一项' in html and '最后一项' in html and '尾段' in html
    assert 'href="https://example.org"' in html and 'class="katex"' in html
    assert '\uE000' not in html and '\uE001' not in html
    assert html.index('</ul>') < html.index('class="reader-media"') < html.index('尾段')


def test_layout_spanning_blocks_and_order_ignore_runtime_ids():
    build=api();raw='首段。\n\n末段。';doc=document(raw)
    row=mount(doc,raw,raw);layout=build(doc,[row],workspace_root=ROOT,display_policy={})
    assert layout['mounts'][0]['resolution_reason']=='span_crosses_complete_blocks'
    block=next(b for b in layout['blocks'] if b['block_id']==layout['mounts'][0]['resolved_block_id'])
    assert raw[block['source_start_cp']:block['source_end_cp']]=='末段。'
    assert build(doc,[{**row,'run_id':'different-run'}],workspace_root=ROOT,display_policy={})==layout


@pytest.mark.parametrize('mode', ['full', 'balanced', 'json_only'])
@pytest.mark.parametrize('edge', ['before', 'after'])
def test_usable_paragraph_location_survives_review_mode(mode, edge):
    raw='首段。\n\n尾段。'; doc=document(raw)
    anchor='尾段。' if edge=='before' else '首段。'
    row=mount(doc,raw,anchor)
    row['edge']=edge
    with review_mode(mode):
        layout=api()(doc,[row],workspace_root=ROOT,display_policy={})
    placed=layout['mounts'][0]
    block=next(b for b in layout['blocks'] if b['block_id']==placed['resolved_block_id'])
    assert placed['requested_span_cp']==([5,8] if edge=='before' else [0,3])
    assert placed['edge']==edge
    assert raw[block['source_start_cp']:block['source_end_cp']]==anchor


@pytest.mark.parametrize('mode', ['balanced', 'json_only'])
@pytest.mark.parametrize('locator', ['span_only', 'anchor_only', 'wrong_span', 'stale_metadata'])
def test_limited_layout_keeps_usable_location_without_identity_gate(mode, locator):
    raw='首段。\n\n尾段。'; doc=document(raw); row=mount(doc,raw,'首段。')
    if locator=='span_only': row.pop('anchor_text')
    if locator=='anchor_only': row.pop('requested_span_cp')
    if locator=='wrong_span': row['requested_span_cp']=[50,60]
    if locator=='stale_metadata': row.update(owner_id='old-owner',field_hash='old-hash',source_hash='old-source')
    before=deepcopy(row)
    with review_mode(mode):
        layout=api()(doc,[row],workspace_root=ROOT,display_policy={})
    assert layout['mounts'][0]['requested_span_cp']==[0,3]
    assert row==before


@pytest.mark.parametrize('mode', ['balanced', 'json_only'])
@pytest.mark.parametrize('edge', ['before', 'after'])
@pytest.mark.parametrize('locator', ['missing', 'out_of_range', 'wrong_type', 'whitespace', 'unknown_anchor', 'missing_field'])
def test_limited_layout_falls_back_when_location_cannot_be_used(mode, edge, locator):
    raw='首段。\n\n尾段。'; doc=document(raw); row=mount(doc,raw,'首段。')
    row['edge']=edge
    row.pop('anchor_text')
    if locator=='missing': row.pop('requested_span_cp')
    if locator=='out_of_range': row['requested_span_cp']=[50,60]
    if locator=='wrong_type': row['requested_span_cp']=['0','3']
    if locator=='whitespace': row['requested_span_cp']=[3,5]
    if locator=='unknown_anchor': row.update(requested_span_cp=[0,3],anchor_text='不存在')
    if locator=='missing_field': row['target_pointer']='/missing'
    with review_mode(mode):
        layout=api()(doc,[row],workspace_root=ROOT,display_policy={})
    placed=layout['mounts'][0]
    assert placed['requested_span_cp']==[0,len(raw)]
    block=next(b for b in layout['blocks'] if b['block_id']==placed['resolved_block_id'])
    assert raw[block['source_start_cp']:block['source_end_cp']]==('首段。' if edge=='before' else '尾段。')
