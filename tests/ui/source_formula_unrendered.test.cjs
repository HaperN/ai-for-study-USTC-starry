/* 原书资料里的坏公式：按原文保留、不渲染，并留可见标注。
   真实来源：run-20261011-125024 的原书"习题参考答案"（material:A0047）里有一个
   `\left[` 缺配对的 `\right]`（`\left[` ×2、`\right]` ×1）。那段原文**必须逐字保留**，
   于是它把整章的浏览器验收钉成 failed（`.katex-error`）。 */
const test = require('node:test');
const assert = require('node:assert/strict');
const marked = require('../../blocks/render/vendor/marked.min.js');
const katex = require('../../blocks/render/vendor/katex.min.js');
const core = require('../../blocks/render/assets/render-core.js');

const BROKEN = '\\left[2k\\pi - \\mathrm{i}\\ln(\\sqrt{2} - 1), \\ln(\\sqrt{5} - 2) + \\mathrm{i}(2k - \\frac{1}{2})\\pi.';

function renderer() {
  return core.createRenderer({marked, katex});
}

test('原书坏公式：不送进 KaTeX，页面里不出现 .katex-error，且原文逐字保留', () => {
  const html = renderer().renderSourceMaterial({title: '习题参考答案', raw_markdown: '$' + BROKEN + '$'});
  assert.ok(html.indexOf('katex-error') < 0,
    '原文侧不得留下 .katex-error —— 否则浏览器验收会被原书自身的排版残缺挡住');
  assert.ok(html.indexOf('source-formula-unrendered') >= 0, '必须留下可见标注');
  assert.ok(html.indexOf('2k\\pi') >= 0, '公式正文必须逐字保留，不许改写或丢弃');
});

test('模型正文里的坏公式仍会被验收抓到（质量闸门不许被兜底悄悄放过去）', () => {
  const html = renderer().renderMarkdown('$' + BROKEN + '$');
  assert.ok(html.indexOf('katex-error') >= 0, '非原文必须保留 KaTeX 自己的 .katex-error');
  assert.ok(html.indexOf('source-formula-unrendered') < 0, '只在原文侧做这个降级');
});

test('能渲染的公式两侧都照常渲染', () => {
  const r = renderer();
  const pages = [r.renderSourceMaterial({title: '资料', raw_markdown: '$a^2+b^2$'}),
                 r.renderMarkdown('$a^2+b^2$')];
  for (const html of pages) {
    assert.ok(html.indexOf('katex') >= 0, '正常公式照常渲染');
    assert.ok(html.indexOf('katex-error') < 0);
    assert.ok(html.indexOf('source-formula-unrendered') < 0, '不该误标正常公式');
  }
});
