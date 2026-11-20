const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const file = path.resolve(__dirname, '../../blocks/render/assets/study-text-led-figures.js');
function fixture(t, kind) {
  assert.ok(fs.existsSync(file), 'the text-led figure module must provide real inline figures');
  const dom = new JSDOM('<main></main>', { pretendToBeVisual: true });
  const w = dom.window;
  // jsdom has no canvas renderer; the actual multiplication scene still owns
  // its state, elements and event listeners, and safely accepts null contexts.
  w.HTMLCanvasElement.prototype.getContext = () => null;
  w.ComplexMultiplication = require('../../examples/text-led-multiplication/multiplication.js');
  const figure = require(file).createFigure(w.document, {
    kind, title: '正文中的对应图', caption: '改变参数，比较正文中的关系。'
  });
  w.document.querySelector('main').appendChild(figure);
  t.after(() => { if (figure.__dispose) figure.__dispose(); w.close(); });
  return { w, figure };
}
function set(w, figure, key, value) {
  const input = figure.querySelector('[data-tlf-param="' + key + '"]');
  assert.ok(input, 'a visible coordinate control exists for ' + key);
  input.value = value;
  input.dispatchEvent(new w.Event('input', { bubbles: true }));
}
function point(figure, name) { return figure.querySelector('svg [data-point="' + name + '"]'); }
function metric(figure, name) { return figure.querySelector('[data-metric="' + name + '"]'); }
function click(figure, action) {
  const button = figure.querySelector('[data-tlf-action="' + action + '"]');
  assert.ok(button, action + ' is available');
  button.click();
}
function near(a, b, epsilon = 1e-8) { assert.ok(Math.abs(Number(a) - b) < epsilon, a + ' ≈ ' + b); }

test('the compact polar quantities use the textbook principal-argument notation and explicit TeX',t=>{
 const dom=new JSDOM('<main></main>'),w=dom.window,katex=require('../../blocks/render/vendor/katex.min.js');t.after(()=>w.close());
 w.DSHAnimations=require('../../blocks/render/assets/animations.js');w.katex={render(tex,node,options){node.innerHTML=katex.renderToString(tex,options);}};
 const f=require(file).createFigure(w.document,{kind:'polar',title:'坐标'});t.after(()=>f.__dispose());
 const dt=f.querySelector('[data-metric="argument"]').previousElementSibling;assert.equal(dt.querySelector('annotation').textContent,String.raw`\arg z`);
 assert.doesNotMatch(dt.textContent,/Arg/);assert.equal(f.querySelector('[data-metric="polar"]').parentElement.dataset.detailOnly,'true');
});

test('conjugate figure draws reflected points with equal distances and coordinate projections', t => {
  const { figure } = fixture(t, 'conjugate');
  assert.equal(figure.tagName, 'FIGURE');
  assert.equal(figure.dataset.studyScene, 'conjugate');
  assert.equal(figure.dataset.studyCoverage, 'authored');
  assert.equal(figure.dataset.textLedFigure, 'true');
  assert.ok(figure.querySelector('figcaption').textContent.includes('改变参数'));
  const z = point(figure, 'z'), bar = point(figure, 'conjugate');
  assert.equal(z.namespaceURI, 'http://www.w3.org/2000/svg');
  near(z.getAttribute('cx'), Number(bar.getAttribute('cx')));
  near(Number(z.getAttribute('cy')) + Number(bar.getAttribute('cy')), 400);
  assert.match(figure.textContent, /z = 2 \+ i/);
  assert.match(figure.textContent, /共轭.*2 − i/);
  near(metric(figure, 'modulus').dataset.value, Math.sqrt(5));
  near(metric(figure, 'conjugate-modulus').dataset.value, Math.sqrt(5));
  for (const label of ['x', 'y', '-y']) assert.ok(figure.querySelector('[data-projection="' + label + '"]'));
});

