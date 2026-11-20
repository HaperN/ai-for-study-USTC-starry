const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../../src/aiforstudy/resources/control-ui');
const state = {
  book_id: 'book-a', run_id: 'run-saved', status: 'needs_review', execution_complete: true,
  quality_status: 'needs_review', release_ready: false, last_event_seq: 0, live_worker: false,
  request: {selectors: {kind: 'chapters', values: ['CH01']}},
  progress: {preparation: {completed: 6, total: 6, tokens: 200, nodes: []},
             selected: {completed: 3, total: 3, tokens: 300, nodes: []}},
  previews: [{chapter: 'CH01', title: 'CH01 章节 HTML', url: '/artifacts/book-a/run-saved/CH01/index.html'}],
};
const requests = [];
let failSubmission = true;
const dom = new JSDOM(fs.readFileSync(path.join(root, 'index.html'), 'utf8'), {
  url: 'http://127.0.0.1:8765', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
w.localStorage.setItem('aifs.workbench', JSON.stringify({bookId: 'book-a', runId: 'run-saved'}));
w.fetch = async (url, options = {}) => {
  const body = options.body ? JSON.parse(options.body) : null;
  requests.push({url, body});
  let data;
  if (url === '/api/books') data = {token: 'test-token', books: [{book_id: 'book-a', title: '测试教材', catalog_present: true}]};
  else if (url === '/api/books/book-a') data = {book_id: 'book-a', title: '测试教材', catalog_present: true,
      note: '全书基础工作会单独计费', units: [{unit_key: 'CH01-S01', title: '第一节', chapter: 'CH01'}],
      chapters: [{chapter_id: 'CH01', title: '第一章'}, {chapter_id: 'CH02', title: '第二章'}],
      runs: [{run_id: 'run-saved', status: 'needs_review', selectors: {kind: 'chapters', values: ['CH01']}}]};
  else if (url.includes('/events?after=')) data = {events: [], last_seq: 0};
  else if (url === '/api/runs/run-saved') data = state;
  else if (url === '/api/runs/run-saved/resume') data = {run_id:'run-saved',resumed:true};
  else if (url === '/api/plans') data = {book_id: 'book-a', target_count: 2,
      preparation: [{stage: 'concepts', scope: 'book', reason: '全书索引'}],
      estimates: [{bucket: 'prepare', unknown_reason: '尚无索引'}, {bucket: 'chapter', tokens_low: 100, tokens_high: 200}],
      capabilities: {available: [], figure_backends: []}, notes: []};
  else if (url === '/api/runs') {
    if (failSubmission) { failSubmission = false; throw new Error('模拟提交响应丢失'); }
    data = {run_id: 'run-saved', created: false};
  } else throw new Error('Unexpected request ' + url);
  return {ok: true, json: async () => data};
};
w.eval(fs.readFileSync(path.join(root, 'app.js'), 'utf8'));
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  await wait(50);
  assert.ok(w.document.querySelector('#history-list button'), '刷新后应展示历史');
  assert.match(w.document.querySelector('#run-summary').textContent, /run-saved/);
  assert.match(w.document.querySelector('#quality-summary').textContent, /待复核/);
  assert.match(w.document.querySelector('#preparation-progress').textContent, /6 \/ 6/);
  assert.match(w.document.querySelector('#selected-progress').textContent, /3 \/ 3/);
  const frame = w.document.querySelector('#preview-frame');
  assert.equal(frame.getAttribute('sandbox'), 'allow-scripts');
  assert.match(frame.src, /\/artifacts\/book-a\/run-saved\/CH01\/index.html$/);

  w.document.querySelector('#scope-mode').value = 'chapters';
  const boxes = [...w.document.querySelectorAll('#chapter-list input[type="checkbox"]')];
  assert.equal(boxes.length, 2);
  boxes.forEach(box => { box.checked = true; box.dispatchEvent(new w.Event('change')); });
  w.document.querySelector('#plan-btn').click();
  await wait(20);
  assert.deepEqual(requests.find(r => r.url === '/api/plans').body.chapters, ['CH01', 'CH02']);
  assert.match(w.document.querySelector('#plan-summary').textContent, /全书准备/);
  assert.match(w.document.querySelector('#plan-summary').textContent, /全书索引/);

  const runButton = w.document.querySelector('#run-btn');
  runButton.click(); runButton.click();
  await wait(20);
  assert.equal(requests.filter(r => r.url === '/api/runs').length, 1, '双击不能重复提交');
  runButton.click();
  await wait(40);
  const submissions = requests.filter(r => r.url === '/api/runs');
  assert.equal(submissions.length, 2);
  assert.equal(submissions[0].body.idempotency_key, submissions[1].body.idempotency_key,
               '响应丢失后的重试必须复用幂等键');
  assert.equal(JSON.parse(w.localStorage.getItem('aifs.workbench')).runId, 'run-saved');
  assert.equal(w.document.querySelector('#budget-tokens'),null);
  w.document.querySelector('#resume-btn').click();
  await wait(30);
  assert.deepEqual(requests.find(r => r.url.endsWith('/resume')).body, {}, '恢复不提交费用或重置尝试');
  state.status = 'stopping';
  w.document.querySelector('#history-list button').click();
  await wait(30);
  state.status = 'needs_review';
  await wait(1100);
  assert.match(w.document.querySelector('#run-summary').textContent, /质量待复核/, '预算收尾状态应继续轮询到终态');
  state.book_active = true;
  w.document.querySelector('#history-list button').click();
  await wait(30);
  state.book_active = false;
  await wait(1100);
  assert.equal(runButton.disabled, false, '书籍锁释放后应恢复提交按钮');
  state.status = 'running'; state.execution_complete = false; state.previews = [];
  state.progress.selected = {completed: 0, total: 14, tokens: 300, nodes: [
    {node_id: 'lesson:CH04', execution_state: 'running'},
    {node_id: 'delivery', execution_state: 'pending'},
  ]};
  w.document.querySelector('#history-list button').click();
  await wait(30);
  const progress = w.document.querySelector('#selected-progress').textContent;
  assert.match(progress, /0 \/ 14/, '未落 state 的节点也必须计入冻结计划分母');
  assert.match(progress, /lesson:CH04 · 进行中/, '已开始事件对应节点应标为进行中');
  const quality = w.document.querySelector('#quality-summary').textContent;
  assert.match(quality, /等待产物/);
  assert.doesNotMatch(quality, /可阅读预览/);
  assert.equal(frame.hidden, true);
  dom.window.close();
  process.stdout.write('control workbench DOM contracts passed\n');
})().catch(error => { console.error(error); dom.window.close(); process.exitCode = 1; });
