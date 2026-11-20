const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'../../blocks/render');
function fixture({motion=false}={}){
 const html=fs.readFileSync(path.join(root,'templates/index.html'),'utf8');
 const dom=new JSDOM(html,{url:'https://reader.invalid/#/A',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,errors=[];
 w.addEventListener('error',e=>errors.push(e.message));
 w.scrollTo=(x,y)=>{w.scrollY=typeof x==='object'?x.top:y};
 w.HTMLElement.prototype.scrollIntoView=function(){this.dataset.scrolled='true'};
 if(motion)w.matchMedia=()=>({matches:false});
 const b=(group,owner,pointer,markdown,anchor,collapsed=false)=>({group,owner_id:owner,json_pointer:pointer,markdown,raw_text:markdown,anchor,collapsed,block_id:pointer,field_label:group==='annotations'?'易错':owner});
 const anchor=(id,suffix)=>'reader-'+(id==='A'?'1':'2')+'0'.repeat(18)+({title:0,body:1,note:2}[suffix]);
 const unit=(id,title,term)=>({title,body_markdown:`## 开始\n\n先读开场。\n\n## ${term}\n\n公式 $z\\neq0$。正文[查看解释](#${anchor(id,'note')})。`,reader_document:{schema_version:'ReaderDocumentV1',blocks:[b('title',id,'/title',title,anchor(id,'title')),b('body_markdown',id,'/body_markdown',`## 开始\n\n先读开场。\n\n## ${term}\n\n公式 $z\\neq0$。正文[查看解释](#${anchor(id,'note')})。`,anchor(id,'body')),b('annotations','note','/annotations/0/title',term+'提醒',anchor(id,'note'),true),b('annotations','note','/annotations/0/markdown',term+'必须保留条件。',anchor(id,'note'),true)]}});
 w.__DATA__={book:'不同教材',scope:['A','B'],units:{A:unit('A','第一节','矩阵'),B:unit('B','第二节','极限')},concepts:{},tree:[{id:'A',title:'第一节',unit:true},{id:'B',title:'第二节',unit:true}]};
 for(const p of ['vendor/marked.min.js','vendor/katex.min.js','assets/reader-markdown.js','assets/render-core.js','assets/reader-layout.js','assets/app.js'])if(fs.existsSync(path.join(root,p)))w.eval(fs.readFileSync(path.join(root,p),'utf8'));
 return {w,d,dom,errors};
}

test('production search finds another unit without changing source data and returns to reading',t=>{
 const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[])});
 const original=JSON.stringify(f.w.__DATA__.units);
 const toggle=f.d.getElementById('reader-search-toggle');assert.ok(toggle,'production template exposes search');toggle.click();
 const input=f.d.getElementById('reader-search-input');assert.ok(input);input.value='极限';input.dispatchEvent(new f.w.Event('input',{bubbles:true}));
 const result=f.d.querySelector('[data-reader-search-unit="B"]');assert.ok(result);assert.match(result.textContent,/极限|第二节/);result.click();
 assert.match(f.d.getElementById('content').textContent,/第二节/);assert.equal(f.d.getElementById('reader-search-layer').hidden,true);assert.equal(JSON.stringify(f.w.__DATA__.units),original);
 assert.ok(f.d.querySelector('[data-reader-topic][data-scrolled="true"]'),'search scrolls to the matching deep topic rather than the unit start');
 const preview=f.d.getElementById('reader-search-results').cloneNode(true);preview.querySelectorAll('.katex-mathml').forEach(n=>n.remove());
 assert.doesNotMatch(preview.textContent,/\\neq|\$|\[\[EXPLAIN/);
 assert.ok(preview.querySelector('.katex'),'search snippets retain formatted math instead of flattening it');
 const back=f.d.getElementById('reader-return');assert.equal(back.hidden,false);back.click();assert.match(f.d.getElementById('content').textContent,/第一节/);
});

test('comparison control changes only presentation while math and references remain',t=>{
 const f=fixture();t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[])});
 const original=f.d.getElementById('content').innerHTML,button=f.d.getElementById('reader-compare-toggle');assert.ok(button);button.click();
 assert.ok(f.d.body.classList.contains('reader-reading-only'));assert.equal(button.textContent.trim(),'开启对照');assert.equal(f.d.getElementById('content').innerHTML,original);button.click();assert.equal(f.d.body.classList.contains('reader-reading-only'),false);
});

test('closing then immediately reopening a reference keeps the same live window',async t=>{
 const f=fixture({motion:true});t.after(()=>{f.dom.window.close();assert.deepEqual(f.errors,[])});
 const link=f.d.querySelector('#content a[href="#reader-10000000000000000002"]');link.click();let popup=f.d.querySelector('.reader-popup');assert.ok(popup);
 popup.querySelector('[data-reader-popup-close]').click();assert.ok(popup.classList.contains('is-closing'));link.click();assert.equal(f.d.querySelector('.reader-popup'),popup);assert.equal(popup.classList.contains('is-closing'),false);
 await new Promise(r=>setTimeout(r,230));assert.equal(f.d.querySelector('.reader-popup'),popup);popup.querySelector('[data-reader-popup-close]').click();await new Promise(r=>setTimeout(r,230));assert.equal(f.d.querySelector('.reader-popup'),null);assert.equal(f.d.activeElement,link);
});
