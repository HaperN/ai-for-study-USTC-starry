const test=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const core=require('../../blocks/render/assets/render-core.js');
const layout=require('../../blocks/render/assets/reader-layout.js');
const parser=require('../../blocks/render/assets/reader-markdown.js');
const marked=require('../../blocks/render/vendor/marked.min.js');
const katex=require('../../blocks/render/vendor/katex.min.js');
function block(group,pointer,raw,owner='U') {return {group,json_pointer:pointer,raw_text:raw,markdown:raw,owner_id:owner,anchor:'reader-12345678901234567890',block_id:'sha256:'+require('node:crypto').createHash('sha256').update(pointer).digest('hex'),field_label:group,collapsed:false};}
function render(t,raw,extra=[]) {
 const b=block('body_markdown','/body_markdown',raw),parsed=parser.parseReaderField(raw,{marked,field:b});
 const proof=parsed.blocks.find(x=>parsed.tokens[x.token_index].text?.includes('从条件'));
 const mount=proof?{mount_id:'M',anchor_id:'A',target_pointer:'/body_markdown',resolved_block_id:proof.block_id,edge:'after',order_at_anchor:0,source_id:'media',source_kind:'media',unit_id:'U',source_hash:'f',owner_id:'U'}:null;
 const unit={title:'任意数学单元',reader_document:{schema_version:'ReaderDocumentV1',blocks:[b,...extra]},reader_layout:{schema_version:'ReaderLayoutV1',mounts:mount?[mount]:[]}};
 const renderer=core.createRenderer({marked,katex});renderer.beginUnit({unitId:'U'});
 const before=JSON.stringify(unit),dom=new JSDOM('<main>'+renderer.readerDocumentHtml(unit.reader_document,'body',[],unit.reader_layout)+'</main>');t.after(()=>dom.window.close());
 const root=dom.window.document.querySelector('main'),nodes=[...root.querySelectorAll('[data-reader-block],.katex,[data-media-anchor]')];
 layout.enhance(root,unit,'U');assert.equal(JSON.stringify(unit),before);nodes.forEach(n=>assert.ok(root.contains(n),'every source, formula and media node survives'));
 return root;
}
function closed(node){return !!node?.closest('details:not([open])');}
test('inline proofs fold at semantic boundaries while conditions and adjacent explanations remain visible',t=>{
 const root=render(t,'## 性质\n\n**定理1**：条件 $z\\ne0$ 时结论成立。\n\n直观解释：这是旋转。\n\n证明：从条件 $z\\ne0$ 开始。\n\n$$w=z/z=1$$\n\n故命题得证。\n\n直观解释：结果表示单位长度。\n\n**邻域**：点的周围。\n\n相邻概念仍可读。');
 const ps=[...root.querySelectorAll('p')],find=text=>ps.find(p=>p.textContent.includes(text));
 assert.equal(closed(find('从条件')),true);assert.equal(closed(root.querySelector('.katex-display')),true);assert.equal(closed(root.querySelector('[data-media-anchor]')),true);
 for(const text of ['条件','这是旋转','单位长度','点的周围','相邻概念'])assert.equal(closed(find(text)),false,text);
 const proof=root.querySelector('details.reader-theorem-proof');assert.ok(proof);assert.equal(proof.open,false);proof.querySelector('summary').click();assert.equal(proof.open,true);assert.match(proof.textContent,/从条件.*w=z\/z=1.*得证/s);
});
test('proof text inside the theorem paragraph folds without hiding its statement or swallowing later prose',t=>{
 const root=render(t,'**性质2**：条件成立则 $a=a$。证明：使用自反性，所以成立。证毕。直观解释：同一对象。\n\n## 后续定义\n\n条件和解释继续可读。');
 const proof=root.querySelector('details.reader-theorem-proof');assert.ok(proof);assert.match(proof.textContent,/使用自反性/);assert.doesNotMatch(proof.textContent,/同一对象|条件和解释/);
 assert.equal(closed([...root.querySelectorAll('p')].find(p=>p.textContent.includes('条件成立'))),false);
 assert.match(root.textContent,/同一对象/);
});
test('whole body examples hide the statement, method, steps and answer behind a title',t=>{
 const root=render(t,'## 概念\n\n可见解释。\n\n### 例3：求复数的模\n\n题干 $z=3+4i$。\n\n思路：转成距离。\n\n第一步，平方相加。\n\n答案：$5$。\n\n## 新概念\n\n新概念解释可读。');
 const example=root.querySelector('details.reader-example-disclosure');assert.ok(example);assert.equal(example.open,false);assert.equal(example.querySelector('summary').textContent,'例3：求复数的模');
 assert.match(example.textContent,/题干.*转成距离.*平方相加.*答案/s);assert.doesNotMatch(example.textContent,/新概念/);
 assert.equal(closed([...root.querySelectorAll('p')].find(p=>p.textContent.includes('新概念解释'))),false);
 example.querySelector('summary').click();assert.equal(example.open,true);
});
test('structured legacy examples default closed even when old blocks say expanded',t=>{
 const fields=['label','statement','approach','steps/0/markdown','conclusion'].map((p,i)=>block('examples','/examples/0/'+p,['例8','题目内容','方法内容','步骤内容','答案内容'][i],'E'));
 const root=render(t,'正文继续。',fields),ex=root.querySelector('[data-reader-group="examples"]');assert.equal(ex.tagName,'DETAILS');assert.equal(ex.open,false);assert.match(ex.querySelector('summary').textContent,/例8/);
 for(const p of ['statement','approach','steps/0/markdown','conclusion'])assert.equal(closed(ex.querySelector('[data-reader-pointer="/examples/0/'+p+'"]')),true);
});
test('literal proof words in code or ordinary explanatory sentences do not become disclosures',t=>{
 const root=render(t,'## 含义\n\n证明的作用是说明结论为何成立。\n\n```text\n证明：只是代码示例。\n```\n\n直观解释中用到“证明”这个词。');
 assert.equal(root.querySelectorAll('.reader-theorem-proof').length,0);assert.match(root.querySelector('code').textContent,/证明/);
});
test('projected theorem cores are visible and their complete proof is closed even without a body marker',t=>{
 const fields=['name','what','why','markdown','steps/0/markdown'].map((p,i)=>({...block('explainables','/explainables/0/'+p,['定理9','条件与结论','直观意义','完整证明','推导步骤'][i],'T'),collapsed:true,disclosure_kind:'theorem',disclosure_part:i<3?'core':'proof'}));
 const root=render(t,'正文继续。',fields),theorem=root.querySelector('[data-reader-group="explainables"]'),proof=theorem.querySelector('.reader-theorem-proof');
 assert.equal(closed(theorem.querySelector('[data-reader-pointer$="/what"]')),false);assert.equal(closed(theorem.querySelector('[data-reader-pointer$="/why"]')),false);
 assert.ok(proof);assert.equal(proof.open,false);assert.match(proof.textContent,/完整证明.*推导步骤/s);
});
test('media comparison does not move proof media outside its closed boundary',t=>{
 const root=render(t,'**定理1**：条件成立则结论成立。\n\n证明：从条件开始。\n\n$$a=a$$\n\n故得证。\n\n## 后续\n\n另一概念。');
 const media=root.querySelector('[data-media-anchor]');assert.ok(media);assert.equal(closed(media),true);assert.equal(closed([...root.querySelectorAll('p')].find(p=>p.textContent.includes('条件成立'))),false);
 assert.equal(closed([...root.querySelectorAll('p')].find(p=>p.textContent.includes('另一概念'))),false);
});
for(const cue of ['下面给出证明：','先证明必要性。','首先证明：','我们先证明：'])test('untitled proof cue folds and stops before the next intuition: '+cue,t=>{
 const root=render(t,'**定理4**：条件与结论保留。\n\n'+cue+'论证秘密内容。\n\n几何上看：这是一个旋转。\n\n**邻域**：后续概念。');
 const p=[...root.querySelectorAll('p')].find(p=>p.textContent.includes('论证秘密内容'));assert.equal(closed(p),true);
 assert.equal(closed([...root.querySelectorAll('p')].find(p=>p.textContent.includes('这是一个旋转'))),false);
});
test('legacy inline theorem windows keep conditions visible and their markdown and steps closed',t=>{
 const renderer=core.createRenderer({marked,katex});renderer.beginUnit({unitId:'U',explain:{T:{id:'T',kind:'theorem',name:'逆元定理',what:'非零条件与结论',why:'直观意义',markdown:'旧格式完整证明',steps:[{markdown:'旧格式推导步骤',basis:'旧格式依据'}]}}});
 const dom=new JSDOM('<main>'+renderer.renderBody('这里[[EXPLAIN:T|逆元定理]]。')+'</main>');t.after(()=>dom.window.close());const root=dom.window.document.querySelector('main');
 layout.enhance(root,{title:'旧单元'},'U');const pop=root.querySelector('.pop-explain');assert.ok(pop);pop.hidden=false;
 const proof=pop.querySelector('.reader-theorem-proof');assert.ok(proof);assert.equal(proof.open,false);assert.match(proof.textContent,/旧格式完整证明.*旧格式推导步骤.*旧格式依据/s);
 const condition=[...pop.querySelectorAll('.pp')].find(n=>n.textContent.includes('非零条件与结论'));assert.ok(condition);assert.equal(closed(condition),false);
});
for(const [unit,cue] of [
 ['CH02-S05-SS04','先证第一个。由式(2.14)和复数乘积的模与辐角公式，'],
 ['CH09-S02-SS10','下面证明本节的主要结论。']
])test('book proof cue is closed but the following conclusion and intuition stay visible: '+unit,t=>{
 const root=render(t,'## 结论与条件\n\n在上述条件下，结论成立。\n\n'+cue+'推导链必须收起。\n\n$$a=a$$\n\n结论：最终结果可见。\n\n直观解释：含义仍可读。\n\n## 后续概念\n\n下一概念不被吞。');
 const ps=[...root.querySelectorAll('p')],find=text=>ps.find(n=>n.textContent.includes(text));
 assert.equal(closed(find('推导链必须收起')),true);assert.equal(closed(root.querySelector('.katex-display')),true);
 for(const text of ['最终结果可见','含义仍可读','下一概念不被吞'])assert.equal(closed(find(text)),false,text);
});
