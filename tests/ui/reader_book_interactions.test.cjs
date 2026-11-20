// Exercise the production reader in an offline DOM: no browser, URL loading or network.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const render = path.resolve(__dirname, '../../blocks/render');
const ids = {body:'reader-00000000000000000001',a:'reader-00000000000000000002',
  b:'reader-00000000000000000003',example:'reader-00000000000000000004'};
function block(group, owner, pointer, markdown, anchor, collapsed=false) {
  return {group,owner_id:owner,json_pointer:pointer,markdown,raw_text:markdown,
    anchor,collapsed,block_id:pointer,field_label:owner};
}
function fixture(options={}) {
  const dom = new JSDOM(`<!doctype html><body class="book-page">
    <header><span id="book-title"></span><span id="scope-note"></span>
    <button id="reader-toc-toggle" aria-expanded="false">目录</button>
    <button id="reader-font-minus">小</button><button id="reader-font-plus">大</button>
    <button id="reader-return" hidden>返回原句</button><div id="reader-progress"><span></span></div></header>
    <aside id="sidebar" class="book-toc"></aside><main id="content"></main>
    <div id="tooltip" class="hidden"></div><div id="drawer"><div id="drawer-body"></div><button id="drawer-close"></button></div>
    </body>`, {url:'https://reader.invalid/#/U',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window; let mounted=0,disposed=0;const errors=[];
  w.addEventListener('error',event=>errors.push(event.message));
  w.scrollTo = function(a,b) { w.scrollY=typeof a==='object'?a.top:b; };
  w.HTMLElement.prototype.scrollIntoView=function(){this.dataset.scrolled='true';w.scrollY=900;};
  w.DSHAnimations={mountAnimations(){mounted++;return()=>{disposed++;};}};
  const body = `## 四则运算\n\n第一句[解释甲](#${ids.a})，然后[例题](#${ids.example})。\n\n第二句也有[解释甲](#${ids.a})。\n\n### 运算步骤\n\n正文仍在这里。`;
  const blocks=[block('title','U','/title','第一节','reader-00000000000000000000'),
    block('body','U','/body_markdown',body,ids.body),
    block('explainables','A','/explainables/0/markdown',`甲的解释。[解释乙](#${ids.b})`,ids.a,true),
    block('annotations','B','/annotations/0/markdown',`乙的解释。[返回甲](#${ids.a})`,ids.b,true),
    block('examples','E','/examples/0/statement','例题完整题目。',ids.example)];
  const u={title:'第一节',reader_document:{schema_version:'ReaderDocumentV1',blocks}};
  if(options.teaching) {
    blocks.find(b=>b.owner_id==='B').field_label='易错';
    blocks.push(block('annotations','unlinked','/annotations/1/markdown','未链接的批注全文。','reader-unlinked',true));
    blocks[blocks.length-1].field_label='说明';
    blocks.push(block('examples','E','/examples/0/key_points/0',`回顾后[看易错](#${ids.b})`,ids.example));
    blocks.push(block('examples','E','/examples/0/pitfalls/0','具体错法与条件。',ids.example));
  }
  w.__DATA__={book:'复变函数',scope:['U','V'],units:{U:u,V:{title:'第二节',body_markdown:'## 共轭复数\n\n第二节正文。'}},
    concepts:{C:{name:'旧概念',explanation:'旧概念解释。',occurrences:[{unit_id:'V',title:'第二节'}]}},
    tree:[{id:'CH',title:'第一章',children:[{id:'U',title:'第一节',unit:true},{id:'V',title:'第二节',unit:true}]}],
    animations:{U:[{request_id:'animation-1',resources:{},steps:[]}]}};
  if(options.numberedExample) {
    const b=blocks.find(b=>b.json_pointer==='/body_markdown');b.group='body_markdown';
    b.markdown=b.raw_text=`例3：[例3](#${ids.example})\n\n题目相关说明。\n\n## 后续正文\n\n这里仍可[引用例3](#${ids.example})。`;
    blocks.push(block('examples','E','/examples/0/label','例3',ids.example));
  }
  if(options.displayTitle)w.__DATA__.display_title=options.displayTitle;
  if(options.numberLink) {
    w.__DATA__.units.U={title:'第一节',body_markdown:'## 四则运算\n\n这里使用式 (1.1)，可以查看原文。'};
    w.__DATA__.units.V.original={cleaned:'公式 (1.1) 的原文。'};
    w.__DATA__.number_refs={'1.1':['V']};
  }
  if(options.realAnimation) {
    w.HTMLMediaElement.prototype.pause=function(){};w.HTMLMediaElement.prototype.load=function(){};
    w.HTMLMediaElement.prototype.play=function(){this.dataset.played='true';return Promise.resolve();};
    w.eval(fs.readFileSync(path.join(render,'assets/animations.js'),'utf8'));
    w.__DATA__.animations.U=[{request_id:'animation-1',unit_id:'U',resources:{mp4:{relative_path:'scene.mp4'}},steps:[]}];
  }
  for(const filename of ['vendor/marked.min.js','vendor/katex.min.js','assets/reader-markdown.js','assets/render-core.js',...(options.numberedExample?['assets/reader-layout.js']:[]),'assets/app.js']) {
    w.eval(fs.readFileSync(path.join(render,filename),'utf8'));
  }
  assert.doesNotMatch(w.document.getElementById('content').textContent,/没能显示/);
  const d=w.document;
  return {w,d,dom,errors,target:key=>d.getElementById(ids[key]),
    link:(key,n=0)=>d.querySelectorAll(`[data-reader-pointer="/body_markdown"] a[href="#${ids[key]}"]`)[n],
    popup:key=>d.querySelector(`.reader-popup[data-reader-target="${ids[key] || key}"]`),
    counts:()=>({mounted,disposed})};
}
function checkUniqueIds(d) {
  const ids=[...d.querySelectorAll('[id]')].map(n=>n.id);
  assert.equal(new Set(ids).size,ids.length,'reading windows must not duplicate IDs');
}

test('review references open a window while authored review and unlinked notes stay in place',t=>{
  const f=fixture({teaching:true});t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {d}=f;
  const review=d.querySelector('[data-reader-pointer="/examples/0/key_points/0"]');
  assert.ok(review.closest('.reader-teaching-review'));
  const link=review.querySelector('a'),target=f.target('b'),home=target.parentNode;
  f.w.scrollY=470;link.click();const popup=f.popup('b');assert.ok(popup,'reference opens a reading window');
  assert.equal(target.parentNode,home);assert.equal(target.open,false);assert.equal(f.w.scrollY,470);
  assert.match(popup.textContent,/乙的解释/);popup.querySelector('[data-reader-popup-close]').click();
  assert.equal(d.activeElement,link);assert.equal(f.w.scrollY,470);
  const unlinked=d.getElementById('reader-unlinked');unlinked.querySelector('summary').click();
  assert.equal(unlinked.open,true);assert.match(unlinked.textContent,/未链接的批注全文/);checkUniqueIds(d);
});
test('windows leave original content, answers, media and scroll position unchanged',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});
  const {w,d}=f,target=f.target('a'),home=target.parentNode,next=target.nextSibling,origin=f.link('a');
  const input=d.createElement('input');input.value='我的作答';input.id='original-answer';target.append(input);
  const media=d.createElement('video');media.currentTime=12;target.append(media);
  w.scrollY=320;origin.click();const popup=f.popup('a');assert.ok(popup);
  assert.equal(popup.getAttribute('role'),'dialog');assert.equal(popup.getAttribute('aria-modal'),'false');
  assert.equal(target.parentNode,home);assert.equal(target.nextSibling,next);assert.equal(target.open,false);
  assert.equal(w.scrollY,320);assert.equal(d.getElementById('reader-return').hidden,true);
  assert.deepEqual(f.counts(),{mounted:1,disposed:0});
  assert.equal(input.value,'我的作答');assert.equal(media.currentTime,12);checkUniqueIds(d);
  popup.querySelector('[data-reader-popup-close]').click();
  assert.equal(f.popup('a'),null);assert.equal(w.scrollY,320);assert.equal(d.activeElement,origin);
  assert.equal(target.querySelector('input'),input);assert.equal(target.querySelector('video'),media);
});
test('nested references open independent windows and reusing an ancestor keeps both windows',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});
  f.link('a').click();const a=f.popup('a');assert.ok(a);
  a.querySelector(`a[href="#${ids.b}"]`).click();const b=f.popup('b');assert.ok(b);
  assert.equal(a.contains(b),false);assert.equal(b.contains(a),false);
  b.querySelector(`a[href="#${ids.a}"]`).click();
  assert.equal(f.popup('a'),a);assert.equal(f.popup('b'),b);assert.equal(f.d.querySelectorAll('.reader-popup').length,2);
  a.querySelector('[data-reader-popup-close]').click();assert.ok(f.popup('b'));
  b.querySelector('[data-reader-popup-close]').click();assert.equal(f.d.activeElement,f.link('a'));checkUniqueIds(f.d);
});
test('repeated references reuse one window and closing focuses the latest source',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});
  f.link('a').click();const popup=f.popup('a');assert.ok(popup);f.link('a',1).click();f.link('a',1).click();
  assert.equal(f.popup('a'),popup);assert.equal(f.d.querySelectorAll('.reader-popup').length,1);
  popup.querySelector('[data-reader-popup-close]').click();assert.equal(f.d.activeElement,f.link('a',1));
  assert.equal(f.link('a').getAttribute('aria-expanded'),'false');assert.equal(f.link('a',1).getAttribute('aria-expanded'),'false');checkUniqueIds(f.d);
});
test('references in lists and headings do not insert content into the reading flow',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const body=f.target('body').querySelector('.reader-block');
  const list=f.d.createElement('ul');list.innerHTML=`<li>列表说明 <a href="#${ids.a}">看解释</a></li>`;body.append(list);
  list.querySelector('a').click();assert.ok(f.popup('a'));
  assert.equal(list.children.length,1);assert.equal(list.querySelector('li').children.length,1);
  f.popup('a').querySelector('[data-reader-popup-close]').click();
  const heading=body.querySelector('h2'),link=f.d.createElement('a');link.href='#'+ids.a;link.textContent='说明';heading.append(link);
  const next=heading.nextSibling;link.click();assert.equal(heading.nextSibling,next);assert.ok(f.popup('a'));
});
test('example references open a window without scrolling or changing the example',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const target=f.target('example'),home=target.parentNode;
  f.w.scrollY=410;f.link('example').click();assert.equal(target.parentNode,home);
  const popup=f.popup('example');assert.ok(popup);assert.match(popup.textContent,/例题完整题目/);
  assert.equal(target.dataset.scrolled,undefined);assert.equal(f.w.scrollY,410);
  popup.querySelector('[data-reader-popup-close]').click();assert.equal(f.d.activeElement,f.link('example'));
  assert.deepEqual(f.counts(),{mounted:1,disposed:0});
});
test('example windows close their own statement and every nested example even after the source was expanded',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});
  const style=f.d.createElement('style');style.textContent=fs.readFileSync(path.join(render,'assets/reader-book.css'),'utf8');f.d.head.appendChild(style);
  const source=f.target('example');source.open=true;
  const nested=f.d.createElement('details');nested.dataset.readerGroup='examples';nested.open=true;
  nested.innerHTML='<summary>嵌套例题</summary><p>嵌套题干与答案</p>';source.appendChild(nested);
  f.link('example').click();const popup=f.popup('example');assert.ok(popup);
  const copy=popup.querySelector('[data-reader-group="examples"]');assert.ok(copy);assert.equal(copy.open,false,'opening a reference window must not open the example itself');
  const child=copy.querySelector('[data-reader-group="examples"]');assert.equal(child.open,false);
  const statement=copy.querySelector('[data-reader-pointer="/examples/0/statement"]');assert.ok(statement.closest('details:not([open])'));
  const summary=copy.querySelector(':scope > summary');assert.ok(summary);assert.notEqual(f.w.getComputedStyle(summary).display,'none','the closed example needs a usable title entry');
  summary.click();assert.equal(copy.open,true);assert.equal(child.open,false);assert.equal(source.open,true);assert.equal(nested.open,true);
});
test('Escape closes only the active window while directory and font controls remain usable',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {w,d}=f;
  f.link('a').click();assert.ok(f.popup('a'));f.popup('a').querySelector(`a[href="#${ids.b}"]`).click();assert.ok(f.popup('b'));
  d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.ok(f.popup('a'));assert.equal(f.popup('b'),null);
  d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(f.popup('a'),null);
  const toggle=d.getElementById('reader-toc-toggle');toggle.click();
  assert.ok(d.getElementById('sidebar').classList.contains('is-open'));assert.equal(toggle.getAttribute('aria-expanded'),'true');
  d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(d.getElementById('sidebar').classList.contains('is-open'),false);
  d.getElementById('reader-font-plus').click();assert.equal(d.body.style.getPropertyValue('--reader-font-size'),'18px');
  d.getElementById('reader-font-minus').click();assert.equal(d.body.style.getPropertyValue('--reader-font-size'),'17px');
  for(let i=0;i<20;i++)d.getElementById('reader-font-minus').click();assert.equal(d.body.style.getPropertyValue('--reader-font-size'),'14px');
});
test('numbered references copy only their definition with rendered math and local labels',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {d}=f;
  const body=f.target('body').querySelector('.reader-block');const property=d.createElement('section');
  property.id='reader-property-two';property.dataset.readerReference='性质2';
  property.innerHTML='<p>性质2 <span class="katex">已渲染的公式</span></p><p>证明。</p><label for="choice">选择</label><input id="choice" name="answer">';
  body.append(property);const unrelated=d.createElement('p');unrelated.textContent='性质3的其他内容';body.append(unrelated);
  const origin=f.link('a');origin.href='#reader-property-two';origin.click();const popup=f.popup('reader-property-two');assert.ok(popup);
  assert.ok(popup.querySelector('.katex'));assert.match(popup.textContent,/证明/);assert.doesNotMatch(popup.textContent,/性质3的其他内容/);
  const input=popup.querySelector('input'),label=popup.querySelector('label');
  assert.notEqual(input.id,'choice');assert.equal(label.htmlFor,input.id);assert.notEqual(input.name,'answer');
  assert.equal(d.getElementById('reader-property-two'),property);checkUniqueIds(d);
});
function pointer(w,node,type,x,y,id=7) {
  const event=new w.MouseEvent(type,{bubbles:true,cancelable:true,clientX:x,clientY:y,button:0});
  Object.defineProperties(event,{pointerId:{value:id},pointerType:{value:'touch'}});node.dispatchEvent(event);
}
test('touch dragging and resize keep windows visible and closing cancels drag',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {w,d}=f;
  Object.defineProperties(w,{innerWidth:{value:700,configurable:true},innerHeight:{value:500,configurable:true}});
  f.link('a').click();const popup=f.popup('a');assert.ok(popup);
  popup.getBoundingClientRect=()=>({left:parseFloat(popup.style.left)||0,top:parseFloat(popup.style.top)||0,width:360,height:240});
  const grip=popup.querySelector('[data-reader-popup-drag]');assert.ok(grip);
  pointer(w,grip,'pointerdown',parseFloat(popup.style.left)+10,parseFloat(popup.style.top)+10);
  pointer(w,d,'pointermove',2000,2000);assert.equal(popup.style.left,'332px');assert.equal(popup.style.top,'252px');
  pointer(w,d,'pointermove',-100,-100);assert.equal(popup.style.left,'8px');assert.equal(popup.style.top,'8px');pointer(w,d,'pointerup',0,0);
  popup.style.left='332px';popup.style.top='252px';Object.defineProperties(w,{innerWidth:{value:500,configurable:true},innerHeight:{value:300,configurable:true}});
  w.dispatchEvent(new w.Event('resize'));assert.equal(popup.style.left,'132px');assert.equal(popup.style.top,'52px');
  pointer(w,grip,'pointerdown',140,60);popup.querySelector('[data-reader-popup-close]').click();
  const last=popup.style.cssText;pointer(w,d,'pointermove',1000,1000);assert.equal(popup.style.cssText,last);
});
test('cloned animation controls are independent and dispose on close and lesson change',t=>{
  const f=fixture({realAnimation:true});t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {w,d}=f;
  const target=f.target('a'),player=d.querySelector('[data-animation-player]');target.append(player);
  const original=player.querySelector('video');original.currentTime=12;f.link('a').click();let popup=f.popup('a');assert.ok(popup);
  let copy=popup.querySelector('video');assert.ok(copy);assert.notEqual(copy,original);
  popup.querySelector('[data-action="play"]').click();assert.equal(copy.dataset.played,'true');assert.equal(original.dataset.played,undefined);assert.equal(original.currentTime,12);
  popup.querySelector('[data-reader-popup-close]').click();assert.equal(copy.hasAttribute('src'),false);assert.ok(original.hasAttribute('src'));assert.equal(original.currentTime,12);
  f.link('a').click();popup=f.popup('a');copy=popup.querySelector('video');pointer(w,popup.querySelector('[data-reader-popup-drag]'),'pointerdown',30,30);
  w.history.replaceState(null,'','#/V');w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  assert.equal(d.querySelector('.reader-popup'),null);assert.equal(copy.hasAttribute('src'),false);assert.equal(original.hasAttribute('src'),false);assert.equal(d.activeElement,d.getElementById('content'));
});

