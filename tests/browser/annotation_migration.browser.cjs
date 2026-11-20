const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

async function pageFor(t, targets = [], items = []) {
  const browser = await chromium.launch({executablePath:process.env.AIFS_TEST_BROWSER,
    args:['--disable-background-networking']});
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://localhost:7777') return route.abort();
    if (url.pathname === '/') return route.fulfill({contentType:'text/html', body:
      '<html data-reader-unit="U" data-reader-ready="ok"><body class="book-page"><header class="bookbar"><div class="book-controls"></div></header><main id="content"><article><p data-shared-anchor="p" data-reader-pointer="/body">现行引文</p></article></main></body></html>'});
    return route.fulfill({json:url.pathname.endsWith('targets') ? {targets} : {items, counts:{}, unit_total:items.length, total:items.length}});
  });
  await page.goto('http://localhost:7777/#/U');
  await page.evaluate(() => {
    window.__DATA__ = {book:'B',scope:['U'],units:{U:{}}};
    window.__AIFS_SHARED_NOTES__ = {book:'B',revision:'v',api:'/api/annotations',targets_api:'/api/annotation-targets'};
  });
  return page;
}

test('historic mismatches stay readable without mounted or cross-edition reveal', async t => {
  const target = {anchor:'a'.repeat(64),kind:'text',quote:'现行引文',locator:{attribute:'data-shared-anchor',value:'p'}};
  const other = {...target,anchor:'b'.repeat(64),editions:['full'],locator:{attribute:'data-shared-anchor',value:'q'}};
  const items = [ {...target, quote:'旧引文'}, {...target, kind:'media'}, {...other, quote:'旧版习题'}, target ]
    .map((row,i) => ({...row, id:i+1,unit_id:'U',text:'感悟'+i,created_at:1,nickname:'原作者'}));
  const page = await pageFor(t, [target,other], items);
  await page.addScriptTag({path:path.resolve('src/aiforstudy/resources/community/shared-notes.js')});
  await page.locator('#community-open').click();
  await page.locator('.community-note').last().waitFor();
  assert.match(await page.locator('#community-open').innerText(), /4/);
  for (const item of items.slice(0,3)) {
    const card = page.locator('[data-note-id="'+item.id+'"]');
    assert.match(await card.innerText(), /位置未核实/);
    assert.ok((await card.innerText()).includes(item.quote));
    assert.equal(await card.getByRole('button',{name:/查看原文/}).count(),0);
  }
  assert.equal(await page.locator('.community-highlight').count(),0);
  assert.equal(page.url(),'http://localhost:7777/#/U');
  await page.locator('[data-note-id="4"] .community-quote').click();
  assert.equal(await page.locator('.community-highlight').count(),1);
});

test('import retains local edits and distinct backup copies across duplicate and concurrent imports', async t => {
  const page = await pageFor(t);
  await page.addScriptTag({path:path.resolve('blocks/render/assets/notes.js')});
  const result = await page.evaluate(async () => {
    await DSHNotes.ready;
    const local = {id:'n',book:'B',bookKey:'local/B',user:'local',scope:'local/B/v/U',version:'v',unit:'U',
      block:'p:/body',kind:'note',text:'本地修改',x:2,y:3,created:1,updated:3};
    const old = {...local,text:'旧文字',updated:2};
    const ink = {...local,kind:'ink',version:'v0',scope:'local/B/v0/U',block:'p:/past',points:[[1,2,.6]],updated:4};
    const pack = items => ({schema_version:'AifsNotesV1',items});
    await DSHNotes.importNotes(pack([local]));
    const payload = pack([old, old, ink, {...local,updated:12}, {...old,id:'legacy',version:'v1',scope:'local/B/v1/U'}]);
    const before = JSON.stringify(payload);
    await Promise.all([DSHNotes.importNotes(payload), DSHNotes.importNotes(payload)]);
    const items = (await DSHNotes.exportNotes()).items;
    await DSHNotes.importNotes(pack(items));
    return {items,again:(await DSHNotes.exportNotes()).items,untouched:before===JSON.stringify(payload),local,old,ink};
  });
  assert.equal(result.untouched,true);
  assert.equal(result.items.length,4);
  assert.deepEqual(result.items,result.again);
  assert.deepEqual(result.items.find(x=>x.id==='n'),result.local);
  for (const expected of [result.old,result.ink]) {
    const found = result.items.find(x=>x.backupOf==='n' && x.kind===expected.kind);
    assert.ok(found);
    const {backupOf,id,...content} = found;
    assert.deepEqual({...content,id:'n'},expected);
  }
  assert.equal(result.items.find(x=>x.id==='legacy').version,'v1');
});

