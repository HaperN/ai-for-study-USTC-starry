const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const {pathToFileURL} = require('node:url');
const {chromium} = require('playwright');

const ROOT = path.resolve(__dirname, '../..');

function createPage(dir) {
  const assets = path.join(dir, 'assets');
  const clip = path.join(assets, 'clip');
  fs.mkdirSync(clip, {recursive: true});
  const scripts = {
    'blocks/render/vendor': ['marked.min.js', 'katex.min.js'],
    'blocks/render/assets': ['reader-markdown.js', 'study-math.js', 'render-core.js', 'audible-audio.js', 'animations.js'],
    'src/aiforstudy/resources/practice': ['practice-media.js', 'practice.js'],
  };
  for (const [source, names] of Object.entries(scripts)) {
    for (const name of names) {
      fs.copyFileSync(path.join(ROOT, source, name), path.join(assets, name));
    }
  }
  for (const name of ['motion.mp4', 'poster.png', 'tone.wav']) {
    fs.copyFileSync(path.join(ROOT, 'tests/fixtures/practice_media', name), path.join(clip, name));
  }
  fs.writeFileSync(path.join(clip, 'captions.vtt'), 'WEBVTT\n\n00:00.000 --> 00:03.000\nMovement\n');
  fs.writeFileSync(path.join(clip, 'transcript.txt'), 'Movement');
  const media = {
    id: 'clip', question_id: 'q1', kind: 'animation', title: '动态关系', purpose: '观察真实移动', base: 'clip/',
    steps: [
      {step_id: 'S1', start: 0, end: 1.5, caption: '运动一'},
      {step_id: 'S2', start: 1.5, end: 3, caption: '运动二'},
    ],
    resources: {
      mp4: {relative_path: 'motion.mp4'}, poster: {relative_path: 'poster.png'},
      captions: {relative_path: 'captions.vtt'}, transcript: {relative_path: 'transcript.txt'},
    },
    playback: {
      video_src: 'clip/motion.mp4',
      audio: {src: 'clip/tone.wav', data_base64: fs.readFileSync(path.join(clip, 'tone.wav')).toString('base64')},
    },
  };
  const question = {
    id: 'q1', title: '练习', stem: '题面', answer: '答案', solution: '过程', source: {},
    priority: '必会', difficulty: 1, topic: '关系',
  };
  const html = [
    '<details class="practice-inline" open><summary>习题</summary><div id="practice-questions"></div></details>',
    '<script>window.PRACTICE_DATA=' + JSON.stringify({asset_base: 'assets', questions: [question]}) +
      ';window.PRACTICE_MEDIA=' + JSON.stringify([media]) + ';</script>',
    ...Object.values(scripts).flat().map(name => '<script src="assets/' + name + '"></script>'),
  ];
  const history = [
    '<a href="/next.html">Next page</a>',
    '<script>addEventListener("pageshow", event => { window.persistedShow = event.persisted; });',
    'addEventListener("pagehide", () => { window.videoDisposedOnHide = !document.querySelector("video"); });</script>',
  ];
  const gallery = [
    '<div id="gallery"></div>',
    ...html.slice(1),
    '<script>AIFSPracticeMedia.gallery(document.getElementById("gallery"), PRACTICE_MEDIA, "assets");</script>',
  ];
  fs.writeFileSync(path.join(dir, 'gallery.html'), [...gallery, ...history].join('\n'));
  fs.writeFileSync(path.join(dir, 'next.html'), '<html><title>Next page</title></html>');
  const pagePath = path.join(dir, 'index.html');
  fs.writeFileSync(pagePath, [...html, ...history].join('\n'));
  return pathToFileURL(pagePath).href;
}

test('answer animation unloads when either disclosure closes and plays after reopening', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'practice-media-'));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.goto(createPage(dir));
  const video = page.locator('video');
  const outer = page.locator('.practice-inline > summary');
  const inner = page.locator('.practice-solution > summary');
  assert.equal(await video.count(), 0);
  await inner.click();
  await page.locator('[data-action="play"]').click();
  await page.waitForFunction(() => document.querySelector('video').currentTime > 0.2);
  const firstVideo = await video.elementHandle();

  await outer.click();
  await page.waitForFunction(() => !document.querySelector('video'));
  assert.equal(await firstVideo.evaluate(v => v.paused && !v.isConnected && !v.hasAttribute('src')), true);
  assert.equal(await page.locator('.practice-solution[open]').count(), 1);

  await outer.click();
  await video.waitFor({state: 'attached'});
  assert.equal(await video.count(), 1);
  await page.locator('[data-action="play"]').click();
  await page.waitForFunction(() => document.querySelector('video').currentTime > 0.2);
  await inner.click();
  await page.waitForFunction(() => !document.querySelector('video'));
  assert.equal(await page.locator('.practice-solution[open]').count(), 0);
});

test('open gallery and answer players return paused after a real back-forward cache restore', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'practice-media-history-'));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  createPage(dir);
  const types = {'.html':'text/html; charset=utf-8', '.js':'text/javascript; charset=utf-8', '.mp4':'video/mp4', '.png':'image/png', '.vtt':'text/vtt'};
  const server = http.createServer((req, res) => {
    const name = req.url.split('?', 1)[0];
    const file = path.join(dir, name);
    if (!name.startsWith('/') || name.includes('..') || !fs.statSync(file, {throwIfNoEntry:false})?.isFile()) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, {'content-type':types[path.extname(file)] || 'text/plain'});
    fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const executablePath = process.env.AIFORSTUDY_BROWSER_EXECUTABLE;
  const browser = await chromium.launch({
    ...(executablePath ? {executablePath} : {channel:'msedge'}),
    headless:true,
    ignoreDefaultArgs:['--disable-back-forward-cache'],
  });
  t.after(() => browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const url = 'http://127.0.0.1:' + server.address().port;
  for (const [file, details] of [['gallery.html', '.practice-media-gallery-item'], ['index.html', '.practice-solution']]) {
    await page.goto(url + '/' + file);
    const disclosure = page.locator(details);
    await disclosure.locator(':scope > summary').click();
    await disclosure.locator('video').waitFor({state:'attached'});
    assert.deepEqual(errors, []);
    await page.getByRole('link', {name:'Next page'}).click();
    await page.waitForURL(url + '/next.html');
    await page.goBack({waitUntil:'commit'});
    await page.waitForFunction(() => window.persistedShow === true, null, {timeout:5000});
    assert.equal(await page.evaluate(() => window.videoDisposedOnHide), true, file);
    assert.equal(await disclosure.evaluate(node => node.open), true, file);
    const video = disclosure.locator('video');
    assert.equal(await video.count(), 1, file);
    assert.equal(await video.evaluate(node => node.paused && node.currentTime === 0), true, file);
    await disclosure.locator('[data-action="play"]').click();
    await page.waitForFunction(() => {
      const video = document.querySelector('video');
      return video && !video.paused && video.currentTime > 0.2;
    });
    await disclosure.locator(':scope > summary').click();
    await video.waitFor({state:'detached'});
  }
});
