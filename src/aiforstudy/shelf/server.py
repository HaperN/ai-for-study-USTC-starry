"""修改计划 S：`./aifs shelf` 本机书架服务——只监听 127.0.0.1，只读提供书架页与书库文件。

支持 HTTP Range（视频拖动与 Service Worker 离线缓存都需要）。不提供任何写接口：
笔记只存在浏览器的 IndexedDB 里。
"""
from __future__ import annotations

from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .library import library_index

MIME = {'.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
        '.html': 'text/html; charset=utf-8', '.json': 'application/json; charset=utf-8',
        '.webmanifest': 'application/manifest+json', '.svg': 'image/svg+xml', '.mp4': 'video/mp4',
        '.vtt': 'text/vtt; charset=utf-8', '.txt': 'text/plain; charset=utf-8', '.png': 'image/png',
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.woff2': 'font/woff2', '.woff': 'font/woff',
        '.ttf': 'font/ttf'}
SHELL_FILES = {'/': 'index.html', '/index.html': 'index.html', '/sw.js': 'sw.js',
               '/manifest.webmanifest': 'manifest.webmanifest'}


def shelf_resource(name: str) -> Path:
    return Path(str(files('aiforstudy').joinpath('resources', 'shelf', name)))


def _safe_join(root: Path, relative: str) -> Path | None:
    parts = [p for p in relative.split('/') if p]
    if any(p in ('.', '..') or '\\' in p or '\x00' in p for p in parts):
        return None
    path = root.joinpath(*parts)
    try:
        resolved = path.resolve()
    except OSError:
        return None
    return resolved if resolved.is_relative_to(root.resolve()) and resolved.is_file() else None


class ShelfHandler(BaseHTTPRequestHandler):
    server_version = 'AIforStudyShelf/1'

    def __init__(self, *args, library: Path, **kwargs):
        self.library = Path(library)
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):  # noqa: A002 - quiet local server
        pass

    def do_HEAD(self):
        self._serve(head=True)

    def do_GET(self):
        self._serve(head=False)

    def _serve(self, *, head):
        path = unquote(urlsplit(self.path).path)
        if path == '/library.json':
            body = json.dumps(library_index(self.library), ensure_ascii=False).encode('utf-8')
            return self._bytes(body, 'application/json; charset=utf-8', head, cache='no-store')
        if path in SHELL_FILES:
            target = shelf_resource(SHELL_FILES[path])
        elif path.startswith('/shelf/'):
            target = _safe_join(shelf_resource('index.html').parent, path[len('/shelf/'):])
        elif path.startswith('/library/'):
            target = _safe_join(self.library / 'books', path[len('/library/'):])
        else:
            target = None
        if target is None or not Path(target).is_file():
            return self._bytes('未找到'.encode('utf-8'), 'text/plain; charset=utf-8', head, status=HTTPStatus.NOT_FOUND)
        extra = {'Service-Worker-Allowed': '/'} if path == '/sw.js' else {}
        return self._file(Path(target), head, extra)

    def _bytes(self, body, ctype, head, *, status=HTTPStatus.OK, cache='no-cache'):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _file(self, path: Path, head, extra):
        size = path.stat().st_size
        ctype = MIME.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        start, end, status = 0, size - 1, HTTPStatus.OK
        header = self.headers.get('Range')
        if header and header.startswith('bytes='):
            first, _, last = header[6:].split(',')[0].partition('-')
            try:
                if first:
                    start, end = int(first), (int(last) if last else size - 1)
                else:
                    start, end = max(0, size - int(last)), size - 1
            except ValueError:
                start, end = 0, size - 1
            if start >= size or start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header('Content-Range', f'bytes */{size}')
                self.end_headers()
                return
            end, status = min(end, size - 1), HTTPStatus.PARTIAL_CONTENT
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Cache-Control', 'no-cache')
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        if head:
            return
        with path.open('rb') as fh:
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = fh.read(min(1 << 16, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)


def make_server(library: Path, *, host: str = '127.0.0.1', port: int = 8790) -> ThreadingHTTPServer:
    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('书架只监听本机（127.0.0.1）；教材未获授权，不对外发布')
    return ThreadingHTTPServer((host, port), partial(ShelfHandler, library=Path(library)))


__all__ = ['make_server', 'shelf_resource']
