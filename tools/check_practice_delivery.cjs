const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {isDeepStrictEqual} = require('node:util');
const {chromium, errors: playwrightErrors} = require('playwright');

const editionNames = {basic: '基础版', full: '完整版', custom: '定制版'};
const publicFields = [
    'id', 'title', 'stem', 'topic', 'unit_id', 'priority', 'difficulty', 'points',
    'answer', 'solution', 'necessary_conditions', 'methods', 'pitfalls', 'priority_reason',
    'public_source_path', 'public_textbook_path', 'section', 'anchor_text',
    'mount_unit_id', 'placement', 'subquestion_count', 'source',
];
const publicSourceFields = ['kind', 'course', 'year', 'pdf_page', 'question_number', 'common_header', 'public_path'];
const placementFields = ['unit_id', 'quote', 'paragraph_sha256', 'method'];
const bookletFields = ['id', 'title', 'stem', 'priority', 'difficulty', 'topic', 'public_source_path', 'source'];
const examFields = ['id', 'stem', 'points', 'section', 'source'];
const bookletSourceFields = publicSourceFields.filter(field => field !== 'public_path');

function expectSameIds(actual, expected, label, problems) {
    if (JSON.stringify([...actual].sort()) !== JSON.stringify([...expected].sort())) {
        problems.push(label + ' IDs differ from the expected question set');
    }
}

