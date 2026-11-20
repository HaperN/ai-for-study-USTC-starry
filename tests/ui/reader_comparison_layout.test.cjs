const test = require('node:test');
const assert = require('node:assert/strict');
const {JSDOM} = require('jsdom');
const marked = require('../../blocks/render/vendor/marked.min.js');
const katex = require('../../blocks/render/vendor/katex.min.js');
const core = require('../../blocks/render/assets/render-core.js');
const parser = require('../../blocks/render/assets/reader-markdown.js');
const layout = require('../../blocks/render/assets/reader-layout.js');

function field(group, pointer, text, options={}) {
  return {block_id:'sha256:'+require('node:crypto').createHash('sha256').update(pointer).digest('hex'),owner_id:options.owner||'unit-arbitrary',json_pointer:pointer,
    raw_text:text,markdown:text,group,field_label:options.label||group,anchor:'anchor-'+(options.owner||group),
    collapsed:false,...options};
}
function render(t, blocks, mediaSpecs=[]) {
  const mounts=mediaSpecs.map((spec,index)=>{
    const b=blocks.find(b=>b.json_pointer===spec.pointer);
    const parsed=parser.parseReaderField(b.markdown,{marked,field:b});
    const anchor=parsed.blocks.find(b=>b.raw_text?.includes(spec.after)) || parsed.blocks[spec.blockIndex||0];
    return {mount_id:'mount-'+index,unit_id:'unit-arbitrary',owner_id:b.owner_id,
      target_pointer:b.json_pointer,source_kind:'media',source_id:'request-'+index,source_hash:'hash-'+index,
      anchor_id:'media-anchor-'+index,resolved_block_id:anchor.block_id,edge:'after',order_at_anchor:index};
  });
  const unit={title:'与复变函数无关的任意单元',reader_document:{schema_version:'ReaderDocumentV1',blocks},
    reader_media:mediaSpecs.map((s,i)=>({request_id:'request-'+i,kind:s.kind||'animation'})),
    reader_layout:{schema_version:'ReaderLayoutV1',mounts}};
  const renderer=core.createRenderer({marked,katex}); renderer.beginUnit({unitId:'unit-arbitrary'});
  const dom=new JSDOM('<main>'+renderer.readerDocumentHtml(unit.reader_document,'body',unit.reader_media,unit.reader_layout)+'</main>');
  t.after(()=>dom.window.close()); return {root:dom.window.document.querySelector('main'),unit};
}
function exampleBlocks(id='exercise-k',label='例7') {
  return [field('examples','/examples/0/label',label,{owner:id,label:'例题'}),
    field('examples','/examples/0/statement','证明 $a=a$。',{owner:id,label:'题目'}),
    field('examples','/examples/0/approach','从等号的自反性出发。',{owner:id,label:'思路'}),
    field('examples','/examples/0/steps/0/markdown','保留原来的第一步。',{owner:id,label:'第 1 步',step_id:'proof-k',step_no:1}),
    field('examples','/examples/0/steps/0/basis','依据不得丢失。',{owner:id,label:'第 1 步依据',step_id:'proof-k',step_no:1}),
    field('examples','/examples/0/conclusion','完整结论。',{owner:id,label:'结论'})];
}

