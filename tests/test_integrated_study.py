import json
import socket
import sys
from urllib.parse import quote

import pytest
from test_shared_annotations_http import A, call, content, reader

from aiforstudy.cli import main
from aiforstudy.integrated.config import load_config
from aiforstudy.integrated.server import running


def prepare(root, **changes):
    practice = root / 'practice'
    practice.mkdir()
    book = reader(practice / '教材')
    (book / 'assets/practice-data.js').write_text('window.PRACTICE_EDITIONS={"basic":[]};')
    (practice / 'index.html').write_text('<a href="制作说明.html">说明</a>')
    (practice / '基础版-习题集.html').write_text('<a href="教材/index.html#/CH01">回到教材</a>')
    assets = practice / 'assets'
    assets.mkdir()
    (assets / 'practice.css').write_text('/*public*/')
    (assets / 'practice.js').symlink_to(root / 'private')
    (root / 'private').write_text('PRIVATE')
    workspace = root / 'workspace/config'
    workspace.mkdir(parents=True)
    (workspace / 'books.json').write_text('{"books":{}}')
    config = dict(reader='practice/教材', practice='practice', database='state/notes.db',
                  port=0, workbench_workspace='workspace', workbench_port=0)
    config.update(changes)
    path = root / 'config.json'
    path.write_text(json.dumps(config))
    return path


def test_cli_check_is_read_only(tmp_path, monkeypatch, capsys):
    path = prepare(tmp_path)
    from aiforstudy.ui.server import ControlServer
    monkeypatch.setattr(ControlServer, 'mark_interrupted', lambda *_: pytest.fail('started workbench'))
    assert main(['integrated', '--config', str(path), '--check']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['node'] and report['revision'] and report['practice_revision']
    assert not (tmp_path/'state').exists()


@pytest.mark.parametrize('change', [
    {'port':True}, {'port':65536}, {'workbench_url':'http://localhost:8900'},
    {'ocr_python':'python'}, {'ocr_python':'missing', 'ocr_model':'missing'},
    {'database':'practice/state.db'}, {'reader':'missing'}, {'practice':'missing'},
])
def test_invalid(tmp_path, change):
    path = prepare(tmp_path, **change)
    assert main(['integrated','--config',str(path),'--check']) != 0


@pytest.mark.parametrize('url', ['http://localhost:2000/?token=x','http://user@localhost/',
    'http://localhost/#secret', 'https://example.org', 'file:///tmp/file','http://localhost:8792'])
def test_link_denial(tmp_path, url):
    path = prepare(tmp_path)
    cfg = json.loads(path.read_text())
    del cfg['workbench_workspace'], cfg['workbench_port']
    path.write_text(json.dumps(dict(cfg, workbench_url=url,port=8792)))
    with pytest.raises(ValueError):
        load_config(path)


def test_integrated_routes_revisions_and_shutdown(tmp_path):
    config = load_config(prepare(tmp_path))
    with running(config) as server:
        port = server.server_address[1]
        from urllib.parse import urlsplit
        workbench_port = urlsplit(server.workbench_url).port
        assert port != workbench_port
        assert socket.create_connection(('127.0.0.1', workbench_port),1).close() is None
        get = lambda path: call(server, path=quote(path,safe='/%?#=&'))
        assert server.workbench_url.encode() in get('/start')[2]
        assert b'/practice/' in get('/')[2] and b'/start' in get('/')[2]
        assert '基础版-习题集.html' in get('/practice/')[2].decode()
        assert '制作说明' not in get('/practice/')[2].decode()
        assert get('/practice/基础版-习题集.html')[0] == 200
        assert get('/practice/assets/practice.css')[2] == b'/*public*/'
        assert get('/practice/教材/index.html?practice=full')[1]['Location'] == '/?practice=full'
        for url in ('/assets/private.json','/data/extra.js','/practice/制作说明.html',
                    '/practice/assets/practice.js','/practice/%2e%2e/private',
                    '/practice/catalog.json','/practice/evidence/a.pdf','/practice/state.db',
                    '/assets/purpose-audio/file.json','/revision-report.json','/api/runs'):
            assert get(url)[0] == 404, url
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'):
            assert call(server, method, '/api/jobs')[0] >= 400
        assert call(server,'POST','/api/annotations',content(),A)[0] == 409
        headers = {'X-AIFS-Practice-Revision':server.context['practice_revision']}
        assert call(server,'POST','/api/annotations',content(),A,headers=headers)[0] == 201
    for closed in (port,workbench_port):
        with pytest.raises(OSError):
            socket.create_connection(('127.0.0.1',closed),1)


def test_workbench_bind_failure_closes_reader_and_ocr(tmp_path, monkeypatch):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0))
        port = probe.getsockname()[1]
    python = tmp_path/'venv/bin/python'
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    model = tmp_path/'model'
    model.mkdir()
    (model/'config.json').write_text('{}')
    closed = []
    class Offline:
        def __init__(self, interpreter, model):
            assert interpreter == python
        def close(self):
            closed.append(True)
    monkeypatch.setattr('aiforstudy.integrated.server.LocalRecognizer',Offline)
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1',0))
        occupied.listen()
        cfg = load_config(prepare(tmp_path, port=port, workbench_port=occupied.getsockname()[1],
                                  ocr_python='venv/bin/python', ocr_model='model'))
        with pytest.raises(OSError), running(cfg):
            pytest.fail('bind succeeded')
    assert closed == [True]
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',port))


