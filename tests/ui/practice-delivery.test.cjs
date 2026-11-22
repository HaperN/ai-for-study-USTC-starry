const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {pathToFileURL} = require('node:url');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../..');
const executablePath = process.env.AIFORSTUDY_BROWSER_EXECUTABLE ||
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge';
const buildFixture = `
import json
import shutil
import sys
from pathlib import Path
from test_practice_builder import teacher_inputs
from tools.build_practice_delivery import build

tmp = Path(sys.argv[1])
catalog, papers, reader, profile, additions = teacher_inputs(tmp, independent_names=True)
if len(sys.argv)>2 and sys.argv[2]=='related':
    rows=json.loads(catalog.read_text())
    rows[0]['additional_mounts']=[{'unit_id':'CH01-S01-SS05','anchor_text':'唯一教学锚点',
        'focus_note':'本节重点：识别同一题中的条件判断。',
        'placement':{'unit_id':'CH01-S01-SS05','quote':'唯一教学锚点','paragraph_sha256':'a'*64,'method':'exact phrase'},
        'private_coverage_evidence':'not student facing'}]
    catalog.write_text(json.dumps(rows))
    (reader/'data/data.js').write_text('window.__DATA__={"units":{"CH01-S01-SS04":{},"CH01-S01-SS05":{}}};')
html = '''<!doctype html><html><body>
<main id="main"><article class="unit"><div class="lesson-prose">
<p id="before">前文段落含有定制练习锚点。</p>
<p id="intended">这一段含有唯一教学锚点并且应当承接习题。</p>
<p id="after">后续段落。</p>
</div></article></main>'''
for name in ('marked.min.js', 'katex.min.js', 'katex.min.css',
             'reader-markdown.js', 'study-math.js', 'render-core.js'):
    source = Path('blocks/render') / ('vendor' if name in ('marked.min.js', 'katex.min.js', 'katex.min.css') else 'assets')
    shutil.copyfile(source / name, reader / 'assets' / name)
    if name.endswith('.js'):
        html += '<script src="assets/' + name + '"></script>'
shutil.copytree('blocks/render/vendor/fonts', reader / 'assets/fonts', dirs_exist_ok=True)
html += '''
<script>
function loadUnit() {
    document.querySelectorAll('.practice-inline').forEach(node=>node.remove());
    document.documentElement.dataset.readerUnit = location.hash.slice(2) || 'CH01-S01-SS04';
}
addEventListener('hashchange', loadUnit);loadUnit();
</script></body></html>'''
(reader / 'index.html').write_text(html, encoding='utf-8')
papers.write_text(json.dumps([
    {'id': 'full-paper', 'edition': 'full', 'kind': 'coverage', 'title': '完整版试卷',
     'purpose': '覆盖训练', 'items': [{'question_id': 'A-1', 'points': 10}]},
    {'id': 'custom-paper', 'edition': 'custom', 'kind': 'coverage', 'title': '定制版试卷',
     'purpose': '覆盖训练', 'items': [{'question_id': 'A-1', 'points': 10}, {'question_id': 'TEACHER-1', 'points': 20}]},
]))
build(catalog, papers, reader, tmp / 'delivery', profile=profile, additions=additions)
`;