test('enhancement keeps frozen blocks, numbered references and multiple original media slots',t=>{
  const raw='## 向量运算\n\n介绍。\n\n**定理8（线性）** 条件与结论 $a=b$。\n\n证明完整保留。\n\n## 应用\n\n由定理8得到结果。';
  const {root,unit}=render(t,[field('body_markdown','/body_markdown',raw)],
    [{pointer:'/body_markdown',blockIndex:2},{pointer:'/body_markdown',blockIndex:2,kind:'drawing'}]);
  const original=[...root.querySelectorAll('[data-reader-block],[data-reader-reference],[data-media-anchor],.katex')];
  const attributes=original.map(n=>[n,n.getAttribute('data-reader-pointer'),n.getAttribute('data-reader-block'),n.id]);
  const ref=root.querySelector('[data-reader-reference]'),link=[...root.querySelectorAll('a')].find(n=>n.textContent==='定理8');
  const topics=layout.enhance(root,unit,'a-different-unit');
  assert.ok(topics.length>=3);
  assert.ok(topics.some(x=>x.title.includes('定理8')));
  for(const [node,p,b,id] of attributes){assert.ok(root.contains(node));assert.equal(node.getAttribute('data-reader-pointer'),p);assert.equal(node.getAttribute('data-reader-block'),b);assert.equal(node.id,id);}
  assert.equal(root.querySelectorAll('[data-media-anchor]').length,2);
  assert.equal(root.querySelectorAll('.reader-media-column [data-media-anchor]').length,2);
  assert.equal(root.querySelector(link.getAttribute('href')),ref);
  assert.match(ref.textContent,/证明完整保留/);
  assert.equal(root.querySelectorAll('.katex-error').length,0);
  assert.equal(root.querySelectorAll('[data-animation-step]').length,0,'prose order is not a video mapping');
});

test('body proof and structured evidence stay complete inside closed example disclosures',t=>{
  const raw='例7：[[EXAMPLE:exercise-k|例7的完整说明]]\n\n例7 证明一个结论。\n\n第一步，保留条件。\n\n第二步，完整运算 $a=a$。\n\n结论：已经证明。';
  const {root,unit}=render(t,[field('body_markdown','/body_markdown',raw),...exampleBlocks()]);
  // The production serializer supplies resolved reader links.
  const body=root.querySelector('[data-reader-pointer="/body_markdown"]');
  const target=root.querySelector('[data-reader-group="examples"]');
  body.querySelector('p').innerHTML='例7：<a href="#'+target.id+'">例7的完整说明</a>';
  const originalBlocks=[...root.querySelectorAll('[data-reader-block]')];
  const topics=layout.enhance(root,unit,'unit-arbitrary');
  const example=topics.find(x=>x.kind==='example'); assert.ok(example);
  const folded=example.element.querySelector(':scope > details.reader-example-disclosure');assert.ok(folded);assert.equal(folded.open,false);
  assert.equal(folded.querySelectorAll(':scope > .reader-proof-steps > .reader-proof-step').length,2);
  assert.equal(example.element.querySelector('.reader-proof-steps').closest('details').open,false);
  const duplicate=target.closest('details.reader-example-alternative');assert.ok(duplicate);assert.equal(duplicate.open,false);
  assert.match(duplicate.querySelector('summary').textContent,/步骤整理与补充依据/);
  assert.ok(example.element.contains(duplicate));
  originalBlocks.forEach(node=>assert.ok(root.contains(node)));
  assert.match(target.textContent,/依据不得丢失/);
  assert.equal(root.querySelectorAll('#'+target.id).length,1);
});

test('reference-only example opens original statement, approach, steps and basis at its reference',t=>{
  const {root,unit}=render(t,[field('body_markdown','/body_markdown','## 方法\n\n例7。证明见此题。'),...exampleBlocks()]);
  const target=root.querySelector('[data-reader-group="examples"]');
  root.querySelector('[data-reader-pointer="/body_markdown"] p').innerHTML='例7。完整证明见 <a href="#'+target.id+'">这道题</a>。';
  const proof=root.querySelector('[data-reader-step="proof-k"]');
  const topics=layout.enhance(root,unit,'opaque-unit');
  const topic=topics.find(x=>x.kind==='example');assert.ok(topic);
  assert.ok(topic.element.contains(target));assert.equal(target.closest('details').open,false);
  target.querySelector('summary').click();assert.equal(target.open,true);
  assert.match(topic.element.textContent,/证明.*从等号的自反性.*依据不得丢失/s);
  assert.equal(root.querySelector('[data-reader-step="proof-k"]'),proof);
  assert.equal(target.querySelectorAll('.reader-proof-step').length,1);
  assert.equal(target.querySelectorAll('.reader-proof-step [data-reader-step="proof-k"]').length,2);
});