test('pointer cancellation and keyboard movement preserve nonmodal access to the page',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {w,d}=f;
  f.link('a').click();const popup=f.popup('a'),head=popup.querySelector('[data-reader-popup-drag]');
  popup.getBoundingClientRect=()=>({left:parseFloat(popup.style.left),top:parseFloat(popup.style.top),width:360,height:240});
  pointer(w,head,'pointerdown',40,110);pointer(w,d,'pointermove',100,160);
  pointer(w,d,'pointercancel',100,160);const stopped=popup.style.cssText;
  pointer(w,d,'pointermove',200,300);assert.equal(popup.style.cssText,stopped);
  const before=parseFloat(popup.style.left);head.focus();
  head.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true}));
  assert.equal(parseFloat(popup.style.left),before+20);
  d.getElementById('reader-font-plus').focus();d.getElementById('reader-font-plus').click();
  assert.equal(d.activeElement,d.getElementById('reader-font-plus'));assert.ok(f.popup('a'));
  assert.equal(d.body.style.getPropertyValue('--reader-font-size'),'18px');
});
test('pagehide removes windows and stops a drag without clearing the original source early',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {w,d}=f;
  f.link('a').click();const popup=f.popup('a');pointer(w,popup.querySelector('[data-reader-popup-drag]'),'pointerdown',20,100);
  assert.ok(f.target('a').isConnected);w.dispatchEvent(new w.Event('pagehide'));
  assert.equal(d.querySelector('.reader-popup'),null);assert.equal(f.link('a').getAttribute('aria-expanded'),'false');
  const stopped=popup.style.cssText;pointer(w,d,'pointermove',200,300);assert.equal(popup.style.cssText,stopped);
  assert.deepEqual(f.counts(),{mounted:1,disposed:1});
});

