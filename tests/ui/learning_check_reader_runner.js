// Test-only browser driver. No author code or model expressions are executed.
const {chromium} = require(process.argv[2]);
const chrome = process.argv[3], url = process.argv[4], evidence = process.argv[5];
(async function () {
  const browser = await chromium.launch({executablePath:chrome,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const results = {};
  for (const width of [1280,390]) {
    const context = await browser.newContext({viewport:{width,height:900}});
    const page = await context.newPage();
    await page.goto(url);
    results[width] = {};
    results[width].grading=await page.evaluate(()=>{
      const renderer=window.DSHRenderCore.createRenderer({marked:window.marked,katex:window.katex});
      const numeric=JSON.parse(document.querySelector('[data-learning-check="numeric"]').dataset.checkConfig);
      const key=JSON.parse(document.querySelector('[data-learning-check="key_step"]').dataset.checkConfig);
      return {numeric:['2.25','1.75','2.250001','NaN','Infinity','2abc',true].map(x=>renderer.gradeLearningCheck(numeric,x).status),
        key_step:[' DIVIDE BY 2 ','ﬂ','STRAẞE','ς'].map(x=>renderer.gradeLearningCheck(key,x).status)};
    });
    for (const kind of ['choice','boolean','numeric','key_step','self_explanation']) {
      const q=page.locator('[data-learning-check="'+kind+'"]');
      if (!await q.count()) {results[width][kind]={missing:true};continue;}
      await q.locator(':scope > summary').click();
      const hint=q.locator('[data-check-action="hint"]'), submit=q.locator('[data-check-action="submit"]');
      const answer=q.locator('[data-check-solution]'), hints=q.locator('[data-check-hint]');
      const r={initialHidden:!await answer.isVisible()&&!await hints.nth(0).isVisible()&&!await hints.nth(1).isVisible(), initialHintDisabled:await hint.isDisabled()};
      await submit.click();
      r.blankInvalid=await q.locator('[data-check-result]').getAttribute('data-status')==='invalid';
      r.blankDidNotUnlock=!await answer.isVisible()&&await hint.isDisabled();
      if (kind==='choice') await q.locator('input[value="b"]').check();
      else if (kind==='boolean') await q.locator('input[value="false"]').check();
      else await q.locator('[data-check-input]').fill(kind==='numeric'?'4':kind==='key_step'?'multiply by 2':'我会说明运算依据');
      await submit.click();
      const feedback=q.locator('[data-check-result]');
      r.status=await feedback.getAttribute('data-status');
      r.wrongSpecific=kind==='self_explanation'||await q.locator('[data-check-feedback="M"]').isVisible();
      r.afterAttemptFolded=await answer.isVisible()&&!await answer.getAttribute('open')&&!await answer.locator('[data-reader-pointer$="/answer"]').isVisible();
      await hint.click();r.firstHint=await hints.nth(0).isVisible()&&!await hints.nth(1).isVisible();
      await hint.click();r.secondHint=await hints.nth(1).isVisible()&&await hint.isDisabled();
      await answer.locator('summary').click();r.solutionVisible=await answer.locator('[data-reader-pointer$="/answer"]').isVisible();
      if (kind==='choice')await q.locator('input[value="a"]').check();
      else if(kind==='boolean')await q.locator('input[value="true"]').check();
      else await q.locator('[data-check-input]').fill(kind==='numeric'?'2':kind==='key_step'?'divide by 2':'一段需要自评的理由');
      await submit.click();r.finalStatus=await feedback.getAttribute('data-status');
      r.oldFeedbackHidden=kind==='self_explanation'||!await q.locator('[data-check-feedback="M"]').isVisible();
      r.noOverflow=await q.evaluate(e=>e.clientWidth>0&&e.scrollWidth<=e.clientWidth+1);
      r.viewport=await page.evaluate(()=>window.innerWidth);
      if (kind==='choice') await q.screenshot({path:evidence+'/choice-'+width+'.png'});
      results[width][kind]=r;
    }
    await context.close();
  }
  await browser.close();
  process.stdout.write(JSON.stringify(results));
})().catch(e=>{console.error(e);process.exitCode=1;});
