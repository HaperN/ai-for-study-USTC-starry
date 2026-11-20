/* Execute candidate pages in a real isolated Chromium; never permit network access. */
const fs=require('fs'), path=require('path'), os=require('os'), {spawn}=require('child_process');
const [chromePath,root,policyJson]=process.argv.slice(2);
const displayPolicy=policyJson ? JSON.parse(policyJson) : null;
const usabilityOnly=displayPolicy?.review_mode==='balanced';
/* 目录模式跑在 file:// 上（代理把回环也挡掉更保险）；**服务模式**要访问本机 HTTP，
   回环必须放行，否则连被测服务都连不上（外网仍由死代理 + Fetch 白名单挡住）。 */
const servedMode=/^https?:\/\//i.test(root);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
/* requestId → url：loadingFailed 只给 id，不记 url 就分不清是哪条资源失败。 */
const requestUrlById=new Map();
/* 进度只写 stderr：stdout 必须是**唯一一段**纯 JSON（调用方 json.loads）。 */
const progress=(...parts)=>{try{process.stderr.write('[browser_acceptance] '+parts.join(' ')+'\n');}catch(_){}};
const profile=fs.mkdtempSync(path.join(os.tmpdir(),'aifs-browser-'));
const chrome=spawn(chromePath,['--headless=new','--no-sandbox','--disable-dev-shm-usage',
 /* 验收要能自己把播放点起来：真实读者是用手点（那是 user gesture），无人值守的检查里
    没有手势，所以显式关掉自动播放限制。--mute-audio 与无音轨产物无关，只是别出声。 */
 '--mute-audio','--autoplay-policy=no-user-gesture-required',
 '--disable-background-timer-throttling','--disable-renderer-backgrounding',
 '--disable-backgrounding-occluded-windows',
 '--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader',
 '--proxy-server=http://127.0.0.1:9',(servedMode?'--proxy-bypass-list=localhost,127.0.0.1,[::1]':'--proxy-bypass-list=<-loopback>'),
 '--disable-background-networking','--disable-sync','--no-first-run',
 '--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{stdio:['ignore','pipe','pipe'],detached:true});
let url='',stderr=''; chrome.stderr.on('data',b=>{stderr+=b;const m=/ws:\/\/[^\s]+/.exec(stderr);if(m)url=m[0]});
const evidence={runtime_errors:[],missing_images:[],failed_requests:[],external_requests:[],animation_requests:[],animation_loading_failed:[],outside_attempts:[],formula_errors:[],model_errors:[],pages:[],interactions:[],screenshots:[],harmonic_models:[],animation_errors:[],animation:null};
/* 原书资料里无法解析、按原文保留且未渲染的公式。这是**信息项**：不进 ok 判定，
   否则原书自身的排版残缺会永久挡住交付；但也绝不能不留痕，否则就成了悄悄跳过。 */
evidence.source_formula_unrendered=[];
evidence.media_layout_errors=[];evidence.reader_content_errors=[];
// 修改计划 F3：等页面的就绪信号而不是固定 350ms；未就绪（超时）与启动报错分开记录。
evidence.page_ready=[];evidence.reader_not_ready=[];evidence.reader_boot_errors=[];evidence.appearance_warnings=[];evidence.media_geometry=[];
evidence.video_playback=[];
let ws;
const frameSessions=new Map();
function verifyMediaGeometry(profile,policy) {
 const errors=[],warnings=[],surfaces=[];
 const viewport={inner_width:innerWidth,client_width:document.documentElement.clientWidth,
  scrollbar_width:innerWidth-document.documentElement.clientWidth,scroll_height:document.documentElement.scrollHeight};
 const expectedWidth=Math.min(policy.maximum_media_width_px||profile.max_content_width,viewport.client_width-2*policy.viewport_gutter_px);
 if(viewport.inner_width!==profile.width || !Number.isFinite(expectedWidth) || expectedWidth<profile.content_width)
  warnings.push('actual viewport is outside reviewed width coverage');
 if(document.documentElement.scrollWidth>viewport.client_width)warnings.push('reader has horizontal overflow');
 for(const viewport of document.querySelectorAll('.reader-media .media-viewport')) {
  const media=viewport.querySelector(':scope > img,:scope > svg,:scope > video');
  if(!media)continue;
  const box=viewport.getBoundingClientRect(),image=media.getBoundingClientRect();
  const id=viewport.closest('[data-source-id]')?.dataset.sourceId || media.tagName;
  // Publication lays media out beside prose. Read that actual column, whose
  // CSS comes from the shared display policy, rather than assuming the
  // single-column maximum or inferring scrollbar width from a viewport name.
  const column=viewport.closest('.reader-media-column');
  const columnStyle=column&&getComputedStyle(column);
  let surfaceWidth=column ? Math.min(policy.maximum_media_width_px,
   column.getBoundingClientRect().width-(parseFloat(columnStyle.borderLeftWidth)||0)-(parseFloat(columnStyle.borderRightWidth)||0)
    -(parseFloat(columnStyle.paddingLeft)||0)-(parseFloat(columnStyle.paddingRight)||0)) : expectedWidth;
  const player=viewport.closest('.animation-player');
  if(player) {const style=getComputedStyle(player);surfaceWidth-=(parseFloat(style.borderLeftWidth)||0)+(parseFloat(style.borderRightWidth)||0);}
  if(surfaceWidth<profile.content_width-1)warnings.push(id+': actual column is below reviewed width coverage');
  if(Math.abs(box.width-surfaceWidth)>1 || Math.abs(image.width-box.width)>1 || Math.abs(image.left-box.left)>1)
   warnings.push(id+': reviewed media width changed ('+box.width+'/'+image.width+', expected '+surfaceWidth+')');
  const css=getComputedStyle(media);
  if(['cover','none'].includes(css.objectFit) || css.clipPath!=='none' || getComputedStyle(viewport).clipPath!=='none'
    || image.height<=0 || image.top<box.top-.5 || image.bottom>box.bottom+.5)
   errors.push(id+': media cropped or empty');
  // An unchanged SVG box can still be cut by a reader/card ancestor. Page
  // scrolling is allowed; nested clipping must never conceal reviewed pixels.
  for(let parent=viewport.parentElement;parent&&parent!==document.body;parent=parent.parentElement) {
   const style=getComputedStyle(parent),rect=parent.getBoundingClientRect();
   const left=rect.left+parent.clientLeft,top=rect.top+parent.clientTop;
   if(style.clipPath!=='none'
     || (style.overflowX!=='visible'&&(image.left<left-.5||image.right>left+parent.clientWidth+.5))
     || (style.overflowY!=='visible'&&(image.top<top-.5||image.bottom>top+parent.clientHeight+.5))) {
    errors.push(id+': media cropped by reader container');break;
   }
  }
  const size=media.tagName.toLowerCase()==='svg' ? media.viewBox.baseVal : {width:media.naturalWidth||media.videoWidth,height:media.naturalHeight||media.videoHeight};
  if(size?.width>0 && size?.height>0 && Math.abs(image.height-image.width*size.height/size.width)>2)warnings.push(id+': media aspect ratio changed');
  for(const text of media.querySelectorAll('text,tspan')) {
   if(!Array.from(text.childNodes).some(n=>n.nodeType===3&&n.textContent.trim()))continue;
   const matrix=text.getScreenCTM();
   if(matrix && parseFloat(getComputedStyle(text).fontSize)*Math.hypot(matrix.c,matrix.d)+.01<profile.min_font_px)
    warnings.push(id+': SVG label below reviewed display minimum');
   const label=text.getBoundingClientRect();
   if(label.width<=0 || label.height<=0 || getComputedStyle(text).visibility==='hidden' || parseFloat(getComputedStyle(text).opacity)===0)
    errors.push(id+': critical SVG label is unavailable');
   if(label.left<image.left-.5||label.right>image.right+.5||label.top<image.top-.5||label.bottom>image.bottom+.5)
    errors.push(id+': SVG label cropped');
  }
  surfaces.push({id,width:box.width,media_width:image.width,height:image.height,expected_width:surfaceWidth});
 }
 return {errors,warnings,surfaces,viewport,expected_media_width:surfaces[0]?.expected_width??expectedWidth,reviewed_minimum_width:profile.content_width};
}
function verifyReaderContent(unit) {
 const reader=window.__DATA__?.units?.[unit]?.reader_document;
 if(!reader)return [];
 const errors=[];
 for(const field of reader.blocks || []) {
  if(!['/title','/reader_intro','/body_markdown','/summary'].includes(field.json_pointer) || !field.raw_text?.trim())continue;
  const element=Array.from(document.querySelectorAll('[data-reader-pointer]')).find(e=>e.dataset.readerPointer===field.json_pointer);
  if(!element){errors.push(field.json_pointer+': required reader field is missing');continue;}
  const box=element.getBoundingClientRect(),style=getComputedStyle(element);
  let hidden=box.width<=0 || box.height<=0;
  for(let node=element;node&&node!==document.body;node=node.parentElement){
   const css=getComputedStyle(node);
   if(css.display==='none'||css.visibility==='hidden'||parseFloat(css.opacity)===0)hidden=true;
  }
  if(hidden)errors.push(field.json_pointer+': required reader field is unavailable');
  if(['hidden','clip'].includes(style.overflowY)&&element.scrollHeight>element.clientHeight+1)
   errors.push(field.json_pointer+': required reader field is clipped');
 }
 return errors;
}
function verifyUsableContent() {
 const main=document.querySelector('main,#content') || document.body;
 const text=(main.innerText||'').trim();
 if(!text)return ['页面正文为空'];
 if(/^正在加载[.。…\s]*$/.test(text))return ['页面仍停在加载提示'];
 const box=main.getBoundingClientRect(),style=getComputedStyle(main);
 if(box.width<=0||box.height<=0||style.display==='none'||style.visibility==='hidden')return ['页面正文不可见'];
 // The reader uses this class for both title and body. An optional empty
 // title has zero height; only the teaching body establishes body visibility.
 const articles=Array.from(main.querySelectorAll('.reader-document'))
  .filter(article=>!article.closest('.unit-head'));
 const expectsReader=Object.values(window.__DATA__?.units||{}).some(unit=>
  unit.reader_document?.blocks?.some(block=>block.json_pointer!=='/title' && typeof block.raw_text==='string' && block.raw_text.trim()));
 if(expectsReader&&!articles.length)return ['教学正文区域未显示'];
 for(const article of articles) {
  const rect=article.getBoundingClientRect();
  if(rect.width<=0||rect.height<=0)return ['教学正文区域不可见'];
  for(let el=article;el;el=el.parentElement) {
   const css=getComputedStyle(el);
   if(css.display==='none'||css.visibility==='hidden'||parseFloat(css.opacity)===0)return ['教学正文区域不可见'];
  }
 }
 return [];
}
// Exercise actual playback once per displayed unit, without requiring captions,
// step declarations or frame IDs. A custom player's play button must work too.
async function verifyVideoPlayback(unit) {
 const results=[];
 const videos=Array.from(document.querySelectorAll('video'));
 const sourceOf=video=>video.currentSrc||video.getAttribute('src')||video.querySelector('source')?.src||'';
 const absolute=source=>{try{return new URL(source,document.baseURI).href;}catch(_){return source;}};
 const remaining=videos.map(video=>absolute(sourceOf(video)));
 const payload=window.__DATA__?.animations?.[unit];
 // Check delivered MP4s, not optional captions/steps or legacy review metadata.
 // Enumerating only mounted players silently accepts a page where none mounted.
 for(const item of (Array.isArray(payload)?payload:payload?[payload]:[])) {
  const relative=item?.resources?.mp4?.relative_path;
  if(!relative)continue;
  const base=String(item.base||'').replace(/\/?$/,'/');
  const source=(item.base?base:'')+String(relative).replace(/^\.?\//,'');
  const index=remaining.indexOf(absolute(source));
  if(index>=0)remaining.splice(index,1);
  else results.push({source,request_id:item.request_id||'',readyState:0,currentTime:0,
   width:0,height:0,error:'动画 '+(item.request_id||relative)+' 的交付视频未挂载到页面',ok:false});
 }
 for(const player of document.querySelectorAll('[data-animation-player]')) {
  if(!player.querySelector('video'))results.push({source:'',readyState:0,currentTime:0,
   width:0,height:0,error:'播放器未加载视频',ok:false});
 }
 for(const video of videos) {
  const source=sourceOf(video);
  let playError='';
  video.pause();
  try {video.currentTime=0;} catch(_) {}
  const start=video.currentTime;
  const button=video.closest('[data-animation-player]')?.querySelector('[data-action="play"]');
  try {
   if(button)button.click();
   else video.play().catch(error=>{playError=String(error);});
  } catch(error) {playError=String(error);}
  const deadline=Date.now()+5000;
  while(Date.now()<deadline) {
   if(video.error||playError)break;
   if(video.readyState>=2&&video.videoWidth>0&&video.videoHeight>0&&video.currentTime>start+0.05)break;
   await new Promise(resolve=>setTimeout(resolve,50));
  }
  const advanced=video.currentTime>start+0.05;
  const error=playError||(video.error ? 'media error '+video.error.code+': '+video.error.message : '');
  results.push({source,readyState:video.readyState,currentTime:video.currentTime,
   width:video.videoWidth,height:video.videoHeight,
   error:error||(!advanced?'视频未实际开始播放':''),
   ok:!error&&advanced&&video.readyState>=2&&video.videoWidth>0&&video.videoHeight>0});
  video.pause();
 }
 return results;
}
// Runs inside the candidate page. The template has one fixed mathematical contract.
function verifyHarmonicModels(unit) {
 const errors=[], results=[];
 for(const id of Object.keys(window.__DATA__?.units?.[unit]?.models||{})) {
  const spec=window.__DATA__?.models?.[id];
  if(spec?.template!=='harmonic-contours')continue;
  const box=Array.from(document.querySelectorAll('figure[data-model-id]')).find(e=>e.dataset.modelId===id);
  if(!box){errors.push(id+': harmonic figure 未挂载');continue;}
  const query=s=>box.querySelector(s), value=n=>query('[data-harmonic-value="'+n+'"]')?.textContent||'';
  const fail=why=>errors.push(id+': '+why);
  const x=query('input[data-axis="x"]'), y=query('input[data-axis="y"]');
  const extent=spec.params?.extent;
  if(!query('svg.harmonic-plot')||!x||!y||!Number.isFinite(extent)||extent<1||extent>4) {
   fail('harmonic SVG 或参数控件未挂载');continue;
  }
  const original=[x.value,y.value];
  function select(a,b) {
   x.value=String(a);x.dispatchEvent(new Event('input',{bubbles:true}));
   y.value=String(b);y.dispatchEvent(new Event('input',{bubbles:true}));
  }
  function snapshot(a,b,origin=false) {
   if(origin) {
    const button=Array.from(box.querySelectorAll('button')).find(e=>e.textContent.includes('查看原点例外'));
    if(button)button.click();else fail('harmonic 缺原点例外控件');
   } else {
    select(a,b);
    // Range controls quantize relative to min, which need not put zero on a step.
    a=Number(x.value);b=Number(y.value);
   }
   const u=value('u'),v=value('v'),dot=value('dot'),angle=value('angle');
   const curves=['u','v'].map(n=>query('[data-contour="'+n+'"]')?.getAttribute('d')||'');
   const gradients=['grad-u','grad-v'].map(n=>(value(n).replace(/−/g,'-').match(/-?\d+(?:\.\d+)?/g)||[]).map(Number));
   const expected=[[2*a,-2*b],[2*b,2*a]];
   if(!u.trim()||!v.trim()||!dot.trim()||Math.abs(Number(u)-(a*a-b*b))>0.011
     ||Math.abs(Number(v)-2*a*b)>0.011||Number(dot)!==0
     ||![Number(u),Number(v),Number(dot)].every(Number.isFinite)
     ||gradients.some((g,i)=>g.length!==2||g.some((n,j)=>Math.abs(n-expected[i][j])>0.011)))
    fail('harmonic 参数改变后的函数值/梯度不符合固定 z² 数学对象');
   if(curves.some(d=>!d||/NaN|Infinity/.test(d)))fail('harmonic 等值曲线无有效路径');
   if(a!==0||b!==0) {if(!angle.includes('90'))fail('非原点切线夹角不是 90°');}
   return {point:[a,b],u,v,dot,angle,curves,gradients};
  }
  const rounded=n=>Math.round(n*100)/100;
  const first=snapshot(rounded(extent/4),rounded(extent/2)),second=snapshot(rounded(extent*3/4),rounded(extent/2));
  if(first.curves.some((d,i)=>d===second.curves[i]))fail('harmonic 参数改变后等值曲线没有响应');
  const origin=snapshot(0,0,true),status=query('.harmonic-status')?.textContent||'';
  const directions=Array.from(box.querySelectorAll('[data-tangent],[data-gradient]'));
  if(!origin.angle.includes('不适用')||!status.includes('原点')||!status.includes('不能套用正交结论')
      ||directions.length!==4||directions.some(e=>e.getAttribute('visibility')!=='hidden'))
   fail('harmonic 原点必须明确不适用正交结论并隐藏无定义方向');
  results.push({id,first:{point:first.point,u:first.u,v:first.v,gradients:first.gradients},
                    second:{point:second.point,u:second.u,v:second.v,gradients:second.gradients},
                    origin:{angle:origin.angle,status},responsive:first.curves.every((d,i)=>d!==second.curves[i])});
  select(Number(original[0]),Number(original[1]));
 }
 return {errors,results};
}

/* ── T10 服务模式：真实 sandbox iframe 里的播放检查 ─────────────────────────
 * 只做"能不能真的播、字幕读不读得到、换节会不会停"这几件**只有真浏览器能回答**的事。
 * 逐帧/倍速/两个视口的细粒度证据由 `tests/browser/animation_player.browser.cjs` 出。
 * 页面里没有播放器时**不算失败**（没有动画请求的普通单元可以合法地没有动画）。 */
async function runServedAnimation({send,evaluate,evidence,base,sleep}){
 progress('served: navigate', base + '/_t10-harness.html');
 const probe={
  wait:`(async()=>{const d=Date.now()+15000;while(Date.now()<d){const h=document.querySelector('[data-animation-player]');if(h)return{ok:true,unit:h.getAttribute('data-unit'),hasVideo:!!h.querySelector('video')};await new Promise(r=>setTimeout(r,100));}return{ok:false};})()`,
  play:`(async()=>{const h=document.querySelector('[data-animation-player]');const v=h.querySelector('video');const b=h.querySelector('[data-action="play"]');if(!b)return{ok:false,error:'no play button'};b.click();const d=Date.now()+15000;while(Date.now()<d){if(v.readyState>=2&&!v.paused&&v.currentTime>0.2)break;if(v.error)break;await new Promise(r=>setTimeout(r,100));}const s={readyState:v.readyState,currentTime:v.currentTime,duration:Number.isFinite(v.duration)?v.duration:null,width:v.videoWidth||0,height:v.videoHeight||0,error:v.error?{code:v.error.code,message:v.error.message}:null,poster:v.getAttribute('poster')};v.pause();return s;})()`,
  captions:`(async()=>{const h=document.querySelector('[data-animation-player]');const v=h.querySelector('video');const el=v.querySelector('track');if(!el)return{ok:false,error:'no track'};const tt=el.track||el;for(let i=0;i<80&&el.readyState!==2;i++)await new Promise(r=>setTimeout(r,100));const text=Array.from(tt.cues||[]).map(c=>c.text).join(' | ');return{ok:el.readyState===2,cues:(tt.cues||[]).length,mode:tt.mode,readyState:el.readyState,src:el.getAttribute('src'),text,hasChinese:/[\u4e00-\u9fff]/.test(text)};})()`,
  switchAway:`(async function(other){const play=()=>Array.from(document.querySelectorAll('video')).filter(v=>!v.paused&&!v.ended&&v.currentTime>0).length;const v=document.querySelector('[data-animation-player] video');if(v){try{await v.play();}catch(e){}await new Promise(r=>setTimeout(r,500));}location.hash='#/'+other;await new Promise(r=>setTimeout(r,1500));return{stillPlaying:play(),players:document.querySelectorAll('[data-animation-player]').length,videos:document.querySelectorAll('video').length};})`,
  management:`(async()=>{const race=(p,ms)=>Promise.race([p,new Promise((_,rej)=>setTimeout(()=>rej(new Error('probe-timeout')),ms))]);const out={};try{const r=await race(fetch('/api/books',{cache:'no-store'}),5000);out.fetched=r.status;}catch(e){out.blocked=String(e);}try{const r=await race(fetch('http://127.0.0.1:1/api/books'),5000);out.external_fetched=r.status;}catch(e){out.external_blocked=String(e);}return out;})()`
 };
 await send('Page.navigate',{url:base+'/_t10-harness.html'});
 let infos=[],frame=null;
 for(let i=0;i<80&&!frame;i++){
  await sleep(250);
  infos=(await send('Target.getTargets')).targetInfos;
  frame=infos.find(t=>t.type==='iframe');
 }
 evidence.animation_targets=infos.map(t=>({type:t.type,url:t.url}));
 progress('served: targets', JSON.stringify(evidence.animation_targets));
 if(!frame){evidence.animation_errors.push('导出页没有作为 sandbox iframe 的 OOPIF target 出现，无法在 sandbox 内取证');evidence.animation={present:false,reason:'no iframe target'};return;}
 const attached=frameSessions.has(frame.targetId)?await frameSessions.get(frame.targetId):await send('Target.attachToTarget',{targetId:frame.targetId,flatten:true});
 progress('served: attached', attached.sessionId);
 const session=attached.sessionId;
 await send('Runtime.enable',{},session);
 await send('Network.enable',{},session);
 await send('Fetch.enable',{patterns:[{urlPattern:'*'}]},session);
 const inFrame=async(expression)=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true},session);if(r.exceptionDetails)throw new Error(((r.exceptionDetails.exception||{}).description)||r.exceptionDetails.text||'evaluate failed');return r.result?r.result.value:undefined;};
 const details={present:false};
 let ready=null;
 for(let i=0;i<20&&!(ready&&ready.ok);i++){ready=await inFrame(probe.wait).catch(e=>({ok:false,error:String(e)}));if(!ready||!ready.ok)await sleep(300);}
 if(!ready||!ready.ok){
  details.diagnosis=await inFrame(`({href:location.href,title:document.title,data:typeof window.__DATA__,animations:Object.keys((window.__DATA__||{}).animations||{}),players:document.querySelectorAll('[data-animation-player]').length,errors:Array.from(document.querySelectorAll('[data-animation-error]')).map(e=>e.textContent)})`).catch(e=>({error:String(e)}));
  progress('served: diagnosis', JSON.stringify(details.diagnosis));
 }
 if(!ready||!ready.ok){progress('served: no player');details.present=false;evidence.animation=details;return;}
 progress('served: player', ready.unit);
 details.present=true;details.unit=ready.unit;details.hasVideo=ready.hasVideo;
 if(!ready.hasVideo){evidence.animation_errors.push('播放器挂上了但没有 <video> 节点（缺 mp4 时必须显示错误条与文字稿，不能假装在播）');evidence.animation=details;return;}
 details.play=await inFrame(probe.play);
 progress('served: played', JSON.stringify(details.play).slice(0,200));
 const play=details.play;
 if(!(play.readyState>=2&&play.currentTime>0.2)){
  evidence.animation_errors.push('视频没有真的播起来（readyState='+play.readyState+'，error='+JSON.stringify(play.error)+'）—— HTTP 200／文件非空都不算通过');
 }else{
  if(!(play.duration>0))evidence.animation_errors.push('duration 不是正数：'+play.duration);
  if(!(play.width>0&&play.height>0))evidence.animation_errors.push('画面尺寸为 0：'+play.width+'x'+play.height);
 }
 details.captions=await inFrame(probe.captions);
 if(!details.captions.ok||!details.captions.cues||!details.captions.hasChinese){
  evidence.animation_errors.push('字幕读不到中文 cue：'+JSON.stringify(details.captions));
 }
 const dataScope=await inFrame('(window.__DATA__&&window.__DATA__.scope)||[]').catch(()=>[]);
 const other=(dataScope||[]).find(u=>u!==ready.unit);
 details.management=await inFrame(probe.management).catch(e=>({error:String(e)}));
 if(details.management&&details.management.fetched!==undefined){
  evidence.animation_errors.push('教材页调到了管理 API（connect-src \'none\' 没挡住）：'+JSON.stringify(details.management));
 }
 if(other){
  details.switchAway=await inFrame('('+probe.switchAway.toString()+')('+JSON.stringify(other)+')').catch(e=>({error:String(e)}));
  const away=details.switchAway||{};
  if(away.stillPlaying>0||away.videos>0){
   evidence.animation_errors.push('换节之后上一节的播放器还在播／没摘干净：'+JSON.stringify(away));
  }
 }
 const external=evidence.external_requests.filter(u=>/^https?:/i.test(u)&&!u.startsWith(base));
 if(external.length)evidence.animation_errors.push('教材页发出了外部请求：'+JSON.stringify(external.slice(0,3)));
 progress('served: done');
 evidence.animation=details;
}

