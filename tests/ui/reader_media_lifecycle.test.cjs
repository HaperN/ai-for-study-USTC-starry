/* T09 接线验收：`assets/app.js` 必须在**选中的那一节**把播放器挂上、换节时收干净。
   这是"函数写对了但没接线"那类问题的唯一判据 —— Python 侧看不到 app.js 的行为。

   与 `animations_player.test.cjs` 的分工：那边判**播放器契约**，这边判**接线**。
*/
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../..');
const assets = path.join(root, 'blocks/render/assets');
const delayed = process.argv.includes('--delayed');
const currentReader = process.argv.includes('--current-reader');

const PAGE = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>合成教材 · 讲解</title></head><body>
<div class="layout">
  <aside class="sidebar" id="sidebar"><h1 class="book-title" id="book-title">合成教材</h1>
    <p class="scope-note" id="scope-note"></p></aside>
  <main class="content" id="content"><p class="empty">正在加载…</p></main>
</div>
<div class="tooltip hidden" id="tooltip"></div>
<div class="drawer" id="drawer" aria-hidden="true"><div class="drawer-backdrop"></div>
  <div class="drawer-panel"><button class="drawer-close" id="drawer-close">×</button>
  <div id="drawer-body"></div></div></div>
</body></html>`;

function payloadFor(unitId) {
  return {
    schema_version: '1.0', unit_id: unitId, request_id: 'req-1', status: 'fulfilled',
    base: 'assets/animations/',
    resources: {
      mp4: {kind: 'mp4', relative_path: 'media/scene.mp4', mime: 'video/mp4'},
      poster: {kind: 'poster', relative_path: 'media/poster.png', mime: 'image/png'},
      captions: {kind: 'captions', relative_path: 'media/captions.vtt', mime: 'text/vtt'},
      transcript: {kind: 'transcript', relative_path: 'media/transcript.txt', mime: 'text/plain'},
    },
    steps: [{step_id: 's1', caption: '第一步', start: 0, end: 2},
            {step_id: 's2', caption: '第二步', start: 2, end: 4}],
    missing: [],
  };
}

const DATA = {
  book: '合成教材', scope: ['CH01-S01', 'CH01-S02'], data_ok: true,
  units: {
    'CH01-S01': {title: '第一节', body_markdown: '第一节正文 [[CONCEPT:C]]\n\n[[MEDIA:media-first]]\n\n后续说明', crumb: '第一章'},
    'CH01-S02': {title: '第二节', body_markdown: '第二节正文', crumb: '第一章'},
  },
  concepts: {C:{id:'C',name:'概念',brief:'定义',formal:'说明'}}, figures: {}, diagrams: {}, models: {}, tree: [], toc_gaps: [],
  concept_figures: {}, number_refs: {},
  animations: {'CH01-S01': payloadFor('CH01-S01'), 'CH01-S02': payloadFor('CH01-S02')},
};

DATA.animations['CH01-S01'].placement = {status:'resolved',anchor_id:'media-first'};

if (currentReader) {
  const marked = require('../../blocks/render/vendor/marked.min.js');
  const parser = require('../../blocks/render/assets/reader-markdown.js');
  for (const unitId of DATA.scope) {
    const first = unitId === 'CH01-S01';
    const raw = first ? '第一节正文 [概念](#reader-cccccccccccccccccccc)\n\n后续说明' : '第二节正文';
    const body = {block_id:'body',owner_id:unitId,json_pointer:'/body_markdown',
      raw_text:raw,markdown:raw,group:'body_markdown',field_label:'正文',anchor:'reader-body'};
    const blocks = parser.parseReaderField(raw,{marked,field:body}).blocks;
    const anchors = first ? ['media-first','media-second'] : ['media-other-unit'];
    const payloads = anchors.map((anchor_id,index) => ({...payloadFor(unitId),request_id:'req-'+(index+1),
      // Mirrors current balanced/json_only output: a usable location, no legacy status.
      placement:{target_pointer:'/body_markdown',anchor_id}}));
    DATA.units[unitId].reader_document = {schema_version:'ReaderDocumentV1',unit_id:unitId,blocks:[body,
      {block_id:'concept',owner_id:'C',json_pointer:'/explainables/0/what',raw_text:'概念说明',markdown:'概念说明',
        group:'explainables',field_label:'解释',anchor:'reader-cccccccccccccccccccc',collapsed:true}]};
    DATA.units[unitId].reader_media = payloads.map(item => ({request_id:item.request_id,kind:'animation',placement:item.placement}));
    DATA.units[unitId].reader_layout = {schema_version:'ReaderLayoutV1',mounts:payloads.map((item,index) => ({
      mount_id:'mount-'+index,unit_id:unitId,owner_id:unitId,target_pointer:'/body_markdown',
      source_kind:'media',source_id:item.request_id,source_hash:'fixture',anchor_id:item.placement.anchor_id,
      resolved_block_id:blocks[index].block_id,edge:'after',order_at_anchor:0}))};
    if (first) payloads.push({...payloadFor(unitId),request_id:'req-missing',placement:{anchor_id:'no-such-slot'}});
    DATA.animations[unitId] = payloads;
  }
}

const dom = new JSDOM(PAGE, {url: 'http://127.0.0.1:8765/', runScripts: 'outside-only',
                             pretendToBeVisual: true});
const w = dom.window;
// 资源请求与脚本加载：**必须都指向本页相对路径**，不许有外网/管理 API
const requested = [];
w.__DATA__ = DATA;
w.console = {warn() {}, error() {}, log() {}};
const media = w.HTMLMediaElement && w.HTMLMediaElement.prototype;
for (const name of ['play', 'pause', 'load', 'canPlayType']) {
  try { media[name] = () => (name === 'canPlayType' ? '' : Promise.resolve()); } catch (err) {}
}
const originalAppend = w.HTMLHeadElement.prototype.appendChild;
w.HTMLHeadElement.prototype.appendChild = function (node) {
  if (node && node.tagName === 'SCRIPT' && node.src) {
    requested.push(node.getAttribute('src'));
    // 模拟浏览器把 assets/animations.js 执行掉（jsdom 默认不取网络资源）
    const file = path.join(assets, 'animations.js');
    setTimeout(() => {
      if (node.getAttribute('src') === 'assets/animations.js' && fs.existsSync(file)) {
        w.eval(fs.readFileSync(file, 'utf8'));
      }
      if (typeof node.onload === 'function') node.onload();
    }, delayed ? 100 : 0);
    return node;
  }
  return originalAppend.call(this, node);
};
w.fetch = async url => { requested.push('fetch:' + url); throw new Error('页面不许发请求'); };
w.scrollTo = () => {};
w.HTMLElement.prototype.scrollIntoView = () => {};

if (currentReader) {
  w.eval(fs.readFileSync(path.join(assets, '../vendor/marked.min.js'), 'utf8'));
  w.eval(fs.readFileSync(path.join(assets, 'reader-markdown.js'), 'utf8'));
}
w.eval(fs.readFileSync(path.join(assets, 'render-core.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(assets, 'app.js'), 'utf8'));

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  if (delayed) {
    w.location.hash = '#/CH01-S02';
    await sleep(150);
    assert.ok(w.document.getElementById('content').querySelector('video'), '脚本加载时换节，新节仍须挂载');
    w.location.hash = '#/CH01-S01';
  }
  await sleep(30);
  const content = () => w.document.getElementById('content');
  // 默认停在第一节（scope[0]）
  assert.ok(content().querySelector('[data-animation-player]'),
            '第一节必须挂上播放器：' + content().innerHTML.slice(0, 200));
  const firstVideos = [...content().querySelectorAll('video')];
  assert.equal(firstVideos.length, currentReader ? 2 : 1, '同节的各个有效落点都必须挂上独立播放器');
  if (currentReader) {
    assert.deepEqual(firstVideos.map(video => video.closest('[data-media-anchor]').dataset.mediaAnchor),
      ['media-first','media-second']);
    const missing = [...content().querySelectorAll('[data-missing-animation]')];
    assert.equal(missing.length,1,'只有真正缺少落点的动画才显示定位提示');
    assert.equal(missing[0].dataset.missingAnimation,'CH01-S01/req-missing');
    assert.match(missing[0].textContent,/教学位置未能定位/);
  }
  const first = content().querySelector('video');
  assert.equal(first.closest('[data-media-anchor]').getAttribute('data-media-anchor'), 'media-first');
  assert.equal(first.getAttribute('src'), 'assets/animations/media/scene.mp4');
  assert.ok(requested.includes('assets/animations.js'), '按需加载播放器脚本：' + JSON.stringify(requested));
  assert.ok(requested.every(item => !/^https?:/.test(item) && !item.includes('/api/')),
            '不许请求外网或管理 API：' + JSON.stringify(requested));

  first.currentTime = 1.25;
  const concept = content().querySelector(currentReader ? '[href="#reader-cccccccccccccccccccc"]' : '[data-concept-id]');
  assert.ok(concept, '生产正文有可触发同节更新的引用');
  concept.dispatchEvent(new w.MouseEvent('click', {bubbles:true}));
  await sleep(30);
  assert.equal(content().querySelector('video'), first, '打开同节概念不重建video');
  assert.deepEqual([...content().querySelectorAll('video')],firstVideos,'同节解释展开不重建其他播放器');
  assert.equal(first.currentTime, 1.25, '播放进度保持');
  assert.equal(first.getAttribute('src'), 'assets/animations/media/scene.mp4');

  // 换节：上一节的播放器必须被销毁（src 清空 + DOM 摘掉）
  w.location.hash = '#/CH01-S02';
  await sleep(30);
  const videoNow = content().querySelector('video');
  assert.equal(videoNow.getAttribute('src'), 'assets/animations/media/scene.mp4');
  assert.equal(first.getAttribute('src'), null, '换节后上一节的 video 必须清掉 src');
  assert.ok(firstVideos.every(video => video.getAttribute('src') === null && !video.isConnected),
    '换节必须清理上一节的全部播放器');
  assert.equal(content().querySelectorAll('[data-missing-animation]').length,0,'上一节的缺落点提示也应清理');
  assert.equal(content().querySelectorAll('[data-animation-player]').length, 1,
               '同时只能有一个播放器实例（§T09：所选单元恰好 1）');

  // 回到没有动画的单元：必须一个播放器都不剩
  w.__DATA__.animations = {'CH01-S01': null};
  w.location.hash = '#/CH01-S01';
  await sleep(30);
  assert.equal(content().querySelectorAll('[data-animation-player]').length, 0);

  process.stdout.write(JSON.stringify({suite: 'reader_media_lifecycle', currentReader, delayed, requested,
                                       ok: true}) + '\n');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => w.close());
