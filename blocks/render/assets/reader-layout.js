/* Organize the rendered reader, without changing its frozen content or mount contract. */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.DSHReaderLayout = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';
  function children(node) { return Array.prototype.slice.call(node.childNodes); }
  function clean(value) { return String(value || '').replace(/\s+/g, ' ').trim(); }
  function label(node) {
    if (!node) return '';
    if (node.nodeType === 3) return node.data;
    if (node.nodeType !== 1) return '';
    if (node.matches('.katex')) {
      var annotation = node.querySelector('annotation[encoding="application/x-tex"]');
      return annotation ? annotation.textContent : node.textContent;
    }
    if (node.matches('.reader-field-label, .reader-proof-number')) return '';
    return children(node).map(label).join('');
  }
  function readable(node) { return clean(label(node)); }
  function digest(value) {
    var n=2166136261;
    for(var i=0;i<value.length;i++) { n^=value.charCodeAt(i); n=Math.imul(n,16777619); }
    return (n>>>0).toString(36);
  }
  function element(doc,tag,className) { var n=doc.createElement(tag);if(className)n.className=className;return n; }
  // Synthetic titles are flattened text whose formulas are TeX source; the
  // caller's inline renderer (same as the body) turns them back into math.
  var titleRenderer=null;
  function setTitle(heading,title) {
    if(titleRenderer){try{heading.innerHTML=titleRenderer(String(title||''));return;}catch(err){}}
    heading.textContent=title;
  }
  function exampleNumber(text) {
    var m=clean(text).match(/^(?:例题?|练习|Example)\s*([0-9一二三四五六七八九十]+(?:[.．-][0-9]+)*)/i);
    return m ? m[0].replace(/\s/g,'') : '';
  }
  function stepNumber(node) {
    if (!node || node.nodeType!==1 || !/^(?:P|DIV)$/.test(node.tagName)) return null;
    if (node.matches('pre,code') || node.querySelector('pre')) return null;
    var text=readable(node);
    var match=text.match(/^(?:逐步(?:计算|证明|推导|求解)[：:]\s*)?第\s*([0-9一二三四五六七八九十百零〇两]+)\s*步(?:[，,：:、。.]|\s|$)/);
    return match ? match[1] : null;
  }
  function stepOrdinal(value, fallback) {
    var text=String(value||'');if(/^\d+$/.test(text))return Number(text);
    var digits={'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9};
    if(Object.prototype.hasOwnProperty.call(digits,text))return digits[text];
    var tens=text.split('十');
    if(tens.length===2&&tens[0].length<=1&&tens[1].length<=1)
      return (tens[0]?digits[tens[0]]:1)*10+(tens[1]?digits[tens[1]]:0)||fallback;
    return fallback;
  }
  var proseCue=/^(?:证明|推导|答案|解答|答|读题(?:与条件)?|选(?:择)?方法|思路|逐步(?:计算|证明|推导|求解)|结论|方法回顾|解后回顾|易错(?:点|提醒|点总提醒)?|小结|依据|注意|于是|所以)[：:。]/;
  function prepareConceptCards(block) {
    var doc=block.ownerDocument;
    children(block).forEach(function(node){
      if(node.nodeType!==1)return;
      if(node.matches('blockquote')) {
        var labelNode=node.querySelector('p > strong:first-child'),name=readable(labelNode);
        if(/^(?:概念|定义|记号)[·：:（(]/.test(name)) {
          node.classList.add('reader-concept-card');node.setAttribute('data-reader-concept-title',name.replace(/^(?:概念|定义|记号)[·：:]\s*/,''));
        }
        return;
      }
      if(node.matches('[data-reader-reference]')) {
        if(/^定义/.test(node.getAttribute('data-reader-reference'))&&!Array.prototype.some.call(node.querySelectorAll('p'),function(p){return /^(?:证明|推导)[：:]/.test(readable(p));}))node.classList.add('reader-concept-card');
        return;
      }
      if(node.tagName!=='P')return;
      var first=node.firstChild;
      while(first&&first.nodeType===3&&!first.data.trim())first=first.nextSibling;
      if(!first||first.nodeType!==1||!first.matches('strong,b')) {
        if(stepNumber(node)||!/称为|叫做|叫作|定义[为：:]|是指/.test(readable(node)))return;
        var conceptLink=Array.prototype.find.call(node.querySelectorAll('a[href^="#reader-"]'),function(link){
          var targetId=link.getAttribute('href').slice(1),reader=block.closest('.reader-document');
          var target=reader&&Array.prototype.find.call(reader.querySelectorAll('[data-reader-group="references"]'),function(item){return item.id===targetId;});
          if(!target)target=doc.getElementById(targetId);
          return target&&target.getAttribute('data-reader-group')==='references'&&!/(?:定理|性质|命题|推论)/.test(readable(link));
        });
        if(!conceptLink)return;
        var compact=element(doc,'div','reader-concept-card');compact.setAttribute('data-reader-concept-title',readable(conceptLink));
        compact.setAttribute('data-reader-concept-synthetic-heading','');node.parentNode.insertBefore(compact,node);compact.appendChild(node);
        return;
      }
      var title=readable(first);
      if(!title||title.length>30||/(?:定理|引理|性质|命题|推论|证明|推导|例题|易错|直觉)/.test(title))return;
      var declaration=[node],cursor=node,complete=/(?:称为|叫做|叫作|定义为|是指)/.test(readable(node));
      // Old named declarations may end with a displayed defining formula.
      // Keep the contiguous declaration; never absorb later explanations,
      // another named topic, a proof, or a media widget into a small card.
      while(!complete&&declaration.length<8) {
        cursor=cursor.nextElementSibling;
        if(!cursor||!/^(?:P|UL|OL)$/.test(cursor.tagName)||cursor.querySelector('strong,b')||/^(?:证明|推导|例如|注意|这里|因此|所以|由此)/.test(readable(cursor)))break;
        declaration.push(cursor);complete=/(?:称为|叫做|叫作|定义为|是指)/.test(readable(cursor));
      }
      if(!complete)return;
      var card=element(doc,'div','reader-concept-card');card.setAttribute('data-reader-concept-title',title);
      node.parentNode.insertBefore(card,node);declaration.forEach(function(part){card.appendChild(part);});
    });
  }
  function boundary(node) {
    if (!node || node.nodeType!==1 || node.matches('.reader-media,.reader-field-label,pre,code')) return null;
    var text=readable(node),number=exampleNumber(text),titleNode=null,title='';
    if(node.hasAttribute('data-reader-concept-title')) {
      titleNode=node.querySelector('p');
      return {title:node.getAttribute('data-reader-concept-title'),kind:'concept',node:titleNode,
        prefix:titleNode&&titleNode.querySelector('strong,b'),headingParent:node,
        synthetic:node.hasAttribute('data-reader-concept-synthetic-heading')};
    }
    if(node.tagName==='BLOCKQUOTE')return null;
    if (node.hasAttribute('data-reader-reference')) {
      titleNode=node.querySelector('h1,h2,h3,h4,h5,h6,p');
      title=readable(titleNode&&titleNode.querySelector('strong')) || node.getAttribute('data-reader-reference');
      var prefix=titleNode&&titleNode.tagName==='P'?titleNode.querySelector('strong,b'):null;
      return {title:title,kind:/^定义/.test(title)?'concept':'property',node:titleNode,prefix:prefix,headingParent:node};
    }
    if (/^H[1-6]$/.test(node.tagName)) return {title:text,kind:number||/^例题$/.test(text)?'example':'section',number:number,node:node};
    if (node.tagName!=='P') return null;
    if (number) {
      var link=node.querySelector('a[href^="#"]');
      title=link ? readable(link) : number;
      return {title:title,kind:'example',number:number,node:node,exampleLink:link,synthetic:!link||text.length>title.length+number.length+8};
    }
    if (stepNumber(node) || proseCue.test(text)) return null;
    var first=node.firstChild;
    while(first&&first.nodeType===3&&!first.data.trim())first=first.nextSibling;
    if (first&&first.nodeType===1&&/^(STRONG|B|EM)$/.test(first.tagName)) {
      title=readable(first);
      var punctuation=first.nextSibling&&first.nextSibling.nodeType===3 ? first.nextSibling.data.match(/^[。:：]/) : null;
      if (title.length<=70 && !proseCue.test(title) && (/[。:：]$/.test(title)||punctuation))
        return {title:title.replace(/[。:：]$/,''),kind:/^(?:定义|概念)/.test(title)?'concept':'section',node:node,prefix:first,punctuation:punctuation?first.nextSibling:null};
      if (title.length<=70 && readable(node)===title) return {title:title,kind:'section',node:node};
    }
    // A short authored prefix such as “乘法：” is a heading; mathematical or
    // long sentence prefixes remain ordinary prose. Only the text node moves.
    if(first&&first.nodeType===3) {
      var match=first.data.match(/^\s*([^\s，,。！？!?：:]{2,16})[：:]/);
      if(match&&!proseCue.test(match[0])&&!/^(?:没有|下面|情形|因为|所以|因此|于是|其中|假设|注意)/.test(match[1])&&!/[=\\$<>]/.test(match[1]))
        return {title:match[1],kind:'section',node:node,textPrefix:first,prefixLength:match[0].length};
    }
    return null;
  }
  function markHeading(topic, info, doc) {
    if(!info||!info.node)return;
    var heading=info.node;
    // The authored prefix and an automatically expanded example link can both
    // say “例2：”. Hide only that exact redundant prefix, retaining its nodes
    // and all formula/link content for the reader's source identity.
    if(info.exampleLink && info.exampleLink.parentNode===heading) {
      var prefixNodes=[],cursor=heading.firstChild;
      while(cursor&&cursor!==info.exampleLink){prefixNodes.push(cursor);cursor=cursor.nextSibling;}
      var prefixText=clean(prefixNodes.map(label).join(''));
      var prefixNumber=exampleNumber(prefixText),linkNumber=exampleNumber(readable(info.exampleLink));
      if(prefixNumber && prefixNumber===linkNumber &&
          prefixText.replace(/\s/g,'').replace(/[：:。．.,，、]$/,'')===prefixNumber) {
        var preserved=element(doc,'span','reader-repeated-example-prefix');
        preserved.hidden=true;preserved.setAttribute('aria-hidden','true');
        heading.insertBefore(preserved,heading.firstChild);
        prefixNodes.forEach(function(node){preserved.appendChild(node);});
      }
    }
    if(info.synthetic) {
      heading=element(doc,'h2','reader-topic-title');setTitle(heading,info.title);
      var container=info.headingParent||topic;container.insertBefore(heading,container.firstChild);return;
    }
    if(info.prefix||info.textPrefix) {
      heading=element(doc,'h2','reader-topic-title');
      if(info.prefix){heading.appendChild(info.prefix);if(info.punctuation){info.punctuation.splitText(1);heading.appendChild(info.punctuation);}}
      else { info.textPrefix.splitText(info.prefixLength);heading.appendChild(info.textPrefix); }
      var parent=info.headingParent||topic;parent.insertBefore(heading,parent.firstChild);
    } else {
      heading.classList.add('reader-topic-title');
      if(!/^H[1-6]$/.test(heading.tagName)){heading.setAttribute('role','heading');heading.setAttribute('aria-level','2');}
    }
  }
  function createTopic(doc,unitId,index,info) {
    var topic=element(doc,'section','reader-topic');
    topic.id='reader-topic-'+digest(String(unitId))+'-'+index;
    topic.setAttribute('data-reader-topic','');
    topic.setAttribute('data-reader-topic-kind',info.kind||'section');
    topic.setAttribute('data-reader-topic-title',info.title||'正文');
    topic.setAttribute('tabindex','-1');
    if(info.number)topic.setAttribute('data-reader-example-label',info.number);
    return topic;
  }
  function bodyTopics(block,unitId,startIndex,unitTitle) {
    var doc=block.ownerDocument,original=children(block),current=null,currentInfo=null,result=[],propertyCount=0;
    original.forEach(function(node) {
      if(node.nodeType===1&&node.matches('.reader-field-label'))return;
      var info=boundary(node);
      // Only a list explicitly introduced as properties establishes numbered
      // property proofs. An arbitrary exercise list must not become this index.
      if(node.nodeType===1&&node.tagName==='P') {
        var intro=readable(node).match(/([^。！？!?]*性质)如下[：:]$/),next=node.nextElementSibling;
        while(next&&next.matches('.reader-media'))next=next.nextElementSibling;
        if(intro&&next&&next.tagName==='OL') {
          propertyCount=next.children.length;
          info={title:intro[1].length<=28?intro[1]:'性质',kind:'property',node:node,synthetic:true};
        } else if(propertyCount) {
          var ordinal=readable(node).match(/^[（(](\d+)[)）]/);
          if(ordinal&&Number(ordinal[1])<=propertyCount) {
            info={title:'性质'+ordinal[1]+'的推导',kind:'property',node:node,synthetic:true};
          }
        }
      }
      if(info&&(info.kind==='example'||(info.node&&/^H[1-6]$/.test(info.node.tagName))))propertyCount=0;
      if(info&&currentInfo&&info.number&&info.number===currentInfo.number)info=null;
      if(info || !current) {
        if(node.nodeType===3&&!node.data.trim()&&!current)return;
        currentInfo=info||{title:unitTitle||'正文',kind:'section'};
        current=createTopic(doc,unitId,startIndex+result.length,currentInfo);
        block.insertBefore(current,node);result.push(current);
      }
      current.appendChild(node);
      if(info)markHeading(current,info,doc);
    });
    result.forEach(function(topic){
      if(topic.getAttribute('data-reader-topic-kind')==='section'&&topic.querySelector('[data-concept-id]')&&/定义/.test(readable(topic)))
        topic.setAttribute('data-reader-topic-kind','concept');
    });
    return result;
  }
  function addStep(list,number,stepId) {
    var doc=list.ownerDocument,li=element(doc,'li','reader-proof-step');
    var badge=element(doc,'span','reader-proof-number');badge.setAttribute('aria-hidden','true');
    badge.textContent=String(number).padStart(2,'0');
    li.setAttribute('data-proof-step-no',String(number));
    if(stepId)li.setAttribute('data-proof-step-id',stepId);
    var content=element(doc,'div','reader-proof-content');li.appendChild(badge);li.appendChild(content);list.appendChild(li);
    return content;
  }
  function bodySteps(topic) {
    var current=null,list=null,count=0,doc=topic.ownerDocument;
    children(topic).forEach(function(node) {
      if(node.nodeType===1&&(node.matches('.reader-topic-title, summary')||node.matches('[data-reader-group],details'))){current=null;list=null;return;}
      var marker=stepNumber(node);
      if(marker!==null) {
        if(!list){list=element(doc,'ol','reader-proof-steps');topic.insertBefore(list,node);}
        count++;current=addStep(list,stepOrdinal(marker,count),null);current.appendChild(node);
      } else if(current) {
        if(node.nodeType===1 && /^(?:结论|方法回顾|解后回顾|小结|易错(?:点|提醒)?)[：:]/.test(readable(node))){current=null;list=null;}
        else current.appendChild(node);
      }
    });
    return count;
  }
  function structuredSteps(group) {
    if(group.querySelector('.reader-proof-steps'))return;
    var doc=group.ownerDocument,list=null,current=null,currentId='',count=0;
    children(group).forEach(function(node) {
      if(node.nodeType!==1)return;
      var stepId=node.getAttribute('data-reader-step');
      var pointer=node.getAttribute('data-reader-pointer')||'';
      var pointerStep=pointer.match(/\/steps\/(\d+)\//);
      if(stepId||pointerStep) {
        var id=stepId||pointerStep[1];
        if(!list){list=element(doc,'ol','reader-proof-steps');group.insertBefore(list,node);}
        if(id!==currentId||!current){count++;current=addStep(list,stepOrdinal(node.getAttribute('data-reader-step-no'),count),stepId);currentId=id;}
        current.appendChild(node);
      } else {current=null;currentId='';list=null;}
    });
  }
  function stepHeading(step) {
    var content=step.querySelector('.reader-proof-content');
    if(!content||content.querySelector('.reader-proof-heading'))return;
    var paragraph=content.querySelector('p');
    if(!paragraph||!paragraph.firstChild||paragraph.firstChild.nodeType!==3)return;
    var first=paragraph.firstChild;
    var prefix=first.data.match(/^(?:逐步(?:计算|证明|推导|求解)[：:]\s*)?第\s*[0-9一二三四五六七八九十百零〇两]+\s*步[，,：:、。.]\s*/);
    var start=prefix?prefix[0].length:0;
    var title=first.data.slice(start).match(/^([^\s，,。！？!?：:]{2,14})[，,：:。]/);
    if(!title||/[=\\$<>0-9]/.test(title[1])||/^(?:因为|由于|若|设|因|由|当|所以|因此|于是|以上|其中)/.test(title[1]))return;
    if(prefix) {
      var rest=first.splitText(start),hidden=element(step.ownerDocument,'span','reader-proof-prefix');
      hidden.hidden=true;hidden.setAttribute('aria-hidden','true');paragraph.insertBefore(hidden,first);hidden.appendChild(first);first=rest;
    }
    first.splitText(title[0].length);
    var heading=element(step.ownerDocument,'h4','reader-proof-heading');heading.appendChild(first);
    paragraph.parentNode.insertBefore(heading,paragraph);
  }
  function groupTitle(group) {
    var field=Array.prototype.find.call(group.querySelectorAll('[data-reader-pointer]'),function(n){return /\/(?:label|title|name)$/.test(n.getAttribute('data-reader-pointer'));});
    return readable(field)||group.getAttribute('data-reader-owner')||'例题';
  }
  function placeExamples(root,body,unitId,nextIndex) {
    var groups=Array.prototype.slice.call(root.querySelectorAll('[data-reader-group="examples"]'));
    var doc=root.ownerDocument,result=[];
    groups.forEach(function(group) {
      if(group.closest('[data-reader-topic]'))return;
      var target=body.find(function(topic){
        if(topic.getAttribute('data-reader-topic-kind')!=='example')return false;
        return Array.prototype.some.call(topic.querySelectorAll('a[href^="#"]'),function(a){
          var id;try{id=decodeURIComponent(a.getAttribute('href').slice(1));}catch(e){return false;}
          return id===group.id;
        });
      });
      structuredSteps(group);
      group.classList.add('reader-structured-example');
      if(target) {
        var bodyCount=target.querySelectorAll(':scope > .reader-proof-steps > .reader-proof-step').length;
        if(bodyCount>=2) {
          var details=element(doc,'details','reader-example-alternative');
          details.open=false;
          var summary=element(doc,'summary');summary.textContent='步骤整理与补充依据 · '+groupTitle(group);
          details.appendChild(summary);target.appendChild(details);details.appendChild(group);
        } else target.appendChild(group);
      } else {
        var info={title:groupTitle(group),kind:'example'};
        var topic=createTopic(doc,unitId,nextIndex+result.length,info);
        group.parentNode.insertBefore(topic,group);topic.appendChild(group);
        var h=element(doc,'h2','reader-topic-title');setTitle(h,info.title);topic.insertBefore(h,group);result.push(topic);
      }
    });
    return result;
  }
  function compare(topic,region) {
    region=region||topic;
    var doc=topic.ownerDocument;
    if(region===topic)Array.prototype.slice.call(topic.querySelectorAll('details')).filter(function(node){return node.closest('[data-reader-topic]')===topic;}).forEach(function(node){compare(topic,node);});
    var media=Array.prototype.slice.call(region.querySelectorAll('.reader-media[data-media-anchor]')).filter(function(node){
      return node.closest('[data-reader-topic]')===topic&&node.closest('details')===region.closest('details');
    });
    if(!media.length)return;
    media.forEach(function(node){
      var step=node.closest('.reader-proof-step');
      if(!step)return;
      var button=element(doc,'button','reader-proof-media-link');button.type='button';button.textContent='查看配套演示';
      button.setAttribute('data-reader-media-target',node.getAttribute('data-media-anchor'));
      node.parentNode.insertBefore(button,node);
      node.setAttribute('tabindex','-1');
    });
    // For a structured example, statement and approach span both columns;
    // comparison begins at the actual proof, as in the approved sample.
    var container=region,group=media[0].closest('.reader-structured-example');
    if(group&&group.closest('details')===region.closest('details')&&media.every(function(node){return group.contains(node);})&&group.querySelector(':scope > .reader-proof-steps'))container=group;
    var comparison=element(doc,'div','reader-comparison'),text=element(doc,'div','reader-text-column'),side=element(doc,'aside','reader-media-column');
    side.setAttribute('aria-label','随文图示与动画');
    var proof=container.querySelector(':scope > .reader-proof-steps');
    var content=children(container),split=proof?content.indexOf(proof):-1;
    // Prelude remains above the comparison; a proof's complete subsequent prose
    // stays in its text column. A conceptual topic keeps its heading above both.
    if(split<0)split=content.findIndex(function(node){return !(node.nodeType===1&&node.matches('.reader-topic-title, summary'));});
    if(split<0)split=0;
    container.insertBefore(comparison,content[split]||null);comparison.appendChild(text);comparison.appendChild(side);
    content.slice(split).forEach(function(node){text.appendChild(node);});
    media.forEach(function(node){side.appendChild(node);});
  }
  function installExampleExpansion(doc) {
    if(doc.__readerExampleExpansion)return;
    doc.__readerExampleExpansion=true;
    doc.addEventListener('click',function(event){
      var summary=event.target.closest&&event.target.closest('summary');
      var outer=summary&&summary.parentElement;
      if(!outer||!outer.matches('details.reader-example-disclosure')||outer.open)return;
      var number=exampleNumber(readable(summary));
      if(!number)return; // A collection headed only “例题” never opens all answers.
      var matching=Array.prototype.filter.call(outer.querySelectorAll('details[data-reader-group="examples"]'),function(group){return exampleNumber(groupTitle(group))===number;});
      if(matching.length!==1)return;
      // A real next task runs after Blink's native summary activation; a
      // Promise microtask may run before that activation. Programmatic open state,
      // initial rendering and cancelled link clicks cannot reveal a question.
      doc.defaultView.setTimeout(function(){
        if(event.defaultPrevented||!outer.open||!outer.isConnected)return;
        var group=matching[0];if(!outer.contains(group))return;
        group.open=true;
        for(var parent=group.parentElement;parent&&parent!==outer;parent=parent.parentElement)
          if(parent.matches('details.reader-example-alternative'))parent.open=true;
      },0);
    });
  }
  function installMediaNavigation(doc) {
    if(doc.__readerMediaNavigation)return;
    doc.__readerMediaNavigation=true;
    // Reference windows clone the original proof. Delegate by actual mount
    // identity so their buttons also lead to the preserved source media.
    doc.addEventListener('click',function(event){
      var button=event.target.closest&&event.target.closest('[data-reader-media-target]');
      if(!button)return;
      var anchor=button.getAttribute('data-reader-media-target');
      var media=Array.prototype.find.call(doc.querySelectorAll('.reader-media[data-media-anchor]'),function(node){
        return node.getAttribute('data-media-anchor')===anchor&&!node.closest('.reader-popup');
      });
      if(!media)return;
      event.preventDefault();
      var step=button.closest('.reader-proof-step'),topic=button.closest('[data-reader-topic]');
      if(topic)topic.querySelectorAll('.reader-proof-step.is-active').forEach(function(other){other.classList.remove('is-active');});
      if(step)step.classList.add('is-active');
      if(media.scrollIntoView)media.scrollIntoView({block:'nearest'});
      media.focus({preventScroll:true});
    });
  }
  function topics(root) {
    return Array.prototype.map.call(root.querySelectorAll('[data-reader-topic]'),function(node){
      return {id:node.id,title:node.getAttribute('data-reader-topic-title'),kind:node.getAttribute('data-reader-topic-kind'),element:node};
    });
  }

  // Display projection only: move existing nodes; never rewrite source fields,
  // formulas, reference identities or frozen media placement offsets.
  function disclosure(doc,kind,title) {
    var node=element(doc,'details',kind==='example'?'reader-example-disclosure':'reader-theorem-proof');
    node.setAttribute('data-reader-disclosure',kind);
    var summary=element(doc,'summary');summary.textContent=title||'展开完整证明';node.appendChild(summary);
    return node;
  }
  function proseNodes(node) {
    var result=[],walker=node.ownerDocument.createTreeWalker(node,4),text;
    while((text=walker.nextNode())) {
      var parent=text.parentElement;
      if(parent&&!parent.closest('.katex,code,pre,.reader-field-label,.reader-math-references'))result.push(text);
    }
    return result;
  }
  function proseText(node) {return proseNodes(node).map(function(n){return n.data;}).join('');}
  function splitProse(node,offset) {
    var texts=proseNodes(node),remaining=offset,point;
    for(var i=0;i<texts.length;i++) {
      if(remaining<=texts[i].data.length){point=texts[i];break;}
      remaining-=texts[i].data.length;
    }
    if(!point)return null;
    var range=node.ownerDocument.createRange();range.setStart(point,remaining);range.setEnd(node,node.childNodes.length);
    var tail=node.cloneNode(false);tail.removeAttribute('id');tail.appendChild(range.extractContents());node.after(tail);
    return tail;
  }
  var proofStart=/^(?:(?:先|再|只需|首先|其次|最后|下面|以下|我们(?:先|来)?|现来|这里|为此)(?:来|给出)?(?:证明|推导)|(?:先|再|后)证(?=第|必要性|充分性)|证明|证|推导)(?=[：:。.\s]|必要性|充分性|如下|本节|第|$)/;
  var proofEnd=/(?:证毕[。.]?|证明完毕[。.]?|(?:命题|定理|结论)?得证[。.]?|[□∎]|Q\.?E\.?D\.?[。.]?)/;
  function proofBoundary(node) {
    if(node.nodeType!==1)return false;
    if(/^H[1-6]$/.test(node.tagName))return true;
    if(node.matches('[data-reader-topic], .reader-concept-card, .reader-theorem-inline, details'))return true;
    var text=proseText(node).trim();
    if(/^(?:结论|直观解释|直观上看|直观地看|直观上|直觉|含义|用途|几何上看|几何上|几何意义|注意|易错|小结|回顾|定义|概念|例题|例\s*\d+)[：:·\s]/.test(text))return true;
    // A named strong/emphasis prefix starts the next authored concept/property.
    var first=node.firstElementChild;
    return !!first&&/^(STRONG|B|EM|I)$/.test(first.tagName)&&!proofStart.test(text)&&
      !/^(?:必要性|充分性|第[一二三四五六七八九十\d]+步|假设|由|所以|因此|故)/.test(text);
  }
  function foldProofContainer(container) {
    var active=null;
    var title=container.getAttribute&&container.getAttribute('data-reader-topic-title');
    if(title&&/(?:证明|推导)$/.test(title)&&container.querySelector(':scope > .reader-topic-title')) {
      active=disclosure(container.ownerDocument,'proof');container.querySelector(':scope > .reader-topic-title').after(active);
    }
    children(container).forEach(function(node) {
      if(node.nodeType!==1)return;
      if(node.matches('.reader-field-label, .reader-topic-title, summary'))return;
      if(node.matches('[data-teaching-main]')) {active=null;return;}
      if(node.querySelector('[data-teaching-main]')) {
        active=null;
        if(node.matches('[data-reader-topic], .reader-numbered-reference, .card')) foldProofContainer(node);
        return;
      }
      if(node.matches('details')){if(node!==active)active=null;return;}
      if(node.matches('[data-reader-topic], .reader-numbered-reference, .card')) {
        active=null;foldProofContainer(node);return;
      }
      if(node.matches('.reader-concept-card')) {active=null;return;}
      if(node.matches('pre,code,blockquote')){if(active)active.appendChild(node);return;}
      var text=proseText(node),trimmed=text.trim(),isProof=proofStart.test(trimmed)||node.matches('.reader-proof-steps');
      // A proof can start after its theorem statement in the very same paragraph.
      var embedded=/(?:^|[。；;！？]\s*)((?:证明|推导|证)[：:])/.exec(text);
      if(embedded&&embedded.index+embedded[0].indexOf(embedded[1])>0&&!active) {
        var start=embedded.index+embedded[0].indexOf(embedded[1]);
        node=splitProse(node,start);text=proseText(node);trimmed=text.trim();isProof=true;
      }
      if(active&&proofBoundary(node)&&!isProof)active=null;
      if(isProof&&!active) {
        active=disclosure(container.ownerDocument,'proof');node.before(active);
      }
      if(!active)return;
      // Explicit QED ends at the exact text boundary; following prose stays out.
      var end=proofEnd.exec(text);
      if(end&&end.index+end[0].length<text.length&&text.slice(end.index+end[0].length).trim())
        splitProse(node,end.index+end[0].length);
      active.appendChild(node);
      if(end)active=null;
    });
  }
  function foldBodyExamples(root) {
    root.querySelectorAll('[data-reader-topic-kind="example"]').forEach(function(topic) {
      if(topic.querySelector(':scope > .reader-example-disclosure'))return;
      // A topic made only for one structured example already has its owner
      // disclosure. Reuse it rather than adding a second control around it.
      var substantive=Array.prototype.filter.call(topic.children,function(node){return !node.matches('.reader-topic-title');});
      if(substantive.length===1&&substantive[0].matches('details.reader-structured-example[data-reader-group="examples"]')) {
        var owner=substantive[0],heading=topic.querySelector(':scope > .reader-topic-title');
        owner.classList.add('reader-example-disclosure');owner.setAttribute('data-reader-disclosure','example');
        if(heading){var ownerSummary=owner.querySelector(':scope > summary');ownerSummary.textContent='';ownerSummary.appendChild(heading);}
        return;
      }
      var title=topic.querySelector(':scope > .reader-topic-title'),details=disclosure(topic.ownerDocument,'example',topic.getAttribute('data-reader-topic-title')||'例题');
      var summary=details.querySelector('summary');
      if(title){summary.textContent='';summary.appendChild(title);}
      // The authored EXAMPLE anchor now labels a native expansion control.
      // Keep its exact text/formula nodes, but remove link activation here so
      // a click on the title cannot be intercepted as a reference popup.
      // Ordinary references in the example body remain real links.
      summary.querySelectorAll('a').forEach(function(link){
        var label=element(topic.ownerDocument,'span','reader-example-title-reference');
        while(link.firstChild)label.appendChild(link.firstChild);
        link.replaceWith(label);
      });
      var remaining=Array.prototype.slice.call(topic.childNodes), protectedIndex=remaining.findIndex(function(node){return node.nodeType===1&&(node.matches('[data-teaching-main]')||node.querySelector('[data-teaching-main]'));});
      var end=protectedIndex<0?remaining.length:protectedIndex;
      topic.insertBefore(details,remaining[0]||null);
      remaining.slice(0,end).forEach(function(node){details.appendChild(node);});
    });
  }
  function recoveredProofProjection(block,unit,unitId) {
    // This reviewed recovery has unlabeled derivations. Its source archive hash
    // identifies the exact historical revision: never infer these boundaries
    // from a generic title or apply them to a later changed lesson.
    var recovery=unit.study_recovery||{};
    if(unitId!=='CH02-S05-SS06'||recovery.unit_id!==unitId||
       recovery.source_sha256!=='sha256:fe82af22bff4e6dc0d0ffce0a0c87e17039abb6d1d5a180f743fa765d4b7d379'||
       recovery.body_characters!==5017)return;
    var original=(unit.reader_document&&unit.reader_document.blocks||[]).find(function(b){return b.json_pointer==='/body_markdown';});
    if(!original||String(original.raw_text||'').length!==5017)return;
    var regions=[
      {section:'与三角函数的关系',start:'先看',end:'这些关系式说明',title:'展开与三角函数关系的推导'},
      {section:'解析性',start:'由上面的关系式',end:'对于',title:'展开整函数解析性的证明'},
      {section:'解析性',start:'先求',end:'这些点就是',title:'展开双曲余弦零点的推导'},
      {section:'解析性',start:'再求',end:'这些点就是',title:'展开双曲正弦零点的推导'},
      {section:'导数',start:'由定义和',summary:'性质·双曲函数的导数公式',title:'展开双曲函数导数的推导'},
      {section:'周期性',start:'由指数函数的加法公式',summary:'性质·双曲函数的周期性',title:'展开双曲函数周期性的证明'}
    ];
    regions.forEach(function(region){
      var heading=children(block).find(function(node){return node.nodeType===1&&/^H[1-6]$/.test(node.tagName)&&readable(node)===region.section;});
      if(!heading)return;
      var section=[],cursor=heading.nextSibling;
      while(cursor&&!(cursor.nodeType===1&&/^H[1-6]$/.test(cursor.tagName))){section.push(cursor);cursor=cursor.nextSibling;}
      function starts(node,text){return node.nodeType===1&&node.tagName==='P'&&proseText(node).trim().indexOf(text)===0;}
      var start=section.findIndex(function(node){return starts(node,region.start);});
      if(start<0)return;
      var end=-1;
      if(region.end){
        for(var i=start;i<section.length;i++){
          var node=section[i];if(node.nodeType!==1||node.tagName!=='P')continue;
          var offset=proseText(node).indexOf(region.end);
          if(offset<0||i===start&&offset===0)continue;
          if(offset>0){var tail=splitProse(node,offset);if(!tail)return;section.splice(i+1,0,tail);end=i+1;}
          else end=i;
          break;
        }
      }else end=section.findIndex(function(node,index){return index>start&&node.nodeType===1&&node.tagName==='BLOCKQUOTE'&&readable(node).indexOf(region.summary)>=0;});
      if(end<0)return; // A missing reviewed boundary never swallows the rest.
      var proof=disclosure(block.ownerDocument,'proof',region.title);
      proof.setAttribute('data-reader-recovery-proof',region.section);
      section[start].before(proof);section.slice(start,end).forEach(function(node){proof.appendChild(node);});
    });
  }

  function projectDisclosures(root) {
    root.querySelectorAll('[data-reader-legacy-proof]').forEach(function(source) {
      var proof=disclosure(source.ownerDocument,'proof');
      while(source.firstChild)proof.appendChild(source.firstChild);source.replaceWith(proof);
    });
    foldBodyExamples(root);
    root.querySelectorAll('[data-reader-pointer="/body_markdown"], .markdown.body').forEach(foldProofContainer);
  }

  function enhance(root,unit,unitId,options) {
    if(!root||!root.querySelectorAll)return [];
    titleRenderer=(options&&typeof options.renderTitle==='function')?options.renderTitle:null;
    if(root.querySelector('[data-reader-topic]'))return topics(root);
    unit=unit||{};
    installMediaNavigation(root.ownerDocument);
    installExampleExpansion(root.ownerDocument);
    if(unit.study_lesson){
      // Authored narrative has its own paragraph order and exact media markers.
      // Colon-based legacy heading inference fragments prose and destroys IDs.
      projectDisclosures(root);
      root.classList.add('reader-comparison-layout');
      return [];
    }
    var bodies=Array.prototype.slice.call(root.querySelectorAll('[data-reader-group="body_markdown"] [data-reader-pointer="/body_markdown"], .markdown.body'));
    var all=[];
    bodies.forEach(function(block){recoveredProofProjection(block,unit,unitId||unit.unit_id);prepareConceptCards(block);all=all.concat(bodyTopics(block,unitId||unit.unit_id||'unit',all.length,unit.title));});
    all.forEach(bodySteps);
    all=all.concat(placeExamples(root,all,unitId||unit.unit_id||'unit',all.length));
    root.querySelectorAll('.reader-proof-step').forEach(stepHeading);
    projectDisclosures(root);
    all.forEach(function(topic){compare(topic);});
    // Long authored equalities get their own visual line. The formula nodes,
    // source text, and mathematical relationships remain exactly the same.
    root.querySelectorAll('.reader-proof-content .katex').forEach(function(math) {
      var source=math.querySelector('annotation[encoding="application/x-tex"]');
      if(source && source.textContent.length>24 && /[=]|\\(?:Leftrightarrow|iff)/.test(source.textContent) && !math.closest('.katex-display')) {
        math.classList.add('reader-proof-formula');
        var after=math.nextSibling, punctuation=after&&after.nodeType===3&&after.data.match(/^\s*[，。；,.;]/);
        if(punctuation) {
          var mark=element(math.ownerDocument,'span','reader-proof-punctuation');
          mark.textContent=punctuation[0]; after.deleteData(0,punctuation[0].length); math.appendChild(mark);
        }
      }
    });
    root.classList.add('reader-comparison-layout');
    return topics(root);
  }
  return {enhance:enhance,topics:topics};
});
