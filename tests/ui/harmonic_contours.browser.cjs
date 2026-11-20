/* Real Chromium operates the production SVG template; the fixture serves only local assets. */
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const http=require('node:http');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'), destination=process.argv[2];
const spec={id:'H1',template:'harmonic-contours',kind:'svg',params:{initial_point:[1,1],extent:3},fallback_text:'参数不可用'};
const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>共轭调和等值线</title>
<link rel="stylesheet" href="/styles.css"><style>main{max-width:720px;margin:auto;padding:16px}h1{font-size:25px}</style>
<main><h1>移动交点，观察两族等值线</h1><figure class="model model-canvas" data-model-id="H1"><h2 class="model-title">f(z)=z²：实部与虚部的等值线</h2><div class="model-stage"></div><div class="model-ui"></div></figure></main>
<script src="/models.js"></script><script>Models.mountAll(document,{H1:${JSON.stringify(spec)}})</script></html>`;
const requests=[];
const server=http.createServer((req,res)=>{
  requests.push(req.url);
  res.setHeader('Content-Security-Policy',"default-src 'self';script-src 'self' 'unsafe-inline';style-src 'self' 'unsafe-inline';connect-src 'none'");
  if(req.url==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);}
  else if(['/styles.css','/models.js'].includes(req.url)){res.setHeader('Content-Type',req.url.endsWith('.js')?'text/javascript':'text/css');res.end(fs.readFileSync(path.join(root,'blocks/render/assets',req.url.slice(1))));}
  else{res.statusCode=404;res.end();}
});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let chrome,ws,profile;
async function run(){
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const base='http://127.0.0.1:'+server.address().port;
  profile=fs.mkdtempSync(path.join(os.tmpdir(),'harmonic-browser-'));
  chrome=spawn(path.join(root,'.devtools/chrome/chrome-linux64/chrome'),['--headless=new','--no-sandbox','--disable-dev-shm-usage','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank']);
  let endpoint;chrome.stderr.on('data',b=>{const m=String(b).match(/ws:\/\/\S+/);if(m)endpoint=m[0];});
  for(let i=0;i<100&&!endpoint;i++)await sleep(50);
  assert.ok(endpoint,'Chromium started');
  const tabs=await(await fetch('http://127.0.0.1:'+new URL(endpoint).port+'/json/list')).json();
  ws=new WebSocket(tabs.find(t=>t.type==='page').webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});
  let id=0;const pending=new Map(),exceptions=[],external=[];
  ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){pending.get(m.id)?.(m);pending.delete(m.id);}
    if(m.method==='Runtime.exceptionThrown')exceptions.push(m.params.exceptionDetails.text);
    if(m.method==='Network.requestWillBeSent'&&!m.params.request.url.startsWith(base))external.push(m.params.request.url);
  };
  async function send(method,params={}){const n=++id;const m=await new Promise(r=>{pending.set(n,r);ws.send(JSON.stringify({id:n,method,params}));});if(m.error)throw Error(JSON.stringify(m.error));return m.result;}
  async function evaluate(expression){const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
  const read=key=>evaluate(`document.querySelector('[data-harmonic-value="${key}"]').textContent`);
  const screenshot=async name=>{if(destination){fs.mkdirSync(destination,{recursive:true});fs.writeFileSync(path.join(destination,name+'.png'),Buffer.from((await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true})).data,'base64'));}};
  await send('Page.enable');await send('Runtime.enable');await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:900,height:1250,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:base});
  let mounted=false;
  for(let i=0;i<100;i++){mounted=await evaluate(`Boolean(document.querySelector('[data-harmonic-value="angle"]'))`);if(mounted)break;await sleep(50);}
  assert.equal(mounted,true,'nonempty SVG interaction mounted');
  assert.equal(await read('u'),'0.00');assert.equal(await read('v'),'2.00');
  await evaluate(`{const x=document.querySelector('input[data-axis="x"]');x.value=2;x.dispatchEvent(new Event('input',{bubbles:true}));}`);
  assert.equal(await read('u'),'3.00');assert.equal(await read('v'),'4.00');
  assert.equal(await read('grad-u'),'(4.00, −2.00)');assert.equal(await read('angle'),'90°');
  const box=await evaluate(`(()=>{const r=document.querySelector('svg.harmonic-plot').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};})()`);
  const at=(x,y)=>({x:box.x+(44+(x+3)/6*432)/520*box.width,y:box.y+(44+(3-y)/6*432)/520*box.height});
  await send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...at(2,1)});
  await send('Input.dispatchMouseEvent',{type:'mouseMoved',button:'left',buttons:1,...at(1.5,-0.5)});
  await send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...at(1.5,-0.5)});
  assert.equal(await read('u'),'2.00');assert.equal(await read('v'),'-1.50');
  await evaluate(`document.querySelectorAll('.harmonic-options input')[1].click()`);
  assert.equal(await evaluate(`document.querySelector('[data-gradient="u"]').getAttribute('visibility')`),'visible');
  await screenshot('P6-harmonic-desktop');
  await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent==='查看原点例外').click()`);
  assert.equal(await read('angle'),'不适用');
  assert.match(await evaluate(`document.querySelector('.harmonic-status').textContent`),/不能.*正交/);
  assert.equal(await evaluate(`document.querySelector('[data-gradient="u"]').getAttribute('visibility')`),'hidden');
  await screenshot('P6-harmonic-origin');
  await send('Emulation.setDeviceMetricsOverride',{width:390,height:1000,deviceScaleFactor:1,mobile:true});
  await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent==='重置选点').click()`);
  await sleep(100);
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true,'390px must not overflow');
  const ratio=await evaluate(`(()=>{const r=document.querySelector('svg.harmonic-plot').getBoundingClientRect();return r.width/r.height;})()`);
  assert.ok(Math.abs(ratio-1)<0.01,'coordinate scales must remain equal on mobile');
  await screenshot('P6-harmonic-mobile');
  assert.deepEqual(exceptions,[]);assert.deepEqual(external,[],'no network dependencies');
  assert.ok(!requests.some(url=>/mathbox|three/.test(url)),'SVG must not initialize WebGL');
  process.stdout.write(JSON.stringify({browser:'Chromium',slider:{u:3,v:4},drag:{x:1.5,y:-0.5,u:2,v:-1.5},origin:'not_applicable',mobileOverflow:false,equalAxes:true,exceptions,external})+'\n');
}
run().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{ws?.close();chrome?.kill();server.close();if(profile)setTimeout(()=>fs.rmSync(profile,{recursive:true,force:true}),100);});
