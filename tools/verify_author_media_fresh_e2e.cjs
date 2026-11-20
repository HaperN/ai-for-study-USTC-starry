const assert=require('node:assert/strict');
const {chromium}=require(process.argv[2]);
(async()=>{
 const browser=await chromium.launch({executablePath:process.argv[3],headless:true,args:['--no-sandbox']});
 const results={},expected=JSON.parse(process.argv[6]);
 try{for(const width of [1280,390]){
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();
  const errors=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{if(/^https?:/.test(route.request().url())&&!route.request().url().startsWith('http://127.0.0.1:')){external.push(route.request().url());route.abort()}else route.continue()});
  await page.goto(process.argv[4]);
  const player=page.locator('[data-animation-player]');await player.waitFor();
  const data=await page.evaluate(()=>{
   const body=document.querySelector('[data-reader-pointer="/body_markdown"]');
   const images=[...document.querySelectorAll('.reader-media svg,.reader-media img')];
   return {media_order:[...body.querySelectorAll('.reader-media')].map(x=>x.dataset.mediaRequest),
    image_count:images.length,images_loaded:images.every(x=>x.tagName.toLowerCase()==='svg'?x.getBoundingClientRect().width>0:x.complete&&x.naturalWidth>0),
    player_count:document.querySelectorAll('[data-animation-player]').length,
    video_css_width:document.querySelector('video').getBoundingClientRect().width,
    no_overflow:document.documentElement.scrollWidth<=innerWidth+1};
  });
  assert.deepEqual(data.media_order,expected);assert.equal(data.image_count,1);assert.equal(data.player_count,1);
  assert(data.images_loaded&&data.no_overflow);
  await player.locator('[data-action="play"]').click();
  await page.waitForFunction(()=>{const v=document.querySelector('video');return v.readyState>=2&&v.currentTime>.2});
  const playback=await player.locator('video').evaluate(async v=>{
   const advanced=v.currentTime>.2;v.pause();v.currentTime=5;
   await new Promise((resolve,reject)=>{if(!v.seeking)return resolve();v.addEventListener('seeked',resolve,{once:true});setTimeout(()=>reject(new Error('seek timeout')),5000)});
   for(const t of v.textTracks)t.mode='showing';await new Promise(r=>setTimeout(r,150));
   return {advanced,video_width:v.videoWidth,duration:v.duration,seek_error:Math.abs(v.currentTime-5),
    captions:[...v.textTracks].reduce((n,t)=>n+(t.cues?t.cues.length:0),0)};
  });
  assert(playback.advanced&&playback.video_width>0&&playback.captions>0&&playback.seek_error<.2,JSON.stringify(playback));
  assert.equal(errors.length,0);assert.equal(external.length,0);
  await page.screenshot({path:process.argv[5]+'/reader-'+width+'.png',fullPage:true});
  results[width]={...data,...playback,no_external_requests:external.length===0,errors};await context.close();
 }}finally{await browser.close()}
 process.stdout.write(JSON.stringify(results));
})().catch(e=>{console.error(e);process.exitCode=1});
