const {chromium}=require(process.argv[2]);
(async()=>{
 const browser=await chromium.launch({executablePath:process.argv[3],headless:true,args:['--no-sandbox']});const results={};
 try{for(const width of [1280,390]){
  const context=await browser.newContext({viewport:{width,height:900}});const page=await context.newPage();const errors=[],externalRequests=[];
  page.on('pageerror',e=>errors.push(e.message));await page.route('**/*',r=>{if(/^https?:/.test(r.request().url())&&!r.request().url().startsWith('http://127.0.0.1:')){externalRequests.push(r.request().url());r.abort()}else r.continue()});
  await page.goto(process.argv[4]);await page.locator('[data-animation-player]').first().waitFor();
  const details=page.locator('.reader-document details');for(let i=0;i<await details.count();i++){const d=details.nth(i);if(!await d.getAttribute('open')) await d.locator(':scope > summary').click();}
  await page.waitForFunction(()=>document.querySelectorAll('[data-animation-player]').length===2);
  const r=await page.evaluate(()=>{
   const nodes=Array.from(document.querySelectorAll('[data-reader-block]')), expected=window.__DATA__.units.U.reader_document.blocks;
   const body=document.querySelector('[data-reader-pointer="/body_markdown"]'),last=body.querySelector('[data-media-request="A1"]');
   const imageNodes=Array.from(document.querySelectorAll('.reader-media img,.reader-media svg'));
   return {viewport:innerWidth,blocks:nodes.map(e=>e.dataset.readerBlock).sort(),expected:expected.map(b=>b.block_id).sort(),
    allVisible:nodes.every(e=>e.getBoundingClientRect().height>0&&e.innerText.trim()),noDuplicateBlocks:new Set(nodes.map(e=>e.dataset.readerBlock)).size===nodes.length,
    mediaOrder:Array.from(body.querySelectorAll('.reader-media')).map(e=>e.dataset.mediaRequest),
    explanationAnimation:!!document.querySelector('[data-reader-pointer="/explainables/0/markdown"] [data-animation-player]'),
    secondParagraphAfterMedia:!!last.nextElementSibling&&last.nextElementSibling.innerText.includes('第二段'),allImagesLoaded:imageNodes.length===2&&imageNodes.every(i=>i.tagName.toLowerCase()==='svg'?i.getBoundingClientRect().width>0&&i.querySelectorAll('text').length>0:i.complete&&i.naturalWidth>0),
    imageWidths:imageNodes.map(e=>e.getBoundingClientRect().width),smallestLabelPx:Math.min(...Array.from(document.querySelectorAll('.reader-media svg text')).map(e=>parseFloat(getComputedStyle(e).fontSize)*Math.hypot(e.getScreenCTM().c,e.getScreenCTM().d))),
    cardInsets:Array.from(document.querySelectorAll('.reader-media figcaption,.reader-media .animation-status,.reader-media .animation-controls,.reader-media .animation-transcript')).map(e=>{const card=e.closest('figure');return e.getBoundingClientRect().left+parseFloat(getComputedStyle(e).paddingLeft)-card.getBoundingClientRect().left}),
    anchorTags:Array.from(document.querySelectorAll('.reader-media[data-media-anchor]')).map(e=>e.tagName),
    figureImageRole:!!document.querySelector('.reader-media figure[role=img]'),
    players:document.querySelectorAll('[data-animation-player]').length,noOverflow:document.documentElement.scrollWidth<=innerWidth+1};
  });
  r.playback=[];const players=page.locator('[data-animation-player]');
  for(let i=0;i<await players.count();i++){
   const host=players.nth(i);await host.locator('[data-action="play"]').click();
   await page.waitForFunction(index=>{const v=document.querySelectorAll('[data-animation-player] video')[index];return v.readyState>=2&&v.currentTime>.2},i);
   await host.locator('[data-action="play"]').click();
   const reading=await host.locator('video').evaluate(async v=>{
    const advanced=v.currentTime>.2;v.pause();v.currentTime=1;
    await new Promise((resolve,reject)=>{if(!v.seeking)return resolve();v.addEventListener('seeked',resolve,{once:true});setTimeout(()=>reject(new Error('seek timeout')),5000)});
    for(const track of v.textTracks)track.mode='showing';
    await new Promise(resolve=>setTimeout(resolve,150));
    return {advanced,frameWidth:v.videoWidth,cues:Array.from(v.textTracks).reduce((n,t)=>n+(t.cues?t.cues.length:0),0),seekError:Math.abs(v.currentTime-1)};
   });r.playback.push(reading);
  }
  await page.screenshot({path:process.argv[5]+'/reader-'+width+'.png',fullPage:true});r.errors=errors;r.externalRequests=externalRequests;results[width]=r;await context.close();
 }}finally{await browser.close()}process.stdout.write(JSON.stringify(results));
})().catch(e=>{console.error(e);process.exitCode=1});
