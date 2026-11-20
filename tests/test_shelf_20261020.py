"""修改计划 S（2026-10-20）：本机书架——书库复制不改原交付、只听本机、Range、阅读页带批注。"""
import json
import threading
import urllib.error
import urllib.request

import pytest

from aiforstudy.errors import InputError
from aiforstudy.shelf.library import READER_ASSETS, add_book, library_index
from aiforstudy.shelf.server import make_server


def _delivery(root):
    for key, title in (('CH01', '复数和平面点集'), ('book', '全书')):
        entry = root / key
        (entry / 'assets').mkdir(parents=True)
        (entry / 'data').mkdir()
        (entry / 'index.html').write_text('<html><head></head><body>'
                                          '<script src="data/data.js"></script></body></html>', encoding='utf-8')
        (entry / 'assets' / 'app.js').write_text('OLD', encoding='utf-8')
        (entry / 'video.mp4').write_bytes(bytes(range(256)) * 4)
        data = {'book': 'b', 'tree': [{'id': key, 'title': title}], 'units': {}}
        (entry / 'data' / 'data.js').write_text('window.__DATA__ = ' + json.dumps(data, ensure_ascii=False) + ';\n',
                                                encoding='utf-8')
    return root


def test_add_book_copies_without_touching_delivery_and_injects_notes(tmp_path):
    delivery = _delivery(tmp_path / 'pkg')
    manifest = add_book(delivery, tmp_path / 'lib', book_id='complex', version='v1', title='复变函数')
    assert (delivery / 'CH01' / 'assets' / 'app.js').read_text(encoding='utf-8') == 'OLD'
    assert 'notes.js' not in (delivery / 'CH01' / 'index.html').read_text(encoding='utf-8')
    entry = tmp_path / 'lib' / 'books' / 'complex' / 'v1' / 'CH01'
    html = (entry / 'index.html').read_text(encoding='utf-8')
    assert 'assets/notes.js' in html and 'assets/notes.css' in html and 'data/version.js' in html
    assert all((entry / 'assets' / name).is_file() for name in READER_ASSETS)
    assert (entry / 'assets' / 'app.js').read_text(encoding='utf-8') != 'OLD'
    rows = manifest['versions'][0]['entries']
    assert [(r['key'], r['title'], r['kind']) for r in rows] == [('CH01', '复数和平面点集', 'chapter'), ('book', '全书', 'book')]
    assert 'video.mp4' in rows[0]['files']
    with pytest.raises(InputError):
        add_book(delivery, tmp_path / 'lib', book_id='complex', version='v1', title='复变函数')
    add_book(delivery, tmp_path / 'lib', book_id='complex', version='v2', title='复变函数')
    index = library_index(tmp_path / 'lib')
    assert index['books'][0]['latest'] == 'v2' and len(index['books'][0]['versions']) == 2


def test_server_is_local_only_serves_ranges_and_refuses_traversal(tmp_path):
    with pytest.raises(ValueError):
        make_server(tmp_path, host='0.0.0.0', port=0)
    add_book(_delivery(tmp_path / 'pkg'), tmp_path / 'lib', book_id='complex', version='v1', title='复变函数')
    httpd = make_server(tmp_path / 'lib', port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}'
    try:
        assert '我的书架' in urllib.request.urlopen(base + '/').read().decode('utf-8')
        assert json.loads(urllib.request.urlopen(base + '/library.json').read())['books'][0]['id'] == 'complex'
        request = urllib.request.Request(base + '/library/complex/v1/CH01/video.mp4', headers={'Range': 'bytes=10-19'})
        with urllib.request.urlopen(request) as response:
            assert response.status == 206 and response.headers['Content-Range'] == 'bytes 10-19/1024'
            assert response.read() == bytes(range(10, 20))
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(base + '/library/../book.json')
        assert caught.value.code == 404
        assert urllib.request.urlopen(base + '/sw.js').headers['Service-Worker-Allowed'] == '/'
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_reader_build_packages_notes_module():
    import sys
    from aiforstudy.pipeline.stage_io import _repo_root
    sys.path.insert(0, str(_repo_root()))
    from blocks.render.build import reader_site_files
    files = reader_site_files({'animations': {}})
    assert 'assets/notes.js' in files and 'assets/notes.css' in files
    template = (_repo_root() / 'blocks/render/templates/index.html').read_text(encoding='utf-8')
    assert 'assets/notes.js' in template and 'assets/notes.css' in template
