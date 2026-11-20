/* T09 播放器验收（jsdom）：属性白名单、控件、步骤 seek ≤0.2s、缺资源不静默消失、
   两个视口下文字稿／控件可用、**一个管理 API 都不许调**。

为什么用 jsdom 而不是真浏览器：这一层要判的是**播放器的行为契约**（DOM 属性、
事件、seek 数值、销毁），jsdom 能确定性地断言；真实 Chromium 的播放／换节停止
与不透明 origin 字幕读取是 T10 的事（`tests/browser/animation_player.browser.cjs`）。
这里**不**假装覆盖真实解码播放。
*/
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const asset = path.resolve(__dirname, '../../blocks/render/assets/animations.js');

function makeDom() {
  const dom = new JSDOM('<!doctype html><html><body><main id="content"></main></body></html>', {
    url: 'http://127.0.0.1:8765/', runScripts: 'outside-only', pretendToBeVisual: true,
  });
  const w = dom.window;
  // jsdom 没实现这些媒体方法，调用只会打印 "Not implemented"；本层判的是播放器**行为**，
  // 真实解码播放留给 T10 的 Chromium 验收，所以这里补成空实现。
  const media = w.HTMLMediaElement && w.HTMLMediaElement.prototype;
  if (media) {
    for (const name of ['play', 'pause', 'load', 'canPlayType']) {
      try { media[name] = () => (name === 'canPlayType' ? '' : Promise.resolve()); } catch (err) {}
    }
  }
  // **任何管理 API 调用都要炸**：播放器一个也不许发（计划 §T09）。
  const calls = [];
  w.fetch = async url => { calls.push(String(url)); throw new Error('播放器不许发请求：' + url); };
  w.XMLHttpRequest = function () { calls.push('XMLHttpRequest'); throw new Error('播放器不许发请求'); };
  w.eval(fs.readFileSync(asset, 'utf8'));
  return {dom, w, calls};
}

/** 伪造 media 元素行为：jsdom 不实现 HTMLMediaElement 的播放与 seek。 */
function fakeMedia(w, root) {
  const video = root.querySelector('video');
  assert.ok(video, '必须有 video 标签');
  const seen = {currentTime: 0, play: 0, pause: 0, paused: true, duration: 4, sources: []};
  Object.defineProperty(video, 'currentTime', {
    get() { return seen.currentTime; },
    set(value) { seen.currentTime = value; video.dispatchEvent(new w.Event('timeupdate')); },
    configurable: true,
  });
  Object.defineProperty(video, 'duration', {get() { return seen.duration; }, configurable: true});
  Object.defineProperty(video, 'paused', {get() { return seen.paused; }, configurable: true});
  video.play = () => { seen.play += 1; seen.paused = false; video.dispatchEvent(new w.Event('play')); return Promise.resolve(); };
  video.pause = () => { seen.pause += 1; seen.paused = true; video.dispatchEvent(new w.Event('pause')); };
  video.load = () => {};
  return seen;
}

async function testStepClickStopsAtBoundaryAndCanReplayOrAdvance() {
  const {dom, w} = makeDom();
  const root = w.document.getElementById('content');
  const dispose = w.DSHAnimations.mountAnimations(root, payload());
  const player = root.querySelector('[data-animation-player]');
  const state = fakeMedia(w, player), video = player.querySelector('video');
  const first = player.querySelector('.animation-steps [data-step="0"]');
  first.click();
  assert.equal(state.paused, false, '点击步骤应立即播放该段，不是只跳转');
  video.currentTime = 1.9;
  assert.equal(state.paused, false, '第一步未结束时继续播放');
  assert.equal(first.getAttribute('aria-current'), 'true', '不要提前高亮下一步');
  video.currentTime = 2.25;
  assert.equal(state.paused, true, '时间更新越过边界时须暂停');
  assert.equal(video.currentTime, 2, '越界后停回本步结束位置');
  assert.equal(first.getAttribute('aria-current'), 'true', '完成后仍显示刚播放的步骤');
  assert.match(player.querySelector('.animation-action-title').textContent, /第一步：x、y/);
  player.querySelector('[data-action="play"]').click();
  assert.equal(video.currentTime, 0, '本步完成后主按钮可重播本步');
  assert.equal(state.paused, false);
  player.querySelector('[data-action="next"]').click();
  assert.equal(video.currentTime, 2, '下一步从第二步起点播放');
  assert.equal(state.paused, false);
  assert.equal(player.querySelector('[data-action="next"]').disabled, true, '末步禁用下一步');
  video.currentTime = 4;
  assert.equal(state.paused, true);
  assert.match(player.querySelector('.animation-action-title').textContent, /第二步：u/);
  dispose(); dom.window.close();
}

