(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.TextLedLesson=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 const presetName=p=>({two:'2',two_i:'2i',zero:'0'}[p]||p);
 function mount(root,options){
  const w=root.ownerDocument.defaultView,d=root.ownerDocument,lesson=options.lesson,scene=options.scene;
  const renderer=w.DSHRenderCore.createRenderer({marked:w.marked,katex:w.katex});
  const audio=root.querySelector('audio')||new(options.Audio||w.Audio)(),rows=new Map((options.manifest||[]).map(r=>[r.id,r]));
  const text=root.querySelector('#lesson-text'),home=root.querySelector('#visual-home'),pane=root.querySelector('#visual-pane');
  const compact=w.matchMedia?w.matchMedia('(max-width:960px)'):null;
  const reduced=w.matchMedia?w.matchMedia('(prefers-reduced-motion:reduce)'):null;
  const segments=[],sections=new Map(),slots=new Map(),cleanups=[];
  let mode='reading',index=0,raf=null,destroyed=false,generation=0,loadedId=null;
  function el(tag,cls,value){const e=d.createElement(tag);if(cls)e.className=cls;if(value)e.textContent=value;return e;}
  function md(host,value){host.innerHTML=renderer.renderMarkdown(value||'');}
  function on(node,event,fn){if(!node)return;node.addEventListener(event,fn);cleanups.push(()=>node.removeEventListener(event,fn));}
  function get(id){return root.querySelector('#'+id);}
  function status(s){const e=get('guided-status');if(e)e.textContent=s;}
  for(const section of lesson.sections){
   const host=el('section','reading-section');host.id=section.id;host.dataset.section=section.id;host.append(el('h2','',section.title));sections.set(section.id,host);
   for(const row of section.narration){
    const paragraph=el('div','spoken-paragraph markdown');paragraph.dataset.narrationId=row.id;md(paragraph,row.display_markdown);host.append(paragraph);
    const part={...row,section,node:paragraph};segments.push(part);
    if(row.phase==='transform'||(section.scene==='point'&&section.narration.length===1)){
     const b=el('button','observe-link',section.task?.action||'看这一段的示范');b.type='button';b.dataset.demonstrate=row.id;host.append(b);
     const slot=el('div','visual-slot');host.append(slot);slots.set(section.id,slot);
    }
   }
   text.append(host);
  }
  const conclusion=el('section','reading-section conclusion');conclusion.id='conclusion';conclusion.append(el('h2','','把刚才看到的变化写成规则'));
  const rule=el('div','spoken-paragraph markdown');rule.dataset.narrationId='conclusion';md(rule,lesson.conclusion_markdown);conclusion.append(rule);
  for(const row of lesson.conclusion_narration||[])segments.push({...row,section:{id:'conclusion',title:'乘法规律',preset:'two_i',scene:'general'},node:rule});
  const zero=el('button','observe-link','看乘 0：所有点去哪里？');zero.type='button';zero.dataset.zero='true';conclusion.append(zero);
  const cs=el('div','visual-slot');conclusion.append(cs);slots.set('conclusion',cs);sections.set('conclusion',conclusion);text.append(conclusion);
  if(lesson.example){const details=el('details','worked-example');details.append(el('summary','',lesson.example.title));const problem=el('div','markdown');md(problem,lesson.example.problem_markdown);details.append(problem);const answer=el('div','markdown');md(answer,lesson.example.answer_markdown);details.append(answer);text.append(details);}
  function relocate(){if(!pane)return;const section=segments[index]?.section;const target=compact?.matches?slots.get(section?.id):home;if(target&&pane.parentElement!==target)target.append(pane);}
  function stopClock(){if(raf!==null){w.cancelAnimationFrame(raf);raf=null;}}
  function stop(){generation++;audio.pause();stopClock();scene.pause();}
  function sync(){const part=segments[index];if(mode==='guided'&&part?.phase==='transform'&&part.section.scene!=='general'){
   const duration=Number.isFinite(audio.duration)&&audio.duration>0?audio.duration:rows.get(part.id)?.duration;
   if(duration)scene.setProgress(reduced?.matches?1:Math.min(1,Math.max(0,audio.currentTime/duration)));
  }}
  function clock(){stopClock();if(destroyed||audio.paused||mode!=='guided')return;sync();raf=w.requestAnimationFrame(clock);}
  function highlight(){root.querySelectorAll('.is-speaking').forEach(e=>e.classList.remove('is-speaking'));const node=segments[index]?.node;if(mode==='guided'&&node)node.classList.add('is-speaking');}
  function prepare(part){
   const p=presetName(part.section.preset);scene.pause();if(scene.setPoint)scene.setPoint([1,1]);
   if(p){scene.setPreset(p,{animate:false});scene.setProgress(part.phase==='explain'?1:0);}
   else if(part.section.scene==='point')scene.reset();
   else if(part.section.scene==='general'&&scene.setPolar){scene.setPolar(1,0,{animate:false});scene.setProgress(1);if(get('rho'))get('rho').value='1';if(get('phi'))get('phi').value='0';if(get('polar-values'))get('polar-values').textContent='模 1；转角 0°';}
   const title=get('scene-title');if(title)title.textContent=part.section.title;const paneHost=get('scene-host');if(paneHost)paneHost.dataset.lessonStage=part.section.id;
   const custom=get('custom-controls');if(custom)custom.hidden=part.section.id!=='general-multiplier';
  }
  function select(next,{speak=false,scroll=false}={}){
   stop();index=Math.max(0,Math.min(segments.length-1,next));const part=segments[index];prepare(part);highlight();relocate();
   const onward=get('scene-continue');if(onward)onward.disabled=mode==='guided'&&index===segments.length-1;status('第 '+(index+1)+' / '+segments.length+' 段 · '+part.section.title);
   if(scroll)part.node.scrollIntoView({block:'start',behavior:reduced?.matches?'auto':'smooth'});
   if(speak)playVoice();
  }
  function playVoice(){
   const part=segments[index],resource=rows.get(part.id);if(!resource){status('这段声音尚未就绪，可以直接阅读和播放示范。');return;}
   const token=++generation;loadedId=part.id;audio.src=resource.file;audio.currentTime=0;
   const playing=audio.play();if(playing?.catch)playing.catch(()=>{if(token===generation)status('声音暂时无法播放，可以继续阅读和看示范。');});
  }
  function setMode(next){stop();mode=next;root.dataset.mode=mode;get('mode-read')?.setAttribute('aria-pressed',String(mode==='reading'));get('mode-guided')?.setAttribute('aria-pressed',String(mode==='guided'));const onward=get('scene-continue');if(onward){onward.textContent=mode==='guided'?'继续听下一段':'继续读对应解释';onward.disabled=mode==='guided'&&index===segments.length-1;}const voiceToggle=get('scene-voice');if(voiceToggle)voiceToggle.hidden=mode!=='guided';const controls=get('guided-controls');if(controls)controls.hidden=mode!=='guided';highlight();status(mode==='guided'?'从当前段开始，旁白与正文、示范一起推进。':'按正文中的按钮看示范。');}
  on(get('mode-read'),'click',()=>setMode('reading'));
  on(get('mode-guided'),'click',()=>setMode('guided'));
  on(get('guided-start'),'click',()=>{setMode('guided');select(index,{speak:true,scroll:true});});
  function toggleVoice(){if(audio.paused){if(loadedId!==segments[index].id){select(index,{speak:true});return;}if(scene.setPoint)scene.setPoint([1,1]);audio.play()?.catch(()=>status('请重新点“从本段开始听”。'));}else audio.pause();}
  on(get('guided-pause'),'click',toggleVoice);on(get('scene-voice'),'click',toggleVoice);
  on(get('guided-prev'),'click',()=>select(index-1,{speak:mode==='guided',scroll:true}));
  on(get('guided-next'),'click',()=>select(index+1,{speak:mode==='guided',scroll:true}));
  on(audio,'play',()=>{const b=get('guided-pause');if(b)b.textContent='暂停讲解';if(get('scene-voice'))get('scene-voice').textContent='暂停讲解';clock();});
  on(audio,'pause',()=>{stopClock();const b=get('guided-pause');if(b)b.textContent='继续讲解';if(get('scene-voice'))get('scene-voice').textContent='继续讲解';});
  on(audio,'timeupdate',sync);
  on(audio,'ended',()=>{stopClock();if(segments[index]?.section.id==='general-multiplier'){status('轮到你：先只改乘数的模，再只改转角。看完点“下一段”听规律。');return;}if(segments[index]?.phase==='transform')scene.setProgress(1);if(mode==='guided'&&index<segments.length-1)select(index+1,{speak:true,scroll:true});else status('这一段讲完了。可以拖动蓝点，检查换一个复数是否仍如此。');});
  on(root,'click',event=>{
   const button=event.target.closest('[data-demonstrate],[data-zero]');if(!button)return;
   stop();if(button.dataset.zero){index=segments.length-1;scene.setPreset('0',{animate:false});scene.setProgress(0);relocate();get('scene-title').textContent='乘 0：全都回到原点';if(get('custom-controls'))get('custom-controls').hidden=true;scene.play();}
   else{select(segments.findIndex(p=>p.id===button.dataset.demonstrate));if(!['point','general'].includes(segments[index].section.scene))scene.play();}
   if(compact?.matches)pane.scrollIntoView({block:'nearest',behavior:reduced?.matches?'auto':'smooth'});
  });
  on(get('scene-play'),'click',()=>{if(mode==='guided'){audio.pause();stopClock();}scene.play();});
  on(get('scene-pause'),'click',()=>{scene.pause();if(mode==='guided')audio.pause();});
  on(get('scene-reset'),'click',()=>{stop();scene.reset();});
  on(get('scene-continue'),'click',()=>select(index+1,{speak:mode==='guided',scroll:true}));
  function customChange(){if(!scene.setPolar)return;if(mode==='guided'){audio.pause();stopClock();status('可以继续调整参数；看完后点“继续听下一段”。');}scene.setPolar(Number(get('rho').value),Number(get('phi').value)*Math.PI/180,{animate:false});const out=get('polar-values');if(out)out.textContent='模 '+get('rho').value+'；转角 '+get('phi').value+'°';}
  on(get('rho'),'input',customChange);on(get('phi'),'input',customChange);
  if(compact?.addEventListener){compact.addEventListener('change',relocate);cleanups.push(()=>compact.removeEventListener('change',relocate));}
  setMode('reading');select(0);
  return {audio,setMode,select,pauseForExploration(){audio.pause();stopClock();status('讲解已暂停，你可以比较自己选择的复数；点正文按钮恢复原来的示例。');},getState:()=>({mode,index,segmentId:segments[index]?.id,segments:segments.length}),destroy(){destroyed=true;stop();cleanups.forEach(fn=>fn());}};
 }
 return {mount};
});
