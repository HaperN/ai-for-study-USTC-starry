/** Offline media acceptance. Usage: node tools/check_practice_media.cjs DELIVERY REPORT_DIR */
'use strict';
const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL, fileURLToPath} = require('node:url');
const {chromium} = require('playwright');

const [delivery, output] = process.argv.slice(2);
if (!delivery || !output) throw new Error('Usage: node check_practice_media.cjs DELIVERY REPORT_DIR');
const root = fs.realpathSync(delivery);
const reportDir = path.resolve(output);
const expected = ['mapping', 'poles', 'pv'];
const result = {delivery: root, gallery: [], workbook: [], reader: [], exams: [], questionOnly: [], errors: [], screenshots: []};
const url = name => pathToFileURL(path.join(root, name)).href;
const card = (page, row) => page.locator('[data-question-id="' + row.question_id + '"]');
const block = (scope, id) => scope.locator('[data-practice-media-id="' + id + '"]');
const selector = (node, name) => node.locator('[data-action="' + name + '"]');
const waitOptions = {timeout: 15000};

function localFile(href) {
  assert.ok(href.startsWith('file:'), 'expected local asset, got ' + href);
  const file = fs.realpathSync(fileURLToPath(href));
  const relative = path.relative(root, file);
  assert.ok(relative && !relative.startsWith('..' + path.sep) && fs.statSync(file).isFile(), file);
  return relative;
}

async function math(page) {
  const bad = await page.locator('.katex-error').allTextContents();
  assert.deepEqual(bad, [], 'KaTeX error in ' + page.url());
}

async function checkImages(scope) {
  return scope.locator('img').evaluateAll(async imgs => {
    await Promise.all(imgs.map(img => img.decode()));
    return imgs.map(img => ({src: img.currentSrc, width: img.naturalWidth}));
  }).then(rows => rows.map(row => {
    assert.ok(row.width > 0, row.src);
    return localFile(row.src);
  }));
}

async function metadata(page, section) {
  const video = section.locator('video');
  await video.waitFor({state: 'attached'});
  const handle = await video.elementHandle();
  await handle.evaluate(v => { v.preload = 'metadata'; });
  await page.waitForFunction(v => v.error || (v.readyState >= 1 && v.videoWidth > 0), handle, waitOptions);
  const meta = await handle.evaluate(v => ({
    src: v.currentSrc, poster: v.poster, width: v.videoWidth, height: v.videoHeight,
    duration: v.duration, error: v.error?.message,
  }));
  assert.ok(!meta.error && meta.width > 0 && meta.duration > 0, JSON.stringify(meta));
  return {...meta, src: localFile(meta.src), poster: localFile(meta.poster)};
}

async function play(page, section, row) {
  const handle = await section.locator('video').elementHandle();
  const control = selector(section, 'play');
  await control.click();
  await page.waitForFunction(v => !v.paused && v.currentTime > 0.25, handle, waitOptions);
  await control.click();
  await page.waitForFunction(v => v.paused, handle, waitOptions);
  const pausedAt = await handle.evaluate(v => v.currentTime);
  await page.waitForTimeout(350);
  assert.ok(Math.abs(await handle.evaluate(v => v.currentTime) - pausedAt) < 0.05, 'pause failed');
  const step = section.locator('button.animation-step[data-step="1"]');
  await step.click();
  const start = row.steps[1].start;
  const end = row.steps[1].end;
  await page.waitForFunction(({v, start, end}) => !v.paused && v.currentTime >= start && v.currentTime < end,
    {v: handle, start, end}, waitOptions);
  assert.equal(await step.getAttribute('aria-current'), 'true');
  await control.click();
  return {pausedAt, selectedStep: 2, stepTime: await handle.evaluate(v => v.currentTime)};
}

async function removed(page, handle) {
  await page.waitForFunction(v => !v.isConnected && v.paused && !v.hasAttribute('src'), handle, waitOptions);
}

