const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const{JSDOM}=require('jsdom'),marked=require('../../blocks/render/vendor/marked.min.js'),katex=require('../../blocks/render/vendor/katex.min.js');
const core=require('../../blocks/render/assets/render-core.js'),layout=require('../../blocks/render/assets/reader-layout.js');
const fixture=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../fixtures/reader/hyperbolic-recovery.json'),'utf8'));
function rendered(t,unit=structuredClone(fixture)){
 const renderer=core.createRenderer({marked,katex});renderer.beginUnit({unitId:unit.unit_id});const original=JSON.stringify(unit);
 const dom=new JSDOM('<main>'+renderer.readerDocumentHtml(unit.reader_document,'body',unit.reader_media,unit.reader_layout)+'</main>');t.after(()=>dom.window.close());const root=dom.window.document.querySelector('main');const nodes=[...root.querySelectorAll('[data-reader-block],.katex,[data-media-anchor]')];
 layout.enhance(root,unit,unit.unit_id);assert.equal(JSON.stringify(unit),original,'the original recovered text, source maps and hashes are immutable');nodes.forEach(n=>assert.ok(root.contains(n),'retain every original formula, field and media node'));return root;
}
function closed(node){return !!node?.closest('details:not([open])');}
function paragraph(root,prefix){return [...root.querySelectorAll('p')].find(p=>p.textContent.trim().startsWith(prefix));}
test('the real recovered hyperbolic example heading folds its complete local media and example entry',t=>{
 const root=rendered(t),topic=[...root.querySelectorAll('[data-reader-topic]')].find(n=>n.dataset.readerTopicTitle==='例题');assert.ok(topic);
 const entry=topic.querySelector('details.reader-example-disclosure');assert.ok(entry);assert.equal(entry.open,false);assert.equal(entry.querySelector('summary').textContent,'例题');
 const mounts=[...root.querySelectorAll('[data-media-anchor]')];assert.equal(mounts.length,3);mounts.forEach(m=>assert.equal(closed(m),true,'every original example media stays inside the closed example'));
 const statement=root.querySelector('[data-reader-pointer="/examples/1/statement"]');assert.equal(closed(statement),true);entry.querySelector('summary').click();assert.equal(entry.open,true);
});
test('reviewed restored derivations close while their property statements and intuition remain visible',t=>{
 const root=rendered(t);
 for(const prefix of ['先看','由上面的关系式','先求','再求','由定义和','由指数函数的加法公式']){
  const p=paragraph(root,prefix);assert.ok(p,prefix);assert.equal(closed(p),true,prefix+' is an authored derivation, not the visible result');
 }
 for(const prefix of ['这些关系式说明','对于','这些点就是']){
  const p=[...root.querySelectorAll('p,h2,h3')].find(n=>n.textContent.trim().startsWith(prefix));assert.ok(p,prefix);assert.equal(closed(p),false,prefix+' keeps conditions or intuition visible');
 }
 const statements=[...root.querySelectorAll('blockquote')].filter(n=>n.textContent.includes('性质·双曲函数'));assert.ok(statements.length>=5);statements.forEach(n=>assert.equal(closed(n),false));
 assert.equal(root.querySelectorAll('[data-reader-recovery-proof]').length,6);assert.equal(root.querySelectorAll('.katex-error,.math-error').length,0);
});
test('the reviewed projection cannot silently apply to another revision with similar wording',t=>{
 const other=structuredClone(fixture);other.study_recovery.source_sha256='sha256:another-revision';const root=rendered(t,other);
 assert.equal(root.querySelectorAll('[data-reader-recovery-proof]').length,0);
});
