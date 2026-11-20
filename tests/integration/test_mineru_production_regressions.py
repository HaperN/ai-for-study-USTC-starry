"""Read-only production audit. Temporary outputs only; network is loopback or denied.

Desired-behavior assertions: failures are acceptance defects, not patches.
Run from project root with PYTHONPATH=src:lib:tests/integration:. python3 -m pytest -q THISFILE
"""
import io
import json
import sys
import threading
import zipfile
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'lib'), str(ROOT), str(ROOT / 'tests/integration')]
from blocks.parse.mineru import fetch as F
from blocks.parse.mineru import adapt as A

def layout(text='ACCEPTANCE SOURCE'):
    return {'_version_name': '3.4.4', '_backend': 'hybrid', '_ocr_enable': True,
            'pdf_info': [{'page_idx': 0, 'page_size': [595, 842],
                          'para_blocks': [{'type': 'text', 'bbox': [0, 0, 200, 30],
                                           'lines': [{'bbox': [0, 0, 200, 30],
                                                      'spans': [{'type': 'text', 'content': text}]}]}],
                          'discarded_blocks': []}]}

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding='utf-8')

def denied(*a, **kw):
    raise AssertionError('Unexpected external network: request denied by acceptance test')

@pytest.fixture(autouse=True)
def deny_external(monkeypatch):
    monkeypatch.setattr(F, '_request', denied)
    monkeypatch.setattr(F, 'bare_put', denied)
    monkeypatch.setenv('MINERU_API_TOKEN', 'controlled-acceptance-token-not-real')

