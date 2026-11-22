const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');
const {preparePrint} = require('../../tools/prepare_practice_print.cjs');

test('print slices preserve the width of a tall narrow vector figure', async () => {
  const executablePath = process.env.AIFORSTUDY_BROWSER_EXECUTABLE;
  const browser = await chromium.launch(executablePath ? {executablePath, headless:true} : {channel:'msedge',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:665,height:986}});
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="1800"><rect width="120" height="1800" fill="white"/>' +
      [20,470,920,1370].map(y => `<rect x="10" y="${y}" width="100" height="350" fill="black"/>`).join('') + '</svg>';
    const source = 'data:image/svg+xml;base64,' + Buffer.from(svg).toString('base64');
    await page.setContent(`<body style="margin:0"><article class="practice-q" style="width:665px"><p><img src="${source}" style="display:block;max-width:100%"></p></article></body>`);
    const report = await preparePrint(page);
    assert.equal(report.figure_slices.length, 1);
    assert.equal(report.figure_slices[0].all_pixels_retained, true);
    const sizes = await page.locator('.print-figure-segment').evaluateAll(nodes=>nodes.map(node=>({width:node.getBoundingClientRect().width,height:node.getBoundingClientRect().height})));
    assert.ok(sizes.length > 1);
    assert.ok(sizes.every(size=>Math.abs(size.width-120)<1 && size.height<=881), JSON.stringify(sizes));
    assert.equal(await page.locator('.print-figure-segment img').first().getAttribute('src'), source);
  } finally {await browser.close();}
});

test('one wide formula does not shrink heading or body type in the actual PDF', async () => {
  const executablePath=process.env.AIFORSTUDY_BROWSER_EXECUTABLE;
  const browser=await chromium.launch(executablePath?{executablePath,headless:true}:{channel:'msedge',headless:true});
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'practice-print-fonts-'));
  try {
    const page=await browser.newPage({viewport:{width:665,height:986}});
    await page.setContent(`<style>body{margin:0;font:11pt Arial}h1{font-size:22pt}.katex{display:inline-block;white-space:nowrap}</style><h1>Unchanged heading</h1><p>Unchanged normal body</p><div class="katex-display"><span class="katex">${'long formula + '.repeat(24)}</span></div>`);
    const report=await preparePrint(page);
    assert.ok(report.min_math_scale<0.8);
    const pdf=path.join(temp,'sizes.pdf');
    await page.pdf({path:pdf,format:'A4',margin:{left:'17mm',right:'17mm',top:'18mm',bottom:'18mm'}});
    const result=spawnSync(process.env.AIFORSTUDY_PYTHON||'python3',['-c',
      "import sys,json,math\nfrom pypdf import PdfReader\nr=PdfReader(sys.argv[1]);sizes={}\ndef visit(text,cm,tm,font,size):\n if text.strip() in ('Unchanged heading','Unchanged normal body'):sizes[text.strip()]=size*math.hypot(cm[0],cm[1])*math.hypot(tm[0],tm[1])\nfor p in r.pages:p.extract_text(visitor_text=visit)\nprint(json.dumps(sizes))",pdf],{encoding:'utf8'});
    assert.equal(result.status,0,result.stderr);
    const sizes=JSON.parse(result.stdout);
    assert.ok(Math.abs(sizes['Unchanged heading']-22)<0.1,JSON.stringify(sizes));
    assert.ok(Math.abs(sizes['Unchanged normal body']-11)<0.1,JSON.stringify(sizes));
  } finally {await browser.close();fs.rmSync(temp,{recursive:true,force:true});}
});