test('body headings remain readable and progress works without local directory anchors',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {w,d}=f;
  assert.equal(d.querySelector('#reader-section-toc'),null);
  assert.match(d.getElementById('content').textContent,/四则运算.*运算步骤/s);
  Object.defineProperty(d.documentElement,'scrollHeight',{value:2200,configurable:true});
  Object.defineProperty(w,'innerHeight',{value:600,configurable:true});w.scrollY=800;
  w.dispatchEvent(new w.Event('scroll'));assert.equal(d.querySelector('#reader-progress span').style.width,'50%');
  assert.equal(d.getElementById('scope-note').textContent,'共 2 节 · 离线阅读');
});

test('changing lessons clears lookups and disposes only the outgoing animation',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {w,d}=f;
  f.link('a').click();d.getElementById('reader-toc-toggle').click();
  w.history.replaceState(null,'','#/V');w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  assert.match(d.getElementById('content').textContent,/第二节正文/);
  assert.equal(d.querySelectorAll('.reader-popup').length,0);assert.equal(d.getElementById('reader-return').hidden,true);
  assert.equal(d.getElementById('sidebar').classList.contains('is-open'),false);
  assert.deepEqual(f.counts(),{mounted:1,disposed:1});
  assert.ok(d.querySelector('.pager .prev'));assert.equal(d.querySelector('#reader-section-toc'),null);assert.match(d.getElementById('content').textContent,/共轭复数/);
});

