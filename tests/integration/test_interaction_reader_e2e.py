"""Real composed ReaderMount, build output and Chromium controls; no paid calls."""
import json
from pathlib import Path
import subprocess

from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.pipeline.stage_io import _legacy
from aiforstudy.blocks.media.validation import display_runtime
from test_interaction_reader_binding import historical_reader_fixture
from test_content_v4_reader_contract import catalog


def test_composed_folded_interactions_have_scoped_ids_and_change_visible_values(tmp_path):
    bindings={}; catalogs={}; originals={}; contents=[]; interactions={}
    units=[{'unit_id':unit,'title':unit,'original_text':unit+'.md'} for unit in ('U','V')]
    write_json_atomic(tmp_path/'parse/chapters.json',{'book':{'title':'交互验收'},
        'chapters':[{'id':row['unit_id'],'title':row['title']} for row in units], 'lesson_units':units})
    write_json_atomic(tmp_path/'parse/concepts.json',{'concepts':[]})
    for unit in ('U','V'):
        content,bundle=historical_reader_fixture(unit)
        originals[unit]=content['body_markdown']; contents.append(content); interactions[unit]=bundle['view']
        write_json_atomic(tmp_path/'lessons/lessons'/f'{unit}.json',bundle['view'])
        bindings[unit]=bundle['binding']; catalogs[unit]=catalog()
    from support.reader_site import write_reader_fixture
    write_reader_fixture(contents,catalog(),tmp_path/'out',interactions=interactions)
    script=tmp_path/'interaction.cjs'
    script.write_text(r'''
const {chromium}=require(process.argv[2]);
(async()=>{
 const browser=await chromium.launch({executablePath:process.argv[3],headless:true,args:['--no-sandbox']});
 try {
  const page=await browser.newPage({viewport:{width:390,height:900}}), errors=[], external=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route(/^https?:/,route=>{external.push(route.request().url());route.abort()});
  await page.goto(process.argv[4]);
  const results=[];
  for(const unit of ['U','V']) {
   await page.locator('[data-unit="'+unit+'"]').first().click();
   const owner=page.locator('.reader-document details').filter({has:page.locator('[data-reader-pointer="/explainables/0/markdown"]')});
   await owner.locator(':scope > summary').click();
   const card=page.locator('figure[data-model-id="'+unit+'.M-01"]');
   await card.locator('.harmonic-plot').waitFor();
   const before=await card.locator('[data-harmonic-value="u"]').innerText();
   const pathBefore=await card.locator('[data-contour="u"]').getAttribute('d');
   await card.locator('input[type="range"][data-axis="x"]').evaluate(input=>{input.value='2';input.dispatchEvent(new Event('input',{bubbles:true}))});
   const after=await card.locator('[data-harmonic-value="u"]').innerText();
   const pathAfter=await card.locator('[data-contour="u"]').getAttribute('d');
   results.push({unit,before,after,pathChanged:pathBefore!==pathAfter,
    models:await page.locator('figure[data-model-id]').count(), visible:await card.isVisible(),
    pointer:await card.evaluate(e=>e.closest('[data-reader-pointer]').dataset.readerPointer),
    source:await card.evaluate(e=>e.closest('[data-source-kind="interaction"]').dataset.sourceId)});
   await owner.locator(':scope > summary').click();await owner.locator(':scope > summary').click();
   results.at(-1).reopened=await card.locator('[data-harmonic-value="u"]').innerText();
  }
  console.log(JSON.stringify({results,errors,external,ids:await page.evaluate(()=>Object.keys(window.__DATA__.models)),
    bodies:await page.evaluate(()=>Object.fromEntries(Object.entries(window.__DATA__.units).map(([k,v])=>[k,v.body_markdown])))}));
 } finally {await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
''')
    browser,node,playwright=display_runtime()
    output=subprocess.run([str(node),str(script),str(playwright),str(browser),(tmp_path/'out/index.html').as_uri()],
        capture_output=True,text=True,timeout=40)
    assert output.returncode==0,output.stderr
    result=json.loads(output.stdout)
    assert result['ids']==['U.M-01','V.M-01']
    assert result['bodies']==originals
    assert not result['errors'] and not result['external']
    for row in result['results']:
        assert float(row['before'])==0 and float(row['after'])==3
        assert row['reopened']==row['after']
        assert row['pathChanged'] and row['visible'] and row['models']==1
        assert row['source']==row['unit']+'.M-01'
        assert row['pointer']=='/explainables/0/markdown'
