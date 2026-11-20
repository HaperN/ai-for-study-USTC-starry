const test=require('node:test'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const player=require('../../blocks/render/assets/animations.js');
test('representative poster stays visible through pre-play seeks and starts the selected step when clicked',t=>{
 const dom=new JSDOM('<main></main>',{url:'https://offline.test',pretendToBeVisual:true});t.after(()=>dom.window.close());const w=dom.window;
 w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.load=()=>{};
 const resources=Object.fromEntries(['mp4','poster','captions','transcript'].map(k=>[k,{relative_path:k+'.dat',sha256:'sha256:'+'a'.repeat(64)}]));
 const dispose=player.mountAnimations(w.document.querySelector('main'),{base:'assets/demo/',resources,steps:[{step_id:'S1',start:0,end:2,caption:'看同一条曲线的变化。'}]});t.after(dispose);
 const cover=w.document.querySelector('[data-animation-poster]'),video=w.document.querySelector('video');assert.ok(cover,'native video posters can disappear after an initial seek');assert.equal(cover.hidden,false);assert.match(cover.querySelector('img').getAttribute('src'),/\?v=aaaaaaaaaaaa$/);
 video.dispatchEvent(new w.Event('loadedmetadata'));video.dispatchEvent(new w.Event('seeked'));assert.equal(cover.hidden,false);
 Object.defineProperty(video,'readyState',{configurable:true,value:4});Object.defineProperty(video,'duration',{configurable:true,value:2});let starts=0;video.play=()=>{starts++;video.dispatchEvent(new w.Event('play'));return Promise.resolve();};
 cover.click();assert.equal(starts,1);assert.equal(cover.hidden,true);video.dispatchEvent(new w.Event('pause'));assert.equal(cover.hidden,true,'pausing must retain the actual animation frame');
});

test('compact lesson playback keeps a named segment directory and expands inside the page',t=>{
 const dom=new JSDOM('<main></main>',{url:'https://offline.test',pretendToBeVisual:true});t.after(()=>dom.window.close());const w=dom.window;
 w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.load=()=>{};
 const resources=Object.fromEntries(['mp4','poster','captions','transcript'].map(k=>[k,{relative_path:k+'.dat'}]));
 const dispose=player.mountAnimations(w.document.querySelector('main'),{base:'assets/demo/',resources,steps:[{step_id:'S1',display_title:'比较两条曲线',start:0,end:2,caption:'先比较启动时刻。'}]},{compact:true});t.after(dispose);
 const d=w.document,menu=d.querySelector('.animation-step-directory');assert.equal(menu.open,false);assert.match(menu.textContent,/比较两条曲线/);
 assert.match(d.querySelector('.animation-stage-title').textContent,/比较两条曲线/);
 const button=d.querySelector('[data-action=fullscreen]');button.click();assert.ok(d.querySelector('.animation-expanded'));button.click();assert.equal(d.querySelector('.animation-expanded'),null);
});

test('a cold video waits for metadata before seeking to a later known segment',t=>{
 const dom=new JSDOM('<main></main>',{url:'https://offline.test',pretendToBeVisual:true});t.after(()=>dom.window.close());const w=dom.window;
 w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.load=()=>{};
 const resources=Object.fromEntries(['mp4','poster','captions','transcript'].map(k=>[k,{relative_path:k+'.dat'}]));
 const dispose=player.mountAnimations(w.document.querySelector('main'),{base:'assets/demo/',resources,steps:[{step_id:'S1',start:0,end:4,caption:'起点'},{step_id:'S2',start:10,end:20,caption:'后段'}]},{compact:true});t.after(dispose);
 const d=w.document,video=d.querySelector('video');let ready=false,time=0,plays=0,loads=0;
 Object.defineProperty(video,'duration',{get(){return ready?20:NaN;}});Object.defineProperty(video,'readyState',{get(){return ready?1:0;}});
 Object.defineProperty(video,'currentTime',{get(){return time;},set(value){time=value;}});
 video.load=()=>{loads++;};video.play=()=>{plays++;if(!ready)time=0;return Promise.resolve();};
 d.querySelector('[data-action=step][data-step="1"]').click();
 assert.equal(plays,0,'playing before metadata can silently reset an early seek to zero');assert.equal(loads,1);
 ready=true;video.dispatchEvent(new w.Event('loadedmetadata'));assert.equal(time,10);assert.equal(plays,1);
});
