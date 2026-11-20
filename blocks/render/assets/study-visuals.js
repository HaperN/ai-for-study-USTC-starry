/* Offline mathematical explorations. Source ReaderDocuments are read, never rewritten.
 * Pure models are shared by the Three.js renderer and the CPU fallback/test suite. */
(function (root, factory) {
  'use strict';
  var api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else {root.DSHStudyVisuals=api;(root.DSHStudySceneExtensions||[]).forEach(api.registerScenes);if(root.document)api.boot(root.document);}
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  var TAU=Math.PI*2,BLUE='#2563eb',TEAL='#087f8c',GOLD='#b6630b',PINK='#b43a78';
  var uid=0,extensions=[];
  function registerScenes(provider){if(provider&&typeof provider.chooseScene==='function'&&extensions.indexOf(provider)<0)extensions.push(provider);}
  function control(key,label,min,max,step,value){return {key:key,label:label,min:min,max:max,step:step,value:value};}
  function minimumUniformTerms(b,epsilon){
    if(!Number.isFinite(b)||!Number.isFinite(epsilon)||b<0||b>=1||epsilon<=0)return null;
    if(b===0||b<epsilon)return 1;
    // Logs estimate the threshold; actual powers decide the strict inequality,
    // including equality and either direction of floating point rounding.
    var n=Math.max(1,Math.floor(Math.log(epsilon)/Math.log(b))+1);
    while(n>1&&Math.pow(b,n-1)<epsilon)n--;
    while(Math.pow(b,n)>=epsilon)n++;
    return n;
  }
  function chooseScene(title,body) {
    for(var extension of extensions){var extended=extension.chooseScene(title,'');if(extended){if(!extended.matchSource)extended.matchSource='title';return extended;}}
    var text=String(title||'').replace(/\\/g,'')+' '+String(body||'').slice(0,650).replace(/\\/g,''),kind='neighborhood';
    // A title has priority over surrounding text: an annotation about log is not
    // misclassified because its chapter also mentions exp, residues, or series.
    function classify(t) {
      if(/(?:不|非)一致收敛/.test(t))return 'uniform';
      if(/望远镜|z\^\{?2\}?\s*[-−]\s*z/.test(t))return 'telescoping';
      if(/双曲函数/.test(t))return 'hyperbolic';
      if(/积分.*读出|柯西公式.*核/.test(t))return 'cauchy';
      if(/保角.*方向/.test(t))return 'derivative';
      if(/许.?克公式|许.?克变换/.test(t))return 'schwarz';
      if(/(?:theta|θ).*不能丢|微分元|参数化.*因子/.test(t))return 'circle-differential';
      if(/deg.*(?:Q|P)|大圆弧.*(?:衰减|趋|积分)|次数条件/.test(t))return 'arc-decay';
      if(/约当|简单曲线|连续曲线|自交|参数曲线|折线连通|无重点/.test(t))return 'curve';
      if(/三角恒等|三角函数公式|cos[²^]|sin[²^]/i.test(t))return 'trig-identity';
      if(/半角|加号与减号/.test(t))return 'half-angle';
      if(/sin.*(?:θ|theta).*分母|正弦.*分母/.test(t))return 'euler-trig';
      if(/反正切|Arctan|arctan/i.test(t))return 'arctan';
      if(/正切|tan\s*[z(]/i.test(t))return 'tan';
      if(/余切|cot\s*[z(]/i.test(t))return 'cot';
      if(/弧长平均|角度平均|平均值|平均值公式/.test(t))return 'mean';
      if(/对数导数.*积分|N.?P公式|辐角原理|鲁歇|儒歇|Rouch|绕.{0,12}圈|圈数|零点.{0,5}个数/i.test(t))return 'argument';
      if(/(?:阶|单)零点|定义.{0,4}零点|^零点$/.test(t))return 'zero';
      if(/椭圆族|椭圆.*线段/.test(t))return 'joukowski';
      if(/割痕端点笔误/.test(t))return 'slit-endpoint';
      if(/叠放.*平面/.test(t))return 'derivative';
      if(/无穷和|部分和|尾巴.*向量|修正项|收敛域/.test(t))return 'series';
      if(/结合律|associativ/i.test(t))return 'associative';
      if(/分配律|distributiv/i.test(t))return 'distributive';
      if(/刘维尔|Liouville/i.test(t))return 'liouville';
      if(/最大模|模.*最大值/i.test(t))return 'maximum';
      if(/唯一性|恒等定理|恒等原理|零点.*聚点/i.test(t))return 'identity';
      if(/儒可夫斯基|茹可夫斯基|Joukowski|机翼剖面/i.test(t))return 'joukowski';
      if(/克利斯托|Schwarz.?Christoffel|多边形.*映/i.test(t))return 'schwarz';
      if(/欧拉公式|Euler|纯虚|虚轴|实部与虚部|虚单位/i.test(t))return 'complex';
      if(/旋转角|伸张系数|求导法则/i.test(t))return 'derivative';
      if(/平移变换/i.test(t))return 'addition';
      if(/旋转变换|相似变换/i.test(t))return 'multiply';
      if(/交换律|commutativ/i.test(t))return 'commutative';
      if(/拉[普氏]|Laplace/i.test(t))return 'laplace';
      if(/强级数|比较判别|[维魏]尔斯特拉斯|Weierstrass|M.?test|阶乘|[kn]\s*!/i.test(t))return 'majorant';
      if(/一致收敛|逐点收敛|uniform/i.test(t))return 'uniform';
      if(/[洛罗]朗|Laurent/i.test(t))return 'laurent';
      if(/本性奇点|essential singular/i.test(t))return 'essential';
      if(/幂级数|泰勒|Taylor|级数|收敛半径|洛朗|Laurent/i.test(t))return 'series';
      if(/开拓|延拓|continuation/i.test(t))return 'continuation';
      if(/调和|harmonic|柯西.?黎曼|Cauchy.?Riemann/i.test(t))return 'harmonic';
      if(/留数|围道|闭路|闭曲线|路径积分|积分定理|积分公式|柯西积分|柯西型积分|Cauchy.*积分|奇点|极点|孤立奇|本性奇|零点.*极点|积分计算/i.test(t))return 'contour';
      if(/导数|导数定义|复可微|微分|解析函数|可导/i.test(t))return 'derivative';
      if(/球面|无穷远|球极|Riemann.*球/i.test(t))return 'sphere';
      if(/保形|分式线性|双线性|莫比|M.bius|共形/i.test(t))return 'mobius';
      if(/双曲正弦|sinh/i.test(t))return 'sinh';
      if(/双曲余弦|cosh/i.test(t))return 'cosh';
      if(/正弦|sin\s*[z(]/i.test(t))return 'sin';
      if(/余弦|cos\s*[z(]/i.test(t))return 'cos';
      if(/共轭/i.test(t))return 'conjugate';
      if(/除法|商的/i.test(t))return 'quotient';
      if(/平方根|根式|sqrt/i.test(t))return 'sqrt';
      if(/主值.*对数|对数|log/i.test(t))return 'log';
      if(/指数|exponential|e\^z|e\^\{z\}|exp/i.test(t))return 'exp';
      if(/倒数|反演|1\s*\/\s*z|frac\{1\}\{z\}/i.test(t))return 'inverse';
      if(/德.{0,3}莫弗|乘方|z\s*\^\s*\{?\s*n/i.test(t))return 'power';
      if(/z²|z\^\{?2|平方|幂函数/i.test(t))return 'square';
      if(/乘法|乘积|相乘|辐角相加|除法|商的/i.test(t))return 'multiply';
      if(/加法|加减|相加|三角不等式/i.test(t))return 'addition';
      if(/复数|复平面|辐角|模与|共轭|极坐标/i.test(t))return 'complex';
      if(/邻域|区域|点集|连通|单连通|连续|极限|圆环|开集|闭集|边界/i.test(t))return 'neighborhood';
      return null;
    }
    var titleMatch=classify(String(title||'')),bodyMatch=classify(text),strongBody=['majorant','uniform','telescoping','maximum','liouville','identity','argument','zero','associative','distributive','commutative','euler-trig','half-angle','trig-identity','slit-endpoint','joukowski','schwarz'].indexOf(bodyMatch)>=0;if(!titleMatch&&!strongBody){for(var extension of extensions){var extended=extension.chooseScene(title,body);if(extended){if(!extended.matchSource)extended.matchSource='body';return extended;}}}
    var identified=titleMatch||bodyMatch,matchSource=titleMatch?'title':(identified?'body':'unmatched');kind=identified||'unmatched';
    var configs={
      hyperbolic:['f(z)：0 sinh z / 1 cosh z / 2 tanh z / 3 coth z','本节双曲函数族：sinh与cosh由指数定义，导数分别为cosh与sinh，且cosh²−sinh²=1。tanh=sinh/cosh的极点在(n+1/2)πi；coth=cosh/sinh的极点在nπi。切换模式核对实际复映照与分母零点。',[control('mode','函数 0/1/2/3',0,3,1,0),control('extent','网格范围',.3,1.6,.05,1),control('phase','标记点辐角',-3.14,3.14,.02,.6)]],
      cauchy:['(1/2πi)∮γ ζ²/(ζ−a)dζ=a²；|a|<R','用 f(ζ)=ζ² 演示柯西积分公式。左图标出积分圆与取值点 a，右图逐项累加归一化积分贡献，终点应重合于金色的 f(a)。a在圆外时积分为0，不能继续用此式读f(a)。',[control('radius','积分圆半径 R',.5,1.5,.05,1),control('real','Re a',-.9,.9,.05,.3),control('imag','Im a',-.9,.9,.05,.2)]],
      'circle-differential':['z=Reⁱᶿ；dz/dθ=iReⁱᶿ；dθ=dz/(iz)','复微分含有旋转90°的 i 与长度因子 R。蓝向量为径向，金向量为正确切向；粉色径向是漏掉i后的错误方向。',[control('radius','圆半径 R',.3,1.5,.05,1),control('angle','参数 θ',0,6.28,.02,.8),control('delta','参数小增量 Δθ',.02,.4,.02,.2)]],
      'arc-decay':['γR: z=Reⁱᶿ，0≤θ≤π；f(z)=1/zᑫ；ML=πR¹⁻ᑫ','用次数差 q 的代表模型看大半圆弧：q=1时上界不趋零；q≥2时 ML 随R增大趋零。左图按z/R归一化，右图为实际积分向量。',[control('radius','半圆半径 R',1,8,.1,2),control('degree','次数差 q',1,4,1,2)]],
      curve:['圆：z(t)=cos t+i sin t；8字：z(t)=sin t+i sin 2t','连续性不排除自交。切换圆与8字形：圆只有首尾参数重合；8字形在 t=0 与 t=π 时都经过原点，因而不是简单闭曲线。',[control('mode','曲线：0 圆 / 1 自交',0,1,1,0),control('phase','运动参数 t',0,6.28,.02,1)]],
      'trig-identity':['cos²z+sin²z=1；z=x+iy','把 cos²z 和 sin²z 作为两个复向量首尾相接；即使 z 不在实轴，两向量之和仍恰好为实数1。',[control('real','Re z',-1.5,1.5,.05,.6),control('imag','Im z',-1.2,1.2,.05,.5)]],
      'euler-trig':['z=eⁱᶿ；sin θ=(z−1/z)/(2i)','左图 z 与 1/z 关于实轴对称。右图把纯虚差 z−1/z 除以2i后得到实数 sin θ；若只除以2，会错误地留在虚轴上。',[control('angle','角度 θ',0,6.28,.01,1.57)]],
      'half-angle':['2+2cosθ=4cos²(θ/2)；2−2cosθ=4sin²(θ/2)','在单位圆上比较 |1+eⁱᶿ|² 与 |1−eⁱᶿ|²。加号对应余弦半角，减号对应正弦半角。',[control('angle','角度 θ',0,6.28,.01,1.2)]],
      tan:['w=tan z=sin z/cos z；周期 π','观察正切映照，cos z=0 的极点被排除。实部方向平移 π 回到同一函数值。',[control('extent','网格范围',.3,2,.05,1.3),control('phase','标记点辐角',-3.14,3.14,.02,.6)]],
      cot:['w=cot z=cos z/sin z；周期 π','观察余切映照，sin z=0 的极点被排除；映照只在分母非零处定义。',[control('extent','网格范围',.3,2,.05,1.3),control('phase','标记点辐角',-3.14,3.14,.02,.6)]],
      arctan:['Arctan z=Log[(1+iz)/(1−iz)]/(2i)；z≠±i','图为反正切的一个主值支。z=±i 时对数比值为零或无穷，不能给出有限的反正切值；并非随意给该点补一个值。',[control('extent','网格范围',.3,1.5,.05,1.2),control('phase','标记点辐角',-3.14,3.14,.02,.6)]],
      mean:['f(z)=z²；(1/2πR)∮f ds=(1/2π)∫f(a+Reⁱᶿ)dθ=f(a)','等角样本对应等弧长权重；把 ds 换成 dθ 时必须同时用 ds=R dθ 调整分母。曲线高度画 Re f，读数同时给出复平均。',[control('radius','圆周半径 R',.3,2,.05,1),control('real','中心 Re a',-.7,.7,.05,.3),control('imag','中心 Im a',-.7,.7,.05,.2)]],
      argument:['f(z)=(zᵐ−a)/zᵖ；Δarg f=2π(N−P)','原像闭圆（左）与像曲线（右）。像绕原点的圈数是圆内零点数减极点数（均按重数）。a越过Rᵐ时零点越过路径；a=0时先约去共同因子再计数。',[control('order','分子次数 m',1,6,1,3),control('radius','闭路半径 R',.3,1.3,.05,1),control('shift','常数 a（实数）',0,2,.05,.4),control('poleOrder','分母次数 p',0,3,1,0)]],
      zero:['f(z)=azᵐ；a≠0；z=0为m阶零点','曲面高度表示 |f(z)|。增大 m 时零点附近更平；前m−1阶导数在零点为0，第m阶导数为m!a≠0。',[control('order','零点阶数 m',1,6,1,2),control('amplitude','非零系数 a',.2,1.5,.05,.8),control('radius','观察半径',.3,1.3,.05,1)]],
      'slit-endpoint':['割痕：u≤−1，v=π；w(−1)=−1+πi','用教材给出的像点校对割痕端点。绿色端点固定在−1+πi；金色滑块为候选端点，取+1会与对应表相差2个实单位。这里只校核这一几何条件。',[control('endpoint','候选端点实部',-1,1,.1,1)]],
      associative:['(a+b)+c=a+(b+c)；(ab)c=a(bc)','b=0.6+0.7i，c=−0.5+0.4i。蓝绿折线展示两种括号下不同的中间结果；金色终点完全相同。',[control('real','Re a',-.8,1.4,.05,.8),control('imag','Im a',-.8,1.4,.05,.6)]],
      distributive:['a(b+c)=ab+ac；b=0.7+0.5i，c=0.3−0.7i','左图先相加，右图把两个向量分别乘以 a 再相加。绿色直达像点与蓝色分步相加的终点重合。',[control('real','Re a',-1.2,1.5,.05,.8),control('imag','Im a',-1.2,1.5,.05,.6)]],
      maximum:['f(z)=zⁿ+c；|z|≤R；max|f|=Rⁿ+|c|','以多项式为代表观察最大模原理：模的曲面最高处出现在边界。此图是具体函数的说明，不能替代一般定理的证明。',[control('radius','圆盘半径 R',.3,1.2,.05,1),control('order','次数 n',1,5,1,2),control('shift','实常数 c',-.8,.8,.05,.35)]],
      liouville:['|f′(0)|≤M/R；非恒定代表 f(z)=az','若整函数在全平面共用上界 M，Cauchy 估计随 R 增大迫使导数为零。对照 f(z)=az：a≠0 时圆盘最大模 aR 随 R 增长，无法共用有限上界。',[control('radius','估计半径 R',.5,10,.1,2),control('bound','假设上界 M',.5,3,.1,1),control('amplitude','代表函数系数 a',0,1.2,.05,.4)]],
      identity:['h(z)=azⁿ；zₖ=R/k → 0','唯一性定理要求区域内聚点处的一列点上函数差恰好为零。a≠0 时 h 只有一个零点；h(zₖ) 趋近零并不等于每项为零。a=0 时才在整个区域恒零。',[control('amplitude','函数差系数 a',0,1,.05,.5),control('order','零点阶数 n',1,5,1,2),control('radius','观察半径 R',.3,1.3,.05,1)]],
      joukowski:['w=(z+1/z)/2；z≠0','左侧平移圆周，右侧为同色像曲线。调整圆心和半径，可观察圆如何映成椭圆或机翼形轮廓；原点是被排除的极点。',[control('radius','原圆半径',.7,1.5,.02,1.12),control('real','圆心实部',-.4,.3,.01,-.12),control('imag','圆心虚部',-.3,.3,.01,.05)]],
      schwarz:['F(z)=∫₀ᶻ(1−tᴺ)^(−2/N)dt；|z|<1','Schwarz–Christoffel 的正多边形代表：单位开圆映到正 N 边形内部。这里沿径向作 Simpson 数值积分，只画 r<1 的内层曲线，不把接近边界的数值图冒充精确边界。',[control('sides','多边形边数 N',3,8,1,4),control('radius','采样半径 r',.3,.98,.02,.94)]],
      power:['w=zⁿ；整数 n≥1','同心圆的模变为 rⁿ，辐角变为 nθ；随次数增加，可观察多重覆盖及单位根的位置。',[control('order','幂次数 n',1,8,1,3),control('extent','网格范围',.3,1.2,.05,.9),control('phase','标记点辐角',-3.14,3.14,.02,.6)]],
      unmatched:['尚未匹配专用模型','此条目尚无与教材条件对应的专用可操作图像；请结合原文阅读。',[]],
      commutative:['a+b = b+a；ab = ba；b = 1 + 0.5i','左图两条折线路径分别先加 a 再加 b、先加 b 再加 a；右图从 1 出发，按两种顺序乘 a、b，同色终点重合。',[control('real','Re a',-.9,1.5,.05,.8),control('imag','Im a',-.9,1.5,.05,.6)]],
      complex:['z = r(cos θ + i sin θ)','拖动辐角与模，比较复平面中的向量、实部与虚部。', [control('radius','模 r',0.1,2.5,.05,1.3),control('angle','辐角 θ',-3.14,3.14,.01,.7)]],
      addition:['w = z + a；z = 1 + i','蓝色为 z，金色为 a，绿色为和向量；虚线补齐平行四边形。',[control('real','Re a',-2,2,.05,.8),control('imag','Im a',-2,2,.05,.5)]],
      quotient:['w = z/a；a ≠ 0','除以 a 会把模除以 |a|，辐角减去 Arg a；a=0 时无定义。',[control('real','Re a',-1.8,1.8,.05,1),control('imag','Im a',-1.8,1.8,.05,1)]],
      conjugate:['w = z̄ = x − iy','共轭映射关于实轴反射，改变定向；它在复平面上不满足保形映照的解析条件。',[control('extent','网格范围',.3,1.5,.05,1),control('phase','标记点辐角',-3.14,3.14,.02,.8)]],
      sqrt:['w = √z = √r exp(i Arg(z)/2)','主值平方根取 −π < Arg z < π；负实轴的两侧分别映到虚轴的两侧。',[control('extent','外半径',.5,2.5,.05,1.8),control('phase','标记点辐角',-3.13,3.13,.01,1)]],
      sin:['w = sin z = sin x cosh y + i cos x sinh y','实轴上是熟悉的正弦函数，虚部方向引入双曲函数；蓝绿曲线表示对应网格。',[control('extent','网格范围',.3,1.6,.05,1),control('phase','标记点辐角',-3.14,3.14,.02,.6)]],
      cos:['w = cos z = cos x cosh y − i sin x sinh y','余弦的复值映照；网格在 sin z = 0 的临界点附近出现压缩。',[control('extent','网格范围',.3,1.6,.05,1),control('phase','标记点辐角',-3.14,3.14,.02,.6)]],
      sinh:['w = sinh z = sinh x cos y + i cosh x sin y','双曲正弦由 (eᶻ−e⁻ᶻ)/2 定义；实部沿实轴增长，虚部方向具有周期性。',[control('extent','网格范围',.3,1.6,.05,1),control('phase','标记点辐角',-3.14,3.14,.02,.6)]],
      cosh:['w = cosh z = cosh x cos y + i sinh x sin y','双曲余弦由 (eᶻ+e⁻ᶻ)/2 定义；z=0 处导数为零，局部映照会折叠。',[control('extent','网格范围',.3,1.6,.05,1),control('phase','标记点辐角',-3.14,3.14,.02,.6)]],
      multiply:['w = az；z = 1 + i','乘数 a 改变长度与辐角；蓝色原网格与绿色像网格对照。',[control('real','Re a',-1.8,1.8,.05,.8),control('imag','Im a',-1.8,1.8,.05,.6)]],
      square:['w = z²','左侧是 z 平面，右侧是 w 平面。同色曲线一一对应，圆的辐角加倍。',[control('extent','网格范围',.3,1.3,.05,.9),control('phase','标记点辐角',-3.14,3.14,.02,.6)]],
      exp:['w = exp(z) = eˣ(cos y + i sin y)','竖线变成同心圆弧，横线变成射线；沿虚轴平移 2π 后回到同一点。',[control('extent','实部范围',.3,1.4,.05,1),control('phase','标记点虚部',-6.28,6.28,.02,1)]],
      log:['w = Log z = ln|z| + i Arg z；−π < Arg z < π','展示主值支。负实轴两侧分开采样，不把分支跳跃连成一条连续曲线。',[control('extent','外半径',.5,2.5,.05,1.8),control('phase','标记点辐角',-3.13,3.13,.01,1)]],
      inverse:['w = 1/z；z ≠ 0','反演把近原点的位置送向远处；图中省略奇点附近的无界部分。',[control('extent','网格范围',.5,2,.05,1.5),control('phase','标记点辐角',-3.14,3.14,.02,.6)]],
      derivative:['f(z) = z²；f′(z₀) = 2z₀','在 z₀ 附近比较真实像网格（绿色）与一阶线性近似（金色）；缩小邻域观察误差。',[control('epsilon','邻域半径 ε',.02,.8,.02,.3),control('real','Re z₀',-.9,.9,.05,.7),control('imag','Im z₀',-.9,.9,.05,.4)]],
      neighborhood:['D(a, ε) = {z : |z−a| < ε}','圆盘模型用于观察中心、边界与邻域尺度；它是点集关系的示意，不能替代任意区域。',[control('radius','邻域半径 ε',.1,2,.05,1),control('real','中心 Re a',-1,1,.05,.3),control('imag','中心 Im a',-1,1,.05,.2)]],
      sphere:['P(z) = (2x, 2y, |z|²−1) / (1+|z|²)','球极投影把复平面的点送到单位球面；|z| 增大时像点趋近北极。',[control('real','Re z',-5,5,.05,1),control('imag','Im z',-5,5,.05,.5)]],
      essential:['w = exp(1/z)；z ≠ 0','以 exp(1/z) 演示本性奇点。沿不同方向接近零会出现增长、衰减与振荡；超出视野的无界曲线已截去。',[control('extent','采样范围',.2,1.5,.05,.8),control('phase','接近方向',-3.14,3.14,.02,.6)]],
      laurent:['f(z) = 1/[z(z−1)]；奇点 z=0,1','同一函数在 0<|z|<1 与 |z|>1 有不同的 Laurent 展开；拖动半径跨过 1，比较两组部分和。',[control('terms','截断阶数 n',1,24,1,5),control('radius','采样半径 r',.2,2,.02,.6)]],
      majorant:['Sₙ(z)=Σₖ₌₀ⁿ zᵏ/k!；|z|≤R','指数级数的正例：|zᵏ/k!|≤Rᵏ/k!，而 ΣRᵏ/k!=eᴿ 有限，故由比较判别法（M-test）在闭圆上一致收敛且绝对收敛。金线是 eᶻ，绿线是部分和。',[control('terms','截断次数 n',1,24,1,4),control('radius','闭圆半径 R',.2,2,.1,1),control('epsilon','统一误差阈值 ε',.001,.2,.001,.02)]],
      series:['Sₙ(z) = 1 + z + ⋯ + zⁿ；|z| < 1','以几何级数演示部分和与收敛圆。蓝色为输入圆，绿色为部分和，金色为 1/(1−z)。',[control('terms','最高次数 n',1,24,1,4),control('radius','采样半径 r',.1,1.3,.02,.7)]],
      telescoping:['Sₙ(z)=z+(z²−z)+⋯+(zⁿ−zⁿ⁻¹)=zⁿ；|z|≤b<1','闭小圆的一致收敛正例：部分和望远镜相消为zⁿ，统一误差sup|Sₙ|=bⁿ→0。各项也受可求和的(1+b)bᵏ⁻¹控制，因此可用比较判别法；这里没有把它替换成阶乘级数。',[control('terms','部分和次数 n',1,300,1,4),control('bound','闭圆半径 b<1',.2,.99,.01,.7),control('epsilon','统一误差阈值 ε',.05,.8,.05,.5)]],
      uniform:['Sₙ(z) = zⁿ；|z| < 1；x = |z|','沿单位开圆的半径画 |Sₙ|=xⁿ。b=1 时始终取 x<1，sup=1 但不取到；xₙ=2^(−1/n) 留在圆内，误差恒为 1/2。b<1 时观察闭小圆上的统一误差。',[control('terms','次数 n',1,300,1,4),control('bound','圆盘半径 b',.2,1,.01,1),control('epsilon','误差阈值 ε',.05,.8,.05,.5)]],
      contour:['f(z) = 1/(z−a)；γ(t) = R eⁱᵗ','逆时针圆周积分：极点在圆内为 2πi，在圆外为 0；落在路径上时积分无定义。',[control('radius','路径半径 R',.3,2,.05,1.2),control('pole','极点位置 a（实轴）',-2.5,2.5,.05,.4),control('phase','沿路径的位置',0,6.28,.02,1)]],
      harmonic:['u(x,y) = c(x²−y²)；v(x,y) = 2cxy','Re(cz²) 的鞍面；金色 u 等值线与紫色 v 等值线同时投影到公共平面。两族在梯度非零的交点正交；原点梯度为零。',[control('amplitude','系数 c',.2,1.4,.05,.65),control('level','实部等值线 u',-.8,.8,.05,.3),control('conjugateLevel','虚部等值线 v',-.8,.8,.05,.3)]],
      continuation:['f(z) = 1/(1−z) = Σ (z−a)ⁿ/(1−a)ⁿ⁺¹','不同中心的 Taylor 收敛圆可以重叠，解析函数沿重叠区域延伸；z=1 是不能跨越的极点。',[control('center','展开中心 a',-.8,.75,.05,.3),control('terms','部分和最高次数',1,20,1,5)]],
      mobius:['w = (z−a)/(1−az)；a ∈ (−1,1)','实参数 Möbius 变换保持单位圆；非极点处网格交角保持不变。',[control('real','变换参数 a',-.8,.8,.02,.4),control('phase','圆上标记点辐角',-3.14,3.14,.02,1)]],
      laplace:['f(t) = e⁻ᵃᵗ；F(s) = 1/(s+a)','青色为 f(t)，金色为被积函数 e⁻ˢᵗf(t)；Re(s+a)>0 才收敛。这里取实数 s。',[control('decay','衰减率 a',.1,2,.05,.7),control('sigma','实参数 s',-.8,2,.05,.6)]]
    };
    var c=configs[kind];
    var exact=/^(?:\s*(?:w\s*=\s*)?(?:z²|z\^2|1\s*\/\s*z|exp\(z\)|Log z))\s*$/.test(String(title||''));
    return {kind:kind,title:String(title||'数学关系'),formula:c[0],explanation:c[1],controls:c[2],illustrative:!exact,matchSource:matchSource,
      label:!identified?'未匹配专用模型':exact?'公式模型':'示意模型 · '+({series:'几何级数',contour:'单极点圆周积分',harmonic:'二次调和函数',derivative:'二次函数局部',laplace:'指数衰减',continuation:'有理函数开拓',mobius:'Möbius 变换'}[kind]||'可操作数学关系')};
  }
  function complexMul(a,b){return [a[0]*b[0]-a[1]*b[1],a[0]*b[1]+a[1]*b[0]];}
  function divide(a,b){var d=b[0]*b[0]+b[1]*b[1];return d<1e-10?[NaN,NaN]:[(a[0]*b[0]+a[1]*b[1])/d,(a[1]*b[0]-a[0]*b[1])/d];}
  // For f(z)=z² on a circle, the midpoint rule aliases a geometric tail.
  // Its absolute discretization error is at most |a|² q^N/(1-q^N),
  // q=min(|a|/R,R/|a|). Select N from this bound, not the expected answer.
  function cauchyCircleQuadrature(radius,a){
    var dist=Math.hypot(a[0],a[1]),gap=Math.abs(dist-radius),scale=Math.max(1,radius,dist);
    if(gap<=32*Number.EPSILON*scale)return {onContour:true,numerical:null,path:[],samples:0,estimatedError:null,converged:false};
    var ratio=dist===0?0:Math.min(dist/radius,radius/dist),count=128,maxCount=131072,tolerance=1e-9*Math.max(1,dist*dist);
    function aliasBound(n){if(ratio===0)return 0;var power=n*Math.log(ratio);return dist*dist*Math.exp(power)/(-Math.expm1(power));}
    while(count<maxCount&&aliasBound(count)>tolerance/4)count*=2;
    var sum=[0,0],compensation=[0,0],path=[[0,0]],absoluteSum=0,stride=Math.max(1,Math.ceil(count/256)),radius3=radius*radius*radius;
    for(var j=0;j<count;j++){
      var theta=TAU*(j+.5)/count,x=radius*Math.cos(theta),y=radius*Math.sin(theta),dx=x-a[0],dy=y-a[1],den=dx*dx+dy*dy;
      var nr=radius3*Math.cos(3*theta),ni=radius3*Math.sin(3*theta),term=[(nr*dx+ni*dy)/den/count,(ni*dx-nr*dy)/den/count];
      absoluteSum+=Math.hypot(term[0],term[1]);
      for(var axis=0;axis<2;axis++){var adjusted=term[axis]-compensation[axis],next=sum[axis]+adjusted;compensation[axis]=(next-sum[axis])-adjusted;sum[axis]=next;}
      if((j+1)%stride===0||j+1===count)path.push(sum.slice());
    }
    var discretizationBound=aliasBound(count),roundoffEstimate=64*Number.EPSILON*absoluteSum*(1+radius/gap),estimatedError=discretizationBound+roundoffEstimate;
    return {onContour:false,numerical:sum,path:path,samples:count,discretizationBound:discretizationBound,roundoffEstimate:roundoffEstimate,estimatedError:estimatedError,tolerance:tolerance,converged:Number.isFinite(estimatedError)&&estimatedError<=tolerance};
  }
  // Regular polygon disk formula: Harvard Math 113, Homework 8 (2024), problem 3.
  // https://people.math.harvard.edu/~siu/math113/8_homework_march28_2024.pdf
  function schwarzMap(x,y,n){var sum=[0,0],steps=40;for(var j=0;j<=steps;j++){var t=j/steps,r=Math.pow(Math.hypot(x*t,y*t),n),a=n*Math.atan2(y,x),u=1-r*Math.cos(a),v=-r*Math.sin(a),mag=Math.pow(Math.hypot(u,v),-2/n),arg=-2/n*Math.atan2(v,u),weight=j===0||j===steps?1:(j%2?4:2);sum[0]+=weight*mag*Math.cos(arg);sum[1]+=weight*mag*Math.sin(arg);}return complexMul([x,y],[sum[0]/(3*steps),sum[1]/(3*steps)]);}
  function createModel(scene){
    var model={scene:scene,params:{},view:{yaw:0,pitch:.18,zoom:1,panX:0,panY:0}};
    scene.controls.forEach(function(c){model.params[c.key]=c.value;});
    model.set=function(key,value){var c=scene.controls.find(function(item){return item.key===key;}),n=Number(value);if(!c||!Number.isFinite(n))return false;
      model.params[key]=Math.max(c.min,Math.min(c.max,Math.round(n/c.step)*c.step));
      // These decimal controls must preserve b=epsilon at the displayed value.
      if((scene.kind==='uniform'||scene.kind==='telescoping')&&(key==='bound'||key==='epsilon'))model.params[key]=Number(model.params[key].toFixed(2));
      return true;};
    model.map=function(x,y){var p=model.params,k=scene.kind;if(typeof scene.map==='function')return scene.map(x,y,p);
      if(k==='power'||k==='maximum'||k==='identity'||k==='zero'||k==='argument'){var coefficient=(k==='identity'||k==='zero')?p.amplitude:1,r=Math.pow(Math.hypot(x,y),p.order),angle=p.order*Math.atan2(y,x);var value=[coefficient*r*Math.cos(angle)+(k==='maximum'?p.shift:(k==='argument'?-p.shift:0)),coefficient*r*Math.sin(angle)];if(k==='argument'){var dr=Math.pow(Math.hypot(x,y),p.poleOrder),da=p.poleOrder*Math.atan2(y,x);return divide(value,[dr*Math.cos(da),dr*Math.sin(da)]);}return value;}
      if(k==='tan'||k==='cot'){var sin=[Math.sin(x)*Math.cosh(y),Math.cos(x)*Math.sinh(y)],cos=[Math.cos(x)*Math.cosh(y),-Math.sin(x)*Math.sinh(y)];return k==='tan'?divide(sin,cos):divide(cos,sin);}
      if(k==='arctan'){var q=divide([1-y,x],[1+y,-x]);return [Math.atan2(q[1],q[0])/2,-Math.log(Math.hypot(q[0],q[1]))/2];}
      if(k==='joukowski'){var q=divide([1,0],[x,y]);return [(x+q[0])/2,(y+q[1])/2];}
      if(k==='schwarz')return schwarzMap(x,y,p.sides);
      if(k==='uniform'||k==='telescoping'){var r=Math.pow(Math.hypot(x,y),p.terms),a=p.terms*Math.atan2(y,x);return [r*Math.cos(a),r*Math.sin(a)];}
      if(k==='majorant'){var sum=[1,0],term=[1,0];for(var j=1;j<=p.terms;j++){term=complexMul(term,[x/j,y/j]);sum[0]+=term[0];sum[1]+=term[1];}return sum;}
      if(k==='addition')return [x+p.real,y+p.imag];
      if(k==='quotient')return divide([x,y],[p.real,p.imag]);
      if(k==='conjugate')return [x,-y];
      if(k==='sin')return [Math.sin(x)*Math.cosh(y),Math.cos(x)*Math.sinh(y)];
      if(k==='cos')return [Math.cos(x)*Math.cosh(y),-Math.sin(x)*Math.sinh(y)];
      if(k==='hyperbolic'){var sh=[Math.sinh(x)*Math.cos(y),Math.cosh(x)*Math.sin(y)],ch=[Math.cosh(x)*Math.cos(y),Math.sinh(x)*Math.sin(y)];return p.mode===0?sh:p.mode===1?ch:p.mode===2?divide(sh,ch):divide(ch,sh);}
      if(k==='sinh')return [Math.sinh(x)*Math.cos(y),Math.cosh(x)*Math.sin(y)];
      if(k==='cosh')return [Math.cosh(x)*Math.cos(y),Math.sinh(x)*Math.sin(y)];
      if(k==='sqrt'){var r=Math.sqrt(Math.hypot(x,y)),a=Math.atan2(y,x)/2;return [r*Math.cos(a),r*Math.sin(a)];}
      if(k==='multiply')return complexMul([x,y],[p.real,p.imag]);
      if(k==='harmonic')return [p.amplitude*(x*x-y*y),2*p.amplitude*x*y];
      if(k==='square'||k==='derivative')return [x*x-y*y,2*x*y];
      if(k==='essential'){var q=divide([1,0],[x,y]);return [Math.exp(q[0])*Math.cos(q[1]),Math.exp(q[0])*Math.sin(q[1])];}
      if(k==='exp')return [Math.exp(x)*Math.cos(y),Math.exp(x)*Math.sin(y)];
      if(k==='log')return [Math.log(Math.hypot(x,y)),Math.atan2(y,x)];
      if(k==='inverse')return divide([1,0],[x,y]);
      if(k==='mobius')return divide([x-p.real,y],[1-p.real*x,-p.real*y]);
      return [x,y];};
    model.reset=function(){scene.controls.forEach(function(c){model.params[c.key]=c.value;});model.view={yaw:scene.initialYaw||0,pitch:scene.initialPitch!=null?scene.initialPitch:scene.kind==='sphere'||scene.kind==='harmonic'||scene.kind==='maximum'||scene.kind==='identity'||scene.kind==='zero'||scene.kind==='mean'?.6:.18,zoom:scene.initialZoom||1,panX:0,panY:0};};
    model.reset();return model;
  }
  function fmt(n){return Number.isFinite(n)?String(Math.round(n*1000)/1000):'无定义';}
  function inputNumber(n){return Number.isFinite(Number(n))?String(Number(Number(n).toPrecision(10))):String(n);}
  function complexTex(z){var real=fmt(z[0]),imag=fmt(Math.abs(z[1]));return imag==='0'?real:real+(z[1]<0?' - ':' + ')+imag+'i';}
  function scientificTex(n){return Number.isFinite(n)?n.toExponential(2).replace(/e([+-]?\d+)$/,'\\times10^{$1}'):'\\infty';}
  function geometry(model){
    for(var extension of extensions){if(typeof extension.geometry==='function'){var extended=extension.geometry(model);if(extended)return extended;}}
    var p=model.params,k=model.scene.kind,out={lines:[],points:[],labels:[],readout:'',surfaces:[]};
    function line(points,color,opacity){out.lines.push({points:points,color:color||TEAL,opacity:opacity==null?1:opacity});}
    function sample(f,a,b,n){var r=[];for(var i=0;i<=(n||80);i++){var t=a+(b-a)*i/(n||80);r.push(f(t));}return r;}
    function pt(x,y,z){return [x,y,z||0];}
    function circle(cx,cy,r,z,color){line(sample(function(t){return [cx+r*Math.cos(t),cy+r*Math.sin(t),z||0];},0,TAU,100),color);}
    function axes(cx,scale){var s=scale||1;line([[cx-2.1*s,0,0],[cx+2.1*s,0,0]],'#536781',.75);line([[cx,-2*s,0],[cx,2*s,0]],'#536781',.75);}
    function grid(cx){for(var i=-4;i<=4;i++){var q=i*.4;line([[cx-1.6,q,0],[cx+1.6,q,0]],'#29384d',.6);line([[cx+q,-1.6,0],[cx+q,1.6,0]],'#29384d',.6);}axes(cx,.9);}
    function mappedCurve(points,left,right){var a=[],b=[];points.forEach(function(z){var w=model.map(z[0],z[1]);a.push([z[0]+left,z[1],0]);b.push([w[0]+right,w[1],0]);});line(a,BLUE,.7);line(b,TEAL,.95);}
    function segment(a,b,color){line([pt(a[0],a[1]),pt(b[0],b[1])],color);}
    function geometricSum(z,n){var sum=[1,0],term=[1,0];for(var j=1;j<=n;j++){term=complexMul(term,z);sum[0]+=term[0];sum[1]+=term[1];}return sum;}
    if(k==='exp'){
      // Keep actual inputs and images separate from their disclosed plot scales.
      // The full phase range and its +2π partner fit a narrow default viewport.
      var expExtent=p.extent,expLeft=-1.15,expRight=1.15,inputXScale=.7/expExtent,inputYScale=.75/TAU,imageScale=.75/Math.exp(expExtent);
      function expSourcePosition(z){return [expLeft+inputXScale*z[0],inputYScale*z[1],0];}
      function expImagePosition(w){return [expRight+imageScale*w[0],imageScale*w[1],0];}
      function expMappedCurve(points){
        line(points.map(expSourcePosition),BLUE,.38);
        line(points.map(function(z){return expImagePosition(model.map(z[0],z[1]));}),TEAL,.5);
      }
      line([[expLeft-.8,0,0],[expLeft+.8,0,0]],'#536781',.8);
      line([[expLeft,-.85,0],[expLeft,1.6,0]],'#536781',.8);
      line([[expRight-.9,0,0],[expRight+.9,0,0]],'#536781',.8);
      line([[expRight,-.9,0],[expRight,.9,0]],'#536781',.8);
      for(var expColumn=-3;expColumn<=3;expColumn++){
        var real=expColumn*expExtent/3;
        expMappedCurve(sample(function(y){return [real,y];},-TAU,2*TAU,180));
      }
      for(var expRow=-6;expRow<=12;expRow++){
        var imaginary=expRow*TAU/6;
        expMappedCurve(sample(function(x){return [x,imaginary];},-expExtent,expExtent,36));
      }
      var input=[.8*expExtent,p.phase],partner=[input[0],input[1]+TAU];
      var image=model.map(input[0],input[1]),partnerImage=model.map(partner[0],partner[1]);
      var inputPosition=expSourcePosition(input),partnerPosition=expSourcePosition(partner),imagePosition=expImagePosition(image);
      line([inputPosition,partnerPosition],GOLD,.9);
      out.points.push({position:[inputPosition[0],inputPosition[1],.04],color:GOLD,role:'source-z'},
        {position:[imagePosition[0],imagePosition[1],.04],color:GOLD,role:'common-image'},
        {position:[partnerPosition[0],partnerPosition[1],.04],color:PINK,role:'source-period-shift'});
      out.lines.push({points:sample(function(t){return [imagePosition[0]+.11*Math.cos(t),imagePosition[1]+.11*Math.sin(t),.05];},0,TAU,48),color:PINK,opacity:1,role:'common-image-ring'});
      out.facts={periodPair:{inputs:[input,partner],images:[image,partnerImage],displayInputs:[inputPosition,partnerPosition],displayImage:imagePosition},
        displayScaling:{inputOrigin:[expLeft,0],inputX:inputXScale,inputY:inputYScale,imageOrigin:[expRight,0],image:imageScale}};
      out.labels=[['输入：$x/a,\ y/(2\\pi)$',.25],['共同像：$w/e^a$',.75]];
      out.readout='金点 $z='+complexTex(input)+'$；紫点 $z+2\\pi i='+complexTex(partner)+'$；共同的实际像 $w='+complexTex(image)+'$。左图纵坐标除以 $2\\pi$，右图像坐标除以 $e^a$，仅缩放显示。';
    } else if(['square','exp','log','inverse','mobius','multiply','quotient','conjugate','sqrt','sin','cos','sinh','cosh','essential','power','tan','cot','arctan','hyperbolic'].indexOf(k)>=0){
      var L=-2.65,R=2.65,e=p.extent||1;grid(L);grid(R);out.labels=[['z 平面 · 原像',.25],['w 平面 · 像',.75]];
      if(k==='log'||k==='sqrt') {
        for(var ring=.3;ring<=e;ring+=.3)mappedCurve(sample(function(t){return [ring*Math.cos(t),ring*Math.sin(t)];},-Math.PI+.025,Math.PI-.025,110),L,R);
        for(var a=-2.8;a<=2.81;a+=.7)mappedCurve(sample(function(r){return [r*Math.cos(a),r*Math.sin(a)];},.2,e,55),L,R);
      } else {
        for(var j=-3;j<=3;j++){
          var q=j*e/3,span=k==='exp'?2.6:e;
          mappedCurve(sample(function(t){return [q,t];},-span,span,90),L,R);
          mappedCurve(sample(function(t){return [t,q];},-e,e,90),L,R);
        }
        mappedCurve(sample(function(t){return [.8*Math.cos(t),.8*Math.sin(t)];},0,TAU,100),L,R);
      }
      var phase=p.phase==null?.6:p.phase,markerRadius=k==='essential'?.8*e:.8;
      var z=k==='exp'?[.8*e,phase]:[markerRadius*Math.cos(phase),markerRadius*Math.sin(phase)],w=model.map(z[0],z[1]);
      out.points.push({position:[z[0]+L,z[1],.04],color:GOLD},{position:[w[0]+R,w[1],.04],color:GOLD});
      out.readout='z = '+fmt(z[0])+' + ('+fmt(z[1])+')i → w = '+fmt(w[0])+' + ('+fmt(w[1])+')i';
      if(k==='essential'){
        // Keep the actual image in points[1]. This separate cue belongs to the
        // finite right-hand grid; its endpoint is never presented as w.
        var windowHalf=1.6,outside=Math.max(Math.abs(w[0]),Math.abs(w[1]))>windowHalf;
        out.facts={actualImage:w.slice(),offscreenImage:outside,displayWindow:{origin:[R,0],halfWidth:windowHalf,halfHeight:windowHalf}};
        if(outside&&w.every(Number.isFinite)){
          var distance=Math.hypot(w[0],w[1]),direction=[w[0]/distance,w[1]/distance],edgeScale=(windowHalf-.12)/Math.max(Math.abs(w[0]),Math.abs(w[1]));
          var tip=[R+w[0]*edgeScale,w[1]*edgeScale,0],base=[tip[0]-.55*direction[0],tip[1]-.55*direction[1],0];
          [[base,tip],[tip,[tip[0]-.22*direction[0]-.15*direction[1],tip[1]-.22*direction[1]+.15*direction[0],0]],
           [tip,[tip[0]-.22*direction[0]+.15*direction[1],tip[1]-.22*direction[1]-.15*direction[0],0]]].forEach(function(points){out.lines.push({points:points,color:GOLD,opacity:1,role:'offscreen-direction'});});
          out.facts.offscreenDirection={unit:direction,tip:tip.slice(),representsActualImage:false};
          out.labels[1]=['像 · 金箭头为超窗方向；实际值见读数',.75];
          out.readout+='；像点超出右图有限显示窗，金箭头只表示超窗方向，非实际像点。';
        }
      }
      if(k==='hyperbolic'){var sh=[Math.sinh(z[0])*Math.cos(z[1]),Math.cosh(z[0])*Math.sin(z[1])],ch=[Math.cosh(z[0])*Math.cos(z[1]),Math.sinh(z[0])*Math.sin(z[1])],d=p.mode===0?ch:p.mode===1?sh:p.mode===2?divide([1,0],complexMul(ch,ch)):divide([-1,0],complexMul(sh,sh));out.readout=['sinh','cosh','tanh','coth'][p.mode]+'：'+out.readout+'；f′(z)='+fmt(d[0])+'+('+fmt(d[1])+')i。';if(p.mode>=2){var poles=p.mode===2?[Math.PI/2,-Math.PI/2]:[0];poles.forEach(function(y){out.points.push({position:[L,y,.05],color:PINK});});}}
    } else if(k==='cauchy'){
      grid(-2.65);grid(2.65);circle(-2.65,0,p.radius,0,BLUE);out.points.push({position:[-2.65+p.real,p.imag,.05],color:GOLD});
      var integral=cauchyCircleQuadrature(p.radius,[p.real,p.imag]),dist=Math.hypot(p.real,p.imag),inside=dist<p.radius,value=integral.onContour?null:inside?[p.real*p.real-p.imag*p.imag,2*p.real*p.imag]:[0,0];
      if(!integral.onContour){line(integral.path.map(function(q){return [2.65+q[0],q[1],0];}),TEAL);out.points.push({position:[2.65+value[0],value[1],.05],color:GOLD});}
      out.facts={inside:inside,value:value,numerical:integral.numerical,onContour:integral.onContour,samples:integral.samples,estimatedError:integral.estimatedError,discretizationBound:integral.discretizationBound,roundoffEstimate:integral.roundoffEstimate,converged:integral.converged};out.labels=[['积分圆γ与取值点a',.25],['归一化积分的累加路径',.75]];
      out.readout=integral.onContour?'a位于积分路径上：核函数奇异，普通曲线积分没有定义。':'归一化积分约为 $'+complexTex(integral.numerical)+'$；'+(inside?'$f(a)=a^2='+complexTex(value)+'$':'取值点在圆外：积分为 $0$，不能据此读出 $f(a)$。')+'；自适应中点采样 '+integral.samples+' 点，误差估计 $'+scientificTex(integral.estimatedError)+'$（几何尾界＋浮点估计）'+(integral.converged?'。':'；尚未达到目标精度，请将取值点移离边界。');
    } else if(k==='circle-differential'){
      grid(0);circle(0,0,p.radius,0,BLUE);var t=p.angle,z=[p.radius*Math.cos(t),p.radius*Math.sin(t)],dz=[-z[1],z[0]],next=[p.radius*Math.cos(t+p.delta),p.radius*Math.sin(t+p.delta)];segment([0,0],z,BLUE);segment(z,[z[0]+dz[0]*p.delta,z[1]+dz[1]*p.delta],GOLD);segment(z,[z[0]+z[0]*p.delta,z[1]+z[1]*p.delta],PINK);out.points.push({position:[z[0],z[1],.04],color:GOLD},{position:[next[0],next[1],.04],color:TEAL});out.facts={derivative:dz,radial:z};out.readout='dz/dθ='+fmt(dz[0])+'+('+fmt(dz[1])+')i；|dz/dθ|=R='+fmt(p.radius)+'；i保证切向方向。';
    } else if(k==='arc-decay'){
      grid(-2.65);grid(2.65);line(sample(function(t){return [-2.65+1.4*Math.cos(t),1.4*Math.sin(t),0];},0,Math.PI,90),BLUE);var q=p.degree,R=p.radius,bound=Math.PI*Math.pow(R,1-q),integral=q===1?[0,Math.PI]:[Math.pow(R,1-q)*(Math.cos((1-q)*Math.PI)-1)/(1-q),Math.pow(R,1-q)*Math.sin((1-q)*Math.PI)/(1-q)];line([[2.65,0,0],[2.65+integral[0],integral[1],0]],TEAL);circle(2.65,0,bound,0,GOLD);out.facts={integral:integral,bound:bound};out.labels=[['原像半圆（显示z/R）',.25],['绿色积分向量，金色ML界',.75]];out.readout='q='+q+'，R='+fmt(R)+'：|积分|='+fmt(Math.hypot(integral[0],integral[1]))+' ≤ πR¹⁻ᑫ='+fmt(bound)+(q===1?'；q=1时不随R趋零。':'；R→∞时趋零。');
    } else if(k==='curve'){
      grid(0);function path(t){return p.mode===1?[Math.sin(t),Math.sin(2*t),0]:[Math.cos(t),Math.sin(t),0];}line(sample(path,0,TAU,200),TEAL);var z=path(p.phase),next=path(p.phase+.12);out.points.push({position:z,color:GOLD});line([z,next],GOLD);if(p.mode===1)out.points.push({position:[0,0,.04],color:PINK});out.facts={selfIntersecting:p.mode===1,crossingA:p.mode===1?[0,0]:[1,0],crossingB:p.mode===1?[0,0]:[-1,0]};out.readout=p.mode===1?'8字曲线：z(0)=z(π)=0，存在不同内参数的重点，不是简单曲线。':'单位圆：除首尾外参数点不重复，是简单闭曲线；金点表示参数方向。';
    } else if(k==='trig-identity'){
      grid(0);var x=p.real,y=p.imag,s=[Math.sin(x)*Math.cosh(y),Math.cos(x)*Math.sinh(y)],c=[Math.cos(x)*Math.cosh(y),-Math.sin(x)*Math.sinh(y)],s2=complexMul(s,s),c2=complexMul(c,c),sum=[s2[0]+c2[0],s2[1]+c2[1]];segment([0,0],c2,BLUE);segment(c2,sum,GOLD);segment([0,0],s2,TEAL);segment(s2,sum,BLUE);out.points.push({position:[sum[0],sum[1],.05],color:GOLD});out.facts={sum:sum};out.readout='cos²z='+c2.map(fmt).join(', ')+'；sin²z='+s2.map(fmt).join(', ')+'；和='+sum.map(fmt).join(', ')+'。';
    } else if(k==='euler-trig'||k==='half-angle'){
      grid(0);circle(0,0,1,0,BLUE);var co=Math.cos(p.angle),si=Math.sin(p.angle);out.points.push({position:[co,si,.03],color:TEAL});segment([0,0],[co,si],TEAL);
      if(k==='euler-trig'){out.points.push({position:[co,-si,.03],color:BLUE});segment([co,-si],[co,si],GOLD);segment([0,0],[0,2*si],GOLD);segment([0,0],[si,0],TEAL);out.points.push({position:[0,si,.05],color:PINK});out.facts={sine:si,wrong:[0,si]};out.readout='sinθ='+fmt(si)+'（实轴绿色）；误除以2得到 '+fmt(si)+'i（粉色），不是sinθ。';}
      else{segment([0,0],[1+co,si],GOLD);segment([0,0],[1-co,-si],TEAL);out.facts={plus:2+2*co,minus:2-2*co};out.readout='加号：'+fmt(2+2*co)+'=4cos²(θ/2)；减号：'+fmt(2-2*co)+'=4sin²(θ/2)。';}
    } else if(k==='mean'){
      grid(0);var a=[p.real,p.imag],value=[a[0]*a[0]-a[1]*a[1],2*a[0]*a[1]];circle(a[0],a[1],p.radius,-.7,BLUE);line(sample(function(t){var x=a[0]+p.radius*Math.cos(t),y=a[1]+p.radius*Math.sin(t);return [x,y,.4*(x*x-y*y)];},0,TAU,120),TEAL);for(var j=0;j<16;j++){var t=TAU*j/16,x=a[0]+p.radius*Math.cos(t),y=a[1]+p.radius*Math.sin(t);line([[x,y,-.7],[x,y,.4*(x*x-y*y)]],'#365b72',.55);}out.points.push({position:[a[0],a[1],.4*value[0]],color:GOLD});out.facts={average:value,centerValue:value};out.readout='复平均=f(a)='+fmt(value[0])+'+('+fmt(value[1])+')i；圆周长2πR='+fmt(TAU*p.radius)+'，ds=R dθ。';
    } else if(k==='argument'){
      grid(-2.65);grid(2.65);mappedCurve(sample(function(t){return [p.radius*Math.cos(t),p.radius*Math.sin(t)];},0,TAU,180),-2.65,2.65);out.points.push({position:[2.65,0,.05],color:PINK});var boundary=Math.pow(p.radius,p.order),delta=p.shift-boundary,zeros=p.shift===0?Math.max(p.order-p.poleOrder,0):(delta<0?p.order:0),poles=p.shift===0?Math.max(p.poleOrder-p.order,0):p.poleOrder,winding=zeros-poles;out.facts={winding:Math.abs(delta)<1e-8?null:winding,zeros:zeros,poles:poles};out.labels=[['原像：逆时针闭圆',.25],['像：绕原点的圈数',.75]];out.readout=Math.abs(delta)<1e-8?'像曲线经过原点：闭路上有零点，计数公式的条件失效。':'Δarg f='+winding+'×2π；N='+zeros+'，P='+poles+'，N−P='+winding+'（按重数计）。';
    } else if(k==='slit-endpoint'){
      axes(0);line([[-3,Math.PI/2,0],[-.5,Math.PI/2,0]],TEAL);out.points.push({position:[-.5,Math.PI/2,.04],color:TEAL},{position:[p.endpoint/2,Math.PI/2,.06],color:GOLD});line([[-.5,Math.PI/2,.03],[p.endpoint/2,Math.PI/2,.03]],PINK);out.readout='图按1/2缩放显示。候选端点 '+fmt(p.endpoint)+'+πi；与 w(−1)=−1+πi 的实部差='+fmt(p.endpoint+1)+(p.endpoint===-1?'，一致。':'，不一致。');
    } else if(k==='associative'||k==='distributive'){
      var a=[p.real,p.imag],b=k==='associative'?[.6,.7]:[.7,.5],c=k==='associative'?[-.5,.4]:[.3,-.7],plus=function(a,b){return [a[0]+b[0],a[1]+b[1]];},round=function(a){return a.map(function(v){return Math.round(v*1e12)/1e12;});};
      grid(-2.65);grid(2.65);function drawPath(path,offset,color,z){line(path.map(function(q){return [q[0]+offset,q[1],z||0];}),color);path.slice(1).forEach(function(q){out.points.push({position:[q[0]+offset,q[1],z||0],color:color});});}
      if(k==='associative'){var sum=plus(plus(a,b),c),product=complexMul(complexMul(a,b),c);drawPath([[0,0],plus(a,b),sum],-2.65,BLUE,.02);drawPath([[0,0],a,plus(a,plus(b,c))],-2.65,TEAL,.05);drawPath([[1,0],complexMul(a,b),product],2.65,BLUE,.02);drawPath([[1,0],a,complexMul(a,complexMul(b,c))],2.65,TEAL,.05);out.facts={left:round(product),right:round(complexMul(a,complexMul(b,c))),additionLeft:round(sum),additionRight:round(plus(a,plus(b,c)))};out.labels=[['加法：两种括号',.25],['乘法：两种括号',.75]];out.readout='加法终点 '+sum.map(fmt).join(', ')+'；乘法终点 '+product.map(fmt).join(', ')+'；中间结果不同，最终结果一致。';}
      else{var bc=plus(b,c),ab=complexMul(a,b),ac=complexMul(a,c),left=complexMul(a,bc),right=plus(ab,ac);drawPath([[0,0],b,bc],-2.65,BLUE);drawPath([[0,0],ab,right],2.65,BLUE,.02);drawPath([[0,0],left],2.65,TEAL,.05);out.facts={left:round(left),right:round(right)};out.labels=[['先求 b+c',.25],['乘法分配：a(b+c)=ab+ac',.75]];out.readout='a(b+c)=ab+ac='+fmt(left[0])+' + ('+fmt(left[1])+')i';}
    } else if(k==='maximum'||k==='identity'||k==='zero'){
      var R=p.radius,n=p.order,c=p.shift||0,A=1;
      for(var ri=1;ri<=10;ri++){var radius=R*ri/10;line(sample(function(t){var z=[radius*Math.cos(t),radius*Math.sin(t)],w=model.map(z[0],z[1]);return [z[0],z[1],.65*A*Math.hypot(w[0],w[1])-.5];},0,TAU,90),ri===10?GOLD:TEAL,ri===10?1:.45);}
      for(var ti=0;ti<12;ti++){var theta=TAU*ti/12;line(sample(function(r){var z=[r*Math.cos(theta),r*Math.sin(theta)],w=model.map(z[0],z[1]);return [z[0],z[1],.65*A*Math.hypot(w[0],w[1])-.5];},0,R,45),BLUE,.45);}
      if(k==='maximum'){out.facts={boundaryMaximum:Math.pow(R,n)+Math.abs(c),interiorSampleMaximum:Math.pow(.9*R,n)+Math.abs(c)};out.readout='边界最大模 Rⁿ+|c|='+fmt(out.facts.boundaryMaximum)+'；内层0.9R上的最大模='+fmt(out.facts.interiorSampleMaximum)+'。';}
      else if(k==='zero'){var factorial=1;for(var j=2;j<=n;j++)factorial*=j;out.facts={zeroOrder:n,mthDerivative:factorial*p.amplitude};out.readout='z=0为'+n+'阶零点；第'+n+'阶导数=m!a='+fmt(factorial*p.amplitude)+'≠0。';}
      else{var seq=[];for(var j=1;j<=10;j++){var x=R/j,value=p.amplitude*Math.pow(x,n);seq.push({x:x,value:value});out.points.push({position:[x,0,.65*value-.5],color:GOLD});}out.facts={identicallyZero:p.amplitude===0,sequence:seq};out.readout=p.amplitude===0?'a=0：h≡0，所有点都是零点。':'a≠0：零点只有 z=0；金点 h(R/k)≠0，仅仅趋近0，不满足零点列聚点条件。';}
    } else if(k==='liouville'){
      grid(-2.65);grid(2.65);function xpos(r,offset){return offset+(r-.5)/9.5*3-1.5;}
      line(sample(function(r){return [xpos(r,-2.65),.35*p.amplitude*r-1,0];},.5,10,80),BLUE);line([[xpos(.5,-2.65),.35*p.bound-1,0],[xpos(10,-2.65),.35*p.bound-1,0]],GOLD);
      line(sample(function(r){return [xpos(r,2.65),.65*p.bound/r-1,0];},.5,10,100),TEAL);out.points.push({position:[xpos(p.radius,-2.65),.35*p.amplitude*p.radius-1,.04],color:PINK},{position:[xpos(p.radius,2.65),.65*p.bound/p.radius-1,.04],color:GOLD});out.labels=[['f(z)=az：蓝线 aR，金线 M',.25],['固定全局上界时：M/R → 0',.75]];
      out.facts={derivativeBound:p.bound/p.radius,globalBoundViolated:p.amplitude*p.radius>p.bound};out.readout='R='+fmt(p.radius)+'：M/R='+fmt(p.bound/p.radius)+'；代表函数最大模 aR='+fmt(p.amplitude*p.radius)+(out.facts.globalBoundViolated?' 已超过M，不能满足全局有界假设。':'；继续增大R检查全局上界。');
    } else if(k==='joukowski'||k==='schwarz'){
      grid(-2.65);grid(2.65);out.labels=[['z 平面 · 原像',.25],['w 平面 · 像',.75]];
      if(k==='tan'||k==='cot'){var sin=[Math.sin(x)*Math.cosh(y),Math.cos(x)*Math.sinh(y)],cos=[Math.cos(x)*Math.cosh(y),-Math.sin(x)*Math.sinh(y)];return k==='tan'?divide(sin,cos):divide(cos,sin);}
      if(k==='arctan'){var q=divide([1-y,x],[1+y,-x]);return [Math.atan2(q[1],q[0])/2,-Math.log(Math.hypot(q[0],q[1]))/2];}
      if(k==='joukowski'){for(var ri=0;ri<5;ri++){var r=p.radius+ri*.14;mappedCurve(sample(function(t){return [p.real+r*Math.cos(t),p.imag+r*Math.sin(t)];},0,TAU,130),-2.65,2.65);}out.readout='圆心 '+fmt(p.real)+'+('+fmt(p.imag)+')i，半径 '+fmt(p.radius)+'；w=(z+1/z)/2。';}
      else{for(var ri=1;ri<=5;ri++){var r=p.radius*ri/5;mappedCurve(sample(function(t){return [r*Math.cos(t),r*Math.sin(t)];},0,TAU,100),-2.65,2.65);}for(var j=0;j<2*p.sides;j++){var a=TAU*j/(2*p.sides);mappedCurve(sample(function(r){return [r*Math.cos(a),r*Math.sin(a)];},0,p.radius,35),-2.65,2.65);}out.facts={interiorAngle:Math.PI*(p.sides-2)/p.sides};out.readout='正'+p.sides+'边形：内角 '+fmt((p.sides-2)*180/p.sides)+'°；采样半径r='+fmt(p.radius)+'<1，数值积分40区间。';}
    } else if(k==='commutative'){
      var a=[p.real,p.imag],b=[1,.5],sum=[a[0]+b[0],a[1]+b[1]],ab=complexMul(a,b),ba=complexMul(b,a);
      var addAB=[[0,0],a,sum],addBA=[[0,0],b,[b[0]+a[0],b[1]+a[1]]],mulAB=[[1,0],a,ab],mulBA=[[1,0],b,ba];
      grid(-2.65);grid(2.65);out.labels=[['加法：先 a 后 b / 先 b 后 a',.25],['乘法：1→a→ab / 1→b→ba',.75]];
      function orderPath(path,offset,color,z){line(path.map(function(q){return [offset+q[0],q[1],z];}),color);path.slice(1).forEach(function(q){out.points.push({position:[offset+q[0],q[1],z],color:color});});}
      orderPath(addAB,-2.65,BLUE,.02);orderPath(addBA,-2.65,TEAL,.05);orderPath(mulAB,2.65,BLUE,.02);orderPath(mulBA,2.65,TEAL,.05);
      circle(-2.65+sum[0],sum[1],.13,.07,GOLD);circle(2.65+ab[0],ab[1],.13,.07,GOLD);
      out.facts={addAB:sum,addBA:addBA[2],multiplyAB:ab,multiplyBA:ba,paths:[addAB,addBA,mulAB,mulBA]};
      out.readout='a+b = b+a = '+fmt(sum[0])+' + ('+fmt(sum[1])+')i；ab = ba = '+fmt(ab[0])+' + ('+fmt(ab[1])+')i；金环为共同终点。';
    } else if(k==='complex'||k==='addition'){
      grid(0);var z0=k==='complex'?[p.radius*Math.cos(p.angle),p.radius*Math.sin(p.angle)]:[1,1];
      segment([0,0],z0,BLUE);line([[z0[0],0,0],pt(z0[0],z0[1]),[0,z0[1],0]],'#52667f',.8);
      circle(0,0,Math.hypot(z0[0],z0[1]),0,'#344762');
      if(k==='addition'){var a0=[p.real,p.imag],w0=[1+p.real,1+p.imag];segment([0,0],a0,GOLD);segment(z0,w0,GOLD);segment(a0,w0,BLUE);segment([0,0],w0,TEAL);out.points.push({position:pt(w0[0],w0[1],.04),color:TEAL});out.readout='z+a = '+fmt(w0[0])+' + ('+fmt(w0[1])+')i';}
      else{out.points.push({position:pt(z0[0],z0[1],.04),color:TEAL});out.readout='Re z = '+fmt(z0[0])+'；Im z = '+fmt(z0[1])+'；|z| = '+fmt(p.radius);}
    } else if(k==='derivative'){
      var l=-2.5,r=2.5,zbase=[p.real,p.imag],fbase=model.map(zbase[0],zbase[1]);grid(l);grid(r);out.labels=[['z₀ 附近 · 放大显示',.25],['绿色：真实像 / 金色：线性近似',.75]];
      for(var m=-3;m<=3;m++)[0,1].forEach(function(axis){var source=[],actual=[],linear=[];
        for(var i=0;i<=45;i++){var u=-1+2*i/45,dx=(axis?m/3:u)*p.epsilon,dy=(axis?u:m/3)*p.epsilon,actualPoint=model.map(p.real+dx,p.imag+dy),lin=complexMul([dx,dy],[2*p.real,2*p.imag]);
          source.push([l+dx/p.epsilon,dy/p.epsilon,0]);actual.push([r+(actualPoint[0]-fbase[0])/p.epsilon,(actualPoint[1]-fbase[1])/p.epsilon,0]);linear.push([r+lin[0]/p.epsilon,lin[1]/p.epsilon,.02]);}
        line(source,BLUE,.6);line(actual,TEAL);line(linear,GOLD,.5);
      });
      out.readout='f′(z₀) = '+fmt(2*p.real)+' + ('+fmt(2*p.imag)+')i；边界误差 ≤ '+fmt(2*p.epsilon*p.epsilon);
    } else if(k==='neighborhood'){
      grid(0);for(var rad=.1;rad<=p.radius;rad+=.08)circle(p.real,p.imag,rad,0,rad+.08>=p.radius?TEAL:'#244c50');circle(p.real,p.imag,p.radius,.01,TEAL);
      out.points.push({position:[p.real,p.imag,.04],color:GOLD});segment([p.real,p.imag],[p.real+p.radius,p.imag],GOLD);
      out.readout='中心 a = '+fmt(p.real)+' + ('+fmt(p.imag)+')i；边界 |z−a| = '+fmt(p.radius);
    } else if(k==='sphere'){
      for(var lat=-1.3;lat<=1.31;lat+=.26)circle(0,0,Math.cos(lat),Math.sin(lat),TEAL);
      for(var lon=0;lon<Math.PI;lon+=Math.PI/10)line(sample(function(t){return [Math.cos(t)*Math.cos(lon),Math.cos(t)*Math.sin(lon),Math.sin(t)];},0,TAU,70),'#436b92',.8);
      var d=1+p.real*p.real+p.imag*p.imag,P=[2*p.real/d,2*p.imag/d,(d-2)/d];
      line([[0,0,1],P,[p.real,p.imag,-1]],GOLD,.9);out.points.push({position:P,color:GOLD},{position:[0,0,1],color:PINK});
      out.readout='球面点 P = ('+P.map(fmt).join(', ')+')；北极对应 ∞';
    } else if(k==='majorant'){
      var R=p.radius,scale=1.6/Math.exp(R),tail=Math.exp(R);for(var j=1;j<=p.terms+1;j++)tail*=R/j;
      grid(-2.65);grid(2.65);out.labels=[['z 平面：闭圆 |z|≤R',.25],['像平面（同屏缩放系数 '+fmt(scale)+'）',.75]];circle(-2.65,0,R,0,BLUE);
      function expSum(x,y){return model.map(x,y);}
      for(var ri=1;ri<=4;ri++){var radius=R*ri/4;line(sample(function(t){var z=[radius*Math.cos(t),radius*Math.sin(t)],w=expSum(z[0],z[1]);return [2.65+scale*w[0],scale*w[1],0];},0,TAU,100),TEAL,.6);}
      line(sample(function(t){var x=R*Math.cos(t),y=R*Math.sin(t);return [2.65+scale*Math.exp(x)*Math.cos(y),scale*Math.exp(x)*Math.sin(y),.03];},0,TAU,120),GOLD);
      out.facts={tailBound:tail,uniformlyConvergent:true,majorantSum:Math.exp(R)};out.readout='sup|eᶻ−Sₙ(z)| ≤ eᴿRⁿ⁺¹/(n+1)! = '+fmt(tail)+(tail<p.epsilon?' < ε：闭圆上全部点均满足误差要求。':' ≥ ε：增大 n 可统一压低误差。');
    } else if(k==='series'){
      axes(0);circle(0,0,1,0,'#526a91');circle(0,0,p.radius,.01,BLUE);
      line(sample(function(t){var z=[p.radius*Math.cos(t),p.radius*Math.sin(t)],s=geometricSum(z,p.terms);return pt(s[0],s[1]);},0,TAU,160),TEAL);
      line(sample(function(t){var z=[p.radius*Math.cos(t),p.radius*Math.sin(t)],s=divide([1,0],[1-z[0],-z[1]]);return pt(s[0],s[1],.03);},.025,TAU-.025,160),GOLD,.6);
      out.readout=p.radius<1?'|Rₙ| ≤ rⁿ⁺¹/(1−r) = '+fmt(Math.pow(p.radius,p.terms+1)/(1-p.radius)):'r ≥ 1：几何级数在此圆上不收敛；曲线为有限部分和。';
    } else if(k==='laurent'){
      axes(0);circle(0,0,1,0,'#526a91');circle(0,0,p.radius,.01,BLUE);
      line(sample(function(t){var z=[p.radius*Math.cos(t),p.radius*Math.sin(t)],s;
        if(p.radius<1){s=geometricSum(z,p.terms+1);s=divide([-s[0],-s[1]],z);}
        else {var inv=divide([1,0],z);s=complexMul(complexMul(inv,inv),geometricSum(inv,p.terms));}
        return pt(s[0],s[1]);},.025,TAU-.025,170),TEAL);
      line(sample(function(t){var z=[p.radius*Math.cos(t),p.radius*Math.sin(t)],s=divide([1,0],complexMul(z,[z[0]-1,z[1]]));return pt(s[0],s[1],.03);},.025,TAU-.025,170),GOLD,.6);
      out.readout=p.radius<1?'内圆环 0 < |z| < 1：−1/z − 1 − z − ⋯ − zⁿ':(p.radius>1?'外圆环 |z| > 1：1/z² + 1/z³ + ⋯ + 1/zⁿ⁺²':'r=1 为收敛边界；圆上含奇点，展开不收敛。');
    } else if(k==='uniform'||k==='telescoping'){
      var isFull=p.bound===1,upper=isFull?1-1e-5:p.bound,witness=Math.pow(.5,1/p.terms),sup=isFull?1:Math.pow(p.bound,p.terms),minimumTerms=minimumUniformTerms(p.bound,p.epsilon);
      line([[-1.5,-.7,0],[1.7,-.7,0]],'#536781',.9);line([[-1.5,-.7,0],[-1.5,1.5,0]],'#536781',.9);
      for(var n=1;n<p.terms;n+=Math.max(1,Math.floor(p.terms/5)))line(sample(function(x){return [x*3-1.5,2*Math.pow(x,n)-.7,0];},0,upper,100),'#365b72',.65);
      line(sample(function(x){return [x*3-1.5,2*Math.pow(x,p.terms)-.7,.02];},0,upper,140),TEAL);
      line([[-1.5,-.7,0],[p.bound*3-1.5,-.7,0]],'#52667f');
      line([[-1.5,2*p.epsilon-.7,.03],[p.bound*3-1.5,2*p.epsilon-.7,.03]],PINK);
      if(isFull){circle(1.5,1.3,.045,.04,GOLD);out.points.push({position:[witness*3-1.5,.3,.05],color:GOLD});line([[witness*3-1.5,-.7,0],[witness*3-1.5,.3,.02]],GOLD,.7);}
      else out.points.push({position:[p.bound*3-1.5,2*sup-.7,.04],color:GOLD});
      out.facts={rightEndpointIncluded:!isFull,supremum:sup,witnessX:witness,witnessValue:.5,epsilon:p.epsilon,minimumTerms:minimumTerms};
      out.labels=[['横轴 |z| · 纵轴误差 |z|ⁿ · 粉线 ε='+fmt(p.epsilon),.5]];
      out.readout=isFull?'全开圆 |z|<1：sup |Sₙ|=1（不取到）。xₙ=2^(−1/n)='+fmt(witness)+'<1，|Sₙ(xₙ)|=1/2；不一致收敛。ε='+fmt(p.epsilon)+'，不存在满足此误差阈值的统一 N。':'闭小圆 |z|≤b<1：sup |Sₙ|=bⁿ≈'+Number(sup.toPrecision(6))+(sup<p.epsilon?' < ε，整个闭小圆误差已统一受控。':' ≥ ε；继续增大 n 可统一压低误差。')+'最小所需项数 N='+minimumTerms+'（所有 n≥N 均满足 bⁿ<ε）。';
    } else if(k==='contour'){
      grid(0);circle(0,0,p.radius,0,TEAL);out.points.push({position:[p.pole,0,.04],color:PINK},{position:[p.radius*Math.cos(p.phase),p.radius*Math.sin(p.phase),.04],color:GOLD});
      var t=p.phase;line([[p.radius*Math.cos(t),p.radius*Math.sin(t),0],[p.radius*Math.cos(t)-.3*Math.sin(t),p.radius*Math.sin(t)+.3*Math.cos(t),0]],GOLD);
      line([[p.pole-.08,-.08,.02],[p.pole+.08,.08,.02]],PINK);line([[p.pole-.08,.08,.02],[p.pole+.08,-.08,.02]],PINK);
      var distance=Math.abs(p.pole)-p.radius;
      out.readout=Math.abs(distance)<.025?'极点位于积分路径上：通常积分无定义。':'∮γ dz/(z−a) = '+(distance<0?'2πi（极点在内部）':'0（极点在外部）');
    } else if(k==='harmonic'){
      for(var ix=-12;ix<=12;ix++){var v=ix/8;
        line(sample(function(u){return [u,v,p.amplitude*(u*u-v*v)];},-1.5,1.5,42),TEAL,.55);
        line(sample(function(u){return [v,u,p.amplitude*(v*v-u*u)];},-1.5,1.5,42),BLUE,.55);
      }
      var c=p.amplitude,uLevel=p.level,vLevel=p.conjugateLevel,projectionHeight=-2.4*c-.15,curves={u:[],v:[]},windowHalf=1.5;
      var uConstant=uLevel/c,uExtent=windowHalf*windowHalf-Math.abs(uConstant);
      if(uExtent>=0){var tLimit=Math.sqrt(uExtent);for(var sign=-1;sign<=1;sign+=2)curves.u.push(sample(function(t){return uConstant>=0?[sign*Math.sqrt(t*t+uConstant),t]:[t,sign*Math.sqrt(t*t-uConstant)];},-tLimit,tLimit,90));}
      var product=vLevel/(2*c);
      if(Math.abs(product)<1e-14){curves.v.push(sample(function(t){return [t,0];},-windowHalf,windowHalf,90),sample(function(t){return [0,t];},-windowHalf,windowHalf,90));}
      else if(Math.abs(product)<=windowHalf*windowHalf){var root=Math.sqrt(Math.abs(product)),logLimit=Math.log(windowHalf/root);for(var sign=-1;sign<=1;sign+=2)curves.v.push(sample(function(t){var x=sign*root*Math.exp(t);return [x,product/x];},-logLimit,logLimit,90));}
      ['u','v'].forEach(function(family){curves[family].forEach(function(points){var color=family==='u'?GOLD:PINK;out.lines.push({points:points.map(function(q){return [q[0],q[1],c*(q[0]*q[0]-q[1]*q[1])+.02];}),color:color,opacity:1,family:family,projection:false});out.lines.push({points:points.map(function(q){return [q[0],q[1],projectionHeight];}),color:color,opacity:1,family:family,projection:true});});});
      line([[-windowHalf,-windowHalf,projectionHeight],[windowHalf,-windowHalf,projectionHeight],[windowHalf,windowHalf,projectionHeight],[-windowHalf,windowHalf,projectionHeight],[-windowHalf,-windowHalf,projectionHeight]],'#536781',.7);
      var r=Math.sqrt(Math.hypot(uLevel,vLevel)/c),angle=Math.atan2(vLevel,uLevel)/2,point=[r*Math.cos(angle),r*Math.sin(angle)],gradU=[2*c*point[0],-2*c*point[1]],gradV=[2*c*point[1],2*c*point[0]],nonCritical=r>1e-12,inWindow=Math.max(Math.abs(point[0]),Math.abs(point[1]))<=windowHalf;
      if(inWindow){out.points.push({position:[point[0],point[1],projectionHeight],color:TEAL},{position:[point[0],point[1],uLevel+.04],color:TEAL});line([[point[0],point[1],projectionHeight],[point[0],point[1],uLevel]],'#536781',.6);if(nonCritical){var tangentScale=.22/Math.hypot(gradU[0],gradU[1]);line([[point[0]-tangentScale*gradU[1],point[1]+tangentScale*gradU[0],projectionHeight+.02],[point[0]+tangentScale*gradU[1],point[1]-tangentScale*gradU[0],projectionHeight+.02]],GOLD);line([[point[0]-tangentScale*gradV[1],point[1]+tangentScale*gradV[0],projectionHeight+.02],[point[0]+tangentScale*gradV[1],point[1]-tangentScale*gradV[0],projectionHeight+.02]],PINK);}}
      out.facts={levelCurves:curves,projectionHeight:projectionHeight,orthogonality:{point:point,gradU:gradU,gradV:gradV,gradientDot:gradU[0]*gradV[0]+gradU[1]*gradV[1],nonCritical:nonCritical,inWindow:inWindow}};
      out.labels=[['金色 u 等值线；紫色 v 等值线；共同平面投影',.5]];
      out.readout='$u_{xx}+u_{yy}='+fmt(2*c)+'-'+fmt(2*c)+'=0$；金线 $u='+fmt(uLevel)+'$，紫线 $v='+fmt(vLevel)+'$；'+(!inWindow?'所选等值线交点在显示窗口外。':nonCritical?'青点两梯度均非零，内积为0；平面投影的两族切线正交。':'原点两梯度均为零，不指定唯一的正交切向。');
    } else if(k==='continuation'){
      grid(0);circle(0,0,1,0,BLUE);circle(p.center,0,Math.abs(1-p.center),.01,TEAL);out.points.push({position:[0,0,.04],color:BLUE},{position:[p.center,0,.04],color:TEAL},{position:[1,0,.05],color:PINK});
      line(sample(function(t){var z=[p.center+.65*(1-p.center)*Math.cos(t),.65*(1-p.center)*Math.sin(t)],q=[(z[0]-p.center)/(1-p.center),z[1]/(1-p.center)],s=geometricSum(q,p.terms);return [s[0]/(1-p.center)-2,s[1]/(1-p.center),.12];},0,TAU,110),GOLD,.8);
      out.readout='中心 a = '+fmt(p.center)+'；收敛半径 |1−a| = '+fmt(1-p.center)+'；n = '+p.terms+'。';
    } else if(k==='laplace'){
      line([[-2,-1,0],[2.5,-1,0]],'#536781');line([[-2,-1,0],[-2,1.7,0]],'#536781');
      line(sample(function(t){return [t*.65-2,2*Math.exp(-p.decay*t)-1,0];},0,6,130),TEAL);
      line(sample(function(t){return [t*.65-2,2*Math.exp(-(p.decay+p.sigma)*t)-1,.02];},0,6,130),GOLD);
      out.readout=p.decay+p.sigma>0?'F(s) = 1/(s+a) = '+fmt(1/(p.decay+p.sigma)):'s+a ≤ 0：积分不收敛。';
    }
    return out;
  }
  function node(doc,tag,cls,text){var el=doc.createElement(tag);if(cls)el.className=cls;if(text)el.textContent=text;return el;}
  function button(doc,action,text,title){var el=node(doc,'button','',text);el.type='button';el.dataset.studyAction=action;if(title){el.title=title;el.setAttribute('aria-label',title);}return el;}
  function syncUniformTermsButton(card,model){if(model.scene.kind==='uniform'){var full=model.params.bound===1;card.querySelectorAll('[data-study-param="bound"]').forEach(function(el){el.disabled=full;el.max=.99;el.setAttribute('aria-label','固定闭小圆半径 b'+(el.type==='number'?'数值':''));el.closest('label').querySelector('span').textContent=full?'整个开圆 |z|<1（不含圆周）':'固定闭小圆 |z|≤b，b<1';});card.querySelectorAll('[data-study-action^="uniform-mode-"]').forEach(function(el){el.setAttribute('aria-pressed',String((el.dataset.studyAction==='uniform-mode-open')===full));});}var jump=card.querySelector('[data-study-action="uniform-terms"]');if(!jump)return;var n=minimumUniformTerms(model.params.bound,model.params.epsilon);jump.disabled=n===null;jump.title=n===null?'全开圆上不存在满足此误差阈值的统一 N':'跳到最小所需项数 N='+n;jump.setAttribute('aria-label',jump.title);}
  function readoutText(card,text){var host=card.querySelector('.study-readout'),w=card.ownerDocument.defaultView;if(w.DSHAnimations&&w.DSHAnimations.captionText)w.DSHAnimations.captionText(host,text);else host.textContent=text;}
  function makeCard(doc,title,body,owner,target,resolvedScene){
    var scene=resolvedScene||chooseScene(title,body),model=createModel(scene),card=node(doc,'section','study-visual');
    card.dataset.studyScene=scene.kind;card.dataset.studyCoverage=scene.kind==='unmatched'?'unmatched':(scene.illustrative?'illustrative':'formula');card.dataset.studyMatch=scene.matchSource;card.dataset.studyOwner=owner;card.dataset.studyTarget=target||owner;card.__studyModel=model;
    var id='study-visual-'+(++uid),header=node(doc,'header','study-visual-header'),eyebrow=node(doc,'p','study-eyebrow','交互图像 / '+scene.label),h=node(doc,'h3','study-visual-title',title);
    h.id=id;card.setAttribute('aria-labelledby',id);header.appendChild(eyebrow);header.appendChild(h);card.appendChild(header);
    var formula=node(doc,'p','study-formula',scene.formula);card.appendChild(formula);
    if(scene.kind==='unmatched'){card.classList.add('study-unmatched');card.appendChild(node(doc,'p','study-explanation',scene.explanation));return card;}
    var viewport=node(doc,'div','study-viewport');viewport.tabIndex=0;viewport.setAttribute('role','img');viewport.setAttribute('aria-label',title+'，拖动旋转，滚轮缩放，Shift 拖动平移；也可用下方按钮。');
    viewport.appendChild(node(doc,'span','study-load-note','滚动至此处加载交互图'));card.appendChild(viewport);
    var readout=node(doc,'p','study-readout',geometry(model).readout);card.appendChild(readout);
    var controls=node(doc,'div','study-parameters');scene.controls.forEach(function(c){var row=node(doc,'label','study-parameter'),name=node(doc,'span','',c.label),range=node(doc,'input'),number=node(doc,'input');
      range.type='range';number.type='number';[range,number].forEach(function(input){input.min=c.min;input.max=c.max;input.step=c.step;input.value=inputNumber(c.value);input.dataset.studyParam=c.key;input.setAttribute('aria-label',c.label+(input===number?'数值':''));});
      row.appendChild(name);row.appendChild(range);row.appendChild(number);controls.appendChild(row);});card.appendChild(controls);
    var tools=node(doc,'div','study-tools');[['play','▶ 演示','播放参数变化'],['reset','复位','复位参数与视角'],['fullscreen','全屏','全屏查看图像']].forEach(function(b){tools.appendChild(button(doc,b[0],b[1],b[2]));});card.appendChild(tools);
    if(scene.kind==='uniform'){tools.appendChild(button(doc,'uniform-mode-open','整个开圆 |z|<1'));tools.appendChild(button(doc,'uniform-mode-closed','固定闭小圆 |z|≤b'));}
    if(scene.kind==='uniform'||scene.kind==='telescoping'){tools.appendChild(button(doc,'uniform-terms','跳到统一误差满足的项数'));syncUniformTermsButton(card,model);}
    var view=node(doc,'div','study-view-tools');[['rotate-left','↶','向左旋转'],['rotate-right','↷','向右旋转'],['tilt','⤢','改变俯仰视角'],['zoom-in','＋','放大'],['zoom-out','−','缩小'],['pan-left','←','向左平移'],['pan-right','→','向右平移']].forEach(function(b){view.appendChild(button(doc,b[0],b[1],b[2]));});card.appendChild(view);
    card.appendChild(node(doc,'p','study-explanation',scene.explanation));card.appendChild(node(doc,'p','study-gesture-hint','拖动旋转 · 滚轮缩放 · Shift 拖动平移'));
    return card;
  }
  // Explicit lesson plans opt in to one exact factory model. Never infer a
  // replacement from neighbouring prose or silently change the declared kind.
  function createTeachingCard(doc,plan,options){
    if(!plan||!plan.model_title||!plan.kind)throw new Error('Teaching visual requires model_title and kind');
    var chosen=chooseScene(plan.model_title,'');
    if(chosen.kind!==plan.kind||chosen.kind==='unmatched')throw new Error('Teaching visual kind mismatch: '+plan.kind+' / '+chosen.kind);
    var scene=Object.assign({},chosen,{controls:chosen.controls.map(function(c){return Object.assign({},c);})});
    if(scene.kind==='uniform'||scene.kind==='telescoping'){scene.initialPitch=0;scene.initialZoom=1.6;}
    if(scene.kind==='exp'){scene.initialPitch=0;scene.initialZoom=1;}
    if(scene.kind==='harmonic'){scene.initialPitch=0;}
    var initial=createModel(scene);
    Object.keys(plan.params||{}).forEach(function(key){
      if(!initial.set(key,plan.params[key]))throw new Error('Unknown or invalid teaching parameter: '+key);
    });
    scene.controls.forEach(function(c){c.value=initial.params[c.key];});
    var card=makeCard(doc,plan.title||plan.model_title,'',plan.id||'',plan.id||'',scene);
    card.dataset.teachingVisual=plan.id||'';card.dataset.studyCoverage='authored';
    card.querySelector('.study-eyebrow').textContent='动手观察';
    card.querySelector('.study-explanation').remove();
    var win=doc.defaultView||{},formula=card.querySelector('.study-formula'),tex=String(plan.formula||scene.formula).trim();
    tex=tex.replace(/^\$\$?|\$\$?$/g,'');
    if(win.DSHStudyMath)tex=win.DSHStudyMath.operatorLimits(tex);
    formula.textContent='';
    if(win.DSHRenderCore&&typeof win.DSHRenderCore.createRenderer==='function'){
      formula.innerHTML=win.DSHRenderCore.createRenderer({marked:win.marked,katex:win.katex}).renderMarkdown('$$'+tex+'$$');
    }else if(win.DSHAnimations&&typeof win.DSHAnimations.captionText==='function'){
      win.DSHAnimations.captionText(formula,'$$'+tex+'$$');
    }else if(win.katex&&typeof win.katex.render==='function'){
      win.katex.render(tex,formula,{displayMode:true,throwOnError:false,trust:false});
    }else formula.textContent=tex;
    var task=node(doc,'div','study-teaching-task');
    [['purpose','这幅图说明'],['action','试着操作'],['observation','注意观察'],['conclusion','由此可知']].forEach(function(entry){
      if(!plan[entry[0]])return;
      if(options&&options.contextOwned&&['purpose','action'].includes(entry[0]))return;
      var row=node(doc,'p','study-task-'+entry[0]),label=node(doc,'strong','',entry[1]+'：');row.appendChild(label);
      var text=node(doc,'span','',String(plan[entry[0]]));row.appendChild(text);task.appendChild(row);
    });
    var viewport=card.querySelector('.study-viewport');
    if(task.firstChild)card.insertBefore(task,viewport);
    if(options&&options.compact){
      card.dataset.studyCompact='true';
      var expandButton=card.querySelector('[data-study-action="fullscreen"]');expandButton.textContent='放大';expandButton.setAttribute('aria-label','放大图像');
      card.querySelectorAll('.study-teaching-task,.study-formula,.study-eyebrow,.study-gesture-hint').forEach(function(e){e.remove();});
      var settings=node(doc,'details','study-camera-settings');settings.appendChild(node(doc,'summary','','视角设置'));
      var camera=card.querySelector('.study-view-tools');camera.before(settings);settings.appendChild(camera);
    }
    readoutText(card,geometry(card.__studyModel).readout);
    return card;
  }
  // The original providers retain their mathematical data. Translate their
  // presentation palette at the draw boundary for a true white workspace.
  function lightColor(color){
    var palette={'#78aaff':'#2563eb','#54e0c0':'#087f8c','#ffcd70':'#b6630b','#f6a0ca':'#b43a78',
      '#536781':'#64748b','#29384d':'#c4d1df','#365b72':'#658399','#52667f':'#6a8099',
      '#344762':'#8093aa','#244c50':'#82a9aa','#436b92':'#6687ad','#526a91':'#758eb1'};
    return palette[String(color).toLowerCase()]||color;
  }
  function titleOf(target,unit){
    var explicit=target.getAttribute('data-reader-concept-title')||target.getAttribute('data-reader-topic-title')||target.getAttribute('data-study-topic-title');if(explicit)return explicit;
    var owner=target.getAttribute('data-reader-owner'),blocks=((unit.reader_document||{}).blocks||[]).filter(function(b){return b.owner_id===owner;});
    var authored=blocks.find(function(b){return /\/(title|name|label)$/.test(b.json_pointer||'');});
    if(authored)return String(authored.markdown||authored.raw_text||'').replace(/\[\[[^\]]+\]\]/g,'').slice(0,120);
    var heading=target.querySelector('summary,h2,h3,h4,.reader-reference-title,strong');
    return ((heading&&heading.textContent)||target.getAttribute('data-reader-reference')||'数学关系').trim().slice(0,120);
  }
  function layoutFor(target){
    var layout=Array.prototype.find.call(target.children,function(c){return c.classList.contains('study-target-layout');});if(layout)return layout.querySelector('.study-visual-column');
    layout=node(target.ownerDocument,'div','study-target-layout');var copy=node(target.ownerDocument,'div','study-target-copy'),side=node(target.ownerDocument,'aside','study-visual-column');side.setAttribute('aria-label','与正文对应的可操作数学图像');
    Array.prototype.slice.call(target.childNodes).forEach(function(child){if(child.nodeType===1&&(child.matches('summary')||child.classList.contains('reader-topic-title')))return;copy.appendChild(child);});
    layout.appendChild(copy);layout.appendChild(side);target.appendChild(layout);target.classList.add('has-study-visuals');return side;
  }
  function enhance(root,options){
    options=options||{};var data=options.data||{};if((data.study_reader||{}).visual_policy==='authored')return [];var unit=(data.units||{})[options.unitId]||{},added=[];
    var selector='[data-reader-concept-title],[data-reader-reference],[data-reader-group="references"],[data-reader-group="concepts"],[data-reader-group="annotations"],[data-reader-group="explainables"],.reader-pitfall-item';
    var candidates=Array.prototype.slice.call(root.querySelectorAll(selector));if(options.includeRoot&&root.matches&&root.matches(selector))candidates.unshift(root);
    var targets=candidates.filter(function(t){return !t.closest('[hidden],.study-visual,.source-material,.original')&&(options.allowPopup||!t.closest('.reader-popup'))&&!t.matches('[data-reader-concept-registration]');});
    // Registration wrappers and reference nodes may describe the same owner.
    targets=targets.filter(function(t){return !targets.some(function(other){return other!==t&&other.contains(t)&&other.getAttribute('data-reader-owner')===t.getAttribute('data-reader-owner')&&t.getAttribute('data-reader-owner');});});
    root.querySelectorAll('[data-reader-topic]').forEach(function(topic){var substantive=Array.prototype.some.call(topic.childNodes,function(n){return !(n.nodeType===1&&n.matches('.reader-topic-title'))&&String(n.textContent||'').trim();});if(!substantive)return;if(!targets.some(function(t){return topic.contains(t);})) {var example=topic.querySelector(':scope > .reader-example-disclosure');if(example){example.setAttribute('data-study-topic-title',topic.getAttribute('data-reader-topic-title')||'例题');targets.push(example);}else targets.push(topic);}});
    targets.forEach(function(target,index){
      if(target.__studyBound)return;target.__studyBound=true;
      var owner=target.getAttribute('data-reader-owner')||target.id||('topic-'+index),title=titleOf(target,unit),body=target.textContent||'';
      var sourceGroup=target.closest('[data-reader-owner]'),sourceOwner=sourceGroup&&sourceGroup.getAttribute('data-reader-owner'),fields=((unit.reader_document||{}).blocks||[]).filter(function(b){return b.owner_id===sourceOwner;});
      var authoredBody=fields.map(function(b){return String(b.markdown||b.raw_text||'');}).join('\n'),scene=chooseScene(title,body),contextual=false;
      if((/^(?:[0-9.一二三四五六七八九十]+[.、．)）\s]*)?(?:导数|求导|恒等式|解析性|周期性|周期不要混淆|与三角函数的关系|基本性质|性质[0-9]*|定义|说明)(?:[：:].*)?$/.test(title)||(/双曲函数/.test(unit.title||'')&&/双曲|sinh|cosh|tanh|coth/.test(title)))&&unit.title){var family=chooseScene(unit.title,'');if(['hyperbolic','exp','log','sin','cos','sqrt','power','arctan'].indexOf(family.kind)>=0){scene=family;if(family.kind==='hyperbolic'&&/tanh|coth/.test(title))family.controls[0].value=/tanh/.test(title)?2:3;scene.matchSource='context';scene.explanation='对应本节“'+unit.title+'”的函数：'+scene.explanation;}}
      if(scene.kind==='unmatched'&&authoredBody){scene=chooseScene(title,authoredBody+'\n'+body);contextual=sourceOwner!==owner;}
      // A short pitfall often names only an operation such as an omitted term.
      // Its own example statement supplies the actual function and conditions.
      if(scene.kind==='unmatched'&&target.classList.contains('reader-pitfall-item')&&unit.title){scene=chooseScene(unit.title,authoredBody+'\n'+body);contextual=true;}
      if(contextual&&scene.kind!=='unmatched'){scene.matchSource='context';scene.explanation='对应所属例题或本节条件的代表关系：'+scene.explanation;}
      var card=makeCard(root.ownerDocument,title,body,owner,target.id||owner,scene);card.__studySource=target;card.__studyBody=body;
      var closed=target.closest('details'),topic=target.closest('[data-reader-topic]');
      if(closed){closed.appendChild(card);card.classList.add('study-inline-visual');}
      else if(topic)layoutFor(topic).appendChild(card);
      else layoutFor(target).appendChild(card);
      added.push(card);
    });return added;
  }
  function isOpen(card){var current=card;while(current&&current.nodeType===1){if(current.hidden)return false;if(current.tagName==='DETAILS'&&!current.open)return false;current=current.parentElement;}return true;}
  // Fit the scene's declared finite plotting windows, never unbounded mapped
  // samples. Both backends share one isotropic world-to-pixel scale and retain
  // the user's zoom. A pole may leave the viewport; it must not shrink the axes.
  function fittedViewport(model,geo,width,height){
    var aspect=width/height,halfX=0,halfY=3.1;
    if(model.scene.kind==='exp'){halfX=2.4;halfY=2.15;}
    else if(geo.labels&&geo.labels.length>1)halfX=5;
    var vertical=Math.max(halfY,halfX/aspect)/model.view.zoom;
    return {halfWidth:vertical*aspect,halfHeight:vertical,pixelsPerUnit:height/(2*vertical)};
  }
  function threeRenderer(card,model){
    var win=card.ownerDocument.defaultView,THREE=win.THREE,viewport=card.querySelector('.study-viewport'),renderer,scene,camera,group,observer,canvas;
    var width=viewport.clientWidth||420,height=viewport.clientHeight||280,drag=null,currentGeometry=null;
    function clear(){if(!group)return;while(group.children.length){var obj=group.children[0];group.remove(obj);if(obj.geometry)obj.geometry.dispose();if(obj.material)obj.material.dispose();}}
    function render(){if(!renderer){draw2d();return;}if(camera){var v=model.view;camera.position.set(9*Math.sin(v.yaw)*Math.cos(v.pitch),9*Math.sin(v.pitch),9*Math.cos(v.yaw)*Math.cos(v.pitch));camera.up.set(0,1,0);camera.lookAt(v.panX,v.panY,0);var fit=fittedViewport(model,currentGeometry||geometry(model),width,height);camera.left=-fit.halfWidth;camera.right=fit.halfWidth;camera.top=fit.halfHeight;camera.bottom=-fit.halfHeight;camera.updateProjectionMatrix();renderer.render(scene,camera);}else draw2d();}
    function draw2d(){var ctx=canvas.getContext('2d');if(!ctx)return;ctx.fillStyle='#ffffff';ctx.fillRect(0,0,width,height);var v=model.view,geo=currentGeometry||geometry(model),scale=fittedViewport(model,geo,width,height).pixelsPerUnit;
      function project(p){var x=p[0]-v.panX,y=p[1]-v.panY,z=p[2]||0,X=x*Math.cos(v.yaw)-z*Math.sin(v.yaw),Z=x*Math.sin(v.yaw)+z*Math.cos(v.yaw);return [width/2+X*scale,height/2-(y*Math.cos(v.pitch)-Z*Math.sin(v.pitch))*scale];}
      geo.lines.forEach(function(l){ctx.strokeStyle=lightColor(l.color);ctx.globalAlpha=l.opacity;ctx.lineWidth=1.3;ctx.beginPath();var previous=null;l.points.forEach(function(p){var q=project(p);if(!Number.isFinite(q[0])||!Number.isFinite(q[1])||Math.abs(q[0])>width*8||Math.abs(q[1])>height*8){previous=null;return;}if(!previous||Math.hypot(q[0]-previous[0],q[1]-previous[1])>width/2)ctx.moveTo(q[0],q[1]);else ctx.lineTo(q[0],q[1]);previous=q;});ctx.stroke();});ctx.globalAlpha=1;
      geo.points.forEach(function(p){var q=project(p.position);ctx.fillStyle=lightColor(p.color);ctx.beginPath();ctx.arc(q[0],q[1],5,0,TAU);ctx.fill();});
    }
    try{
      if(!THREE||!win.WebGLRenderingContext)throw new Error('WebGL unavailable');
      renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,powerPreference:'low-power'});renderer.setPixelRatio(Math.min(win.devicePixelRatio||1,1.7));renderer.setSize(width,height);renderer.setClearColor('#ffffff',1);
      canvas=renderer.domElement;scene=new THREE.Scene();camera=new THREE.OrthographicCamera(-5,5,3,-3,.1,100);group=new THREE.Group();scene.add(group);card.dataset.studyRenderer='webgl';
    }catch(error){if(renderer){renderer.dispose();if(renderer.forceContextLoss)renderer.forceContextLoss();}renderer=null;canvas=node(card.ownerDocument,'canvas');canvas.width=width;canvas.height=height;card.dataset.studyRenderer='canvas';}
    viewport.replaceChildren(canvas);var labels=node(card.ownerDocument,'div','study-plot-labels');if(card.dataset.studyCompact==='true')card.insertBefore(labels,card.querySelector('.study-readout'));else viewport.appendChild(labels);
    function update(){var geo=geometry(model);currentGeometry=geo;readoutText(card,geo.readout);clear();
      if(renderer){geo.lines.forEach(function(line){var segments=[],prev=null;line.points.forEach(function(p){if(!p.every(Number.isFinite)||Math.abs(p[0])>14||Math.abs(p[1])>12||Math.abs(p[2])>12){prev=null;return;}if(prev&&(line.points.length===2||Math.hypot(p[0]-prev[0],p[1]-prev[1],p[2]-prev[2])<2))segments.push(new THREE.Vector3(prev[0],prev[1],prev[2]),new THREE.Vector3(p[0],p[1],p[2]));prev=p;});if(!segments.length)return;
          var geom=new THREE.BufferGeometry().setFromPoints(segments),material=new THREE.LineBasicMaterial({color:lightColor(line.color),transparent:true,opacity:line.opacity});group.add(new THREE.LineSegments(geom,material));});
        geo.points.forEach(function(p){if(!p.position.every(Number.isFinite))return;var geom=new THREE.SphereGeometry(.065,12,8),material=new THREE.MeshBasicMaterial({color:lightColor(p.color)}),mesh=new THREE.Mesh(geom,material);mesh.position.set.apply(mesh.position,p.position);group.add(mesh);});}
      labels.replaceChildren();labels.classList.toggle('study-plot-labels-pair',geo.labels.length>1);geo.labels.forEach(function(l){var span=node(card.ownerDocument,'span','',l[0]);if(win.DSHAnimations)win.DSHAnimations.captionText(span,l[0]);span.style.left=(l[1]*100)+'%';labels.appendChild(span);});render();}
    function down(e){drag={x:e.clientX,y:e.clientY,pan:e.shiftKey||e.button===2};if(viewport.setPointerCapture)viewport.setPointerCapture(e.pointerId);}
    function move(e){if(!drag)return;var dx=e.clientX-drag.x,dy=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;if(drag.pan){model.view.panX-=dx/65/model.view.zoom;model.view.panY+=dy/65/model.view.zoom;}else{model.view.yaw-=dx*.009;model.view.pitch=Math.max(-1.48,Math.min(1.48,model.view.pitch+dy*.009));}render();}
    function up(){drag=null;}
    function wheel(e){e.preventDefault();model.view.zoom=Math.max(.35,Math.min(5,model.view.zoom*Math.exp(-e.deltaY*.001)));render();}
    function key(e){if(!/^(ArrowLeft|ArrowRight|ArrowUp|ArrowDown|\+|=|-|0)$/.test(e.key))return;e.preventDefault();if(e.key==='ArrowLeft')model.view.yaw-=.15;if(e.key==='ArrowRight')model.view.yaw+=.15;if(e.key==='ArrowUp')model.view.pitch+=.1;if(e.key==='ArrowDown')model.view.pitch-=.1;if(e.key==='+'||e.key==='=')model.view.zoom=Math.min(5,model.view.zoom*1.15);if(e.key==='-')model.view.zoom=Math.max(.35,model.view.zoom/1.15);if(e.key==='0')model.reset();render();}
    function resize(){width=viewport.clientWidth||420;height=viewport.clientHeight||280;if(renderer)renderer.setSize(width,height);else{canvas.width=width;canvas.height=height;}render();}
    viewport.addEventListener('pointerdown',down);viewport.addEventListener('pointermove',move);viewport.addEventListener('pointerup',up);viewport.addEventListener('pointercancel',up);viewport.addEventListener('wheel',wheel,{passive:false});viewport.addEventListener('keydown',key);
    if(win.ResizeObserver){observer=new win.ResizeObserver(resize);observer.observe(viewport);}update();
    return {update:update,view:render,dispose:function(){if(observer)observer.disconnect();viewport.removeEventListener('pointerdown',down);viewport.removeEventListener('pointermove',move);viewport.removeEventListener('pointerup',up);viewport.removeEventListener('pointercancel',up);viewport.removeEventListener('wheel',wheel);viewport.removeEventListener('keydown',key);clear();if(renderer){renderer.dispose();if(renderer.forceContextLoss)renderer.forceContextLoss();}if(labels)labels.remove();viewport.replaceChildren(node(card.ownerDocument,'span','study-load-note','滚动至此处加载交互图'));}};
  }
  function createController(root,options){
    options=options||{};var win=root.ownerDocument.defaultView,states=new Map(),active=new Map(),destroyed=false,timer=null,max=options.maxActive||4;
    var makeRenderer=options.createRenderer||threeRenderer,raf=win.requestAnimationFrame?win.requestAnimationFrame.bind(win):function(fn){return win.setTimeout(function(){fn(Date.now());},32);},caf=win.cancelAnimationFrame?win.cancelAnimationFrame.bind(win):win.clearTimeout.bind(win);
    function stop(state){if(state.raf)caf(state.raf);state.raf=null;state.playing=false;var b=state.card.querySelector('[data-study-action="play"]');if(b){b.textContent='▶ 演示';b.setAttribute('aria-pressed','false');}}
    function release(state){stop(state);if(state.renderer){state.renderer.dispose();state.renderer=null;active.delete(state.card);state.card.dataset.studyActive='false';}}
    function sync(state){var model=state.card.__studyModel;state.card.querySelectorAll('[data-study-param]').forEach(function(input){input.value=inputNumber(model.params[input.dataset.studyParam]);});readoutText(state.card,geometry(model).readout);syncUniformTermsButton(state.card,model);if(state.renderer)state.renderer.update();}
    function mount(state){if(state.renderer||!state.visible||!state.card.isConnected||!isOpen(state.card)||root.ownerDocument.hidden)return;
      if(active.size>=max){var oldest=active.values().next().value;release(oldest);}
      state.renderer=makeRenderer(state.card,state.card.__studyModel);active.set(state.card,state);state.card.dataset.studyActive='true';}
    function entries(items){items.forEach(function(e){var s=states.get(e.target);if(!s)return;s.visible=e.isIntersecting;if(s.visible)mount(s);else release(s);});}
    var observer=options.observe?options.observe(entries):(win.IntersectionObserver?new win.IntersectionObserver(entries,{rootMargin:'0px',threshold:.05}):null);
    function register(){root.querySelectorAll('.study-visual').forEach(function(card){if(states.has(card)||card.dataset.studyCoverage==='unmatched'||card.dataset.textLedFigure==='true')return;var state={card:card,visible:false,renderer:null,playing:false,raf:null,open:isOpen(card)};states.set(card,state);if(observer)observer.observe(card);});}
    function checkViewport(){if(observer)return;states.forEach(function(state){var rect=state.card.getBoundingClientRect();state.visible=rect.bottom>0&&rect.top<win.innerHeight&&rect.width>0;});}
    function refresh(){if(destroyed)return;register();checkViewport();states.forEach(function(state,card){if(!root.contains(card)){release(state);if(observer)observer.unobserve(card);states.delete(card);}else {var open=isOpen(card);if(!state.visible||!open||root.ownerDocument.hidden)release(state);else if(!state.open&&open)mount(state);state.open=open;}});}
    function visibility(){refresh();states.forEach(function(s){if(s.visible)mount(s);});}
    function input(event){var el=event.target;if(!el.dataset.studyParam)return;var card=el.closest('.study-visual'),state=states.get(card);if(!state)return;stop(state);var value=el.value;if(card.__studyModel.scene.kind==='uniform'&&el.dataset.studyParam==='bound')value=Math.min(.99,Number(value));if(card.__studyModel.set(el.dataset.studyParam,value))sync(state);}
    function play(state){if(state.playing){stop(state);return;}mount(state);if(!state.renderer)return;state.playing=true;var card=state.card,model=card.__studyModel,c=model.scene.controls[0],start=model.params[c.key],time0=null;
      var b=card.querySelector('[data-study-action="play"]');b.textContent='Ⅱ 暂停';b.setAttribute('aria-pressed','true');
      function tick(time){if(!state.playing)return;if(time0==null)time0=time;var phase=(time-time0)/3500;model.set(c.key,c.min+(c.max-c.min)*(1-Math.cos(phase+Math.acos(1-2*(start-c.min)/(c.max-c.min))))/2);sync(state);state.raf=raf(tick);}state.raf=raf(tick);}
    function click(event){var el=event.target.closest('[data-study-action]');if(!el)return;var card=el.closest('.study-visual'),state=states.get(card);if(!state)return;var a=el.dataset.studyAction,m=card.__studyModel,v=m.view;
      if(a==='play'){play(state);return;}if(a==='reset'){stop(state);card.__closedBound=.7;m.reset();sync(state);return;}
      if((a==='uniform-mode-open'||a==='uniform-mode-closed')&&m.scene.kind==='uniform'){stop(state);if(a==='uniform-mode-open'){if(m.params.bound<1)card.__closedBound=m.params.bound;m.set('bound',1);}else m.set('bound',m.params.bound<1?m.params.bound:(card.__closedBound||.7));sync(state);return;}
      if(a==='uniform-terms'&&(m.scene.kind==='uniform'||m.scene.kind==='telescoping')){var n=minimumUniformTerms(m.params.bound,m.params.epsilon);if(n!==null){stop(state);m.set('terms',n);sync(state);}return;}
      if(a==='fullscreen'){if(card.dataset.studyCompact==='true'){var expanded=card.classList.toggle('study-expanded');el.textContent=expanded?'收起':'放大';el.setAttribute('aria-label',expanded?'收起图像':'放大图像');el.setAttribute('aria-pressed',String(expanded));return;}if(card.classList.contains('study-expanded')){card.classList.remove('study-expanded');return;}if(root.ownerDocument.fullscreenElement){if(root.ownerDocument.exitFullscreen)root.ownerDocument.exitFullscreen().catch(function(){});}else if(card.requestFullscreen){card.requestFullscreen().catch(function(){card.classList.toggle('study-expanded');});}else card.classList.toggle('study-expanded');return;}
      if(a==='rotate-left')v.yaw-=.25;if(a==='rotate-right')v.yaw+=.25;if(a==='tilt')v.pitch=v.pitch>.9?.15:v.pitch+.25;if(a==='zoom-in')v.zoom=Math.min(5,v.zoom*1.2);if(a==='zoom-out')v.zoom=Math.max(.35,v.zoom/1.2);if(a==='pan-left')v.panX-=.3;if(a==='pan-right')v.panX+=.3;
      mount(state);if(state.renderer&&state.renderer.view)state.renderer.view();}
    root.addEventListener('input',input);root.addEventListener('click',click);root.addEventListener('toggle',visibility,true);root.ownerDocument.addEventListener('visibilitychange',visibility);
    if(!observer){win.addEventListener('scroll',visibility,{passive:true});win.addEventListener('resize',visibility);}
    register();if(!observer)visibility();
    // refresh() does not remount an evicted context. Only a new intersection,
    // explicit user action, or disclosure/visibility change can do that.
    return {refresh:refresh,activeCount:function(){return active.size;},destroy:function(){destroyed=true;if(timer)win.clearTimeout(timer);if(observer)observer.disconnect();states.forEach(release);states.clear();root.removeEventListener('input',input);root.removeEventListener('click',click);root.removeEventListener('toggle',visibility,true);root.ownerDocument.removeEventListener('visibilitychange',visibility);win.removeEventListener('scroll',visibility);win.removeEventListener('resize',visibility);}};
  }
  // readerCopy copies DOM but not JS models/listeners. A popup owns a fresh
  // scope and renderer budget; source-page controls and model state stay separate.
  function mountReaderScope(container,options){
    options=options||{};if(!container||!container.querySelectorAll)return function(){};
    if(container.__studyScopeDispose)container.__studyScopeDispose();
    var previous=Array.prototype.map.call(container.querySelectorAll('.study-visual'),function(card){return {title:(card.querySelector('.study-visual-title')||{}).textContent||'数学关系',owner:card.dataset.studyOwner,target:card.dataset.studyTarget};});
    container.querySelectorAll('.study-visual').forEach(function(card){card.remove();});
    container.__studyBound=false;container.querySelectorAll('*').forEach(function(el){el.__studyBound=false;});
    var data=options.data||(container.ownerDocument.defaultView.__DATA__||{}),unitId=options.unitId||'',settings={data:data,unitId:unitId,allowPopup:true,includeRoot:true};
    var cards=enhance(container,settings);
    if(!cards.length&&previous.length&&(data.study_reader||{}).visual_policy!=='authored'){var unit=(data.units||{})[unitId]||{};previous.forEach(function(old){var body=((unit.reader_document||{}).blocks||[]).filter(function(b){return b.owner_id===old.owner;}).map(function(b){return b.markdown||b.raw_text||'';}).join('\n');var card=makeCard(container.ownerDocument,old.title,body,old.owner||'popup',old.target);container.appendChild(card);cards.push(card);});}
    var controller=createController(container,{maxActive:Math.max(1,Math.min(2,options.maxActive||1)),observe:options.observe,createRenderer:options.createRenderer}),disposed=false;
    function dispose(){if(disposed)return;disposed=true;controller.destroy();container.querySelectorAll('.study-visual button,.study-visual input').forEach(function(el){el.disabled=true;});if(container.__studyScopeDispose===dispose)delete container.__studyScopeDispose;}
    dispose.controller=controller;container.__studyScopeDispose=dispose;return dispose;
  }
  function boot(doc){
    var root=doc.querySelector('#content');if(!root||root.__studyController)return;var win=doc.defaultView,controller,scheduled=false,lastUnitId=((doc.defaultView.__DATA__||{}).scope||[])[0]||'';
    function update(){scheduled=false;var hash=decodeURIComponent(win.location.hash||''),unitId=hash.indexOf('#/')===0?hash.slice(2).split('?')[0]:lastUnitId;if(!unitId)unitId=lastUnitId;else lastUnitId=unitId;
      enhance(root,{data:win.__DATA__||{},unitId:unitId});if(!controller)controller=createController(root);else controller.refresh();root.__studyController=controller;
    }
    update();var mutation=new win.MutationObserver(function(records){if(scheduled)return;if(!records.some(function(r){return Array.prototype.some.call(r.addedNodes,function(n){return n.nodeType===1&&!n.closest('.study-visual');})||Array.prototype.some.call(r.removedNodes,function(n){return n.nodeType===1&&(n.matches('.unit,.study-visual,[data-reader-topic]')||n.querySelector('.study-visual'));});}))return;scheduled=true;win.setTimeout(update,0);});
    mutation.observe(root,{childList:true,subtree:true});win.addEventListener('pagehide',function(){mutation.disconnect();controller.destroy();});
  }
  return {registerScenes:registerScenes,chooseScene:chooseScene,createModel:createModel,minimumUniformTerms:minimumUniformTerms,geometry:geometry,enhance:enhance,createTeachingCard:createTeachingCard,createController:createController,mountReaderScope:mountReaderScope,boot:boot};
});
