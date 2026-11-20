const test = require('node:test');
const assert = require('node:assert/strict');
const marked = require('../../blocks/render/vendor/marked.min.js');
const core = require('../../blocks/render/assets/render-core.js');

test('original exercise source displays literally and only packaged images load', () => {
  const r = core.createRenderer({marked, sourceAssets:{'images/q.png':'assets/source/'+'a'.repeat(64)+'.png'}});
  const html = r.renderSourceMaterial({title:'习题',raw_markdown:'[[NOTE:book]] <script>alert(1)</script>\n\n![题图](images/q.png) ![外链](https://x.test/x.png)',source_lines:[5,8],source_pages:[3],state:'recorded'});
  assert.match(html, /\[\[NOTE:book\]\]/);
  assert.doesNotMatch(html, /<script>|src="https:/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /<img[^>]+src="assets\/source\/a{64}\.png"/);
  assert.match(html, /5–8/);
  assert.doesNotMatch(html, /<button|<input|data-check-answer/);
});

test('unlocated source material explains unavailable bounds without claiming extraction', () => {
  const html = core.createRenderer({marked}).renderSourceMaterial({title:'习题专章',raw_markdown:'',state:'unresolved',reason:'目录有条目，正文位置尚未定位',source_hint:{pdf_page:8}});
  assert.match(html, /尚未定位/);
  assert.match(html, /8/);
  assert.doesNotMatch(html, /已解析|已核实/);
});

test('unit exercise block preserves source syntax and renders no answer interaction', () => {
  const raw = '习题 [[EXAMPLE:X]]\n1. 求值。';
  const block = {block_id:'B', owner_id:'ER',json_pointer:'/exercise_regions/0/raw_markdown',raw_text:raw,markdown:raw,group:'exercise_regions',field_label:'原文记录',anchor:'ER',raw_source:true,source_lines:[4,5],collapsed:true};
  const html = core.createRenderer({marked}).readerDocumentHtml({schema_version:'ReaderDocumentV1',blocks:[block]}, 'body');
  assert.match(html, /\[\[EXAMPLE:X\]\]/);
  assert.match(html, /4–5/);
  assert.doesNotMatch(html, /<button|<input/);
});

test('formatted material is primary while unchanged original stays collapsed and safe', () => {
  const r = core.createRenderer({marked, sourceAssets:{'images/q.png':'assets/source/'+'b'.repeat(64)+'.png'}});
  const html = r.renderSourceMaterial({title:'参考答案', raw_markdown:'原始排版 [[NOTE:book]]',
    display_markdown:'## 整理后排版\n\n![题图](images/q.png) <script>bad()</script>',
    formatting:{status:'formatted', notes:['第 2 题符号不清。', '<img src=x onerror=bad()>']}});
  const [primary, original] = html.split(/<details(?= class="source-original">)/);
  assert.match(primary, /<h2[^>]*>整理后排版/);
  assert.doesNotMatch(primary, /原始排版/);
  assert.match(original, /^ class="source-original">/);
  assert.match(original, /<summary>查看原文<\/summary>/);
  assert.match(original, /原始排版 \[\[NOTE:book\]\]/);
  assert.match(primary, /仅整理格式/);
  assert.match(primary, /未核实答案正确性/);
  assert.match(html, /第 2 题符号不清/);
  assert.match(html, /<img[^>]+src="assets\/source\/b{64}\.png"/);
  assert.doesNotMatch(html, /<script>|<img src=x|data-check-answer|已核实/);
  const dom=new (require('jsdom').JSDOM)(html);
  assert.equal(dom.window.document.querySelector('article > .markdown').closest('details'),null);
  assert.equal(dom.window.document.querySelector('.source-original').open,false);dom.window.close();
});

test('raw fallback never advertises a formatted result or empty doubts', () => {
  const r = core.createRenderer({marked});
  for (const source of [
    {display_markdown:'不可用整理版'},
    {display_markdown:' ', formatting:{status:'formatted', notes:[]}},
    {display_markdown:'不可用整理版', formatting:{status:'original', notes:[]}},
  ]) {
    const html = r.renderSourceMaterial({...source, raw_markdown:'原文可读'});
    assert.match(html, /原文可读/);
    assert.doesNotMatch(html, /不可用整理版|<details|仅整理格式|source-format-notes/);
  }
  const html = r.renderSourceMaterial({raw_markdown:'原文',display_markdown:'整理版',
    formatting:{status:'formatted', notes:[]}});
  assert.doesNotMatch(html, /source-format-notes/);
});
