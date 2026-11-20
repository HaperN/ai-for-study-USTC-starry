(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const job=new URLSearchParams(location.search).get('job')||'';
  let result, driver, pendingSeek=null, pendingRevision=null;
  const evidence=new Map();
  function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.className=cls;return n;}
  function describe(value){return typeof value==='string'?value:JSON.stringify(value,null,2);}
  function clock(n){n=Math.max(0,Math.floor(Number(n)||0));return (n>=3600?String(Math.floor(n/3600)).padStart(2,'0')+':':'')+String(Math.floor(n/60)%60).padStart(2,'0')+':'+String(n%60).padStart(2,'0');}
  function asset(relative){
    if(typeof relative!=='string'||!relative||relative.includes('\\')||relative.includes(':')||relative.startsWith('/')||relative.split('/').some(x=>!x||x==='.'||x==='..'))return null;
    return '/api/videos/'+encodeURIComponent(job)+'/artifacts/'+relative.split('/').map(encodeURIComponent).join('/');
  }
  function seek(value){
    const valueNumber=Number(value);if(!Number.isFinite(valueNumber)||valueNumber<0)return;
    const time=Math.min(valueNumber,Number(result.duration)||valueNumber),video=$('source-video');
    pendingSeek=time;try{video.currentTime=time;}catch(_){}
    const bounds=video.getBoundingClientRect();if((bounds.top<0||bounds.bottom>innerHeight)&&video.scrollIntoView)video.scrollIntoView({behavior:'smooth',block:'center'});
    $('player-status').textContent='已定位 '+clock(time)+'。这是窗口／抽样定位，点击“有声播放”回看上下文。';
  }
  function timeButton(start,end,label){
    const button=el('button',label||(clock(start)+(Number(end)>Number(start)?'–'+clock(end):'')),'time-link');
    button.type='button';button.dataset.start=String(start);button.onclick=()=>seek(start);return button;
  }
  function sourceButton(id){const row=evidence.get(String(id));if(!row){const missing=el('span','[来源待定位]','muted');missing.title=String(id);return missing;}const button=timeButton(row.start,row.end,(String(id).startsWith('frame-')?'画面 ':'语音 ')+clock(row.start));button.dataset.evidenceId=String(id);button.title='来源编号：'+id;return button;}
  function refs(ids,collapse=false){const group=el(collapse?'details':'div',undefined,'evidence-links');if(collapse)group.append(el('summary','查看本节 '+(ids||[]).length+' 处来源'));(ids||[]).forEach(id=>group.append(sourceButton(id)));return group;}
  function math(latex,display=false){const node=el('span',undefined,display?'math-display':'math-inline');try{if(!window.katex)throw new Error('公式渲染器未加载');window.katex.render(window.DSHStudyMath?window.DSHStudyMath.operatorLimits(latex):latex,node,{displayMode:display,throwOnError:true,trust:false,strict:'warn',maxExpand:1000,maxSize:20});}catch(_){node.textContent=latex;node.classList.add('formula-error');node.title='公式未能渲染，保留原始 LaTeX 供核对';}return node;}
  // A deliberately small DOM-only Markdown renderer. HTML and URLs remain text;
  // only trusted local evidence controls create links, and KaTeX has trust:false.
  function inline(node,text){
    const pattern=/\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$([^$\n]+?)\$|\*\*([^*]+)\*\*|`([^`]+)`|\[((?:asr|frame)-[A-Za-z0-9_-]+)\]/g;
    let match,last=0;while((match=pattern.exec(text))){node.append(document.createTextNode(text.slice(last,match.index)));if(match[1]!==undefined||match[2]!==undefined)node.append(math(match[1]??match[2],true));else if(match[3]!==undefined||match[4]!==undefined)node.append(math(match[3]??match[4]));else if(match[5]!==undefined)node.append(el('strong',match[5]));else if(match[6]!==undefined)node.append(el('code',match[6]));else{const link=sourceButton(match[7]);link.classList.add('inline-evidence');node.append(link);}last=pattern.lastIndex;}node.append(document.createTextNode(text.slice(last)));
  }
  function markdown(host,text){
    const lines=String(text||'').replace(/\r\n/g,'\n').split('\n');let paragraph=[],code=null,display=null;
    const flush=()=>{if(paragraph.length){const p=el('p');inline(p,paragraph.join('\n'));host.append(p);paragraph=[];}};
    for(const line of lines){
      if(code!==null){if(/^```/.test(line)){host.append(el('pre',code.join('\n')));code=null;}else code.push(line);continue;}
      if(display!==null){display.push(line);if(line.includes(display[0]==='$$'?'$$':'\\]')){const p=el('div');inline(p,display.join('\n'));host.append(p);display=null;}continue;}
      if(/^```/.test(line)){flush();code=[];continue;}
      if(line.trim()==='$$'||line.trim()==='\\['){flush();display=[line.trim()];continue;}
      if(!line.trim()){flush();continue;}
      const heading=line.match(/^(#{1,6})\s+(.+)$/);if(heading){flush();const h=el('h'+Math.min(heading[1].length+2,6));inline(h,heading[2]);host.append(h);continue;}
      const item=line.match(/^\s*(?:[-*]|\d+\.)\s+(.+)$/);if(item){flush();const p=el('p');inline(p,'• '+item[1]);host.append(p);continue;}
      if(/^>\s?/.test(line)){flush();const q=el('blockquote');inline(q,line.replace(/^>\s?/,''));host.append(q);continue;}
      paragraph.push(line);
    }
    flush();if(code)host.append(el('pre',code.join('\n')));if(display)host.append(el('pre',display.join('\n')));
  }
  function download(label,relative){const href=asset(relative);if(!href)return;const link=el('a',label);link.href=href;link.download=relative.split('/').pop();$('downloads').append(link);}
  function playback(){
    const video=$('source-video'),source=result.source||{},compat=source.playback;
    if(compat && asset(compat.video_src) && compat.audio && window.DSHAudibleAudio){
      driver=window.DSHAudibleAudio.attachVideo(video,{video_src:asset(compat.video_src),audio:{src:asset(compat.audio.src),data_base64:compat.audio.data_base64,mime:'audio/mpeg'}});
      $('playback-mode').textContent='兼容播放：纯画面与原片音频同步。首次点击会解码声音；倍速可能改变音调。';
    }else{
      const src=asset(source.path);if(src)video.src=src;
      $('playback-mode').textContent='使用浏览器原生播放。若无声音，可下载原片在本机播放器中打开。';
    }
    $('play-video').onclick=()=>{const promise=driver?driver.play():video.play();Promise.resolve(promise).then(()=>{$('player-status').textContent='播放已启动。声音与原片同步；可拖动进度条回看。';}).catch(error=>{$('player-status').textContent='播放未启动：'+error.message+'。可再次点击播放或下载原片。';});};
    $('pause-video').onclick=()=>driver?driver.pause():video.pause();
    video.addEventListener('loadedmetadata',()=>{if(pendingSeek!==null){video.currentTime=pendingSeek;pendingSeek=null;}});
    video.addEventListener('error',()=>{$('player-status').textContent='播放遇到问题，可再次点击播放，或下载原片／音频在本机打开。';});
    download('下载原片',source.path);if(compat?.audio?.src)download('下载原片音频',compat.audio.src);
    const exports=result.exports||{};
    download('下载讲义 Markdown',exports.markdown||'lesson.md');download('下载全部结果 JSON',exports.json||'result.json');
    if(exports.review_evidence)download('下载来源核查证据',exports.review_evidence);
    if(exports.corrections)download('下载校订记录',exports.corrections);
    if(exports.original_json&&exports.original_json!==exports.json)download('查看自动解析初稿',exports.original_json);
    if(exports.parent_json&&exports.parent_json!==exports.original_json)download('查看本次编辑前版本',exports.parent_json);
    window.addEventListener('pagehide',()=>{if(driver)driver.dispose();});
  }
  function render(){
    document.title=(result.title||'视频讲义')+' · AIforStudy';$('result-title').textContent=result.title||'视频讲义';
    const deferred=Array.isArray(result.deferred_work)?result.deferred_work:[];
    const unfinished=deferred.length||result.review?.state==='deferred'||['deferred','partial'].includes(result.reconciliation?.state);
    const editor=result.editorial_revision;
    const currentEditorial=Boolean(editor?.id && result.exports?.json==='revisions/'+editor.id+'/result.json');
    $('result-status').textContent=(unfinished?'本轮解析已结束，含局部待核或未完成检查':currentEditorial?'已完成解析、全片校订与来源核对修订':result.reconciliation?.input_digest?'已完成解析与全片校订':'已完成解析')+' · 时长 '+clock(result.duration)+'。请结合原始转写、抽样画面与待核实项阅读。'+(pendingRevision?' 当前显示先前已发布版本；新校订尚未发布。':'');$('reader').hidden=false;
    [...(result.transcript?.segments||[]),...(result.frames||[]),...(result.sections||[])].forEach(row=>evidence.set(String(row.id),row));
    playback();
    if(deferred.length){
      const box=$('deferred-work'),details=el('details');box.hidden=false;
      details.append(el('summary','查看 '+deferred.length+' 项局部待核与未完成检查'));
      details.append(el('p','这些局部问题已暂存，其余内容继续整理。出现更清晰的来源或新上下文后再回查，不把未核内容当作确定结论。'));
      const labels={asr:'语音识别',ocr:'画面文字识别',vision:'画面理解',lesson:'分段讲义',reconcile:'全片校订',review:'全片审核'};
      deferred.forEach(item=>{const card=el('div',undefined,'deferred-note');card.append(el('strong',(labels[item.stage]||'局部整理')+' · 待核/未完成'),timeButton(item.start,item.end),el('p',item.message||'本处尚未完成核查'),refs(item.source_ids));details.append(card);});box.append(details);
    }
    (result.sections||[]).forEach(section=>{
      const item=el('li');item.append(timeButton(section.start,section.end,clock(section.start)+' '+section.title));$('time-directory').append(item);
      const article=el('article',undefined,'lesson-section');article.append(el('h2',section.title),timeButton(section.start,section.end));
      if(['partial','deferred'].includes(section.processing_status))article.append(el('p','本段含未核实来源或未完成整理；依赖相关前提的结论也仍待核实。','deferred-note'));
      markdown(article,section.markdown);article.append(refs(section.evidence_ids,true));$('lesson').append(article);
    });
    if(!(result.sections||[]).length)$('lesson').append(el('p','本次没有生成讲义章节，请查看原始记录与任务错误。'));
    if(result.corrections?.length){$('corrections').hidden=false;$('correction-summary').textContent='查看全片校订的 '+result.corrections.length+' 处变化';result.corrections.forEach(change=>{const card=el('article');card.append(el('h3','初稿与校订后'));const before=el('blockquote');markdown(before,change.before||'');const after=el('div');markdown(after,change.after||'');card.append(before,after,el('p',change.reason||''),refs(change.source_ids));$('correction-list').append(card);});}
    (result.transcript?.segments||[]).forEach(segment=>{const row=el('div',undefined,'transcript-row'),text=el('div');text.append(el('p',segment.text));(segment.quality_warnings||[]).forEach(warning=>text.append(el('p','转写提示：'+describe(warning),'muted')));row.append(timeButton(segment.start,segment.end),text);$('transcript').append(row);});
    if(!result.transcript?.segments?.length)$('transcript').append(el('p','没有可用语音转写。'));
    (result.frames||[]).forEach(frame=>{
      const card=el('article',undefined,'frame-card');card.append(el('h3','抽样画面 · '+clock(frame.sampled_time??frame.start)),timeButton(frame.sampled_time??frame.start,frame.end));
      const href=asset(frame.path);if(href){const img=el('img');img.src=href;img.alt='视频在 '+clock(frame.sampled_time??frame.start)+' 的抽样帧';img.loading='lazy';card.append(img);}
      card.append(el('h3','原始 OCR'),el('pre',frame.text||'未识别到文字'));
      (frame.ocr_quality_warnings||[]).forEach(q=>card.append(el('p','OCR 提示：'+describe(q),'muted')));
      card.append(el('h3','视觉转录'));const visual=el('div');markdown(visual,frame.visual_text||'未生成视觉转录');card.append(visual);
      if(frame.explanation){card.append(el('h3','本帧解释'));const explanation=el('div');markdown(explanation,frame.explanation);card.append(explanation);}
      (frame.uncertainties||[]).forEach(q=>card.append(el('p','待核实：'+describe(q),'muted')));$('frames').append(card);
    });
    (result.formulas||[]).forEach(formula=>{const card=el('div',undefined,'formula-card');const equation=el('div',undefined,'formula');equation.append(math(String(formula.latex||''),true));card.append(equation,refs(formula.source_ids));if(formula.note){const note=el('div',undefined,'formula-note');markdown(note,formula.note);card.append(note);}if(formula.uncertainty)card.append(el('p','待核实：'+describe(formula.uncertainty)));$('formulas').append(card);});
    (result.uncertainties||[]).forEach(q=>{const item=el('li');inline(item,describe(q));$('questions').append(item);});
    const review=result.review||{},issues=review.issues||[];
    issues.forEach(issue=>{const item=el('li');const severity={error:'错误风险',uncertain:'待核实',minor:'细节问题'}[issue.severity]||'待核实';item.append(el('strong','独立审核 · '+severity),el('p',issue.description||'审核未给出问题描述'));if(issue.suggestion)item.append(el('p','核对建议：'+issue.suggestion));item.append(refs(issue.source_ids));$('questions').append(item);});
    (review.limitations||[]).forEach(limit=>$('questions').append(el('li','审核局限：'+describe(limit))));
    if(!(result.uncertainties||[]).length&&!issues.length)$('questions').append(el('li','未列出额外疑问；这不表示每句口述或每帧画面均已核实。'));
    $('questions').append(el('li',review.state==='deferred'?'独立审核未完成，当前没有取得有效审核判决；已保留讲义与原始证据。':review.state==='source_checked_edits'?'本次按记录中的原片与识别证据作了限定范围的编辑修订；没有逐句逐帧核实全文。':review.state==='not_requested'?'本次未执行独立审核；讲义仍为自动解析候选稿。':'独立审核仅做识别文本交叉检查，不等于逐句逐帧核验或人工批准。'));
    const coverage=el('div',undefined,'coverage-card');coverage.append(el('p','语音覆盖与画面采样分开记录；时间引用为识别窗口，不是逐字时间戳。'),el('pre',JSON.stringify({语音窗口:result.transcript?.coverage,画面及总体覆盖:result.coverage},null,2)));$('coverage').append(coverage);
    $('provenance').textContent=JSON.stringify({语音模型:result.transcript?.identity,来源:result.provenance,审核:result.review},null,2);
    const initialTime=new URLSearchParams(location.search).get('t');
    if(initialTime!==null&&Number.isFinite(Number(initialTime))&&Number(initialTime)>=0)seek(Number(initialTime));
  }
  (async()=>{try{
    if(!/^[A-Za-z0-9_-]+$/.test(job))throw new Error('缺少有效任务编号，请返回视频解析页。');
    const response=await fetch('/api/videos/'+encodeURIComponent(job));const data=await response.json();if(!response.ok)throw new Error(data.error||'结果读取失败');
    if(!data.result||(data.state!=='completed'&&!data.revision))throw new Error('结果尚未完成。'+(data.error?describe(data.error):'当前状态：'+data.state+'，请返回任务页查看进度。'));
    pendingRevision=data.state!=='completed'?data.revision:null;result=data.result;render();
  }catch(e){$('result-status').textContent=e.message;$('result-status').className='error';}})();
})();
