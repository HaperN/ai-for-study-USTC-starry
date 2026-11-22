/* Offline, inspectable spatial models. Three.js is supplied by the local bundle. */
(function (global) {
  'use strict';
  const TAU = 2 * Math.PI;
  function stereographic(x, y) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) throw new RangeError('Coordinates must be finite');
    const r2 = x*x + y*y, d = r2 + 4;
    return [4*x/d, 4*y/d, 2*r2/d];
  }
  function logarithmPoint(radius, theta) {
    if (!(radius > 0) || !Number.isFinite(radius) || !Number.isFinite(theta)) throw new RangeError('Logarithm requires a finite nonzero radius');
    let principal = ((theta + Math.PI) % TAU + TAU) % TAU - Math.PI;
    if (Math.abs(Math.abs(principal)-Math.PI) < 1e-10) principal = null;
    return {x:radius*Math.cos(theta), y:radius*Math.sin(theta), real:Math.log(radius), imag:theta, principal};
  }
  const controllers = new Set();
  const f = n => (Math.abs(n)<0.00005 ? 0 : n).toFixed(3);
  const signed = n => (n < 0 ? ' − ' : ' + ') + f(Math.abs(n));
  function mount(root) {
    if (root.__spatial) return root.__spatial;
    const kind = root.dataset.spatial, T = global.THREE;
    const host = root.querySelector('[data-canvas-host]');
    const status = root.querySelector('[data-status]');
    const fallback = root.querySelector('[data-fallback]');
    const controls = root.querySelector('.spatial-controls');
    let renderer;
    function unavailable(message) {
      root.dataset.mode = 'static';
      status.textContent = message;
      fallback.hidden = false; host.hidden = true; controls.hidden = true;
      root.querySelector('.spatial-readings').hidden=true;root.querySelector('.spatial-interaction-hint').hidden=true;
    }
    try {
      if (!T) throw new Error('Three.js missing');
      renderer = new T.WebGLRenderer({antialias:true, alpha:false, preserveDrawingBuffer:true});
    } catch (_) {
      unavailable('当前环境无法创建 WebGL 三维画面。下面是静态矢量图，不是可旋转或播放的动态图。');
      return null;
    }
    const scene = new T.Scene();
    scene.background = new T.Color('#f2f6f6');
    const camera = new T.PerspectiveCamera(38, 1, 0.05, 250);
    const target = new T.Vector3(0, 0.85, 0);
    const initial = {radius:kind === 'sphere' ? 1.4 : 1.25, theta:Math.PI/4};
    const state = {...initial, azimuth:1.35, elevation:0.50, distance:kind === 'log' ? 14 : 7.9, playing:false};
    const initialCamera = {azimuth:state.azimuth, elevation:state.elevation, distance:state.distance};
    let frame = 0, last = 0, disposed = false, drag = null;
    const canvas = renderer.domElement;
    const labels = [];
    canvas.setAttribute('aria-label', kind === 'sphere' ? '黎曼球面三维图。方向键旋转，正负键缩放，Home 恢复视角。' : '复对数螺旋曲面。方向键旋转，正负键缩放，Home 恢复视角。');
    canvas.setAttribute('role','img'); canvas.tabIndex = 0;
    host.append(canvas); host.hidden = false; fallback.hidden = true; controls.hidden = false;
    root.dataset.mode = 'webgl';
    root.querySelector('.spatial-readings').hidden=false;root.querySelector('.spatial-interaction-hint').hidden=false;
    scene.add(new T.AmbientLight(0xffffff, 0.8));
    const light = new T.DirectionalLight(0xffffff, 0.75); light.position.set(4,7,3); scene.add(light);
    function v(x,y,z) {return new T.Vector3(x,z,y);}
    function path(points, color, opacity=1, dashed=false) {
      const geometry=new T.BufferGeometry().setFromPoints(points);
      const material=dashed ? new T.LineDashedMaterial({color,transparent:opacity<1,opacity,dashSize:0.12,gapSize:0.08}) : new T.LineBasicMaterial({color,transparent:opacity<1,opacity});
      const line=new T.Line(geometry,material); if(dashed)line.computeLineDistances(); scene.add(line); return line;
    }
    function dot(position, color, size=0.055) {
      const node=new T.Mesh(new T.SphereGeometry(size,18,12),new T.MeshPhongMaterial({color}));
      node.position.copy(position); scene.add(node); return node;
    }
    function label(text, position, color='#294d51', scale=1) {
      const c=global.document.createElement('canvas');
      let context=c.getContext('2d');context.font='600 28px system-ui, sans-serif';
      c.width=Math.ceil(context.measureText(text).width)+24;c.height=48;
      context=c.getContext('2d');context.font='600 28px system-ui, sans-serif';context.textAlign='center';context.textBaseline='middle';
      context.fillStyle='rgba(255,255,255,.88)';context.fillRect(0,0,c.width,c.height);
      context.fillStyle=color;context.fillText(text,c.width/2,24);
      const texture=new T.CanvasTexture(c);
      const sprite=new T.Sprite(new T.SpriteMaterial({map:texture,depthTest:false,transparent:true,sizeAttenuation:false}));
      sprite.position.copy(position);sprite.renderOrder=8;scene.add(sprite);
      labels.push({sprite,aspect:c.width/c.height,pixels:Math.max(21,26*scale)});return sprite;
    }
    function plane(size,height) {
      const mesh=new T.Mesh(new T.PlaneGeometry(size*2,size*2), new T.MeshBasicMaterial({color:0xd1e4e2,opacity:.24,transparent:true,side:T.DoubleSide,depthWrite:false}));
      mesh.rotation.x=-Math.PI/2;mesh.position.y=height;scene.add(mesh);
      for(let i=-Math.floor(size);i<=Math.floor(size);i++){
        path([v(-size,i,height),v(size,i,height)],0xb4cccb,.44);
        path([v(i,-size,height),v(i,size,height)],0xb4cccb,.44);
      }
      path([v(-size,0,height),v(size,0,height)],0x607d80,.7);
      path([v(0,-size,height),v(0,size,height)],0x607d80,.7);
      label('Re z',v(size-.30,0,height),undefined,.55);
      label('Im z',v(0,size-.30,height),undefined,.55);
    }
    let point, input, projection, markerLabel, updateModel;
    if(kind === 'sphere') {
      plane(2.6,0);
      const sphere = new T.Mesh(new T.SphereGeometry(1,48,32),new T.MeshPhongMaterial({color:0x73b8bb,transparent:true,opacity:.15,side:T.DoubleSide,depthWrite:false})); sphere.position.y=1;scene.add(sphere);
      for(let latitude=-Math.PI/3;latitude<=Math.PI/3+.01;latitude+=Math.PI/6){
        const pts=[];for(let i=0;i<=120;i++){const t=i/120*TAU;pts.push(v(Math.cos(latitude)*Math.cos(t),Math.cos(latitude)*Math.sin(t),1+Math.sin(latitude)));}path(pts,0x3d888d,.45);
      }
      for(let j=0;j<8;j++){
        const pts=[];for(let i=0;i<=120;i++){const t=i/120*TAU;pts.push(v(Math.cos(t)*Math.cos(j*Math.PI/8),Math.cos(t)*Math.sin(j*Math.PI/8),1+Math.sin(t)));}path(pts,0x3d888d,.35);
      }
      dot(v(0,0,2),0x1c555d,.045);label('N：∞',v(0,0,2.24),undefined,.65);
      dot(v(0,0,0),0x49797b,.035);label('S：z = 0',v(0,0,-.20),undefined,.60);
      label('赤道：|z| = 2',v(-1.15,0,1),undefined,.60);
      point=dot(v(0,0,0),0xe2763e,.075);input=dot(v(0,0,0),0x194fbd,.055);
      projection=path([v(0,0,2),v(0,0,0)],0xd17843,.95);
      markerLabel=label('Q(z)',v(0,0,0),'#a55124',.48);
      label('切平面：ζ = 0',v(-1.5,-.8,0),undefined,.72);
      updateModel=()=>{
        const x=state.radius*Math.cos(state.theta), y=state.radius*Math.sin(state.theta), q=stereographic(x,y);
        point.position.copy(v(...q));input.position.copy(v(x,y,0));
        markerLabel.position.copy(v(q[0]+.18,q[1],q[2]+.16));
        // Q-N = 4/(r²+4) * (P-N), so Q lies on the full N-to-P segment for every finite r.
        projection.geometry.dispose();projection.geometry=new T.BufferGeometry().setFromPoints([v(0,0,2),v(x,y,0)]);
        root.querySelector('[data-value="input"]').textContent=`z = ${f(x)}${signed(y)}i；r = ${f(state.radius)}`;
        root.querySelector('[data-value="output"]').textContent=`Q = (${f(q[0])}, ${f(q[1])}, ${f(q[2])})`;
        root.querySelector('[data-value="distance"]').textContent=`|Q − N| = ${f(4/Math.sqrt(state.radius*state.radius+4))} > 0`;
        root.querySelector('[data-window-note]').textContent=(Math.abs(x)>2.6 || Math.abs(y)>2.6) ? '当前平面点超出方形展示窗口；投影线继续指向该有限点。可缩小视图查看。' : '蓝点是复平面中的 z；橙点是球面上的 Q(z)。';
      };
    } else {
      const vertical=.36, bottom=-2*Math.PI*vertical-.45;
      plane(2.6,bottom);
      // Coordinates are (Re z, Im z, continuous arg); the vertical display scale is .36.
      const vertices=[], colors=[]; const radialSteps=14, angularSteps=180;
      function pos(r,t){return [r*Math.cos(t),t*vertical,r*Math.sin(t)];}
      for(let a=0;a<angularSteps;a++){
        const t0=-TAU+3*TAU*a/angularSteps,t1=-TAU+3*TAU*(a+1)/angularSteps;
        const color=new T.Color().setHSL(.51+(.06*a/angularSteps),.36,.56);
        for(let r=0;r<radialSteps;r++){
          const r0=.3+2.1*r/radialSteps,r1=.3+2.1*(r+1)/radialSteps;
          for(const xyz of [pos(r0,t0),pos(r1,t0),pos(r1,t1),pos(r0,t0),pos(r1,t1),pos(r0,t1)]){vertices.push(...xyz);colors.push(color.r,color.g,color.b);}
        }
      }
      const geometry=new T.BufferGeometry();geometry.setAttribute('position',new T.Float32BufferAttribute(vertices,3));geometry.setAttribute('color',new T.Float32BufferAttribute(colors,3));geometry.computeVertexNormals();
      scene.add(new T.Mesh(geometry,new T.MeshPhongMaterial({vertexColors:true,transparent:true,opacity:.42,side:T.DoubleSide,depthWrite:false})));
      for(const r of [.3,.65,1,1.35,1.7,2.05,2.4]){
        const pts=[];for(let i=0;i<=240;i++){const t=-TAU+3*TAU*i/240;pts.push(v(r*Math.cos(t),r*Math.sin(t),t*vertical));}path(pts,0x3d878c,.48);
      }
      for(let i=-12;i<=24;i++){
        const t=i*Math.PI/6;path([v(.3*Math.cos(t),.3*Math.sin(t),t*vertical),v(2.4*Math.cos(t),2.4*Math.sin(t),t*vertical)],0x4c8a8d,.3);
      }
      path([v(0,0,bottom),v(0,0,4*Math.PI*vertical+.5)],0x456a71,.8);
      for(let n=-2;n<=4;n++)label(n===0?'θ = 0':`${n}π`,v(-.15,-.1,n*Math.PI*vertical),undefined,.43);
      label('高度：连续 θ',v(0,0,4*Math.PI*vertical+.7),undefined,.8);
      path([v(-2.6,0,bottom+.015),v(0,0,bottom+.015)],0xbe4a44,1,true);
      label('主支割线',v(-1.8,-.35,bottom),'#a8413c',.67);
      point=dot(v(0,0,0),0xdf733a,.095);input=dot(v(0,0,bottom),0x204ebc,.075);
      projection=path([v(0,0,bottom),v(0,0,0)],0xcf743e,.85,true);
      const orbit=path([],0xe29342,.95);markerLabel=label('连续 θ',v(0,0,0),'#a55124',.6);
      updateModel=()=>{
        const p=logarithmPoint(state.radius,state.theta);
        point.position.copy(v(p.x,p.y,p.imag*vertical));input.position.copy(v(p.x,p.y,bottom));
        markerLabel.position.copy(v(p.x+.60,p.y-.30,p.imag*vertical+.10));
        projection.geometry.dispose();projection.geometry=new T.BufferGeometry().setFromPoints([input.position,point.position]);projection.computeLineDistances();
        const pts=[];for(let i=0;i<=90;i++){const t=state.theta-TAU+TAU*i/90;if(t>=-TAU)pts.push(v(state.radius*Math.cos(t),state.radius*Math.sin(t),t*vertical));}
        orbit.geometry.dispose();orbit.geometry=new T.BufferGeometry().setFromPoints(pts);
        root.querySelector('[data-value="input"]').textContent=`z = ${f(p.x)}${signed(p.y)}i；r = ${f(state.radius)}`;
        root.querySelector('[data-value="output"]').textContent=`连续 log z = ${f(p.real)}${signed(p.imag)}i`;
        root.querySelector('[data-value="principal"]').textContent=p.principal===null ? '解析主支 Log z：此点在负实轴上，不属于定义域' : `解析主支 Log z = ${f(p.real)}${signed(p.principal)}i`;
        root.querySelector('[data-action="turn"]').disabled=state.theta+TAU>4*Math.PI+1e-8;
      };
    }
    function render(){
      if(disposed)return;
      camera.position.set(target.x+state.distance*Math.cos(state.elevation)*Math.cos(state.azimuth),target.y+state.distance*Math.sin(state.elevation),target.z+state.distance*Math.cos(state.elevation)*Math.sin(state.azimuth));
      camera.lookAt(target);renderer.render(scene,camera);
    }
    function sync(){
      for(const name of ['radius','theta']){
        const range=root.querySelector(`[data-parameter="${name}"]`);range.value=String(name==='theta'?state[name]/Math.PI:state[name]);
        root.querySelector(`[data-label="${name}"]`).textContent=name==='theta'?`${f(state.theta/Math.PI)}π` : f(state.radius);
        root.dataset[name]=String(state[name]);
      }
      updateModel();render();
    }
    function visible(){return root.isConnected && !global.document.hidden && root.getClientRects().length>0 && global.getComputedStyle(root).visibility!=='hidden';}
    function pause(message='已暂停。可调整参数或旋转视角。'){
      state.playing=false;root.dataset.playing='false';cancelAnimationFrame(frame);frame=0;last=0;
      root.querySelector('[data-action="play"]').disabled=false;root.querySelector('[data-action="pause"]').disabled=true;status.textContent=message;
    }
    function tick(now){
      if(!state.playing||disposed)return;
      if(!visible()){pause('页面或图解不可见，播放已暂停。');return;}
      const dt=last?Math.min((now-last)/1000,.1):0;last=now;
      if(kind==='sphere'){
        state.radius=Math.min(12,state.radius+dt*(.45+state.radius*.22));
        if(state.radius>=12)pause('演示停在有限 r = 12；Q 仍未到达北极。');
      } else {
        state.theta=Math.min(4*Math.PI,state.theta+dt*.65);
        if(state.theta>=4*Math.PI)pause('已到显示范围上端；连续辐角仍可在数学上继续增长。');
      }
      sync();if(state.playing)frame=requestAnimationFrame(tick);
    }
    function play(){
      if(disposed||!visible())return;
      if(kind==='sphere'&&state.radius>=12)state.radius=.2;
      if(kind==='log'&&state.theta>=4*Math.PI)state.theta=-TAU;
      state.playing=true;root.dataset.playing='true';last=0;status.textContent=kind==='sphere'?'播放中：r 增大，球面点趋近北极。':'播放中：θ 连续增长，绕一圈升高 2π。';
      root.querySelector('[data-action="play"]').disabled=true;root.querySelector('[data-action="pause"]').disabled=false;
      cancelAnimationFrame(frame);frame=requestAnimationFrame(tick);
    }
    function reset(){pause('已恢复初始参数和视角。');Object.assign(state,initial,initialCamera);sync();}
    const cleanups=[];
    function listen(node,event,fn,options){node.addEventListener(event,fn,options);cleanups.push(()=>node.removeEventListener(event,fn,options));}
    for(const range of root.querySelectorAll('[data-parameter]'))listen(range,'input',()=>{pause();state[range.dataset.parameter]=Number(range.value)*(range.dataset.parameter==='theta'?Math.PI:1);sync();});
    for(const button of root.querySelectorAll('[data-action]'))listen(button,'click',()=>{
      if(button.dataset.action==='play')play();if(button.dataset.action==='pause')pause();if(button.dataset.action==='reset')reset();
      if(button.dataset.action==='turn'){pause('同一非零 z 绕一圈：连续 log 的虚部增加 2π。');state.theta=Math.min(4*Math.PI,state.theta+TAU);sync();}
    });
    listen(canvas,'pointerdown',event=>{drag={x:event.clientX,y:event.clientY};canvas.setPointerCapture(event.pointerId);canvas.focus({preventScroll:true});});
    listen(canvas,'pointermove',event=>{if(!drag)return;state.azimuth-=(event.clientX-drag.x)*.008;state.elevation=Math.max(-1.2,Math.min(1.35,state.elevation+(event.clientY-drag.y)*.006));drag={x:event.clientX,y:event.clientY};render();});
    listen(canvas,'pointerup',()=>{drag=null;});listen(canvas,'pointercancel',()=>{drag=null;});
    listen(canvas,'wheel',event=>{event.preventDefault();state.distance=Math.max(3,Math.min(50,state.distance*Math.exp(event.deltaY*.001)));render();},{passive:false});
    listen(canvas,'keydown',event=>{
      const actions={ArrowLeft:()=>state.azimuth+=.12,ArrowRight:()=>state.azimuth-=.12,ArrowUp:()=>state.elevation=Math.min(1.35,state.elevation+.10),ArrowDown:()=>state.elevation=Math.max(-1.2,state.elevation-.10),'+':()=>state.distance=Math.max(3,state.distance*.9),'=':()=>state.distance=Math.max(3,state.distance*.9),'-':()=>state.distance=Math.min(50,state.distance/0.9),Home:()=>Object.assign(state,initialCamera)};
      if(actions[event.key]){event.preventDefault();actions[event.key]();render();}
    });
    function resize(){if(disposed)return;const width=host.clientWidth,height=host.clientHeight;if(!width||!height)return;renderer.setPixelRatio(Math.min(global.devicePixelRatio||1,2));renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();for(const item of labels){const h=item.pixels*2*Math.tan(camera.fov*Math.PI/360)/height;item.sprite.scale.set(h*item.aspect,h,1);}render();}
    const resizeObserver=new ResizeObserver(resize);resizeObserver.observe(host);
    const intersection=new IntersectionObserver(entries=>{if(entries.some(e=>!e.isIntersecting)&&state.playing)pause('图解已移出视野，播放已暂停。');});intersection.observe(root);
    listen(global.document,'visibilitychange',()=>{if(global.document.hidden)pause('页面已隐藏，播放已暂停。');});
    listen(global,'pagehide',()=>pause('离开页面，播放已暂停。'));
    listen(canvas,'webglcontextlost',event=>{event.preventDefault();pause();unavailable('WebGL 画面已中断。下面显示静态矢量后备图；重新载入页面可重试。');});
    function dispose(){if(disposed)return;pause();disposed=true;resizeObserver.disconnect();intersection.disconnect();mutation.disconnect();for(const clean of cleanups)clean();scene.traverse(node=>{if(node.geometry)node.geometry.dispose();for(const material of Array.isArray(node.material)?node.material:[node.material])if(material){if(material.map)material.map.dispose();material.dispose();}});renderer.dispose();canvas.remove();controllers.delete(controller);delete root.__spatial;}
    const mutation=new MutationObserver(()=>{if(!root.isConnected)dispose();else if(!visible()&&state.playing)pause('图解已隐藏，播放已暂停。');});mutation.observe(global.document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden','style','class','open']});
    const controller={play,pause,reset,dispose};controllers.add(controller);root.__spatial=controller;
    pause('已就绪。拖拽画面旋转，或按播放观察变化。');sync();resize();return controller;
  }
  function mountAll(scope=global.document){for(const root of scope.querySelectorAll('[data-spatial]'))if(!root.hidden)mount(root);for(const node of scope.querySelectorAll('[data-tex]'))if(global.katex)global.katex.render(node.dataset.tex,node,{throwOnError:false,displayMode:node.classList.contains('spatial-equation')});}
  function selectScene(){
    const query=new URLSearchParams(global.location.search);
    const hash=global.location.hash.slice(1);
    const selected=['sphere','log'].includes(hash)?hash:query.get('scene');
    const filtered=['sphere','log'].includes(selected);
    for(const root of global.document.querySelectorAll('[data-spatial]')){
      const shown=!filtered||root.dataset.spatial===selected;
      if(!shown&&root.__spatial)root.__spatial.dispose();
      root.hidden=!shown;
    }
    global.document.body.dataset.embedded=query.get('embed')==='1'?'true':'false';
    if(query.get('embed')==='1')for(const link of global.document.querySelectorAll('.spatial-footer a:not([target])'))link.target='_top';
    mountAll();
  }
  const api={stereographic,logarithmPoint,mount,mountAll};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  global.AIFSPracticeSpatial=api;
  if(global.document){if(global.document.readyState==='loading')global.document.addEventListener('DOMContentLoaded',selectScene,{once:true});else selectScene();global.addEventListener('hashchange',selectScene);}
})(typeof window!=='undefined'?window:globalThis);