test('an example without body reference keeps a discoverable title and starts closed',t=>{
  const {root,unit}=render(t,[field('body_markdown','/body_markdown','平常的一段正文。'),...exampleBlocks('other-k','练习11')]);
  const topics=layout.enhance(root,unit,'unit-long-id');
  assert.ok(topics.some(x=>x.kind==='example'&&x.title.includes('练习11')));
  const target=root.querySelector('[data-reader-group="examples"]');assert.equal(target.tagName,'DETAILS');assert.equal(target.open,false);assert.match(target.querySelector('summary').textContent,/练习11/);
});

test('no media, semantic lists, code and nested headings keep their content and enhancement is idempotent',t=>{
  const raw='## 完整正文\n\n- 第一项\n  - 内嵌项目\n\n> ## 引用中的标题\n> 完整引文。\n\n```text\n第一步，代码不是真正步骤\n```\n\n**另一话题。** 段落正文。';
  const {root,unit}=render(t,[field('body_markdown','/body_markdown',raw)]);
  const preserved=[...root.querySelectorAll('ul,li,blockquote,pre,code')];
  const content=preserved.map(n=>n.textContent);
  const first=layout.enhance(root,unit,'one'),html=root.innerHTML,second=layout.enhance(root,unit,'one');
  assert.equal(root.innerHTML,html);assert.deepEqual(second.map(x=>x.id),first.map(x=>x.id));
  assert.equal(root.querySelectorAll('.reader-media-column').length,0);
  preserved.forEach((n,i)=>{assert.ok(root.contains(n));assert.equal(n.textContent,content[i]);});
  assert.equal(root.querySelectorAll('.reader-proof-step').length,0);
});

test('topic identities include the unit and do not depend on a fixed book or example label',t=>{
  const a=render(t,[field('body_markdown','/body_markdown','## 同名标题\n\n正文。')]);
  const b=render(t,[field('body_markdown','/body_markdown','## 同名标题\n\n另一正文。')]);
  const at=layout.enhance(a.root,a.unit,'different/a'),bt=layout.enhance(b.root,b.unit,'different/b');
  assert.notEqual(at[0].id,bt[0].id);
  assert.equal(layout.topics(a.root)[0].element,at[0].element);
});

test('authored step numbers survive excerpts and proof cues are not split into unrelated topics',t=>{
  const {root,unit}=render(t,[field('body_markdown','/body_markdown','## 推导\n\n没有解：原因继续在当前主题内说明。\n\n第五步，原编号必须保留。\n\n第六步，继续说明。\n\n结论：结束。')]);
  const topics=layout.enhance(root,unit,'excerpt-unit');
  assert.equal(topics.length,1);
  assert.deepEqual([...root.querySelectorAll('.reader-proof-number')].map(x=>x.textContent),['05','06']);
  assert.ok(!root.querySelectorAll('.reader-proof-step')[1].textContent.includes('结论'));
});

test('emphasized heading prefixes form separate media topics without losing punctuation or authored words',t=>{
  const raw='**基本规则。** 总述。\n\n*旋转*：沿给定方向转动。\n\n完整公式。\n\n*平移*：移动到指定位置。';
  const {root,unit}=render(t,[field('body_markdown','/body_markdown',raw)]);
  const before=root.querySelector('[data-reader-pointer]').textContent;
  const topics=layout.enhance(root,unit,'geometry-u');
  assert.deepEqual(topics.map(x=>x.title),['基本规则','旋转','平移']);
  assert.equal(root.querySelector('[data-reader-pointer]').textContent,before);
  assert.match(root.querySelector('.reader-topic-title').textContent,/基本规则。/);
});

