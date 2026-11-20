from support.reader_site import write_reader_fixture
from copy import deepcopy
import importlib
import json
import subprocess
from pathlib import Path
import pytest
from aiforstudy.errors import ContractError
from aiforstudy.blocks.content.content_contracts import parse_content


def candidate():
    return {'_meta': {'schema_version': '4.0', 'file_type': 'LessonContentV4'},
        'unit_id': 'U', 'title': '标题', 'reader_intro': '目标导语',
        'body_markdown': r'采用[[CONCEPT:C]]，\(x=1\)。[[NOTE:N]][[EXPLAIN:F]]',
        'prerequisite_recaps': [{'id':'P','title':'先修回顾','markdown':'回顾内容'}],
        'summary':'小结内容', 'annotations':[{'id':'N','title':'批注','markdown':'批注内容'}],
        'explainables':[{'id':'F','kind':'formula','name':'公式','what':'对象条件','why':'使用理由','markdown':'解释内容','steps':[{'id':'FS','markdown':'解释步骤','basis':'解释依据'}]}],
        'examples':[{'id':'E','kind':'source','label':'例1','statement':'原题', 'approach':'思路内容','steps':[{'id':'S','markdown':'步骤内容','basis':'步骤依据'}], 'book_solution':'原书解答','source_line':1}],
        'concepts':[{'id':'C','role':'introduced'}],
        'teaching_plan':{'goals':[{'id':'G','observable_task':'计算结果','concept_refs':['C'],'claim_refs':['K'],'level':'core'}], 'prerequisites':[], 'claims':[{'id':'K','kind':'definition','conditions':['条件'],'conclusion':'结论','symbols':['x'],'source_refs':[]}]},
        'teaching_evidence':[{'goal_id':'G','purpose':'derivation','reader_pointer':'/examples/0/steps/0/markdown','claim_refs':['K']}], 'math_models':[], 'learning_checks':[]}


def catalog():
    from aiforstudy.blocks.content.source_context import FrozenReferenceCatalog
    return FrozenReferenceCatalog(entries=[{'id':'C','kind':'CONCEPT','label':'冻结概念名','version':'v1','unit_id':'U','markdown':'冻结定义'}])


@pytest.mark.parametrize('mode', ['full', 'balanced', 'json_only'])
@pytest.mark.parametrize('kind,label', [('note','说明'),('intuition','直觉'),
    ('pitfall','易错'),('derive','补充推导'),('legacy_kind','说明'),(None,'说明')])
def test_annotation_category_labels_keep_original_text_identity_and_mapping(mode, kind, label):
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.core.review_mode import review_mode
    c=candidate(); annotation=c['annotations'][0]
    annotation['markdown']='原解释：$x=1$。'
    if kind is not None: annotation['kind']=kind
    before=deepcopy(c)
    with review_mode(mode):
        doc=project_reader(c,catalog())
        changed=deepcopy(c); changed['annotations'][0]['kind']='derive'
        other=project_reader(changed,catalog())
    rows=[b for b in doc.blocks if b['group']=='annotations']
    assert [b['field_label'] for b in rows]==[label,label]
    assert [b['raw_text'] for b in rows]==['批注','原解释：$x=1$。']
    assert c==before
    for block in rows:
        assert block['source_map']==list(range(len(block['raw_text'])))
        match=next(b for b in other.blocks if b['json_pointer']==block['json_pointer'])
        for key in ('block_id','owner_id','json_pointer','raw_text','visible_text','source_map','marker_spans','markdown','anchor'):
            assert block[key]==match[key]


def test_v4_reader_and_review_share_every_registered_field():
    m = importlib.import_module('aiforstudy.blocks.content.content_contracts')
    assert hasattr(m, 'LessonContentV4'), 'strict v4 contract missing'
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.blocks.content.review_document import build_review_document
    content = parse_content(candidate())
    doc = project_reader(content, catalog())
    review = build_review_document(content, catalog())
    assert doc.blocks == review.blocks
    by_pointer = {b['json_pointer']:b for b in doc.blocks}
    for pointer in ('/reader_intro','/summary','/prerequisite_recaps/0/markdown','/examples/0/approach','/examples/0/steps/0/markdown','/explainables/0/steps/0/markdown'):
        assert pointer in by_pointer
    assert '冻结概念名' in by_pointer['/body_markdown']['visible_text']
    assert '\\(x=1\\)' in by_pointer['/body_markdown']['raw_text']
    assert '/teaching_plan/claims/0/conclusion' not in by_pointer
    changed = catalog().as_dict(); changed['entries'][0]['version'] = 'v2'
    assert project_reader(content, changed).digest != doc.digest
    assert content.as_dict()['examples'][0]['book_solution'] == '原书解答'


