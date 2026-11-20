// Real layout check: native details must hide text ranges, not merely add a class.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const render=path.resolve(__dirname,'../../blocks/render');
const executable=[process.env.CHROME_BIN,'/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',path.resolve(__dirname,'../../.devtools/chrome/chrome/chrome-linux64/chrome')].find(p=>p&&fs.existsSync(p));
test('actual browser hides proof and complete examples until clicked while keeping theorem core and adjacent explanations readable',{skip:!executable},async()=>{
 const browser=await chromium.launch({executablePath:executable,headless:true,args:['--no-sandbox']});
 try {
  const page=await browser.newPage();await page.setContent('<!doctype html><body class="book-page"><main id="content"></main></body>');
  await page.addStyleTag({path:path.join(render,'assets/reader-book.css')});
  for(const p of ['vendor/marked.min.js','vendor/katex.min.js','assets/reader-markdown.js','assets/render-core.js','assets/reader-layout.js'])await page.addScriptTag({path:path.join(render,p)});
  await page.evaluate(()=>{
   const raw='**定理1**：条件满足时结论成立。证明：证明秘密内容 $a=a$。证毕。直观解释：相邻解释可读。\n\n### 例3：计算距离\n\n例题秘密题干 $z=3+4i$。\n\n思路：使用距离。\n\n第一步，平方相加。\n\n答案：$5$。\n\n## 后续定义\n\n后续概念可读。';
   const block={group:'body_markdown',json_pointer:'/body_markdown',raw_text:raw,markdown:raw,block_id:'sha256:1234567890123456789012345678901234567890',field_label:'正文',anchor:'reader-12345678901234567890',owner_id:'U'};
   const unit={title:'数学单元',reader_document:{schema_version:'ReaderDocumentV1',blocks:[block]}};
   const renderer=DSHRenderCore.createRenderer({marked,katex});renderer.beginUnit({unitId:'U'});
   document.querySelector('main').innerHTML=renderer.readerDocumentHtml(unit.reader_document,'body');DSHReaderLayout.enhance(document.querySelector('main'),unit,'U');
  });
  async function visible(phrase){return page.evaluate(phrase=>{const walker=document.createTreeWalker(document.querySelector('main'),NodeFilter.SHOW_TEXT);let n;while(n=walker.nextNode()){let i=n.data.indexOf(phrase);if(i<0||!n.parentElement.checkVisibility())continue;const r=document.createRange();r.setStart(n,i);r.setEnd(n,i+phrase.length);if([...r.getClientRects()].some(r=>r.width>0&&r.height>0))return true;}return false;},phrase);}
  for(const text of ['条件满足','相邻解释可读','后续概念可读'])assert.equal(await visible(text),true,text);
  for(const text of ['证明秘密内容','例题秘密题干','平方相加','使用距离'])assert.equal(await visible(text),false,text);
  await page.locator('details.reader-theorem-proof > summary').click();assert.equal(await visible('证明秘密内容'),true);
  await page.locator('details.reader-example-disclosure > summary').click();for(const text of ['例题秘密题干','平方相加','使用距离'])assert.equal(await visible(text),true,text);
  assert.equal(await page.locator('.katex-error,.math-error').count(),0);assert.ok(await page.locator('.katex').count());
 } finally {await browser.close();}
});
