const test = require('node:test');
const assert = require('node:assert/strict');
const {JSDOM} = require('jsdom');
const fs = require('node:fs');
const path = require('node:path');
const file = path.resolve(__dirname,'../../blocks/render/assets/study-visuals.js');
function api() { assert.ok(fs.existsSync(file),'interactive mathematics module must exist'); return require(file); }
function fixture() { return new JSDOM(`<main id="content"><section data-reader-topic data-reader-topic-title="复数乘法"><h2 class="reader-topic-title">复数乘法</h2><p>模相乘，辐角相加。</p></section><details data-reader-group="annotations" data-reader-owner="N1"><summary>易错：主值对数</summary><p data-reader-pointer="/annotations/0/markdown">主值对数在负实轴有跳跃。</p></details><section data-reader-group="references" data-reader-owner="C2"><h3>留数定理</h3><p>闭路围住极点。</p></section></main>`,{url:'https://offline.test/#/U1',pretendToBeVisual:true}); }
test('chapter concepts select mathematically relevant families and disclose illustrative models',()=>{
  const v=api();
  for (const [title,kind] of [['复数乘法','multiply'],['复数加法','addition'],['指数函数的映照','exp'],['主值对数','log'],['倒数映射 1/z','inverse'],['幂函数 z²','square'],['复导数的几何意义','derivative'],['复球面与无穷远点','sphere'],['邻域与区域','neighborhood'],['幂级数收敛半径','series'],['一致收敛','uniform'],['留数定理','contour'],['调和函数','harmonic'],['解析开拓','continuation'],['保形映照','mobius'],['拉普拉斯变换','laplace']]) {
    const scene=v.chooseScene(title,''); assert.equal(scene.kind,kind,title); assert.ok(scene.formula); assert.ok(scene.explanation);
  }
  assert.equal(v.chooseScene('未识别的抽象定理','').illustrative,true);
});
test('real mathematical geometry changes with parameters and matches known complex values',()=>{
  const v=api(), multiply=v.createModel(v.chooseScene('复数乘法',''));
  multiply.set('real',0); multiply.set('imag',1);
  assert.deepEqual(multiply.map(1,0).map(n=>Math.round(n*1e6)/1e6),[0,1]);
  const square=v.createModel(v.chooseScene('z²',''));
  assert.deepEqual(square.map(1,1),[0,2]);
  const exp=v.createModel(v.chooseScene('指数函数','')); assert.ok(Math.abs(exp.map(0,Math.PI)[0]+1)<1e-10);
  const inverse=v.createModel(v.chooseScene('1/z','')); assert.deepEqual(inverse.map(1,1),[.5,-.5]);
  const model=v.createModel(v.chooseScene('幂级数','')); const before=JSON.stringify(v.geometry(model));
  model.set('terms',11); assert.notEqual(JSON.stringify(v.geometry(model)),before);
  model.set('terms',NaN); assert.equal(model.params.terms,11); model.set('terms',999); assert.equal(model.params.terms,24);
  for(const title of ['邻域','复球面','调和函数','拉普拉斯变换','一致收敛','留数']) {
    const m=v.createModel(v.chooseScene(title,'')), initial=JSON.stringify(v.geometry(m)); const p=m.scene.controls[0]; m.set(p.key,p.max);
    assert.notEqual(JSON.stringify(v.geometry(m)),initial,title+' must change actual geometry');
    assert.ok(v.geometry(m).readout.length>0);
  }
});
test('all visible topic and support owners get independent bound controls without mutating frozen data',t=>{
  const v=api(),dom=fixture(); t.after(()=>dom.window.close());
  const data=Object.freeze({units:Object.freeze({U1:Object.freeze({title:'复数',reader_document:Object.freeze({blocks:Object.freeze([])})})})});
  const root=dom.window.document.querySelector('main');const cards=v.enhance(root,{data,unitId:'U1'});
  assert.equal(cards.length,3); assert.equal(root.querySelectorAll('.study-visual').length,3);
  assert.equal(root.querySelector('[data-study-owner="N1"]').closest('details').dataset.readerOwner,'N1');
  assert.equal(root.querySelector('[data-study-owner="C2"]').dataset.studyScene,'contour');
  assert.equal(v.enhance(root,{data,unitId:'U1'}).length,0,'enhancement must be idempotent');
  assert.ok(root.querySelectorAll('input[type="range"]').length>=3); assert.ok(root.querySelectorAll('input[type="number"]').length>=3);
  assert.ok(root.querySelector('[data-study-action="reset"]'));assert.ok(root.querySelector('[data-study-action="fullscreen"]'));
  assert.ok(root.querySelector('[data-study-action="play"]'));assert.ok(root.querySelector('[data-study-action="rotate-left"]'));
});
test('multiple theorem and concept targets in one topic each receive a binding',t=>{
  const v=api(),dom=new JSDOM(`<main><section data-reader-topic data-reader-topic-title="解析函数"><h2 class="reader-topic-title">解析函数</h2><div data-reader-concept-title="复导数"><p>定义</p></div><section data-reader-reference="定理" data-reader-owner="T1"><h3>柯西积分公式</h3></section><details data-reader-group="explainables" data-reader-owner="D1"><summary>为什么闭路积分为零？</summary><p>解释</p></details></section></main>`);t.after(()=>dom.window.close());
  v.enhance(dom.window.document.querySelector('main'),{});
  assert.equal(dom.window.document.querySelectorAll('.study-visual').length,3);
  assert.equal(dom.window.document.querySelector('[data-study-owner="T1"]').dataset.studyScene,'contour');
  assert.ok(dom.window.document.querySelector('[data-study-owner="D1"]').closest('details'));
});
test('lifecycle allocates only visible open scenes, releases GPU offscreen and on removal, and caps active contexts',t=>{
  const v=api(),dom=fixture();t.after(()=>dom.window.close());const root=dom.window.document.querySelector('main');v.enhance(root,{});
  let mounted=0,disposed=0,updates=0; const callbacks=[];
  const controller=v.createController(root,{maxActive:1,observe(callback){callbacks.push(callback);return {observe(){},unobserve(){},disconnect(){}};},createRenderer(){mounted++;return {update(){updates++;},dispose(){disposed++;},view(){}};}});
  const cards=[...root.querySelectorAll('.study-visual')]; assert.equal(mounted,0);
  callbacks[0]([{target:cards[0],isIntersecting:true}]); assert.equal(mounted,1);
  callbacks[0]([{target:cards[1],isIntersecting:true}]); assert.equal(mounted,1,'closed details must not allocate GPU');
  cards[1].closest('details').open=true; controller.refresh(); assert.equal(mounted,2);assert.equal(disposed,1);assert.equal(controller.activeCount(),1);
  const range=cards[1].querySelector('input[type="range"]');range.value=Number(range.min)+Number(range.step); range.dispatchEvent(new dom.window.Event('input',{bubbles:true})); assert.ok(updates>0);
  callbacks[0]([{target:cards[1],isIntersecting:false}]);assert.equal(disposed,2);
  callbacks[0]([{target:cards[2],isIntersecting:true}]); assert.equal(mounted,3);cards[2].remove();controller.refresh();assert.equal(disposed,3);
  controller.destroy();assert.equal(controller.activeCount(),0);
});
test('template ships offline scene and final theme styles',()=>{
  const template=fs.readFileSync(path.resolve(__dirname,'../../blocks/render/templates/index.html'),'utf8');
  assert.match(template,/assets\/three.min.js/); assert.match(template,/assets\/study-visuals.js/);assert.match(template,/assets\/study-reader.css/);
  assert.ok(template.indexOf('assets/three.min.js')<template.indexOf('assets/study-visuals.js'));
});
test('elementary and hyperbolic maps preserve their actual formula instead of borrowing another graph',()=>{
  const v=api();
  for(const [title,kind,point,expected] of [
    ['正弦函数 sin z','sin',[0,1],[0,Math.sinh(1)]],
    ['余弦函数 cos z','cos',[0,1],[Math.cosh(1),0]],
    ['双曲正弦 sinh z','sinh',[1,0],[Math.sinh(1),0]],
    ['双曲余弦 cosh z','cosh',[1,0],[Math.cosh(1),0]],
    ['共轭复数','conjugate',[1,2],[1,-2]],
    ['复数除法','quotient',[1,1],[1,0]],
    ['平方根的分支','sqrt',[0,2],[1,1]],
  ]) {
    const m=v.createModel(v.chooseScene(title,''));assert.equal(m.scene.kind,kind,title);
    if(kind==='quotient'){m.set('real',1);m.set('imag',1);}
    assert.deepEqual(m.map(...point).map(x=>Math.round(x*1e7)/1e7||0),expected.map(x=>Math.round(x*1e7)/1e7||0));
    assert.ok(v.geometry(m).lines.length>10);
  }
});
test('controls synchronize numeric input, change camera, reset, and leave authored prose intact',t=>{
  const v=api(),dom=fixture();t.after(()=>dom.window.close());const root=dom.window.document.querySelector('main'),original=root.querySelector('p').textContent;
  v.enhance(root,{});const card=root.querySelector('.study-visual'),model=card.__studyModel;
  const c=v.createController(root,{observe(){return {observe(){},disconnect(){}};},createRenderer(){return {view(){},update(){},dispose(){}};}});t.after(()=>c.destroy());
  const number=card.querySelector('input[type="number"]');number.value='1.5';number.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
  assert.equal(model.params.real,1.5);assert.equal(card.querySelector('input[type="range"]').value,'1.5');
  card.querySelector('[data-study-action="rotate-right"]').click();assert.notEqual(model.view.yaw,0);
  card.querySelector('[data-study-action="zoom-in"]').click();assert.ok(model.view.zoom>1);
  card.querySelector('[data-study-action="reset"]').click();assert.equal(model.view.yaw,0);assert.equal(model.view.zoom,1);assert.equal(model.params.real,.8);
  assert.equal(root.querySelector('.study-target-copy p').textContent,original);
});
test('boot observes replacement content, uses initial scope for authored owner titles, and remains lazy',async t=>{
  const source=fs.readFileSync(file,'utf8'),dom=new JSDOM(`<main id="content"><details data-reader-group="annotations" data-reader-owner="N"><summary>易错</summary><p>条件</p></details></main>`,{runScripts:'outside-only',pretendToBeVisual:true,url:'https://offline.test/'});t.after(()=>dom.window.close());
  let observers=0;dom.window.IntersectionObserver=class {constructor(){observers++;}observe(){}unobserve(){}disconnect(){}};
  dom.window.__DATA__={scope:['U1'],units:{U1:{reader_document:{blocks:[{owner_id:'N',json_pointer:'/annotations/0/title',markdown:'主值对数的分支'}]}}}};
  dom.window.eval(source);const doc=dom.window.document;
  assert.equal(doc.querySelector('.study-visual-title').textContent,'主值对数的分支');assert.equal(doc.querySelectorAll('canvas').length,0);
  doc.querySelector('#content').innerHTML='<section data-reader-topic data-reader-topic-title="复球面"><h2 class="reader-topic-title">复球面</h2><p>球极投影</p></section>';
  await new Promise(resolve=>setTimeout(resolve,30));
  assert.equal(doc.querySelectorAll('.study-visual').length,1);assert.equal(doc.querySelector('.study-visual').dataset.studyScene,'sphere');assert.equal(observers,1);assert.equal(doc.querySelectorAll('canvas').length,0);
});
test('Laurent and essential singularity use their own functions and show the excluded convergence boundary',()=>{
  const v=api(),laurent=v.createModel(v.chooseScene('洛朗级数 Laurent 展开',''));
  assert.equal(laurent.scene.kind,'laurent');laurent.set('radius',.5);const inner=v.geometry(laurent);assert.match(inner.readout,/内圆环|0 < \|z\| < 1/);
  laurent.set('radius',1.5);assert.match(v.geometry(laurent).readout,/外圆环|\|z\| > 1/);assert.notDeepEqual(v.geometry(laurent).lines,inner.lines);
  laurent.set('radius',1);assert.match(v.geometry(laurent).readout,/不收敛|边界/);
  const essential=v.createModel(v.chooseScene('本性奇点',''));assert.equal(essential.scene.kind,'essential');assert.ok(Math.abs(essential.map(1,0)[0]-Math.E)<1e-10);
});
test('uniform model treats full unit disk as open, shows a moving interior witness and adjustable epsilon',()=>{
  const v=api(),m=v.createModel(v.chooseScene('全开圆上逐点收敛但不一致',''));assert.equal(m.scene.kind,'uniform');
  m.set('bound',1);m.set('terms',8);m.set('epsilon',.25);const g=v.geometry(m);
  assert.equal(g.facts.rightEndpointIncluded,false);assert.equal(g.facts.supremum,1);assert.ok(g.facts.witnessX<1);assert.ok(Math.abs(Math.pow(g.facts.witnessX,8)-.5)<1e-12);
  assert.match(g.readout,/不取到/);assert.match(g.readout,/xₙ/);assert.match(m.scene.formula,/开|< 1|＜1/);
  const before=JSON.stringify(g.lines);m.set('epsilon',.1);assert.notEqual(JSON.stringify(v.geometry(m).lines),before);
  m.set('bound',.8);const small=v.geometry(m);assert.equal(small.facts.rightEndpointIncluded,true);assert.ok(Math.abs(small.facts.supremum-.8**8)<1e-12);assert.match(small.readout,/闭小圆/);
});
test('commutativity has both addition and multiplication orders with the same actual endpoint',()=>{
  const v=api(),m=v.createModel(v.chooseScene('交换律','$z_1+z_2=z_2+z_1$，$z_1z_2=z_2z_1$'));
  assert.equal(m.scene.kind,'commutative');m.set('real',1.2);m.set('imag',.4);const g=v.geometry(m);
  assert.deepEqual(g.facts.addAB,g.facts.addBA);assert.deepEqual(g.facts.multiplyAB,g.facts.multiplyBA);
  assert.equal(g.facts.paths.length,4);for(const p of g.facts.paths)assert.ok(p.length>=3);
  m.set('imag',1);assert.notDeepEqual(v.geometry(m).facts.multiplyAB,g.facts.multiplyAB);assert.match(m.scene.explanation,/两|顺序/);
});
test('unmatched abstract items are honestly reported and do not allocate an unrelated neighborhood graph',t=>{
  const v=api(),scene=v.chooseScene('Gamma 函数的积分表示','');assert.equal(scene.matchSource,'unmatched');assert.equal(scene.kind,'unmatched');
  const dom=new JSDOM('<main><section data-reader-topic data-reader-topic-title="未识别的抽象结论"><h2 class="reader-topic-title">未识别的抽象结论</h2><p>具体条件。</p></section></main>');t.after(()=>dom.window.close());
  const root=dom.window.document.querySelector('main'),cards=v.enhance(root,{});assert.equal(cards[0].dataset.studyCoverage,'unmatched');assert.match(cards[0].textContent,/尚无|未匹配/);assert.equal(cards[0].querySelectorAll('input').length,0);
  let allocations=0;const c=v.createController(root,{observe(callback){callback([{target:cards[0],isIntersecting:true}]);return {observe(){},disconnect(){}};},createRenderer(){allocations++;return {dispose(){}};}});c.refresh();assert.equal(allocations,0);c.destroy();
  assert.equal(v.chooseScene('邻域','').matchSource,'title');assert.equal(v.chooseScene('一个解释','在单位圆上考察幂级数。').matchSource,'body');
});
test('scene extensions can supply their own formulas, controls, geometry and exact map',()=>{
 const v=api();v.registerScenes({chooseScene(title){return title==='扩展场景'?{kind:'test-extension',title,formula:'w=3z',explanation:'测试扩展',controls:[{key:'factor',label:'系数',min:1,max:5,step:1,value:3}],illustrative:true,label:'示意模型',map(x,y,p){return [x*p.factor,y*p.factor];}}:null;},geometry(m){return m.scene.kind==='test-extension'?{lines:[{points:[[0,0,0],[m.params.factor,0,0]],color:'#fff',opacity:1}],points:[],labels:[],readout:'扩展'}:null;}});
 const m=v.createModel(v.chooseScene('扩展场景',''));assert.deepEqual(m.map(1,2),[3,6]);assert.equal(v.geometry(m).readout,'扩展');
});
test('algebra groupings, maximum modulus, Liouville and identity principle use relevant representative functions',()=>{
 const v=api();for(const title of ['结合律','分配律']){const m=v.createModel(v.chooseScene(title,'')),g=v.geometry(m);assert.ok(['associative','distributive'].includes(m.scene.kind));assert.deepEqual(g.facts.left,g.facts.right);m.set('imag',1.1);assert.deepEqual(v.geometry(m).facts.left,v.geometry(m).facts.right);}
 const max=v.createModel(v.chooseScene('最大模原理',''));assert.equal(max.scene.kind,'maximum');const g=v.geometry(max);assert.ok(g.facts.boundaryMaximum>=g.facts.interiorSampleMaximum);
 const liouville=v.createModel(v.chooseScene('刘维尔定理',''));assert.equal(liouville.scene.kind,'liouville');const bound=v.geometry(liouville).facts.derivativeBound;liouville.set('radius',8);assert.ok(v.geometry(liouville).facts.derivativeBound<bound);
 const identity=v.createModel(v.chooseScene('唯一性定理',''));assert.equal(identity.scene.kind,'identity');identity.set('amplitude',.5);assert.equal(v.geometry(identity).facts.identicallyZero,false);identity.set('amplitude',0);assert.equal(v.geometry(identity).facts.identicallyZero,true);
});
test('Joukowski and Schwarz-Christoffel draw their stated conformal map and preserve symmetry',()=>{
 const v=api(),j=v.createModel(v.chooseScene('儒可夫斯基变换',''));assert.equal(j.scene.kind,'joukowski');assert.deepEqual(j.map(1,0),[1,0]);assert.ok(v.geometry(j).lines.length>10);
 const sc=v.createModel(v.chooseScene('许瓦兹－克利斯托菲变换',''));assert.equal(sc.scene.kind,'schwarz');assert.deepEqual(sc.map(0,0),[0,0]);const w=sc.map(.4,0),n=sc.params.sides,angle=Math.PI*2/n,z=[.4*Math.cos(angle),.4*Math.sin(angle)],rot=sc.map(...z);assert.ok(Math.abs(rot[0]-w[0]*Math.cos(angle))<1e-7);assert.ok(Math.abs(rot[1]-w[0]*Math.sin(angle))<1e-7);const g=v.geometry(sc);sc.set('sides',5);assert.notDeepEqual(v.geometry(sc).lines,g.lines);assert.equal(v.geometry(sc).facts.interiorAngle,Math.PI*3/5);
});
test('generic pitfalls use their own authored example context rather than an unrelated default model',t=>{
 const v=api(),dom=new JSDOM('<main><section data-reader-topic data-reader-topic-title="例1"><section data-reader-group="examples" data-reader-owner="E"><p>例1</p><details class="reader-pitfall-item"><summary>易错 · 第 1 点</summary><p>不要漏掉最后一项。</p></details></section></section></main>');t.after(()=>dom.window.close());
 const data={units:{U:{title:'多项式运算',reader_document:{blocks:[{owner_id:'E',json_pointer:'/examples/0/statement',markdown:'验证复数的分配律 a(b+c)=ab+ac。'}]}}}};
 v.enhance(dom.window.document.querySelector('main'),{data,unitId:'U'});const card=dom.window.document.querySelector('.reader-pitfall-item .study-visual');assert.equal(card.dataset.studyScene,'distributive');assert.match(card.querySelector('.study-explanation').textContent,/所属|例题|对应/);
});
test('all example-associated graph stays inside its closed example disclosure',t=>{
 const v=api(),dom=new JSDOM('<main><section data-reader-topic data-reader-topic-kind="example" data-reader-topic-title="例3"><h2 class="reader-topic-title">例3</h2><details class="reader-example-disclosure"><summary>展开例题</summary><p>讨论 exp(z) 的级数。</p></details></section></main>');t.after(()=>dom.window.close());
 v.enhance(dom.window.document.querySelector('main'),{});const card=dom.window.document.querySelector('.study-visual');assert.ok(card.closest('.reader-example-disclosure'));assert.equal(dom.window.document.querySelector('[data-reader-topic] > .study-target-layout'),null);assert.equal(dom.window.document.querySelector('.reader-example-disclosure').open,false);
});
test('M-test, majorant and factorial series use a uniform-convergence positive example',()=>{
 const v=api();for(const title of ['强级数','比较判别法（维尔斯特拉斯判别法）','维尔斯特拉斯定理','例3']){
  const m=v.createModel(v.chooseScene(title,'在任意闭圆 |z|≤R 上 Σ z^k/k! 绝对一致收敛。'));assert.equal(m.scene.kind,'majorant',title);const g=v.geometry(m);assert.ok(g.facts.tailBound>=0);m.set('terms',18);assert.ok(v.geometry(m).facts.tailBound<g.facts.tailBound);assert.match(m.scene.explanation,/一致收敛/);
 }
 assert.equal(v.chooseScene('不一致收敛反例','S_n(z)=z^n').kind,'uniform');
});
test('a precise core title takes precedence over extension guesses from incidental body text',()=>{
 const v=api();v.registerScenes({chooseScene(title,body){return /偶然支割/.test(body||'')?{kind:'wrong-body-branch',title,formula:'test',explanation:'test',controls:[],illustrative:true}:null;},geometry(){return null;}});
 assert.equal(v.chooseScene('指数函数','正文偶然支割一词。').kind,'exp');
});
test('trigonometric pitfalls, zero orders, argument principle and mean value get related mathematical objects',()=>{
 const v=api(),identity=v.createModel(v.chooseScene('复三角恒等式 cos²z+sin²z=1',''));assert.equal(identity.scene.kind,'trig-identity');identity.set('imag',.8);assert.ok(Math.abs(v.geometry(identity).facts.sum[0]-1)<1e-10);assert.ok(Math.abs(v.geometry(identity).facts.sum[1])<1e-10);
 const e=v.createModel(v.chooseScene('sinθ 的分母是2i，不是2',''));assert.equal(e.scene.kind,'euler-trig');e.set('angle',Math.PI/2);assert.ok(Math.abs(v.geometry(e).facts.sine-1)<.001);
 const tan=v.createModel(v.chooseScene('复正切函数的周期性',''));assert.equal(tan.scene.kind,'tan');const t1=tan.map(.4,.3),t2=tan.map(.4+Math.PI,.3);assert.ok(Math.hypot(t1[0]-t2[0],t1[1]-t2[1])<1e-10);
 const arctan=v.createModel(v.chooseScene('反正切没有值 z=±i',''));assert.equal(arctan.scene.kind,'arctan');assert.ok(Math.abs(arctan.map(1,0)[0]-Math.PI/4)<1e-10);assert.ok(arctan.map(0,1).some(n=>!Number.isFinite(n)));
 const zero=v.createModel(v.chooseScene('m阶零点',''));assert.equal(zero.scene.kind,'zero');zero.set('order',3);assert.equal(v.geometry(zero).facts.zeroOrder,3);
 const a=v.createModel(v.chooseScene('鲁歇定理与辐角原理',''));assert.equal(a.scene.kind,'argument');a.set('radius',1);a.set('shift',.2);assert.equal(v.geometry(a).facts.winding,a.params.order);a.set('shift',1.5);assert.equal(v.geometry(a).facts.winding,0);
 const mean=v.createModel(v.chooseScene('弧长平均不是角度平均',''));assert.equal(mean.scene.kind,'mean');const g=v.geometry(mean);assert.deepEqual(g.facts.average,g.facts.centerValue);mean.set('radius',1.8);assert.deepEqual(v.geometry(mean).facts.average,g.facts.average);
});
test('factorial positive examples retain their function even if an extension sees an incidental note',()=>{
 const v=api();assert.equal(v.chooseScene('例3','Σz^k/k! 阶乘级数绝对一致收敛；偶然支割注释。').kind,'majorant');
});
test('simple versus self-intersecting curve and corrected slit endpoint preserve their defining geometry',()=>{
 const v=api(),curve=v.createModel(v.chooseScene('约当曲线（简单曲线）',''));assert.equal(curve.scene.kind,'curve');curve.set('mode',1);const g=v.geometry(curve);assert.equal(g.facts.selfIntersecting,true);assert.deepEqual(g.facts.crossingA,g.facts.crossingB);
 const slit=v.createModel(v.chooseScene('例10原题割痕端点笔误',''));assert.equal(slit.scene.kind,'slit-endpoint');const old=v.geometry(slit).readout;slit.set('endpoint',-1);assert.notEqual(v.geometry(slit).readout,old);assert.match(v.geometry(slit).readout,/，一致/);
});
test('zero marker angle is a real parameter value and exponential Taylor map matches its partial sum',()=>{
 const v=api(),m=v.createModel(v.chooseScene('z²',''));m.set('phase',0);const g=v.geometry(m);assert.equal(g.points[0].position[1],0);assert.equal(g.points[1].position[1],0);
 const e=v.createModel(v.chooseScene('强级数',''));e.set('terms',3);assert.ok(Math.abs(e.map(1,0)[0]-(1+1+1/2+1/6))<1e-10);
});
test('argument principle includes pole orders and cancels common factors when a is zero',()=>{
 const v=api(),m=v.createModel(v.chooseScene('对数导数积分公式（N-P公式）',''));assert.equal(m.scene.kind,'argument');m.set('radius',1);m.set('order',2);m.set('poleOrder',1);m.set('shift',.2);assert.equal(v.geometry(m).facts.winding,1);assert.equal(v.geometry(m).facts.poles,1);
 m.set('shift',1.5);assert.equal(v.geometry(m).facts.winding,-1);m.set('shift',0);assert.equal(v.geometry(m).facts.zeros,1);assert.equal(v.geometry(m).facts.poles,0);
});
test('circular differential retains i and arc decay distinguishes degree gap one from two',()=>{
 const v=api(),d=v.createModel(v.chooseScene('dθ 中的 i 不能丢',''));assert.equal(d.scene.kind,'circle-differential');const f=v.geometry(d).facts;assert.ok(Math.abs(Math.hypot(...f.derivative)-d.params.radius)<1e-10);assert.ok(Math.abs(f.derivative[0]*f.radial[0]+f.derivative[1]*f.radial[1])<1e-10);
 const arc=v.createModel(v.chooseScene('不要忽略 deg Q≥deg P+2',''));assert.equal(arc.scene.kind,'arc-decay');arc.set('degree',1);const b=v.geometry(arc).facts.bound;arc.set('radius',8);assert.equal(v.geometry(arc).facts.bound,b);arc.set('degree',2);assert.ok(v.geometry(arc).facts.bound<b);assert.ok(Math.abs(v.geometry(arc).facts.integral[0]-2/8)<1e-10);
});
test('structural headings without their own content do not invent a mathematical target',t=>{
 const v=api(),dom=new JSDOM('<main><section data-reader-topic data-reader-topic-title="定义"><h2 class="reader-topic-title">定义</h2></section><section data-reader-topic data-reader-topic-title="基本性质"><h2 class="reader-topic-title">基本性质</h2>  </section></main>');t.after(()=>dom.window.close());const root=dom.window.document.querySelector('main');v.enhance(root,{});assert.equal(root.querySelectorAll('.study-visual').length,0);assert.equal(root.querySelectorAll('h2').length,2);
});
test('Cauchy value-reading uses the integral of the declared function and checks the evaluation point domain',()=>{
 const v=api(),m=v.createModel(v.chooseScene('为什么积分能读出 f(z)',''));assert.equal(m.scene.kind,'cauchy');m.set('radius',1);m.set('real',.3);m.set('imag',.2);const g=v.geometry(m);assert.ok(Math.hypot(g.facts.numerical[0]-g.facts.value[0],g.facts.numerical[1]-g.facts.value[1])<1e-8);assert.ok(Math.abs(g.facts.value[0]-.05)<1e-10);m.set('radius',.5);m.set('real',.9);assert.equal(v.geometry(m).facts.inside,false);
 assert.equal(v.chooseScene('许－克公式','').kind,'schwarz');assert.equal(v.chooseScene('保角性也保持角的方向','').kind,'derivative');
});
test('hyperbolic chapter headings and generic calculus headings retain their own function family',t=>{
 const v=api(),m=v.createModel(v.chooseScene('2.5.6 双曲函数','本节计算导数并检验解析性。'));assert.equal(m.scene.kind,'hyperbolic');assert.deepEqual(m.map(0,0),[0,0]);m.set('mode',1);assert.deepEqual(m.map(0,0),[1,0]);
 const dom=new JSDOM('<main><section data-reader-topic data-reader-topic-title="导数"><h2 class="reader-topic-title">导数</h2><p>求导并检查恒等式。</p></section></main>');t.after(()=>dom.window.close());v.enhance(dom.window.document.querySelector('main'),{unitId:'U',data:{units:{U:{title:'双曲函数',reader_document:{blocks:[]}}}}});const card=dom.window.document.querySelector('.study-visual');assert.equal(card.dataset.studyScene,'hyperbolic');assert.match(card.querySelector('.study-formula').textContent,/sinh|cosh/);assert.match(card.querySelector('.study-explanation').textContent,/本节/);
});
test('telescoping closed-disk positive example retains z to the n instead of borrowing factorial terms',()=>{
 const v=api(),s=v.chooseScene('例4','证明 z+(z^2-z)+...+(z^n-z^{n-1}) 在闭圆|z|≤r<1绝对一致收敛，用维尔斯特拉斯判别法。');assert.equal(s.kind,'telescoping');const m=v.createModel(s);assert.ok(m.params.bound<1);assert.equal(m.scene.controls.find(c=>c.key==='bound').max<1,true);assert.ok(Math.abs(v.geometry(m).facts.supremum-m.params.bound**m.params.terms)<1e-12);
 assert.equal(v.chooseScene('例5','望远镜部分和z^n在全开圆逐点但非一致收敛。').kind,'uniform');
});
test('copied popup scope rebuilds live models and owns a disposable capped renderer controller',t=>{
 const v=api(),dom=fixture();t.after(()=>dom.window.close());const doc=dom.window.document,main=doc.querySelector('main');v.enhance(main,{});const source=main.querySelector('.study-visual'),sourceModel=source.__studyModel;
 const popup=doc.createElement('section');popup.className='reader-popup';const copy=main.cloneNode(true);copy.id='popup-copy';popup.appendChild(copy);doc.body.appendChild(popup);
 const dead=copy.querySelector('.study-visual');assert.equal(dead.__studyModel,undefined);let intersection,mounted=0,released=0;
 const dispose=v.mountReaderScope(copy,{observe(fn){intersection=fn;return {observe(){},unobserve(){},disconnect(){}};},createRenderer(){mounted++;return {view(){},update(){},dispose(){released++;}};}});
 const cards=[...copy.querySelectorAll('.study-visual')];assert.equal(cards.length,3);assert.ok(cards.every(c=>c.__studyModel));assert.equal(dead.isConnected,false);assert.notEqual(cards[0].__studyModel,sourceModel);
 const input=cards[0].querySelector('input[type=number]');input.value='1.4';input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));assert.ok(Math.abs(cards[0].__studyModel.params.real-1.4)<1e-10);assert.equal(sourceModel.params.real,.8);
 cards[0].querySelector('[data-study-action=rotate-right]').click();assert.notEqual(cards[0].__studyModel.view.yaw,0);
 intersection(cards.map(target=>({target,isIntersecting:true})));assert.ok(mounted>=1);assert.equal(dispose.controller.activeCount(),1);assert.ok(released>=1);
 dispose();assert.equal(dispose.controller.activeCount(),0);assert.equal(released,mounted);assert.equal(cards[0].querySelector('button').disabled,true);assert.equal(source.querySelector('button').disabled,false);dispose();assert.equal(released,mounted);
});

