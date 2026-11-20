const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'../../blocks/render');
const ids={body:'reader-10000000000000000000',a:'reader-20000000000000000000',b:'reader-30000000000000000000',
 refA:'reader-40000000000000000000',refB:'reader-50000000000000000000',note:'reader-60000000000000000000'};
function block(group,owner,pointer,text,anchor,collapsed=false){return {group,owner_id:owner,json_pointer:pointer,markdown:text,raw_text:text,anchor,collapsed,block_id:pointer,field_label:owner};}
function theorem(unit,id,name,anchor,otherRef,noteText){
 const base='/explainables/0',core=[block('explainables',id,base+'/name',name,anchor,true),block('explainables',id,base+'/what','条件 $z\\ne0$。结论 $w=\\bar w$。',anchor,true),block('explainables',id,base+'/why','用共轭把实数条件转为代数关系。',anchor,true)],
 proof=[block('explainables',id,base+'/markdown',`${unit} 的完整证明，从已知条件开始。`,anchor,true),block('explainables',id,base+'/steps/0/markdown',`先应用[另一条定理](#${otherRef})，再[检查条件](#${ids.note})。`,anchor,true),block('explainables',id,base+'/steps/0/basis','依据：每个分母都不为零。',anchor,true)];
 return {unit_id:unit,concept_id:id,name,anchor,source_label:unit+' 的原课程',core_blocks:core,proof_blocks:proof,
  reader_document:{schema_version:'ReaderDocumentV1',blocks:[...core,...proof,block('annotations','same-note','/annotations/0/title','条件提醒',ids.note,true),block('annotations','same-note','/annotations/0/markdown',noteText,ids.note,true)]},review_outcome:{status:'unverified',reasons:[]}};
}
function fixture({indexed=true,inline=true,formula=false}={}){
 const dom=new JSDOM(fs.readFileSync(path.join(root,'templates/index.html'),'utf8'),{url:'https://reader.invalid/#/U',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,d=w.document,errors=[];
 w.addEventListener('error',event=>errors.push(event.message));w.scrollTo=(a,b)=>{w.scrollY=typeof a==='object'?a.top:b;};
 w.HTMLElement.prototype.scrollIntoView=function(){this.dataset.scrolled='true';w.scrollY=900;};
 const a=theorem('U','T','零乘积定理',ids.a,ids.refB,'当前课的提醒。'),b=theorem('V','T','商的共轭定理',ids.b,ids.refA,'外部课的提醒。');
 const text=`## 学习正文\n\n${inline?'[定理1](#'+ids.a+')':'先[查看定理1](#'+ids.a+')，稍后再应用。'}\n\n这里[重看定理1](#${ids.refA})，然后用${formula?'$$[z_1](#'+ids.refB+')+z_2=z_2+z_1$$':'[定理2](#'+ids.refB+')'}。\n\n正文后续不移动。`;
 const blocks=[block('title','U','/title','当前课','reader-00000000000000000000'),block('body_markdown','U','/body_markdown',text,ids.body),...a.reader_document.blocks,
  block('references','T-A','/reference_catalog/entries/0/label','旧定理1',ids.refA,true),block('references','T-A','/reference_catalog/entries/0/markdown','旧目录简述A。',ids.refA,true),
  block('references','T-B','/reference_catalog/entries/1/label','旧定理2',ids.refB,true),block('references','T-B','/reference_catalog/entries/1/markdown','旧目录简述B。',ids.refB,true)];
 const refsU={[ids.a]:{unit_id:'U',concept_id:'T'},[ids.refA]:{unit_id:'U',concept_id:'T'},[ids.refB]:{unit_id:'V',concept_id:'T'}},refsV={[ids.b]:{unit_id:'V',concept_id:'T'},[ids.refA]:{unit_id:'U',concept_id:'T'}};
 w.__DATA__={book:'测试教材',scope:['U'],units:{U:{title:'当前课',reader_document:{schema_version:'ReaderDocumentV1',blocks},...(indexed?{theorem_refs:refsU}:{})}},tree:[{id:'U',title:'当前课',unit:true}],concepts:{}};
 if(indexed)w.__DATA__.theorem_index={by_unit:{U:{T:a},V:{T:b}},refs_by_unit:{U:refsU,V:refsV}};
 const original=JSON.stringify(w.__DATA__.units),index=JSON.stringify(w.__DATA__.theorem_index);
 for(const name of ['vendor/marked.min.js','vendor/katex.min.js','assets/reader-markdown.js','assets/render-core.js','assets/reader-layout.js','assets/app.js'])w.eval(fs.readFileSync(path.join(root,name),'utf8'));
 return {dom,w,d,errors,original,index};
}
function finish(t,f){t.after(()=>{assert.equal(JSON.stringify(f.w.__DATA__.units),f.original);assert.equal(JSON.stringify(f.w.__DATA__.theorem_index),f.index);f.dom.window.close();assert.deepEqual(f.errors,[]);});}
function bodyLink(f,id){return f.d.querySelector(`[data-reader-pointer="/body_markdown"] a[href="#${id}"]`);}
function popup(f,name){return [...f.d.querySelectorAll('.reader-popup')].find(node=>node.querySelector('.reader-popup-head > span').textContent===name);}

test('the first standalone owning EXPLAIN shows its core with the complete proof closed at that paragraph',t=>{
 const f=fixture();finish(t,f);const original=f.d.getElementById(ids.a);
 assert.ok(original.classList.contains('reader-theorem-inline'));
 assert.ok(original.closest('[data-reader-pointer="/body_markdown"]'));
 assert.match(original.textContent,/U 的完整证明/);
 assert.ok(original.querySelector('[data-reader-pointer="/explainables/0/steps/0/basis"]'));
 const proof=original.querySelector('.reader-theorem-proof');assert.ok(proof);assert.equal(proof.open,false);
 assert.ok(original.querySelector('[data-reader-pointer="/explainables/0/what"]'));
 proof.querySelector('summary').click();assert.equal(proof.open,true);
 assert.equal(f.d.querySelectorAll('#'+ids.a).length,1);
 assert.equal(bodyLink(f,ids.refA).textContent,'零乘积定理');
 assert.equal(bodyLink(f,ids.refB).textContent,'商的共轭定理');
});

test('theorem lookup shows conditions and conclusion with proof collapsed without navigation',t=>{
 const f=fixture();finish(t,f);const body=f.d.getElementById('content'),text=body.textContent,blocks=[...body.querySelectorAll('[data-reader-block]')],link=bodyLink(f,ids.refB);
 f.w.scrollY=330;const hash=f.w.location.hash;link.click();const win=popup(f,'商的共轭定理');assert.ok(win);
 assert.equal(f.w.scrollY,330);assert.equal(f.w.location.hash,hash);assert.equal(body.textContent,text);assert.deepEqual([...body.querySelectorAll('[data-reader-block]')],blocks);
 assert.equal(win.querySelector('[data-reader-popup-locate]'),null);
 assert.equal(win.querySelector('[data-reader-popup-return]').textContent,'继续阅读');
 assert.match(win.querySelector('.reader-theorem-core').textContent,/条件.*结论.*用共轭/s);assert.ok(win.querySelector('.reader-theorem-core .katex'));
 const proof=win.querySelector('.reader-theorem-proof');assert.ok(proof);assert.equal(proof.open,false);
 proof.querySelector('summary').click();assert.equal(proof.open,true);assert.match(proof.textContent,/V 的完整证明.*每个分母都不为零/s);
 win.querySelector('[data-reader-popup-return]').click();assert.equal(f.w.scrollY,330);assert.equal(f.w.location.hash,hash);assert.equal(f.d.activeElement,link);
});

test('nested theorem windows use source scopes and same NOTE IDs never select another unit',t=>{
 const f=fixture();finish(t,f);f.w.scrollY=250;bodyLink(f,ids.refB).click();const outer=popup(f,'商的共轭定理');
 outer.querySelector('.reader-theorem-proof').open=true;
 outer.querySelector(`a[href="#${ids.refA}"]`).click();const inner=popup(f,'零乘积定理');assert.ok(inner);assert.equal(outer.contains(inner),false);
 outer.querySelector(`a[href="#${ids.note}"]`).click();let note=[...f.d.querySelectorAll('.reader-popup')].find(n=>/外部课的提醒/.test(n.textContent));assert.ok(note);assert.doesNotMatch(note.textContent,/当前课的提醒/);
 inner.querySelector('.reader-theorem-proof').open=true;inner.querySelector(`a[href="#${ids.note}"]`).click();
 const local=[...f.d.querySelectorAll('.reader-popup')].find(n=>/当前课的提醒/.test(n.textContent));assert.ok(local);assert.notEqual(local,note);
 assert.equal(f.w.scrollY,250);assert.equal(f.w.location.hash,'#/U');
 const all=[...f.d.querySelectorAll('[id]')].map(n=>n.id);assert.equal(new Set(all).size,all.length);
 outer.querySelector('[data-reader-popup-close]').click();assert.ok(inner.isConnected);assert.ok(note.isConnected);assert.ok(local.isConnected);
 const head=inner.querySelector('[data-reader-popup-drag]');head.focus();head.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
 assert.equal(inner.style.left,'20px');assert.equal(f.w.scrollY,250);
 f.d.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(inner.isConnected,false);assert.ok(note.isConnected);assert.ok(local.isConnected);
});

test('an ordinary mention stays in place and absent indexes retain prior popup behavior',t=>{
 const f=fixture({inline:false});finish(t,f);assert.equal(f.d.getElementById(ids.a).classList.contains('reader-theorem-inline'),false);
 bodyLink(f,ids.a).click();assert.ok(popup(f,'零乘积定理'));assert.equal(f.d.getElementById(ids.a).open,false);
 const legacy=fixture({indexed:false});finish(t,legacy);assert.equal(bodyLink(legacy,ids.refB).textContent,'概念定理2');bodyLink(legacy,ids.refB).click();
 assert.ok(legacy.d.querySelector('.reader-popup [data-reader-popup-locate]'));assert.equal(legacy.d.querySelector('.reader-theorem-proof'),null);
 assert.equal(legacy.d.querySelector('[data-reader-popup-return]').textContent,'回到引用处');
});

test('a reference misplaced inside math opens its exact draggable theorem without navigation',t=>{
 const f=fixture({formula:true});finish(t,f);
 const link=bodyLink(f,ids.refB),body=f.d.getElementById('content');
 assert.ok(link);assert.equal(link.closest('.katex'),null);
 assert.equal(body.querySelectorAll('.katex-error,.math-error').length,0);
 assert.ok([...body.querySelectorAll('annotation[encoding="application/x-tex"]')].some(n=>n.textContent==='z_1+z_2=z_2+z_1'));
 const blocks=[...body.querySelectorAll('[data-reader-block]')];f.w.scrollY=360;const hash=f.w.location.hash;
 link.click();const win=popup(f,'商的共轭定理');assert.ok(win);
 assert.equal(f.w.scrollY,360);assert.equal(f.w.location.hash,hash);
 assert.deepEqual([...body.querySelectorAll('[data-reader-block]')],blocks);
 const head=win.querySelector('[data-reader-popup-drag]');head.focus();head.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
 assert.equal(win.style.left,'20px');assert.equal(f.w.scrollY,360);
 win.querySelector('[data-reader-popup-return]').click();assert.equal(f.d.activeElement,link);
});

test('a theorem popup mounts study scenes using its original unit scope',t=>{
 const f=fixture();finish(t,f);let mounted=0,disposed=0;
 f.w.DSHStudyVisuals={mountReaderScope(container,options){mounted++;container.dataset.mountedStudyUnit=options.unitId;return()=>disposed++;}};
 bodyLink(f,ids.refB).click();const win=popup(f,'商的共轭定理');assert.equal(win.querySelector('.reader-popup-body').dataset.mountedStudyUnit,'V');assert.equal(mounted,1);
 win.querySelector('[data-reader-popup-close]').click();assert.equal(disposed,1);
});
