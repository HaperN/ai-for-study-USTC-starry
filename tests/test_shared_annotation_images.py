"""Images are real decoded bytes; publication controls whether other readers see them."""
from io import BytesIO
import json,struct,zlib,uuid
from http.client import HTTPConnection
import pytest
from PIL import Image
from test_shared_annotations_store import store,payload,A,B
from test_shared_annotations_http import reader,running,call,content


def picture(format='PNG',exif=None):
    out=BytesIO();im=Image.new('RGB',(64,32),'white');im.putpixel((8,9),(0,0,0))
    im.save(out,format=format,**({'exif':exif} if exif else {}));return out.getvalue()


def upload(s,unit='CH01',token=A,data=None,request_id=None):
    from aiforstudy.community.images import normalize_image
    return s.upload_image(unit,normalize_image(data or picture()),request_id or str(uuid.uuid4()),token)


def test_normalized_images_strip_metadata_rotate_and_reject_executables():
    from aiforstudy.community.images import normalize_image
    from aiforstudy.community.store import StoreError
    exif=Image.Exif();exif[274]=6;exif[315]='do not publish this metadata'
    n=normalize_image(picture('JPEG',exif))
    assert (n.width,n.height,n.mime)==(32,64,'image/jpeg')
    with Image.open(BytesIO(n.data)) as im:assert not im.getexif()
    for blob in [b'<svg onload="alert(1)"></svg>',b'<html>not an image</html>',b'broken PNG',b'x'*(10*1024*1024+1)]:
        with pytest.raises(StoreError):normalize_image(blob)
    # Oversized PNG dimensions are rejected before pixel allocation.
    def chunk(tag,data):return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
    bomb=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',100000,100000,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'))+chunk(b'IEND',b'')
    with pytest.raises(StoreError):normalize_image(bomb)


def test_pending_images_are_private_then_publish_atomically_and_survive_restart(tmp_path):
    from aiforstudy.community.store import StoreError
    db=tmp_path/'notes.db';s=store(db);im=upload(s)
    assert s.image(im['id'],A)['data']
    for identity in ['',B]:
        with pytest.raises(StoreError) as exc:s.image(im['id'],identity)
        assert exc.value.status==404
    p=payload(text='',images=[{'id':im['id'],'presentation':'image'}])
    note,made=s.create('CH01',p,A)
    assert made and note['text']=='' and len(note['images'])==1
    assert note['images'][0]['url']=='/api/annotation-images/'+im['id']
    reopened=store(db);assert reopened.image(im['id'],B)['data']==s.image(im['id'],A)['data']
    assert reopened.list('CH01',B)['items'][0]['images'][0]['id']==im['id']
    assert s.create('CH01',p,A)[1] is False
    s.delete(note['id'],A)
    with pytest.raises(StoreError):s.image(im['id'],A)


def test_cannot_publish_foreign_wrong_unit_or_already_bound_images(tmp_path):
    from aiforstudy.community.store import StoreError
    s=store(tmp_path/'notes.db');im=upload(s)
    for who,unit in [(B,'CH01'),(A,'CH02')]:
        with pytest.raises(StoreError):s.create(unit,payload(images=[{'id':im['id'],'presentation':'image'}]),who)
    assert s.list('CH01')['total']==0
    n,_=s.create('CH01',payload(images=[{'id':im['id'],'presentation':'image'}]),A)
    with pytest.raises(StoreError):s.create('CH01',payload(images=[{'id':im['id'],'presentation':'image'}]),A)
    assert s.list('CH01')['total']==1
    with pytest.raises(StoreError):s.remove_image(im['id'],A)


def test_image_retry_limits_and_cross_revision_scope(tmp_path):
    from aiforstudy.community.store import StoreError
    s=store(tmp_path/'notes.db');rid=str(uuid.uuid4());a=upload(s,request_id=rid);b=upload(s,request_id=rid)
    assert a['id']==b['id']
    with pytest.raises(StoreError):store(tmp_path/'notes.db','v2').image(a['id'],A)
    for i in range(11):upload(s)
    with pytest.raises(StoreError) as exc:upload(s)
    assert exc.value.status==429
    s.remove_image(a['id'],A);assert upload(s)['id']


def test_no_blank_or_more_than_three_images_and_no_recognition_forgery(tmp_path):
    from aiforstudy.community.store import StoreError
    s=store(tmp_path/'notes.db');images=[upload(s) for _ in range(4)]
    cases=[payload(text=''),payload(images=[{'id':im['id'],'presentation':'image'} for im in images]),
           payload(images=[{'id':images[0]['id'],'presentation':'recognized','confirmed':True,'text':'pretend OCR'}])]
    for p in cases:
        with pytest.raises(StoreError):s.create('CH01',p,A)
    assert s.list('CH01')['total']==0


def post_image(server,data,token=A,unit='CH01',request_id=None):
    port=server.server_address[1];conn=HTTPConnection('127.0.0.1',port,timeout=5)
    conn.request('POST','/api/annotation-images',data,{'Content-Type':'image/png','Authorization':'Bearer '+token,
        'Origin':f'http://127.0.0.1:{port}','X-AIFS-Book-Revision':server.context['revision'],
        'X-AIFS-Unit':unit,'X-Upload-Request':request_id or str(uuid.uuid4())})
    response=conn.getresponse();result=(response.status,json.loads(response.read()));conn.close();return result


