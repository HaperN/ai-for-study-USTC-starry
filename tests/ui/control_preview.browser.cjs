/* Real Chromium: generated HTML runs, opaque sandbox cannot read the workbench. */
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const [base, previewUrl, screenshot, mode] = process.argv.slice(2);
const chromePath = path.resolve(__dirname, '../../.devtools/chrome/chrome-linux64/chrome');
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'aifs-control-preview-'));
const chrome = spawn(chromePath, ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
  '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank'], {stdio: ['ignore', 'pipe', 'pipe']});
let websocket;
chrome.stderr.on('data', buffer => { const match = String(buffer).match(/ws:\/\/\S+/); if (match) websocket = match[0]; });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let ws;
async function run() {
  for (let n=0; n<100 && !websocket; n++) await sleep(50);
  assert.ok(websocket, 'Chromium should start');
  const tabs = await (await fetch('http://127.0.0.1:' + new URL(websocket).port + '/json/list')).json();
  ws = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen=resolve; ws.onerror=reject; });
  let next=0; const pending = new Map(); const contexts = new Map(); const exceptions=[];
  ws.onmessage = event => {
    const message=JSON.parse(event.data);
    if (message.id) { pending.get(message.id)?.(message); pending.delete(message.id); }
    if (message.method === 'Runtime.executionContextCreated') {
      const context=message.params.context; if (context.auxData?.isDefault) contexts.set(context.auxData.frameId, context.id);
    }
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails.text);
  };
  const send = async (method, params={}, sessionId) => {
    const id=++next; const result=await new Promise(resolve => { pending.set(id, resolve); ws.send(JSON.stringify({id,method,params,sessionId})); });
    if (result.error) throw new Error(JSON.stringify(result.error)); return result.result;
  };
  const evaluate = async (expression, sessionId) => {
    const result=await send('Runtime.evaluate', {expression, returnByValue:true, awaitPromise:true}, sessionId);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails)); return result.result.value;
  };
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', {width:1200,height:900,deviceScaleFactor:1,mobile:false});
  if (mode === 'legacy') {
    const parts = previewUrl.split('/');
    await send('Page.addScriptToEvaluateOnNewDocument', {source: `if (location.origin === ${JSON.stringify(base)}) localStorage.setItem('aifs.workbench', ${JSON.stringify(JSON.stringify({bookId:parts[2],runId:parts[3]}))});`});
  }
  await send('Page.navigate', {url:base});
  let target;
  for (let n=0; n<100; n++) {
    await sleep(50);
    target=(await send('Target.getTargets')).targetInfos.find(item => item.type === 'iframe' && item.url.endsWith(previewUrl));
    if (target) break;
  }
  assert.ok(target, '历史中的章节应自动进入隔离阅读区');
  const {sessionId:context} = await send('Target.attachToTarget', {targetId:target.targetId,flatten:true});
  await send('Runtime.enable', {}, context);
  let metrics;
  if (mode === 'legacy') {
    await sleep(1500);
    const scope = await evaluate('window.__DATA__?.scope || []', context);
    assert.equal(scope.length, 3, '旧CH04应包含3单元');
    await evaluate("document.querySelector('#result').scrollIntoView()");
    const units = [];
    for (const unit of scope) {
      await evaluate(`location.hash = ${JSON.stringify('/' + unit)}`, context);
      await sleep(350);
      await evaluate(`(async () => { for (const image of document.images) { image.scrollIntoView(); await new Promise(resolve=>setTimeout(resolve,250)); } })()`, context);
      await sleep(300);
      units.push(await evaluate(`({unit:${JSON.stringify(unit)},formulae:document.querySelectorAll('.katex').length,images:[...document.images].map(i=>({src:i.getAttribute('src'),loaded:i.complete&&i.naturalWidth>0}))})`, context));
    }
    metrics = {title:await evaluate('document.title', context), scope, units,
               fontStatus:await evaluate('document.fonts.status', context)};
    assert.ok(units.some(unit=>unit.formulae>0), '旧HTML公式应实际渲染');
    const images = units.flatMap(unit=>unit.images);
    assert.ok(images.length > 0 && images.every(image=>image.loaded), '登记图像应实际加载：' + JSON.stringify(metrics));
  } else {
    // A discovered iframe target may still be navigating; wait for the fixture's
    // real script effect instead of assuming Target.attachToTarget implies load.
    let title;
    for (let n=0; n<60; n++) {
      title = await evaluate('document.title', context);
      if (title === 'preview') break;
      await sleep(50);
    }
    assert.equal(title, 'preview', '登记的相对JS资源应加载');
  }
  assert.equal(await evaluate('(() => { try { return parent.document.title; } catch (e) { return e.name; } })()', context),
               'SecurityError', '生成HTML不能读取工作台DOM/启动令牌');
  assert.equal(await evaluate("fetch('/api/books').then(()=>'leaked', ()=>'blocked')", context), 'blocked',
               '生成HTML不能读取管理API');
  assert.equal(await evaluate("document.querySelector('#preview-frame').getAttribute('sandbox')"), 'allow-scripts');
  assert.equal(exceptions.length, 0, '正常加载不应有JS异常');
  if (screenshot) {
    await evaluate("document.querySelector('#result').scrollIntoView()");
    const capture=await send('Page.captureScreenshot', {format:'png'});
    fs.writeFileSync(screenshot, Buffer.from(capture.data, 'base64'));
  }
  await send('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
  await sleep(100);
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true, '手机宽度不应横向溢出');
  process.stdout.write(JSON.stringify({browser:'Chromium',preview:previewUrl,artifactScript:true,
    parentAccess:'blocked',managementFetch:'blocked',mobileOverflow:false,metrics,exceptions}) + '\n');
}
run().catch(error => { console.error(error); process.exitCode=1; }).finally(() => {
  ws?.close(); chrome.kill(); setTimeout(() => fs.rmSync(profile,{recursive:true,force:true}),100);
});
