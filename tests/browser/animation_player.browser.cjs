/* T10 真实浏览器播放器验收（计划 §T10 / 判据 V04）。
 *
 *     node tests/browser/animation_player.browser.cjs <config.json>
 *
 * 它做的是**真的播一遍**，不是"HTTP 200 就算过"：
 *   · 打开生产工作台页（`ControlServer` 的 `/`），等它把章节 HTML 挂进
 *     `<iframe sandbox="allow-scripts">`——**与生产完全同一份 sandbox 配置**；
 *   · 沙箱子 frame 在 headless Chrome 里是一个 **OOPIF target**（`type=iframe`），
 *     用 `Target.attachToTarget{flatten:true}` 拿到 session 后可以 `Runtime.evaluate`
 *     进到**frame 自己的文档**里（T09 的"列不到子 frame"结论只对 `Target.setAutoAttach`
 *     成立；`Target.getTargets` + 显式 attach 是可行的，实测见证据目录的
 *     `cdp-sandbox-feasibility.json`）。所以逐帧／倍速／seek／字幕／换节停止全都能在
 *     **真正的 sandbox 里**量，不需要"直接导航到 src"绕开它。
 *   · 播放器 `preload="none"`（有 poster 时）是**故意的**：所以验收先证明
 *     "先出封面、点了播放才去取视频"，再证明真的解出了元数据与呈现帧。
 *   · 阻断外网：chrome 走死代理（回环放行）+ 每个 session 上 `Fetch` 只放行本机产物；
 *     同时把**所有**请求与响应状态记进报告（外部请求数必须为 0，4xx/5xx 必须为 0）。
 *   · 页面 JS 异常与 console error 全部收集。
 *
 * 配置（JSON 文件，由 `tests/integration/test_animation_browser.py` 生成）：
 *   {chrome, base, out, unit, other_unit, unit_url_hint,
 *    viewports:[{name,width,height}],
 *    expect:{duration,width,height,steps:[{id,start}]}, timeout_ms}
 *
 * 退出码：0 = 全部通过；1 = 有行为失败（红）；2 = 环境阻断（缺 chrome / 起不来）。
 * 报告固定写到 `<out>/report.json`，截图写 `<out>/shots/`。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const {spawn} = require('child_process');

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const startedAt = Date.now();

function killChrome(child) {
  if (!child || child.killed) return;
  try { process.kill(-child.pid, 'SIGKILL'); return; } catch (_) { /* 没有进程组 */ }
  try { child.kill('SIGKILL'); } catch (_) { /* 已退出 */ }
}

function parseArgs(argv) {
  const config = argv[2];
  if (!config) throw new Error('用法：node animation_player.browser.cjs <config.json>');
  return JSON.parse(fs.readFileSync(config, 'utf8'));
}

function writeReport(out, report) {
  fs.mkdirSync(out, {recursive: true});
  fs.writeFileSync(path.join(out, 'report.json'),
                   JSON.stringify(report, null, 2) + '\n', 'utf8');
}

/* ── 页面里跑的探针 ─────────────────────────────────────────────────────────
 * 每个探针都是自包含表达式，在**沙箱 frame 自己的 execution context** 里求值。
 * 它们只读播放器暴露的 DOM 契约（`data-animation-*` / `data-action`），
 * 不依赖内部变量名。                                                          */

