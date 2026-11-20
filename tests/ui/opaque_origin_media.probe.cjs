/* T09 实测：**不透明 origin 下**视频与字幕到底能不能读（真实 Chromium）。

   为什么必须实测：产物页的响应头带 `sandbox allow-scripts`（工作台 iframe 也是
   `sandbox="allow-scripts"`），文档 origin 因此是**不透明的 `null`**。
   `<video>` 走 media 请求、`<track>` 按 CORS 取 —— 它们在
   "不透明 origin + `media-src 'self'` + `Access-Control-Allow-Origin: *`" 下到底成不成，
   读规范推断不算证据（T09 复选框要求实测）。

   协议：CDP 只导航一次（`Page.navigate`），然后在**页面自己的** execution context 里
   跑一个返回 Promise 的探针，等 loadedmetadata／cue 就绪。这样不依赖
   `Target.getTargets` 会不会列出沙箱子 frame（实测这一版 headless 里它不列）。

   用法：node tests/ui/opaque_origin_media.probe.cjs <base> <contentPath> <out.json>
*/
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');

const [base, contentPath, output] = process.argv.slice(2);
const chromePath = path.resolve(__dirname, '../../.devtools/chrome/chrome-linux64/chrome');
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'aifs-opaque-media-'));
const chrome = spawn(chromePath, ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
  '--autoplay-policy=no-user-gesture-required', '--remote-debugging-port=0',
  '--user-data-dir=' + profile, 'about:blank'], {stdio: ['ignore', 'pipe', 'pipe']});
let websocket;
chrome.stderr.on('data', buffer => {
  const match = String(buffer).match(/ws:\/\/\S+/);
  if (match) websocket = match[0];
});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let ws;

async function run() {
  for (let n = 0; n < 100 && !websocket; n++) await sleep(50);
  assert.ok(websocket, 'Chromium 必须能起来');
  const tabs = await (await fetch('http://127.0.0.1:' + new URL(websocket).port + '/json/list')).json();
  ws = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let next = 0; const pending = new Map(); const network = [];
  ws.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id) { pending.get(message.id)?.(message); pending.delete(message.id); }
    if (message.method === 'Network.requestWillBeSent') network.push(message.params.request.url);
  };
  const send = async (method, params = {}, sessionId) => {
    const id = ++next;
    const result = await new Promise(resolve => {
      pending.set(id, resolve); ws.send(JSON.stringify({id, method, params, sessionId}));
    });
    if (result.error) throw new Error(JSON.stringify(result.error));
    return result.result;
  };
  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate',
      {expression, returnByValue: true, awaitPromise: true});
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride',
             {width: 1280, height: 800, deviceScaleFactor: 1, mobile: false});
  await send('Page.navigate', {url: base + contentPath});
  await sleep(1200);
  const metrics = await evaluate(`new Promise(resolve => {
    const video = document.querySelector('video');
    const track = video && video.querySelector('track');
    const state = {origin: location.origin, video: null, track: null, trackText: null,
                   securityError: null, managementFetch: null, parentAccess: null};
    try { state.parentAccess = String(parent.document.title); }
    catch (err) { state.parentAccess = err.name; }
    fetch('${base}/api/books').then(() => { state.managementFetch = 'leaked'; },
                                    () => { state.managementFetch = 'blocked'; });
    const describe = () => {
      state.video = video ? {
        readyState: video.readyState, networkState: video.networkState,
        duration: Number.isFinite(video.duration) ? video.duration : null,
        videoWidth: video.videoWidth, videoHeight: video.videoHeight,
        error: video.error ? {code: video.error.code, message: video.error.message} : null,
        currentSrc: video.currentSrc,
      } : null;
      state.track = track ? {readyState: track.readyState, mode: track.mode,
                             src: track.getAttribute('src')} : null;
      try {
        const cues = track && track.track ? track.track.cues : null;
        state.trackText = cues ? [...cues].map(cue => cue.text) : null;
      } catch (err) { state.trackText = 'exception:' + err.name; }
    };
    const done = () => { describe(); resolve(state); };
    if (!video) return resolve(state);
    if (video.readyState >= 1) return setTimeout(done, 300);
    video.addEventListener('loadedmetadata', () => setTimeout(done, 600), {once: true});
    video.addEventListener('error', () => setTimeout(done, 300), {once: true});
    setTimeout(done, 6000);
  })`);
  metrics.networkRequests = network;
  metrics.contentPath = contentPath;
  if (output) fs.writeFileSync(output, JSON.stringify(metrics, null, 2) + '\n');
  process.stdout.write(JSON.stringify(metrics) + '\n');
}

run().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => {
  ws?.close(); chrome.kill(); setTimeout(() => fs.rmSync(profile, {recursive: true, force: true}), 100);
});
