/* Real PCM regression, including offline loading and playback lifecycle. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const asset=path.resolve(__dirname,'../../blocks/render/assets/audible-audio.js');
const SILENT='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';
function wave(){const n=24000*4,b=Buffer.alloc(44+n*2);b.write('RIFF');b.writeUInt32LE(b.length-8,4);b.write('WAVEfmt ',8);b.writeUInt32LE(16,16);b.writeUInt16LE(1,20);b.writeUInt16LE(1,22);b.writeUInt32LE(24000,24);b.writeUInt32LE(48000,28);b.writeUInt16LE(2,32);b.writeUInt16LE(16,34);b.write('data',36);b.writeUInt32LE(n*2,40);for(let i=0;i<n;i++)b.writeInt16LE(Math.round(Math.sin(i*2*Math.PI*440/24000)*8000),44+i*2);return b.toString('base64')}
(async()=>{
const edge='/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge';
const browser=await chromium.launch({...(fs.existsSync(edge)?{executablePath:edge}:{}),headless:true,ignoreDefaultArgs:['--mute-audio'],args:['--disable-gpu']});
try{
 const page=await browser.newPage();await page.setContent('<button id="play">Play</button>');
 await page.addScriptTag({content:fs.existsSync(asset)?fs.readFileSync(asset,'utf8'):'/* missing controller */'});
 assert.equal(await page.evaluate(()=>typeof window.DSHAudibleAudio?.createAudio),'function','real narration controller must be available');
 await page.evaluate(base64=>{
  const Native=window.AudioContext;window.contexts=[];window.AudioContext=class extends Native{constructor(){super();window.contexts.push(this)}createGain(){const g=super.createGain(),an=super.createAnalyser();g.connect(an);window.analyser=an;return g}};
  window.fetch=()=>{throw Error('offline base64 must not fetch')};window.events=[];
  window.a=DSHAudibleAudio.createAudio(window,{src:'file:///must-not-fetch.mp3',data_base64:base64,mime:'audio/wav'});
  for(const e of ['play','playing','pause','ended','error'])a.addEventListener(e,()=>events.push(e));
  document.querySelector('#play').onclick=()=>{window.playPromise=a.play()};
  window.sample=()=>{const b=new Float32Array(analyser.fftSize);analyser.getFloatTimeDomainData(b);return{time:a.currentTime,paused:a.paused,ended:a.ended,peak:Math.max(...b.map(Math.abs)),rate:a.playbackRate,ctx:contexts[0].state}};
 },wave());
 await page.locator('#play').click();await page.evaluate(()=>playPromise);await page.waitForFunction(()=>a.currentTime>.15);
 let s=await page.evaluate(()=>sample());assert.ok(s.time>.1&&s.time<1);assert.equal(s.ctx,'running');assert.ok(s.peak>.1,'narration must produce nonzero PCM');
 await page.evaluate(()=>a.pause());const paused=await page.evaluate(()=>a.currentTime);await page.waitForTimeout(220);s=await page.evaluate(()=>sample());assert.equal(s.time,paused);assert.equal(s.paused,true);assert.equal(s.peak,0,'pause stops output');
 await page.evaluate(()=>{a.currentTime=1;a.playbackRate=2});await page.locator('#play').click();await page.evaluate(()=>playPromise);await page.waitForTimeout(250);s=await page.evaluate(()=>sample());assert.ok(s.time>1.35&&s.time<1.85,'seek and 2x use audio clock');
 await page.evaluate(()=>a.volume=.2);await page.waitForTimeout(120);s=await page.evaluate(()=>sample());assert.ok(s.peak>.01&&s.peak<.07,'gain follows volume');
 await page.evaluate(()=>a.muted=true);await page.waitForTimeout(120);assert.equal((await page.evaluate(()=>sample())).peak,0,'mute silences graph');
 await page.evaluate(()=>{a.muted=false;a.currentTime=3.8});await page.waitForFunction(()=>a.ended);assert.equal(await page.evaluate(()=>a.paused),true);assert.ok((await page.evaluate(()=>events)).includes('ended'));
 await page.locator('#play').click();await page.evaluate(()=>playPromise);await page.waitForTimeout(150);assert.ok((await page.evaluate(()=>sample())).time<1,'replay restarts after end');
 await page.evaluate(()=>a.dispose());await page.waitForFunction(()=>contexts[0].state==='closed');assert.equal(await page.evaluate(()=>a.paused),true);
 const cancellation=await page.evaluate(async base64=>{const x=DSHAudibleAudio.createAudio(window,{data_base64:base64});const p=x.play().catch(e=>e.name);x.pause();await p;await new Promise(r=>setTimeout(r,60));const state=x.paused;x.dispose();return state},wave());assert.equal(cancellation,true,'pause cancels pending decode');
 const failure=await page.evaluate(async()=>{const x=DSHAudibleAudio.createAudio(window,{data_base64:'bm90IGF1ZGlv'});let emitted=false;x.addEventListener('error',()=>emitted=true);let rejected=false;try{await x.play()}catch(e){rejected=true}const state={emitted,rejected,paused:x.paused};x.dispose();return state});assert.deepEqual(failure,{emitted:true,rejected:true,paused:true});

 assert.equal(await page.evaluate(()=>typeof DSHAudibleAudio.attachVideo),'function','native video controls must synchronize the narration clock');
 await page.evaluate(({audio,video})=>{
  document.body.innerHTML='<video width="320" controls></video><button id="video-play">Play video</button>';
  window.v=document.querySelector('video');window.driver=DSHAudibleAudio.attachVideo(v,{video_src:'data:video/mp4;base64,'+video,audio:{data_base64:audio}});
  document.querySelector('#video-play').onclick=()=>{window.videoPromise=driver.play()};
 },{audio:wave(),video:SILENT});
 await page.locator('#video-play').click();await page.evaluate(()=>videoPromise);await page.waitForTimeout(250);
 let vs=await page.evaluate(()=>({video:v.currentTime,audio:driver.audio.currentTime,paused:driver.audio.paused}));assert.ok(vs.video>.1&&vs.audio>.1);assert.ok(Math.abs(vs.video-vs.audio)<.2);assert.equal(vs.paused,false);
 await page.evaluate(()=>v.pause());await page.waitForTimeout(100);const held=await page.evaluate(()=>driver.audio.currentTime);await page.waitForTimeout(120);assert.equal(await page.evaluate(()=>driver.audio.currentTime),held,'native pause stops narration');
 await page.evaluate(()=>{v.currentTime=1;v.playbackRate=2;v.volume=.25;v.muted=true});await page.waitForTimeout(100);assert.deepEqual(await page.evaluate(()=>({rate:driver.audio.playbackRate,volume:driver.audio.volume,muted:driver.audio.muted})),{rate:2,volume:.25,muted:true});
 await page.evaluate(()=>v.muted=false);await page.locator('#video-play').click();await page.evaluate(()=>videoPromise);await page.waitForTimeout(250);vs=await page.evaluate(()=>({video:v.currentTime,audio:driver.audio.currentTime}));assert.ok(vs.audio>1.3&&Math.abs(vs.video-vs.audio)<.2,'native seek and extension rate changes remain synchronized');
 await page.evaluate(()=>v.currentTime=3.8);await page.waitForFunction(()=>v.ended);await page.waitForTimeout(100);assert.equal(await page.evaluate(()=>driver.audio.paused),true,'native end stops audio');
 const cancelledVideo=await page.evaluate(async()=>{const p=driver.play().catch(e=>e.name);driver.pause();await p;return{video:v.paused,audio:driver.audio.paused}});assert.deepEqual(cancelledVideo,{video:true,audio:true},'pause cancels queued video start');
 await page.evaluate(()=>driver.dispose());assert.equal(await page.evaluate(()=>driver.audio.paused),true);


 // Keep the real decoder, but defer its completion to reproduce hiding during load.
 for(const mode of ['hidden','closed','removed']){
  await page.evaluate(({audio,video})=>{
   document.body.innerHTML='<details open id="fold"><summary>Media</summary><section id="holder"><video width="320"></video></section></details><button id="deferred-play">Play</button>';
   window.holder=document.querySelector('#holder');window.fold=document.querySelector('#fold');window.releaseDecode=null;
   const Previous=window.AudioContext;window.previousContext=Previous;
   window.AudioContext=class extends Previous{decodeAudioData(bytes){return super.decodeAudioData(bytes).then(decoded=>new Promise(resolve=>{window.releaseDecode=()=>resolve(decoded)}))}};
   window.v=document.querySelector('video');window.driver=DSHAudibleAudio.attachVideo(v,{video_src:'data:video/mp4;base64,'+video,audio:{data_base64:audio}});
   document.querySelector('#deferred-play').onclick=()=>{window.deferredPlay=driver.play().catch(e=>e.name)};
  },{audio:wave(),video:SILENT});
  await page.locator('#deferred-play').click();await page.waitForFunction(()=>typeof releaseDecode==='function');
  assert.equal(await page.evaluate(()=>v.paused),true,'native video has not started while decode is pending');
  await page.evaluate(mode=>{if(mode==='hidden')holder.hidden=true;else if(mode==='closed')fold.open=false;else holder.remove()},mode);
  await page.waitForTimeout(30);await page.evaluate(()=>releaseDecode());await page.evaluate(()=>deferredPlay);await page.waitForTimeout(120);
  assert.deepEqual(await page.evaluate(()=>({video:v.paused,audio:driver.audio.paused})),{video:true,audio:true},mode+' cancels pending narration and video');
  await page.evaluate(()=>{holder.hidden=false;fold.open=true;if(!holder.isConnected)fold.append(holder)});
  await page.locator('#deferred-play').click();await page.evaluate(()=>deferredPlay);await page.waitForFunction(()=>v.currentTime>.1&&!v.paused);
  await page.evaluate(()=>fold.open=false);await page.waitForFunction(()=>v.paused&&driver.audio.paused);
  await page.evaluate(()=>{driver.dispose();window.AudioContext=window.previousContext});
 }

 await page.addScriptTag({path:path.resolve(__dirname,'../../blocks/render/assets/animations.js')});
 await page.evaluate(({audio,video})=>{
  document.body.innerHTML='<main id="host"></main>';
  window.mounted=DSHAnimations.mountAnimations(document.querySelector('#host'),{unit_id:'test',request_id:'test',status:'fulfilled',base:'',resources:{mp4:{relative_path:'original.mp4'},poster:{relative_path:'poster.png'},captions:{relative_path:'captions.vtt'},transcript:{relative_path:'transcript.txt'}},missing:[],steps:[{step_id:'first',caption:'First',start:0,end:1},{step_id:'second',caption:'Second',start:1,end:2.5}],playback:{video_src:'data:video/mp4;base64,'+video,audio:{data_base64:audio}}});
  window.v=document.querySelector('video');
 },{audio:wave(),video:SILENT});
 assert.ok(await page.evaluate(()=>v.src.startsWith('data:video/mp4')),'animation uses the compatible video source');
 await page.locator('[data-action="play"]').click();await page.waitForFunction(()=>v.currentTime>.15);
 assert.ok(await page.evaluate(()=>{const b=new Float32Array(analyser.fftSize);analyser.getFloatTimeDomainData(b);return Math.max(...b.map(Math.abs))>.1}),'animation produces real narration');
 await page.waitForFunction(()=>v.paused&&v.currentTime>=.98);assert.ok(await page.evaluate(()=>v.currentTime<=1.05),'step boundary stops both clocks');
 await page.waitForTimeout(100);assert.equal(await page.evaluate(()=>{const b=new Float32Array(analyser.fftSize);analyser.getFloatTimeDomainData(b);return Math.max(...b.map(Math.abs))}),0,'step pause stops PCM');
 await page.locator('[data-action="next"]').click();await page.waitForFunction(()=>v.currentTime>1.2&&!v.paused);
 await page.evaluate(()=>v.playbackRate=2);await page.waitForFunction(()=>document.querySelector('[data-action="speed"]').value==='2');
 await page.evaluate(()=>v.playbackRate=1.8);await page.waitForFunction(()=>document.querySelector('[data-action="speed"]').value==='1.8');
 await page.evaluate(()=>mounted());await page.waitForFunction(()=>contexts.at(-1).state==='closed');
 console.log(JSON.stringify({suite:'audible_audio',ok:true,realPCM:true,offline:true}));
}finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