const IN_PAGE = {
  waitPlayer: `(async () => {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      const host = document.querySelector('[data-animation-player]');
      if (host) {
        const video = host.querySelector('video');
        return {ok: true, unit: host.getAttribute('data-unit'),
                request: host.getAttribute('data-request'),
                hasVideo: !!video, poster: video ? video.getAttribute('poster') : null,
                readyState: video ? video.readyState : null, preload: video ? video.preload : null};
      }
      await new Promise(r => setTimeout(r, 100));
    }
    return {ok: false, reason: 'no [data-animation-player] within 15s'};
  })()`,

  /* 点播放按钮之前：**必须还没取视频**（preload=none 的封面态）。 */
  posterFirst: `(() => {
    const host = document.querySelector('[data-animation-player]');
    const video = host.querySelector('video');
    const box = video.getBoundingClientRect();
    return {readyState: video.readyState, paused: video.paused,
            poster: video.getAttribute('poster'), src: video.getAttribute('src'),
            preload: video.preload, width: Math.round(box.width), height: Math.round(box.height),
            error: video.error ? {code: video.error.code, message: video.error.message} : null,
            banner: (host.querySelector('[data-animation-error]') || {}).textContent || null};
  })()`,

  /* 读者动作：点"播放"。等**真的在播**（readyState≥2 且 currentTime 前进），然后暂停。
     元数据与画面尺寸在这里量 —— 封面态下量不到是 preload=none 的正常现象。 */
  playAndLoad: `(async () => {
    const host = document.querySelector('[data-animation-player]');
    const video = host.querySelector('video');
    const button = host.querySelector('[data-action="play"]');
    if (!button) return {ok: false, error: 'no play button'};
    if (!video.paused) video.pause();
    button.click();
    const deadline = Date.now() + 20000;
    let loaded = false;
    while (Date.now() < deadline) {
      if (video.readyState >= 2 && !video.paused && video.currentTime > 0.2) { loaded = true; break; }
      if (video.error) break;
      await new Promise(r => setTimeout(r, 100));
    }
    const state = {readyState: video.readyState, paused: video.paused,
                   currentTime: video.currentTime,
                   duration: Number.isFinite(video.duration) ? video.duration : null,
                   width: video.videoWidth || 0, height: video.videoHeight || 0,
                   playingAttr: host.getAttribute('data-playing'),
                   status: (host.querySelector('[data-animation-status]') || {}).textContent || '',
                   banner: (host.querySelector('[data-animation-error]') || {}).textContent || null,
                   error: video.error ? {code: video.error.code, message: video.error.message} : null};
    video.pause();
    await new Promise(r => setTimeout(r, 250));
    state.pausedAfter = video.paused;
    state.playingAttrAfter = host.getAttribute('data-playing');
    state.statusAfter = (host.querySelector('[data-animation-status]') || {}).textContent || '';
    return {ok: loaded, ...state};
  })()`,

  /* 帧证据：requestVideoFrameCallback 报的是**真正被合成出来的帧**的媒体时间，
     比 currentTime 更能证明"画面确实解码并呈现了"。暂停态下 seek 不一定产生
     呈现帧，所以这里 seek 后**短暂播放**取一帧，再暂停。 */
  frameAt: `(async (target) => {
    const host = document.querySelector('[data-animation-player]');
    const video = host && host.querySelector('video');
    if (!video) return {ok: false, error: 'no <video>'};
    if (video.readyState < 1) return {ok: false, error: 'media not loaded', readyState: video.readyState};
    /* 把播放器滚进 frame 自己的视口：截图截的是**顶级页**，播放器在 iframe 里
       如果不在可视区，截到的就只是正文（第一版就踩了这个，画面证据等于没有）。 */
    try { host.scrollIntoView({block: 'center'}); } catch (err) { /* 无布局实现 */ }
    const seeked = new Promise(resolve => {
      let done = false;
      const finish = () => { if (!done) { done = true; resolve(); } };
      video.addEventListener('seeked', finish, {once: true});
      setTimeout(finish, 6000);
    });
    try { video.currentTime = target; } catch (err) { return {ok: false, error: String(err)}; }
    await seeked;
    let presented = null;
    if (typeof video.requestVideoFrameCallback === 'function') {
      try { await video.play(); } catch (err) { /* 已经在内联播放 */ }
      presented = await new Promise(resolve => {
        const timer = setTimeout(() => resolve(null), 5000);
        video.requestVideoFrameCallback((_n, meta) => {
          clearTimeout(timer);
          resolve({mediaTime: meta.mediaTime, presentedFrames: meta.presentedFrames,
                   width: meta.width, height: meta.height});
        });
      });
      video.pause();
    }
    await new Promise(r => setTimeout(r, 150));
    return {ok: true, target, currentTime: video.currentTime, readyState: video.readyState,
            presented, decoded: (video.getVideoPlaybackQuality
              ? video.getVideoPlaybackQuality().totalVideoFrames : null),
            status: (host.querySelector('[data-animation-status]') || {}).textContent || '',
            current: Array.from(host.querySelectorAll('[aria-current="true"]'))
              .map(n => n.getAttribute('data-step'))};
  })`,

  /* 倍速：**比较两个倍速下的前进量**，不只读 playbackRate。
     （headless 没有音轨时媒体时钟可能跑得比墙钟快，所以"1 秒前进多少"不是绝对判据，
      "2× 明显快于 1×"才是。） */
  speed: `(async () => {
    const host = document.querySelector('[data-animation-player]');
    const video = host.querySelector('video');
    const select = host.querySelector('select[data-action="speed"]');
    if (!select) return {ok: false, error: 'no speed select'};
    async function measure(rate) {
      select.value = String(rate);
      select.dispatchEvent(new Event('change', {bubbles: true}));
      const applied = video.playbackRate;
      video.currentTime = 1;
      await new Promise(r => setTimeout(r, 250));
      try { await video.play(); } catch (err) { return {applied, error: String(err)}; }
      const begin = video.currentTime;
      await new Promise(r => setTimeout(r, 1000));
      const advanced = video.currentTime - begin;
      video.pause();
      await new Promise(r => setTimeout(r, 200));
      return {applied, advanced};
    }
    const one = await measure(1);
    const two = await measure(2);
    select.value = '1';
    select.dispatchEvent(new Event('change', {bubbles: true}));
    return {ok: two.applied === 2 && one.applied === 1 && two.advanced > one.advanced * 1.5,
            rate1: one, rate2: two, rateBack: video.playbackRate,
            options: Array.from(select.options).map(o => o.value)};
  })()`,

  seek: `(async () => {
    const host = document.querySelector('[data-animation-player]');
    const video = host.querySelector('video');
    async function jump(t) {
      const seeked = new Promise(resolve => {
        let done = false;
        const finish = () => { if (!done) { done = true; resolve(); } };
        video.addEventListener('seeked', finish, {once: true});
        setTimeout(finish, 6000);
      });
      video.currentTime = t; await seeked;
      return video.currentTime;
    }
    const forward = await jump(24.0);
    const backward = await jump(4.0);
    return {ok: true, forward, backward,
            forward_error: Math.abs(forward - 24.0), backward_error: Math.abs(backward - 4.0)};
  })()`,

  stepJump: `(async () => {
    const host = document.querySelector('[data-animation-player]');
    const video = host.querySelector('video');
    const buttons = Array.from(host.querySelectorAll('[data-action="step"][data-step]'));
    if (buttons.length < 4) return {ok: false, error: 'step buttons missing', count: buttons.length};
    const target = host.querySelector('[data-action="step"][data-step="2"]');
    target.click();
    await new Promise(r => setTimeout(r, 600));
    const first = {currentTime: video.currentTime,
                   status: (host.querySelector('[data-animation-status]') || {}).textContent || '',
                   current: target.getAttribute('aria-current')};
    const last = host.querySelector('[data-action="step"][data-step="3"]');
    last.click();
    await new Promise(r => setTimeout(r, 600));
    const second = {currentTime: video.currentTime, current: last.getAttribute('aria-current'),
                    status: (host.querySelector('[data-animation-status]') || {}).textContent || ''};
    return {ok: true, steps: buttons.length, first, second,
            error: Math.abs(first.currentTime - 16.0)};
  })()`,

  captions: `(async () => {
    const host = document.querySelector('[data-animation-player]');
    const video = host.querySelector('video');
    const element = video.querySelector('track');
    const button = host.querySelector('[data-action="captions"]');
    if (!element || !button) return {ok: false, error: 'no track/captions button'};
    /* 真浏览器里 cue 与 mode 在 track 元素的 .track（TextTrack）上；
       元素自己只有 readyState。读错对象会得到"永远 0 条 cue"的假象。 */
    const track = element.track || element;
    for (let i = 0; i < 80 && element.readyState !== 2; i++) await new Promise(r => setTimeout(r, 100));
    const text = Array.from(track.cues || []).map(c => c.text).join(' | ');
    const before = {mode: track.mode, readyState: element.readyState,
                    src: element.getAttribute('src'), pressed: button.getAttribute('aria-pressed')};
    button.click(); await new Promise(r => setTimeout(r, 250));
    const off = {mode: track.mode, pressed: button.getAttribute('aria-pressed')};
    button.click(); await new Promise(r => setTimeout(r, 250));
    const on = {mode: track.mode, pressed: button.getAttribute('aria-pressed')};
    return {ok: element.readyState === 2, before, off, on, cues: (track.cues || []).length,
            text, hasChinese: /[\\u4e00-\\u9fff]/.test(text)};
  })()`,

  /* 换节：`app.js` 必须 dispose 播放器（pause + 清 src + 摘节点）。
     "换节仍播放"是负例夹具要暴露的真实故障。 */
  sectionChange: `(async () => {
    const playing = () => Array.from(document.querySelectorAll('video'))
      .filter(v => !v.paused && !v.ended && v.currentTime > 0).length;
    const video0 = document.querySelector('[data-animation-player] video');
    if (video0) { try { await video0.play(); } catch (err) {} await new Promise(r => setTimeout(r, 500)); }
    const beforePlaying = playing();
    location.hash = '#/' + '__OTHER__';
    await new Promise(r => setTimeout(r, 1500));
    const away = {players: document.querySelectorAll('[data-animation-player]').length,
                  videos: document.querySelectorAll('video').length,
                  stillPlaying: playing(),
                  leakedSrc: Array.from(document.querySelectorAll('video'))
                    .map(v => v.getAttribute('src')).filter(Boolean)};
    location.hash = '#/' + '__UNIT__';
    await new Promise(r => setTimeout(r, 1500));
    const backHost = document.querySelector('[data-animation-player]');
    const back = {players: document.querySelectorAll('[data-animation-player]').length,
                  readyState: null, src: null};
    if (backHost) {
      const v = backHost.querySelector('video');
      if (v) {
        /* 播放器是 preload="none"：重新进入后 readyState 理应还是 0，
           要**真的再点一次播放**才算"能重新进入"。 */
        back.readyStateBeforePlay = v.readyState;
        back.src = v.getAttribute('src');
        const button = backHost.querySelector('[data-action="play"]');
        if (button) {
          button.click();
          for (let i = 0; i < 200 && !(v.readyState >= 2 && v.currentTime > 0.2); i++) {
            await new Promise(r => setTimeout(r, 100));
          }
          back.readyState = v.readyState;
          back.currentTime = v.currentTime;
          v.pause();
        }
      }
    }
    return {ok: true, beforePlaying, away, back};
  })()`,

  /* 教材页调不到管理 API：产物 CSP 是 `connect-src 'none'`。 */
  managementApi: `(async () => {
    const result = {origin: location.origin};
    try {
      const response = await fetch('/api/books', {cache: 'no-store'});
      result.fetched = response.status;
    } catch (err) { result.blocked = String(err); }
    try {
      const response = await fetch('http://127.0.0.1:1/api/books');
      result.external_fetched = response.status;
    } catch (err) { result.external_blocked = String(err); }
    return result;
  })()`,

  layout: `(() => {
    const host = document.querySelector('[data-animation-player]');
    if (!host) return {ok: false, error: 'no player'};
    const video = host.querySelector('video');
    const controls = host.querySelector('[data-animation-controls]');
    const transcript = host.querySelector('[data-animation-transcript]');
    const text = transcript ? (transcript.textContent || '').trim() : '';
    const rect = node => { const r = node.getBoundingClientRect();
      return {top: Math.round(r.top), left: Math.round(r.left), width: Math.round(r.width),
              height: Math.round(r.height), bottom: Math.round(r.bottom)}; };
    const buttons = Array.from(host.querySelectorAll('button'));
    return {
      ok: true,
      viewport: {width: window.innerWidth, height: window.innerHeight},
      overflow: document.documentElement.scrollWidth - window.innerWidth,
      video: video ? rect(video) : null,
      controls: controls ? rect(controls) : null,
      transcript: transcript ? rect(transcript) : null,
      transcript_text_length: text.length,
      transcript_visible: !!transcript && transcript.offsetParent !== null,
      controls_visible: !!controls && controls.offsetParent !== null,
      buttons: buttons.length,
      min_button_height: buttons.length
        ? Math.round(Math.min(...buttons.map(b => b.getBoundingClientRect().height))) : 0,
      step_buttons: host.querySelectorAll('.animation-steps [data-action="step"][data-step]').length,
      transcript_step_buttons: host.querySelectorAll('.animation-transcript [data-action="step"]').length,
      visible_steps: Array.from(host.querySelectorAll('.animation-steps [data-action="step"][data-step]'))
        .filter(b => { const r = b.getBoundingClientRect();
          return r.width > 0 && r.height > 0 && r.left >= -1 && r.right <= window.innerWidth + 1; }).length,
      error_banner: (host.querySelector('[data-animation-error]') || {}).textContent || null,
    };
  })()`,
};

