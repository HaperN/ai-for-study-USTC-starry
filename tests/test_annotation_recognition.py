from pathlib import Path
import json,threading,sys,uuid,time
import pytest
from test_shared_annotation_images import picture,upload
from test_shared_annotations_store import store,payload,A,B
from test_shared_annotations_http import reader,call,content


def test_only_complete_server_candidate_can_be_selected_and_original_stays(tmp_path):
    from aiforstudy.community.store import StoreError
    s=store(tmp_path/'db');im=upload(s)
    saved=s.save_recognition(im['id'],A,{'text':'$$x^2+1$$','engine':'fixture','truncated':False})
    assert saved['text']=='$$x^2+1$$'
    assert s.save_recognition(im['id'],A,{'text':'tampered','engine':'fixture','truncated':False})['text']==saved['text']
    with pytest.raises(StoreError):s.recognition(im['id'],B)
    with pytest.raises(StoreError):s.create('CH01',payload(images=[{'id':im['id'],'presentation':'recognized'}]),A)
    note,_=s.create('CH01',payload(text='',images=[{'id':im['id'],'presentation':'recognized','confirmed':True,'text':'client forgery'}]),A)
    assert note['images'][0]['recognized_text']=='$$x^2+1$$'
    assert s.image(im['id'])['data']


def test_truncated_candidate_cannot_publish_as_recognized_but_image_mode_works(tmp_path):
    from aiforstudy.community.store import StoreError
    s=store(tmp_path/'db');im=upload(s)
    s.save_recognition(im['id'],A,{'text':'incomplete','engine':'fixture','truncated':True})
    with pytest.raises(StoreError):s.create('CH01',payload(images=[{'id':im['id'],'presentation':'recognized','confirmed':True}]),A)
    note,_=s.create('CH01',payload(text='',images=[{'id':im['id'],'presentation':'image'}]),A)
    assert 'recognized_text' not in note['images'][0]


@pytest.mark.parametrize('bad',[{}, {'text':'','engine':'a','truncated':False},{'text':'x'*20001,'engine':'a','truncated':False},{'text':'x','engine':'a','truncated':'false'},{'text':'a\x00b','engine':'a','truncated':False}])
def test_invalid_ocr_output_is_not_saved_as_a_valid_candidate(tmp_path,bad):
    from aiforstudy.community.store import StoreError
    s=store(tmp_path/'db');im=upload(s)
    with pytest.raises(StoreError):s.save_recognition(im['id'],A,bad)
    assert s.recognition(im['id'],A) is None


def test_http_recognition_is_explicit_owner_only_and_memoized(tmp_path):
    from aiforstudy.community.server import make_server
    class Recognizer:
        calls=0
        def recognize(self,data,mime):
            self.calls+=1
            assert data and mime=='image/png'
            return {'text':'$$\\frac{1}{2}$$','engine':'fixture','truncated':False}
    engine=Recognizer();server=make_server(reader(tmp_path/'book'),tmp_path/'db',port=0,recognizer=engine)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        im=upload(server.store);assert engine.calls==0
        uri='/api/annotation-images/'+im['id']+'/recognize'
        assert call(server,'POST',uri,{},B)[0]==404 and engine.calls==0
        status,_,body=call(server,'POST',uri,{},A);assert status==200 and json.loads(body)['recognition']['text']=='$$\\frac{1}{2}$$'
        assert call(server,'POST',uri,{},A)[0]==200 and engine.calls==1
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def test_no_configured_recognizer_does_not_block_image_publication(tmp_path):
    from test_shared_annotations_http import running
    with running(reader(tmp_path/'book'),tmp_path/'db') as server:
        im=upload(server.store)
        assert call(server,'POST','/api/annotation-images/'+im['id']+'/recognize',{},A)[0]==503
        assert call(server,'POST','/api/annotations',content(text='',images=[{'id':im['id'],'presentation':'image'}]),A)[0]==201


def test_local_worker_timeout_reaps_owned_process_and_releases_slot(tmp_path):
    from aiforstudy.community.recognition import LocalRecognizer
    from aiforstudy.community.store import StoreError
    interpreter=tmp_path/'sleep-python';interpreter.write_text('#!/bin/sh\nsleep 30\n');interpreter.chmod(0o755)
    model=tmp_path/'model';model.mkdir();(model/'config.json').write_text('{}')
    worker=LocalRecognizer(interpreter,model,timeout=.15)
    started=time.monotonic()
    with pytest.raises(StoreError) as exc:worker.recognize(picture(),'image/png')
    assert exc.value.status==504 and time.monotonic()-started<3
    with pytest.raises(StoreError) as again:worker.recognize(picture(),'image/png')
    assert again.value.status==504


def test_configured_python_keeps_virtual_environment_symlink(tmp_path):
    from aiforstudy.community.recognition import LocalRecognizer
    base=tmp_path/'base';base.mkdir();venv=tmp_path/'venv/bin';venv.mkdir(parents=True)
    executable=base/'python';executable.write_text('''#!/bin/sh
[ -f "$(dirname "$0")/venv-marker" ] || exit 4
out=
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output" ]; then shift; out=$1; fi
  shift
done
printf '%s' '{"text":"configured environment","truncated":false}' > "$out"
''');executable.chmod(0o755)
    link=venv/'python';link.symlink_to(executable);(venv/'venv-marker').write_text('environment identity')
    model=tmp_path/'model';model.mkdir();(model/'config.json').write_text('{}')
    assert LocalRecognizer(link,model).recognize(picture(),'image/png')['text']=='configured environment'


