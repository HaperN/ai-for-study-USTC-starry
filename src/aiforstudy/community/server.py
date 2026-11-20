"""Loopback annotation service: a read-only reader and a narrowly scoped API."""
from __future__ import annotations

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from io import BytesIO
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from .book import BOOK, MEDIA, PRACTICE, BookSnapshot
from .store import AnnotationStore, StoreError
from .images import normalize_image, MAX_INPUT_BYTES, image_capabilities
from .identity import IdentityError, IdentityProvider, NicknameIdentityProvider, Principal

BODY_LIMIT = 16_384
ASSETS = {'community-images.js': 'text/javascript; charset=utf-8',
          'shared-notes.js': 'text/javascript; charset=utf-8',
          'shared-notes.css': 'text/css; charset=utf-8'}
STATIC_EXTENSIONS = {'.js', '.json', '.css', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.gif',
                     '.ico', '.woff2', '.woff', '.ttf', '.otf', '.pdf', '.mp4', '.mp3', '.wav', '.vtt'}


class CommunityServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, reader: Path, database: Path, port: int, identity_provider: IdentityProvider | None = None, recognizer=None, *, practice=None):
        self.identity_provider = identity_provider if identity_provider is not None else NicknameIdentityProvider()
        self.recognizer = recognizer
        self.image_capabilities = image_capabilities()
        self.reader = Path(os.path.abspath(reader))
        database = database.resolve()
        if database.is_relative_to(self.reader):
            raise ValueError('批注数据库必须放在教材静态文件目录之外。')
        self.snapshot = BookSnapshot(self.reader, practice)
        self.context = self.snapshot.context
        self.units = frozenset(self.context['units'])
        self.store = AnnotationStore(database, self.context['book'], self.context['revision'])
        self.bursts = {}
        self.burst_lock = threading.Lock()
        super().__init__(('127.0.0.1', port), CommunityHandler)
        bound_port = self.server_address[1]
        self.hosts = {f'127.0.0.1:{bound_port}', f'localhost:{bound_port}'}
        self.origins = {'http://' + host for host in self.hosts}

    def server_close(self):
        try:
            close = getattr(self.recognizer, 'close', None)
            if callable(close): close()
        finally:
            super().server_close()

    def check_revision(self):
        self.snapshot.check()

    def check_burst(self, client):
        now = time.monotonic()
        with self.burst_lock:
            queue = self.bursts.setdefault(client, deque())
            while queue and queue[0] <= now - 60: queue.popleft()
            if len(queue) >= 60:
                raise StoreError(429, '请求过于频繁，请稍等一分钟再试。')
            queue.append(now)


