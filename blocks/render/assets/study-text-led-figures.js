(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.DSHTextLedFigures = factory();
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var BLUE = '#1769b0', ORANGE = '#b85517', GREEN = '#237450', PURPLE = '#7851a1';
  var EPS = 1e-9, uid = 0;
  var O = [220, 200], SCALE = 36;
  var TITLES = {
    conjugate: '共轭：关于实轴的镜像', polar: '模与辐角：同一个点的两种描述',
    vectors: '向量加减与长度不等式', distance: '以 a 为圆心的距离条件',
    multiplication: '复数乘除：长度与方向一起改变'
  };

  function clean(n) { return Math.abs(n) < EPS ? 0 : n; }
  function fmt(n) { return String(Number(clean(n).toFixed(3))).replace('-', '−'); }
  function complex(z) {
    var x = clean(z[0]), y = clean(z[1]);
    if (!y) return fmt(x);
    var im = (Math.abs(y) === 1 ? '' : fmt(Math.abs(y))) + 'i';
    if (!x) return (y < 0 ? '−' : '') + im;
    return fmt(x) + (y < 0 ? ' − ' : ' + ') + im;
  }
  function pixel(p) { return [O[0] + p[0] * SCALE, O[1] - p[1] * SCALE]; }
  function node(doc, tag, cls, text) {
    var el = doc.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function svgNode(doc, tag, attrs, text) {
    var el = doc.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (key) { el.setAttribute(key, attrs[key]); });
    if (text !== undefined) el.textContent = text;
    return el;
  }

  function createFigure(doc, plan) {
    if (!doc || !doc.createElement) throw new TypeError('A document is required');
    plan = plan || {};
    if (!TITLES[plan.kind]) throw new RangeError('Unknown text-led figure: ' + plan.kind);
    var id = 'tlf-' + (++uid), disposed = false, scene = null, listeners = [];
    var figure = node(doc, 'figure', 'study-visual study-text-led-figure');
    figure.dataset.studyScene = plan.kind;
    figure.dataset.studyCoverage = 'authored';
    figure.dataset.textLedFigure = 'true';
    var title = node(doc, 'h4', 'study-visual-title study-text-led-title', plan.title || TITLES[plan.kind]);
    title.id = id + '-title';
    figure.setAttribute('aria-labelledby', title.id);
    figure.appendChild(title);
    var body = node(doc, 'div', 'tlf-body');
    var chart = node(doc, 'div', 'tlf-chart');
    var measures = node(doc, 'dl', 'tlf-measures');
    var controls = node(doc, 'div', 'tlf-controls');
    controls.setAttribute('aria-label', '改变图中参数');
    var note = node(doc, 'p', 'tlf-note');
    body.appendChild(chart);
    body.appendChild(measures);
    figure.appendChild(body);
    figure.appendChild(controls);
    figure.appendChild(note);
    var caption = node(doc, 'figcaption', 'study-text-led-caption', plan.caption || TITLES[plan.kind]);
    figure.appendChild(caption);

    function listen(el, name, fn) {
      el.addEventListener(name, fn);
      listeners.push(function () { el.removeEventListener(name, fn); });
    }
    function input(key, label, value, update, opts) {
      opts = opts || {};
      var wrap = node(doc, 'label', 'tlf-control');
      wrap.appendChild(node(doc, 'span', '', label));
      var el = node(doc, 'input');
      el.type = opts.type || 'number';
      el.min = opts.min === undefined ? '-3' : opts.min;
      el.max = opts.max === undefined ? '3' : opts.max;
      el.step = opts.step || '0.1';
      el.value = value;
      el.dataset.tlfParam = key;
      wrap.appendChild(el);
      var valueLabel = null;
      if (el.type === 'range') {
        valueLabel = node(doc, 'output', '', fmt(value) + '°');
        wrap.appendChild(valueLabel);
      }
      controls.appendChild(wrap);
      listen(el, 'input', function () {
        if (disposed || el.value.trim() === '') return;
        var n = Number(el.value);
        if (!Number.isFinite(n)) return;
        n = Math.max(Number(el.min), Math.min(Number(el.max), n));
        if (valueLabel) valueLabel.textContent = fmt(n) + '°';
        update(n);
      });
      return el;
    }
    function button(action, text, update) {
      var el = node(doc, 'button', 'tlf-button', text);
      el.type = 'button';
      el.dataset.tlfAction = action;
      listen(el, 'click', function () { if (!disposed) update(); });
      controls.appendChild(el);
      return el;
    }
    function clearDiagram() { chart.replaceChildren(); measures.replaceChildren(); }
    function measure(key, label, value, text, attrs) {
      var group = node(doc, 'div', 'tlf-measure');
      group.appendChild(node(doc, 'dt', '', label));
      var dd = node(doc, 'dd', '', text === undefined ? fmt(value) : text);
      dd.dataset.metric = key;
      dd.dataset.value = value === null ? 'undefined' : String(value);
      Object.keys(attrs || {}).forEach(function (key) { dd.setAttribute(key, attrs[key]); });
      group.appendChild(dd);
      if(key==='polar')group.dataset.detailOnly='true';
      if(doc.defaultView.DSHAnimations){Array.from(group.children).forEach(function(el){doc.defaultView.DSHAnimations.captionText(el,el.textContent);});}
      measures.appendChild(group);
      return dd;
    }
    function diagram(label) {
      clearDiagram();
      var svg = svgNode(doc, 'svg', { viewBox: '0 0 440 400', role: 'img', 'aria-label': label, class: 'tlf-svg' });
      var desc = svgNode(doc, 'desc', {}, label + '。坐标单位间距相同，x 轴为实轴，y 轴为虚轴。');
      svg.appendChild(desc);
      var defs = svgNode(doc, 'defs');
      [['blue', BLUE], ['orange', ORANGE], ['green', GREEN], ['purple', PURPLE]].forEach(function (pair) {
        var marker = svgNode(doc, 'marker', { id: id + '-' + pair[0], markerWidth: 7, markerHeight: 7,
          refX: 6, refY: 3.5, orient: 'auto', markerUnits: 'userSpaceOnUse' });
        marker.appendChild(svgNode(doc, 'path', { d: 'M 0 0 L 7 3.5 L 0 7 Z', fill: pair[1] }));
        defs.appendChild(marker);
      });
      svg.appendChild(defs);
      chart.appendChild(svg);
      function line(from, to, attrs) {
        var a = pixel(from), b = pixel(to);
        var el = svgNode(doc, 'line', Object.assign({ x1: a[0], y1: a[1], x2: b[0], y2: b[1],
          stroke: '#81909c', 'stroke-width': 1.4 }, attrs));
        svg.appendChild(el);
        return el;
      }
      function text(at, content, attrs, offset) {
        var p = pixel(at), dx = offset ? offset[0] : 0, dy = offset ? offset[1] : 0;
        var el = svgNode(doc, 'text', Object.assign({ x: Math.max(18, Math.min(422, p[0] + dx)),
          y: Math.max(24, Math.min(380, p[1] + dy)), fill: '#4a5967' }, attrs), content);
        svg.appendChild(el);
        return el;
      }
      function dot(p, name, color, radius) {
        var xy = pixel(p);
        var el = svgNode(doc, 'circle', { cx: xy[0], cy: xy[1], r: radius || 5, fill: color,
          stroke: '#fff', 'stroke-width': 1.5, 'data-point': name });
        svg.appendChild(el);
        return el;
      }
      function vector(a, b, name, color, hue, attrs) {
        var result = Object.assign({ stroke: color, 'stroke-width': 3, 'data-vector': name }, attrs);
        if (Math.hypot(b[0] - a[0], b[1] - a[1]) > EPS) result['marker-end'] = 'url(#' + id + '-' + hue + ')';
        return line(a, b, result);
      }
      function projections(p, color) {
        line(p, [p[0], 0], { stroke: color, 'stroke-dasharray': '4 5', opacity: 0.65 });
        line(p, [0, p[1]], { stroke: color, 'stroke-dasharray': '4 5', opacity: 0.65 });
      }
      for (var n = -4; n <= 4; n++) {
        if (!n) continue;
        line([n, -4.1], [n, 4.1], { stroke: '#e9eef2', 'stroke-width': 1 });
        line([-4.6, n], [4.6, n], { stroke: '#e9eef2', 'stroke-width': 1 });
      }
      line([-4.7, 0], [4.7, 0], { stroke: '#7a8996' });
      line([0, -4.2], [0, 4.2], { stroke: '#7a8996' });
      text([4.7, 0], 'x', { 'font-style': 'italic' }, [8, -11]);
      text([0, 4.2], 'y', { 'font-style': 'italic' }, [10, 0]);
      text([0, 0], '0', { 'data-origin-label': '', 'text-anchor': 'end' }, [-9, 23]);
      text([1, 0], '1', { 'text-anchor': 'middle', class: 'tlf-unit-label' }, [0, 23]);
      dot([0, 0], 'origin', '#7a8996', 2.5);
      return { svg: svg, line: line, text: text, dot: dot, vector: vector, projections: projections };
    }

    function conjugate() {
      var p = [2, 1];
      input('x', '实部 x', p[0], function (n) { p[0] = n; draw(); });
      input('y', '虚部 y', p[1], function (n) { p[1] = n; draw(); });
      note.textContent = '蓝点与橙点关于实轴对称；虚部变号，实部不变，到原点的距离相等。';
      function draw() {
        var bar = [p[0], -p[1]], r = Math.hypot(p[0], p[1]);
        var g = diagram('蓝点 z 与橙点共轭 z̄ 关于实轴对称');
        g.projections(p, BLUE);
        g.projections(bar, ORANGE);
        g.vector([0, 0], p, 'z', BLUE, 'blue', { 'stroke-width': 5 });
        g.vector([0, 0], bar, 'conjugate', ORANGE, 'orange');
        g.dot(p, 'z', BLUE, 7);
        g.dot(bar, 'conjugate', ORANGE, 4.5);
        if (Math.abs(p[1]) < EPS) {
          g.text(p, 'z 与共轭 z̄ 重合' + (r < EPS ? ' = 0' : ''),
            { 'data-point-label': 'coincident', 'text-anchor': p[0] > 0 ? 'end' : 'start' }, [p[0] > 0 ? 14 : -14, -22]);
        } else {
          g.text(p, 'z', { fill: BLUE, 'data-point-label': 'z' }, [12, p[1] > 0 ? -12 : 26]);
          g.text(bar, 'z̄', { fill: ORANGE, 'data-point-label': 'conjugate' }, [12, p[1] > 0 ? 26 : -12]);
          g.text([0, p[1]], 'y = ' + fmt(p[1]), { fill: BLUE, 'data-projection': 'y', 'text-anchor': 'end' }, [-12, -9]);
          g.text([0, -p[1]], '−y = ' + fmt(-p[1]), { fill: ORANGE, 'data-projection': '-y', 'text-anchor': 'end' }, [-12, p[1] > 0 ? 25 : -9]);
        }
        if (Math.abs(p[0]) > EPS) g.text([p[0], 0], 'x = ' + fmt(p[0]), { 'data-projection': 'x', 'text-anchor': 'middle' }, [0, 28]);
        measure('point', '蓝点', null, 'z = ' + complex(p));
        measure('conjugate', '橙点', null, '共轭 z̄ = ' + complex(bar));
        measure('modulus', '$|z|$', r);
        measure('conjugate-modulus', '$|\\bar z|$', r, fmt(r) + '（与 $|z|$ 相等）');
      }
      draw();
    }

    function polar() {
      var p = [3, 2];
      input('x', '实部 x', p[0], function (n) { p[0] = n; draw(); });
      input('y', '虚部 y', p[1], function (n) { p[1] = n; draw(); });
      note.textContent = '从正实轴转到蓝色向量，逆时针为正。这里取主值 arg z ∈ (−π, π]；负实轴取 π，零点没有辐角。';
      function draw() {
        var r = Math.hypot(p[0], p[1]), angle = r < EPS ? null : (clean(p[1]) === 0 && p[0] < 0 ? Math.PI : Math.atan2(clean(p[1]), clean(p[0])));
        var g = diagram('点 z 的模 r、实虚部投影与辐角主值');
        g.projections(p, BLUE);
        g.vector([0, 0], p, 'radius', BLUE, 'blue');
        if (angle !== null) {
          var arc = 42, end = [O[0] + arc * Math.cos(angle), O[1] - arc * Math.sin(angle)];
          if (Math.abs(angle) > EPS) g.svg.appendChild(svgNode(doc, 'path', { d: 'M ' + (O[0] + arc) + ' ' + O[1] + ' A ' + arc + ' ' + arc + ' 0 0 ' + (angle > 0 ? 0 : 1) + ' ' + end[0] + ' ' + end[1],
            fill: 'none', stroke: PURPLE, 'stroke-width': 2.5, 'data-angle-arc': '' }));
          g.text([1.65 * Math.cos(angle / 2), 1.65 * Math.sin(angle / 2)], 'φ', { fill: PURPLE, 'text-anchor': 'middle' }, [0, angle < 0 ? 14 : 0]);
          g.text([p[0] * 0.65, p[1] * 0.65], 'r', { fill: BLUE }, [p[1] < 0 ? 8 : -18, -10]);
          g.text(p, 'z', { fill: BLUE }, [12, -12]);
        } else g.text([0, 0], 'z = 0', { fill: BLUE }, [14, -18]);
        g.dot(p, 'z', BLUE, 6);
        if (Math.abs(p[0]) > EPS) g.text([p[0], 0], 'x = ' + fmt(p[0]), { 'data-projection': 'x', 'text-anchor': 'middle' }, [0, 29]);
        if (Math.abs(p[1]) > EPS) g.text([0, p[1]], 'y = ' + fmt(p[1]), { 'data-projection': 'y', 'text-anchor': 'end' }, [-12, -8]);
        measure('point', '直角坐标', null, 'z = ' + complex(p));
        measure('radius', '模 $r$', r);
        measure('argument', '主辐角 $\\arg z$', angle,
          angle === null ? 'z = 0，辐角无定义' : (Math.abs(angle - Math.PI) < EPS ? 'π rad（180°）' : fmt(angle) + ' rad（' + fmt(angle * 180 / Math.PI) + '°）'));
        measure('polar', '极坐标关系', null, r < EPS ? 'r = 0；不指定 φ' : 'x = r cos φ；y = r sin φ');
      }
      draw();
    }

    function vectors() {
      var a = [2, 1], b = [-1, 2], length = Math.sqrt(5), angle = Math.atan2(2, -1) * 180 / Math.PI;
      var slider = input('angle', 'z₂ 的方向', angle, function (n) {
        b = [clean(length * Math.cos(n * Math.PI / 180)), clean(length * Math.sin(n * Math.PI / 180))];
        draw();
      }, { type: 'range', min: -180, max: 180, step: 0.1 });
      function setB(value) {
        b = value.slice();
        if (Math.hypot(b[0], b[1]) > EPS) {
          slider.value = Math.atan2(b[1], b[0]) * 180 / Math.PI;
          slider.parentElement.querySelector('output').textContent = fmt(Number(slider.value)) + '°';
        } else slider.parentElement.querySelector('output').textContent = '无定义';
        draw();
      }
      button('original-vectors', '恢复原例', function () { setB([-1, 2]); });
      button('same-direction', '同向', function () { setB([2, 1]); });
      button('zero-vector', '一个为零', function () { setB([0, 0]); });
      note.dataset.vectorNote = '';
      function draw() {
        var sum = [a[0] + b[0], a[1] + b[1]], la = Math.hypot(a[0], a[1]), lb = Math.hypot(b[0], b[1]);
        var ls = Math.hypot(sum[0], sum[1]), ld = Math.hypot(a[0] - b[0], a[1] - b[1]);
        var triangle = Math.abs(ls - la - lb) < EPS, reverse = Math.abs(ld - Math.abs(la - lb)) < EPS;
        var g = diagram('蓝色 z₁、橙色 z₂、绿色和向量及紫色两端之间的差向量');
        g.vector([0, 0], sum, 'sum', GREEN, 'green');
        g.vector(a, sum, 'translated-z2', ORANGE, 'orange', { 'stroke-dasharray': '7 5', opacity: 0.8 });
        g.vector(b, a, 'difference', PURPLE, 'purple', { 'stroke-width': 2.4 });
        g.vector([0, 0], a, 'z1', BLUE, 'blue', { 'stroke-width': 6 });
        g.vector([0, 0], b, 'z2', ORANGE, 'orange', { 'stroke-width': 3 });
        g.dot(a, 'z1', BLUE, 7);
        g.dot(b, 'z2', ORANGE, 4.5);
        g.dot(sum, 'sum', GREEN, 4.5);
        g.text(a, 'z₁', { fill: BLUE }, [11, -14]);
        g.text(b, lb < EPS ? 'z₂ = 0' : 'z₂', { fill: ORANGE }, [lb < EPS ? 12 : -30, lb < EPS ? 28 : -15]);
        g.text(sum, 'z₁ + z₂', { fill: GREEN, 'text-anchor': sum[0] > 2 ? 'end' : 'start' }, [12, -15]);
        if (ld > EPS) g.text([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], 'z₁ − z₂', { fill: PURPLE, 'text-anchor': 'middle' }, [0, -15]);
        measure('z1-length', '蓝色原向量 $z_1 = 2 + i$', la, '|z₁| = ' + fmt(la));
        measure('z2-length', '橙色原向量 z₂ = ' + complex(b), lb, '|z₂| = ' + fmt(lb));
        measure('sum-length', '绿色和向量 $|z_1+z_2|$', ls);
        measure('difference-length', '紫色差向量 $|z_1-z_2|$', ld);
        measure('triangle', '$|z_1+z_2|\\le|z_1|+|z_2|$', ls, fmt(ls) + (triangle ? ' = ' : ' < ') + fmt(la + lb), { 'data-equality': String(triangle) });
        measure('reverse-triangle', '$\\big||z_1|-|z_2|\\big|\\le|z_1-z_2|$', ld, fmt(Math.abs(la - lb)) + (reverse ? ' = ' : ' < ') + fmt(ld), { 'data-equality': String(reverse) });
        note.textContent = '虚线是 z₂ 平移到 z₁ 末端的副本；两条从原点出发的原向量始终保留。紫色箭头从 z₂ 末端指向 z₁ 末端。' +
          (lb < EPS ? '此时 z₂ 是零向量，两式均取等号；零向量不需要指定方向。' : triangle ? '此时两原向量同向，两式均取等号。' : '改变橙色向量的方向，再与“同向”和“一个为零”比较等号条件。');
      }
      draw();
    }

    function distance() {
      var center = [1, 1], rho = 1.5, p = [2, 2];
      input('x', '点 z 的实部 x', p[0], function (n) { p[0] = n; draw(); });
      input('y', '点 z 的虚部 y', p[1], function (n) { p[1] = n; draw(); });
      note.textContent = '圆心是 a = 1 + i。连线从 a 到 z，表示 |z − a|；这里度量的是到 a 的距离，而不是到原点的距离。';
      function draw() {
        var d = Math.hypot(p[0] - center[0], p[1] - center[1]), region = Math.abs(d - rho) < EPS ? 'boundary' : d < rho ? 'inside' : 'outside';
        var color = region === 'inside' ? GREEN : region === 'boundary' ? PURPLE : ORANGE;
        var g = diagram('以 a = 1 + i 为圆心、ρ = 1.5 为半径的圆与点 z');
        var c = pixel(center);
        var disk = svgNode(doc, 'circle', { cx: c[0], cy: c[1], r: rho * SCALE, fill: '#e7f3ec', 'fill-opacity': 0.65,
          stroke: GREEN, 'stroke-width': 2.2, 'data-distance-circle': '' });
        // Keep the coordinate axes visible through the lightly filled disk.
        g.svg.appendChild(disk);
        g.vector(center, p, 'distance', color, region === 'inside' ? 'green' : region === 'boundary' ? 'purple' : 'orange');
        g.dot(center, 'a', PURPLE, 5);
        g.dot(p, 'z', color, 6);
        g.text(center, 'a', { fill: PURPLE }, [-24, 24]);
        g.text(p, d < EPS ? 'z = a' : 'z', { fill: color }, [12, -13]);
        g.text([center[0] - rho, center[1]], 'ρ', { fill: GREEN }, [-20, -12]);
        measure('center', '圆心与半径', rho, '圆心 a = 1 + i；ρ = 1.5');
        measure('point', '可移动点', null, 'z = ' + complex(p));
        measure('distance', '$|z-a|$', d);
        measure('region', '距离条件的含义', d, region === 'inside' ? '|z − a| < ρ：点在圆内' : region === 'boundary' ? '|z − a| = ρ：点在圆周上' : '|z − a| > ρ：点在圆外', { 'data-region': region });
      }
      draw();
    }

    function multiplication() {
      body.classList.add('tlf-multiplication-body');
      measures.remove();
      var engine = doc.defaultView && doc.defaultView.ComplexMultiplication;
      if (!engine || typeof engine.createScene !== 'function') {
        chart.appendChild(node(doc, 'p', 'tlf-unavailable', '乘法图像暂未加载。乘以 i 使辐角增加 π/2，乘以 2 使长度加倍；除以 2i 等于乘 −i/2。'));
        return;
      }
      note.dataset.multiplierExplanation = '';
      var division = false, actions = {}, presetButtons = [];
      scene = engine.createScene(chart, { overlay:true, onChange: function (state) {
        note.textContent = (division ? '除以 2i 等于乘 −i/2：目标长度缩为 1/2，顺时针转 π/2。' : '目标乘数决定总缩放和总转角；播放时两图展示同一个连续变换。') +
          '当前乘数 a = ' + complex(state.a) + '，当前长度乘 ' + fmt(state.rho) + '，当前转角 ' + fmt(state.phi * 180 / Math.PI) + '°。';
        if (actions.play) actions.play.disabled = state.isPlaying;
        if (actions.pause) actions.pause.disabled = !state.isPlaying;
      } });
      function choose(name) {
        division = name === 'divide-2i';
        presetButtons.forEach(function (el) { el.setAttribute('aria-pressed', String(el.dataset.tlfAction === name)); });
        if (division) scene.setPolar(0.5, -Math.PI / 2, { animate: false });
        else scene.setPreset(name.slice(7), { animate: false });
      }
      ['i', '2', '2i'].forEach(function (preset) {
        presetButtons.push(button('preset-' + preset, '乘以 ' + preset, function () { choose('preset-' + preset); }));
      });
      presetButtons.push(button('divide-2i', '除以 2i', function () { choose('divide-2i'); }));
      actions.play = button('play', '播放', function () { scene.play(); });
      actions.pause = button('pause', '暂停', function () { scene.pause(); });
      actions.reset = button('reset', '重置', function () { scene.reset(); });
      choose('preset-i');
    }

    if (plan.kind === 'conjugate') conjugate();
    if (plan.kind === 'polar') polar();
    if (plan.kind === 'vectors') vectors();
    if (plan.kind === 'distance') distance();
    if (plan.kind === 'multiplication') multiplication();
    var expand=button('expand','放大图像',function(){var open=figure.classList.toggle('study-expanded');expand.textContent=open?'收起图像':'放大图像';expand.setAttribute('aria-expanded',String(open));});
    expand.setAttribute('aria-expanded','false');
    listen(figure,'keydown',function(event){if(event.key==='Escape'&&figure.classList.contains('study-expanded')){figure.classList.remove('study-expanded');expand.textContent='放大图像';expand.setAttribute('aria-expanded','false');expand.focus();}});
    figure.__pause = function () { if(scene)scene.pause(); };
    figure.__dispose = function () {
      if (disposed) return;
      disposed = true;
      listeners.forEach(function (remove) { remove(); });
      if (scene) scene.destroy();
      figure.querySelectorAll('button,input').forEach(function (el) { el.disabled = true; });
    };
    return figure;
  }

  return { createFigure: createFigure };
}));
