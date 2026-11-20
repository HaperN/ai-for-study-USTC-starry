const test = require('node:test');
const assert = require('node:assert/strict');
const {JSDOM} = require('jsdom');
const visuals = require('../../blocks/render/assets/study-visuals.js');

test('minimum uniform terms enforces strict error and corrects floating point power boundaries', () => {
  assert.equal(typeof visuals.minimumUniformTerms, 'function');
  for (const [b, epsilon, expected] of [
    [.5, .5, 2], [.99, .05, 299], [.2, .8, 1],
    [.3, .09, 3], [.3, .09000000000000001, 2],
    [.9, .81, 3], [.9, .8100000000000002, 2], [0, .05, 1],
  ]) {
    const n = visuals.minimumUniformTerms(b, epsilon);
    assert.equal(n, expected, `b=${b}, epsilon=${epsilon}`);
    assert.ok(Math.pow(b, n) < epsilon);
    if (n > 1) assert.ok(Math.pow(b, n - 1) >= epsilon);
  }
  for (const [b, epsilon] of [[1, .05], [1, .8], [NaN, .5], [.5, Infinity], [.5, 0], [-.1, .5], [1.1, .5]]) {
    assert.equal(visuals.minimumUniformTerms(b, epsilon), null);
  }
});

test('every finite legal uniform and telescoping control combination can reach strict uniform error', () => {
  for (const title of ['一致收敛', '望远镜级数']) {
    const model = visuals.createModel(visuals.chooseScene(title, ''));
    for (let hundredths = 20; hundredths < 100; hundredths++) {
      for (let twentieths = 1; twentieths <= 16; twentieths++) {
        const b = hundredths / 100, epsilon = twentieths / 20;
        // Independent exhaustive search is bounded by the current control domain.
        let expected = 1;
        while (Math.pow(b, expected) >= epsilon) expected++;
        assert.equal(visuals.minimumUniformTerms(b, epsilon), expected);
        model.set('bound', b);
        model.set('epsilon', epsilon);
        model.set('terms', expected);
        assert.equal(model.params.terms, expected, `${title}: b=${b}, epsilon=${epsilon}`);
      }
    }
    model.set('bound', .99); model.set('epsilon', .05); model.set('terms', 298);
    const before = visuals.geometry(model);
    assert.ok(before.facts.supremum >= .05);
    assert.ok(Number(before.readout.match(/bⁿ[=≈]([\d.e+-]+)/)[1]) >= .05);
    model.set('terms', 299);
    const geometry = visuals.geometry(model);
    assert.ok(geometry.facts.supremum < .05);
    assert.ok(Number(geometry.readout.match(/bⁿ[=≈]([\d.e+-]+)/)[1]) < .05, 'displayed error must not round up to epsilon while claiming strict control');
    assert.equal(geometry.facts.minimumTerms, 299);
    assert.match(geometry.readout, /最小.*N\s*=\s*299/);
    assert.match(geometry.readout, /已统一受控/);
  }
});

test('decimal uniform controls preserve equality instead of manufacturing a smaller threshold N', () => {
  for (const title of ['一致收敛', '望远镜级数']) {
    const model = visuals.createModel(visuals.chooseScene(title, ''));
    for (const value of [.3, .35, .6, .7]) {
      model.set('bound', value); model.set('epsilon', value); model.set('terms', 1);
      assert.equal(model.params.bound, value);
      assert.equal(model.params.epsilon, value);
      const geometry = visuals.geometry(model);
      assert.equal(geometry.facts.minimumTerms, 2, `b=epsilon=${value}`);
      assert.match(geometry.readout, /≥ ε/);
    }
  }
});

function setup(t) {
  const dom = new JSDOM('<main><details data-reader-group="references"><summary>原始定理</summary><p>参考原文保持不变。</p></details></main>', {pretendToBeVisual:true});
  t.after(() => dom.window.close());
  const doc = dom.window.document, root = doc.querySelector('main');
  for (const [kind, model_title] of [['uniform', '一致收敛'], ['telescoping', '望远镜级数'], ['multiply', '复数乘法']]) {
    root.append(visuals.createTeachingCard(doc, {id:kind, kind, model_title, params:kind === 'multiply' ? {} : {bound:.99, epsilon:.05, terms:4}}));
  }
  return {dom, doc, root};
}