async function testResumeAndContinuousPlaybackRemainDistinct() {
  const {dom, w} = makeDom(); const root = w.document.getElementById('content');
  const dispose = w.DSHAnimations.mountAnimations(root, payload());
  const player = root.querySelector('[data-animation-player]');
  const state = fakeMedia(w, player), video = player.querySelector('video');
  player.querySelector('[data-action="play"]').click();
  video.currentTime = 0.8;
  player.querySelector('[data-action="play"]').click();
  player.querySelector('[data-action="play"]').click();
  assert.equal(video.currentTime, 0.8, '暂停后继续本步不应从头播放');
  const speed = player.querySelector('[data-action="speed"]');
  speed.value = '2'; speed.dispatchEvent(new w.Event('change'));
  video.currentTime = 2.1;
  assert.equal(state.paused, true, '倍速仍按媒体时间边界暂停');
  player.querySelector('[data-action="play-all"]').click();
  video.currentTime = 2.8;
  assert.equal(state.paused, false, '连续播放不沿用之前的单步边界');
  assert.equal(player.querySelector('.animation-steps [data-step="1"]').getAttribute('aria-current'), 'true');
  player.querySelector('[data-action="play"]').click();
  assert.equal(state.paused,true);
  player.querySelector('[data-action="play"]').click();
  assert.equal(video.currentTime,2.8,'统一主按钮继续整段播放，不跳回当前步骤起点');
  assert.equal(player.querySelector('[data-action="play-all"]').getAttribute('aria-pressed'),'true');
  video.currentTime=4.1;
  assert.equal(state.paused,false,'继续整段播放不重新启用单步停止逻辑');
  player.querySelector('.animation-transcript-time[data-step="0"]').click();
  assert.equal(video.currentTime, 0, '文字稿时间按钮与步骤按钮具有同样行为');
  video.currentTime = 2.1;
  assert.equal(state.paused, true, '从文字稿选步后重新按步暂停');
  dispose(); dom.window.close();
}

async function testStepBoundsFallBackToNextStartAndActualDuration() {
  const {dom, w} = makeDom(); const root = w.document.getElementById('content');
  const dispose = w.DSHAnimations.mountAnimations(root, payload({steps: [
    {step_id:'s1', caption:'首步', start:0, end:null},
    {step_id:'s2', caption:'末步', start:2, end:99},
    {step_id:'invalid', caption:'超出实际视频', start:20, end:30},
  ]}));
  const player = root.querySelector('[data-animation-player]');
  const state = fakeMedia(w, player), video = player.querySelector('video');
  player.querySelector('.animation-steps [data-step="0"]').click();
  video.currentTime = 2.1;
  assert.equal(state.paused, true, '未提供 end 时用下一步起点');
  assert.equal(video.currentTime, 2);
  player.querySelector('.animation-steps [data-step="1"]').click();
  video.currentTime = 4.1;
  assert.equal(state.paused, true, '声明结束过长时用视频实际时长');
  assert.equal(video.currentTime, 4);
  const played = state.play;
  player.querySelector('.animation-steps [data-step="2"]').click();
  assert.equal(state.play, played, '超出实际视频的步骤不开始无界播放');
  assert.match(player.querySelector('[data-animation-status]').textContent, /时间|范围|边界/);
  assert.equal(player.querySelector('[data-action="play-all"]').disabled, false, '仍保留整段播放入口');
  dispose(); dom.window.close();
}

async function testActiveStepLoopAndDisposalArePerPlayer() {
  const {dom,w} = makeDom(), frames = new Map(); let frameId = 0;
  w.requestAnimationFrame = callback => {frames.set(++frameId, callback); return frameId;};
  w.cancelAnimationFrame = id => frames.delete(id);
  const root = w.document.getElementById('content');
  const dispose = w.DSHAnimations.mountAnimations(root, payload());
  const otherRoot = w.document.createElement('div'); root.appendChild(otherRoot);
  const disposeOther = w.DSHAnimations.mountAnimations(otherRoot, payload());
  const player = root.querySelector('[data-animation-player]'), state = fakeMedia(w, player);
  const other = otherRoot.querySelector('[data-animation-player]'), otherState = fakeMedia(w, other);
  player.querySelector('[data-action="play"]').click();
  other.querySelector('[data-action="play"]').click();
  // Real timeupdate events may be sparse; a display-frame check must stop only this player.
  state.currentTime = 2.15;
  const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn());
  assert.equal(state.paused, true, '逐帧检查在 timeupdate 尚未触发时也能停止');
  assert.equal(otherState.paused, false, '另一个播放器不受影响');
  assert.equal(frames.size, 1, '已结束的播放器不再轮询');
  disposeOther(); assert.equal(frames.size, 0, 'dispose 取消正在等待的逐帧检查');
  const plays = otherState.play;
  other.querySelector('[data-action="play"]').click();
  assert.equal(otherState.play, plays, '旧弹窗或旧章节 DOM 不能重启播放');
  dispose(); dom.window.close();
}

async function testLastStepWaitsForMetadataWithoutReloadLoop() {
  const {dom,w} = makeDom(), root = w.document.getElementById('content');
  const dispose = w.DSHAnimations.mountAnimations(root, payload({steps:[
    {step_id:'last', caption:'最后一步的完整说明', start:1, end:null},
  ]}));
  const player = root.querySelector('[data-animation-player]'), state = fakeMedia(w, player);
  const video = player.querySelector('video'); let loads = 0;
  video.load = () => loads++;
  state.duration = NaN;
  player.querySelector('[data-action="play"]').click();
  assert.equal(state.play, 0, '未知结束边界不贸然开始无限单步播放');
  assert.equal(loads, 1, '仅加载元数据以获得末步实际时长');
  state.duration = 4;
  video.dispatchEvent(new w.Event('loadedmetadata'));
  assert.equal(state.paused, false, '元数据就绪后播放用户所选的步骤');
  assert.equal(video.currentTime, 1);
  assert.match(player.querySelector('.animation-action-title').textContent, /最后一步的完整说明/);
  video.currentTime = 4;
  assert.equal(state.paused, true);
  state.duration = NaN;
  player.querySelector('[data-action="play"]').click();
  const beforeMetadata = loads;
  video.dispatchEvent(new w.Event('loadedmetadata'));
  assert.equal(loads, beforeMetadata, '元数据无有效时长时不反复 reload');
  assert.match(player.querySelector('[data-animation-status]').textContent, /连续播放/);
  dispose(); dom.window.close();
}

