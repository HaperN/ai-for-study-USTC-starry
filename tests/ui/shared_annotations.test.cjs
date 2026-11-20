const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const {JSDOM}=require('jsdom'),{webcrypto}=require('node:crypto');
const mod=()=>require(path.resolve(__dirname,'../../src/aiforstudy/resources/community/shared-notes.js'));
const html='<header><div class="book-controls"></div></header><main id="content"><article><div data-reader-pointer="/body_markdown"><p>复数的模表示距离。</p><p>复数的模表示距离。</p></div></article></main>';
function setup(t,markup=html){const dom=new JSDOM(markup,{url:'http://127.0.0.1:8791/#/CH01',pretendToBeVisual:true});t.after(()=>dom.window.close());const w=dom.window;Object.defineProperty(w,'crypto',{value:webcrypto});w.TextEncoder=TextEncoder;w.AbortController=AbortController;w.__AIFS_SHARED_NOTES__={book:'book',revision:'v1',api:'/api/annotations'};w.__DATA__={scope:['CH01','CH02'],units:{CH01:{},CH02:{}}};w.document.documentElement.dataset.readerUnit='CH01';return w;}
const response=(value,status=200)=>({ok:status<400,status,json:async()=>value});
const list=(items=[])=>({items,counts:{},total:items.length,next_before:null});
async function until(predicate){for(let i=0;i<100;i++){if(predicate())return;await new Promise(r=>setTimeout(r,10));}assert.fail('UI did not reach the expected state');}
function note(text,unit='CH01'){return {id:1,unit_id:unit,anchor:'1'.repeat(64),kind:'text',quote:'原文',nickname:'同学',text,created_at:1,is_mine:false};}

test('math anchor uses source LaTeX once and ignores injected controls',t=>{
 const w=setup(t,'<main><p>观察 <span class="katex"><span class="katex-mathml"><math><annotation encoding="application/x-tex">x^2</annotation></math></span><span class="katex-html">x²</span></span><button data-community-ui>写感悟</button></p></main>');
 assert.equal(mod().canonicalText(w.document.querySelector('p')),'观察 $x^2$');
});
test('identical paragraphs have distinct stable anchors across browsers and reflow',async t=>{
 const a=setup(t),b=setup(t);b.document.querySelector('main').style.fontSize='24px';
 let aa=await mod().describeTargets(a.document.querySelector('main'),'CH01');
 let bb=await mod().describeTargets(b.document.querySelector('main'),'CH01');
 assert.equal(aa.length,2);assert.notEqual(aa[0].anchor,aa[1].anchor);assert.deepEqual(aa.map(x=>x.anchor),bb.map(x=>x.anchor));
 const marker=a.document.createElement('button');marker.dataset.communityUi='';marker.textContent='3条感悟';a.document.querySelector('p').append(marker);
 assert.deepEqual((await mod().describeTargets(a.document.querySelector('main'),'CH01')).map(x=>x.anchor),aa.map(x=>x.anchor));
 assert.notEqual((await mod().describeTargets(a.document.querySelector('main'),'CH02'))[0].anchor,aa[0].anchor);
});
test('a media anchor follows its teaching ID rather than interactive readout or rail position',async t=>{
 const w=setup(t,'<main id="content"><div data-reader-pointer="/body_markdown"><section class="teaching-block" data-teaching-id="complex-plane"><h3>复平面</h3><p class="readout">x=2</p></section><div id="rail"></div></div></main>');
 const first=await mod().describeTargets(w.document.querySelector('main'),'CH01');
 w.document.querySelector('.readout').textContent='x=5';w.document.querySelector('#rail').append(w.document.querySelector('section'));
 const next=await mod().describeTargets(w.document.querySelector('main'),'CH01');
 assert.equal(first.length,1);assert.equal(first[0].kind,'media');assert.equal(first[0].anchor,next[0].anchor);
});
test('a late response for an old chapter cannot overwrite the new chapter notes',async t=>{
 const w=setup(t);let oldResolve;
 w.fetch=async url=>String(url).includes('unit=CH01')?new Promise(resolve=>{oldResolve=resolve}):response(list([note('第二节感悟','CH02')]));
 mod().mount(w);await until(()=>oldResolve);
 w.document.querySelector('main').innerHTML='<article><p data-reader-pointer="/body_markdown">第二节正文</p></article>';
 w.document.documentElement.dataset.readerUnit='CH02';w.location.hash='#/CH02';
 await until(()=>w.document.querySelector('.community-list')?.textContent.includes('第二节感悟'));
 oldResolve(response(list([note('旧节迟到内容')])));await new Promise(r=>setTimeout(r,20));
 assert.ok(!w.document.querySelector('.community-list').textContent.includes('旧节迟到内容'));
});
test('failed publishing preserves the draft and retry reuses its request identity',async t=>{
 const w=setup(t);const posted=[];
 w.fetch=async(url,options={})=>{if(options.method==='POST'){posted.push(JSON.parse(options.body));return response({error:'暂时无法保存'},503)}return response(list())};
 mod().mount(w);await until(()=>w.document.querySelector('.community-note-mark'));
 w.document.querySelector('.community-note-mark').click();
 w.document.querySelector('#community-nickname').value='小明';w.document.querySelector('#community-text').value='重试应保留这段文字';
 const form=w.document.querySelector('.community-form');form.dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));
 await until(()=>w.document.querySelector('.community-status').textContent.includes('暂时无法保存'));
 assert.equal(w.document.querySelector('#community-text').value,'重试应保留这段文字');
 form.dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));await until(()=>posted.length===2);
 assert.equal(posted[0].request_id,posted[1].request_id);assert.match(w.localStorage.getItem('aifs:community:author'),/^[a-f0-9]{64}$/);
});
test('public note text is inert HTML and unavailable storage still allows reading',async t=>{
 const w=setup(t);Object.defineProperty(w,'localStorage',{get(){throw new Error('blocked')}});
 w.fetch=async()=>response(list([note('<img src=x onerror=alert(1)>')]));mod().mount(w);
 await until(()=>w.document.querySelector('.community-note-text'));
 assert.equal(w.document.querySelector('.community-note-text').textContent,'<img src=x onerror=alert(1)>');
 assert.equal(w.document.querySelectorAll('.community-list img').length,0);
 await until(()=>w.document.querySelector('.community-note-mark'));w.document.querySelector('.community-note-mark').click();
 w.document.querySelector('#community-nickname').value='小明';w.document.querySelector('#community-text').value='感悟';
 w.document.querySelector('.community-form').dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));
 await until(()=>w.document.querySelector('.community-status').textContent.includes('浏览器'));
 assert.equal(w.document.querySelector('#community-text').value,'感悟');
});

