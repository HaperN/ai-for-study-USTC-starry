// Supporting prose stays intact behind compact links using the same reader windows.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'../../blocks/render');
function fixture({extraBlocks=[]}={}) {
 const dom=new JSDOM(fs.readFileSync(path.join(root,'templates/index.html'),'utf8'),{url:'https://reader.invalid/#/U',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,errors=[];
 w.addEventListener('error',event=>errors.push(event.message));
 w.scrollTo=(a,b)=>{w.scrollY=typeof a==='object'?a.top:b;};
 w.HTMLElement.prototype.scrollIntoView=function(){this.dataset.scrolled='true';w.scrollY=900;};
 const b=(group,owner,pointer,text,anchor,kind='解释',collapsed=false)=>({group,owner_id:owner,json_pointer:pointer,markdown:text,raw_text:text,anchor,field_label:kind,collapsed,block_id:pointer});
 const blocks=[
  b('title','U','/title','本课','reader-00000000000000000000','标题'),
  b('body_markdown','U','/body_markdown','## 必要推导\n\n保留[实数判据](#reader-00000000000000000002)、[看条件](#reader-00000000000000000003)和[过程直觉](#reader-00000000000000000004)。\n\n第一步：保留必要条件 $z\ne0$。\n\n第二步：完整计算得到结论。','reader-00000000000000000001'),
  b('annotations','N','/annotations/0/title','分母不为零','reader-00000000000000000003','易错',true),
  b('annotations','N','/annotations/0/markdown','不可以把 $z=0$ 代入分母。','reader-00000000000000000003','易错',true),
  b('annotations','I','/annotations/1/title','镜面对照','reader-00000000000000000004','直觉',true),
  b('annotations','I','/annotations/1/markdown','反射保留实部，反转虚部。','reader-00000000000000000004','直觉',true),
  b('annotations','L','/annotations/2/markdown','未内链的完整提醒仍然可查。','reader-00000000000000000005','说明',true),
  b('references','C','/reference_catalog/entries/0/label','实数判据','reader-00000000000000000002','概念',true),
  b('references','C','/reference_catalog/entries/0/markdown','当 $w=\\bar w$ 时为实数。\n\n另一个相关概念的条件也原样保留。','reader-00000000000000000002','定义',true),
  b('examples','E','/examples/0/statement','证明完整例题。','reader-00000000000000000006','例题'),
  b('examples','E','/examples/0/steps/0/markdown','必要推导逐步展开，不得隐藏。','reader-00000000000000000006','第1步'),
  b('examples','E','/examples/0/pitfalls/0','第一项：不能约去零因子 $z$。','reader-00000000000000000006','易错点'),
  b('examples','E','/examples/0/pitfalls/1','第二项：不能遗漏条件 $y\ne0$。','reader-00000000000000000006','易错点'),
  b('examples','E','/examples/0/pitfalls/2','[已有的分母易错](#reader-00000000000000000003)','reader-00000000000000000006','易错点')
 ].concat(extraBlocks);
 w.__DATA__={book:'阅读测试',scope:['U'],units:{U:{title:'本课',reader_document:{schema_version:'ReaderDocumentV1',blocks}}},tree:[{id:'U',title:'本课',unit:true}],concepts:{}};
 const original=JSON.stringify(w.__DATA__.units);
 for(const file of ['vendor/marked.min.js','vendor/katex.min.js','assets/reader-markdown.js','assets/render-core.js','assets/reader-layout.js','assets/app.js'])w.eval(fs.readFileSync(path.join(root,file),'utf8'));
 return {dom,w,d,errors,original};
}
function finish(t,f){t.after(()=>{assert.equal(JSON.stringify(f.w.__DATA__.units),f.original);f.dom.window.close();assert.deepEqual(f.errors,[]);});}

const conceptBlock=(owner,index,field,text)=>({group:'concepts',owner_id:owner,json_pointer:`/concepts/${index}/${field}`,markdown:text,raw_text:text,
 anchor:'reader-concept-'+owner,field_label:field==='name'?'概念':'定义',collapsed:false,block_id:`concepts/${owner}/${field}`});

test('name-only concept registrations do not repeat in the page or search when a real reference exists',t=>{
 const f=fixture({extraBlocks:[conceptBlock('C',0,'name','实数判据')]});finish(t,f);
 const registration=f.d.querySelector('[data-reader-group="concepts"][data-reader-owner="C"]');
 assert.ok(!registration||registration.hidden,'a duplicate registration must not become a standalone paragraph');
 const definition=f.d.getElementById('reader-00000000000000000002');assert.ok(definition);
 f.w.scrollY=320;f.d.querySelector('[data-reader-pointer="/body_markdown"] a[href="#'+definition.id+'"]').click();
 assert.equal(f.w.scrollY,320);assert.match(f.d.querySelector('.reader-popup').textContent,/另一个相关概念的条件也原样保留/);
 f.d.getElementById('reader-search-toggle').click();const input=f.d.getElementById('reader-search-input');
 input.value='实数判据';input.dispatchEvent(new f.w.Event('input',{bubbles:true}));
 const results=[...f.d.querySelectorAll('#reader-search-results [data-reader-search-target]')];
 assert.equal(results.filter(n=>n.dataset.readerSearchTarget==='reader-concept-C').length,0);
 const reference=results.find(n=>n.dataset.readerSearchTarget===definition.id);assert.ok(reference);reference.click();
 assert.equal(definition.open,true,'search still reveals the actual definition');
});

test('actual concept definitions and unresolved names remain visible',t=>{
 const f=fixture({extraBlocks:[conceptBlock('C',0,'name','实数判据'),conceptBlock('C',0,'definition','必须保留的首次完整定义。'),
   conceptBlock('NEW',1,'name','还没有引用资料的概念')]});finish(t,f);
 const definition=f.d.querySelector('[data-reader-group="concepts"][data-reader-owner="C"]');
 assert.ok(definition&&!definition.hidden);assert.match(definition.textContent,/必须保留的首次完整定义/);
 const unresolved=f.d.querySelector('[data-reader-group="concepts"][data-reader-owner="NEW"]');assert.ok(unresolved&&!unresolved.hidden);
});

test('body reference labels retain their meaning and show the existing support category',t=>{
 const f=fixture();finish(t,f);
 for(const [anchor,kind,label] of [['reader-00000000000000000002','概念','实数判据'],['reader-00000000000000000003','易错','看条件'],['reader-00000000000000000004','直觉','过程直觉']]) {
  const link=f.d.querySelector(`[data-reader-pointer="/body_markdown"] a[href="#${anchor}"]`);
  assert.equal(link.dataset.readerLinkKind,kind);assert.match(link.textContent,new RegExp(label));
  assert.equal(link.querySelectorAll('.reader-link-kind').length,1);link.click();
  const popup=f.d.querySelector(`.reader-popup[data-reader-target="${anchor}"]`);assert.ok(popup);
  const title=popup.querySelector('.reader-popup-head > span').textContent;
  assert.equal(title,kind+' · '+label,'category prefix is not duplicated and semantic words stay intact');
 }
 assert.equal(f.d.querySelectorAll('.reader-popup').length,3);
 assert.match(f.d.querySelector('.reader-popup[data-reader-target="reader-00000000000000000002"]').textContent,/另一个相关概念的条件也原样保留/);
 assert.ok(f.d.querySelector('.reader-popup[data-reader-target="reader-00000000000000000002"] .katex'));
});

test('unlinked supporting text retains a compact entry, original location and return path',t=>{
 const f=fixture();finish(t,f);const target=f.d.getElementById('reader-00000000000000000005');
 const link=target.querySelector('summary .reader-support-link');assert.ok(link);
 assert.equal(target.open,false);f.w.scrollY=240;link.click();
 const popup=f.d.querySelector('.reader-popup[data-reader-target="reader-00000000000000000005"]');assert.ok(popup);
 assert.equal(target.open,false);assert.equal(f.w.scrollY,240);assert.match(popup.textContent,/未内链的完整提醒仍然可查/);
 popup.querySelector('[data-reader-popup-locate]').click();assert.equal(target.open,true);assert.equal(target.dataset.scrolled,'true');
 f.d.getElementById('reader-return').click();assert.equal(f.w.scrollY,240);
});

test('each authored pitfall opens independently while link-only reminders reuse their target',t=>{
 const f=fixture();finish(t,f);
 const items=[...f.d.querySelectorAll('#content .reader-pitfall-item')];assert.equal(items.length,2);
 items.forEach(item=>{assert.equal(item.open,false);item.querySelector('summary a').click();});
 const popups=[...f.d.querySelectorAll('.reader-popup')];assert.equal(popups.length,2);
 assert.match(popups[0].textContent,/不能约去零因子/);assert.doesNotMatch(popups[0].textContent,/不能遗漏条件/);
 assert.match(popups[1].textContent,/不能遗漏条件/);assert.doesNotMatch(popups[1].textContent,/不能约去零因子/);
 const direct=f.d.querySelector('[data-reader-pointer="/examples/0/pitfalls/2"] a');direct.click();
 assert.ok(f.d.querySelector('.reader-popup[data-reader-target="reader-00000000000000000003"]'));
 assert.equal(direct.closest('.reader-pitfall-item'),null,'existing NOTE does not require an intermediate window');
 const step=f.d.querySelector('[data-reader-pointer="/examples/0/steps/0/markdown"]');assert.equal(step.closest('details').open,false);assert.equal(step.closest('[data-reader-group="examples"]').open,false);
 assert.match(f.d.querySelector('[data-reader-pointer="/body_markdown"]').textContent,/第一步.*第二步/s);
 const ids=[...f.d.querySelectorAll('[id]')].map(n=>n.id);assert.equal(new Set(ids).size,ids.length);
});

test('hidden individual pitfall content remains searchable and resolves to that item',t=>{
 const f=fixture();finish(t,f);f.d.getElementById('reader-search-toggle').click();
 const input=f.d.getElementById('reader-search-input');input.value='不能遗漏';input.dispatchEvent(new f.w.Event('input',{bubbles:true}));
 const results=[...f.d.querySelectorAll('#reader-search-results [data-reader-search-target]')];assert.ok(results.length);
 const result=results.find(node=>/pitfall-1$/.test(node.dataset.readerSearchTarget));assert.ok(result,'individual hidden item has its own searchable destination');
 result.click();const target=f.d.getElementById(result.dataset.readerSearchTarget);assert.equal(target.open,true);assert.equal(target.dataset.scrolled,'true');
 assert.match(target.textContent,/不能遗漏条件/);
});

test('only a pure existing reference bypasses an individual pitfall window',t=>{
 const f=fixture();finish(t,f);
 const render=f.w.DSHRenderCore.createRenderer({marked:f.w.marked,katex:f.w.katex});
 const block={anchor:'reader-00000000000000000006',json_pointer:'/examples/0/pitfalls/0'};
 assert.equal(render.readerSupportTarget({...block,markdown:'[已有提醒](#reader-00000000000000000003)'}),'reader-00000000000000000003');
 assert.equal(render.readerSupportTarget({...block,markdown:'[前一条件](#reader-00000000000000000003)和[后一条件](#reader-00000000000000000004)'}),'reader-00000000000000000006-pitfall-0');
});
