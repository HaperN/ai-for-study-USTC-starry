'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {JSDOM} = require('jsdom');
const parser = require('../../blocks/render/assets/reader-markdown.js');
const marked = require('../../blocks/render/vendor/marked.min.js');

test('optional block and its mount stay inside a closed disclosure', () => {
  const raw = '条件成立时结论成立。\n\n证明：逐步推导。\n\n应用这个结论。';
  const field = {block_id:'b', json_pointer:'/body_markdown', owner_id:'u', raw_text:raw};
  const parsed = parser.parseReaderField(raw, {marked, field});
  const proof = parsed.blocks[1];
  const mounts = [{resolved_block_id:proof.block_id, edge:'after', order_at_anchor:0, mount_id:'m'}];
  const html = parser.renderReaderField(parsed, mounts, () => '<div class="reader-media">media</div>', {teachingRoles:{[proof.block_id]:'proof'}, bareMath:false});
  const dom = new JSDOM(html).window.document;
  const details = dom.querySelector('details[data-teaching-role="proof"]');
  assert.ok(details && !details.open);
  assert.match(details.textContent, /逐步推导/);
  assert.ok(details.querySelector('.reader-media'));
  assert.match(dom.body.textContent, /条件成立时结论成立/);
  assert.match(dom.body.textContent, /应用这个结论/);
});

function bind(unit, block, role) {
 const doc=unit.reader_document;doc.unit_id='u';doc.content_hash='sha256:content';
 const binding=[doc.unit_id,doc.content_hash,...doc.blocks.map(b=>['block_id','json_pointer','raw_text','markdown','visible_text','anchor','group','owner_id','field_label'].map(k=>String(b[k]??'')).concat([(b.source_map||[]).map(String),(b.marker_spans||[]).map(span=>['visible_start','visible_end','raw_start','raw_end'].map(k=>String(span[k]??'')))]))];
 const binding_hash='sha256:'+require('node:crypto').createHash('sha256').update(JSON.stringify(binding)).digest('hex');
 unit.reader_layout.reader_document_hash='sha256:reader';
 unit.teaching_presentation={schema_version:'TeachingPresentationV1',content_hash:doc.content_hash,reader_document_hash:'sha256:reader',binding_hash,roles:{[block.block_id]:role},regions:[{block_id:block.block_id,reader_block_id:block.field_id,reader_pointer:block.target_pointer,source_start_cp:block.source_start_cp,source_end_cp:block.source_end_cp,reader_role:role}]};
}

test('Reader layout preserves main conclusion and confines proof media', () => {
  const core = require('../../blocks/render/assets/render-core.js');
  const layout = require('../../blocks/render/assets/reader-layout.js');
  const raw = '## 定理\n\n条件成立时结论成立。\n\n这里是演算。\n\n方法适用于相同条件。';
  const field = {group:'body_markdown',block_id:'b',json_pointer:'/body_markdown',owner_id:'u',raw_text:raw,markdown:raw,field_label:'正文',anchor:'reader-12345678901234567890',collapsed:false};
  const parsed = parser.parseReaderField(raw, {marked,field});
  const proof = parsed.blocks.find(b => raw.slice(b.source_start_cp,b.source_end_cp).includes('演算'));
  const mount = {mount_id:'m',anchor_id:'a',target_pointer:'/body_markdown',resolved_block_id:proof.block_id,edge:'after',order_at_anchor:0,source_id:'v',source_kind:'media',unit_id:'u',source_hash:'x',owner_id:'u'};
  const unit = {title:'测试',reader_document:{schema_version:'ReaderDocumentV1',blocks:[field]},reader_layout:{schema_version:'ReaderLayoutV1',mounts:[mount]},teaching_presentation:{roles:{[proof.block_id]:'proof'}}};
  bind(unit,proof,'proof');
  const renderer = core.createRenderer({marked});renderer.beginUnit({unitId:'u'});
  const dom = new JSDOM('<main>'+renderer.readerDocumentHtml(unit.reader_document,'body',[],unit.reader_layout,unit.teaching_presentation)+'</main>');
  const root = dom.window.document.querySelector('main');layout.enhance(root,unit,'u');
  const detail = root.querySelector('details[data-teaching-role="proof"]');
  assert.ok(detail && !detail.open && detail.querySelector('[data-media-anchor]'));
  assert.ok([...root.querySelectorAll('p')].find(p=>p.textContent.includes('条件成立')).closest('details') === null);
  assert.ok([...root.querySelectorAll('p')].find(p=>p.textContent.includes('方法适用')).closest('details') === null);
  dom.window.close();
});

