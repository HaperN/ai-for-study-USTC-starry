// A real-reader smoke can point at an offline disposable v0.3 practice-attached reader.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const readline = require('node:readline');
const {chromium} = require('playwright');

test('mounted reader reveals nested reviewed media and preserves older private versions', {
  skip: !process.env.AIFS_TEST_REAL_READER, timeout: 150000
}, async t => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'aifs-real-notes-'));
  t.after(()=>fs.rmSync(tmp,{recursive:true,force:true}));
  const server = spawn(process.env.AIFS_TEST_PYTHON, ['-u','-c', [
    'import sys',
    'from aiforstudy.community.server import make_server',
    'class Offline:',
    ' def recognize(self,*args): raise AssertionError("no recognition expected")',
    's=make_server(sys.argv[1],sys.argv[2],port=0,recognizer=Offline())',
    'print(s.server_port,flush=True)',
    's.serve_forever()'
  ].join('\n'), process.env.AIFS_TEST_REAL_READER,path.join(tmp,'notes.db')], {stdio:['ignore','pipe','inherit']});
  t.after(async()=>{if(server.exitCode===null&&server.signalCode===null){server.kill();await once(server,'exit');}});
  const lines=readline.createInterface({input:server.stdout});
  const [port]=await once(lines,'line');lines.close();
  const origin='http://127.0.0.1:'+port;
  const browser=await chromium.launch({executablePath:process.env.AIFS_TEST_BROWSER||'/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'});
  t.after(()=>browser.close());
  const page=await browser.newPage();
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  const uid='CH08-S04-SS02';
  await page.goto(origin+'/?practice=full#/'+uid);
  await page.waitForFunction(id=>document.documentElement.dataset.communityUnit===id,uid);
  const result=await page.evaluate(async uid=>{
    const cfg=window.__AIFS_SHARED_NOTES__;
    const {targets}=await (await fetch('/api/annotation-targets?unit='+uid)).json();
    const media=targets.find(t=>t.locator.attribute==='data-practice-media-id'&&t.locator.value==='mapping');
    const body={unit_id:uid,anchor:media.anchor,kind:media.kind,quote:media.quote,nickname:'离线验收',text:'核对动画',request_id:'test-media-request'};
    const r=await fetch('/api/annotations',{method:'POST',headers:{'Content-Type':'application/json',
      'Authorization':'Bearer '+'a'.repeat(64),'X-AIFS-Book-Revision':cfg.revision,'X-AIFS-Practice-Revision':cfg.practice_revision},body:JSON.stringify(body)});
    const fullOnly=targets.find(t=>t.locator.attribute==='data-practice-id'&&t.editions.length===1&&t.editions[0]==='full');
    const second=await fetch('/api/annotations',{method:'POST',headers:{'Content-Type':'application/json',
      'Authorization':'Bearer '+'a'.repeat(64),'X-AIFS-Book-Revision':cfg.revision,'X-AIFS-Practice-Revision':cfg.practice_revision},
      body:JSON.stringify({...body,anchor:fullOnly.anchor,kind:fullOnly.kind,quote:fullOnly.quote,text:'完整版习题',request_id:'test-full-question'})});
    if(second.status!==201)throw Error('full-only question POST '+second.status);
    await window.DSHNotes.ready;
    const note={id:'legacy',book:cfg.book,user:'local',unit:uid,block:'p:/body_markdown',version:'unversioned',text:'私人旧便签',kind:'note',created:1,
      scope:['local',cfg.book,'unversioned',uid].join('/'),bookKey:'local/'+cfg.book};
    await window.DSHNotes.importNotes({schema_version:'AifsNotesV1',items:[note]});
    return {status:r.status,privateVersion:DSHNotes.version,revision:cfg.revision,note};
  },uid);
  assert.equal(result.status,201);
  assert.equal(result.privateVersion,result.revision);
  await page.locator('.notes-collapse').focus();
  await page.locator('[data-notes-action="panel"]').click();
  await page.locator('.notes-panel details summary').click();
  await page.getByText('unversioned · '+uid+' · 私人旧便签').waitFor();
  assert.deepEqual(await page.evaluate(async()=> (await DSHNotes.exportNotes()).items),[result.note]);
  await page.locator('.notes-collapse').focus();
  await page.locator('[data-notes-action="panel"]').click();
  await page.locator('#community-open').click();await page.locator('.community-refresh').click();
  await page.locator('.community-note').filter({hasText:'核对动画'}).locator('.community-quote').click();
  const media=page.locator('[data-practice-media-id=mapping]');
  assert.equal(await media.evaluate(n=>!!n.closest('details:not([open])')),false);
  assert.ok(await media.isVisible());
  await page.locator('.community-close').click();
  await page.goto(origin+'/?practice=basic#/'+uid);
  await page.locator('#community-open').click();
  await page.locator('.community-note').filter({hasText:'完整版习题'}).locator('.community-quote').click();
  await page.waitForFunction(()=>new URL(location.href).searchParams.get('practice')==='full'&&
    !new URL(location.href).searchParams.has('note_target')&&document.querySelector('.practice-inline[data-practice-id="GEN-CV8"]')?.open);
  await page.locator('.community-close').click();
  const units=await page.evaluate(()=>__DATA__.scope);
  let compared=0, conclusions=0, coordinates=0;
  for(const unit of units){
    await page.evaluate(id=>{location.hash='#/'+id;},unit);
    await page.waitForFunction(id=>document.documentElement.dataset.communityUnit===id&&
      !!document.querySelector('[data-shared-anchor] .community-note-mark'),unit);
    const match=await page.evaluate(async uid=>{
      const rows=(await (await fetch('/api/annotation-targets?unit='+uid)).json()).targets;
      const main=document.querySelector('#content');
      if(rows.some(t=>/^\[\[TEXTBOOK_END_INTEGRATED\]\]$/.test(t.quote)))throw Error('discarded end marker in '+uid);
      const clean=node=>{
        const copy=node.cloneNode(true);
        copy.querySelectorAll('.reader-link-kind[aria-hidden=true]').forEach(label=>label.remove());
        return window.AIFSCommunity.canonicalText(copy).replace(/\s+/g,' ').trim();
      };
      const bound=new Map([...main.querySelectorAll('[data-shared-anchor]')].map(n=>[n.dataset.sharedAnchor,n]));
      let verified=0;
      for(const t of rows.filter(row=>row.locator.attribute==='data-shared-anchor')){
        const n=bound.get(t.locator.value);if(!n)throw Error('missing rendered block '+uid+' '+t.locator.value);
        const start=Number(n.dataset.sharedStart),end=Number(n.dataset.sharedEnd);
        if(start!==t.source.start_cp||end!==t.source.end_cp||n.closest('[data-reader-block]')?.dataset.readerBlock!==t.source.field)
          throw Error('rendered source coordinate differs '+uid+' '+t.locator.value);
        verified++;
      }
      const plain=rows.filter(t=>t.locator.attribute==='data-shared-anchor'&&t.quote.length<600&&
        !/[<>\[\]\\$`*_#&~]/.test(t.quote));
      let count=0, checked=0;
      for(const t of plain){
        const n=main.querySelector('[data-shared-anchor="'+CSS.escape(t.locator.value)+'"]');
        if(!n||!n.matches('p'))continue;
        // Bare mathematics can acquire markup without authored math delimiters.
        // The reader deliberately expands some theorem-link labels after rendering.
        if(n.querySelector('.katex,.reader-theorem-link'))continue;
        const quote=t.quote.replace(/\s+/g,' ').trim();
        if(clean(n)!==quote)throw Error('rendered paragraph differs '+uid+' '+t.locator.value+' actual '+clean(n)+' expected '+quote+' html '+n.innerHTML.slice(0,350));
        count++;
      }
      for(const t of rows.filter(t=>/^data-textbook-(result|supplement)$/.test(t.locator.attribute))){
        const actual=main.querySelector('['+t.locator.attribute+'="'+CSS.escape(t.locator.value)+'"] .lesson-integrated-statement');
        if(!actual)continue;
        const ranges=[...actual.querySelectorAll('[data-shared-anchor]')];
        const first=rows.find(other=>other.locator.attribute==='data-shared-anchor'&&other.locator.value===ranges[0]?.dataset.sharedAnchor);
        if(!first||!t.quote.startsWith(first.quote.slice(0,80)))throw Error('conclusion excerpt differs '+t.locator.value);
        checked++;
        if(t.locator.value==='CH01-S01-SS01-C001'&&(!t.quote.startsWith('方程 $x^2+1=0$ 在实数中没有解')||!clean(actual).startsWith('方程 $x^2+1=0$ 在实数中没有解')))
          throw Error('CH01-S01-SS01-C001 must quote its displayed passage');
      }
      return [count,checked,verified];
    },unit);
    compared+=match[0];conclusions+=match[1];coordinates+=match[2];
  }
  assert.ok(compared>1000&&conclusions===418&&coordinates===9255);
  console.log('Matched plain rendered paragraphs',compared,'integrated conclusions',conclusions,'rendered source ranges',coordinates);
  console.log('Verified source markers on',units.length,'units and nested practice-media reveal.');
  assert.deepEqual(errors,[]);
});