async function testNoStepsStillPlaysAndPendingMetadataCanBeCancelled() {
  const {dom,w} = makeDom(), root = w.document.getElementById('content');
  let dispose = w.DSHAnimations.mountAnimations(root, payload({steps:[]}));
  let player = root.querySelector('[data-animation-player]'), state = fakeMedia(w, player);
  player.querySelector('[data-action="play"]').click();
  assert.equal(state.paused, false, '无步骤的旧视频仍可整段播放');
  assert.equal(player.querySelector('[data-action="next"]'), null);
  dispose();
  dispose = w.DSHAnimations.mountAnimations(root, payload({steps:[{caption:'末步',start:0,end:null}]}));
  player = root.querySelector('[data-animation-player]'); state = fakeMedia(w, player);
  state.duration = NaN;
  // A real paused video does not emit another pause event when pause() is called.
  player.querySelector('video').pause = () => { state.pause += 1; };
  player.querySelector('[data-action="play"]').click();
  assert.equal(player.querySelector('[data-action="play"]').textContent, '暂停');
  player.querySelector('[data-action="play"]').click();
  assert.equal(player.querySelector('[data-action="play"]').textContent, '播放这一步',
    '取消元数据等待后，主按钮立即恢复，不依赖未发生的 pause 事件');
  state.duration = 4;
  player.querySelector('video').dispatchEvent(new w.Event('loadedmetadata'));
  assert.equal(state.play, 0, '读者暂停后，迟到的元数据不能重启播放');
  dispose(); dom.window.close();
}

function payload(overrides = {}) {
  return {
    schema_version: '1.0', unit_id: 'CH01-S01', request_id: 'req-saddle-1',
    status: 'fulfilled',
    resources: {
      mp4: {kind: 'mp4', relative_path: 'media/scene.mp4', mime: 'video/mp4', bytes: 3256,
            sha256: 'sha256:' + '1'.repeat(64)},
      poster: {kind: 'poster', relative_path: 'media/poster.png', mime: 'image/png', bytes: 90,
               sha256: 'sha256:' + '2'.repeat(64)},
      captions: {kind: 'captions', relative_path: 'media/captions.vtt',
                 mime: 'text/vtt; charset=utf-8', bytes: 80, sha256: 'sha256:' + '3'.repeat(64)},
      transcript: {kind: 'transcript', relative_path: 'media/transcript.txt',
                   mime: 'text/plain; charset=utf-8', bytes: 40, sha256: 'sha256:' + '4'.repeat(64)},
    },
    steps: [
      {step_id: 's1', caption: '第一步：x、y 是两个方向', start: 0.0, end: 2.0},
      {step_id: 's2', caption: '第二步：u 是高度', start: 2.0, end: 4.0},
    ],
    base: 'assets/animations/',
    ...overrides,
  };
}

async function testComparisonControlsUseActualActionsAndOnePlayToggle() {
  const {dom,w}=makeDom(), root=w.document.getElementById('content');
  const data=payload({request_id:'unrelated-book-action',steps:[
    {step_id:'rotate',title:'转动向量',caption:'保持长度不变，转动至指定方向。',start:0,end:2},
    {step_id:'scale',label:'缩放长度',caption:'将向量长度乘以正数。',start:2,end:4},
    {step_id:'short',caption:'比较终点',start:4,end:6},
    {step_id:'long',caption:'这是一段需要完整保留的较长说明，不能截断公式或用固定教学案例中的标题替换。',start:6,end:8},
  ]});
  const original=JSON.stringify(data), dispose=w.DSHAnimations.mountAnimations(root,data);
  const player=dispose.player;
  assert.equal(player,root.querySelector('[data-animation-player]'),'兼容的挂载句柄应暴露当前播放器');
  const state=fakeMedia(w,player), video=player.querySelector('video');
  state.duration=8;
  assert.equal(player,root.querySelector('[data-animation-player]'));
  assert.deepEqual([...player.querySelectorAll('.animation-step-label')].map(n=>n.textContent),
    ['转动向量','缩放长度','比较终点','第 4 步']);
  assert.equal(player.querySelector('.animation-stage-title').textContent,'转动向量');
  assert.equal(player.querySelector('.animation-action-title').textContent,'转动向量');
  assert.equal(player.querySelector('[data-action="pause"]'),null,'主按钮本身负责暂停');
  assert.ok(player.querySelector('.animation-action-row [data-action="play-all"]'));
  assert.ok(player.querySelector('.animation-settings [data-action="speed"]'));
  assert.ok(player.querySelector('.animation-settings [data-action="captions"]'));
  assert.equal(player.querySelector('.animation-settings').open,false);
  const play=player.querySelector('[data-action="play"]');play.click();
  assert.equal(play.getAttribute('aria-label'),'暂停');
  video.currentTime=.8;play.click();assert.equal(state.paused,true);
  play.click();assert.equal(video.currentTime,.8);
  player.querySelector('[data-action="replay"]').click();assert.equal(video.currentTime,0);
  player.querySelector('[data-action="next"]').click();
  assert.equal(player.querySelector('.animation-stage-title').textContent,'缩放长度');
  video.currentTime=4.1;assert.equal(state.paused,true);
  assert.match(player.querySelector('[data-animation-status]').textContent,/本步已结束/);
  assert.equal(JSON.stringify(data),original,'标题与字幕只用于展示，不改载荷');
  dispose();dom.window.close();
}

