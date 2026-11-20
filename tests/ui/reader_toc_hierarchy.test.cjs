// Production navigation in an offline DOM; no browser, network, or model calls.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../../blocks/render');
function fixture({data, unitId='CH04-S02-SS01'}={}) {
  const dom = new JSDOM(fs.readFileSync(path.join(root,'templates/index.html'),'utf8'), {
    url:'https://reader.invalid/#/'+unitId,runScripts:'outside-only',pretendToBeVisual:true,
  });
  const w=dom.window,d=w.document,errors=[];
  w.addEventListener('error',event=>errors.push(event.message));
  w.scrollTo=(a,b)=>{w.scrollY=typeof a==='object'?a.top:b;};
  w.HTMLElement.prototype.scrollIntoView=function(){this.dataset.scrolled='true';};
  w.__DATA__=data || {
    book:'另一门课程',scope:['CH04','CH04-S01-SS01','CH04-S02-SS01','CH04-S02-SS02'],
    units:{
      CH04:{title:'第四章',body_markdown:'## 学习路线\n\n先了解全章。'},
      'CH04-S01-SS01':{title:'预备知识',body_markdown:'## 向量\n\n完整定义。'},
      'CH04-S02-SS01':{title:'4.2.1 主定理',body_markdown:'## 4.2.1 主定理\n\n原始导入。\n\n## 定义与条件\n\n条件完整。\n\n## 也就是说\n\n保留这段解释。\n\n## 充分性\n\n保留完整证明。\n\n## 定理 7\n\n数学结论。\n\n## 例 2：一个标题很长但仍然必须可读的完整教学例题\n\n例题步骤不能消失。'},
      'CH04-S02-SS02':{title:'后续方法',body_markdown:'## 应用\n\n继续完整教学。'},
    }, concepts:{},
    tree:[{id:'CH04',title:'第四章',unit:true},{id:'CH04-S01-SS01',title:'预备知识',unit:true},
      {id:'CH04-S02-SS01',title:'4.2.1 主定理',unit:true},{id:'CH04-S02-SS02',title:'后续方法',unit:true}],
  };
  const original=JSON.stringify(w.__DATA__);
  for(const p of ['vendor/marked.min.js','vendor/katex.min.js','assets/reader-markdown.js','assets/render-core.js','assets/reader-layout.js','assets/app.js']) w.eval(fs.readFileSync(path.join(root,p),'utf8'));
  assert.doesNotMatch(d.getElementById('content').textContent,/这一页没能显示/);
  return {w,d,dom,errors,original,node:id=>[...d.querySelectorAll('[data-reader-toc-node]')].find(n=>n.dataset.readerTocNode===id),
    navigate(id){w.history.replaceState(null,'','#/'+id);w.dispatchEvent(new w.HashChangeEvent('hashchange'));}};
}
function finish(t,f){t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});}

test('chapter overview exposes lessons without an internal guide outline',t=>{
  const f=fixture({unitId:''});finish(t,f);
  const overview=f.d.querySelector('#content .reader-overview-toc');assert.ok(overview);
  assert.deepEqual([...overview.querySelectorAll('a')].map(a=>a.getAttribute('href')),
    ['#/CH04-S01-SS01','#/CH04-S02-SS01','#/CH04-S02-SS02']);
  assert.equal(f.node('CH04-S01').open,true);assert.equal(f.node('CH04-S02').open,true);
  assert.equal(f.d.getElementById('reader-section-toc'),null);
  assert.match(f.d.getElementById('content').textContent,/先了解全章/);
  overview.querySelector('a[href="#/CH04-S02-SS01"]').click();
  assert.equal(f.w.location.hash,'#/CH04-S02-SS01');
  assert.match(f.d.getElementById('content').textContent,/原始导入/);
  assert.equal(f.d.querySelector('.reader-overview-toc'),null);
  assert.equal(JSON.stringify(f.w.__DATA__.units),JSON.stringify(JSON.parse(f.original).units));
});

