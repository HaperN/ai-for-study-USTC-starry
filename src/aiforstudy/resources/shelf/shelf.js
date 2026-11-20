/* 修改计划 S：书架页。封面由书名生成；章节列表含大小、下载状态、更新状态；
 * 我的笔记数；继续上次阅读；按章离线下载（含视频）与删除。 */
(function () {
  'use strict';
  var root = document.getElementById('shelf');
  var chapterCache = function (book, version, key) { return 'aifs-ch:' + book + ':' + version + ':' + key; };

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function size(bytes) {
    return bytes >= 1 << 30 ? (bytes / (1 << 30)).toFixed(1) + ' GB' : (bytes / (1 << 20)).toFixed(1) + ' MB';
  }
  function hue(text) { var h = 0; for (var i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) % 360; return h; }
  function cover(title) {
    var h = hue(title), svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    [['viewBox', '0 0 300 400'], ['class', 'book-cover'], ['role', 'img'], ['aria-label', title + ' 封面']]
      .forEach(function (pair) { svg.setAttribute(pair[0], pair[1]); });
    svg.innerHTML = '<defs><linearGradient id="g' + h + '" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="hsl(' + h + ',42%,38%)"/>'
      + '<stop offset="1" stop-color="hsl(' + ((h + 40) % 360) + ',46%,24%)"/></linearGradient></defs>'
      + '<rect width="300" height="400" fill="url(#g' + h + ')"/><rect x="18" y="0" width="6" height="400" fill="rgba(255,255,255,.18)"/>';
    var lines = title.match(/.{1,6}/g) || [title];
    lines.slice(0, 4).forEach(function (line, i) {
      var t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      t.setAttribute('x', '150'); t.setAttribute('y', String(150 + i * 46)); t.setAttribute('text-anchor', 'middle');
      t.setAttribute('fill', '#fff'); t.setAttribute('font-size', '38'); t.setAttribute('font-weight', '700');
      t.textContent = line; svg.appendChild(t);
    });
    return svg;
  }
  function noteCount(bookId) {
    return new Promise(function (resolve) {
      if (!window.indexedDB) return resolve(0);
      var request = indexedDB.open('aifs-notes', 1);
      request.onupgradeneeded = function () {
        var store = request.result.createObjectStore('items', {keyPath: 'id'});
        store.createIndex('scope', 'scope'); store.createIndex('bookKey', 'bookKey');
      };
      request.onerror = function () { resolve(0); };
      request.onsuccess = function () {
        var db = request.result, t = db.transaction('items', 'readonly');
        var count = t.objectStore('items').index('bookKey').count('local/' + bookId);
        count.onsuccess = function () { resolve(count.result); };
        count.onerror = function () { resolve(0); };
      };
    });
  }
  function lastRead(bookId) {
    try { return JSON.parse(localStorage.getItem('aifs:last:' + bookId) || 'null'); } catch (e) { return null; }
  }
  function downloaded(book, version, key) {
    return window.caches ? caches.has(chapterCache(book, version, key)) : Promise.resolve(false);
  }
  async function download(book, version, entry, status, buttonEl) {
    buttonEl.disabled = true;
    var cache = await caches.open(chapterCache(book.id, version.id, entry.key));
    var base = '/library/' + encodeURIComponent(book.id) + '/' + version.id + '/' + entry.key + '/';
    var done = 0;
    try {
      for (var i = 0; i < entry.files.length; i++) {
        var url = base + entry.files[i].split('/').map(encodeURIComponent).join('/');
        var response = await fetch(url, {cache: 'no-store'});
        if (!response.ok) throw new Error(response.status + ' ' + url);
        await cache.put(url, response);
        done += 1;
        status.textContent = '下载中 ' + Math.round(done * 100 / entry.files.length) + '%';
      }
      status.textContent = '已离线';
      status.className = 'status badge';
    } catch (err) {
      await caches['delete'](chapterCache(book.id, version.id, entry.key));
      status.textContent = '下载失败';
      status.className = 'status badge warn';
    }
    render();
  }
  async function remove(book, version, entry) {
    await caches['delete'](chapterCache(book.id, version.id, entry.key));
    render();
  }

  async function renderBook(book, container) {
    var versions = book.versions || [];
    var stored = localStorage.getItem('aifs:shelf-version:' + book.id);
    var chosen = versions.filter(function (v) { return v.id === stored; })[0] || versions[versions.length - 1];
    if (!chosen) return;
    var latest = versions[versions.length - 1];
    var card = el('article', {'class': 'book', 'data-book': book.id});
    card.appendChild(cover(book.title || book.id));
    var body = el('div');
    body.appendChild(el('h2', {}, book.title || book.id));
    if (book.intro) body.appendChild(el('p', {'class': 'book-intro'}, book.intro));
    var last = lastRead(book.id);
    if (last && last.version && last.version !== latest.id) {
      body.appendChild(el('p', {'class': 'new-version'}, '有新版本 ' + latest.id + '。你的笔记绑定在版本 ' + last.version
        + '：文字便签按内容块迁移，墨迹保留在旧版本中可查看。'));
    }
    var meta = el('div', {'class': 'book-meta'});
    var picker = el('select', {'aria-label': '选择版本'});
    versions.forEach(function (v) {
      var option = el('option', {value: v.id}, '版本 ' + v.id + (v.id === latest.id ? '（最新）' : ''));
      if (v.id === chosen.id) option.selected = true;
      picker.appendChild(option);
    });
    picker.addEventListener('change', function () { localStorage.setItem('aifs:shelf-version:' + book.id, picker.value); render(); });
    meta.appendChild(picker);
    var notes = el('span', {'data-notes-count': ''}, '我的笔记：…');
    meta.appendChild(notes);
    noteCount(book.id).then(function (n) { notes.textContent = '我的笔记：' + n + ' 条'; });
    if (last && last.href) meta.appendChild(el('a', {'class': 'continue', href: last.href}, '继续上次阅读'));
    body.appendChild(meta);
    var list = el('ul', {'class': 'chapters'});
    for (var i = 0; i < chosen.entries.length; i++) {
      var entry = chosen.entries[i];
      var li = el('li', {'data-entry': entry.key});
      var href = '/library/' + encodeURIComponent(book.id) + '/' + entry.path;
      li.appendChild(el('a', {'class': 'title', href: href}, (entry.kind === 'book' ? '全书入口' : entry.key + ' ' + entry.title)));
      li.appendChild(el('span', {'class': 'size'}, size(entry.size_bytes)));
      var status = el('span', {'class': 'status'}, '…');
      li.appendChild(status);
      var action = el('button', {type: 'button', 'class': 'button secondary', 'data-action': 'download'}, '下载');
      li.appendChild(action);
      list.appendChild(li);
      (function (entry, status, action) {
        var newer = latest.id !== chosen.id ? '（有新版本）' : '';
        downloaded(book.id, chosen.id, entry.key).then(function (has) {
          status.textContent = (has ? '已离线' : '在线') + newer;
          status.className = 'status' + (has ? ' badge' : '');
          action.textContent = has ? '删除离线' : '下载';
          action.setAttribute('data-action', has ? 'remove' : 'download');
          action.onclick = function () { return has ? remove(book, chosen, entry) : download(book, chosen, entry, status, action); };
          if (!window.caches) { action.disabled = true; action.title = '此浏览器不支持离线缓存'; }
        });
      })(entry, status, action);
    }
    body.appendChild(list);
    card.appendChild(body);
    container.appendChild(card);
  }

  async function render() {
    var library;
    try {
      library = await (await fetch('/library.json', {cache: 'no-store'})).json();
    } catch (err) {
      root.innerHTML = '<p class="empty">书库读取失败（离线且没有缓存的书库清单）。</p>';
      return;
    }
    var fragment = document.createDocumentFragment();
    if (!library.books.length) fragment.appendChild(el('p', {'class': 'empty'}, '书库是空的。用 ./aifs shelf --add <交付目录> 加一本书。'));
    for (var i = 0; i < library.books.length; i++) await renderBook(library.books[i], fragment);
    root.textContent = '';
    root.appendChild(fragment);
    document.documentElement.setAttribute('data-shelf-ready', String(library.books.length));
  }

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js', {scope: '/'}).catch(function () { /* 离线下载不可用时书架照常 */ });
  }
  render();
})();
