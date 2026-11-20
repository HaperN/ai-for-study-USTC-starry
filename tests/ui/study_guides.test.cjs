const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
test('scene listening stays local, one voice at a time, and old events cannot stop a new guide',async t=>{
  const dom=new JSDOM(`<main id="content"><article class="study-visual" data-study-scene="square"><div class="study-tools"></div></article><article class="study-visual" data-study-scene="square"><div class="study-tools"></div></article></main>`,{runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,voices=[];
  w.__DATA__={study_reader:{guides:{square:{audio:'assets/study-guides/square.wav'}}}};
  w.Audio=class extends w.EventTarget {constructor(src){super();this.src=src;this.paused=true;this.currentTime=0;voices.push(this);}play(){this.paused=false;return Promise.resolve();}pause(){this.paused=true;}};
  w.eval(fs.readFileSync(path.resolve(__dirname,'../../blocks/render/assets/study-guides.js'),'utf8'));
  w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
  const buttons=w.document.querySelectorAll('[data-study-listen]');assert.equal(buttons.length,2);
  buttons[0].click();assert.equal(voices[0].paused,false);assert.equal(voices[0].src,'assets/study-guides/square.wav');
  buttons[1].click();assert.equal(voices[0].paused,true);assert.equal(voices[1].paused,false);
  voices[0].dispatchEvent(new w.Event('ended'));assert.equal(voices[1].paused,false,'stale audio must not stop new guide');
  w.document.getElementById('content').innerHTML='';await Promise.resolve();assert.equal(voices[1].paused,true);
});
