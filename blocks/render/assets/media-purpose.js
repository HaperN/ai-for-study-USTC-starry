(function(root,factory){
  if(typeof module==='object'&&module.exports)module.exports=factory();
  else root.DSHMediaPurpose=factory();
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  function node(doc,tag,cls,text){var el=doc.createElement(tag);el.className=cls||'';if(text)el.textContent=text;return el;}
  function clock(seconds){seconds=Math.max(0,Number(seconds)||0);return Math.floor(seconds/60)+':'+String(Math.floor(seconds%60)).padStart(2,'0');}
  function mount(host,item){
    if(!item||!item.purpose_markdown)return function(){};
    var doc=host.ownerDocument,w=doc.defaultView,box=node(doc,'section','media-purpose'),voice=null,disposed=false,request=0,off=[];
    box.dataset.purposeId=item.id||'';
    box.appendChild(node(doc,'h4','media-purpose-title',item.kind==='animation'?'这段动画用来做什么':'这张图用来做什么'));
    var text=node(doc,'div','media-purpose-text');
    if(w.DSHAnimations&&w.DSHAnimations.captionText)w.DSHAnimations.captionText(text,item.purpose_markdown);
    else text.textContent=item.purpose_markdown;
    box.appendChild(text);
    var controls=node(doc,'div','media-purpose-controls'),button=node(doc,'button','media-purpose-play','听用途说明');
    button.type='button';button.dataset.purposePlay='';button.setAttribute('aria-pressed','false');
    var seek=node(doc,'input','media-purpose-seek');seek.type='range';seek.min=0;seek.max=String(item.audio&&item.audio.duration_seconds||0);seek.step='.1';seek.value=0;seek.disabled=true;seek.setAttribute('aria-label','用途讲解进度');
    var time=node(doc,'span','media-purpose-time','0:00 / '+clock(item.audio&&item.audio.duration_seconds));
    controls.append(button,seek,time);box.appendChild(controls);
    var volumeLabel=node(doc,'label','media-purpose-volume','讲解音量'),volume=node(doc,'input');volume.type='range';volume.min=0;volume.max=1;volume.step='.05';volume.value=1;volume.setAttribute('aria-label','用途讲解音量');volumeLabel.appendChild(volume);box.appendChild(volumeLabel);
    var error=node(doc,'p','media-purpose-status');error.setAttribute('role','status');box.appendChild(error);host.prepend(box);
    function on(target,type,fn,capture){target.addEventListener(type,fn,capture);off.push(function(){target.removeEventListener(type,fn,capture);});}
    function update(){if(!voice||disposed)return;var playing=!voice.paused&&!voice.ended;button.textContent=playing?'暂停用途讲解':'听用途说明';button.setAttribute('aria-pressed',String(playing));seek.max=String(voice.duration||item.audio.duration_seconds||0);seek.disabled=!(voice.duration>0);seek.value=String(voice.currentTime||0);time.textContent=clock(voice.currentTime)+' / '+clock(voice.duration||item.audio.duration_seconds);}
    function pause(){request+=1;if(voice)voice.pause();update();}
    function failed(){error.textContent='无法播放用途说明，请重新点击；也可先阅读上方文字。';pause();}
    function hidden(){for(var p=host;p&&p!==doc;p=p.parentElement){if(p.hidden||p.tagName==='DETAILS'&&!p.open)return true;}return !host.isConnected;}
    function stopHidden(){if(hidden())pause();}
    on(button,'click',async function(){
      if(disposed)return;
      if(voice&&!voice.paused){pause();return;}
      var token=++request;
      error.textContent='';
      try{
        if(!voice){
          if(!w.DSHAudibleAudio)throw new Error('Audio runtime unavailable');
          voice=w.DSHAudibleAudio.createAudio(w,item.audio);
          ['playing','pause','ended','timeupdate','loadedmetadata','durationchange'].forEach(function(e){on(voice,e,update);});on(voice,'error',failed);
        }
        doc.querySelectorAll('video,audio').forEach(function(el){if(!el.paused)el.pause();});
        doc.dispatchEvent(new w.CustomEvent('aiforstudy-audio-start',{detail:{owner:voice}}));
        voice.volume=Number(volume.value);if(voice.ended)voice.currentTime=0;
        var pending=voice.play();update();await pending;
        if(disposed||token!==request)return;
        if(hidden()){pause();return;}update();
      }catch(e){if(!disposed&&token===request&&e.name!=='AbortError')failed();}
    });
    on(seek,'input',function(){if(voice){voice.currentTime=Number(seek.value);update();}});
    on(volume,'input',function(){if(voice)voice.volume=Number(volume.value);});
    on(doc,'play',function(event){if(event.target&&/^(VIDEO|AUDIO)$/.test(event.target.tagName))pause();},true);
    on(doc,'aiforstudy-audio-start',function(event){if(!event.detail||event.detail.owner!==voice)pause();});
    on(doc,'toggle',stopHidden,true);
    var observer=new w.MutationObserver(stopHidden);observer.observe(doc.documentElement,{attributes:true,subtree:true,attributeFilter:['hidden','open']});
    if(!item.audio){button.disabled=true;volume.disabled=true;error.textContent='用途配音尚未生成。';}
    return function(){if(disposed)return;disposed=true;off.forEach(function(fn){fn();});observer.disconnect();if(voice)voice.dispose();};
  }
  return {mount:mount};
});