def test_real_http_upload_preview_pure_image_publication_and_deletion(tmp_path):
    with running(reader(tmp_path/'book'),tmp_path/'notes.db') as server:
        status,value=post_image(server,picture());assert status==201;im=value['image']
        assert call(server,path=im['url'])[0]==404
        status,h,blob=call(server,path=im['url'],token=A);assert status==200 and h['Content-Type']=='image/png'
        with Image.open(BytesIO(blob)) as decoded:assert decoded.size==(64,32)
        body=content(text='',images=[{'id':im['id'],'presentation':'image'}])
        status,_,raw=call(server,'POST','/api/annotations',body,A);assert status==201
        note=json.loads(raw)['item'];assert call(server,path=im['url'])[0]==200
        assert call(server,'DELETE','/api/annotation-images/'+im['id'],token=B)[0] in (403,404)
        assert call(server,'DELETE','/api/annotations/'+str(note['id']),token=A)[0]==200
        assert call(server,path=im['url'])[0]==404
        assert post_image(server,b'<svg onload="alert(1)"></svg>')[0] in (400,415)
        assert post_image(server,picture(),unit='unknown')[0]==400


def test_pending_expiry_never_removes_published_images(tmp_path):
    import sqlite3,time
    from aiforstudy.community.store import StoreError
    s=store(tmp_path/'notes.db');pending=upload(s);published=upload(s)
    s.create('CH01',payload(images=[{'id':published['id'],'presentation':'image'}]),A)
    with sqlite3.connect(s.path) as db:db.execute('UPDATE annotation_images SET created_at=?',(time.time()-90000,))
    upload(s)
    with pytest.raises(StoreError):s.image(pending['id'],A)
    assert s.image(published['id'])['data']


def test_webp_dimensions_are_rejected_before_native_decoder_allocation(monkeypatch):
    from aiforstudy.community.images import normalize_image
    from aiforstudy.community.store import StoreError
    def chunk(kind,body):return kind+struct.pack('<I',len(body))+body+(b'\0' if len(body)%2 else b'')
    def webp(body):return b'RIFF'+struct.pack('<I',len(body)+4)+b'WEBP'+body
    def forbidden(*args,**kwargs):raise AssertionError('native decoder reached before dimension check')
    monkeypatch.setattr(Image,'open',forbidden)
    large=(99999).to_bytes(3,'little')
    packets=[chunk(b'VP8X',b'\0'*4+large+large),
        chunk(b'VP8 ',b'\0'*3+b'\x9d\x01\x2a'+struct.pack('<HH',10000,10000)),
        chunk(b'VP8L',b'\x2f'+((9999)|((9999)<<14)).to_bytes(4,'little'))]
    for packet in packets:
        with pytest.raises(StoreError) as exc:normalize_image(webp(packet))
        assert exc.value.status==413


def test_valid_webp_still_uploads_and_expired_pending_is_hidden_but_deletable(tmp_path):
    import sqlite3,time
    from aiforstudy.community.images import normalize_image
    from aiforstudy.community.store import StoreError
    assert normalize_image(picture('WEBP')).mime=='image/webp'
    s=store(tmp_path/'db');im=upload(s)
    with sqlite3.connect(s.path) as db:db.execute('UPDATE annotation_images SET created_at=?',(time.time()-90000,))
    with pytest.raises(StoreError):s.image(im['id'],A)
    s.remove_image(im['id'],A)


def test_owner_can_find_and_clear_abandoned_uploads_after_reloads(tmp_path):
    with running(reader(tmp_path/'book'),tmp_path/'db') as server:
        ids=[post_image(server,picture())[1]['image']['id'] for _ in range(12)]
        assert post_image(server,picture())[0]==429
        status,_,data=call(server,path='/api/annotation-images?pending=1',token=A)
        assert status==200 and len(json.loads(data)['images'])==12
        assert json.loads(call(server,path='/api/annotation-images?pending=1',token=B)[2])['images']==[]
        assert call(server,'DELETE','/api/annotation-images/'+ids[0],token=A)[0]==200
        assert post_image(server,picture())[0]==201


def test_missing_decoder_is_advertised_without_disabling_text_comments(tmp_path,monkeypatch):
    import aiforstudy.community.server as module
    monkeypatch.setattr(module,'image_capabilities',lambda:{'available':False,'mime_types':[]},raising=False)
    with running(reader(tmp_path/'book'),tmp_path/'db') as server:
        raw=call(server)[2].decode();marker='window.__AIFS_SHARED_NOTES__='
        cfg=json.JSONDecoder().raw_decode(raw.split(marker,1)[1])[0]
        assert cfg['image_upload_available'] is False
        assert post_image(server,picture())[0]==503
        assert call(server,'POST','/api/annotations',content(),A)[0]==201


def test_nonempty_original_schema_migrates_without_changing_owner_or_image(tmp_path):
    import sqlite3
    db = tmp_path/'notes.db'
    s = store(db)
    image = upload(s)
    body = payload(images=[{'id':image['id'], 'presentation':'image'}])
    original,_ = s.create('CH01',body,A)
    original_bytes = s.image(image['id'])['data']
    # Recreate the pre-edit schema, preserving its real bearer digest, image and request ID.
    with sqlite3.connect(db) as conn:
        conn.execute('DROP TABLE annotation_requests')
        conn.execute('ALTER TABLE annotations DROP COLUMN edit_revision')
        conn.execute('ALTER TABLE annotations DROP COLUMN updated_at')
    migrated = store(db)
    original_id = original['id']
    note, = migrated.list('CH01',A)['items']
    assert note['is_mine'] and note['revision']==1 and note['updated_at']==note['created_at']
    updated = migrated.edit(original_id,dict(revision=1,text=''),A)
    assert updated['revision']==2 and updated['images']==original['images']
    assert migrated.image(image['id'])['data']==original_bytes
    replay,created = store(db).create('CH01',body,A)
    assert not created and replay==updated
    migrated.delete(original_id,A)
    from aiforstudy.community.store import StoreError
    with pytest.raises(StoreError) as exc:
        store(db).create('CH01',body,A)
    assert exc.value.status==410
