const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),{pathToFileURL}=require('node:url'),{chromium}=require('playwright');
const {execFileSync}=require('node:child_process');
test('real embedded WebGL canvas remains stable while paused and can be scrolled into view',async t=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'aifs-spatial-size-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 execFileSync(process.env.AIFORSTUDY_PYTHON||'python',['tools/build_practice_spatial.py',root],{cwd:path.resolve(__dirname,'../..')});
 fs.writeFileSync(path.join(root,'frame.html'),'<body style="margin:0"><iframe title="实际三维图" src="三维图解.html?scene=sphere&embed=1" style="display:block;width:1008px;height:820px;border:0" sandbox="allow-scripts allow-same-origin"></iframe></body>');
 const b=await chromium.launch({channel:'msedge',headless:true,args:['--enable-unsafe-swiftshader']});t.after(()=>b.close());
 const p=await b.newPage({viewport:{width:1360,height:1050}});await p.goto(pathToFileURL(path.join(root,'frame.html')).href);
 const frame=await (await p.locator('iframe').elementHandle()).contentFrame();
 await frame.waitForFunction(()=>document.querySelector('#sphere[data-mode="webgl"] canvas'));
 await frame.evaluate(()=>document.fonts.ready);
 const samples=[];
 for(let i=0;i<8;i++){await p.waitForTimeout(180);samples.push(await frame.evaluate(()=>({height:document.querySelector('#sphere canvas').getBoundingClientRect().height,scroll:document.documentElement.scrollHeight,playing:document.querySelector('#sphere').dataset.playing})));}
 assert.ok(samples.every(s=>s.playing==='false'));
 assert.ok(Math.max(...samples.map(s=>s.height))-Math.min(...samples.map(s=>s.height))<1,JSON.stringify(samples));
 assert.ok(Math.max(...samples.map(s=>s.scroll))-Math.min(...samples.map(s=>s.scroll))<2,JSON.stringify(samples));
 await frame.locator('#sphere canvas').scrollIntoViewIfNeeded({timeout:2000});
});
test('spatial iframe loads only on request and is destroyed when outer answer closes',async t=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'aifs-spatial-attachment-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));fs.mkdirSync(path.join(root,'assets'));fs.copyFileSync(path.resolve('src/aiforstudy/resources/practice/practice-spatial-attachments.js'),path.join(root,'assets/attachments.js'));
 fs.writeFileSync(path.join(root,'三维图解.html'),'<p>Scene</p><script>window.ticks=0;setInterval(()=>ticks++,10)</script>');fs.writeFileSync(path.join(root,'assets/practice-spatial-sphere.svg'),'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><circle cx="10" cy="10" r="8"/></svg>');fs.writeFileSync(path.join(root,'index.html'),'<head></head><body><details id="outer" open><summary>答案</summary><div id="answer"></div></details><script src="assets/attachments.js"></script><script>AIFSPracticeSpatialAttachments.attach({spatial_scenes:["sphere"]},document.getElementById("answer"),"")</script></body>');
 const b=await chromium.launch({channel:'msedge',headless:true});t.after(()=>b.close());const p=await b.newPage();await p.goto(pathToFileURL(path.join(root,'index.html')).href);assert.equal(await p.locator('iframe').count(),0);await p.locator('.practice-spatial-fold > summary').click();await p.locator('iframe').waitFor();const old=await p.locator('iframe').elementHandle();assert.match(await old.getAttribute('src'),/scene=sphere&embed=1/);await p.locator('#outer > summary').click();await p.waitForFunction(()=>!document.querySelector('iframe'));assert.equal(await old.evaluate(n=>!n.isConnected&&n.src==='about:blank'),true);await p.locator('#outer > summary').click();await p.locator('iframe').waitFor();assert.equal(await p.locator('iframe').count(),1);await p.evaluate(()=>document.getElementById('outer').remove());await p.waitForFunction(()=>!document.querySelector('iframe'));
});
