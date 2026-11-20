const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const {execFileSync}=require('node:child_process');const {JSDOM}=require('jsdom');const lib=require('../../blocks/render/assets/course-library.js');
test('real study projection preserves separate courses, fixed budget and source pages',()=>{
 const python=process.env.EXAM_TEST_PYTHON || 'python3';
 const c=JSON.parse(execFileSync(python,['-c',"import json;from aiforstudy.core.course_library import project_library;x={};project_library(x,'data/parse/complex-analysis-v2/course-library');print(json.dumps(x['course_library']))"],{env:{...process.env,PYTHONPATH:'src'},encoding:'utf8'}));
 const doc=new JSDOM(lib.renderStudy(c)).window.document;
 assert.equal(doc.querySelectorAll('ol li').length,12);assert.equal(doc.querySelectorAll('tbody tr').length,18);
 assert.match(doc.body.textContent,/复变函数A/);assert.match(doc.body.textContent,/复变函数B/);
 assert.match(doc.body.textContent,/strict_asof 不成立/);assert.match(doc.body.textContent,/资料条件冲突/);assert.match(doc.body.textContent,/未排除除a以外的零点/);assert.equal(c.exam_study.questions.filter(q=>q.annotation.status==='pending').length,11);
 for(const a of doc.querySelectorAll('a'))assert.match(a.getAttribute('href'),/^assets\/source\/[a-f0-9]{64}\.pdf#page=\d+$/);
 assert.match(doc.body.textContent,/120分钟/);assert.match(doc.body.textContent,/分值覆盖：未知/);
});