test('delivery checker validates all profiles and the projected question mounts at its paragraph', async t => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'practice-delivery-'));
    t.after(() => fs.rmSync(tmp, {recursive: true, force: true}));
    const fixture = spawnSync(process.env.AIFORSTUDY_TEST_PYTHON || 'python3',
        ['-c', buildFixture, tmp], {
            cwd: root, encoding: 'utf8',
            env: {...process.env, PYTHONPATH: 'src:.:tests', PYTHONDONTWRITEBYTECODE: '1'},
        });
    assert.equal(fixture.status, 0, fixture.error?.message || fixture.stderr);
    const delivery = path.join(tmp, 'delivery');
    const reportDir = path.join(tmp, 'report');
    const check = () => spawnSync(process.execPath,
        ['tools/check_practice_delivery.cjs', delivery, reportDir], {
            cwd: root, encoding: 'utf8',
            env: {...process.env, AIFORSTUDY_BROWSER_EXECUTABLE: executablePath},
        });
    const valid = check();
    assert.equal(valid.status, 0, valid.stderr + valid.stdout);
    const report = JSON.parse(fs.readFileSync(path.join(reportDir, 'report.json')));
    assert.deepEqual(Object.fromEntries(Object.entries(report.editions)
        .map(([id, profile]) => [id, profile.readerQuestions])), {basic: 1, full: 1, custom: 2});
    assert.equal(report.pages.filter(page => page.name.startsWith('定制版-')).length, 2);
    assert.equal(report.pages.find(page => page.name.endsWith('full-paper-解析.html')).questions, 1);
    assert.equal(report.pages.find(page => page.name.endsWith('custom-paper-解析.html')).questions, 2);

    const browser = await chromium.launch({executablePath, headless: true});
    try {
        const page = await browser.newPage();
        const url = pathToFileURL(path.join(delivery, '教材/index.html'));
        await page.goto(url.href + '?practice=custom#/CH01-S01-SS04');
        const question = page.locator('#intended + details[data-practice-id="A-1"]');
        await question.waitFor({timeout: 5000});
        assert.equal(await question.getAttribute('data-anchor-state'), 'matched');
        const row = await page.evaluate(() => window.PRACTICE_EDITIONS.custom.find(q => q.id === 'A-1'));
        assert.equal(row.unit_id, 'CURRICULUM-UNIT');
        assert.equal(row.mount_unit_id, 'CH01-S01-SS04');
        assert.equal(row.anchor_text, '唯一教学锚点');
        assert.deepEqual(row.placement, {
            unit_id: 'CH01-S01-SS04', quote: '唯一教学锚点',
            paragraph_sha256: 'a'.repeat(64), method: 'exact phrase',
        });
        assert.equal(row.source.pdf_path, undefined);
        assert.equal(row.review, undefined);
        assert.equal(await question.locator('.katex-error').count(), 0);
    } finally {
        await browser.close();
    }

    // A leaked key is forbidden even when it agrees with the private catalog.
    const catalogPath = path.join(delivery, 'catalog.json');
    const catalog = JSON.parse(fs.readFileSync(catalogPath, 'utf8'));
    for (const row of catalog) row.placement.pdf_path = '/private/placement.pdf';
    fs.writeFileSync(catalogPath, JSON.stringify(catalog));
    const exercisePath = path.join(delivery, '完整版-习题集.html');
    const exercise = fs.readFileSync(exercisePath, 'utf8');
    fs.writeFileSync(exercisePath, exercise.replace('"placement":{', '"placement":{"pdf_path":"/private/placement.pdf",'));
    const dataPath = path.join(delivery, '教材/assets/practice-data.js');
    const dataScript = fs.readFileSync(dataPath, 'utf8');
    fs.writeFileSync(dataPath, dataScript.replace('"placement":{', '"placement":{"pdf_path":"/private/placement.pdf",'));
    const leakedPlacement = check();
    assert.equal(leakedPlacement.status, 1);
    const placementReport = JSON.parse(fs.readFileSync(path.join(reportDir, 'report.json')));
    const full = placementReport.pages.find(page => page.name === '完整版-习题集.html');
    assert.ok(full.restrictions.some(issue => issue.includes('unexpected placement field pdf_path')));
    assert.ok(placementReport.editions.basic.problems.some(issue => issue.includes('unexpected placement field pdf_path')));
    fs.writeFileSync(exercisePath, exercise);
    fs.writeFileSync(dataPath, dataScript);

    const file = path.join(delivery, '定制版-习题题册.html');
    const original = fs.readFileSync(file, 'utf8');
    fs.writeFileSync(file, original.replace('"questions":[{', '"questions":[{"answer":"LEAKED_ANSWER",'));
    const invalid = check();
    assert.equal(invalid.status, 1);
    const invalidReport = JSON.parse(fs.readFileSync(path.join(reportDir, 'report.json')));
    const leaked = invalidReport.pages.find(page => page.name === '定制版-习题题册.html');
    assert.ok(leaked.restrictions.some(issue => issue.includes('answer')));
});

test('delivery checker visits related-only units and rejects modified focus notes', async t => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'practice-related-delivery-'));
    t.after(() => fs.rmSync(tmp, {recursive: true, force: true}));
    const fixture = spawnSync(process.env.AIFORSTUDY_TEST_PYTHON || 'python3', ['-c', buildFixture, tmp, 'related'], {
        cwd: root, encoding: 'utf8', env: {...process.env, PYTHONPATH: 'src:.:tests', PYTHONDONTWRITEBYTECODE: '1'},
    });
    assert.equal(fixture.status, 0, fixture.error?.message || fixture.stderr);
    const delivery = path.join(tmp, 'delivery'), reportDir = path.join(tmp, 'report');
    const check = () => spawnSync(process.execPath, ['tools/check_practice_delivery.cjs', delivery, reportDir], {
        cwd: root, encoding: 'utf8', env: {...process.env, AIFORSTUDY_BROWSER_EXECUTABLE: executablePath},
    });
    const valid = check();
    assert.equal(valid.status, 0, valid.stderr + valid.stdout);
    const report = JSON.parse(fs.readFileSync(path.join(reportDir, 'report.json')));
    assert.equal(report.editions.full.readerQuestions, 2);
    assert.equal(report.editions.full.uniqueQuestions, 1);
    const related = report.editions.full.mounts.find(row => row.unit === 'CH01-S01-SS05');
    assert.equal(related.anchors[0].id, 'A-1');
    assert.match(related.anchors[0].focus, /条件判断/);
    const script = path.join(delivery, '教材/assets/practice-data.js');
    fs.writeFileSync(script, fs.readFileSync(script, 'utf8').replaceAll('本节重点：识别同一题中的条件判断。', '被错误替换的重点'));
    const invalid = check();
    assert.equal(invalid.status, 1);
    const bad = JSON.parse(fs.readFileSync(path.join(reportDir, 'report.json')));
    assert.ok(bad.editions.full.problems.some(issue => issue.includes('additional_mounts differs')));
});
