// Host-owned browser evidence collection. All inputs are data or prevalidated SVG.
const fs=require('fs'), {chromium}=require(process.argv[2]);
const job=JSON.parse(fs.readFileSync(process.argv[4],'utf8'));
(async()=>{
  const browser=await chromium.launch({executablePath:process.argv[3],headless:true,
    ignoreDefaultArgs:['--hide-scrollbars'],args:['--no-sandbox','--disable-dev-shm-usage']});
  const records=[];
  try {for(const profile of job.profiles){
    const context=await browser.newContext({viewport:{width:profile.width,height:profile.height},deviceScaleFactor:1});
    const page=await context.newPage();
    await page.route('**/*',route=>route.request().url().startsWith('http')?route.abort():route.continue());
    const source=job.svg||'<img alt="Registered media" src="data:'+job.mime+';base64,'+job.image+'">';
    await page.setContent('<!doctype html><meta charset="utf-8"><style>'+job.display_policy_css+job.media_surface_css+
      'html,body{margin:0;background:white}main{margin:var(--media-viewport-gutter);width:'+profile.content_width+
      'px;max-width:calc(100% - 2 * var(--media-viewport-gutter))}</style><main class="media-probe"><div class="media-viewport">'+source+'</div></main>');
    await page.locator('main').waitFor();await page.evaluate(()=>document.fonts.ready);
    if(!job.svg) await page.locator('img').evaluate(img=>img.decode());
    const evidence=await page.evaluate(()=>{
      const art=document.querySelector('.media-viewport > *'),box=art.getBoundingClientRect();
      const labels=Array.from(document.querySelectorAll('svg text, svg tspan')).filter(e=>Array.from(e.childNodes).some(n=>n.nodeType===3&&n.textContent.trim())).map(e=>{
        const r=e.getBoundingClientRect(),m=e.getScreenCTM(),style=getComputedStyle(e);
        return {text:e.textContent,font_px:parseFloat(style.fontSize)*Math.hypot(m.c,m.d),
          x:r.x,y:r.y,width:r.width,height:r.height,
          unavailable:r.width<=0||r.height<=0||style.visibility==='hidden'||parseFloat(style.opacity)===0,
          clipped:r.left<box.left-.5||r.right>box.right+.5||r.top<box.top-.5||r.bottom>box.bottom+.5};
      });
      return {viewport_width:innerWidth,viewport_height:innerHeight,media_width:box.width,media_height:box.height,
        client_width:document.documentElement.clientWidth,
        scrollbar_width:innerWidth-document.documentElement.clientWidth,
        scroll_height:document.documentElement.scrollHeight,
        overflow:document.documentElement.scrollWidth>document.documentElement.clientWidth,labels};
    });
    const output=job.out_dir+'/'+profile.profile_id+'.png';
    await page.locator('main').screenshot({path:output});
    records.push({...evidence,profile_id:profile.profile_id,image_path:output});
    await context.close();
  }}finally{await browser.close();}
  process.stdout.write(JSON.stringify(records));
})().catch(e=>{console.error(e);process.exitCode=1});
