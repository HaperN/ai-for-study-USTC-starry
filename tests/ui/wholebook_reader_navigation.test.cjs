// Exercise the production reader with a whole-book payload, entirely offline.
// Two chapters are enough to expose boundaries that chapter-only snapshots hide.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../../blocks/render');
const first = 'CH01-S01-SS01', last = 'CH01-S01-SS02', later = 'CH02-S01-SS01';
const material = 'material:A9000';
const anchors = {theorem:'reader-10000000000000000001',reference:'reader-20000000000000000001'};

function block(group, owner, pointer, markdown, anchor, collapsed=false) {
  return {group,owner_id:owner,json_pointer:pointer,markdown,raw_text:markdown,
    anchor,collapsed,block_id:pointer,field_label:owner};
}
function unit(id, title, body, extra=[]) {
  return {title, body_markdown:body, reader_document:{schema_version:'ReaderDocumentV1', blocks:[
    block('title',id,'/title',title,'reader-'+id+'-title'),
    block('body_markdown',id,'/body_markdown',body,'reader-'+id+'-body'), ...extra,
  ]}};
}
function data() {
  const core = [block('explainables','T-real','/explainables/0/name','共轭实数判据',anchors.theorem,true),
    block('explainables','T-real','/explainables/0/what','对任意复数 $w$，$w\\in\\mathbb{R}\\iff w=\\bar w$。',anchors.theorem,true),
    block('explainables','T-real','/explainables/0/why','实数的虚部为零，共轭不改变它。',anchors.theorem,true)];
  const proof = [block('explainables','T-real','/explainables/0/markdown','设 $w=a+bi$。相等当且仅当 $b=0$，所以判据双向成立。',anchors.theorem,true)];
  const theorem = {unit_id:first,concept_id:'T-real',name:'共轭实数判据',anchor:anchors.theorem,
    source_label:'第一章的首次讲解',core_blocks:core,proof_blocks:proof,
    reader_document:{schema_version:'ReaderDocumentV1',blocks:[...core,...proof]},
    review_outcome:{status:'verified',reasons:[]}};
  const refs = {[anchors.reference]:{unit_id:first,concept_id:'T-real'}};
  return {
    book:'复变函数完整教材',scope:['CH01',first,last,'CH02',later],concepts:{},
    units:{
      CH01:unit('CH01','第一章 复数','## 本章引言\n\n第一章路线。'),
      [first]:unit(first,'1.1.1 共轭复数',`## 定义\n\n第一章完整定义。\n\n[判据](#${anchors.theorem})`,[...core,...proof]),
      [last]:unit(last,'1.1.2 四则运算','## 例题\n\n第一章末节完整推导。'),
      CH02:unit('CH02','第二章 复变函数','## 本章引言\n\n第二章路线。'),
      [later]:{...unit(later,'2.1.1 复变函数的极限',`## 极限与共轭\n\n第二章连续阅读。由[定理1](#${anchors.reference})核对实数条件。\n\n## 例题\n\n第二章完整证明。`,[
        block('references','T-real','/reference_catalog/entries/0/label','定理1',anchors.reference,true),
        block('references','T-real','/reference_catalog/entries/0/markdown','前文判据。',anchors.reference,true),
      ]),theorem_refs:refs},
    },
    tree:[
      {id:'CH01',title:'第一章 复数',unit:true,children:[{id:'CH01-S01',title:'复数的运算',anchor:'1.1',children:[
        {id:first,title:'1.1.1 共轭复数',unit:true},{id:last,title:'1.1.2 四则运算',unit:true},
      ]}]},
      {id:'CH02',title:'第二章 复变函数',unit:true,children:[{id:'CH02-S01',title:'函数与极限',anchor:'2.1',children:[
        {id:later,title:'2.1.1 复变函数的极限',unit:true},
      ]}]},
      {id:material,material_id:material,title:'习题参考答案',unit:false},
    ],
    theorem_index:{by_unit:{[first]:{'T-real':theorem}},refs_by_unit:{[later]:refs,[first]:{[anchors.theorem]:{unit_id:first,concept_id:'T-real'}}}},
    source_materials:{[material]:{title:'习题参考答案',state:'recorded',raw_markdown:'原始 OCR 答案。',
      display_markdown:'## 第一章参考答案\n\n整理后的第一章答案。\n\n## 第二章参考答案\n\n整理后的第二章答案。',
      formatting:{status:'formatted',notes:[]}}},
  };
}
function fixture(t, initial=last) {
  const dom = new JSDOM(fs.readFileSync(path.join(root,'templates/index.html'),'utf8'),{
    url:'https://reader.invalid/#/'+initial,runScripts:'outside-only',pretendToBeVisual:true,
  });
  const w=dom.window,d=w.document,errors=[];
  w.addEventListener('error',e=>errors.push(e.message));
  w.scrollTo=(x,y)=>{w.scrollY=typeof x==='object'?x.top:y;};
  w.HTMLElement.prototype.scrollIntoView=function(){this.dataset.scrolled='true';};
  w.__DATA__=data();
  const source=JSON.stringify({units:w.__DATA__.units,tree:w.__DATA__.tree,theorems:w.__DATA__.theorem_index,materials:w.__DATA__.source_materials});
  for(const file of ['vendor/marked.min.js','vendor/katex.min.js','assets/reader-markdown.js','assets/render-core.js','assets/reader-layout.js','assets/app.js'])
    w.eval(fs.readFileSync(path.join(root,file),'utf8'));
  t.after(()=>{
    assert.equal(JSON.stringify({units:w.__DATA__.units,tree:w.__DATA__.tree,theorems:w.__DATA__.theorem_index,materials:w.__DATA__.source_materials}),source);
    dom.window.close();assert.deepEqual(errors,[]);
  });
  const node=id=>[...d.querySelectorAll('[data-reader-toc-node]')].find(n=>n.dataset.readerTocNode===id);
  return {w,d,node,async click(selector){const a=d.querySelector(selector);assert.ok(a,selector);a.click();await new Promise(r=>setTimeout(r,20));
    assert.doesNotMatch(d.getElementById('content').textContent,/这一页没能显示/);}};
}