async function testPublicStepSelectionAndScopedChangeEvents() {
  const {dom,w}=makeDom(), root=w.document.getElementById('content'), events=[];
  root.addEventListener('stepchange',e=>events.push({target:e.target,...e.detail}));
  const dispose=w.DSHAnimations.mountAnimations(root,payload());
  assert.equal(typeof dispose,'function');assert.equal(typeof dispose.selectStep,'function');
  const player=dispose.player,state=fakeMedia(w,player),video=player.querySelector('video');
  assert.equal(dispose.selectStep(1,{autoplay:false}),true);
  assert.equal(video.currentTime,2);assert.equal(state.play,0);assert.equal(state.paused,true);
  assert.deepEqual({...events.at(-1),target:undefined},{target:undefined,unit_id:'CH01-S01',request_id:'req-saddle-1',step_id:'s2',index:1});
  assert.equal(events.at(-1).target,player,'事件从当前播放器冒泡，供正文按身份过滤');
  const count=events.length;
  for(const index of [-1,2,1.5,'1',NaN])assert.equal(dispose.selectStep(index),false);
  assert.equal(events.length,count);assert.equal(video.currentTime,2);
  assert.equal(dispose.selectStep(0),true);assert.equal(state.paused,false);
  player.querySelector('[data-action="play-all"]').click();video.currentTime=2.5;
  assert.equal(events.at(-1).step_id,'s2');assert.equal(state.paused,false);
  const finalCount=events.length,plays=state.play;dispose();
  assert.equal(dispose.selectStep(0),false);assert.equal(events.length,finalCount);assert.equal(state.play,plays);
  dom.window.close();
}

async function testExternalPausedSelectionSurvivesMetadataAndFullscreenIsLocal() {
  const {dom,w,calls}=makeDom(), root=w.document.getElementById('content');
  const dispose=w.DSHAnimations.mountAnimations(root,payload({steps:[{step_id:'final',start:1,end:null,caption:'最后一步'}]}));
  const player=dispose.player,state=fakeMedia(w,player),video=player.querySelector('video');
  state.duration=NaN;dispose.selectStep(0,{autoplay:false});
  state.duration=4;video.dispatchEvent(new w.Event('loadedmetadata'));
  assert.equal(video.currentTime,1);assert.equal(state.play,0,'等待元数据不能丢失autoplay=false');
  const button=player.querySelector('[data-action="fullscreen"]');
  let full=null;Object.defineProperty(w.document,'fullscreenElement',{get:()=>full,configurable:true});
  player.requestFullscreen=async()=>{full=player;w.document.dispatchEvent(new w.Event('fullscreenchange'));};
  w.document.exitFullscreen=async()=>{full=null;w.document.dispatchEvent(new w.Event('fullscreenchange'));};
  button.click();await Promise.resolve();assert.equal(full,player);assert.equal(button.getAttribute('aria-label'),'退出全屏');
  for(const action of ['play','replay','next','play-all'])
    assert.ok(full.contains(player.querySelector('[data-action="'+action+'"]')),'全屏必须保留 '+action+' 控件');
  full.querySelector('[data-action="play"]').click();assert.equal(state.paused,false);
  full.querySelector('[data-action="play"]').click();assert.equal(state.paused,true,'全屏内可暂停');
  button.click();await Promise.resolve();assert.equal(full,null);assert.equal(button.getAttribute('aria-label'),'全屏查看动画');
  player.requestFullscreen=async()=>{throw new Error('denied');};button.click();await Promise.resolve();await Promise.resolve();
  assert.match(player.querySelector('[data-animation-status]').textContent,/全屏/);
  assert.deepEqual(calls,[]);dispose();button.click();await Promise.resolve();assert.equal(full,null);
  dom.window.close();
}

async function testMountRendersWhitelistedTagsAndControls() {
  const {w} = makeDom();
  const root = w.document.getElementById('content');
  const dispose = w.DSHAnimations.mountAnimations(root, payload());
  assert.equal(typeof dispose, 'function', 'mountAnimations 必须返回 dispose');

  const player = root.querySelector('[data-animation-player]');
  assert.ok(player, '播放器必须挂到一个显式容器上');
  const video = player.querySelector('video');
  assert.equal(video.getAttribute('src'), 'assets/animations/media/scene.mp4');
  assert.equal(video.getAttribute('poster'), 'assets/animations/media/poster.png');
  // 有 poster 时**不预加载视频**：读者没点播放就不该拉整段 MP4（离线/流量都受影响），
  // 封面已经能表达画面。没有 poster 时才退到只取元数据。
  assert.equal(video.getAttribute('preload'), 'none');
  assert.equal(video.getAttribute('controls'), null,
               '自带控件与我们的按钮重复，必须显式关掉');
  // **属性白名单**：以播放器自己导出的 ATTRS 为准（一处定义，测试不另抄一份），
  // 且任何事件属性（on*）永远不许出现。
  const allowed = new Set([...w.DSHAnimations.ATTRS, 'playsinline']);
  for (const node of player.querySelectorAll('*')) {
    for (const attr of node.attributes || []) {
      assert.ok(!/^on/i.test(attr.name), '不许出现事件属性：' + attr.name);
      assert.ok(allowed.has(attr.name), '属性不在白名单：' + attr.name);
    }
  }
  assert.ok(allowed.has('src') && allowed.has('poster') && allowed.has('kind'));
  assert.ok(!allowed.has('onerror'));
  const track = player.querySelector('track');
  assert.equal(track.getAttribute('kind'), 'captions');
  assert.equal(track.getAttribute('src'), 'assets/animations/media/captions.vtt');
  assert.equal(track.getAttribute('srclang'), 'zh');
  assert.equal(track.getAttribute('default'), '');

  // 步骤可见；文字稿保留在 DOM 中，正常播放时默认收起。
  // 步骤按钮只数工具栏里那排（文字稿行也带 data-step，别混起来数）
  const steps = player.querySelectorAll('.animation-steps [data-step]');
  assert.equal(steps.length, 2);
  assert.equal(player.querySelectorAll('.animation-transcript-row').length, 2);
  assert.match(player.textContent, /第一步：x、y 是两个方向/);
  assert.match(player.textContent, /第二步：u 是高度/);
  const transcript = player.querySelector('[data-animation-transcript]');
  assert.ok(transcript && transcript.textContent.includes('u 是高度'));
  assert.equal(transcript.hidden, true, '正常动画的文字稿默认收起');
  assert.equal(player.querySelector('[data-action="transcript"]').getAttribute('aria-expanded'), 'false');
  dispose();
}

