/* 修改计划 S 的浏览器验收（本机 Chromium，模拟笔与手指；不联网）。
 *
 * node shelf_notes.browser.cjs <playwright> <chromium> <shelf-url> <out.json>
 *
 * 1. 书架列出 9 章 + 全书入口；
 * 2. 下载一章后断网，章节仍能打开，视频能播放（经 Service Worker 缓存）；
 * 3. 模拟触控笔（带压感）画一笔，刷新后墨迹还在；换窗口宽度后墨迹与文字仍对齐；
 * 4. 手指滑动是滚动、不画线；书写模式下手指才写字；
 * 5. 导出→清空→导入后笔记一致。
 */
'use strict';
const fs = require('node:fs');
const [playwrightModule, browserPath, shelfUrl, outPath] = process.argv.slice(2);
const {chromium} = require(playwrightModule);
const results = {};
const check = (name, ok, detail) => { results[name] = {ok: !!ok, detail}; };

async function stroke(page, points, pointerType, pressure = 0.7) {
  // One CDP session per stroke: a touch sequence must start and end in the same session.
  const client = await page.context().newCDPSession(page);
  const send = async (phase, [x, y]) => {
    if (pointerType === 'touch') {
      await client.send('Input.dispatchTouchEvent', {type: phase === 'down' ? 'touchStart' : phase === 'move' ? 'touchMove' : 'touchEnd',
        touchPoints: phase === 'up' ? [] : [{x, y, radiusX: 4, radiusY: 4, force: 1, id: 1}]});
    } else {
      await client.send('Input.dispatchMouseEvent', {type: phase === 'down' ? 'mousePressed' : phase === 'move' ? 'mouseMoved' : 'mouseReleased',
        x, y, button: 'left', buttons: phase === 'up' ? 0 : 1, clickCount: 1, pointerType, force: phase === 'up' ? 0 : pressure});
    }
  };
  await send('down', points[0]);
  for (const point of points.slice(1)) await send('move', point);
  await send('up', points[points.length - 1]);
  await client.detach();
}
async function inkVsBlock(page) {
  return page.evaluate(() => {
    const g = document.querySelector('.notes-layer [data-note-id]');
    const item = window.DSHNotes.state.items.find((it) => it.kind === 'ink');
    if (!g || !item) return null;
    const attr = item.block.startsWith('p:') ? 'data-reader-pointer' : 'data-media-anchor';
    const block = [...document.querySelectorAll('[' + attr + ']')].find((n) => n.getAttribute(attr) === item.block.slice(2));
    const gr = g.getBoundingClientRect(), br = block.getBoundingClientRect();
    const scale = window.DSHNotes.state.scale;
    return {dx: (gr.left - br.left) / scale, dy: (gr.top - br.top) / scale, scale};
  });
}

