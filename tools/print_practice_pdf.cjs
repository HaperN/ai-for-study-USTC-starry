/* Print audited local HTML with its own fonts, formulae and vector figures. */
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
const { spawnSync } = require('child_process');
const { chromium } = require('playwright');
const { preparePrint } = require('./prepare_practice_print.cjs');

async function main() {
  const root = path.resolve(process.argv[2] || '');
  if (!fs.existsSync(path.join(root, 'build-report.json'))) {
    throw new Error('Expected a practice delivery directory');
  }
  const jobs = [];
  for (const edition of ['基础版', '完整版', '定制版']) {
    if (!fs.existsSync(path.join(root, edition + '-习题集.html'))) continue;
    jobs.push({ html: edition + '-习题题册.html', pdf: '可打印/' + edition + '-习题题册.pdf', answers: false });
    jobs.push({ html: edition + '-习题集.html', pdf: '可打印/' + edition + '-习题解析.pdf', answers: true });
  }
  const paperDir = path.join(root, '试卷包/生成试卷');
  for (const file of fs.readdirSync(paperDir).filter(x => x.endsWith('.html')).sort()) {
    jobs.push({ html: '试卷包/生成试卷/' + file, pdf: '试卷包/生成试卷/' + file.replace(/\.html$/, '.pdf'), answers: file.includes('解析') });
  }
  const executable = process.env.AIFORSTUDY_BROWSER_EXECUTABLE;
  const browser = await chromium.launch(executable ? { executablePath: executable, headless: true } : { channel: 'msedge', headless: true });
  const results = [], blocked = [];
  const report = () => fs.writeFileSync(path.join(root, 'pdf-build-report.json'), JSON.stringify({
    expected_documents: jobs.length, complete: results.length === jobs.length,
    documents: results, blocked_external_requests: blocked
  }, null, 2));
  try {
    for (const job of jobs) {
      // A4 minus 17 mm on each side: formula measurement must use printable width.
      const page = await browser.newPage({ viewport: { width: 665, height: 986 } });
      try {
        await page.route('**/*', route => /^https?:/.test(route.request().url())
          ? (blocked.push(route.request().url()), route.abort()) : route.continue());
        await page.goto(pathToFileURL(path.join(root, job.html)).href + (job.answers ? '?answers=1' : ''), { waitUntil: 'load', timeout: 120000 });
        const errors = await page.locator('.katex-error').allTextContents();
        if (errors.length) throw new Error('Formula parse error ' + job.html + ': ' + errors.join(';'));
        const layout = await preparePrint(page);
        const unsupported = await page.locator('.katex-html').allTextContents();
        if (unsupported.some(text => /\\[A-Za-z]+/.test(text))) {
          throw new Error('Unrendered mathematical command in ' + job.html);
        }
        const target = path.join(root, job.pdf);
        fs.mkdirSync(path.dirname(target), { recursive: true });
        await page.pdf({ path: target, format: 'A4', preferCSSPageSize: true,
          printBackground: true, tagged: true, outline: true, timeout: 300000,
          displayHeaderFooter: true, headerTemplate: '<div></div>',
          footerTemplate: '<div style="font-size:9px;width:100%;text-align:center;color:#6b7785">复变函数 A · <span class="pageNumber"></span> / <span class="totalPages"></span></div>',
          margin: { top: '18mm', bottom: '18mm', left: '17mm', right: '17mm' } });
        const repaired = spawnSync(process.env.AIFORSTUDY_PYTHON || 'python3',
          [path.join(__dirname, 'make_print_links_portable.py'), target, '--root', root],
          { encoding: 'utf8', maxBuffer: 1024 * 1024 });
        if (repaired.status !== 0) throw new Error('PDF link validation failed: ' + repaired.stderr);
        results.push({ ...job, ...layout, ...JSON.parse(repaired.stdout), bytes: fs.statSync(target).size });
        report();
        console.log(`[${results.length}/${jobs.length}] ${job.pdf}: ${results.at(-1).pages} 页`);
      } finally { await page.close(); }
    }
    if (blocked.length) throw new Error('PDF requested external resources; see pdf-build-report.json');
  } finally { report(); await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