test('separate necessary and sufficient proofs keep their intervening conclusions in order',t=>{
  const raw='## 命题证明\n\n第一步，必要性甲。\n\n第二步，必要性乙。\n\n结论：必要性完成，下面证明充分性。\n\n第一步，充分性丙。\n\n第二步，充分性丁。\n\n结论：充分性完成。';
  const {root,unit}=render(t,[field('body_markdown','/body_markdown',raw)]);
  const before=[...root.querySelectorAll('p')];
  layout.enhance(root,unit,'two-directions');
  assert.deepEqual([...root.querySelectorAll('p')],before);
  assert.equal(root.querySelectorAll('.reader-proof-steps').length,2);
  const lists=[...root.querySelectorAll('.reader-proof-steps')];
  assert.match(lists[0].textContent,/必要性甲.*必要性乙/s);
  assert.doesNotMatch(lists[0].textContent,/充分性/);
  assert.match(lists[1].textContent,/充分性丙.*充分性丁/s);
});

test('structured proof fragments retain non-step material between them',t=>{
  const blocks=exampleBlocks();
  blocks.splice(5,0,
    field('examples','/examples/0/warmup','下一步之前的条件说明。',{owner:'exercise-k',label:'回顾'}),
    field('examples','/examples/0/steps/1/markdown','条件说明之后才进行第二步。',{owner:'exercise-k',label:'第 2 步',step_id:'proof-next',step_no:2}));
  const {root,unit}=render(t,blocks),before=[...root.querySelectorAll('[data-reader-pointer]')];
  layout.enhance(root,unit,'structured-fragments');
  assert.deepEqual([...root.querySelectorAll('[data-reader-pointer]')],before);
  assert.equal(root.querySelectorAll('.reader-proof-steps').length,2);
});

test('only an exact repeated example prefix is hidden and the expansion title preserves formula nodes',t=>{
  const {root,unit}=render(t,[field('body_markdown','/body_markdown','例2：[例2：判断 $z=\\bar z$](#reader-12345678901234567890)\n\n完整题目。')]);
  const body=root.querySelector('[data-reader-pointer]'),before=body.textContent;
  const prefix=body.querySelector('p').firstChild,link=body.querySelector('a'),math=link.querySelector('.katex');
  layout.enhance(root,unit,'same-prefix');
  const hidden=body.querySelector('.reader-repeated-example-prefix');
  assert.ok(hidden);assert.equal(hidden.hidden,true);assert.equal(hidden.firstChild,prefix);
  assert.equal(body.textContent,before);assert.equal(body.querySelector('a'),null,'a summary title cannot intercept the expansion as a popup link');const label=body.querySelector('summary .reader-example-title-reference');assert.ok(label);assert.equal(label.querySelector('.katex'),math);
  assert.match(label.textContent,/例2：判断/);
  const visible=body.cloneNode(true);visible.querySelectorAll('[hidden],.katex-mathml').forEach(n=>n.remove());
  assert.equal((visible.textContent.match(/例2：/g)||[]).length,1);
});

for(const raw of ['例2：[例3：另一题](#reader-12345678901234567890)','例2：根据[例2的证明](#reader-12345678901234567890)继续。']) {
  test('non-duplicate example prose stays visible: '+raw,t=>{
    const {root,unit}=render(t,[field('body_markdown','/body_markdown',raw)]);
    assert.ok(root.querySelector('a'));
    layout.enhance(root,unit,'distinct-prefix');
    assert.equal(root.querySelectorAll('.reader-repeated-example-prefix').length,0);
  });
}

test('an explicit property list and its numbered proofs become navigable without treating ordinary lists as properties',t=>{
  const raw='## 运算\n\n回顾定义。\n\n本节常用性质如下：\n\n1. $a=a$。\n2. $b=b$。\n\n下面证明。\n\n(1) 第一条的完整证明。\n\n(2) 第二条的完整证明。\n\n例8：另一题。\n\n(1) 题目的第一问。';
  const {root,unit}=render(t,[field('body_markdown','/body_markdown',raw)]);
  const originals=[...root.querySelectorAll('p,ol,li,.katex')];
  const topics=layout.enhance(root,unit,'unrelated-book');
  assert.ok(topics.some(x=>x.kind==='property'&&x.title==='本节常用性质'));
  assert.deepEqual(topics.filter(x=>/^性质\d+的推导$/.test(x.title)).map(x=>x.title),['性质1的推导','性质2的推导']);
  originals.forEach(n=>assert.ok(root.contains(n)));
  assert.match(topics.find(x=>x.kind==='example').element.textContent,/题目的第一问/);
});

