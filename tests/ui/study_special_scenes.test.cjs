const test=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),path=require('path');
const file=path.resolve(__dirname,'../../blocks/render/assets/study-special-scenes.js');
function api(){assert.ok(fs.existsSync(file),'special function scene provider must exist');return require(file);}
function close(actual,expected,tol=1e-8){assert.ok(Math.abs(actual-expected)<tol,`${actual} != ${expected}`);}
function model(title){const v=api(),scene=v.chooseScene(title,'');assert.ok(scene,title);return {scene,params:Object.fromEntries(scene.controls.map(c=>[c.key,c.value]))};}
test('complex Gamma satisfies known integral values, recurrence and poles',()=>{
 const v=api();close(v.gammaComplex([1,0])[0],1);close(v.gammaComplex([.5,0])[0],Math.sqrt(Math.PI));close(v.gammaComplex([-.5,0])[0],-2*Math.sqrt(Math.PI));
 const z=[.7,.9],g=v.gammaComplex(z),next=v.gammaComplex([z[0]+1,z[1]]);close(next[0],z[0]*g[0]-z[1]*g[1]);close(next[1],z[0]*g[1]+z[1]*g[0]);
 assert.equal(v.gammaComplex([-2,0]),null);assert.ok(v.gammaComplex([-2,.1]).every(Number.isFinite));
});
test('integer Bessel series has its standard values and differential recurrence',()=>{
 const v=api();close(v.besselJ(0,0,32),1);close(v.besselJ(1,0,32),0);close(v.besselJ(0,1,32),.7651976865579666);close(v.besselJ(1,1,32),.4400505857449335);
 close(v.besselJ(0,2.404825557695773,32),0);for(const x of [.4,2,6])close(v.besselJ(1,x,32)+v.besselJ(3,x,32),4/x*v.besselJ(2,x,32),1e-7);
 const m=model('Bessel函数的级数');const before=api().geometry(m);m.params.terms=1;assert.notDeepEqual(api().geometry(m).lines,before.lines);
});
test('causal convolution uses the actual sliding product and has the equal-rate limit',()=>{
 const v=api();close(v.convolution(2,1,1),2*Math.exp(-2));close(v.convolution(2,1,2),Math.exp(-2)-Math.exp(-4));close(v.convolution(-1,1,2),0);
 const m=model('卷积定理');m.params.time=2;m.params.a=1;m.params.b=2;const g=v.geometry(m);close(g.facts.value,v.convolution(2,1,2));close(g.facts.integral,g.facts.value,2e-6);assert.match(g.readout,/积分|卷积/);
});
test('Laplace laws retain initial values, scaling factor, causal delay and ROC',()=>{
 const v=api(),a=.7,s=1.2;close(v.laplaceValue('linear',{a,b:1.3,s,A:2,B:-.5}),2/(s+a)-.5/(s+1.3));
 close(v.laplaceValue('scale',{a,s,k:2}),1/(s+2*a));close(v.laplaceValue('derivative',{a,s}),-a/(s+a));close(v.laplaceValue('integral',{a,s}),1/(s*(s+a)));
 close(v.laplaceValue('delay',{a,s,tau:2}),Math.exp(-2*s)/(s+a));close(v.laplaceValue('shift',{a,s,c:.3}),1/(s+a-.3));
 close(v.laplaceValue('periodic',{a,s,period:2}),(1-Math.exp(-(s+a)*2))/((s+a)*(1-Math.exp(-2*s))));
 assert.equal(v.laplaceValue('shift',{a:.2,s:.3,c:1}),null);
 for(const name of ['线性性质','相似性质','微分性质','积分性质','延迟定理','位移定理','周期函数的拉氏变换'])assert.ok(model('拉普拉斯变换'+name).scene.kind.startsWith('special-laplace-'),name);
});
test('Fourier Gaussian has the correct normalization and time-shift phase',()=>{
 const v=api();close(v.fourierGaussian(0,1,0)[0],Math.sqrt(Math.PI));const value=v.fourierGaussian(2,1,.4),r=Math.sqrt(Math.PI)*Math.exp(-1);close(value[0],r*Math.cos(.8));close(value[1],-r*Math.sin(.8));
 close(v.squareWaveSeries(Math.PI/2,40),1,.02);assert.match(model('Fourier级数的吉布斯现象').scene.explanation,/Gibbs|吉布斯/);
});
test('step and impulse approximations state the causal convention and conserve unit area',()=>{
 const v=api();const m=model('单位冲激函数的抽样性质');m.params.epsilon=.2;const g=v.geometry(m);close(g.facts.area,1);close(g.facts.height,5);assert.match(m.scene.explanation,/不是普通函数|近似/);
 assert.match(model('单位阶跃函数').scene.formula,/u\(t/);assert.equal(v.chooseScene('完全无关的几何定义',''),null);
});
test('providers emit finite real geometry and every offered parameter changes the scene',()=>{
 for(const title of ['弗雷涅积分','F(p)→0 只在特定方向下成立','三角与双曲公式不要混用','回路电压定律','条件 f(t)/t 有界不能省','双曲余切形式的来源','重因式不能只列一次','Z变换','例15 结果式的适用条件','换元时不要丢掉 i','B 函数与 Γ 函数的关系','傅氏反变换','像函数的积分性质','像函数的微分法','Gamma函数及其极点','第一类贝塞尔函数','卷积的定义','拉氏变换线性性质','拉氏变换相似定理','拉氏变换微分性质','拉氏变换积分性质','拉氏变换延迟定理','拉氏变换位移定理','周期函数的拉氏变换','傅里叶变换','傅里叶级数','单位阶跃函数','狄拉克冲激函数']){
  const m=model(title),v=api(),g=v.geometry(m);assert.ok(g.lines.length>=2,title);assert.ok(g.readout&&m.scene.formula&&m.scene.explanation);assert.equal(m.scene.illustrative,true);
  for(const line of g.lines)for(const p of line.points)assert.ok(p.length===3&&p.every(Number.isFinite),title);
  for(const c of m.scene.controls){const before=JSON.stringify(v.geometry(m));m.params[c.key]=c.value===c.max?c.min:c.max;assert.notEqual(JSON.stringify(v.geometry(m)),before,title+' '+c.key);m.params[c.key]=c.value;}
 }
});
test('book terminology distinguishes image-domain calculus and Fourier inverse from time-domain laws',()=>{
 const v=api();for(const [title,kind]of [['复 Γ 函数','gamma'],['Γ(z) 无零点','gamma'],['B 函数与 Γ 函数的关系','beta'],['单位阶跃函数','step'],['傅氏变换','fourier'],['傅氏反变换','fourier-inverse'],['相似变换','laplace-scale'],['本函数的积分法','laplace-integral'],['像函数的积分性质','laplace-image-integral'],['像函数的微分法','laplace-image-derivative'],['延迟函数','laplace-delay']])assert.equal(model(title).scene.kind,'special-'+kind,title);
 close(v.laplaceValue('image-derivative',{a:1,s:2}),1/9);close(v.laplaceValue('image-integral',{a:1,s:2}),1/3);
 close(v.beta(1,1),1);close(v.beta(.5,.5),Math.PI);
 const m=model('傅氏反变换');m.params.cutoff=8;m.params.a=1;m.params.cursor=.5;close(v.geometry(m).facts.reconstructed,Math.exp(-.25),1e-6);
});

test('browser provider queues itself before the engine and is available for offline registration',()=>{
 const vm=require('node:vm'),context={};vm.runInNewContext(fs.readFileSync(file,'utf8'),context);
 assert.equal(context.DSHStudySceneExtensions.length,1);const provider=context.DSHStudySceneExtensions[0];assert.equal(provider.chooseScene('傅氏变换','').kind,'special-fourier');
 const engine=require('../../blocks/render/assets/study-visuals.js');engine.registerScenes(api());const m=engine.createModel(engine.chooseScene('Gamma函数',''));assert.equal(engine.geometry(m).facts.value.length,2);
});
test('Greek angle parameters never trigger Beta or Gamma special functions',()=>{
 const v=api();for(const [title,body]of [
  ['辐角相加',String.raw`设 $z_1=r_1e^{i\alpha}$，$z_2=r_2e^{i\beta}$，乘积辐角是 $\alpha+\beta$。`],
  ['约当曲线',String.raw`$\gamma(t)$ 绕过顶点，角度 $\beta=\pi/2$。`],
  ['小圆弧',String.raw`令 $\beta$ 为弧角，$\gamma$ 为积分路径。`],
  ['Schwarz-Christoffel角度',String.raw`$\beta_k\pi$ 是内角。`],
 ])assert.equal(v.chooseScene(title,body),null,title);
 assert.equal(v.chooseScene('Beta函数','').kind,'special-beta');assert.equal(v.chooseScene('Gamma函数','').kind,'special-gamma');
 assert.equal(v.chooseScene('B 函数与 Γ 函数的关系','').kind,'special-beta');
});
test('ambiguous unit function uses causal step conditions and leaves identity maps to their own provider',()=>{
 const v=api();assert.equal(v.chooseScene('单位函数',''),null);assert.equal(v.chooseScene('单位函数','恒等映照 f(z)=z'),null);
 assert.equal(v.chooseScene('单位函数','h(t)=0 (t<0)，h(t)=1 (t>0)').kind,'special-step');
});
test('negative Bessel orders keep parity and Fresnel integrals use their stated t squared convention',()=>{
 const v=api();close(v.besselJ(-1,1,32),-v.besselJ(1,1,32));close(v.besselJ(-2,1,32),v.besselJ(2,1,32));
 assert.equal(model(String.raw`$J_{-n}$ 的符号因子`).scene.kind,'special-bessel');
 const f=v.fresnel(1,1);close(f[0],.904524237900272,1e-8);close(f[1],.310268301723381,1e-8);
 assert.equal(model('弗雷涅积分').scene.kind,'special-fresnel');
});
test('remaining Laplace application models preserve ROC bounds, circuit voltage and repeated factors',()=>{
 const v=api(),roc=model('F(p)→0 只在特定方向下成立');roc.params.c=.5;roc.params.sigma=1.5;roc.params.omega=2;
 const g=v.geometry(roc);close(g.facts.bound,1);close(g.facts.value[0],.2);close(g.facts.value[1],-.4);roc.params.sigma=.1;assert.equal(v.geometry(roc).facts.converges,false);
 const rc=v.rcState(2,1,2,3);close(rc.vr+rc.vc,3);close(rc.current,3*Math.exp(-1));
 assert.equal(model('回路电压定律').scene.kind,'special-rc');assert.equal(model('重因式不能只列一次').scene.kind,'special-repeated-pole');
 assert.equal(model('别把求导公式的负号丢掉').scene.kind,'special-laplace-image-derivative');
 const mix=model('三角与双曲公式不要混用');mix.params.s=.5;mix.params.omega=1;assert.equal(v.geometry(mix).facts.coshTransform,null);
});
test('division by t is bounded only after the nonzero initial term is removed',()=>{
 const v=api(),m=model(String.raw`条件 $t\to0^+$ 时 $|f(t)/t|$ 有界不能省`);m.params.initial=0;m.params.epsilon=.001;m.params.a=1;m.params.s=1;
 close(v.geometry(m).facts.integral,Math.exp(-.002)/2,1e-7);m.params.initial=1;const first=v.geometry(m).facts.integral;m.params.epsilon=.0001;assert.ok(v.geometry(m).facts.integral>first+2);
 const coth=model('双曲余切形式的来源');close(v.geometry(coth).facts.coth,v.geometry(coth).facts.exponentialRatio);
});
test('geometric Z transform and equal-frequency inverse use actual convergent sums and limits',()=>{
 const v=api(),z=v.zTransform(.5,[2,0],30);close(z.partial[0],4/3);close(z.reference[0],4/3);assert.equal(z.converges,true);assert.equal(v.zTransform(1,[.5,0],10).converges,false);
 assert.equal(model('Z变换').scene.kind,'special-z-transform');
 close(v.inverseOscillatory(0,0,2),8/6);close(v.inverseOscillatory(1,1,2),(Math.sin(2)-2*Math.cos(2))/2);close(v.inverseOscillatory(1,1.00001,2),v.inverseOscillatory(1,1,2),1e-4);
 assert.equal(model('例15 结果式的适用条件').scene.kind,'special-inverse-oscillatory');
 close(v.bromwich(1,1,1,80),Math.exp(-1),.03);assert.equal(model(String.raw`换元时不要丢掉 $\mathrm{i}$`).scene.kind,'special-bromwich');
});

test('Z transform distinguishes the pole from a merely divergent geometric series',()=>{
 const v=api(),m=model('Z变换');m.params.r=1;m.params.radius=1;m.params.angle=0;const g=v.geometry(m);
 assert.equal(g.facts.converges,false);assert.equal(g.facts.reference,null);for(const l of g.lines)for(const point of l.points)assert.ok(point.every(Number.isFinite));
});
test('coth transform factor does not claim unrelated complex hyperbolic definitions',()=>{
 const provider=require('../../blocks/render/assets/study-special-scenes.js');
 assert.equal(provider.chooseScene('双曲函数','定义 sinh z, cosh z, tanh z, coth z；coth z=cosh z/sinh z。'),null);
 assert.equal(provider.chooseScene('双曲余切形式的来源','(1+e^{-pT})/(1-e^{-pT})=coth(pT/2)').kind,'special-coth');
});