@pytest.mark.parametrize('path,key', [((),'summray'),(('examples',0),'thought'),(('examples',0,'steps',0),'text'),(('teaching_plan','claims',0),'conditionz')])
def test_v4_rejects_misspelled_or_legacy_fields(path,key):
    c=candidate(); target=c
    for part in path: target=target[part]
    target[key]='bad'
    with pytest.raises(ContractError): parse_content(c)


def test_unknown_note_and_duplicate_ids_rejected():
    c=candidate(); c['body_markdown']='[[NOTE:missing]]'
    with pytest.raises(ContractError, match='missing'): parse_content(c)
    c=candidate(); c['examples'][0]['steps'].append(deepcopy(c['examples'][0]['steps'][0]))
    with pytest.raises(ContractError, match='duplicate'): parse_content(c)


def test_source_inventory_is_independent_and_examples_preserve_original_fields():
    from aiforstudy.blocks.content.source_context import extract_source_examples, source_context_from_frozen
    text='定义1.1 若 x>0，则称 x 为正数。\n\n例1.1 求 x。\n解. x=1。'
    frozen=dict(book_id='B',unit_id='U',source_text=text,original_text='U.md',original_sha256='h',source_inputs={'textbook':'h'},unit={},chapters={},concept_index={'concepts':[]})
    context=source_context_from_frozen(frozen)
    assert {'definition','example'} <= {o.kind for o in context.source_obligations}
    assert all(o.source_excerpt in text for o in context.source_obligations)
    assert context.source_examples[0].book_solution == '解. x=1。'
    assert context.source_examples[0].source_line == 3
    assert context.source_context_hash == context.digest
    assert context.obligation_extraction_status == 'structural_only'


def test_actual_build_and_chromium_expand_reader_fields(tmp_path):
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('q01_render_build',root/'blocks/render/build.py')
    build=importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
    parse=tmp_path/'parse'; lessons=tmp_path/'lesson'/'lessons'; parse.mkdir(); lessons.mkdir(parents=True)
    (parse/'chapters.json').write_text(json.dumps({'book':{'title':'演示'},'chapters':[{'id':'U','title':'标题'}],'lesson_units':[{'unit_id':'U','title':'标题','original_text':'U.md'}]}))
    (parse/'concepts.json').write_text(json.dumps({'concepts':[{'id':'C','name':'冻结概念名','type':'definition'}]}))
    (parse/'U.md').write_text('原文')
    c=candidate(); c['original_text']='U.md'
    (lessons/'U.json').write_text(json.dumps(c))
    out=tmp_path/'out'
    write_reader_fixture([c],catalog(),out)
    html=out/'index.html'
    script='''<script>window.addEventListener('load',function(){
      var ref=document.querySelector('.reader-document a[href^="#reader-"]');
      var target=ref&&document.querySelector(ref.getAttribute('href'));
      if(ref) ref.click();
      var reachable=!!target&&target.open;
      var collapsed=document.querySelectorAll('.reader-document details');
      collapsed.forEach(function(d){if(!d.open)d.querySelector('summary').click();});
      var pointers=['/reader_intro','/summary','/prerequisite_recaps/0/markdown','/examples/0/approach','/examples/0/steps/0/markdown','/explainables/0/steps/0/markdown'];
      var results=pointers.map(function(p){var e=document.querySelector('[data-reader-pointer="'+p+'"]');return !!e&&e.getBoundingClientRect().height>0&&e.innerText.length>0;});
      var blocks=Array.from(document.querySelectorAll('[data-reader-pointer]'));
      var expected=window.__DATA__.units.U.reader_document.blocks;
      var all=expected.every(function(b){return blocks.some(function(e){return e.dataset.readerPointer===b.json_pointer&&e.getBoundingClientRect().height>0;});});
      document.body.setAttribute('data-q01-result',results.every(Boolean)&&all&&reachable&&collapsed.length>0?'pass':'fail');
      document.body.setAttribute('data-q01-math',document.querySelector('.reader-document .katex')?'pass':'fail');
    });</script>'''
    html.write_text(html.read_text().replace('</body>',script+'</body>'))
    chrome=root/'.devtools/chrome/chrome/chrome-linux64/chrome'
    assert chrome.is_file(), 'actual Chromium required for Q01 display verification'
    proc=subprocess.run([str(chrome),'--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--dump-dom','--virtual-time-budget=2000','--user-data-dir='+str(tmp_path/'chrome'),html.as_uri()],capture_output=True,text=True,timeout=30)
    assert proc.returncode==0,proc.stderr[-2000:]
    assert 'data-q01-result="pass"' in proc.stdout,proc.stdout[-5000:]
    assert 'data-q01-math="pass"' in proc.stdout


