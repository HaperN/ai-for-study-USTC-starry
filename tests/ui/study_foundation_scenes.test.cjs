const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const file=path.resolve(__dirname,'../../blocks/render/assets/study-foundation-scenes.js');
function api(){assert.ok(fs.existsSync(file),'foundation scene provider must exist');return require(file);}
function model(title,params={}){const scene=api().chooseScene(title,'');assert.ok(scene,title);return {scene,params:{...Object.fromEntries(scene.controls.map(c=>[c.key,c.value])),...params}};}
function near(a,b,t=1e-9){assert.ok(Math.abs(a-b)<t,`${a} ≈ ${b}`);}
function mul(a,b){return [a[0]*b[0]-a[1]*b[1],a[0]*b[1]+a[1]*b[0]];}
test('specific foundation and method titles select their own disclosed mathematical model',()=>{
 const p=api();for(const [title,kind] of [['实部与虚部','foundation-components'],['纯虚数','foundation-components'],['虚单位','foundation-components'],['单值函数与多值函数','foundation-injective'],['一一映照（双方单值映照）','foundation-injective'],['角域 D_k 的单叶映照','foundation-injective'],['恒等函数','foundation-affine'],['旋转变换','foundation-affine'],['反函数的求导法则','foundation-inverse'],['求导法则','foundation-chain'],['极限路径与连续','foundation-limits'],['柯西－黎曼条件 C-R','foundation-cr'],['牛顿－莱布尼兹公式','foundation-primitive'],['复积分的反向路径性质','foundation-primitive'],['长大不等式','foundation-ml'],['德·莫弗公式','foundation-roots'],['单位圆上的n次根','foundation-roots'],['支割线','foundation-branch'],['泊松积分','foundation-poisson'],['狄氏问题 III','foundation-poisson'],['无源无旋场','foundation-field'],['静电场复势与场强关系','foundation-field']]){
  const s=p.chooseScene(title,'');assert.equal(s?.kind,kind,title);assert.equal(s.illustrative,true);assert.match(s.label,/代表|示意/);assert.ok(s.formula&&s.explanation&&s.controls.length);
 }
 assert.equal(p.chooseScene('单位函数',''),null,'unit function may mean the Heaviside step, not identity');assert.equal(p.chooseScene('未识别的抽象理论',''),null);assert.equal(p.geometry({scene:{kind:'other'},params:{}}),null);
 assert.equal(p.chooseScene('一个易错点','长大不等式中的 M 是整条曲线上的上界').kind,'foundation-ml');
});
test('real and imaginary components and the imaginary unit keep Im z real-valued',()=>{
 const p=api(),m=model('纯虚数',{real:1.2,imag:-.8});let g=p.geometry(m);near(g.facts.real,0);near(g.facts.imag,-.8);assert.equal(g.facts.pureImaginary,true);
 m.params.imag=0;g=p.geometry(m);assert.equal(g.facts.pureImaginary,false,'zero is excluded by this book pure-imaginary convention');
 const z=p.geometry(model('实部与虚部',{real:1.2,imag:-.8}));near(z.facts.real,1.2);near(z.facts.imag,-.8);near(z.facts.modulus,Math.hypot(1.2,.8));
});
test('single-valued z² has a concrete noninjective witness and restricted-domain correspondence',()=>{
 const p=api(),m=model('单值函数',{angle:.7,radius:1.2,restricted:0}),g=p.geometry(m),[a,b]=g.facts.inputs;
 assert.notDeepEqual(a,b);assert.deepEqual(g.facts.outputs[0],g.facts.outputs[1]);assert.equal(g.facts.injectiveOnShownDomain,false);
 m.params.restricted=1;assert.equal(p.geometry(m).facts.injectiveOnShownDomain,true);assert.match(p.geometry(m).readout,/右半平面|Re z > 0/);
 near(m.scene.map(1,1,m.params)[1],2);
});
test('affine and inverse maps obey the inverse derivative relation',()=>{
 const m=model('反函数的求导法则',{scale:1.6,real:.6,imag:-.4}),g=api().geometry(m);near(g.facts.derivative*g.facts.inverseDerivative,1);near(g.facts.restored[0],.6);near(g.facts.restored[1],-.4);
 const a=model('旋转变换',{scale:1,angle:Math.PI/2,real:0,imag:0});const z=a.scene.map(1,0,a.params);near(z[0],0);near(z[1],1);
});
test('chain rule derivative equals the direct derivative of (az)²',()=>{
 const g=api().geometry(model('求导法则',{scale:1.4,real:.6,imag:.3}));near(g.facts.derivative[0],2*1.4**2*.6);near(g.facts.derivative[1],2*1.4**2*.3);near(g.facts.chainDerivative[0],g.facts.derivative[0]);near(g.facts.chainDerivative[1],g.facts.derivative[1]);
});
test('direction-dependent limits remain distinct while continuous z² tends to zero',()=>{
 const p=api();near(p.geometry(model('极限路径',{direction:0,mode:0})).facts.value[0],1);near(p.geometry(model('极限路径',{direction:Math.PI/2,mode:0})).facts.value[0],-1);
 const g=p.geometry(model('极限路径',{radius:.02,direction:.8,mode:1}));near(Math.hypot(...g.facts.value),.02**2);assert.equal(g.facts.pathIndependent,true);
});
test('Cauchy Riemann compares actual partial derivatives at a nonzero point',()=>{
 const p=api(),yes=p.geometry(model('C-R条件',{coefficient:1}));assert.deepEqual(yes.facts.residual,[0,0]);assert.equal(yes.facts.satisfiesCR,true);
 const no=p.geometry(model('C-R条件',{coefficient:.4}));assert.ok(Math.hypot(...no.facts.residual)>.1);assert.equal(no.facts.satisfiesCR,false);
});
test('primitive evaluation is path independent and reversed paths reverse the integral',()=>{
 const p=api(),a=p.geometry(model('牛顿－莱布尼兹公式',{real:1.2,imag:.6,bend:.1,reverse:0})),b=p.geometry(model('牛顿－莱布尼兹公式',{real:1.2,imag:.6,bend:1.1,reverse:0}));
 near(a.facts.integral[0],1.08);near(a.facts.integral[1],1.44);near(a.facts.integral[0],b.facts.integral[0]);near(a.facts.integral[1],b.facts.integral[1]);
 const c=p.geometry(model('复积分的反向路径性质',{real:1.2,imag:.6,bend:.4,reverse:1}));near(c.facts.integral[0],-1.08);near(c.facts.integral[1],-1.44);
});
test('ML uses a bound over the whole arc and the true complex primitive difference',()=>{
 const p=api();for(const r of [.3,.8,1.4])for(const angle of [.4,2.1,5.4]){
  const g=p.geometry(model('长大不等式',{radius:r,angle}));near(g.facts.length,r*angle);near(g.facts.maximum,Math.exp(r));assert.ok(g.facts.integralMagnitude<=g.facts.bound+1e-9);
 }
});
test('all displayed roots raise to the chosen complex number and no root is duplicated',()=>{
 const g=api().geometry(model('德·莫弗公式',{order:5,radius:1.3,angle:.8}));assert.equal(g.facts.roots.length,5);assert.equal(new Set(g.facts.roots.map(z=>z.join(','))).size,5);
 for(const r of g.facts.roots){let z=[1,0];for(let k=0;k<5;k++)z=mul(z,r);near(z[0],1.3*Math.cos(.8));near(z[1],1.3*Math.sin(.8));}
});
test('log branch geometry separates the cut and exposes a 2pi argument jump',()=>{
 const m=model('支割线',{cut:-Math.PI,epsilon:.03,radius:1.2}),g=api().geometry(m);near(Math.abs(g.facts.arguments[0]-g.facts.arguments[1]),2*Math.PI-.06);near(g.facts.values[0][0],Math.log(1.2));
 const oncut=m.scene.map(-1,0,m.params);assert.ok(oncut.every(Number.isNaN),'the cut is excluded from the displayed branch');
});
test('Poisson integral reproduces its harmonic Fourier mode and keeps r below one',()=>{
 const g=api().geometry(model('泊松积分',{order:3,radius:.6,angle:.4}));near(g.facts.poissonIntegral,.6**3*Math.cos(1.2),1e-7);near(g.facts.value,g.facts.poissonIntegral,1e-7);assert.ok(g.facts.radius<1);
});
test('plane field derives flow direction from the conjugate complex derivative',()=>{
 const p=api(),uniform=p.geometry(model('平面向量场',{mode:0,strength:1.2,angle:.6}));near(uniform.facts.vector[0],1.2*Math.cos(.6));near(uniform.facts.vector[1],1.2*Math.sin(.6));assert.equal(uniform.facts.divergence,0);assert.equal(uniform.facts.curl,0);
 const point=p.geometry(model('平面点电荷',{mode:1,strength:1.2,angle:.6}));near(point.facts.flux,2*Math.PI*1.2);near(point.facts.circulation,0);assert.match(point.readout,/原点|除外/);
});
test('each family control changes real geometry and every coordinate stays finite',()=>{
 for(const title of ['实部与虚部','单值函数','旋转变换','反函数的求导法则','求导法则','极限路径','C-R条件','牛顿－莱布尼兹公式','长大不等式','德·莫弗公式','支割线','泊松积分','平面向量场']){
  const m=model(title),p=api(),before=JSON.stringify(p.geometry(m).lines),c=m.scene.controls.find(c=>c.key!=='restricted');m.params[c.key]=c.max;const g=p.geometry(m);
  assert.notEqual(JSON.stringify(g.lines),before,title);assert.ok(g.readout.length);for(const line of g.lines)for(const point of line.points)assert.ok(point.length===3&&point.every(Number.isFinite),title);
 }
});
test('browser provider registers in the extension array without a live renderer',()=>{
 const context={};vm.runInNewContext(fs.readFileSync(file,'utf8'),context);assert.equal(context.DSHStudySceneExtensions.length,1);assert.equal(typeof context.DSHStudySceneExtensions[0].chooseScene,'function');
});

