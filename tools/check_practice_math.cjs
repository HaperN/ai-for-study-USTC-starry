/* Check every published math field through the actual practice renderer. */
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');

function checkMath(files) {
  const dom = new JSDOM('<!doctype html><body></body>', {runScripts:'outside-only'});
  const win = dom.window;
  win.marked = require(root + '/blocks/render/vendor/marked.min.js');
  win.katex = require(root + '/blocks/render/vendor/katex.min.js');
  win.DSHRenderCore = require(root + '/blocks/render/assets/render-core.js');
  win.eval(fs.readFileSync(root + '/src/aiforstudy/resources/practice/practice.js','utf8'));
  const issues = [];
  let fields = 0, questions = 0;
  try {
    for (const file of files) {
      const rows = JSON.parse(fs.readFileSync(file,'utf8'));
      for (const q of rows) {
        questions++;
        for (const field of ['stem','answer','solution','pitfalls','source.common_header',
          'title','topic','priority_reason','source.adaptation_note']) {
          const value = field.startsWith('source.') ? q.source?.[field.slice(7)] : q[field];
          if (!value) continue;
          const text = Array.isArray(value) ? value.join('\n') : String(value);
          const inline = ['title','topic','priority_reason','source.adaptation_note'].includes(field);
          const node = inline ? win.AIFSPractice.inline('div','',text) : win.AIFSPractice.md(text);
          fields++;
          const unrendered = [...node.querySelectorAll('.katex-html')].map(n=>n.textContent).filter(s=>/\\[A-Za-z]+/.test(s));
          const errors = [...node.querySelectorAll('.katex-error')].map(n=>({text:n.textContent,reason:n.title}));
          if (errors.length || unrendered.length) issues.push({id:q.id,field,errors,unrendered});
        }
      }
    }
    return {questions, fields, issues, renderer:'src/aiforstudy/resources/practice/practice.js'};
  } finally {win.close();}
}

if (require.main === module) {
  const args = process.argv.slice(2), reportAt = args.indexOf('--report');
  const report = reportAt >= 0 ? args.splice(reportAt,2)[1] : null;
  if (!args.length) throw new Error('Usage: node tools/check_practice_math.cjs CATALOG... [--report FILE]');
  const result = checkMath(args);
  if (report) fs.writeFileSync(report, JSON.stringify(result,null,2));
  console.log(JSON.stringify({questions:result.questions,fields:result.fields,issues:result.issues.length,ids:[...new Set(result.issues.map(i=>i.id))]}));
  if (result.issues.length) process.exitCode=1;
}
module.exports={checkMath};