def test_explicit_candidate_import_is_lossless_and_reports_alias_conversion():
    from aiforstudy.blocks.content import content_contracts as cc
    assert hasattr(cc,'import_candidate_v4'), 'explicit candidate importer missing'
    c=candidate(); c['_meta']={'schema_version':'3.0'}
    c['examples'][0]['thought']=c['examples'][0].pop('approach')
    c['examples'][0]['steps'][0]['text']=c['examples'][0]['steps'][0].pop('markdown')
    c['explainables'][0]['walkthrough']=c['explainables'][0].pop('steps')
    c['explainables'][0]['walkthrough'][0]['claim']=c['explainables'][0]['walkthrough'][0].pop('markdown')
    before=deepcopy(c)
    result=cc.import_candidate_v4(c,v4_fields={})
    assert c==before
    assert result['content']['examples'][0]['approach']=='思路内容'
    assert result['content']['examples'][0]['book_solution']=='原书解答'
    assert len(result['migration_report']['changes'])==5
    assert result['migration_report']['source_hash']==cc.content_digest(before)
    c['examples'][0]['approach']='conflict'
    with pytest.raises(ContractError,match='conflict'): cc.import_candidate_v4(c,v4_fields={})


def test_learning_check_private_regression_data_is_not_reader_prose():
    from aiforstudy.blocks.content.reader_document import project_reader
    from test_learning_checks import check_fixture
    c=candidate(); c['learning_checks']=[check_fixture()]
    doc=project_reader(parse_content(c),catalog())
    assert not any('test_cases' in b['json_pointer'] for b in doc.blocks)
    assert any(b['json_pointer']=='/learning_checks/0/answer' and b['collapsed'] for b in doc.blocks)


@pytest.mark.parametrize('payload',['<img src=x onerror="alert(1)">','<script>alert(1)</script>','[click](javascript:alert(1))'])
def test_v4_projection_preserves_markup_as_reader_data(payload):
    from aiforstudy.blocks.content.reader_document import project_reader
    c=candidate(); c['body_markdown']=payload
    doc=project_reader(parse_content(c),catalog())
    block=next(b for b in doc.blocks if b['json_pointer']=='/body_markdown')
    assert block['raw_text']==payload and block['visible_text']==payload


def test_multiple_source_refs_do_not_require_optional_identity():
    c=candidate()
    c['teaching_plan']['claims'][0]['source_refs']=[{'kind':'textbook','quote':'source A'},{'kind':'textbook','quote':'source B'}]
    assert len(parse_content(c).teaching_plan.claims[0].source_refs)==2


def test_catalog_transitive_closure_is_deduplicated_and_cycles_terminate():
    from aiforstudy.blocks.content.reader_document import project_reader
    refs=catalog().as_dict()
    refs['entries'][0]['markdown']='依赖[[CONCEPT:D]]。'
    refs['entries'].append({'id':'D','kind':'CONCEPT','label':'额外概念','version':'v1','unit_id':'V','markdown':'回到[[CONCEPT:C]]。'})
    doc=project_reader(parse_content(candidate()),refs)
    assert [b['owner_id'] for b in doc.blocks if b['group']=='references' and b['field_label']=='概念']==['C','D']


def test_multi_step_projection_keeps_each_basis_adjacent():
    from aiforstudy.blocks.content.reader_document import project_reader
    c=candidate()
    c['examples'][0]['steps']=[{'id':'S1','markdown':'第一步结论','basis':'第一步依据'}, {'id':'S2','markdown':'第二步结论','basis':'第二步依据'}]
    doc=project_reader(parse_content(c),catalog())
    paths=[b['json_pointer'] for b in doc.blocks if b['json_pointer'].startswith('/examples/0/steps/')]
    assert paths==['/examples/0/steps/0/markdown','/examples/0/steps/0/basis','/examples/0/steps/1/markdown','/examples/0/steps/1/basis']