test('numbered section closing keeps proof mount inside disclosure', () => {
 const raw='## 定理1\n\n条件成立则结论成立。\n\n证明：逐步推导。\n\n## 方法\n\n主线方法。';
 const field={block_id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',json_pointer:'/body_markdown',owner_id:'u',raw_text:raw,markdown:raw};
 const parsed=parser.parseReaderField(raw,{marked,field}); const proof=parsed.blocks.find(b=>raw.slice(b.source_start_cp,b.source_end_cp).includes('逐步推导'));
 const mounts=[{resolved_block_id:proof.block_id,edge:'after',order_at_anchor:0,mount_id:'m'}];
 const html=parser.renderReaderField(parsed,mounts,()=>'<div class="reader-media">media</div>',{teachingRoles:{[proof.block_id]:'proof'},bareMath:false});
 const doc=new JSDOM(html).window.document;assert.ok(doc.querySelector('details[data-teaching-role=proof] .reader-media'));
});

test('explicit proof does not disable folding unrelated legacy example', () => {
 const core=require('../../blocks/render/assets/render-core.js'),layout=require('../../blocks/render/assets/reader-layout.js');
 const raw='## 定理\n\n可见结论。\n\n明确推演。\n\n### 例3：测试\n\n题目内容。\n\n答案内容。\n\n## 后续\n\n后续解释。';
 const field={group:'body_markdown',block_id:'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',json_pointer:'/body_markdown',owner_id:'u',raw_text:raw,markdown:raw,field_label:'正文',anchor:'reader-12345678901234567890',collapsed:false};
 const parsed=parser.parseReaderField(raw,{marked,field}),proof=parsed.blocks.find(b=>raw.slice(b.source_start_cp,b.source_end_cp).includes('明确推演'));
 const unit={title:'测试',reader_document:{schema_version:'ReaderDocumentV1',blocks:[field]},reader_layout:{schema_version:'ReaderLayoutV1',mounts:[]},teaching_presentation:{roles:{[proof.block_id]:'proof'}}};
 bind(unit,proof,'proof');
 const renderer=core.createRenderer({marked});renderer.beginUnit({unitId:'u'});
 const dom=new JSDOM('<main>'+renderer.readerDocumentHtml(unit.reader_document,'body',[],unit.reader_layout,unit.teaching_presentation)+'</main>');
 const root=dom.window.document.querySelector('main');layout.enhance(root,unit,'u');
 assert.ok([...root.querySelectorAll('p')].find(p=>p.textContent.includes('题目内容')).closest('details:not([open])'));
 assert.ok(root.querySelector('details[data-teaching-role=proof]'));dom.window.close();
});

test('stale presentation is rejected by Reader consumer', () => {
 const core=require('../../blocks/render/assets/render-core.js');
 const field={block_id:'b',json_pointer:'/body_markdown',owner_id:'u',raw_text:'main',markdown:'main'};
 const block=parser.parseReaderField('main',{marked,field}).blocks[0];
 const unit={reader_document:{schema_version:'ReaderDocumentV1',blocks:[field]},reader_layout:{schema_version:'ReaderLayoutV1',mounts:[]}};bind(unit,block,'proof');
 const renderer=core.createRenderer({marked});renderer.beginUnit({unitId:'u'});
 unit.reader_document.blocks[0].raw_text='changed';
 assert.throws(()=>renderer.readerDocumentHtml(unit.reader_document,'body',[],unit.reader_layout,unit.teaching_presentation),/Stale teaching presentation/);
});

test('stale reader hash and nonexistent token assignments are rejected', () => {
 const core=require('../../blocks/render/assets/render-core.js');
 const field={group:'body_markdown',block_id:'b',json_pointer:'/body_markdown',owner_id:'u',raw_text:'main',markdown:'main'};
 const block=parser.parseReaderField('main',{marked,field}).blocks[0];
 const unit={reader_document:{schema_version:'ReaderDocumentV1',blocks:[field]},reader_layout:{schema_version:'ReaderLayoutV1',mounts:[]}};bind(unit,block,'proof');
 const renderer=core.createRenderer({marked});renderer.beginUnit({unitId:'u'});
 unit.teaching_presentation.reader_document_hash='sha256:old';
 assert.throws(()=>renderer.readerDocumentHtml(unit.reader_document,'body',[],unit.reader_layout,unit.teaching_presentation),/Stale/);
 bind(unit,block,'proof');unit.teaching_presentation.roles={'unknown':'proof'};
 assert.throws(()=>renderer.readerDocumentHtml(unit.reader_document,'body',[],unit.reader_layout,unit.teaching_presentation),/Invalid/);
});

test('explicit main conclusion remains visible beside worked example and legacy example still folds', () => {
 const core=require('../../blocks/render/assets/render-core.js'),layout=require('../../blocks/render/assets/reader-layout.js');
 const raw='### 例3：测试\n\n题干及完整解答。\n\n若条件成立，则该方法适用于所有此类问题。\n\n## 后续\n\n后续解释。\n\n### 例4：旧题\n\n旧题题干和解答。';
 const field={group:'body_markdown',block_id:'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',json_pointer:'/body_markdown',owner_id:'u',raw_text:raw,markdown:raw,field_label:'正文',anchor:'reader-12345678901234567890',collapsed:false};
 const parsed=parser.parseReaderField(raw,{marked,field});
 const example=parsed.blocks.find(b=>raw.slice(b.source_start_cp,b.source_end_cp).includes('题干及完整解答'));
 const main=parsed.blocks.find(b=>raw.slice(b.source_start_cp,b.source_end_cp).includes('若条件成立'));
 const unit={title:'测试',reader_document:{schema_version:'ReaderDocumentV1',blocks:[field]},reader_layout:{schema_version:'ReaderLayoutV1',mounts:[]}};bind(unit,example,'worked_example');
 unit.teaching_presentation.roles[main.block_id]='main';unit.teaching_presentation.regions.push({block_id:main.block_id,reader_block_id:main.field_id,reader_pointer:main.target_pointer,source_start_cp:main.source_start_cp,source_end_cp:main.source_end_cp,reader_role:'main'});
 const renderer=core.createRenderer({marked});renderer.beginUnit({unitId:'u'});
 const dom=new JSDOM('<main>'+renderer.readerDocumentHtml(unit.reader_document,'body',[],unit.reader_layout,unit.teaching_presentation)+'</main>');const root=dom.window.document.querySelector('main');layout.enhance(root,unit,'u');
 const find=text=>[...root.querySelectorAll('p')].find(p=>p.textContent.includes(text));
 assert.equal(find('若条件成立').closest('details:not([open])'),null);
 assert.ok(find('题干及完整解答').closest('details:not([open])'));
 assert.ok(find('旧题题干').closest('details:not([open])'));dom.window.close();
});

test('explicit main text is not reclassified as a legacy proof', () => {
 const layout=require('../../blocks/render/assets/reader-layout.js');
 const raw='## 定理1\n\n证明：这是主线必要方法。\n\n下一段说明。';
 const field={block_id:'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',json_pointer:'/body_markdown',owner_id:'u',raw_text:raw,markdown:raw};
 const parsed=parser.parseReaderField(raw,{marked,field}),main=parsed.blocks.find(b=>raw.slice(b.source_start_cp,b.source_end_cp).includes('主线必要'));
 const html=parser.renderReaderField(parsed,[],()=>'',{teachingRoles:{[main.block_id]:'main'},bareMath:false});
 const dom=new JSDOM('<main><div class="markdown body" data-reader-pointer="/body_markdown">'+html+'</div></main>');const root=dom.window.document.querySelector('main');
 layout.enhance(root,{title:'测试'},'u');
 const p=[...root.querySelectorAll('p')].find(n=>n.textContent.includes('主线必要'));assert.equal(p.closest('details:not([open])'),null);dom.window.close();
});
