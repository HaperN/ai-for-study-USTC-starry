"""Diagnostic canvas templates must render changing pixels, or an explicit failure."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from aiforstudy.blocks.media.validation import display_runtime
from aiforstudy.blocks.render.diagnostics import build_diagnostic_preview
from aiforstudy.pipeline.stage_io import _legacy
from test_diagnostic_snapshot_integrity import canvas_run


@pytest.mark.parametrize('missing',[False,True])
def test_diagnostic_canvas_changes_visible_geometry_or_explains_missing_runtime(tmp_path,monkeypatch,missing):
    if missing:
        renderer=_legacy('build');vendor=tmp_path/'vendor';shutil.copytree(renderer.VENDOR,vendor)
        (vendor/'mathbox/mathbox.min.js').unlink();monkeypatch.setattr(renderer,'VENDOR',vendor)
    report=build_diagnostic_preview(canvas_run(tmp_path),unit_ids=['U'],out_dir=tmp_path/'preview')
    script=tmp_path/'canvas.cjs'
    script.write_text(r'''
const {chromium}=require(process.argv[2]),crypto=require('crypto');
(async()=>{
 const browser=await chromium.launch({executablePath:process.argv[3],headless:true,
  args:['--no-sandbox','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 try {
  const page=await browser.newPage({viewport:{width:1000,height:900}}),errors=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route(/^https?:/,r=>{external.push(r.request().url());r.abort()});
  await page.goto(process.argv[4]);
  await page.locator('[data-unit="U"]').first().click();
  const owner=page.locator('.reader-document details').filter({has:page.locator('[data-reader-pointer="/explainables/0/markdown"]')});
  await owner.locator(':scope > summary').click();
  const model=page.locator('figure[data-model-id="U.M-01"]');
  if(process.argv[5]==='missing') {
   console.log(JSON.stringify({failure:await model.locator('.model-fallback').innerText(),
    canvas:await model.locator('canvas').count(),failed:await model.evaluate(e=>e.classList.contains('model-failed')),errors,external}));
  } else {
   const canvas=model.locator('canvas');await canvas.waitFor();
   await page.waitForFunction(()=>window.__MODEL_RENDERERS__?.['U.M-01']?.calls>0);
   const before=await model.locator('.mdl-readout').innerText();
   const first=crypto.createHash('sha256').update(await canvas.screenshot()).digest('hex');
   await model.locator('input[type="range"]').first().evaluate(e=>{e.value='2';e.dispatchEvent(new Event('input',{bubbles:true}))});
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   const after=await model.locator('.mdl-readout').innerText();
   const second=crypto.createHash('sha256').update(await canvas.screenshot()).digest('hex');
   console.log(JSON.stringify({before,after,pixelsChanged:first!==second,visible:await canvas.isVisible(),
    renderer:await page.evaluate(()=>window.__MODEL_RENDERERS__['U.M-01']),errors,external}));
  }
 } finally {await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
''')
    browser,node,playwright=display_runtime()
    output=subprocess.run([str(node),str(script),str(playwright),str(browser),
        (Path(report['output_dir'])/'index.html').as_uri(),'missing' if missing else 'ready'],
        capture_output=True,text=True,timeout=45)
    assert output.returncode==0,output.stderr
    result=json.loads(output.stdout)
    (tmp_path/'canvas-browser.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    assert not result['errors'] and not result['external']
    if missing:
        assert result['failed'] and result['canvas']==0
        assert 'mathbox.min.js' in result['failure']
        assert report['missing'] and report['problems']
    else:
        assert not report['missing']
        # This canvas fixture has no replayable content audit. Working pixels
        # cannot certify the source, and must retain its explicit uncertainty.
        assert report['review_outcome']['status'] == 'unverified'
        assert all(row['kind'] == 'content' for row in report['problems'])
        assert result['pixelsChanged'] and result['visible'] and result['renderer']['triangles']>0
        assert '3.00' in result['before'] and '5.00' in result['after'] and '4.00' in result['after']
