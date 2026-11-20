/* Foundation/method scene provider. Every family states its representative
 * function and domain; source teaching text is never changed or inferred. */
(function(root,factory){
  'use strict';var api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else {(root.DSHStudySceneExtensions||(root.DSHStudySceneExtensions=[])).push(api);if(root.DSHStudyVisuals)root.DSHStudyVisuals.registerScenes(api);}
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  var PI=Math.PI,TAU=2*PI,BLUE='#78aaff',GREEN='#54e0c0',GOLD='#ffcd70',PINK='#f6a0ca',DIM='#536781';
  function c(key,label,min,max,step,value){return {key:key,label:label,min:min,max:max,step:step,value:value};}
  function square(z){return [z[0]*z[0]-z[1]*z[1],2*z[0]*z[1]];}
  function mul(a,b){return [a[0]*b[0]-a[1]*b[1],a[0]*b[1]+a[1]*b[0]];}
  function polar(r,t){return [r*Math.cos(t),r*Math.sin(t)];}
  function exp(z){return polar(Math.exp(z[0]),z[1]);}
  function fmt(x){return Number.isFinite(x)?String(Math.round(x*10000)/10000):'无定义';}
  function complex(z){return fmt(z[0])+' + ('+fmt(z[1])+')i';}
  function classify(t){
    if(/平面场中的(?:点|曲线)|均匀荷电曲线/.test(t))return 'extrusion';
    if(/高斯定理|Gauss.*(?:定理|flux)/i.test(t))return 'gauss';
    if(/格林公式|Green.*(?:公式|theorem)/i.test(t))return 'green';
    if(/叠加原理/.test(t))return 'superposition';
    if(/约去.*(?:z|共轭)|例3分母的化简|分母.*\(1.?x\)/.test(t))return 'cancel';
    if(/ε.?N|ϵ.?N|epsilon.?N/i.test(t))return 'sequence';
    if(/无界集/.test(t))return 'unbounded';
    if(/梯度.*(?:垂直|等值线)|等值线.*梯度/.test(t))return 'gradient';
    if(/关于圆周的对称点|圆周反演|直交圆|正交圆|同一条射线上|关于圆周.{0,15}对称/.test(t))return 'circle';
    if(/C\s*[-－–]?\s*R|柯西.{0,3}黎曼|Cauchy.{0,3}Riemann/i.test(t))return 'cr';
    if(/实部.{0,2}虚部|纯虚数|虚单位|虚数单位|虚轴/.test(t))return 'components';
    if(/反函数|反映照/.test(t))return 'inverse';
    if(/求导法则|复合函数.*导|链式法则/.test(t))return 'chain';
    if(/长大不等式|ML\s*(?:估计|不等式)|模不等式|ε\s*L/.test(t))return 'ml';
    if(/德.{0,2}莫弗|单位圆.*根|[nｎ]次(?:方)?根|算术根|整数次幂|开方|根的分布|α=1\/n|k 的几何意义/.test(t))return 'roots';
    if(/支割线|割平面|对数支|Ln 当成单值|多值函数的.“值”/.test(t))return 'branch';
    if(/泊松|Poisson|狄氏问题|Dirichlet/i.test(t))return 'poisson';
    if(/牛顿.{0,2}莱布尼[兹茨]|Newton.{0,3}Leibniz|复积分的(?:参数化|线性|反向路径|路径可加)|被积变量与积分上限/i.test(t))return 'primitive';
    if(/极限路径|路径.*极限|极限.*路径|函数的连续|连续函数|连续性|ε.?δ|极限不存在|极限的定义/.test(t))return 'limits';
    if(/平面向量场|稳定物理场|无源无旋|稳定平面流场|静电场|均匀流动|平面点电荷|流函数|等位线|流线|电力线|电位与电场|复势|势函数|速度位|环量|通量|流场与静电场/.test(t))return 'field';
    if(/恒等函数|identity|平移变换|旋转变换|相似变换|整线性变换|伸张系数/.test(t))return 'affine';
    if(/单值函数|单叶|一一映[照射]|双方单值|像只有一层|每个点都有 n 个原像/.test(t))return 'injective';
    return null;
  }
  function chooseScene(title,body){
    title=String(title||'');body=String(body||'').slice(0,900);
    var own=classify(title),k=own||classify(body);if(!k)return null;
    var p=[],formula='',explanation='',map=null,mode='';
    if(k==='extrusion'){
      mode=/中的点/.test(title)?'point':/荷电/.test(title)?'charged':'curve';
      p=mode==='point'?[c('real','截面点 x',-.9,.9,.05,.5),c('imag','截面点 y',-.9,.9,.05,.3),c('height','显示高度窗口 H',.5,2,.1,1.4)]:[c('radius','截面圆半径 R',.3,1.3,.05,.8),c('height','显示高度窗口 H',.5,2,.1,1.4)];if(mode==='charged')p.push(c('charge','归一化面密度 q',-1,1,.1,.6));
      formula=mode==='point'?'二维点 (x,y) ↔ 三维直线 {(x,y,h):h∈ℝ}':mode==='charged'?'圆形截面对应无限圆柱面；归一化场 |E|∝qR/r (r>R)':'截面曲线 C ↔ 柱面 C×ℝ；圆形代表 x²+y²=R²';
      explanation='这是平面场的截面解释：三维对象沿垂直坐标面的方向保持不变。图中只裁取 |h|≤H 的可见窗口，真实理想模型沿该方向无限延伸；调H改变显示范围。'+(mode==='charged'?'以均匀荷电圆柱面为代表，q改变圆柱外归一化电场的强度和方向；任意荷电曲线仍按其自身几何求场。':'');
    }else if(k==='gauss'){
      p=[c('coefficient','场系数 c',-.8,1.2,.1,.6),c('radius','积分圆半径 R',.3,1.5,.05,1)];formula='E=c(x,y)；div E=2c；∮E·n ds=∬div E dA=2πcR²';explanation='用径向线性场检验二维高斯公式：圆周法向通量，等于圆盘内部散度的面积积分。它是明确代表场的等式，改变c可同时观察源与汇。';
    }else if(k==='green'){
      p=[c('rotation','旋转系数 ω',-1,1,.1,.6),c('source','径向源强 s',-1,1,.1,.4),c('radius','闭路半径 R',.3,1.4,.05,1)];formula='E=(sx−ωy,sy+ωx)；∮overline(w) dz=Γ+iQ';explanation='逆时针圆周上，环量Γ=2πωR²，向外通量Q=2πsR²。w=U+iV 时必须取共轭再乘dz，实部给环量、虚部给通量；两种贡献在同一具体场中分别调节。';
    }else if(k==='superposition'){
      p=[c('first','第一点源 q₁',-1.2,1.2,.1,1),c('second','第二点源 q₂',-1.2,1.2,.1,-.7),c('distance','点源到原点距离 a',.3,1,.05,.6)];formula='E=E₁+E₂；Eⱼ=qⱼ(z−aⱼ)/|z−aⱼ|²（归一化电场）';explanation='两条沿垂直方向延伸的线电荷在平面中表示为两个点源。蓝、粉向量分别为两源对选定点的场，金向量为实际矢量和；源点附近不取样。归一化系数只去掉共同物理单位。';
    }else if(k==='sequence'){
      p=[c('index','观察序号 n',1,80,1,12),c('epsilon','圆盘半径 ε',.04,1,.01,.2)];
      formula='zₙ=(1+i)/n → 0；|zₙ|=√2/n；n>N=⌈√2/ε⌉ ⇒ |zₙ|<ε';
      explanation='蓝点为序列前 80 项，金点由序号滑块选出；绿圆是给定 ε 的开圆盘。有限个早期点可以在外，定义要求足够靠后的所有点在内。这里用明确序列给出可验证的 N，不能把它当作所有收敛序列的统一 N。';
    }else if(k==='unbounded'){
      p=[c('bound','候选界 R',.2,3,.05,1)];formula='E={x+0i:x≥0}；任给 R>0，z=R+1∈E 且 |z|>R';
      explanation='代表无界集取非负实轴。绿圆表示任意候选界 R；金点总取在 R+1，仍属于该射线且越出圆盘。增加 R 时见证点也向外移动，所以不存在能包住整个集合的有限圆盘。';
    }else if(k==='cancel'){
      mode=/分母/.test(title)?'denominator':'cancel';p=[c('real','Re z=x',-.8,1.5,.05,.5),c('imag','Im z=y',-1,1,.05,.4)];
      formula=mode==='denominator'?'(1−z)(1−z̄)=(1−x)²+y²；仅 z=1 时为零':'z−z̄=2iy；约去该因子须 y≠0；(z−z̄)(1−|z|²)=0';
      explanation=mode==='denominator'?'蓝线是 1 到 z 的向量，绿色是其共轭；分母是此向量模的平方，不是 1−x²+y²。改变 x、y 比较正确值与常见错式；z=1 处两分量同时为零，分母不能约去。':'蓝、绿点为 z 与其共轭，金线为差 2iy。当 y=0 时两点重合，方程乘积为零不能推出第二因子为零。图中同时显示 1−|z|²，避免把“可约”与原方程是否成立混淆。';
    }else if(k==='gradient'){
      p=[c('level','等值线 u=K>0',.2,1,.05,.6),c('parameter','线上位置 t',-.9,.9,.02,.4)];formula='u=x²−y²=K；∇u=(2x,−2y)；切向量 T=(y,x)；∇u·T=0';
      explanation='青色为双曲等值线，金点沿该曲线移动。蓝向量是梯度，粉向量沿切线；内积持续为零。这是在非临界点的正交关系，不把梯度为零处误说成存在唯一法向方向。';
    }else if(k==='circle'){
      mode=/直交|正交/.test(title)?'orthogonal':'reflection';p=[c('radius','基圆半径 R',.5,1.4,.05,1),mode==='orthogonal'?c('ratio','另一圆心距离 / R',1.2,2.2,.05,1.6):c('pointRadius','原点到 z 的距离 r',.3,2.2,.05,.7),c('angle','点的位置角 θ',-PI,PI,.02,.7)];
      formula=mode==='orthogonal'?'C=|c|>R，s²=C²−R²；交点处半径向量点积为 0':'关于 |z|=R 的对称：z*=R²/ z̄；|z||z*|=R²；z≠0';
      explanation=mode==='orthogonal'?'基圆与另一圆满足圆心距平方等于两半径平方之和，交点处两半径向量垂直。把另一圆上的点作基圆反演，仍落回同一圆；这解释圆反演与直交圆的关系。':'以原点为圆心的圆反演保持辐角，并把距离 r 改为 R²/r；圆内与圆外交换，圆上点不变，原点不能直接代入。这里只展示已声明圆心为零的代表情况。';
      map=function(x,y,p){var d=x*x+y*y;return d?[p.radius*p.radius*x/d,p.radius*p.radius*y/d]:[NaN,NaN];};
    }else if(k==='components'){
      mode=/纯虚数|虚轴/.test(title)?'pure':/虚单位|虚数单位/.test(title)?'unit':'general';
      p=mode==='unit'?[c('power','虚单位的整数次幂 n',1,8,1,1)]:mode==='pure'?[c('imag','虚部 y（实数）',-1.8,1.8,.05,1)]:[c('real','实部 x',-1.8,1.8,.05,.8),c('imag','虚部 y（实数）',-1.8,1.8,.05,1)];
      formula='z = x + iy；Re z = x；Im z = y ∈ ℝ；i² = −1';
      explanation='蓝色是复数向量，金色和绿色分别为实轴、虚轴投影。虚部是实数 y；iy 才是虚部对应的复向量。纯虚数按本书约定还要求 y≠0；虚单位 i 固定为 (0,1)，n 次幂滑块展示 i、−1、−i、1 的四步循环。';
    }else if(k==='injective'){
      p=[c('radius','原像模 r',.2,1.5,.05,.9),c('angle','原像方向 θ',-1.4,1.4,.02,.6),c('restricted','定义域：0 全平面 / 1 右半平面',0,1,1,0)];
      formula='f(z)=z²；f(z)=f(−z)，z≠0；限制 Re z>0 后为单叶';
      explanation='用 z² 区分单值与一一：每个 z 只有一个像，但全平面中的 z、−z 像相同。切换到右半平面后，成对原像只保留一个；本模型只演示该代表函数的单叶限制，并不把“单值”当成“一一”。';map=function(x,y){return square([x,y]);};
    }else if(k==='affine'){
      var identity=/恒等函数|identity/i.test(title),translation=/平移/.test(title),rotation=/旋转/.test(title),stretch=/伸张/.test(title);
      p=[c('scale','伸张系数 a>0',.3,1.8,.05,identity||translation||rotation?1:1.3),c('angle','旋转角 α',-PI,PI,.02,identity||translation||stretch?0:.4),c('real','平移 Re b',-1,1,.05,translation?.4:0),c('imag','平移 Im b',-1,1,.05,translation?.2:0)];
      formula='w = a eⁱᵅ z + b；a>0；恒等映照为 a=1, α=0, b=0';
      explanation='左图原网格按同色对应到右图；a 改变长度，α 改变方向，b 平移整体。把参数设为 a=1、α=0、b=0 可还原恒等映照。';
      map=function(x,y,p){var z=mul(polar(p.scale,p.angle),[x,y]);return [z[0]+p.real,z[1]+p.imag];};
    }else if(k==='inverse'){
      p=[c('scale','斜率 a>0',.3,2,.05,1.3),c('real','Re z',-.9,.9,.05,.6),c('imag','Im z',-.9,.9,.05,.4)];
      formula='f(z)=az；f⁻¹(w)=w/a；(f⁻¹)′(f(z)) = 1/f′(z) = 1/a';
      explanation='以双射 az（a>0）演示反函数：蓝色点乘 a 成为绿色像点，再除以 a 回到金色原点。导数乘积恒为 1；这只说明反函数法则的可核对代表，使用一般法则仍需原文的局部可逆条件。';map=function(x,y,p){return [p.scale*x,p.scale*y];};
    }else if(k==='chain'){
      p=[c('scale','内函数系数 a',.3,1.6,.05,1.1),c('real','Re z₀',-.9,.9,.05,.6),c('imag','Im z₀',-.9,.9,.05,.3)];
      formula='g(z)=az，f(w)=w²；(f∘g)′(z)=f′(g(z))g′(z)=2a²z';
      explanation='依次显示 z→az→(az)² 的同色点和小邻域；金色小向量是链式法则给出的一阶变化。改变 a 或 z₀ 时，直接求导与分两步求导的数值一起变化。';map=function(x,y,p){var z=square([x,y]);return [p.scale*p.scale*z[0],p.scale*p.scale*z[1]];};
    }else if(k==='limits'){
      p=[c('radius','到原点距离 r',.01,1.4,.01,.7),c('direction','接近方向 θ',-PI,PI,.02,.5),c('mode','函数：0 共轭z/z / 1 z²',0,1,1,0)];
      formula='z=r eⁱᶿ：z̄/z=e⁻²ⁱᶿ（依赖方向）；z²=r²e²ⁱᶿ→0';
      explanation='左图沿选定射线接近原点，右图是函数值。共轭z/z 在实轴与虚轴方向分别为 1、−1，故零点极限不存在；切换 z² 后，像的模等于 r²，所有方向都趋向 0，展示连续性。';
      map=function(x,y,p){if(p.mode===1)return square([x,y]);var d=x*x+y*y;return d?[ (x*x-y*y)/d,-2*x*y/d]:[NaN,NaN];};
    }else if(k==='cr'){
      p=[c('coefficient','虚部系数 c',-1,1.5,.05,1),c('real','检验点 x₀',.2,1,.05,.7),c('imag','检验点 y₀',.2,1,.05,.5)];
      formula='u=x²−y²，v=2cxy；uₓ=vᵧ，uᵧ=−vₓ 当 c=1';
      explanation='蓝、绿小向量是沿实、虚方向的差商极限。c=1 时它们给出同一个复导数；非零检验点处 c≠1 时残差非零。本例偏导连续，C-R 条件在邻域成立时可推出解析，不能用单个点的图代替一般充分性假设。';map=function(x,y,p){return [x*x-y*y,2*p.coefficient*x*y];};
    }else if(k==='primitive'){
      p=[c('real','终点 Re b',-.8,1.3,.05,1),c('imag','终点 Im b',-.8,1.3,.05,.5),c('bend','路径弯曲程度',-.9,1.2,.05,.4),c('reverse','方向：0 正向 / 1 反向',0,1,1,0),c('coefficient','被积函数倍数 c',.3,1.5,.05,1),c('split','路径分段位置 t',.1,.9,.05,.5)];
      formula='∫γ 2cz dz=cb²；∫γ₁+γ₂=∫γ₁+∫γ₂；倍数提出积分；反向变号';
      explanation='直线和弯曲路径连接 0 与 b；蓝、绿分别是连续路径的两段，右图是原函数 F(z)=cz² 的像。用每一线段上的中点值 2cz 与复增量 dz 计算，离散结果即精确望远镜求和 cb²。分段积分相加等于全程，倍数 c 可以提出；改变弯曲程度不改变积分，反向路径使结果变号。此处原函数在全平面存在，一般区域仍按教材条件判断。';map=function(x,y){return square([x,y]);};
    }else if(k==='ml'){
      p=[c('radius','圆弧半径 R',.2,1.5,.05,.8),c('angle','圆弧角度 β>0',.2,TAU,.02,2.4)];
      formula='γ(t)=Reⁱᵗ，0≤t≤β；|∫γ eᶻ dz|≤ML；M=eᴿ，L=Rβ';
      explanation='蓝线为圆弧，绿色曲线为 eᶻ 的像。整个弧上 Re z≤R，故 |eᶻ|≤eᴿ；积分真值为 e^(Reⁱᵝ)−eᴿ。金色长度是 ML 上界，不能把估计当成等号。';map=function(x,y){return exp([x,y]);};
    }else if(k==='roots'){
      p=[c('order','根的次数 n',2,12,1,4),c('radius','被开方数的模 ρ',.3,1.5,.05,1),c('angle','被开方数的辐角 θ',-PI,PI,.02,.5)];
      formula='zₖ=ρ¹⁄ⁿ exp(i(θ+2kπ)/n)，k=0,…,n−1；(reⁱᵅ)ⁿ=rⁿeⁱⁿᵅ';
      explanation='蓝色 n 个点是同一复数 ρeⁱᶿ 的全部 n 次根，等角间隔 2π/n；右图绿色点是它们共同的 n 次幂。ρ=1 时位于单位圆。德·莫弗公式决定模的乘方与辐角的 n 倍，不能把不同根误数成无限多个。';
      map=function(x,y,p){return polar(Math.pow(Math.hypot(x,y),p.order),p.order*Math.atan2(y,x));};
    }else if(k==='branch'){
      p=[c('cut','支割射线角 β',-PI,PI,.02,-PI),c('epsilon','射线两侧角距离 ε',.01,.4,.01,.08),c('radius','采样点模 r',.3,1.8,.05,1)];
      formula='Logβ z = ln|z| + i Argβ z；β<Argβ z<β+2π；割线上不定义';
      explanation='左图粉线是排除的支割射线，两侧不同颜色的点在几何上接近；右图对数像的虚部却相差约 2π。改变 β 会移动割线及选取的辐角区间。图中的两侧采样从不跨切口连线。';
      map=function(x,y,p){var r=Math.hypot(x,y);if(!r)return [NaN,NaN];var a=Math.atan2(y,x),delta=((a-p.cut)%TAU+TAU)%TAU;if(delta<1e-10||TAU-delta<1e-10)return [NaN,NaN];return [Math.log(r),p.cut+delta];};
    }else if(k==='poisson'){
      p=[c('order','边界 Fourier 模式 n',1,6,1,2),c('radius','内点半径 r<1',.05,.95,.01,.6),c('angle','内点方向 θ',-PI,PI,.02,.5)];
      formula='φ(t)=cos(nt)；u(r,θ)=(1/2π)∫Pᵣ(θ−t)φ(t)dt=rⁿcos(nθ)';
      explanation='单位圆边界给定 cos(nt)，圆盘内的曲面是其调和延拓 rⁿcos(nθ)。金点对应滑块内点；用 1024 个圆周样本计算泊松积分并与解析值对照。圆盘边界固定，r 始终小于 1。';
    }else if(k==='field'){
      p=[c('strength','场强 / 源强 q',-.9,1.5,.05,1),c('angle','均匀场方向 / 点源取样角 α',-PI,PI,.02,.4),c('mode','代表场：0 均匀 / 1 点源',0,1,1,/点电荷|通量|环量/.test(title)?1:0)];
      formula=/静电|电场|电荷|电位|电力/.test(title)?'电势 φ=Re W；E=−∇φ；W=−q e⁻ⁱᵅz 或 −q Log z':'复势 W=φ+iψ；速度 (U,V)=(Re W′,−Im W′)；W=q e⁻ⁱᵅz 或 q Log z';
      explanation='均匀场的等位线与流线正交，且处处无源无旋；点源场中等位线为圆、流线为射线，原点必须排除。复势与速度的对应采用 W=φ+iψ、W′=U−iV 的约定；电场若取 E=−∇φ，方向相反。此图明确区分该符号约定。';
    }
    return {kind:'foundation-'+k,title:title,formula:formula,explanation:explanation,controls:p,map:map,mode:mode,electric:/静电|电场|电荷|电位|电力/.test(title),initialPitch:k==='extrusion'?.65:undefined,initialYaw:k==='extrusion'?.4:undefined,illustrative:true,matchSource:own?'title':'body',label:'代表函数示意 · '+({extrusion:'截面与柱面',gauss:'散度与通量',green:'环量和通量',superposition:'两个点源叠加',sequence:'ε-N 序列',unbounded:'无界射线',cancel:'共轭因子',gradient:'梯度与等值线',circle:'圆反演',components:'坐标分量',injective:'平方映照',affine:'整线性映照',inverse:'非零线性映照',chain:'二次复合函数',limits:'方向极限',cr:'可调C-R条件',primitive:'原函数与路径',ml:'指数函数圆弧估计',roots:'等角根族',branch:'对数支',poisson:'圆盘调和延拓',field:'均匀场与点源'}[k])};
  }
  function geometry(m){
    var k=String(m.scene.kind||'').replace(/^foundation-/,''),p=m.params;if(m.scene.kind!=='foundation-'+k)return null;
    if(['extrusion','gauss','green','superposition','sequence','unbounded','cancel','gradient','circle','components','injective','affine','inverse','chain','limits','cr','primitive','ml','roots','branch','poisson','field'].indexOf(k)<0)return null;
    var g={lines:[],points:[],labels:[],readout:'',facts:{}};
    function line(points,color,opacity){g.lines.push({points:points,color:color||GREEN,opacity:opacity==null?1:opacity});}
    function sample(f,a,b,n){var r=[];n=n||80;for(var i=0;i<=n;i++)r.push(f(a+(b-a)*i/n));return r;}
    function v(z,offset,height){return [z[0]+(offset||0),z[1],height||0];}
    function point(z,color,offset){g.points.push({position:v(z,offset,.04),color:color||GOLD});}
    function axes(offset){offset=offset||0;line([[offset-1.9,0,0],[offset+1.9,0,0]],DIM,.7);line([[offset,-1.9,0],[offset,1.9,0]],DIM,.7);}
    function circle(r,offset,color){line(sample(function(t){return [offset+r*Math.cos(t),r*Math.sin(t),0];},0,TAU),color||DIM);}
    function pairLabels(left,right){g.labels=[[left,.25],[right,.75]];}
    function mapped(points,map){line(points.map(function(z){return v(z,-2.5);}),BLUE,.8);line(points.map(function(z){return v(map(z),2.5);}),GREEN,.9);}
    function grid(map){axes(-2.5);axes(2.5);for(var a=-1;a<=1.001;a+=.5){mapped(sample(function(t){return [a,t];},-1,1,30),map);mapped(sample(function(t){return [t,a];},-1,1,30),map);}}
    function arrow(x,y,dx,dy,color,scale){scale=scale||.25;var X=x+scale*dx,Y=y+scale*dy;line([[x,y,0],[X,Y,0]],color);var a=Math.atan2(dy,dx);if(Math.hypot(dx,dy)>.00001)line([[X-.055*Math.cos(a-.5),Y-.055*Math.sin(a-.5),0],[X,Y,0],[X-.055*Math.cos(a+.5),Y-.055*Math.sin(a+.5),0]],color);}
    if(k==='extrusion'){
      axes();var H=p.height;if(m.scene.mode==='point'){line([[p.real,p.imag,-H],[p.real,p.imag,H]],GOLD);point([p.real,p.imag],GREEN);g.facts={representsInfiniteLine:true};g.readout='截面点('+fmt(p.real)+','+fmt(p.imag)+')对应整条垂直直线；显示窗口−H≤h≤H，H='+fmt(H)+'。';}
      else{var R=p.radius;[-H,0,H].forEach(function(h){line(sample(function(t){return [R*Math.cos(t),R*Math.sin(t),h];},0,TAU,90),h===0?BLUE:GREEN,.8);});for(var j=0;j<16;j++){var t=TAU*j/16,x=R*Math.cos(t),y=R*Math.sin(t);line([[x,y,-H],[x,y,H]],GREEN,.6);if(m.scene.mode==='charged'){var r=R+.3;arrow(r*Math.cos(t),r*Math.sin(t),p.charge*R/r*Math.cos(t),p.charge*R/r*Math.sin(t),GOLD,.4);}}g.facts={representsInfiniteCylinder:true,charge:p.charge||0};g.readout='圆周截面半径R='+fmt(R)+'；柱面沿h方向无限延伸，图只裁取|h|≤'+fmt(H)+(m.scene.mode==='charged'?'；q='+fmt(p.charge)+'控制归一化外场方向。':'。');}g.labels=[['蓝色截面；绿色柱面/金色母线（可旋转）',.5]];
    }else if(k==='gauss'||k==='green'){
      axes();var R=p.radius,source=k==='gauss'?p.coefficient:p.source,omega=k==='gauss'?0:p.rotation;circle(R,0,GREEN);for(var xi=-3;xi<=3;xi++)for(var yi=-3;yi<=3;yi++){var x=xi*.4,y=yi*.4;if(Math.hypot(x,y)>1.7)continue;arrow(x,y,source*x-omega*y,source*y+omega*x,GOLD,.23);}
      var flux=TAU*source*R*R,circulation=TAU*omega*R*R;g.facts=k==='gauss'?{flux:flux,divergenceIntegral:2*source*PI*R*R,divergence:2*source}:{integral:[circulation,flux],circulation:circulation,flux:flux};g.readout=k==='gauss'?'div E=2c='+fmt(2*source)+'；圆周通量='+fmt(flux)+'=散度面积积分。':'Γ='+fmt(circulation)+'，Q='+fmt(flux)+'；∮overline(w)dz='+complex([circulation,flux]);
    }else if(k==='superposition'){
      axes();var a=p.distance;point([-a,0],BLUE);point([a,0],PINK);function contribution(x,y,cx,q){var d=(x-cx)*(x-cx)+y*y;return d<.035?null:[q*(x-cx)/d,q*y/d];}for(var xi=-4;xi<=4;xi++)for(var yi=-3;yi<=3;yi++){var x=xi*.35,y=yi*.4,E1=contribution(x,y,-a,p.first),E2=contribution(x,y,a,p.second);if(E1&&E2)arrow(x,y,E1[0]+E2[0],E1[1]+E2[1],GREEN,.12);}
      var x=.2,y=.75,first=contribution(x,y,-a,p.first),second=contribution(x,y,a,p.second),sum=[first[0]+second[0],first[1]+second[1]];arrow(x,y,first[0],first[1],BLUE,.4);arrow(x,y,second[0],second[1],PINK,.4);arrow(x,y,sum[0],sum[1],GOLD,.4);g.facts={first:first,second:second,sum:sum};g.readout='选定点(.2,.75)：E₁='+complex(first)+'；E₂='+complex(second)+'；合场='+complex(sum)+'。';
    }else if(k==='sequence'){
      axes();circle(p.epsilon,0,GREEN);var n=Math.round(p.index),N=Math.ceil(Math.SQRT2/p.epsilon),z=[1/n,1/n];
      for(var j=1;j<=80;j++)point([1/j,1/j],j>N?GREEN:BLUE);point(z,GOLD);line([[0,0,0],v(z)],GOLD);g.facts={distance:Math.SQRT2/n,tailThreshold:N,inside:Math.SQRT2/n<p.epsilon};g.readout='n='+n+'，|zₙ|='+fmt(Math.SQRT2/n)+'；ε='+fmt(p.epsilon)+'；取 N='+N+'，全部 n>N 的项都在开圆盘内。';
    }else if(k==='unbounded'){
      axes();circle(p.bound,0,GREEN);line([[0,0,0],[p.bound+1.4,0,0]],BLUE);point([p.bound+1,0],GOLD);g.facts={bound:p.bound,witnessModulus:p.bound+1};g.readout='候选界 R='+fmt(p.bound)+'；见证 z=R+1='+fmt(p.bound+1)+'，|z|>R；射线还继续向外延伸。';
    }else if(k==='cancel'){
      axes();var x=p.real,y=p.imag,z=[x,y],bar=[x,-y],factor=2*y,second=1-x*x-y*y,den=(1-x)*(1-x)+y*y;
      point(z,BLUE);point(bar,GREEN);line([v(z),v(bar)],GOLD);var center=m.scene.mode==='denominator'?1:0;circle(m.scene.mode==='denominator'?Math.sqrt(den):1,center,DIM);line([[center,0,0],v(z)],BLUE);line([[center,0,0],v(bar)],GREEN);
      g.facts={canCancel:y!==0,difference:[0,factor],secondFactor:second,product:[0,factor*second],denominator:den,wrongExpression:1-x*x+y*y,defined:den>0};
      g.readout=m.scene.mode==='denominator'?'正确分母 (1−x)²+y²='+fmt(den)+'；错式 1−x²+y²='+fmt(g.facts.wrongExpression)+(den>0?'；分母非零':'；z=1，分母为零、不可定义'):'z−z̄='+fmt(factor)+'i；1−|z|²='+fmt(second)+'；'+(y!==0?'因子非零，可以约去（原方程仍须成立）':'因子为零，不能约去、不能推出第二因子为零');
    }else if(k==='gradient'){
      axes();var K=p.level,t=p.parameter,root=Math.sqrt(K),x=root*Math.cosh(t),y=root*Math.sinh(t),gradient=[2*x,-2*y],tangent=[y,x];
      line(sample(function(t){return [root*Math.cosh(t),root*Math.sinh(t),0];},-1.1,1.1,90),GREEN);point([x,y],GOLD);line([[x,y,0],[x+.35*gradient[0],y+.35*gradient[1],0]],BLUE);line([[x-.6*tangent[0],y-.6*tangent[1],.03],[x+.6*tangent[0],y+.6*tangent[1],.03]],PINK);g.facts={point:[x,y],gradient:gradient,tangent:tangent};g.readout='u='+fmt(K)+'；∇u·T='+fmt(gradient[0]*tangent[0]+gradient[1]*tangent[1])+'；梯度非零，法线与切线正交。';
    }else if(k==='circle'){
      axes();var R=p.radius,R2=R*R;circle(R,0,BLUE);var z,reflected;
      if(m.scene.mode==='orthogonal'){
        var C=p.ratio*R,s=Math.sqrt(C*C-R2),P=[R2/C,R*Math.sqrt(1-R2/(C*C))];line(sample(function(t){return [C+s*Math.cos(t),s*Math.sin(t),0];},0,TAU,100),GREEN);line([[0,0,0],v(P),[C,0,0]],GOLD);point(P,GOLD);z=[C+s*Math.cos(p.angle),s*Math.sin(p.angle)];reflected=m.scene.map(z[0],z[1],p);g.facts={centerDistance:C,radius:R,otherRadius:s,radiusDotProduct:P[0]*(P[0]-C)+P[1]*P[1],original:z,reflected:reflected};g.readout='C²='+fmt(C*C)+' = R²+s²='+fmt(R2+s*s)+'；交点两半径点积='+fmt(g.facts.radiusDotProduct)+'；反演点仍在绿圆。';
      }else{z=polar(p.pointRadius,p.angle);reflected=m.scene.map(z[0],z[1],p);g.facts={original:z,reflected:reflected,radiusProduct:Math.hypot.apply(Math,z)*Math.hypot.apply(Math,reflected)};g.readout='r='+fmt(p.pointRadius)+'，r*='+fmt(R2/p.pointRadius)+'；rr*='+fmt(g.facts.radiusProduct)+'=R²；辐角保持不变。';}
      point(z,GREEN);point(reflected,PINK);line([[0,0,0],v(z),v(reflected)],GOLD,.8);
    }else if(k==='components'){
      axes();var unitPower=[[1,0],[0,1],[-1,0],[0,-1]][Math.round(p.power||1)%4],x=m.scene.mode==='unit'?unitPower[0]:m.scene.mode==='pure'?0:p.real,y=m.scene.mode==='unit'?unitPower[1]:p.imag;
      line([[0,0,0],[x,y,0]],BLUE);line([[0,0,0],[x,0,0]],GOLD);line([[x,0,0],[x,y,0],[0,y,0]],GREEN);point([x,y]);circle(Math.hypot(x,y),0,DIM);
      g.facts={real:x,imag:y,modulus:Math.hypot(x,y),pureImaginary:x===0&&y!==0};g.readout='Re z='+fmt(x)+'；Im z='+fmt(y)+'（实数）；|z|='+fmt(g.facts.modulus)+(x===0?(y!==0?'；满足本书纯虚数条件':'；z=0，不属于本书纯虚数'):'');
    }else if(k==='injective'){
      axes(-2.5);axes(2.5);var a=polar(p.radius,p.angle),b=[-a[0],-a[1]],w=square(a),restricted=p.restricted===1;
      var begin=restricted?-PI/2+.015:0,end=restricted?PI/2-.015:TAU;
      mapped(sample(function(t){return polar(p.radius,t);},begin,end,100),square);
      point(a,BLUE,-2.5);point(w,GOLD,2.5);line([[-2.5,0,0],v(a,-2.5)],BLUE);
      if(!restricted){point(b,PINK,-2.5);line([[-2.5,0,0],v(b,-2.5)],PINK);}
      g.facts={inputs:[a,b],outputs:[w,square(b)],injectiveOnShownDomain:restricted};pairLabels('z 平面 · 定义域','w=z² 平面');g.readout=restricted?'限制 Re z > 0：右半平面中一一对应；边界虚轴不包含。':'全平面：z 与 −z 不同，而 z²=(−z)²='+complex(w)+'；单值不等于一一。';
    }else if(k==='affine'){
      var map=function(z){return m.scene.map(z[0],z[1],p);};grid(map);point([.7,.3],GOLD,-2.5);point(map([.7,.3]),GOLD,2.5);pairLabels('z 平面','a eⁱᵅz+b 平面');g.facts={scale:p.scale,rotation:p.angle,translation:[p.real,p.imag]};g.readout='伸张 '+fmt(p.scale)+' 倍；旋转 '+fmt(p.angle)+' 弧度；平移 '+complex([p.real,p.imag]);
    }else if(k==='inverse'){
      var z=[p.real,p.imag],w=[p.scale*z[0],p.scale*z[1]],restored=[w[0]/p.scale,w[1]/p.scale];grid(function(q){return [p.scale*q[0],p.scale*q[1]];});point(z,BLUE,-2.5);point(w,GREEN,2.5);point(restored,GOLD,-2.5);pairLabels('原像与反函数返回','像 w=az');g.facts={derivative:p.scale,inverseDerivative:1/p.scale,restored:restored};g.readout='f′='+fmt(p.scale)+'；(f⁻¹)′='+fmt(1/p.scale)+'；返回 '+complex(restored);
    }else if(k==='chain'){
      var z=[p.real,p.imag],az=[p.scale*z[0],p.scale*z[1]],out=square(az),d=[2*p.scale*p.scale*z[0],2*p.scale*p.scale*z[1]];
      var chain=[2*az[0]*p.scale,2*az[1]*p.scale];axes(-3);axes(0);axes(3);line(sample(function(t){return v([z[0]+.16*Math.cos(t),z[1]+.16*Math.sin(t)],-3);},0,TAU),BLUE);line(sample(function(t){return v([az[0]+p.scale*.16*Math.cos(t),az[1]+p.scale*.16*Math.sin(t)]);},0,TAU),GREEN);line(sample(function(t){var q=square([p.scale*(z[0]+.16*Math.cos(t)),p.scale*(z[1]+.16*Math.sin(t))]);return v(q,3);},0,TAU),GOLD);point(z,BLUE,-3);point(az,GREEN);point(out,GOLD,3);line([v(out,3),v([out[0]+.16*d[0],out[1]+.16*d[1]],3)],PINK);g.labels=[['z',.17],['g(z)=az',.5],['f(g(z))',.83]];g.facts={derivative:d,chainDerivative:chain};g.readout='直接求导 = 链式求导 = '+complex(d);
    }else if(k==='limits'){
      var z=polar(p.radius,p.direction),map=function(q){return m.scene.map(q[0],q[1],p);},w=map(z);axes(-2.5);axes(2.5);mapped(sample(function(r){return polar(r,p.direction);},.01,p.radius,70),map);point(z,GOLD,-2.5);point(w,GOLD,2.5);if(!p.mode)circle(1,2.5,DIM);pairLabels('接近原点的射线','对应函数值');g.facts={value:w,pathIndependent:p.mode===1};g.readout=(p.mode===1?'z²：像的模 r²=' +fmt(p.radius*p.radius):'共轭z/z：值取决于方向 θ='+fmt(p.direction))+'；值='+complex(w);
    }else if(k==='cr'){
      var x=p.real,y=p.imag,q=p.coefficient,ux=2*x,uy=-2*y,vx=2*q*y,vy=2*q*x,d1=[ux,vx],d2=[vy,-uy],residual=[ux-vy,uy+vx];axes();line([[0,0,0],v(d1)],BLUE);line([[0,0,.03],[d2[0],d2[1],.03]],GREEN);point(d1,BLUE);point(d2,GREEN);g.facts={ux:ux,uy:uy,vx:vx,vy:vy,residual:residual,satisfiesCR:Math.hypot.apply(Math,residual)<1e-10};g.readout='uₓ−vᵧ='+fmt(residual[0])+'；uᵧ+vₓ='+fmt(residual[1])+'；'+(g.facts.satisfiesCR?'两方向差商一致':'非零点处不满足C-R');g.labels=[['蓝：实方向差商；绿：虚方向差商；重合才表示一致',.5]];
    }else if(k==='primitive'){
      axes(-2.5);axes(2.5);var b=[p.real,p.imag],path=sample(function(t){return [t*b[0],t*b[1]+p.bend*Math.sin(PI*t)];},0,1,100);
      if(p.reverse===1)path.reverse();
      var split=Math.round((p.split==null?.5:p.split)*(path.length-1)),first=path.slice(0,split+1),second=path.slice(split),coef=p.coefficient==null?1:p.coefficient;
      var primitive=function(z){return square(z).map(function(q){return coef*q;});};
      function integrate(points,coefficient){var result=[0,0];for(var j=1;j<points.length;j++){var a=points[j-1],z=points[j],term=mul([coefficient*(a[0]+z[0]),coefficient*(a[1]+z[1])],[z[0]-a[0],z[1]-a[1]]);result[0]+=term[0];result[1]+=term[1];}return result;}
      [first,second].forEach(function(points,index){var color=index?GREEN:BLUE;line(points.map(function(z){return v(z,-2.5);}),color);line(points.map(function(z){return v(primitive(z),2.5);}),color);});
      line([[-2.5,0,.02],v(b,-2.5,.02)],GOLD,.6);point(path[split],PINK,-2.5);point(primitive(path[split]),PINK,2.5);
      var integral=integrate(path,coef),parts=[integrate(first,coef),integrate(second,coef)],exact=primitive(b);if(p.reverse===1)exact=exact.map(function(q){return -q;});
      g.facts={integral:integral,primitiveDifference:exact,parts:parts,unscaledIntegral:integrate(path,1)};
      pairLabels('γ₁（蓝）+γ₂（绿）','F(z)=cz² 的对应路径');
      g.readout='全程积分='+complex(integral)+'；分段积分相加='+complex([parts[0][0]+parts[1][0],parts[0][1]+parts[1][1]])+'；c='+fmt(coef)+' 倍未缩放积分；端点差='+complex(exact);
    }else if(k==='ml'){
      axes(-2.5);axes(2.5);mapped(sample(function(t){return polar(p.radius,t);},0,p.angle,100),exp);var z=polar(p.radius,p.angle),end=exp(z),start=[Math.exp(p.radius),0],value=[end[0]-start[0],end[1]-start[1]],L=p.radius*p.angle,M=Math.exp(p.radius),bound=M*L;
      point(z,GOLD,-2.5);point(end,GOLD,2.5);line([v(start,2.5),v(end,2.5)],GOLD);g.facts={integral:value,integralMagnitude:Math.hypot.apply(Math,value),maximum:M,length:L,bound:bound};pairLabels('圆弧 γ','eᶻ 的像与端点差');g.readout='|积分|='+fmt(g.facts.integralMagnitude)+' ≤ ML='+fmt(bound)+'；M='+fmt(M)+'，L='+fmt(L);
    }else if(k==='roots'){
      axes(-2.5);axes(2.5);var roots=[],n=Math.round(p.order),r=Math.pow(p.radius,1/n),target=polar(p.radius,p.angle);circle(r,-2.5,DIM);for(var j=0;j<n;j++){var z=polar(r,(p.angle+TAU*j)/n);roots.push(z);point(z,BLUE,-2.5);line([[-2.5,0,0],v(z,-2.5)],BLUE,.8);}point(target,GOLD,2.5);line([[2.5,0,0],v(target,2.5)],GOLD);g.facts={roots:roots,target:target};pairLabels('n 个等角分布的根','共同的 n 次幂');g.readout=n+' 个互异根；相邻夹角 2π/n='+fmt(TAU/n)+'；共同 n 次幂='+complex(target);
    }else if(k==='branch'){
      axes(-2.5);axes(2.5);line([[-2.5,0,0],v(polar(1.9,p.cut),-2.5)],PINK);var args=[p.cut+p.epsilon,p.cut+TAU-p.epsilon],inputs=args.map(function(a){return polar(p.radius,a);}),values=inputs.map(function(z){return m.scene.map(z[0],z[1],p);});for(var j=0;j<2;j++){var color=j?GOLD:BLUE;point(inputs[j],color,-2.5);point(values[j],color,2.5);line(sample(function(r){return v(polar(r,args[j]),-2.5);},.2,1.8,40),color);line(sample(function(r){return [2.5+Math.log(r),args[j],0];},.2,1.8,40),color);}g.facts={arguments:args,values:values};pairLabels('排除粉色支割线','Logβ 像（虚部为选定辐角）');g.readout='Arg区间 ('+fmt(p.cut)+', '+fmt(p.cut+TAU)+')；两侧虚部差='+fmt(TAU-2*p.epsilon)+'≈2π；切口不连接。';
    }else if(k==='poisson'){
      axes();var n=Math.round(p.order);for(var r=.2;r<=1.001;r+=.2){(function(r){line(sample(function(t){return [r*Math.cos(t),r*Math.sin(t),.7*Math.pow(r,n)*Math.cos(n*t)];},0,TAU,100),r>.99?GOLD:GREEN,.8);})(r);}for(var a=0;a<TAU;a+=PI/8){(function(a){line(sample(function(r){return [r*Math.cos(a),r*Math.sin(a),.7*Math.pow(r,n)*Math.cos(n*a)];},0,1,40),BLUE,.7);})(a);}var value=Math.pow(p.radius,n)*Math.cos(n*p.angle),sum=0,N=1024;for(var j=0;j<N;j++){var t=TAU*j/N;sum+=(1-p.radius*p.radius)/(1-2*p.radius*Math.cos(p.angle-t)+p.radius*p.radius)*Math.cos(n*t)/N;}g.points.push({position:[p.radius*Math.cos(p.angle),p.radius*Math.sin(p.angle),.7*value],color:GOLD});g.facts={value:value,poissonIntegral:sum,radius:p.radius};g.readout='解析值 rⁿcos(nθ)='+fmt(value)+'；数值泊松积分='+fmt(sum)+'；误差='+fmt(Math.abs(value-sum));g.labels=[['圆盘内调和曲面；金色边界为给定值',.5]];
    }else if(k==='field'){
      axes();var q=p.strength,a=p.angle,mode=p.mode===1,vector=polar(q,a);
      if(!mode){for(var j=-3;j<=3;j++){var t=j*.4;line([v(mul([t,-1.6],polar(1,a))),v(mul([t,1.6],polar(1,a)))],BLUE,.8);line([v(mul([-1.6,t],polar(1,a))),v(mul([1.6,t],polar(1,a)))],GREEN,.8);}for(var x=-1;x<=1.01;x+=.5)for(var y=-1;y<=1.01;y+=.5)line([[x,y,0],[x+.3*vector[0],y+.3*vector[1],0]],GOLD);}
      else{for(var r=.4;r<1.81;r+=.35)circle(r,0,BLUE);for(var t=p.angle;t<TAU+p.angle;t+=PI/8){line(sample(function(r){return v(polar(r,t));},.2,1.8,15),GREEN,.8);var center=polar(1,t);line([v(center),v([center[0]+.3*q*center[0],center[1]+.3*q*center[1]])],GOLD);}g.points.push({position:[0,0,0],color:PINK});vector=polar(q,a);}
      g.facts={vector:vector,divergence:0,curl:0,flux:mode?TAU*q:0,circulation:0,electric:m.scene.electric};g.labels=[['蓝：等位线；绿：流线；金：速度向量',.5]];g.readout=mode?(m.scene.electric?'点电荷场，φ=−q ln r：':'点源速度场，φ=q ln r：')+'原点除外无源无旋；圆周通量 2πq='+fmt(TAU*q)+'；环量=0。':'均匀'+(m.scene.electric?'电场 E=−∇φ':'速度场 (U,V)=∇φ')+'=('+fmt(vector[0])+','+fmt(vector[1])+')；div=0，curl=0。';
    }
    return g;
  }
  return {chooseScene:chooseScene,geometry:geometry};
});
