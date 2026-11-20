const test=require('node:test'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const flow=require('../../blocks/render/assets/study-lesson.js');
test('authored textbook prose and media markers are not split into inferred knowledge headings',t=>{
 const dom=new JSDOM('<main></main>');t.after(()=>dom.window.close());
 const render=require('../../blocks/render/assets/render-core.js').createRenderer({marked:require('../../blocks/render/vendor/marked.min.js'),katex:require('../../blocks/render/vendor/katex.min.js')});
 const raw='用同一个例子说明：$z^n$ 是部分和，不要把这一句话切成知识卡片。\n\n[[TEACHING_MEDIA:M1]]\n\n下面继续解释这个观察。';
 const doc={schema_version:'ReaderDocumentV1',unit_id:'U',blocks:[{group:'body_markdown',json_pointer:'/body_markdown',raw_text:raw,markdown:raw,block_id:'body',field_label:'正文',anchor:'reader-body',owner_id:'U'}]};
 const root=dom.window.document.querySelector('main');root.innerHTML=render.readerDocumentHtml(doc,'body');
 require('../../blocks/render/assets/reader-layout.js').enhance(root,{title:'连续讲解',reader_document:doc,study_lesson:{body_markdown:raw}},'U');
 assert.equal(root.querySelectorAll('h2').length,0);
 assert.ok([...root.querySelectorAll('p')].some(p=>p.textContent==='[[TEACHING_MEDIA:M1]]'));
});
test('only explicit teaching markers mount a figure, between its explanation and conclusion',t=>{
 const dom=new JSDOM('<main><section data-reader-group="body_markdown"><p>我们先要看清这个问题。</p><p>[[TEACHING_VISUAL:V1]]</p><p>因此可以进入下一步。</p><p>没有标记的邻域字样不应产生图。</p></section><details data-reader-group="references"><summary>来源定义</summary>原始资料</details></main>');t.after(()=>dom.window.close());
 let calls=0;dom.window.DSHStudyVisuals={createTeachingCard(doc,plan){calls++;const c=doc.createElement('figure');c.className='study-visual';c.dataset.studyScene=plan.kind;return c;}};
 const lesson={body_markdown:'new',visuals:[{id:'V1',kind:'multiply',model_title:'复数乘法',title:'旋转',purpose:'看清乘法',action:'拖动角度',observation:'观察终点',conclusion:'乘法改变模和辐角'}],media:[]};
 const root=dom.window.document.querySelector('main');
 flow.enhance(root,{unit:{study_lesson:lesson},data:{media_purposes:{items:[{unit_id:'U',id:'V1',kind:'visual',purpose_markdown:'看清乘法：拖动角度，观察终点。'}]}},unitId:'U'});
 assert.equal(calls,1);assert.equal(root.querySelectorAll('.study-visual').length,1);
 assert.ok(root.textContent.includes('没有标记的邻域'));
 assert.ok(!root.textContent.includes('[[TEACHING_VISUAL:'));
 assert.ok(root.querySelector('.teaching-block').textContent.includes('看清乘法'),'every visual exposes its purpose next to the figure');
 assert.equal(root.querySelector('.lesson-sources').open,false);
 flow.enhance(root,{unit:{study_lesson:lesson},data:{},unitId:'U'});assert.equal(calls,1);
});

test('desktop media occupies a separate rail while the authored paragraphs remain together',t=>{
 const dom=new JSDOM('<main><article class="unit"><div data-reader-group="body_markdown"><p>先比较原数。</p><p>[[TEACHING_VISUAL:A]]</p><p>这里解释变化。</p><p>[[TEACHING_VISUAL:B]]</p><p>最后回到结论。</p></div></article></main>',{pretendToBeVisual:true});t.after(()=>dom.window.close());
 dom.window.DSHStudyVisuals={createTeachingCard(doc,p){const card=doc.createElement('figure');card.className='study-visual';card.textContent=p.title;return card;}};
 const root=dom.window.document.querySelector('main');
 const report=flow.enhance(root,{unit:{study_lesson:{visuals:[{id:'A',title:'原图'},{id:'B',title:'变化图'}],media:[]}},unitId:'U',data:{}});
 assert.deepEqual(report.errors,[]);assert.equal(root.querySelectorAll('.lesson-media-rail .teaching-block').length,2);
 assert.match(root.querySelector('.lesson-prose').textContent,/先比较原数。.*这里解释变化。.*最后回到结论。/s);
 const cues=root.querySelectorAll('.lesson-media-cue > summary');assert.equal(cues.length,2);
 cues[1].click();assert.equal(root.querySelector('[data-teaching-id="B"]').hidden,false);assert.equal(root.querySelector('[data-teaching-id="A"]').hidden,true);
 root.__teachingState.dispose();
});

test('on a phone each media stays in a closed disclosure at the paragraph which requested it',t=>{
 const dom=new JSDOM('<main><article class="unit"><div data-reader-group="body_markdown"><p>先读正文。</p><p>[[TEACHING_VISUAL:A]]</p><p>再读解释。</p></div></article></main>',{pretendToBeVisual:true});t.after(()=>dom.window.close());Object.defineProperty(dom.window,'innerWidth',{value:390,writable:true});
 dom.window.DSHStudyVisuals={createTeachingCard(doc){const card=doc.createElement('figure');card.className='study-visual';return card;}};
 const root=dom.window.document.querySelector('main');flow.enhance(root,{unit:{study_lesson:{visuals:[{id:'A',title:'对应图解'}],media:[]}},unitId:'U',data:{}});
 const cue=root.querySelector('.lesson-media-cue');assert.ok(cue);assert.equal(cue.open,false);assert.ok(cue.querySelector('.study-visual'));cue.querySelector('summary').click();assert.equal(cue.open,true);
 dom.window.dispatchEvent(new dom.window.Event('resize'));assert.equal(cue.open,true,'opening the phone keyboard must not collapse the selected diagram');
 root.__teachingState.dispose();
});
test('textbook conclusions remain visible with typeset conditions while proofs and examples stay closed',t=>{
 const dom=new JSDOM('<main><article class="unit"><div data-reader-group="body_markdown"><p>先解释统一误差。</p><p>[[TEXTBOOK_RESULT:U-C1]]</p><p>下面继续应用。</p></div><details data-reader-group="explainables"><summary>证明</summary>隐藏的证明过程</details><details data-reader-group="examples"><summary>例题</summary>隐藏的例题步骤</details></article></main>');t.after(()=>dom.window.close());
 const w=dom.window;w.DSHRenderCore=require('../../blocks/render/assets/render-core.js');w.marked=require('../../blocks/render/vendor/marked.min.js');w.katex=require('../../blocks/render/vendor/katex.min.js');
 const lesson={visuals:[],media:[],conclusions:[{id:'U-C1',title:'柯西一致收敛准则',statement_markdown:'对任意 $\\varepsilon>0$，存在与 $z,p$ 无关的 $N$，使 $n>N$ 时对全部 $z\\in E$ 和 $p\\ge1$ 都有\n\n$$\\left|\\sum_{k=n+1}^{n+p}f_k(z)\\right|<\\varepsilon.$$'}]};
 const root=w.document.querySelector('main');flow.enhance(root,{unit:{study_lesson:lesson},unitId:'U',data:{}});
 const result=root.querySelector('[data-textbook-result="U-C1"]');assert.ok(result,'the required theorem must be present in default reading');
 assert.equal(result.closest('details'),null,'the theorem statement must not be hidden with its proof');
 assert.match(result.textContent,/柯西一致收敛准则/);assert.ok(result.querySelector('.katex .op-limits'));
 assert.equal(root.querySelector('.lesson-sources').open,false);assert.equal(root.querySelectorAll('.lesson-sources details[open]').length,0);
 assert.doesNotMatch(root.textContent,/\[\[TEXTBOOK_RESULT/);
});
test('reusable conclusions from textbook exercises remain visible independently from worked solutions',t=>{
 const dom=new JSDOM('<main><article class="unit"><div data-reader-group="body_markdown"><p>本节只讲区域。</p></div></article></main>');t.after(()=>dom.window.close());
 const w=dom.window;w.DSHRenderCore=require('../../blocks/render/assets/render-core.js');w.marked=require('../../blocks/render/vendor/marked.min.js');w.katex=require('../../blocks/render/vendor/katex.min.js');
 const lesson={visuals:[],media:[],conclusions:[],supplementary_conclusions:[{id:'EX-C1',title:'平行四边形恒等式',statement_markdown:'$$|a+b|^2+|a-b|^2=2(|a|^2+|b|^2).$$'}]};
 const root=w.document.querySelector('main');flow.enhance(root,{unit:{study_lesson:lesson},unitId:'U',data:{}});
 const supplement=root.querySelector('.lesson-exercise-results');assert.ok(supplement);assert.notEqual(supplement.tagName,'DETAILS');assert.equal(supplement.closest('details'),null);assert.ok(supplement.querySelector('.katex'));
 assert.equal(root.querySelectorAll('[data-textbook-result]').length,0);
});
test('textbook transform table rows are readable without unfolding and keep both sides of the formula',t=>{
 const dom=new JSDOM('<main><article class="unit"><div data-reader-pointer="/body_markdown" data-reader-group="body_markdown"><p>读完本节，再对照教材的变换表。</p></div></article></main>');t.after(()=>dom.window.close());
 const w=dom.window;w.DSHRenderCore=require('../../blocks/render/assets/render-core.js');w.marked=require('../../blocks/render/vendor/marked.min.js');w.katex=require('../../blocks/render/vendor/katex.min.js');
 const lesson={visuals:[],media:[],conclusions:[],transform_tables:[{id:'T1',title:'拉普拉斯变换表',rows:[{id:'T1-R1',original_number:1,statement_markdown:'当 $\\operatorname{Re}p>0$ 时，$$\\mathcal L[1](p)=\\frac1p.$$'}]}]};
 const root=w.document.querySelector('main');flow.enhance(root,{unit:{study_lesson:lesson},unitId:'U',data:{}});
 const row=root.querySelector('[data-textbook-table-result="T1-R1"]');assert.ok(row);assert.equal(row.closest('details'),null);assert.ok(row.querySelectorAll('.katex').length>=2);assert.match(root.querySelector('.lesson-transform-tables').textContent,/拉普拉斯变换表/);
});

test('integrated knowledge annotates the actual authored text once without appending a second statement',t=>{
 const dom=new JSDOM('<main><article class="unit"><div data-reader-group="body_markdown"><p>[[TEXTBOOK_INTEGRATED:C1|C2]]</p><p>共轭保持实部，反转虚部。</p><p>[[TEXTBOOK_END_INTEGRATED]]</p><p>[[TEXTBOOK_INTEGRATED:C1]]</p><p>分子分母一起乘共轭。</p><p>[[TEXTBOOK_END_INTEGRATED]]</p></div></article></main>');t.after(()=>dom.window.close());
 const root=dom.window.document.querySelector('main');
 const report=flow.enhance(root,{unit:{study_lesson:{visuals:[],media:[],conclusions:[{id:'C1'},{id:'C2'}]}},unitId:'U',data:{}});
 assert.deepEqual(report.errors,[]);
 assert.equal(root.querySelectorAll('[data-textbook-result]').length,2);
 assert.equal(root.querySelectorAll('.lesson-integrated-knowledge').length,2);
 assert.equal(root.textContent,'共轭保持实部，反转虚部。分子分母一起乘共轭。');
 assert.equal(root.querySelectorAll('.lesson-conclusion-title').length,0);
 assert.equal(root.querySelector('[data-textbook-result="C1"]').closest('details'),null);
});

test('an exercise result explained in the main narrative is not duplicated in an extra card list',t=>{
 const dom=new JSDOM('<main><article class="unit"><div data-reader-group="body_markdown"><p>[[TEXTBOOK_INTEGRATED:EX1]]</p><p>实系数保证非实根共轭成对。</p><p>[[TEXTBOOK_END_INTEGRATED]]</p></div></article></main>');t.after(()=>dom.window.close());
 const root=dom.window.document.querySelector('main');const report=flow.enhance(root,{unit:{study_lesson:{visuals:[],media:[],conclusions:[],supplementary_conclusions:[{id:'EX1',title:'共轭根',statement_markdown:'实系数保证非实根共轭成对。'}]}},unitId:'U',data:{}});
 assert.deepEqual(report.errors,[]);assert.equal(root.querySelectorAll('[data-textbook-supplement="EX1"]').length,1);assert.equal(root.querySelector('.lesson-exercise-results'),null);assert.equal(root.textContent,'实系数保证非实根共轭成对。');
});

test('text led figures use authored placement without repeating the purpose panel',t=>{
 const dom=new JSDOM('<main><article class="unit"><p>看两点的横坐标。</p><p>[[TEACHING_VISUAL:M]]</p><p>虚部正好相反。</p></article></main>');t.after(()=>dom.window.close());
 const w=dom.window;w.DSHTextLedFigures={createFigure(doc,plan){const figure=doc.createElement('figure');figure.className='study-visual';figure.dataset.studyScene=plan.kind;figure.textContent='镜像图';return figure;}};
 const root=w.document.querySelector('main');
 const report=flow.enhance(root,{unit:{study_lesson:{visuals:[{id:'M',kind:'conjugate',presentation:'text-led-figure',purpose:'内部设计理由',action:'重复动作'}],media:[]}},unitId:'U',data:{}});
 assert.deepEqual(report.errors,[]);assert.equal(report.visuals,1);
 assert.equal(root.querySelectorAll('.teaching-purpose,.teaching-instruction').length,0);
 assert.equal(root.querySelector('.lesson-prose > p').textContent,'看两点的横坐标。');
 assert.doesNotMatch(root.textContent,/内部设计理由|重复动作/);
});

test('a standalone text led figure keeps its own renderer instead of entering the legacy model controller',t=>{
 const dom=new JSDOM('<main><figure class="study-visual" data-text-led-figure="true"></figure></main>',{pretendToBeVisual:true});t.after(()=>dom.window.close());
 const root=dom.window.document.querySelector('main'),figure=root.querySelector('figure');let observe,mounted=0;
 const controller=require('../../blocks/render/assets/study-visuals.js').createController(root,{observe(fn){observe=fn;return{observe(){},unobserve(){},disconnect(){}};},createRenderer(){mounted++;return{dispose(){}};}});
 t.after(()=>controller.destroy());observe([{target:figure,isIntersecting:true}]);assert.equal(mounted,0);
});

test('proof-contained media never escapes its disclosure into the desktop rail',t=>{
 const dom=new JSDOM('<main><article class="unit"><section data-reader-pointer="/body_markdown"><details class="reader-proof"><summary>证明</summary><p>[[TEACHING_VISUAL:P]]</p></details><p>[[TEACHING_VISUAL:M]]</p></section></article></main>');t.after(()=>dom.window.close());
 dom.window.innerWidth=1440;
 dom.window.DSHStudyVisuals={createTeachingCard(doc,plan){const el=doc.createElement('section');el.className='study-visual';return el;}};
 const root=dom.window.document.querySelector('main');flow.enhance(root,{unit:{study_lesson:{visuals:[{id:'P',title:'证明图'},{id:'M',title:'主线图'}],media:[]}},unitId:'U',data:{}});
 assert.ok(root.querySelector('[data-teaching-id="P"]').closest('details.reader-proof:not([open])'));
 assert.equal(root.querySelectorAll('.lesson-media-rail .teaching-block').length,1);
 assert.equal(root.querySelector('[data-teaching-id="P"] .study-visual'),null);
 let refreshes=0;root.__studyController={refresh(){refreshes++;}};
 const proof=root.querySelector('details.reader-proof');proof.open=true;proof.dispatchEvent(new dom.window.Event('toggle'));
 assert.ok(root.querySelector('[data-teaching-id="P"] .study-visual'));assert.ok(refreshes>0);
 assert.ok(root.querySelector('[data-teaching-id="P"]').closest('details.reader-proof'));
});