async function testPlaybackErrorRevealsTranscript() {
  const {dom, w} = makeDom();
  const root = w.document.getElementById('content');
  const dispose = w.DSHAnimations.mountAnimations(root, payload());
  const player = root.querySelector('[data-animation-player]');
  const button = player.querySelector('[data-action="transcript"]');
  const transcript = player.querySelector('[data-animation-transcript]');
  assert.equal(transcript.hidden, true);
  button.click();
  assert.equal(transcript.hidden, false);
  assert.equal(button.getAttribute('aria-expanded'), 'true');
  button.click();
  assert.equal(transcript.hidden, true);
  player.querySelector('video').dispatchEvent(new w.Event('error'));
  assert.equal(transcript.hidden, false, '视频错误时展示可读文字稿');
  assert.equal(button.getAttribute('aria-expanded'), 'true');
  assert.ok(player.querySelector('[data-animation-error]'));
  button.click();
  assert.equal(transcript.hidden, false, '失败时保留文字替代内容');
  dispose();
  dom.window.close();
}

async function testPlaybackSpeedStepSeekAndDispose() {
  const {w} = makeDom();
  const root = w.document.getElementById('content');
  const dispose = w.DSHAnimations.mountAnimations(root, payload());
  const player = root.querySelector('[data-animation-player]');
  const state = fakeMedia(w, player);
  const video = player.querySelector('video');

  // 播放 / 暂停
  player.querySelector('[data-action="play"]').click();
  assert.equal(state.play, 1, '点播放要真的调用 play()');
  player.querySelector('[data-action="play"]').click();
  assert.equal(state.pause, 1, '点暂停要真的调用 pause()');

  // 倍速
  const speed = player.querySelector('[data-action="speed"]');
  assert.ok(speed, '必须有倍速控件');
  speed.value = '1.5';
  speed.dispatchEvent(new w.Event('change'));
  assert.equal(player.querySelector('video').playbackRate, 1.5);

  // 步骤跳转：**误差 ≤ 0.2s**
  const second = player.querySelectorAll('.animation-steps [data-step]')[1];
  second.click();
  assert.ok(Math.abs(video.currentTime - 2.0) <= 0.2,
            `步骤 seek 误差必须 ≤0.2s，实际 ${video.currentTime} vs 2.0`);
  assert.equal(second.getAttribute('aria-current'), 'true');

  // 字幕开关（修改计划 R1）：原生轨道只加载不绘制（hidden），由播放器字幕层显示；
  // 关掉时轨道 disabled、字幕层隐藏。
  const track = player.querySelector('track');
  const toggle = player.querySelector('[data-action="captions"]');
  const layer = player.querySelector('[data-animation-caption]');
  assert.ok(layer, 'player must own a caption layer');
  assert.equal(track.mode, 'hidden');
  toggle.click();
  assert.equal(track.mode, 'disabled');
  assert.equal(layer.hidden, true);
  toggle.click();
  assert.equal(track.mode, 'hidden');

  // 文字稿开关（可见/隐藏都要是真的，不是改个装饰类）
  const button = player.querySelector('[data-action="transcript"]');
  const host = player.querySelector('[data-animation-transcript]');
  const wasHidden = host.hasAttribute('hidden');
  button.click();
  assert.equal(host.hasAttribute('hidden'), !wasHidden, '点文字稿开关必须真的隐藏/展开');

  // dispose：pause + 清 src + 摘监听（返回的 dispose 必须做到）
  const before = state.pause;
  dispose();
  assert.equal(state.pause, before + 1, 'dispose 必须 pause()');
  assert.equal(video.getAttribute('src'), null, 'dispose 必须清掉 src');
  assert.equal(root.querySelector('[data-animation-player]'), null, 'dispose 必须摘掉 DOM');
  // 监听必须真的摘掉：拿**已脱离**的旧节点再点一次，不许再改到媒体。
  const frozen = video.currentTime;
  player.querySelectorAll('.animation-steps [data-step]')[0]?.click?.();
  player.querySelector('[data-action="play"]')?.click?.();
  assert.equal(video.currentTime, frozen, 'dispose 之后旧节点上的点击不该再改媒体');
}

async function testMissingResourcesShowErrorAndTranscript() {
  const {w} = makeDom();
  const root = w.document.getElementById('content');
  const broken = payload({resources: {
    transcript: {kind: 'transcript', relative_path: 'media/transcript.txt',
                 mime: 'text/plain; charset=utf-8', bytes: 40, sha256: 'sha256:' + '4'.repeat(64)},
  }, missing: ['mp4', 'poster', 'captions']});
  const dispose = w.DSHAnimations.mountAnimations(root, broken);
  const player = root.querySelector('[data-animation-player]');
  const error = player.querySelector('[data-animation-error]');
  assert.ok(error && !error.hasAttribute('hidden'), '缺资源必须显示错误，不许静默消失');
  assert.match(error.textContent, /mp4/);
  assert.ok(player.querySelector('[data-animation-transcript]'), '缺资源时文字稿仍要在');
  const video = player.querySelector('video');
  assert.ok(!video || !video.getAttribute('src'), '缺 mp4 时不许挂一个空 src 的 video');
  dispose();
}