def test_image_ocr_uses_both_revisions_and_closes(tmp_path, monkeypatch):
    from http.client import HTTPConnection
    from test_shared_annotation_images import picture
    model = tmp_path/'model'
    model.mkdir()
    (model/'config.json').write_text('{}')
    path = prepare(tmp_path, ocr_python=sys.executable, ocr_model='model')
    events = []
    class Offline:
        def __init__(self, *args):
            events.append('ready')
        def recognize(self, data, mime):
            assert data and mime == 'image/png'
            events.append('recognize')
            return {'text':'单位圆','truncated':False,'engine':'fixture'}
        def close(self):
            events.append('close')
    monkeypatch.setattr('aiforstudy.integrated.server.LocalRecognizer',Offline)
    assert main(['integrated','--config',str(path),'--check']) == 0
    assert not events
    with running(load_config(path)) as server:
        assert events == ['ready']
        port = server.server_address[1]
        headers = {'Origin':f'http://127.0.0.1:{port}','Authorization':'Bearer '+A,
                   'Content-Type':'image/png','X-AIFS-Unit':'CH01','X-Upload-Request':'a'*32,
                   'X-AIFS-Book-Revision':server.context['revision']}
        def upload():
            conn = HTTPConnection('127.0.0.1',port,timeout=3)
            conn.request('POST','/api/annotation-images',picture(),headers)
            response = conn.getresponse()
            result = response.status,json.loads(response.read())
            conn.close()
            return result
        assert upload()[0] == 409
        rev = {'X-AIFS-Practice-Revision':server.context['practice_revision']}
        headers.update(rev)
        status,result = upload()
        assert status == 201
        url = result['image']['url']+'/recognize'
        assert call(server,'POST',url,{},A)[0] == 409
        assert call(server,'POST',url,{},A,headers=rev)[0] == 200
        assert events == ['ready','recognize']
    assert events[-1] == 'close'