test('every advertised control changes displayed geometry or a numerical result',()=>{
 for(const title of ['实部与虚部','纯虚数','虚单位','单值函数','恒等函数','旋转变换','反函数的求导法则','求导法则','极限路径','C-R条件','牛顿－莱布尼兹公式','长大不等式','德·莫弗公式','支割线','泊松积分','平面向量场','平面点电荷','ε-N 定义的几何含义','无界集','例2中约去 $z-\\bar z$ 的前提','例3分母的化简','梯度为什么垂直于等值线','关于圆周的对称点','直交圆']){
  const base=model(title),provider=api(),before=provider.geometry(base);
  for(const control of base.scene.controls){const m=model(title),value=control.max!==control.value?control.max:control.min;m.params[control.key]=value;const after=provider.geometry(m);assert.notEqual(JSON.stringify([after.lines,after.points,after.readout]),JSON.stringify([before.lines,before.points,before.readout]),title+' '+control.key);}
 }
});
test('integral method scene shows linearity and path additivity with independently summed subpaths',()=>{
 const g=api().geometry(model('复积分的路径可加性',{real:1,imag:.5,bend:.7,coefficient:.6,split:.4}));
 near(g.facts.integral[0],.45);near(g.facts.integral[1],.6);
 for(let i=0;i<2;i++){near(g.facts.parts[0][i]+g.facts.parts[1][i],g.facts.integral[i]);near(.6*g.facts.unscaledIntegral[i],g.facts.integral[i]);}
 assert.match(g.readout,/分段|相加/);assert.match(g.readout,/倍|c=/);
});
test('epsilon N and unbounded sets use actual witnesses rather than a generic disk',()=>{
 const p=api(),s=p.geometry(model('ε-N 定义的几何含义',{index:20,epsilon:.1}));near(s.facts.distance,Math.SQRT2/20);assert.ok(s.facts.tailThreshold>=Math.SQRT2/.1);assert.equal(s.facts.inside,true);
 const b=p.geometry(model('无界集',{bound:2.3}));assert.ok(b.facts.witnessModulus>b.facts.bound);near(b.facts.witnessModulus,3.3);
});
test('conjugate cancellation and the exact denominator expose their zero conditions',()=>{
 const p=api();assert.equal(p.geometry(model('例2中约去 $z-\\bar z$ 的前提',{real:.5,imag:0})).facts.canCancel,false);
 assert.equal(p.geometry(model('例2中约去 $z-\\bar z$ 的前提',{real:.5,imag:.2})).facts.canCancel,true);
 const d=p.geometry(model('例3分母的化简',{real:.4,imag:.3}));near(d.facts.denominator,.45);assert.ok(Math.abs(d.facts.denominator-d.facts.wrongExpression)>.1);
 assert.equal(p.geometry(model('例3分母的化简',{real:1,imag:0})).facts.defined,false);
});
test('gradient and level tangent are orthogonal at a regular hyperbola point',()=>{
 const g=api().geometry(model('梯度为什么垂直于等值线',{level:.7,parameter:.6}));near(g.facts.point[0]**2-g.facts.point[1]**2,.7);near(g.facts.gradient[0]*g.facts.tangent[0]+g.facts.gradient[1]*g.facts.tangent[1],0);assert.ok(Math.hypot(...g.facts.gradient)>0);
});
test('circle symmetry uses radial inverse and orthogonal circles have perpendicular radius vectors',()=>{
 const p=api(),g=p.geometry(model('关于圆周的对称点',{radius:1.2,pointRadius:.6,angle:.8}));near(g.facts.radiusProduct,1.2**2);near(g.facts.original[0]*g.facts.reflected[1]-g.facts.original[1]*g.facts.reflected[0],0);
 const o=p.geometry(model('直交圆',{radius:1.2,ratio:1.8,angle:.8}));near(o.facts.radiusDotProduct,0);near(o.facts.centerDistance**2,o.facts.radius**2+o.facts.otherRadius**2);
});
test('provider integrates with the production model setters and geometry dispatch',()=>{
 const main=require('../../blocks/render/assets/study-visuals.js');main.registerScenes(api());
 for(const title of ['纯虚数','反函数的求导法则','求导法则','C-R条件','牛顿－莱布尼兹公式','长大不等式','德·莫弗公式','支割线','泊松积分','平面点电荷','ε-N 定义的几何含义','例3分母的化简']){
  const scene=main.chooseScene(title,''),m=main.createModel(scene);assert.match(scene.kind,/^foundation-/);const first=scene.controls[0];assert.equal(m.set(first.key,first.max),true);const g=main.geometry(m);assert.ok(g.lines.length&&g.readout);m.reset();assert.equal(m.params[first.key],first.value);
 }
});
test('remaining plane-field definitions preserve extrusion, Gauss flux and Green circulation meanings',()=>{
 const provider=require('../../blocks/render/assets/study-foundation-scenes.js');function make(title,body=''){const scene=provider.chooseScene(title,body);assert.ok(scene,title);return {scene,params:Object.fromEntries(scene.controls.map(c=>[c.key,c.value]))};}
 const circle=make('定义3','两个点在同一条射线上，距离之积为R²，关于圆周C对称。');assert.equal(circle.scene.kind,'foundation-circle');
 const point=make('平面场中的点'),pg=provider.geometry(point);assert.equal(pg.facts.representsInfiniteLine,true);assert.ok(pg.lines.some(l=>l.points.some(p=>p[2]!==0)));
 const charged=make('均匀荷电曲线'),cg=provider.geometry(charged);assert.equal(cg.facts.representsInfiniteCylinder,true);assert.ok(cg.lines.some(l=>l.points.some(p=>p[2]!==0)));
 const gauss=make('高斯定理'),gg=provider.geometry(gauss);assert.ok(Math.abs(gg.facts.flux-gg.facts.divergenceIntegral)<1e-10);
 const green=make('格林公式'),gr=provider.geometry(green);assert.deepEqual(gr.facts.integral,[gr.facts.circulation,gr.facts.flux]);
 const s=make('叠加原理'),sg=provider.geometry(s);assert.ok(Math.abs(sg.facts.sum[0]-sg.facts.first[0]-sg.facts.second[0])<1e-10);assert.ok(Math.abs(sg.facts.sum[1]-sg.facts.first[1]-sg.facts.second[1])<1e-10);
});