async function testPlayerNeverCallsManagementApis() {
  const {w, calls} = makeDom();
  const root = w.document.getElementById('content');
  const dispose = w.DSHAnimations.mountAnimations(root, payload());
  const player = root.querySelector('[data-animation-player]');
  fakeMedia(w, player);
  player.querySelector('[data-action="play"]').click();
  player.querySelectorAll('.animation-steps [data-step]')[0].click();
  player.querySelector('[data-action="captions"]').click();
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.deepEqual(calls, [], '播放器一个请求都不许发：' + JSON.stringify(calls));
  // 载荷里也不该带管理 API 地址
  const player2 = w.document.createElement('div');
  w.document.body.appendChild(player2);
  const dispose2 = w.DSHAnimations.mountAnimations(player2, payload());
  assert.equal(player2.querySelector('[data-action]').closest('[data-api]'), null);
  dispose(); dispose2();
}

async function testBothViewportsKeepControlsAndTranscriptUsable() {
  /* jsdom 不做布局，所以这里断言的是**可用性契约**：
     390px 与 1280px 两个视口下 DOM 与控件都在、文字稿可展开、没有 fixed 宽度锁死。 */
  for (const [width, height] of [[390, 844], [1280, 720]]) {
    const {dom, w} = makeDom();
    Object.defineProperty(w, 'innerWidth', {value: width, configurable: true});
    Object.defineProperty(w, 'innerHeight', {value: height, configurable: true});
    const root = w.document.getElementById('content');
    const dispose = w.DSHAnimations.mountAnimations(root, payload());
    const player = root.querySelector('[data-animation-player]');
    assert.ok(player.querySelector('[data-action="play"]'), `${width}px 下要有播放控件`);
    assert.ok(player.querySelector('[data-action="speed"]'), `${width}px 下要有倍速`);
    assert.ok(player.querySelector('[data-action="transcript"]'), `${width}px 下要有文字稿开关`);
    assert.equal(player.querySelectorAll('.animation-steps [data-step]').length, 2,
                 `${width}px 下步骤要都在`);
    const transcript = player.querySelector('[data-animation-transcript]');
    assert.ok(transcript.textContent.trim().length > 0, `${width}px 下文字稿非空`);
    assert.equal(player.hasAttribute('data-viewport-fixed'), false);
    // 控件容器必须允许换行（390px 下按钮不会溢出被裁掉）
    const controls = player.querySelector('[data-animation-controls]');
    assert.ok(controls, '控件容器要有显式钩子');
    assert.ok(/wrap/.test(controls.getAttribute('class') || '') || controls.children.length <= 8);
    dom.window.close();
    dispose();
  }
}