test('file importer reports copies and preserves a different book with a colliding primary key', async t => {
  const page = await pageFor(t);
  await page.addScriptTag({path:path.resolve('blocks/render/assets/notes.js')});
  const incoming = {id:'shared-key',book:'B',bookKey:'local/B',user:'local',scope:'local/B/v/U',version:'v',unit:'U',
    block:'p:/body',kind:'note',text:'本书便签',x:2,y:3,created:1,updated:3};
  const foreign = {...incoming,book:'Other',bookKey:'local/Other',scope:'local/Other/v/U',text:'另一本书'};
  await page.evaluate(async item => {
    await DSHNotes.ready;
    await new Promise((resolve,reject) => {
      const req = indexedDB.open('aifs-notes',1);
      req.onsuccess = () => {
        const db = req.result, tx = db.transaction('items','readwrite');
        tx.objectStore('items').add(item);
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => reject(tx.error);
      };
    });
  }, foreign);
  const chooser = page.waitForEvent('filechooser');
  await page.locator('[data-notes-action="import"]').click();
  const message = new Promise(resolve => page.once('dialog',async dialog => {
    const text = dialog.message(); await dialog.accept(); resolve(text);
  }));
  await (await chooser).setFiles({name:'backup.json',mimeType:'application/json',
    buffer:Buffer.from(JSON.stringify({schema_version:'AifsNotesV1',items:[incoming,incoming]}))});
  assert.match(await message,/备份副本 1 条。已跳过相同笔记 1 条/);
  await page.locator('[data-notes-action="panel"]').click();
  assert.match(await page.locator('.notes-panel').innerText(),/备份副本.*本书便签/);
  const saved = await page.evaluate(() => new Promise(resolve => {
    const req = indexedDB.open('aifs-notes',1);
    req.onsuccess = () => {
      const all = req.result.transaction('items').objectStore('items').getAll();
      all.onsuccess = () => { req.result.close(); resolve(all.result); };
    };
  }));
  assert.equal(saved.length,2);
  assert.deepEqual(saved.find(x=>x.id===foreign.id),foreign);
  assert.equal(saved.find(x=>x.book==='B').backupOf,foreign.id);
});

test('edition landing with a valid anchor but stale quote keeps the reader in place', async t => {
  const row = {anchor:'a'.repeat(64),kind:'text',quote:'现行引文',locator:{attribute:'data-shared-anchor',value:'p'}};
  const page = await pageFor(t,[row]);
  await page.evaluate(anchor => {
    const url = new URL(location.href);
    for (const [name,value] of Object.entries({note_target:anchor,note_unit:'U',note_kind:'text',note_quote:'旧引文'})) {
      url.searchParams.set(name,value);
    }
    history.replaceState(null,'',url);
  }, row.anchor);
  await page.addScriptTag({path:path.resolve('src/aiforstudy/resources/community/shared-notes.js')});
  await page.waitForFunction(()=>document.documentElement.dataset.communityUnit==='U');
  assert.equal(await page.locator('.community-selected').isVisible(),false);
  assert.equal(await page.locator('#community-open').getAttribute('aria-expanded'),'false');
});