test('legacy concept hashes still open their explanation without resetting the current lesson',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const original=f.target('body');
  f.w.history.replaceState(null,'','#concept=C');f.w.dispatchEvent(new f.w.HashChangeEvent('hashchange'));
  assert.match(f.d.getElementById('drawer-body').textContent,/旧概念解释/);
  assert.ok(f.d.getElementById('drawer').classList.contains('open'));assert.equal(f.target('body'),original);
});

test('cross-lesson equation links return to the original sentence and scroll position',t=>{
  const f=fixture({numberLink:true});t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {w,d}=f;
  const later=[];const schedule=w.setTimeout.bind(w);
  w.setTimeout=function(fn,delay){if(delay===450){later.push(fn);return 0;}return schedule(fn,delay);};
  w.scrollY=345;d.querySelector('a.numref').click();
  assert.match(d.getElementById('content').textContent,/第二节正文/);
  assert.equal(d.getElementById('num-1-1').dataset.scrolled,'true');
  assert.equal(d.getElementById('reader-return').hidden,false);
  d.getElementById('reader-return').click();
  assert.match(d.getElementById('content').textContent,/这里使用式/);
  assert.equal(w.scrollY,345);assert.equal(d.activeElement,d.querySelector('a.numref'));
  assert.deepEqual(f.counts(),{mounted:2,disposed:1});
  assert.equal(later.length,1);later.forEach(fn=>fn());
  assert.equal(w.scrollY,345,'the old target cannot scroll a page after the reader has returned');
});

