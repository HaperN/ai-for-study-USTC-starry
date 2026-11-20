const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {createInterface} = require('node:readline');
const {chromium} = require('playwright');
const {sourceUnit} = require('./note-source.cjs');

test('读教材、打开练习、回到原单元并发布修改删除感悟', {timeout:45000}, async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'integrated-browser-'));
  t.after(() => fs.rmSync(root,{recursive:true,force:true}));
  const reader = path.join(root,'practice','教材');
  fs.mkdirSync(path.join(reader,'data'), {recursive:true});
  fs.mkdirSync(path.join(reader,'assets'));
  const passage = '复数的模表示到原点的距离。';
  const unit = sourceUnit([passage]);
  const book = {book:'复变函数A',scope:['CH01'],units:{CH01:unit}};
  fs.writeFileSync(path.join(reader,'data/data.js'),'window.__DATA__='+JSON.stringify(book)+';');
  const q = {id:'Q1',stem:'求 i 的模。',unit_id:'CH01'};
  fs.writeFileSync(path.join(reader,'assets/practice-data.js'),'window.PRACTICE_EDITIONS='+JSON.stringify({basic:[q]})+';');
  fs.writeFileSync(path.join(reader,'index.html'),`<!doctype html><html lang="zh-CN" data-reader-ready="ok" data-reader-unit="CH01">
    <meta charset="utf-8"><script src="data/data.js"></script><body><header class="bookbar"><div class="book-controls"></div></header>
    <main id="content"><article><h1>复数的模</h1><p data-shared-anchor="field0:0">${passage}</p></article></main></body></html>`);
  fs.writeFileSync(path.join(root,'practice','index.html'),'<title>复变函数</title>');
  fs.writeFileSync(path.join(root,'practice','基础版-习题集.html'),
    `<meta charset="utf-8"><body><h1>${q.stem}</h1><a href="教材/index.html?practice=basic#/CH01">回到教材对应讲解</a></body>`);
  fs.writeFileSync(path.join(root,'config.json'),JSON.stringify({reader:'practice/教材',practice:'practice',database:'notes.db',port:0}));
  const proc = spawn(process.env.AIFS_TEST_PYTHON || 'python3',['-m','aiforstudy','integrated','--config',path.join(root,'config.json')],
    {stdio:['ignore','pipe','pipe']});
  let stderr = '';
  proc.stderr.on('data',d => stderr += d);
  const exited = once(proc,'exit');
  t.after(async () => { if (proc.exitCode === null) proc.kill('SIGTERM'); await exited; });
  const lines = createInterface({input:proc.stdout});
  const [line] = await Promise.race([once(lines,'line'),exited.then(() => {throw Error(stderr);})]);
  const {url} = JSON.parse(line);
  const browser = await chromium.launch({executablePath:process.env.AIFS_TEST_BROWSER || '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'});
  t.after(() => browser.close());
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror',e => errors.push(e.message));
  await page.goto(url);
  await page.getByRole('link',{name:'阅读教材与共享感悟'}).click();
  await page.getByText(passage,{exact:true}).waitFor();
  await page.getByRole('link',{name:'练习目录'}).click();
  await page.getByRole('link',{name:'基础版-习题集'}).click();
  await page.getByRole('heading',{name:q.stem}).waitFor();
  await page.getByRole('link',{name:'回到教材对应讲解'}).click();
  assert.equal(new URL(page.url()).pathname,'/');
  assert.equal(new URL(page.url()).hash,'#/CH01');
  await page.locator('#community-open').click();
  await page.locator('.community-note-mark').first().click();
  await page.locator('#community-nickname').fill('同学');
  await page.locator('#community-text').fill('模长等于一。');
  await page.locator('.community-submit').click();
  await page.locator('.community-edit').click();
  await page.locator('.community-edit-text').fill('单位圆上的点模长均为一。');
  await page.locator('.community-edit-save').click();
  await page.getByText('单位圆上的点模长均为一。',{exact:true}).waitFor();
  await page.locator('.community-delete').click();
  await page.locator('.community-delete').click();
  await page.locator('.community-note').waitFor({state:'detached'});
  assert.deepEqual(errors,[]);
});
