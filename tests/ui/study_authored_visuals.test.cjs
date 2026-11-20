const test=require('node:test'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const api=require('../../blocks/render/assets/study-visuals.js');
api.registerScenes(require('../../blocks/render/assets/study-foundation-scenes.js'));
api.registerScenes(require('../../blocks/render/assets/study-special-scenes.js'));
function setup(t){const dom=new JSDOM('<main><section data-reader-concept-title="复数乘法"><p>模相乘</p></section></main>',{pretendToBeVisual:true});t.after(()=>dom.window.close());return dom;}
function plan(){return {id:'V1',kind:'multiply',model_title:'复数乘法',title:'先旋转再伸缩',formula:'w=az',purpose:'辨认乘数对向量的作用。',action:'将实部设为零、虚部设为一。',observation:'向量逆时针旋转直角。',conclusion:'乘以i不改变长度。',params:{real:0,imag:1}};}
test('authored policy never infers graphics from keywords or mutates paragraph layout',t=>{const dom=setup(t),root=dom.window.document.querySelector('main'),before=root.innerHTML;assert.deepEqual(api.enhance(root,{data:{study_reader:{visual_policy:'authored'}}}),[]);assert.equal(root.innerHTML,before);});
test('explicit teaching card rejects a mismatch instead of substituting a representative',t=>{const dom=setup(t);assert.throws(()=>api.createTeachingCard(dom.window.document,{...plan(),kind:'log'}),/mismatch|匹配/);assert.equal(dom.window.document.querySelectorAll('.study-visual').length,0);});
test('teaching card applies presets, displays its authored task and formula, and preserves live controls',t=>{const dom=setup(t),doc=dom.window.document,root=doc.querySelector('main');let formulaInput='';dom.window.DSHRenderCore={createRenderer(){return {renderMarkdown(s){formulaInput=s;return '<span class="katex">渲染公式</span>';}};}};const p=plan(),snapshot=JSON.stringify(p),card=api.createTeachingCard(doc,p);root.append(card);assert.equal(JSON.stringify(p),snapshot);assert.equal(card.querySelector('.study-visual-title').textContent,p.title);assert.ok(card.querySelector('.study-formula .katex'));assert.match(formulaInput,/w=az/);assert.equal(card.__studyModel.params.real,0);assert.equal(card.__studyModel.params.imag,1);assert.deepEqual(card.__studyModel.map(1,0),[0,1]);for(const s of [p.purpose,p.action,p.observation,p.conclusion])assert.ok(card.textContent.includes(s));assert.doesNotMatch(card.querySelector('.study-eyebrow').textContent,/示意模型|代表函数/);const controller=api.createController(root,{observe(){return {observe(){},disconnect(){}};},createRenderer(){return {view(){},update(){},dispose(){}};}});t.after(()=>controller.destroy());const number=card.querySelector('input[type="number"][data-study-param="real"]');number.value='1';number.dispatchEvent(new dom.window.Event('input',{bubbles:true}));assert.equal(card.__studyModel.params.real,1);card.querySelector('[data-study-action="reset"]').click();assert.equal(card.__studyModel.params.real,0);assert.equal(card.__studyModel.view.zoom,1);});
test('real fallback canvas paints white and uses legible scene colors',t=>{const dom=setup(t),doc=dom.window.document,root=doc.querySelector('main'),card=api.createTeachingCard(doc,plan());root.append(card);const fills=[],strokes=[],ctx={fillRect(){fills.push(this.fillStyle);},beginPath(){},moveTo(){},lineTo(){},arc(){},fill(){},stroke(){strokes.push(this.strokeStyle);}};dom.window.HTMLCanvasElement.prototype.getContext=()=>ctx;let observe;const controller=api.createController(root,{observe(callback){observe=callback;return {observe(){},disconnect(){}};}});t.after(()=>controller.destroy());observe([{target:card,isIntersecting:true}]);assert.equal(card.dataset.studyRenderer,'canvas');assert.equal(fills[0],'#ffffff');assert.ok(strokes.includes('#2563eb'));assert.ok(strokes.includes('#087f8c'));});
test('actual local math renderer typesets authored limits and no raw formula leaks',t=>{const dom=setup(t),win=dom.window;win.DSHRenderCore=require('../../blocks/render/assets/render-core.js');win.DSHStudyMath=require('../../blocks/render/assets/study-math.js');win.marked=require('../../blocks/render/vendor/marked.min.js');win.katex=require('../../blocks/render/vendor/katex.min.js');const card=api.createTeachingCard(win.document,{...plan(),formula:'\\sum_{n=1}^N z^n'});assert.ok(card.querySelector('.study-formula .katex'));assert.ok(card.querySelector('.study-formula .vlist'));assert.ok(!card.querySelector('.katex-error'));});
test('compact media omits duplicate author text and formula but keeps typeset live measurements',t=>{
 const dom=setup(t),w=dom.window;w.DSHAnimations=require('../../blocks/render/assets/animations.js');const katex=require('../../blocks/render/vendor/katex.min.js');w.katex={render(tex,node,options){node.innerHTML=katex.renderToString(tex,options);}};
 const p=plan(),card=api.createTeachingCard(w.document,p,{compact:true});dom.window.document.querySelector('main').append(card);
 assert.equal(card.querySelectorAll('.study-teaching-task,.study-formula,.study-eyebrow,.study-gesture-hint').length,0);
 assert.ok(card.querySelector('.study-readout .katex'));assert.ok(card.querySelector('details.study-camera-settings .study-view-tools'));
});
test('WebGL retains long authored axes and threshold segments while sampled pole jumps stay bounded',t=>{
 const dom=setup(t),w=dom.window,doc=w.document,root=doc.querySelector('main'),drawn=[];
 const position={set(){}};
 class Geometry{setFromPoints(points){this.points=points;return this;}dispose(){}}
 class Material{constructor(o){this.color=o.color;}dispose(){}}
 class Segment{constructor(g,m){this.geometry=g;this.material=m;drawn.push(g.points);}}
 class Group{constructor(){this.children=[];}add(x){this.children.push(x);}remove(x){this.children.splice(this.children.indexOf(x),1);}}
 w.WebGLRenderingContext=function(){};w.THREE={
  WebGLRenderer:class{constructor(){this.domElement=doc.createElement('canvas');}setPixelRatio(){}setSize(){}setClearColor(){}render(){}dispose(){}forceContextLoss(){}},
  Scene:class{add(){}},OrthographicCamera:class{constructor(){this.position=position;this.up=position;}lookAt(){}updateProjectionMatrix(){}},
  Group,BufferGeometry:Geometry,LineBasicMaterial:Material,LineSegments:Segment,
  Vector3:class{constructor(x,y,z){Object.assign(this,{x,y,z});}},SphereGeometry:Geometry,MeshBasicMaterial:Material,
  Mesh:class{constructor(g,m){this.geometry=g;this.material=m;this.position=position;}}
 };
 const card=api.createTeachingCard(doc,{...plan(),kind:'uniform',model_title:'一致收敛',params:{terms:8,bound:1,epsilon:.5}});root.append(card);
 let observe;const controller=api.createController(root,{observe(cb){observe=cb;return{observe(){},disconnect(){}};}});t.after(()=>controller.destroy());observe([{target:card,isIntersecting:true}]);
 assert.equal(card.dataset.studyRenderer,'webgl');
 assert.ok(drawn.some(points=>points.length===2&&Math.hypot(points[1].x-points[0].x,points[1].y-points[0].y)>3),'an explicit long coordinate axis must reach the WebGL draw list');
 assert.ok(drawn.some(points=>points.length===2&&Math.abs(points[0].y-.3)<1e-8&&Math.abs(points[1].y-.3)<1e-8),'the full epsilon threshold must remain visible');
});
