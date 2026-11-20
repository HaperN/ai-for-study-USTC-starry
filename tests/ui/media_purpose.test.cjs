const test = require('node:test');
const assert = require('node:assert/strict');
const {JSDOM} = require('jsdom');
const purpose = require('../../blocks/render/assets/media-purpose.js');

function fixture(t) {
  const dom = new JSDOM('<main><section id="a"></section><section id="b"></section></main>', {pretendToBeVisual:true});
  t.after(()=>dom.window.close());
  const w=dom.window, players=[];
  w.DSHAnimations=require('../../blocks/render/assets/animations.js');
  const katex=require('../../blocks/render/vendor/katex.min.js');
  w.katex={render(src,host,options){host.innerHTML=katex.renderToString(src,options);}};
  w.DSHAudibleAudio={createAudio(win,payload){
    const p=new win.EventTarget();Object.assign(p,{payload,paused:true,ended:false,currentTime:0,duration:25,volume:1,muted:false,playbackRate:1});
    p.play=async()=>{p.paused=false;p.dispatchEvent(new win.Event('playing'));};
    p.pause=()=>{if(!p.paused){p.paused=true;p.dispatchEvent(new win.Event('pause'));}};
    p.dispose=()=>{p.pause();p.disposed=true;};players.push(p);return p;
  }};
  const row={unit_id:'U',id:'V',kind:'visual',purpose_markdown:'改变 $a$，观察新的收敛圆如何越过旧圆。',audio:{src:'assets/a.mp3',data_base64:'YWJj',mime:'audio/mpeg',duration_seconds:25}};
  return {w,players,row,a:w.document.querySelector('#a'),b:w.document.querySelector('#b')};
}
test('purpose stays visible next to its media and renders math with an explicit listening control',t=>{
  const {a,row}=fixture(t);const dispose=purpose.mount(a,row);t.after(dispose);
  assert.match(a.textContent,/这张图用来做什么/);assert.match(a.textContent,/观察新的收敛圆/);
  assert.ok(a.querySelector('.katex'));assert.equal(a.querySelector('details'),null);
  assert.equal(a.querySelector('[data-purpose-play]').textContent,'听用途说明');
  assert.equal(a.querySelector('audio'),null,'direct audio avoids the broken media-element path');
});
test('starting another purpose or a video stops the previous voice; no narration overlaps',async t=>{
  const {w,a,b,row,players}=fixture(t);const da=purpose.mount(a,row),db=purpose.mount(b,{...row,id:'B'});t.after(da);t.after(db);
  a.querySelector('button').click();await Promise.resolve();assert.equal(players[0].paused,false);
  b.querySelector('button').click();await Promise.resolve();assert.equal(players[0].paused,true);assert.equal(players[1].paused,false);
  const v=w.document.createElement('video');w.document.body.appendChild(v);v.dispatchEvent(new w.Event('play'));
  assert.equal(players[1].paused,true);
});
test('hiding a selected card and disposing a unit stop its voice',async t=>{
  const {a,row,players}=fixture(t);const dispose=purpose.mount(a,row);
  a.querySelector('button').click();await Promise.resolve();a.hidden=true;
  await new Promise(r=>setImmediate(r));assert.equal(players[0].paused,true);
  a.hidden=false;a.querySelector('button').click();await Promise.resolve();dispose();assert.equal(players[0].disposed,true);
});
test('playback rejection is visible and leaves the control ready to retry',async t=>{
  const {w,a,row}=fixture(t);w.DSHAudibleAudio.createAudio=()=>({paused:true,duration:0,currentTime:0,addEventListener(){},removeEventListener(){},pause(){},dispose(){},play:async()=>{throw new Error('output blocked');}});
  const dispose=purpose.mount(a,row);t.after(dispose);a.querySelector('button').click();await new Promise(r=>setImmediate(r));
  assert.match(a.textContent,/无法播放用途说明/);assert.equal(a.querySelector('button').textContent,'听用途说明');
});
test('missing audio does not pretend to play and plain markup cannot inject controls',t=>{
  const {a,row}=fixture(t);const dispose=purpose.mount(a,{...row,audio:null,purpose_markdown:'<img src=x onerror=alert(1)> 只解释距离。'});t.after(dispose);
  assert.equal(a.querySelector('button').disabled,true);assert.match(a.textContent,/配音尚未生成/);assert.equal(a.querySelector('img'),null);
});
test('a cancelled pending play cannot stop a newer play or show a false failure',async t=>{
  const {w,a,row,players}=fixture(t);const make=w.DSHAudibleAudio.createAudio;let rejectOld;
  w.DSHAudibleAudio.createAudio=(...args)=>{const p=make(...args);let count=0;p.play=()=>{p.paused=false;if(++count===1)return new Promise((_,reject)=>{rejectOld=reject;});return Promise.resolve();};return p;};
  const dispose=purpose.mount(a,row);t.after(dispose);const button=a.querySelector('button');
  button.click();button.click();button.click();rejectOld(new w.DOMException('cancelled','AbortError'));
  await new Promise(r=>setImmediate(r));assert.equal(players[0].paused,false);assert.equal(button.textContent,'暂停用途讲解');assert.doesNotMatch(a.textContent,/无法播放用途说明/);
});
