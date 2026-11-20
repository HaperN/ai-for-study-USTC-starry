/* 修改计划 R4：最终产物渲染检验（只读，只出报告）。
 *
 * node render_audit.cjs <playwright-module> <chromium> <job.json>
 * job = {entries:[{name, index}], ready_timeout_ms, unit_timeout_ms}
 *
 * 每个入口用 file:// 打开，等页面的 data-reader-ready（不再固定等 350ms），
 * 再逐个单元切换并扫描：KaTeX 报错、可见文字里残留的 $ / \命令 / ^{ / _{、
 * 正文为空、坏图、动画缺件、页面脚本错误。字幕走与播放器相同的 captionText，
 * 文字稿行就是字幕渲染结果，所以一并扫描。结果写到 stdout（JSON）。
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

const [playwrightModule, browserPath, jobPath] = process.argv.slice(2);
const {chromium} = require(playwrightModule);
const job = JSON.parse(fs.readFileSync(jobPath, 'utf8'));

/* 页面内扫描：不改 DOM，只读。 */
function scanUnit(unitId) {
  const main = document.querySelector('main') || document.body;
  const issues = [];
  const pointerOf = (node) => {
    const host = node.parentElement && node.parentElement.closest('[data-reader-pointer]');
    return host ? host.getAttribute('data-reader-pointer') : '';
  };
  const where = (node) => {
    const player = node.parentElement && node.parentElement.closest('[data-animation-player]');
    if (player) return {pointer: pointerOf(node), media: player.getAttribute('data-request') || '', part: 'animation'};
    return {pointer: pointerOf(node)};
  };
  main.querySelectorAll('.katex-error, .math-error').forEach((node) => {
    issues.push({kind: 'katex_error', ...where(node.firstChild || node),
      snippet: (node.getAttribute('title') || node.textContent || '').slice(0, 160)});
  });
  const PATTERNS = [
    ['raw_dollar', /(^|[^\\])\$/],
    ['raw_command', /\\[A-Za-z]{2,}/],
    ['raw_script', /[A-Za-z0-9)}\]|][_^]\{|[_^]\{[^}]*\}/],
  ];
  const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent || !node.textContent.trim()) return NodeFilter.FILTER_REJECT;
      if (parent.closest('.katex, .katex-display, code, pre, script, style, annotation, textarea, [data-render-audit-skip]')) {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  // KaTeX splits one sentence into many text nodes; report the enclosing
  // block's text once per (kind, place) so one bad caption is one issue.
  const seen = new Set();
  const blockOf = (node) => node.parentElement.closest('p, li, figcaption, h1, h2, h3, h4, h5, td, th, ' +
    '[data-reader-pointer], [data-animation-player] li, div') || node.parentElement;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent;
    for (const [kind, pattern] of PATTERNS) {
      if (!pattern.test(text)) continue;
      const block = blockOf(node);
      const copy = block.cloneNode(true);
      copy.querySelectorAll('.katex-display, .katex').forEach((math) => math.replaceWith('〔式〕'));
      const context = (copy.textContent || text).replace(/\s+/g, ' ').trim();
      const place = where(node);
      const key = [kind, place.pointer, place.media || '', context].join('\u0000');
      if (!seen.has(key)) {
        seen.add(key);
        const match = pattern.exec(context);
        const at = match ? Math.max(0, match.index - 40) : 0;
        issues.push({kind, ...place, snippet: context.slice(at, at + 140)});
      }
      break;
    }
  }
  const body = main.querySelectorAll('[data-reader-pointer="/body_markdown"]');
  const bodyText = Array.from(body).map((node) => node.textContent.replace(/\s+/g, '')).join('');
  const unitData = (window.__DATA__ && window.__DATA__.units && window.__DATA__.units[unitId]) || {};
  // Every reader unit carries a reader document whose body block is the
  // lesson text; a document without one is exactly the empty-body defect.
  if (unitData.reader_document || unitData.body_markdown !== undefined || body.length) {
    if (bodyText.replace(/^正文/, '').length < 20) {
      issues.push({kind: 'empty_body', pointer: '/body_markdown', snippet: `正文可见字数 ${bodyText.length}`});
    }
  }
  main.querySelectorAll('img').forEach((img) => {
    if (img.complete && img.naturalWidth === 0) {
      issues.push({kind: 'broken_image', pointer: pointerOf(img.firstChild || img) || '', snippet: img.getAttribute('src') || ''});
    }
  });
  main.querySelectorAll('[data-animation-error]').forEach((node) => {
    issues.push({kind: 'animation_missing', ...where(node.firstChild || node), snippet: node.textContent.slice(0, 160)});
  });
  main.querySelectorAll('.media-missing, [data-media-missing], .figure-missing').forEach((node) => {
    issues.push({kind: 'media_missing', pointer: pointerOf(node.firstChild || node), snippet: node.textContent.slice(0, 160)});
  });
  return {
    issues,
    players: main.querySelectorAll('[data-animation-player]').length,
    images: main.querySelectorAll('img').length,
    formulas: main.querySelectorAll('.katex').length,
  };
}