def test_real_protocol_functions_loopback_submit_put_poll_download_adapt(tmp_path, monkeypatch):
    """Actual HTTP helper stack, generated fixture only. No hosted API request."""
    calls = []
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('layout.json', json.dumps(layout()))
        z.writestr('fixture_content_list.json', json.dumps([{'type': 'text', 'text': 'ACCEPTANCE SOURCE', 'page_idx': 0}]))
        z.writestr('full.md', 'ACCEPTANCE SOURCE')
    class Handler(BaseHTTPRequestHandler):
        polls = 0
        def log_message(self, *args):
            pass
        def respond(self, data, content_type='application/json'):
            raw = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(('POST', self.path, body))
            assert self.path == '/api/v4/file-urls/batch'
            assert body['model_version'] == 'vlm'
            assert body['files'] == [{'name': 'fixture.pdf', 'is_ocr': False}]
            self.respond({'code': 0, 'data': {'batch_id': 'local-batch', 'file_urls': [base + '/upload?sig=fixture']}})
        def do_PUT(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            calls.append(('PUT', self.path, {'content_type': self.headers.get('Content-Type'), 'body': body.decode()}))
            self.respond({})
        def do_GET(self):
            calls.append(('GET', self.path))
            if self.path == '/result.zip':
                self.respond(archive.getvalue(), 'application/zip')
            else:
                self.__class__.polls += 1
                item = {'state': 'running', 'extract_progress': {'total_pages': 1, 'extracted_pages': 0}}
                if self.polls > 1:
                    item = {'state': 'done', 'full_zip_url': base + '/result.zip'}
                self.respond({'code': 0, 'data': {'extract_result': [item]}})
    server = HTTPServer(('127.0.0.1', 0), Handler)
    base = f'http://127.0.0.1:{server.server_port}'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Recover real helpers from a separate import, guarded to loopback only.
    import importlib.util
    spec = importlib.util.spec_from_file_location('acceptance_fetch_real', ROOT / 'blocks/parse/mineru/fetch.py')
    real = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = real
    spec.loader.exec_module(real)
    def local_request(url, **kw):
        assert url.startswith(base + '/')
        return real._request(url, **kw)
    def local_put(url, data, **kw):
        assert url.startswith(base + '/')
        return real.bare_put(url, data, **kw)
    monkeypatch.setattr(F, 'API', base + '/api/v4')
    monkeypatch.setattr(F, '_request', local_request)
    monkeypatch.setattr(F, 'bare_put', local_put)
    monkeypatch.setattr(F, 'page_count', lambda _: 1)
    pdf = tmp_path / 'fixture.pdf'
    pdf.write_bytes(b'GENERATED FIXTURE BYTES')
    try:
        record = F.fetch(pdf, tmp_path / 'parse', token='stub-token', interval_s=0, log=lambda *_: None)
        result = A.adapt(tmp_path / 'parse/mineru', tmp_path / 'adapted')
        # Exercise the production default factory and preparation fallback too.
        # No injected Providers or stage registry; only HTTP destinations are loopback.
        from support.preparation_book import _mini_book, _request
        from aiforstudy.pipeline.engine import build_engine, execute
        from aiforstudy.core.digest import digest_file
        ws = _mini_book(tmp_path / 'engine', with_mineru=False)
        source = ws.root / 'fixture.pdf'
        source.write_bytes(b'GENERATED FIXTURE BYTES')
        config = json.loads((ws.root / 'config/books.json').read_text())
        config['books']['mini-book']['sources'] = [
            {'kind': 'pdf', 'path': 'fixture.pdf', 'sha256': digest_file(source)}]
        write_json(ws.root / 'config/books.json', config)
        import shutil
        shutil.rmtree(ws.root / 'book/parse')
        engine = build_engine(_request('mini-book'), ws, until='mineru')
        assert engine.plan.resolved_scope is None
        engine_result, store = execute(engine)
        assert store.node_states()['mineru'].execution_state.value == 'succeeded'
        assert not (store.work / 'mineru-service-usage.json').exists()
        assert (store.work / 'prepared-parse/document.md').is_file()
    finally:
        server.shutdown()
        server.server_close()
    assert record['merged']['pages'] == 1
    assert not result.audit['checks']['hard_rules']
    assert calls[1][1] == '/upload?sig=fixture'
    assert calls[1][2]['content_type'] is None
    assert (tmp_path / 'adapted/document.md').is_file()


def seed_stale(tmp_path):
    pdf = tmp_path / 'new.pdf'
    pdf.write_bytes(b'NEW SOURCE BYTES')
    write_json(tmp_path / 'out/_fetch_chunks/all/layout.json', layout('OLD SOURCE CONTENT'))
    return pdf, tmp_path / 'out'

def test_refetch_forwards_refresh_to_existing_chunk(tmp_path, monkeypatch):
    pdf, out = seed_stale(tmp_path)
    monkeypatch.setattr(F, 'page_count', lambda _: 1)
    with pytest.raises(AssertionError, match='Unexpected external network'):
        F.fetch(pdf, out, token='stub-token', refetch=True, log=lambda *_: None)
    assert not (out / 'mineru/layout.json').exists()

def test_existing_chunk_requires_matching_source_hash(tmp_path, monkeypatch):
    pdf, out = seed_stale(tmp_path)
    monkeypatch.setattr(F, 'page_count', lambda _: 1)
    with pytest.raises(AssertionError, match='Unexpected external network'):
        F.fetch(pdf, out, token='stub-token', log=lambda *_: None)
    assert not (out / 'mineru/layout.json').exists()


def test_poll_rejects_response_after_deadline(monkeypatch):
    class Clock:
        value = 0
        def monotonic(self): return self.value
        def sleep(self, n): self.value += n
    clock = Clock()
    def late_result(*a, **kw):
        clock.value += 61
        return {'code': 0, 'data': {'extract_result': [{'state': 'done', 'full_zip_url': 'unused'}]}}
    monkeypatch.setattr(F, 'time', clock)
    monkeypatch.setattr(F, '_api_json', late_result)
    with pytest.raises(F.MinerUFetchError, match='超过'):
        F.poll('fake', 'fake', timeout_s=1, log=lambda *_: None)

def test_engine_checks_old_layout_hash_before_readapt(tmp_path, monkeypatch):
    from support.preparation_book import _mini_book, _request
    from aiforstudy.pipeline.engine import build_engine, execute
    from aiforstudy.pipeline.services import Providers
    ws = _mini_book(tmp_path)
    parse = ws.root / 'book/parse'
    write_json(parse / 'mineru/layout.json', layout('TAMPERED LOCAL CONTENT'))
    (parse / 'document.md').unlink()
    engine = build_engine(_request('mini-book'), ws, until='mineru', providers=Providers())
    result, store = execute(engine)
    state = store.node_states()['mineru']
    assert state.execution_state.value == 'blocked', (
        'Tampered layout was adapted and original ingest hash overwritten; state=' + state.execution_state.value)

@pytest.mark.parametrize('stop', ['cancel', 'control'])
def test_engine_does_not_start_next_chunk_after_stop(tmp_path, monkeypatch, stop):
    from support.preparation_book import _mini_book, _request
    from aiforstudy.core.digest import digest_file
    from aiforstudy.pipeline.engine import build_engine, execute
    from aiforstudy.pipeline.services import Providers
    ws = _mini_book(tmp_path, with_mineru=False)
    pdf = ws.root / 'book/source.pdf'
    pdf.write_bytes(b'GENERATED SOURCE FOR TWO CHUNKS')
    config = json.loads((ws.root / 'config/books.json').read_text())
    config['books']['mini-book']['sources'] = [
        {'kind': 'pdf', 'path': 'book/source.pdf', 'sha256': digest_file(pdf)}]
    write_json(ws.root / 'config/books.json', config)
    monkeypatch.setattr(F, 'page_count', lambda _: 201)
    calls = []
    def controlled_chunk(pdf, dest, token, **kw):
        calls.append(kw['page_ranges'])
        if len(calls) == 1:
            if stop == 'cancel':
                engine.services.cancel.cancel('acceptance cancel after first chunk')
            else:
                engine.services.control.stop('acceptance execution stop after first chunk')
        write_json(dest / 'layout.json', layout('GENERATED CHUNK'))
        return {'batch_id': 'local', 'states': ['done']}
    monkeypatch.setattr(F, '_fetch_one', controlled_chunk)
    engine = build_engine(_request('mini-book'), ws, until='mineru', providers=Providers())
    result, store = execute(engine)
    assert len(calls) == 1, f'{stop} after first chunk still started chunks {calls}; terminal={result.status}'