test('authored short step headings are separated while formulas and frozen block identities survive',t=>{
  const blocks=exampleBlocks(),step=blocks.find(b=>b.json_pointer.endsWith('/steps/0/markdown'));step.raw_text=step.markdown='交叉相乘，得 $a=b$。';
  const {root,unit}=render(t,blocks);
  const formula=root.querySelector('.katex'),fields=[...root.querySelectorAll('[data-reader-block]')];
  layout.enhance(root,unit,'other-proof');
  assert.equal(root.querySelector('.reader-proof-heading').textContent,'交叉相乘，');
  assert.equal(root.querySelector('.katex'),formula);
  fields.forEach(n=>assert.ok(root.contains(n)));
  assert.match(root.textContent,/交叉相乘，得/);
});

test('a structured proof keeps its statement above comparison and a source-location button focuses the actual media without inventing video steps',t=>{
  const {root,unit}=render(t,exampleBlocks(),[{pointer:'/examples/0/steps/0/markdown'}]);
  const slot=root.querySelector('[data-media-anchor]');
  layout.enhance(root,unit,'paired-proof');
  const group=root.querySelector('[data-reader-group="examples"]');
  assert.equal(group.querySelector('[data-reader-pointer$="/statement"]').closest('.reader-comparison'),null);
  assert.equal(slot.closest('.reader-media-column').closest('[data-reader-group]'),group);
  const button=group.querySelector('.reader-proof-media-link');assert.ok(button);
  assert.equal(button.closest('.reader-proof-step').getAttribute('data-proof-step-id'),'proof-k');
  slot.scrollIntoView=()=>{};button.click();
  assert.equal(root.ownerDocument.activeElement,slot);
  assert.ok(button.closest('.reader-proof-step').classList.contains('is-active'));
  assert.equal(root.querySelectorAll('[data-animation-step]').length,0);
  const popup=root.ownerDocument.createElement('aside');popup.className='reader-popup';popup.appendChild(button.cloneNode(true));root.ownerDocument.body.appendChild(popup);
  button.focus();popup.querySelector('button').click();
  assert.equal(root.ownerDocument.activeElement,slot,'a cloned reference button still locates the original media');
});

test('an authored compact concept is a small in-flow card and its optional explanation stays outside',t=>{
  const raw='开场。\n\n> **概念·共轭复数**\n> 若 $z=x+iy$ 且 $x,y\\in\\mathbb R$，则 $\\bar z=x-iy$。\n\n接下来的必要性质仍然完整讲解。';
  const {root,unit}=render(t,[field('body_markdown','/body_markdown',raw)]);
  const original=[...root.querySelectorAll('[data-reader-block],.katex')];
  const topics=layout.enhance(root,unit,'concept-layout');
  const card=root.querySelector('.reader-concept-card');assert.ok(card);
  assert.match(card.textContent,/共轭复数/);assert.ok(!card.closest('details'));
  assert.doesNotMatch(card.textContent,/接下来的必要性质/);
  assert.ok(topics.some(t=>t.kind==='concept'&&t.title.includes('共轭复数')));
  original.forEach(n=>assert.ok(root.contains(n)));
});

test('legacy named definitions retain their entire formula declaration in a card, without swallowing proof or changing theorem presentation',t=>{
  const raw='**内点**：如果存在邻域完全属于集合，就称为内点。\n\n解释留在正文。\n\n**连续曲线**：设 $x(t)$、$y(t)$ 连续，由方程\n\n$$z(t)=x(t)+iy(t)$$\n\n所决定的点集称为连续曲线。\n\n证明：这是后续论证。\n\n**约当定理**：结论不能当成概念定义折叠。';
  const {root,unit}=render(t,[field('body_markdown','/body_markdown',raw)]);
  const originals=[...root.querySelectorAll('p,.katex')];layout.enhance(root,unit,'legacy-definitions');
  const cards=[...root.querySelectorAll('.reader-concept-card')];assert.equal(cards.length,2);
  assert.match(cards[1].textContent,/所决定的点集称为连续曲线/);
  assert.doesNotMatch(cards[1].textContent,/后续论证|约当定理/);
  assert.equal(root.querySelectorAll('details.reader-theorem-proof').length,1);assert.equal(root.querySelector('details.reader-theorem-proof').open,false);
  originals.forEach(n=>assert.ok(root.contains(n)));
});

