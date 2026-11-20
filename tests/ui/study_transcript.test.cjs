const test=require('node:test'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const player=require('../../blocks/render/assets/animations.js');
test('one readable time-linked LaTex transcript replaces the duplicate raw production log',t=>{
 const dom=new JSDOM('<main></main>',{url:'https://offline.test/'}),w=dom.window;t.after(()=>w.close());
 const katex=require('../../blocks/render/vendor/katex.min.js');w.katex={render:(tex,node,options)=>{node.innerHTML=katex.renderToString(tex,options);}};w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.play=()=>Promise.resolve();w.HTMLMediaElement.prototype.load=()=>{};
 const data={base:'assets/demo/',resources:Object.fromEntries(['mp4','poster','captions','transcript'].map(k=>[k,{relative_path:k+'.dat'}])),
  steps:[{step_id:'S1',start:0,end:8,caption:'对M1画圆的制作指令',display_markdown:String.raw`先看 $M_1$。当 $\lim_{n\to\infty}z_n=z_0$ 时，点会进入给定邻域。`}],
  transcript_text:'S1 00:00:00.000 — 00:00:08.000 重复的原始长文本'};
 const dispose=player.mountAnimations(w.document.querySelector('main'),data);t.after(dispose);
 const section=w.document.querySelector('[data-animation-transcript]');
 assert.equal(section.querySelectorAll('.animation-transcript-row').length,1);
 assert.ok(!section.textContent.includes('制作指令'));assert.ok(!section.textContent.includes('原始长文本'));
 assert.ok(section.querySelector('.katex .op-limits'));
 assert.equal(section.hidden,true);w.document.querySelector('[data-action="transcript"]').click();assert.equal(section.hidden,false);
 assert.ok(section.querySelector('.animation-transcript-time'));
});
