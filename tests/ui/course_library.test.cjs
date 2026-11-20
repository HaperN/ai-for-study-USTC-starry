const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const library = require('../../blocks/render/assets/course-library.js');
const base = 'assets/source/' + 'a'.repeat(64) + '.pdf';
const source = {id:'s',sha256:'a'.repeat(64),url:base,provenance:[{source_kind:'学生维护',answer_note:'尚未验算'}]};
const locators = [{id:'q',source_id:'s',pdf_pages:[1,2]}, {id:'a',source_id:'s',pdf_pages:[3,4]}];
const exam = {id:'e',source_id:'s',course:'复分析',academic_year:null,year_as_listed:'2026',exam_type:'期末',question_locator_id:'q',answer_locator_id:null,teachers_row_as_listed:'张老师、王老师',teacher_specific:null,known_defective_items:[],content_quality_notes:[]};
const catalog = {sources:[source],locators,exam_sessions:[exam,{...exam,id:'a-only',course:'复变函数A',year_as_listed:'2025',question_locator_id:null,answer_locator_id:'a',known_defective_items:[{pdf_page:3,question:'3(3)',evidence:'原卷标错'}]}],textbook_pool:{chapters:[]},summary:{unique_exam_pdf_files:2,exam_sessions:2,textbook_chapters:0,textbook_top_level_questions:0}};

test('course, year and role filters preserve answer-only semantics', () => {
  const items = library.records(catalog);
  assert.equal(library.matches(items[1], {type:'questions'}), false);
  assert.equal(library.matches(items[1], {type:'answers-only',course:'复变函数A',year:'2025'}), true);
  assert.equal(library.matches(items[0], {course:'复变函数A'}), false);
  assert.equal(library.matches(items[0], {search:'王老师 2026'}), true);
  assert.equal(library.matches(items[1], {search:'错题'}), true);
});

test('page links use packaged PDFs and reject unsafe paths', () => {
  assert.equal(library.locatorUrl(catalog, 'a'), base + '#page=3');
  assert.equal(library.locatorUrl({...catalog,sources:[{...source,url:'https://evil.test/q.pdf'}]}, 'a'), null);
  assert.equal(library.locatorUrl(catalog, 'missing'), null);
});

test('normal reader entry opens filters and preserves source caveats safely', () => {
  const dom = new JSDOM('<button id="course-library-open" hidden>课程资料</button><dialog id="course-library-dialog"></dialog>');
  const doc = dom.window.document;
  const dialog = doc.querySelector('dialog');
  dialog.showModal = function() { this.open=true; };
  dialog.close = function() { this.open=false; };
  library.mount(doc, catalog);
  const open = doc.getElementById('course-library-open');
  assert.equal(open.hidden, false); open.click(); assert.equal(dialog.open, true);
  assert.match(dialog.textContent, /仅解答 · 无完整题卷/);
  assert.match(dialog.textContent, /该场教师未明确/);
  assert.match(dialog.textContent, /网页同一行教师.*张老师、王老师/);
  assert.match(dialog.textContent, /已标错题/);
  const type=doc.getElementById('course-library-type'); type.value='answers-only';
  type.dispatchEvent(new dom.window.Event('change'));
  assert.equal(dialog.querySelectorAll('.course-card:not([hidden])').length, 1);
  assert.equal(dialog.querySelector('.course-card:not([hidden]) a').getAttribute('href'), base+'#page=3');
  doc.getElementById('course-library-close').click(); assert.equal(dialog.open,false);
});

test('real corpus exposes all 129 numbered exercise page links and correct course counts', t => {
  const baseDir = require('node:path').resolve(__dirname, '../../data/parse/complex-analysis-v2/course-library');
  if (!fs.existsSync(baseDir+'/current.json')) return t.skip('Optional corpus not installed');
  const pointer=JSON.parse(fs.readFileSync(baseDir+'/current.json'));
  const real=JSON.parse(fs.readFileSync(baseDir+'/'+pointer.catalog));
  real.sources.forEach(s => {s.url='assets/source/'+s.sha256+'.pdf';});
  const dom=new JSDOM('<button id="course-library-open" hidden>课程资料</button><dialog id="course-library-dialog"></dialog>');
  library.mount(dom.window.document, real);
  const doc=dom.window.document;
  assert.equal(doc.querySelectorAll('.course-card').length,60);
  assert.equal(doc.querySelectorAll('.course-question-links a').length,129);
  for(const [course,count] of Object.entries({'教材':9,'复变函数A':7,'复变函数B':6,'复分析':38})) {
    assert.equal(library.records(real).filter(i=>library.matches(i,{course})).length,count);
  }
  assert.equal(library.records(real).filter(i=>library.matches(i,{type:'answers-only'})).length,1);
  assert.equal(library.records(real).filter(i=>library.matches(i,{type:'questions'})).length,50);
  assert.equal(library.records(real).filter(i=>library.matches(i,{type:'answers'})).length,4);
  for (const a of doc.querySelectorAll('a[href^="assets/"]')) assert.match(a.getAttribute('href'),/^assets\/source\/[a-f0-9]{64}\.pdf#page=\d+$/);
});
