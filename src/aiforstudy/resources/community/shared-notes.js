/* Public notes are separate from the reader's private aifs-notes database. */
(function(root,factory){
  if(typeof module==='object'&&module.exports)module.exports=factory();
  else {root.AIFSCommunity=factory();if(root.__AIFS_SHARED_NOTES__)root.AIFSCommunity.mount(root);}
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  var MEDIA='.teaching-block[data-teaching-id],.reader-media[data-media-anchor]';
  var EXCLUDE='[data-community-ui],.notes-layer,.notes-stickies,.notes-toolbar,.notes-panel,.reader-popup,nav,script,style';
  function canonicalText(element){
    var clone=element.cloneNode(true);
    clone.querySelectorAll(EXCLUDE).forEach(function(n){n.remove();});
    clone.querySelectorAll('.katex').forEach(function(n){
      var tex=n.querySelector('annotation[encoding="application/x-tex"]');
      if(tex)n.replaceWith(clone.ownerDocument.createTextNode('$'+tex.textContent+'$'));
    });
    clone.querySelectorAll('button,input,select,textarea,canvas,svg').forEach(function(n){n.remove();});
    return clone.textContent.replace(/\s+/gu,' ').trim();
  }
  function clipped(value,n){return Array.from(value).slice(0,n).join('');}
  async function describeTargets(main,unit){
    var w=main.ownerDocument.defaultView,seen=new Map();
    var nodes=Array.from(main.querySelectorAll('p,li,blockquote,.katex-display,'+MEDIA)).filter(function(el){
      if(el.closest(EXCLUDE))return false;
      if(el.matches(MEDIA))return true;
      if(el.closest(MEDIA))return false;
      if(el.matches('li,blockquote')&&el.querySelector('p,li'))return false;
      return !!canonicalText(el);
    });
    return Promise.all(nodes.map(async function(el){
      var media=el.matches(MEDIA),pointer=el.closest('[data-reader-pointer]');
      var kind=media?'media':el.matches('.katex-display')?'formula':'text';
      var identity=media?(el.getAttribute('data-teaching-id')||el.getAttribute('data-media-anchor')):(pointer?pointer.getAttribute('data-reader-pointer'):'body');
      var quote=media?canonicalText(el.querySelector('.study-visual-title,.animation-title')||el.querySelector('h3,h2,figcaption')||el):canonicalText(el);
      if(!quote)quote='图像或动画';
      var base=JSON.stringify([unit,kind,identity,media?'':quote]);
      var occurrence=seen.get(base)||0;seen.set(base,occurrence+1);
      var bytes=new w.TextEncoder().encode(JSON.stringify([base,occurrence]));
      var digest=await w.crypto.subtle.digest('SHA-256',bytes);
      var anchor=Array.from(new Uint8Array(digest)).map(function(n){return n.toString(16).padStart(2,'0');}).join('');
      return {element:el,unit_id:unit,anchor:anchor,kind:kind,quote:clipped(quote,600)};
    }));
  }
  function revealTarget(main,element){
    var cue=element.closest('.teaching-block');
    if(cue&&cue.closest('.lesson-media-rail')){
      var marker=Array.from(main.querySelectorAll('[data-shared-media-target]')).find(function(n){return n.dataset.sharedMediaTarget===cue.dataset.teachingId;});
      if(marker){marker.querySelector('summary').click();element=marker;}
    }
    for(var n=element;n&&n!==main;n=n.parentElement){if(n.tagName==='DETAILS')n.open=true;}
    return element;
  }
  function mount(w){
    var d=w.document,cfg=w.__AIFS_SHARED_NOTES__;
    var schoolMode=!!(cfg&&cfg.identity_mode&&cfg.identity_mode!=='nickname');
    var identityState={ready:!schoolMode,canPublish:!schoolMode,name:null};
    if(!cfg||d.getElementById('community-panel'))return;
    function node(tag,cls,text){var el=d.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;}
    function button(cls,text){var el=node('button',cls,text);el.type='button';return el;}
    var state={unit:'',article:null,targets:[],active:null,epoch:0,serial:0,controller:null,counts:{},unitTotal:0,items:[],next:null,open:false,selecting:false,posting:false,drafts:new Map(),pending:new Map()};
    var trigger=button('community-open','同学感悟');trigger.id='community-open';trigger.dataset.communityUi='';trigger.setAttribute('aria-controls','community-panel');trigger.setAttribute('aria-expanded','false');
    var controls=d.querySelector('.book-controls')||d.querySelector('.reader-toolbar')||d.querySelector('.bookbar');
    function placeTrigger(){var floating=w.innerWidth<=700||!controls;var parent=floating?d.body:controls;if(trigger.parentNode!==parent)parent.appendChild(trigger);trigger.classList.toggle('community-floating',floating);}
    placeTrigger();w.addEventListener('resize',placeTrigger);
    var panel=node('aside','community-panel');panel.id='community-panel';panel.dataset.communityUi='';panel.hidden=true;panel.setAttribute('role','dialog');panel.setAttribute('aria-labelledby','community-heading');
    var header=node('header','community-header'),heading=node('h2','','同学感悟');heading.id='community-heading';
    var close=button('community-close','关闭');header.append(heading,close);panel.append(header);
    var tools=node('div','community-tools'),all=button('community-all','本节全部'),refresh=button('community-refresh','刷新');var choose=button('community-select','选位置');tools.append(all,choose,refresh);panel.append(tools);
    var selected=node('blockquote','community-selected');selected.hidden=true;panel.append(selected);
    var hint=node('p','community-hint','点“选位置”收起面板，再点正文旁的“写感悟”。');panel.append(hint);
    var status=node('p','community-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');panel.append(status);
    var identityNotice=node('p','community-identity');identityNotice.hidden=!schoolMode;panel.append(identityNotice);
    var loginLink=node('a','community-school-login','学校身份认证');loginLink.hidden=true;panel.append(loginLink);
    var form=node('form','community-form');form.hidden=true;
    var nameLabel=node('label','','昵称'),nickname=node('input');nickname.id='community-nickname';nickname.name='nickname';nickname.required=true;nickname.autocomplete='nickname';nameLabel.htmlFor=nickname.id;nameLabel.append(nickname);
    var textLabel=node('label','','你的感悟'),text=node('textarea');text.id='community-text';text.name='text';text.required=true;text.rows=4;text.placeholder='写下你的理解、疑问或补充…';textLabel.htmlFor=text.id;textLabel.append(text);
    var privacy=node('p','community-privacy','发布后同学们都能看到。昵称可重复；清除浏览器数据后，将无法删除自己之前发布的感悟。');
    if(cfg.identity_mode&&cfg.identity_mode!=='nickname')privacy.textContent='感悟对同学可见，作者身份由学校认证服务确认。';
    var submit=button('community-submit','发布感悟');submit.type='submit';submit.disabled=schoolMode;form.append(nameLabel,textLabel,privacy,submit);panel.append(form);
    var composer=null;
    if(cfg.image_upload_api&&w.AIFSCommunityImages){var imageHost=node('div','community-image-composer');form.insertBefore(imageHost,privacy);composer=w.AIFSCommunityImages.createComposer({window:w,host:imageHost,pasteTarget:form,config:cfg,getHeaders:function(){return headers(token(true));},notify:message,onChange:function(){text.required=!composer||composer.count()===0;}});text.placeholder='可以写说明，也可以只上传截图或手写照片。';}
    var list=node('div','community-list');panel.append(list);var more=button('community-more','加载更多');more.hidden=true;panel.append(more);d.body.append(panel);
    try{nickname.value=w.localStorage.getItem('aifs:community:nickname')||'';}catch(e){}
    function message(value,error){status.textContent=value||'';status.classList.toggle('is-error',!!error);}
    function token(create){
      if(cfg.identity_mode&&cfg.identity_mode!=='nickname')return '';
      try{
        var value=w.localStorage.getItem('aifs:community:author');
        if(value&&/^[a-f0-9]{64}$/.test(value))return value;
        if(!create)return '';
        var bytes=w.crypto.getRandomValues(new Uint8Array(32));value=Array.from(bytes).map(function(n){return n.toString(16).padStart(2,'0');}).join('');
        w.localStorage.setItem('aifs:community:author',value);
        if(w.localStorage.getItem('aifs:community:author')!==value)throw Error();
        return value;
      }catch(e){if(create)throw Error('浏览器无法保存发布身份，请允许网站保存数据后再发布。');return '';}
    }
    function headers(author){var h={'X-AIFS-Book-Revision':cfg.revision};if(cfg.practice_revision)h['X-AIFS-Practice-Revision']=cfg.practice_revision;if(author)h.Authorization='Bearer '+author;return h;}
    async function request(url,options){var response=await w.fetch(url,Object.assign({credentials:'same-origin'},options));var data=await response.json();if(!response.ok){var error=Error(data.error||'暂时无法连接，请重试。');error.status=response.status;throw error;}return data;}
    async function loadIdentity(){
      if(!schoolMode)return;
      identityState.ready=false;submit.disabled=true;loginLink.hidden=true;
      try{
        var profile=await request(cfg.identity_api||'/api/identity',{headers:headers('')});
        identityState={ready:true,canPublish:profile.can_publish===true,name:typeof profile.display_name==='string'?profile.display_name:null};
        nickname.readOnly=identityState.name!==null;if(identityState.name!==null)nickname.value=identityState.name;
        identityNotice.textContent=identityState.name!==null?'将以“'+identityState.name+'”公开发布。':identityState.canPublish?'学校身份已确认，可填写公开昵称。':'请先完成学校身份认证，再发布感悟。';
        if(!identityState.canPublish&&typeof profile.login_path==='string'&&profile.login_path.startsWith('/')){var login=new w.URL(profile.login_path,w.location.href);if(login.origin===w.location.origin){loginLink.href=login.href;loginLink.hidden=false;}}
      }catch(e){identityState={ready:false,canPublish:false,name:null};identityNotice.textContent='暂时无法确认学校身份，请刷新后重试。';}
      submit.disabled=state.posting||!identityState.ready||!identityState.canPublish;
    }
    function key(target){return target?target.unit_id+':'+target.anchor:'';}
    function saveDraft(){if(state.active)state.drafts.set(key(state.active),{text:text.value,nickname:nickname.value});}
    function updateMarkers(){
      state.targets.forEach(function(t){var n=state.counts[t.anchor]||0;t.marker.textContent=n?'感悟 '+n:'写感悟';t.marker.hidden=!state.open&&!state.selecting&&!n;t.marker.setAttribute('aria-label',(n?n+' 条感悟，':'写感悟，')+clipped(t.quote,45));});
      var count=state.unitTotal;trigger.textContent=state.selecting?'返回感悟':count?'同学感悟 · '+count:'同学感悟';
    }
    function open(){state.selecting=false;state.open=true;panel.hidden=false;trigger.setAttribute('aria-expanded','true');updateMarkers();}
    function hide(){saveDraft();state.selecting=false;state.open=false;panel.hidden=true;trigger.setAttribute('aria-expanded','false');updateMarkers();trigger.focus();}
    trigger.onclick=function(){if(state.open)hide();else open();};close.onclick=hide;
    choose.onclick=function(){saveDraft();state.selecting=true;state.open=false;panel.hidden=true;trigger.setAttribute('aria-expanded','false');updateMarkers();};
    panel.addEventListener('keydown',function(event){if(event.key==='Escape')hide();});
    function select(target){
      saveDraft();state.items=[];state.next=null;list.replaceChildren();more.hidden=true;state.active=target||null;selected.hidden=!target;selected.textContent=target?target.quote:'';if(target&&w.AIFSCommunityImages&&w.katex)w.AIFSCommunityImages.renderRecognition(selected,target.quote,w);form.hidden=!target;hint.hidden=!!target;
      heading.textContent=target?'这里的感悟':'同学感悟';
      if(composer)composer.setTarget(target);
      var draft=state.drafts.get(key(target));text.value=draft?draft.text:'';if(draft)nickname.value=draft.nickname;if(identityState.name!==null)nickname.value=identityState.name;
      open();load(false);
    }
    all.onclick=function(){select(null);};refresh.onclick=async function(){await loadIdentity();load(false);};more.onclick=function(){load(true);};
    var editDrafts=new Map();
    function currentUnit(unit){
      var main=d.getElementById('content'),route=w.location.hash.match(/^#\/([^/?#]+)/);
      return unit===state.unit&&(!route||route[1]===unit)&&
        (!d.documentElement.dataset.readerUnit||d.documentElement.dataset.readerUnit===unit)&&
        main&&main.contains(state.article);
    }
    function editControls(card,item){
      var draft=editDrafts.get(item.id);
      if(!draft){
        var edit=button('community-edit','编辑');
        edit.onclick=function(){editDrafts.set(item.id,{text:item.text,revision:item.revision});renderList();};
        card.append(edit);return;
      }
      var input=node('textarea','community-edit-text');
      input.setAttribute('aria-label','修改感悟');input.value=draft.text;
      input.oninput=function(){draft.text=input.value;};
      var save=button('community-edit-save','保存修改'),cancel=button('community-edit-cancel','取消编辑');
      var conflict=draft.revision!==item.revision||draft.conflict;
      save.disabled=!!conflict||!!draft.busy;cancel.disabled=!!draft.busy;
      if(conflict){
        card.append(node('p','','服务器上的内容已更新，请对照上方原文与编辑框中的草稿。'));
        if(item.revision!==draft.revision){
          var review=button('community-edit-review','已核对最新内容，继续编辑');
          review.onclick=function(){draft.revision=item.revision;draft.conflict=false;renderList();};
          card.append(review);
        }
      }
      cancel.onclick=function(){editDrafts.delete(item.id);renderList();};
      save.onclick=async function(){
        if(draft.busy||!currentUnit(item.unit_id))return;
        draft.text=input.value;
        var sent=draft.text,epoch=state.epoch;draft.busy=true;save.disabled=true;cancel.disabled=true;
        try{
          var data=await request(cfg.api+'/'+item.id,{method:'PATCH',headers:Object.assign(headers(token(false)),{'Content-Type':'application/json'}),
            body:JSON.stringify({revision:draft.revision,text:sent})});
          if(sent===draft.text)editDrafts.delete(item.id);else draft.revision=data.item.revision;
          if(state.epoch===epoch)await load(false);
        }catch(e){
          if(e.status===409){draft.conflict=true;if(state.epoch===epoch)await load(false);}
          if(state.epoch===epoch)message(e.message,true);
        }finally{draft.busy=false;if(state.epoch===epoch)renderList();}
      };
      card.append(input,save,cancel);
    }
    function renderList(){
      list.replaceChildren();
      if(!state.items.length)list.append(node('p','community-empty','还没有感悟，可以留下你的第一条理解。'));
      state.items.forEach(function(item){
        var card=node('article','community-note');card.dataset.noteId=String(item.id);
        var meta=node('div','community-note-meta'),author=node('strong','',item.nickname),date=node('time','',new Date(item.created_at*1000).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}));meta.append(author,date);card.append(meta);
        if(!state.active){
          var row=currentSource(item);
          var quote=row?button('community-quote','查看原文：'):node('p','community-quote','原文位置未核实：');
          var excerpt=node('span','community-quote-content');
          if(w.AIFSCommunityImages&&w.katex)w.AIFSCommunityImages.renderRecognition(excerpt,item.quote,w);
          else excerpt.textContent=item.quote;
          quote.append(excerpt);
          if(row)quote.onclick=function(){
            if(!currentUnit(item.unit_id)||!currentSource(item))return;
            var found=state.targets.find(function(t){return sameSource(t,item);});
            if(!found){
              if(!switchEdition(row,item))message('保存的原文位置暂时无法定位，请按引文核对。',true);
              return;
            }
            select(found);
            revealTarget(d.getElementById('content'),found.element).scrollIntoView({block:'center',behavior:'smooth'});
            found.element.classList.add('community-highlight');
            w.setTimeout(function(){found.element.classList.remove('community-highlight');},1800);
          };
          card.append(quote);
        }
        if(item.text)card.append(node('p','community-note-text',item.text));
        if(w.AIFSCommunityImages)w.AIFSCommunityImages.renderAttachments(card,item.images||[],w);
        if(item.is_mine){editControls(card,item);var remove=button('community-delete','删除');remove.onclick=async function(){
          if(!remove.dataset.confirm){remove.dataset.confirm='yes';remove.textContent='确认删除';return;}
          var epoch=state.epoch;remove.disabled=true;
          try{await request(cfg.api+'/'+item.id,{method:'DELETE',headers:headers(token(false))});if(epoch===state.epoch)load(false);}
          catch(e){if(epoch===state.epoch){message(e.message,true);remove.disabled=false;remove.textContent='重试删除';}}
        };card.append(remove);}
        list.append(card);
      });more.hidden=!state.next;
    }
    async function load(append){
      if(!state.unit)return;
      var epoch=state.epoch,serial=++state.serial;
      if(state.controller)state.controller.abort();state.controller=new w.AbortController();
      var query=new w.URLSearchParams({unit:state.unit});if(state.active)query.set('anchor',state.active.anchor);if(append&&state.next)query.set('before',String(state.next));
      refresh.disabled=true;more.disabled=true;message('正在读取感悟…');
      try{
        var data=await request(cfg.api+'?'+query.toString(),{headers:headers(token(false)),signal:state.controller.signal});
        if(epoch!==state.epoch||serial!==state.serial)return;
        state.counts=data.counts||{};
        state.unitTotal=data.unit_total===undefined?Object.values(state.counts).reduce(function(a,b){return a+b;},0):data.unit_total;
        state.items=append?state.items.concat(data.items):data.items;state.next=data.next_before;
        renderList();updateMarkers();message('已加载 '+data.total+' 条感悟');
      }catch(e){if(epoch===state.epoch&&serial===state.serial&&e.name!=='AbortError')message(e.message||'读取失败，请刷新重试。',true);}
      finally{if(epoch===state.epoch&&serial===state.serial){refresh.disabled=false;more.disabled=false;}}
    }
    form.addEventListener('submit',async function(event){
      event.preventDefault();if(state.posting||!state.active||!currentUnit(state.active.unit_id)||!state.active.element.isConnected)return;
      if(schoolMode&&(!identityState.ready||!identityState.canPublish)){message('请先确认学校身份后再发布。',true);return;}
      var target=state.active,unit=state.unit,ctx=key(target),body;
      try{
        var attachments=composer?composer.payload():[];
        var name=nickname.value.trim(),value=text.value.trim();if(!name||(!value&&!attachments.length))throw Error('请填写昵称，并写文字或添加图片。');
        if(Array.from(name).length>30||Array.from(value).length>2000)throw Error('昵称最多 30 字，感悟最多 2000 字。');
        var author=token(true);try{w.localStorage.setItem('aifs:community:nickname',name);}catch(e){}
        body={unit_id:unit,anchor:target.anchor,kind:target.kind,quote:target.quote,nickname:name,text:value,images:attachments};
        var fingerprint=JSON.stringify(body),prior=state.pending.get(ctx);
        body.request_id=prior&&prior.fingerprint===fingerprint?prior.id:Array.from(w.crypto.getRandomValues(new Uint8Array(16))).map(function(n){return n.toString(16).padStart(2,'0');}).join('');
        state.pending.set(ctx,{fingerprint:fingerprint,id:body.request_id});saveDraft();
        if(state.controller)state.controller.abort();state.serial++;refresh.disabled=false;more.disabled=false;
        state.posting=true;submit.disabled=true;if(composer)composer.setPublishing(ctx,true);message('正在发布…');
        await request(cfg.api,{method:'POST',headers:Object.assign(headers(author),{'Content-Type':'application/json'}),body:JSON.stringify(body)});
        state.pending.delete(ctx);if(composer)composer.published(ctx,attachments);
        var saved=state.drafts.get(ctx);
        if(saved&&saved.text.trim()===body.text&&saved.nickname.trim()===body.nickname)state.drafts.delete(ctx);
        if(state.unit===unit){
          if(key(state.active)===ctx){if(text.value.trim()===body.text&&nickname.value.trim()===body.nickname)text.value='';else saveDraft();}
          await load(false);
        }
      }catch(e){if(schoolMode&&e.status===401)await loadIdentity();if(key(state.active)===ctx)message(e.message||'发布失败，文字已保留，请重试。',true);}
      finally{state.posting=false;if(composer)composer.setPublishing(ctx,false);submit.disabled=schoolMode&&(!identityState.ready||!identityState.canPublish);}
    });
    var registry=[], scheduled=false;
    var locatorAttrs=['data-shared-anchor','data-teaching-id','data-textbook-result','data-textbook-supplement','data-practice-id','data-practice-media-id'];
    var locatorSelector=locatorAttrs.map(function(attr){return '['+attr+']';}).join(',');
    function sameSource(row,item){
      return item.unit_id===state.unit&&row.anchor===item.anchor&&row.kind===item.kind&&row.quote===item.quote;
    }
    function currentSource(item){
      return (cfg.targets_api?registry:state.targets).find(function(row){return sameSource(row,item);});
    }
    function switchEdition(row,item){
      var url=new w.URL(w.location.href),edition=url.searchParams.get('practice')||'basic';
      if(!row||!row.editions||row.editions.includes(edition))return false;
      url.searchParams.set('practice',row.editions[0]);url.searchParams.set('note_target',item.anchor);
      url.searchParams.set('note_unit',item.unit_id);url.searchParams.set('note_kind',item.kind);url.searchParams.set('note_quote',item.quote);
      w.location.assign(url.href);return true;
    }
    function markTarget(target,epoch){
      var mark=button('community-note-mark','写感悟');mark.dataset.communityUi='';mark.dataset.anchor=target.anchor;target.marker=mark;
      mark.onclick=function(event){event.preventDefault();event.stopPropagation();if(epoch===state.epoch&&currentUnit(target.unit_id))select(target);};
      if(target.element.tagName==='DETAILS')target.element.querySelector('summary').after(mark);
      else if(target.kind==='formula')target.element.after(mark);else target.element.append(mark);
    }
    function bindRegistry(main){
      var nodes=new Map(),previous=new Map(state.targets.map(function(t){return [t.anchor,t];}));
      main.querySelectorAll(locatorSelector).forEach(function(el){
        locatorAttrs.forEach(function(attr){if(el.hasAttribute(attr))nodes.set(attr+':'+el.getAttribute(attr),el);});
      });
      state.targets=[];
      registry.forEach(function(row){
        var el=nodes.get(row.locator.attribute+':'+row.locator.value);if(!el)return;
        var target=previous.get(row.anchor);
        if(!target||el!==target.element||!target.marker.isConnected){
          target=Object.assign({unit_id:state.unit,element:el},row);markTarget(target,state.epoch);
        }
        if(state.active&&state.active.anchor===target.anchor)state.active=target;
        state.targets.push(target);
      });
      var url=new w.URL(w.location.href),anchor=url.searchParams.get('note_target');
      var item={anchor:anchor,unit_id:url.searchParams.get('note_unit'),kind:url.searchParams.get('note_kind'),quote:url.searchParams.get('note_quote')};
      var landing=anchor&&state.targets.find(function(t){return sameSource(t,item);});
      if(landing){
        ['note_target','note_unit','note_kind','note_quote'].forEach(function(key){url.searchParams.delete(key);});w.history.replaceState(null,'',url.href);
        select(landing);revealTarget(main,landing.element).scrollIntoView({block:'center'});
      }
      updateMarkers();
    }

    async function rebuild(){
      scheduled=false;var main=d.getElementById('content');if(!main)return;
      var unit=d.documentElement.dataset.readerUnit||(w.location.hash.match(/^#\/([\w-]+)/)||[])[1]||((w.__DATA__||{}).scope||[])[0];
      if(/^#\/materials\//.test(w.location.hash)||!unit||!((w.__DATA__||{}).units||{})[unit]){
        saveDraft();state.unit='';state.article=null;state.epoch++;state.serial++;
        if(state.controller)state.controller.abort();state.active=null;state.selecting=false;state.targets=[];state.counts={};state.unitTotal=0;state.items=[];state.next=null;
        main.querySelectorAll('.community-note-mark').forEach(function(n){n.remove();});
        if(composer)composer.setTarget(null);
        selected.textContent='';selected.hidden=true;form.hidden=true;hint.hidden=true;list.replaceChildren();more.hidden=true;choose.disabled=true;refresh.disabled=true;all.disabled=true;
        d.documentElement.dataset.communityUnit='';updateMarkers();message('当前是资料页，请回到教学单元查看或发布感悟。');return;
      }
      choose.disabled=false;refresh.disabled=false;all.disabled=false;
      var article=main.querySelector('article')||main.firstElementChild;
      if(unit===state.unit&&article===state.article){if(registry.length)bindRegistry(main);return;}
      registry=[];
      saveDraft();state.unit=unit;state.article=article;state.epoch++;state.serial++;if(state.controller)state.controller.abort();
      var epoch=state.epoch;if(composer)composer.setTarget(null);state.active=null;state.targets=[];state.items=[];state.counts={};state.unitTotal=0;state.next=null;selected.hidden=true;form.hidden=true;hint.hidden=false;text.value='';heading.textContent='同学感悟';
      main.querySelectorAll('.community-note-mark').forEach(function(n){n.remove();});list.replaceChildren();updateMarkers();
      try{
        if(cfg.targets_api){
          var result=await request(cfg.targets_api+'?unit='+encodeURIComponent(unit),{headers:headers(token(false))});
          if(epoch!==state.epoch)return;
          registry=result.targets;bindRegistry(main);
        }else{
          var targets=await describeTargets(main,unit);if(epoch!==state.epoch)return;
          state.targets=targets;targets.forEach(function(t){markTarget(t,epoch);});
        }
        d.documentElement.dataset.communityUnit=unit;updateMarkers();load(false);
      }catch(e){message('该浏览器暂时无法定位教材内容，请使用新版浏览器重试。',true);}
    }
    function schedule(){if(!scheduled){scheduled=true;w.setTimeout(rebuild,20);}}
    w.addEventListener('hashchange',schedule);
    var observer=new w.MutationObserver(function(records){
      if(records.some(function(r){
        if(r.type==='attributes'||r.target===main)return true;
        return Array.from(r.addedNodes).some(function(n){return n.nodeType===1&&(n.matches(locatorSelector)||n.querySelector(locatorSelector));});
      }))schedule();
    });observer.observe(d.documentElement,{attributes:true,attributeFilter:['data-reader-unit','data-reader-ready']});
    var main=d.getElementById('content');if(main)observer.observe(main,{childList:true,subtree:true});
    loadIdentity();
    if(d.readyState==='loading')d.addEventListener('DOMContentLoaded',schedule,{once:true});else schedule();
  }
  return {revealTarget:revealTarget,canonicalText:canonicalText,describeTargets:describeTargets,mount:mount};
});
