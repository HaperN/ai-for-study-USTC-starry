const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.resolve(__dirname, '../../blocks/render/assets/models.js'), 'utf8');
function setup(params) {
  const dom = new JSDOM('<figure class="model model-canvas" data-model-id="H1"><div class="model-stage"></div><div class="model-ui"></div></figure>', {runScripts:'outside-only'});
  dom.window.console = {log(){},warn(){}};
  dom.window.eval(source);
  const figure = dom.window.document.querySelector('figure');
  dom.window.Models.mount(figure, {id:'H1',template:'harmonic-contours',kind:'svg',params,
    controls:[{type:'drag'},{type:'slider'}],steps:[],readouts:[],fallback_text:'参数不可用'});
  return {dom, figure, value:key => figure.querySelector('[data-harmonic-value="'+key+'"]')?.textContent,
    set:(axis,value) => { const input=figure.querySelector('input[data-axis="'+axis+'"]'); input.value=value; input.dispatchEvent(new dom.window.Event('input')); }};
}
test('real harmonic controls change the selected contours, gradient values and origin exception', () => {
  const page = setup({initial_point:[1,1],extent:3});
  const {figure,value,set,dom} = page;
  assert.ok(figure.querySelector('svg.harmonic-plot'), 'safe built-in SVG template must mount');
  assert.equal(figure.classList.contains('model-canvas'), false);
  assert.equal(value('u'),'0.00'); assert.equal(value('v'),'2.00');
  const before=figure.querySelector('[data-contour="u"]').getAttribute('d');
  set('x',2);
  assert.equal(value('u'),'3.00'); assert.equal(value('v'),'4.00');
  assert.equal(value('grad-u'),'(4.00, −2.00)');
  assert.equal(value('grad-v'),'(2.00, 4.00)');
  assert.equal(value('angle'),'90°');
  assert.notEqual(figure.querySelector('[data-contour="u"]').getAttribute('d'), before);
  assert.ok(figure.querySelector('[data-contour="v"]').getAttribute('d').length > 100);
  set('x',0); set('y',0);
  assert.match(figure.querySelector('.harmonic-status').textContent,/原点/);
  assert.match(figure.querySelector('.harmonic-status').textContent,/不能.*正交/);
  assert.equal(value('angle'),'不适用');
  assert.equal(figure.querySelector('[data-tangent="u"]').getAttribute('visibility'),'hidden');
  assert.equal(figure.querySelector('[data-gradient="u"]').getAttribute('visibility'),'hidden');
  assert.equal(figure.querySelectorAll('script').length,0);
  dom.window.close();
});
test('invalid and executable parameters are rejected before rendering or creating controls', () => {
  for (const params of [null, {}, {initial_point:[1],extent:3}, {initial_point:['1',1],extent:3},
    {initial_point:[Infinity,1],extent:3}, {initial_point:[0,0],extent:0},
    {initial_point:[0,0],extent:5}, {initial_point:[4,1],extent:3},
    {initial_point:[1,1],extent:3,code:'window.pwned=1'},
    {initial_point:[1,1],extent:3,fn:'x*x-y*y'}]) {
    const {dom,figure}=setup(params);
    assert.equal(figure.classList.contains('model-failed'),true,JSON.stringify(params));
    assert.equal(figure.querySelector('svg'),null);
    assert.equal(figure.querySelector('input'),null);
    assert.equal(dom.window.pwned,undefined);
    dom.window.close();
  }
});
test('boundary and negative coordinates stay finite and reset to the requested point', () => {
  const {dom,figure,value,set}=setup({initial_point:[-4,4],extent:4});
  assert.ok(figure.querySelector('svg'));
  assert.equal(value('v'),'-32.00');
  set('x',0); set('y',-2);
  assert.equal(value('u'),'-4.00');
  for (const path of figure.querySelectorAll('[data-contour]')) assert.doesNotMatch(path.getAttribute('d'),/NaN|Infinity/);
  [...figure.querySelectorAll('button')].find(b=>b.textContent==='重置选点').click();
  assert.equal(value('v'),'-32.00');
  dom.window.close();
});
test('finite subnormal coordinates do not create invalid SVG paths', () => {
  const {dom,figure}=setup({initial_point:[Number.MIN_VALUE,1],extent:3});
  assert.ok(figure.querySelector('svg'));
  for (const path of figure.querySelectorAll('path[d]')) assert.doesNotMatch(path.getAttribute('d'),/NaN|Infinity/);
  assert.equal(figure.classList.contains('model-failed'),false);
  dom.window.close();
});