test('whole-book directory keeps original lesson numbers and no concept/example subdirectory',t=>{
  const f=fixture(t);
  for(const [id,number] of [[first,'1.1.1'],[last,'1.1.2'],[later,'2.1.1']]) {
    const node=f.node(id);assert.ok(node);
    const label=node.querySelector('.toc-item').textContent;
    assert.equal(label.split(number).length-1,1,'original number appears exactly once');
    assert.equal(node.querySelector('.reader-toc-unit-body').childElementCount,0);
  }
  assert.equal(f.d.querySelector('#reader-section-toc'),null);
  assert.equal(f.d.querySelector('#sidebar [data-reader-topic], #sidebar [data-reader-support]'),null);
  assert.equal(f.node('CH01').open,true);assert.equal(f.node('CH02').open,false);
  assert.match(f.d.getElementById('content').textContent,/第一章末节完整推导/);
});

test('previous and next cross chapter boundaries in source order and retain chapter overviews',async t=>{
  const f=fixture(t);
  assert.equal(f.d.querySelector('.pager .next').getAttribute('href'),'#/CH02');
  await f.click('.pager .next');
  assert.equal(f.w.location.hash,'#/CH02');
  assert.match(f.d.getElementById('content').textContent,/第二章路线/);
  assert.deepEqual([...f.d.querySelectorAll('.reader-overview-toc a')].map(a=>a.getAttribute('href')),['#/'+later]);
  assert.equal(f.node('CH02').open,true);assert.equal(f.node('CH01').open,false);
  assert.equal(f.d.querySelector('.pager .prev').getAttribute('href'),'#/'+last);
  await f.click('.pager .next');
  assert.equal(f.w.location.hash,'#/'+later);
  assert.match(f.d.getElementById('content').textContent,/第二章完整证明/);
  assert.equal(f.d.querySelector('.pager .next'),null);
  await f.click('.pager .prev');await f.click('.pager .prev');
  assert.equal(f.w.location.hash,'#/'+last);
  assert.match(f.d.getElementById('content').textContent,/第一章末节完整推导/);
});

test('later chapter reuses earlier theorem in a draggable window without leaving current reading',t=>{
  const f=fixture(t,later),body=f.d.getElementById('content'),before=body.textContent;
  const blocks=[...body.querySelectorAll('[data-reader-block]')];
  const link=body.querySelector(`[data-reader-pointer="/body_markdown"] a[href="#${anchors.reference}"]`);
  assert.equal(link.textContent,'共轭实数判据');
  f.w.scrollY=480;link.click();
  const popup=f.d.querySelector('.reader-theorem-popup');assert.ok(popup);
  assert.equal(f.w.location.hash,'#/'+later);assert.equal(f.w.scrollY,480);assert.equal(body.textContent,before);
  assert.deepEqual([...body.querySelectorAll('[data-reader-block]')],blocks);
  assert.match(popup.querySelector('.reader-theorem-core').textContent,/对任意复数.*虚部为零/s);
  assert.ok(popup.querySelector('.reader-theorem-core .katex'));
  const proof=popup.querySelector('.reader-theorem-proof');assert.equal(proof.open,false);
  proof.querySelector('summary').click();assert.equal(proof.open,true);assert.match(proof.textContent,/双向成立/);
  assert.equal(popup.querySelector('[data-reader-popup-locate]'),null);
  const drag=popup.querySelector('[data-reader-popup-drag]');drag.focus();
  const left=parseFloat(popup.style.left)||0;
  drag.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
  assert.ok(parseFloat(popup.style.left)>left);
  popup.querySelector('[data-reader-popup-return]').click();
  assert.equal(f.w.location.hash,'#/'+later);assert.equal(f.w.scrollY,480);assert.equal(f.d.activeElement,link);
});

test('one independent answer entry is shared by chapters and shows formatted copy with original folded',async t=>{
  const f=fixture(t);
  const selector=`#sidebar [data-material="${material}"]`;
  assert.equal(f.d.querySelectorAll(selector).length,1);
  await f.click('.pager .next');await f.click('.pager .next');
  assert.equal(f.d.querySelectorAll(selector).length,1);
  await f.click(selector);
  assert.equal(f.w.location.hash,'#/materials/'+encodeURIComponent(material));
  const source=f.d.querySelector('#content .source-material');assert.ok(source);
  assert.match(source.textContent,/整理后的第一章答案.*整理后的第二章答案/s);
  const original=source.querySelector('.source-original');assert.ok(original);assert.equal(original.open,false);
  assert.match(original.textContent,/原始 OCR 答案/);
  assert.equal(f.d.querySelectorAll(selector).length,1);
  assert.equal(f.d.querySelector(selector).getAttribute('aria-current'),'page');
  await f.click(`#sidebar [data-unit="${later}"]`);
  assert.match(f.d.getElementById('content').textContent,/第二章完整证明/);
  assert.equal(f.d.querySelector('#content .source-material'),null);
});