test('successful publishing does not erase new text typed while the request is pending',async t=>{
 const w=setup(t);let finish;
 w.fetch=async(url,o={})=>o.method==='POST'?new Promise(r=>{finish=r}):response(list());
 mod().mount(w);await until(()=>w.document.querySelector('.community-note-mark'));w.document.querySelector('.community-note-mark').click();
 w.document.querySelector('#community-nickname').value='小明';w.document.querySelector('#community-text').value='已发送的内容';
 w.document.querySelector('.community-form').dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));await until(()=>finish);
 w.document.querySelector('#community-text').value='请求期间继续写的新草稿';finish(response({item:note('已发送的内容'),created:true},201));
 await until(()=>!w.document.querySelector('.community-submit').disabled);
 assert.equal(w.document.querySelector('#community-text').value,'请求期间继续写的新草稿');
});
test('a pending list refresh cannot hide a publishing error',async t=>{
 const w=setup(t);let delayedRead,gets=0;
 w.fetch=async(url,o={})=>{if(o.method==='POST')return response({error:'保存失败，稍后重试'},503);if(++gets>1)return new Promise(r=>{delayedRead=r});return response(list());};
 mod().mount(w);await until(()=>w.document.querySelector('.community-note-mark'));w.document.querySelector('.community-note-mark').click();await until(()=>delayedRead);
 w.document.querySelector('#community-nickname').value='小明';w.document.querySelector('#community-text').value='草稿';w.document.querySelector('.community-form').dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));
 await until(()=>w.document.querySelector('.community-status').textContent.includes('保存失败'));
 delayedRead(response(list()));await new Promise(r=>setTimeout(r,20));assert.ok(w.document.querySelector('.community-status').textContent.includes('保存失败'));
});
test('publishing an old unit cannot insert its response or clear a new unit draft',async t=>{
 const w=setup(t);let finish;
 w.fetch=async(url,o={})=>o.method==='POST'?new Promise(r=>{finish=r}):response(list());
 mod().mount(w);await until(()=>w.document.querySelector('.community-note-mark'));w.document.querySelector('.community-note-mark').click();w.document.querySelector('#community-nickname').value='小明';w.document.querySelector('#community-text').value='第一节';w.document.querySelector('.community-form').dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));await until(()=>finish);
 w.document.querySelector('main').innerHTML='<article><p data-reader-pointer="/body_markdown">第二节</p></article>';w.document.documentElement.dataset.readerUnit='CH02';
 await until(()=>w.document.documentElement.dataset.communityUnit==='CH02');w.document.querySelector('.community-note-mark').click();w.document.querySelector('#community-text').value='第二节的未发送草稿';
 finish(response({item:note('第一节'),created:true},201));await until(()=>!w.document.querySelector('.community-submit').disabled);
 assert.equal(w.document.querySelector('#community-text').value,'第二节的未发送草稿');assert.ok(!w.document.querySelector('.community-list').textContent.includes('第一节'));
});

