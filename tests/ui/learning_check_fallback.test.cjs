const test = require('node:test');
const assert = require('node:assert/strict');
const marked = require('../../blocks/render/vendor/marked.min.js');
const core = require('../../blocks/render/assets/render-core.js');

test('missing self-check interaction metadata preserves the question and the rest of the lesson', () => {
  const entries = [
    ['learning_checks', 'Q', '/learning_checks/0/prompt', '计算虚部', '题干'],
    ['learning_checks', 'Q', '/learning_checks/0/input_spec/options/0/label', '选项 A：实数', '选项'],
    ['learning_checks', 'Q', '/learning_checks/0/hints/0', '观察虚部系数', '提示'],
    ['learning_checks', 'Q', '/learning_checks/0/answer', '答案：虚部是实数', '答案'],
    ['body_markdown', 'U', '/body_markdown', '后续正文仍可阅读', '正文'],
  ];
  const blocks = entries.map(([group, owner_id, json_pointer, markdown, field_label], i) => ({
    group, owner_id, json_pointer, markdown, raw_text: markdown, field_label,
    block_id: 'block-' + i, anchor: 'anchor-' + owner_id, collapsed: group === 'learning_checks',
  }));
  const doc = {schema_version: 'ReaderDocumentV1', unit_id: 'U', learning_checks: [], blocks};
  const renderer = core.createRenderer({marked});
  const html = renderer.readerDocumentHtml(doc, 'body');
  for (const entry of entries) assert.ok(html.includes(entry[3]), entry[3]);
  assert.match(html, /<details[^>]*id="anchor-Q"/);
  assert.doesNotMatch(html, /data-learning-check=|data-check-action=|data-check-input|\shidden(?:\s|>)/);
});
