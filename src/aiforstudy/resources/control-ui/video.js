(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const labels = {queued:'排队中',running:'处理中',cancelling:'正在取消',cancelled:'已取消',interrupted:'已中断',failed:'失败',completed:'完成',pending:'待开始'};
  const stages = {queued:'等待开始',completed:'全部阶段已完成',probe:'读取视频',audio:'提取音频与兼容播放',export:'保存结果',extract:'提取音频与画面',extract_audio:'提取音频',frames:'抽取关键帧',asr:'语音转写',ocr:'画面文字识别',vision:'关键帧理解',synthesis:'整理讲义',lesson:'整理讲义',reconcile:'全片统一校订',global_review:'全片独立复核',review:'核验与疑问',publish:'保存结果',playback:'准备兼容播放'};
  let token = '', source = null, caps = {}, busy = false, refreshing = false, last = '';
  function el(tag, text, cls) {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;}
  function message(text, error=false) {$('video-message').textContent=text;$('video-message').className=error?'error':'';}
  function buttons() {$('submit-video').disabled=busy||!source||caps.available===false;$('sample-video').disabled=busy||!caps.sample_available;$('video-file').disabled=busy;}
  async function api(path, body) {
    const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Aifs-Token':token},body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'请求失败');return data;
  }
  async function action(fn) {if(busy)return;busy=true;buttons();try{await fn();}catch(e){message(e.message,true);}finally{busy=false;buttons();}}
  function selected(data) {source=data;$('selected-source').textContent='已选择：'+data.filename;$('video-title').placeholder=data.filename.replace(/\.[^.]+$/,'');buttons();}
  function percent(value) {return Math.round(Math.max(0,Math.min(1,Number(value)||0))*100);}
  function render(jobs) {
    const host=$('video-jobs');host.replaceChildren();
    if(!jobs.length){host.append(el('p','还没有解析任务。选择视频后开始。','muted'));return;}
    jobs.forEach(job=>{
      const card=el('article',undefined,'job');card.dataset.state=job.state;
      const heading=el('div',undefined,'job-heading');heading.append(el('h3',job.title||job.job_id),el('span',labels[job.state]||job.state,'badge'));card.append(heading,el('div',job.job_id,'job-id'));
      card.append(el('p',(stages[job.stage]||job.stage||'等待开始')+' · '+percent(job.progress)+'%'));
      const meter=el('progress');meter.max=1;meter.value=Number(job.progress)||0;meter.setAttribute('aria-label','总进度');card.append(meter);
      if(job.error)card.append(el('p',typeof job.error==='string'?job.error:JSON.stringify(job.error),'error'));
      const actions=el('div',undefined,'actions');
      if(['queued','running','cancelling'].includes(job.state)){
        const cancel=el('button',job.state==='cancelling'?'正在停止…':'取消解析');cancel.dataset.action='cancel';cancel.disabled=job.state==='cancelling';cancel.onclick=()=>change(job.job_id,'cancel',cancel);actions.append(cancel);
      }
      if(['failed','cancelled','interrupted'].includes(job.state)){
        const resume=el('button','恢复本次解析');resume.dataset.action='resume';resume.onclick=()=>change(job.job_id,'resume',resume);actions.append(resume);
      }
      if(job.state==='completed'||job.revision?.parent_result){
        const link=el('a',job.state==='completed'?'阅读讲义与回看原片 →':'阅读先前已发布讲义 →');link.href='/video-result.html?job='+encodeURIComponent(job.job_id);actions.append(link);
      }
      if(job.state==='completed'||(['failed','cancelled','interrupted'].includes(job.state)&&job.revision?.parent_result)){
        const revise=el('button',job.state==='completed'?'按全片来源再校订':'新建全片校订');revise.dataset.action='reconcile';revise.onclick=()=>change(job.job_id,'reconcile',revise);actions.append(revise);
      }
      card.append(actions);
      if(job.stages){const details=el('details');details.append(el('summary','阶段记录'));const list=el('ul',undefined,'stage-list');Object.entries(job.stages).forEach(([name,s])=>list.append(el('li',(stages[name]||name)+'：'+(labels[s.state]||s.state||'待开始')+' '+percent(s.progress)+'%'+(s.error?' · '+s.error:''))));details.append(list);card.append(details);}
      host.append(card);
    });
  }
  async function refresh() {
    if(refreshing)return;refreshing=true;
    try{const data=await api('/api/videos');const serialized=JSON.stringify(data.jobs);if(serialized!==last){render(data.jobs||[]);last=serialized;}}catch(e){message('读取任务失败：'+e.message,true);}finally{refreshing=false;}
  }
  async function change(id, op, button) {button.disabled=true;try{await api('/api/videos/'+encodeURIComponent(id)+'/'+op,{});await refresh();}catch(e){message(e.message,true);button.disabled=false;}}
  $('sample-video').onclick=()=>action(async()=>{selected(await api('/api/videos/sample',{}));message('已选中提供的第一讲。点击“开始解析”提交。');});
  $('video-file').onchange=()=>action(async()=>{
    const file=$('video-file').files[0];if(!file)return;
    source=null;buttons();
    if(!file.size||file.size>Number(caps.max_upload_bytes||2*1024**3))throw new Error('视频为空或超过允许大小。');
    if(!/\.(mp4|m4v|mov|webm|mkv|avi)$/i.test(file.name))throw new Error('请选择 MP4、MOV、WebM、MKV 或 AVI 视频。');
    $('selected-source').textContent='正在上传：'+file.name+'（请保持页面打开）';
    const response=await fetch('/api/videos/upload',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Aifs-Token':token,'X-Video-Filename':encodeURIComponent(file.name)},body:file});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'上传失败');selected(data);message('视频已存入本机工作区，可以开始解析。');
  });
  $('submit-video').onclick=()=>action(async()=>{
    if(!source)return;const job=await api('/api/videos',{source_id:source.source_id||source.id,title:$('video-title').value.trim()||undefined,force:$('force-video').checked});
    message('已提交任务 '+job.job_id+'。关闭页面不会取消任务。');await refresh();
  });
  $('refresh-videos').onclick=refresh;
  (async()=>{try{caps=await api('/api/videos/capabilities');token=caps.token;const mb=Math.round(Number(caps.max_upload_bytes||0)/1024**2);$('video-capabilities').textContent=(caps.available===false?'处理依赖尚未就绪。':'本地处理能力已就绪。')+(mb?' 单文件上限 '+mb+' MB。':'')+(caps.sample_available?'':' 未配置示例视频。');if(caps.available===false && caps.dependencies){const detail=el('pre',JSON.stringify(caps.dependencies,null,2));$('video-capabilities').append(detail);}buttons();await refresh();}catch(e){message(e.message,true);}})();
  const timer=setInterval(()=>{if(!document.hidden)refresh();},2500);
  window.addEventListener('pagehide',()=>clearInterval(timer));
})();
