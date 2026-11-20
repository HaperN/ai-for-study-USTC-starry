import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from aiforstudy.blocks.media.local_narration import AutoLocalQwenBackend


def backend(tmp_path):
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'status':'verified_complete','repo':'test','revision':'a','files':[]}))
    return AutoLocalQwenBackend({'model_dir':str(tmp_path),'model_manifest':str(manifest),'python':str(tmp_path/'python'),'cache_dir':str(tmp_path/'cache'),'lock_file':str(tmp_path/'gpu.lock'),'timeout':2})


def publish(b,text):
    import wave
    from aiforstudy.blocks.media.local_narration import cache_key,digest
    folder=b.cache/cache_key(text,b.source);folder.mkdir(parents=True,exist_ok=True)
    with wave.open(str(folder/'audio.wav'),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\x01\x00'*24000)
    (folder/'result.json').write_text(json.dumps({'cache_key':folder.name,'identity':b.source,'text':text,'audio_sha256':digest(folder/'audio.wav')}))


def test_miss_worker_protocol_and_concurrent_same_text_hit(tmp_path,monkeypatch):
    b=backend(tmp_path);calls=[]
    def run(args,**kwargs):
        request=json.loads(Path(args[args.index('--corpus')+1]).read_text())
        text=request['records'][0]['comparison_spoken_text'];calls.append(text)
        time.sleep(.05);publish(b,text)
    monkeypatch.setattr(subprocess,'run',run)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda i:b.synthesize('新句',tmp_path/f'{i}.wav'),range(2)))
    assert calls==['新句']
    b.synthesize('新句',tmp_path/'hit.wav');assert len(calls)==1


@pytest.mark.parametrize('failure',[RuntimeError('failed'),subprocess.TimeoutExpired('worker',2)])
def test_worker_failure_and_timeout_never_fallback(tmp_path,monkeypatch,failure):
    b=backend(tmp_path)
    def fail(*a,**k):raise failure
    monkeypatch.setattr(subprocess,'run',fail)
    with pytest.raises((RuntimeError,subprocess.TimeoutExpired)):b.synthesize('失败',tmp_path/'out.wav')
    assert not (tmp_path/'out.wav').exists()


def test_corrupt_cache_rejected_without_worker(tmp_path,monkeypatch):
    b=backend(tmp_path);publish(b,'坏缓存')
    next(b.cache.glob('*/audio.wav')).write_bytes(b'broken')
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('must not silently regenerate corrupt cache'))
    with pytest.raises(ValueError,match='cache'):b.synthesize('坏缓存',tmp_path/'out.wav')


def test_different_misses_serialized_and_identity_revision_changes_key(tmp_path,monkeypatch):
    from aiforstudy.blocks.media.local_narration import cache_key
    b=backend(tmp_path);active=0;peak=0
    def run(args,**kwargs):
        nonlocal active,peak
        active+=1;peak=max(peak,active);time.sleep(.05)
        text=json.loads(Path(args[args.index('--corpus')+1]).read_text())['records'][0]['comparison_spoken_text']
        publish(b,text);active-=1
    monkeypatch.setattr(subprocess,'run',run)
    with ThreadPoolExecutor(2) as pool:list(pool.map(lambda i:b.synthesize(str(i),tmp_path/f'{i}.wav'),range(2)))
    assert peak==1
    old=cache_key('0',b.source)
    manifest=tmp_path/'manifest.json';data=json.loads(manifest.read_text());data['revision']='b';manifest.write_text(json.dumps(data))
    changed=AutoLocalQwenBackend({**b.paths,'lock_file':str(b.lock)})
    assert cache_key('0',changed.source)!=old


def test_interpreter_symlink_preserves_virtualenv_entrypoint(tmp_path):
    b=backend(tmp_path)
    target=tmp_path/'system-python';target.touch()
    link=tmp_path/'python';link.symlink_to(target)
    b=AutoLocalQwenBackend({**b.paths,'lock_file':str(b.lock)})
    assert b.paths['python']==str(link)


def test_cancel_active_process_reaped_before_return(tmp_path):
    import sys,os
    from types import SimpleNamespace
    from aiforstudy.errors import Cancelled
    from aiforstudy.blocks.media.local_narration import run_worker
    token=SimpleNamespace(cancelled=False);pidfile=tmp_path/'pid'
    script="import os,time;from pathlib import Path;Path(%r).write_text(str(os.getpid()));time.sleep(60)" % str(pidfile)
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(run_worker,[sys.executable,'-c',script],timeout=10,env=None,cancel=token)
        deadline=time.monotonic()+3
        while not pidfile.exists() and time.monotonic()<deadline:time.sleep(.01)
        assert pidfile.exists();token.cancelled=True;started=time.monotonic()
        with pytest.raises(Cancelled):future.result(timeout=2)
        assert time.monotonic()-started<2
        with pytest.raises(ProcessLookupError):os.kill(int(pidfile.read_text()),0)


