"""Exercise the real local service over HTTP, without external network/models."""
from contextlib import contextmanager
from http.client import HTTPConnection
import hashlib
import json
from pathlib import Path
import threading
import uuid
import pytest

A, B = 'a'*64, 'b'*64


def reader(root):
    root.mkdir(); (root/'data').mkdir(); (root/'assets').mkdir()
    (root/'index.html').write_text('<!doctype html><html><head><title>教材</title></head><body>正文</body></html>')
    field = dict(block_id='field', json_pointer='/body_markdown', raw_text='原文', markdown='原文')
    block = dict(block_id='field:0', field_id='field', owner_id='', target_pointer='/body_markdown',
                 token_type='paragraph', token_index=0, source_start_cp=0, source_end_cp=2)
    unit = dict(reader_document={'blocks':[field]}, reader_layout={'blocks':[block]})
    data = dict(book='testbook', scope=['CH01','CH02'], units={'CH01':unit,'CH02':{}})
    (root/'data/data.js').write_text('window.__DATA__ = '+json.dumps(data)+';')
    (root/'assets/video.mp4').write_bytes(b'0123456789')
    return root


@contextmanager
def running(root, database):
    from aiforstudy.community.server import make_server
    server = make_server(root, database, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield server
    finally: server.shutdown(); server.server_close(); thread.join(timeout=2)


def call(server, method='GET', path='/', body=None, token='', origin='same', headers=None):
    port = server.server_address[1]
    h = {'Host':f'127.0.0.1:{port}', 'X-AIFS-Book-Revision':server.context['revision']}
    if origin == 'same': h['Origin'] = f'http://127.0.0.1:{port}'
    elif origin: h['Origin'] = origin
    if token: h['Authorization'] = 'Bearer '+token
    if body is not None:
        body = json.dumps(body).encode(); h['Content-Type'] = 'application/json'
    h.update(headers or {})
    conn = HTTPConnection('127.0.0.1', port, timeout=3)
    conn.request(method, path, body, h); response = conn.getresponse(); data = response.read()
    out = (response.status, dict(response.getheaders()), data)
    conn.close(); return out


def content(**updates):
    base = json.dumps(['CH01', 'text', '/body_markdown', '原文'], ensure_ascii=False, separators=(',', ':'))
    anchor = hashlib.sha256(json.dumps([base, 0], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    d = dict(unit_id='CH01', anchor=anchor, kind='text', quote='原文', nickname='小明', text='我的理解', request_id=str(uuid.uuid4()))
    d.update(updates); return d


def test_two_clients_persist_through_service_restart_and_owner_can_delete(tmp_path):
    root = reader(tmp_path/'book'); db = tmp_path/'data/notes.db'
    with running(root, db) as server:
        status, _, body = call(server,'POST','/api/annotations',content(),A)
        assert status == 201
        note = json.loads(body)['item']
        assert note['is_mine']
        status, _, body = call(server, path='/api/annotations?unit=CH01',token=B)
        assert status == 200 and json.loads(body)['items'][0]['text'] == '我的理解'
        assert json.loads(body)['items'][0]['is_mine'] is False
        assert call(server,'DELETE',f'/api/annotations/{note["id"]}',token=B)[0] == 403
    with running(root, db) as server:
        assert json.loads(call(server,path='/api/annotations?unit=CH01')[2])['total'] == 1
        assert call(server,'DELETE',f'/api/annotations/{note["id"]}',token=A)[0] == 200
        assert json.loads(call(server,path='/api/annotations?unit=CH01')[2])['total'] == 0


def test_index_is_injected_without_changing_book_bytes(tmp_path):
    root = reader(tmp_path/'book'); before = (root/'index.html').read_bytes()
    with running(root,tmp_path/'notes.db') as server:
        status, headers, body = call(server)
        assert status == 200
        assert b'__AIFS_SHARED_NOTES__' in body and b'/community-assets/shared-notes.js' in body
        assert b'__AIFS_SHARED_NOTES__' not in before
        assert (root/'index.html').read_bytes() == before
        assert headers['X-Content-Type-Options'] == 'nosniff'
        assert call(server, path='/api/annotations?unit=unknown')[0] == 400


def test_cross_origin_missing_origin_and_forged_host_cannot_write(tmp_path):
    with running(reader(tmp_path/'book'),tmp_path/'notes.db') as server:
        for origin in ['https://elsewhere.invalid','null','']:
            assert call(server,'POST','/api/annotations',content(),A,origin=origin)[0] == 403
        assert call(server,'POST','/api/annotations',content(),A,headers={'Host':'attacker.invalid'})[0] == 403
        assert call(server,'POST','/api/annotations',content(),token='')[0] == 401
        assert call(server,'OPTIONS','/api/annotations',origin='https://elsewhere.invalid')[0] == 405
        assert json.loads(call(server,path='/api/annotations?unit=CH01')[2])['total'] == 0


def test_traversal_symlinks_database_and_non_asset_files_are_not_served(tmp_path):
    root = reader(tmp_path/'book'); secret = tmp_path/'secret.txt'; secret.write_text('private')
    (root/'assets/escape.json').symlink_to(secret)
    (root/'.git').mkdir(); (root/'.git/config').write_text('private')
    with running(root,tmp_path/'notes.db') as server:
        for url in ['/../secret.txt','/%2e%2e/secret.txt','/assets/escape.json','/.git/config','/notes.db','/api/runs']:
            assert call(server,path=url)[0] == 404
    from aiforstudy.community.server import make_server
    with pytest.raises(ValueError): make_server(root,root/'assets/notes.db',port=0)


def test_media_byte_ranges_and_head_preserve_reader_playback(tmp_path):
    with running(reader(tmp_path/'book'),tmp_path/'notes.db') as server:
        status, h, body = call(server,path='/assets/video.mp4',headers={'Range':'bytes=2-5'})
        assert (status,body,h['Content-Range']) == (206,b'2345','bytes 2-5/10')
        assert call(server,path='/assets/video.mp4',headers={'Range':'bytes=-3'})[2] == b'789'
        assert call(server,path='/assets/video.mp4',headers={'Range':'bytes=20-'})[0] == 416
        status,h,body = call(server,'HEAD','/assets/video.mp4')
        assert status == 200 and body == b'' and h['Content-Length'] == '10'


def test_requests_are_bounded_and_invalid_query_does_not_crash_service(tmp_path):
    with running(reader(tmp_path/'book'),tmp_path/'notes.db') as server:
        assert call(server,'POST','/api/annotations',content(text='字'*6000),A)[0] == 413
        assert call(server,'POST','/api/annotations',content(unit_id='unknown'),A)[0] == 400
        assert call(server,'POST','/api/annotations',content(),A,headers={'Content-Type':'text/plain'})[0] == 415
        for query in ['unit=CH01&before=banana','unit=CH01&before=-1','unit=CH01&limit=999','unit=CH01&unit=CH02','unit=CH01&before='+('9'*100)]:
            assert call(server,path='/api/annotations?'+query)[0] == 400
        assert call(server,'DELETE','/api/annotations/'+('9'*100),token=A)[0] == 400
        assert call(server,path='/api/annotations?unit=CH01')[0] == 200


def test_http_retry_returns_same_record_and_changed_book_uses_new_scope(tmp_path):
    root = reader(tmp_path/'book'); db = tmp_path/'notes.db'; payload = content()
    with running(root,db) as server:
        first = call(server,'POST','/api/annotations',payload,A)
        again = call(server,'POST','/api/annotations',payload,A)
        assert first[0] == 201 and again[0] == 200
        assert json.loads(first[2])['item']['id'] == json.loads(again[2])['item']['id']
    with (root/'data/data.js').open('a') as f: f.write('\n// revised reader\n')
    with running(root,db) as server:
        assert json.loads(call(server,path='/api/annotations?unit=CH01')[2])['total'] == 0


def test_reader_start_rejects_incomplete_metadata_and_cli_registers_entry(tmp_path):
    from aiforstudy.community.server import make_server
    from aiforstudy.cli import build_parser
    args = build_parser().parse_args(['community','--reader',str(tmp_path),'--database',str(tmp_path/'notes.db'),'--port','0'])
    assert args.command == 'community' and args.port == 0
    root = reader(tmp_path/'book'); (root/'data/data.js').write_text('window.__DATA__ = {};')
    with pytest.raises(ValueError): make_server(root,tmp_path/'notes.db',port=0)


def test_stale_page_cannot_publish_into_a_new_book_version(tmp_path):
    with running(reader(tmp_path/'book'),tmp_path/'notes.db') as server:
        status, _, _ = call(server,'POST','/api/annotations',content(),A,
                            headers={'X-AIFS-Book-Revision':'old-reader'})
        assert status == 409
        assert json.loads(call(server,path='/api/annotations?unit=CH01')[2])['total'] == 0


def test_index_symlinks_cannot_serve_files_outside_reader(tmp_path):
    from aiforstudy.community.server import make_server
    root = reader(tmp_path/'book'); secret=tmp_path/'outside.html';secret.write_text('PRIVATE OUTSIDE')
    with running(root,tmp_path/'notes.db') as server:
        (root/'index.html').unlink();(root/'index.html').symlink_to(secret)
        assert call(server)[0] == 404
    with pytest.raises(ValueError): make_server(root,tmp_path/'notes.db',port=0)


def test_stale_reader_get_cannot_display_new_revision_comments(tmp_path):
    root=reader(tmp_path/'book');db=tmp_path/'notes.db'
    with running(root,db) as server: old=server.context['revision']
    with (root/'data/data.js').open('a') as f:f.write('\n// next edition')
    with running(root,db) as server:
        assert call(server,'POST','/api/annotations',content(text='新版感悟'),A)[0] == 201
        status,_,body=call(server,path='/api/annotations?unit=CH01',headers={'X-AIFS-Book-Revision':old})
        assert status == 409
        assert '新版感悟' not in body.decode()


def test_author_edit_conflict_replay_and_target_authority(tmp_path):
    with running(reader(tmp_path/'book'), tmp_path/'notes.db') as server:
        response = call(server, path='/api/annotation-targets?unit=CH01')
        assert response[0] == 200
        target, = json.loads(response[2])['targets']
        body = content(**{k: target[k] for k in ('kind', 'anchor', 'quote')})
        for changes in [dict(anchor='f'*64), dict(quote='其他原文')]:
            assert call(server, 'POST', '/api/annotations', dict(body, **changes), A)[0] == 400
        item = json.loads(call(server,'POST','/api/annotations',body,A)[2])['item']
        url = '/api/annotations/' + str(item['id'])
        change = dict(revision=1, text='修改后')
        assert call(server, 'PATCH', url, change, B)[0] == 403
        assert call(server, 'PATCH', url, change, A, origin='null')[0] == 403
        updated = json.loads(call(server, 'PATCH', url, change, A)[2])['item']
        assert updated['revision'] == 2 and updated['updated_at'] >= item['created_at']
        assert call(server, 'PATCH', url, change, A)[0] == 409
        replay = call(server, 'POST', '/api/annotations', body, A)
        assert replay[0] == 200 and json.loads(replay[2])['item']['text'] == '修改后'
        assert call(server, 'DELETE', url, token=A)[0] == 200
        assert call(server, 'POST', '/api/annotations', body, A)[0] == 410


@pytest.mark.parametrize('filename', ['data/data.js','assets/practice-data.js','assets/practice-media-data.js'])
def test_same_size_preserved_mtime_change_retires_snapshot(tmp_path, filename):
    import os
    root = reader(tmp_path/'book')
    for name, data in [('practice-data.js','window.PRACTICE_EDITIONS={"basic":[]}'),
                       ('practice-media-data.js','window.PRACTICE_MEDIA=[]')]:
        (root/'assets'/name).write_text(data+'; ')
    with running(root,tmp_path/'db') as server:
        assert call(server,path='/'+filename)[0] == 200
        source = root/filename
        stat = source.stat()
        raw = source.read_bytes()
        source.write_bytes(raw.replace(b'testbook',b'nextbook') if filename.startswith('data/') else raw[:-1]+b'\n')
        os.utime(source,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        assert call(server,path='/'+filename)[0] == 409
        assert call(server,path='/api/annotation-targets?unit=CH01')[0] == 409


def test_practice_directory_must_match_served_practice_bytes(tmp_path):
    from aiforstudy.community.server import make_server
    root = reader(tmp_path/'book')
    layer = 'window.PRACTICE_EDITIONS={"basic":[]};'
    (root/'assets/practice-data.js').write_text(layer)
    other = tmp_path/'practice/教材/assets'
    other.mkdir(parents=True)
    (other/'practice-data.js').write_text(layer+' ')
    with pytest.raises(ValueError,match='不一致'):
        make_server(root,tmp_path/'db',port=0,practice=tmp_path/'practice')
    with running(root,tmp_path/'db') as server:
        assert call(server,'POST','/api/annotations',content(),A)[0] == 409
        headers = {'X-AIFS-Practice-Revision':server.context['practice_revision']}
        assert call(server,'POST','/api/annotations',content(),A,headers=headers)[0] == 201


def test_registry_rejects_mismatched_stored_field_hash(tmp_path):
    from aiforstudy.community.server import make_server
    root = reader(tmp_path/'book')
    source = root/'data/data.js'
    source.write_text(source.read_text().replace('"field_id": "field"', '"field_hash": "stale", "field_id": "field"'))
    with pytest.raises(ValueError, match='来源'):
        make_server(root,tmp_path/'db',port=0)


@pytest.mark.parametrize('tag', ['<script src="assets/notes.js"></script>',
                                 '<script defer src="assets/notes.js?v=one"></script>'])
def test_authoritative_private_version_is_bound_at_notes_script(tmp_path, tag):
    root = reader(tmp_path/'book')
    old = '<script>window.__BOOK_VERSION__={version:"old"}</script>'
    (root/'index.html').write_text('<html><head>'+old+tag+'</head><body></body></html>')
    with running(root,tmp_path/'db') as server:
        html = call(server)[2].decode()
        boundary = html[html.index(old)+len(old):html.index(tag)]
        assert server.context['revision'] in boundary and 'window.__BOOK_VERSION__=' in boundary


def test_concurrent_source_replacement_during_capture_rejects_startup(tmp_path, monkeypatch):
    from aiforstudy.community.server import make_server
    from aiforstudy.community.store import StoreError
    root = reader(tmp_path/'book')
    data = root/'data/data.js'
    from aiforstudy.community import book
    open_source = book._source

    def replace_after_read(path, *, read=False):
        signature, contents = open_source(path, read=read)
        if read and path == data:
            replacement = path.with_name('next.js')
            replacement.write_bytes(contents.replace(b'testbook', b'nextbook'))
            replacement.replace(path)
        return signature, contents

    monkeypatch.setattr(book, '_source', replace_after_read)
    with pytest.raises(StoreError) as exc:
        make_server(root,tmp_path/'db',port=0)
    assert exc.value.status == 409
    assert not (tmp_path/'db').exists()


@pytest.mark.parametrize('damage', ['range','duplicate-field','missing-field','projection'])
def test_registry_validates_coordinates_with_actual_renderer(tmp_path,damage):
    from aiforstudy.community.server import make_server
    from aiforstudy.core.digest import digest_of
    root=reader(tmp_path/'book')
    raw='First paragraph.\n\nSecond paragraph.'
    field=dict(block_id='f',json_pointer='/body_markdown',raw_text=raw,markdown=raw)
    blocks=[dict(block_id='f:'+str(i),field_id='f',owner_id='',target_pointer='/body_markdown',
                 token_type='paragraph',token_index=i,source_start_cp=a,source_end_cp=b,field_hash=digest_of(raw))
            for i,a,b in [(0,0,16),(2,18,len(raw))]]
    fields=[field]
    if damage=='range':
        blocks[0].update(source_start_cp=18,source_end_cp=len(raw))
    elif damage=='duplicate-field': fields.append(field.copy())
    elif damage=='missing-field': fields.clear()
    elif damage=='projection': field['markdown']='Other paragraph.\n\nSecond paragraph.'
    unit=dict(reader_document={'blocks':fields},reader_layout={'blocks':blocks})
    (root/'data/data.js').write_text('window.__DATA__='+json.dumps(dict(book='b',scope=['CH01'],units={'CH01':unit}))+';')
    with pytest.raises(ValueError,match='来源|投影'):
        make_server(root,tmp_path/'db',port=0)


def test_http_legacy_anchor_exposes_receipt_only_to_original_request(tmp_path):
    import sqlite3
    root=reader(tmp_path/'book')
    db=tmp_path/'notes.db'
    legacy=content(anchor='d'*64,quote='旧版摘录')
    with running(root,db) as server:
        note,_=server.store.create('CH01',legacy,A)
    with sqlite3.connect(db) as conn:
        conn.execute('DROP TABLE annotation_requests')
        conn.execute('ALTER TABLE annotations DROP COLUMN edit_revision')
        conn.execute('ALTER TABLE annotations DROP COLUMN updated_at')
    url='/api/annotations/'+str(note['id'])
    with running(root,db) as server:
        assert call(server,'POST','/api/annotations',legacy,A)[0]==200
        assert call(server,'POST','/api/annotations',legacy,B)[0]==400
        assert call(server,'POST','/api/annotations',dict(legacy,request_id=str(uuid.uuid4())),A)[0]==400
        assert call(server,'POST','/api/annotations',dict(legacy,text='不同请求'),A)[0]==409
        assert call(server,'PATCH',url,dict(revision=1,text='修改'),A)[0]==200
        assert json.loads(call(server,'POST','/api/annotations',legacy,A)[2])['item']['text']=='修改'
        assert call(server,'DELETE',url,token=A)[0]==200
    with running(root,db) as server:
        assert call(server,'POST','/api/annotations',legacy,A)[0]==410
        assert call(server,'POST','/api/annotations',dict(legacy,text='不同请求'),A)[0]==409
        assert call(server,'POST','/api/annotations',dict(legacy,nickname='不同署名'),A)[0]==409
        assert json.loads(call(server,path='/api/annotations?unit=CH01')[2])['total']==0


def test_shared_reader_requires_working_trusted_node(tmp_path,monkeypatch):
    from aiforstudy.community.server import make_server
    root=reader(tmp_path/'book')
    monkeypatch.setenv('AIFS_BROWSER_NODE',str(tmp_path/'missing-node'))
    with pytest.raises(ValueError,match='AIFS_BROWSER_NODE'):
        make_server(root,tmp_path/'db',port=0)
    assert not (tmp_path/'db').exists()


def test_verified_reader_reference_projection_uses_visible_label_and_source_range(tmp_path):
    from aiforstudy.core.digest import digest_of
    root=reader(tmp_path/'book')
    raw='参阅 [[NOTE:X|原注]]。'
    markdown='参阅 [原注](#reader-'+'a'*20+')。'
    field=dict(block_id='f',json_pointer='/body_markdown',raw_text=raw,markdown=markdown,visible_text='参阅 原注。',
               marker_spans=[dict(raw_start=3,raw_end=len(raw)-1,visible_start=3,visible_end=5)])
    block=dict(block_id='f:0',field_id='f',owner_id='',target_pointer='/body_markdown',token_index=0,
               token_type='paragraph',source_start_cp=0,source_end_cp=len(raw),field_hash=digest_of(raw))
    unit=dict(reader_document={'blocks':[field]},reader_layout={'blocks':[block]})
    (root/'data/data.js').write_text('window.__DATA__='+json.dumps(dict(book='b',scope=['CH01'],units={'CH01':unit}))+';')
    with running(root,tmp_path/'notes') as server:
        row,=json.loads(call(server,path='/api/annotation-targets?unit=CH01')[2])['targets']
        assert row['quote']=='参阅 原注。'
        assert row['source']==dict(field='f',start_cp=0,end_cp=len(raw))
        assert call(server,'POST','/api/annotations',content(anchor=row['anchor'],quote=row['quote']),A)[0]==201
        assert call(server,'POST','/api/annotations',content(anchor=row['anchor'],quote=raw),A)[0]==400


def test_historic_source_tuples_counts_pages_and_receipts(tmp_path):
    root = reader(tmp_path/'book')
    with running(root, tmp_path/'notes.db') as server:
        valid, old_quote, old_kind, unknown, recent = (
            content(), content(quote='旧引文'), content(kind='media'), content(anchor='d'*64), content()
        )
        rows = [server.store.create('CH01', item, A)[0]
                for item in (valid, old_quote, old_kind, unknown, recent)]
        def page(suffix):
            status, _, body = call(server, path='/api/annotations?unit=CH01'+suffix, token=A)
            assert status == 200
            return json.loads(body)

        history = page('&limit=4')
        assert history['counts'] == {valid['anchor']: 2}
        assert history['total'] == 5 and history['next_before'] == rows[1]['id']
        assert [(r['id'], r['kind'], r['quote'], r['is_mine']) for r in history['items']] == [
            (r['id'], r['kind'], r['quote'], True) for r in reversed(rows[1:])]
        target_query = '&limit=1&anchor='+valid['anchor']
        first = page(target_query)
        assert first['unit_total'] == 5 and first['total'] == 2 and first['next_before'] == rows[-1]['id']
        last = page(target_query+'&before='+str(first['next_before']))
        assert last['next_before'] is None and last['items'][0]['id'] == rows[0]['id']
        assert page('&anchor='+unknown['anchor'])['total'] == 0
        url = '/api/annotations/'+str(rows[1]['id'])
        assert call(server,'PATCH',url,dict(revision=1,text='旧作者修订'),A)[0] == 200
        replay = json.loads(call(server,'POST','/api/annotations',old_quote,A)[2])['item']
        assert replay['quote'] == old_quote['quote'] and replay['text'] == '旧作者修订'
        assert call(server,'DELETE',url,token=B)[0] == 403
        assert call(server,'DELETE',url,token=A)[0] == 200
        assert call(server,'POST','/api/annotations',old_quote,A)[0] == 410