test('uniform domain modes preserve closed radius and strict threshold, reset to open',t=>{
 const v=api(),dom=new JSDOM('<main></main>');t.after(()=>dom.window.close());const root=dom.window.document.querySelector('main');
 const card=v.createTeachingCard(dom.window.document,{kind:'uniform',model_title:'一致收敛',title:'一致收敛',action:'比较',observation:'误差',conclusion:'条件'},'U');root.appendChild(card);
 const model=card.__studyModel,c=v.createController(root,{observe(){return {observe(){},disconnect(){}};},createRenderer(){return {update(){},dispose(){}};}});t.after(()=>c.destroy());
 const bound=card.querySelector('input[type="number"][data-study-param="bound"]');assert.equal(bound.disabled,true);assert.equal(model.params.bound,1);
 card.querySelector('[data-study-action="uniform-mode-closed"]').click();assert.equal(bound.disabled,false);assert.equal(model.params.bound,.7);
 bound.value='.99';bound.dispatchEvent(new dom.window.Event('input',{bubbles:true}));const eps=card.querySelector('input[type="number"][data-study-param="epsilon"]');eps.value='.05';eps.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
 card.querySelector('[data-study-action="uniform-terms"]').click();assert.equal(model.params.terms,299);assert.equal(v.geometry(model).facts.rightEndpointIncluded,true);assert.ok(v.geometry(model).facts.supremum<.05);
 card.querySelector('[data-study-action="uniform-mode-open"]').click();assert.equal(model.params.bound,1);assert.equal(v.geometry(model).facts.rightEndpointIncluded,false);
 card.querySelector('[data-study-action="uniform-mode-closed"]').click();assert.equal(model.params.bound,.99);
 bound.value='1';bound.dispatchEvent(new dom.window.Event('input',{bubbles:true}));assert.equal(model.params.bound,.99);
 card.querySelector('[data-study-action="reset"]').click();assert.equal(model.params.bound,1);assert.equal(bound.disabled,true);
});
