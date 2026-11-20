/* Diagnose real rendered output, including formulas which never reached KaTeX. */
const fs=require('node:fs'),path=require('node:path'),{JSDOM}=require('jsdom');
const book=path.resolve(process.argv[2]||'../delivery/复变函数-教材连贯版-20261029/book');
const output=path.resolve(process.argv[3]||'../discussion-audit-20261029/wholebook/math-baseline.json');
const raw=fs.readFileSync(path.join(book,'data/data.js'),'utf8');
const match=raw.match(/window\.__DATA__\s*=\s*([\s\S]*?);?\s*$/);
const data=JSON.parse(match[1].replace(/;\s*$/,''));
const renderer=require('../blocks/render/assets/render-core.js').createRenderer({marked:require('../blocks/render/vendor/marked.min.js'),katex:require('../blocks/render/vendor/katex.min.js'),concepts:data.concepts,figures:data.figures,models:data.models,sourceAssets:data.source_assets});
const dom=new JSDOM('<main></main>'),host=dom.window.document.querySelector('main'),rows=[];
let renderedMath=0;
for(const [uid,u] of Object.entries(data.units)){
 renderer.beginUnit(u);
 const kinds=[['body',renderer.renderMarkdown(u.study_lesson.body_markdown)],['support',renderer.readerDocumentHtml(u.reader_document,'all')],['textbook',renderer.renderOriginal(u.study_textbook_display||u.study_textbook_original||'')]];
 for(const [kind,html] of kinds){
  host.innerHTML=html;renderedMath+=host.querySelectorAll('.katex').length;
  const errors=[...host.querySelectorAll('.math-error,.katex-error,.source-formula-unrendered')].map(x=>({text:x.textContent,error:x.getAttribute('title')}));
  host.querySelectorAll('.katex,code,script,style').forEach(x=>x.remove());
  const bare=[],walker=dom.window.document.createTreeWalker(host,4);let n;
  while(n=walker.nextNode()){const s=n.nodeValue;if(/^\s*\[\[(?:TEACHING|TEXTBOOK)_/.test(s))continue;if(/\\[a-zA-Z]+|[_^][{a-zA-Z0-9]|[∑∏∫∮∂≤≥≠∞]|[a-zA-Z]\s*[=<>]\s*[-a-zA-Z0-9]/.test(s))bare.push(s.trim().slice(0,350));}
  if(errors.length||bare.length)rows.push({uid,kind,errors,bare});
 }
}
for(const [id,material] of Object.entries(data.source_materials||{})){
 host.innerHTML=renderer.renderSourceMaterial(material);renderedMath+=host.querySelectorAll('.katex').length;
 const errors=[...host.querySelectorAll('.math-error,.katex-error,.source-formula-unrendered')].map(x=>({text:x.textContent,error:x.getAttribute('title')}));
 host.querySelectorAll('.katex,code,script,style').forEach(x=>x.remove());
 const bare=[],walker=dom.window.document.createTreeWalker(host,4);let n;
 while(n=walker.nextNode())if(/\\[a-zA-Z]+|[_^][{a-zA-Z0-9]|[∑∏∫∮∂≤≥≠∞]|[a-zA-Z]\s*[=<>]\s*[-a-zA-Z0-9]/.test(n.nodeValue))bare.push(n.nodeValue.trim().slice(0,350));
 if(errors.length||bare.length)rows.push({uid:id,kind:'answer-material',errors,bare});
}
const report={book,generatedAt:new Date().toISOString(),scope:'All 76 authored bodies, active reader support, textbook comparison text and the independent answer page including its notes/raw display. Bare-math findings are candidates requiring classification, not automatic errors.',renderedMath,errors:rows.reduce((n,r)=>n+r.errors.length,0),bareCandidates:rows.reduce((n,r)=>n+r.bare.length,0),rows};
fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,JSON.stringify(report,null,2));
console.log(JSON.stringify({output,renderedMath,errors:report.errors,bareCandidates:report.bareCandidates,errorsByKind:Object.fromEntries(['body','support','textbook'].map(k=>[k,rows.filter(r=>r.kind===k).reduce((n,r)=>n+r.errors.length,0)])),errorSamples:rows.filter(r=>r.errors.length).slice(0,6)},null,2));dom.window.close();

process.exitCode=report.errors||report.bareCandidates?1:0;