function loadKatex(w) {
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../blocks/render/vendor/katex.min.js'), 'utf8'));
}
function mathSources(node) {
  return [...node.querySelectorAll('annotation[encoding="application/x-tex"]')].map(item=>item.textContent);
}
function proseOnly(node) {
  const copy=node.cloneNode(true);copy.querySelectorAll('.animation-math').forEach(item=>item.remove());return copy.textContent;
}
async function testAuthoredBareMathRendersWholeExpressions() {
  const {dom,w,calls}=makeDom();loadKatex(w);const root=w.document.getElementById('content');
  const first=String.raw`分子分母同乘 \bar z_2：因为 \bar z_2/\bar z_2=1（设 z_2≠0），等式不变。`;
  const second=String.raw`展开分母 z_2\bar z_2，虚部两项相消，得到 x_2^2+y_2^2=|z_2|^2。`;
  const data=payload({steps:[{step_id:'s1',caption:first,start:0,end:2},{step_id:'s2',caption:second,start:2,end:4}],
    transcript_text:first+'\n'+second,captions_text:'WEBVTT\n\n'+first+'\n'});
  const before=JSON.stringify(data);const dispose=w.DSHAnimations.mountAnimations(root,data);
  const currentCaption=()=>root.querySelector('.animation-transcript-row[aria-current] .animation-transcript-text');
  assert.deepEqual(mathSources(currentCaption()),[String.raw`\bar z_2`,String.raw`\bar z_2/\bar z_2=1`,'z_2≠0'],
    'the full conjugate quotient and condition should render, not isolated slash commands');
  assert.equal(proseOnly(currentCaption()),'分子分母同乘 ：因为 （设 ），等式不变。');
  const transcript=root.querySelector('[data-animation-transcript]');assert.equal(transcript.hidden,true);
  assert.deepEqual(mathSources(root.querySelector('[data-source="step:s2"]')),[String.raw`z_2\bar z_2`,'x_2^2+y_2^2=|z_2|^2']);
  const fullTranscript=root.querySelector('[data-source="transcript"]');
  assert.equal(fullTranscript,null,'structured steps must not be repeated as a raw transcript dump');
  assert.equal(mathSources(transcript).length,5,'the same complete math remains in the time-linked rows');
  assert.equal(transcript.querySelectorAll('.animation-transcript-row').length,2);
  assert.equal(transcript.querySelectorAll('.animation-transcript-text').length,2);
  fakeMedia(w,root);root.querySelector('.animation-steps [data-step="1"]').click();
  assert.deepEqual(mathSources(currentCaption()),[String.raw`z_2\bar z_2`,'x_2^2+y_2^2=|z_2|^2']);
  assert.equal(proseOnly(currentCaption()),'展开分母 ，虚部两项相消，得到 。');
  assert.equal(JSON.stringify(data),before,'rendering must not rewrite source captions or WebVTT');
  assert.equal(root.querySelector('track').getAttribute('src'),'assets/animations/media/captions.vtt');
  assert.deepEqual(calls,[]);dispose();dom.window.close();
}
async function testDelimitedMathAndUntrustedTextStaySeparate() {
  const {dom,w}=makeDom();loadKatex(w);const root=w.document.getElementById('content');
  const caption=String.raw`标准 $\bar{z}_2$；整式 $$x_2^2+y_2^2=|z_2|^2$$；行内 \(z_2\bar z_2\)。<img src=x onerror="alert(1)">`;
  const dispose=w.DSHAnimations.mountAnimations(root,payload({steps:[{caption,start:0,end:2}],
    transcript_text:String.raw`对照 \[\frac{1}{z_2}\] 与 $\href{javascript:alert(1)}{x}$。`}));
  const status=root.querySelector('.animation-transcript-text');
  assert.deepEqual(mathSources(status),[String.raw`\bar{z}_2`,'x_2^2+y_2^2=|z_2|^2',String.raw`z_2\bar z_2`]);
  assert.equal(status.querySelectorAll('.katex-display').length,1);
  assert.match(proseOnly(status),/<img src=x onerror="alert\(1\)">/);assert.equal(status.querySelector('img'),null);
  assert.equal(root.querySelector('[href^="javascript:"]'),null);assert.equal(root.querySelector('[onerror]'),null);
  assert.ok(root.querySelector('.animation-transcript-text .katex-display'));
  assert.equal(root.querySelector('[data-source="transcript"]'),null,'structured captions provide the single readable transcript');
  dispose();dom.window.close();
}
async function testPlainLanguageAndInvalidMathRemainLiteral() {
  const {dom,w}=makeDom();loadKatex(w);const root=w.document.getElementById('content');
  const caption=String.raw`文件 report_v2.txt，HTTP API，步骤1/2，时间 00:02 --> 00:04，普通 x 和 y；路径 C:\notes\bar.txt；坏公式 $\frac{$。`;
  const dispose=w.DSHAnimations.mountAnimations(root,payload({steps:[{caption,start:0,end:2}]}));
  const status=root.querySelector('.animation-transcript-text');
  assert.equal(status.querySelector('.katex'),null);assert.equal(status.textContent,caption);
  dispose();dom.window.close();
}
async function testTimestampsBeforeMathRemainPlainText() {
  const {dom,w}=makeDom();loadKatex(w);const root=w.document.getElementById('content');
  const transcript=String.raw`00:02 z_2\bar z_2=|z_2|^2；12:34:56 \bar z_2。`;
  const dispose=w.DSHAnimations.mountAnimations(root,payload({steps:[],transcript_text:transcript}));
  const whole=root.querySelector('[data-source="transcript"]');
  assert.deepEqual(mathSources(whole),[String.raw`z_2\bar z_2=|z_2|^2`,String.raw`\bar z_2`]);
  assert.equal(proseOnly(whole),'00:02 ；12:34:56 。');dispose();dom.window.close();
}
async function testMathWithoutKatexFallsBackToUnchangedText() {
  const {dom,w}=makeDom();const root=w.document.getElementById('content');
  const caption=String.raw`$\bar z_2$ 与 z_2\bar z_2=|z_2|^2。`;
  const dispose=w.DSHAnimations.mountAnimations(root,payload({steps:[{caption,start:0,end:2}],transcript_text:caption}));
  assert.equal(root.querySelector('.animation-transcript-text').textContent,caption);
  assert.equal(root.querySelector('[data-source="transcript"]'),null,'the plain fallback must not duplicate structured captions');
  assert.equal(root.querySelector('.katex'),null);dispose();dom.window.close();
}

