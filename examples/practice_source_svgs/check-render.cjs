// Real Chromium render of the generated SVGs; no raster is used to author them.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {createHash} = require('node:crypto');
const {chromium} = require('playwright');

async function main() {
  const out = path.resolve(process.argv[2]);
  const edge = '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge';
  const browser = await chromium.launch({headless: true, args: ['--disable-gpu'],
    ignoreDefaultArgs: ['--enable-features=CDPScreenshotNewSurface'],
    ...(fs.existsSync(edge) ? {executablePath: edge} : {})});
  const page = await browser.newPage({viewport: {width: 1120, height: 900}, deviceScaleFactor: 1});
  const report = {browser: await browser.version(), figures: [], errors: []};
  try {
    for (const name of ['fig-8-26', 'fig-9-8', 'fig-9-9']) {
      const input = path.join(out, name + '.svg');
      await page.setContent('<!doctype html><meta charset="UTF-8"><style>body{margin:0}svg{display:block}</style>' + fs.readFileSync(input, 'utf8'));
      await page.evaluate(() => document.fonts.ready);
      const geometry = await page.evaluate(() => {
        const svg = document.querySelector('svg');
        const view = svg.viewBox.baseVal;
        const boxes = [...document.querySelectorAll('text')].map(t => {
          const b = t.getBBox();
          return {text: t.textContent, x: b.x, y: b.y, w: b.width, h: b.height};
        });
        return {width: view.width, height: view.height, texts: boxes.length,
          outside: boxes.filter(b => b.x < 0 || b.y < 0 || b.x + b.w > view.width || b.y + b.h > view.height),
          rasterImages: document.querySelectorAll('image').length};
      });
      if (geometry.outside.length || geometry.rasterImages) report.errors.push({name, ...geometry});
      const screenshot = path.join(out, name + '-chromium.png');
      await page.setViewportSize({width: geometry.width, height: geometry.height});
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await page.waitForTimeout(150);
      await page.screenshot({path: screenshot, fullPage: false, timeout: 15000});
      report.figures.push({name, svg_sha256: createHash('sha256').update(fs.readFileSync(input)).digest('hex'),
        screenshot: path.basename(screenshot), ...geometry});
    }
    await page.goto(pathToFileURL(path.join(out, 'preview.html')).href);
    await page.locator('img').evaluateAll(imgs => Promise.all(imgs.map(img => img.decode())));
    await page.pdf({path: path.join(out, 'source-figures-print-preview.pdf'), preferCSSPageSize: true, printBackground: true});
    report.pdf = 'source-figures-print-preview.pdf';
    fs.writeFileSync(path.join(out, 'browser-checks.json'), JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify(report, null, 2));
    if (report.errors.length) process.exitCode = 1;
  } finally { await browser.close(); }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
