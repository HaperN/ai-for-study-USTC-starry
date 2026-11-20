const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const file=path.resolve(__dirname,'../../blocks/render/assets/study-math.js');
test('sum limit integral bounds stack even in inline math without changing variable subscripts',()=>{
  assert.ok(fs.existsSync(file),'shared operator rendering module is required');
  const math=require(file);
  const input=String.raw`\lim_{n\to\infty}x_n+\sum\nolimits_{k=0}^{N}a_k+\int_0^1 f(x)\,dx`;
  const result=math.operatorLimits(input);
  assert.match(result,/\\lim\\limits_/);assert.match(result,/\\sum\\limits_/);assert.match(result,/\\int\\limits_/);
  assert.ok(result.includes('x_n'));assert.ok(result.includes('a_k'));assert.ok(!result.includes('nolimits'));
  assert.equal(math.operatorLimits(result),result,'operator normalization is idempotent');
  const katex=require('../../blocks/render/vendor/katex.min.js');
  const dom=new JSDOM(katex.renderToString(result,{displayMode:false,throwOnError:true}));
  assert.equal(dom.window.document.querySelectorAll('.op-limits').length,3);dom.window.close();
});

test('a cases row break followed by a TeX command preserves both the row and pi',()=>{
 const render=require('../../blocks/render/assets/render-core.js').createRenderer({marked:require('../../blocks/render/vendor/marked.min.js'),katex:require('../../blocks/render/vendor/katex.min.js')});
 const raw=String.raw`$$\arg z=\begin{cases}\arctan(y/x),&x>0,\\\pi+\arctan(y/x),&x<0.\end{cases}$$`;
 const dom=new JSDOM(render.renderMarkdown(raw));const d=dom.window.document;
 assert.ok(d.querySelector('.katex-html').textContent.includes('π'),'the second row begins with pi, not the product p i');
 assert.ok(d.querySelector('annotation').textContent.includes(String.raw`\\\pi`));
 assert.equal(d.querySelectorAll('.math-error,.katex-error').length,0);dom.window.close();
});

test('Unicode sum and integral notation follows the same above-and-below bounds rule',()=>{
 const math=require(file),katex=require('../../blocks/render/vendor/katex.min.js');
 const tex=math.operatorLimits('∑_{n=0}^{N}a_n+∫_0^1f(x)dx');
 const dom=new JSDOM(katex.renderToString(tex,{throwOnError:true,displayMode:false}));
 assert.equal(dom.window.document.querySelectorAll('.op-limits').length,2);assert.ok(tex.includes('a_n'));assert.equal(math.operatorLimits(tex),tex);dom.window.close();
});

test('ordinary mathematical relations in prose are typeset while code and prose stay literal',()=>{
 const render=require('../../blocks/render/assets/render-core.js').createRenderer({marked:require('../../blocks/render/vendor/marked.min.js'),katex:require('../../blocks/render/vendor/katex.min.js')});
 const dom=new JSDOM(render.renderMarkdown('当 z = 0 时方向无定义；若 n > N，则误差变小。∞ 不是普通复数。\n\n`z = 0` 是输入代码。'));
 const d=dom.window.document;assert.equal(d.querySelectorAll('.katex').length,3);assert.equal(d.querySelector('code').textContent,'z = 0');assert.match(d.body.textContent,/方向无定义/);dom.window.close();
});

test('frozen reader support also typesets bare relations without changing its source text',()=>{
 const render=require('../../blocks/render/assets/render-core.js').createRenderer({marked:require('../../blocks/render/vendor/marked.min.js'),katex:require('../../blocks/render/vendor/katex.min.js')});
 const raw='当 z = 0 时无辐角，若 n > N 则进入邻域。';
 const source={schema_version:'ReaderDocumentV1',unit_id:'U',blocks:[{group:'references',json_pointer:'/references/0/definition',raw_text:raw,markdown:raw,block_id:'a',field_label:'定义',anchor:'reader-a',owner_id:'R'}]};
 const before=JSON.stringify(source),dom=new JSDOM(render.readerDocumentHtml(source,'all'));
 assert.equal(dom.window.document.querySelectorAll('.katex').length,2);assert.equal(JSON.stringify(source),before);dom.window.close();
});

test('plot-label styling preserves the hidden MathML and internal KaTeX layout',()=>{
 const katex=require('../../blocks/render/vendor/katex.min.js');
 const css=['../../blocks/render/vendor/katex.min.css','../../blocks/render/assets/study-reader.css','../../blocks/render/assets/study-light.css'].map(f=>fs.readFileSync(path.resolve(__dirname,f),'utf8')).join('\n');
 const dom=new JSDOM('<style>'+css+'</style><body class="book-page"><aside class="lesson-media-rail"><div class="study-plot-labels"><span>'+katex.renderToString(String.raw`w/e^a`)+'</span></div></aside></body>');
 const mathml=dom.window.document.querySelector('.katex-mathml');assert.equal(dom.window.getComputedStyle(mathml).position,'absolute','accessible MathML must stay visually hidden instead of overlapping the equation');dom.window.close();
});