async function screenshot(page, name) {
  await page.screenshot({path: path.join(reportDir, name)});
  result.screenshots.push(name);
}

function checkPublicMediaFiles(rows) {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, '媒体源/manifest.json'), 'utf8'));
  const allowed = new Set(['mp4', 'picture', 'audio', 'poster', 'captions', 'transcript', 'svg']);
  const files = {};
  for (const row of rows) {
    const reviewed = manifest.items.find(item => item.id === row.id);
    assert.equal(row.question_id, reviewed.question_id);
    assert.equal(row.base, 'practice-media/' + row.id + '/');
    assert.deepEqual(Object.keys(row.resources).sort(), Object.keys(reviewed.files).filter(key => allowed.has(key)).sort());
    for (const [kind, resource] of Object.entries(row.resources)) {
      const original = reviewed.files[kind];
      assert.equal(resource.relative_path, path.basename(original.path));
      assert.equal(resource.sha256, original.sha256);
      files[path.join(row.id, resource.relative_path)] = original.sha256;
    }
  }
  for (const base of ['assets', '教材/assets']) {
    const folder = path.join(root, base, 'practice-media');
    const found = [];
    for (const entry of fs.readdirSync(folder, {withFileTypes:true})) {
      assert.ok(entry.isDirectory(), entry.name);
      for (const file of fs.readdirSync(path.join(folder, entry.name), {withFileTypes:true})) {
        assert.ok(file.isFile(), file.name);
        const relative = path.join(entry.name, file.name);
        found.push(relative);
        const hash = createHash('sha256').update(fs.readFileSync(path.join(folder, relative))).digest('hex');
        assert.equal(hash, files[relative], base + '/' + relative);
      }
    }
    assert.deepEqual(found.sort(), Object.keys(files).sort());
  }
  result.publicPracticeFiles = Object.keys(files).sort();
}