test('conjugate points on the real axis and at zero are visibly identified as coincident', t => {
  const { w, figure } = fixture(t, 'conjugate');
  set(w, figure, 'y', 0);
  near(point(figure, 'z').getAttribute('cy'), Number(point(figure, 'conjugate').getAttribute('cy')));
  assert.match(figure.querySelector('[data-point-label="coincident"]').textContent, /z.*共轭.*重合/);
  assert.equal(figure.querySelectorAll('[data-projection="y"], [data-projection="-y"]').length, 0);
  set(w, figure, 'x', 0);
  assert.match(figure.querySelector('[data-point-label="coincident"]').textContent, /0/);
  near(metric(figure, 'modulus').dataset.value, 0);
  assert.match(figure.querySelector('[data-origin-label]').textContent, /0/);
});

test('polar diagram uses all quadrants and the principal value pi on the negative real axis', t => {
  const { w, figure } = fixture(t, 'polar');
  assert.ok(figure.querySelector('svg [data-vector="radius"]'));
  near(metric(figure, 'radius').dataset.value, Math.sqrt(13));
  near(metric(figure, 'argument').dataset.value, Math.atan2(2, 3));
  set(w, figure, 'x', -3);
  set(w, figure, 'y', -2);
  near(metric(figure, 'argument').dataset.value, -2.5535900500422257);
  assert.ok(figure.querySelector('path[data-angle-arc]').getAttribute('d').includes('A'));
  set(w, figure, 'y', 0);
  near(metric(figure, 'argument').dataset.value, Math.PI);
  assert.match(metric(figure, 'argument').textContent, /π/);
});

test('polar zero has no argument and no misleading angle arc', t => {
  const { w, figure } = fixture(t, 'polar');
  set(w, figure, 'x', 0);
  set(w, figure, 'y', 0);
  assert.equal(metric(figure, 'argument').dataset.value, 'undefined');
  assert.match(metric(figure, 'argument').textContent, /无定义/);
  assert.equal(figure.querySelector('path[data-angle-arc]'), null);
  near(metric(figure, 'radius').dataset.value, 0);
});

test('vector diagram retains both original vectors and the sum, difference and translated copy', t => {
  const { w, figure } = fixture(t, 'vectors');
  for (const name of ['z1', 'z2', 'translated-z2', 'sum', 'difference']) {
    assert.ok(figure.querySelector('svg [data-vector="' + name + '"]'), name + ' is drawn');
  }
  const first = figure.querySelector('[data-vector="z1"]');
  const second = figure.querySelector('[data-vector="z2"]');
  const translated = figure.querySelector('[data-vector="translated-z2"]');
  const difference = figure.querySelector('[data-vector="difference"]');
  near(Number(first.getAttribute('x2')) - Number(first.getAttribute('x1')), 72);
  near(Number(first.getAttribute('y2')) - Number(first.getAttribute('y1')), -36);
  near(translated.getAttribute('x1'), Number(first.getAttribute('x2')));
  near(translated.getAttribute('y1'), Number(first.getAttribute('y2')));
  near(Number(translated.getAttribute('x2')) - Number(translated.getAttribute('x1')), -36);
  near(Number(translated.getAttribute('y2')) - Number(translated.getAttribute('y1')), -72);
  near(difference.getAttribute('x1'), Number(second.getAttribute('x2')));
  near(difference.getAttribute('y1'), Number(second.getAttribute('y2')));
  near(difference.getAttribute('x2'), Number(first.getAttribute('x2')));
  near(difference.getAttribute('y2'), Number(first.getAttribute('y2')));
  near(metric(figure, 'sum-length').dataset.value, Math.sqrt(10));
  near(metric(figure, 'difference-length').dataset.value, Math.sqrt(10));
  near(metric(figure, 'z1-length').dataset.value, Math.sqrt(5));
  near(metric(figure, 'z2-length').dataset.value, Math.sqrt(5));
  const before = figure.querySelector('[data-vector="z2"]').getAttribute('x2');
  set(w, figure, 'angle', 180);
  assert.notEqual(figure.querySelector('[data-vector="z2"]').getAttribute('x2'), before);
  assert.equal(figure.querySelectorAll('[data-vector="z1"], [data-vector="z2"]').length, 2);
});