async function main(){
 for(let i=0;i<100&&!url;i++)await sleep(50);
 if(!url)throw new Error('Chromium startup failed: '+stderr.slice(-800));
 const targets=await(await fetch('http://127.0.0.1:'+new URL(url).port+'/json/list')).json();
 ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);
 await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
 let id=0;const pending=new Map();
 /* `sessionId`：flatten 之后**必须**带上，否则命令发到顶级 page target ——
    服务模式下"进沙箱 frame 里求值"就会变成"在父页里找播放器"，永远找不到
    （T10 实测踩到：40 次 × 15s 的超时才暴露出来）。 */
 function send(method,params={},sessionId=null){return new Promise((resolve,reject)=>{const n=++id;
  /* 45s：服务模式里 playAndLoad 这类探针**在页面里**就要等 15–20s（要真的等出画面），
     比它还短的 CDP 超时会把'等得久'误报成'量不到'。 */
  const timer=setTimeout(()=>{pending.delete(n);reject(new Error('CDP timeout: '+method))},45000);
  pending.set(n,{resolve,reject,timer});
  const payload={id:n,method,params};if(sessionId)payload.sessionId=sessionId;
  ws.send(JSON.stringify(payload));})}
 ws.onmessage=e=>{const m=JSON.parse(e.data);
  if(m.method==='Target.attachedToTarget'){
   const q=m.params;
   const setup=(async()=>{await send('Runtime.enable',{},q.sessionId);await send('Network.enable',{},q.sessionId);await send('Fetch.enable',{patterns:[{urlPattern:'*'}]},q.sessionId);await send('Runtime.runIfWaitingForDebugger',{},q.sessionId);return{sessionId:q.sessionId};})();
   frameSessions.set(q.targetInfo.targetId,setup);setup.catch(err=>evidence.runtime_errors.push(String(err)));
  }
if(m.id&&pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);clearTimeout(p.timer);m.error?p.reject(new Error(m.error.message)):p.resolve(m.result);}
  if(m.method==='Network.webSocketCreated')evidence.external_requests.push(m.params.url);
  if(m.method==='Network.requestWillBeSent'&&m.params.request){requestUrlById.set(m.params.requestId,m.params.request.url);}
  if(m.method==='Network.requestWillBeSent'&&servedMode){evidence.animation_requests=evidence.animation_requests||[];evidence.animation_requests.push({url:m.params.request.url,sessionId:m.sessionId||null,type:m.params.type});}
  if(m.method==='Network.loadingFailed'&&!m.params.canceled&&servedMode){evidence.animation_loading_failed=evidence.animation_loading_failed||[];evidence.animation_loading_failed.push({url:m.params.requestId,error:m.params.errorText,blockedReason:m.params.blockedReason||null,type:m.params.type});}
  if(m.method==='Runtime.exceptionThrown') evidence.runtime_errors.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);
  if(m.method==='Runtime.consoleAPICalled'&&m.params.type==='error')evidence.runtime_errors.push(m.params.args.map(a=>a.value||a.description).join(' '));
  if(m.method==='Network.loadingFailed'&&!m.params.canceled)evidence.failed_requests.push({url:requestUrlById.get(m.params.requestId)||'',error:m.params.errorText,requestId:m.params.requestId,sessionId:m.sessionId,blockedReason:m.params.blockedReason,type:m.params.type});
  if(m.method==='Fetch.requestPaused') {const q=m.params;if(q.networkId)requestUrlById.set(q.networkId,q.request.url);let deny=/^https?:/i.test(q.request.url);
   if(/^https?:/i.test(root)&&q.request.url.startsWith(root.replace(/\/[^/]*$/,'')+'/'))deny=false;
   if(/^https?:/i.test(root)&&q.request.url.startsWith(root))deny=false;
   if(/^file:/i.test(q.request.url)){const target=decodeURIComponent(new URL(q.request.url).pathname);deny=!(target===root||target.startsWith(root+path.sep));}
   if(deny){
    /* 探针**自己**故意往 127.0.0.1:1 打的那一条是"证明页面连不出去"的对照组，
       不是页面发出的外部请求，所以单独记，不污染 external_requests（判据是后者必须为 0）。 */
    if(/^https?:\/\/127\.0\.0\.1:1\//.test(q.request.url))evidence.outside_attempts.push(q.request.url);
    else evidence.external_requests.push(q.request.url);
    send('Fetch.failRequest',{requestId:q.requestId,errorReason:'BlockedByClient'},m.sessionId).catch(()=>{});}
   else send('Fetch.continueRequest',{requestId:q.requestId},m.sessionId).catch(()=>{});
  }
 };
 await send('Runtime.enable');await send('Page.enable');await send('Network.enable');
 await send('Target.setAutoAttach',{autoAttach:true,waitForDebuggerOnStart:true,flatten:true});
 /* 目录模式跑在 file:// 上，断网不会挡住被测页面；**服务模式**要访问本机 HTTP，
    所以在服务模式下不启用 offline（外网仍由死代理 + Fetch 白名单挡住）。 */
 const served=servedMode;
 if(!served)await send('Network.emulateNetworkConditions',{offline:true,latency:0,downloadThroughput:0,uploadThroughput:0});
 await send('Fetch.enable',{patterns:[{urlPattern:'*'}]});
 async function waitReaderReady(file){
  // 外层整体超时 90 秒：单页等待默认最多 60 秒。没有就绪信号的页面（旧页面、测试夹具）
  // 在加载完成且主区不再显示“正在加载”时按旧页面处理，不必等到超时。
  const started=Date.now(),limit=Number(process.env.AIFS_READER_READY_TIMEOUT_MS||60000),rel=path.relative(root,file);
  const probe=`document.documentElement.getAttribute('data-reader-ready')||(!window.__DATA__&&document.readyState==='complete'?'static':'')||((document.readyState==='complete'&&!/正在加载/.test((document.querySelector('main')||document.body||{}).textContent||''))?'legacy':'')`;
  let state='';
  while(Date.now()-started<limit){state=await evaluate(probe).catch(()=>'');if(state)break;await sleep(100);}
  const seconds=(Date.now()-started)/1000;
  const units=state&&state!=='static'?await evaluate('Number(document.documentElement.getAttribute("data-reader-units")||Object.keys(window.__DATA__?.units||{}).length)').catch(()=>0):0;
  evidence.page_ready.push({file:rel,state:state||'timeout',seconds,units});
  if(!state)evidence.reader_not_ready.push(rel+': 页面在 '+(limit/1000)+' 秒内没有给出就绪信号（加载慢，只记警告，不判失败）');
  else if(state==='failed')evidence.reader_boot_errors.push(rel+': 页面启动失败 '+(await evaluate('document.documentElement.getAttribute("data-reader-error")||""').catch(()=>'')));
  else if(state==='ok'&&!units){
   // 空章节总结页（已声明未生成单元）本来就没有单元，不算失败。
   const declaredMissing=await evaluate('(()=>{const u=window.__DATA__?.unavailable_units;return Array.isArray(u)?u.length:Object.keys(u||{}).length;})()').catch(()=>0);
   if(!declaredMissing)evidence.reader_boot_errors.push(rel+': 页面没有任何单元（0 单元判失败）');
  }
  return state;
 }
 function pages(dir){return fs.readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.isDirectory()?pages(path.join(dir,e.name)):(e.name.endsWith('.html')?[path.join(dir,e.name)]:[]));}
 async function evaluate(expression){const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.text);return r.result?.value;}
 async function checkVideos(file,unit) {
  const rows=await evaluate('('+verifyVideoPlayback.toString()+')('+JSON.stringify(unit)+')');
  evidence.video_playback.push(...rows.map(row=>({file:path.relative(root,file),unit,...row})));
  evidence.animation_errors.push(...rows.filter(row=>!row.ok).map(row=>
   path.relative(root,file)+(unit?'/'+unit:'')+': '+row.source+': '+(row.error||'视频未解码出可播放画面')));
 }
 if(served){
  /* ── T10 服务模式：在**独立导出目录**上起本机服务，用生产一致的
     `sandbox="allow-scripts"` iframe 打开章节页，然后真的播一遍。
     沙箱子 frame 是 OOPIF target：`Target.getTargets` + `attachToTarget{flatten}`
     之后可以在 frame 自己的文档里求值（T09 的"列不到子 frame"只对 auto-attach 成立）。 */
  await runServedAnimation({send,evaluate,evidence,base:root.replace(/\/[^/]*$/,''),sleep});
 }
 for(const file of served?[]:pages(root)){
  await send('Page.navigate',{url:require('url').pathToFileURL(file).href});await waitReaderReady(file);
  const keys=await evaluate('Object.keys(window.__DATA__?.units||{})');
  for(const unit of keys){
   await evaluate(`(()=>{const e=Array.from(document.querySelectorAll('[data-unit]')).find(e=>e.dataset.unit===${JSON.stringify(unit)});if(e)e.click();})()`);await sleep(180);
   for(let n=0;n<40;n++) {const waiting=await evaluate(`Array.from(document.querySelectorAll('[data-model-id]')).some(e=>window.__DATA__?.models?.[e.dataset.modelId]?.kind==='canvas'&&!e.querySelector('canvas')&&!e.classList.contains('model-failed'))`);if(!waiting)break;await sleep(200);}
   // Scroll all lazy content into view and exercise model parameter/reset controls.
   const controls=await evaluate(`(()=>{for(const e of document.images)e.loading='eager';for(const e of document.querySelectorAll('img,[data-model-id],canvas'))e.scrollIntoView();let count=0;for(const e of document.querySelectorAll('input[type="range"]')){e.value=String((Number(e.min||0)+Number(e.max||100))/2);e.dispatchEvent(new Event('input',{bubbles:true}));count++;}for(const e of document.querySelectorAll('button'))if(/下一步|回到|重置|reset/i.test(e.textContent)){e.click();count++;}return count;})()`);
   evidence.interactions.push({file:path.relative(root,file),unit,parameter_controls:controls});await sleep(200);
   const harmonic=usabilityOnly ? {errors:[],results:[]} : await evaluate('('+verifyHarmonicModels.toString()+')('+JSON.stringify(unit)+')');
   evidence.model_errors.push(...harmonic.errors);
   evidence.harmonic_models.push(...harmonic.results.map(result=>({file:path.relative(root,file),unit,...result})));
   const errors=await evaluate(`({missing:Array.from(document.images).filter(i=>!i.complete||i.naturalWidth===0).map(i=>i.src),formulas:Array.from(document.querySelectorAll('.katex-error')).map(e=>e.textContent),models:Array.from(document.querySelectorAll('.model-failed,.model-fallback')).map(e=>e.textContent)})`);
   evidence.missing_images.push(...errors.missing);evidence.formula_errors.push(...errors.formulas);evidence.model_errors.push(...errors.models);
   evidence.source_formula_unrendered.push(...await evaluate(`Array.from(document.querySelectorAll('.source-formula-unrendered')).map(e=>e.textContent)`));
   const missingCanvas=await evaluate(`Array.from(document.querySelectorAll('[data-model-id]')).filter(e=>window.__DATA__?.models?.[e.dataset.modelId]?.kind==='canvas'&&!e.querySelector('canvas')).map(e=>e.dataset.modelId)`);evidence.model_errors.push(...missingCanvas);
   const missingWidgets=usabilityOnly ? [] : await evaluate(`Object.keys(window.__DATA__?.units?.[${JSON.stringify(unit)}]?.models||{}).filter(id=>!Array.from(document.querySelectorAll('[data-model-id]')).some(e=>e.dataset.modelId===id))`);evidence.model_errors.push(...missingWidgets.map(id=>'模型未挂载: '+id));
   if(displayPolicy) {
    await evaluate(`(()=>{for(const e of document.querySelectorAll('.reader-document details')){e.dataset.probeOpen=e.open?'1':'0';e.open=true;}})()`);
    for(const profile of displayPolicy.profiles) {
     await send('Emulation.setDeviceMetricsOverride',{width:profile.width,height:profile.height,deviceScaleFactor:1,mobile:false});await sleep(120);
     const measured=await evaluate('('+verifyMediaGeometry.toString()+')('+JSON.stringify(profile)+','+JSON.stringify(displayPolicy)+')');
     const contentErrors=usabilityOnly ? await evaluate('('+verifyUsableContent.toString()+')()')
      : await evaluate('('+verifyReaderContent.toString()+')('+JSON.stringify(unit)+')');
     evidence.reader_content_errors.push(...contentErrors.map(error=>unit+'/'+profile.profile_id+': '+error));
     evidence.media_layout_errors.push(...measured.errors.map(error=>unit+'/'+profile.profile_id+': '+error));
     evidence.appearance_warnings.push(...measured.warnings.map(error=>unit+'/'+profile.profile_id+': '+error));
     evidence.media_geometry.push({unit,profile:profile.profile_id,surfaces:measured.surfaces,
      viewport:measured.viewport,expected_media_width:measured.expected_media_width,reviewed_minimum_width:measured.reviewed_minimum_width});
    }
    await evaluate(`(()=>{for(const e of document.querySelectorAll('[data-probe-open]')){e.open=e.dataset.probeOpen==='1';delete e.dataset.probeOpen;}})()`);
   }
   if(usabilityOnly)await checkVideos(file,unit);
  }
  if(usabilityOnly&&!keys.length)await checkVideos(file,null);
  await sleep(300);
  const state=await evaluate(`({title:document.title,images:Array.from(document.images).filter(i=>!i.complete||i.naturalWidth===0).map(i=>i.src),formulas:Array.from(document.querySelectorAll('.katex-error')).map(e=>e.textContent),models:Object.keys(window.__DATA__?.models||{}).length,units:Object.keys(window.__DATA__?.units||{}).length})`);
  if(usabilityOnly)evidence.reader_content_errors.push(...await evaluate('('+verifyUsableContent.toString()+')()'));
  evidence.missing_images.push(...state.images);evidence.formula_errors.push(...state.formulas);
  evidence.source_formula_unrendered.push(...await evaluate(`Array.from(document.querySelectorAll('.source-formula-unrendered')).map(e=>e.textContent)`));
  evidence.pages.push({path:path.relative(root,file),...state});
 }
 if(!evidence.pages.length&&!served)throw new Error('No HTML pages to execute');
 const probeNoise=row=>/\/api\/books$/.test(String(row&&row.url||''))||/^https?:\/\/127\.0\.0\.1:1\//.test(String(row&&row.url||''))||/\/favicon\.ico$/.test(String(row&&row.url||''));
 for(const row of evidence.failed_requests)if(!row.url)row.url=requestUrlById.get(row.requestId)||'';
 const resourceFailures=evidence.failed_requests.filter(row=>!probeNoise(row));
 // 用户 2026-10-20 定：只有正文显示相关的问题判不通过；其余（脚本报错、图片/视频/模型、媒体几何、
 // 资源请求失败、外部请求、加载慢）在 balanced 下只记警告。full 模式保持原有全部门槛。
 const bodyKeys=['reader_content_errors','reader_boot_errors','formula_errors'];
 const technicalKeys=['runtime_errors','missing_images','external_requests','model_errors','animation_errors','media_layout_errors','reader_not_ready'];
 evidence.technical_warnings=Object.fromEntries(technicalKeys.filter(k=>(evidence[k]||[]).length).map(k=>[k,evidence[k].length]));
 if(resourceFailures.length)evidence.technical_warnings.resource_failures=resourceFailures.length;
 const ok=usabilityOnly ? bodyKeys.every(k=>evidence[k].length===0)
   : ['runtime_errors','missing_images','external_requests','formula_errors','model_errors','animation_errors','media_layout_errors','reader_content_errors','reader_boot_errors'].every(k=>evidence[k].length===0)&&resourceFailures.length===0;
 evidence.resource_failures=resourceFailures;
 process.stdout.write(JSON.stringify({ok,evidence}));
}
function killChrome(){if(!chrome||chrome.killed)return;try{process.kill(-chrome.pid,'SIGKILL');return;}catch(_){}
 try{chrome.kill('SIGKILL');}catch(_){}}
main().catch(e=>{process.stdout.write(JSON.stringify({blocked:true,error:String(e),evidence}));process.exitCode=2;}).finally(async()=>{if(ws)ws.close();killChrome();await sleep(200);fs.rmSync(profile,{recursive:true,force:true,maxRetries:3});});
