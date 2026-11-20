(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ComplexMultiplication = factory();
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  const PRESETS = Object.freeze({
    i: Object.freeze({ rho: 1, phi: Math.PI / 2, label: 'i' }),
    '2': Object.freeze({ rho: 2, phi: 0, label: '2' }),
    '2i': Object.freeze({ rho: 2, phi: Math.PI / 2, label: '2i' }),
    '0': Object.freeze({ rho: 0, phi: 0, label: '0' })
  });
  const BLUE = '#1769b0';
  const ORANGE = '#c15a13';
  // This fixed window also contains the entire 2i homotopy of the source
  // square [-1.5,1.5]^2, including its oblique intermediate positions.
  const VIEW_HALF = 3.75;
  const SOURCE_HALF = 1.5;
  const EPSILON = 1e-12;
  let nextId = 0;

  function clean(value) { return Math.abs(value) < EPSILON ? 0 : value; }
  function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }
  function finite(value, name) {
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new TypeError(name + ' must be finite');
    return value;
  }
  function complex(value, name) {
    if (!Array.isArray(value) || value.length !== 2) throw new TypeError(name + ' must be [real, imaginary]');
    return [finite(value[0], name), finite(value[1], name)];
  }
  function presetFor(name) {
    if (!Object.prototype.hasOwnProperty.call(PRESETS, name)) throw new RangeError('Unknown multiplier: ' + name);
    return PRESETS[name];
  }
  function polarTarget(rho, phi) {
    finite(rho, 'rho');
    finite(phi, 'phi');
    if (rho < 0 || rho > 2) throw new RangeError('rho must be between 0 and 2');
    if (phi < -Math.PI || phi > Math.PI) throw new RangeError('phi must be between -pi and pi');
    // Zero has no argument: its demonstration is a pure collapse.
    return { rho, phi: rho === 0 ? 0 : phi };
  }

  function mapComplex(z, a) {
    const source = complex(z, 'z');
    const multiplier = complex(a, 'a');
    return [
      source[0] * multiplier[0] - source[1] * multiplier[1],
      source[0] * multiplier[1] + source[1] * multiplier[0]
    ];
  }

  // The multiplier itself moves continuously: a_s = rho_s exp(i phi_s).
  // In particular, intermediate frames are not multiplication by the target.
  function interpolateMultiplier(preset, s) {
    const target = typeof preset === 'object' && preset !== null ? polarTarget(preset.rho, preset.phi) : presetFor(preset);
    const progress = clamp(finite(s, 'progress'), 0, 1);
    if (progress === 0) return [1, 0];
    if (progress === 1) {
      if (target.phi === 0) return [target.rho, 0];
      if (Math.abs(target.phi) === Math.PI / 2) return [0, Math.sign(target.phi) * target.rho];
      if (Math.abs(target.phi) === Math.PI) return [-target.rho, 0];
    }
    const rho = 1 + (target.rho - 1) * progress;
    const phi = target.phi * progress;
    return [rho * Math.cos(phi), rho * Math.sin(phi)];
  }

  function number(value) {
    return clean(value).toFixed(2).replace(/\.00$/, '').replace(/(\.\d)0$/, '$1').replace('-', '−');
  }
  function complexText(point) {
    const x = clean(point[0]);
    const y = clean(point[1]);
    if (y === 0) return number(x);
    const imaginary = Math.abs(y) === 1 ? 'i' : number(Math.abs(y)) + 'i';
    if (x === 0) return (y < 0 ? '−' : '') + imaginary;
    return number(x) + (y < 0 ? ' − ' : ' + ') + imaginary;
  }
  function degrees(angle) { return number(angle * 180 / Math.PI) + '°'; }

  function createScene(host, options) {
    if (!host || !host.ownerDocument) throw new TypeError('A host element is required');
    options = options || {};
    const doc = host.ownerDocument;
    const win = doc.defaultView;
    const id = 'cm2-' + (++nextId);
    const media = win.matchMedia ? win.matchMedia('(prefers-reduced-motion: reduce)') : null;
    let reducedMotion = Boolean(media && media.matches);
    let preset = 'i';
    let target = PRESETS.i;
    let viewHalf = VIEW_HALF;
    let progress = 0;
    let z = [1, 1];
    let isPlaying = false;
    let destroyed = false;
    let frameId = null;
    let frameStart = null;
    let startProgress = 0;
    let dragging = null;
    let isOverlay = options.overlay === true || win.innerWidth <= 560;
    let size = 320;
    let dpr = Math.max(1, win.devicePixelRatio || 1);
    const duration = 1800;

    const root = doc.createElement('section');
    root.className = 'cm2-scene';
    root.setAttribute('aria-label', '复数乘法的原图与像图');
    const style = doc.createElement('style');
    style.textContent = `
      .cm2-scene,.cm2-scene *{box-sizing:border-box}
      .cm2-scene{width:100%;min-width:0;color:#243546;font-family:inherit}
      .cm2-current{font-size:16px;font-weight:650;line-height:1.6;margin:0 0 4px;overflow-wrap:anywhere}
      .cm2-parameters{font-size:14px;line-height:1.65;margin:0 0 12px;color:#526170;font-variant-numeric:tabular-nums}
      .cm2-pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;min-width:0}
      .cm2-overlay .cm2-pair{grid-template-columns:minmax(0,1fr)}
      .cm2-panel{margin:0;min-width:0;border:1px solid #d8e1e8;border-radius:12px;overflow:hidden;background:#fff}
      .cm2-panel[hidden]{display:none}
      .cm2-panel-title{font-size:15px;font-weight:650;margin:12px 12px 0;line-height:1.5}
      .cm2-source .cm2-panel-title,.cm2-source .cm2-readout{color:${BLUE}}
      .cm2-image .cm2-panel-title,.cm2-image .cm2-readout{color:${ORANGE}}
      .cm2-canvas{display:block;width:100%;height:auto;aspect-ratio:1;min-width:0}
      .cm2-source .cm2-canvas{touch-action:none;cursor:grab}
      .cm2-overlay .cm2-image .cm2-canvas{touch-action:none;cursor:grab}
      .cm2-canvas:focus-visible{outline:3px solid ${BLUE};outline-offset:-4px;border-radius:8px}
      .cm2-readout{font-size:14px;line-height:1.65;margin:0;padding:0 12px 12px;min-height:62px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
      .cm2-note{font-size:13px;line-height:1.7;margin:10px 0 0;color:#526170}
      .cm2-source-summary{display:block;color:${BLUE}}
      .cm2-source-summary[hidden]{display:none}
      @media(max-width:560px){.cm2-pair{grid-template-columns:minmax(0,1fr);gap:12px}.cm2-readout{min-height:0}}
    `;
    root.appendChild(style);
    function element(tag, className, text) {
      const node = doc.createElement(tag);
      node.className = className;
      if (text) node.textContent = text;
      return node;
    }
    const current = element('p', 'cm2-current');
    const parameters = element('p', 'cm2-parameters');
    current.setAttribute('aria-live', 'off');
    root.appendChild(current);
    root.appendChild(parameters);
    const pair = element('div', 'cm2-pair');
    root.appendChild(pair);
    function panel(kind, title) {
      const figure = element('figure', 'cm2-panel cm2-' + kind);
      const heading = element('figcaption', 'cm2-panel-title', title);
      const canvas = element('canvas', 'cm2-canvas');
      canvas.width = 320;
      canvas.height = 320;
      canvas.setAttribute('role', 'img');
      const readout = element('p', 'cm2-readout');
      figure.appendChild(heading);
      figure.appendChild(canvas);
      figure.appendChild(readout);
      pair.appendChild(figure);
      return { figure, heading, canvas, readout, context: canvas.getContext('2d') };
    }
    const source = panel('source', '原图 · z（蓝色）');
    const image = panel('image', '像图 · w = az（橙色）');
    const sourceSummary = element('span', 'cm2-source-summary');
    const imageSummary = element('span', 'cm2-image-summary');
    image.readout.appendChild(sourceSummary);
    image.readout.appendChild(imageSummary);
    source.canvas.tabIndex = 0;
    source.canvas.setAttribute('aria-describedby', id + '-note');
    image.canvas.setAttribute('aria-describedby', id + '-note');
    const note = element('p', 'cm2-note', '两图坐标比例相同，刻度间隔为 1；x 是实部，y 是虚部。彩色小格边长原为 0.5，填色区域原为单位正方形。拖动左图 A，或聚焦左图后用方向键移动（Shift 精调），可比较任意点的像。');
    note.id = id + '-note';
    root.appendChild(note);
    host.appendChild(root);

    function getState() {
      const a = interpolateMultiplier(target, progress);
      const w = mapComplex(z, a);
      const sourceLength = Math.hypot(z[0], z[1]);
      const imageLength = Math.hypot(w[0], w[1]);
      const sourceAngle = sourceLength === 0 ? null : Math.atan2(z[1], z[0]);
      const phi = target.phi * progress;
      return {
        progress, preset, z: z.slice(), a, w,
        rho: 1 + (target.rho - 1) * progress, phi,
        target: interpolateMultiplier(target, 1), targetRho: target.rho, targetPhi: target.phi,
        sourceLength, imageLength, sourceAngle,
        imageAngle: imageLength === 0 || sourceAngle === null ? null : sourceAngle + phi,
        isPlaying, reducedMotion, comparisonMode: isOverlay ? 'overlay' : 'side-by-side'
      };
    }

    function geometry() {
      const margin = size < 280 ? 26 : 32;
      return { center: size / 2, scale: (size - 2 * margin) / (viewHalf * 2) };
    }
    function toPixel(point) {
      const g = geometry();
      return [g.center + point[0] * g.scale, g.center - point[1] * g.scale];
    }
    function path(ctx, points, close) {
      ctx.beginPath();
      points.forEach(function (point, index) {
        const p = toPixel(point);
        if (index) ctx.lineTo(p[0], p[1]);
        else ctx.moveTo(p[0], p[1]);
      });
      if (close) ctx.closePath();
    }
    function segment(ctx, from, to) {
      path(ctx, [from, to]);
      ctx.stroke();
    }
    function pill(ctx, text, x, y, color) {
      ctx.font = '13px system-ui, sans-serif';
      const width = ctx.measureText(text).width;
      const safeX = clamp(x, 9 + width / 2, size - 9 - width / 2);
      const safeY = clamp(y, 19, size - 10);
      ctx.fillStyle = 'rgba(255,255,255,0.94)';
      ctx.fillRect(safeX - width / 2 - 4, safeY - 14, width + 8, 19);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'alphabetic';
      ctx.fillStyle = color;
      ctx.fillText(text, safeX, safeY);
    }
    function drawAxes(ctx) {
      const g = geometry();
      const axisEnd = viewHalf - 0.23;
      ctx.strokeStyle = '#7b8790';
      ctx.lineWidth = 1;
      segment(ctx, [-axisEnd, 0], [axisEnd, 0]);
      segment(ctx, [0, -axisEnd], [0, axisEnd]);
      ctx.font = '12px system-ui, sans-serif';
      ctx.fillStyle = '#58656f';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      for (let n = -Math.floor(axisEnd); n <= Math.floor(axisEnd); n++) {
        if (!n) continue;
        const x = toPixel([n, 0])[0];
        const y = toPixel([0, n])[1];
        ctx.beginPath();
        ctx.moveTo(x, g.center - 3);
        ctx.lineTo(x, g.center + 3);
        ctx.moveTo(g.center - 3, y);
        ctx.lineTo(g.center + 3, y);
        ctx.stroke();
        ctx.textAlign = 'center';
        ctx.fillText(String(n).replace('-', '−'), x, g.center + 6);
        ctx.textAlign = 'right';
        ctx.fillText(String(n).replace('-', '−'), g.center - 7, y - 6);
      }
      ctx.font = '14px system-ui, sans-serif';
      ctx.fillStyle = '#35434f';
      ctx.textAlign = 'left';
      ctx.fillText('x', toPixel([axisEnd, 0])[0] + 3, g.center - 16);
      ctx.fillText('y', g.center + 9, toPixel([0, axisEnd])[1] - 4);
      ctx.fillText('O', g.center - 17, g.center + 7);
    }

    function drawPanel(panelObject, isImage, state, layer) {
      const ctx = panelObject.context;
      if (!ctx) return;
      const underlay = layer === 'source-underlay';
      const overlay = layer === 'image-overlay';
      const coincident = Math.hypot(state.z[0] - state.w[0], state.z[1] - state.w[1]) < 1e-10;
      const color = isImage ? ORANGE : BLUE;
      const rgb = isImage ? '193,90,19' : '23,105,176';
      const multiplier = isImage ? state.a : [1, 0];
      const transform = point => mapComplex(point, multiplier);
      const point = isImage ? state.w : state.z;
      const length = isImage ? state.imageLength : state.sourceLength;
      const angle = isImage ? state.imageAngle : state.sourceAngle;
      const label = isImage ? 'A′' : 'A';
      const g = geometry();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (!overlay) {
        ctx.clearRect(0, 0, size, size);
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, size, size);
      }
      ctx.lineCap = 'round';
      // The same bounded grid is multiplied at every frame, rather than
      // replaced by a coordinate grid in the destination plane.
      for (let n = -3; n <= 3; n++) {
        const value = n / 2;
        ctx.strokeStyle = 'rgba(' + rgb + ',' + (underlay ? (n % 2 ? '0.14' : '0.26') : (n % 2 ? '0.25' : '0.40')) + ')';
        ctx.lineWidth = n % 2 ? 1 : 1.2;
        segment(ctx, transform([value, -SOURCE_HALF]), transform([value, SOURCE_HALF]));
        segment(ctx, transform([-SOURCE_HALF, value]), transform([SOURCE_HALF, value]));
      }
      const square = [[0, 0], [1, 0], [1, 1], [0, 1]].map(transform);
      path(ctx, square, true);
      ctx.fillStyle = 'rgba(' + rgb + (underlay ? ',0.06)' : ',0.13)');
      ctx.fill();
      ctx.strokeStyle = underlay ? 'rgba(' + rgb + ',0.65)' : color;
      ctx.lineWidth = 1.8;
      ctx.stroke();
      drawAxes(ctx);

      ctx.strokeStyle = 'rgba(' + rgb + ',0.65)';
      ctx.lineWidth = 1.2;
      ctx.setLineDash([4, 4]);
      segment(ctx, point, [point[0], 0]);
      segment(ctx, point, [0, point[1]]);
      ctx.setLineDash([]);
      const end = toPixel(point);
      if (length > EPSILON) {
        ctx.strokeStyle = color;
        ctx.lineWidth = underlay ? 2.5 : 3;
        segment(ctx, [0, 0], point);
        const heading = Math.atan2(end[1] - g.center, end[0] - g.center);
        const arrowSize = Math.min(9, length * g.scale / 3);
        ctx.beginPath();
        ctx.moveTo(end[0], end[1]);
        ctx.lineTo(end[0] - arrowSize * Math.cos(heading - 0.42), end[1] - arrowSize * Math.sin(heading - 0.42));
        ctx.lineTo(end[0] - arrowSize * Math.cos(heading + 0.42), end[1] - arrowSize * Math.sin(heading + 0.42));
        ctx.closePath();
        ctx.fillStyle = color;
        ctx.fill();

        const arcRadius = Math.min(28, length * g.scale * 0.55);
        if (arcRadius > 5 && Math.abs(angle) > 0.005) {
          ctx.beginPath();
          ctx.strokeStyle = color;
          ctx.lineWidth = 1.6;
          // Canvas y points down; the mathematical angle points up.
          ctx.arc(g.center, g.center, arcRadius, 0, -angle, angle > 0);
          ctx.stroke();
        }
        const nx = -(end[1] - g.center) / (length * g.scale);
        const ny = (end[0] - g.center) / (length * g.scale);
        if (length * g.scale > 45) {
          const side = overlay ? -1 : 1;
          pill(ctx, '|O' + label + '|=' + number(length),
            g.center + (end[0] - g.center) * 0.67 + nx * 19 * side,
            g.center + (end[1] - g.center) * 0.67 + ny * 19 * side, color);
        }
      }
      ctx.beginPath();
      ctx.arc(end[0], end[1], isImage ? 5 : 6, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = '#fff';
      ctx.stroke();
      if (overlay && coincident) {
        // A blue outer ring remains visible when the orange image is exactly
        // on the original point, including the identity and the origin.
        ctx.beginPath();
        ctx.arc(end[0], end[1], 9, 0, 2 * Math.PI);
        ctx.strokeStyle = BLUE;
        ctx.lineWidth = 2.5;
        ctx.stroke();
      }
      const dx = point[0] < 0 ? -15 : 15;
      if (!(underlay && coincident)) {
        const labelText = overlay && coincident ? 'A = A′' : label;
        const labelDx = overlay && coincident ? dx * 2.8 : (underlay ? -dx : dx);
        pill(ctx, labelText, end[0] + labelDx, end[1] - 12,
          overlay && coincident ? '#35434f' : color);
      }
      const direction = angle === null ? '方向无定义' : '方向 ' + degrees(angle);
      if (!underlay) pill(ctx, (overlay ? 'A′ ' : '') + direction, overlay ? size * 0.73 : size / 2, size - 13, color);
      if (overlay) {
        const sourceDirection = state.sourceAngle === null ? 'A 方向无定义' : 'A 方向 ' + degrees(state.sourceAngle);
        pill(ctx, sourceDirection, size * 0.25, size - 13, BLUE);
      }
      if (isImage && state.rho === 0) pill(ctx, '像网格、像正方形和 A′ 全部归于 O', size / 2, 23, color);
    }

    function render(emit) {
      if (destroyed) return;
      const state = getState();
      current.textContent = '当前乘数 a = ' + complexText(state.a);
      parameters.textContent = (state.rho === 0 ? '缩放 ρ = 0；已归零，方向无定义' : '缩放 ρ = ' + number(state.rho) + '　转角 φ = ' + degrees(state.phi)) +
        '　目标 a = ' + complexText(state.target) + ' · ' + Math.round(progress * 100) + '%';
      source.readout.textContent = 'A：z = ' + complexText(state.z) + '；|OA| = ' + number(state.sourceLength);
      sourceSummary.textContent = source.readout.textContent;
      sourceSummary.hidden = !isOverlay;
      imageSummary.textContent = 'A′：w = ' + complexText(state.w) + '；|OA′| = ' + number(state.imageLength) +
        (state.imageAngle === null ? '；辐角无定义' : '');
      source.canvas.setAttribute('aria-label', '可移动点 A，z 等于 ' + complexText(state.z) + '。方向键移动，Shift 精调。');
      const coincident = Math.hypot(state.z[0] - state.w[0], state.z[1] - state.w[1]) < 1e-10;
      image.canvas.setAttribute('aria-label', (isOverlay ? '蓝色原图 A 等于 ' + complexText(state.z) + '，橙色像图。' +
        (coincident ? 'A 与 A 撇重合，蓝圈包围橙点。' : '') + '可拖动蓝点 A 或用方向键移动。' : '') +
        '当前乘数 ' + complexText(state.a) + '，像点 A 撇等于 ' + complexText(state.w) +
        '，长度 ' + number(state.imageLength) + (state.imageAngle === null ? '，辐角无定义' : '，方向 ' + degrees(state.imageAngle)));
      if (isOverlay) {
        drawPanel(image, false, state, 'source-underlay');
        drawPanel(image, true, state, 'image-overlay');
      } else {
        drawPanel(source, false, state);
        drawPanel(image, true, state);
      }
      if (emit !== false && typeof options.onChange === 'function') options.onChange(state);
    }

    function resize() {
      if (destroyed) return;
      isOverlay = options.overlay === true || win.innerWidth <= 560;
      root.classList.toggle('cm2-overlay', isOverlay);
      source.figure.hidden = isOverlay;
      source.canvas.tabIndex = isOverlay ? -1 : 0;
      image.canvas.tabIndex = isOverlay ? 0 : -1;
      image.heading.textContent = isOverlay ? '同一坐标内比较原图与像图' : '像图 · w = az（橙色）';
      if (isOverlay && doc.activeElement === source.canvas) image.canvas.focus({ preventScroll: true });
      note.textContent = isOverlay ?
        '蓝色是原图，橙色是像；重合处用蓝圈与橙点标记。刻度间隔为 1，x 是实部、y 是虚部，填色区域原为单位正方形。拖动蓝点 A，或聚焦图后用方向键移动（Shift 精调）。' :
        '两图坐标比例相同，刻度间隔为 1；x 是实部，y 是虚部。彩色小格边长原为 0.5，填色区域原为单位正方形。拖动左图 A，或聚焦左图后用方向键移动（Shift 精调），可比较任意点的像。';
      const widths = [source.canvas, image.canvas].map(canvas => canvas.getBoundingClientRect().width).filter(width => width > 0);
      size = widths.length ? Math.min.apply(null, widths) : 320;
      dpr = Math.max(1, win.devicePixelRatio || 1);
      [source.canvas, image.canvas].forEach(function (canvas) {
        canvas.width = Math.round(size * dpr);
        canvas.height = Math.round(size * dpr);
      });
      render(false);
    }
    function stop() {
      if (frameId !== null) win.cancelAnimationFrame(frameId);
      frameId = null;
      frameStart = null;
      isPlaying = false;
    }
    function tick(now) {
      if (destroyed || !isPlaying) return;
      if (frameStart === null) frameStart = now;
      progress = clamp(startProgress + (now - frameStart) / duration, 0, 1);
      if (progress === 1) stop();
      render();
      if (isPlaying) frameId = win.requestAnimationFrame(tick);
    }
    function play() {
      if (destroyed || isPlaying) return api;
      if (progress === 1) progress = 0;
      if (reducedMotion) {
        progress = 1;
        render();
        return api;
      }
      isPlaying = true;
      startProgress = progress;
      frameStart = null;
      render();
      frameId = win.requestAnimationFrame(tick);
      return api;
    }
    function pause() {
      if (destroyed) return api;
      stop();
      render();
      return api;
    }
    function setProgress(s) {
      if (destroyed) return api;
      const nextProgress = clamp(finite(s, 'progress'), 0, 1);
      stop();
      progress = nextProgress;
      render();
      return api;
    }
    function setPreset(name, settings) {
      if (destroyed) return api;
      const nextTarget = presetFor(name);
      stop();
      preset = name;
      target = nextTarget;
      viewHalf = VIEW_HALF;
      progress = settings && settings.animate === false ? 1 : 0;
      if (!settings || settings.animate !== false) return play();
      render();
      return api;
    }
    function setPolar(rho, phi, settings) {
      if (destroyed) return api;
      const nextTarget = polarTarget(rho, phi);
      stop();
      preset = 'custom';
      target = nextTarget;
      // A corner can reach radius 1.5 sqrt(2) rho at any custom angle.
      // Both planes change scale together, and retain this scale while playing.
      viewHalf = Math.max(VIEW_HALF, SOURCE_HALF * Math.SQRT2 * Math.max(1, rho) + 0.25);
      progress = settings && settings.animate === false ? 1 : 0;
      if (!settings || settings.animate !== false) return play();
      render();
      return api;
    }
    function setPoint(point) {
      if (destroyed) return api;
      z = complex(point, 'z').map(value => clamp(value, -SOURCE_HALF, SOURCE_HALF));
      render();
      return api;
    }
    function reset() {
      if (destroyed) return api;
      stop();
      progress = 0;
      render();
      return api;
    }

    function eventPoint(event) {
      const rect = event.currentTarget.getBoundingClientRect();
      const factor = rect.width > 0 ? size / rect.width : 1;
      const g = geometry();
      return [((event.clientX - rect.left) * factor - g.center) / g.scale,
        (g.center - (event.clientY - rect.top) * factor) / g.scale];
    }
    function pointerDown(event) {
      const canvas = event.currentTarget;
      if (canvas !== (isOverlay ? image.canvas : source.canvas)) return;
      if (event.button !== undefined && event.button !== 0) return;
      const point = eventPoint(event);
      const distance = Math.hypot(point[0] - z[0], point[1] - z[1]) * geometry().scale;
      if (distance > (event.pointerType === 'touch' ? 32 : 24)) return;
      event.preventDefault();
      stop();
      dragging = { pointerId: event.pointerId, canvas };
      canvas.focus({ preventScroll: true });
      if (canvas.setPointerCapture) canvas.setPointerCapture(event.pointerId);
      canvas.style.cursor = 'grabbing';
      render();
    }
    function pointerMove(event) {
      if (dragging === null || event.pointerId !== dragging.pointerId || event.currentTarget !== dragging.canvas) return;
      event.preventDefault();
      setPoint(eventPoint(event));
    }
    function pointerUp(event) {
      if (dragging === null || event.pointerId !== dragging.pointerId) return;
      const canvas = dragging.canvas;
      if (canvas.hasPointerCapture && canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
      dragging = null;
      canvas.style.cursor = 'grab';
    }
    function keyDown(event) {
      if (event.currentTarget !== (isOverlay ? image.canvas : source.canvas)) return;
      const moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] };
      if (!moves[event.key]) return;
      event.preventDefault();
      stop();
      const step = event.shiftKey ? 0.01 : 0.1;
      const move = moves[event.key];
      setPoint([z[0] + move[0] * step, z[1] + move[1] * step]);
    }
    function motionChange(event) {
      reducedMotion = event.matches;
      if (reducedMotion && isPlaying) {
        stop();
        progress = 1;
      }
      render();
    }
    [source.canvas, image.canvas].forEach(function (canvas) {
      canvas.addEventListener('pointerdown', pointerDown);
      canvas.addEventListener('pointermove', pointerMove);
      canvas.addEventListener('pointerup', pointerUp);
      canvas.addEventListener('pointercancel', pointerUp);
      canvas.addEventListener('lostpointercapture', pointerUp);
      canvas.addEventListener('keydown', keyDown);
    });
    win.addEventListener('resize', resize);
    if (media && media.addEventListener) media.addEventListener('change', motionChange);
    const observer = win.ResizeObserver ? new win.ResizeObserver(resize) : null;
    if (observer) observer.observe(pair);

    function destroy() {
      if (destroyed) return;
      stop();
      destroyed = true;
      if (observer) observer.disconnect();
      win.removeEventListener('resize', resize);
      if (media && media.removeEventListener) media.removeEventListener('change', motionChange);
      [source.canvas, image.canvas].forEach(function (canvas) {
        canvas.removeEventListener('pointerdown', pointerDown);
        canvas.removeEventListener('pointermove', pointerMove);
        canvas.removeEventListener('pointerup', pointerUp);
        canvas.removeEventListener('pointercancel', pointerUp);
        canvas.removeEventListener('lostpointercapture', pointerUp);
        canvas.removeEventListener('keydown', keyDown);
      });
      root.remove();
    }
    const api = { setPreset, setPolar, setProgress, setPoint, play, pause, reset, getState, destroy };
    resize();
    render();
    return api;
  }

  return { createScene, mapComplex, interpolateMultiplier };
}));
