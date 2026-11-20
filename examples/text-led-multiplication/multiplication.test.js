'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');

let component = {};
try { component = require('./multiplication.js'); } catch (error) {
  if (error.code !== 'MODULE_NOT_FOUND') throw error;
}

function near(actual, expected, tolerance = 1e-10) {
  assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} differs from ${expected}`);
}

function pointNear(actual, expected) {
  assert.equal(actual.length, 2);
  actual.forEach((value, index) => near(value, expected[index]));
}

test('multiplication by i turns a vector a quarter turn counterclockwise', () => {
  assert.equal(typeof component.mapComplex, 'function');
  pointNear(component.mapComplex([3, -4], [0, 1]), [4, 3]);
  pointNear(component.mapComplex([1, 1], [0, 1]), [-1, 1]);
});

test('the four presets have their stated geometric endpoints', () => {
  assert.equal(typeof component.interpolateMultiplier, 'function');
  for (const [preset, image] of [['i', [-1, 1]], ['2', [2, 2]], ['2i', [-2, 2]], ['0', [0, 0]]]) {
    pointNear(component.mapComplex([1, 1], component.interpolateMultiplier(preset, 1)), image);
    pointNear(component.interpolateMultiplier(preset, 0), [1, 0]);
  }
});

test('halfway through 2i means scale by 1.5 and turn 45 degrees', () => {
  assert.equal(typeof component.interpolateMultiplier, 'function');
  const a = component.interpolateMultiplier('2i', 0.5);
  near(Math.hypot(...a), 1.5);
  near(Math.atan2(a[1], a[0]), Math.PI / 4);
  pointNear(component.mapComplex([1, 1], a), [0, 1.5 * Math.SQRT2]);
});

test('zero multiplication shrinks along the same ray without turning', () => {
  assert.equal(typeof component.interpolateMultiplier, 'function');
  pointNear(component.mapComplex([-2, 3], component.interpolateMultiplier('0', 0.5)), [-1, 1.5]);
  pointNear(component.interpolateMultiplier('0', 1), [0, 0]);
});

test('arbitrary vectors obey modulus multiplication and addition of angles', () => {
  assert.equal(typeof component.mapComplex, 'function');
  const z = [-2.3, 0.7];
  const a = [0.7, -1.3];
  const w = component.mapComplex(z, a);
  near(Math.hypot(...w), Math.hypot(...z) * Math.hypot(...a));
  const turn = Math.atan2(w[1], w[0]) - Math.atan2(z[1], z[0]);
  near(Math.cos(turn), a[0] / Math.hypot(...a));
  near(Math.sin(turn), a[1] / Math.hypot(...a));
});

test('identity preserves small nonzero vectors and their direction', () => {
  assert.equal(typeof component.mapComplex, 'function');
  assert.deepEqual(component.mapComplex([1e-14, -2e-14], [1, 0]), [1e-14, -2e-14]);
});

test('every intermediate state has the requested scale and turn', () => {
  assert.equal(typeof component.interpolateMultiplier, 'function');
  for (const s of [0.07, 0.25, 0.63, 0.91]) {
    const a = component.interpolateMultiplier('2i', s);
    near(Math.hypot(...a), 1 + s);
    near(Math.atan2(a[1], a[0]), s * Math.PI / 2);
  }
});

// jsdom has no rasterizer. Only that browser primitive is substituted; DOM,
// keyboard handling, state, callbacks and animation scheduling remain real.
function mount(t, reducedMotion = false, width = 1024) {
  assert.equal(typeof component.createScene, 'function');
  const dom = new JSDOM('<main></main>', { pretendToBeVisual: true });
  const win = dom.window;
  Object.defineProperty(win, 'innerWidth', { value: width, writable: true });
  win.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
    get: (_target, key) => key === 'measureText' ? text => ({ width: String(text).length * 7 }) : () => {},
    set: () => true
  });
  win.matchMedia = () => ({ matches: reducedMotion, addEventListener() {}, removeEventListener() {} });
  Object.defineProperty(win, 'devicePixelRatio', { value: 2 });
  const changes = [];
  const host = win.document.querySelector('main');
  const scene = component.createScene(host, { onChange: state => changes.push(state) });
  t.after(() => { scene.destroy(); dom.window.close(); });
  return { scene, host, win, changes };
}

test('the scene starts still at identity, with i selected as its target', t => {
  const { scene, host, changes } = mount(t);
  const state = scene.getState();
  assert.equal(state.preset, 'i');
  assert.equal(state.progress, 0);
  assert.equal(state.isPlaying, false);
  pointNear(state.z, [1, 1]);
  pointNear(state.a, [1, 0]);
  pointNear(state.w, [1, 1]);
  assert.equal(host.querySelectorAll('canvas').length, 2);
  assert.equal(changes.length, 1);
});

test('a compact textbook rail can use one plane while preserving both original and image',t=>{
 const {win,host}=mount(t);const scene=component.createScene(host,{overlay:true});t.after(()=>scene.destroy());
 const root=host.querySelectorAll('.cm2-scene')[1];
 assert.ok(root.classList.contains('cm2-overlay'));assert.equal(root.querySelector('.cm2-source').hidden,true);
 scene.setPreset('2i',{animate:false});assert.match(root.querySelector('.cm2-image').textContent,/原图|蓝色/);
});

test('seeking publishes the actual intermediate multiplier, never the final label', t => {
  const { scene, host, changes } = mount(t);
  scene.setPreset('2i', { animate: false });
  scene.setProgress(0.5);
  const state = changes.at(-1);
  near(state.rho, 1.5);
  near(state.phi, Math.PI / 4);
  pointNear(state.w, [0, 1.5 * Math.SQRT2]);
  assert.equal(state.isPlaying, false);
  assert.match(host.textContent, /1\.06/);
  assert.match(host.textContent, /45/);
});

test('zero length has no argument and reset preserves the movable source point', t => {
  const { scene } = mount(t);
  scene.setPoint([-1.2, 0.8]);
  scene.setPreset('0', { animate: false });
  assert.equal(scene.getState().imageAngle, null);
  assert.equal(scene.getState().imageLength, 0);
  scene.reset();
  pointNear(scene.getState().z, [-1.2, 0.8]);
  pointNear(scene.getState().a, [1, 0]);
  assert.equal(scene.getState().preset, '0');
});

test('the source point is keyboard operable and constrained to the visible source grid', t => {
  const { scene, host, win } = mount(t);
  const sourceCanvas = host.querySelector('canvas');
  assert.equal(sourceCanvas.tabIndex, 0);
  sourceCanvas.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  pointNear(scene.getState().z, [1.1, 1]);
  scene.setPoint([9, -9]);
  pointNear(scene.getState().z, [1.5, -1.5]);
  scene.setPreset('2i', { animate: false });
  pointNear(scene.getState().w, [3, 3]);
});

test('reduced motion displays the explicit final state when play is requested', t => {
  const { scene, changes } = mount(t, true);
  scene.play();
  assert.equal(scene.getState().progress, 1);
  assert.equal(scene.getState().isPlaying, false);
  pointNear(scene.getState().w, [-1, 1]);
  assert.equal(changes.at(-1).reducedMotion, true);
});

test('pause retains progress and a completed animation can be replayed from identity', t => {
  const { scene } = mount(t);
  scene.setProgress(0.4);
  scene.play();
  assert.equal(scene.getState().isPlaying, true);
  near(scene.getState().progress, 0.4);
  scene.pause();
  near(scene.getState().progress, 0.4);
  scene.setProgress(1);
  scene.play();
  near(scene.getState().progress, 0);
});

test('destroy removes the component and returned state cannot mutate the scene', t => {
  const { scene, host } = mount(t);
  const snapshot = scene.getState();
  snapshot.z[0] = 100;
  pointNear(scene.getState().z, [1, 1]);
  scene.destroy();
  assert.equal(host.children.length, 0);
});

test('a custom polar multiplier independently controls scale and signed turn', t => {
  const { scene } = mount(t);
  scene.setPolar(1.7, -Math.PI / 3, { animate: false });
  let state = scene.getState();
  assert.equal(state.preset, 'custom');
  near(state.targetRho, 1.7);
  near(state.targetPhi, -Math.PI / 3);
  near(state.imageLength / state.sourceLength, 1.7);
  near(state.imageAngle - state.sourceAngle, -Math.PI / 3);
  scene.setProgress(0.5);
  state = scene.getState();
  near(state.rho, 1.35);
  near(state.phi, -Math.PI / 6);
  near(Math.hypot(...state.a), 1.35);
  near(Math.atan2(state.a[1], state.a[0]), -Math.PI / 6);
  scene.setPreset('i', { animate: false });
  assert.equal(scene.getState().preset, 'i');
  pointNear(scene.getState().w, [-1, 1]);
});

test('custom polar interpolation respects both endpoint quadrants', () => {
  assert.equal(typeof component.interpolateMultiplier, 'function');
  pointNear(component.interpolateMultiplier({ rho: 2, phi: Math.PI }, 1), [-2, 0]);
  pointNear(component.interpolateMultiplier({ rho: 2, phi: -Math.PI / 2 }, 1), [0, -2]);
});

test('custom parameters outside the teaching range are rejected without changing state', t => {
  const { scene } = mount(t);
  assert.throws(() => scene.setPolar(2.1, 0), RangeError);
  assert.throws(() => scene.setPolar(-0.1, 0), RangeError);
  assert.throws(() => scene.setPolar(1, Math.PI + 0.1), RangeError);
  assert.throws(() => scene.setPolar(1, NaN), TypeError);
  assert.equal(scene.getState().preset, 'i');
});

test('mobile compares both objects in one visible, keyboard-operable canvas', t => {
  const { scene, host, win } = mount(t, false, 390);
  const sourceFigure = host.querySelector('.cm2-source');
  const comparison = host.querySelector('.cm2-image canvas');
  assert.equal(sourceFigure.hidden, true);
  assert.equal(comparison.tabIndex, 0);
  assert.equal(host.querySelector('.cm2-source canvas').tabIndex, -1);
  assert.equal(scene.getState().comparisonMode, 'overlay');
  assert.match(host.querySelector('.cm2-image figcaption').textContent, /同一坐标/);
  assert.match(comparison.getAttribute('aria-label'), /重合/);
  scene.setPreset('i', { animate: false });
  comparison.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
  pointNear(scene.getState().z, [1, 1.1]);
  pointNear(scene.getState().w, [-1.1, 1]);
  assert.doesNotMatch(comparison.getAttribute('aria-label'), /重合/);
});

test('mobile comparison accepts dragging the blue source point', t => {
  const { scene, host, win } = mount(t, false, 390);
  const comparison = host.querySelector('.cm2-image canvas');
  comparison.getBoundingClientRect = () => ({ left: 10, top: 20, width: 320, height: 320 });
  const pointer = (type, x, y) => {
    const event = new win.MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 });
    Object.defineProperty(event, 'pointerId', { value: 3 });
    comparison.dispatchEvent(event);
  };
  pointer('pointerdown', 204.1333333333, 145.8666666667);
  pointer('pointermove', 187.0666666667, 197.0666666667);
  pointer('pointerup', 187.0666666667, 197.0666666667);
  pointNear(scene.getState().z, [0.5, -0.5]);
});

test('returning to desktop restores two panels and the original source controls', t => {
  const { scene, host, win } = mount(t, false, 390);
  win.innerWidth = 1024;
  win.dispatchEvent(new win.Event('resize'));
  assert.equal(scene.getState().comparisonMode, 'side-by-side');
  assert.equal(host.querySelector('.cm2-source').hidden, false);
  assert.equal(host.querySelector('.cm2-source canvas').tabIndex, 0);
  assert.equal(host.querySelector('.cm2-image canvas').tabIndex, -1);
  assert.match(host.querySelector('.cm2-image figcaption').textContent, /像图/);
});

test('zero multiplier does not label zero as having an argument of zero',t=>{
 const {scene,host}=mount(t);scene.setPreset('0',{animate:false});
 assert.match(host.querySelector('.cm2-parameters').textContent,/方向无定义/);
 assert.doesNotMatch(host.querySelector('.cm2-parameters').textContent,/转角 φ = 0/);
});