def test_original_question_pdf_and_gallery_paths(tmp_path):
    cfg = load_config(prepare(tmp_path))
    original = '试卷包/往年原卷/复变函数A-B/complexexams.pdf'
    exam = cfg.practice/original
    exam.parent.mkdir(parents=True)
    exam.write_bytes(b'original pdf')
    (cfg.practice/'试卷包/index.html').write_text('<a href="往年原卷/复变函数A-B/complexexams.pdf">原卷</a>')
    (cfg.reader/'assets/textbook.pdf').write_bytes(b'textbook pdf')
    gallery = 'assets/narrated/CH01/MR-abcdef/narrated.txt'
    transcript = cfg.reader/gallery
    transcript.parent.mkdir(parents=True)
    transcript.write_bytes(b'captions')
    # A generated book stores narrated resource paths relative to its per-request base.
    data = cfg.reader/'data/data.js'
    data.write_text(data.read_text().replace('"scope"', '"animation":{"base":"assets/narrated/CH01/MR-abcdef/"}, "scope"'))
    with running(cfg) as server:
        def get(path):
            return call(server,path=quote(path,safe='/?='))
        assert get('/'+original)[1]['Location'] == quote('/practice/'+original)
        assert get('/practice/'+original)[2] == b'original pdf'
        assert get('/教材/assets/textbook.pdf')[2] == b'textbook pdf'
        assert get('/practice/教材/'+gallery)[2] == b'captions'
        assert get('/教材/index.html?practice=full')[1]['Location'] == '/?practice=full'


def test_real_font_names_and_files_outside_projection(tmp_path):
    cfg = load_config(prepare(tmp_path))
    for root in (cfg.practice,cfg.reader):
        fonts = root/'assets/fonts'
        fonts.mkdir()
        (fonts/'KaTeX_Size4-Regular.woff2').write_bytes(b'size4')
        (fonts/'KaTeX_Private-Regular.woff2').write_bytes(b'private')
    for name in ('assets/private.js','assets/purpose-audio/'+'a'*64+'.json',
                 'assets/source/'+'f'*64+'.pdf','assets/practice-media/mapping/render.json'):
        for root in (cfg.reader,cfg.practice):
            f = root/name
            f.parent.mkdir(parents=True,exist_ok=True)
            f.write_text('secret')
    with running(cfg) as server:
        for prefix in ('/','/practice/'):
            assert call(server,path=prefix+'assets/fonts/KaTeX_Size4-Regular.woff2')[2] == b'size4'
            for name in ('assets/fonts/KaTeX_Private-Regular.woff2','assets/private.js',
                         'assets/purpose-audio/'+'a'*64+'.json','assets/source/'+'f'*64+'.pdf',
                         'assets/practice-media/mapping/render.json'):
                assert call(server,path=prefix+name)[0] == 404


def test_linked_reader_only(tmp_path):
    path = prepare(tmp_path)
    cfg = json.loads(path.read_text())
    for field in ('workbench_workspace','workbench_port','practice'):
        del cfg[field]
    cfg.update(workbench_url='http://localhost:18917/',reader='book')
    reader(tmp_path/'book')
    path.write_text(json.dumps(cfg))
    with running(load_config(path)) as server:
        page = call(server,path='/start')[2]
        assert b'http://localhost:18917/' in page and b'/practice/' not in page
        assert call(server,path='/practice/')[0] == 404
        assert b'practice_revision' not in call(server)[2]


def test_check_requires_trusted_node_and_preserves_existing_database(tmp_path, monkeypatch, capsys):
    import sqlite3
    cfg = prepare(tmp_path, database='old.db')
    db = tmp_path/'old.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE old_notes (text)')
        conn.execute("INSERT INTO old_notes VALUES ('保存原样')")
    before = db.read_bytes()
    assert main(['integrated','--config',str(cfg),'--check']) == 0
    monkeypatch.setenv('AIFS_BROWSER_NODE',str(tmp_path/'missing-node'))
    assert main(['integrated','--config',str(cfg),'--check']) != 0
    assert 'Node' in capsys.readouterr().err
    assert db.read_bytes() == before