test('home route returns to the first scoped unit while local anchors retain the lesson',async t=>{
  const f=fixture();finish(t,f);
  f.w.history.replaceState(null,'','#reader-a-local-topic');f.w.dispatchEvent(new f.w.HashChangeEvent('hashchange'));
  assert.match(f.d.getElementById('content').textContent,/原始导入/);
  f.navigate('');
  assert.match(f.d.getElementById('content').textContent,/先了解全章/);
  assert.ok(f.d.querySelector('.reader-overview-toc'));
  f.w.scrollY=900;f.navigate('CH04-S02-SS01');
  f.d.querySelector('.brand-link').click();
  await new Promise(resolve=>setTimeout(resolve,30));
  assert.equal(f.w.location.hash,'#/');assert.equal(f.w.scrollY,0);
  assert.ok(f.d.querySelector('.reader-overview-toc'));
});

test('overview respects chapter boundaries, real titles, and nonlesson groups',t=>{
  const data={book:'多章',scope:['CH03','CH03-S01-SS01','CH04','CH04-S01-SS01'],
    units:{CH03:{title:'第三章',body_markdown:'## 导读\n\n第三章说明。'},'CH03-S01-SS01':{title:'先修',body_markdown:'定义。'},
      CH04:{title:'第四章',body_markdown:'## 导读\n\n第四章说明。'},'CH04-S01-SS01':{title:'应用',body_markdown:'推导。'}},concepts:{},
    tree:[{id:'CH03',title:'已有章节',unit:true,children:[{id:'CH03-S01',title:'已有分组',children:[{id:'CH03-S01-SS01',title:'先修',unit:true}]}]},
      {id:'CH04',title:'当前章节',unit:true,children:[{id:'CH04-S01',title:'实际分组',children:[{id:'CH04-S01-SS01',title:'应用',unit:true}]}]}]};
  const f=fixture({data,unitId:'CH04'});finish(t,f);
  const overview=f.d.querySelector('.reader-overview-toc');assert.ok(overview);
  assert.match(overview.textContent,/实际分组/);assert.doesNotMatch(overview.textContent,/先修/);
  assert.deepEqual([...overview.querySelectorAll('a')].map(a=>a.getAttribute('href')),['#/CH04-S01-SS01']);
  assert.equal(f.node('CH04-S01').open,true);assert.equal(f.node('CH03').open,false);
  assert.equal(f.node('CH03-S01').open,false);
});

test('flat snapshot recovers chapter/section hierarchy and opens only its current path',t=>{
  const f=fixture();finish(t,f);
  assert.equal(f.node('CH04').open,true);
  assert.equal(f.node('CH04-S02').open,true);
  assert.equal(f.node('CH04-S01').open,false);
  assert.equal(f.node('CH04-S02-SS01').parentElement.closest('[data-reader-toc-node]'),f.node('CH04-S02'));
  assert.equal(f.node('CH04-S01').textContent.includes('第1节'),true,'missing source section name is not invented');
  assert.equal(f.d.querySelectorAll('#reader-section-toc').length,0);
  const label=f.node('CH04-S02-SS01').querySelector('.toc-item').textContent;
  assert.equal((label.match(/4\.2\.1/g)||[]).length,1,'number is displayed only once');
  assert.equal(f.node('CH04-S02-SS01').querySelector('a').getAttribute('aria-current'),'page');
});

test('sidebar exposes the real chapter context and reuses the existing search dialog',t=>{
  const f=fixture();finish(t,f);
  const chapter=f.d.getElementById('reader-chapter-title');assert.ok(chapter);
  assert.equal(chapter.textContent,'第四章');assert.equal(chapter.getAttribute('href'),'#/CH04');
  const search=f.d.getElementById('reader-sidebar-search');assert.ok(search);search.click();
  assert.equal(f.d.getElementById('reader-search-layer').hidden,false);
  assert.equal(f.d.querySelectorAll('#reader-search-layer').length,1);
  assert.equal(search.getAttribute('aria-expanded'),'true');
  assert.equal(f.d.activeElement,f.d.getElementById('reader-search-input'));
  f.d.querySelector('[data-reader-search-close]').click();
  assert.equal(f.d.getElementById('reader-search-layer').hidden,true);
  assert.equal(search.getAttribute('aria-expanded'),'false');
});