@pytest.fixture(scope='module')
def reviewed_reader_dom(tmp_path_factory):
    import html
    import re
    root=Path(__file__).resolve().parents[1]
    tmp_path=tmp_path_factory.mktemp('q01-review-regressions')
    spec=importlib.util.spec_from_file_location('q01_review_render_build',root/'blocks/render/build.py')
    build=importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
    parse=tmp_path/'parse'; lessons=tmp_path/'lesson'/'lessons'; parse.mkdir(); lessons.mkdir(parents=True)
    (parse/'chapters.json').write_text(json.dumps({'book':{'title':'演示'},'chapters':[{'id':'U','title':'标题'}],'lesson_units':[{'unit_id':'U','title':'标题','original_text':'U.md'}]}))
    (parse/'concepts.json').write_text(json.dumps({'concepts':[{'id':'C','name':'未审核全局名称','type':'definition'}]}))
    (parse/'U.md').write_text('原文')
    c=candidate(); c['original_text']='U.md'; c['one_line']='导读[[CONCEPT:C]]'; c['title']='标题[[CONCEPT:C]]'
    c['body_markdown']=r'<span hidden>关键条件甲</span><span style="display:none">关键条件乙</span><template>关键条件丙</template>可见结论。采用[[CONCEPT:C]]，\(x<y\)。'
    c['body_markdown']+=' <img src=x onerror="window.q01Executed=true"><script>window.q01Executed=true</script>[数据链接](javascript:window.q01Executed=true)'
    c['examples'][0]['steps']=[{'id':'S1','markdown':'第一步结论','basis':'第一步依据'}, {'id':'S2','markdown':'第二步结论','basis':'第二步依据'}]
    c['explainables'][0]['steps']=[{'id':'FS1','markdown':'第一段推导','basis':'第一段依据'}, {'id':'FS2','markdown':'第二段推导','basis':'第二段依据'}]
    refs=catalog().as_dict(); refs['entries'][0]['markdown']='依赖[[CONCEPT:D]]。'
    refs['entries'].append({'id':'D','kind':'CONCEPT','label':'额外概念','version':'v1','unit_id':'V','markdown':'回到[[CONCEPT:C]]。'})
    (lessons/'U.json').write_text(json.dumps(c))
    out=tmp_path/'out'; write_reader_fixture([c],refs,out)
    page=out/'index.html'
    # Test-owned fixed JS measures rendered text nodes, not merely label/parent height.
    script='''<script>window.addEventListener('load',function(){
      var links=Array.from(document.querySelectorAll('.reader-document a[href^="#reader-"]'));
      var linkResults=links.map(function(a){
        var id=a.getAttribute('href').slice(1),t=document.getElementById(id),wasOpen=t&&t.open;
        a.click();
        var popup=document.querySelector('.reader-popup[data-reader-target="'+id+'"]');
        var valid=!!t&&!!popup&&!!popup.querySelector('.reader-popup-body')&&(t.tagName!=='DETAILS'||t.open===wasOpen);
        if(popup) popup.querySelector('[data-reader-popup-close]').click();
        return valid;
      });
      document.querySelectorAll('.reader-document details').forEach(function(d){if(!d.open)d.querySelector('summary').click();});
      var body=document.querySelector('[data-reader-pointer="/body_markdown"]');
      function visibleText(phrase){
        var walker=document.createTreeWalker(body,NodeFilter.SHOW_TEXT), node;
        while(node=walker.nextNode()){
          var i=node.textContent.indexOf(phrase);if(i<0)continue;
          var range=document.createRange();range.setStart(node,i);range.setEnd(node,i+phrase.length);
          if(Array.from(range.getClientRects()).some(function(r){return r.width>0&&r.height>0;}))return true;
        }return false;
      }
      var pointers=Array.from(document.querySelectorAll('[data-reader-pointer]')).map(function(e){return e.dataset.readerPointer;});
      var visibleSteps=Array.from(document.querySelectorAll('[data-reader-step]')).map(function(e){return {pointer:e.dataset.readerPointer,step:e.dataset.readerStep,no:e.dataset.readerStepNo,text:e.innerText};});
      var result={header:document.querySelector('.unit-head').innerText,allText:document.getElementById('content').innerText,
        introCount:pointers.filter(function(p){return p==='/one_line';}).length, titleCount:pointers.filter(function(p){return p==='/title';}).length,
        links:linkResults, linkCount:links.length, prose:body.innerText,
        conditions:['关键条件甲','关键条件乙','关键条件丙'].map(visibleText),
        rawControls:Array.from(body.querySelectorAll('[hidden],[style],template,img,script')).filter(function(e){return !e.closest('.katex');}).length,
        executed:!!window.q01Executed,unsafeLinks:body.querySelectorAll('a[href^="javascript:"]').length,
        steps:pointers.filter(function(p){return p.indexOf('/steps/')>=0;}),stepMetadata:visibleSteps,
        math:!!body.querySelector('.katex'),mathErrors:body.querySelectorAll('.katex-error').length};
      document.body.setAttribute('data-q01-review',JSON.stringify(result));
    });</script>'''
    page.write_text(page.read_text().replace('</body>',script+'</body>'))
    chrome=root/'.devtools/chrome/chrome/chrome-linux64/chrome'
    proc=subprocess.run([str(chrome),'--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--dump-dom','--virtual-time-budget=2000','--user-data-dir='+str(tmp_path/'chrome'),page.as_uri()],capture_output=True,text=True,timeout=30)
    assert proc.returncode==0,proc.stderr[-2000:]
    marker=re.search(r'data-q01-review="([^"]*)"',proc.stdout)
    assert marker is not None,proc.stdout[-5000:]
    return json.loads(html.unescape(marker.group(1)))