(async () => {
  const browser = await chromium.launch({executablePath: browserPath, headless: true, args: ['--no-sandbox']});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, hasTouch: true});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e.message || e)));
  try {
    // 1. 书架
    await page.goto(shelfUrl, {waitUntil: 'load'});
    await page.waitForFunction(() => document.documentElement.getAttribute('data-shelf-ready'));
    const entries = await page.$$eval('.chapters li', (rows) => rows.map((r) => r.getAttribute('data-entry')));
    check('shelf_lists_9_chapters_and_book', entries.filter((e) => /^CH0\d$/.test(e)).length === 9 && entries.includes('book'), entries);
    await page.evaluate(() => navigator.serviceWorker.ready);
    await page.reload({waitUntil: 'load'});
    await page.waitForFunction(() => navigator.serviceWorker.controller && document.documentElement.getAttribute('data-shelf-ready'));

    // 2. 下载 CH04 → 断网 → 打开章节、播放视频
    await page.click('li[data-entry="CH04"] button[data-action="download"]');
    await page.waitForFunction(() => /已离线/.test(document.querySelector('li[data-entry="CH04"] .status').textContent), null, {timeout: 120000});
    const href = await page.getAttribute('li[data-entry="CH04"] a.title', 'href');
    await context.setOffline(true);
    await page.goto(new URL(href, shelfUrl).href, {waitUntil: 'load'});
    await page.waitForFunction(() => document.documentElement.getAttribute('data-reader-ready') === 'ok', null, {timeout: 60000});
    const units = await page.evaluate(() => Object.keys(window.__DATA__.units));
    let played = null;
    for (const unit of units) {
      await page.evaluate((id) => { location.hash = '#/' + id; }, unit);
      await page.waitForFunction((id) => document.documentElement.getAttribute('data-reader-unit') === id, unit);
      if (await page.$('video')) {
        played = await page.evaluate(async () => {
          const v = document.querySelector('video'); v.muted = true;
          await v.play(); await new Promise((r) => setTimeout(r, 1500));
          return {time: v.currentTime, ready: v.readyState, unit: location.hash};
        }).catch((e) => ({error: String(e)}));
        break;
      }
    }
    check('offline_chapter_opens', true, {units: units.length});
    check('offline_video_plays', played && played.time > 0.2, played);
    await context.setOffline(false);

    // 3. 笔（带压感）画一笔 → 刷新后还在 → 换宽度后对齐
    const unit = units[1] || units[0];
    await page.evaluate((id) => { location.hash = '#/' + id; }, unit);
    await page.waitForFunction((id) => document.documentElement.getAttribute('data-reader-unit') === id, unit);
    await page.evaluate(() => window.DSHNotes.ready);
    await page.evaluate(() => window.DSHNotes.setTool('pen'));
    const box = await page.evaluate(() => {
      const b = document.querySelector('[data-reader-pointer="/body_markdown"] p') || document.querySelector('[data-reader-pointer]');
      b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect();
      return {x: r.left + 40, y: r.top + Math.min(20, r.height / 2)};
    });
    await stroke(page, [[box.x, box.y], [box.x + 40, box.y + 8], [box.x + 90, box.y + 2], [box.x + 140, box.y + 10]], 'pen', 0.8);
    await page.waitForTimeout(300);
    const saved = await page.evaluate(() => window.DSHNotes.state.items.filter((i) => i.kind === 'ink').map((i) => ({pressure: i.pressure, n: i.points.length, block: i.block})));
    check('pen_stroke_saved_with_pressure', saved.length === 1 && saved[0].pressure && saved[0].n >= 4, saved);
    const before = await inkVsBlock(page);
    await page.reload({waitUntil: 'load'});
    await page.waitForFunction(() => document.documentElement.getAttribute('data-reader-ready') === 'ok');
    await page.evaluate(() => window.DSHNotes.ready);
    await page.waitForFunction(() => document.querySelector('.notes-layer [data-note-id]'));
    const afterReload = await inkVsBlock(page);
    check('ink_survives_reload', afterReload && Math.abs(afterReload.dx - before.dx) < 1 && Math.abs(afterReload.dy - before.dy) < 1, {before, afterReload});
    await page.setViewportSize({width: 760, height: 1000});
    await page.waitForTimeout(500);
    const narrow = await inkVsBlock(page);
    check('ink_aligned_after_resize', narrow && narrow.scale < 0.8 && Math.abs(narrow.dx - before.dx) < 1.5 && Math.abs(narrow.dy - before.dy) < 1.5, {before, narrow});
    const twoColumns = await page.evaluate(() => {
      const c = document.querySelector('.reader-comparison');
      return c ? getComputedStyle(c).gridTemplateColumns.split(' ').length : 'no-comparison';
    });
    check('fixed_layout_keeps_columns_when_narrow', twoColumns === 'no-comparison' || twoColumns === 2, twoColumns);
    await page.setViewportSize({width: 1440, height: 1000});
    await page.waitForTimeout(300);

    // 4. 手指滑动 = 滚动，不画线；书写模式下手指写字
    await page.evaluate(() => window.DSHNotes.setTool('pen'));
    const count = () => page.evaluate(() => window.DSHNotes.state.items.filter((i) => i.kind === 'ink').length);
    const n0 = await count();
    await stroke(page, [[700, 700], [700, 600], [700, 450]], 'touch');
    await page.waitForTimeout(300);
    const n1 = await count();
    check('finger_scrolls_not_draws', n1 === n0, {n0, n1});
    await page.evaluate(() => window.DSHNotes.setWriteMode(true));
    const fbox = await page.evaluate(() => {
      const b = document.querySelector('[data-reader-pointer]'); b.scrollIntoView({block: 'center'});
      const r = b.getBoundingClientRect(); return {x: r.left + 60, y: r.top + 12};
    });
    await stroke(page, [[fbox.x, fbox.y], [fbox.x + 50, fbox.y + 6], [fbox.x + 100, fbox.y]], 'touch');
    await page.waitForTimeout(300);
    const n2 = await count();
    check('finger_draws_in_write_mode', n2 === n1 + 1, {n1, n2});
    await page.evaluate(() => window.DSHNotes.setWriteMode(false));

    // 5. 导出 → 清空 → 导入
    const exported = await page.evaluate(() => window.DSHNotes.exportNotes());
    await page.evaluate(() => new Promise((resolve) => {
      const r = indexedDB.open('aifs-notes', 1);
      r.onsuccess = () => { const t = r.result.transaction('items', 'readwrite'); t.objectStore('items').clear(); t.oncomplete = resolve; };
    }));
    const empty = await page.evaluate(() => window.DSHNotes.count());
    const imported = await page.evaluate((p) => window.DSHNotes.importNotes(p), exported);
    const again = await page.evaluate(() => window.DSHNotes.exportNotes());
    const same = JSON.stringify(exported.items) === JSON.stringify(again.items);
    check('export_import_round_trip', empty === 0 && imported === exported.items.length && same,
          {exported: exported.items.length, empty, imported});
  } catch (err) {
    check('script', false, String(err && err.stack || err));
  } finally {
    check('no_page_errors', errors.length === 0, errors.slice(0, 5));
    fs.writeFileSync(outPath, JSON.stringify(results, null, 1));
    await browser.close();
  }
})();
