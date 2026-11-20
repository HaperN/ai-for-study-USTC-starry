"""macOS kernel Seatbelt policy, inherited across fork/exec.

The trusted entry applies sandbox_init before importing any generated source. Default-deny
files/network/IPC/signals; only job directories are writable. Unix syscall
filtering also prevents leaving the monitored process group (including the
posix_spawn SETSID/SETPGROUP bypass). No privileged helper or global process
identification is used. A real kernel self-test gates availability.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys


def build_profile(*, read_only: list[str], read_write: list[str]) -> str:
    rules = ['(version 1)', '(deny default)', '(allow process-exec)', '(allow process-fork)',
             '(allow signal (target same-sandbox))',
             '(allow process-info* (target self))',
             '(deny process-info* (target others))',
             '(deny sysctl-read (sysctl-name-prefix "kern.procargs"))',
             # Never grant kern.procargs2: it exposes other processes' env.
             '(allow sysctl-read (sysctl-name-regex #"^hw\\.") '
             '(sysctl-name "kern.osrelease") (sysctl-name "kern.ostype") '
             '(sysctl-name "kern.osversion") (sysctl-name "kern.version") '
             '(sysctl-name "kern.hostname") (sysctl-name "kern.osvariant_status") '
             '(sysctl-name "kern.maxfilesperproc") (sysctl-name "kern.argmax") '
             '(sysctl-name "kern.usrstack64") (sysctl-name "sysctl.proc_cputype"))',
             '(allow file-read-metadata)', '(allow file-read* (literal "/"))',
             # Darwin syscall numbers in sys/syscall.h. posix_spawn can set a
             # group/session without invoking setpgid/setsid, so deny it too.
             '(allow syscall-unix)',
             '(deny syscall-unix (syscall-number 82) (syscall-number 147) (syscall-number 244))',
             '(allow mach-lookup (global-name "com.apple.FontObjectsServer") (global-name "com.apple.fonts"))']
    for raw in dict.fromkeys([*read_only, *read_write]):
        path = Path(raw).expanduser().resolve()
        if str(path) == '/':
            raise ValueError('the entire filesystem cannot be allowed')
        kind = 'subpath' if path.is_dir() else 'literal'
        rules.append(f'(allow file-read* ({kind} {json.dumps(str(path), ensure_ascii=False)}))')
    for raw in dict.fromkeys(read_write):
        path = Path(raw).expanduser().resolve()
        kind = 'subpath' if path.is_dir() else 'literal'
        rules.append(f'(allow file-write* ({kind} {json.dumps(str(path), ensure_ascii=False)}))')
    return '\n'.join(rules)


def apply(*, read_only: list[str], read_write: list[str]) -> dict:
    import ctypes
    import hashlib
    profile = build_profile(read_only=read_only, read_write=read_write)
    library = ctypes.CDLL('/usr/lib/libsandbox.dylib')
    init = library.sandbox_init
    init.argtypes = [ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(ctypes.c_char_p)]
    init.restype = ctypes.c_int
    error = ctypes.c_char_p()
    if init(profile.encode('utf-8'), 0, ctypes.byref(error)) != 0:
        raise RuntimeError('Seatbelt activation failed: ' + (error.value or b'unknown').decode())
    return {'backend': 'seatbelt', 'profile_sha256': hashlib.sha256(profile.encode()).hexdigest(),
            'network': 'deny', 'signal_scope': 'same-sandbox',
            'process_info_scope': 'self', 'sysctl_policy': 'hardware-and-os-only',
            'denied_syscalls': ['setpgid', 'setsid', 'posix_spawn']}


def selftest() -> dict:
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').is_file():
        return {'available': False, 'detail': 'macOS sandbox-exec unavailable'}
    code = '''import os, socket
for fn in (os.setsid, lambda: os.setpgid(0,0), lambda: os.posix_spawn('/bin/true',['/bin/true'],{},setpgroup=0), lambda: socket.socket(socket.AF_INET,socket.SOCK_DGRAM).sendto(b'x',('127.0.0.1',9))):
 try: fn()
 except PermissionError: continue
 raise SystemExit('policy did not deny a forbidden operation')
print('seatbelt-ok')
'''
    try:
        prefix = 'import sys; sys.path.insert(0,' + repr(str(Path(__file__).parent)) + '); import seatbelt; seatbelt.apply(read_only=' + repr(['/System','/usr','/bin','/Library','/opt/homebrew',sys.prefix]) + ',read_write=[\"/dev/null\"])\n'
        proc = subprocess.run([sys.executable,'-c',prefix + code],capture_output=True,text=True,timeout=15,cwd='/usr')
    except (OSError, subprocess.SubprocessError) as exc:
        return {'available': False, 'detail': str(exc)}
    return {'available': proc.returncode == 0 and 'seatbelt-ok' in proc.stdout,
            'detail': 'Seatbelt file/network/signal policy + process-group syscall restrictions' if proc.returncode == 0 else (proc.stderr or proc.stdout)[-500:]}
