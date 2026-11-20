const {sourceUnit}=require('./note-source.cjs');
/* Real two-browser acceptance; no account, external requests or model calls. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),readline=require('node:readline'),{chromium}=require('playwright');
const ROOT=path.resolve(__dirname,'../..');
function fixture(directory){const book=path.join(directory,'book');fs.mkdirSync(path.join(book,'data'),{recursive:true});
 fs.writeFileSync(path.join(book,'data/data.js'),'window.__DATA__ = '+JSON.stringify({book:'shared-fixture',scope:['CH01','CH02'],units:{CH01:sourceUnit(['复数的模表示到原点的距离。','相同内容。','相同内容。']),CH02:sourceUnit(['解析函数的第二节正文。','相同内容。','相同内容。'])}})+';');
 fs.writeFileSync(path.join(book,'index.html'),`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>共享感悟验收</title><style>body{font:18px sans-serif;margin:24px}main{max-width:800px}p{margin:20px 0;padding:15px}.bookbar{backdrop-filter:blur(8px);height:64px}button,input,textarea{font:inherit}</style><header class="bookbar"><div class="book-controls"></div></header><nav><a href="#/CH01">第一节</a> <a href="#/CH02">第二节</a></nav><main id="content"></main><script src="data/data.js"></script><script>function render(){const id=location.hash==='#/CH02'?'CH02':'CH01';document.querySelector('main').innerHTML='<article><h1>'+id+'</h1><div data-reader-pointer="/body_markdown"><p data-shared-anchor="field0:0">'+(id==='CH01'?'复数的模表示到原点的距离。':'解析函数的第二节正文。')+'</p><p data-shared-anchor="field1:0">相同内容。</p><p data-shared-anchor="field2:0">相同内容。</p></div></article>';document.documentElement.dataset.readerUnit=id;document.documentElement.dataset.readerReady='ok';}addEventListener('hashchange',render);render();</script></html>`);return book;}
async function service(reader,database){const python=process.env.AIFS_TEST_PYTHON||'python3';const proc=spawn(python,['-m','aiforstudy','community','--reader',reader,'--database',database,'--port','0'],{cwd:ROOT,env:{...process.env,PYTHONPATH:path.join(ROOT,'src'),PYTHONDONTWRITEBYTECODE:'1',NO_PROXY:'127.0.0.1,localhost',no_proxy:'127.0.0.1,localhost'},stdio:['ignore','pipe','pipe']});let stderr='';proc.stderr.on('data',d=>stderr+=d);const lines=readline.createInterface({input:proc.stdout});const info=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{proc.kill();reject(Error('server startup timeout '+stderr))},10000);lines.once('line',l=>{clearTimeout(timer);try{resolve(JSON.parse(l))}catch(e){reject(e)}});proc.once('exit',code=>{clearTimeout(timer);reject(Error('server exit '+code+' '+stderr))})});return {proc,url:info.url,stop:async()=>{if(proc.exitCode===null){proc.kill('SIGTERM');await new Promise(r=>proc.once('exit',r))}lines.close()}};}

test('nickname notes are shared, persistent, scoped and only deletable by the posting browser', {timeout:60000},async t=>{
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'aifs-community-'));t.after(()=>fs.rmSync(temp,{recursive:true,force:true}));
 const reader=fixture(temp),database=path.join(temp,'notes.db');let current=await service(reader,database);t.after(async()=>current.stop());
 const browser=await chromium.launch({executablePath:process.env.AIFS_TEST_BROWSER||'/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',headless:true});t.after(()=>browser.close());
 const ca=await browser.newContext(),cb=await browser.newContext(),a=await ca.newPage(),b=await cb.newPage();const errors=[];a.on('pageerror',e=>errors.push(e.message));b.on('pageerror',e=>errors.push(e.message));
 await a.goto(current.url);await b.goto(current.url);
 await a.locator('#community-open').click();await a.locator('.community-note-mark').first().click();
 await a.locator('#community-nickname').fill('同一个昵称');await a.locator('#community-text').fill('我的感悟：距离的几何意义。');await a.locator('.community-submit').click();
 await a.locator('.community-note-text').filter({hasText:'我的感悟：距离的几何意义。'}).waitFor();
 await b.locator('#community-open').click();await b.locator('.community-refresh').click();await b.locator('.community-note-text').filter({hasText:'我的感悟：距离的几何意义。'}).waitFor();
 assert.equal(await b.locator('.community-delete').count(),0);
 // Same nickname from another real browser is not owner authority.
 await b.locator('.community-note-mark').first().click();await b.locator('#community-nickname').fill('同一个昵称');await b.locator('#community-text').fill('另一位同学');await b.locator('.community-submit').click();await b.locator('.community-note-text').filter({hasText:'另一位同学'}).waitFor();
 const state=await a.evaluate(async()=>{const token=localStorage.getItem('aifs:community:author');const r=await fetch('/api/annotations?unit=CH01',{headers:{Authorization:'Bearer '+token}});return r.json()});
 const aid=state.items.find(x=>x.text==='我的感悟：距离的几何意义。').id;
 assert.equal(await b.evaluate(async id=>(await fetch('/api/annotations/'+id,{method:'DELETE',headers:{Authorization:'Bearer '+localStorage.getItem('aifs:community:author'),'X-AIFS-Book-Revision':window.__AIFS_SHARED_NOTES__.revision}})).status,aid),403);
 await a.reload();await a.locator('#community-open').click();await a.locator('.community-note-text').filter({hasText:'我的感悟：距离的几何意义。'}).waitFor();
 await a.locator('a[href="#/CH02"]').click();await a.waitForFunction(()=>document.documentElement.dataset.communityUnit==='CH02');await a.locator('.community-refresh').click();await a.locator('.community-empty').waitFor();assert.equal(await a.locator('.community-note-text').count(),0);
 await current.stop();current=await service(reader,database);
 // A different port is a different browser-storage origin, so verify durable server data with B.
 await b.goto(current.url);await b.locator('#community-open').click();await b.locator('.community-note-text').filter({hasText:'我的感悟：距离的几何意义。'}).waitFor();
 const savedToken=await ca.storageState(); // Original origin data remains in A's context.
 assert.ok(savedToken.origins.some(o=>o.localStorage.some(x=>x.name==='aifs:community:author')));
 const token=savedToken.origins.flatMap(o=>o.localStorage).find(x=>x.name==='aifs:community:author').value;
 await a.goto(current.url);await a.evaluate(t=>localStorage.setItem('aifs:community:author',t),token);await a.reload();await a.locator('#community-open').click();
 const own=a.locator('.community-note').filter({hasText:'我的感悟：距离的几何意义。'});await own.locator('.community-delete').click();await own.locator('.community-delete').click();await own.waitFor({state:'detached'});
 await b.locator('.community-refresh').click();await b.waitForFunction(()=>!document.querySelector('.community-list').textContent.includes('我的感悟：距离的几何意义。'));
 await a.setViewportSize({width:390,height:844});await a.waitForTimeout(50);assert.equal(await a.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.ok(await a.locator('#community-open').evaluate(e=>e.getBoundingClientRect().top>innerHeight/2),'mobile entry must not overlap the reader header');
 // On a short mobile chapter, dismiss the panel but retain zero-count entry buttons.
 await a.locator('.community-select').click();await a.locator('.community-panel').waitFor({state:'hidden'});
 await a.locator('.community-note-mark').last().click();await a.locator('.community-form').waitFor();
 await a.locator('#community-nickname').fill('');await a.locator('#community-nickname').pressSequentially('😀'.repeat(30));
 assert.equal(Array.from(await a.locator('#community-nickname').inputValue()).length,30,'30 Unicode characters must fit the nickname limit');
 await a.locator('#community-text').fill('最后一段也可以写感悟');await a.locator('.community-submit').click();await a.locator('.community-note-text').filter({hasText:'最后一段也可以写感悟'}).waitFor();
 assert.deepEqual(errors,[]);
});
