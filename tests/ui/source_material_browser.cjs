const {chromium}=require(process.argv[2]);
(async()=>{
 const browser=await chromium.launch({executablePath:process.argv[3],headless:true,args:['--no-sandbox']});
 try {
  const page=await browser.newPage();const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',r=>r.request().url().startsWith('http://127.0.0.1:')?r.continue():r.abort());
  await page.goto(process.argv[4]);
  const source=page.locator('[data-reader-pointer="/exercise_regions/0/raw_markdown"]');
  await source.waitFor({state:'attached'});
  await page.locator('details:has([data-reader-pointer="/exercise_regions/0/raw_markdown"]) > summary').click();
  const inline=await source.isVisible()&&(await source.innerText()).includes('[[NOTE:book-literal]]');
  await page.locator('[data-material="material:X1"]').click();
  await page.locator('article.source-material').waitFor();
  const standalone=(await page.locator('#content').innerText()).includes('资料题目原文')&&await page.locator('#content input,#content button').count()===0;
  await page.locator('[data-unit="CH01-S01"]').click();
  await source.waitFor({state:'attached'});
  const back=await page.locator('[data-reader-pointer="/body_markdown"]').isVisible();
  console.log(JSON.stringify({inline,standalone,back,errors}));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