test('ordinary lesson navigation restores each lesson reading position',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {w,d}=f;
  w.scrollY=360;w.history.replaceState(null,'','#/V');w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  assert.equal(w.scrollY,0);w.scrollY=810;
  w.history.replaceState(null,'','#/U');w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  assert.equal(w.scrollY,360);assert.equal(d.getElementById('reader-return').hidden,true);
  w.history.replaceState(null,'','#/V');w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  assert.equal(w.scrollY,810);
});

test('legacy concept occurrence links return to their original unit and explanation',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {w,d}=f;
  w.scrollY=280;w.history.replaceState(null,'','#concept=C');w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  d.querySelector('#drawer-body a[href="#/V"]').click();
  assert.match(d.getElementById('content').textContent,/第二节正文/);
  d.getElementById('reader-return').click();
  assert.ok(f.target('body'));assert.equal(w.scrollY,280);
  assert.ok(d.getElementById('drawer').classList.contains('open'));
  assert.equal(d.activeElement,d.querySelector('#drawer-body a[href="#/V"]'));
});

test('a prior reading window cannot shift the cross-lesson return to a different sentence',t=>{
  const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {w,d}=f;
  f.link('a').click();
  const origin=f.link('a',1);
  // Exercise the shared cross-unit route with a source link in a later native
  // reader block. Its label and original position survive the unit rebuild.
  origin.href='#/V';w.scrollY=430;origin.click();
  d.getElementById('reader-return').click();
  assert.equal(w.scrollY,430);
  assert.equal(d.activeElement,f.link('a',1),'the source sentence, rather than its shifted DOM child index, is restored');
});