test('mobile sidebar search closes the directory before focusing a result in the same lesson',t=>{
  const f=fixture();finish(t,f);
  Object.defineProperty(f.w,'innerWidth',{value:390,configurable:true});
  const toggle=f.d.getElementById('reader-toc-toggle');toggle.click();
  assert.equal(f.d.getElementById('sidebar').classList.contains('is-open'),true);
  f.d.getElementById('reader-sidebar-search').click();
  assert.equal(f.d.getElementById('sidebar').classList.contains('is-open'),false);
  assert.equal(toggle.getAttribute('aria-expanded'),'false');
  const result=f.d.querySelector('[data-reader-search-unit="CH04-S02-SS01"]');assert.ok(result);result.click();
  assert.equal(f.d.getElementById('reader-search-layer').hidden,true);
  assert.equal(f.d.getElementById('sidebar').classList.contains('is-open'),false);
});

test('core topics and supplementary explanations remain in the lesson without directory subentries',t=>{
  const block=(group,pointer,markdown,anchor,collapsed=false)=>({group,owner_id:group,json_pointer:pointer,markdown,raw_text:markdown,anchor,collapsed,block_id:pointer,field_label:group});
  const body='## 定义与直觉\n\n定义全文。\n\n## 运算性质\n\n性质全文。\n\n## 例1\n\n完整例题。';
  const data={book:'真实书名',scope:['U'],tree:[{id:'U',title:'本课',unit:true}],units:{U:{title:'本课',reader_document:{schema_version:'ReaderDocumentV1',blocks:[
    block('title','/title','本课','reader-title'),block('body_markdown','/body_markdown',body,'reader-body'),
    block('explainables','/explainables/0/name','性质的进一步解释','reader-extra',true),block('explainables','/explainables/0/markdown','补充依据全文。','reader-extra',true)
  ]}}},concepts:{}};
  const f=fixture({data,unitId:'U'});finish(t,f);
  assert.equal(f.d.querySelector('#reader-section-toc'),null);
  assert.equal(f.d.querySelector('.reader-toc-utility'),null);
  assert.match(f.d.getElementById('content').textContent,/定义全文.*性质全文.*完整例题/s);
  const support=f.d.getElementById('reader-extra');assert.ok(support.querySelector('summary a'));
  support.querySelector('summary a').click();assert.match(f.d.querySelector('.reader-popup').textContent,/补充依据全文/);
});

test('directory stops at lessons while all headings and examples remain readable',t=>{
  const f=fixture();finish(t,f);
  assert.equal(f.d.querySelector('#reader-section-toc'),null);
  assert.equal(f.d.querySelector('#sidebar [data-reader-section], #sidebar [data-reader-support]'),null);
  const body=f.d.getElementById('content').textContent;
  assert.match(body,/原始导入/);assert.match(body,/保留这段解释/);assert.match(body,/保留完整证明/);assert.match(body,/例题步骤不能消失/);
  assert.ok(f.d.querySelector('[data-reader-topic]'),'body layout still exposes topics for search and comparison');
});