async function audit(page) {
  await page.goto(url('完整版-习题集.html'));
  checkPublicMediaFiles(await page.evaluate(() => window.PRACTICE_MEDIA));
  const rows = await page.evaluate(() => window.PRACTICE_MEDIA.filter(r => r.kind === 'animation').map(r => ({
    id: r.id, question_id: r.question_id, steps: r.steps.map(s => ({start:s.start, end:s.end})),
    unit: (() => { const q = window.PRACTICE_DATA.questions.find(q => q.id === r.question_id); return q.mount_unit_id || q.unit_id; })(),
  })));
  assert.deepEqual(rows.map(r => r.id).sort(), [...expected].sort());
  for (const row of rows) {
    const question = card(page, row);
    const fold = question.locator('details.practice-solution');
    assert.equal(await fold.evaluate(d => d.open), false);
    assert.equal(await question.locator('video').count(), 0);
    await fold.locator(':scope > summary').click();
    const section = block(question, row.id);
    const video = await metadata(page, section);
    const playback = await play(page, section, row);
    const images = await checkImages(fold);
    assert.ok(images.some(name => name.toLowerCase().endsWith('.svg')), row.id + ' missing static SVG');
    await math(page);
    if (row.id === 'mapping') {
      const frame = await section.locator('video').elementHandle();
      const step = row.steps[1];
      const time = step.start + 0.8 * (step.end - step.start);
      await frame.evaluate((v, position) => { v.currentTime = position; }, time);
      await page.waitForFunction(({v, position}) => !v.seeking && Math.abs(v.currentTime - position) < 0.1,
        {v:frame, position:time}, waitOptions);
      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: width === 390 ? 844 : 1000});
        await section.scrollIntoViewIfNeeded();
        await section.evaluate(el => {
          el.scrollIntoView({block: 'start'});
          const toolbar = document.querySelector('.practice-toolbar');
          if (getComputedStyle(toolbar).position === 'sticky') window.scrollBy(0, -toolbar.offsetHeight);
        });
        await screenshot(page, 'practice-media-' + width + '.png');
        const bodyWidth = await page.evaluate(() => document.documentElement.scrollWidth);
        assert.ok(bodyWidth <= width + 1, 'page overflows at ' + width + ': ' + bodyWidth);
      }
      await page.setViewportSize({width: 1440, height: 1000});
    }
    const old = await section.locator('video').elementHandle();
    await selector(section, 'play').click();
    await page.waitForFunction(v => !v.paused, old, waitOptions);
    await page.locator('#practice-search').fill('no-matching-question-for-media-check');
    await removed(page, old);
    assert.equal(await page.locator('#practice-questions video').count(), 0);
    await page.locator('#practice-search').fill('');
    result.workbook.push({id:row.id, video, images, playback, filterUnloaded:true});
  }

  for (const row of rows) {
    await page.goto(url('教材/index.html') + '?practice=full#/' + encodeURIComponent(row.unit));
    const outer = page.locator('[data-practice-id="' + row.question_id + '"]');
    await outer.waitFor({state:'attached'});
    assert.equal(await outer.evaluate(d => d.open), false);
    await outer.locator(':scope > summary').click();
    const inner = outer.locator('.practice-solution');
    assert.equal(await inner.evaluate(d => d.open), false);
    assert.equal(await inner.locator('video').count(), 0);
    await inner.locator(':scope > summary').click();
    const section = block(inner, row.id);
    const video = await metadata(page, section);
    const handle = await section.locator('video').elementHandle();
    await selector(section, 'play').click();
    await page.waitForFunction(v => !v.paused && v.currentTime > 0.2, handle, waitOptions);
    await outer.locator(':scope > summary').click();
    await removed(page, handle);
    await outer.locator(':scope > summary').click();
    await section.locator('video').waitFor({state:'attached'});
    await math(page);
    const remounted = await section.locator('video').elementHandle();
    await selector(section, 'play').click();
    await page.waitForFunction(v => !v.paused && v.currentTime > 0.2, remounted, waitOptions);
    const nextUnit = rows.find(other => other.unit !== row.unit).unit;
    await page.evaluate(unit => { location.hash = '#/' + encodeURIComponent(unit); }, nextUnit);
    await removed(page, remounted);
    result.reader.push({id:row.id, unit:row.unit, video, ancestorUnloaded:true, remounted:true, navigationUnloaded:true});
  }

  const papers = '试卷包/生成试卷';
  const files = fs.readdirSync(path.join(root, papers));
  for (const name of files.filter(n => n.endsWith('-解析.html'))) {
    await page.goto(url(path.join(papers, name)));
    for (const row of rows) {
      const question = card(page, row);
      if (!await question.count()) continue;
      const solution = question.locator('.practice-solution');
      await solution.locator(':scope > summary').click();
      const section = block(question, row.id);
      const video = await metadata(page, section);
      const images = await checkImages(solution);
      result.exams.push({name, id:row.id, video, images});
    }
    await math(page);
  }
  assert.ok(result.exams.length, 'No nested solution paper exercised an animation');
  for (const name of files.filter(n => n.endsWith('-题卷.html'))) {
    await page.goto(url(path.join(papers, name)));
    const clean = await page.evaluate(() =>
      !('PRACTICE_MEDIA' in window) && !document.querySelector('video,.practice-solution,[src*="practice-media-data"]') &&
      window.PRACTICE_DATA.question_only &&
      window.PRACTICE_DATA.questions.every(q => !('answer' in q) && !('solution' in q)));
    assert.ok(clean && !fs.readFileSync(path.join(root,papers,name),'utf8').includes('PRACTICE_MEDIA'), name);
    result.questionOnly.push(name);
  }
  assert.ok(result.questionOnly.length > 0);
  for (const name of ['基础版-习题题册.html', '完整版-习题题册.html']) {
    await page.goto(url(name));
    assert.ok(await page.evaluate(() => window.PRACTICE_DATA.question_only &&
      !('PRACTICE_MEDIA' in window) && !document.querySelector('video,.practice-solution,[src*="practice-media"]')), name);
    result.questionOnly.push(name);
  }
  await page.goto(url('动画目录.html'));
  const entries = page.locator('details.practice-media-gallery-item');
  assert.equal(await entries.count(), 15);
  assert.equal(await page.locator('#textbook-animation-gallery details.practice-media-gallery-item').count(), 12);
  assert.equal(await page.locator('#practice-animation-gallery details.practice-media-gallery-item').count(), 3);
  const galleryFiles = await page.evaluate(() => {
    const items = [...window.TEXTBOOK_ANIMATIONS, ...window.PRACTICE_MEDIA.filter(r => r.kind === 'animation')];
    return Object.fromEntries(items.map(item => {
      const base = (item.asset_root || 'assets').replace(/\/$/, '') + '/' + item.base.replace(/\/$/, '') + '/';
      return [item.id, Object.fromEntries(['mp4','poster','captions','transcript'].map(kind =>
        [kind, new URL(base + item.resources[kind].relative_path, document.baseURI).href]))];
    }));
  });
  for (const entry of await entries.all()) {
    const id = (await entry.getAttribute('id')).replace(/^media-/, '');
    const summary = entry.locator(':scope > summary');
    await summary.click();
    const video = await metadata(page, entry);
    const images = await checkImages(entry);
    const row = rows.find(r => r.id === id);
    const playback = row ? await play(page, entry, row) : undefined;
    const back = entry.getByRole('link', {name: row ? /^回到习题/ : /^回到教材/});
    const href = new URL(await back.getAttribute('href'), page.url());
    const destination = localFile(href.href);
    if (row) {
      assert.equal(destination, '完整版-习题集.html');
      assert.equal(href.hash, '#practice-' + row.question_id);
    } else {
      assert.equal(destination, '教材/index.html');
      assert.ok(href.hash.startsWith('#/'));
    }
    await math(page);
    const handle = await entry.locator('video').elementHandle();
    await summary.click();
    await removed(page, handle);
    const resources = Object.fromEntries(Object.entries(galleryFiles[id]).map(([kind, href]) => [kind, localFile(href)]));
    result.gallery.push({id, origin:row ? 'practice' : 'textbook', video, images, resources, playback, back: destination + href.hash});
  }
  assert.deepEqual(result.gallery.filter(x => x.origin === 'practice').map(x => x.id).sort(), [...expected].sort());
  assert.equal(new Set(result.gallery.map(x => x.id)).size, 15);
  const first = rows[0];
  const entry = page.locator('#media-' + first.id);
  await entry.locator(':scope > summary').click();
  await entry.getByRole('link', {name: /^回到习题/}).click();
  assert.equal(new URL(page.url()).hash, '#practice-' + first.question_id);
  await card(page, first).waitFor({state:'attached'});
}

(async () => {
  fs.mkdirSync(reportDir, {recursive:true});
  let browser;
  try {
    const executablePath = process.env.AIFORSTUDY_BROWSER_EXECUTABLE;
    browser = await chromium.launch(executablePath ? {executablePath, headless:true} : {channel:'msedge', headless:true});
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.on('pageerror', e => result.errors.push(e.message));
    page.on('requestfailed', request => {
      if (request.failure()?.errorText !== 'net::ERR_ABORTED') result.errors.push(request.url() + ' ' + request.failure()?.errorText);
    });
    await audit(page);
    assert.deepEqual(result.errors, []);
    result.passed = true;
  } catch (e) {
    result.passed = false;
    result.failure = e.stack;
    process.exitCode = 1;
  } finally {
    fs.writeFileSync(path.join(reportDir,'report.json'), JSON.stringify(result,null,2));
    console.log(JSON.stringify({passed:result.passed, gallery:result.gallery.length, workbook:result.workbook.length,
      reader:result.reader.length, exams:result.exams.length, questionOnly:result.questionOnly.length,
      failure:result.failure, report:path.join(reportDir,'report.json')}));
    if (browser) await browser.close();
  }
})();
