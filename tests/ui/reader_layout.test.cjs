const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const marked = require('../../blocks/render/vendor/marked.min.js');
const core = require('../../blocks/render/assets/render-core.js');
const parserPath = path.resolve(__dirname, '../../blocks/render/assets/reader-markdown.js');
const {JSDOM} = require('jsdom');
function teachingBlock(group, pointer, text, label) {
  return {block_id:pointer,owner_id:group,json_pointer:pointer,raw_text:text,markdown:text,
    group,field_label:label,anchor:'anchor-'+group,collapsed:group==='annotations'||group==='explainables'};
}
test('annotation summary shows its category and only an authored title', t => {
  const blocks=[teachingBlock('annotations','/annotations/0/title','共轭配对','直觉'),
    teachingBlock('annotations','/annotations/0/markdown','这一段解释应只出现在正文。','直觉')];
  const render=core.createRenderer({marked});
  const dom=new JSDOM(render.readerDocumentHtml({schema_version:'ReaderDocumentV1',blocks},'body'));
  t.after(()=>dom.window.close());
  const summary=dom.window.document.querySelector('summary');
  assert.ok(summary.querySelector('.reader-annotation-kind'),'annotation summary must expose its teaching category');
  assert.equal(summary.querySelector('.reader-annotation-kind').textContent,'直觉');
  assert.match(summary.textContent,/共轭配对/);
  const untitled=new JSDOM(render.readerDocumentHtml({schema_version:'ReaderDocumentV1',blocks:[blocks[1]]},'body'));
  t.after(()=>untitled.window.close());
  assert.equal(untitled.window.document.querySelector('summary').textContent,'直觉');
  assert.equal(untitled.window.document.querySelectorAll('[data-reader-pointer="/annotations/0/markdown"]').length,1);
  assert.equal(untitled.window.document.body.textContent.split('这一段解释应只出现在正文。').length-1,1);
});
for (const label of ['说明','直觉','易错','补充推导','旧批注标签']) {
  test('annotation '+label+' remains readable without a title',t=>{
    const block=teachingBlock('annotations','/annotations/0/markdown','正文与 $x=1$ 保留。',label);
    const dom=new JSDOM(core.createRenderer({marked}).readerDocumentHtml({schema_version:'ReaderDocumentV1',blocks:[block]},'body'));
    t.after(()=>dom.window.close());const d=dom.window.document;
    assert.equal(d.querySelector('summary').textContent,label==='旧批注标签'?'说明':label);
    assert.match(d.querySelector('[data-reader-pointer]').textContent,/正文与/);
    assert.equal(d.querySelector('[data-reader-pointer]').dataset.readerBlock,block.block_id);
  });
}
for (const group of ['examples','explainables']) {
  test(group+' groups existing review and pitfalls once without moving media or steps',t=>{
    const base='/'+group+'/0';
    const blocks=[teachingBlock(group,base+'/steps/0/markdown','逐步结论','第 1 步'),
      teachingBlock(group,base+'/steps/0/basis','合法依据','第 1 步依据'),
      teachingBlock(group,base+'/conclusion','最终结论','结论'),
      teachingBlock(group,base+'/key_points/0','先识别共轭对。','要点'),
      teachingBlock(group,base+'/key_points/1','先识别共轭对。','要点'),
      teachingBlock(group,base+'/pitfalls/0','不要把 $w+\\bar w$ 写成 $2w$。','易错点')];
    const pitfall=blocks[5],parsed=require(parserPath).parseReaderField(pitfall.markdown,{marked,field:pitfall});
    const mount={mount_id:'review-media',unit_id:'U',owner_id:group,target_pointer:pitfall.json_pointer,
      source_kind:'media',source_id:'M',source_hash:'hash',anchor_id:'A',resolved_block_id:parsed.blocks[0].block_id,edge:'after',order_at_anchor:0};
    const html=core.createRenderer({marked}).readerDocumentHtml({schema_version:'ReaderDocumentV1',blocks},'body',
      [{request_id:'M',kind:'animation'}],{schema_version:'ReaderLayoutV1',mounts:[mount]});
    const dom=new JSDOM(html);t.after(()=>dom.window.close());const d=dom.window.document;
    const reviews=[...d.querySelectorAll('.reader-teaching-review')];
    assert.deepEqual(reviews.map(e=>e.querySelector('h3').textContent),['解后回顾','易错提醒']);
    assert.deepEqual(reviews.map(e=>e.querySelectorAll('[data-reader-pointer]').length),[2,1]);
    assert.deepEqual([...d.querySelectorAll('[data-reader-pointer]')].map(e=>e.dataset.readerPointer),blocks.map(b=>b.json_pointer));
    assert.equal(d.querySelectorAll('[data-reader-block]').length,blocks.length);
    assert.ok(reviews[1].contains(d.querySelector('[data-mount-id="review-media"]')));
    assert.equal(reviews[0].textContent.split('先识别共轭对。').length-1,2,'authored repeated blocks are preserved');
    const empty=new JSDOM(core.createRenderer({marked}).readerDocumentHtml({schema_version:'ReaderDocumentV1',blocks:blocks.slice(0,3)},'body'));
    t.after(()=>empty.window.close());assert.equal(empty.window.document.querySelectorAll('.reader-teaching-review').length,0);
  });
}
const examples = [
  ['tight list', '- first\n- second\n- third', 'second', 'ul'],
  ['loose list', '- first\n\n  second\n\n- third', 'second', 'ul'],
  ['nested list', '- first\n  - second\n  - third', 'second', 'ul'],
  ['quote', '> first\n>\n> > second\n>\n> third', 'second', 'blockquote'],
  ['fence', '```text\nfirst\n\nsecond\n```', 'second', 'pre'],
  ['indented code', '    first\n\n    second', 'second', 'pre'],
  ['table', '| first | value |\n| --- | --- |\n| second | 2 |', 'second', 'table'],
];
for (const [label, raw, anchor, container] of examples) {
  test('whole-field mount preserves ' + label, () => {
    const block = {block_id:'field',owner_id:'U',json_pointer:'/body_markdown',raw_text:raw,markdown:raw,group:'body_markdown',field_label:'正文',anchor:'body'};
    const doc = {schema_version:'ReaderDocumentV1',unit_id:'U',blocks:[block]};
    const start = Array.from(raw.slice(0,raw.indexOf(anchor))).length;
    const renderer = core.createRenderer({marked});
    let layout;
    if (require('node:fs').existsSync(parserPath)) {
      const parser = require(parserPath);
      const parsed = parser.parseReaderField(raw,{marked,field:block});
      const containing = parsed.blocks.find(b => b.source_start_cp <= start && b.source_end_cp >= start+anchor.length);
      layout = {schema_version:'ReaderLayoutV1',mounts:[{mount_id:'mount',unit_id:'U',owner_id:'U',target_pointer:block.json_pointer,source_kind:'media',source_id:'M',source_hash:'hash',anchor_id:'A',requested_span_cp:[start,start+anchor.length],resolved_block_id:containing.block_id,edge:'after',order_at_anchor:0}]};
    }
    const html = renderer.readerDocumentHtml(doc,'body',[{request_id:'M',kind:'animation',placement:{target:'/body_markdown',anchor_id:'A'},markdown_offset:raw.indexOf(anchor)+anchor.length}],layout);
    const baseline = marked.parse(raw);
    // Source-coordinate attributes do not change the semantic container.
    assert.equal(JSDOM.fragment(html).querySelectorAll(container).length,
                 JSDOM.fragment(baseline).querySelectorAll(container).length);
    assert.match(html,/class="reader-media"[^>]*data-mount-id="mount"/);
    const mediaAt = html.indexOf('class="reader-media"');
    const close = label.includes('list') ? '</ul>' : label === 'quote' ? '</blockquote>' : label === 'table' ? '</table>' : '</pre>';
    assert.ok(mediaAt > html.lastIndexOf(close),'mount must follow the entire semantic container');
  });
}
test('shared parser retains link definitions and code text with one lexer call', () => {
  assert.ok(require('node:fs').existsSync(parserPath),'shared reader parser is required');
  const p = require(parserPath); let calls = 0;
  const engine = {...marked,lexer(...args){calls++;return marked.lexer(...args);}};
  const raw = '[reference][target]\n\n```text\n$x$\n```\n\n[target]: https://example.org';
  const parsed = p.parseReaderField(raw,{marked:engine,field:{block_id:'f',raw_text:raw,markdown:raw}});
  const html = p.renderReaderField(parsed,[],()=>'',{mathHtml:s=>'<math>'+s+'</math>'});
  assert.equal(calls,1); assert.match(html,/href="https:\/\/example.org"/); assert.match(html,/\$x\$/); assert.doesNotMatch(html,/<math>/);
});
test('math in nested prose renders while quoted fenced code remains literal', () => {
  const p=require(parserPath), raw='- first\n    - second $x$\n\n> ~~~text\n> $code$\n> ~~~';
  const parsed=p.parseReaderField(raw,{marked,field:{block_id:'f',raw_text:raw}});
  const html=p.renderReaderField(parsed,[],()=>'',{mathHtml:s=>'<math>'+s+'</math>'});
  assert.match(html,/<math>\$x\$<\/math>/); assert.match(html,/>\$code\$/); assert.doesNotMatch(html,/<math>\$code\$/);
});
test('diagnostic slots show escaped status and a missing-image reason', () => {
  const block={block_id:'f',owner_id:'U',json_pointer:'/body_markdown',raw_text:'正文',markdown:'正文',group:'body_markdown',field_label:'正文',anchor:'body'};
  const parsed=require(parserPath).parseReaderField(block.markdown,{marked,field:block});
  const mount={mount_id:'m',unit_id:'U',owner_id:'U',target_pointer:block.json_pointer,source_kind:'media',source_id:'M',source_hash:'hash',anchor_id:'A',resolved_block_id:parsed.blocks[0].block_id,edge:'after',order_at_anchor:0};
  const html=core.createRenderer({marked}).readerDocumentHtml({schema_version:'ReaderDocumentV1',unit_id:'U',blocks:[block]},'body',
    [{request_id:'M',kind:'geometry',figure_id:null,diagnostic_status:'missing<script>',diagnostic_reason:'资源<未审核>'}],{schema_version:'ReaderLayoutV1',mounts:[mount]});
  assert.match(html,/reader-media-missing/);assert.match(html,/missing&lt;script&gt;/);assert.match(html,/资源&lt;未审核&gt;/);assert.doesNotMatch(html,/<script>/);
});
test('title media remain heading siblings inside their reader group', () => {
  const field={block_id:'title',owner_id:'U',json_pointer:'/title',raw_text:'标题😀',markdown:'标题😀',group:'title',field_label:'标题',anchor:'heading'};
  const parsed=require(parserPath).parseReaderField(field.markdown,{marked,field});
  const mount={mount_id:'m',unit_id:'U',owner_id:'U',target_pointer:'/title',source_kind:'media',source_id:'M',source_hash:'hash',anchor_id:'A',resolved_block_id:parsed.blocks[0].block_id,edge:'after',order_at_anchor:0};
  const html=core.createRenderer({marked}).readerDocumentHtml({schema_version:'ReaderDocumentV1',unit_id:'U',blocks:[field]},'header',[{request_id:'M',kind:'animation'}],{schema_version:'ReaderLayoutV1',mounts:[mount]});
  assert.match(html,/<h1[^>]*>标题😀<\/h1><div class="reader-media"/);
  assert.ok(html.indexOf('class="reader-media"')<html.indexOf('</section>'));
});
