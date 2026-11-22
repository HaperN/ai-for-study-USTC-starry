/* Prepare only the temporary browser DOM; original HTML and SVG bytes stay intact. */
const fs=require('fs'),path=require('path'),os=require('os'),{spawnSync}=require('child_process');
async function preparePrint(page){
 await page.emulateMedia({media:'print'});
 await page.evaluate(()=>document.fonts.ready);
 await page.waitForFunction(()=>[...document.images].filter(i=>i.getClientRects().length).every(i=>i.complete&&i.naturalWidth>0),null,{timeout:15000});
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'aifs-print-figures-')),figures=[];
 try{
  const images=await page.locator('.practice-q img').elementHandles();
  for(let i=0;i<images.length;i++){
   const image=images[i],box=await image.boundingBox();
   if(!box||box.height<=900)continue;
   const png=path.join(temp,`image-${i}.png`);await image.screenshot({path:png});
   const result=spawnSync(process.env.AIFORSTUDY_PYTHON||'python3',[path.join(__dirname,'find_print_image_slices.py'),png,'--max-height','880'],{encoding:'utf8'});
   if(result.status!==0)throw new Error('Figure layout analysis failed: '+result.stderr);
   const info=JSON.parse(result.stdout);const src=await image.getAttribute('src');
   if(!info.segments.length)throw new Error('Long continuous figure needs manual print layout: '+src);
   await image.evaluate((original,info)=>{
    const parent=document.createElement('div');parent.className='print-figure-segments';
    parent.style.cssText=`width:${info.display_width}px;max-width:100%;margin:0 auto;`;
    for(const [top,bottom] of info.segments){
     const part=document.createElement('div');part.className='print-figure-segment';part.style.cssText=`position:relative;overflow:hidden;width:100%;aspect-ratio:${info.width}/${bottom-top};break-inside:avoid;margin:0;`;
     const img=original.cloneNode(true);img.removeAttribute('id');img.style.cssText=`position:absolute!important;left:0;top:0;display:block!important;width:100%!important;max-width:none!important;height:auto!important;margin:0!important;transform:translateY(-${top/info.height*100}%);`;
     part.append(img);parent.append(part);
    }
    original.replaceWith(parent);
   },{...info,display_width:box.width});
   figures.push({src,...info,display_width:box.width});
  }
 }finally{fs.rmSync(temp,{recursive:true,force:true})}
 await page.waitForFunction(()=>[...document.images].filter(i=>i.getClientRects().length).every(i=>i.complete&&i.naturalWidth>0),null,{timeout:15000});
 const math=await page.evaluate(()=>{
  const changes=[];document.querySelectorAll('.katex-display').forEach(box=>{const child=box.querySelector(':scope > .katex');if(!child)return;const width=Math.max(child.scrollWidth,child.getBoundingClientRect().width),available=box.clientWidth;if(width>available+1&&available>0){const ratio=available/width;child.style.display='inline-block';child.style.transform='none';child.style.zoom=String(ratio);child.style.width=width+'px';box.style.textAlign='left';changes.push({question:box.closest('[data-question-id]')?.dataset.questionId,ratio,tex:box.querySelector('annotation')?.textContent})}});return changes;
 });
 return {figure_slices:figures,math_sizing:math,min_math_scale:Math.min(1,...math.map(x=>x.ratio))};
}
module.exports={preparePrint};