test('leaving for original-material pages retires the old form and late notes response',async t=>{
 const w=setup(t);let delayed;
 w.fetch=async()=>new Promise(r=>{delayed=r});mod().mount(w);await until(()=>w.document.querySelector('.community-note-mark'));w.document.querySelector('.community-note-mark').click();await until(()=>delayed);
 w.document.querySelector('#community-nickname').value='小明';w.document.querySelector('#community-text').value='旧正文草稿';
 w.location.hash='#/materials/source-1';w.document.querySelector('main').innerHTML='<article><p>原书资料页</p></article>';w.document.documentElement.dataset.readerUnit='material:source-1';
 await new Promise(r=>setTimeout(r,60));delayed(response(list([note('上一节的迟到感悟')])));await new Promise(r=>setTimeout(r,20));
 assert.equal(w.document.querySelector('.community-form').hidden,true);
 assert.equal(w.document.querySelector('.community-selected').textContent,'');
 assert.ok(!w.document.querySelector('.community-list').textContent.includes('上一节'));
 assert.equal(w.document.querySelectorAll('.community-note-mark').length,0);
});
test('changing selected paragraph clears old deletion controls even when the new load fails',async t=>{
 const w=setup(t);let reads=0;
 w.fetch=async()=>++reads<=2?response({...list([{...note('A段落的评论'),is_mine:true}]),next_before:1}):response({error:'B暂时无法读取'},503);
 mod().mount(w);await until(()=>w.document.querySelectorAll('.community-note-mark').length===2);
 w.document.querySelectorAll('.community-note-mark')[0].click();await until(()=>w.document.querySelector('.community-delete'));
 w.document.querySelectorAll('.community-note-mark')[1].click();await until(()=>w.document.querySelector('.community-status').textContent.includes('B暂时'));
 assert.equal(w.document.querySelectorAll('.community-note').length,0);assert.equal(w.document.querySelectorAll('.community-delete').length,0);assert.equal(w.document.querySelector('.community-more').hidden,true);
});

test('school adapter mode can submit with a server session without browser nickname credentials',async t=>{
 const w=setup(t);w.__AIFS_SHARED_NOTES__.identity_mode='school';Object.defineProperty(w,'localStorage',{get(){throw Error('blocked')}});let posted;
 w.fetch=async(url,o={})=>{if(String(url)==='/api/identity')return response({mode:'school',authenticated:true,display_name:null,can_publish:true,login_path:null});if(o.method==='POST'){posted=o;return response({item:note('学校会话发布'),created:true},201)}return response(list())};
 mod().mount(w);await until(()=>w.document.querySelector('.community-note-mark'));w.document.querySelector('.community-note-mark').click();w.document.querySelector('#community-nickname').value='张同学';w.document.querySelector('#community-text').value='学校会话发布';
 w.document.querySelector('.community-form').dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));await until(()=>posted||w.document.querySelector('.community-status').textContent.includes('浏览器无法'));
 assert.ok(posted,'school mode must use the server-authenticated session, not require anonymous browser storage');assert.equal(posted.headers.Authorization,undefined);
});