function isRelativeLink(value) {
    if (typeof value !== 'string' || !value) return false;
    try {
        const decoded = decodeURIComponent(value.split(/[?#]/, 1)[0]);
        return !!decoded && !/^(?:[a-z][a-z\d+.-]*:|\/)|\\/i.test(decoded) &&
            !decoded.split('/').includes('..');
    } catch {
        return false;
    }
}

function projectPlacement(placement) {
    if (!placement || typeof placement !== 'object' || Array.isArray(placement)) return undefined;
    if (!placementFields.every(key => typeof placement[key] === 'string' && placement[key].trim()) ||
        !/^[a-f0-9]{64}$/.test(placement.paragraph_sha256)) return undefined;
    return Object.fromEntries(placementFields.map(key => [key, placement[key]]));
}

function checkRows(rows, expected, fields = publicFields, sourceFields = publicSourceFields) {
    if (!Array.isArray(rows)) return ['missing public question payload'];
    const problems = [];
    const byId = new Map(expected.map(row => [row.id, row]));
    expectSameIds(rows.map(row => row?.id), byId.keys(), 'Payload', problems);
    for (const row of rows) {
        if (!row || typeof row !== 'object' || Array.isArray(row)) {
            problems.push('invalid public question');
            continue;
        }
        for (const field of Object.keys(row)) {
            if (!fields.includes(field)) problems.push(row.id + ': unexpected question field ' + field);
        }
        if (!row.source || typeof row.source !== 'object' || Array.isArray(row.source)) {
            problems.push(row.id + ': invalid public source');
        } else {
            for (const field of Object.keys(row.source)) {
                if (!sourceFields.includes(field)) problems.push(row.id + ': unexpected source field ' + field);
            }
        }
        for (const [field, value] of [
            ['public_source_path', row.public_source_path], ['public_textbook_path', row.public_textbook_path],
            ['source.public_path', row.source?.public_path],
        ]) {
            if (value !== undefined && value !== '' && !isRelativeLink(value)) problems.push(row.id + ': invalid ' + field);
        }
        if (fields.includes('placement') && row.placement !== undefined) {
            const placement = projectPlacement(row.placement);
            if (!placement) problems.push(row.id + ': invalid public placement');
            if (row.placement && typeof row.placement === 'object') {
                for (const key of Object.keys(row.placement)) {
                    if (!placementFields.includes(key)) problems.push(row.id + ': unexpected placement field ' + key);
                }
            }
        }
        const original = byId.get(row.id);
        if (!original) continue;
        if (fields.includes('placement') &&
            !isDeepStrictEqual(projectPlacement(row.placement), projectPlacement(original.placement))) {
            problems.push(row.id + ': published placement differs');
        }
        for (const field of ['title', 'stem', 'topic', 'priority', 'priority_reason', 'difficulty', 'answer',
            'solution', 'section', 'points', 'unit_id', 'mount_unit_id', 'anchor_text',
            'necessary_conditions', 'methods', 'pitfalls', 'subquestion_count']) {
            if (fields.includes(field) && original[field] !== undefined &&
                !isDeepStrictEqual(row[field], original[field])) {
                problems.push(row.id + ': published ' + field + ' differs');
            }
        }
        for (const field of sourceFields) {
            if (field !== 'public_path' && original.source?.[field] !== undefined &&
                !isDeepStrictEqual(row.source?.[field], original.source[field])) problems.push(row.id + ': published source.' + field + ' differs');
        }
    }
    return problems;
}

function readDelivery(root) {
    const read = name => JSON.parse(fs.readFileSync(path.join(root, name), 'utf8'));
    const exists = name => fs.existsSync(path.join(root, name));
    const catalog = read('catalog.json');
    const editions = {basic: catalog.filter(row => row.basic === true), full: catalog};
    const hasCustom = exists('teacher-profile.json');
    if (hasCustom) {
        const profile = read('teacher-profile.json');
        const additions = exists('teacher-additions.json') ? read('teacher-additions.json') : [];
        const excluded = new Set(profile.exclude_ids || []);
        const included = new Set(profile.include_ids || []);
        const topics = new Set(profile.topics || []);
        const overrides = profile.priority_overrides || {};
        editions.custom = [...catalog, ...additions]
            .filter(row => !excluded.has(row.id) && (row.basic === true || included.has(row.id) || topics.has(row.topic)))
            .map(row => {
                if (!Object.hasOwn(overrides, row.id)) return row;
                const override = overrides[row.id];
                return {...row, priority: override.priority, priority_reason: override.reason};
            });
    }
    const problems = [];
    const reported = read('build-report.json').editions;
    expectSameIds(Object.keys(reported), Object.keys(editions), 'Build report edition', problems);
    for (const [name, rows] of Object.entries(editions)) {
        if (reported[name]?.questions !== rows.length || !rows.length) problems.push(name + ': invalid edition count');
        if (new Set(rows.map(row => row.id)).size !== rows.length) problems.push(name + ': duplicate question ID');
    }

    const jobs = Object.entries(editions).flatMap(([edition, rows]) => [
        {name: editionNames[edition] + '-习题集.html', rows, only: false},
        {name: editionNames[edition] + '-习题题册.html', rows, only: true},
    ]);
    if (!hasCustom && ['习题集', '习题题册'].some(kind => exists('定制版-' + kind + '.html'))) {
        problems.push('Custom edition is missing its exported profile');
    }
    const paperDir = '试卷包/生成试卷';
    const papers = read('papers.json');
    const paperFiles = fs.readdirSync(path.join(root, paperDir)).filter(name => name.endsWith('.html'));
    expectSameIds(paperFiles, papers.flatMap(paper => [paper.id + '-题卷.html', paper.id + '-解析.html']),
        'Paper files', problems);
    for (const paper of papers) {
        if (typeof paper.id !== 'string' || !/^[A-Za-z0-9_-]+$/.test(paper.id)) {
            problems.push('Invalid paper ID');
            continue;
        }
        const members = new Map((editions[paper.edition || 'full'] || []).map(row => [row.id, row]));
        const rows = [];
        for (const item of paper.items) {
            const question = members.get(item.question_id);
            if (question) rows.push({...question, points: item.points, section: item.section || ''});
            else problems.push(paper.id + ': question is outside its edition: ' + item.question_id);
        }
        for (const [suffix, only] of [['题卷', true], ['解析', false]]) {
            jobs.push({name: paperDir + '/' + paper.id + '-' + suffix + '.html', rows, only, paper: paper.id});
        }
    }
    return {editions, jobs, problems};
}

async function inspectPage(page, root, job) {
    await page.goto(pathToFileURL(path.join(root, job.name)).href, {waitUntil: 'load'});
    const {payload, ids, ...result} = await page.evaluate(() => ({
        payload: window.PRACTICE_DATA,
        ids: [...document.querySelectorAll('#practice-questions .practice-q')].map(node => node.dataset.questionId),
        hasAnswerMedia: Object.hasOwn(window, 'PRACTICE_MEDIA') ||
            !!document.querySelector('video,[src*="practice-media"],[href$="/practice-media.css"]'),
        mathErrors: [...document.querySelectorAll('.katex-error')].map(node => ({text: node.textContent, error: node.title})),
        brokenImages: [...document.querySelectorAll('.practice-q img')]
            .filter(node => !node.complete || !node.naturalWidth).map(node => node.getAttribute('src')),
        unsupportedCommands: [...document.querySelectorAll('.katex-html')]
            .map(node => node.textContent).filter(text => /\\[A-Za-z]+/.test(text)),
        openAnswers: document.querySelectorAll('.practice-solution[open]').length,
        answers: document.querySelectorAll('.practice-solution').length,
        bodyChars: document.body.innerText.length,
    }));
    const fields = !job.only ? publicFields : job.paper ? examFields : bookletFields;
    const sourceFields = !job.only ? publicSourceFields : job.paper ? ['common_header'] : bookletSourceFields;
    const restrictions = checkRows(payload?.questions, job.rows, fields, sourceFields);
    expectSameIds(ids, job.rows.map(row => row.id), 'Rendered', restrictions);
    if (payload?.question_only !== job.only || (payload?.paper?.id || undefined) !== job.paper) {
        restrictions.push('unexpected page mode');
    }
    if (job.only && result.hasAnswerMedia) restrictions.push('question-only page loaded answer media');
    if (result.openAnswers || result.answers !== (job.only ? 0 : job.rows.length)) {
        restrictions.push('unexpected solution fold state');
    }
    return {name: job.name, questions: ids.length, restrictions, ...result};
}

async function inspectReader(page, url, edition, rows, editions) {
    const perUnit = new Map();
    for (const row of rows) {
        const unit = row.mount_unit_id || row.unit_id;
        if (!perUnit.has(unit)) perUnit.set(unit, []);
        perUnit.get(unit).push(row);
    }
    const first = perUnit.keys().next().value;
    const problems = [];
    const mounts = [];
    if (!first) return {mounts, readerQuestions: 0, problems: ['empty edition']};
    await page.goto(url + '?practice=' + edition + '#/' + encodeURIComponent(first), {waitUntil: 'load'});
    const payload = await page.evaluate(() => window.PRACTICE_EDITIONS || {});
    expectSameIds(Object.keys(payload), Object.keys(editions), 'Reader edition', problems);
    problems.push(...checkRows(payload[edition], rows));
    for (const [unit, members] of perUnit) {
        const ids = members.map(row => row.id);
        await page.evaluate(unit => location.hash = '#/' + encodeURIComponent(unit), unit);
        try {
            await page.waitForFunction(({unit, ids}) =>
                document.documentElement.dataset.readerUnit === unit &&
                ids.every(id => window.PRACTICE_MOUNT_REPORT?.[id]?.unit === unit) &&
                [...document.querySelectorAll('.practice-inline img')].every(image => image.complete),
            {unit, ids}, {timeout: 4000});
        } catch (error) {
            if (!(error instanceof playwrightErrors.TimeoutError)) throw error;
            problems.push(unit + ': reader did not report all expected mounts');
        }
        const mount = await page.evaluate(() => ({
            unit: document.documentElement.dataset.readerUnit,
            anchors: [...document.querySelectorAll('.practice-inline')]
                .map(node => ({id: node.dataset.practiceId, anchor: node.dataset.anchorState})),
            mathErrors: document.querySelectorAll('.practice-inline .katex-error').length,
            brokenImages: [...document.querySelectorAll('.practice-inline img')]
                .filter(node => !node.complete || !node.naturalWidth).map(node => node.getAttribute('src')),
        }));
        mount.count = mount.anchors.length;
        expectSameIds(mount.anchors.map(anchor => anchor.id), ids, unit + ' mounted', problems);
        if (mount.unit !== unit || mount.anchors.some(anchor => anchor.anchor !== 'matched') ||
            mount.mathErrors || mount.brokenImages.length) problems.push(unit + ': invalid mounted question');
        mounts.push(mount);
    }
    return {mounts, readerQuestions: mounts.reduce((total, mount) => total + mount.count, 0), problems};
}

async function main() {
    if (!process.argv[2]) throw new Error('Usage: node tools/check_practice_delivery.cjs DELIVERY [REPORT_DIR]');
    const root = path.resolve(process.argv[2]);
    const out = process.argv[3] ? path.resolve(process.argv[3]) : path.join(root, '核验/browser');
    fs.mkdirSync(out, {recursive: true});
    const {editions, jobs, problems} = readDelivery(root);
    const executablePath = process.env.AIFORSTUDY_BROWSER_EXECUTABLE;
    const browser = await chromium.launch(executablePath ? {executablePath, headless: true} : {channel: 'msedge', headless: true});
    try {
        const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
        const errors = [];
        const external = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.route('**/*', route => {
            const url = route.request().url();
            if (!/^https?:/i.test(url)) return route.continue();
            external.push(url);
            return route.abort();
        });
        const pages = [];
        for (const job of jobs) {
            if (!fs.existsSync(path.join(root, job.name))) {
                problems.push('Missing page: ' + job.name);
                continue;
            }
            pages.push(await inspectPage(page, root, job));
        }

        let mobile;
        const fullPage = path.join(root, '完整版-习题集.html');
        if (fs.existsSync(fullPage)) {
            await page.goto(pathToFileURL(fullPage).href);
            const solution = page.locator('.practice-solution summary').first();
            if (await solution.count()) await solution.click();
            await page.screenshot({path: path.join(out, 'practice-desktop.png')});
            await page.setViewportSize({width: 390, height: 844});
            await page.screenshot({path: path.join(out, 'practice-mobile.png')});
            mobile = await page.evaluate(() => ({width: innerWidth, scrollWidth: document.documentElement.scrollWidth}));
            if (mobile.scrollWidth > mobile.width) problems.push('Practice page overflows the mobile viewport');
            await page.setViewportSize({width: 1440, height: 1000});
        }

        const book = pathToFileURL(path.join(root, '教材/index.html')).href;
        const checked = {};
        for (const [name, rows] of Object.entries(editions)) {
            checked[name] = await inspectReader(page, book, name, rows, editions);
            if (name === 'full') {
                const fold = page.locator('.practice-inline').first();
                if (await fold.count()) {
                    await fold.locator('summary').first().click();
                    await fold.scrollIntoViewIfNeeded();
                }
                await page.screenshot({path: path.join(out, 'reader-practice.png')});
            }
        }
        const report = {pages, mobile, mounts: checked.full.mounts, editions: checked, problems, errors, external};
        fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
        const failedPages = pages.filter(page => page.restrictions.length || page.mathErrors.length ||
            page.unsupportedCommands.length || page.brokenImages.length);
        console.log(JSON.stringify({
            pages: pages.length, failedPages: failedPages.map(page => page.name),
            readerQuestions: Object.fromEntries(Object.entries(checked).map(([name, result]) => [name, result.readerQuestions])),
            readerProblems: Object.fromEntries(Object.entries(checked).map(([name, result]) => [name, result.problems])),
            mobile, problems, errors, external: external.length,
        }));
        if (problems.length || errors.length || external.length || failedPages.length ||
            Object.values(checked).some(edition => edition.problems.length)) {
            throw new Error('Delivery checks failed; see report.json');
        }
    } finally {
        await browser.close();
    }
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
