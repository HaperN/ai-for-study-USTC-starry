const fs=require('node:fs'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const marked=require('../../blocks/render/vendor/marked.min.js');
const core=require('../../blocks/render/assets/render-core.js'),layout=require('../../blocks/render/assets/reader-layout.js');
const unit=JSON.parse(fs.readFileSync(0,'utf8'));
// A mount resolved from the production layout's syntax blocks follows its owner.
const anchor=unit.reader_layout.blocks.find(b=>b.target_pointer==='/examples/0/steps/0/markdown');
assert.ok(anchor);
unit.reader_layout.mounts.push({mount_id:'test-media',anchor_id:'test-media-anchor',target_pointer:anchor.target_pointer,resolved_block_id:anchor.block_id,edge:'after',order_at_anchor:0,source_id:'test-media',source_kind:'media',unit_id:unit.unit_id,source_hash:'test-only',owner_id:anchor.owner_id});
const renderer=core.createRenderer({marked});renderer.beginUnit({unitId:unit.unit_id});
const dom=new JSDOM('<main>'+renderer.readerDocumentHtml(unit.reader_document,'body',[],unit.reader_layout,unit.teaching_presentation)+'</main>');
const root=dom.window.document.querySelector('main'),original=[...root.querySelectorAll('[data-reader-block],[data-media-anchor]')];
layout.enhance(root,unit,unit.unit_id);
if(unit.check_body){
 const p=text=>[...root.querySelectorAll('p')].find(n=>n.textContent===text);
 assert.equal(p('必要条件和主线结论。').closest('details'),null);
 assert.ok(p('独立例题题干。').closest('details[data-teaching-role="worked_example"]:not([open])'));
 assert.ok(p('独立证明推导。').closest('details[data-teaching-role="proof"]:not([open])'));
}
assert.ok(root.querySelector('[data-media-anchor]').closest('details:not([open])'),'media initially inside closed example');
for(const group of root.querySelectorAll('[data-reader-group="examples"]')){
 const outer=group.closest('[data-reader-topic]').querySelector('details.reader-example-disclosure');
 assert.ok(outer,'example has an accessible disclosure');assert.equal(outer.open,false);
 const fields=[...group.querySelectorAll('[data-reader-pointer]')].filter(n=>/\/(statement|markdown|basis|approach)$/.test(n.dataset.readerPointer));
 for(const field of fields)assert.ok(field.closest('details:not([open])'),'initially closed');
 outer.querySelector(':scope > summary').click();assert.equal(outer.open,true);
 for(const field of fields){
  assert.equal(field.closest('details:not([open])'),null,'one click reveals field '+field.dataset.readerPointer);
  assert.equal(field.querySelector('details.reader-teaching-disclosure[data-teaching-role="worked_example"]'),null,'same-owner role uses its example disclosure');
 }
 const media=group.querySelector('[data-media-anchor]');if(media)assert.equal(media.closest('details:not([open])'),null);
 for(const detail of group.querySelectorAll('details.reader-pitfall-item'))assert.equal(detail.open,false,'independent support remains closed');
 outer.open=false;
}
for(const n of original)assert.ok(root.contains(n),'original nodes retained');
assert.ok(root.querySelectorAll('[data-teaching-role="worked_example"]').length===0||root.querySelectorAll('[data-teaching-role="worked_example"]:not(details)').length>0,'role metadata retained');
layout.enhance(root,unit,unit.unit_id);
assert.equal(root.querySelectorAll('details.reader-example-disclosure').length,unit.example_count);
dom.window.close();