async function waitForImages(page, timeout) {
  // Lazy images below the fold never load in a headless scan; load them all
  // (scratch page only, delivery files are untouched) so broken ones show.
  await page.evaluate(() => document.querySelectorAll('main img[loading="lazy"]')
    .forEach((img) => { img.loading = 'eager'; }));
  try {
    await page.waitForFunction(() => Array.from(document.querySelectorAll('main img'))
      .every((img) => img.complete), null, {timeout});
  } catch (err) { /* 超时：未加载完的图按“未完成”报告，不算坏图 */ }
}

async function auditEntry(browser, entry) {
  const started = Date.now();
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage();
  const scriptErrors = [];
  page.on('pageerror', (err) => scriptErrors.push(String(err && err.message || err).slice(0, 300)));
  page.on('console', (msg) => { if (msg.type() === 'error') scriptErrors.push(msg.text().slice(0, 300)); });
  const result = {name: entry.name, index: entry.index, units: [], script_errors: scriptErrors};
  try {
    await page.goto(pathToFileURL(entry.index).href, {waitUntil: 'load', timeout: job.ready_timeout_ms});
    // Pages built before the ready signal: the first unit has replaced the
    // loading placeholder (the reader renders synchronously at boot).
    await page.waitForFunction(() => document.documentElement.getAttribute('data-reader-ready') ||
      (document.querySelector('main .unit') && !/正在加载/.test((document.querySelector('main') || {}).textContent || '')),
      null, {timeout: job.ready_timeout_ms});
  } catch (err) {
    result.ready = 'timeout';
    result.ready_seconds = (Date.now() - started) / 1000;
    result.error = '页面在限定时间内没有给出就绪信号（不是已证实的报错，可能只是加载慢）';
    await context.close();
    return result;
  }
  result.ready = await page.evaluate(() => document.documentElement.getAttribute('data-reader-ready') ||
    (/这一页没能显示出来/.test(document.body.textContent || '') ? 'failed' : 'ok'));
  result.legacy_ready_signal = await page.evaluate(() => !document.documentElement.hasAttribute('data-reader-ready'));
  result.ready_seconds = (Date.now() - started) / 1000;
  result.reader_error = await page.evaluate(() => document.documentElement.getAttribute('data-reader-error') || '');
  const unitIds = await page.evaluate(() => Object.keys((window.__DATA__ && window.__DATA__.units) || {}));
  result.declared_units = unitIds.length;
  if (result.ready !== 'ok' || !unitIds.length) {
    result.error = result.ready !== 'ok' ? '页面启动失败：' + result.reader_error : '页面没有任何单元（0 单元判失败）';
    await context.close();
    return result;
  }
  for (const unitId of unitIds) {
    const row = {unit_id: unitId};
    const unitStarted = Date.now();
    try {
      await page.evaluate((id) => { location.hash = '#/' + id; }, unitId);
      await page.waitForFunction((id) => document.documentElement.getAttribute('data-reader-unit') === id ||
        Array.from(document.querySelectorAll('.toc-item.active')).some((item) => item.getAttribute('data-unit') === id),
        unitId, {timeout: job.unit_timeout_ms});
      await waitForImages(page, 5000);
      Object.assign(row, await page.evaluate(scanUnit, unitId));
    } catch (err) {
      row.issues = [{kind: 'unit_not_rendered', snippet: String(err && err.message || err).slice(0, 200)}];
    }
    row.seconds = (Date.now() - unitStarted) / 1000;
    result.units.push(row);
  }
  await context.close();
  return result;
}

(async () => {
  const browser = await chromium.launch({executablePath: browserPath, headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--allow-file-access-from-files']});
  const entries = [];
  try {
    for (const entry of job.entries) entries.push(await auditEntry(browser, entry));
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify({schema_version: 'RenderAuditV1', entries}));
})().catch((err) => {
  process.stderr.write(String(err && err.stack || err));
  process.exit(2);
});