test('same direction and a zero vector expose the correct triangle equality conditions', t => {
  const { figure } = fixture(t, 'vectors');
  click(figure, 'same-direction');
  assert.equal(metric(figure, 'triangle').dataset.equality, 'true');
  assert.equal(metric(figure, 'reverse-triangle').dataset.equality, 'true');
  near(metric(figure, 'sum-length').dataset.value, 2 * Math.sqrt(5));
  near(metric(figure, 'difference-length').dataset.value, 0);
  click(figure, 'zero-vector');
  near(metric(figure, 'z2-length').dataset.value, 0);
  assert.equal(metric(figure, 'triangle').dataset.equality, 'true');
  assert.equal(metric(figure, 'reverse-triangle').dataset.equality, 'true');
  assert.match(figure.querySelector('[data-vector-note]').textContent, /零向量/);
  assert.match(figure.querySelector('.tlf-control output').textContent, /无定义/);
  assert.ok(figure.querySelector('[data-point="z2"]'), 'zero vector remains marked at the origin');
  assert.ok(figure.querySelector('[data-vector="z1"]'));
});

test('distance diagram measures from a rather than the origin and updates inside, boundary and outside', t => {
  const { w, figure } = fixture(t, 'distance');
  const disk = figure.querySelector('svg [data-distance-circle]');
  assert.ok(disk);
  assert.notEqual(disk.getAttribute('cx'), point(figure, 'origin').getAttribute('cx'));
  assert.match(figure.textContent, /圆心.*a = 1 \+ i/);
  set(w, figure, 'x', 1);
  set(w, figure, 'y', 1);
  near(metric(figure, 'distance').dataset.value, 0);
  assert.equal(metric(figure, 'region').dataset.region, 'inside');
  set(w, figure, 'x', 2.5);
  near(metric(figure, 'distance').dataset.value, 1.5);
  assert.equal(metric(figure, 'region').dataset.region, 'boundary');
  set(w, figure, 'x', 3);
  near(metric(figure, 'distance').dataset.value, 2);
  assert.equal(metric(figure, 'region').dataset.region, 'outside');
  assert.ok(figure.querySelector('[data-vector="distance"]'));
});

test('multiplication uses the actual existing scene and division by 2i multiplies by -i/2', t => {
  const { figure } = fixture(t, 'multiplication');
  assert.equal(figure.querySelectorAll('.cm2-scene canvas').length, 2);
  click(figure, 'divide-2i');
  assert.match(figure.querySelector('.cm2-current').textContent, /−0\.5i/);
  assert.match(figure.querySelector('.cm2-image-summary').textContent, /0\.5 − 0\.5i/);
  assert.match(figure.querySelector('[data-multiplier-explanation]').textContent, /除以 2i.*−i\/2/);
  click(figure, 'preset-2');
  assert.match(figure.querySelector('.cm2-current').textContent, /= 2$/);
  assert.match(figure.querySelector('.cm2-image-summary').textContent, /2 \+ 2i/);
  click(figure, 'play');
  click(figure, 'pause');
  assert.equal(figure.querySelector('[data-tlf-action="play"]').disabled, false);
  click(figure, 'reset');
  assert.match(figure.querySelector('.cm2-current').textContent, /= 1$/);
});

test('disposing the figure stops its scene and disables detached figure controls', t => {
  const { figure } = fixture(t, 'multiplication');
  figure.__dispose();
  assert.ok(Array.from(figure.querySelectorAll('button, input')).every(el => el.disabled));
  assert.doesNotThrow(() => figure.__dispose());
});