def test_cancel_queued_lock_starts_no_worker(tmp_path,monkeypatch):
    import fcntl
    from types import SimpleNamespace
    from aiforstudy.errors import Cancelled
    b=backend(tmp_path);token=SimpleNamespace(cancelled=False)
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('queued worker started'))
    with b.lock.open('a') as lock,ThreadPoolExecutor(1) as pool:
        fcntl.flock(lock,fcntl.LOCK_EX)
        future=pool.submit(b.synthesize,'等待',tmp_path/'out.wav',cancel=token)
        time.sleep(.1);token.cancelled=True
        with pytest.raises(Cancelled):future.result(timeout=2)
        fcntl.flock(lock,fcntl.LOCK_UN)


def test_cancel_during_first_step_never_starts_second(tmp_path):
    from types import SimpleNamespace
    from aiforstudy.errors import Cancelled
    from aiforstudy.blocks.media.narration import prepare_step_audio
    token=SimpleNamespace(cancelled=False);calls=[]
    class Backend:
        source={'backend':'test'}
        supports_cancel=True
        def synthesize(self,text,destination,*,cancel):
            calls.append(text);cancel.cancelled=True
    with pytest.raises(Cancelled):
        prepare_step_audio([{'step_id':'S1','narration_text':'一。'},{'step_id':'S2','narration_text':'二。'}],tmp_path,backend=Backend(),cancel=token)
    assert calls==['一。']


def test_cancel_waiting_for_separate_process_gpu_lease(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace
    from aiforstudy.errors import Cancelled
    b=backend(tmp_path);ready=tmp_path/'ready';token=SimpleNamespace(cancelled=False)
    code="import fcntl,time;from pathlib import Path;f=open(%r,'a');fcntl.flock(f,fcntl.LOCK_EX);Path(%r).touch();time.sleep(60)" % (str(b.lock),str(ready))
    locker=subprocess.Popen([sys.executable,'-c',code])
    try:
        deadline=time.monotonic()+3
        while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
        assert ready.exists()
        monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('must not launch queued worker'))
        with ThreadPoolExecutor(1) as pool:
            task=pool.submit(b.synthesize,'排队',tmp_path/'out.wav',cancel=token)
            time.sleep(.1);token.cancelled=True
            with pytest.raises(Cancelled):task.result(timeout=2)
        assert not (tmp_path/'out.wav').exists()
    finally:locker.kill();locker.wait(timeout=2)


def test_active_backend_cancel_reaps_before_unlock_and_stops_later_steps(tmp_path,monkeypatch):
    import sys,os,fcntl
    from types import SimpleNamespace
    from aiforstudy.errors import Cancelled
    from aiforstudy.blocks.media import local_narration as L
    from aiforstudy.blocks.media.narration import prepare_step_audio
    b=backend(tmp_path);token=SimpleNamespace(cancelled=False);pidfile=tmp_path/'model-pid';calls=[]
    actual=L.run_worker
    def protocol(argv,**kwargs):
        calls.append(json.loads(Path(argv[argv.index('--corpus')+1]).read_text()))
        code="import os,time;from pathlib import Path;Path(%r).write_text(str(os.getpid()));time.sleep(60)" % str(pidfile)
        return actual([sys.executable,'-c',code],**kwargs)
    monkeypatch.setattr(L,'run_worker',protocol)
    with ThreadPoolExecutor(1) as pool:
        task=pool.submit(prepare_step_audio,[{'step_id':'S1','narration_text':'第一步。'},{'step_id':'S2','narration_text':'第二步。'}],tmp_path/'voices',backend=b,cancel=token)
        deadline=time.monotonic()+3
        while not pidfile.exists() and time.monotonic()<deadline:time.sleep(.01)
        assert pidfile.exists();token.cancelled=True
        with pytest.raises(Cancelled):task.result(timeout=2)
    with b.lock.open('a') as lock:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    with pytest.raises(ProcessLookupError):os.kill(int(pidfile.read_text()),0)
    assert len(calls)==1 and not list(b.cache.glob('*/result.json'))
