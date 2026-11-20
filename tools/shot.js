/* 真·实时截图工具：用 CDP 打开页面 → **真的等几秒** → 截整页。
 *
 * 为什么不用 `--screenshot --virtual-time-budget`（以前一直用的那招）：
 * 虚拟时间会把页面的 setTimeout 快进，但 MathBox/WebGL 的初始化要**真实的 GPU 时间**，
 * 于是"等 3 秒"在虚拟时间里几毫秒就跑完了 —— 模型还没起来就被判定失败（实测踩过）。
 *
 * 用法：
 *   node tools/shot.js <url> <out.png> [--wait 6000] [--width 1400] [--height 900]
 * 依赖：Node 22+（自带 WebSocket）；无头 Chrome 在 .devtools/chrome/。
 * 退出码：0 成功 / 3 没有无头浏览器（跳过）。
 */
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

const args = process.argv.slice(2);
const url = args[0];
const out = args[1];
function opt(name, dflt) {
  const i = args.indexOf("--" + name);
  return i >= 0 ? parseFloat(args[i + 1]) : dflt;
}
const waitMs = opt("wait", 6000);
const evalIdx = args.indexOf("--eval");
const evalJs = evalIdx >= 0 ? args[evalIdx + 1] : null;
const width = opt("width", 1400);
const height = opt("height", 900);
/* --viewport y ：只截**当前视口**（先把页面滚到 y），不要整页。
   为什么需要它：整页截图长到两万多像素时，Chrome 用 swiftshader 分块合成会出错 ——
   实测截图右半边出现一条**斜的空白**，看着像 CSS 坏了，其实页面是好的（白查半天）。
   看细节一律用 --viewport。 */
const viewportY = opt("viewport", -1);

if (!url || !out) {
  console.error("用法：node tools/shot.js <url> <out.png> [--wait 6000] [--width 1400]");
  process.exit(2);
}

const root = path.dirname(__dirname);
const chromePath = path.join(root, ".devtools/chrome/chrome-linux64/chrome");
if (!fs.existsSync(chromePath)) {
  console.error("没有无头浏览器（.devtools/chrome/…），跳过。装法见方案附录 B.5。");
  process.exit(3);
}

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "shot_"));
const chrome = spawn(chromePath, [
  "--headless=old", "--no-sandbox", "--disable-dev-shm-usage",
  "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
  "--hide-scrollbars", "--remote-debugging-port=0",
  "--window-size=" + width + "," + height,
  "--user-data-dir=" + profile, "about:blank",
], { stdio: ["ignore", "pipe", "pipe"] });

let wsUrl = null;
chrome.stderr.on("data", (buf) => {
  const m = /ws:\/\/[^\s]+/.exec(String(buf));
  if (m) wsUrl = m[0];
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  for (let i = 0; i < 100 && !wsUrl; i++) await sleep(100);
  if (!wsUrl) throw new Error("拿不到 DevTools 地址");
  const port = new URL(wsUrl).port;
  const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = list.find((t) => t.type === "page") || list[0];
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0;
  const pending = new Map();
  const logs = [];
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg.result || msg.error); pending.delete(msg.id); }
    if (msg.method === "Runtime.consoleAPICalled") {
      logs.push("[" + msg.params.type + "] " + msg.params.args.map((a) => a.value === undefined ? a.description : a.value).join(" "));
    }
  };
  const send = (method, params) => new Promise((res) => {
    const myId = ++id;
    pending.set(myId, res);
    ws.send(JSON.stringify({ id: myId, method, params: params || {} }));
  });

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url });
  await sleep(waitMs);                       // ← 真的等（这一条就是本工具存在的理由）

  /* 顺手把页面里关键元素的位置带回来（模型卡片、画布），方便我精确裁图 */
  const rects = await send("Runtime.evaluate", {
    expression: `JSON.stringify(Array.from(document.querySelectorAll('[data-model-id], canvas, .unit')).map(e => {
      const r = e.getBoundingClientRect();
      return {sel: e.tagName.toLowerCase() + (e.dataset && e.dataset.modelId ? '#' + e.dataset.modelId : ''),
              x: Math.round(r.x), y: Math.round(r.y + window.scrollY), w: Math.round(r.width), h: Math.round(r.height)};
    }))`, returnByValue: true,
  });
  if (rects && rects.result && rects.result.value) {
    const list = JSON.parse(rects.result.value).filter((r) => r.w > 0);
    if (list.length) console.log("元素位置（截图坐标）：\n  " +
      list.map((r) => `${r.sel} ${r.w}x${r.h} @ ${r.x},${r.y}`).join("\n  "));
  }
  if (viewportY >= 0) {
    await send("Runtime.evaluate", { expression: `window.scrollTo(0, ${viewportY}); 1`, returnByValue: true });
    await sleep(400);
  }
  if (evalJs) {
    /* awaitPromise：`--eval` 里常常要"点一下、等页面动完再看结果"，
       所以返回 Promise 时必须等它 —— 否则拿到的是 `{}`（实测踩过：
       P-035 的编号跳转验证第一次跑出来就是一串空对象，白高兴一场）。 */
    const r = await send("Runtime.evaluate", { expression: evalJs, returnByValue: true,
                                               awaitPromise: true });
    const v = r && r.result ? r.result.value : r;
    console.log("—eval—\n" + (typeof v === "string" ? v : JSON.stringify(v, null, 1)));
  }
  const shot = await send("Page.captureScreenshot",
    { format: "png", captureBeyondViewport: viewportY < 0 });
  if (!shot || !shot.data) throw new Error("截图失败：" + JSON.stringify(shot));
  fs.writeFileSync(out, Buffer.from(shot.data, "base64"));
  console.log("截图:", out, "(" + Math.round(fs.statSync(out).size / 1024) + " KB)");
  const interesting = logs.filter((l) => /模型|错误|失败|error|Error/.test(l));
  if (interesting.length) console.log("页面日志（模型/错误相关）：\n  " + interesting.join("\n  "));
  ws.close();
}

main().then(() => { chrome.kill(); process.exit(0); })
  .catch((e) => { console.error("出错：" + (e && e.message)); chrome.kill(); process.exit(1); });