def test_changed_source_retires_workbook_data(tmp_path):
    cfg = load_config(prepare(tmp_path))
    with running(cfg) as server:
        source = cfg.reader/'assets/practice-data.js'
        assert call(server,path='/practice/assets/practice-data.js')[2] == source.read_bytes()
        source.write_bytes(source.read_bytes()+b' ')
        for route in ('/assets/practice-data.js','/practice/assets/practice-data.js','/api/identity','/'):
            assert call(server,path=route)[0] == 409


@pytest.mark.parametrize('location', [
    'book/data/data.js', 'book/data', 'book/assets/practice-data.js',
    'book/assets/practice-media-data.js', 'practice/教材/assets/practice-data.js',
    'practice/教材/assets/practice-media-data.js', 'practice/教材',
])
def test_frozen_sources_reject_symlinks_before_db_or_bind(tmp_path, location, monkeypatch):
    import shutil
    from aiforstudy.community.book import MEDIA
    config_path = prepare(tmp_path, reader='book')
    comparison = tmp_path/'practice/教材'
    (comparison/MEDIA).write_text('window.PRACTICE_MEDIA=[];')
    shutil.copytree(comparison, tmp_path/'book')
    public_path = tmp_path/location
    private_path = public_path.with_name('private-source')
    public_path.rename(private_path)
    public_path.symlink_to(private_path, target_is_directory=private_path.is_dir())

    # Neither workflow may touch the database or bind any listener on this input.
    def unexpected_bind(*args):
        pytest.fail('bound a listener for a rejected source')
    monkeypatch.setattr(socket.socket, 'bind', unexpected_bind)
    assert main(['integrated','--config',str(config_path),'--check']) != 0
    with pytest.raises(ValueError), running(load_config(config_path)):
        pytest.fail('started with a symlinked critical source')
    assert not (tmp_path/'state').exists()


@pytest.mark.parametrize('status', ['queued', 'running'])
def test_workbench_bind_preserves_run_history_and_success_recovers(tmp_path, status):
    from dataclasses import replace
    from aiforstudy.pipeline.graph import GRAPH_VERSION
    from aiforstudy.pipeline.state import RunStore, book_runs_root
    cfg = load_config(prepare(tmp_path))
    run = book_runs_root(cfg.workbench_workspace,'testbook')/'synthetic-run'
    run.mkdir(parents=True)
    store = RunStore(run, run.name)
    store.state_path.write_text(json.dumps(dict(status=status, graph_version=GRAPH_VERSION)))
    store.emit('run.started',message='fixture')
    log = run/'events.jsonl'
    original = store.state_path.read_bytes(), log.read_bytes()
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1',0))
        occupied.listen()
        with pytest.raises(OSError), running(replace(cfg,workbench_port=occupied.getsockname()[1])):
            pytest.fail('bound an occupied port')
    assert (store.state_path.read_bytes(),log.read_bytes()) == original
    with running(cfg):
        assert store.load_state()['status'] == 'interrupted'
        assert [e.kind for e in store.events_after()] == ['run.started','run.finished']


def test_symlink_replacing_captured_directory_invalidates_reader(tmp_path):
    cfg = load_config(prepare(tmp_path))
    with running(cfg) as server:
        public_dir = cfg.reader/'data'
        saved_dir = cfg.reader/'moved'
        public_dir.rename(saved_dir)
        public_dir.symlink_to(saved_dir, target_is_directory=True)
        for path in ('/','/data/data.js','/api/identity','/practice/assets/practice-data.js'):
            assert call(server,path=path)[0] == 409


@pytest.mark.parametrize('key', ['reader','practice'])
def test_configured_source_directory_rejects_symlinked_parent(tmp_path, key):
    path = prepare(tmp_path)
    alias = tmp_path/'linked-practice'
    alias.symlink_to(tmp_path/'practice', target_is_directory=True)
    values = json.loads(path.read_text())
    values[key] = 'linked-practice/教材' if key == 'reader' else 'linked-practice'
    path.write_text(json.dumps(values))
    assert main(['integrated','--config',str(path),'--check']) != 0
    assert not (tmp_path/'state').exists()