class CommunityHandler(BaseHTTPRequestHandler):
    server: CommunityServer
    server_version = 'AIforStudyCommunity/1'

    def log_message(self, format, *args):
        pass  # No author credentials, request bodies or reader paths in access logs.

    def do_GET(self): self._handle()
    def do_HEAD(self): self._handle()
    def do_PATCH(self): self._handle()
    def do_POST(self): self._handle()
    def do_DELETE(self): self._handle()
    def do_OPTIONS(self): self._reply(405, {'error': '仅支持同源请求。'})

    def _reply(self, status, value, *, headers=None):
        self._bytes(status, json.dumps(value, ensure_ascii=False).encode(),
                    'application/json; charset=utf-8', headers=headers)

    def _headers(self, status, content_type, length, extra=None):
        self.send_response(status)
        for name, value in {'Content-Type': content_type, 'Content-Length': str(length),
                            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                            'X-Frame-Options': 'SAMEORIGIN', 'Referrer-Policy': 'no-referrer',
                            **(extra or {})}.items():
            self.send_header(name, value)
        self.end_headers()

    def _bytes(self, status, data, content_type, *, headers=None):
        self._headers(status, content_type, len(data), headers)
        if self.command != 'HEAD': self.wfile.write(data)

    def _identity(self, *, required=False):
        if any(len(self.headers.get_all(name, [])) > 1 for name in ('Authorization', 'Cookie')):
            raise IdentityError(401, '身份请求格式无效。')
        identity = self.server.identity_provider.resolve(self.headers, required=required)
        if identity is not None and not isinstance(identity, Principal):
            raise IdentityError(503, '身份服务暂时不可用。')
        if required and identity is None:
            raise IdentityError(401, '请先完成身份认证。')
        return identity

    def _write_guard(self):
        origins = self.headers.get_all('Origin', [])
        if len(origins) != 1 or origins[0] not in self.server.origins:
            raise StoreError(403, '请在当前教材网站中发布或删除感悟。')
        revisions = self.headers.get_all('X-AIFS-Book-Revision', [])
        if revisions != [self.server.context['revision']]:
            raise StoreError(409, '教材版本已变化，请刷新页面后再发布。')
        practice = self.server.context.get('practice_revision')
        if practice and self.headers.get_all('X-AIFS-Practice-Revision', []) != [practice]:
            raise StoreError(409, '练习版本已变化，请刷新页面。')
        self.server.check_burst(self.client_address[0])

    def _body(self):
        if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            raise StoreError(415, '请提交 JSON 格式的感悟。')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not lengths[0].isdigit() or self.headers.get('Transfer-Encoding'):
            raise StoreError(411, '请求长度无效。')
        if len(lengths[0]) > 8 or int(lengths[0]) > BODY_LIMIT:
            raise StoreError(413, '提交内容太长。')
        length = int(lengths[0])
        raw = self.rfile.read(length)
        if len(raw) != length: raise StoreError(400, '请求内容不完整。')
        value = json.loads(raw)
        if not isinstance(value, dict): raise StoreError(400, '发布内容格式不正确。')
        return value

    def _unit(self, value):
        if not isinstance(value, str) or value not in self.server.units:
            raise StoreError(400, '该教学单元不属于当前教材。')
        return value

    def _handle(self):
        self.connection.settimeout(10)
        try:
            hosts = self.headers.get_all('Host', [])
            if len(hosts) != 1 or hosts[0] not in self.server.hosts:
                raise StoreError(403, '网站入口无效，请使用服务显示的本地地址。')
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if path.startswith('/api/'):
                self.server.check_revision()
                caller_revisions = self.headers.get_all('X-AIFS-Book-Revision', [])
                if caller_revisions and caller_revisions != [self.server.context['revision']]:
                    raise StoreError(409, '教材版本已变化，请刷新页面后再查看感悟。')
                caller_practice = self.headers.get_all('X-AIFS-Practice-Revision', [])
                if caller_practice and caller_practice != [self.server.context.get('practice_revision')]:
                    raise StoreError(409, '练习版本已变化，请刷新页面。')
                if self.command in ('POST', 'PATCH', 'DELETE'): self._write_guard()
                if path == '/api/annotation-targets' and self.command in ('GET', 'HEAD'):
                    params = parse_qs(parsed.query, max_num_fields=1)
                    unit = self._unit(params.get('unit', [''])[0])
                    return self._reply(200, {'targets': list(self.server.snapshot.targets[unit].values())})
                if path == '/api/annotation-images' and self.command in ('GET', 'HEAD'):
                    if parse_qs(parsed.query, keep_blank_values=True, max_num_fields=2) != {'pending':['1']}:
                        raise StoreError(400, '待发布图片查询无效。')
                    return self._reply(200, {'images': self.server.store.pending_images(self._identity(required=True))})
                if path == '/api/annotation-images' and self.command == 'POST':
                    if not self.server.image_capabilities['available']:
                        raise StoreError(503, '图片处理环境尚未配置。')
                    identity = self._identity(required=True)
                    unit = self._unit(self.headers.get('X-AIFS-Unit'))
                    if self.headers.get('Content-Type', '').split(';')[0] not in ('image/png','image/jpeg','image/webp'):
                        raise StoreError(415, '请上传 PNG、JPG 或 WebP 图片。')
                    lengths = self.headers.get_all('Content-Length', [])
                    if len(lengths) != 1 or not lengths[0].isdigit() or self.headers.get('Transfer-Encoding'):
                        raise StoreError(411, '上传长度无效。')
                    if len(lengths[0]) > 8 or int(lengths[0]) > MAX_INPUT_BYTES:
                        raise StoreError(413, '每张图片最多 10 MB。')
                    raw = self.rfile.read(int(lengths[0]))
                    if len(raw) != int(lengths[0]): raise StoreError(400, '图片上传不完整。')
                    result = self.server.store.upload_image(unit, normalize_image(raw), self.headers.get('X-Upload-Request'), identity)
                    return self._reply(201, {'image': result})
                recognition_match = re.fullmatch(r'/api/annotation-images/([a-f0-9]{32})/recognize', path)
                if recognition_match and self.command == 'POST':
                    self._body()
                    identity = self._identity(required=True)
                    image_id = recognition_match[1]
                    cached = self.server.store.recognition(image_id, identity)
                    if cached is not None: return self._reply(200, {'recognition': cached})
                    if self.server.recognizer is None:
                        raise StoreError(503, '本地识别尚未配置，可以直接按图片发布。')
                    image = self.server.store.image(image_id, identity)
                    result = self.server.recognizer.recognize(image['data'], image['mime'])
                    saved = self.server.store.save_recognition(image_id, identity, result)
                    return self._reply(200, {'recognition': saved})
                image_match = re.fullmatch(r'/api/annotation-images/([a-f0-9]{32})', path)
                if image_match and self.command in ('GET', 'HEAD'):
                    result = self.server.store.image(image_match[1], self._identity() or '')
                    return self._bytes(200, result['data'], result['mime'])
                if image_match and self.command == 'DELETE':
                    self.server.store.remove_image(image_match[1], self._identity(required=True))
                    return self._reply(200, {'deleted': True})
                if path == '/api/identity' and self.command in ('GET', 'HEAD'):
                    identity = self._identity()
                    provider = self.server.identity_provider
                    return self._reply(200, {'mode': provider.mode,
                        'authenticated': bool(identity and identity.authenticated),
                        'display_name': identity.display_name if identity else None,
                        'can_publish': provider.mode == 'nickname' or identity is not None,
                        'login_path': provider.login_path})
                if path == '/api/annotations' and self.command in ('GET', 'HEAD'):
                    query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=12)
                    if any(len(v) != 1 for v in query.values()) or set(query) - {'unit', 'anchor', 'before', 'limit'}:
                        raise StoreError(400, '查询参数无效。')
                    unit = self._unit(query.get('unit', [''])[0])
                    before = int(query['before'][0]) if 'before' in query else None
                    limit = int(query.get('limit', ['50'])[0])
                    result = self.server.store.list(unit, self._identity() or '', anchor=query.get('anchor', [None])[0],
                                                    before=before, limit=limit, targets=self.server.snapshot.targets[unit])
                    return self._reply(200, result)
                if path == '/api/annotations' and self.command == 'POST':
                    body = self._body(); unit = self._unit(body.get('unit_id'))
                    identity = self._identity(required=True)
                    self.server.check_revision()
                    item, created = self.server.store.create(unit, body, identity, validate_target=self.server.snapshot.validate)
                    return self._reply(201 if created else 200, {'item': item, 'created': created})
                match = re.fullmatch(r'/api/annotations/(\d+)', path)
                if match and self.command == 'PATCH':
                    body = self._body()
                    self.server.check_revision()
                    item = self.server.store.edit(int(match[1]), body, self._identity(required=True))
                    return self._reply(200, {'item': item})
                if match and self.command == 'DELETE':
                    note_id = int(match[1])
                    self.server.store.delete(note_id, self._identity(required=True))
                    return self._reply(200, {'deleted': True})
                return self._reply(404, {'error': '未找到该接口。'})
            if self.command not in ('GET', 'HEAD'):
                return self._reply(405, {'error': '该资源为只读。'})
            return self._static(path, parsed.query)
        except (StoreError, IdentityError) as exc:
            self._reply(exc.status, {'error': exc.message}, headers={'Retry-After':'60'} if exc.status == 429 else None)
        except (ValueError, UnicodeError, TimeoutError):
            self._reply(400, {'error': '请求内容或参数无法读取。'})
        except (sqlite3.Error, OSError) as exc:
            if not isinstance(exc, (BrokenPipeError, ConnectionResetError)):
                self._reply(503, {'error': '暂时无法读取或保存，请稍后重试。'})

    def reader_html(self, source):
        return source

    def _static(self, path, query):
        if path in ('/', '/index.html'):
            self.server.check_revision()
            index = (self.server.reader / 'index.html').resolve()
            if not index.is_relative_to(self.server.reader) or not index.is_file():
                return self._reply(404, {'error': '未找到教材首页。'})
            source = self.reader_html(index.read_text(encoding='utf-8'))
            context = {k: self.server.context[k] for k in ('book', 'revision')}
            context['targets_api'] = '/api/annotation-targets'
            if 'practice_revision' in self.server.context:
                context['practice_revision'] = self.server.context['practice_revision']
            context['api'] = '/api/annotations'
            context['identity_api'] = '/api/identity'
            context['identity_mode'] = self.server.identity_provider.mode
            context['image_upload_api'] = '/api/annotation-images'
            context['image_limits'] = {'count': 3, 'bytes': MAX_INPUT_BYTES, 'mime_types': self.server.image_capabilities['mime_types']}
            context['image_upload_available'] = self.server.image_capabilities['available']
            context['recognition_enabled'] = self.server.recognizer is not None
            config = json.dumps(context, ensure_ascii=False).replace('<', '\\u003c')
            # Bind before the external notes script, after older embedded version metadata.
            binding = ('<script>window.__AIFS_SHARED_NOTES__=' + config +
                       ';window.__BOOK_VERSION__={...window.__BOOK_VERSION__,book:window.__AIFS_SHARED_NOTES__.book,'
                       'version:window.__AIFS_SHARED_NOTES__.revision};</script>')
            source = re.sub(r"""<script\b[^>]*\bsrc=["'](?:[^"']*/)?notes\.js(?:[?#][^"']*)?["'][^>]*>""",
                            lambda m: binding + m.group(), source, flags=re.I)
            extra = ('<link rel="stylesheet" href="/community-assets/shared-notes.css">'
                     '<script>window.__AIFS_SHARED_NOTES__=' + config + ';</script>'
                     '<script defer src="/community-assets/community-images.js"></script>'
                     '<script defer src="/community-assets/shared-notes.js"></script>')
            source = source.replace('</body>', extra + '</body>') if '</body>' in source else source + extra
            return self._bytes(200, source.encode(), 'text/html; charset=utf-8')
        if path.startswith('/community-assets/'):
            name = path.removeprefix('/community-assets/')
            if name not in ASSETS: return self._reply(404, {'error': '未找到资源。'})
            data = files('aiforstudy').joinpath('resources/community', name).read_bytes()
            return self._bytes(200, data, ASSETS[name])
        parts = Path(path.lstrip('/')).parts
        if (not parts or parts[0] not in ('assets', 'data') or
                any(p in ('.', '..') or p.startswith('.') or '\\' in p or '\x00' in p for p in parts)):
            return self._reply(404, {'error': '未找到资源。'})
        name = '/'.join(parts)
        if name in (BOOK, PRACTICE, MEDIA):
            self.server.check_revision()
            if name not in self.server.snapshot.raw:
                return self._reply(404, {'error': '未找到资源。'})
            return self._file(self.server.reader / name, frozen=self.server.snapshot.raw[name])
        target = self.server.reader.joinpath(*parts).resolve()
        if (not target.is_relative_to(self.server.reader) or not target.is_file() or
                target.suffix.lower() not in STATIC_EXTENSIONS):
            return self._reply(404, {'error': '未找到资源。'})
        if name in ('assets/notes.js', 'assets/reader-markdown.js', 'assets/study-lesson.js'):
            from ..pipeline.stage_io import _repo_root
            target = _repo_root() / 'blocks/render' / name
        if name == 'assets/marked.min.js':
            from ..pipeline.stage_io import _repo_root
            target = _repo_root() / 'blocks/render/vendor/marked.min.js'
        return self._file(target)

    def _file(self, target, *, frozen=None):
        size = len(frozen) if frozen is not None else target.stat().st_size
        start, end, status = 0, size - 1, 200
        extra = {'Accept-Ranges': 'bytes', 'Cache-Control': 'no-cache'}
        range_value = self.headers.get('Range')
        if range_value:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_value)
            try:
                if not match or not any(match.groups()): raise ValueError()
                left, right = match.groups()
                if left:
                    start, end = int(left), min(int(right), size-1) if right else size-1
                else:
                    length = int(right)
                    if length <= 0: raise ValueError()
                    start, end = max(0, size-length), size-1
                if start < 0 or start > end or start >= size: raise ValueError()
            except ValueError:
                return self._reply(416, {'error': '范围超出文件。'}, headers={'Content-Range': f'bytes */{size}'})
            status = 206; extra['Content-Range'] = f'bytes {start}-{end}/{size}'
        content_type = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
        if target.suffix == '.js': content_type = 'text/javascript; charset=utf-8'
        self._headers(status, content_type, max(0, end-start+1), extra)
        if self.command != 'HEAD':
            with (BytesIO(frozen) if frozen is not None else target.open('rb')) as stream:
                stream.seek(start); remaining = end-start+1
                while remaining > 0:
                    chunk = stream.read(min(256*1024, remaining))
                    if not chunk: break
                    self.wfile.write(chunk); remaining -= len(chunk)


def make_server(reader_dir: Path, database: Path, *, port: int = 8791,
                identity_provider: IdentityProvider | None = None, recognizer=None, practice=None) -> CommunityServer:
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('端口必须是 0–65535 的整数。')
    return CommunityServer(Path(reader_dir), Path(database), port, identity_provider, recognizer, practice=practice)