def test_v4_dom_header_uses_only_frozen_projection(reviewed_reader_dom):
    result=reviewed_reader_dom
    assert '未审核全局名称' not in result['allText']
    assert '标题冻结概念名' in result['header']
    assert '导读冻结概念名' in result['header']
    assert result['introCount']==1 and result['titleCount']==1


def test_v4_dom_all_transitive_reference_links_open_targets(reviewed_reader_dom):
    assert reviewed_reader_dom['linkCount']>=4
    assert all(reviewed_reader_dom['links'])


def test_v4_dom_html_is_literal_visible_prose_and_math_survives(reviewed_reader_dom):
    result=reviewed_reader_dom
    assert all(result['conditions']),result['prose']
    assert '<span hidden>关键条件甲</span>' in result['prose']
    assert result['rawControls']==0
    assert not result['executed'] and result['unsafeLinks']==0
    assert result['math'] and result['mathErrors']==0


def test_v4_dom_steps_show_adjacent_basis_and_explicit_step_identity(reviewed_reader_dom):
    result=reviewed_reader_dom
    for group in ('examples','explainables'):
        expected=[f'/{group}/0/steps/{i}/{field}' for i in (0,1) for field in ('markdown','basis')]
        assert [p for p in result['steps'] if p.startswith('/'+group+'/')]==expected
        rows=[b for b in result['stepMetadata'] if b['pointer'].startswith('/'+group+'/')]
        assert len(rows)==4
        assert rows[0]['step']==rows[1]['step'] and rows[2]['step']==rows[3]['step']
        assert [r['no'] for r in rows]==['1','1','2','2']


def test_every_example_projects_as_closed_without_changing_source_identity():
    from aiforstudy.blocks.content.reader_document import project_reader
    c=candidate(); before=deepcopy(c)
    doc=project_reader(c,catalog())
    example=[b for b in doc.blocks if b['group']=='examples']
    assert example and all(b['collapsed'] for b in example)
    assert c==before
    assert next(b for b in example if b['json_pointer'].endswith('/statement'))['raw_text']=='原题'
    assert next(b for b in doc.blocks if b['json_pointer']=='/body_markdown')['raw_text']==c['body_markdown']


def test_theorem_core_and_proof_have_explicit_display_roles_and_preserve_raw_text():
    from aiforstudy.blocks.content.reader_document import project_reader
    c=candidate(); c['explainables'][0]['kind']='theorem'
    c['explainables'][0]['concept_id']='C'
    before=deepcopy(c); doc=project_reader(c,catalog())
    rows={b['json_pointer'].split('/explainables/0/')[1]:b for b in doc.blocks if b['group']=='explainables'}
    for key in ('name','what','why'):
        assert rows[key]['disclosure_kind']=='theorem'
        assert rows[key]['disclosure_part']=='core'
    for key in ('markdown','steps/0/markdown','steps/0/basis'):
        assert rows[key]['disclosure_part']=='proof'
    assert c==before
    assert rows['markdown']['raw_text']=='解释内容'