test('uniform controls reach the required N, keep range and number synchronized, and preserve reset and references', t => {
  const {dom, root} = setup(t), reference = root.querySelector('details').outerHTML;
  const controller = visuals.createController(root, {observe(){return {observe(){},disconnect(){}};}});
  t.after(() => controller.destroy());
  for (const card of root.querySelectorAll('[data-study-scene="uniform"], [data-study-scene="telescoping"]')) {
    const numbers = card.querySelector('input[type="number"][data-study-param="terms"]');
    const range = card.querySelector('input[type="range"][data-study-param="terms"]');
    numbers.value = '299';
    numbers.dispatchEvent(new dom.window.Event('input', {bubbles:true}));
    assert.equal(card.__studyModel.params.terms, 299);
    assert.equal(range.value, '299');
    assert.match(card.querySelector('.study-readout').textContent, /最小.*N\s*=\s*299/);
    assert.match(card.querySelector('.study-readout').textContent, /已统一受控/);
    card.querySelector('[data-study-action="reset"]').click();
    assert.equal(card.__studyModel.params.terms, 4);
    assert.equal(numbers.value, '4');
    assert.equal(range.value, '4');
    assert.equal(card.__studyModel.params.bound, .99);
  }
  assert.equal(root.querySelector('details').outerHTML, reference);
});

test('only uniform models offer a jump to N and full open disk explains why it is disabled', t => {
  const {dom, root} = setup(t);
  const controller = visuals.createController(root, {observe(){return {observe(){},disconnect(){}};}});
  t.after(() => controller.destroy());
  assert.equal(root.querySelector('[data-study-scene="multiply"] [data-study-action="uniform-terms"]'), null);
  for (const card of root.querySelectorAll('[data-study-scene="uniform"], [data-study-scene="telescoping"]')) {
    const jump = card.querySelector('[data-study-action="uniform-terms"]');
    assert.ok(jump);
    assert.equal(jump.disabled, false);
    jump.click();
    assert.equal(card.__studyModel.params.terms, 299);
    assert.equal(card.querySelector('input[type="range"][data-study-param="terms"]').value, '299');
    assert.match(card.querySelector('.study-readout').textContent, /已统一受控/);
  }
  const card = root.querySelector('[data-study-scene="uniform"]');
  const bound = card.querySelector('input[type="number"][data-study-param="bound"]');
  bound.value = '1'; bound.dispatchEvent(new dom.window.Event('input', {bubbles:true}));
  const jump = card.querySelector('[data-study-action="uniform-terms"]');
  assert.equal(card.__studyModel.params.bound, .99,'radius input stays in the fixed closed-disk mode');
  assert.equal(jump.disabled, false);
  card.querySelector('[data-study-action="uniform-mode-open"]').click();
  assert.equal(card.__studyModel.params.bound, 1);
  assert.equal(bound.disabled, true);
  assert.equal(jump.disabled, true);
  assert.match(card.querySelector('.study-readout').textContent, /不存在.*统一.*N/);
  assert.equal(visuals.geometry(card.__studyModel).facts.minimumTerms, null);
  jump.click();
  assert.equal(card.__studyModel.params.terms, 299);
  card.querySelector('[data-study-action="uniform-mode-closed"]').click();
  assert.equal(card.__studyModel.params.bound, .99,'returning to closed mode restores its radius');
  assert.equal(bound.disabled, false);
  assert.equal(jump.disabled, false);
  assert.equal(visuals.geometry(card.__studyModel).facts.minimumTerms, 299);
  card.querySelector('[data-study-action="reset"]').click();
  assert.equal(jump.disabled, false);
  assert.equal(card.__studyModel.params.terms, 4);
});

test('uniform animation reaches the enlarged range and jump and reset stop playback cleanly', t => {
  const {dom, root} = setup(t), frames = new Map();
  let sequence = 0, intersection;
  dom.window.requestAnimationFrame = callback => {frames.set(++sequence, callback); return sequence;};
  dom.window.cancelAnimationFrame = id => frames.delete(id);
  const controller = visuals.createController(root, {
    observe(callback){intersection = callback; return {observe(){},disconnect(){}};},
    createRenderer(){return {update(){},view(){},dispose(){}};},
  });
  t.after(() => controller.destroy());
  const card = root.querySelector('[data-study-scene="uniform"]'), model = card.__studyModel;
  const play = card.querySelector('[data-study-action="play"]');
  intersection([{target:card,isIntersecting:true}]);
  function frame(time) {
    const [id, callback] = frames.entries().next().value;
    frames.delete(id); callback(time);
  }
  play.click(); frame(0); frame(10000);
  assert.ok(model.params.terms > 40 && model.params.terms <= 300);
  assert.equal(play.getAttribute('aria-pressed'), 'true');
  assert.equal(card.querySelector('input[type="number"][data-study-param="terms"]').value, String(model.params.terms));
  card.querySelector('[data-study-action="uniform-terms"]').click();
  assert.equal(model.params.terms, 299);
  assert.equal(play.getAttribute('aria-pressed'), 'false');
  assert.equal(frames.size, 0);
  play.click(); frame(11000);
  card.querySelector('[data-study-action="reset"]').click();
  assert.equal(model.params.terms, 4);
  assert.equal(play.getAttribute('aria-pressed'), 'false');
  assert.equal(frames.size, 0);
});
