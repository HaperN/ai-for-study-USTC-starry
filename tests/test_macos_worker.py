"""Platform isolation must work before macOS rendering can be advertised."""
import json
import os
from pathlib import Path
import subprocess
import sys
import signal

import pytest
from aiforstudy.blocks.visual import worker
from aiforstudy.providers import capabilities

ROOT = Path(__file__).resolve().parents[1]


def test_binary_nonzero_is_not_available(monkeypatch):
    monkeypatch.setattr(capabilities.shutil, 'which', lambda _: '/broken/tool')
    monkeypatch.setattr(capabilities.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 1, b'broken'))
    assert not capabilities._probe_binary('ffprobe', ('-version',)).available


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS kernel contract')
def test_seatbelt_protects_files_network_signals_and_process_group(tmp_path):
    seatbelt = worker.load_asset(ROOT / 'tools/manim_worker', 'seatbelt')
    allowed = tmp_path / 'allowed'; allowed.mkdir()
    sentinel = tmp_path / 'secret'; sentinel.write_text('fake credential')
    program = '''import os,socket,json,subprocess,ctypes
libc=ctypes.CDLL(None,use_errno=True)
def query_outside_environment():
 mib=(ctypes.c_int*3)(1,49,ENVHOST)
 buf=ctypes.create_string_buffer(1024*1024)
 size=ctypes.c_size_t(len(buf))
 if libc.sysctl(mib,3,buf,ctypes.byref(size),None,0)!=0:
  raise OSError(ctypes.get_errno(),'outside process environment query')
checks={}
for name, action in [
 ('read', lambda: open(SENTINEL).read()),
 ('process_environment', query_outside_environment),
 ('write', lambda: open(SENTINEL,'w').write('bad')),
 ('tcp', lambda: socket.create_connection(('127.0.0.1',PORT),timeout=1)),
 ('udp', lambda: socket.socket(socket.AF_INET,socket.SOCK_DGRAM).sendto(b'probe',('127.0.0.1',PORT))),
 ('signal', lambda: os.kill(HOST,CONT)),
 ('setsid', os.setsid),
 ('setpgid', lambda: os.setpgid(0,0)),
 ('spawn_escape', lambda: os.posix_spawn('/bin/true',['/bin/true'],{},setpgroup=0)),
]:
 try: action(); checks[name]=False
 except PermissionError: checks[name]=True
open(OUTPUT,'w').write('allowed')
checks['subprocess']=subprocess.run(['/bin/echo','child'],capture_output=True).stdout == b'child\\n'
checks['inherited_read_block']=subprocess.run(['/bin/cat',SENTINEL],capture_output=True).returncode != 0
print(json.dumps(checks))
'''
    import socket
    with socket.socket() as listener, subprocess.Popen(['/bin/sleep','10'],env={'AIFS_FAKE_SECRET':'not-real'}) as outside:
        listener.bind(('127.0.0.1',0)); listener.listen()
        source = ('SENTINEL='+repr(str(sentinel))+'\nOUTPUT='+repr(str(allowed/'out'))+'\nHOST='+str(os.getpid())+'\nENVHOST='+str(outside.pid)+'\nCONT='+str(signal.SIGCONT)+'\nPORT='+str(listener.getsockname()[1])+'\n'+program)
        prefix = 'import sys;sys.path.insert(0,'+repr(str(ROOT/'tools/manim_worker'))+');import seatbelt;seatbelt.apply(read_only='+repr(['/System','/usr','/bin','/Library','/opt/homebrew',str(Path(sys.prefix))])+',read_write='+repr([str(allowed)])+')\n'
        try:
            proc = subprocess.run([sys.executable,'-c',prefix+source],capture_output=True,text=True,timeout=20,cwd=allowed,start_new_session=True)
        finally:
            outside.terminate()
    assert proc.returncode == 0, proc.stderr
    assert all(json.loads(proc.stdout).values()), proc.stdout
    assert sentinel.read_text() == 'fake credential'
    assert (allowed/'out').read_text() == 'allowed'