def test_recognizer_close_stops_inflight_worker_and_prevents_restart(tmp_path):
    import os,subprocess,threading
    from aiforstudy.community.recognition import LocalRecognizer
    interpreter=tmp_path/'slow-python';pidfile=tmp_path/'pid'
    interpreter.write_text('#!/bin/sh\necho $$ > "'+str(pidfile)+'"\nsleep 30\n');interpreter.chmod(0o755)
    model=tmp_path/'model';model.mkdir();(model/'config.json').write_text('{}')
    worker=LocalRecognizer(interpreter,model);caught=[]
    def run():
        try:worker.recognize(picture(),'image/png')
        except Exception as exc:caught.append(exc)
    thread=threading.Thread(target=run,daemon=True);thread.start()
    for _ in range(100):
        if pidfile.exists():break
        time.sleep(.02)
    assert pidfile.exists();pid=int(pidfile.read_text())
    try:
        worker.close();thread.join(timeout=3);assert not thread.is_alive()
        with pytest.raises(ProcessLookupError):os.kill(pid,0)
        from aiforstudy.community.store import StoreError
        with pytest.raises(StoreError):worker.recognize(picture(),'image/png')
    finally:
        try:os.killpg(pid,9)
        except ProcessLookupError:pass
        thread.join(timeout=2)


def test_cli_sigterm_closes_recognition_worker(tmp_path):
    import subprocess,os,signal
    from types import SimpleNamespace
    from test_shared_annotation_images import post_image
    root=Path(__file__).resolve().parents[1];book=reader(tmp_path/'book');model=tmp_path/'model';model.mkdir();(model/'config.json').write_text('{}')
    pidfile=tmp_path/'worker-pid';interpreter=tmp_path/'slow';interpreter.write_text('#!/bin/sh\necho $$ > "'+str(pidfile)+'"\nsleep 30\n');interpreter.chmod(0o755)
    process=subprocess.Popen([sys.executable,'-m','aiforstudy','community','--reader',str(book),'--database',str(tmp_path/'db'),'--port','0','--ocr-python',str(interpreter),'--ocr-model',str(model)],cwd=root,env=dict(os.environ,PYTHONPATH=str(root/'src'),PYTHONDONTWRITEBYTECODE='1'),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    pid=None
    try:
        info=json.loads(process.stdout.readline());port=int(info['url'].split(':')[-1].strip('/'))
        remote=SimpleNamespace(server_address=('127.0.0.1',port),context={'revision':info['revision']})
        im=post_image(remote,picture())[1]['image'];errors=[]
        def request():
            try:call(remote,'POST','/api/annotation-images/'+im['id']+'/recognize',{},A)
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=request,daemon=True);thread.start()
        for _ in range(100):
            if pidfile.exists():break
            time.sleep(.02)
        assert pidfile.exists();pid=int(pidfile.read_text())
        process.terminate();process.wait(timeout=5);thread.join(timeout=3)
        with pytest.raises(ProcessLookupError):os.kill(pid,0)
    finally:
        if process.poll() is None:process.kill();process.wait()
        if pid:
            try:os.killpg(pid,signal.SIGKILL)
            except ProcessLookupError:pass
        process.stdout.close()


def test_independent_guard_cleans_child_and_temporary_photo_when_parent_dies(tmp_path):
    import subprocess,os,signal,tempfile
    from aiforstudy.community import process_guard
    work=Path(tempfile.mkdtemp(prefix='aifs-image-ocr-',dir=tmp_path));pidfile=work/'worker-pid';(work/'photo.png').write_bytes(picture())
    script=tmp_path/'parent.py'
    child_code='import os,time,pathlib;pathlib.Path('+repr(str(pidfile))+').write_text(str(os.getpid()));time.sleep(30)'
    script.write_text('import os,time,subprocess,json,pathlib,sys\n'+'root=pathlib.Path('+repr(str(work))+')\n'+"(root/'.aifs-ocr-owner').write_text(str(os.getpid()))\n"+'command='+repr([sys.executable,process_guard.__file__,'--parent-pid','PARENT','--timeout','30','--cleanup-dir',str(work),'--',sys.executable,'-c',child_code])+"\ncommand[command.index('PARENT')]=str(os.getpid())\np=subprocess.Popen(command,start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nprint(json.dumps({'guard':p.pid}),flush=True)\ntime.sleep(30)\n")
    parent=subprocess.Popen([sys.executable,str(script)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True);guard=None;worker=None
    try:
        guard=json.loads(parent.stdout.readline())['guard']
        for _ in range(100):
            if pidfile.exists():break
            time.sleep(.02)
        assert pidfile.exists();worker=int(pidfile.read_text());parent.kill();parent.wait(timeout=3)
        for _ in range(100):
            if not work.exists():break
            time.sleep(.02)
        assert not work.exists()
        with pytest.raises(ProcessLookupError):os.kill(worker,0)
    finally:
        if parent.poll() is None:parent.kill();parent.wait()
        if guard:
            try:os.killpg(guard,signal.SIGKILL)
            except ProcessLookupError:pass
        parent.stdout.close()
