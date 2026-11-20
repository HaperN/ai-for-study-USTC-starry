"""The school adapter boundary never treats nickname/client IDs as ownership."""
import hashlib
import importlib
import json
from test_shared_annotations_http import reader, running, call, content, A, B


def test_default_identity_adapter_keeps_nickname_mode_without_accounts():
    auth=importlib.import_module('aiforstudy.community.identity')
    provider=auth.NicknameIdentityProvider()
    assert provider.resolve({},required=False) is None
    principal=provider.resolve({'Authorization':'Bearer '+A},required=True)
    assert principal.storage_key == hashlib.sha256(A.encode()).hexdigest()
    assert principal.display_name is None and principal.authenticated is False


def test_school_subject_is_stable_and_namespaced_not_display_name():
    auth=importlib.import_module('aiforstudy.community.identity')
    a=auth.Principal('school:example', 'student-001', '张同学')
    renamed=auth.Principal('school:example', 'student-001', '新昵称')
    b=auth.Principal('school:example', 'student-002', '张同学')
    other=auth.Principal('school:another', 'student-001', '张同学')
    assert a.storage_key==renamed.storage_key
    assert len({a.storage_key,b.storage_key,other.storage_key})==3
    assert a.authenticated is True


def test_default_api_exposes_only_public_identity_metadata(tmp_path):
    with running(reader(tmp_path/'book'),tmp_path/'notes.db') as server:
        status,_,body=call(server,path='/api/identity',token=A)
        assert status==200
        value=json.loads(body)
        assert value=={'mode':'nickname','authenticated':False,'display_name':None,'can_publish':True,'login_path':None}
        assert A not in body.decode()


def test_configured_school_provider_owns_identity_and_default_mode_ignores_spoofed_ids(tmp_path):
    auth=importlib.import_module('aiforstudy.community.identity')
    from aiforstudy.community.server import make_server
    import threading
    class FakeVerifiedSchoolSessions:
        mode='school'
        login_path='/auth/school/start'
        def resolve(self,headers,*,required=False):
            # Test double for the future adapter's verified server-side session.
            session=headers.get('Cookie','')
            if session in ('school_session=verified-a','school_session=verified-a-second-device'):
                return auth.Principal('school:campus', 'student-A', '校内张同学')
            if session=='school_session=verified-b':
                return auth.Principal('school:campus', 'student-B', '校内张同学')
            if required:raise auth.IdentityError(401,'请先完成学校身份认证。')
            return None
    root=reader(tmp_path/'book');db=tmp_path/'notes.db'
    server=make_server(root,db,port=0,identity_provider=FakeVerifiedSchoolSessions())
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        status,_,body=call(server,path='/api/identity')
        assert status==200 and json.loads(body)['can_publish'] is False
        assert json.loads(body)['login_path']=='/auth/school/start'
        assert call(server,'POST','/api/annotations',content(),A,headers={'X-School-User':'student-A'})[0]==401
        status,_,body=call(server,'POST','/api/annotations',content(nickname='伪造名字',subject='student-B'),B,headers={'Cookie':'school_session=verified-a'})
        assert status==201;note=json.loads(body)['item'];assert note['nickname']=='校内张同学'
        assert call(server,'DELETE',f'/api/annotations/{note["id"]}',headers={'Cookie':'school_session=verified-b'})[0]==403
        assert call(server,'DELETE',f'/api/annotations/{note["id"]}',headers={'Cookie':'school_session=verified-a-second-device'})[0]==200
    finally:server.shutdown();server.server_close();thread.join(timeout=2)
    # Without configuring a school adapter, client-provided user IDs are ignored.
    with running(root,db) as server:
        status,_,body=call(server,'POST','/api/annotations',content(subject='student-A'),A,headers={'X-School-User':'student-A'})
        assert status==201
        note=json.loads(body)['item']
        assert call(server,'DELETE',f'/api/annotations/{note["id"]}',token=B,headers={'X-School-User':'student-A'})[0]==403


def test_empty_valid_school_adapter_never_falls_back_to_nickname(tmp_path):
    from aiforstudy.community.identity import IdentityError
    from aiforstudy.community.server import make_server
    import threading
    class EmptySchoolSessions(dict):
        mode='school'; login_path='/auth/school/start'
        def resolve(self,headers,*,required=False):
            if required:raise IdentityError(401,'学校会话缺失')
            return None
    provider=EmptySchoolSessions();assert not provider
    server=make_server(reader(tmp_path/'book'),tmp_path/'notes.db',port=0,identity_provider=provider)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        assert json.loads(call(server,path='/api/identity')[2])['mode']=='school'
        assert call(server,'POST','/api/annotations',content(),A)[0]==401
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def test_retry_after_school_display_name_change_keeps_original_record(tmp_path):
    from aiforstudy.community.identity import Principal
    from aiforstudy.community.store import AnnotationStore
    store=AnnotationStore(tmp_path/'notes.db','book','v1');body=content(nickname='客户端昵称')
    one,_=store.create('CH01',body,Principal('school:campus','id-1','第一次署名'))
    two,created=store.create('CH01',body,Principal('school:campus','id-1','后来的署名'))
    assert not created and one['id']==two['id']
    assert one['nickname']==two['nickname']=='第一次署名'
