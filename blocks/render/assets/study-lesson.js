/* The teaching plan, not a keyword classifier, determines every visual's place. */
(function(root,factory){
  if(typeof module==='object'&&module.exports)module.exports=factory();
  else {root.DSHStudyLesson=factory();root.DSHStudyLesson.boot(root.document);}
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  var purposeModule=typeof module==='object'&&module.exports?require('./media-purpose.js'):null;
  function node(doc,tag,cls,text){var e=doc.createElement(tag);if(cls)e.className=cls;if(text)e.textContent=text;return e;}
  function rich(host,text){
    var w=host.ownerDocument.defaultView;
    if(w.DSHAnimations)w.DSHAnimations.captionText(host,text||'');else host.textContent=text||'';
  }
  function arrangeMedia(root,article,plans){
    var doc=root.ownerDocument,w=doc.defaultView,body=root.querySelector('[data-reader-pointer="/body_markdown"]')||root.querySelector('[data-reader-group="body_markdown"]')||article;
    var blocks=Array.from(body.querySelectorAll('.teaching-block')).filter(function(block){return !block.closest('details');});if(!blocks.length)return function(){};
    var layout=node(doc,'div','lesson-reading-layout'),prose=node(doc,'div','lesson-prose'),rail=node(doc,'aside','lesson-media-rail');
    rail.setAttribute('aria-label','与正文对应的图像和动画');
    var cues=blocks.map(function(block,index){
      var cue=node(doc,'details','lesson-media-cue'),summary=node(doc,'summary'),plan=plans.get(block.dataset.teachingId)||{};
      summary.textContent=(block.dataset.teachingKind==='MEDIA'?'播放示范：':'查看图解：')+(plan.title||plan.model_title||'本段示范');
      cue.dataset.sharedMediaTarget=block.dataset.teachingId;
      cue.dataset.mediaIndex=String(index);block.before(cue);cue.appendChild(summary);cue.appendChild(block);return cue;
    });
    while(body.firstChild)prose.appendChild(body.firstChild);
    layout.appendChild(prose);layout.appendChild(rail);body.appendChild(layout);
    var selected=0,desktop=false,observer=null,placed=false;
    function pause(block){block.querySelectorAll('video,audio').forEach(function(media){if(!media.paused)media.pause();});var figure=block.querySelector('[data-text-led-figure]');if(figure&&figure.__pause)figure.__pause();}
    function select(index){
      selected=index;
      blocks.forEach(function(block,i){block.hidden=desktop&&i!==selected;if(block.hidden)pause(block);cues[i].classList.toggle('is-current',i===selected);});
      if(root.__studyController)root.__studyController.refresh();
    }
    function place(){
      var nextDesktop=w.innerWidth>=1000;if(placed&&nextDesktop===desktop)return;
      desktop=nextDesktop;placed=true;layout.classList.toggle('has-media-rail',desktop);rail.hidden=!desktop;
      blocks.forEach(function(block,i){(desktop?rail:cues[i]).appendChild(block);cues[i].open=false;});select(selected);
    }
    var handlers=cues.map(function(cue,i){var summary=cue.querySelector('summary');var click=function(e){if(desktop){e.preventDefault();select(i);}};summary.addEventListener('click',click);return function(){summary.removeEventListener('click',click);};});
    place();w.addEventListener('resize',place);
    if(w.IntersectionObserver){observer=new w.IntersectionObserver(function(entries){if(!desktop)return;var visible=entries.filter(function(e){return e.isIntersecting;});if(visible.length){visible.sort(function(a,b){return a.boundingClientRect.top-b.boundingClientRect.top;});select(Number(visible[0].target.dataset.mediaIndex));}},{rootMargin:'-12% 0px -58% 0px',threshold:0});cues.forEach(function(c){observer.observe(c);});}
    return function(){w.removeEventListener('resize',place);handlers.forEach(function(fn){fn();});if(observer)observer.disconnect();blocks.forEach(pause);};
  }
  function enhance(root,options){
    options=options||{};var unit=options.unit||{},lesson=unit.study_lesson;
    if(!lesson)return {visuals:0,media:0,errors:[]};
    var article=root.querySelector('.unit')||root,previous=root.__teachingState;
    if(previous&&previous.article===article&&previous.unitId===options.unitId)return previous.report;
    if(previous)previous.dispose();
    var doc=root.ownerDocument,w=doc.defaultView,disposers=[],report={visuals:0,media:0,errors:[]};
    var visualMap=new Map((lesson.visuals||[]).map(function(p){return [p.id,p];}));
    var mediaMap=new Map((lesson.media||[]).map(function(p){return [p.id,p];}));
    root.classList.add('textbook-lesson');article.classList.add('textbook-lesson-article');
    // A conclusion is mandatory reading. Only its proof and worked examples
    // belong in disclosures; source preservation alone is not presentation.
    var results=new Map((lesson.conclusions||[]).concat(lesson.supplementary_conclusions||[]).map(function(p){return [p.id,p];}));
    var mainIds=new Set((lesson.conclusions||[]).map(function(p){return p.id;}));
    report.conclusions=0;
    var integratedSeen=new Set();
    Array.from(root.querySelectorAll('p')).forEach(function(marker){
      var match=marker.textContent.trim().match(/^\[\[TEXTBOOK_INTEGRATED:([^\]]+)\]\]$/);if(!match||!marker.parentNode)return;
      var ids=match[1].split('|'),end=marker.nextElementSibling;
      while(end&&end.textContent.trim()!=='[[TEXTBOOK_END_INTEGRATED]]')end=end.nextElementSibling;
      if(!end||ids.some(function(id){return !results.has(id);})){report.errors.push('正文知识覆盖标记无效：'+match[1]);return;}
      var section=node(doc,'section','lesson-integrated-knowledge');section.dataset.knowledgeIds=ids.join(' ');
      var content=node(doc,'div','lesson-integrated-statement');
      while(marker.nextSibling&&marker.nextSibling!==end)content.appendChild(marker.nextSibling);
      if(!content.textContent.trim()){report.errors.push('正文知识覆盖段落为空：'+match[1]);return;}
      var parent=section;
      ids.forEach(function(id){
        var ref=node(doc,'div','lesson-knowledge-reference');
        if(!integratedSeen.has(id)){if(mainIds.has(id)){ref.dataset.textbookResult=id;report.conclusions++;}else ref.dataset.textbookSupplement=id;integratedSeen.add(id);}
        else ref.dataset.textbookCoveragePart=id;
        parent.appendChild(ref);parent=ref;
      });
      parent.appendChild(content);marker.replaceWith(section);end.remove();
    });
    Array.from(root.querySelectorAll('p')).forEach(function(marker){
      var match=marker.textContent.trim().match(/^\[\[TEXTBOOK_RESULT:([^\]]+)\]\]$/);if(!match)return;
      var result=results.get(match[1]);
      if(!result){report.errors.push('缺少教材结论：'+match[1]);return;}
      var section=node(doc,'section','lesson-conclusion');section.dataset.textbookResult=result.id;
      section.appendChild(node(doc,'h3','lesson-conclusion-title',result.title));
      var content=node(doc,'div','lesson-conclusion-statement markdown');
      if(w.DSHRenderCore&&w.marked&&w.katex){
        content.innerHTML=w.DSHRenderCore.createRenderer({marked:w.marked,katex:w.katex}).renderMarkdown(result.statement_markdown);
      }else rich(content,result.statement_markdown);
      section.appendChild(content);marker.replaceWith(section);report.conclusions++;
    });
    var paragraphs=Array.from(root.querySelectorAll('p'));
    paragraphs.forEach(function(marker){
      var match=marker.textContent.trim().match(/^\[\[TEACHING_(VISUAL|MEDIA):([^\]]+)\]\]$/);
      if(!match)return;
      var kind=match[1],id=match[2],plan=(kind==='VISUAL'?visualMap:mediaMap).get(id);
      if(!plan){report.errors.push('未登记的教学标记：'+id);return;}
      var block=node(doc,'section','teaching-block '+(kind==='VISUAL'?'teaching-visual-layout':'teaching-media'));
      block.dataset.teachingId=id;block.dataset.teachingKind=kind;
      var purposeRows=((options.data||{}).media_purposes||{}).items||[];
      var purpose=purposeRows.find(function(row){return row.unit_id===options.unitId&&row.id===id;});
      // Publish learner-facing copy only; plan fields may contain drawing instructions.
      var purposeRuntime=w.DSHMediaPurpose||purposeModule;
      if(purpose&&purposeRuntime)disposers.push(purposeRuntime.mount(block,purpose));
      var textLed=kind==='VISUAL'&&plan.presentation==='text-led-figure';
      if(textLed)block.classList.add('teaching-text-led');
      var stage=node(doc,'div','teaching-stage');block.appendChild(stage);marker.replaceWith(block);
      var ancestors=[],parent=block.parentElement;while(parent){if(parent.tagName==='DETAILS')ancestors.push(parent);parent=parent.parentElement;}
      var mounted=false;function mountWhenOpen(){if(ancestors.some(function(d){return !d.open;})){stage.querySelectorAll('video,audio').forEach(function(m){if(!m.paused)m.pause();});return;}if(mounted)return;mounted=true;
      try{
        if(kind==='VISUAL'){
          var card;
          if(textLed){
            if(!w.DSHTextLedFigures)throw new Error('正文图解运行时未就绪');
            card=w.DSHTextLedFigures.createFigure(doc,plan);
            if(card.__dispose)disposers.push(card.__dispose);
          }else{
            if(!w.DSHStudyVisuals||!w.DSHStudyVisuals.createTeachingCard)throw new Error('交互图像运行时未就绪');
            card=w.DSHStudyVisuals.createTeachingCard(doc,plan,{contextOwned:true,compact:true});
          }
          card.dataset.teachingVisualId=id;stage.appendChild(card);report.visuals++;
        }else{
          var animation=((options.data||{}).animations||{})[options.unitId]||[];
          var payload=animation.find(function(a){return a.request_id===plan.request_id;});
          if(!payload||!w.DSHAnimations)throw new Error('选定的动画尚未生成');
          disposers.push(w.DSHAnimations.mountAnimations(stage,payload,{compact:true,title:plan.title}));report.media++;
        }
      }catch(error){report.errors.push(id+': '+error.message);stage.appendChild(node(doc,'p','teaching-unavailable','这段图解暂时无法显示。'));}
      if(root.__studyController)root.__studyController.refresh();
      }
      ancestors.forEach(function(d){d.addEventListener('toggle',mountWhenOpen);disposers.push(function(){d.removeEventListener('toggle',mountWhenOpen);});});mountWhenOpen();
      // The next authored paragraph interprets the observation. It deliberately
      // stays in the same narrative flow instead of becoming another card.
    });
    if((lesson.transform_tables||[]).length){
      var tables=node(doc,'section','lesson-transform-tables');
      if(lesson.table_conventions_markdown){
        var conventions=node(doc,'div','lesson-table-conventions markdown');
        if(w.DSHRenderCore&&w.marked&&w.katex)conventions.innerHTML=w.DSHRenderCore.createRenderer({marked:w.marked,katex:w.katex}).renderMarkdown(lesson.table_conventions_markdown);
        else rich(conventions,lesson.table_conventions_markdown);
        tables.appendChild(conventions);
      }
      lesson.transform_tables.forEach(function(source){
        tables.appendChild(node(doc,'h2','',source.title));
        var wrap=node(doc,'div','lesson-transform-table-wrap'),table=node(doc,'table','lesson-transform-table');
        var head=node(doc,'thead'),labels=node(doc,'tr');
        ['原表编号','函数、变换关系及适用条件'].forEach(function(label){labels.appendChild(node(doc,'th','',label));});head.appendChild(labels);table.appendChild(head);
        var body=node(doc,'tbody');
        (source.rows||[]).forEach(function(result){
          var row=node(doc,'tr');row.dataset.textbookTableResult=result.id;
          row.appendChild(node(doc,'td','lesson-table-number',String(result.original_number)));
          var content=node(doc,'td','lesson-table-statement markdown');
          if(w.DSHRenderCore&&w.marked&&w.katex)content.innerHTML=w.DSHRenderCore.createRenderer({marked:w.marked,katex:w.katex}).renderMarkdown(result.statement_markdown);
          else rich(content,result.statement_markdown);
          row.appendChild(content);body.appendChild(row);
        });table.appendChild(body);wrap.appendChild(table);tables.appendChild(wrap);
      });
      if((lesson.table_source_pages||[]).length){
        var scans=node(doc,'details','lesson-table-sources');scans.appendChild(node(doc,'summary','','对照教材原表页'));
        lesson.table_source_pages.forEach(function(source){var img=doc.createElement('img');img.src=source.url;img.alt='教材原表第'+source.user_pdf_page+'页';img.loading='lazy';scans.appendChild(img);});tables.appendChild(scans);
      }
      (root.querySelector('[data-reader-pointer="/body_markdown"]')||article).appendChild(tables);
    }
    var candidates=Array.from(root.querySelectorAll('[data-reader-group]')).filter(function(group){
      return !['title','body_markdown'].includes(group.dataset.readerGroup)&&
        !group.closest('[data-reader-pointer="/body_markdown"],.teaching-block,.lesson-sources');
    });
    candidates=candidates.filter(function(group){return !candidates.some(function(other){return other!==group&&other.contains(group);});});
    if(candidates.length){
      var sources=node(doc,'details','lesson-sources');
      sources.appendChild(node(doc,'summary','','需要时查阅：证明、例题和补充资料'));
      candidates.forEach(function(group){sources.appendChild(group);});article.appendChild(sources);
    }
    var unplacedSupplements=(lesson.supplementary_conclusions||[]).filter(function(r){return !integratedSeen.has(r.id);});
    if(unplacedSupplements.length){
      var extra=node(doc,'section','lesson-exercise-results');
      extra.appendChild(node(doc,'h2','','教材例题与习题中的可复用结论'));
      unplacedSupplements.forEach(function(result){
        var section=node(doc,'section','lesson-conclusion');section.dataset.textbookSupplement=result.id;
        section.appendChild(node(doc,'h3','lesson-conclusion-title',result.title));
        var text=node(doc,'div','lesson-conclusion-statement markdown');
        if(w.DSHRenderCore&&w.marked&&w.katex)text.innerHTML=w.DSHRenderCore.createRenderer({marked:w.marked,katex:w.katex}).renderMarkdown(result.statement_markdown);
        else rich(text,result.statement_markdown);
        section.appendChild(text);extra.appendChild(section);
      });(root.querySelector('[data-reader-pointer="/body_markdown"]')||root.querySelector('[data-reader-group="body_markdown"]')||article).appendChild(extra);
    }
    if(unit.study_textbook_original){
      var original=node(doc,'details','lesson-textbook-source');original.appendChild(node(doc,'summary','','对照教材原文'));
      var recognition=unit.study_textbook_pdf?node(doc,'details','lesson-recognition-text'):original;
      if(unit.study_textbook_pdf){
        var link=node(doc,'a','','打开教材 PDF');link.href=unit.study_textbook_pdf;link.target='_blank';link.rel='noopener';original.appendChild(link);
        original.appendChild(node(doc,'p','','原页以 PDF 为准；识别文字可用于检索。'));
        recognition.appendChild(node(doc,'summary','','文字识别稿（便于检索）'));original.appendChild(recognition);
      }
      recognition.addEventListener('toggle',function(){
        if(!recognition.open||recognition.dataset.rendered)return;recognition.dataset.rendered='1';
        var content=node(doc,'div','lesson-original-content');
        if(w.DSHRenderCore&&w.marked&&w.katex){
          var renderer=w.DSHRenderCore.createRenderer({marked:w.marked,katex:w.katex});
          content.innerHTML=renderer.renderOriginal(unit.study_textbook_display||unit.study_textbook_original);
        }else content.textContent=unit.study_textbook_display||unit.study_textbook_original;
        recognition.appendChild(content);
      });article.appendChild(original);
    }
    disposers.push(arrangeMedia(root,article,new Map(Array.from(visualMap).concat(Array.from(mediaMap)))));
    function dispose(){disposers.forEach(function(fn){try{fn();}catch(e){}});disposers=[];}
    root.__teachingState={article:article,unitId:options.unitId,report:report,dispose:dispose};
    if(root.__studyController)root.__studyController.refresh();
    return report;
  }
  function boot(doc){
    if(!doc)return;var root=doc.getElementById('content');if(!root)return;
    var w=doc.defaultView,pending=false;
    function update(){pending=false;var d=w.__DATA__||{},uid=doc.documentElement.dataset.readerUnit||String(w.location.hash||'').replace(/^#\//,'');
      var unit=(d.units||{})[uid];if(unit&&unit.study_lesson)enhance(root,{unit:unit,data:d,unitId:uid});
      else if(root.__teachingState){root.__teachingState.dispose();delete root.__teachingState;root.classList.remove('textbook-lesson');}}
    update();new w.MutationObserver(function(records){
      if(pending||!records.some(function(record){return Array.from(record.addedNodes).some(function(n){return n.nodeType===1&&(n.matches('.unit,.reader-document')||n.querySelector('.reader-document'));});}))return;
      pending=true;w.setTimeout(update,0);
    }).observe(root,{childList:true,subtree:true});
    w.addEventListener('pagehide',function(){if(root.__teachingState)root.__teachingState.dispose();});
  }
  return {enhance:enhance,boot:boot};
});