test('human book title is displayed while the source book identity is retained',t=>{
 const f=fixture({displayTitle:'复变函数学习阅读器'});t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});assert.equal(f.d.getElementById('book-title').textContent,'复变函数学习阅读器');assert.equal(f.w.__DATA__.book,'复变函数');
});

test('reference windows mount independent study scenes and dispose their scope on close and navigation',t=>{
 const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {w,d}=f,source=f.target('a');
 const original=d.createElement('section');original.className='study-visual';original.innerHTML='<canvas></canvas><div class="study-tools"><button>源图控件</button></div>';source.appendChild(original);original.querySelector('canvas').toDataURL=()=> 'data:image/png;base64,AA==';
 const mounted=[],disposed=[];
 w.DSHStudyVisuals={mountReaderScope(container,options){assert.equal(options.data,w.__DATA__);assert.equal(options.unitId,'U');assert.ok(container.isConnected);const stale=container.querySelector('.study-visual');if(stale)stale.remove();const scene=d.createElement('section');scene.className='study-visual';scene.innerHTML='<button>副本独立控件</button>';scene.querySelector('button').addEventListener('click',()=>scene.dataset.updated='true');container.appendChild(scene);mounted.push(container);return()=>{disposed.push(container);scene.remove();};}};
 f.link('a').click();const popup=f.popup('a');assert.ok(popup.querySelector('.study-visual button'));popup.querySelector('.study-visual button').click();assert.equal(popup.querySelector('.study-visual').dataset.updated,'true');assert.equal(original.dataset.updated,undefined);assert.ok(source.contains(original));assert.ok(original.querySelector('canvas'));
 f.link('a',1).click();assert.equal(mounted.length,1,'reusing an existing window does not create a second controller');popup.querySelector('[data-reader-popup-close]').click();assert.equal(disposed.length,1);
 f.link('a').click();assert.equal(mounted.length,2);w.history.replaceState(null,'','#/V');w.dispatchEvent(new w.HashChangeEvent('hashchange'));assert.equal(disposed.length,2);
});

