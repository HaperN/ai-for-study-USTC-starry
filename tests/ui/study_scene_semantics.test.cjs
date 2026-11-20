const test = require('node:test');
const assert = require('node:assert/strict');
const visuals = require('../../blocks/render/assets/study-visuals.js');
function model(title) { return visuals.createModel(visuals.chooseScene(title, '')); }
function near(actual, expected, tolerance = 1e-10) { assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} differs from ${expected}`); }

test('exponential marker uses the controlled imaginary coordinate and maps that same point', () => {
  const m = model('指数函数'); m.set('phase', 1.2);
  const g = visuals.geometry(m), pair = g.facts?.periodPair;
  assert.ok(pair, 'the scene must display both actual inputs, not merely mention a period');
  near(pair.inputs[0][1], 1.2);
  near(pair.inputs[1][1] - pair.inputs[0][1], 2 * Math.PI);
  near(pair.images[0][0], Math.exp(.8) * Math.cos(1.2));
  near(pair.images[0][1], Math.exp(.8) * Math.sin(1.2));
  near(pair.images[1][0], pair.images[0][0]);
  near(pair.images[1][1], pair.images[0][1]);
});

test('exponential period pair and its full grid remain visible at both phase limits in a narrow viewport', () => {
  const m = model('指数函数');
  for (const extent of [.3, 1, 1.4]) for (const phase of [-6.28, -3.14, 0, 1.2, 3.14, 6.28]) {
    m.set('extent', extent); m.set('phase', phase);
    const g = visuals.geometry(m), pair = g.facts?.periodPair;
    assert.ok(pair?.displayInputs?.length === 2, 'two separated input markers must be present');
    near(pair.inputs[0][0], .8 * extent);
    near(pair.inputs[0][1], phase);
    near(pair.inputs[1][1], phase + 2 * Math.PI);
    const first = g.points.find(point => point.role === 'source-z').position;
    const second = g.points.find(point => point.role === 'source-period-shift').position;
    const common = g.points.find(point => point.role === 'common-image').position;
    near((first[0] + 1.15) / (.7 / extent), .8 * extent);
    near(first[1] / (.75 / (2 * Math.PI)), phase);
    near(second[1] / (.75 / (2 * Math.PI)), phase + 2 * Math.PI);
    near((common[0] - 1.15) / (.75 / Math.exp(extent)), Math.exp(.8 * extent) * Math.cos(phase));
    near(common[1] / (.75 / Math.exp(extent)), Math.exp(.8 * extent) * Math.sin(phase));
    assert.ok(Math.hypot(pair.displayInputs[1][0] - pair.displayInputs[0][0], pair.displayInputs[1][1] - pair.displayInputs[0][1]) > .6);
    // At 200 x 280 the unchanged orthographic renderer sees x in +/-2.214.
    // All offered extrema must fit without requiring a pan/zoom gesture.
    const coordinates = [...g.points.map(p => p.position), ...g.lines.flatMap(line => line.points)];
    for (const point of coordinates) {
      assert.ok(point.every(Number.isFinite));
      assert.ok(Math.abs(point[0]) < 2.1 && Math.abs(point[1]) < 2.5, `offscreen: ${point}`);
    }
    assert.ok(g.points.some(point => point.role === 'source-period-shift'));
    assert.ok(g.lines.some(line => line.role === 'common-image-ring'));
    near(g.facts.displayScaling.inputY, .75 / (2 * Math.PI));
    near(g.facts.displayScaling.image, .75 / Math.exp(extent));
    assert.ok(g.labels.some(label => /2\\pi/.test(label[0])), 'vertical normalization must be disclosed visibly');
    assert.ok(g.labels.some(label => /e\^a/.test(label[0])), 'image normalization must be disclosed visibly');
  }
});

test('exponential period partner is mapped from its raw input, never from an already exponentiated image', () => {
  const m = model('指数函数'); m.set('phase', -6.28);
  const calls = [], originalMap = m.map;
  m.map = (x, y) => { calls.push([x, y]); return originalMap(x, y); };
  const g = visuals.geometry(m), pair = g.facts?.periodPair;
  assert.ok(pair);
  for (const input of [[.8, -6.28], [.8, -6.28 + 2 * Math.PI]]) {
    assert.ok(calls.some(call => Math.abs(call[0] - input[0]) < 1e-12 && Math.abs(call[1] - input[1]) < 1e-12));
  }
  near(pair.images[1][0], Math.exp(.8) * Math.cos(-6.28 + 2 * Math.PI));
  near(pair.images[1][1], Math.exp(.8) * Math.sin(-6.28 + 2 * Math.PI));
});

test('essential-singularity marker approaches zero with the displayed sampling range', () => {
  const m = model('本性奇点'); m.set('phase', 0); m.set('extent', .8);
  let g = visuals.geometry(m); near(g.points[0].position[0] + 2.65, .64);
  const oldValue = g.points[1].position[0] - 2.65;
  m.set('extent', .2); g = visuals.geometry(m);
  near(g.points[0].position[0] + 2.65, .16);
  near(g.points[1].position[0] - 2.65, Math.exp(6.25), 1e-8);
  assert.ok(g.points[1].position[0] - 2.65 > oldValue * 50);
});

test('Cauchy quadrature resolves the near-boundary case and exposes a credible error estimate', () => {
  const m = model('柯西公式积分读出'); m.set('radius', .95); m.set('real', .3); m.set('imag', .9);
  const f = visuals.geometry(m).facts;
  // (0.3+0.9i)^2 = -0.72+0.54i; the old 128-point rule errs by more than 2.
  const error = Math.hypot(f.numerical[0] + .72, f.numerical[1] - .54);
  assert.ok(error < 1e-8, `quadrature error ${error}`);
  assert.equal(f.converged, true);
  assert.ok(f.samples > 128);
  assert.ok(Number.isFinite(f.estimatedError) && f.estimatedError < 1e-8);
  assert.ok(error <= f.estimatedError + 1e-12, `${error} exceeds estimate ${f.estimatedError}`);
  m.set('radius', .5); m.set('real', .3); m.set('imag', .4);
  const singular = visuals.geometry(m).facts;
  assert.equal(singular.onContour, true);
  assert.equal(singular.numerical, null, 'ordinary integral on a pole must not receive a numerical value');
});

test('Cauchy quadrature also resolves an exterior point close to the circle', () => {
  const m = model('柯西公式积分读出'); m.set('radius', .9); m.set('real', .05); m.set('imag', .9);
  const f = visuals.geometry(m).facts;
  assert.equal(f.inside, false);
  assert.ok(Math.hypot(...f.numerical) < 1e-8);
  assert.equal(f.converged, true);
});

test('harmonic display includes both conjugate level families on a common plane and scales them consistently', () => {
  const m = model('调和函数'); m.set('amplitude', .5); m.set('level', .3); m.set('conjugateLevel', .2);
  assert.deepEqual(m.map(1, 1), [0, 1]);
  const g = visuals.geometry(m), f = g.facts;
  assert.ok(f?.levelCurves.u.length >= 2 && f.levelCurves.v.length >= 2);
  for (const [family, curves] of Object.entries(f.levelCurves)) {
    const level = family === 'u' ? .3 : .2;
    for (const points of curves) for (const [x, y] of points) near(family === 'u' ? .5 * (x*x-y*y) : x*y, level, 1e-9);
    assert.ok(g.lines.some(line => line.family === family && line.projection && line.points.length > 10));
  }
  const uLine = g.lines.find(line => line.family === 'u' && line.projection), vLine = g.lines.find(line => line.family === 'v' && line.projection);
  assert.notEqual(uLine.color, vLine.color);
  for (const line of [uLine, vLine]) for (const p of line.points) near(p[2], f.projectionHeight);
  assert.equal(f.orthogonality.nonCritical, true);
  near(f.orthogonality.gradientDot, 0);
  const [x, y] = f.orthogonality.point; near(.5 * (x*x-y*y), .3); near(x*y, .2);
  m.set('level', 0); m.set('conjugateLevel', 0);
  assert.equal(visuals.geometry(m).facts.orthogonality.nonCritical, false, 'zero gradients cannot define orthogonal directions');
});