/* ── 极简 CDP 客户端 ─────────────────────────────────────────────────────── */

class Browser {
  constructor(ws) {
    this.ws = ws;
    this.seq = 0;
    this.pending = new Map();
    this.handlers = [];
    ws.onmessage = event => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const slot = this.pending.get(message.id);
        this.pending.delete(message.id);
        clearTimeout(slot.timer);
        if (message.error) slot.reject(new Error(message.error.message));
        else slot.resolve(message.result);
        return;
      }
      if (message.method) this.handlers.forEach(handler => handler(message));
    };
  }
  send(method, params = {}, sessionId = null) {
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('CDP timeout: ' + method));
      }, 30000);
      this.pending.set(id, {resolve, reject, timer});
      const payload = {id, method, params};
      if (sessionId) payload.sessionId = sessionId;
      this.ws.send(JSON.stringify(payload));
    });
  }
  onEvent(handler) { this.handlers.push(handler); }
}

async function launch(config) {
  if (!config.chrome || !fs.existsSync(config.chrome)) {
    return {blocked: '找不到 Chromium：' + config.chrome};
  }
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'aifs-player-'));
  const first = config.viewports[0] || {width: 1280, height: 720};
  const args = ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
    '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
    '--autoplay-policy=no-user-gesture-required', '--mute-audio',
    '--force-device-scale-factor=1',
    /* 沙箱子 frame 在页面下方会被 Chrome 节流（定时器 + 不合成帧）：
       rVFC 拿不到帧、setTimeout 被拉长。测试要量的正是"画面"，所以关掉这几项节流。 */
    '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
    '--disable-backgrounding-occluded-windows',
    /* 死代理挡外网；**本机回环要放行**（`<-loopback>` 会把回环也塞进代理，
       那样连被测服务都连不上）。外部请求另有 Fetch 白名单兜底。 */
    '--proxy-server=http://127.0.0.1:9', '--proxy-bypass-list=localhost,127.0.0.1,[::1]',
    '--disable-background-networking', '--disable-sync', '--no-first-run',
    '--disable-component-update', '--disable-default-apps', '--disable-domain-reliability',
    '--window-size=' + first.width + ',' + first.height,
    '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank'];
  /* `detached:true` + 退出时**按进程组** SIGKILL：只 kill 启动器会把 zygote/renderer
     漏在机器上（跑一轮验收要起 5 个 chrome，泄漏会拖慢后面所有人）。 */
  const child = spawn(config.chrome, args, {stdio: ['ignore', 'pipe', 'pipe'], detached: true});
  let stderr = '';
  let wsUrl = '';
  child.stderr.on('data', chunk => {
    stderr += chunk;
    const match = /ws:\/\/[^\s]+/.exec(stderr);
    if (match) wsUrl = match[0];
  });
  for (let i = 0; i < 240 && !wsUrl; i++) await sleep(50);
  if (!wsUrl) {
    child.kill();
    return {blocked: 'Chromium 没给出调试地址：' + stderr.slice(-600)};
  }
  const port = new URL(wsUrl).port;
  const targets = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json();
  const page = targets.find(target => target.type === 'page');
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  return {child, profile, wsUrl, browser: new Browser(socket)};
}