test('legacy concept drawers replace and dispose their independent study scope',t=>{
 const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const {w,d}=f;let mounted=0,disposed=0;
 w.DSHStudyVisuals={mountReaderScope(container,options){assert.equal(container,d.getElementById('drawer-body'));assert.equal(options.unitId,'U');mounted++;const marker=d.createElement('button');marker.textContent='独立抽屉模型';container.appendChild(marker);return()=>{disposed++;marker.remove();};}};
 w.history.replaceState(null,'','#concept=C');w.dispatchEvent(new w.HashChangeEvent('hashchange'));assert.ok([...d.getElementById('drawer-body').querySelectorAll('button')].some(n=>n.textContent==='独立抽屉模型'));assert.equal(mounted,1);
 w.dispatchEvent(new w.HashChangeEvent('hashchange'));assert.equal(mounted,2);assert.equal(disposed,1,'replacing drawer content releases its prior scene');d.getElementById('drawer-close').click();assert.equal(disposed,2);
});

test('clicking inside the original linked example title expands once instead of opening a reference popup',async t=>{
 const f=fixture({numberedExample:true});t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[]);});const original=JSON.stringify(f.w.__DATA__.units);
 const outer=f.d.querySelector('[data-reader-topic-kind="example"] > details.reader-example-disclosure'),summary=outer.querySelector(':scope > summary');assert.ok(outer);assert.equal(outer.open,false);
 const title=summary.querySelector('a,.reader-example-title-reference')||summary.querySelector('.reader-topic-title');assert.ok(title);title.click();await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(outer.open,true,'clicking the heading descendant must perform the native summary action');assert.equal(f.target('example').open,true);assert.equal(f.popup('example'),null,'the expansion title is not a popup link');
 const statement=f.target('example').querySelector('[data-reader-pointer="/examples/0/statement"]');assert.equal(statement.closest('details:not([open])'),null);assert.equal(JSON.stringify(f.w.__DATA__.units),original);
 const reference=[...f.d.querySelectorAll('[data-reader-pointer="/body_markdown"] a')].find(a=>a.textContent.includes('引用例3'));assert.ok(reference);reference.click();assert.ok(f.popup('example'),'ordinary references outside the summary still open a window');
});
