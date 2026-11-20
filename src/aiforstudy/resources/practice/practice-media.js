(function(){'use strict';
const active=new Map();
const movies=new WeakMap();const records=()=>window.PRACTICE_MEDIA||[];
function node(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;}
function markdown(text){const box=node('div','practice-media-purpose');const r=window.DSHRenderCore.createRenderer({marked:window.marked,katex:window.katex,concepts:{},figures:{}});box.innerHTML=r.renderMarkdown(text||'');return box;}
function prefix(base,path){return String(base||'assets').replace(/\/$/,'')+'/'+String(path||'').replace(/^\//,'');}
function runtime(item,base){const value={...item,resources:{...item.resources},base:prefix(base,item.base||'')};if(item.playback){value.playback={...item.playback,video_src:prefix(base,item.playback.video_src),audio:{...item.playback.audio,src:prefix(base,item.playback.audio.src)}};}return value;}
function stop(container){const dispose=active.get(container);if(dispose){dispose();active.delete(container);}}
function mount(container,item,base){if(active.has(container))return;if(!window.DSHAnimations){container.textContent='动画播放器尚未加载';return;}const dispose=window.DSHAnimations.mountAnimations(container,runtime(item,base),{compact:false});active.set(container,dispose);}
function syncMovie(container) {
  const settings = movies.get(container);
  if (!settings) return;
  if (!container.isConnected || container.closest('details:not([open])')) {
    stop(container);
  } else {
    mount(container, settings.item, settings.base);
  }
}
// A native toggle event does not bubble. Capture it at every disclosure level.
document.addEventListener('toggle', event => {
  if (event.target.tagName === 'DETAILS') {
    event.target.querySelectorAll('.practice-animation-container').forEach(syncMovie);
  }
}, true);
function block(item,base){const section=node('section','practice-media');section.dataset.practiceMediaId=item.id;section.append(node('h3','',item.kind==='animation'?'动画讲解：'+item.title:'静态图：'+item.title),markdown(item.purpose));
if(item.kind==='animation'){const movie=node('div','practice-animation-container');section.append(movie);const printNote=node('p','practice-media-print-note','动态过程请在电子版中播放；静态示意图用于对照条件和结论。');section.append(printNote);movies.set(movie,{item,base});queueMicrotask(()=>syncMovie(movie));}
if(item.resources&&item.resources.svg&&!item.static_existing_in_solution){const figure=node('figure','practice-media-still');const img=node('img');img.alt=item.svg_alt||item.title+'静态示意图';img.src=prefix(base,(item.base||'')+item.resources.svg.relative_path);figure.append(img,node('figcaption','',item.static_caption||'静态图用于保留条件和对应关系；动态过程见上方播放器。'));section.append(figure)}return section;}
function attach(question,answer,disclosure,base){const items=records().filter(x=>x.question_id===question.id);for(const item of items)answer.append(block(item,base));return items;}
function disposeWithin(root){for(const element of [...active.keys()])if(root===element||root.contains(element))stop(element);}
function gallery(root,items,base){for(const item of items){const details=node('details','practice-media-gallery-item');details.id='media-'+item.id;details.append(node('summary','',(item.origin==='textbook'?'正文动画 · ':'习题动画 · ')+item.title));const host=node('div');details.append(host);root.append(details);let built=false;details.addEventListener('toggle',()=>{if(details.open&&!built){host.append(block({...item,static_existing_in_solution:false},item.asset_root||base));built=true;}});if(location.hash==='#media-'+item.id)details.open=true;}}
new MutationObserver(()=>{for(const element of [...active.keys()])if(!element.isConnected)stop(element)}).observe(document.documentElement,{childList:true,subtree:true});window.addEventListener('pagehide',()=>{for(const element of [...active.keys()])stop(element)});
// A cached page retains its open disclosures without firing toggle again.
window.addEventListener('pageshow', event => {
  if (event.persisted) document.querySelectorAll('.practice-animation-container').forEach(syncMovie);
});
window.AIFSPracticeMedia={attach,disposeWithin,gallery,records};
})();
