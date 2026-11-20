#!/usr/bin/env node
/* Read-only whole-book DOM audit. Uses the real app route and source renderers,
   does not load WebGL, videos, network resources, or modify the book data. */
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM,VirtualConsole}=require('jsdom');
const visuals=require('../blocks/render/assets/study-visuals.js');
for(const name of ['study-foundation-scenes','study-special-scenes']){const plugin=path.resolve(__dirname,'../blocks/render/assets/'+name+'.js');if(fs.existsSync(plugin))visuals.registerScenes(require(plugin));}
const book=path.resolve(process.argv[2]||'../delivery/复变函数-交互有声版-20261028/book');
const output=path.resolve(process.argv[3]||'../delivery/复变函数-交互有声版-20261028/visual-coverage.json');
const context={window:{}};vm.runInNewContext(fs.readFileSync(path.join(book,'data/data.js'),'utf8'),context);
const data=context.window.__DATA__;
const template=fs.readFileSync(path.resolve(__dirname,'../blocks/render/templates/index.html'),'utf8');
const errors=[];const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',e=>errors.push(String(e.message)));virtualConsole.on('error',e=>errors.push(String(e)));
const dom=new JSDOM(template,{url:'https://offline.test/#/'+data.scope[0],runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
const win=dom.window;win.scrollTo=()=>{};win.HTMLElement.prototype.scrollIntoView=()=>{};win.requestAnimationFrame=()=>1;win.cancelAnimationFrame=()=>{};
win.IntersectionObserver=class {observe(){}unobserve(){}disconnect(){}};
win.__DATA__={...data,animations:{},models:{}};
win.marked=require('../blocks/render/vendor/marked.min.js');win.katex=require('../blocks/render/vendor/katex.min.js');
win.DSHRenderCore=require('../blocks/render/assets/render-core.js');win.DSHReaderLayout=require('../blocks/render/assets/reader-layout.js');
win.eval(fs.readFileSync(path.resolve(__dirname,'../blocks/render/assets/app.js'),'utf8'));
const report={generatedAt:new Date().toISOString(),book,dataUnits:Object.keys(data.units).length,method:'Actual app.js unit routing + source render-core/reader-layout; mathematical scene model geometry; no GPU allocation. A binding or illustrative model does not certify exact textbook equivalence.',units:[],summary:{cards:0,interactive:0,unmatched:0,titleMatched:0,bodyMatched:0,contextMatched:0,missingOwners:0,sceneCounts:{}},errors};
const selector='[data-reader-concept-title],[data-reader-reference],[data-reader-group="references"],[data-reader-group="concepts"],[data-reader-group="annotations"],[data-reader-group="explainables"],.reader-pitfall-item';
for(const unitId of data.scope){
  win.history.replaceState(null,'','#/'+unitId);win.dispatchEvent(new win.HashChangeEvent('hashchange'));
  const root=win.document.querySelector('#content');
  if(win.document.documentElement.dataset.readerUnit!==unitId){errors.push(unitId+': app did not render requested unit');continue;}
  const targets=[...root.querySelectorAll(selector)].filter(n=>!n.closest('[hidden],.source-material,.original')&&!n.matches('[data-reader-concept-registration]'));
  visuals.enhance(root,{data,unitId});
  for(const card of root.querySelectorAll('.study-visual')){var topic=card.__studySource&&card.__studySource.closest('[data-reader-topic-kind="example"]');if(topic&&!card.closest('.reader-example-disclosure'))errors.push(unitId+': example graph outside its disclosure: '+card.dataset.studyOwner);}
  const cards=[...root.querySelectorAll('.study-visual')].map(card=>{const m=card.__studyModel;return {owner:card.dataset.studyOwner,target:card.dataset.studyTarget,title:card.querySelector('.study-visual-title').textContent,scene:card.dataset.studyScene,coverage:card.dataset.studyCoverage,matchSource:card.dataset.studyMatch,formula:m.scene.formula,explanation:m.scene.explanation,sourceExcerpt:(card.__studyBody||'').slice(0,1800),lines:visuals.geometry(m).lines.length};});
  const boundOwners=new Set(cards.map(c=>c.owner));
  const missingOwners=targets.filter(n=>!n.__studyBound&&!boundOwners.has(n.dataset.readerOwner)).map(n=>({owner:n.dataset.readerOwner||n.id,title:(n.getAttribute('data-reader-concept-title')||n.getAttribute('data-reader-reference')||n.textContent).slice(0,100)}));
  const item={unitId,title:data.units[unitId].title,cards,missingOwners};report.units.push(item);
  report.summary.cards+=cards.length;report.summary.missingOwners+=missingOwners.length;
  for(const c of cards){report.summary.sceneCounts[c.scene]=(report.summary.sceneCounts[c.scene]||0)+1;if(c.coverage==='unmatched')report.summary.unmatched++;else report.summary.interactive++;if(c.matchSource==='title')report.summary.titleMatched++;if(c.matchSource==='body')report.summary.bodyMatched++;if(c.matchSource==='context')report.summary.contextMatched++;}
}
dom.window.close();fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');
const unmatched=report.units.flatMap(u=>u.cards.filter(c=>c.coverage==='unmatched').map(c=>({unit:u.unitId,unitTitle:u.title,...c})));fs.writeFileSync(path.join(path.dirname(output),'visual-unmatched.json'),JSON.stringify(unmatched,null,2)+'\n');
console.log(JSON.stringify({output,units:report.units.length,...report.summary,errors:errors.length},null,2));
if(report.summary.missingOwners||report.units.length!==Object.keys(data.units).length||errors.length)process.exitCode=1;
