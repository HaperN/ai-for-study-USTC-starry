const test = require('node:test');
const assert = require('node:assert/strict');
const {JSDOM} = require('jsdom');
const marked = require('../../blocks/render/vendor/marked.min.js');
const katex = require('../../blocks/render/vendor/katex.min.js');
const core = require('../../blocks/render/assets/render-core.js');
const parser = require('../../blocks/render/assets/reader-markdown.js');

function render(raw, t) {
  const field = {block_id:'sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
    owner_id:'U',json_pointer:'/body_markdown',raw_text:raw,markdown:raw,
    group:'body_markdown',field_label:'正文',anchor:'reader-1234567890abcdef1234'};
  const dom = new JSDOM(core.createRenderer({marked,katex}).readerDocumentHtml(
    {schema_version:'ReaderDocumentV1',blocks:[field]},'body'));
  t.after(()=>dom.window.close());
  return {d:dom.window.document,field};
}

test('numbered property references open the exact definition and proof range', t => {
  const raw = String.raw`**性质2（实部与共轭）** 对任意复数 $z$，有
$$z+\bar z=2\operatorname{Re}z.$$
证明：令 $z=x+iy$，相加得 $2x$。

**性质3（另一性质）** 其他内容。

## 判据

由性质2，马上可以得到两个非常有用的判据。`;
  const {d} = render(raw,t);
  const link = [...d.querySelectorAll('a')].find(e=>e.textContent==='性质2');
  assert.ok(link,'plain numbered reference should have an exact reader target');
  const target = d.getElementById(link.hash.slice(1));
  assert.equal(target.dataset.readerReference,'性质2');
  assert.match(target.textContent,/证明：令/);
  assert.doesNotMatch(target.textContent,/性质3|马上/);
  assert.ok(target.querySelector('.katex'));
});

test('numbered theorem heading includes its proof and stops at the next heading', t => {
  const {d} = render('## 定理1.2 连续性\n\n定理的条件和结论。\n\n证明的第二段。\n\n## 应用\n\n由定理1.2得到结果。',t);
  const link = [...d.querySelectorAll('a')].find(e=>e.textContent==='定理1.2');
  assert.ok(link);
  const target = d.getElementById(link.hash.slice(1));
  assert.match(target.textContent,/证明的第二段/);
  assert.doesNotMatch(target.textContent,/应用|得到结果/);
});

test('a separate proof paragraph belongs to its numbered definition', t => {
  const {d}=render('**性质2（实部）** 结论。\n\n证明：第一步。\n\n另一方面，第二步。\n\n由性质2得到应用。',t);
  const link=[...d.querySelectorAll('a')].find(e=>e.textContent==='性质2');
  const target=d.getElementById(link.hash.slice(1));
  assert.match(target.textContent,/证明：第一步/);
  assert.match(target.textContent,/另一方面，第二步/);
  assert.doesNotMatch(target.textContent,/得到应用/);
});

for (const nested of ['> **性质2（另一条）** B。','- **性质2（另一条）** B。']) {
  test('a nested duplicate definition prevents an ambiguous numbered link: '+nested[0], t => {
    const {d}=render('**性质2（第一条）** A。\n\n'+nested+'\n\n由性质2得到结果。',t);
    assert.equal(d.querySelectorAll('a[href^="#reader-"]').length,0);
  });
}

test('numbered heading stops before the next bold numbered definition', t => {
  const {d}=render('## 定理1（第一条）\n\n证明：第一条的证明。\n\n**定理2（第二条）** 第二条结论。\n\n由定理1可知。',t);
  const link=[...d.querySelectorAll('a')].find(e=>e.textContent==='定理1');
  const target=d.getElementById(link.hash.slice(1));
  assert.match(target.textContent,/第一条的证明/);
  assert.doesNotMatch(target.textContent,/定理2|第二条结论/);
});

test('ambiguous or absent numbered definitions remain plain prose', t => {
  const {d} = render('**性质2（第一条）** A。\n\n**性质2（另一条）** B。\n\n由性质2和定理9得到结果。',t);
  assert.equal(d.querySelectorAll('a[href^="#reader-"]').length,0);
});

test('numbered links exclude code, math, existing links and longer numbers', t => {
  const {d} = render('**定义2（非零）** 条件。\n\n由定义2可知。定义20不在这里。`定义2`，$\\text{定义2}$，[定义2](https://example.org)。\n\n```text\n定义2\n```',t);
  assert.deepEqual([...d.querySelectorAll('a[href^="#reader-"]')].map(e=>e.textContent),['定义2']);
  assert.equal(d.querySelector('a[href="https://example.org"]').textContent,'定义2');
  assert.equal(d.querySelector('code').textContent,'定义2');
  assert.equal(d.querySelectorAll('.katex-error').length,0);
});

test('numbered definition decoration preserves frozen source positions and media mounts', t => {
  const raw='😀\n\n**性质2（实部）** 原段。\n\n由性质2可知。';
  const {field}=render(raw,t), parsed=parser.parseReaderField(raw,{marked,field});
  const definition=parsed.blocks.find(b=>raw.slice(b.source_start_cp,b.source_end_cp).includes('原段'));
  assert.ok(definition);
  const mount={mount_id:'m',resolved_block_id:definition.block_id,edge:'after',order_at_anchor:0};
  for(const teachingRoles of [{}, {[definition.block_id]:'main'}]) {
    const html=parser.renderReaderField(parsed,[mount],()=>'<aside data-mount-id="m"></aside>',{teachingRoles});
    assert.equal((html.match(/data-mount-id="m"/g)||[]).length,1);
    assert.match(html,/<\/section><aside data-mount-id="m">/);
    const dom=new JSDOM(html);t.after(()=>dom.window.close());
    const slot=dom.window.document.querySelector('[data-mount-id="m"]');
    assert.equal(slot.closest('.reader-numbered-reference'),null,'ordinary after media is not copied into the definition reference window');
    assert.equal(slot.previousElementSibling.dataset.readerReference,'性质2');
  }
});

test('unbraced conjugate subscript survives JSON, Markdown and KaTeX', t => {
  const raw=JSON.parse('"分子分母同乘 $\\\\bar z_2$，得到 $z_2\\\\bar z_2=|z_2|^2$。"');
  const {d}=render(raw,t);
  assert.deepEqual([...d.querySelectorAll('annotation[encoding="application/x-tex"]')].map(e=>e.textContent),
    [String.raw`\bar z_2`,String.raw`z_2\bar z_2=|z_2|^2`]);
  assert.equal(d.querySelectorAll('.katex-error,.math-error').length,0);
  assert.equal(d.querySelectorAll('mover').length,2,'both conjugate bars survive to MathML');
});

test('a numbered definition retains its introduced condition list in the definition window',t=>{
  const {d}=render('**定义2（区域）** 具有下列性质的非空点集称为区域：\n\n1. 它是开集；\n2. 它是连通集。\n\n这里解释为什么需要两个条件。\n\n由定义2得到。',t);
  const link=[...d.querySelectorAll('a')].find(a=>a.textContent==='定义2');
  const target=d.getElementById(link.hash.slice(1));
  assert.equal(target.querySelectorAll('li').length,2);
  assert.match(target.textContent,/开集.*连通集/s);
  assert.doesNotMatch(target.textContent,/这里解释/);
});
