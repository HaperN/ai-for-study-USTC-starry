/* 修改计划 S：阅读页批注（书本身只读）。
 *
 * - 固定版式：主阅读区按 1200 逻辑宽度用 CSS zoom 等比缩放，墨迹按逻辑坐标保存，
 *   并挂在最近的内容块上（正文块 data-reader-pointer = 单元 + 块类型 + 来源位置；
 *   媒体块 data-media-anchor）。换窗口宽度后墨迹与文字仍对齐。
 * - 工具：笔（触控笔有压感；鼠标和手指固定粗细）、3 种笔色、2 种荧光色、2 档粗细、
 *   整笔橡皮、撤销/重做、文字便签、本页笔记列表、导出/导入。
 * - 触控笔写字、手指滚动；“书写模式”打开后手指也写字。
 * - 存储：本机 IndexedDB，键为 用户(local)/书/版本/单元/块；笔记绑定书的版本。
 */
(function () {
  'use strict';
  if (typeof window === 'undefined' || window.DSHNotes) return;
  // 批注只是附加功能：缺浏览器能力（如测试用 jsdom、禁用存储的隐私窗口）时不启用，绝不影响阅读页。
  try {
    // 沙箱 iframe（不透明来源）访问存储会抛 SecurityError：这种场景不启用批注。
    if (!window.indexedDB || !window.localStorage || typeof document.elementsFromPoint !== 'function'
        || !window.MutationObserver) return;
  } catch (err) { return; }
  var D = window.__DATA__ || {};
  var V = window.__BOOK_VERSION__ || {};
  var USER = 'local';
  var shared = window.__AIFS_SHARED_NOTES__ || {};
  var BOOK = String(shared.book || V.book || D.book || 'book');
  var VERSION = String(shared.revision || V.version || 'unversioned');
  // 逻辑宽度 = 参考电脑尺寸（1280 视口）下原生主阅读区宽度：此时缩放为 1，
  // 媒体显示宽度与制作、审核所用的电脑尺寸完全一致（D4）。更宽的屏幕最多放大 1.25 倍。
  // The study reader uses native responsive columns. Its annotation layer must
  // use the same unscaled coordinates; legacy book snapshots keep their scale.
  var RESPONSIVE_READER = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).some(function(link){
    return /(?:^|\/)study-(?:reader|light)\.css(?:[?#]|$)/.test(link.getAttribute('href')||'');
  });
  var LOGICAL_WIDTH = 1026;
  var MAX_SCALE = 1.25;
  var BLOCK_SELECTOR = '[data-reader-pointer], .reader-media[data-media-anchor]';
  var PEN_COLORS = ['#1f2933', '#c62828', '#1d4ed8'];
  var HIGHLIGHT_COLORS = ['#facc15', '#4ade80'];
  var WIDTHS = {pen: [2.2, 4.4], highlight: [14, 24]};
  var SVG_NS = 'http://www.w3.org/2000/svg';

  var state = {tool: 'read', color: PEN_COLORS[0], highlight: HIGHLIGHT_COLORS[0], size: 0,
               writeMode: false, scale: 1, unit: '', items: [], undo: [], redo: []};
  var main = null, layer = null, stickyBox = null, toolbar = null, panel = null, drawing = null;

  // ── 存储 ────────────────────────────────────────────────────────────────
  var dbPromise = null;
  function db() {
    if (!dbPromise) {
      dbPromise = new Promise(function (resolve, reject) {
        var request = window.indexedDB.open('aifs-notes', 1);
        request.onupgradeneeded = function () {
          var store = request.result.createObjectStore('items', {keyPath: 'id'});
          store.createIndex('scope', 'scope');
          store.createIndex('bookKey', 'bookKey');
        };
        request.onsuccess = function () { resolve(request.result); };
        request.onerror = function () { reject(request.error); };
      });
    }
    return dbPromise;
  }
  function tx(mode, fn) {
    return db().then(function (database) {
      return new Promise(function (resolve, reject) {
        var t = database.transaction('items', mode), store = t.objectStore('items'), out = fn(store);
        t.oncomplete = function () { resolve(out && out.result !== undefined ? out.result : out); };
        t.onabort = t.onerror = function () { reject(t.error || new Error('笔记存储事务中断')); };
      });
    });
  }
  function scopeOf(unit, version) { return [USER, BOOK, version || VERSION, unit].join('/'); }
  function bookKey() { return USER + '/' + BOOK; }
  function putItem(item) { item.updated = Date.now(); return tx('readwrite', function (s) { s.put(item); }); }
  function deleteItem(id) { return tx('readwrite', function (s) { s['delete'](id); }); }
  function itemsWhere(index, value) {
    return tx('readonly', function (s) { return s.index(index).getAll(value); });
  }
  function newId() { return 'n-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 9); }

  // ── 固定版式 ─────────────────────────────────────────────────────────────
  function applyScale() {
    main = document.getElementById('content');
    if (!main) return;
    if (RESPONSIVE_READER) {
      document.documentElement.removeAttribute('data-fixed-layout');
      state.scale = 1;
      document.documentElement.style.setProperty('--reader-page-scale', '1');
      redraw();
      return;
    }
    var layout = main.parentElement, available = layout ? layout.clientWidth : window.innerWidth;
    if (layout && window.getComputedStyle(layout).display === 'grid') {
      // 用网格里主阅读区那一列的实际宽度（与原生版式同一算法），参考尺寸下缩放严格为 1。
      var tracks = window.getComputedStyle(layout).gridTemplateColumns.split(' ').map(parseFloat)
        .filter(function (v) { return v > 0; });
      if (tracks.length) available = tracks[tracks.length - 1];
    }
    var scale = Math.max(0.25, Math.min(MAX_SCALE, available / LOGICAL_WIDTH));
    state.scale = scale;
    document.documentElement.style.setProperty('--reader-page-scale', String(scale));
    redraw();
  }

  // ── 坐标与内容块 ─────────────────────────────────────────────────────────
  function mainRect() { return main.getBoundingClientRect(); }
  function toLogical(clientX, clientY) {
    var r = mainRect();
    return {x: (clientX - r.left) / state.scale, y: (clientY - r.top) / state.scale};
  }
  function blockKey(el) {
    return el.hasAttribute('data-reader-pointer') ? 'p:' + el.getAttribute('data-reader-pointer')
      : 'm:' + el.getAttribute('data-media-anchor');
  }
  function findBlock(key) {
    if (!main || !key) return null;
    var attr = key.slice(0, 2) === 'p:' ? 'data-reader-pointer' : 'data-media-anchor';
    var value = key.slice(2), nodes = main.querySelectorAll('[' + attr + ']');
    for (var i = 0; i < nodes.length; i++) if (nodes[i].getAttribute(attr) === value) return nodes[i];
    return null;
  }
  function blockOrigin(el) {
    var r = el.getBoundingClientRect(), m = mainRect();
    return {x: (r.left - m.left) / state.scale, y: (r.top - m.top) / state.scale};
  }
  function blockAt(clientX, clientY) {
    var stack = document.elementsFromPoint(clientX, clientY);
    for (var i = 0; i < stack.length; i++) {
      var el = stack[i];
      if (el === layer || (layer && layer.contains(el)) || !main.contains(el)) continue;
      var block = el.closest(BLOCK_SELECTOR);
      if (block && main.contains(block)) return block;
    }
    var best = null, distance = Infinity, blocks = main.querySelectorAll(BLOCK_SELECTOR);
    for (var j = 0; j < blocks.length; j++) {
      var r = blocks[j].getBoundingClientRect();
      if (!r.height) continue;
      var d = clientY < r.top ? r.top - clientY : clientY > r.bottom ? clientY - r.bottom : 0;
      if (d < distance) { distance = d; best = blocks[j]; }
    }
    return best;
  }
  function anchorOf(item) {
    var block = findBlock(item.block);
    return block ? blockOrigin(block) : null;
  }

  // ── 墨迹几何（自带的小型平滑：移动平均 + 压感宽度轮廓） ───────────────────────
  function smooth(points) {
    if (points.length < 3) return points;
    var out = [points[0]];
    for (var i = 1; i < points.length - 1; i++) {
      var a = points[i - 1], b = points[i], c = points[i + 1];
      out.push([(a[0] + 2 * b[0] + c[0]) / 4, (a[1] + 2 * b[1] + c[1]) / 4, b[2]]);
    }
    out.push(points[points.length - 1]);
    return out;
  }
  function centerline(points) {
    if (!points.length) return '';
    if (points.length === 1) return 'M' + points[0][0] + ' ' + points[0][1] + 'l0.01 0';
    var d = 'M' + points[0][0].toFixed(2) + ' ' + points[0][1].toFixed(2);
    for (var i = 1; i < points.length - 1; i++) {
      var mx = (points[i][0] + points[i + 1][0]) / 2, my = (points[i][1] + points[i + 1][1]) / 2;
      d += 'Q' + points[i][0].toFixed(2) + ' ' + points[i][1].toFixed(2) + ' ' + mx.toFixed(2) + ' ' + my.toFixed(2);
    }
    var last = points[points.length - 1];
    return d + 'L' + last[0].toFixed(2) + ' ' + last[1].toFixed(2);
  }
  function outline(points, width) {
    // 压感：每点宽度 = 基础宽度 × (0.35 + 0.65 × 压力)，两侧偏移围成填充轮廓。
    var left = [], right = [];
    for (var i = 0; i < points.length; i++) {
      var p = points[i], q = points[Math.min(i + 1, points.length - 1)], o = points[Math.max(i - 1, 0)];
      var dx = q[0] - o[0], dy = q[1] - o[1], len = Math.sqrt(dx * dx + dy * dy) || 1;
      var w = width * (0.35 + 0.65 * Math.max(0, Math.min(1, p[2]))) / 2;
      left.push([p[0] - dy / len * w, p[1] + dx / len * w]);
      right.push([p[0] + dy / len * w, p[1] - dx / len * w]);
    }
    var ring = left.concat(right.reverse());
    return 'M' + ring.map(function (pt) { return pt[0].toFixed(2) + ' ' + pt[1].toFixed(2); }).join('L') + 'Z';
  }
  function strokeNode(item) {
    var g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('data-note-id', item.id);
    g.setAttribute('class', 'notes-stroke notes-' + item.tool);
    var points = smooth(item.points);
    if (item.tool === 'pen' && item.pressure) {
      var path = document.createElementNS(SVG_NS, 'path');
      path.setAttribute('d', outline(points, item.width * 2));
      path.setAttribute('fill', item.color);
      g.appendChild(path);
    } else {
      var line = document.createElementNS(SVG_NS, 'path');
      line.setAttribute('d', centerline(points));
      line.setAttribute('fill', 'none');
      line.setAttribute('stroke', item.color);
      line.setAttribute('stroke-width', String(item.width));
      line.setAttribute('stroke-linecap', 'round');
      line.setAttribute('stroke-linejoin', 'round');
      g.appendChild(line);
    }
    return g;
  }

  // ── 渲染 ─────────────────────────────────────────────────────────────────
  function ensureLayer() {
    main = document.getElementById('content');
    if (!main) return false;
    if (!layer || layer.parentNode !== main) {
      layer = document.createElementNS(SVG_NS, 'svg');
      layer.setAttribute('class', 'notes-layer');
      layer.setAttribute('aria-hidden', 'true');
      main.appendChild(layer);
      layer.addEventListener('pointerdown', onPointerDown);
    }
    if (!stickyBox || stickyBox.parentNode !== main) {
      stickyBox = document.createElement('div');
      stickyBox.className = 'notes-stickies';
      main.appendChild(stickyBox);
    }
    return true;
  }
  function redraw() {
    if (!ensureLayer()) return;
    layer.setAttribute('width', String(RESPONSIVE_READER ? main.clientWidth : main.scrollWidth));
    layer.setAttribute('height', String(Math.max(main.scrollHeight, main.clientHeight)));
    while (layer.firstChild) layer.removeChild(layer.firstChild);
    stickyBox.textContent = '';
    state.items.forEach(function (item) {
      var origin = anchorOf(item);
      if (!origin) return;
      if (item.kind === 'ink') {
        var g = strokeNode(item);
        g.setAttribute('transform', 'translate(' + origin.x.toFixed(2) + ' ' + origin.y.toFixed(2) + ')');
        layer.appendChild(g);
      } else if (item.kind === 'note' && item.block.slice(0, 4) !== 'pop:') {
        var note = document.createElement('button');
        note.type = 'button';
        note.className = 'notes-sticky';
        note.setAttribute('data-note-id', item.id);
        note.style.left = (origin.x + item.x) + 'px';
        note.style.top = (origin.y + item.y) + 'px';
        note.title = (item.backupOf ? '【备份副本】' : '') + item.text;
        note.textContent = '✎';
        note.addEventListener('click', function () { editNote(item); });
        stickyBox.appendChild(note);
      }
    });
    renderPanel();
  }
  function loadUnit() {
    var unit = document.documentElement.getAttribute('data-reader-unit') || (location.hash.replace(/^#\/?/, '') || '');
    state.unit = unit;
    state.undo = []; state.redo = [];
    return itemsWhere('scope', scopeOf(unit)).then(function (items) {
      if (state.unit !== unit) return;
      state.items = items.sort(function (a, b) { return a.created - b.created; });
      redraw();
      rememberPosition();
    });
  }

  // ── 指针输入 ─────────────────────────────────────────────────────────────
  function drawingTool() { return state.tool === 'pen' || state.tool === 'highlight'; }
  function acceptsPointer(ev) {
    if (ev.pointerType === 'touch') return state.writeMode;     // 手指默认滚动
    if (ev.pointerType === 'mouse') return ev.button === 0;
    return true;                                               // 触控笔
  }
  function onPointerDown(ev) {
    if (state.tool === 'read' || !acceptsPointer(ev)) return;
    if (state.tool === 'erase') { ev.preventDefault(); eraseAt(ev); startErase(ev); return; }
    if (state.tool === 'note') { ev.preventDefault(); addNoteAt(ev); return; }
    if (!drawingTool()) return;
    ev.preventDefault();
    var block = blockAt(ev.clientX, ev.clientY);
    if (!block) return;
    var origin = blockOrigin(block), start = toLogical(ev.clientX, ev.clientY);
    var pressure = ev.pointerType === 'pen' && ev.pressure > 0;
    var pen = state.tool === 'pen';
    drawing = {pointerId: ev.pointerId, origin: origin, item: {
      id: newId(), kind: 'ink', tool: state.tool, pressure: pen && pressure,
      color: pen ? state.color : state.highlight, width: WIDTHS[state.tool][state.size],
      block: blockKey(block), unit: state.unit, points: [], created: Date.now()}};
    drawing.node = null;
    addPoint(ev, start);
    try { layer.setPointerCapture(ev.pointerId); } catch (e) { /* synthetic events */ }
    layer.addEventListener('pointermove', onPointerMove);
    layer.addEventListener('pointerup', onPointerUp);
    layer.addEventListener('pointercancel', onPointerUp);
  }
  function addPoint(ev, logical) {
    var p = logical || toLogical(ev.clientX, ev.clientY);
    var pressure = drawing.item.pressure ? ev.pressure : 0.5;
    drawing.item.points.push([+(p.x - drawing.origin.x).toFixed(2), +(p.y - drawing.origin.y).toFixed(2), +pressure.toFixed(3)]);
    var node = strokeNode(drawing.item);
    node.setAttribute('transform', 'translate(' + drawing.origin.x.toFixed(2) + ' ' + drawing.origin.y.toFixed(2) + ')');
    if (drawing.node) layer.replaceChild(node, drawing.node); else layer.appendChild(node);
    drawing.node = node;
  }
  function onPointerMove(ev) {
    if (!drawing || ev.pointerId !== drawing.pointerId) return;
    var events = typeof ev.getCoalescedEvents === 'function' ? ev.getCoalescedEvents() : [ev];
    (events.length ? events : [ev]).forEach(function (e) { addPoint(e); });
  }
  function onPointerUp(ev) {
    if (!drawing || ev.pointerId !== drawing.pointerId) return;
    layer.removeEventListener('pointermove', onPointerMove);
    layer.removeEventListener('pointerup', onPointerUp);
    layer.removeEventListener('pointercancel', onPointerUp);
    var item = drawing.item;
    drawing = null;
    if (ev.type === 'pointercancel' && item.points.length < 2) { redraw(); return; }
    commit({op: 'add', item: fillScope(item)});
  }
  function fillScope(item) {
    item.user = USER; item.book = BOOK; item.version = VERSION;
    item.scope = scopeOf(item.unit); item.bookKey = bookKey();
    return item;
  }
  function startErase(ev) {
    function move(e) { if (e.pointerId === ev.pointerId) eraseAt(e); }
    function up(e) {
      if (e.pointerId !== ev.pointerId) return;
      layer.removeEventListener('pointermove', move); layer.removeEventListener('pointerup', up);
    }
    layer.addEventListener('pointermove', move); layer.addEventListener('pointerup', up);
  }
  function eraseAt(ev) {
    var stack = document.elementsFromPoint(ev.clientX, ev.clientY);
    for (var i = 0; i < stack.length; i++) {
      var g = stack[i].closest && stack[i].closest('[data-note-id]');
      if (!g || !layer.contains(g)) continue;
      var id = g.getAttribute('data-note-id');
      var item = state.items.filter(function (it) { return it.id === id; })[0];
      if (item) commit({op: 'delete', item: item});
      return;
    }
  }

  // ── 撤销 / 重做 ──────────────────────────────────────────────────────────
  function apply(action, reverse) {
    if (action.op === 'replace') {
      var target = reverse ? action.before : action.after;
      state.items = state.items.filter(function (it) { return it.id !== target.id; }).concat([target]);
      redraw();
      return putItem(target);
    }
    var adding = (action.op === 'add') !== !!reverse;
    if (adding) {
      state.items = state.items.filter(function (it) { return it.id !== action.item.id; }).concat([action.item]);
      redraw();
      return putItem(action.item);
    }
    state.items = state.items.filter(function (it) { return it.id !== action.item.id; });
    redraw();
    return deleteItem(action.item.id);
  }
  function commit(action) { state.undo.push(action); state.redo = []; return apply(action, false); }
  function undo() { var a = state.undo.pop(); if (a) { state.redo.push(a); return apply(a, true); } return Promise.resolve(); }
  function redo() { var a = state.redo.pop(); if (a) { state.undo.push(a); return apply(a, false); } return Promise.resolve(); }

  // ── 文字便签 ─────────────────────────────────────────────────────────────
  function popupKey(target) {
    var popup = target.closest && target.closest('.reader-popup');
    if (!popup) return '';
    var head = popup.querySelector('.reader-popup-head');
    return 'pop:' + ((head && head.textContent) || '浮窗').replace(/\s+/g, ' ').trim().slice(0, 80);
  }
  function addNoteAt(ev) {
    var block = blockAt(ev.clientX, ev.clientY);
    if (!block) return;
    var origin = blockOrigin(block), p = toLogical(ev.clientX, ev.clientY);
    editNote(fillScope({id: newId(), kind: 'note', unit: state.unit, block: blockKey(block),
                        x: +(p.x - origin.x).toFixed(1), y: +(p.y - origin.y).toFixed(1), text: '', created: Date.now()}), true);
  }
  function addPopupNote(popupElement) {
    editNote(fillScope({id: newId(), kind: 'note', unit: state.unit, block: popupKey(popupElement),
                        x: 0, y: 0, text: '', created: Date.now()}), true);
  }
  function editNote(item, fresh) {
    var text = window.prompt(fresh ? '新的文字便签' : '编辑文字便签（清空即删除）', item.text || '');
    if (text === null) return Promise.resolve();
    text = text.trim();
    if (!text) return fresh ? Promise.resolve() : commit({op: 'delete', item: item});
    var updated = Object.assign({}, item, {text: text});
    return commit(fresh ? {op: 'add', item: updated} : {op: 'replace', before: item, after: updated});
  }
  document.addEventListener('click', function (ev) {
    if (state.tool !== 'note' || !ev.target.closest || !ev.target.closest('.reader-popup')) return;
    ev.preventDefault(); ev.stopPropagation();
    addPopupNote(ev.target);
  }, true);

  // ── 工具栏与笔记列表 ─────────────────────────────────────────────────────
  function button(label, action, extra) {
    var b = document.createElement('button');
    b.type = 'button'; b.textContent = label; b.setAttribute('data-notes-action', action);
    if (extra) Object.keys(extra).forEach(function (k) { b.setAttribute(k, extra[k]); });
    return b;
  }
  function buildToolbar() {
    toolbar = document.createElement('div');
    toolbar.className = 'notes-toolbar';
    toolbar.setAttribute('role', 'toolbar');
    toolbar.setAttribute('aria-label', '批注工具');
    toolbar.appendChild(button('批注', 'collapse', {'class': 'notes-collapse', 'aria-expanded': 'true', title: '收起/展开批注工具'}));
    [['阅读', 'tool:read'], ['笔', 'tool:pen'], ['荧光', 'tool:highlight'], ['橡皮', 'tool:erase'], ['便签', 'tool:note']]
      .forEach(function (row) { toolbar.appendChild(button(row[0], row[1])); });
    PEN_COLORS.forEach(function (color, i) {
      toolbar.appendChild(button('', 'color:' + i, {'class': 'notes-swatch', style: 'background:' + color, 'aria-label': '笔色 ' + (i + 1)}));
    });
    HIGHLIGHT_COLORS.forEach(function (color, i) {
      toolbar.appendChild(button('', 'highlight:' + i, {'class': 'notes-swatch notes-swatch-hl', style: 'background:' + color, 'aria-label': '荧光色 ' + (i + 1)}));
    });
    toolbar.appendChild(button('细', 'size:0')); toolbar.appendChild(button('粗', 'size:1'));
    toolbar.appendChild(button('撤销', 'undo')); toolbar.appendChild(button('重做', 'redo'));
    toolbar.appendChild(button('书写模式', 'write', {'aria-pressed': 'false', title: '打开后手指也能写字；关闭时手指滚动'}));
    toolbar.appendChild(button('笔记', 'panel'));
    toolbar.appendChild(button('导出', 'export'));
    toolbar.appendChild(button('导入', 'import'));
    toolbar.addEventListener('click', function (ev) {
      var b = ev.target.closest('[data-notes-action]');
      if (!b) return;
      var parts = b.getAttribute('data-notes-action').split(':');
      if (parts[0] === 'tool') setTool(parts[1]);
      else if (parts[0] === 'color') { state.color = PEN_COLORS[+parts[1]]; setTool('pen'); }
      else if (parts[0] === 'highlight') { state.highlight = HIGHLIGHT_COLORS[+parts[1]]; setTool('highlight'); }
      else if (parts[0] === 'size') { state.size = +parts[1]; syncToolbar(); }
      else if (parts[0] === 'undo') undo();
      else if (parts[0] === 'redo') redo();
      else if (parts[0] === 'write') setWriteMode(!state.writeMode);
      else if (parts[0] === 'panel') { panel.hidden = !panel.hidden; renderPanel(); }
      else if (parts[0] === 'export') downloadExport();
      else if (parts[0] === 'import') pickImport();
      else if (parts[0] === 'collapse') {
        var collapsed = toolbar.classList.toggle('is-collapsed');
        b.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        if (collapsed) setTool('read');
        try { window.localStorage.setItem('aifs:notes-toolbar-collapsed', collapsed ? '1' : ''); } catch (e) { /* private mode */ }
      }
    });
    document.body.appendChild(toolbar);
    var startCollapsed = typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 900px)').matches;
    try { var saved = window.localStorage.getItem('aifs:notes-toolbar-collapsed'); if (saved !== null) startCollapsed = saved === '1'; } catch (e) { /* private mode */ }
    if (startCollapsed) {
      toolbar.classList.add('is-collapsed');
      toolbar.querySelector('.notes-collapse').setAttribute('aria-expanded', 'false');
    }
    panel = document.createElement('aside');
    panel.className = 'notes-panel'; panel.hidden = true;
    panel.setAttribute('aria-label', '本页笔记');
    document.body.appendChild(panel);
    var hinted = true;
    try { hinted = !!window.localStorage.getItem('aifs:notes-local-hint'); } catch (e) { /* private mode */ }
    if (location.protocol === 'file:' && !hinted) {
      var hint = document.createElement('div');
      hint.className = 'notes-hint';
      hint.textContent = '笔记只存在这个浏览器里，请定期导出。';
      var close = button('知道了', 'hint-close');
      close.addEventListener('click', function () {
        hint.remove();
        try { window.localStorage.setItem('aifs:notes-local-hint', '1'); } catch (e) { /* private mode */ }
      });
      hint.appendChild(close);
      document.body.appendChild(hint);
    }
    syncToolbar();
  }
  function syncToolbar() {
    if (!toolbar) return;
    Array.prototype.forEach.call(toolbar.querySelectorAll('[data-notes-action]'), function (b) {
      var a = b.getAttribute('data-notes-action').split(':');
      var on = (a[0] === 'tool' && a[1] === state.tool) || (a[0] === 'size' && +a[1] === state.size)
        || (a[0] === 'color' && PEN_COLORS[+a[1]] === state.color) || (a[0] === 'highlight' && HIGHLIGHT_COLORS[+a[1]] === state.highlight)
        || (a[0] === 'write' && state.writeMode);
      b.classList.toggle('is-active', !!on);
      if (a[0] === 'write') b.setAttribute('aria-pressed', state.writeMode ? 'true' : 'false');
    });
  }
  function setTool(tool) {
    state.tool = tool;
    document.documentElement.setAttribute('data-notes-tool', tool);
    syncToolbar();
  }
  function setWriteMode(on) {
    state.writeMode = !!on;
    document.documentElement.toggleAttribute('data-notes-write', state.writeMode);
    syncToolbar();
  }
  function renderPanel() {
    if (!panel || panel.hidden) return;
    panel.textContent = '';
    var title = document.createElement('h2'); title.textContent = '本页笔记'; panel.appendChild(title);
    var ink = state.items.filter(function (it) { return it.kind === 'ink'; });
    var notes = state.items.filter(function (it) { return it.kind === 'note'; });
    var summary = document.createElement('p');
    summary.textContent = '墨迹 ' + ink.length + ' 笔 · 文字便签 ' + notes.length + ' 条 · 版本 ' + VERSION;
    var inkCopies = ink.filter(function (item) { return item.backupOf; });
    if (inkCopies.length) summary.textContent += '（含 ' + inkCopies.length + ' 笔备份副本墨迹）';
    panel.appendChild(summary);
    var list = document.createElement('ol');
    notes.forEach(function (item) {
      var li = document.createElement('li');
      li.textContent = (item.backupOf ? '【备份副本】' : '') + item.text + (item.block.slice(0, 4) === 'pop:' ? '（浮窗：' + item.block.slice(4) + '）'
        : findBlock(item.block) ? '' : '（原位置在本版本中已不存在）');
      var go = button('定位', 'noop');
      go.addEventListener('click', function () { var b = findBlock(item.block); if (b) b.scrollIntoView({block: 'center'}); });
      li.appendChild(go);
      list.appendChild(li);
    });
    panel.appendChild(list);
    var legacy = document.createElement('details');
    itemsWhere('bookKey', bookKey()).then(function (items) {
      if (!list.isConnected || panel.hidden) return;
      var older = items.filter(function (it) { return it.version !== VERSION; });
      if (!older.length) return;
      var label = document.createElement('summary');
      label.textContent = '其他版本的私人笔记（' + older.length + '）';
      legacy.appendChild(label);
      older.forEach(function (item) {
        var row = document.createElement('p');
        row.textContent = (item.backupOf ? '【备份副本】' : '') + [item.version || 'unversioned', item.unit, item.kind === 'ink' ? '墨迹' : item.text].join(' · ');
        legacy.appendChild(row);
      });
      var download = button('导出全书各版本笔记', 'noop');
      download.onclick = downloadExport;
      legacy.appendChild(download);
      panel.appendChild(legacy);
    });

  }

  // ── 导出 / 导入 ──────────────────────────────────────────────────────────
  function exportNotes() {
    return itemsWhere('bookKey', bookKey()).then(function (items) {
      return {schema_version: 'AifsNotesV1', user: USER, book: BOOK, exported_at: new Date().toISOString(),
              items: items.sort(function (a, b) { return a.created - b.created; })};
    });
  }
  // Compare JSON note data independently of object key order. Array order is part of an ink stroke.
  function equalFields(a, b) {
    if (a === b) return true;
    if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false;
    var keys = Object.keys(a);
    return keys.length === Object.keys(b).length && keys.every(function (key) {
      return Object.prototype.hasOwnProperty.call(b, key) && equalFields(a[key], b[key]);
    });
  }
  function noteContent(item) {
    var content = Object.assign(Object.create(null), item);
    delete content.id;
    delete content.backupOf;
    delete content.updated;
    return content;
  }
  function restoreNotes(payload) {
    if (!payload || payload.schema_version !== 'AifsNotesV1' || !Array.isArray(payload.items)) {
      return Promise.reject(new Error('不是 AifsNotesV1 笔记文件'));
    }
    var items = payload.items.filter(function (it) { return it && it.id && it.scope && it.book === BOOK; });
    var summary = {accepted:items.length, added:0, copies:0, skipped:0};
    return tx('readwrite', function (store) {
      // IDs are global across books. Read and add in one serialized transaction.
      store.getAll().onsuccess = function (event) {
        var ids = new Set(), families = new Map(), reserved = new Set(items.map(function (it) { return it.id; }));
        function index(item) {
          ids.add(item.id);
          if (item.book !== BOOK) return;
          var family = item.backupOf || item.id;
          if (!families.has(family)) families.set(family, []);
          families.get(family).push(item);
        }
        event.target.result.forEach(index);
        items.forEach(function (incoming) {
          var item = incoming, family = item.backupOf || item.id;
          var content = noteContent(item);
          var occupied = ids.has(item.id);
          var related = families.get(family) || [];
          if ((occupied || item.backupOf) && related.some(function (other) { return equalFields(noteContent(other), content); })) {
            summary.skipped++;
            return;
          }
          if (occupied) {
            var id = newId();
            while (ids.has(id) || reserved.has(id)) id = newId();
            item = Object.assign(Object.create(null), item, {id:id, backupOf:family});
            summary.copies++;
          }
          store.add(item);
          index(item);
          summary.added++;
        });
      };
    }).then(loadUnit).then(function () { return summary; });
  }
  function importNotes(payload) {
    return restoreNotes(payload).then(function (summary) { return summary.accepted; });
  }
  function downloadExport() {
    return exportNotes().then(function (payload) {
      var blob = new Blob([JSON.stringify(payload, null, 1)], {type: 'application/json'});
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = BOOK + '-笔记-' + new Date().toISOString().slice(0, 10) + '.json';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    });
  }
  function pickImport() {
    var input = document.createElement('input');
    input.type = 'file'; input.accept = 'application/json,.json';
    input.addEventListener('change', function () {
      var file = input.files && input.files[0];
      if (!file) return;
      file.text().then(function (text) { return restoreNotes(JSON.parse(text)); })
        .then(function (summary) {
          window.alert('新增 ' + summary.added + ' 条笔记，其中同编号的不同内容保留为备份副本 ' +
            summary.copies + ' 条。已跳过相同笔记 ' + summary.skipped + ' 条。');
        },
              function (err) { window.alert('导入失败：' + err.message); });
    });
    input.click();
  }
  function rememberPosition() {
    try {
      window.localStorage.setItem('aifs:last:' + BOOK, JSON.stringify({version: VERSION,
        href: location.pathname + location.hash, unit: state.unit, at: Date.now()}));
    } catch (e) { /* private mode */ }
  }

  // ── 启动 ─────────────────────────────────────────────────────────────────
  function start() {
    if (RESPONSIVE_READER) document.documentElement.removeAttribute('data-fixed-layout');
    else document.documentElement.setAttribute('data-fixed-layout', '');
    buildToolbar();
    setTool('read');
    applyScale();
    window.addEventListener('resize', applyScale);
    new MutationObserver(function (records) {
      if (records.some(function (r) { return r.attributeName === 'data-reader-unit'; })) loadUnit();
    }).observe(document.documentElement, {attributes: true, attributeFilter: ['data-reader-unit']});
    var pending = false;
    if (window.ResizeObserver) {
      new ResizeObserver(function () {
        if (pending) return;
        pending = true;
        window.requestAnimationFrame(function () { pending = false; if (!drawing) redraw(); });
      }).observe(document.getElementById('content') || document.body);
    }
    // app.js 每次换节会整体替换主区内容；层被冲掉时重新挂上并重画。
    new MutationObserver(function () { if (!drawing && main && (!layer || layer.parentNode !== main)) redraw(); })
      .observe(document.getElementById('content') || document.body, {childList: true});
    return loadUnit();
  }
  var ready = new Promise(function (resolve) {
    function boot() {
      try { start().then(resolve, resolve); } catch (err) { resolve(); }   // 失败只关掉批注，不抛给阅读页
    }
    if (document.documentElement.getAttribute('data-reader-ready')) boot();
    else {
      var observer = new MutationObserver(function () {
        if (document.documentElement.getAttribute('data-reader-ready')) { observer.disconnect(); boot(); }
      });
      observer.observe(document.documentElement, {attributes: true, attributeFilter: ['data-reader-ready']});
    }
  });

  window.DSHNotes = {ready: ready, exportNotes: exportNotes, importNotes: importNotes, setTool: setTool,
                     setWriteMode: setWriteMode, undo: undo, redo: redo, state: state, version: VERSION, book: BOOK,
                     count: function () { return itemsWhere('bookKey', bookKey()).then(function (x) { return x.length; }); }};
})();