test('a school display name is shown and locked before public submission',async t=>{
 const w=setup(t);w.__AIFS_SHARED_NOTES__.identity_mode='school';w.__AIFS_SHARED_NOTES__.identity_api='/api/identity';let posted;
 w.fetch=async(url,o={})=>{if(String(url)==='/api/identity')return response({mode:'school',authenticated:true,display_name:'校内张同学',can_publish:true,login_path:null});if(o.method==='POST'){posted=JSON.parse(o.body);return response({created:true,item:note('学校署名')},201)}return response(list());};
 mod().mount(w);await until(()=>w.document.querySelector('.community-note-mark'));w.document.querySelector('.community-note-mark').click();
 await until(()=>w.document.querySelector('#community-nickname').value==='校内张同学');
 assert.equal(w.document.querySelector('#community-nickname').readOnly,true);assert.ok(w.document.querySelector('.community-identity').textContent.includes('校内张同学'));
 w.document.querySelector('#community-text').value='学校署名';w.document.querySelector('.community-form').dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));await until(()=>posted);assert.equal(posted.nickname,'校内张同学');
});

test('quoted textbook formulas display as math in selected and public contexts',async t=>{
 const w=setup(t);w.katex=require('../../blocks/render/vendor/katex.min.js');w.AIFSCommunityImages=require('../../src/aiforstudy/resources/community/community-images.js');
 w.document.querySelector('p').innerHTML='公式 '+w.katex.renderToString('x^2');
 w.fetch=async()=>response(list([{...note('讨论这个公式'),quote:'公式 $x^2$'}]));mod().mount(w);
 await until(()=>w.document.querySelector('.community-quote'));
 assert.equal(w.document.querySelectorAll('.community-quote .katex').length,1);
 w.document.querySelector('.community-note-mark').click();await until(()=>w.document.querySelector('.community-selected .katex'));
 assert.equal(w.document.querySelectorAll('.community-selected .katex').length,1);
});

test('stale unit cannot submit during the render debounce',async t=>{
 const w=setup(t);let posts=0;
 w.fetch=async(url,options={})=>{if(options.method==='POST')posts++;return response(list());};
 mod().mount(w);await until(()=>w.document.querySelector('.community-note-mark'));
 w.document.querySelector('.community-note-mark').click();
 w.document.querySelector('#community-nickname').value='同学';w.document.querySelector('#community-text').value='草稿';
 w.location.hash='#/CH02';
 w.document.querySelector('.community-form').dispatchEvent(new w.Event('submit',{cancelable:true}));
 assert.equal(posts,0);
});

test('edit conflict preserves separate draft and original expected revision',async t=>{
 const w=setup(t);let rev=1;const updates=[];
 const item=()=>({...note('服务器第'+rev+'版'),revision:rev,is_mine:true});
 w.fetch=async(url,o={})=>{
   if(o.method==='PATCH'){updates.push(JSON.parse(o.body));rev=2;return response({error:'版本冲突'},409);}
   return response(list([item()]));
 };
 mod().mount(w);await until(()=>w.document.querySelector('.community-edit'));
 w.document.querySelector('.community-edit').click();const edit=w.document.querySelector('.community-edit-text');
 edit.value='我的编辑草稿';edit.dispatchEvent(new w.Event('input'));
 w.document.querySelector('.community-edit-save').click();await until(()=>updates.length);
 await until(()=>w.document.querySelector('.community-edit-review'));
 assert.equal(w.document.querySelector('.community-edit-text').value,'我的编辑草稿');
 assert.equal(w.document.querySelector('#community-text').value,'');
 assert.equal(updates[0].revision,1);
 assert.equal(w.document.querySelector('.community-edit-save').disabled,true);
 w.document.querySelector('.community-edit-review').click();
 assert.equal(w.document.querySelector('.community-edit-save').disabled,false);
});

test('source reveal opens ancestor disclosures and selects a hidden rail item',t=>{
 const w=setup(t,'<main><details id="outer"><details id="inner"><p>隐藏的原文</p></details></details><details data-shared-media-target="media-1"><summary>选择动画</summary></details><aside class="lesson-media-rail"><section class="teaching-block" data-teaching-id="media-1" hidden></section></aside></main>');
 const main=w.document.querySelector('main'),media=main.querySelector('section');
 mod().revealTarget(main,main.querySelector('p'));
 assert.ok(main.querySelector('#outer').open&&main.querySelector('#inner').open);
 main.querySelector('[data-shared-media-target] summary').onclick=()=>{media.hidden=false;};
 mod().revealTarget(main,media);assert.equal(media.hidden,false);
});
