"""Small independent guardian for one trusted OCR command and its process group."""
from __future__ import annotations
import argparse,os,signal,subprocess,time,shutil
from pathlib import Path


def cleanup_owned(path, parent_pid):
    if path is None: return
    root=Path(path)
    try:
        marker=root/'.aifs-ocr-owner'
        if not root.is_symlink() and root.name.startswith('aifs-image-ocr-') and marker.read_text()==str(parent_pid):
            shutil.rmtree(root)
    except (OSError,ValueError):pass


def supervise(command, *, parent_pid, timeout, cleanup_dir=None):
    if os.getpgrp()!=os.getpid():
        raise RuntimeError('OCR guard must own its process group')
    stopped=False;process=None;started=time.monotonic();status=130
    def stop(signum,frame):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        if os.getppid()!=parent_pid:return 130
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        while process.poll() is None:
            if stopped or os.getppid()!=parent_pid:status=130;break
            if time.monotonic()-started>=timeout:status=124;break
            time.sleep(.05)
        else:status=process.returncode
        return status
    finally:
        # The child inherits this group. Do not kill the API server's group.
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        signal.signal(signal.SIGINT,signal.SIG_IGN)
        try:os.killpg(os.getpgrp(),signal.SIGTERM)
        except ProcessLookupError:pass
        if process is not None and process.poll() is None:
            try:process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                if os.getppid()!=parent_pid:cleanup_owned(cleanup_dir,parent_pid)
                os.killpg(os.getpgrp(),signal.SIGKILL)
        if os.getppid()!=parent_pid:cleanup_owned(cleanup_dir,parent_pid)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--parent-pid',type=int,required=True);parser.add_argument('--timeout',type=float,required=True);parser.add_argument('--cleanup-dir');parser.add_argument('command',nargs=argparse.REMAINDER);args=parser.parse_args()
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    if not command or not 0<args.timeout<=90:raise SystemExit(2)
    raise SystemExit(supervise(command,parent_pid=args.parent_pid,timeout=args.timeout,cleanup_dir=args.cleanup_dir))

if __name__=='__main__':main()
