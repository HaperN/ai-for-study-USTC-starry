/* 渲染层的界面逻辑（浏览器里跑）。
 *
 * 分工：把 markdown 变成 HTML 的"纯逻辑"在 render-core.js（那边能在 node 里测）；
 * 这里只管界面：目录、切页、引用阅读浮窗、原文对照及旧概念抽屉。
 */
(function () {
  "use strict";

  /* ── P-025-2：数据坏了也不许崩，更不许停在"正在加载…" ─────────────────────
     `window.__DATA__` 是**外部文件**（data/data.js）给的。实测过的坏法：那个文件没跟着
     拷过来、拷了一半、或者是个空对象 —— 这时 `D.units` 是 undefined，页面脚本在
     "设置样张节数"那一行就抛异常，后面的渲染全不执行，读者看到的就是一个永远转不完的
     "正在加载…"（用户报的 P-025-2）。所以：**先把数据补齐成能用的形状**，
     再让缺东西的地方走"这一页没能显示出来"这种看得见的状态。 */
  var D = window.__DATA__ || {};
  D.units = D.units || {};
  D.concepts = D.concepts || {};
  D.figures = D.figures || {};
  D.diagrams = D.diagrams || {};
  D.models = D.models || {};
  D.source_materials = D.source_materials || {};
  D.tree = D.tree || [];
  D.book = D.book || "教材";
  D.scope = (D.scope && D.scope.length) ? D.scope : Object.keys(D.units);
  // A teaching snapshot can intentionally contain only its ungenerated-unit list.
  // Missing data.js must still fail visibly, so require that explicit report.
  var incompleteChapter = D.preview_only === false && Array.isArray(D.unavailable_units) &&
    D.unavailable_units.length > 0 && Array.isArray(D.problem_summary) && D.problem_summary.length > 0;
  D.data_ok = Object.keys(D.units).length > 0 || Object.keys(D.source_materials).length > 0 || incompleteChapter;
  var R = window.DSHRenderCore.createRenderer({
    marked: window.marked, katex: window.katex,
    concepts: D.concepts, figures: D.figures, diagrams: D.diagrams || {}, sourceAssets: D.source_assets || {},
    models: D.models || {},         /* 交互模型：只给规格，画法在 assets/models.js */
    numberRefs: D.number_refs || null   /* P-035：编号 → 哪些节的原文里有它（构建时算好） */
  });

  var el = {
    side: document.getElementById("sidebar"),
    main: document.getElementById("content"),
    title: document.getElementById("book-title"),
    tip: document.getElementById("tooltip"),
    drawer: document.getElementById("drawer"),
    drawerBody: document.getElementById("drawer-body")
  };

  // Reading windows never move the source DOM; navigation keeps its own return stack.
  var readerReturns = [];
  var readerPositions = new Map();
  var drawerStudyDispose = null;
  function mountReaderStudyScope(container, unitId) {
    var api = window.DSHStudyVisuals;
    return api && typeof api.mountReaderScope === 'function'
      ? api.mountReaderScope(container, {data:D, unitId:unitId}) : null;
  }
  function disposeDrawerStudy() {
    var dispose = drawerStudyDispose; drawerStudyDispose = null;
    if (typeof dispose === 'function') dispose();
  }
  function nativeReaderLinks(root) {
    if (!root) return [];
    return Array.from(root.querySelectorAll("a")).filter(function (link) {
      if (root.hasAttribute("data-reader-block")) return link.closest("[data-reader-block]") === root;
      var lookup = link.closest(".reader-inline-lookup, .explain-inline");
      return !lookup || lookup === root || lookup.contains(root);
    });
  }
  function readerOrigin(origin) {
    var anchor = origin.closest("[id]") || el.main;
    var block = origin.closest("[data-reader-block]");
    var path = [], node = origin;
    while (node && node !== anchor) {
      path.unshift(Array.prototype.indexOf.call(node.parentElement.children, node));
      node = node.parentElement;
    }
    var concept = (location.hash || "").match(/^#concept=([A-Za-z0-9_-]+)/);
    return {origin:origin, unitId:renderedUnitId, y:window.scrollY,
      anchorId:anchor.id, path:path,
      readerBlockId:block ? block.dataset.readerBlock : null,
      linkIndex:origin.tagName === "A" ? nativeReaderLinks(block || anchor).indexOf(origin) : -1,
      conceptId:el.drawerBody.contains(origin) && concept ? concept[1] : null};
  }
  function resolveReaderOrigin(entry) {
    if (entry.origin && entry.origin.isConnected) return entry.origin;
    var node = document.getElementById(entry.anchorId);
    if (entry.linkIndex >= 0) {
      if (entry.readerBlockId) node = Array.from(el.main.querySelectorAll("[data-reader-block]")).find(function (block) {
        return block.dataset.readerBlock === entry.readerBlockId;
      });
      return nativeReaderLinks(node)[entry.linkIndex];
    }
    (entry.path || []).forEach(function (index) { node = node && node.children[index]; });
    return node;
  }
  function updateReaderReturn() {
    var button = document.getElementById("reader-return");
    if (button) button.hidden = readerReturns.length === 0;
  }
  function highlightReaderSource(node) {
    if (!node) return;
    node.classList.add("reader-source-highlight");
    window.setTimeout(function () { node.classList.remove("reader-source-highlight"); }, 1700);
  }
  function returnReaderTo(index, restorePosition) {
    if (index < 0 || index >= readerReturns.length) return;
    var destination = readerReturns[index];
    while (readerReturns.length > index) readerReturns.pop();
    updateReaderReturn();
    if (restorePosition && destination.unitId && destination.unitId !== renderedUnitId && D.units[destination.unitId]) {
      readerPositions.set(destination.unitId, destination.y);
      history.replaceState(null, "", "#/" + destination.unitId);
      route();
      if (destination.conceptId) {
        history.replaceState(null, "", "#concept=" + destination.conceptId);
        openDrawer(destination.conceptId);
      }
    }
    var origin = resolveReaderOrigin(destination);
    if (restorePosition && origin) {
      var parent = origin.parentElement;
      while (parent && parent !== el.main) {
        if (parent.tagName === "DETAILS") parent.open = true;
        parent = parent.parentElement;
      }
      origin.focus({preventScroll:true});
      window.scrollTo({top:destination.y, behavior:"instant"});
      highlightReaderSource(origin);
    }
    updateReaderProgress();
  }
  function clearReaderReturns() {
    if (readerReturns.length) returnReaderTo(0, false);
  }
  function closeReaderToc() {
    el.side.classList.remove("is-open");
    var toggle = document.getElementById("reader-toc-toggle");
    if (toggle) toggle.setAttribute("aria-expanded", "false");
  }
  var readerPopups = new Map(), readerPopupSequence = 0, readerPopupDrag = null, readerTheoremSources = new Map();
  function theoremForAnchor(unitId, anchor) {
    var index = D.theorem_index;
    if (!index || !index.by_unit) return null;
    var references = (index.refs_by_unit || {})[unitId] || (D.units[unitId] || {}).theorem_refs || {};
    var ref = references[anchor], entry = ref && (index.by_unit[ref.unit_id] || {})[ref.concept_id];
    return entry && entry.name && entry.anchor && entry.reader_document &&
      Array.isArray(entry.reader_document.blocks) ? entry : null;
  }
  function scopedReaderTarget(scope, anchor) {
    var root = scope.sourceRoot;
    if (root.id === anchor) return root;
    return Array.from(root.querySelectorAll('[id]')).find(function (node) { return node.id === anchor; });
  }
  function readerLinkScope(origin) {
    var popup = origin.closest('.reader-popup'), entry = popup && readerPopups.get(popup.dataset.readerTarget);
    return entry && entry.scope || {unitId:renderedUnitId, sourceRoot:el.main};
  }
  function decorateTheoremLinks(root, unitId) {
    root.querySelectorAll('.reader-document a[href^="#reader-"]').forEach(function (link) {
      if (link.closest('.source-material, [data-reader-group="exercise_regions"]')) return;
      var entry = theoremForAnchor(unitId, link.getAttribute('href').slice(1));
      if (!entry) return;
      link.innerHTML = R.renderInline(entry.name);
      link.classList.add('reader-theorem-link'); link.classList.remove('reader-support-link');
      link.dataset.readerTheoremUnit = entry.unit_id; link.dataset.readerTheoremConcept = entry.concept_id;
      link.setAttribute('aria-description', '定理，在可拖动窗口查看条件、结论和完整证明');
    });
  }
  function prepareReaderTheorems(root, unitId) {
    if (!D.theorem_index) return;
    decorateTheoremLinks(root, unitId);
    var moved = new Set();
    root.querySelectorAll('a[data-reader-theorem-unit]').forEach(function (link) {
      var field = link.closest('[data-reader-pointer]');
      if (!field || field.dataset.readerPointer !== '/body_markdown') return;
      var anchor = link.getAttribute('href').slice(1), entry = theoremForAnchor(unitId, anchor);
      if (!entry || entry.unit_id !== unitId || anchor !== entry.anchor || moved.has(entry.concept_id)) return;
      var paragraph = link.closest('p'), target = scopedReaderTarget({sourceRoot:root}, anchor);
      if (!paragraph || paragraph.querySelectorAll('a').length !== 1 || paragraph.textContent.trim() !== link.textContent.trim() ||
          !target || target.dataset.readerGroup !== 'explainables' || target.contains(paragraph)) return;
      // Keep the actual marker and source blocks; only their presentation moves.
      paragraph.hidden = true; paragraph.classList.add('reader-theorem-marker');
      target.classList.remove('reader-support-item'); target.classList.add('reader-theorem-inline');
      target.dataset.readerReference = entry.name; target.dataset.readerSupportKind = '定理';
      // Reuse the existing frozen nodes and media mounts. Only proof fields move
      // into a closed disclosure; the theorem statement remains in the flow.
      var proofIds = new Set((entry.proof_blocks || []).map(function (b) { return b.block_id; }));
      var existingProof = target.querySelector(':scope > .reader-theorem-proof');
      var proof = existingProof || document.createElement('details'); proof.className = 'reader-theorem-proof';
      if (!existingProof) { var proofSummary = document.createElement('summary'); proofSummary.textContent = '展开完整证明'; proof.appendChild(proofSummary); }
      Array.from(target.querySelectorAll('[data-reader-block]')).forEach(function (node) {
        if (!existingProof && proofIds.has(node.dataset.readerBlock)) proof.appendChild(node);
      });
      if (proof.childElementCount > 1) target.appendChild(proof);
      if (target.tagName === 'DETAILS') {
        var inline = document.createElement('section');
        Array.from(target.attributes).forEach(function (attr) { if (attr.name !== 'open') inline.setAttribute(attr.name, attr.value); });
        Array.from(target.childNodes).forEach(function (node) { if (node.nodeName !== 'SUMMARY') inline.appendChild(node); });
        target.replaceWith(inline); target = inline;
      }
      paragraph.after(target); moved.add(entry.concept_id);
    });
  }
  function theoremReaderSource(entry) {
    var key = 'theorem:' + entry.unit_id + ':' + entry.concept_id, existing = readerTheoremSources.get(key);
    if (existing) return existing;
    var root = document.createElement('div'); root.innerHTML = R.theoremReaderHtml(entry);
    var scope = {unitId:entry.unit_id, sourceRoot:root};
    decorateTheoremLinks(root, entry.unit_id); decorateReaderSupportLinks(root, scope);
    var source = {key:key, target:scopedReaderTarget(scope, entry.anchor), scope:scope};
    readerTheoremSources.set(key, source); return source;
  }
  var readerTopics = [], readerSurfaceTimers = new WeakMap();
  function readerMotionEnabled() {
    return typeof window.matchMedia === "function" && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  function cancelReaderTransition(node) {
    var pending = readerSurfaceTimers.get(node);
    if (pending) { window.clearTimeout(pending.timer); window.cancelAnimationFrame(pending.frame); }
    readerSurfaceTimers.delete(node);
    node.classList.remove("is-entering", "is-closing");
  }
  function enterReaderSurface(node) {
    cancelReaderTransition(node); node.hidden = false;
    if (!readerMotionEnabled()) return;
    node.classList.add("is-entering");
    node.getBoundingClientRect();
    var pending = {frame:window.requestAnimationFrame(function () {
      node.classList.remove("is-entering"); readerSurfaceTimers.delete(node);
    })};
    readerSurfaceTimers.set(node, pending);
  }
  function exitReaderSurface(node, finish, immediate) {
    cancelReaderTransition(node);
    if (immediate || !readerMotionEnabled()) { finish(); return; }
    node.classList.add("is-closing");
    readerSurfaceTimers.set(node, {timer:window.setTimeout(function () {
      readerSurfaceTimers.delete(node); node.classList.remove("is-closing"); finish();
    }, 180)});
  }
  function focusReader(node) {
    if (!node || !node.isConnected) return;
    if (window.innerWidth <= 820 && el.side.contains(node) && !el.side.classList.contains('is-open'))
      node = document.getElementById('reader-toc-toggle') || el.main;
    if (node === el.main) node.setAttribute("tabindex", "-1");
    node.focus({preventScroll:true});
  }
  function raiseReaderPopup(entry) {
    if (!entry) return;
    readerPopups.delete(entry.key);
    readerPopups.set(entry.key, entry);
    var layer = 60;
    readerPopups.forEach(function (item) { item.popup.style.zIndex = String(layer++); });
  }
  function placeReaderPopup(popup, left, top) {
    var rect = popup.getBoundingClientRect();
    var width = rect.width || Math.min(480, window.innerWidth - 16);
    var height = rect.height || Math.min(360, window.innerHeight - 16);
    popup.style.left = Math.max(8, Math.min(left, window.innerWidth - width - 8)) + "px";
    popup.style.top = Math.max(8, Math.min(top, window.innerHeight - height - 8)) + "px";
  }
  function rememberReaderPopupOrigin(entry, origin) {
    entry.origins.add(origin);
    origin.setAttribute("aria-expanded", "true");
    var parent = origin.closest(".reader-popup");
    var parentEntry = parent && readerPopups.get(parent.dataset.readerTarget);
    if (parentEntry !== entry) entry.focusOrigins = Array.from(new Set([origin].concat(
      parentEntry ? parentEntry.focusOrigins : [], entry.focusOrigins)));
  }
  function closeReaderPopup(entry, restoreFocus, immediate) {
    if (!entry) return;
    if (readerPopupDrag && readerPopupDrag.entry === entry) readerPopupDrag.stop();
    entry.closing = true;
    entry.popup.querySelectorAll("video, audio").forEach(function (media) { if (!media.paused) media.pause(); });
    entry.origins.forEach(function (origin) { origin.setAttribute("aria-expanded", "false"); });
    exitReaderSurface(entry.popup, function () {
    entry.dispose.forEach(function (dispose) { dispose(); });
    // Native audio/video may be embedded alongside the managed player.
    entry.popup.querySelectorAll("video, audio").forEach(function (media) {
      if (media.hasAttribute("src") || media.querySelector("source[src]")) {
        media.pause(); media.removeAttribute("src");
        media.querySelectorAll("source, track").forEach(function (node) { node.removeAttribute("src"); });
        media.load();
      }
    });
    entry.popup.remove(); readerPopups.delete(entry.key);
    if (restoreFocus) focusReader(entry.focusOrigins.find(function (node) { return node.isConnected; }) || el.main);
    }, immediate);
  }
  function clearReaderPopups() {
    var focused = !!(document.activeElement && document.activeElement.closest(".reader-popup"));
    Array.from(readerPopups.values()).forEach(function (entry) { closeReaderPopup(entry, false, true); });
    readerTheoremSources.clear();
    if (focused) focusReader(el.main);
  }
  function copyReaderContent(target, prefix, entry) {
    // Numbered definitions own an explicit local section. Other reader anchors
    // own their authored concept/annotation/example group, never the whole page.
    var source = target.matches('.reader-support-item') ? target : target.closest("[data-reader-reference]") || target;
    if (source.matches("a, span")) source = source.closest("p, li, blockquote") || source;
    var copy = source.cloneNode(true), idMap = new Map();
    if (source.matches('.reader-support-item')) {
      var content = document.createElement('section');
      Array.from(copy.attributes).forEach(function (attr) { content.setAttribute(attr.name, attr.value); });
      Array.from(copy.childNodes).forEach(function (node) { if (node.nodeType !== 1 || node.tagName !== 'SUMMARY') content.appendChild(node); });
      content.classList.remove('reader-support-item'); content.classList.add('reader-support-copy');
      copy = content;
    }
    [copy].concat(Array.from(copy.querySelectorAll("[id]"))).forEach(function (node) {
      if (!node.id) return;
      var old = node.id; node.id = prefix + "-" + idMap.size; idMap.set(old, node.id);
    });
    [copy].concat(Array.from(copy.querySelectorAll("*"))).forEach(function (node) {
      ["for", "aria-labelledby", "aria-describedby", "aria-controls", "aria-owns", "headers", "list"].forEach(function (attr) {
        if (node.hasAttribute(attr)) node.setAttribute(attr, node.getAttribute(attr).split(/\s+/).map(function (id) { return idMap.get(id) || id; }).join(" "));
      });
      if (node.hasAttribute("name")) node.setAttribute("name", prefix + "-" + node.getAttribute("name"));
      node.removeAttribute("autofocus"); node.removeAttribute("autoplay");
      // Reader reference links intentionally retain original IDs: nested links
      // open/reuse the matching source window rather than jumping inside a copy.
      var href = node.getAttribute("href");
      if (href && href.charAt(0) === "#" && !/^#reader-/.test(href) && idMap.has(href.slice(1))) node.setAttribute("href", "#" + idMap.get(href.slice(1)));
    });
    var fields = source.querySelectorAll("input, textarea, select");
    copy.querySelectorAll("input, textarea, select").forEach(function (node, index) {
      node.value = fields[index].value;
      if (node.tagName === "INPUT") node.checked = fields[index].checked;
    });
    var closedDisclosures = '.reader-theorem-proof, .reader-example-disclosure, details[data-reader-group="examples"]';
    var closedRoot = copy.matches(closedDisclosures);
    if (copy.tagName === "DETAILS") copy.open = !closedRoot;
    copy.querySelectorAll(closedDisclosures).forEach(function (details) { details.open = false; });
    // Event listeners are not cloned. Build each animation from the existing
    // local payload and retain its disposer only for this window.
    var payload = (D.animations || {})[entry.scope ? entry.scope.unitId : renderedUnitId] || [];
    if (!Array.isArray(payload)) payload = [payload];
    copy.querySelectorAll("[data-animation-player]").forEach(function (player) {
      var data = payload.find(function (item) { return item.request_id === player.dataset.request; });
      if (!data || !window.DSHAnimations) {
        player.querySelectorAll("[data-animation-controls], button, select").forEach(function (node) { node.remove(); });
        player.querySelectorAll("video").forEach(function (node) { node.controls = true; });
        return;
      }
      var slot = document.createElement("div"); player.replaceWith(slot);
      entry.dispose.push(window.DSHAnimations.mountAnimations(slot, data));
    });
    // A copied canvas has no drawing or renderer. Reuse the visible frame as an
    // image, leaving the original interactive model and its event loop alone.
    var canvases = source.querySelectorAll("canvas");
    copy.querySelectorAll("canvas").forEach(function (canvas, index) {
      var picture = document.createElement("img"); picture.alt = "图示当前画面";
      try { picture.src = canvases[index].toDataURL(); canvas.replaceWith(picture); }
      catch (err) { canvas.replaceWith(document.createTextNode("图示请参照正文。")); }
    });
    copy.querySelectorAll(".model-ui").forEach(function (node) { node.remove(); });
    // The generic popup stylesheet hides a root support summary. A protected
    // disclosure keeps its own visible title so opening the window never reveals
    // its body and the reader can explicitly expand it afterwards.
    if (closedRoot) {
      var disclosureHost = document.createElement('section');
      disclosureHost.className = 'reader-disclosure-copy'; disclosureHost.appendChild(copy);
      return disclosureHost;
    }
    return copy;
  }
  function openReaderPopup(target, origin, options) {
    options = options || {};
    var key = options.key || target.id, entry = readerPopups.get(key);
    if (entry) {
      if (entry.closing) { entry.closing = false; enterReaderSurface(entry.popup); }
      rememberReaderPopupOrigin(entry, origin); raiseReaderPopup(entry); focusReader(entry.popup); return;
    }
    var popup = document.createElement("section"), prefix = "reader-popup-" + (++readerPopupSequence);
    popup.className = "reader-popup"; popup.id = prefix;
    popup.dataset.readerTarget = key;
    if (options.theorem) popup.classList.add('reader-theorem-popup');
    popup.setAttribute("role", "dialog"); popup.setAttribute("aria-modal", "false");
    popup.setAttribute("aria-labelledby", prefix + "-title"); popup.tabIndex = -1;
    var head = document.createElement("div"); head.className = "reader-popup-head";
    head.dataset.readerPopupDrag = ""; head.tabIndex = 0; head.title = "拖动标题栏，或用方向键移动；Esc 关闭";
    var title = document.createElement("span"); title.id = prefix + "-title";
    var label = origin.cloneNode(true); label.querySelectorAll(".katex-mathml, .reader-link-kind").forEach(function (node) { node.remove(); });
    var kind = target.getAttribute('data-reader-support-kind') || '', labelText = label.textContent.trim().replace(/^[·：:]\s*/, '');
    if (kind && labelText.indexOf(kind) !== 0) labelText = kind + (labelText ? ' · ' + labelText : '');
    if (options.theorem) title.innerHTML = R.renderInline(options.theorem.name);
    else title.textContent = target.getAttribute("data-reader-reference") || labelText || "阅读参考";
    var close = document.createElement("button"); close.type = "button";
    close.dataset.readerPopupClose = ""; close.textContent = "×"; close.setAttribute("aria-label", "关闭" + title.textContent);
    head.append(title, close); popup.appendChild(head);
    var body = document.createElement("div"); body.className = "reader-popup-body reader-document";
    entry = {key:key, target:target, popup:popup, origins:new Set(), focusOrigins:[], dispose:[], scope:options.scope,
      noNavigation:!!options.noNavigation};
    body.appendChild(copyReaderContent(target, prefix + "-copy", entry)); popup.appendChild(body);
    var actions = document.createElement("footer"); actions.className = "reader-popup-actions";
    actions.innerHTML = (entry.noNavigation ? '' : '<button type="button" data-reader-popup-locate>定位到原文</button>') +
      '<button type="button" data-reader-popup-return>' + (entry.noNavigation ? '继续阅读' : '回到引用处') + '</button>';
    popup.appendChild(actions);
    function fit() { placeReaderPopup(popup, parseFloat(popup.style.left) || 8, parseFloat(popup.style.top) || 8); }
    popup.addEventListener("load", fit, true); popup.addEventListener("toggle", fit, true);
    document.body.appendChild(popup); rememberReaderPopupOrigin(entry, origin); raiseReaderPopup(entry);
    var studyDispose = mountReaderStudyScope(body, entry.scope && entry.scope.unitId || renderedUnitId);
    if (typeof studyDispose === 'function') entry.dispose.push(studyDispose);
    var rect = origin.getBoundingClientRect(), offset = (readerPopups.size - 1) * 24;
    placeReaderPopup(popup, rect.right + 16 + offset, Math.max(88, rect.top) + offset);
    enterReaderSurface(popup);
    focusReader(popup);
  }
  document.addEventListener("pointerdown", function (event) {
    var popup = event.target.closest && event.target.closest(".reader-popup"); if (!popup) return;
    var entry = readerPopups.get(popup.dataset.readerTarget); raiseReaderPopup(entry);
    var head = event.target.closest("[data-reader-popup-drag]");
    if (!head || event.target.closest("button") || event.button !== 0 || event.isPrimary === false) return;
    event.preventDefault(); if (readerPopupDrag) readerPopupDrag.stop();
    var rect = popup.getBoundingClientRect(), dx = event.clientX - rect.left, dy = event.clientY - rect.top;
    var pointerId = event.pointerId;
    function move(ev) {
      if (ev.pointerId !== pointerId) return;
      ev.preventDefault(); placeReaderPopup(popup, ev.clientX - dx, ev.clientY - dy);
    }
    function stop(ev) {
      if (ev && ev.pointerId !== pointerId) return;
      document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", stop);
      document.removeEventListener("pointercancel", stop); head.removeEventListener("lostpointercapture", stop);
      readerPopupDrag = null; popup.classList.remove("is-dragging");
      if (head.hasPointerCapture && head.hasPointerCapture(pointerId)) head.releasePointerCapture(pointerId);
    }
    readerPopupDrag = {entry:entry, stop:stop}; popup.classList.add("is-dragging");
    document.addEventListener("pointermove", move, {passive:false}); document.addEventListener("pointerup", stop);
    document.addEventListener("pointercancel", stop); head.addEventListener("lostpointercapture", stop);
    if (head.setPointerCapture) head.setPointerCapture(pointerId);
  });
  document.addEventListener("focusin", function (event) {
    var popup = event.target.closest(".reader-popup");
    if (popup) raiseReaderPopup(readerPopups.get(popup.dataset.readerTarget));
  });
  document.addEventListener("keydown", function (event) {
    var head = event.target.closest && event.target.closest("[data-reader-popup-drag]");
    if (!head || event.target !== head || !/^Arrow(Left|Right|Up|Down)$/.test(event.key)) return;
    event.preventDefault(); var popup = head.closest(".reader-popup"), rect = popup.getBoundingClientRect();
    placeReaderPopup(popup, rect.left + (event.key === "ArrowLeft" ? -20 : event.key === "ArrowRight" ? 20 : 0),
      rect.top + (event.key === "ArrowUp" ? -20 : event.key === "ArrowDown" ? 20 : 0));
  });
  window.addEventListener("resize", function () {
    readerPopups.forEach(function (entry) { placeReaderPopup(entry.popup, parseFloat(entry.popup.style.left), parseFloat(entry.popup.style.top)); });
  });
  function jumpReaderTarget(target, origin) {
    var entry = readerOrigin(origin); entry.target = target;
    readerReturns.push(entry);
    var parent = target;
    while (parent && parent !== el.main) {
      if (parent.tagName === "DETAILS") parent.open = true;
      parent = parent.parentElement;
    }
    target.scrollIntoView({block:"nearest"});
    highlightReaderSource(target); updateReaderReturn(); closeReaderToc();
  }
  function decorateReaderSupportLinks(root, scope) {
    root.querySelectorAll('.reader-document a[href^="#reader-"]').forEach(function (link) {
      if (link.dataset.readerTheoremUnit) return;
      var target = scopedReaderTarget(scope || {sourceRoot:root}, link.getAttribute('href').slice(1));
      if (!target) return;
      var kind = target.getAttribute('data-reader-support-kind');
      if (!kind) return;
      link.classList.add('reader-support-link'); link.dataset.readerLinkKind = kind;
      link.setAttribute('aria-description', kind + '，在可拖动窗口查看');
      if (!link.querySelector('.reader-link-kind') && !new RegExp('^' + kind + '(?:[·：:\\s]|$)').test(link.textContent.trim())) {
        var badge = document.createElement('span'); badge.className = 'reader-link-kind'; badge.textContent = kind;
        badge.setAttribute('aria-hidden', 'true'); link.insertBefore(badge, link.firstChild);
      }
    });
  }
  function jumpReaderUnit(unitId, origin) {
    var entry = readerOrigin(origin);
    history.pushState(null, "", "#/" + unitId);
    route();
    readerReturns.push(entry); updateReaderReturn();
  }
  function updateReaderProgress() {
    var bar = document.querySelector("#reader-progress span");
    if (!bar) return;
    var range = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    bar.style.width = Math.max(0, Math.min(100, window.scrollY / range * 100)) + "%";
  }
  var readerNavigationTree = [];
  function revealReaderTocPath(unitId) {
    var link = Array.from(el.side.querySelectorAll('[data-unit]')).find(function (node) { return node.dataset.unit === unitId; });
    el.side.querySelectorAll('details.reader-toc-branch').forEach(function (branch) { branch.open = branch.classList.contains('reader-toc-chapter'); });
    for (var node = link && link.parentElement; node && node !== el.side; node = node.parentElement) {
      if (node.matches('details.reader-toc-branch, details.reader-toc-unit')) node.open = true;
    }
    // A chapter guide should expose its lessons, not only its own prose outline.
    var current = link && link.closest('[data-reader-toc-node]');
    if (current) current.querySelectorAll('details.reader-toc-branch').forEach(function (branch) { branch.open = true; });
  }
  function updateReaderChapterContext(unitId) {
    function pathTo(nodes, path) {
      for (var i = 0; i < nodes.length; i++) {
        var next = path.concat(nodes[i]);
        if (nodes[i].id === unitId) return next;
        var found = pathTo(nodes[i].children || [], next); if (found) return found;
      }
      return null;
    }
    var path = pathTo(readerNavigationTree, []) || [];
    var chapter = path.find(function (node) { return /^CH\d+$/.test(node.id); }) || (path.length > 1 ? path[0] : null);
    if (!chapter) chapter = readerNavigationTree.find(function (node) { return /^CH\d+$/.test(node.id); });
    var title = document.getElementById('reader-chapter-title'); if (!title) return;
    title.innerHTML = R.renderInline(chapter ? chapter.title : (D.display_title || D.book));
    if (chapter && chapter.unit && D.units[chapter.id]) {
      title.href = '#/' + chapter.id; title.title = '阅读本章导读';
      if (chapter.id === unitId) title.setAttribute('aria-current', 'page'); else title.removeAttribute('aria-current');
    } else { title.removeAttribute('href'); title.removeAttribute('title'); title.removeAttribute('aria-current'); }
  }
  function updateReaderNavigationContext(unitId) {
    // Navigation stops at real reading units. Body topics remain available to
    // search, reference windows and comparison, never as nested TOC entries.
    revealReaderTocPath(unitId);
    updateReaderChapterContext(unitId);
    updateReaderActiveTopic();
  }
  function updateReaderActiveTopic() {
    var current = readerTopics[0];
    readerTopics.forEach(function (topic) { if (topic.element.getBoundingClientRect().top <= 150) current = topic; });
    if (!current) return;
    var crumb = document.getElementById('reader-current-topic');
    if (crumb) {
      var titleNode = current.element.querySelector('.reader-topic-title');
      crumb.innerHTML = ' / ' + (titleNode ? readerSearchHtml(titleNode) : esc(current.title));
    }
  }

  var readerSearchRows = null, readerSearchFilter = '全部', readerSearchOrigin = null;
  function readerPlain(text) {
    return String(text || '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/[#*_`$]/g, '').replace(/\s+/g, ' ').trim();
  }
  function readerSearchText(markdown) {
    var copy = document.createElement('div'); copy.innerHTML = R.renderMarkdown(String(markdown || ''));
    copy.querySelectorAll('.katex-mathml, .reader-field-label').forEach(function (node) { node.remove(); });
    return copy.textContent.replace(/\s+/g, ' ').trim();
  }
  function readerSearchHtml(node) {
    var copy = node.cloneNode(true);
    copy.querySelectorAll('[hidden], .reader-media').forEach(function (n) { n.remove(); });
    copy.querySelectorAll('[id]').forEach(function (n) { n.removeAttribute('id'); });
    copy.querySelectorAll('a').forEach(function (n) { n.replaceWith.apply(n, Array.from(n.childNodes)); });
    return copy.innerHTML;
  }
  function readerSearchFragments(node) {
    return Array.from(node.querySelectorAll('p, li')).filter(function (n) { return !n.closest('.reader-media'); }).map(function (n) {
      return {text:n.textContent.toLowerCase(), html:readerSearchHtml(n)};
    });
  }
  function searchRows() {
    if (readerSearchRows) return readerSearchRows;
    readerSearchRows = [];
    Object.keys(D.units).forEach(function (unitId) {
      var unit = D.units[unitId], groups = new Map();
      var blocks = (unit.reader_document || {}).blocks || [];
      // Build the index with the same generic topic boundaries as the visible
      // reader; no chapter-specific map or inferred animation correspondence.
      var indexedBody = false;
      if (unit.reader_document && window.DSHReaderLayout) {
        var searchRenderer = window.DSHRenderCore.createRenderer({marked:window.marked, katex:window.katex,
          concepts:D.concepts, figures:D.figures, diagrams:D.diagrams, models:D.models, sourceAssets:D.source_assets || {}});
        var detached = document.createElement('div');
        detached.innerHTML = searchRenderer.readerDocumentHtml(unit.reader_document, 'body', unit.reader_media, unit.reader_layout, unit.teaching_presentation);
        prepareReaderTheorems(detached, unitId);
        window.DSHReaderLayout.enhance(detached, unit, unitId, {renderTitle: searchRenderer.renderInline}).forEach(function (topic) {
          var copy = topic.element.cloneNode(true);
          copy.querySelectorAll('.reader-media, [hidden]').forEach(function (node) { node.remove(); });
          var titleNode = copy.querySelector('.reader-topic-title');
          readerSearchRows.push({unitId:unitId, unitTitle:unit.title || unitId,
            title:titleNode ? titleNode.textContent.trim() : topic.title,
            titleHtml:titleNode ? readerSearchHtml(titleNode) : esc(topic.title), fragments:readerSearchFragments(copy),
            kind:{example:'例题',concept:'概念',property:'性质'}[topic.kind] || '正文',
            text:copy.textContent.replace(/\s+/g, ' ').trim(), target:topic.id});
          indexedBody = true;
        });
      }
      blocks.forEach(function (block) {
        if (block.group === 'title' || block.group === 'one_line') return;
        if (indexedBody && /body/.test(block.group)) return;
        var key = /\/(?:examples|explainables)\/\d+\/pitfalls\/\d+$/.test(block.json_pointer || '') ? block.json_pointer : block.group + '/' + block.owner_id;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(block);
      });
      if (!groups.size) groups.set('body', [{group:'body', raw_text:unit.body_markdown || unit.explanation_markdown || ''}]);
      groups.forEach(function (fields) {
        if (unit.reader_document && R.readerConceptRegistrationTarget(fields, unit.reader_document, unit.reader_layout)) return;
        var first = fields[0], titleField = fields.find(function (b) { return /\/(title|name|label|statement|prompt|statement_markdown)$/.test(b.json_pointer || ''); });
        var pitfall = /\/(?:examples|explainables)\/\d+\/pitfalls\/(\d+)$/.exec(first.json_pointer || '');
        var kind = pitfall ? '易错' : {examples:'例题',learning_checks:'自测',explainables:'解释',references:'概念'}[first.group] || '正文';
        if (first.group === 'annotations') kind = /直觉|易错|先修/.test(first.field_label || '') ? first.field_label : '解释';
        var title = pitfall ? '易错 · 第 ' + (Number(pitfall[1]) + 1) + ' 点' : readerSearchText(titleField ? titleField.markdown || titleField.raw_text : /body/.test(first.group) ? unit.title : first.field_label || unit.title);
        var text = readerSearchText(fields.map(function (b) { return b.markdown || b.raw_text || ''; }).join('\n\n'));
        var fragment = document.createElement('div');
        fragment.innerHTML = R.renderMarkdown(fields.map(function (b) { return b.markdown || b.raw_text || ''; }).join('\n\n'));
        readerSearchRows.push({unitId:unitId, unitTitle:unit.title || unitId, title:title,
          titleHtml:titleField ? R.renderInline(titleField.markdown || titleField.raw_text || '') : esc(title),
          fragments:readerSearchFragments(fragment), kind:kind, text:text, target:pitfall ? R.readerSupportTarget(first) : first.anchor || ''});
      });
    });
    return readerSearchRows;
  }
  function renderReaderSearch() {
    var input = document.getElementById('reader-search-input'), results = document.getElementById('reader-search-results');
    if (!input || !results) return;
    var query = readerPlain(input.value).toLowerCase(), rows = searchRows();
    var kinds = ['全部'].concat(Array.from(new Set(rows.map(function (r) { return r.kind; }))));
    document.getElementById('reader-search-filters').innerHTML = kinds.map(function (kind) {
      return '<button type="button" data-reader-search-filter="' + esc(kind) + '" aria-pressed="' + (kind === readerSearchFilter) + '">' + esc(kind) + '</button>';
    }).join('');
    var matches = rows.filter(function (r) {
      return (readerSearchFilter === '全部' || r.kind === readerSearchFilter) && (r.title + ' ' + r.unitTitle + ' ' + r.text).toLowerCase().indexOf(query) >= 0;
    });
    document.getElementById('reader-search-count').textContent = matches.length + ' 条结果';
    results.innerHTML = matches.map(function (row) {
      var fragments = row.fragments || [], snippet = fragments.find(function (item) { return item.text.indexOf(query) >= 0; }) || fragments[0];
      return '<button type="button" data-reader-search-unit="' + esc(row.unitId) + '" data-reader-search-target="' + esc(row.target) + '"><strong>' + (row.titleHtml || esc(row.title)) + '</strong><small>' + esc(row.unitTitle + ' · ' + row.kind) + '</small><div class="reader-search-excerpt">' + (snippet ? snippet.html : esc(row.text.slice(0, 130))) + '</div></button>';
    }).join('') || '<p class="reader-search-empty">没有找到匹配内容，试试其他词。</p>';
  }
  function openReaderSearch(origin) {
    var layer = document.getElementById('reader-search-layer'); if (!layer) return;
    readerSearchOrigin = origin || document.getElementById('reader-search-toggle');
    closeReaderToc();
    renderReaderSearch(); enterReaderSurface(layer);
    document.querySelectorAll('#reader-search-toggle, #reader-sidebar-search').forEach(function (button) { button.setAttribute('aria-expanded', 'true'); });
    document.getElementById('reader-search-input').focus({preventScroll:true});
  }
  function closeReaderSearch(restore, immediate) {
    var layer = document.getElementById('reader-search-layer'); if (!layer || layer.hidden) return;
    document.querySelectorAll('#reader-search-toggle, #reader-sidebar-search').forEach(function (button) { button.setAttribute('aria-expanded', 'false'); });
    exitReaderSurface(layer, function () { layer.hidden = true; if (restore) focusReader(readerSearchOrigin); }, immediate);
  }
  function closeReaderSettings(restore) {
    var panel = document.getElementById('reader-settings-popover'); if (!panel || panel.hidden) return;
    document.getElementById('reader-settings-toggle').setAttribute('aria-expanded', 'false');
    exitReaderSurface(panel, function () { panel.hidden = true; if (restore) focusReader(document.getElementById('reader-settings-toggle')); });
  }
  document.addEventListener('input', function (event) { if (event.target.id === 'reader-search-input') renderReaderSearch(); });
  document.addEventListener('keydown', function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault(); openReaderSearch(document.getElementById('reader-search-toggle')); return;
    }
    var layer = document.getElementById('reader-search-layer');
    if (!layer || layer.hidden || layer.classList.contains('is-closing')) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); closeReaderSearch(true); return; }
    if (event.key === 'Tab') {
      var nodes = Array.from(layer.querySelectorAll('.reader-search-panel button, .reader-search-panel input'));
      var first = nodes[0], last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });

  /* 当前挂着的动画播放器 dispose。换节/卸载时必须调用它 —— §T09 要求
     "换单元或销毁时 pause()、清除监听与 src"，留着就会让上一节的视频继续占资源。 */
  var animationInstances = new Map();
  var animationsLoading = false;

  function esc(text) { return R.escapeHtml(text); }

  /** 这个分组下面第一节有讲解的是谁（分组标题点一下就跳过去）。 */
  function firstUnit(node) {
    if (node.unit) return node.id;
    var children = node.children || [];
    for (var i = 0; i < children.length; i++) {
      var found = firstUnit(children[i]);
      if (found) return found;
    }
    return null;
  }

  /** 这个分组下面所有有讲解的节（用来判断当前在哪一节，好高亮）。 */
  function unitIds(node) {
    var ids = node.unit ? [node.id] : [];
    (node.children || []).forEach(function (child) {
      ids = ids.concat(unitIds(child));
    });
    return ids;
  }

  // 概念类型的中文名 —— 与后端 `extract_concepts.TYPE_LABELS` **同一套**
  // （2026-09-29：类型从 4 类扩到 7 类，页面原来写死"不是定理就显示定义"，会把
  //  定律/性质/命题/推论/公式全显示成"定义"）
  var TYPE_CN = {definition: "定义", theorem: "定理", law: "定律",
                 property: "性质", proposition: "命题", corollary: "推论",
                 formula: "公式"};

  function readerTocTree() {
    var source = D.tree.length ? D.tree : D.scope.filter(function (id) { return !!D.units[id]; }).map(function (id) {
      return {id:id, title:D.units[id].title, unit:true};
    });
    var nodes = source.map(function copy(node) {
      return Object.assign({}, node, {children:(node.children || []).map(copy)});
    });
    // Partial production snapshots may contain a flat list. Their canonical
    // unit IDs still encode the hierarchy; reuse it without inventing names.
    if (nodes.some(function (node) { return node.children.length; })) return nodes;
    var byId = new Map(), roots = [];
    nodes.forEach(function (node) { byId.set(node.id, node); });
    function ensure(id) {
      if (byId.has(id)) return byId.get(id);
      var parts = id.split('-'), last = parts[parts.length - 1], number = Number(last.replace(/^[A-Z]+/, ''));
      var node = {id:id, title:/^CH/.test(last) ? '第' + number + '章' : '第' + number + '节', unit:false, children:[]};
      if (parts.length > 1) node.anchor = parts.map(function (part) { return Number(part.replace(/^[A-Z]+/, '')); }).join('.');
      byId.set(id, node); return node;
    }
    var attached = new Set();
    function attach(node) {
      if (attached.has(node.id)) return;
      attached.add(node.id);
      if ((node.unit && /^CH\d+(?:-(?:S|SS)\d+)+$/.test(node.id)) || (!node.unit && /^CH\d+-S\d+$/.test(node.id))) {
        var parentId = node.id.slice(0, node.id.lastIndexOf('-'));
        var parent = ensure(parentId); attach(parent); parent.children.push(node);
      } else roots.push(node);
    }
    nodes.forEach(attach);
    return roots;
  }
  function readerTocLabel(node) {
    var anchor = node.anchor || '';
    if (!anchor && node.unit && /^CH\d+(?:-(?:S|SS)\d+)+$/.test(node.id))
      anchor = node.id.split('-').map(function (part) { return Number(part.replace(/^[A-Z]+/, '')); }).join('.');
    var title = String(node.title || '');
    if (anchor && title.indexOf(anchor + ' ') === 0) title = title.slice(anchor.length).trim();
    return (anchor ? "<span class='toc-anchor'>" + esc(anchor) + '</span>' : '') +
      "<span class='toc-title'>" + R.renderInline(title) + '</span>';
  }
  function readerOverviewToc(unitId) {
    function find(nodes) {
      for (var i = 0; i < nodes.length; i++) {
        if (nodes[i].id === unitId) return nodes[i];
        var found = find(nodes[i].children || []); if (found) return found;
      }
      return null;
    }
    var parent = find(readerNavigationTree);
    if (!parent || !parent.children.length) return '';
    function list(nodes) {
      return '<ul>' + nodes.map(function (node) {
        var target = node.material_id ? '#/materials/' + encodeURIComponent(node.material_id)
          : node.unit && D.units[node.id] ? '#/' + node.id : null;
        var label = readerTocLabel(node);
        return '<li>' + (target ? '<a href="' + esc(target) + '">' + label + '</a>'
          : '<span class="reader-overview-group">' + label + '</span>') +
          (node.children.length ? list(node.children) : '') + '</li>';
      }).join('') + '</ul>';
    }
    var title = /^CH\d+$/.test(unitId) ? '本章目录' : '本节目录';
    return '<nav class="reader-overview-toc" aria-label="' + title + '"><h2>' + title + '</h2>' +
      list(parent.children) + '</nav>';
  }
  function renderSidebar() {
    readerNavigationTree = readerTocTree();
    var chapters = readerNavigationTree.filter(function (node) { return /^CH\d+$/.test(node.id) && node.children.length; });
    var singleChapter = chapters.length === 1 && readerNavigationTree.every(function (node) { return node === chapters[0] || !!node.material_id; });
    var html = ["<nav class='toc reader-toc-tree" + (singleChapter ? ' reader-toc-single-chapter' : '') + "' aria-label='章节目录'>"];
    function walk(nodes, depth) {
      nodes.forEach(function (node) {
        var label = readerTocLabel(node), children = node.children || [], branch = children.length > 0;
        var contextChapter = singleChapter && depth === 0 && node === chapters[0];
        var extra = node.declared === false ? "<span class='toc-badge' title='书的目录未列此节'>目录未列</span>" : '';
        var target = node.material_id ? '#/materials/' + encodeURIComponent(node.material_id) : '#/' + node.id;
        var attributes = node.material_id ? " data-material='" + esc(node.material_id) + "'" : " data-unit='" + esc(node.id) + "'";
        var link = node.unit || node.material_id ? "<a class='toc-item depth-" + depth + "' href='" + esc(target) + "'" + attributes + '>' + label + extra + '</a>' :
          "<span class='toc-group depth-" + depth + "' data-group='" + esc(unitIds(node).join(',')) + "'>" + label + extra + '</span>';
        if (branch || node.unit) {
          html.push("<details class='" + (branch ? 'reader-toc-branch' : 'reader-toc-unit') + (contextChapter ? ' reader-toc-chapter' : '') + "' data-reader-toc-node='" + esc(node.id) + "'>");
          html.push("<summary class='reader-toc-summary'" + (contextChapter ? ' hidden' : '') + '>' + link + "<span class='reader-toc-chevron' aria-hidden='true'></span></summary>");
          html.push("<div class='" + (branch ? 'reader-toc-children' : 'reader-toc-unit-body') + "'>");
          if (branch) walk(children, depth + 1);
          html.push('</div></details>');
        } else html.push("<div class='reader-toc-leaf' data-reader-toc-node='" + esc(node.id) + "'>" + link + '</div>');
      });
    }
    walk(readerNavigationTree, 0);
    html.push('</nav>');
    // ── 目录里有、正文里没找到的条目（灰显，别装看不见）─────────────────────
    // 来源是目录层的对照表 `body.json`（`empty_nodes` = 目录有正文缺；`unconfirmed` = 标了不确定）
    var gaps = D.toc_gaps || [];
    if (gaps.length) {
      html.push("<div class='toc-gaps'><h4>目录里有、正文里没找到（" + gaps.length + "）</h4>");
      gaps.forEach(function (gap) {
        html.push("<div class='toc-gap' title='" + esc(gap.reason || "") + "'>" +
          "<span class='toc-anchor'>" + esc(gap.number || "") + "</span>" +
          // ⚠️ 2026-10-03：目录标题里可能有公式（实测《复变函数》目录有 `$\int_0^{2\pi}R(\cos\theta,…)d\theta$ 型积分`）
          //    逐字 escape 会让页面上出现裸露 `$` → 走 renderInline
          "<span class='toc-title'>" + R.renderInline(gap.title || "") + "</span>" +
          "<span class='toc-gap-why'>" + esc(gap.reason || "") + "</span></div>");
      });
      html.push("</div>");
    }
    var note = D.toc_note || {};
    if (note.undeclared_units) {
      html.push("<p class='toc-footnote'>骨架 = " + esc(note.skeleton || "书的目录") +
        "；另有 " + note.undeclared_units + " 处「目录未列、正文有」已标出。</p>");
    }
    var host = document.getElementById('reader-toc-content');
    if (!host) {
      // Older saved templates can load updated assets without losing navigation.
      el.side.innerHTML = '<div class="reader-toc-heading"><a class="reader-toc-context-link" id="reader-chapter-title"></a></div>' +
        '<button type="button" class="reader-toc-search" id="reader-sidebar-search" aria-controls="reader-search-layer" aria-expanded="false">查找概念、性质、例题</button>';
      host = document.createElement('div'); host.id = 'reader-toc-content'; el.side.appendChild(host);
    }
    host.innerHTML = html.join('');
  }

  /* ── 当前是哪一节：**只有一个来源**（P-019 的根治）────────────────────────
     用户报的现象：点一下概念，整节被重置（滚回顶部、展开的东西全没了）。
     根因是 `route()` 每次 hashchange 都**无条件重画整节**，而点概念会把 hash 改成
     `#concept=C-0014` —— 这个 hash 里没有节号，于是 `currentUnitId()` 返回 null，
     route 又退回"第一节"。两件事叠在一起：读者被踢回第一节 + 页面被重画。
     现在：① hash 里没节号时，用**最近看的那一节**（记住 lastUnitId）；
           ② 只有"节真的换了"才重画（renderedUnitId 是唯一判据）。 */
  var lastUnitId = null;         // 最近一次看的是哪一节
  var renderedUnitId = null;     // 现在**画在页面上**的是哪一节

  function currentUnitId() {
    var hash = decodeURIComponent(location.hash || "");
    if (!hash || hash === '#' || hash === '#/') return D.scope[0];
    var match = hash.match(/^#\/([A-Za-z0-9\-_]+)/);
    if (match) return match[1];
    return lastUnitId;           // `#concept=…` 这种 hash 不带节号 → 留在原地
  }

  var NOTE_KIND_LABEL = { note: "注", derive: "推导", pitfall: "易错", intuition: "直觉" };

  /** 详解栏（小清新 D 的"柔光侧栏详解"）。
   *
   * 栏里按**正文出现顺序**排两组卡片：先「公式与定理」（P-006），再「边注」（P-002）。
   * 每张卡片带 `data-card="<id>"`，和正文里的小圆点/胶囊**同号同名** ——
   * 点正文 → 卡片高亮；点卡片的「回到那句话」→ 跳回正文并展开。
   * 编号由渲染层在渲染正文时发（render-core 的 noteOrder/explainOrder），
   * 所以侧栏和正文永远对得上（教案里的顺序可能是乱的，实测 §2.1 就是 01→05→06→03…）。 */
  /* ── 右栏**跟随阅读位置**（2026-10-01，用户："改成 F 那套试试"）──────────────
     原来右栏是一份静止的列表：读正文时得自己在右栏里找"我正在看的那条"。
     现在按正文**当前视口**里出现的 [[EXPLAIN:…]] 反查：把右栏对应卡片高亮并滚进视野，
     读者眼睛不用来回找（F 演示里验证过的那套）。 */
  function followReading(article, rail) {
    if (!article || !rail || !window.IntersectionObserver) return;
    var blocks = article.querySelectorAll("p, li, section, figure");
    var watched = [];
    Array.prototype.forEach.call(blocks, function (node) {
      var ids = {};
      node.querySelectorAll("[data-explain]").forEach(function (chip) {
        ids[chip.getAttribute("data-explain")] = 1;
      });
      var list = Object.keys(ids);
      if (!list.length) return;
      node.setAttribute("data-explain-ids", list.join(","));
      watched.push(node);
    });
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var ids = String(entry.target.getAttribute("data-explain-ids") || "").split(",");
        var first = null;
        Array.prototype.forEach.call(rail.querySelectorAll(".rcard-explain"), function (card) {
          var on = ids.indexOf(card.getAttribute("data-card")) >= 0;
          card.classList.toggle("on", on);
          if (on && !first) first = card;
        });
        if (first && first.scrollIntoView) first.scrollIntoView({block: "nearest"});
      });
    }, {rootMargin: "-40% 0px -50% 0px", threshold: 0});
    watched.forEach(function (node) { obs.observe(node); });
  }

  /* ── 右栏＝**详情面板**（2026-10-01 用户："这种公式要么就和概念表一样，点击后侧边栏出现，
        有推导有解析"）────────────────────────────────────────────────────────
     改造前：右栏是一堆平铺的卡片 —— 点正文里的公式只是"滚到那张卡"，推导和解析挤在一起、没有主次。
     现在：右栏顶部是**一行索引**（类型徽标 + 名字，点击切换），下面是**详情区**；
     正文里点任意公式/定理/概念 → 右栏立刻换成它的详情：**推导（分步 + 每步依据）** 与
     **解析（它是什么 / 为什么在这里用 / 先看一个简单情形 / 配图）** 分成两块，主次分明。 */
  function explainDetailHtml(item) {
    if (!item) return "<div class='rbody empty'>点上面的索引，或直接点正文里的公式/定理。</div>";
    var out = ["<div class='rdetail'>",
      "<div class='rline'><span class='chip'>" + esc(TYPE_CN[item.kind] || "详解") + "</span>" +
      "<b>" + R.renderInline(item.name || item.id) + "</b></div>"];
    out.push("<h4 class='rsec'>解析</h4>");
    if (item.what) out.push("<div class='rbody'><b>它是什么：</b>" + R.renderInline(item.what) + "</div>");
    if (item.why) out.push("<div class='rbody why'><b>为什么在这里用：</b>" + R.renderInline(item.why) + "</div>");
    if (item.warmup) {
      out.push("<div class='rbody rwarm'><b>先用一个简单情形看明白：</b>" +
               R.renderInline(item.warmup) + "</div>");
    }
    if (item.kind === 'theorem') out.push('<details class="reader-theorem-proof"><summary>展开完整证明</summary>');
    if (item.markdown) out.push("<div class='markdown rbody'>" + R.renderMarkdown(item.markdown) + "</div>");
    var steps = item.walkthrough || item.steps || [];
    if (steps.length) {
      out.push("<h4 class='rsec'>推导</h4>");
      out.push("<table class='rwalk'><thead><tr><th>#</th><th>这一步在说什么</th><th>依据</th></tr></thead><tbody>");
      steps.forEach(function (step) {
        out.push("<tr><td>" + esc(step.step) + "</td><td>" +
                 R.renderInline(String(step.claim || step.markdown || "")) + "</td><td><span class='basis'>" +
                 R.renderInline(String(step.basis || "")) + "</span></td></tr>");
      });
      out.push("</tbody></table>");
    }
    if (item.kind === 'theorem') out.push('</details>');
    if (item.figure) {
      out.push("<h4 class='rsec'>配图</h4>");
      out.push(R.renderMarkdown("[[FIGURE:" + item.figure + "]]"));
    }
    out.push("</div>");
    return out.join("");
  }

  //: 详情面板要用的数据（渲染单元时填；点索引/正文时读）
  var EXPLAIN_DATA = { map: {}, order: [], current: null,
    //: `?ui=drawer` 直接弹抽屉；默认 peek（右栏换内容、正文不动）
    mode: (/[?&]ui=drawer/.test(location.search) ? "drawer"
           : (/[?&]ui=peek/.test(location.search) ? "peek" : "inline")) };

  function selectExplain(id) {
    var item = EXPLAIN_DATA.map[id];
    if (!item) return;
    EXPLAIN_DATA.current = id;
    var box = document.getElementById("explain-detail");
    if (box) {
      box.innerHTML = explainDetailHtml(item) +
        "<button class='rexpand' type='button' data-expand='" + esc(id) + "'>" +
        "展开为大抽屉 ↗</button>";
    }
    Array.prototype.forEach.call(document.querySelectorAll(".rindex .ritem"), function (node) {
      node.classList.toggle("on", node.getAttribute("data-explain") === id);
    });
    mountUnitAnimation(currentUnitId());
    // 正文里对应的胶囊也高亮一下，读者知道"侧栏讲的就是这个词"
    Array.prototype.forEach.call(document.querySelectorAll("[data-explain]"), function (chip) {
      chip.classList.toggle("on", chip.getAttribute("data-explain") === id);
    });
  }

  document.addEventListener("click", function (event) {
    var action = event.target.closest('button, a, summary');
    if (!event.target.closest('#reader-settings-popover, #reader-settings-toggle')) closeReaderSettings(false);
    if (action) {
      if (action.classList.contains('reader-skip-link')) { event.preventDefault(); focusReader(el.main); el.main.scrollIntoView(); return; }
      if (action.id === 'reader-search-toggle' || action.id === 'reader-sidebar-search') { openReaderSearch(action); return; }
      if (action.hasAttribute('data-reader-search-close')) { closeReaderSearch(true); return; }
      if (action.hasAttribute('data-reader-search-filter')) { readerSearchFilter = action.dataset.readerSearchFilter; renderReaderSearch(); return; }
      if (action.dataset.readerSearchUnit) {
        var destinationUnit = action.dataset.readerSearchUnit, destinationAnchor = action.dataset.readerSearchTarget;
        var from = readerSearchOrigin || document.getElementById('reader-search-toggle');
        closeReaderSearch(false, true);
        if (destinationUnit !== renderedUnitId) jumpReaderUnit(destinationUnit, from);
        else readerReturns.push(readerOrigin(from));
        var destination = document.getElementById(destinationAnchor) || el.main;
        for (var parent = destination; parent && parent !== el.main; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
        destination.scrollIntoView({block:'start'}); highlightReaderSource(destination); updateReaderReturn(); focusReader(from); return;
      }
      if (action.id === 'reader-compare-toggle') {
        var readingOnly = document.body.classList.toggle('reader-reading-only');
        action.textContent = readingOnly ? '开启对照' : '退出对照'; action.setAttribute('aria-pressed', String(!readingOnly)); return;
      }
      if (action.id === 'reader-settings-toggle') {
        var settings = document.getElementById('reader-settings-popover');
        if (!settings.hidden && !settings.classList.contains('is-closing')) closeReaderSettings(true);
        else { enterReaderSurface(settings); action.setAttribute('aria-expanded', 'true'); }
        return;
      }
      if (action.hasAttribute('data-reader-popup-return') || action.hasAttribute('data-reader-popup-locate')) {
        var popupEntry = readerPopups.get(action.closest('.reader-popup').dataset.readerTarget);
        var popupOrigin = popupEntry.focusOrigins.find(function (node) { return node.isConnected && !node.closest('.reader-popup'); }) || el.main;
        if (!popupEntry.noNavigation && action.hasAttribute('data-reader-popup-locate')) jumpReaderTarget(popupEntry.target, popupOrigin);
        else if (!popupEntry.noNavigation) { popupOrigin.scrollIntoView({block:'nearest'}); highlightReaderSource(popupOrigin); }
        closeReaderPopup(popupEntry, true); return;
      }
      if (action.id === "reader-return") {
        event.preventDefault(); returnReaderTo(readerReturns.length - 1, true); return;
      }
      if (action.hasAttribute("data-reader-popup-close")) {
        event.preventDefault(); closeReaderPopup(readerPopups.get(action.closest(".reader-popup").dataset.readerTarget), true); return;
      }
      if (action.matches('summary.reader-toc-summary')) {
        var tocUnit = action.parentElement;
        if (tocUnit.classList.contains('reader-toc-unit')) {
          var destinationLink = action.querySelector('[data-unit]');
          if (destinationLink) {
            event.preventDefault();
            if (destinationLink.dataset.unit !== renderedUnitId) {
              history.pushState(null, '', destinationLink.getAttribute('href')); route();
            }
            closeReaderToc();
          }
        }
        return;
      }
      if (action.id === "reader-toc-toggle") {
        var open = el.side.classList.toggle("is-open");
        action.setAttribute("aria-expanded", String(open)); return;
      }
      if (action.id === "reader-font-minus" || action.id === "reader-font-plus") {
        var size = parseFloat(window.getComputedStyle(document.body).getPropertyValue("--reader-font-size")) || 17;
        document.body.style.setProperty("--reader-font-size", Math.max(14, Math.min(22,
          size + (action.id === "reader-font-plus" ? 1 : -1))) + "px");
        updateReaderProgress(); return;
      }
      if (action.matches('#sidebar a, .pager a')) closeReaderToc();
      else if (action.tagName === "A" && action.closest("#content, #drawer-body, .reader-popup")) {
        var unitLink = (action.getAttribute("href") || "").match(/^#\/([A-Za-z0-9_-]+)$/);
        if (unitLink && D.units[unitLink[1]] && unitLink[1] !== currentUnitId()) {
          event.preventDefault(); jumpReaderUnit(unitLink[1], action); return;
        }
      }
    }
    var checkAction = event.target.closest('[data-check-action]');
    if (checkAction) {
      var question = checkAction.closest('[data-learning-check]');
      var action = checkAction.dataset.checkAction;
      var hints = question.querySelectorAll('[data-check-hint]');
      if (action === "hint") {
        if (checkAction.disabled) return;
        for (var h=0;h<hints.length;h++) {
          if (hints[h].hidden) { hints[h].hidden=false; break; }
        }
        checkAction.disabled = Array.from(hints).every(function (node) { return !node.hidden; });
        return;
      }
      var config = JSON.parse(question.dataset.checkConfig);
      var field = config.kind === "choice" || config.kind === "boolean" ?
        question.querySelector('[data-check-input]:checked') : question.querySelector('[data-check-input]');
      var value = field ? field.value : "";
      if (config.kind === "boolean" && field) value = value === "true";
      var result = R.gradeLearningCheck(config,value);
      var output = question.querySelector('[data-check-result]');
      output.dataset.status = result.status; output.textContent = result.feedback;
      question.querySelectorAll('[data-check-feedback]').forEach(function (node) {
        node.hidden = node.dataset.checkFeedback !== result.feedback_id;
      });
      if (result.status !== "invalid") {
        question.querySelector('[data-check-solution]').hidden = false;
        question.querySelector('[data-check-action="hint"]').disabled = Array.from(hints).every(function (node) { return !node.hidden; });
      }
      return;
    }
    var readerLink = event.target.closest('.reader-document a[href^="#reader-"]');
    if (readerLink) {
      event.preventDefault();
      var scope = readerLinkScope(readerLink), anchor = readerLink.getAttribute('href').slice(1);
      var theorem = readerLink.closest('.source-material, [data-reader-group="exercise_regions"]') ? null : theoremForAnchor(scope.unitId, anchor);
      if (theorem) {
        var source = theoremReaderSource(theorem);
        if (source.target) openReaderPopup(source.target, readerLink, {key:source.key, scope:source.scope, theorem:theorem, noNavigation:true});
      } else {
        var readerTarget = scopedReaderTarget(scope, anchor);
        if (readerTarget) openReaderPopup(readerTarget, readerLink, {scope:scope,
          key:scope.sourceRoot === el.main ? readerTarget.id : 'reference:' + scope.unitId + ':' + readerTarget.id,
          noNavigation:scope.sourceRoot !== el.main});
      }
      return;
    }

    var expand = event.target.closest ? event.target.closest("[data-expand]") : null;
    if (expand) { openExplainDrawer(expand.getAttribute("data-expand")); return; }
    var target = event.target.closest ? event.target.closest("[data-explain]") : null;
    if (!target) return;
    var id = target.getAttribute("data-explain");
    if (!id || !EXPLAIN_DATA.map[id]) return;
    event.preventDefault();
    // 默认 **Peek**（点击换右栏内容、**正文完全不动**，这是 Notion/Linear 那一类文档站的主流）；
    // 想直接弹抽屉（长内容、要专注）用 `?ui=drawer`。
    var mode = EXPLAIN_DATA.mode || "inline";
    if (mode === "drawer") { openExplainDrawer(id); return; }
    if (mode === "peek") { selectExplain(id); return; }
    // **inline（默认）**：就地展开 —— 解析/推导/配图直接出现在**这句话下面**。
    // 为什么改成默认：用户实测"右栏根本没在使用"，那藏到右栏就等于没做（2026-10-01）。
    var host = target.closest("p, li, section, figure") || target.parentNode;
    if (!host) return;
    var existing = host.nextElementSibling;
    if (existing && existing.classList && existing.classList.contains("explain-inline")) {
      var sameId = existing.getAttribute("data-for") === id;
      existing.remove();                       // 再点一次收起
      if (sameId) return;
    }
    var box = document.createElement("div");
    box.className = "explain-inline";
    box.setAttribute("data-for", id);
    box.innerHTML = explainDetailHtml(EXPLAIN_DATA.map[id]);
    host.parentNode.insertBefore(box, host.nextSibling);
    mountUnitAnimation(currentUnitId());
    Array.prototype.forEach.call(document.querySelectorAll("[data-explain]"), function (chip) {
      chip.classList.toggle("on", chip.getAttribute("data-explain") === id);
    });
  });

  /* 右栏**整块去掉**（2026-10-02 用户定案："右栏要不全删除，在原文的地方放小字链接"）——
     它原来把每条批注的**全文**再列一遍，和正文里的入口重复；读者会以为那是另一条，
     而且批注一多就变成卡片墙，还把正文挤窄。
     现在：批注只在**正文原位**留一个**小字链接**（见 render-core.js::noteHtml），
     点它就地展开；公式/定理本来就已改成就地展开。这个函数保留成空实现，
     是为了不打断调用方（`el.main.innerHTML = parts.join("")` 里那一处）。 */
  function railHtml(noteOrder, explainOrder, noteMap, explainMap) {
    return "";
  }
  function renderUnit(unitId) {
    clearReaderPopups(); clearReaderReturns(); closeReaderToc();
    var unit = D.units[unitId];
    if (!unit) {
      el.main.innerHTML = incompleteChapter
        ? "<section class='unit'><h1>本章未完成</h1><p>正文尚未生成，请查看页面顶部的未生成单元清单。</p></section>"
        : "<p class='empty'>这一节还没有产物。</p>";
      return;
    }
    var parts = [];
    parts.push("<article class='unit'>");
    parts.push("<header class='unit-head'>");
    if (unit.reader_document) {
      parts.push(R.readerDocumentHtml(unit.reader_document, "header", unit.reader_media, unit.reader_layout, unit.teaching_presentation));
    } else {
      parts.push("<div class='crumb'>" + esc(unit.crumb || "") + "</div>");
      parts.push("<h1><span class='anchor'>" + esc(unit.anchor || "") + "</span> " +
                 esc(unit.title || "") + "</h1>");
      if (unit.one_line) parts.push("<p class='one-line'>" + R.renderInline(unit.one_line) + "</p>");
    }
    parts.push("</header>");
    parts.push(readerOverviewToc(unitId));

    // 教学页只展示读者内容；质量状态、返修与缺失诊断保留在运行产物中。

    var isV2 = !!unit.body_markdown;
    /* P-002/P-006：把"指向上下文"交给渲染层。
       正文里点 [[NOTE:N-01]] 要**当场展开**那条批注，还要按**出现顺序**发编号
       （教案里的顺序不一定是 N-01、N-02…，实测 §2.1 就是 01→05→06→03→02→04）。
       数据仍在教案 JSON 里（G2），这里只是把两份索引递过去。 */
    var noteMap = {};
    (unit.annotations || []).forEach(function (a) { if (a && a.id) noteMap[a.id] = a; });
    var explainMap = {};
    (unit.explainables || []).forEach(function (x) { if (x && x.id) explainMap[x.id] = x; });
    R.beginUnit({ notes: noteMap, explain: explainMap, examples: unit.examples || [],
                 unitId: unitId });

    if (unit.reader_document) {
      parts.push(R.readerDocumentHtml(unit.reader_document, "body", unit.reader_media, unit.reader_layout, unit.teaching_presentation));
    } else {
    var main = [];
    main.push("<h2>" + (isV2 ? "教案" : "讲解") + "</h2>");
    if (isV2) {
      main.push("<p class='hint'>正文就是这一节的主要内容；原文在页面最下面（折叠，可对照）。</p>");
    }
    main.push("<div class='markdown body'>" +
              (isV2 ? R.renderBody(unit.body_markdown || "")
                    : R.renderMarkdown(unit.explanation_markdown || "")) + "</div>");
    // 例题放进正文那一栏（而不是整页最下面）：详解栏才能一路贴着它们，
    // 例题步骤里的「依据」也才有地方就地展开（P-006 第四条）。
    if (isV2 && unit.examples && unit.examples.length) {
      main.push(R.examplesHtml(unit.examples));
    }
    // 编号顺序要等正文与例题都渲染完才定得下来
    var noteOrder = R.noteOrder();
    var explainOrder = R.explainOrder();
    // 正文里没被引用到的批注也要能看见（否则等于悄悄丢了内容）
    (unit.annotations || []).forEach(function (a) {
      if (a && a.id && noteOrder.indexOf(a.id) < 0) noteOrder.push(a.id);
    });

    parts.push("<section class='explain'><div class='explain-grid'>");
    parts.push("<div class='explain-main'>" + main.join("") + "</div>");
    parts.push(railHtml(noteOrder, explainOrder, noteMap, explainMap));
    parts.push("</div></section>");

    }

    // ── 需要图但没有现成的：如实列出来（渲染层不自己画图）──────────────────
    var pendingFigures = unit.pending_figure_requests || unit.figure_requests || [];
    if (isV2 && pendingFigures.length) {
      parts.push("<details class='figure-requests'><summary>待补的图（" +
                 pendingFigures.length + " 处）</summary><ul>" +
                 // 这里的文字也可能带公式（"消去 $x_1$"），同样要用 renderInline
                 pendingFigures.map(function (x) {
                   return "<li>" + R.renderInline(x.what || "") + "　<span class='why'>" +
                          R.renderInline(x.why || "") + "</span></li>"; }).join("") + "</ul></details>");
    }

    if (!unit.reader_document && unit.concepts && unit.concepts.length) {
      parts.push("<section class='concept-list'><h2>本节概念</h2><div class='chips'>");
      unit.concepts.forEach(function (ref) {
        var concept = D.concepts[ref.id];
        if (!concept) return;
        parts.push("<button class='chip" + (ref.role === "introduced" ? " new" : "") +
          "' data-concept-id='" + esc(ref.id) + "'>" + R.renderInline(concept.name) +
          (ref.role === "introduced" ? "<span class='tag'>本节定义</span>" : "") + "</button>");
      });
      parts.push("</div></section>");
    }

    if (unit.original && unit.original.cleaned) {
      parts.push(isV2 ? "<details class='original-wrap'><summary>原文（对照用，点开）</summary>"
                      : "");
      parts.push("<section class='original'><h2>原文（对照用）</h2>");
      parts.push("<p class='hint'>公式已整理成可读的 LaTeX，正文一个字没改。");
      if (unit.original.raw) {
        parts.push("<label class='toggle'><input type='checkbox' id='show-raw'>" +
                   " 看扫描 OCR 的原始版（公式是坏的）</label>");
      }
      parts.push("</p>");
      parts.push("<div class='markdown cleaned'>" + R.renderOriginal(unit.original.cleaned) + "</div>");
      if (unit.original.raw) {
        parts.push("<div class='markdown raw hidden' id='raw-text'>" +
                   R.renderOriginal(unit.original.raw) + "</div>");
      }
      parts.push("</section>");
      if (isV2) parts.push("</details>");
    } else if (unit.original && unit.original.raw) {
      // 没有清洗产物时要分两种情形说：MinerU 版原文**本来就带 LaTeX**（不需要二次整理），
      // OCR 版原文才是"尚未整理公式"。写死在 MinerU 版上是错的（实测发现）。
      var rawHasMath = /\$[^$]+\$/.test(unit.original.raw);
      parts.push("<section class='original'><h2>原文</h2>" +
                 "<p class='hint'>" + (rawHasMath
                   ? "公式由解析层直接给出（MinerU），正文一个字没改。"
                   : "尚未整理公式。") + "</p>" +
                 "<div class='markdown'>" + R.renderOriginal(unit.original.raw) +
                 "</div></section>");
    }

    if (!unit.reader_document && unit.corrections && unit.corrections.length) {
      parts.push("<details class='corrections'><summary>这一节改掉了 " +
                 unit.corrections.length + " 处 OCR 公式错误（点开看）</summary>");
      parts.push("<table><thead><tr><th>原文</th><th>整理后</th><th>理由</th></tr></thead><tbody>");
      unit.corrections.forEach(function (row) {
        // "原文"和"理由"两列**故意字面显示**（原文就是要看 OCR 的坏样子），
        // 所以标成 .raw，页面的公式总检查会跳过它们；
        // "清洗后"那一列是给读者看的正确公式，必须渲染。
        // ⚠️ 2026-10-03 用户要求：**页面上不要出现 $ 源码**。这一列原来是逐字显示 OCR 原文
        // （"原文就是要看坏样子"），但读者看到的是 `$|\lambda||a|$` 这种东西。
        // 折中：保留"原文/整理后"的对照意义，但把公式记号**捋成人话**（plainMath）再显示。
        var _raw = R.plainMath ? R.plainMath(row["原文"] || "") : (row["原文"] || "");
        _raw = _raw.replace(/\$/g, "").replace(/\\/g, "");
        parts.push("<tr><td class='raw'><code>" + esc(_raw) + "</code></td>" +
                   "<td><code>" + R.renderInline(row["清洗"] || row["讲解"] || "") +
                   // "理由"列也是**给作者看的**：可能引用 LaTeX（"原文用黑斜体 $\mathbf b$"）
                   // → 同样捋成人话再显示（2026-10-03：实测这一列是页面上最后一处裸露 `$`）
                   "</code></td><td class='raw'>" + esc(
                     (R.plainMath ? R.plainMath(row["理由"] || "") : (row["理由"] || ""))
                       .replace(/\$/g, "").replace(/\\/g, "")) + "</td></tr>");
      });
      parts.push("</tbody></table></details>");
    }
    var index = D.scope.indexOf(unitId);
    var prev = index > 0 ? D.scope[index - 1] : null;
    var next = index >= 0 && index < D.scope.length - 1 ? D.scope[index + 1] : null;
    parts.push("<nav class='pager'>");
    parts.push(prev
      ? "<a class='prev' href='#/" + esc(prev) + "'>← " + R.renderInline(D.units[prev].title || '') + "</a>"
      : "<span class='disabled'>← 已是第一节</span>");
    parts.push(next
      ? "<a class='next' href='#/" + esc(next) + "'>" + R.renderInline(D.units[next].title || '') + " →</a>"
      : "<span class='disabled'>已是最后一节 →</span>");
    parts.push("</nav>");
    parts.push("</article>");
    /* 换节/重画之前先**销毁上一批画布模型**（P-025-1）。
       浏览器对 WebGL 上下文有数量上限（实测十几二十个就开始静默失败），
       以前只把 DOM 换掉、Three.js 的上下文和渲染循环全留着 → 读者多点几节，
       后面的模型就初始化不出来（这正是"一直显示正在加载…"的一个来源，P-025-2）。 */
    disposeModels();
    disposeAnimations();     // §T09：换节先停上一节的播放器（pause + 清 src + 摘监听）
    el.main.innerHTML = parts.join("");
    prepareReaderTheorems(el.main, unitId);
    readerTopics = window.DSHReaderLayout ? window.DSHReaderLayout.enhance(el.main, unit, unitId, {renderTitle: R.renderInline}) : [];
    decorateReaderSupportLinks(el.main);
    var unitCrumb = document.getElementById('reader-current-unit');
    if (unitCrumb) unitCrumb.innerHTML = R.renderInline(unit.title || '');
    updateReaderNavigationContext(unitId);
    // 接上「跟随阅读位置」（2026-10-01）：正文插进 DOM 之后才能观察它的段落
    followReading(el.main.querySelector("article") || el.main,
                  el.main.querySelector("aside.rail"));
    // 3D 图：正文插进 DOM 之后再初始化画布（canvas 需要有尺寸）
    initThreeFigures();
    /* P-002：把"就地批注"从句子中间**提到段落后**。
       批注卡片是块级的；留在 `<p>` 中间会把句末的标点挤到下一行 ——
       实测页面上出现孤零零的一个「。」，看着像坏了。
       提到段落后仍然是"就地"（紧贴那句话下面），指向关系一点没变。 */
    Array.prototype.forEach.call(el.main.querySelectorAll(".pop"), function (pop) {
      var host = pop.parentNode;
      while (host && host !== el.main &&
             host.tagName !== "P" && host.tagName !== "LI" &&
             !(host.classList && host.classList.contains("card"))) {
        host = host.parentNode;
      }
      if (!host || host === el.main) return;
      if (host.tagName === "P" && host.parentNode) host.parentNode.insertBefore(pop, host.nextSibling);
      else if (host.tagName === "LI" || host.classList.contains("card")) host.appendChild(pop);
    });
    bindContent();
    // 交互模型：规格 → 画布/分步播放（渲染层的事，见 assets/models.js）
    if (window.Models && window.Models.mountAll) {
      try { window.Models.mountAll(el.main, D.models || {}); }
      catch (err) { if (window.console) console.warn("[模型] 挂载失败：" + err.message); }
    }
    // 教学动画（§T09）：数据在 D.animations[unitId]，播放器在 assets/animations.js。
    // **只读本地 manifest**：播放器不发任何请求（连管理 API 都不认识）。
    mountUnitAnimation(unitId);
    el.main.scrollTop = 0;
    window.scrollTo(0, readerPositions.get(unitId) || 0);
    updateReaderProgress();
  }

  /** 把当前这一节的动画挂上去；换节/销毁时先 dispose（pause + 清 src + 摘监听）。
   *
   *  播放器脚本**按需加载**（与 3D 运行时、MathBox 同一个先例：用不到就别塞给读者）。
   *  模板里没有 <script src="assets/animations.js">，也不打算为它加一条 ——
   *  "有动画才拷、拷了才加载"这件事写在一处（这里），构建产物与页面行为不会走岔。
   */
  function mountUnitAnimation(unitId) {
    if((D.units[unitId]||{}).study_lesson)return; // exact teaching markers own these players
    if (currentUnitId() !== unitId) return;
    var payload = (D.animations || {})[unitId];
    if (!payload) { disposeUnitAnimations(unitId); return; }
    ensureAnimations(function () {
      mountUnitAnimation(currentUnitId());
    });
    if (!(window.DSHAnimations && window.DSHAnimations.mountAnimations)) return;
    reconcileUnitAnimations(unitId, el.main, Array.isArray(payload) ? payload : [payload]);
  }

  function reconcileUnitAnimations(unitId, root, payloads) {
    var host = root.querySelector("[data-animation-host]") || root.querySelector(".unit");
    if (!host) return;
    var wanted = new Set();
    payloads.forEach(function (item) {
      var key = unitId + "/" + item.request_id;
      if (wanted.has(key)) throw new Error("重复动画请求 " + key);
      wanted.add(key);
      var target = host;
      if (item.placement) {
        target = Array.from(root.querySelectorAll('[data-media-anchor]')).find(function (node) {
          // Current ReaderLayout already supplied the slot; its placement has no legacy status.
          return (D.units[unitId].reader_document || item.placement.status === 'resolved') &&
            node.getAttribute('data-media-anchor') === item.placement.anchor_id;
        });
        if (!target && !D.units[unitId].reader_document && el.drawerBody && el.drawerBody.dataset.unitId === unitId) {
          target = Array.from(el.drawerBody.querySelectorAll('[data-media-anchor]')).find(function (node) {
            return item.placement.status === 'resolved' && node.getAttribute('data-media-anchor') === item.placement.anchor_id;
          });
        }
      }
      var signature = JSON.stringify([item.resources, item.steps, item.placement, item.base, item.status, item.missing]);
      var previous = animationInstances.get(key);
      if (previous && previous.target === target && previous.signature === signature && target && target.isConnected) return;
      if (previous) { previous.dispose(); animationInstances.delete(key); }
      if (!target) {
        // v4 has exact slots even inside collapsed details; absence is an error.
        if (!((D.units[unitId] || {}).reader_document) && /^\/(explainables|annotations)\//.test((item.placement || {}).target || '')) return;
        var note = document.createElement('p'); note.className = 'missing-ref';
        note.dataset.missingAnimation = key;
        note.textContent = '动画 ' + item.request_id + ' 的教学位置未能定位，请查看配图位置清单。';
        host.appendChild(note);
        animationInstances.set(key, {unitId:unitId,target:host,signature:signature,dispose:function(){note.remove();}});
        return;
      }
      animationInstances.set(key, {unitId:unitId,target:target,signature:signature,
        dispose:window.DSHAnimations.mountAnimations(target,item)});
    });
    animationInstances.forEach(function (entry,key) {
      if (entry.unitId === unitId && !wanted.has(key)) { entry.dispose(); animationInstances.delete(key); }
    });
  }

  function disposeUnitAnimations(unitId) {
    animationInstances.forEach(function (entry,key) {
      if (entry.unitId === unitId) { entry.dispose(); animationInstances.delete(key); }
    });
  }

  /** 按需把 assets/animations.js 加载进来；加载过一次就不再加载。 */
  function ensureAnimations(retry) {
    if (window.DSHAnimations && window.DSHAnimations.mountAnimations) return;
    if (!animationsLoading) {
      animationsLoading = true;
      var tag = document.createElement("script");
      tag.src = "assets/animations.js";
      tag.async = false;          /* 经典脚本 + async=false：按插入顺序执行 */
      tag.onload = function () { animationsLoading = false; if (retry) retry(); };
      tag.onerror = function () {
        animationsLoading = false;
        var host = el.main.querySelector("[data-animation-host]") || el.main.querySelector(".unit");
        if (!host) return;
        var note = document.createElement("p");
        note.className = "animation-error";
        note.setAttribute("role", "alert");
        note.textContent = "这一节有动画，但播放器脚本（assets/animations.js）没能加载。";
        host.appendChild(note);
      };
      document.head.appendChild(tag);
    }
  }

  /** 换节或销毁时把上一节的播放器收干净（§T09：不许留着监听与 src）。 */
  function disposeAnimations() {
    animationInstances.forEach(function (entry) { entry.dispose(); });
    animationInstances.clear();
  }

  /** 把当前页面上挂着的模型全部销毁（由 models.js 决定怎么销毁，界面层不猜）。 */
  function disposeModels() {
    if (!(window.Models && window.Models.disposeAll)) return;
    try { window.Models.disposeAll(el.main); }
    catch (err) { if (window.console) console.warn("[模型] 销毁失败：" + err.message); }
  }

  function bindContent() {
    var toggle = document.getElementById("show-raw");
    if (toggle) {
      toggle.addEventListener("change", function () {
        var raw = document.getElementById("raw-text");
        if (raw) raw.classList.toggle("hidden", !toggle.checked);
      });
    }
  }

  // ── 概念：悬停出简介，点击出抽屉 ────────────────────────────────────
  function conceptTip(event) {
    var link = event.target.closest("[data-concept-id]");
    if (!link) { hideTip(); return; }
    var concept = D.concepts[link.getAttribute("data-concept-id")];
    if (!concept) return;
    el.tip.innerHTML = "<strong>" + R.renderInline(concept.name) + "</strong><br>" +
                       R.renderInline(concept.brief || "（没有简介）");
    el.tip.classList.remove("hidden");
    var box = link.getBoundingClientRect();
    var top = box.bottom + window.scrollY + 8;
    var left = Math.min(box.left + window.scrollX, window.innerWidth - el.tip.offsetWidth - 12);
    el.tip.style.top = top + "px";
    el.tip.style.left = Math.max(8, left) + "px";
  }

  function hideTip() { el.tip.classList.add("hidden"); }

  function openDrawer(conceptId) {
    var concept = D.concepts[conceptId];
    if (!concept) return;
    var noteHtml = concept.name_note
      ? "<p class='concept-note'>" + R.renderInline(concept.name_note) + "</p>" : "";
    // ⚠️ 类型标签不能盲加：概念名本身就常以类型结尾（实测 "欧姆定律" + "定律" → 页面上印成
    // "欧姆定律定律"）。名字里已经含了这个类型词就不再加徽标。
    var _tn = TYPE_CN[concept.type] || "概念";
    var _namePlain = String(concept.name || "");
    var _typeTag = _namePlain.indexOf(_tn) >= 0
      ? "" : "<span class='type'>" + esc(_tn) + "</span>";
    var parts = ["<h2>" + R.renderInline(concept.name) + _typeTag + "</h2>"];
    if (concept.explanation) {
      parts.push("<h3>通俗解释</h3><div class='markdown'>" + R.renderMarkdown(concept.explanation) + "</div>");
    } else {
      // 解释由教案阶段产出（抽取只登记概念）—— 还没跑到那一节时**明说**，别给个空白抽屉
      parts.push("<p class='concept-note'>这一条的通俗解释随它所在那一节的讲解一起生成。</p>");
    }
    if (concept.formal) {
      // formal 里几乎一定有公式（"定义的简单概念"尤其如此）→ 必须走公式渲染
      parts.push("<h3>教材原文表述</h3><blockquote>" +
                 R.renderInline(concept.formal) + "</blockquote>");
    }
    // 这个概念配的图（由图形登记表的 concepts 字段反查得到）
    var figs = (D.concept_figures || {})[conceptId] || [];
    if (figs.length) {
      parts.push("<h3>看图</h3>");
      figs.forEach(function (figureId) {
        parts.push(R.renderFigure(figureId));
      });
    }
    if (concept.occurrences && concept.occurrences.length) {
      parts.push("<h3>出现在</h3><ul class='occ'>");
      concept.occurrences.forEach(function (occ) {
        var label = (occ.anchor ? occ.anchor + " " : "") + (occ.title || "");
        if (D.units[occ.unit_id]) {
          parts.push("<li><a href='#/" + esc(occ.unit_id) + "'>" + esc(label) + "</a></li>");
        } else {
          parts.push("<li>" + esc(label) + "（不在本次样张范围内）</li>");
        }
      });
      parts.push("</ul>");
    }
    if (noteHtml) parts.splice(1, 0, noteHtml);
    disposeDrawerStudy();
    el.drawerBody.innerHTML = parts.join("");
    el.drawerBody.dataset.unitId = currentUnitId();
    mountUnitAnimation(currentUnitId());
    initThreeFigures();          // 抽屉里也可能有 3D 图（实测漏在这里：正文渲染时抽屉还没开）
    el.drawer.classList.add("open");
    el.drawer.setAttribute("aria-hidden", "false");
    drawerStudyDispose = mountReaderStudyScope(el.drawerBody, currentUnitId());
  }

  /* 公式 / 定理 / 概念的**统一抽屉**（2026-10-01 用户："做成概念那样点击显示的"）——
     和 `openDrawer`（概念）用**同一套外观**：标题 + 类型徽标，然后分块。
     分块顺序按读者的疑问来：**解析**（它是什么 / 为什么在这里用 / 先看一个简单情形）
     → **推导**（分步表，每步带依据）→ **配图**（有图才显示）。 */
  function openExplainDrawer(id) {
    var item = EXPLAIN_DATA.map[id];
    if (!item) return;
    var _tn2 = TYPE_CN[item.kind] || "详解";
    var _nm2 = String(item.name || id);
    var _tag2 = _nm2.indexOf(_tn2) >= 0 ? "" : "<span class='type'>" + esc(_tn2) + "</span>";
    var parts = ["<h2>" + R.renderInline(item.name || id) + _tag2 + "</h2>"];
    parts.push("<h3>解析</h3>");
    if (item.what) parts.push("<p><b>它是什么：</b>" + R.renderInline(item.what) + "</p>");
    if (item.why) parts.push("<p><b>为什么在这里用：</b>" + R.renderInline(item.why) + "</p>");
    if (item.warmup) {
      parts.push("<p class='rwarm'><b>先用一个简单情形看明白：</b>" + R.renderInline(item.warmup) + "</p>");
    }
    if (item.kind === 'theorem') parts.push('<details class="reader-theorem-proof"><summary>展开完整证明</summary>');
    if (item.markdown) parts.push("<div class='markdown'>" + R.renderMarkdown(item.markdown) + "</div>");
    var steps = item.walkthrough || item.steps || [];
    if (steps.length) {
      parts.push("<h3>推导</h3>");
      parts.push("<table class='rwalk'><thead><tr><th>#</th><th>这一步在说什么</th>" +
                 "<th>依据</th></tr></thead><tbody>");
      steps.forEach(function (step) {
        parts.push("<tr><td>" + esc(step.step) + "</td><td>" + R.renderInline(String(step.claim || step.markdown || "")) +
                   "</td><td><span class='basis'>" + R.renderInline(String(step.basis || "")) + "</span></td></tr>");
      });
      parts.push("</tbody></table>");
    }
    if (item.kind === 'theorem') parts.push('</details>');
    if (item.figure) {
      parts.push("<h3>配图</h3>");
      parts.push(R.renderFigure(item.figure));
    }
    disposeDrawerStudy();
    el.drawerBody.innerHTML = parts.join("");
    el.drawerBody.dataset.unitId = currentUnitId();
    mountUnitAnimation(currentUnitId());
    initThreeFigures();          // 抽屉里也可能有 3D 图（实测漏在这里：正文渲染时抽屉还没开）
    el.drawer.classList.add("open");
    el.drawer.setAttribute("aria-hidden", "false");
    drawerStudyDispose = mountReaderStudyScope(el.drawerBody, currentUnitId());
    // 索引里把当前这条点亮（右栏还在，读者知道位置）
    Array.prototype.forEach.call(document.querySelectorAll(".rindex .ritem"), function (node) {
      node.classList.toggle("on", node.getAttribute("data-explain") === id);
    });
  }

  function closeDrawer() {
    disposeDrawerStudy();
    el.drawer.classList.remove("open");
    el.drawer.setAttribute("aria-hidden", "true");
  }

  function route() {
    var materialRoute = (location.hash || '').match(/^#\/materials\/(.+)$/);
    var materialId = materialRoute ? decodeURIComponent(materialRoute[1]) : null;
    if (!D.scope.length && !materialId) materialId = Object.keys(D.source_materials)[0];
    if (materialId && D.source_materials[materialId]) {
      document.documentElement.removeAttribute('data-reader-unit');
      if (renderedUnitId !== 'material:'+materialId) {
        if (renderedUnitId) readerPositions.set(renderedUnitId, window.scrollY);
        clearReaderPopups(); clearReaderReturns(); closeReaderToc();
        disposeModels(); disposeAnimations(); closeDrawer();
        el.main.innerHTML = R.renderSourceMaterial(D.source_materials[materialId]);
        readerTopics = [];
        var materialCrumb = document.getElementById('reader-current-unit'), topicCrumb = document.getElementById('reader-current-topic');
        if (materialCrumb) materialCrumb.textContent = D.source_materials[materialId].title || '原书资料';
        if (topicCrumb) topicCrumb.textContent = '';
        updateReaderNavigationContext('material-' + materialId); updateReaderProgress();
        renderedUnitId = 'material:'+materialId;
      }
      Array.prototype.forEach.call(document.querySelectorAll('.toc-item'), function(item) {
        var active = item.getAttribute('data-material') === materialId;
        item.classList.toggle('active', active);
        if (active) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
      });
      return;
    }
    var conceptMatch = decodeURIComponent(location.hash || "").match(/^#concept=([A-Za-z0-9\-_]+)/);
    // 用 #concept= 链接直接打开时，背后也得把当前这一节渲染出来，
    // 否则分享出去的链接打开是一片空白（以前就是这个毛病）。
    if (conceptMatch) openDrawer(conceptMatch[1]);
    else closeDrawer();

    var unitId = currentUnitId();
    if (!unitId || !D.units[unitId]) unitId = D.scope[0];
    lastUnitId = unitId;
    if (unitId !== renderedUnitId) {          // ← 只有真换了节才重画（点概念不再重置这一节）
      if (renderedUnitId) readerPositions.set(renderedUnitId, window.scrollY);
      renderUnit(unitId);
      renderedUnitId = unitId;
    }
    if (document.documentElement) document.documentElement.setAttribute("data-reader-unit", unitId || "");

    Array.prototype.forEach.call(document.querySelectorAll(".toc-item"), function (item) {
      var active = item.getAttribute("data-unit") === unitId;
      item.classList.toggle("active", active);
      if (active) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-group]"), function (item) {
      var contains = (item.getAttribute("data-group") || "").split(",");
      item.classList.toggle("ancestor", contains.indexOf(unitId) >= 0);
    });
    if (/^#?\/?$/.test(location.hash || '')) {
      window.scrollTo(0, 0); el.side.scrollTop = 0;
      updateReaderProgress();
    }
  }

  // ── P-002/P-006：正文编号 ↔ 就地批注 ↔ 详解栏卡片，三处互相高亮 ──────
  function popFor(kind, id) {
    var attr = kind === "note" ? "data-note" : kind === "explain" ? "data-explain" : "data-basis";
    return el.main.querySelector(".pop[" + attr + "='" + id + "']");
  }
  function setCardOn(id, on) {
    Array.prototype.forEach.call(el.main.querySelectorAll(".rcard[data-card]"), function (card) {
      if (card.getAttribute("data-card") === id) card.classList.toggle("is-on", on);
    });
  }
  function togglePoint(trigger, kind, id) {
    var pop = popFor(kind, id);
    if (!pop) return;
    ensurePopHead(pop);            // 打开前补上「拖动手柄 + ×」（三种弹层通用）
    applySavedPopPos(pop);         // 上次拖到哪儿，这次还去哪儿
    var open = pop.hidden;
    pop.hidden = !open;
    if (open) mountUnitAnimation(currentUnitId());
    trigger.setAttribute("aria-expanded", String(open));
    trigger.classList.toggle("is-on", open);
    if (kind !== "basis") setCardOn(id, open);
  }
  /* ── P-035：正文里点「(2.1)」→ 跳到原文对照里的那个 (2.1) ───────────────────
     用户在 2026-09-25 定案：跳到**本页"原文对照"里**的编号（不跳教材的 2.1 节）。
     三件事都要做到，少一件读者就会觉得"点了没用"：
       ① 目标在**别的节**时先切过去（编号可能定义在上一节的原文里）；
       ② 原文区是**折叠**的（`<details>`）→ 要替读者点开；
       ③ 滚到那一处并**闪一下**，否则读者不知道跳到哪了。 */
  function flashNumber(number) {
    var target = el.main.querySelector("#" + R.numAnchorId(number));
    if (!target) return false;
    var fold = target.closest("details");
    if (fold) fold.open = true;                      // ② 折叠的原文区替读者展开
    var unitAtRequest = renderedUnitId;
    var scroll = function () {
      if (!target.isConnected || renderedUnitId !== unitAtRequest) return;
      target.scrollIntoView({ block: "center", behavior: "smooth" });
    };
    scroll();
    /* ③ 再校一次位置：目标后面的内容**还在长**（交互模型的画布是异步起来的、
       KaTeX 的行高也要等字体）。实测：跨节跳转时第一次滚到位又被撑出去了，
       读者看到的就是"点了没反应"（自检里 `目标在视口里: false`）。 */
    window.setTimeout(scroll, 450);
    target.classList.remove("is-flash");
    void target.offsetWidth;                         // 重启动画（连点两次也要闪）
    target.classList.add("is-flash");
    window.setTimeout(function () { target.classList.remove("is-flash"); }, 1800);
    return true;
  }
  function gotoNumber(link) {
    var number = link.getAttribute("data-num");
    var unit = link.getAttribute("data-num-unit");
    if (unit && unit !== currentUnitId()) {          // ① 跨节
      jumpReaderUnit(unit, link);
      flashNumber(number);
      return;
    }
    readerReturns.push(readerOrigin(link)); updateReaderReturn();
    flashNumber(number);
  }

  function backToText(id) {
    var target = el.main.querySelector(".mk[data-note='" + id + "'], .fchip[data-explain='" + id + "']");
    if (!target) return;
    var isNote = target.classList.contains("mk");
    var pop = popFor(isNote ? "note" : "explain", id);
    if (pop) pop.hidden = false;
    target.classList.add("is-on");
    setCardOn(id, true);
    target.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  document.addEventListener("click", function (event) {
    var numref = event.target.closest("a.numref[data-num]");
    if (numref) { event.preventDefault(); gotoNumber(numref); return; }
    var point = event.target.closest(".mk[data-note]");
    if (point) { togglePoint(point, "note", point.getAttribute("data-note")); return; }
    var fchip = event.target.closest(".fchip[data-explain]");
    if (fchip) { togglePoint(fchip, "explain", fchip.getAttribute("data-explain")); return; }
    var basis = event.target.closest(".basis[data-basis]");
    if (basis) { togglePoint(basis, "basis", basis.getAttribute("data-basis")); return; }
    var back = event.target.closest("[data-back]");
    if (back) { backToText(back.getAttribute("data-back")); return; }
    var card = event.target.closest(".rcard[data-card]");
    if (card) { backToText(card.getAttribute("data-card")); return; }
    var link = event.target.closest("[data-concept-id]");
    if (link) {
      event.preventDefault();
      var id = link.getAttribute("data-concept-id");
      if (location.hash === "#concept=" + id) openDrawer(id);
      else location.hash = "#concept=" + id;
      hideTip();
      return;
    }
    if (event.target.closest("#drawer-close") || event.target.classList.contains("drawer-backdrop")) {
      closeDrawer();
      if (location.hash.indexOf("#concept=") === 0) location.hash = "#/" + (currentUnitId() || "");
    }
  });
  document.addEventListener("mouseover", conceptTip);
  document.addEventListener("mouseout", function (event) {
    if (event.target.closest && event.target.closest("[data-concept-id]")) hideTip();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      var settings = document.getElementById('reader-settings-popover');
      if (settings && !settings.hidden) { closeReaderSettings(true); event.preventDefault(); return; }
      if (readerPopups.size) {
        closeReaderPopup(Array.from(readerPopups.values()).pop(), true); event.preventDefault(); return;
      }
      if (readerReturns.length) returnReaderTo(readerReturns.length - 1, true);
      closeReaderToc();
      hideTip();
      closeDrawer();
    }
  });
  window.addEventListener("hashchange", route);
  var readerScrollFrame = 0;
  window.addEventListener("scroll", function () {
    updateReaderProgress();
    if (readerScrollFrame) return;
    readerScrollFrame = window.requestAnimationFrame(function () { readerScrollFrame = 0; updateReaderActiveTopic(); });
  }, {passive:true});
  window.addEventListener("resize", updateReaderProgress);
  el.main.addEventListener("load", updateReaderProgress, true);
  el.main.addEventListener("toggle", updateReaderProgress, true);
  // 关页/刷新：停播放器（浏览器会关掉资源，但不该依赖它）
  window.addEventListener("pagehide", function () { clearReaderPopups(); disposeDrawerStudy(); disposeAnimations(); });

  el.title.textContent = D.display_title || D.book;
  /* ── P-025-2：**绝不把读者留在"正在加载…"上** ──────────────────────────
     用户报的现象：页面卡在"正在加载…"，等多久都不动、也不说为什么。
     根因是 `#content` 里那句占位文字只在渲染成功时才被替换 —— 启动一旦出异常
     （数据没读到、某个渲染函数报错），读者看到的就是一个永远转不完的加载中。
     现在两层兜底：① 启动/渲染异常当场写明"哪一步出错、错误是什么、怎么反馈"；
     ② 就算异常没被捕获，8 秒后也把占位文字换成一个能自查的说明。 */
  /* 修改计划 F3/R4：页面就绪信号。检验程序等这个属性，而不是固定等几百毫秒：
     data-reader-ready = ok | failed；data-reader-units = 本页单元数；
     data-reader-unit = 当前已渲染的单元。只是属性，读者看不到。 */
  function markReaderReady(state, detail) {
    var root = document.documentElement;
    if (!root || !root.setAttribute) return;
    root.setAttribute("data-reader-ready", state);
    root.setAttribute("data-reader-units", String(Object.keys(D.units || {}).length));
    if (detail) root.setAttribute("data-reader-error", String(detail).slice(0, 300));
  }
  function bootFailed(err) {
    var msg = (err && (err.message || err)) || "未知错误";
    markReaderReady("failed", msg);
    if (window.console) console.error("[页面] 渲染失败：", err);
    el.main.innerHTML = "<section class='unit'><h1>这一页没能显示出来</h1>" +
      "<p>出错的地方：<code>" + esc(String(msg)) + "</code></p>" +
      "<p class='hint'>这是可离线打开的阅读页面。请将上面的具体错误发给制作方。" +
      "移动页面时，请一并保留同目录中的 <code>data</code> 和 <code>assets</code> 文件夹。</p></section>";
  }
  function watchdog() {
    if (el.main.querySelector(".unit")) return;             // 已经画出来了
    var text = (el.main.textContent || "").trim();
    if (text.indexOf("正在加载") < 0) return;                // 已经不是占位文字了
    bootFailed(new Error("页面初始化没有完成（等了 8 秒）。"));
  }
  /* 启动这一整段都要在 try 里 —— 包括"本次样张 N 节"这类小字：
     实测就是它在数据缺失时先抛异常，把后面的兜底全带走了。 */
  try {
    var scope = document.getElementById("scope-note");
    if (scope) {
      scope.textContent = incompleteChapter && !D.scope.length ? "本章未完成：正文尚未生成"
                         : D.data_ok ? "共 " + Object.keys(D.units).length + " 节 · 离线阅读"
                                    : "这一页没有读到内容数据";
    }
    if (!D.data_ok) {
      bootFailed(new Error("data/data.js 里没有 units —— 内容数据是空的，或者没跟页面一起拷过来。"));
    } else {
      renderSidebar();
      route();
      if (document.documentElement.getAttribute("data-reader-ready") !== "failed") markReaderReady("ok");
    }
  } catch (err) {
    bootFailed(err);
  }
  window.setTimeout(watchdog, 8000);
  // 3D 画布的统一入口暴露给外面：app.js 整体是个 IIFE（第 6–802 行），
  // 观察器写在 IIFE 外面时**看不见**这个函数（实测：typeof 恒为 false，静默失效）。

  /* ── 批注/详解弹层：**可拖动**、**可叉掉**（2026-10-02 用户要求）────────────────
     头部**运行时注入**，而不改三处标记：弹层由 render-core 用三种写法生成（批注/详解/依据），
     在标记里插字符串既脆又容易只改到一处（实测正则只命中 1/3）。
     拖动：按住手柄时把弹层临时改成 `position: fixed` 并写入当前屏幕位置 —— **不会跳**，跟手移动。
     关掉时清掉内联定位，下次打开仍贴着那句话（不记住位置，免得"再也找不到"）。 */
  //: 拖动后的位置**按类型**记住（同一类批注下次打开还在你拖过去的地方）
  var POP_POS_KEY = "aiforstudy.pop.pos.v1";

  function popKindOf(pop) {
    var cls = String(pop.className || "");
    var m = cls.match(/pop-([a-z]+)/);
    return m ? m[1] : "note";
  }

  function popPosLoad() {
    try { return JSON.parse(localStorage.getItem(POP_POS_KEY) || "{}") || {}; }
    catch (e) { return {}; }
  }

  function popPosSave(kind, left, top) {
    try {
      var all = popPosLoad();
      all[kind] = [Math.round(left), Math.round(top)];
      localStorage.setItem(POP_POS_KEY, JSON.stringify(all));
    } catch (e) {}
  }

  function ensurePopHead(pop) {
    if (!pop || pop.getAttribute("data-head") === "1") return;
    var head = document.createElement("span");
    head.className = "pop-head";
    head.title = "双击这里折叠/展开；按住 ⠿ 拖动";
    head.innerHTML = '<span class="pop-grip" title="按住拖动">⠿</span>' +
                     '<button class="pop-x" type="button" data-pop-close="1" title="关掉">×</button>';
    pop.insertBefore(head, pop.firstChild);
    pop.setAttribute("data-head", "1");
  }

  /** 打开时应用"上次拖到的位置"（只对记过位置的类型生效）。 */
  function applySavedPopPos(pop) {
    var pos = popPosLoad()[popKindOf(pop)];
    if (!pos) return false;
    pop.style.position = "fixed";
    pop.style.left = Math.min(pos[0], window.innerWidth - pop.offsetWidth - 8) + "px";
    pop.style.top = Math.min(pos[1], window.innerHeight - 60) + "px";
    pop.style.width = Math.min(pop.getBoundingClientRect().width || 380, window.innerWidth - 24) + "px";
    pop.style.zIndex = "60";
    return true;
  }

  function closePop(pop) {
    if (!pop) return;
    pop.hidden = true;
    pop.style.position = pop.style.left = pop.style.top = pop.style.width = pop.style.zIndex = "";
    ["data-note", "data-explain", "data-basis"].forEach(function (attr) {
      var id = pop.getAttribute(attr);
      if (!id) return;
      var trig = document.querySelector("[" + attr + '="' + id + '"]');
      if (trig) { trig.classList.remove("is-on"); trig.setAttribute("aria-expanded", "false"); }
    });
  }

  document.addEventListener("pointerdown", function (event) {
    var grip = event.target.closest ? event.target.closest(".pop-grip") : null;
    if (!grip) return;
    var pop = grip.closest(".pop");
    if (!pop) return;
    event.preventDefault();
    var rect = pop.getBoundingClientRect();
    pop.style.position = "fixed";
    pop.style.left = rect.left + "px";
    pop.style.top = rect.top + "px";
    pop.style.width = Math.min(rect.width, window.innerWidth - 24) + "px";
    pop.style.zIndex = "60";
    var dx = event.clientX - rect.left, dy = event.clientY - rect.top;
    function move(ev) {
      var left = Math.min(Math.max(8, ev.clientX - dx), window.innerWidth - pop.offsetWidth - 8);
      var top = Math.min(Math.max(8, ev.clientY - dy), window.innerHeight - pop.offsetHeight - 8);
      pop.style.left = left + "px";
      pop.style.top = top + "px";
    }
    function up() {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
      popPosSave(popKindOf(pop), parseFloat(pop.style.left) || rect.left,
                 parseFloat(pop.style.top) || rect.top);      // 记住这一类的落点
    }
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", up);
  });

  document.addEventListener("click", function (event) {
    var x = event.target.closest ? event.target.closest("[data-pop-close]") : null;
    if (x) { event.preventDefault(); closePop(x.closest(".pop")); return; }
  });

  // 双击标题栏 → 折成小条（长批注可以摊开好几条对照着看）
  document.addEventListener("dblclick", function (event) {
    var head = event.target.closest ? event.target.closest(".pop-head") : null;
    if (!head) return;
    var pop = head.closest(".pop");
    if (!pop) return;
    event.preventDefault();
    pop.classList.toggle("pop-folded");
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    Array.prototype.forEach.call(document.querySelectorAll(".pop:not([hidden])"), closePop);
  });

  window.__fig3d = {init: initThreeFigures};
  /* ── 3D 图：把 `data-scene` 画成可转视角的画布（2026-10-01）─────────────────
     为什么**懒加载** three：它 619 KB，没 3D 图的页面不该为它买单。
     为什么不用 GLTFLoader：vendor 里没有那个加载器，而场景描述（物体+相机）已随载荷到了，
     用 three 的基本体直接建即可 —— 少一个依赖、少一次解析。
     失败一律**回退静态贴片**（不静默留白）。 */
  var THREE_READY = null;
  function ensureThree(cb) {
    if (window.THREE && window.THREE.OrbitControls) { cb(); return; }
    if (THREE_READY) { THREE_READY.push(cb); return; }
    THREE_READY = [cb];
    var files = ["assets/three.min.js", "assets/OrbitControls.js"];
    (function next(i) {
      if (i >= files.length) { THREE_READY.forEach(function (fn) { fn(); }); return; }
      var dup = document.querySelector('script[src="' + files[i] + '"]');
      if (dup) { next(i + 1); return; }        // 已经有人插过同一个脚本（实测会插两遍）
      var s = document.createElement("script");
      s.src = files[i];
      s.onload = function () { next(i + 1); };
      s.onerror = function () { THREE_READY.forEach(function (fn) { fn(new Error(files[i])); }); };
      document.head.appendChild(s);
    })(0);
  }

  function initThreeFigures() {
    var canvases = document.querySelectorAll("canvas.fig3d-canvas[data-scene]");
    if (!canvases.length) return;
    ensureThree(function (err) {
      Array.prototype.forEach.call(canvases, function (cv) {
        if (cv.getAttribute("data-3d") === "ready") return;      // 幂等：抽屉会反复打开
        var host = cv.closest(".fig3d");
        function fail() { if (host) host.classList.add("fig3d-failed"); }
        if (err || !window.THREE) { fail(); return; }
        try {
          var scene3d = JSON.parse(cv.getAttribute("data-scene") || "{}");
          var THREE = window.THREE;
          var renderer = new THREE.WebGLRenderer({canvas: cv, antialias: true, alpha: true});
          renderer.setPixelRatio(window.devicePixelRatio || 1);
          var width = cv.clientWidth || 520, height = 360;
          renderer.setSize(width, height, false);
          var scene = new THREE.Scene();
          scene.background = new THREE.Color(0xffffff);
          var cam = scene3d.camera || {};
          var loc = cam.location || [6, -6, 5], look = cam.look_at || [0, 0, 0];
          var camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
          camera.position.set(loc[0], loc[1], loc[2]);
          camera.lookAt(new THREE.Vector3(look[0], look[1], look[2]));
          scene.add(new THREE.AmbientLight(0xffffff, 0.75));
          var dir = new THREE.DirectionalLight(0xffffff, 0.8);
          dir.position.set(5, -5, 8);
          scene.add(dir);
          (scene3d.objects || []).forEach(function (o) {
            var size = o.size || [1, 1, 1];
            var geo = String(o.kind) === "cylinder"
              ? new THREE.CylinderGeometry(Math.max(size[0], size[1]) / 2, Math.max(size[0], size[1]) / 2,
                                           size[2] || 0.5, 32)
              : (String(o.kind) === "sphere"
                 ? new THREE.SphereGeometry(Math.max(size[0], size[1]) / 2, 24, 16)
                 : new THREE.BoxGeometry(size[0], size[1], size[2]));
            var mesh = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({
              color: String(o.kind) === "plane" ? 0xdbe4ff : 0xf0a35e,
              side: THREE.DoubleSide}));
            var at = o.at || [0, 0, 0];
            mesh.position.set(at[0], at[1], at[2]);
            scene.add(mesh);
          });
          var controls = new THREE.OrbitControls(camera, cv);
          controls.enableDamping = true;
          controls.addEventListener("change", function () { renderer.render(scene, camera); });
          renderer.render(scene, camera);
          cv.setAttribute("data-3d", "ready");
        } catch (e) { fail(); }
      });
    });
  }

})();

/* ── 界面版本标记（2026-10-01）────────────────────────────────────────────
   页面上**看得见**的一行小字。为什么要它：改了界面却"看不出变化"时，
   第一嫌疑永远是"打开的是缓存/旧文件" —— 有标记就能一眼判断看到的是哪一版。
   本轮记号：右栏 = Peek（点击换内容、正文不动）+「展开为大抽屉」。 */
(function stampUiVersion() {
  function put() {
    try {
      if (document.body.classList.contains("book-page")) return;
      if (document.getElementById("ui-version")) return;
      var el = document.createElement("div");
      el.id = "ui-version";
      el.style.cssText = "position:fixed;right:10px;bottom:10px;z-index:99;font-size:12px;" +
        "color:#6b7280;background:rgba(255,255,255,.94);border:1px solid #e6e8ec;" +
        "border-radius:999px;padding:2px 10px;";
      el.textContent = "界面 v4 · 点击公式/定理＝就地展开解析与推导";
      document.body.appendChild(el);
    } catch (e) {}
  }
  if (document.body) put(); else document.addEventListener("DOMContentLoaded", put);
})();

/* 3D 画布的统一入口：**观察**正文与抽屉的内容变化，出现 `canvas[data-scene]` 就初始化（幂等）。
   为什么不撒调用点：实测漏过一次 —— 抽屉里的画布压根没人初始化。观察 DOM 与调用点解耦，只留一处。 */
(function watchFor3D() {
  function kick() {
    var api = window.__fig3d;
    if (!api || typeof api.init !== "function") {
      // 静默失效过一次（函数在 IIFE 里、观察器在外面）→ 这里**出声**，别再让人瞎猜
      if (!kick.__warned) { kick.__warned = true; console.warn("[fig3d] 入口没暴露：window.__fig3d 不可用"); }
      return;
    }
    if (document.querySelector("canvas.fig3d-canvas[data-scene]")) api.init();
  }
  if (document.readyState !== "loading") kick();
  document.addEventListener("DOMContentLoaded", kick);
  if (window.MutationObserver) {
    var obs = new MutationObserver(function () { kick(); });
    var attach = function () {
      [document.getElementById("main"), document.getElementById("drawer-body")].forEach(function (node) {
        if (node && !node.__fig3dWatched) { node.__fig3dWatched = true; obs.observe(node, {childList: true, subtree: true}); }
      });
    };
    if (document.readyState !== "loading") attach();
    document.addEventListener("DOMContentLoaded", attach);
    setTimeout(function () { attach(); kick(); }, 500);         // 抽屉元素可能后建
  }
})();
