/* The reader's one whole-field Marked lexer, shared by Node and the browser.
 * All public source positions are Unicode code points in the approved raw field.
 * Token rendering never lexes slices and retains the full link-definition context. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.DSHReaderMarkdown = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  function escapeHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
  function cpLength(s) { return Array.from(s).length; }
  function projectedMap(markdown, field) {
    var raw = field.raw_text === undefined ? markdown : field.raw_text;
    var rawChars = Array.from(raw), output = '', map = [0], cursor = 0;
    function append(text, start, end) {
      var chars = Array.from(text);
      output += text;
      chars.forEach(function (_, i) { map.push(end === undefined ? start+i+1 : i===chars.length-1 ? end : start); });
    }
    (field.marker_spans || []).forEach(function (span) {
      var before = rawChars.slice(cursor,span.raw_start).join('');
      // The frozen visible-source map records the existing concept-name merge.
      if (span.visible_start > 0 && field.source_map) {
        var prior = field.source_map[span.visible_start-1];
        if (prior >= cursor && prior < span.raw_start) before = rawChars.slice(cursor,prior+1).join('');
      } else if (span.visible_start === 0) before = '';
      append(before,cursor);
      var label = Array.from(field.visible_text).slice(span.visible_start,span.visible_end).join('');
      var prefix = '['+label.replace(/\[/g,'\\[').replace(/\]/g,'\\]')+'](#reader-';
      var tail = markdown.slice(output.length);
      if (tail.slice(0,prefix.length)!==prefix) throw new Error('Reader reference projection differs from frozen document');
      var reference = tail.slice(0,prefix.length+21);
      if (!/^\[.*\]\(#reader-[a-f0-9]{20}\)$/s.test(reference)) throw new Error('Invalid frozen reader reference');
      append(reference,span.raw_start,span.raw_end); cursor=span.raw_end;
    });
    append(rawChars.slice(cursor).join(''),cursor);
    if (output!==markdown) throw new Error('Reader markdown/source projection mismatch');
    return map;
  }
  function normalize(markdown, sourceMap) {
    var chars=Array.from(markdown), out='', map=[sourceMap[0]];
    for (var i=0;i<chars.length;i++) {
      if(chars[i]==='\r') { if(chars[i+1]==='\n') i++; out+='\n'; }
      else out+=chars[i];
      map.push(sourceMap[i+1]);
    }
    return {text:out,map:map};
  }
  function protectMath(text, map) {
    var ranges=[], offset=0, fence=null, listIndent=null;
    text.split(/(?<=\n)/).forEach(function(line) {
      var content=line.replace(/^(?: {0,3}> ?)+/,''), list=/^( *)(?:[-+*]|\d+[.)]) +/.exec(content);
      if(list) listIndent=listIndent===null?list[0].length:Math.min(listIndent,list[0].length);
      else if(/^\S/.test(content)) listIndent=null;
      var f=/^\s*(`{3,}|~{3,})/.exec(content);
      if (fence) { ranges.push([offset,offset+line.length]); if(f && f[1][0]===fence[0] && f[1].length>=fence.length) fence=null; }
      else if(f) { fence=f[1]; ranges.push([offset,offset+line.length]); }
      else if (!list && /^(?: {4}|\t)/.test(content) && (listIndent===null || /^ */.exec(content)[0].length>=listIndent+4)) ranges.push([offset,offset+line.length]);
      offset+=line.length;
    });
    var code=/(`+)([\s\S]*?)\1/g,m;
    while((m=code.exec(text))) ranges.push([m.index,m.index+m[0].length]);
    function inCode(i) { return ranges.some(function(r){return r[0]<=i&&i<r[1];}); }
    var spans=[];
    for(var i=0;i<text.length;) {
      if(inCode(i)) { i++;continue; }
      var open='',close='';
      if(text.slice(i,i+2)==='\\(') {open='\\(';close='\\)';}
      else if(text.slice(i,i+2)==='\\[') {open='\\[';close='\\]';}
      else if(text[i]==='$' && text[i-1]!=='\\') {open=text[i+1]==='$'?'$$':'$';close=open;}
      else {i++;continue;}
      var end=text.indexOf(close,i+open.length);
      if(end<0 || inCode(end)) { i+=open.length;continue; }
      spans.push({start:i,end:end+close.length,raw:text.slice(i,end+close.length)});i=end+close.length;
    }
    var prefix='\uE000READERMATH'; while(text.indexOf(prefix)>=0) prefix+='X';
    var out='', result=[map[0]], cursor=0, math=[];
    function append(start,end) {
      var from=cpLength(text.slice(0,start)), chars=Array.from(text.slice(start,end));out+=chars.join('');
      chars.forEach(function(_,n){result.push(map[from+n+1]);});
    }
    spans.forEach(function(span,index){
      append(cursor,span.start);var slot=prefix+index+'\uE001';out+=slot;
      var a=map[cpLength(text.slice(0,span.start))],b=map[cpLength(text.slice(0,span.end))];
      Array.from(slot).forEach(function(_,n){result.push(n===slot.length-1?b:a);});
      math.push({slot:slot,raw:span.raw});cursor=span.end;
    });
    append(cursor,text.length);return {text:out,map:result,math:math};
  }
  function parseReaderField(markdown, options) {
    options=options||{};var marked=options.marked;
    if(!marked || !marked.lexer) throw new Error('Local Marked lexer required');
    var field=options.field||{}, source=projectedMap(markdown,field);
    var normal=normalize(markdown,source), prepared=protectMath(normal.text,normal.map);
    var tokens=marked.lexer(prepared.text,Object.assign({},marked.getDefaults(),{mangle:false,headerIds:false})), blocks=[],cursor=0;
    tokens.forEach(function(token,index){
      var start=prepared.text.indexOf(token.raw,cursor);
      if(start<0) throw new Error('Marked token cannot be mapped to approved source');
      var end=start+token.raw.length;cursor=end;
      if(token.type==='space') return;
      blocks.push({block_id:(field.block_id||'field')+':'+index,field_id:field.block_id||'field',
        target_pointer:field.json_pointer||'',owner_id:field.owner_id||'',token_index:index,token_type:token.type,
        source_start_cp:prepared.map[cpLength(prepared.text.slice(0,start))],source_end_cp:prepared.map[cpLength(prepared.text.slice(0,end))]});
    });
    return {tokens:tokens,links:tokens.links||{},sourceMap:prepared.map,blocks:blocks,math:prepared.math,marked:marked,field:field};
  }
  // Only an explicit numbered heading/definition in this same field can supply
  // a target. Duplicate numbers, code, math and existing links are never guessed.
  var numberedLabel = '(?:性质|定理|定义|引理|推论|命题|判据)\\s*(?:[0-9]+(?:[.．][0-9]+)*|[一二三四五六七八九十百零〇]+)';
  function numberedDefinition(token) {
    if (!/^(?:paragraph|heading|text)$/.test(token.type) || !token.tokens) return '';
    var first=token.tokens[0], text=token.text||'', explicit=token.type==='heading';
    if (first && first.type==='strong') { text=first.text; explicit=true; }
    var match=new RegExp('^('+numberedLabel+')(?=$|[\\s（(:：.、])').exec(text);
    if (!match || (!explicit && !/^[（(:：]/.test(text.slice(match[0].length)))) return '';
    return match[1].replace(/\s/g,'').replace(/．/g,'.');
  }
  function numberedReferences(parsed) {
    var starts={}, ends={}, byLabel={}, field=parsed.field||{};
    var identity=String(field.block_id||'').match(/[a-f0-9]{20,}/) || String(field.anchor||'').match(/[a-f0-9]{20}/);
    if (!identity) return {starts:starts,ends:ends,byLabel:byLabel};
    function nestedDefinitions(token) {
      if (/^(?:link|image|code|codespan|html)$/.test(token.type)) return;
      (token.tokens||token.items||[]).forEach(function(child) {
        var label=numberedDefinition(child);
        if (label) (byLabel[label]||(byLabel[label]=[])).push(null);
        nestedDefinitions(child);
      });
    }
    parsed.blocks.forEach(function(block, index) {
      var token=parsed.tokens[block.token_index];
      nestedDefinitions(token);
      var label=numberedDefinition(token), end=index, proof=false;
      if (!label) return;
      while (end+1<parsed.blocks.length) {
        var next=parsed.tokens[parsed.blocks[end+1].token_index];
        if (numberedDefinition(next) || next.type==='heading') break;
        var nextText=(next.text||'').replace(/\s/g,'').replace(/．/g,'.');
        if (nextText.indexOf(label)>=0) break; // a reuse paragraph is outside the definition
        if (token.type!=='heading' && !proof) {
          var previous=parsed.tokens[parsed.blocks[end].token_index];
          // A definition that explicitly introduces a list owns those
          // conditions as well. A popup must not omit half of a definition.
          if(/^定义/.test(label)&&/[:：]\s*$/.test(previous.text||'')&&/^(?:list|table)$/.test(next.type)) {end++;continue;}
          if (!/^(?:证明|证[:：]|再证|先证|推导[:：])/.test(nextText)) break;
          proof=true;
        }
        end++;
      }
      var target={label:label,start:index,end:end,
        anchor:'reader-'+identity[0].slice(0,12)+block.source_start_cp.toString(16).padStart(8,'0')};
      (byLabel[label]||(byLabel[label]=[])).push(target);
    });
    Object.keys(byLabel).forEach(function(label) {
      if (byLabel[label].length!==1 || !byLabel[label][0]) { delete byLabel[label];return; }
      var target=byLabel[label][0];byLabel[label]=target;
      starts[target.start]=target;(ends[target.end]||(ends[target.end]=[])).push(target);
    });
    return {starts:starts,ends:ends,byLabel:byLabel};
  }
  function linkNumberedTokens(tokens, references, blockIndex) {
    var out=[];
    (tokens||[]).forEach(function(original) {
      var token=Object.assign({},original);
      if (/^(?:link|image|code|codespan|html)$/.test(token.type)) {out.push(token);return;}
      if (token.tokens) token.tokens=linkNumberedTokens(token.tokens,references,blockIndex);
      if (token.items) token.items=linkNumberedTokens(token.items,references,blockIndex);
      if (token.header) token.header=linkNumberedTokens(token.header,references,blockIndex);
      if (token.rows) token.rows=token.rows.map(function(row){return linkNumberedTokens(row,references,blockIndex);});
      if (token.type!=='text' || token.tokens) {out.push(token);return;}
      var pattern=new RegExp(numberedLabel,'g'), cursor=0, match;
      while ((match=pattern.exec(token.text))) {
        var target=references.byLabel[match[0].replace(/\s/g,'').replace(/．/g,'.')];
        if (!target || target.start===blockIndex) continue;
        if (match.index>cursor) out.push({type:'text',raw:token.text.slice(cursor,match.index),text:token.text.slice(cursor,match.index)});
        out.push({type:'link',raw:match[0],text:match[0],href:'#'+target.anchor,title:null,
          tokens:[{type:'text',raw:match[0],text:match[0]}]});
        cursor=match.index+match[0].length;
      }
      if (cursor<token.text.length) out.push(Object.assign({},token,{raw:token.text.slice(cursor),text:token.text.slice(cursor)}));
      else if (!cursor) out.push(token);
    });
    return out;
  }
  // Plan R3: bare TeX (e^{1+i}, f'(z_0)=0, w=\sqrt[n]{z}) in link text and in
  // short label fields renders as math. Same rule as render-core maskBareMath:
  // a _ ^ or \ marker, no ordinary English word, known commands, balanced brackets.
  var BARE_TOKEN = "A-Za-z0-9\\\\_^{}\\[\\]=+\\-*\\/().,'|<>≠≤≥±×−·→↦⇒⇔≈≡∞∂∫∮∑∏√∈′″α-ωΑ-Ωϑϕϵ";
  var BARE_FUNCTIONS = /(^|[^\\A-Za-z])(arcsin|arccos|arctan|sinh|cosh|tanh|sin|cos|tan|cot|exp|log|ln|arg|Arg|Ln|Re|Im|lim|max|min)(?![A-Za-z])/g;
  var BARE_MATH = new RegExp("[" + BARE_TOKEN + "]+(?:[ \t]+[" + BARE_TOKEN + "]+)*", "g");
  var BARE_COMMANDS = /^(?:arcsin|arccos|arctan|geqslant|leqslant|ll|gg|circ|angle|perp|parallel|forall|exists|setminus|emptyset|varnothing|Leftrightarrow|leftrightarrow|iff|longrightarrow|mid|vert|langle|rangle|ell|bigl|bigr|Bigl|Bigr|displaystyle|boldsymbol|mathbf|mathcal|widehat|widetilde|underline|binom|neg|cot|sec|csc|max|min|sup|inf|det|deg|bar|overline|hat|tilde|vec|frac|dfrac|tfrac|sqrt|cdot|cdots|ldots|dots|times|div|pm|mp|ne|neq|le|leq|ge|geq|approx|equiv|sim|to|mapsto|rightarrow|in|left|right|quad|mathrm|mathbb|operatorname|ln|log|exp|sin|cos|tan|sinh|cosh|tanh|arg|Arg|Ln|Re|Im|lim|sum|prod|int|oint|partial|nabla|prime|alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|phi|varphi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Phi|Psi|Omega|infty)$/;
  var LABEL_FIELD = /\/(?:label|title|name)$/;
  function bareMathExpression(raw) {
    var trail = raw.match(/[.,\s]*$/)[0], expr = raw.slice(0, raw.length - trail.length);
    if (!/[_^\\=<>≠≤≥∞∂∫∮∑∏√∈α-ωΑ-Ω]/.test(expr) && !/^[A-Za-z](?:\d+)?$/.test(expr)) return null;
    var known = true;
    var letters = expr.replace(/\\(?:mathrm|operatorname|text)\{[^{}]*\}/g, '')
      .replace(/\\([A-Za-z]+)/g, function (_all, command) { if (!BARE_COMMANDS.test(command)) known = false; return ''; });
    // Function names may be written without a backslash (cos4φ) and short
    // letter runs are implicit products (nz^{n−1}); four or more letters are prose.
    if (!known || /[A-Za-z]{4,}/.test(letters.replace(BARE_FUNCTIONS, '$1'))) return null;
    expr = expr.replace(BARE_FUNCTIONS, '$1\\$2');
    var depth = 0, square = 0;
    for (var i = 0; i < expr.length; i++) {
      var ch = expr.charAt(i);
      if (ch === '{') depth++; else if (ch === '}') depth--; else if (ch === '[') square++; else if (ch === ']') square--;
      if (depth < 0 || square < 0) return null;
    }
    return depth || square ? null : {expr: expr, trail: trail};
  }
  function bareMathTokens(tokens, wrapAll, math, prefix) {
    return (tokens || []).map(function (original) {
      var token = Object.assign({}, original);
      if (/^(?:code|codespan|html|image)$/.test(token.type)) return token;
      var inside = wrapAll || token.type === 'link';
      if (token.tokens) token.tokens = bareMathTokens(token.tokens, inside, math, prefix);
      if (token.items) token.items = bareMathTokens(token.items, inside, math, prefix);
      if (token.header) token.header = bareMathTokens(token.header, inside, math, prefix);
      if (token.rows) token.rows = token.rows.map(function(row){return bareMathTokens(row,inside,math,prefix);});
      if (token.type !== 'text' || token.tokens || !wrapAll) return token;
      var entities={'amp':'&','lt':'<','gt':'>','quot':'"','#39':"'"};
      var changed = false, decoded=token.raw.replace(/&(amp|lt|gt|quot|#39);/g,function(_,key){return entities[key];}), raw = decoded.replace(BARE_MATH, function (part) {
        var found = bareMathExpression(part);
        if (!found) return part;
        changed = true;
        var slot = prefix + math.length + '\uE001';
        math.push({slot: slot, raw: '$' + found.expr + '$'});
        return slot + found.trail;
      });
      return changed ? Object.assign(token, {raw: raw, text: escapeHtml(raw)}) : token;
    });
  }
  function renderReaderField(parsed, mounts, renderMount, options) {
    options=options||{};var marked=parsed.marked,renderer=new marked.Renderer();
    var codeRenderer=renderer.code,codespanRenderer=renderer.codespan;
    function restoreCode(text) { parsed.math.forEach(function(item){text=text.split(item.slot).join(item.raw);});return text; }
    renderer.code=function(text,language,escaped){return codeRenderer.call(this,restoreCode(text),language,escaped);};
    renderer.codespan=function(text){return codespanRenderer.call(this,restoreCode(text));};
    renderer.html=function(raw){return escapeHtml(raw);};
    renderer.link=function(href,title,text){return /^(?:https?:\/\/|#reader-[a-f0-9]{20}$)/i.test(href)?'<a href="'+escapeHtml(href)+'">'+text+'</a>':text+' ('+escapeHtml(href)+')';};
    renderer.image=function(href,title,text){return escapeHtml(text)+' ('+escapeHtml(href)+')';};
    function renderMath(item) {
      // Legacy authors sometimes put a reader reference inside a formula.
      // Keep its label in the mathematical expression (it may be a variable),
      // and expose the original target beside it without trusting TeX HTML.
      var links=[], raw=item.raw.replace(/\[((?:\\[\s\S]|[^\]\\])*)\]\(#(reader-[a-f0-9]{20})\)/g,function(_,label,anchor){
        label=label.replace(/\\([\[\]])/g,'$1');
        links.push({label:label,anchor:anchor});
        return label;
      });
      var html=options.mathHtml?options.mathHtml(raw):escapeHtml(raw);
      if (!links.length) return html;
      return html+'<span class="reader-math-references">'+links.map(function(link){
        var label=options.mathHtml?options.mathHtml('$'+link.label+'$'):escapeHtml(link.label);
        return '<a href="#'+link.anchor+'">'+label+'</a>';
      }).join('、')+'</span>';
    }
    var opts=Object.assign({},marked.getDefaults(),{renderer:renderer,mangle:false,headerIds:false}),out=[],consumed=new Set();
    function at(block,edge) { (mounts||[]).filter(function(m){return m.resolved_block_id===block.block_id&&m.edge===edge;}).sort(function(a,b){return a.order_at_anchor-b.order_at_anchor;}).forEach(function(m){if(consumed.has(m.mount_id)) throw new Error('Duplicate reader mount '+m.mount_id);consumed.add(m.mount_id);out.push(renderMount(m));}); }
    var references=numberedReferences(parsed);
    var bare=[], barePrefix='\uE000READERBARE', labelField=LABEL_FIELD.test((parsed.field||{}).json_pointer||'');
    var activeRole='';
    function roleTag(role){return role===options.disclosureRole?'div':'details';}
    function closeRole(){out.push('</'+roleTag(activeRole)+'>');}
    parsed.blocks.forEach(function(block,index){
      var role=(options.teachingRoles||{})[block.block_id]||'main';
      var target=references.starts[index];
      if(target && activeRole && activeRole!=='main'){closeRole();activeRole='';}
      if (target) out.push('<section class="reader-numbered-reference" data-reader-reference="'+escapeHtml(target.label)+'" id="'+target.anchor+'">');
      if(role!==activeRole){
        if(activeRole && activeRole!=='main')closeRole();
        if(role!=='main') {
          // The containing structured example owns this same semantic role.
          // Keep the role boundary and its mounts, without a second disclosure.
          var inherited=role===options.disclosureRole;
          out.push('<'+roleTag(role)+' class="'+(inherited?'reader-teaching-region':'reader-teaching-disclosure')+'" data-teaching-role="'+escapeHtml(role)+'">'+(inherited?'':'<summary>'+(role==='proof'?'展开证明':'展开例题')+'</summary>'));
        }
        activeRole=role;
      }
      at(block,'before');
      var token=options.bareMath===false?linkNumberedTokens([parsed.tokens[block.token_index]],references,index)[0]:bareMathTokens(linkNumberedTokens([parsed.tokens[block.token_index]],references,index),true,bare,barePrefix)[0], group=[token];group.links=parsed.links;
      var html=options.inline&&token.tokens ? marked.Parser.parseInline(token.tokens,opts) : marked.parser(group,opts);
      parsed.math.concat(bare).forEach(function(item){html=html.split(item.slot).join(renderMath(item));});
      if(role==='main' && Object.prototype.hasOwnProperty.call(options.teachingRoles||{},block.block_id))
        html=html.replace(/^(\s*<[A-Za-z][A-Za-z0-9-]*)/, '$1 data-teaching-main=""');
      if (!options.inline && block.block_id)
        html=html.replace(/^(\s*<[A-Za-z][A-Za-z0-9-]*)/, '$1 data-shared-anchor="'+escapeHtml(block.block_id)+
          '" data-shared-start="'+block.source_start_cp+'" data-shared-end="'+block.source_end_cp+'"');
      out.push(html);
      // Optional regions own their media, including the last proof paragraph.
      // Ordinary after slots stay outside the numbered reference boundary so
      // opening a definition window does not also clone adjacent media.
      var optionalAfter=activeRole && activeRole!=='main';
      if(optionalAfter)at(block,'after');
      if((references.ends[index]||[]).length && activeRole && activeRole!=='main'){closeRole();activeRole='';}
      (references.ends[index]||[]).forEach(function(){out.push('</section>');});
      if(!optionalAfter)at(block,'after');
    });
    if(activeRole && activeRole!=='main')closeRole();
    if(consumed.size!==(mounts||[]).length) throw new Error('Reader mount has no matching syntax block');
    return out.join('');
  }
  return {parseReaderField:parseReaderField,renderReaderField:renderReaderField};
});
