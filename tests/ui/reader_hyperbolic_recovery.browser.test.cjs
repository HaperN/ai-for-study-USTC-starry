const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const{chromium}=require('playwright');
const root=path.resolve(__dirname,'../../blocks/render'),unit=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../fixtures/reader/hyperbolic-recovery.json'),'utf8'));
const executable=[process.env.CHROME_BIN,'/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',path.resolve(__dirname,'../../.devtools/chrome/chrome/chrome-linux64/chrome')].find(p=>p&&fs.existsSync(p));
test('recovered hyperbolic derivations and all example media are hidden by native disclosure until explicit expansion',{skip:!executable},async()=>{
 const browser=await chromium.launch({executablePath:executable,headless:true,args:['--no-sandbox']});try{
  const page=await browser.newPage();await page.setContent('<!doctype html><body class="book-page"><main id="content"></main></body>');
  for(const filename of ['assets/reader-book.css','vendor/katex.min.css'])if(fs.existsSync(path.join(root,filename)))await page.addStyleTag({path:path.join(root,filename)});
  for(const filename of ['vendor/marked.min.js','vendor/katex.min.js','assets/reader-markdown.js','assets/render-core.js','assets/reader-layout.js'])await page.addScriptTag({path:path.join(root,filename)});
  await page.evaluate(unit=>{window.__unit=unit;window.__before=JSON.stringify(unit);const renderer=DSHRenderCore.createRenderer({marked,katex});renderer.beginUnit({unitId:unit.unit_id});const root=document.querySelector('main');root.innerHTML=renderer.readerDocumentHtml(unit.reader_document,'body',unit.reader_media,unit.reader_layout);DSHReaderLayout.enhance(root,unit,unit.unit_id);},unit);
  const state=await page.evaluate(()=>({proofs:[...document.querySelectorAll('[data-reader-recovery-proof]')].map(n=>({closed:!n.open,bodyVisible:n.querySelector('p').checkVisibility()})),media:[...document.querySelectorAll('[data-media-anchor]')].map(n=>n.checkVisibility()),statements:[...document.querySelectorAll('blockquote')].filter(n=>n.textContent.includes('性质·双曲函数')).map(n=>n.checkVisibility()),untouched:JSON.stringify(window.__unit)===window.__before}));
  assert.equal(state.proofs.length,6);state.proofs.forEach(p=>{assert.equal(p.closed,true);assert.equal(p.bodyVisible,false);});assert.equal(state.media.length,3);assert.ok(state.media.every(v=>!v));assert.ok(state.statements.length>=5&&state.statements.every(Boolean));assert.equal(state.untouched,true);
  await page.locator('[data-reader-recovery-proof]').first().locator('summary').first().click();assert.equal(await page.locator('[data-reader-recovery-proof]').first().locator('p').first().isVisible(),true);
  const example=page.locator('[data-reader-topic-kind="example"][data-reader-topic-title="例题"] > details');await example.locator(':scope > summary').click();assert.equal(await page.locator('[data-media-anchor]').first().evaluate(n=>n.checkVisibility()),true);
 }finally{await browser.close();}
});