for(const detached of [false,true]) test('an existing explicit concept definition uses its real catalog title, including search documents: '+detached,t=>{
  const raw='形如 $z=x+iy$ 的数称为[复数](#reader-12345678901234567890)，其中 $x,y$ 都是实数。\n\n后续分析继续保留。';
  const {root,unit}=render(t,[field('body_markdown','/body_markdown',raw)]);
  const reference=root.ownerDocument.createElement('details');reference.id='reader-12345678901234567890';reference.dataset.readerGroup='references';root.querySelector('.reader-document').appendChild(reference);
  if(detached)root.remove();
  const original=root.querySelector('p'),text=original.textContent,link=original.querySelector('a');
  layout.enhance(root,unit,'existing-concept');
  assert.ok(original.closest('.reader-concept-card'));assert.equal(original.textContent,text);assert.equal(original.querySelector('a'),link);
  assert.doesNotMatch(original.closest('.reader-concept-card').textContent,/后续分析/);
});

test('one explicit outer numbered example click opens its unique matching structured question',async t=>{
  const raw='例7：完整题目入口。\n\n第一步，正文演算。\n\n第二步，正文演算结束。\n\n结论：结束。';
  const {root,unit}=render(t,[field('body_markdown','/body_markdown',raw),...exampleBlocks()]);
  const group=root.querySelector('[data-reader-group="examples"]');
  root.querySelector('[data-reader-pointer="/body_markdown"] p').innerHTML='例7：<a href="#'+group.id+'">例7</a>';
  const topics=layout.enhance(root,unit,'one-click-example'),outer=topics.find(x=>x.kind==='example').element.querySelector(':scope > details.reader-example-disclosure');
  assert.equal(outer.open,false);assert.equal(group.open,false);
  const alternative=group.closest('.reader-example-alternative');assert.ok(alternative);assert.equal(alternative.open,false);
  // Merely restoring or assigning outer state must not reveal answers.
  outer.open=true;await new Promise(resolve=>setTimeout(resolve,0));assert.equal(group.open,false);outer.open=false;
  outer.querySelector(':scope > summary').click();await Promise.resolve();
  assert.equal(group.open,false,'do not reveal the inner question during a pre-activation microtask checkpoint');
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(outer.open,true);assert.equal(group.open,true,'a deliberate click on the single numbered title opens its matching question');assert.equal(alternative.open,true);
  assert.equal(group.querySelector('[data-reader-pointer$="/statement"]').closest('details:not([open])'),null,'the statement requires only this one explicit click');
});

test('a bare example collection click leaves every separate question closed',async t=>{
 const second=exampleBlocks('exercise-next','例8').map(b=>({...b,json_pointer:b.json_pointer.replace('/examples/0/','/examples/1/'),block_id:b.block_id+'-next'}));
 const {root,unit}=render(t,[field('body_markdown','/body_markdown','## 例题\n\n题目入口。'),...exampleBlocks(),...second]);
 const groups=[...root.querySelectorAll('[data-reader-group="examples"]')];root.querySelector('[data-reader-pointer="/body_markdown"] p').innerHTML=groups.map((g,i)=>'<a href="#'+g.id+'">例'+(i+7)+'</a>').join('、');
 const topics=layout.enhance(root,unit,'collection'),outer=topics.find(x=>x.title==='例题').element.querySelector(':scope > details');assert.ok(outer);assert.equal(groups.length,2);groups.forEach(g=>assert.equal(g.open,false));
 outer.querySelector(':scope > summary').click();await new Promise(resolve=>setTimeout(resolve,0));assert.equal(outer.open,true);groups.forEach(g=>assert.equal(g.open,false));
});
