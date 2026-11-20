/* T10 工作台反馈验收：**在真实浏览器里读工作台自己渲染出来的 DOM**。
 *
 *     node tests/browser/workbench_feedback.browser.cjs <config.json>
 *     config = {chrome, base, out, run_id, unit, click_resume}
 *
 * 为什么不用静态检查 `app.js` 里有没有那些字：
 * "界面上到底显示了什么"只有把页面打开、让 `app.js` 真的跑一遍才知道。
 * 静态 grep 会在"函数写了但没接线"时照样通过 —— 而那正是这一类需求最容易坏的方式。
 *
 * 它读的是**渲染结果**：
 *   · 每张失败卡的八个字段（`dd[data-field]`）与"还缺哪一格"的提示；
 *   · 媒体/审核时间线的分组（`#media-timeline .timeline-group[data-phase]`）；
 *   · "继续"按钮的说明文字，以及点下去之后运行摘要里出现了什么。
 *
 * 退出码：0 = 探针跑完（**判据在 Python 测试里**）；2 = 环境阻断。
 * 结果写 `<out>/workbench.json`。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const {spawn} = require('child_process');

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function launch(config) {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'aifs-workbench-'));
  const args = ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
    '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
    '--window-size=1400,1000', '--disable-background-timer-throttling',
    '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows',
    '--proxy-server=http://127.0.0.1:9', '--proxy-bypass-list=localhost,127.0.0.1,[::1]',
    '--disable-background-networking', '--disable-sync', '--no-first-run',
    '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank'];
  const child = spawn(config.chrome, args, {stdio: ['ignore', 'pipe', 'pipe']});
  let stderr = '', wsUrl = '';
  child.stderr.on('data', chunk => {
    stderr += chunk;
    const match = /ws:\/\/[^\s]+/.exec(stderr);
    if (match) wsUrl = match[0];
  });
  for (let i = 0; i < 240 && !wsUrl; i++) await sleep(50);
  if (!wsUrl) { child.kill(); return {blocked: 'Chromium 没给出调试地址：' + stderr.slice(-400)}; }
  const port = new URL(wsUrl).port;
  const targets = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json();
  const socket = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let seq = 0;
  const pending = new Map();
  const errors = [];
  const requests = [];
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const slot = pending.get(message.id);
      pending.delete(message.id);
      clearTimeout(slot.timer);
      message.error ? slot.reject(new Error(message.error.message)) : slot.resolve(message.result);
      return;
    }
    if (message.method === 'Runtime.exceptionThrown') {
      const details = message.params.exceptionDetails || {};
      errors.push((details.exception && details.exception.description) || details.text || '');
    }
    if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
      errors.push((message.params.args || []).map(a => a.value || a.description || '').join(' '));
    }
    if (message.method === 'Network.requestWillBeSent') {
      requests.push({url: message.params.request.url, method: message.params.request.method,
                     body: message.params.request.postData || null});
    }
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++seq;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 30000);
    pending.set(id, {resolve, reject, timer});
    socket.send(JSON.stringify({id, method, params}));
  });
  return {child, profile, send, errors, requests};
}

const READ = `(() => {
  const text = node => (node ? (node.textContent || '').trim() : null);
  const cards = Array.from(document.querySelectorAll('#failure-cards .failure-card')).map(card => {
    const fields = {};
    card.querySelectorAll('dd[data-field]').forEach(dd => { fields[dd.dataset.field] = text(dd); });
    return {unit: card.dataset.unit, ticket: card.dataset.ticket,
            headings: Array.from(card.querySelectorAll('h3')).map(text),
            fields, labels: Array.from(card.querySelectorAll('dt')).map(text),
            missing_note: text(card.querySelector('p.hint')),
            incomplete: card.classList.contains('incomplete')};
  });
  const groups = Array.from(document.querySelectorAll('#media-timeline .timeline-group')).map(g => ({
    phase: g.dataset.phase, title: text(g.querySelector('h3')),
    lines: Array.from(g.querySelectorAll('.stage')).map(text)}));
  return {
    cards,
    card_container_text: text(document.getElementById('failure-cards')),
    groups,
    timeline_container_text: text(document.getElementById('media-timeline')),
    resume_hint: text(document.getElementById('resume-hint')),
    resume_label: text(document.getElementById('resume-btn')),
    resume_disabled: document.getElementById('resume-btn') ? document.getElementById('resume-btn').disabled : null,
    run_summary: text(document.getElementById('run-summary')),
    quality_summary: text(document.getElementById('quality-summary')),
    preview_url: (document.getElementById('preview-frame') || {}).src || null,
    body_text: (document.body.textContent || '').slice(0, 4000)
  };
})()`;

async function main() {
  const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const out = config.out;
  fs.mkdirSync(out, {recursive: true});
  const report = {ok: false, blocked: false, base: config.base, errors: [], requests: [],
                  before: null, after_resume: null, failures: []};
  if (!config.chrome || !fs.existsSync(config.chrome)) {
    report.blocked = true;
    report.failures.push('找不到 Chromium：' + config.chrome);
    fs.writeFileSync(path.join(out, 'workbench.json'), JSON.stringify(report, null, 2));
    process.stdout.write(JSON.stringify(report) + '\n');
    process.exitCode = 2;
    return;
  }
  const launched = await launch(config);
  if (launched.blocked) {
    report.blocked = true;
    report.failures.push(launched.blocked);
    fs.writeFileSync(path.join(out, 'workbench.json'), JSON.stringify(report, null, 2));
    process.stdout.write(JSON.stringify(report) + '\n');
    process.exitCode = 2;
    return;
  }
  const {child, profile, send} = launched;
  try {
    await send('Runtime.enable');
    await send('Page.enable');
    await send('Network.enable');
    await send('Page.navigate', {url: config.base + '/'});
    await sleep(2500);
    const evaluate = async expression => {
      const result = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'evaluate failed');
      return result.result ? result.result.value : undefined;
    };
    /* 等失败卡渲染出来（服务端已经给了 failure_cards 才会出现卡片）。 */
    for (let i = 0; i < 60; i++) {
      const count = await evaluate(
        "document.querySelectorAll('#failure-cards .failure-card').length").catch(() => 0);
      if (count > 0) break;
      await sleep(300);
    }
    report.before = await evaluate(READ);
    report.errors = launched.errors.slice(0, 20);
    report.requests = launched.requests.slice(0, 200);
    if (config.click_resume) {
      await evaluate("(() => { const b = document.getElementById('resume-btn'); if (b && !b.disabled) b.click(); return true; })()");
      await sleep(2500);
      report.after_resume = await evaluate(READ);
    }
    report.ok = true;
  } catch (error) {
    report.failures.push('驱动异常：' + (error && error.stack ? error.stack : String(error)));
  } finally {
    try { child.kill(); } catch (_) { /* 已退出 */ }
    await sleep(120);
    try { fs.rmSync(profile, {recursive: true, force: true, maxRetries: 3}); } catch (_) {}
  }
  fs.writeFileSync(path.join(out, 'workbench.json'), JSON.stringify(report, null, 2));
  process.stdout.write(JSON.stringify({
    ok: report.ok, blocked: report.blocked, failures: report.failures,
    cards: report.before ? report.before.cards.length : 0,
    groups: report.before ? report.before.groups.map(g => g.phase) : [],
    workbench_json: path.join(out, 'workbench.json'),
  }, null, 1) + '\n');
  if (report.blocked) process.exitCode = 2;
  else if (!report.ok) process.exitCode = 1;
}

main().catch(error => {
  process.stdout.write(JSON.stringify({ok: false, blocked: true, error: String(error)}) + '\n');
  process.exitCode = 2;
});
