/* 修改计划 S：书架的 Service Worker。
 * - 书架页本身缓存在 aifs-shell，离线也能打开书架。
 * - 每个“书/版本/章”下载后存在独立缓存 aifs-ch:<书>:<版本>:<章>，含视频；
 *   离线时按缓存提供，视频的 Range 请求从缓存里切片返回。
 */
'use strict';
const SHELL = 'aifs-shell-v1';
const SHELL_FILES = ['/', '/shelf/shelf.js', '/shelf/shelf.css', '/shelf/icon.svg', '/manifest.webmanifest'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (event) => { event.waitUntil(self.clients.claim()); });

async function fromChapterCaches(request) {
  const url = new URL(request.url);
  const plain = new Request(url.origin + url.pathname);
  for (const name of await caches.keys()) {
    if (!name.startsWith('aifs-ch:')) continue;
    const hit = await (await caches.open(name)).match(plain);
    if (hit) return hit;
  }
  return null;
}

async function sliceRange(response, header) {
  const buffer = await response.arrayBuffer();
  const size = buffer.byteLength;
  const match = /bytes=(\d*)-(\d*)/.exec(header || '');
  let start = match && match[1] ? Number(match[1]) : 0;
  let end = match && match[2] ? Number(match[2]) : size - 1;
  if (match && !match[1] && match[2]) { start = Math.max(0, size - Number(match[2])); end = size - 1; }
  end = Math.min(end, size - 1);
  return new Response(buffer.slice(start, end + 1), {status: 206, headers: {
    'Content-Type': response.headers.get('Content-Type') || 'application/octet-stream',
    'Content-Range': `bytes ${start}-${end}/${size}`, 'Content-Length': String(end - start + 1),
    'Accept-Ranges': 'bytes'}});
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname === '/library.json') {
    event.respondWith(fetch(request).then(async (response) => {
      (await caches.open(SHELL)).put('/library.json', response.clone());
      return response;
    }).catch(async () => (await caches.match('/library.json')) || Response.error()));
    return;
  }
  if (url.pathname.startsWith('/library/')) {
    event.respondWith((async () => {
      try {
        return await fetch(request);
      } catch (err) {
        const cached = await fromChapterCaches(request);
        if (!cached) throw err;
        const range = request.headers.get('Range');
        return range ? sliceRange(cached, range) : cached;
      }
    })());
    return;
  }
  event.respondWith(fetch(request).catch(async () => (await caches.match(url.pathname)) || Response.error()));
});
