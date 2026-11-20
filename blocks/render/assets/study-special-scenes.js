/* Offline special-function and transform explorations. All functions are local,
 * deterministic representative models; original textbook prose is never edited. */
(function(root,factory){'use strict';var api=factory();if(typeof module==='object'&&module.exports)module.exports=api;
 else{(root.DSHStudySceneExtensions||(root.DSHStudySceneExtensions=[])).push(api);if(root.DSHStudyVisuals)root.DSHStudyVisuals.registerScenes(api);}
})(typeof globalThis!=='undefined'?globalThis:this,function(){'use strict';
 var BLUE='#78aaff',TEAL='#54e0c0',GOLD='#ffcd70',PINK='#f6a0ca',GRAY='#526781',PI=Math.PI;
 function ctl(key,label,min,max,step,value){return {key:key,label:label,min:min,max:max,step:step,value:value};}
 function add(a,b){return[a[0]+b[0],a[1]+b[1]];}function mul(a,b){return[a[0]*b[0]-a[1]*b[1],a[0]*b[1]+a[1]*b[0]];}
 function div(a,b){var d=b[0]*b[0]+b[1]*b[1];return[(a[0]*b[0]+a[1]*b[1])/d,(a[1]*b[0]-a[0]*b[1])/d];}
 function exp(z){return[Math.exp(z[0])*Math.cos(z[1]),Math.exp(z[0])*Math.sin(z[1])];}
 function log(z){return[Math.log(Math.hypot(z[0],z[1])),Math.atan2(z[1],z[0])];}
 function sin(z){return[Math.sin(z[0])*Math.cosh(z[1]),Math.cos(z[0])*Math.sinh(z[1])];}
 // Lanczos g=7, with the reflection formula on Re z < 1/2.
 function gammaComplex(z){var x=z[0],y=z[1];if(!Number.isFinite(x)||!Number.isFinite(y))return null;
  if(Math.abs(y)<1e-12&&x<=0&&Math.abs(x-Math.round(x))<1e-12)return null;
  if(x<.5){var reflected=gammaComplex([1-x,-y]);if(!reflected)return null;return div([PI,0],mul(sin([PI*x,PI*y]),reflected));}
  var c=[.99999999999980993,676.5203681218851,-1259.1392167224028,771.32342877765313,-176.61502916214059,12.507343278686905,-.13857109526572012,9.984369578019572e-6,1.5056327351493116e-7];
  var w=[x-1,y],sum=[c[0],0];for(var i=1;i<c.length;i++)sum=add(sum,div([c[i],0],[w[0]+i,w[1]]));
  var t=[w[0]+7.5,w[1]],power=exp(mul([w[0]+.5,w[1]],log(t))),result=mul(mul(power,exp([-t[0],-t[1]])),sum);
  return[result[0]*Math.sqrt(2*PI),result[1]*Math.sqrt(2*PI)];
 }
 function besselJ(order,x,terms){order=Math.round(order);if(order<0)return (Math.abs(order)%2?-1:1)*besselJ(-order,x,terms);order=Math.max(0,order);terms=Math.max(1,Math.round(terms||36));var term=1;for(var j=1;j<=order;j++)term*=x/(2*j);var sum=term;
  for(var k=1;k<terms;k++){term*=-x*x/(4*k*(order+k));sum+=term;}return sum;
 }
 function beta(a,b){return gammaComplex([a,0])[0]*gammaComplex([b,0])[0]/gammaComplex([a+b,0])[0];}
 function convolution(t,a,b){if(t<=0)return 0;return Math.abs(a-b)<1e-9?t*Math.exp(-a*t):(Math.exp(-a*t)-Math.exp(-b*t))/(b-a);}
 function simpson(f,a,b,n){n=n||400;if(n%2)n++;var h=(b-a)/n,sum=f(a)+f(b);for(var i=1;i<n;i++)sum+=(i%2?4:2)*f(a+i*h);return sum*h/3;}
 function laplaceValue(mode,p){var a=p.a,s=p.s;if(!(s>0)||!(a>0))return null;
  if(mode==='linear')return p.A/(s+a)+p.B/(s+p.b);
  if(mode==='scale')return p.k>0?1/(s+a*p.k):null;
  if(mode==='derivative')return-a/(s+a);
  if(mode==='image-derivative')return 1/((s+a)*(s+a));
  if(mode==='image-integral')return 1/(s+a);
  if(mode==='integral')return 1/(s*(s+a));
  if(mode==='delay')return Math.exp(-s*p.tau)/(s+a);
  if(mode==='shift')return s+a-p.c>0?1/(s+a-p.c):null;
  if(mode==='periodic')return(1-Math.exp(-(s+a)*p.period))/((s+a)*(1-Math.exp(-s*p.period)));
  return 1/(s+a);
 }
 function fourierGaussian(omega,a,tau){var value=Math.sqrt(PI/a)*Math.exp(-omega*omega/(4*a));return[value*Math.cos(omega*tau),-value*Math.sin(omega*tau)];}
 function squareWaveSeries(t,terms){var sum=0;for(var n=0;n<terms;n++)sum+=Math.sin((2*n+1)*t)/(2*n+1);return 4/PI*sum;}
 function fresnel(x,k,n){k=k||1;return[simpson(function(t){return Math.cos(k*t*t);},0,x,n||600),simpson(function(t){return Math.sin(k*t*t);},0,x,n||600)];}
 function rcState(t,R,C,E){var rate=Math.exp(-t/(R*C));return{current:E/R*rate,vr:E*rate,vc:E*(1-rate)};}
 function zTransform(r,z,n){var ratio=div([r,0],z),term=[1,0],sum=[1,0];for(var j=1;j<n;j++){term=mul(term,ratio);sum=add(sum,term);}return{partial:sum,reference:Math.hypot(z[0]-r,z[1])<1e-12?null:div(z,[z[0]-r,z[1]]),converges:Math.hypot(z[0],z[1])>Math.abs(r)};}
 function inverseOscillatory(a,b,t){function sinc(w){return Math.abs(w)<1e-8?t:Math.sin(w*t)/w;}
  if(Math.abs(a*a-b*b)<1e-8){var q=(a+b)/2;return Math.abs(q)<1e-5?t*t*t/6:(Math.sin(q*t)-q*t*Math.cos(q*t))/(2*q*q*q);}
  return(sinc(a)-sinc(b))/(b*b-a*a);
 }
 function bromwich(a,sigma,t,cutoff){var q=sigma+a;return Math.exp(sigma*t)/PI*simpson(function(w){return(q*Math.cos(w*t)+w*Math.sin(w*t))/(q*q+w*w);},0,cutoff,800);}
 function divisionIntegral(initial,a,s,epsilon){var rate=a+s,upper=Math.max(epsilon*2,45/rate);return initial*simpson(function(u){return Math.exp(-rate*Math.exp(u));},Math.log(epsilon),Math.log(upper),600)+Math.exp(-rate*epsilon)/rate;}
 function classify(text){text=String(text||'').replace(/\\beta\b/g,'β').replace(/\\gamma\b/g,'γ').replace(/\\/g,'');
  if(/弗雷[涅奈]|Fresnel/i.test(text))return'fresnel';
  if(/J_(?:\{)?-n|J[−-]n/.test(text))return'bessel';
  if(/F\(p\).*0.*方向|收敛半平面|拉[氏普].*复频率/.test(text))return'roc';
  if(/三角.*双曲.*(?:混用|分母)|cos.*ch.*(?:拉|变换)/.test(text))return'trighyper';
  if(/回路电压|R[−-]?C.*电路|电容电压|充电电路/.test(text))return'rc';
  if(/求导公式.*负号/.test(text))return'laplace-image-derivative';
  if(/f\(t\)\s*\/\s*t.*(?:有界|不能省)|除以.*t.*有界/.test(text))return'division';
  if(/余切形式|周期因子|coth.*(?:[ps]T|[ps]\s*\*\s*T)|(?:[ps]T|[ps]\s*\*\s*T).*coth/.test(text))return'coth';
  if(/重因式|重零点.*项数|部分分式分解式/.test(text))return'repeated-pole';
  if(/Z\s*变换|Z-transform/i.test(text))return'z-transform';
  if(/例\s*15.*(?:条件|结果)|sin.*a.*sin.*b.*适用/.test(text))return'inverse-oscillatory';
  if(/换元时.*(?:丢掉|遗漏).*i/.test(text))return'bromwich';
  if(/(?:Beta|贝塔|贝他)\s*(?:函数|function)|B\s*函数.*Γ|β\s*函数/i.test(text))return'beta';
  if(/Gamma\s*(?:函数|function|\()|伽[玛马]\s*函数|欧拉.*第二类/i.test(text)||/Γ\s*(?:\(|函数)/.test(text))return'gamma';
  if(/Bessel|贝[塞赛][尔耳]/i.test(text))return'bessel';
  if(/卷积|convolution/i.test(text))return'convolution';
  if(/冲激|冲击函数|狄拉克|Dirac|δ函数|delta函数/i.test(text))return'impulse';
  if(/阶跃|Heaviside|单位跳跃/i.test(text))return'step';
  if(/Fourier|傅[里立]叶|傅氏/i.test(text))return /级数|周期|吉布斯|Gibbs/i.test(text)?'fourier-series':/反变换|逆变换/.test(text)?'fourier-inverse':'fourier';
  var lap=/拉[普氏]|Laplace|象函数|像函数|原函数.*变换|相似性质|相似定理|相似变换|换时间尺|延迟定理|延迟函数|位移定理|时移|频移|s域|s平面|本函数|像函数|逆变换|初值项|除以\s*[$ ]*t/i.test(text);
  if(lap){if(/周期/i.test(text))return'laplace-periodic';if(/线性|叠加/i.test(text))return'laplace-linear';
   if(/相似|尺度|缩放|换时间尺/i.test(text))return'laplace-scale';if(/延迟|时移|迟延|滞后/i.test(text))return'laplace-delay';
   if(/位移|频移|指数移位|平移/i.test(text))return'laplace-shift';if(/(?:像|象)函数.*(?:微分|导数)|求导公式.*负号/.test(text))return'laplace-image-derivative';if(/(?:像|象)函数.*积分|除以\s*[$ ]*t/.test(text))return'laplace-image-integral';if(/微分|导数|初值项/i.test(text))return'laplace-derivative';
   if(/积分/i.test(text))return'laplace-integral';return'laplace-base';}
  return null;
 }
 function chooseScene(title,body){var unitName=/^\s*单位函数\s*$/.test(String(title||''));
  var causal=/(?:阶跃|Heaviside|[huH]\s*\(t\)|t\s*(?:<|≤|>|≥|\\(?:leq?|geq?))\s*0)/i.test(String(body||''));
  if(unitName&&!causal)return null;
  var found=unitName?'step':classify(title),kind=found||classify(String(body||'').slice(0,1300));if(!kind)return null;
  var a=ctl('a','衰减参数 a',.2,2,.05,.7),s=ctl('s','实数 s（>0）',.1,2.5,.05,1),data;
  var configs={
   fresnel:['C(x)=∫₀ˣ cos(kt²)dt，S(x)=∫₀ˣ sin(kt²)dt；C(∞)=S(∞)=√(π/(8k))','这里采用 kt² 的相位约定，不混用 πt²/2 的另一种归一化。左侧是真实振荡被积函数，右侧是积分累积形成的 Cornu 螺线；有限截断值并不等于无穷积分。',[ctl('upper','截断上限 x',.2,8,.1,3),ctl('frequency','相位系数 k',.4,2,.1,1)]],
   roc:['f(t)=eᶜᵗu(t)；F(p)=1/(p−c)，Re p=σ>c；|F(p)|≤1/(σ−c)','以指数增长函数演示收敛半平面与估计的方向。左图只给 σ>c 的区域画网线；右图为真实像值。固定 σ、仅增大 Im p 时，这个上界并不减小；不能只根据该估计把 Re p→∞ 换成任意 |p|→∞。',[ctl('c','增长率 c',0,1.2,.05,.4),ctl('sigma','Re p=σ',-.5,3,.05,1),ctl('omega','Im p=ω',-5,5,.1,1)]],
   trighyper:['L{cos(ωt)}=s/(s²+ω²)；L{cosh(ωt)}=s/(s²−ω²)','两种函数的指数分别是 ±iωt 与 ±ωt，分母符号不同。s 取实数；cos 的变换在 s>0 收敛，cosh 须 s>ω。纵轴用 asinh 压缩，不把指数增长画成有界余弦。',[ctl('omega','角频率/增长率 ω',.3,2,.05,1),ctl('s','实数 s',.2,3,.05,1.5)]],
   rc:['R i(t)+uC(t)=E；i(t)=(E/R)exp(−t/(RC))，uC(t)=E(1−exp(−t/(RC)))','代表电容初始无电荷的串联 R-C 充电电路。电容电压来自电流累计积分；金线 uC 增大、绿线 uR 减小，其和始终为 E。蓝线是电流的衰减。',[ctl('R','电阻 R',.3,2,.1,1),ctl('C','电容 C',.3,2,.1,1),ctl('E','电源电压 E',.5,3,.1,1.5),ctl('time','观察时间 t',0,6,.1,2)]],
   division:['f(t)=(b+t)e⁻ᵃᵗ；f(t)/t=(b/t+1)e⁻ᵃᵗ','改变初值 b。b=0 时 f(t)/t 在零点附近有界；b>0 时出现 b/t，积分按对数发散。图中只积分 t≥ε，减小截断 ε 看积分持续增大，不能把有限截断当成真正收敛。',[ctl('initial','初值 b=f(0)',0,1.5,.1,.5),ctl('epsilon','零点截断 ε',.0001,.5,.0001,.05),ctl('a','衰减 a',.2,2,.1,1),ctl('s','实数 s',.2,2,.1,1)]],
   coth:['(1+e⁻ˢᵀ)/(1−e⁻ˢᵀ)=coth(sT/2)','用实数 s,T>0 演示双曲余切的代数改写：令 x=sT/2，分子分母同时乘 eˣ 即得 coth x。两条曲线重合；x=0 的分母为零，不连过极点。',[ctl('s','实数 s',.1,3,.1,1),ctl('period','周期 T',.5,3,.1,1.5)]],
   'repeated-pole':['F(s)=1/(s−a)+B/(s−a)ᵐ；f(t)=eᵃᵗ[1+B tᵐ⁻¹/(m−1)!]','用含重极点的真分式展示高次分母项的独立贡献。绿色为完整反变换，金色是遗漏高次项的错误结果；一般 m 重根应预留从 1 到 m 的全部幂次，具体系数可以为零。纵轴使用 asinh 压缩。',[ctl('order','极点重数 m',2,4,1,2),ctl('a','极点位置 a',-.5,.5,.1,.2),ctl('B','高次项系数 B',.2,3,.1,1),ctl('s','观察像变量 s',.8,2.5,.1,1.5)]],
   'z-transform':['xₙ=rⁿu[n]；X(z)=Σₙ₌₀∞(r/z)ⁿ=z/(z−r)，|z|>|r|','用双边 Z 变换中的因果序列子例演示收敛域。左侧为采样序列，右侧为 z 平面的收敛圆边界与标记点。圈外级数收敛；圈内代数式仍可能有值，但不能当作该级数之和。',[ctl('r','序列参数 r',.1,1.5,.1,.7),ctl('radius','|z|',.2,2.5,.1,1.5),ctl('angle','Arg z',-3.14,3.14,.1,.4),ctl('terms','级数项数',1,30,1,12)]],
   'inverse-oscillatory':['L⁻¹{1/[(s²+a²)(s²+b²)]}=[sin(at)/a−sin(bt)/b]/(b²−a²)','原写法要求 a,b 非零且 a²≠b²。控制器可以跨过这些参数，但在重合或零频率处改用极限值，并明确标出原式不能直接代入；a=b=0 时极限为 t³/6。',[ctl('a','频率 a',0,2,.05,1),ctl('b','频率 b',0,2,.05,1.5),ctl('time','观察时间 t',0,6,.1,2)]],
   bromwich:['p=σ+iω，dp=i dω；f(t)=(1/2πi)∫ F(p)eᵖᵗdp','代表函数 F(p)=1/(p+a)，取竖线 σ>−a 与 t>0。左侧竖直切向量显示 dp 的 i 因子，右侧画出截断的实部积分核。读数由 |ω|≤Ω 的数值积分产生；有限截断有误差。',[ctl('a','参数 a',.2,1.5,.1,1),ctl('sigma','竖线 Re p=σ',.2,1.5,.1,.8),ctl('time','时间 t',.2,3,.1,1),ctl('cutoff','频率截断 Ω',5,80,1,30)]],
   beta:['B(a,b)=∫₀¹ tᵃ⁻¹(1−t)ᵇ⁻¹dt=Γ(a)Γ(b)/Γ(a+b)', '用 a,b>0 的实数切片演示 Beta 与 Gamma 的关系。两端可能有可积奇性，曲线不包含端点；纵轴 asinh 压缩，读数由 Gamma 恒等式计算。',[ctl('a','参数 a',.2,4,.05,1),ctl('b','参数 b',.2,4,.05,2)]],
   'fourier-inverse':['f(t)=(1/2π)∫ℝF(ω)eⁱωᵗdω；F(ω)=√(π/a) exp(−ω²/(4a))','以高斯函数演示傅氏反变换，金色为原函数，绿色为只积分 |ω|≤Ω 的重建。核符号和 1/(2π) 与正变换配对；增大截断频率观察重建误差。',[ctl('cutoff','截断频率 Ω',1,8,.1,3),ctl('a','高斯参数 a',.3,3,.05,1),ctl('cursor','观察 t',0,3,.05,.5)]],
   gamma:['Γ(z+1)=zΓ(z)；Γ(1/2)=√π；极点 z=0,−1,−2,…','用复数 Lanczos 近似和反射公式计算 Γ。曲线为水平切片 z=x+iy 的实部与虚部；纵坐标使用 asinh 压缩显示无界增长，读数保留原数值。极点处断开，不跨极点连线。',[ctl('real','标记点 Re z',-3.5,4.5,.05,1.4),ctl('imag','切片 Im z',-2,2,.05,.5)]],
   bessel:['Jₙ(x)=Σₖ (−1)ᵏ(x/2)²ᵏ⁺ⁿ/[k!(n+k)!]','展示整数阶第一类贝塞尔 Jₙ 的真实幂级数。绿色为可调部分和，金色为 40 项参考值；它们在小 x 处先重合。此模型不把 Yₙ 或修正贝塞尔函数当作 Jₙ。',[ctl('terms','级数项数',1,32,1,8),ctl('order','整数阶 n',-5,5,1,0),ctl('cursor','观察 x',0,12,.05,3)]],
   convolution:['(f*g)(t)=∫₀ᵗ e⁻ᵃτ e⁻ᵇ⁽ᵗ⁻τ⁾ dτ','代表函数 f(t)=e⁻ᵃᵗu(t)、g(t)=e⁻ᵇᵗu(t)。左侧蓝线 f(τ)、金线 g(t−τ) 随 t 滑动，绿色为重叠区乘积；右侧为该乘积的积分。积分区间是 0≤τ≤t，t<0 时为零。',[ctl('time','滑动时间 t',0,6,.05,2),a,ctl('b','第二衰减参数 b',.2,2,.05,1.3)]],
   fourier:['F(ω)=∫ℝ e⁻ᵃ⁽ᵗ⁻τ⁾²e⁻ⁱωᵗdt=√(π/a) exp(−ω²/(4a)) exp(−iωτ)','采用角频率和核 e⁻ⁱωᵗ，未在正变换中放 1/(2π)。左侧是真实平移高斯，右侧为频谱实部与虚部；平移改变相位而不改变幅度。',[ctl('a','高斯宽度参数 a',.3,3,.05,1),ctl('tau','时间平移 τ',-2,2,.05,.5),ctl('omega','标记角频率 ω',0,4,.05,1)]],
   'fourier-series':['Sₙ(t)=(4/π)Σₖ₌₀ⁿ⁻¹ sin((2k+1)t)/(2k+1)','代表 2π 周期方波。增大项数观察跳跃附近的 Gibbs（吉布斯）振荡；跳点的级数值为左右极限平均值 0，不把跳点定义成连续值。',[ctl('terms','奇谐波项数',1,40,1,5),ctl('cursor','观察位置 t',-3.14,3.14,.01,.2)]],
   step:['u(t−τ)=0 (t<τ)，1 (t>τ)；L{u(t−τ)}=e⁻ˢτ/s','代表单边拉普拉斯变换的因果单位阶跃。图中跳点用空心间隔表示，u(0) 的具体约定不改变通常积分；改变延迟会平移跳点。',[ctl('tau','延迟 τ',0,3,.05,1),ctl('s','实数 s',.1,2.5,.05,1)]],
   impulse:['δ(t−τ)≈1/ε（τ≤t≤τ+ε）；面积恒为 1','δ 不是普通函数，图中是宽 ε、高 1/ε 的单位面积矩形近似。缩小 ε 观察集中效应；变换趋于 e⁻ˢτ。图形按固定高度比例显示，读数给出实际峰高和面积。',[ctl('epsilon','矩形宽度 ε',.15,1.2,.05,.5),ctl('tau','冲激位置 τ',0,3,.05,1),ctl('s','实数 s',.1,2.5,.05,1)]]
  };
  if(kind.indexOf('laplace-')===0){var mode=kind.slice(8),formulas={base:'L{e⁻ᵃᵗ}=1/(s+a)',linear:'L{Af+Bg}=AF+BG',scale:'L{f(kt)}=F(s/k)/k=1/(s+ak)，k>0',derivative:'L{f′}=sF(s)−f(0⁺)=−a/(s+a)','image-derivative':'L{t f(t)}=−F′(s)=1/(s+a)²','image-integral':'f(t)=t e⁻ᵃᵗ；L{f(t)/t}=∫ₛ∞F(q)dq=1/(s+a)',integral:'L{∫₀ᵗ f(τ)dτ}=F(s)/s=1/[s(s+a)]',delay:'L{u(t−τ)f(t−τ)}=e⁻ˢτF(s)',shift:'L{eᶜᵗf(t)}=F(s−c)=1/(s+a−c)',periodic:'Fₚ(s)=[∫₀ᵀ e⁻ˢᵗe⁻ᵃᵗdt]/(1−e⁻ˢᵀ)'};
   var explanations={base:'比较指数衰减和拉普拉斯权重。',linear:'用两条不同衰减曲线显示线性叠加，系数可以为负。',scale:'压缩时间会同时改变横轴尺度和积分中的 1/k 因子；这里 k>0。',derivative:'f(0⁺)=1，因此不能漏掉初值项。绿色是 f′，金色是加权被积函数。','image-derivative':'乘 t 对应像函数求导并加负号，绿色为 t e⁻ᵃᵗ，F(s)=1/(s+a)。','image-integral':'本图特取 f(t)=t e⁻ᵃᵗ，使 f(t)/t 在零点附近有界；绿色为 f(t)/t=e⁻ᵃᵗ。不能对 f(0)≠0 的指数函数直接除 t。',integral:'绿色是从 0 开始的累计积分，起点为零；s>0 保证外层变换收敛。',delay:'延迟后的函数必须乘 u(t−τ)，τ 前严格为零；金线为其加权积分核。',shift:'乘 eᶜᵗ 对应像变量 s→s−c，须重新检查 s+a−c>0，不能沿用旧收敛域。',periodic:'每周期重置为 e⁻ᵃᵗ 的一段（0≤t<T），几何级数因子 1/(1−e⁻ˢᵀ) 汇总全部周期。'};
   var controls=[a,s];if(mode==='linear')controls.push(ctl('b','第二衰减 b',.2,2,.05,1.3),ctl('A','系数 A',-2,2,.1,1),ctl('B','系数 B',-2,2,.1,.5));
   if(mode==='scale')controls.push(ctl('k','时间缩放 k',.4,2.5,.05,1.5));if(mode==='delay')controls.push(ctl('tau','延迟 τ',0,3,.05,1));if(mode==='shift')controls.push(ctl('c','指数位移 c',-.6,1.8,.05,.4));if(mode==='periodic')controls.push(ctl('period','周期 T',.5,3,.05,1.5));
   data=[formulas[mode], (mode==='image-integral'?'代表函数 f(t)=t e⁻ᵃᵗu(t)，':'代表函数 f(t)=e⁻ᵃᵗu(t)，')+'s 取实数；'+explanations[mode]+' 图中绿色为变换前函数，金色为 e⁻ˢᵗ 加权函数；读数使用解析变换值。',controls];
  }else data=configs[kind];
  var scene={kind:'special-'+kind,title:String(title||'特殊函数'),formula:data[0],explanation:data[1],controls:data[2],illustrative:true,label:'代表函数 · '+({'fresnel':'Fresnel 振荡积分','roc':'收敛半平面与方向','trighyper':'三角/双曲变换','rc':'R-C 充电电路','division':'f(t)/t 的零点条件','coth':'双曲余切恒等式','repeated-pole':'重极点的反变换','z-transform':'因果序列 Z 变换','inverse-oscillatory':'重合频率的极限','bromwich':'竖线反演积分','beta':'Beta/Gamma 关系','fourier-inverse':'傅氏数值反变换','gamma':'复数 Gamma','bessel':'整数阶 Jₙ','convolution':'因果卷积','fourier':'高斯 Fourier','fourier-series':'方波 Fourier','step':'单位阶跃','impulse':'冲激的单位面积近似'}[kind]||'Laplace 性质'),matchSource:found?'title':'body'};
  if(kind==='step'&&!/延迟|时移/.test(String(title||'')))scene.controls.find(function(c){return c.key==='tau';}).value=0;
  if(kind==='bessel'&&/J_(?:\{)?-n|J[−-]n/.test(String(title||''))){scene.controls.find(function(c){return c.key==='order';}).value=-1;scene.formula+='；J₋ₙ(x)=(−1)ⁿJₙ(x)';}
  if(kind==='gamma')scene.map=function(x,y){return gammaComplex([x,y])||[NaN,NaN];};
  return scene;
 }
 function fmt(n){return Number.isFinite(n)?String(Math.round(n*1e5)/1e5):'无定义';}
 function geometry(model){var kind=model.scene.kind;if(kind.indexOf('special-')!==0)return null;kind=kind.slice(8);var p=model.params,out={lines:[],points:[],labels:[],readout:'',facts:{}};
  function line(points,color,opacity){if(points.length>1)out.lines.push({points:points,color:color||TEAL,opacity:opacity==null?1:opacity});}
  function sample(f,a,b,n,color){var points=[];for(var i=0;i<=(n||150);i++){var t=a+(b-a)*i/(n||150),q=f(t);if(!q||!q.every(Number.isFinite)){line(points,color);points=[];}else points.push(q);}line(points,color);}
  function dot(x,y,color){if(Number.isFinite(x)&&Number.isFinite(y))out.points.push({position:[x,y,.04],color:color||GOLD});}
  function axis(x0,x1,y0,y1){line([[x0,y0,0],[x1,y0,0]],GRAY,.7);line([[x0,y0,0],[x0,y1,0]],GRAY,.7);}
  function timePlot(fn,color){sample(function(t){return[-2.6+t*.8,-.8+fn(t),0];},0,6,240,color);}
  if(kind==='fresnel'){
   axis(-3.4,-.3,-1.1,1.3);axis(.3,3.5,-1.1,1.6);
   [0,1].forEach(function(part){sample(function(t){return[-3.4+3.1*t/p.upper,(part?Math.sin(p.frequency*t*t):Math.cos(p.frequency*t*t))*.8,0];},0,p.upper,300,part?PINK:BLUE);});
   sample(function(t){var q=fresnel(t,p.frequency,180);return[.5+q[0]*2.8,-.9+q[1]*2.8,0];},0,p.upper,180,TEAL);var value=fresnel(p.upper,p.frequency),limit=Math.sqrt(PI/(8*p.frequency));dot(.5+value[0]*2.8,-.9+value[1]*2.8);
   out.facts={C:value[0],S:value[1],limit:limit};out.readout='C('+fmt(p.upper)+')='+fmt(value[0])+'；S='+fmt(value[1])+'；无穷上限的共同极限 '+fmt(limit);out.labels=[['t轴：蓝cos(kt²) / 粉sin(kt²)',.24],['(C(x),S(x))：累积积分螺线',.76]];
  }else if(kind==='roc'){
   axis(-3.6,-.3,-1.5,1.5);axis(.4,3.5,-1.5,1.5);var border=-3.2+p.c*.75;line([[border,-1.4,0],[border,1.4,0]],GOLD);
   for(var sigma=p.c+.12;sigma<=3;sigma+=.16)line([[-3.2+sigma*.75,-1.35,0],[-3.2+sigma*.75,1.35,0]],TEAL,.3);
   dot(-3.2+p.sigma*.75,p.omega*.27,p.sigma>p.c?TEAL:PINK);
   var valid=p.sigma>p.c,value=valid?div([1,0],[p.sigma-p.c,p.omega]):null,bound=valid?1/(p.sigma-p.c):null;
   if(value)dot(1.8+Math.asinh(value[0])*.55,Math.asinh(value[1])*.55,GOLD);out.facts={converges:valid,value:value,bound:bound};out.readout=valid?'F(p)='+fmt(value[0])+' + '+fmt(value[1])+'i；|F|='+fmt(Math.hypot(value[0],value[1]))+' ≤ 1/(σ−c)='+fmt(bound)+'。上界仅依赖实部 σ。':'σ≤c：此拉氏积分不收敛，不能把代数延拓值当作积分。';out.labels=[['p平面：金边界 σ=c，绿色右半平面',.24],['F平面：坐标按 asinh 压缩',.76]];
  }else if(kind==='trighyper'){
   axis(-2.8,3,-1.1,2);sample(function(t){return[-2.8+t*1.4,Math.asinh(Math.cos(p.omega*t)),0];},0,4,240,BLUE);sample(function(t){return[-2.8+t*1.4,Math.asinh(Math.cosh(p.omega*t)),0];},0,4,240,TEAL);
   sample(function(t){return[-2.8+t*1.4,Math.asinh(Math.exp(-p.s*t)*Math.cos(p.omega*t)),.02];},0,4,240,PINK);sample(function(t){return[-2.8+t*1.4,Math.asinh(Math.exp(-p.s*t)*Math.cosh(p.omega*t)),.02];},0,4,240,GOLD);
   var cosValue=p.s/(p.s*p.s+p.omega*p.omega),coshValue=p.s>p.omega?p.s/(p.s*p.s-p.omega*p.omega):null;out.facts={cosTransform:cosValue,coshTransform:coshValue};out.readout='L{cos}='+fmt(cosValue)+'；'+(coshValue===null?'s≤ω，cosh 的拉氏积分发散。':'L{cosh}='+fmt(coshValue)+'（s>ω）');out.labels=[['横轴t；蓝cos / 绿cosh / 粉加权cos / 金加权cosh；纵轴asinh',.5]];
  }else if(kind==='rc'){
   line([[-3.4,-1,0],[-3.4,1.1,0],[-.5,1.1,0],[-.5,-1,0],[-3.4,-1,0]],GRAY);line([[-2.6,1.1,0],[-2.4,1.3,0],[-2.2,.9,0],[-2,1.3,0],[-1.8,.9,0],[-1.6,1.1,0]],TEAL);line([[-.8,.2,0],[-.2,.2,0]],GOLD);line([[-.8,0,0],[-.2,0,0]],GOLD);
   axis(.2,3.4,-1.1,1.7);[0,1,2].forEach(function(part){sample(function(t){var q=rcState(t,p.R,p.C,p.E),y=part===0?q.vr:part===1?q.vc:q.current;return[.2+t*.5,-1.1+Math.asinh(y)*.9,0];},0,6,160,part===0?TEAL:part===1?GOLD:BLUE);});
   var q=rcState(p.time,p.R,p.C,p.E);dot(.2+p.time*.5,-1.1+Math.asinh(q.vc)*.9,GOLD);out.facts=q;out.readout='i='+fmt(q.current)+'；uR='+fmt(q.vr)+'，uC='+fmt(q.vc)+'；uR+uC='+fmt(q.vr+q.vc)+'=E。时间常数 RC='+fmt(p.R*p.C);out.labels=[['R-C串联示意（电容初始未带电）',.23],['t轴；蓝i / 绿uR / 金uC；纵轴asinh',.76]];
  }else if(kind==='division'){
   axis(-3.3,-.3,-.8,2);axis(.3,3.5,-.8,2);sample(function(t){return[-3.3+t*.75,-.8+Math.asinh((p.initial/t+1)*Math.exp(-p.a*t))*.6,0];},p.epsilon,4,240,TEAL);
   sample(function(u){var eps=Math.exp(-u),value=divisionIntegral(p.initial,p.a,p.s,eps);return[.3+(u-Math.log(2))*.35,-.8+Math.asinh(value)*.7,0];},Math.log(2),Math.log(10000),90,GOLD);
   var value=divisionIntegral(p.initial,p.a,p.s,p.epsilon);dot(.3+(-Math.log(p.epsilon)-Math.log(2))*.35,-.8+Math.asinh(value)*.7,GOLD);out.facts={integral:value,bounded:p.initial===0,initial:p.initial};out.readout='截断积分 ∫ε∞ e⁻ˢᵗ f(t)/t dt='+fmt(value)+'；'+(p.initial===0?'b=0，ε→0 时趋于 '+fmt(1/(p.s+p.a)):'b>0，ε→0 时对数发散。');out.labels=[['t轴：f(t)/t，纵轴asinh',.24],['横轴−log ε；金色截断积分，纵轴asinh',.76]];
  }else if(kind==='coth'){
   axis(-2.8,3,-.8,2);sample(function(x){return[-2.8+x*1.15,-.8+Math.asinh(Math.cosh(x)/Math.sinh(x)),0];},.1,5,200,TEAL);sample(function(x){return[-2.8+x*1.15,-.8+Math.asinh((1+Math.exp(-2*x))/(1-Math.exp(-2*x))),.02];},.1,5,200,GOLD);
   var x=p.s*p.period/2,coth=Math.cosh(x)/Math.sinh(x),ratio=(1+Math.exp(-p.s*p.period))/(1-Math.exp(-p.s*p.period));dot(-2.8+x*1.15,-.8+Math.asinh(coth));out.facts={coth:coth,exponentialRatio:ratio};out.readout='x=sT/2='+fmt(x)+'；coth(x)='+fmt(coth)+'；指数比='+fmt(ratio)+'；两式差='+fmt(coth-ratio);out.labels=[['横轴x；两式同值；纵轴asinh',.5]];
  }else if(kind==='repeated-pole'){
   var fac=1;for(var i=2;i<p.order;i++)fac*=i;axis(-2.8,3,-.8,2);sample(function(t){return[-2.8+t*.9,-.8+Math.asinh(Math.exp(p.a*t)*(1+p.B*Math.pow(t,p.order-1)/fac))*.5,0];},0,6,200,TEAL);sample(function(t){return[-2.8+t*.9,-.8+Math.asinh(Math.exp(p.a*t))*.5,.02];},0,6,200,GOLD);
   var exact=1/(p.s-p.a)+p.B/Math.pow(p.s-p.a,p.order),wrong=1/(p.s-p.a);out.facts={transform:exact,missingTerm:p.B/Math.pow(p.s-p.a,p.order),incomplete:wrong};out.readout='s='+fmt(p.s)+' 时完整 F='+fmt(exact)+'；漏高次项仅得 '+fmt(wrong)+'；差值='+fmt(exact-wrong);out.labels=[['t轴：绿 完整本函数 / 金 漏高次项；纵轴asinh×0.5',.5]];
  }else if(kind==='z-transform'){
   axis(-3.4,-.1,-.8,2);axis(.2,3.6,-1.4,1.5);for(var i=0;i<=12;i++){var x=-3.4+i*.25,y=-.8+Math.asinh(Math.pow(p.r,i))*.55;line([[x,-.8,0],[x,y,0]],BLUE);dot(x,y,BLUE);}
   sample(function(t){return[1.8+.65*p.r*Math.cos(t),.65*p.r*Math.sin(t),0];},0,2*PI,120,GOLD);var z=[p.radius*Math.cos(p.angle),p.radius*Math.sin(p.angle)];dot(1.8+.65*z[0],.65*z[1],TEAL);var value=zTransform(p.r,z,p.terms),error=value.reference?Math.hypot(value.partial[0]-value.reference[0],value.partial[1]-value.reference[1]):NaN;if(Number.isFinite(error))line([[.3,-1.6,0],[.3+Math.min(3,Math.log1p(error)),-1.6,0]],PINK);
   out.facts=value;out.readout=p.terms+'项部分和='+fmt(value.partial[0])+' + '+fmt(value.partial[1])+'i；'+(value.converges?'|z|>|r|，真实 Z='+fmt(value.reference[0])+' + '+fmt(value.reference[1])+'i':'|z|≤|r|：级数不收敛，不能拿代数式当级数值。');out.labels=[['n轴：采样序列 rⁿ（纵轴asinh）',.24],['z平面：金收敛边界；下方粉条=log(1+部分和误差)',.76]];
  }else if(kind==='inverse-oscillatory'){
   axis(-2.8,3,-.8,2);sample(function(t){return[-2.8+t*.9,-.8+Math.asinh(inverseOscillatory(p.a,p.b,t)),0];},0,6,200,TEAL);var value=inverseOscillatory(p.a,p.b,p.time),valid=p.a!==0&&p.b!==0&&Math.abs(p.a*p.a-p.b*p.b)>1e-8;dot(-2.8+p.time*.9,-.8+Math.asinh(value));out.facts={value:value,writtenFormulaDefined:valid};out.readout=(valid?'原式条件满足。':'原写法含0/0，不能直接代入；这里显示连续极限。')+' f('+fmt(p.time)+')='+fmt(value);out.labels=[['t轴：逆变换本函数（含退化参数的极限）；纵轴asinh',.5]];
  }else if(kind==='bromwich'){
   axis(-3.2,-.3,-1.4,1.4);var x=-2.9+p.sigma;line([[x,-1.2,0],[x,1.2,0]],TEAL);line([[x,.3,0],[x,.8,0],[x-.1,.6,0]],GOLD);line([[x,.8,0],[x+.1,.6,0]],GOLD);
   axis(.2,3.5,-1.1,1.4);sample(function(w){var q=p.sigma+p.a,y=Math.exp(p.sigma*p.time)*(q*Math.cos(w*p.time)+w*Math.sin(w*p.time))/(q*q+w*w)/PI;return[.2+w/p.cutoff*3.1,-.5+Math.asinh(y)*.7,0];},0,p.cutoff,320,BLUE);
   var value=bromwich(p.a,p.sigma,p.time,p.cutoff),exact=Math.exp(-p.a*p.time);out.facts={reconstructed:value,exact:exact,wrongImaginary:-value};out.readout='Ω='+p.cutoff+' 的截断重建='+fmt(value)+'；真实 e⁻ᵃᵗ='+fmt(exact)+'；dp=i dω；漏掉i会得到 '+fmt(-value)+'i。';out.labels=[['p平面竖线，金箭头为 i 方向',.23],['ω轴：实部积分核（纵轴asinh）；有限截断有误差',.76]];
  }else if(kind==='beta'){
   axis(-2.8,2.8,-.8,2.2);sample(function(t){return[-2.8+t*5.6,-.8+Math.asinh(Math.pow(t,p.a-1)*Math.pow(1-t,p.b-1))*.7,0];},.001,.999,240,TEAL);var value=beta(p.a,p.b);out.facts={value:value};out.readout='B('+fmt(p.a)+','+fmt(p.b)+')='+fmt(value)+' = Γ(a)Γ(b)/Γ(a+b)；a,b>0。';out.labels=[['t∈(0,1)；纵轴 asinh(被积函数)×0.7',.5]];
  }else if(kind==='fourier-inverse'){
   function recover(t){return simpson(function(w){return Math.sqrt(PI/p.a)*Math.exp(-w*w/(4*p.a))*Math.cos(w*t)/PI;},0,p.cutoff,180);}
   axis(-3,3,-.8,1.8);sample(function(t){return[t,-.8+2*Math.exp(-p.a*t*t),0];},-3,3,120,GOLD);sample(function(t){return[t,-.8+2*recover(t),.02];},-3,3,120,TEAL);var value=recover(p.cursor),original=Math.exp(-p.a*p.cursor*p.cursor);dot(p.cursor,-.8+2*value);out.facts={reconstructed:value,original:original,error:Math.abs(value-original)};out.readout='Ω='+fmt(p.cutoff)+'；f('+fmt(p.cursor)+')='+fmt(original)+'；重建 '+fmt(value)+'；误差 '+fmt(Math.abs(value-original));out.labels=[['t轴：金 原高斯 / 绿 截断频率反变换',.5]];
  }else if(kind==='gamma'){
   axis(-3.2,3.6,0,2.2);[-3,-2,-1,0].forEach(function(x){line([[x*.8,-1.8,0],[x*.8,1.8,0]],GRAY,.35);});
   [0,1].forEach(function(part){sample(function(x){if(Math.abs(p.imag)<.015&&x<.02&&Math.abs(x-Math.round(x))<.035)return null;var value=gammaComplex([x,p.imag]);return value?[x*.8,Math.asinh(value[part])/1.7,0]:null;},-3.5,4.5,320,part?PINK:TEAL);});
   var g=gammaComplex([p.real,p.imag]);if(g){dot(p.real*.8,Math.asinh(g[0])/1.7,TEAL);dot(p.real*.8,Math.asinh(g[1])/1.7,PINK);var next=gammaComplex([p.real+1,p.imag]),zg=mul([p.real,p.imag],g);out.facts={value:g,recurrenceError:next?Math.hypot(next[0]-zg[0],next[1]-zg[1]):null};out.readout='Γ('+fmt(p.real)+' + '+fmt(p.imag)+'i) = '+fmt(g[0])+' + '+fmt(g[1])+'i；递推误差 '+fmt(out.facts.recurrenceError);}
   else{out.facts={pole:true};out.readout='此处为 Γ 的极点，函数无定义；不能把两侧曲线连过极点。';}out.labels=[['横轴 x；绿 ReΓ / 粉 ImΓ；纵轴 asinh 值 /1.7',.5]];
  }else if(kind==='bessel'){
   axis(-2.8,3,-1.4,1.7);sample(function(x){return[-2.8+x*.48,besselJ(p.order,x,40)*1.8,0];},0,12,240,GOLD);sample(function(x){return[-2.8+x*.48,besselJ(p.order,x,p.terms)*1.8,.02];},0,12,240,TEAL);
   if(p.order<0)sample(function(x){return[-2.8+x*.48,besselJ(-p.order,x,40)*1.8,.01];},0,12,240,BLUE);
   var actual=besselJ(p.order,p.cursor,40),partial=besselJ(p.order,p.cursor,p.terms);dot(-2.8+p.cursor*.48,partial*1.8);out.facts={partial:partial,reference:actual,error:Math.abs(partial-actual)};out.readout='J_'+p.order+'('+fmt(p.cursor)+') ≈ '+fmt(actual)+'；'+p.terms+' 项部分和 '+fmt(partial)+'；差 '+fmt(Math.abs(partial-actual));out.labels=[['横轴 x∈[0,12]；绿 部分和 / 金 40项参考',.5]];
  }else if(kind==='convolution'){
   axis(-3.2,.1,-.8,1.5);axis(.5,3.6,-.8,1.5);
   sample(function(tau){return[-3.2+tau*.54,-.8+Math.exp(-p.a*tau)*1.6,0];},0,6,200,BLUE);
   sample(function(tau){return[-3.2+tau*.54,-.8+(tau<=p.time?Math.exp(-p.b*(p.time-tau)):0)*1.6,.01];},0,6,240,GOLD);
   sample(function(tau){return[-3.2+tau*.54,-.8+(tau<=p.time?Math.exp(-p.a*tau-p.b*(p.time-tau)):0)*1.6,.02];},0,6,240,TEAL);
   sample(function(t){return[.5+t*.5,-.8+convolution(t,p.a,p.b)*2.2,0];},0,6,200,TEAL);
   var value=convolution(p.time,p.a,p.b),integral=simpson(function(tau){return Math.exp(-p.a*tau-p.b*(p.time-tau));},0,p.time,400);dot(.5+p.time*.5,-.8+value*2.2);out.facts={value:value,integral:integral,overlap:[0,p.time]};out.readout='卷积积分 (f*g)('+fmt(p.time)+') = '+fmt(value)+'；滑动乘积数值积分 '+fmt(integral);out.labels=[['τ轴：蓝f / 金g(t−τ) / 绿乘积',.24],['t轴：绿色为卷积',.76]];
  }else if(kind.indexOf('laplace-')===0){
   var mode=kind.slice(8),fn=function(t){if(mode==='linear')return p.A*Math.exp(-p.a*t)+p.B*Math.exp(-p.b*t);if(mode==='scale')return Math.exp(-p.a*p.k*t);if(mode==='derivative')return-p.a*Math.exp(-p.a*t);if(mode==='image-derivative')return t*Math.exp(-p.a*t);if(mode==='image-integral')return Math.exp(-p.a*t);if(mode==='integral')return-Math.expm1(-p.a*t)/p.a;if(mode==='delay')return t<p.tau?0:Math.exp(-p.a*(t-p.tau));if(mode==='shift')return Math.exp((p.c-p.a)*t);if(mode==='periodic')return Math.exp(-p.a*(t%p.period));return Math.exp(-p.a*t);};
   axis(-2.6,2.5,-.8,2);timePlot(function(t){return Math.asinh(fn(t));},TEAL);timePlot(function(t){return Math.asinh(Math.exp(-p.s*t)*fn(t));},GOLD);var transform=laplaceValue(mode,p);
   out.facts={transform:transform,initialValue:mode==='derivative'?1:fn(0),converges:transform!==null};out.readout=transform===null?'s+a−c≤0：拉普拉斯积分不收敛，不能使用该像函数值。':'解析变换值 = '+fmt(transform)+'；s='+fmt(p.s)+(mode==='derivative'?'；sF(s)−1 = '+fmt(p.s/(p.s+p.a)-1):mode==='scale'?'；(1/k)F(s/k) = '+fmt(1/p.k/(p.s/p.k+p.a)):mode==='delay'?'；延迟前 t<τ 为零。':'');out.labels=[['横轴 t∈[0,6]；纵轴 asinh(函数值)；绿原函数 / 金加权',.5]];
  }else if(kind==='fourier'){
   axis(-3.6,-.2,-.8,1.8);axis(.3,3.7,-.8,1.8);sample(function(t){return[-1.9+t*.45,-.8+Math.exp(-p.a*(t-p.tau)*(t-p.tau))*2,0];},-4,4,240,TEAL);
   [0,1].forEach(function(part){sample(function(w){return[.35+w*.75,-.8+fourierGaussian(w,p.a,p.tau)[part]*.8,0];},0,4,240,part?PINK:GOLD);});
   var value=fourierGaussian(p.omega,p.a,p.tau);dot(.35+p.omega*.75,-.8+value[0]*.8,GOLD);dot(.35+p.omega*.75,-.8+value[1]*.8,PINK);out.facts={transform:value,magnitude:Math.hypot(value[0],value[1])};out.readout='F('+fmt(p.omega)+') = '+fmt(value[0])+' + '+fmt(value[1])+'i；|F|='+fmt(out.facts.magnitude);out.labels=[['t轴：平移高斯',.24],['ω轴：金 ReF / 粉 ImF',.76]];
  }else if(kind==='fourier-series'){
   axis(-3.2,3.2,-1.6,1.8);line([[-PI,-1,0],[0,-1,0]],GOLD);line([[0,1,0],[PI,1,0]],GOLD);sample(function(t){return[t,squareWaveSeries(t,p.terms),.02];},-PI,PI,480,TEAL);var value=squareWaveSeries(p.cursor,p.terms);dot(p.cursor,value);out.facts={partial:value,jumpValue:0};out.readout=p.terms+'个奇谐波；Sₙ('+fmt(p.cursor)+')='+fmt(value)+'；跳点收敛到0，邻近仍有Gibbs振荡。';out.labels=[['t轴：金 周期方波 / 绿 部分和',.5]];
  }else if(kind==='step'){
   axis(-2.8,3,-.8,1.6);var x=-2.8+p.tau;line([[-2.8,-.8,0],[x-.025,-.8,0]],TEAL);line([[x+.025,.8,0],[3,.8,0]],TEAL);sample(function(t){return[-2.8+t,-.8+(t>p.tau?Math.exp(-p.s*t)*1.6:0),.02];},0,5.8,240,GOLD);var value=Math.exp(-p.s*p.tau)/p.s;out.facts={transform:value,jumpTime:p.tau};out.readout='延迟 τ='+fmt(p.tau)+'；L{u(t−τ)}='+fmt(value)+'（s>0）；跳点单点取值不影响积分。';out.labels=[['横轴 t；绿色 阶跃 / 金色 加权函数',.5]];
  }else if(kind==='impulse'){
   axis(-2.8,3,-.8,1.8);var height=1/p.epsilon,x0=-2.8+p.tau,x1=x0+p.epsilon;line([[-2.8,-.8,0],[x0,-.8,0],[x0,-.8+height*.3,0],[x1,-.8+height*.3,0],[x1,-.8,0],[3,-.8,0]],TEAL);
   var exact=Math.exp(-p.s*p.tau),approx=exact*(-Math.expm1(-p.s*p.epsilon))/(p.s*p.epsilon);line([[x0,-.8,0],[x1,-.8,0]],GOLD);out.facts={area:1,height:height,width:p.epsilon,transform:approx,deltaTransform:exact};out.readout='近似矩形宽 '+fmt(p.epsilon)+'、高 '+fmt(height)+'、面积=1；变换 '+fmt(approx)+' → e⁻ˢτ='+fmt(exact)+'。';out.labels=[['横轴 t；实际高度按 0.3 倍绘制；δ 本身不是有限高曲线',.5]];
  }return out;
 }
 return{chooseScene:chooseScene,geometry:geometry,fresnel:fresnel,rcState:rcState,zTransform:zTransform,inverseOscillatory:inverseOscillatory,bromwich:bromwich,beta:beta,gammaComplex:gammaComplex,besselJ:besselJ,convolution:convolution,laplaceValue:laplaceValue,fourierGaussian:fourierGaussian,squareWaveSeries:squareWaveSeries};
});
