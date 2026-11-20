"""修改计划 S：本地书架的书库（只做本地效果，不对外发布——教材未获授权）。

书库目录结构::

    <library>/books/<book_id>/book.json
    <library>/books/<book_id>/<version>/<entry>/index.html   （entry = CH01…CH09、book）

`add_book` 从一个交付目录复制一份内容（数据一字不改），把阅读器脚本和样式换成当前
版本（字幕层、批注模块、固定版式），再给每个入口加上书本版本信息。原交付目录不动。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import time

from ..errors import InputError

READER_ASSETS = ('animations.js', 'app.js', 'reader-book.css', 'reader-layout.js', 'reader-markdown.js',
                 'render-core.js', 'styles.css', 'notes.js', 'notes.css')
_SAFE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')


def _assets_dir() -> Path:
    from ..pipeline.stage_io import _repo_root
    return _repo_root() / 'blocks' / 'render' / 'assets'


def _read_data(entry: Path) -> dict:
    text = (entry / 'data' / 'data.js').read_text(encoding='utf-8')
    return json.loads(text[text.index('=') + 1:].strip().rstrip(';'))


def _entry_title(key: str, data: dict) -> str:
    tree = data.get('tree') or []
    if key == 'book':
        return '全书'
    if tree and isinstance(tree[0], dict) and tree[0].get('title'):
        return str(tree[0]['title'])
    return key


def _link_or_copy(src: str, dst: str) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _replace(path: Path, data: bytes) -> None:
    # Hard links share bytes with the source delivery: unlink before writing so
    # the original delivery is never modified through the link.
    if path.exists() or path.is_symlink():
        path.unlink()
    path.write_bytes(data)


def _inject(index: Path) -> None:
    html = index.read_text(encoding='utf-8')
    if 'assets/notes.js' not in html:
        html = html.replace('</head>', '<link rel="stylesheet" href="assets/notes.css">\n</head>', 1)
        html = html.replace('</body>', '<script src="assets/notes.js"></script>\n</body>', 1)
    if 'data/version.js' not in html:
        html = html.replace('<script src="data/data.js"></script>',
                            '<script src="data/version.js"></script>\n<script src="data/data.js"></script>', 1)
    _replace(index, html.encode('utf-8'))


def add_book(delivery: Path, library: Path, *, book_id: str, version: str, title: str, intro: str = '') -> dict:
    delivery, library = Path(delivery).resolve(), Path(library).resolve()
    if not _SAFE_ID.match(book_id) or not _SAFE_ID.match(version):
        raise InputError('book_id / version 只能用字母、数字、点、下划线和连字符')
    entries = sorted(p for p in delivery.iterdir() if (p / 'index.html').is_file() and (p / 'data' / 'data.js').is_file())
    if not entries:
        raise InputError(f'{delivery} 下没有阅读入口（含 index.html 与 data/data.js 的目录）')
    book_dir = library / 'books' / book_id
    target = book_dir / version
    if target.exists():
        raise InputError(f'书库里已有 {book_id} 的版本 {version}；新版本请换一个版本号')
    assets = _assets_dir()
    rows = []
    for entry in entries:
        out = target / entry.name
        shutil.copytree(entry, out, copy_function=_link_or_copy)
        for name in READER_ASSETS:
            _replace(out / 'assets' / name, (assets / name).read_bytes())
        version_info = {'book': book_id, 'version': version, 'entry': entry.name, 'title': title}
        _replace(out / 'data' / 'version.js',
                 ('window.__BOOK_VERSION__ = ' + json.dumps(version_info, ensure_ascii=False) + ';\n').encode('utf-8'))
        _inject(out / 'index.html')
        files = sorted(p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file())
        rows.append({'key': entry.name, 'kind': 'book' if entry.name == 'book' else 'chapter',
                     'title': _entry_title(entry.name, _read_data(out)),
                     'path': f'{version}/{entry.name}/index.html',
                     'size_bytes': sum((out / f).stat().st_size for f in files), 'files': files})
    manifest_path = book_dir / 'book.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.is_file() else {
        'schema_version': 'ShelfBookV1', 'id': book_id, 'versions': []}
    manifest.update(title=title or manifest.get('title') or book_id, intro=intro or manifest.get('intro', ''))
    manifest['versions'].append({'id': version, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                                 'source': str(delivery), 'entries': rows})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    return manifest


def library_index(library: Path) -> dict:
    books = []
    for path in sorted((Path(library) / 'books').glob('*/book.json')):
        try:
            book = json.loads(path.read_text(encoding='utf-8'))
        except ValueError:
            continue
        book['latest'] = book['versions'][-1]['id'] if book.get('versions') else None
        books.append(book)
    return {'schema_version': 'ShelfLibraryV1', 'books': books}


__all__ = ['add_book', 'library_index', 'READER_ASSETS']