async function testCaptionLayerRendersMathAndKeepsAngleBrackets() {
  const {dom,w,calls}=makeDom();loadKatex(w);const root=w.document.getElementById('content');
  const steps=[{step_id:'S1',caption:String.raw`把满足 |z_n−z_0|<δ 的点放入圆盘，$\dfrac{1}{2}\cdot 2$ 不变`,start:0,end:2},
    {step_id:'S2',caption:String.raw`辐角相加：e^{i(φ_1+φ_2)}`,start:2,end:4}];
  const dispose=w.DSHAnimations.mountAnimations(root,payload({steps}));
  const video=root.querySelector('video');const layer=root.querySelector('[data-animation-caption]');
  assert.equal(layer.hidden,true,'no caption over the poster before playback');
  Object.defineProperty(video,'currentTime',{configurable:true,writable:true,value:0.5});
  video.dispatchEvent(new w.Event('play'));video.dispatchEvent(new w.Event('timeupdate'));
  assert.equal(layer.hidden,false);
  assert.deepEqual(mathSources(layer),['|z_n−z_0|<δ',String.raw`\dfrac{1}{2}\cdot 2`]);
  assert.equal(layer.querySelector('.katex-error'),null);
  video.currentTime=3;video.dispatchEvent(new w.Event('timeupdate'));
  assert.deepEqual(mathSources(layer),['e^{i(φ_1+φ_2)}']);
  assert.equal(layer.getAttribute('data-step'),'1');
  assert.deepEqual(calls,[]);dispose();dom.window.close();
}
async function testCaptionImplicitProductsAndBareFunctionNames() {
  // Found by check-render (2026-10-20): letter runs such as `nz` and `cos`
  // used to keep whole captions as raw TeX in the layer and the transcript.
  const {dom,w}=makeDom();loadKatex(w);const root=w.document.getElementById('content');
  const steps=[{step_id:'S1',caption:'屏幕上只剩 nz^{n−1}。',start:0,end:2},
    {step_id:'S2',caption:'得到 z^4 = r^4(cos4φ + i sin4φ)，字段 report_v2 保持文字',start:2,end:4},
    {step_id:'S3',caption:'不超过 M(R)e^{λa}·2R arcsin(a/R)，趋于 0。',start:4,end:6}];
  const dispose=w.DSHAnimations.mountAnimations(root,payload({steps}));
  const rows=root.querySelectorAll('.animation-transcript-text');
  assert.deepEqual(mathSources(rows[0]),['nz^{n−1}']);
  assert.deepEqual(mathSources(rows[1]),[String.raw`z^4 = r^4(\cos4φ + i \sin4φ)`]);
  assert.match(rows[1].textContent,/report_v2/);
  assert.deepEqual(mathSources(rows[2]),[String.raw`M(R)e^{λa}·2R \arcsin(a/R)`]);
  assert.equal(root.querySelector('.katex-error'),null);
  dispose();dom.window.close();
}
function testFallbackWebvttPutsEscapedTextOnItsOwnLine() {
  const {dom,w}=makeDom();
  const text=w.DSHAnimations.webvttFromSteps([{caption:'圆盘 |z-2|<1 与 D & E',start:0,end:2}]);
  assert.equal(text,'WEBVTT\n\nstep-1\n00:00:00.000 --> 00:00:02.000\n圆盘 |z-2|&lt;1 与 D &amp; E\n\n');
  dom.window.close();
}

async function testNarrationVolumeUsesTheSameVideoClock() {
  const {dom,w}=makeDom();const root=w.document.getElementById('content');
  const dispose=w.DSHAnimations.mountAnimations(root,payload());
  const state=fakeMedia(w,root),video=root.querySelector('video');
  const volume=root.querySelector('[data-action="volume"]');
  assert.ok(volume,'narrated animation needs volume control');
  assert.equal(volume.type,'range'); assert.equal(volume.min,'0'); assert.equal(volume.max,'1');
  volume.value='0.35';volume.dispatchEvent(new w.Event('input'));
  assert.equal(video.volume,.35);
  const mute=root.querySelector('[data-action="mute"]');assert.ok(mute);
  mute.click();assert.equal(video.muted,true);assert.equal(mute.getAttribute('aria-pressed'),'true');
  mute.click();assert.equal(video.muted,false);assert.equal(video.volume,.35);
  root.querySelector('[data-action="play"]').click();video.currentTime=.7;
  root.querySelector('[data-action="play"]').click();assert.equal(state.paused,true);
  root.querySelector('.animation-steps [data-step="1"]').click();assert.equal(video.currentTime,2);
  root.querySelector('[data-action="replay"]').click();assert.equal(video.currentTime,2);
  assert.equal(root.querySelector('audio'),null,'embedded audio must follow the video clock');
  dispose();assert.equal(state.paused,true);dom.window.close();
}

(async () => {
  const results = {};
  for (const [name, fn] of Object.entries({
    narration_volume: testNarrationVolumeUsesTheSameVideoClock,
    comparison_controls: testComparisonControlsUseActualActionsAndOnePlayToggle,
    public_step_selection: testPublicStepSelectionAndScopedChangeEvents,
    paused_selection_and_fullscreen: testExternalPausedSelectionSurvivesMetadataAndFullscreenIsLocal,
    bounded_steps: testStepClickStopsAtBoundaryAndCanReplayOrAdvance,
    step_resume_and_continuous: testResumeAndContinuousPlaybackRemainDistinct,
    bounded_fallbacks: testStepBoundsFallBackToNextStartAndActualDuration,
    step_frame_lifecycle: testActiveStepLoopAndDisposalArePerPlayer,
    metadata_boundary: testLastStepWaitsForMetadataWithoutReloadLoop,
    no_steps_and_pending_pause: testNoStepsStillPlaysAndPendingMetadataCanBeCancelled,
    bare_math: testAuthoredBareMathRendersWholeExpressions,
    caption_layer: testCaptionLayerRendersMathAndKeepsAngleBrackets,
    fallback_webvtt: testFallbackWebvttPutsEscapedTextOnItsOwnLine,
    caption_implicit_products: testCaptionImplicitProductsAndBareFunctionNames,
    delimited_math: testDelimitedMathAndUntrustedTextStaySeparate,
    literal_text: testPlainLanguageAndInvalidMathRemainLiteral,
    math_fallback: testMathWithoutKatexFallsBackToUnchangedText,
    timestamp_math: testTimestampsBeforeMathRemainPlainText,
    mount: testMountRendersWhitelistedTagsAndControls,
    controls: testPlaybackSpeedStepSeekAndDispose,
    missing: testMissingResourcesShowErrorAndTranscript,
    playback_error: testPlaybackErrorRevealsTranscript,
    isolation: testPlayerNeverCallsManagementApis,
    viewports: testBothViewportsKeepControlsAndTranscriptUsable,
  })) {
    await fn();
    results[name] = 'pass';
  }
  process.stdout.write(JSON.stringify({suite: 'animations_player', results}) + '\n');
})().catch(error => { console.error(error); process.exitCode = 1; });
