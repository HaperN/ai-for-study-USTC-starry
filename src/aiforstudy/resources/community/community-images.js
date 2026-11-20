/* Uploaded pixels and OCR candidates stay attached to their originating draft. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.AIFSCommunityImages=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 function node(d,tag,cls,text){var n=d.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
 function button(d,cls,text){var b=node(d,'button',cls,text);b.type='button';return b;}
 function key(t){return t?t.unit_id+':'+t.anchor:'';}
 function random(w){return Array.from(w.crypto.getRandomValues(new Uint8Array(16))).map(n=>n.toString(16).padStart(2,'0')).join('');}
 function validImage(m){return m&&/^[a-f0-9]{32}$/.test(m.id)&&m.url==='/api/annotation-images/'+m.id;}
 function renderRecognition(host,text,w){
  host.replaceChildren();text=String(text||'').slice(0,20000);var d=host.ownerDocument,rx=/\$\$([\s\S]*?)\$\$|\\\[([\s\S]*?)\\\]|\\\(([\s\S]*?)\\\)|(?<!\\)\$([^\n$]+?)(?<!\\)\$/g,cursor=0,match,count=0,ok=true;
  while((match=rx.exec(text))){
   if(count++>=128){ok=false;break;}
   host.append(d.createTextNode(text.slice(cursor,match.index)));var tex=match[1]??match[2]??match[3]??match[4],display=match[1]!==undefined||match[2]!==undefined;
   var math=node(d,display?'div':'span','community-ocr-math');
   try{if(!w.katex||tex.length>6000)throw Error('math unavailable');if(w.DSHStudyMath)tex=w.DSHStudyMath.operatorLimits(tex);math.innerHTML=w.katex.renderToString(tex,{displayMode:display,throwOnError:true,trust:false,strict:'ignore',maxExpand:1000,maxSize:10});}
   catch(e){math.textContent=match[0];ok=false;}
   host.append(math);cursor=rx.lastIndex;
  }
  host.append(d.createTextNode(text.slice(cursor)));
  if(count===0&&/\\(?:frac|sqrt|sum|int|begin)\b/.test(text))ok=false;
  return {ok:ok};
 }
 function showImage(w,url){
  var d=w.document,dialog=d.querySelector('.community-image-viewer');
  if(!dialog){dialog=node(d,'dialog','community-image-viewer');dialog.dataset.communityUi='';d.body.append(dialog);}
  if(dialog.open)dialog.close();dialog.replaceChildren();var toolbar=node(d,'div','community-viewer-toolbar'),close=button(d,'','关闭图片'),size=button(d,'','原始尺寸'),image=node(d,'img');
  image.src=url;image.alt='上传的图片批注';close.onclick=()=>dialog.close();size.onclick=()=>{image.classList.toggle('at-natural-size');size.textContent=image.classList.contains('at-natural-size')?'适应窗口':'原始尺寸';};toolbar.append(size,close);dialog.append(toolbar,image);
  if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');return dialog;
 }
 function renderAttachments(host,images,w){
  var d=host.ownerDocument;(images||[]).slice(0,3).forEach(function(m){if(!validImage(m))return;var card=node(d,'section','community-published-image');
   if(m.presentation==='recognized'&&typeof m.recognized_text==='string'){card.append(node(d,'p','community-image-caption','图片识别结果'));var text=node(d,'div','community-recognized-read');var result=renderRecognition(text,m.recognized_text,w);card.append(text);if(!result.ok)card.append(node(d,'p','community-image-status is-error','部分公式无法排版，请查看图片。'));}
   var open=button(d,'community-open-image',''),image=node(d,'img');image.src=m.url;image.loading='lazy';image.alt='图片批注，点击放大';open.append(image);open.onclick=()=>showImage(w,m.url);card.append(open,node(d,'p','community-image-caption',m.presentation==='recognized'?'点击图片对照原文':'点击图片放大'));host.append(card);
  });
 }
 class Composer{
  constructor(options){this.o=options;this.w=options.window;this.d=this.w.document;this.host=options.host;this.cfg=options.config;this.target=null;this.drafts=new Map();this.locks=new Set();this.uploadQueue=Promise.resolve();this.pendingResults=[];this.pendingVisible=false;this.pendingError='';this.pendingSerial=0;
   this.host.dataset.communityUi='';this.pick=button(this.d,'community-add-image','添加图片');this.input=node(this.d,'input');this.input.type='file';this.input.accept=(this.cfg.image_limits?.mime_types||['image/png','image/jpeg','image/webp']).join(',');this.input.multiple=true;this.input.hidden=true;this.rows=node(this.d,'div','community-image-drafts');
   this.manage=button(this.d,'community-manage-images','未发布图片');this.pendingHost=node(this.d,'div','community-pending-images');this.pendingHost.hidden=true;
   var hint=this.cfg.image_upload_available===false?'当前图片上传暂不可用，请联系网站维护者；已有图片仍可查看。':'可粘贴截图；每条最多 3 张，每张 10 MB。图片仅在发布后公开。';
   this.host.append(this.pick,this.input,this.manage,node(this.d,'p','community-image-hint',hint),this.pendingHost,this.rows);
   this.manage.onclick=()=>{this.pendingVisible=!this.pendingVisible;this.pendingHost.hidden=!this.pendingVisible;if(this.pendingVisible)this.loadPending();};
   this.pick.onclick=()=>this.input.click();this.input.onchange=()=>{var files=Array.from(this.input.files||[]);this.input.value='';this.addFiles(files).catch(e=>this.o.notify(e.message,true));};
   options.pasteTarget.addEventListener('paste',e=>{if(!this.target)return;var fs=Array.from(e.clipboardData?.items||[]).filter(i=>i.kind==='file'&&i.type.startsWith('image/')).map(i=>i.getAsFile()).filter(Boolean);if(fs.length){e.preventDefault();this.addFiles(fs).catch(err=>this.o.notify(err.message,true));}});
   this.render();
  }
  entries(k=key(this.target)){if(!this.drafts.has(k))this.drafts.set(k,[]);return this.drafts.get(k);}
  count(){return this.target?this.entries().length:0;}
  setTarget(target){this.target=target;this.render();this.o.onChange();}
  setPublishing(k,busy){if(busy)this.locks.add(k);else this.locks.delete(k);if(k===key(this.target))this.render();}
  async fetch(url,options={}){var response=await this.w.fetch(url,Object.assign({credentials:'same-origin'},options));if(!response.ok){var err=await response.json().catch(()=>({}));throw Error(err.error||'图片操作失败，请重试。');}return response;}
  async addFiles(files){
   if(this.cfg.image_upload_available===false)throw Error('当前图片上传暂不可用，请联系网站维护者。');
   if(!this.target)throw Error('请先在教材中选择批注位置。');var k=key(this.target);if(this.locks.has(k))throw Error('正在发布，请稍后添加图片。');
   if(this.entries(k).length+files.length>3)throw Error('每条批注最多 3 张图片。');
   for(const f of files){if(!(this.cfg.image_limits?.mime_types||['image/png','image/jpeg','image/webp']).includes(f.type))throw Error('请使用 PNG、JPG 或 WebP 图片。');if(f.size>10485760)throw Error('每张图片最多 10 MB。');}
   var unit=this.target.unit_id,jobs=files.map(file=>{var e={key:k,unit:unit,local:random(this.w),file:file,name:file.name||'粘贴图片',nonce:random(this.w),state:'queued',mode:'image',confirmed:false,preview:null,meta:null,recognition:null,ocrBusy:false,removed:false,error:''};this.entries(k).push(e);this.uploadQueue=this.uploadQueue.then(()=>{if(!e.removed)return this.upload(e);});return this.uploadQueue;});this.render();this.o.onChange();await Promise.all(jobs);
  }
  async upload(e){
   e.state='uploading';e.error='';this.renderIfActive(e);e.controller=new this.w.AbortController();
   try{
    if(!e.meta){var headers=Object.assign(this.o.getHeaders(),{'Content-Type':e.file.type,'X-AIFS-Unit':e.unit,'X-Upload-Request':e.nonce});var response=await this.fetch(this.cfg.image_upload_api,{method:'POST',headers:headers,body:e.file,signal:e.controller.signal});var data=await response.json();if(!validImage(data.image))throw Error('服务器返回的图片信息无效。');e.meta=data.image;}
    if(e.removed){this.removeRemote(e);return;}
    var preview=await this.fetch(e.meta.url,{headers:this.o.getHeaders(),signal:e.controller.signal});var blob=await preview.blob();if(e.removed)return;
    e.preview=this.w.URL.createObjectURL(blob);e.state='ready';e.file=null;
   }catch(err){if(!e.removed){e.state='failed';e.error=err.message||'图片上传失败';}}
   this.renderIfActive(e);this.o.onChange();
  }
  async removeRemote(e){if(!e.meta)return;try{await this.fetch(e.meta.url,{method:'DELETE',headers:this.o.getHeaders()});}catch(err){/* Unpublished failed cleanup remains private and expires on the server. */}}
  remove(e){if(this.locks.has(e.key))return;this.pendingSerial++;this.pendingError='';if(e.meta)this.pendingResults=this.pendingResults.filter(m=>m.id!==e.meta.id);e.removed=true;if(e.controller)e.controller.abort();if(e.ocrController)e.ocrController.abort();this.drafts.set(e.key,this.entries(e.key).filter(x=>x!==e));if(e.preview)this.w.URL.revokeObjectURL(e.preview);this.removeRemote(e);this.renderIfActive(e);this.o.onChange();}
  async recognize(e){
   if(e.state!=='ready'||e.ocrBusy||e.recognition||this.locks.has(e.key))return;e.ocrBusy=true;e.error='';e.ocrController=new this.w.AbortController();this.renderIfActive(e);
   try{var r=await this.fetch(e.meta.url+'/recognize',{method:'POST',headers:Object.assign(this.o.getHeaders(),{'Content-Type':'application/json'}),body:'{}',signal:e.ocrController.signal});var data=await r.json();if(!data.recognition||typeof data.recognition.text!=='string'||typeof data.recognition.truncated!=='boolean')throw Error('识别结果格式无效，请按图片发布。');if(!e.removed)e.recognition=data.recognition;}
   catch(err){if(!e.removed)e.error=err.message||'识别失败，仍可按图片发布。';}
   finally{e.ocrBusy=false;this.renderIfActive(e);this.o.onChange();}
  }
  payload(k=key(this.target)){
   return this.entries(k).map(e=>{if(e.state!=='ready')throw Error('图片尚未上传成功，请等待、重试或移除。');if(e.mode==='recognized'&&(!e.recognition||e.recognition.truncated||!e.renderOK||!e.confirmed))throw Error('请完成识别并核对结果，或切换为直接图片。');return {id:e.meta.id,presentation:e.mode,confirmed:e.mode==='recognized'&&e.confirmed};});
  }
  published(k,refs){var ids=new Set(refs.map(r=>r.id));this.pendingSerial++;this.pendingError='';this.pendingResults=this.pendingResults.filter(m=>!ids.has(m.id));this.drafts.set(k,this.entries(k).filter(e=>{if(!e.meta||!ids.has(e.meta.id))return true;e.removed=true;if(e.preview)this.w.URL.revokeObjectURL(e.preview);return false;}));if(k===key(this.target))this.render();this.o.onChange();}
  async loadPending(){
   var serial=++this.pendingSerial;
   this.pendingError='正在读取未发布图片…';this.renderPending();
   try{var response=await this.fetch(this.cfg.image_upload_api+'?pending=1',{headers:this.o.getHeaders()});var data=await response.json();if(serial!==this.pendingSerial)return;if(!Array.isArray(data.images))throw Error('未发布图片列表无法读取。');this.pendingResults=data.images.filter(validImage).slice(0,64);this.pendingError='';}
   catch(err){if(serial!==this.pendingSerial)return;this.pendingError=err.message||'读取失败，请重试。';}
   this.renderPending();
  }
  async restorePending(m){
   if(!this.target||m.unit_id!==this.target.unit_id)throw Error('请先切换到图片所属小节并选择位置。');
   var k=key(this.target);if(this.locks.has(k))throw Error('正在发布，请稍后恢复图片。');
   for(const entries of this.drafts.values())if(entries.some(e=>e.meta&&e.meta.id===m.id))throw Error('这张图片已在本页的一个草稿中。');
   if(this.entries(k).length>=3)throw Error('每条批注最多 3 张图片。');
   var e={key:k,unit:m.unit_id,local:random(this.w),file:null,name:'恢复的未发布图片',nonce:random(this.w),state:'queued',mode:'image',confirmed:false,preview:null,meta:m,recognition:null,ocrBusy:false,removed:false,error:''};
   this.entries(k).push(e);this.uploadQueue=this.uploadQueue.then(()=>{if(!e.removed)return this.upload(e);});this.render();this.o.onChange();await this.uploadQueue;
  }
  async discardPending(m){
   for(const [k,entries] of this.drafts)if(this.locks.has(k)&&entries.some(e=>e.meta&&e.meta.id===m.id))throw Error('这张图片正在发布，请稍后操作。');
   this.pendingSerial++;this.pendingError='';await this.fetch(m.url,{method:'DELETE',headers:this.o.getHeaders()});
   for(const [k,entries] of this.drafts)this.drafts.set(k,entries.filter(e=>{if(!e.meta||e.meta.id!==m.id)return true;e.removed=true;if(e.controller)e.controller.abort();if(e.ocrController)e.ocrController.abort();if(e.preview)this.w.URL.revokeObjectURL(e.preview);return false;}));
   this.pendingResults=this.pendingResults.filter(x=>x.id!==m.id);this.render();this.o.onChange();
  }
  renderPending(){
   var d=this.d;this.pendingHost.replaceChildren();this.pendingHost.hidden=!this.pendingVisible;if(!this.pendingVisible)return;
   var refresh=button(d,'community-pending-refresh','刷新列表');refresh.onclick=()=>this.loadPending();this.pendingHost.append(refresh,node(d,'p','community-image-hint','未发布图片保留 24 小时。可恢复到当前小节的位置；删除也会影响其他窗口中的草稿。'));
   if(this.pendingError)this.pendingHost.append(node(d,'p','community-pending-status',this.pendingError));
   if(!this.pendingError&&!this.pendingResults.length)this.pendingHost.append(node(d,'p','community-image-hint','没有待处理的未发布图片。'));
   this.pendingResults.forEach(m=>{
    var row=node(d,'div','community-pending-row'),title=(this.w.__DATA__?.units?.[m.unit_id]?.title)||m.unit_id;
    row.append(node(d,'p','',title+' · '+m.width+' × '+m.height));
    var view=button(d,'community-pending-view','查看'),restore=button(d,'community-pending-restore','恢复到这里'),remove=button(d,'community-pending-delete','删除');
    view.onclick=async()=>{try{var r=await this.fetch(m.url,{headers:this.o.getHeaders()});var u=this.w.URL.createObjectURL(await r.blob());showImage(this.w,u).addEventListener('close',()=>this.w.URL.revokeObjectURL(u),{once:true});}catch(err){this.pendingError=err.message;this.renderPending();}};
    var present=Array.from(this.drafts.values()).some(entries=>entries.some(e=>e.meta&&e.meta.id===m.id));restore.disabled=present||!this.target||m.unit_id!==this.target.unit_id||this.locks.has(key(this.target))||this.entries().length>=3;if(present)restore.textContent='已在草稿中';
    restore.onclick=()=>this.restorePending(m).catch(err=>{this.pendingError=err.message;this.renderPending();});
    remove.onclick=()=>{if(!remove.dataset.confirm){remove.dataset.confirm='yes';remove.textContent='确认删除';return;}remove.disabled=true;this.discardPending(m).catch(err=>{this.pendingError=err.message;this.renderPending();});};
    row.append(view,restore,remove);this.pendingHost.append(row);
   });
  }
  renderIfActive(e){if(e.key===key(this.target))this.render();}
  render(){
   var d=this.d,k=key(this.target),locked=this.locks.has(k);this.pick.disabled=!this.target||locked||this.cfg.image_upload_available===false;this.manage.disabled=!this.target;this.rows.replaceChildren();if(this.pendingVisible)this.renderPending();
   if(!this.target)return;
   this.entries().forEach(e=>{
    var row=node(d,'section','community-image-card');row.dataset.imageDraft=e.local;var top=node(d,'div','community-image-card-header'),name=node(d,'span','',e.name),remove=button(d,'community-image-remove','移除');remove.disabled=locked;remove.onclick=()=>this.remove(e);top.append(name,remove);row.append(top);
    if(e.preview){var img=node(d,'img','community-image-preview');img.src=e.preview;img.alt='待发布图片预览';img.onclick=()=>showImage(this.w,e.preview);row.append(img);}
    var status=node(d,'p','community-image-status',e.state==='queued'?'等待上传…':e.state==='uploading'?'正在上传…':e.error||'');status.classList.toggle('is-error',!!e.error);row.append(status);
    if(e.state==='failed'){var retry=button(d,'community-image-retry','重试上传');retry.disabled=locked;retry.onclick=()=>this.upload(e);row.append(retry);}
    if(e.state==='ready'){
     var label=node(d,'label','community-image-mode-label','发布方式'),select=node(d,'select','community-image-mode');select.setAttribute('aria-label','图片发布方式');
     [['image','直接显示图片'],['recognized','识别后发布（保留图片）']].forEach(pair=>{var opt=node(d,'option','',pair[1]);opt.value=pair[0];if(pair[0]==='recognized'&&!this.cfg.recognition_enabled)opt.disabled=true;select.append(opt);});select.value=e.mode;select.disabled=locked;select.onchange=()=>{e.mode=select.value;e.confirmed=false;this.render();this.o.onChange();};label.append(select);row.append(label);
     if(!this.cfg.recognition_enabled)row.append(node(d,'p','community-image-hint','当前未配置本地识别，仍可直接发图片。'));
     if(e.mode==='recognized'){
      var recognize=button(d,'community-recognize',e.ocrBusy?'正在本地识别…':e.recognition?'已生成识别结果':'开始识别');recognize.disabled=locked||e.ocrBusy||!!e.recognition;recognize.onclick=()=>this.recognize(e);row.append(recognize);
      if(e.recognition){var preview=node(d,'div','community-recognition-preview');e.renderOK=renderRecognition(preview,e.recognition.text,this.w).ok;row.append(preview);
       if(e.recognition.truncated||!e.renderOK)row.append(node(d,'p','community-image-status is-error','识别未完整或公式无法排版，请切回图片方式。'));
       var confirm=node(d,'label','community-recognition-check'),check=node(d,'input','community-recognition-confirm');check.type='checkbox';check.checked=e.confirmed;check.disabled=locked||e.recognition.truncated||!e.renderOK;check.onchange=()=>{e.confirmed=check.checked;this.o.onChange();};confirm.append(check,d.createTextNode('我已对照图片核对识别结果'));row.append(confirm);
      }
     }
    }
    this.rows.append(row);
   });
  }
 }
 function createComposer(options){return new Composer(options);}
 return {createComposer:createComposer,renderRecognition:renderRecognition,renderAttachments:renderAttachments};
});
