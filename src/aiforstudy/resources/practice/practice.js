(function(){'use strict';
const data=window.PRACTICE_DATA||{};const renderer=window.DSHRenderCore.createRenderer({marked:window.marked,katex:{renderToString:(tex,opts)=>window.katex.renderToString(tex,{...opts,macros:{...(opts.macros||{}),'\\Log':'\\operatorname{Log}','\\Arg':'\\operatorname{Arg}','\\Ln':'\\operatorname{Ln}'}})},concepts:{},figures:{}});
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
function inline(tag,cls,text){const n=el(tag,cls);n.innerHTML=renderer.renderInline(String(text||''));return n;}
function md(text,headingLevel=4){const n=el('div','markdown');n.innerHTML=renderer.renderMarkdown(String(text||'').replace(/(?<!\\)\\n(?![A-Za-z])/g,'\n').replace(/_{3,}/g,'＿＿＿＿'));n.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(old=>{const h=el('h'+Math.min(6,headingLevel+Number(old.tagName[1])-1),'practice-content-heading');while(old.firstChild)h.append(old.firstChild);old.replaceWith(h)});n.querySelectorAll('img').forEach(img=>{const src=img.getAttribute('src')||'';if(src.startsWith('assets/'))img.setAttribute('src',(data.asset_base||'assets')+'/'+src.slice(7));});return n;}
function card(q,number,options){options=options||{};const article=el('section','practice-q');article.id='practice-'+q.id;article.dataset.questionId=q.id;
if(!options.exam){const meta=el('div','practice-meta');meta.append(el('span','practice-chip '+(q.priority==='必会'?'must':''),q.priority),el('span','practice-chip','难度 '+q.difficulty+'/5'),inline('span','practice-chip',q.topic));article.append(meta)}
article.append(inline(options.sectioned?'h3':'h2','practice-question-heading',options.exam?'第 '+number+' 题'+(q.points?'（'+q.points+' 分）':''):number+'. '+q.title));if(!options.exam&&q.focus_note)article.append(el('p','practice-note practice-focus',q.focus_note));article.append(md(q.stem,options.sectioned?4:3));if(q.source?.common_header)article.append(md("**题目约定与补充说明：** "+q.source.common_header,options.sectioned?4:3));
if(!options.questionOnly){
 const d=el('details','practice-solution');d.append(el('summary','','展开答案与完整解析'));
 const body=el('div','practice-answer');d.append(body);article.append(d);let built=false;
 function buildAnswer(){
  if(built)return;built=true;
  const heading=options.sectioned?'h4':'h3',level=options.sectioned?5:4;
  body.append(el(heading,'practice-answer-heading','答案'),md(q.answer,level),el(heading,'practice-answer-heading','思路与推导'),md(q.solution,level));
  if((q.pitfalls||[]).length){body.append(el(heading,'practice-answer-heading','容易出错的地方'));const ul=el('ul');q.pitfalls.forEach(x=>{const li=el('li');li.append(md(x,level));ul.append(li)});body.append(ul)}
  if(window.AIFSPracticeSpatialAttachments){const prefix=options.sourcePrefix||String(data.asset_base||'assets').replace(/assets\/?$/,'');window.AIFSPracticeSpatialAttachments.attach(q,body,prefix)}
  if(window.AIFSPracticeMedia)window.AIFSPracticeMedia.attach(q,body,d,data.asset_base||'assets');
 }
 d.firstElementChild.addEventListener('click',()=>{if(!d.open)buildAnswer()});
 d.addEventListener('toggle',()=>{if(d.open)buildAnswer()});
 if(document.body.classList.contains('print-answers'))buildAnswer();
 const mediaItems=window.AIFSPracticeMedia?.records().filter(item=>item.question_id===q.id)||[];
 const hasVideo=mediaItems.some(item=>item.kind==='animation'),hasSpatial=!!(q.spatial_scenes||[]).length;
 if(mediaItems.length||hasSpatial){const label=hasSpatial?(hasVideo?'查看动画与三维图解':'查看三维动态图'):(hasVideo?'查看动画讲解':'查看静态 SVG');const jump=el('button','practice-media-button',label);jump.type='button';jump.addEventListener('click',()=>{d.open=true;buildAnswer();if(hasSpatial)body.querySelectorAll('.practice-spatial-fold').forEach(fold=>fold.open=true);setTimeout(()=>body.querySelector('.practice-media,.practice-spatial-attachment')?.scrollIntoView({block:'start',behavior:'smooth'}),0)});article.insertBefore(jump,d);}
}
if(!options.exam){const s=q.source||{};const source=el('div','practice-source');if(s.adaptation_note)source.append(inline('p','practice-adaptation',s.adaptation_note));let label=(s.kind==='past_exam'?'历年真题':s.kind==='textbook_exercise'?'教材课后题（实际布置状态未知）':'基于考法的新编题')+' · '+(s.year||'')+' '+(s.question_number||q.id);if(s.pdf_page)label+=' · '+(s.kind==='generated_variant'?'参考资料第':'原件第')+s.pdf_page+'页';if(q.public_source_path||s.public_path){const a=el('a','',label);a.href=(options.sourcePrefix||'')+(q.public_source_path||s.public_path)+(s.pdf_page?'#page='+s.pdf_page:'');a.target='_blank';source.append(a)}else source.append(el('span','',label));source.append(inline('div','',q.priority_reason||''));if(q.public_textbook_path){const back=el('a','','回到教材对应讲解');back.href=(options.sourcePrefix||'')+q.public_textbook_path;source.append(back);}article.append(source)}
return article;}
window.AIFSPractice={card:card,md:md,inline:inline};
const target=document.getElementById('practice-questions');if(!target)return;let rows=data.questions||[];
function show(){if(window.AIFSPracticeMedia)window.AIFSPracticeMedia.disposeWithin(target);target.replaceChildren();const query=(document.getElementById('practice-search')?.value||'').toLowerCase(),tier=document.getElementById('practice-tier')?.value||'';const selected=rows.filter(q=>(!tier||q.priority===tier)&&(!query||(q.id+' '+q.title+' '+q.topic+' '+q.stem+' '+(q.section||'')).toLowerCase().includes(query)));let section='';selected.forEach((q,i)=>{if(q.section&&q.section!==section){section=q.section;target.append(el('h2',data.paper?'practice-section':'practice-chapter',section));}target.append(card(q,rows.indexOf(q)+1,{exam:!!data.paper,sectioned:!data.paper&&!!q.section,questionOnly:!!data.question_only}));});if(!selected.length)target.append(el('div','practice-empty','没有匹配的题目。'));const count=document.getElementById('practice-count');if(count)count.textContent='显示 '+selected.length+' / '+rows.length+' 题';}
document.getElementById('practice-search')?.addEventListener('input',show);document.getElementById('practice-tier')?.addEventListener('change',show);document.getElementById('practice-expand')?.addEventListener('click',()=>target.querySelectorAll('details.practice-solution').forEach(x=>x.open=true));document.getElementById('practice-collapse')?.addEventListener('click',()=>target.querySelectorAll('details').forEach(x=>x.open=false));document.getElementById('practice-print')?.addEventListener('click',()=>window.print());
if(new URLSearchParams(location.search).get('answers')==='1'){document.body.classList.add('print-answers')}
show();if(document.body.classList.contains('print-answers'))target.querySelectorAll('details.practice-solution').forEach(x=>x.open=true);
})();