/* ── 主流程 ───────────────────────────────────────────────────────────────── */

async function run(config) {
  const report = {
    ok: false, blocked: false, failures: [], notes: [], warnings: [],
    base: config.base, unit: config.unit, other_unit: config.other_unit,
    chrome: config.chrome, started_at: new Date().toISOString(),
    js_errors: [], console_errors: [], requests: [], responses: [],
    external_requests: [], failed_requests: [], frame_targets: [],
    loading_failed_raw: [], vtt_extra_info: [],
    viewports: [], shots: [], management_api: null, external_request_count: null,
  };
  const out = config.out;
  fs.mkdirSync(path.join(out, 'shots'), {recursive: true});
  const fail = message => { if (!report.failures.includes(message)) report.failures.push(message); };

  const launched = await launch(config);
  if (launched.blocked) {
    report.blocked = true;
    report.failures.push(launched.blocked);
    writeReport(out, report);
    return report;
  }
  const {child, profile, browser} = launched;
  const requestUrls = new Map();

  try {
    browser.onEvent(message => {
      if (message.method === 'Runtime.exceptionThrown') {
        const details = message.params.exceptionDetails || {};
        const text = (details.exception && details.exception.description) || details.text || '';
        report.js_errors.push({session: message.sessionId || 'top', text});
      }
      if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
        report.console_errors.push({session: message.sessionId || 'top',
          text: (message.params.args || []).map(a => a.value || a.description || '').join(' ')});
      }
      if (message.method === 'Network.requestWillBeSent') {
        requestUrls.set(message.params.requestId, message.params.request.url);
        report.requests.push({session: message.sessionId || 'top',
          url: message.params.request.url, requestId: message.params.requestId});
      }
      if (message.method === 'Network.responseReceived') {
        const status = message.params.response.status;
        report.responses.push({session: message.sessionId || 'top',
          url: message.params.response.url, status});
        if (status >= 400) {
          report.failed_requests.push({session: message.sessionId || 'top',
            url: message.params.response.url, status, error: 'HTTP ' + status});
        }
      }
      if (message.method === 'Network.loadingFailed' && !message.params.canceled) {
        const url = requestUrls.get(message.params.requestId) || message.params.requestId;
        report.failed_requests.push({session: message.sessionId || 'top', url,
          error: message.params.errorText,
          blockedReason: message.params.blockedReason || null,
          corsErrorStatus: message.params.corsErrorStatus || null,
          type: message.params.type || null});
        report.loading_failed_raw.push({session: message.sessionId || 'top',
          params: message.params});
      }
      if (message.method === 'Network.requestWillBeSentExtraInfo') {
        const url = requestUrls.get(message.params.requestId) || '';
        if (/\.vtt$/.test(url)) {
          report.vtt_extra_info.push({url, headers: message.params.headers,
                                      associatedCookies: message.params.associatedCookies});
        }
      }
      if (message.method === 'Fetch.requestPaused') {
        const request = message.params;
        const url = request.request.url;
        const local = url.startsWith(config.base) || /^(data|blob|about):/.test(url);
        const call = local
          ? browser.send('Fetch.continueRequest', {requestId: request.requestId}, message.sessionId)
          : (report.external_requests.push(url),
             browser.send('Fetch.failRequest',
                          {requestId: request.requestId, errorReason: 'BlockedByClient'},
                          message.sessionId));
        call.catch(() => {});
      }
    });

    await browser.send('Runtime.enable');
    await browser.send('Page.enable');
    await browser.send('Network.enable');
    await browser.send('Target.setDiscoverTargets', {discover: true});
    await browser.send('Fetch.enable', {patterns: [{urlPattern: '*'}]});

    const evaluateTop = async (expression, awaitPromise = false) => {
      const result = await browser.send('Runtime.evaluate',
        {expression, returnByValue: true, awaitPromise});
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'evaluate failed');
      return result.result ? result.result.value : undefined;
    };

    for (const viewport of config.viewports) {
      const entry = {name: viewport.name, width: viewport.width, height: viewport.height,
                     checks: {}, failures: [], shots: []};
      report.viewports.push(entry);
      const viewportFail = message => {
        if (!entry.failures.includes(message)) entry.failures.push(message);
        fail(viewport.name + ': ' + message);
      };

      await browser.send('Emulation.setDeviceMetricsOverride',
        {width: viewport.width, height: viewport.height, deviceScaleFactor: 1, mobile: false});
      /* 每个视口重新加载工作台：状态干净，且能重新观察沙箱 frame 的创建。 */
      await browser.send('Page.navigate', {url: config.base + '/'});
      await sleep(1500);

      let previewUrl = '';
      for (let i = 0; i < 80 && !previewUrl; i++) {
        previewUrl = await evaluateTop(
          "(() => { const f = document.getElementById('preview-frame'); return (f && f.getAttribute('src')) || ''; })()"
        ).catch(() => '');
        if (!previewUrl) await sleep(250);
      }
      entry.checks.workbench_preview_url = previewUrl;
      if (!previewUrl) {
        viewportFail('工作台没有把章节 HTML 挂进 sandbox iframe（预览未登记或前端没渲染）');
        continue;
      }
      entry.checks.iframe_sandbox = await evaluateTop(
        "(() => { const f = document.getElementById('preview-frame'); if (!f) return null; f.hidden = false; f.scrollIntoView({block: 'start'}); return f.getAttribute('sandbox'); })()");
      if (!/allow-scripts/.test(String(entry.checks.iframe_sandbox)) ||
          /allow-same-origin/.test(String(entry.checks.iframe_sandbox))) {
        viewportFail('工作台 iframe 的 sandbox 属性与生产不一致：' + entry.checks.iframe_sandbox);
      }
      await sleep(500);

      /* 沙箱子 frame 是一个 OOPIF target —— 找到它并 attach。 */
      const targets = (await browser.send('Target.getTargets')).targetInfos;
      report.frame_targets.push(...targets.filter(t => t.type !== 'page')
        .map(t => ({type: t.type, url: t.url, viewport: viewport.name})));
      const frameTarget = targets.find(t => t.type === 'iframe' &&
          t.url.includes(config.unit_url_hint || '/index.html'))
        || targets.find(t => t.type === 'iframe');
      if (!frameTarget) {
        viewportFail('Target.getTargets 里没有 iframe target：无法在 sandbox 内取证');
        continue;
      }
      entry.checks.frame_target = {type: frameTarget.type, url: frameTarget.url};
      const attached = await browser.send('Target.attachToTarget',
        {targetId: frameTarget.targetId, flatten: true});
      const session = attached.sessionId;
      await browser.send('Runtime.enable', {}, session);
      await browser.send('Network.enable', {}, session);
      await browser.send('Fetch.enable', {patterns: [{urlPattern: '*'}]}, session);

      const evaluateFrame = async (expression, awaitPromise = true) => {
        const result = await browser.send('Runtime.evaluate',
          {expression, returnByValue: true, awaitPromise}, session);
        if (result.exceptionDetails) {
          throw new Error((result.exceptionDetails.exception || {}).description
                          || result.exceptionDetails.text || 'frame evaluate failed');
        }
        return result.result ? result.result.value : undefined;
      };

      let player = null;
      for (let i = 0; i < 40 && !(player && player.ok); i++) {
        player = await evaluateFrame(IN_PAGE.waitPlayer)
          .catch(error => ({ok: false, reason: String(error)}));
        if (!player || !player.ok) await sleep(300);
      }
      entry.checks.player = player;
      if (!player || !player.ok) {
        viewportFail('章节页里没有挂上播放器：' + JSON.stringify(player));
        continue;
      }
      if (player.unit !== config.unit) {
        viewportFail('播放器 data-unit=' + player.unit + '，期望 ' + config.unit);
      }

      /* ① 封面态：还没取视频。 */
      entry.checks.posterFirst = await evaluateFrame(IN_PAGE.posterFirst);
      const poster = entry.checks.posterFirst;
      if (!poster.poster) viewportFail('video 上没有 poster 属性');
      if (poster.width <= 0 || poster.height <= 0) {
        viewportFail('画面区域尺寸为 0：' + JSON.stringify(poster));
      }
      if (poster.preload !== 'none' || poster.readyState !== 0) {
        report.notes.push(viewport.name + '：播放器不是"先封面后取流"（preload=' +
                          poster.preload + ' readyState=' + poster.readyState + '）');
      }

      /* ② 点播放 → 真的解出元数据并在播 → 暂停。 */
      entry.checks.playAndLoad = await evaluateFrame(IN_PAGE.playAndLoad);
      const load = entry.checks.playAndLoad;
      const detail = load.ok ? '' : ('（readyState=' + load.readyState + '，error=' +
        JSON.stringify(load.error) + '，banner=' + JSON.stringify(load.banner) + '）');
      if (!load.ok) viewportFail('视频没有 loadedmetadata / 没有真的播起来' + detail);
      else {
        if (Math.abs(load.duration - config.expect.duration) > 0.2) {
          viewportFail('duration=' + load.duration + '，期望 ' + config.expect.duration);
        }
        if (load.width !== config.expect.width || load.height !== config.expect.height) {
          viewportFail('画面尺寸=' + load.width + 'x' + load.height +
                       '，期望 ' + config.expect.width + 'x' + config.expect.height);
        }
        if (!load.pausedAfter) viewportFail('暂停后 video.paused 仍为 false');
        if (String(load.playingAttrAfter) === 'true') viewportFail('暂停后 data-playing 仍然是 true');
        if (!(load.currentTime > 0.2)) viewportFail('播放期间 currentTime 没有前进');
      }

      /* ③ 每一步：seek 到步首 → 取"真正呈现的帧" → 截图。 */
      const frames = [];
      let frameClip = null;
      for (let index = 0; index < config.expect.steps.length; index++) {
        const step = config.expect.steps[index];
        let frame = null;
        try { frame = await evaluateFrame(IN_PAGE.frameAt + '(' + Number(step.start + 0.4) + ')'); }
        catch (error) { frame = {ok: false, error: String(error)}; }
        frames.push({step: step.id, start: step.start, ...frame});
        const shot = path.join(out, 'shots', viewport.name + '-step' + (index + 1) + '.png');
        try {
          if (!frameClip) frameClip = await evaluateTop(`(() => {
            const f = document.getElementById('preview-frame');
            if (!f) return null;
            const r = f.getBoundingClientRect();
            return {x: Math.round(r.left + window.scrollX), y: Math.round(r.top + window.scrollY),
                    width: Math.round(r.width), height: Math.round(r.height), scale: 1};
          })()`);
          const captured = await browser.send('Page.captureScreenshot',
            frameClip ? {format: 'png', clip: frameClip} : {format: 'png'});
          fs.writeFileSync(shot, Buffer.from(captured.data, 'base64'));
          entry.shots.push(shot);
          report.shots.push(shot);
        } catch (error) { report.warnings.push('截图失败 ' + shot + '：' + error.message); }
      }
      entry.checks.frames = frames;
      frames.forEach(row => {
        if (!row.ok) viewportFail('第 ' + row.step + ' 步取帧失败：' + JSON.stringify(row.error || row));
        else if (Math.abs(row.currentTime - row.target) > 0.2) {
          viewportFail('第 ' + row.step + ' 步 seek 误差 ' +
                       Math.abs(row.currentTime - row.target).toFixed(3) + 's > 0.2s');
        } else if (!row.presented) {
          viewportFail('第 ' + row.step + ' 步没有拿到 requestVideoFrameCallback 的真实帧');
        }
      });

      entry.checks.speed = await evaluateFrame(IN_PAGE.speed);
      if (!entry.checks.speed.ok) viewportFail('倍速失效：' + JSON.stringify(entry.checks.speed));

      entry.checks.seek = await evaluateFrame(IN_PAGE.seek);
      if (entry.checks.seek.forward_error > 0.2 || entry.checks.seek.backward_error > 0.2) {
        viewportFail('seek 误差超 0.2s：' + JSON.stringify(entry.checks.seek));
      }

      entry.checks.stepJump = await evaluateFrame(IN_PAGE.stepJump);
      if (!entry.checks.stepJump.ok) {
        viewportFail('步骤跳转失效：' + JSON.stringify(entry.checks.stepJump));
      } else {
        if (entry.checks.stepJump.error > 0.2) {
          viewportFail('步骤跳转 seek 误差 ' + entry.checks.stepJump.error.toFixed(3) + 's > 0.2s');
        }
        if (entry.checks.stepJump.first.current !== 'true') {
          viewportFail('步骤跳转后 aria-current 没落到该步');
        }
      }

      entry.checks.captions = await evaluateFrame(IN_PAGE.captions);
      const captions = entry.checks.captions;
      if (!captions.ok) viewportFail('字幕没读到 cue：' + JSON.stringify(captions));
      else {
        if (!captions.hasChinese) viewportFail('字幕 cue 里没有中文：' + captions.text);
        if (captions.off.mode === 'showing' || captions.off.pressed !== 'false') {
          viewportFail('字幕关不掉：' + JSON.stringify(captions.off));
        }
        // R1: captions are drawn by the player layer; the native track stays loaded but hidden.
        if (captions.on.mode !== 'hidden' || captions.on.pressed !== 'true') {
          viewportFail('字幕开不回来：' + JSON.stringify(captions.on));
        }
      }

      entry.checks.layout = await evaluateFrame(IN_PAGE.layout);
      const layout = entry.checks.layout;
      if (!layout.ok) viewportFail('布局探针失败：' + JSON.stringify(layout));
      else {
        if (layout.overflow > 1) viewportFail('横向溢出 ' + layout.overflow + 'px');
        if (!layout.transcript_visible || layout.transcript_text_length < 40) {
          viewportFail('文字稿不可见或为空：' + JSON.stringify(layout));
        }
        if (!layout.controls_visible || layout.visible_steps < 4) {
          viewportFail('控件/步骤按钮不可见：' + JSON.stringify(layout));
        }
        if (layout.video && layout.video.width > layout.viewport.width + 1) {
          viewportFail('画面比视口还宽：' + JSON.stringify(layout.video));
        }
      }

      entry.checks.managementApi = await evaluateFrame(IN_PAGE.managementApi);
      report.management_api = report.management_api || entry.checks.managementApi;
      const management = entry.checks.managementApi;
      if (management.fetched !== undefined || management.external_fetched !== undefined) {
        viewportFail('教材页居然调到了管理 API：' + JSON.stringify(management));
      }

      entry.checks.sectionChange = await evaluateFrame(
        IN_PAGE.sectionChange.replace('__OTHER__', config.other_unit).replace('__UNIT__', config.unit));
      const change = entry.checks.sectionChange;
      if (change.away.stillPlaying > 0 || change.away.videos > 0) {
        viewportFail('换节后上一节还在播/播放器没摘干净：' + JSON.stringify(change.away));
      }
      if (change.back.players !== 1 || !(change.back.readyState >= 2)) {
        viewportFail('换回来没有重新进入（再次播放没有起来）：' + JSON.stringify(change.back));
      }
    }

    report.external_request_count = report.external_requests.length;
    if (report.external_requests.length) {
      fail('出现外部请求 ' + report.external_requests.length + ' 条：' +
           JSON.stringify(report.external_requests.slice(0, 5)));
    }
    if (report.js_errors.length) {
      fail('页面抛了 JS 异常：' + JSON.stringify(report.js_errors.slice(0, 5)));
    }
    if (report.failed_requests.length) {
      const relevant = report.failed_requests.filter(row =>
        !/BlockedByClient|net::ERR_BLOCKED_BY_CLIENT/.test(String(row.error))
        && !/\/favicon\.ico$/.test(String(row.url)));
      if (relevant.length) fail('有资源请求失败：' + JSON.stringify(relevant.slice(0, 5)));
    }
    if (!report.viewports.length) fail('没有任何视口跑完');
    report.ok = report.failures.length === 0;
  } catch (error) {
    report.failures.push('驱动异常：' + (error && error.stack ? error.stack : String(error)));
    report.ok = false;
  } finally {
    killChrome(child);
    await sleep(250);
    try { fs.rmSync(profile, {recursive: true, force: true, maxRetries: 3}); } catch (_) {}
  }
  report.finished_at = new Date().toISOString();
  report.elapsed_ms = Date.now() - startedAt;
  writeReport(out, report);
  return report;
}

async function main() {
  const config = parseArgs(process.argv);
  const report = await run(config);
  const summary = {
    ok: report.ok, blocked: report.blocked, failures: report.failures,
    external_request_count: report.external_request_count,
    js_errors: report.js_errors.length, failed_requests: report.failed_requests.length,
    viewports: report.viewports.map(v => ({name: v.name, failures: v.failures})),
    report: path.join(config.out, 'report.json'),
  };
  process.stdout.write(JSON.stringify(summary, null, 1) + '\n');
  if (report.blocked) process.exitCode = 2;
  else if (!report.ok) process.exitCode = 1;
}

main().catch(error => {
  process.stdout.write(JSON.stringify({ok: false, blocked: true, error: String(error)}) + '\n');
  process.exitCode = 2;
});
