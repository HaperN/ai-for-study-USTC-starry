const test=require('node:test'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const visuals=require('../../blocks/render/assets/study-visuals.js');
function near(a,b,tol=1e-9){assert.ok(Math.abs(a-b)<tol,`${a} != ${b}`);}
function mount(t,title,kind,backend='webgl'){
 const dom=new JSDOM('<main></main>',{pretendToBeVisual:true}),w=dom.window,d=w.document,root=d.querySelector('main'),cameras=[],paths=[],dots=[];
 const ctx={fillRect(){paths.length=0;dots.length=0;},beginPath(){},moveTo(x,y){paths.push([x,y]);},lineTo(x,y){paths.push([x,y]);},arc(x,y,r){dots.push([x,y,r]);},fill(){},stroke(){}};
 w.HTMLCanvasElement.prototype.getContext=()=>ctx;
 if(backend==='webgl'){
  class Geometry{setFromPoints(points){this.points=points;return this;}dispose(){}}
  class Material{constructor(options){Object.assign(this,options);}dispose(){}}
  class Group{constructor(){this.children=[];}add(x){this.children.push(x);}remove(x){this.children.splice(this.children.indexOf(x),1);}}
  w.WebGLRenderingContext=function(){};
  w.THREE={WebGLRenderer:class{constructor(){this.domElement=d.createElement('canvas');}setPixelRatio(){}setSize(){}setClearColor(){}render(){}dispose(){}forceContextLoss(){}},
   Scene:class{add(){}},OrthographicCamera:class{constructor(left,right,top,bottom){Object.assign(this,{left,right,top,bottom});this.position={set(){}};this.up={set(){}};cameras.push(this);}lookAt(){}updateProjectionMatrix(){}},Group,
   BufferGeometry:Geometry,LineBasicMaterial:Material,LineSegments:class{constructor(g,m){this.geometry=g;this.material=m;}},Vector3:class{constructor(x,y,z){Object.assign(this,{x,y,z});}},SphereGeometry:Geometry,MeshBasicMaterial:Material,
   Mesh:class{constructor(g,m){this.geometry=g;this.material=m;this.position={set(){}};}}};
 }
 const plan={id:'FIT-'+kind,kind,model_title:title,title,formula:'f(z)',params:{}},card=visuals.createTeachingCard(d,plan,{compact:true});root.append(card);
 const viewport=card.querySelector('.study-viewport');Object.defineProperty(viewport,'clientWidth',{value:263});Object.defineProperty(viewport,'clientHeight',{value:210});
 let show;const controller=visuals.createController(root,{observe(cb){show=cb;return{observe(){},disconnect(){}};}});show([{target:card,isIntersecting:true}]);
 t.after(()=>{controller.destroy();dom.window.close();});
 function set(key,value){const el=card.querySelector(`input[type=number][data-study-param="${key}"]`);el.value=value;el.dispatchEvent(new w.Event('input',{bubbles:true}));}
 return{w,card,model:card.__studyModel,camera:cameras[0],paths,dots,set};
}
for(const [title,kind]of [['柯西公式积分读出','cauchy'],['平方映照','square']]){
 test(`narrow real WebGL camera contains both ${kind} grids without nonuniform units`,t=>{
  const m=mount(t,title,kind),cam=m.camera;
  // These are the actual factory grid/axis endpoints, independently of the
  // camera-fit implementation. Previously x=+/-4.25 was clipped at +/-3.88.
  for(const x of [-4.54,-4.25,4.25,4.54])assert.ok(x>cam.left&&x<cam.right,`${x} is clipped`);
  near((cam.right-cam.left)/263,(cam.top-cam.bottom)/210);
  const width=cam.right-cam.left;m.card.querySelector('[data-study-action="zoom-in"]').click();near((cam.right-cam.left)*1.2,width);
 });
 test(`narrow actual Canvas fallback contains the default ${kind} grids`,t=>{
  const m=mount(t,title,kind,'canvas');assert.equal(m.card.dataset.studyRenderer,'canvas');assert.ok(m.paths.length>20);
  for(const [x,y]of m.paths)assert.ok(x>=0&&x<=263&&y>=0&&y<=210,`grid leaves canvas: ${x},${y}`);
  assert.ok(Math.min(...m.paths.map(p=>p[0]))<30&&Math.max(...m.paths.map(p=>p[0]))>233,'two planes should occupy the small canvas');
 });
}
test('narrow exponential camera fills its own smaller domain at both phase limits',t=>{
 const m=mount(t,'指数函数','exp');near(m.model.view.zoom,1);
 for(const phase of [-6.28,6.28]){
  m.set('phase',phase);const g=visuals.geometry(m.model),cam=m.camera,coords=[...g.points.map(p=>p.position),...g.lines.flatMap(l=>l.points)];
  for(const [x,y]of coords)assert.ok(x>cam.left&&x<cam.right&&y>cam.bottom&&y<cam.top,`exponential coordinate clipped: ${x},${y}`);
  const coverage=(Math.max(...coords.map(p=>p[0]))-Math.min(...coords.map(p=>p[0])))/(cam.right-cam.left);assert.ok(coverage>.7,'exp should not inherit a mostly empty general two-plane viewport');
 }
});
test('WebGL and Canvas use the same exponential viewport scale',t=>{
 const gl=mount(t,'指数函数','exp'),canvas=mount(t,'指数函数','exp','canvas');
 for(const phase of [-6.28,6.28]){
  gl.set('phase',phase);canvas.set('phase',phase);const points=visuals.geometry(gl.model).points,cam=gl.camera;
  assert.equal(canvas.dots.length,points.length);
  points.forEach((p,i)=>{const expectedX=(p.position[0]-cam.left)/(cam.right-cam.left)*263,expectedY=(cam.top-p.position[1])/(cam.top-cam.bottom)*210;near(canvas.dots[i][0],expectedX);near(canvas.dots[i][1],expectedY);});
 }
});
test('single-plane camera and harmonic top-down preset keep their existing bases',t=>{
 const single=mount(t,'复数加法','addition');near(single.camera.top,3.1);near(single.camera.bottom,-3.1);
 const harmonic=mount(t,'调和函数','harmonic');near(harmonic.model.view.pitch,0);
});
test('essential marker keeps exp(6.25) and adds a distinct finite-window direction arrow',()=>{
 const m=visuals.createModel(visuals.chooseScene('本性奇点',''));m.set('phase',0);m.set('extent',.2);const g=visuals.geometry(m),actual=Math.exp(6.25);
 near(m.map(.16,0)[0],actual,1e-8);near(g.points[1].position[0]-2.65,actual,1e-8);
 const arrows=g.lines.filter(l=>l.role==='offscreen-direction');assert.ok(arrows.length>=3,'large actual image needs a separate visible direction cue');
 for(const l of arrows)for(const[x,y]of l.points)assert.ok(x>2.65&&x<4.4&&Math.abs(y)<1.6,'the direction cue belongs to the right finite window');
 assert.match(g.labels.map(l=>l[0]).join(' '),/超窗方向/);assert.match(g.labels.map(l=>l[0]).join(' '),/实际.*读数/);assert.match(g.readout,/非实际像点/);
 for(const phase of [Math.PI/2,Math.PI]){m.params.phase=phase;const finite=visuals.geometry(m);assert.equal(finite.lines.filter(l=>l.role==='offscreen-direction').length,0);assert.ok(finite.points[1].position.every(Number.isFinite));}
});
test('essential outlier never shrinks the actual camera or replaces its real image',t=>{
 const m=mount(t,'本性奇点','essential'),before=[m.camera.left,m.camera.right,m.camera.top,m.camera.bottom];m.set('phase',0);m.set('extent',.2);
 assert.deepEqual([m.camera.left,m.camera.right,m.camera.top,m.camera.bottom],before);near(visuals.geometry(m.model).points[1].position[0]-2.65,Math.exp(6.25),1e-8);
});