test('authored tree labels remain authoritative and changing lessons never creates a local outline',t=>{
  const f=fixture({data:{book:'测试',scope:['U','V'],units:{U:{title:'第一课',body_markdown:'## 概念\n\n定义。'},V:{title:'第二课',body_markdown:'## 性质\n\n结论。'}},concepts:{},tree:[{id:'part-one',title:'几何篇',children:[{id:'U',title:'第一课',unit:true}]},{id:'part-two',title:'代数篇',children:[{id:'V',title:'第二课',unit:true}]}]},unitId:'U'});finish(t,f);
  assert.equal(f.node('part-one').open,true);assert.equal(f.node('part-two').open,false);
  f.node('part-one').querySelector('summary').click();assert.equal(f.node('part-one').open,false,'native branch summary can collapse');
  f.navigate('V');assert.equal(f.node('part-two').open,true);assert.equal(f.node('part-one').open,false);
  assert.equal(f.d.querySelectorAll('#reader-section-toc').length,0);
  assert.equal(f.node('U').querySelector('[aria-current]'),null);
});

test('directory lesson summary navigates and closes the narrow-screen menu',t=>{
  const f=fixture();finish(t,f);
  Object.defineProperty(f.w,'innerWidth',{value:390,configurable:true});
  const toggle=f.d.getElementById('reader-toc-toggle');toggle.click();
  f.node('CH04-S02-SS02').querySelector('summary').click();
  assert.equal(f.w.location.hash,'#/CH04-S02-SS02');assert.equal(f.node('CH04-S02-SS02').open,true);assert.equal(f.d.getElementById('sidebar').classList.contains('is-open'),false);
});

test('reading progress keeps the topic breadcrumb without adding or scrolling a local directory',async t=>{
  const f=fixture();finish(t,f);const side=f.d.getElementById('sidebar');
  Object.defineProperty(f.w,'innerWidth',{value:1440,configurable:true});
  const topics=[...f.d.querySelectorAll('[data-reader-topic]')];
  topics.forEach((node,i)=>node.getBoundingClientRect=()=>({top:i*250-520,bottom:i*250-300}));
  side.scrollTop=0;f.w.scrollY=520;f.w.dispatchEvent(new f.w.Event('scroll'));await new Promise(resolve=>setTimeout(resolve,30));
  assert.equal(f.d.querySelector('#reader-section-toc'),null);
  const nearest=topics.filter(node=>node.getBoundingClientRect().top<=150).at(-1);
  assert.equal(f.d.getElementById('reader-current-topic').textContent,' / '+nearest.querySelector('.reader-topic-title').textContent);
  assert.equal(side.scrollTop,0);assert.equal(f.w.scrollY,520);
  side.scrollTop=10;f.w.dispatchEvent(new f.w.Event('scroll'));await new Promise(resolve=>setTimeout(resolve,30));
  assert.equal(side.scrollTop,10,'an unchanged reading anchor does not fight manual sidebar scrolling');
});

test('real first chapter retains all content and original-numbered unit links with no local outlines', {skip:!process.env.READER_TOC_REAL_DATA}, t=>{
  const script=fs.readFileSync(process.env.READER_TOC_REAL_DATA,'utf8');
  const box={window:{}};require('node:vm').runInNewContext(script,box);
  const data=JSON.parse(JSON.stringify(box.window.__DATA__)),before=JSON.stringify(data.units),treeBefore=JSON.stringify(data.tree);
  const f=fixture({data,unitId:data.scope[0]});finish(t,f);
  for(const id of data.scope){
    f.navigate(id);assert.doesNotMatch(f.d.getElementById('content').textContent,/这一页没能显示/);
    assert.equal(f.d.querySelector('#reader-section-toc'),null);
    assert.equal(f.d.querySelector('#sidebar [data-reader-section], #sidebar .reader-toc-utility'),null);
    for(const unitId of data.scope)assert.ok(f.d.querySelector('#sidebar [data-unit="'+unitId+'"]'));
    assert.ok(f.d.querySelector('[data-reader-pointer="/body_markdown"]'));
    if(!/^CH\d+$/.test(id)) {
      assert.equal(f.node(id).querySelector('.reader-toc-unit-body').childElementCount,0);
      assert.equal(f.node(id).querySelector('a').getAttribute('aria-current'),'page');
    }
  }
  assert.equal(JSON.stringify(data.units),before);assert.equal(JSON.stringify(data.tree),treeBefore);
});
